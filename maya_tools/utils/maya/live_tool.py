# maya_tools/utils/maya/live_tool.py
"""LiveTool — Maya scene-event subscription backend with debounced UI dispatch.

Two-layer architecture:
  1. OpenMaya callbacks fire (may be mid-DG-evaluation). Capture handle,
     queue event, restart debounce timer. Minimum work, no Qt mutation,
     no cmds.* calls inside the callback.
  2. QTimer fires (Qt main thread, DG settled). Drain queue, validate
     handles, dispatch to host widget's hooks. Hooks are free to call
     cmds.*, mutate Qt models, do heavy work.

The LiveBackend is owned by a Qt host widget via composition — never
inherited. Hooks are passed as callables at construction. See the
LiveUI Callbacks spec for the full safety contract.
"""
__author__ = "Adrian Melian"

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

import maya.api.OpenMaya as om

from PySide6 import QtCore


class DagChangeKind(Enum):
    """Every MDagMessage msg type the API exposes gets a named member.

    Lossless wrapping — the underlying integer flags are an implementation
    detail of LiveBackend. Subclasses consume DagChangeKind exclusively.
    Never expose a .raw escape hatch on DagChange — if a flag matters
    enough for a consumer to need it, it gets named here.
    """
    INVALID = om.MDagMessage.kInvalidMsg
    PARENT_ADDED = om.MDagMessage.kParentAdded
    PARENT_REMOVED = om.MDagMessage.kParentRemoved
    CHILD_ADDED = om.MDagMessage.kChildAdded
    CHILD_REMOVED = om.MDagMessage.kChildRemoved
    CHILD_REORDERED = om.MDagMessage.kChildReordered
    INSTANCE_ADDED = om.MDagMessage.kInstanceAdded
    INSTANCE_REMOVED = om.MDagMessage.kInstanceRemoved


_DAG_MSG_TO_KIND: dict[int, DagChangeKind] = {
    member.value: member for member in DagChangeKind
}


@dataclass(frozen=True)
class DagChange:
    """One DAG-structure change. Delivered to on_dag_changed during drain.

    `child_handle` and `parent_handle` are MObjectHandles wrapping the
    nodes involved in the change. Either may be invalid by the time the
    drain runs (e.g. PARENT_REMOVED + immediate delete). Drain validates
    handles before delivery; invalid entries are skipped.
    """
    kind: DagChangeKind
    child_handle: om.MObjectHandle
    parent_handle: Optional[om.MObjectHandle] = None


@dataclass(frozen=True)
class RenameEvent:
    """One node rename. Delivered to on_renamed during drain.

    `old_name` is captured at callback time (Maya passes it as a
    parameter to addNameChangedCallback). `new_name` is read at drain
    time via MFnDependencyNode against the still-valid handle.
    """
    handle: om.MObjectHandle
    old_name: str


@dataclass(frozen=True)
class SubscriptionSet:
    """Declares which subscriptions a LiveBackend has registered.

    set_subscription_mode(set) atomically tears down the previous set's
    callbacks and registers callbacks for every flag set to True in the
    new set. Concrete EDIT / ANIMATION / PASSIVE sets are defined in
    Phase 6 when the Canvas needs them.
    """
    scene_lifecycle: bool = True   # always True in practice — kBeforeNew/kAfter*
    dg_node_added: bool = False
    dg_node_removed: bool = False
    dag_firehose: bool = False
    global_rename: bool = False
    selection: bool = False        # subscription path unimplemented in Phase 0
    node_type_filter: str = "dagNode"  # filter for dg_node_added/removed


