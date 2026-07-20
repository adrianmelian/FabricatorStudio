# maya_tools/rigging/fabricator/armature_watch.py
"""DAG watchers — native Maya gestures on Armature ctrls become real
Fabricator branch ops (Adrian's 2026-07-05 designs).

Two gestures, one module:
  * PARENT — ctrl lands under another ctrl ("they select the clav
    controller, they shift select the spine3 controller and press p,
    we somehow intercept that"): the native parent is undone and the
    underlying joints go through branch_ops.reparent_branches
    (teardown → surgery → rebuild).
  * DELETE — ctrl(s) deleted ("if the control was deleted, delete
    everything associated with that joint, including the children and
    their modules"): the native delete is undone and the branches go
    through branch_ops.delete_branches (joints + subtree + aimers +
    modules, one sandwich).

Neither is a hotkey override. Maya hotkeys are global session state,
and p / Delete are each only one road to the same damage — Edit >
Parent, outliner drags, Edit > Delete, MEL all do it too. The DAG
callbacks (MDagMessage.addParentAddedCallback,
MDGMessage.addNodeRemovedCallback) catch every route, so the Armature
cannot be corrupted by native gestures on its ctrls.

Mechanics:
  * Events accumulate and flush ONCE per idle cycle via evalDeferred —
    a multi-select gesture (clav_l + clav_r under spine3; three ctrls
    deleted at once) is one command and must become one native undo +
    one sandwich. A native delete of a ctrl cascades to its whole ctrl
    subtree, so delete events are reduced to their TOPMOST joints
    before the op runs.
  * The native command is undone only when it tops the undo stack
    under the expected name ('parent' / 'delete'); when in doubt it
    stays — the sandwich alone still yields the right scene (teardown
    discards ctrl wreckage wholesale), at the cost of one extra undo
    step.
  * suspended() mutes the watchers while Fabricator's own code runs:
    build_armature / delete_armature parent AND delete ctrls
    constantly (armature.py wraps itself, so every op is quiet).
  * Undo/redo replay is ignored (MGlobal.isUndoing/isRedoing): undoing
    a fixup restores or removes ctrls node by node, and without the
    guard those would re-trigger the fixup and make gestures
    impossible to undo.
  * Scene teardown is ignored (MFileIO.isNewingFile/isOpeningFile):
    File > New deletes every ctrl in the scene and must not read as a
    user branch delete. Deleting the armature GROUP itself is also
    ignored — that gesture means "remove the armature", not "delete
    every branch".
  * Cycles cannot arrive through the parent door: the ctrl tree
    mirrors the joint tree one to one, so parenting an ancestor ctrl
    under its descendant is refused by Maya before any event fires.
    reparent_branches keeps its own guard for other entry points.

Lifecycle: FSWindow installs on open and removes in closeEvent (the
same failsafe block as Symmetry). set_reporter routes results into the
window logger; set_refresher lets the window repaint the canvas after
a watcher-driven op. Events referencing nodes that no longer exist at
flush time are dropped silently.
"""
__author__ = "Adrian Melian"

import contextlib
import traceback

import maya.cmds as cmds
import maya.api.OpenMaya as om

_CTRL_SUFFIX = '_amt_CTL'
_ARMATURE_GRP = 'fab_armature_grp'   # mirrors armature._GRP

_STATE = {
    'parent_cb_id': None,
    'delete_cb_id': None,
    'pending_parents': [],   # [(child_ctrl_short, parent_ctrl_short)]
    'pending_deletes': [],   # [ctrl_short]
    'grp_deleted': False,    # armature grp seen in this idle's deletes
    'flush_scheduled': False,
    'suspend_depth': 0,
    'reporter': None,        # callable(str) — FSWindow logger hook
    'refresher': None,       # callable() — FSWindow canvas repaint hook
}


# ─────────────────────────────────────────────
# Queries / host hooks
# ─────────────────────────────────────────────

def is_installed() -> bool:
    return _STATE['parent_cb_id'] is not None


def set_reporter(fn) -> None:
    """Route flush results into a UI logger; None falls back to
    MGlobal.displayInfo/Warning (script editor + command line)."""
    _STATE['reporter'] = fn


def set_refresher(fn) -> None:
    """Called after a watcher-driven op completes so the host can
    repaint (canvas rows, Symmetry button state). None = no-op."""
    _STATE['refresher'] = fn


