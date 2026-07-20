# maya_tools/utils/maya/live_tool_demo.py
"""LiveTool demo widget — permanent Phase 0 regression surface.

Bare QTreeWidget projecting the scene's transform hierarchy via a
LiveBackend. UUID-keyed rows. Manual Refresh button (force full
rebuild) + fake Switch Mode button (exercises set_subscription_mode
in the toy before Canvas adopts it in Phase 6).

Scope: every transform in the scene is shown — joints, groups, AND
the default cameras (persp/top/front/side and their shape transforms).
That's by design. The demo's job is to validate that LiveBackend
captures every dagNode the broad subscription set reaches, not to
curate an outliner. The Phase 1 Canvas filters to KS-managed joints
only, which is the right behavior for that consumer.

Running this widget through the Phase 0 test matrix is the gate that
qualifies LiveTool for Phase 1 dependence. Don't delete this file
when later phases ship — when Maya 2028 changes something, this is
the first thing to run.
"""
__author__ = "Adrian Melian"

import traceback

import maya.cmds as cmds
import maya.api.OpenMaya as om

from PySide6 import QtCore, QtGui, QtWidgets

from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.maya.live_tool import (
    DagChange,
    DagChangeKind,
    LiveBackend,
    RenameEvent,
    SubscriptionSet,
)
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget

_win: 'LiveToolDemoWindow | None' = None


def show() -> 'LiveToolDemoWindow':
    """Project-standard launch pattern — force-close any prior instance,
    create fresh. Avoids Qt-show-on-hidden lookup bugs that bit
    Curve-O-Matic before this pattern landed."""
    global _win
    if _win is not None:
        try:
            _win.close()
            _win.deleteLater()
        except RuntimeError:
            pass
        _win = None
    _win = LiveToolDemoWindow(parent=get_maya_window())
    _win.show()
    return _win