class LiveBackend:
    """Maya scene-event subscription backend with debounced UI dispatch.

    Composition pattern — owned by a Qt host widget, not inherited. Hooks
    are callables passed at construction; the host wires them to its own
    methods.

    Lifecycle:
      backend = LiveBackend(on_nodes_added=..., ...)
      backend.start()                        # registers callbacks
      backend.set_subscription_mode(set)     # swap subscription set
      backend.stop()                         # removes all callbacks (idempotent)

    All hooks are invoked on the Qt main thread, after debounce, with
    stale handles already filtered. Hooks may call cmds.*, mutate Qt
    models, do heavy work — Maya is settled by hook-invocation time.
    """

    DEFAULT_DEBOUNCE_MS = 50

    def __init__(
        self,
        on_nodes_added: Optional[Callable[[list[om.MObject]], None]] = None,
        on_nodes_removed: Optional[Callable[[list[str]], None]] = None,
        on_dag_changed: Optional[Callable[[list[DagChange]], None]] = None,
        on_renamed: Optional[Callable[[list[RenameEvent]], None]] = None,
        on_scene_reset: Optional[Callable[[], None]] = None,
        debounce_ms: int = DEFAULT_DEBOUNCE_MS,
        debug: bool = False,
        logger=None,  # Optional LoggerWidget — see _log()
    ):
        self._on_nodes_added = on_nodes_added
        self._on_nodes_removed = on_nodes_removed
        self._on_dag_changed = on_dag_changed
        self._on_renamed = on_renamed
        self._on_scene_reset = on_scene_reset

        self._debug = debug
        self._logger = logger

        self._callback_ids: list[int] = []
        self._pending: list[tuple] = []  # (event_type, payload...) tuples
        self._active_set: Optional[SubscriptionSet] = None

        self._timer = QtCore.QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._drain)

    def start(self, subscription_set: Optional[SubscriptionSet] = None) -> None:
        """Register callbacks per `subscription_set`. Defaults to a set
        with scene_lifecycle=True only — host should call
        set_subscription_mode() to widen subscriptions for real use."""
        if self._active_set is not None:
            raise RuntimeError("LiveBackend.start() called while already started")
        self._active_set = subscription_set or SubscriptionSet()
        self._register_callbacks(self._active_set)
        self._log(f"start: registered {len(self._callback_ids)} callbacks")

    def set_subscription_mode(self, subscription_set: SubscriptionSet) -> None:
        """Atomic switch — tear down current subscription set, register
        the new set, fire on_scene_reset so the host rebuilds from
        current scene state. Drops any pending events queued under the
        old set (they may not make sense under the new set).

        Strict toggle — a backend is in exactly one mode at a time. If
        you need both 'edit + animation' subscriptions simultaneously,
        define a third SubscriptionSet that combines the flags.
        """
        if self._active_set is None:
            raise RuntimeError("set_subscription_mode() called before start()")

        # Teardown
        for cid in self._callback_ids:
            try:
                om.MMessage.removeCallback(cid)
            except RuntimeError:
                pass
        self._callback_ids.clear()
        self._pending.clear()
        self._timer.stop()

        # Register new
        self._active_set = subscription_set
        self._register_callbacks(subscription_set)
        self._log(f"set_subscription_mode: {len(self._callback_ids)} callbacks")

        # Queue scene_reset so host rebuilds from current state. We queue
        # rather than fire synchronously because callers may be inside a
        # cmds.* call stack (Phase 6 will trigger mode switches from rig
        # build/unbuild paths); routing through the drain preserves the
        # two-layer architecture's safety guarantees.
        self._queue_event('scene_reset')

    def stop(self) -> None:
        """Remove every registered callback. Idempotent — safe to call
        multiple times. The host widget MUST call this from closeEvent."""
        for cid in self._callback_ids:
            try:
                om.MMessage.removeCallback(cid)
            except RuntimeError:
                pass  # callback may already be gone (scene-new etc.)
        n = len(self._callback_ids)
        self._callback_ids.clear()
        self._pending.clear()
        self._timer.stop()
        self._active_set = None
        self._log(f"stop: removed {n} callbacks")

    def _register_callbacks(self, subs: SubscriptionSet) -> None:
        """Plug callbacks into Maya per `subs`. Each registration appends
        the returned callback ID to self._callback_ids."""
        if subs.scene_lifecycle:
            for ev_type in (om.MSceneMessage.kBeforeNew,
                            om.MSceneMessage.kBeforeOpen):
                cid = om.MSceneMessage.addCallback(ev_type, self._on_scene_before)
                self._callback_ids.append(cid)
            for ev_type in (om.MSceneMessage.kAfterNew,
                            om.MSceneMessage.kAfterOpen):
                cid = om.MSceneMessage.addCallback(ev_type, self._on_scene_after)
                self._callback_ids.append(cid)
        if subs.dg_node_added:
            cid = om.MDGMessage.addNodeAddedCallback(
                self._on_dg_added, subs.node_type_filter
            )
            self._callback_ids.append(cid)
        if subs.dg_node_removed:
            cid = om.MDGMessage.addNodeRemovedCallback(
                self._on_dg_removed, subs.node_type_filter
            )
            self._callback_ids.append(cid)
        if subs.dag_firehose:
            cid = om.MDagMessage.addAllDagChangesCallback(self._on_dag_firehose)
            self._callback_ids.append(cid)
        if subs.global_rename:
            null_obj = om.MObject()
            cid = om.MNodeMessage.addNameChangedCallback(
                null_obj, self._on_renamed_global
            )
            self._callback_ids.append(cid)

    def _on_scene_before(self, _client_data) -> None:
        """kBeforeNew / kBeforeOpen — scene about to be torn down.
        Drop pending events; per-node subscriptions (Phase 6) tear down
        here. Drain does not fire — host's on_scene_reset fires from
        the After handler."""
        self._pending.clear()
        self._timer.stop()
        # Phase 6: tear down per-node watch_node callbacks here.

    def _on_scene_after(self, _client_data) -> None:
        """kAfterNew / kAfterOpen — scene is rebuilt, fire on_scene_reset
        so the host can repopulate from current scene state."""
        self._queue_event('scene_reset')

    def _on_dg_added(self, mobj, _client_data) -> None:
        """addNodeAddedCallback — node of subs.node_type_filter just
        created. Wrap in MObjectHandle for safe deferred use."""
        try:
            handle = om.MObjectHandle(mobj)
        except Exception:
            return  # defensive; mobj should always be valid here
        self._queue_event('added', handle)

    def _on_dg_removed(self, mobj, _client_data) -> None:
        """addNodeRemovedCallback — node about to be deleted. Read UUID
        now (still readable); pass UUID string to drain since the
        MObject will be invalid by drain time."""
        try:
            uuid_str = om.MFnDependencyNode(mobj).uuid().asString()
        except Exception:
            return  # node may be in an unreadable state
        self._queue_event('removed', uuid_str)

    def _on_dag_firehose(self, msg_type, child_dag, parent_dag, _client_data) -> None:
        """addAllDagChangesCallback — every DAG-structure change."""
        kind = _DAG_MSG_TO_KIND.get(msg_type, DagChangeKind.INVALID)
        if kind is DagChangeKind.INVALID:
            return  # unknown msg type; skip rather than crash drain

        try:
            child_handle = om.MObjectHandle(child_dag.node())
        except Exception:
            return

        parent_handle: Optional[om.MObjectHandle] = None
        if parent_dag is not None:
            try:
                parent_mobj = parent_dag.node()
                if not parent_mobj.isNull():
                    parent_handle = om.MObjectHandle(parent_mobj)
            except Exception:
                pass

        self._queue_event('dag', kind, child_handle, parent_handle)

    def _on_renamed_global(self, mobj, old_name, _client_data) -> None:
        """MNodeMessage.addNameChangedCallback wildcard form (null MObject)
        fires for every node rename in the scene."""
        try:
            handle = om.MObjectHandle(mobj)
        except Exception:
            return
        self._queue_event('renamed', handle, old_name)

    def _drain(self) -> None:
        """Drain self._pending (Qt main thread). For each queued event:
        - validate handles (skip stale)
        - dedupe by (event_type, hashCode) within this drain
        - group by event_type
        - invoke the corresponding hook with the batch

        Short-circuit: if 'scene_reset' is queued, drop all other events
        and fire only on_scene_reset. The host rebuilds from scratch, so
        firing incremental hooks first would feed it stale handles from
        the pre-reset scene.
        """
        if not self._pending:
            return

        events = self._pending
        self._pending = []

        self._log(f"drain: {len(events)} events")

        if any(e[0] == 'scene_reset' for e in events):
            if self._on_scene_reset is not None:
                self._on_scene_reset()
            return

        added_handles: list[om.MObjectHandle] = []
        removed_uuids: list[str] = []
        dag_changes: list[DagChange] = []
        rename_events: list[RenameEvent] = []

        seen_added: set[int] = set()
        seen_removed: set[str] = set()
        seen_dag: set[tuple] = set()
        seen_rename: set[int] = set()

        for ev in events:
            tag = ev[0]
            if tag == 'added':
                handle: om.MObjectHandle = ev[1]
                if not (handle.isValid() and handle.isAlive()):
                    continue
                hc = handle.hashCode()
                if hc in seen_added:
                    continue
                seen_added.add(hc)
                added_handles.append(handle)
            elif tag == 'removed':
                uuid_str: str = ev[1]
                if uuid_str in seen_removed:
                    continue
                seen_removed.add(uuid_str)
                removed_uuids.append(uuid_str)
            elif tag == 'dag':
                kind: DagChangeKind = ev[1]
                child_h: om.MObjectHandle = ev[2]
                parent_h: Optional[om.MObjectHandle] = ev[3]
                if not (child_h.isValid() and child_h.isAlive()):
                    continue
                key = (kind, child_h.hashCode(),
                       parent_h.hashCode() if parent_h is not None else 0)
                if key in seen_dag:
                    continue
                seen_dag.add(key)
                dag_changes.append(DagChange(kind=kind,
                                             child_handle=child_h,
                                             parent_handle=parent_h))
            elif tag == 'renamed':
                handle = ev[1]
                old_name: str = ev[2]
                if not (handle.isValid() and handle.isAlive()):
                    continue
                hc = handle.hashCode()
                if hc in seen_rename:
                    continue
                seen_rename.add(hc)
                rename_events.append(RenameEvent(handle=handle, old_name=old_name))
            # 'scene_reset' is handled by the short-circuit at the top
            # of _drain; if we reach here with that tag, it's a logic
            # error — drop silently rather than crash the drain.

        # Deduplicate: a single node creation fires both DG node_added
        # AND DAG firehose (CHILD_ADDED). The host shouldn't see both.
        # Drop DAG-only entries whose handle is also in added_handles.
        if added_handles and dag_changes:
            added_hcs = {h.hashCode() for h in added_handles}
            dag_changes = [
                dc for dc in dag_changes
                if not (dc.kind in (DagChangeKind.CHILD_ADDED,
                                    DagChangeKind.PARENT_ADDED)
                        and dc.child_handle.hashCode() in added_hcs)
            ]

        if added_handles and self._on_nodes_added is not None:
            self._on_nodes_added([h.object() for h in added_handles])
        if removed_uuids and self._on_nodes_removed is not None:
            self._on_nodes_removed(removed_uuids)
        if dag_changes and self._on_dag_changed is not None:
            self._on_dag_changed(dag_changes)
        if rename_events and self._on_renamed is not None:
            self._on_renamed(rename_events)

    def _queue_event(self, *event_tuple) -> None:
        """Append an event tuple to the queue, restart the debounce timer.
        Safe to call from any callback context — including file IO callbacks
        that may fire on a non-main thread (per the LiveUI spec safety
        rule §2). The timer.start() is dispatched via QueuedConnection so
        it crosses the thread boundary safely; the actual restart happens
        on the Qt main thread that owns _timer."""
        self._pending.append(event_tuple)
        QtCore.QMetaObject.invokeMethod(
            self._timer, "start", QtCore.Qt.ConnectionType.QueuedConnection
        )

    def _log(self, msg: str) -> None:
        """Debug log to LoggerWidget if provided + debug flag is on."""
        if not self._debug:
            return
        if self._logger is not None:
            try:
                self._logger.info(f"[LiveBackend] {msg}")
            except Exception:
                pass
        else:
            print(f"[LiveBackend] {msg}")