def _report(msg: str, warn: bool = False) -> None:
    fn = _STATE['reporter']
    if fn is not None:
        try:
            fn(msg)
            return
        except Exception:
            pass
    if warn:
        om.MGlobal.displayWarning(msg)
    else:
        om.MGlobal.displayInfo(msg)


def _refresh() -> None:
    fn = _STATE['refresher']
    if fn is not None:
        try:
            fn()
        except Exception:
            traceback.print_exc()


# ─────────────────────────────────────────────
# Suspension (Fabricator's own builds)
# ─────────────────────────────────────────────

@contextlib.contextmanager
def suspended():
    """Mute the watchers for the duration — nestable (depth-counted)."""
    _STATE['suspend_depth'] += 1
    try:
        yield
    finally:
        _STATE['suspend_depth'] = max(0, _STATE['suspend_depth'] - 1)


# ─────────────────────────────────────────────
# Shared guards
# ─────────────────────────────────────────────

def _short(path_name: str) -> str:
    return path_name.split('|')[-1].split(':')[-1]


def _muted() -> bool:
    """True when events must be ignored: our own ops, undo/redo
    replay, or scene teardown (File > New/Open)."""
    if _STATE['suspend_depth'] > 0:
        return True
    try:
        if om.MGlobal.isUndoing() or om.MGlobal.isRedoing():
            return True
    except AttributeError:
        pass
    try:
        if om.MFileIO.isNewingFile() or om.MFileIO.isOpeningFile():
            return True
    except AttributeError:
        pass
    return False


def is_muted() -> bool:
    """Public wrapper for _muted() — the shared suspend/undo/redo/
    scene-teardown guard. Reused by other Armature live-update hooks
    (the follow-rule live-evaluate hook in armature.py, Task 1.2) so a
    single suspended() call here mutes every Armature watcher at once,
    including ones this module doesn't itself own."""
    return _muted()


def _schedule_flush() -> None:
    if not _STATE['flush_scheduled']:
        _STATE['flush_scheduled'] = True
        # Out of the DAG callback before touching the scene — editing
        # the graph mid-callback is undefined-behavior territory.
        cmds.evalDeferred(_flush)


def _native_on_top(keyword: str) -> bool:
    """True when the last undoable entry looks like the native command
    that raised our events ('parent' / 'delete')."""
    try:
        name = cmds.undoInfo(q=True, undoName=True) or ''
    except Exception:
        return False
    return keyword in name.lower()


# ─────────────────────────────────────────────
# Callbacks
# ─────────────────────────────────────────────

def _on_parent_added(child: om.MDagPath, parent: om.MDagPath, *_):
    if _muted():
        return
    try:
        c = _short(child.fullPathName())
        p = _short(parent.fullPathName())
    except Exception:
        return
    if not (c.endswith(_CTRL_SUFFIX) and p.endswith(_CTRL_SUFFIX)):
        return
    _STATE['pending_parents'].append((c, p))
    _schedule_flush()


def _on_node_removed(node: om.MObject, *_):
    if _muted():
        return
    try:
        name = _short(om.MFnDependencyNode(node).name())
    except Exception:
        return
    if name == _ARMATURE_GRP:
        # Deleting the group is an armature-teardown gesture, not a
        # branch delete — poison this idle's delete batch.
        _STATE['grp_deleted'] = True
        _schedule_flush()
        return
    if not name.endswith(_CTRL_SUFFIX):
        return
    _STATE['pending_deletes'].append(name)
    _schedule_flush()


# ─────────────────────────────────────────────
# Flush
# ─────────────────────────────────────────────

def _flush():
    _STATE['flush_scheduled'] = False
    parents, _STATE['pending_parents'] = list(_STATE['pending_parents']), []
    deletes, _STATE['pending_deletes'] = list(_STATE['pending_deletes']), []
    grp_deleted, _STATE['grp_deleted'] = _STATE['grp_deleted'], False
    if _STATE['suspend_depth'] > 0:
        return

    if grp_deleted and deletes:
        _report('Armature group deleted natively — treated as a '
                'teardown, branches untouched. Any Fabricator op '
                'rebuilds it.', warn=True)
        deletes = []

    did_work = False
    if deletes:
        did_work = _fixup_deletes(deletes) or did_work
    if parents:
        did_work = _fixup_parents(parents) or did_work
    if did_work:
        _refresh()