class LiveToolDemoWindow(QtWidgets.QDialog):
    """Live scene-tree projection. Validates LiveBackend safety contract."""

    WINDOW_TITLE = 'LiveTool Demo'
    OBJECT_NAME = 'LiveToolDemoWindow'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(self.OBJECT_NAME)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setMinimumSize(420, 600)

        # UUID -> QTreeWidgetItem map. UUID is durable in-session and
        # available at both add (via MFnDependencyNode) and remove (via
        # the LiveBackend's removed-uuids contract) time.
        self._items: dict[str, QtWidgets.QTreeWidgetItem] = {}

        # Two arbitrary subscription sets for the fake mode-switch button.
        # Phase 0 doesn't define EDIT/ANIMATION/PASSIVE — those land in
        # Phase 6. These two sets are toys to exercise the switch path.
        self._mode_a = SubscriptionSet(
            scene_lifecycle=True, dg_node_added=True,
            dg_node_removed=True, dag_firehose=True, global_rename=True,
        )
        self._mode_b = SubscriptionSet(
            scene_lifecycle=True, dg_node_added=True, dg_node_removed=True,
            # firehose + rename OFF — observable difference
        )
        self._current_mode = 'A'

        self._build_ui()
        self._build_backend()
        self._populate_from_scene()

    def _build_ui(self) -> None:
        """Tree, toolbar, log section."""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        toolbar = QtWidgets.QHBoxLayout()
        self._refresh_btn = QtWidgets.QPushButton('Refresh')
        self._refresh_btn.setToolTip('Force full rebuild from scene state')
        self._switch_btn = QtWidgets.QPushButton('Switch Mode (A)')
        self._switch_btn.setToolTip('Toggle SubscriptionSet — exercises set_subscription_mode()')
        self._cb_count_label = QtWidgets.QLabel('callbacks: 0')
        toolbar.addWidget(self._refresh_btn)
        toolbar.addWidget(self._switch_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self._cb_count_label)
        layout.addLayout(toolbar)

        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderLabels(['Node', 'Type', 'UUID'])
        self._tree.setColumnWidth(0, 240)
        layout.addWidget(self._tree, stretch=1)

        self._logger = LoggerWidget()
        log_section = CollapsibleSection('Drain Log')
        log_layout = QtWidgets.QVBoxLayout()
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addWidget(self._logger)
        log_section.set_body_layout(log_layout)
        log_section.set_expanded(True)
        layout.addWidget(log_section)

        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        self._switch_btn.clicked.connect(self._on_switch_clicked)

    def _build_backend(self) -> None:
        """Construct LiveBackend with hooks bound to widget methods."""
        self._backend = LiveBackend(
            on_nodes_added=self._on_nodes_added,
            on_nodes_removed=self._on_nodes_removed,
            on_dag_changed=self._on_dag_changed,
            on_renamed=self._on_renamed,
            on_scene_reset=self._on_scene_reset,
            debug=True,
            logger=self._logger,
        )
        self._backend.start(self._mode_a)
        self._update_cb_count()

    # Hook + button handlers — Tasks 11-13.
    def _populate_from_scene(self) -> None:
        """Walk every transform in the scene, build a row keyed by UUID,
        attach as child of its DAG parent. Idempotent — clears tree first."""
        self._tree.clear()
        self._items.clear()

        all_transforms = cmds.ls(type='transform', long=True) or []
        if not all_transforms:
            return

        # Pre-resolve UUIDs and parents in a single pass
        rows = []
        for full_path in all_transforms:
            short = full_path.rsplit('|', 1)[-1]
            try:
                uuid_str = cmds.ls(full_path, uuid=True)[0]
            except Exception:
                continue
            type_str = cmds.nodeType(full_path)
            par_list = cmds.listRelatives(full_path, parent=True, fullPath=True) or []
            parent_path = par_list[0] if par_list else None
            rows.append((full_path, short, uuid_str, type_str, parent_path))

        # Build items in DAG order — parents before children. cmds.ls
        # returns DG-creation order (NOT DAG-traversal order), so a scene
        # populated by Skeleton IO etc. can have children listed before
        # their parents. Depth-sort by '|' count guarantees parent-first.
        rows.sort(key=lambda r: r[0].count('|'))
        for full_path, short, uuid_str, type_str, parent_path in rows:
            self._add_row(short, uuid_str, type_str, parent_path)

    def _add_row(self, name: str, uuid_str: str, type_str: str,
                 parent_path: str | None) -> QtWidgets.QTreeWidgetItem:
        """Add one tree row keyed by UUID under the correct parent."""
        item = QtWidgets.QTreeWidgetItem([name, type_str, uuid_str])
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, uuid_str)
        if parent_path:
            parent_uuid_list = cmds.ls(parent_path, uuid=True)
            if parent_uuid_list:
                parent_item = self._items.get(parent_uuid_list[0])
                if parent_item is not None:
                    parent_item.addChild(item)
                else:
                    self._tree.addTopLevelItem(item)
            else:
                self._tree.addTopLevelItem(item)
        else:
            self._tree.addTopLevelItem(item)
        self._items[uuid_str] = item
        return item

    def _remove_row(self, uuid_str: str) -> None:
        """Remove the row keyed by UUID. Children of the removed row
        become children of its parent (Maya re-parents on delete; our
        firehose handler re-renders if needed)."""
        item = self._items.pop(uuid_str, None)
        if item is None:
            return
        parent = item.parent()
        if parent is not None:
            parent.removeChild(item)
        else:
            idx = self._tree.indexOfTopLevelItem(item)
            if idx >= 0:
                self._tree.takeTopLevelItem(idx)

    def _on_nodes_added(self, mobjs: list[om.MObject]) -> None:
        """LiveBackend drain — list[MObject] of newly-added nodes
        (filtered to dagNode by SubscriptionSet.node_type_filter).

        Maya fires addNodeAddedCallback in the order it creates nodes,
        which for cmds.duplicate of a chain is NOT guaranteed to be
        parent-before-child. We depth-sort the batch by full-path
        component count before calling _add_row so a parent's row always
        lands in self._items before any child of that parent looks it
        up. Without this, a child processed first falls through to
        addTopLevelItem and ends up flat at the tree root."""
        try:
            # Pre-resolve metadata so we can sort by DAG depth
            records = []
            for mobj in mobjs:
                # Only mirror transforms (joints are transforms too).
                # The default node_type_filter='dagNode' matches transforms
                # AND shape nodes; we want transforms only.
                if not mobj.hasFn(om.MFn.kTransform):
                    continue
                fn = om.MFnDagNode(mobj)
                full_path = fn.fullPathName()
                short = full_path.rsplit('|', 1)[-1]
                uuid_str = om.MFnDependencyNode(mobj).uuid().asString()
                type_str = mobj.apiTypeStr
                par_list = cmds.listRelatives(full_path, parent=True, fullPath=True) or []
                parent_path = par_list[0] if par_list else None
                records.append((full_path, short, uuid_str, type_str, parent_path))

            # Sort by DAG depth — '|' count in the full path. Roots come
            # first, deepest descendants last. Stable sort preserves
            # callback-fire order within a depth tier (ties).
            records.sort(key=lambda r: r[0].count('|'))

            for _full_path, short, uuid_str, type_str, parent_path in records:
                self._add_row(short, uuid_str, type_str, parent_path)
            self._update_cb_count()
        except Exception as e:
            self._handle_error(e, brief='_on_nodes_added failed')

    def _on_nodes_removed(self, uuids: list[str]) -> None:
        """LiveBackend drain — list[str] of UUIDs for nodes that have
        been deleted (already gone from the scene by the time this fires)."""
        try:
            for uuid_str in uuids:
                self._remove_row(uuid_str)
            self._update_cb_count()
        except Exception as e:
            self._handle_error(e, brief='_on_nodes_removed failed')

    def _on_dag_changed(self, changes: list[DagChange]) -> None:
        """LiveBackend drain — list[DagChange] of structural changes.
        For Phase 0 we handle reparent by rebuilding affected branches.
        Fully optimised incremental updates land in Phase 1 for the real
        canvas; the demo is content with full repopulate on reparent."""
        try:
            structural_kinds = {
                DagChangeKind.PARENT_ADDED, DagChangeKind.PARENT_REMOVED,
                DagChangeKind.CHILD_ADDED, DagChangeKind.CHILD_REMOVED,
                DagChangeKind.CHILD_REORDERED,
            }
            if any(c.kind in structural_kinds for c in changes):
                # Pragmatic Phase 0 strategy: rebuild on any structural
                # change. The Phase 0 test matrix only validates that
                # the tree is correct, not the surgical-update cost.
                self._populate_from_scene()
            self._update_cb_count()
        except Exception as e:
            self._handle_error(e, brief='_on_dag_changed failed')

    def _on_renamed(self, events: list[RenameEvent]) -> None:
        """LiveBackend drain — list[RenameEvent]. Look up by UUID, update
        the row's display column."""
        try:
            for ev in events:
                if not (ev.handle.isValid() and ev.handle.isAlive()):
                    continue
                mobj = ev.handle.object()
                uuid_str = om.MFnDependencyNode(mobj).uuid().asString()
                item = self._items.get(uuid_str)
                if item is None:
                    continue
                fn = om.MFnDagNode(mobj) if mobj.hasFn(om.MFn.kDagNode) else om.MFnDependencyNode(mobj)
                new_short = fn.name() if not mobj.hasFn(om.MFn.kDagNode) else fn.fullPathName().rsplit('|', 1)[-1]
                item.setText(0, new_short)
            self._update_cb_count()
        except Exception as e:
            self._handle_error(e, brief='_on_renamed failed')

    def _on_scene_reset(self) -> None:
        """LiveBackend drain — fired on kAfterNew / kAfterOpen and on
        every set_subscription_mode() switch. Rebuild from current
        scene state."""
        try:
            self._populate_from_scene()
            self._update_cb_count()
        except Exception as e:
            self._handle_error(e, brief='_on_scene_reset failed')

    def _on_refresh_clicked(self) -> None:
        """Force full rebuild — diagnostic separator for 'is this a
        live-update bug or a real scene-state bug?'. Always available."""
        try:
            self._populate_from_scene()
            self._update_cb_count()
            self._logger.info("[Demo] Refresh: rebuilt from scene state")
        except Exception as e:
            self._handle_error(e, brief='Refresh failed')

    def _on_switch_clicked(self) -> None:
        """Toggle between mode A (full subscriptions) and mode B (no
        firehose, no rename) — exercises set_subscription_mode() and
        on_scene_reset re-fire. Phase 0's load-bearing path for Pattern 5."""
        try:
            if self._current_mode == 'A':
                self._backend.set_subscription_mode(self._mode_b)
                self._current_mode = 'B'
                self._switch_btn.setText('Switch Mode (B)')
                self._logger.info("[Demo] Mode B — no firehose, no rename")
            else:
                self._backend.set_subscription_mode(self._mode_a)
                self._current_mode = 'A'
                self._switch_btn.setText('Switch Mode (A)')
                self._logger.info("[Demo] Mode A — full subscriptions")
            self._update_cb_count()
        except Exception as e:
            self._handle_error(e, brief='Switch Mode failed')

    def _update_cb_count(self) -> None:
        """Toolbar label reflects active callback count. Updated on every
        backend state change."""
        count = len(self._backend._callback_ids)  # noqa: SLF001 — debug-only
        self._cb_count_label.setText(f'callbacks: {count}')

    def closeEvent(self, ev: QtGui.QCloseEvent) -> None:
        """Mandatory teardown — remove every callback, log final count."""
        try:
            self._backend.stop()
        except Exception as e:
            self._handle_error(e, brief='stop() failed')
        super().closeEvent(ev)

    def _handle_error(self, e: Exception, brief: str | None = None) -> None:
        """Project-standard error reporting — full trace to Script Editor,
        brief one-line to logger."""
        traceback.print_exc()
        msg = brief or str(e)
        try:
            self._logger.error(f"[LiveToolDemo] {msg}")
        except Exception:
            pass