def _fixup_deletes(ctrl_names: list) -> bool:
    """Ctrl delete → branch delete. The native delete killed the ctrl
    subtree only — joints survive, so the mapping still resolves."""
    joints = []
    for c in dict.fromkeys(ctrl_names):        # dedupe, keep order
        j = c[:-len(_CTRL_SUFFIX)]
        if cmds.objExists(j):
            joints.append(j)
    if not joints:
        return False

    # One gesture = one native undo entry; withdraw it so the sandwich
    # starts from an intact armature (and one Ctrl+Z restores all).
    if _native_on_top('delete'):
        try:
            cmds.undo()
        except Exception:
            pass

    roots = _topmost(joints)
    try:
        from maya_tools.rigging.fabricator import branch_ops
        from maya_tools.rigging.joint_orient import (
            joint_orient_app as joa,
        )
        with joa.undo_chunk('DeleteLimb'):
            result = branch_ops.delete_branches(roots)
        _report(f"Delete: removed {', '.join(result['roots'])} — "
                f"{result['joints']} joint(s), "
                f"{result['components']} module(s).")
        return True
    except Exception as e:
        traceback.print_exc()
        _report(f'Limb delete failed: {e}', warn=True)
        return False


def _fixup_parents(events: list) -> bool:
    """Ctrl-under-ctrl parent → branch reparent."""
    moves = []
    seen = set()
    for c, p in events:
        pair = (c[:-len(_CTRL_SUFFIX)], p[:-len(_CTRL_SUFFIX)])
        if pair in seen:
            continue
        seen.add(pair)
        if cmds.objExists(pair[0]) and cmds.objExists(pair[1]):
            moves.append(pair)
    if not moves:
        return False

    if _native_on_top('parent'):
        try:
            cmds.undo()
        except Exception:
            pass

    # One command parents everything under a single target; group by
    # target anyway so an exotic multi-target burst still resolves.
    by_target = {}
    for root, target in moves:
        by_target.setdefault(target, []).append(root)

    try:
        from maya_tools.rigging.fabricator import branch_ops
        from maya_tools.rigging.joint_orient import (
            joint_orient_app as joa,
        )
        with joa.undo_chunk('ReparentBranch'):
            moved_all = []
            for target, roots in by_target.items():
                result = branch_ops.reparent_branches(roots, target)
                if result['moved']:
                    moved_all.append(
                        f"{', '.join(result['moved'])} → {target}")
        if moved_all:
            _report(f"Reparent: {'; '.join(moved_all)} "
                    f"(ctrls selected).")
            return True
        return False
    except Exception as e:
        traceback.print_exc()
        _report(f'Reparent failed: {e}', warn=True)
        return False


def _topmost(joints: list) -> list:
    """Reduce to joints with no ancestor in the set — a native ctrl
    delete cascades to the whole ctrl subtree, so every descendant
    fires its own event; only the top of each island is a branch
    root."""
    longs = {j: cmds.ls(j, long=True)[0] for j in joints}
    keep = []
    for j in sorted(joints, key=lambda x: longs[x].count('|')):
        if not any(longs[j].startswith(longs[k] + '|') for k in keep):
            keep.append(j)
    return keep


# ─────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────

def install() -> None:
    """Register both DAG watchers. Idempotent."""
    if _STATE['parent_cb_id'] is None:
        _STATE['parent_cb_id'] = om.MDagMessage.addParentAddedCallback(
            _on_parent_added)
    if _STATE['delete_cb_id'] is None:
        # 'transform' scopes the firehose — ctrls (and the grp) are
        # transforms; name filtering does the rest.
        _STATE['delete_cb_id'] = om.MDGMessage.addNodeRemovedCallback(
            _on_node_removed, 'transform')


def remove() -> None:
    """Drop both DAG watchers and any pending events."""
    for key in ('parent_cb_id', 'delete_cb_id'):
        if _STATE[key] is not None:
            try:
                om.MMessage.removeCallback(_STATE[key])
            except Exception:
                pass
            _STATE[key] = None
    _STATE['pending_parents'] = []
    _STATE['pending_deletes'] = []
    _STATE['grp_deleted'] = False
    _STATE['flush_scheduled'] = False
