"""Skeleton creation/editing helpers."""
__author__ = "Adrian Melian"

import re

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.framework.decorators import undo_chunk


_TRAILING_DIGITS = re.compile(r'^(.*?)(\d+)$')

# A new joint copies its parent's local translate, so a parent carrying no
# offset of its own (the root at origin, overwhelmingly) would drop the child
# exactly on top of it: invisible, unpickable, and zero bone length. Step up
# in local Y instead so the joint is visible and grabbable the moment it
# exists (Adrian, 2026-07-18).
_COINCIDENT_EPS = 1e-6
_ZERO_OFFSET_FALLBACK_Y = 5.0


def create_joint() -> str:
    """Create a joint based on current selection.

    - No joint in selection: create a free joint at world origin.
    - One or more joints in selection: create a child of the LAST selected joint,
      copying its local translate and radius. New joint is named by incrementing
      the parent's trailing numeric suffix (or appending '_01' if none).

    The new joint is left selected so repeated Alt+J presses build a chain.

    Returns:
        str: The new joint's name.
    """
    with undo_chunk("Create Joint"):
        sel = cmds.ls(sl=True, long=False) or []
        joints_in_sel = [s for s in sel if cmds.nodeType(s) == 'joint']

        if not joints_in_sel:
            cmds.select(clear=True)
            new_jnt = cmds.createNode('joint', name='joint1')
            cmds.select(new_jnt, replace=True)
            return new_jnt

        return create_joint_child(parent=joints_in_sel[-1])


def create_joint_child(parent: str, name: str | None = None) -> str:
    """Create a child joint under `parent`, copying parent's local translate and radius.

    When the parent's local translate is (near) zero, copying it would land the
    new joint exactly on top of the parent, so it steps up
    `_ZERO_OFFSET_FALLBACK_Y` in local Y instead. That is the root-at-origin
    case in practice; local Y is world up for an unrotated parent, and follows
    the parent's own frame for an oriented one, which is what a chain wants.

    Args:
        parent: Name of an existing joint to parent the new joint under.
        name:   Optional explicit name. If omitted, the parent's trailing numeric
                suffix is incremented (or '_01' is appended if there is none),
                with collision auto-bumping.

    Returns:
        str: The new joint's name. New joint is left selected.
    """
    if not cmds.objExists(parent):
        raise RuntimeError(f"Parent joint '{parent}' does not exist.")
    if cmds.nodeType(parent) != 'joint':
        raise RuntimeError(f"Parent '{parent}' is not a joint.")

    if name is None:
        name = _next_joint_name(parent)

    with undo_chunk("Create Joint Child"):
        new_jnt = cmds.createNode('joint', name=name, parent=parent)

        offset = [cmds.getAttr(f'{parent}.{axis}') for axis in ('tx', 'ty', 'tz')]
        if max(abs(v) for v in offset) <= _COINCIDENT_EPS:
            offset = [0.0, _ZERO_OFFSET_FALLBACK_Y, 0.0]
        for axis, value in zip(('tx', 'ty', 'tz'), offset):
            cmds.setAttr(f'{new_jnt}.{axis}', value)
        try:
            cmds.setAttr(f'{new_jnt}.radius', cmds.getAttr(f'{parent}.radius'))
        except Exception:
            pass

        cmds.select(new_jnt, replace=True)
    return new_jnt


def create_joint_chain(start_joint=None, end_joint=None, num_joints=None):
    """Create a joint chain between two given joints and position each new joint equally distributed between them."""
    if not start_joint:
        selected = cmds.ls(sl=True) or []
        if not selected:
            raise RuntimeError("No joint selected.")
        if not cmds.nodeType(selected[0]) == 'joint':
            raise RuntimeError("Selected object is not a joint.")
        start_joint = selected[0]
    if not cmds.objExists(start_joint):
        raise RuntimeError(f"Start joint '{start_joint}' does not exist.")
    if not cmds.nodeType(start_joint) == 'joint':
        raise RuntimeError(f"Start '{start_joint}' is not a joint.")
    if not end_joint:
        selected = cmds.ls(sl=True) or []
        if len(selected) < 2:
            raise RuntimeError("No end joint selected.")
        if not cmds.nodeType(selected[1]) == 'joint':
            raise RuntimeError("Selected object is not a joint.")
        end_joint = selected[1]
    if not cmds.objExists(end_joint):
        raise RuntimeError(f"End joint '{end_joint}' does not exist.")
    if not num_joints:
        num_joints = cmds.promptDialog(title="Number of Joints", message="Enter the number of joints to create:", button=["OK", "Cancel"], defaultButton="OK", dismissString="Cancel", cancelButton="Cancel")
        if num_joints == "Cancel":
            raise RuntimeError("Operation cancelled.")
        num_joints = int(cmds.promptDialog(query=True, text=True))

    with undo_chunk("Create Joint Chain"):
        cmds.select(clear=True)

        start_pos = om.MVector(*cmds.xform(start_joint, q=True, ws=True, t=True))
        end_pos = om.MVector(*cmds.xform(end_joint, q=True, ws=True, t=True))
        new_joints = []

        total = num_joints + 1
        for i in range(total + 1):
            t = float(i) / total
            pos = start_pos + (end_pos - start_pos) * t
            name = f"{start_joint}_mid_{i}" if 0 < i < total else f'{start_joint}_temp' if i == 0 else f'{end_joint}_temp'
            jnt = cmds.joint(name=name)
            cmds.xform(jnt, ws=True, ro=cmds.xform(start_joint, q=True, ws=True, ro=True))
            cmds.xform(jnt, ws=True, t=pos)
            new_joints.append(jnt)

        cmds.parent(new_joints[1], start_joint)
        cmds.parent(end_joint, new_joints[-2])
        cmds.delete(new_joints[-1])
        cmds.delete(new_joints[0])


def resolve_ancestor_descendant(joint_a: str, joint_b: str):
    """Return (ancestor, descendant) for two joints, or (None, None) if
    neither is an ancestor of the other.

    Walks the parent chain from each joint up to the world; the first
    joint where the other is found in the walk is the ancestor.
    """
    def walk_parents(start: str):
        chain = []
        cursor = start
        while True:
            par_list = cmds.listRelatives(cursor, parent=True, type='joint') or []
            if not par_list:
                return chain
            cursor = par_list[0]
            chain.append(cursor)

    if joint_b in walk_parents(joint_a):
        return joint_b, joint_a  # b is ancestor of a
    if joint_a in walk_parents(joint_b):
        return joint_a, joint_b  # a is ancestor of b
    return None, None


def is_linear_joint_chain(ancestor: str, descendant: str) -> tuple:
    """Return (ok: bool, offending_joint: str | None).

    A "linear chain" between ancestor and descendant means every joint
    STRICTLY BETWEEN them (proper descendants of ancestor that are
    proper ancestors of descendant) has exactly one joint child — the
    next step toward descendant. A branching intermediate (e.g. chest
    with multiple clavicles + neck) is rejected because Insert Joints
    Between would interpolate world positions through it and reparent
    the descendant onto an interpolated mid-joint, severing the
    side-branches.

    ancestor itself is allowed to have multiple joint children (we're
    inserting BETWEEN, not changing the ancestor). descendant is
    allowed to have any children (their structure is irrelevant since
    they ride along under the reparented descendant).

    Returns (True, None) if the chain is linear, else
    (False, <name of first branching intermediate joint>).
    """
    cursor = descendant
    while True:
        par_list = cmds.listRelatives(cursor, parent=True, type='joint') or []
        if not par_list:
            return False, descendant  # walked to root without hitting ancestor
        parent = par_list[0]
        if parent == ancestor:
            return True, None  # success — chain is linear
        # parent is an intermediate joint. It must have exactly ONE joint child.
        siblings = cmds.listRelatives(parent, children=True, type='joint') or []
        if len(siblings) != 1:
            return False, parent
        cursor = parent


def insert_joints_between(count, bias=0.0, joints=None):
    """Insert `count` joints between two joints (default: the two selected,
    start then end), splitting the hierarchy exactly as
    insert_joints_between_selection does. bias (-1..1) slides every inserted
    joint's interpolation parameter toward the start (-) or end (+):
    t_i = clamp((i + 1)/(count + 1) + bias/(count + 1), 0.02, 0.98) - order
    is preserved by construction. No prompts, no Qt - the toolbar popover
    and the old UI wrapper both call this."""
    count = int(count)
    if count < 1:
        raise RuntimeError("Select exactly two joints (start, then end)")

    if joints is None:
        joints = cmds.ls(sl=True, type='joint') or []
    joints = list(joints)
    if len(joints) != 2:
        raise RuntimeError("Select exactly two joints (start, then end)")

    ancestor, descendant = resolve_ancestor_descendant(joints[0], joints[1])
    if ancestor is None:
        raise RuntimeError(
            "Select exactly two joints (start, then end) - one must be a "
            "descendant of the other.")

    ok, offender = is_linear_joint_chain(ancestor, descendant)
    if not ok:
        raise RuntimeError(
            f"Cannot insert between {ancestor} and {descendant} - "
            f"the chain branches at {offender!r}.")

    with undo_chunk("Insert Joints"):
        cmds.select(clear=True)

        start_pos = om.MVector(*cmds.xform(ancestor, q=True, ws=True, t=True))
        end_pos = om.MVector(*cmds.xform(descendant, q=True, ws=True, t=True))
        new_joints = []

        total = count + 1
        for i in range(total + 1):
            t = float(i) / total
            if 0 < i < total:
                t = min(max(t + bias / total, 0.02), 0.98)
            pos = start_pos + (end_pos - start_pos) * t
            name = f"{ancestor}_mid_{i}" if 0 < i < total else f'{ancestor}_temp' if i == 0 else f'{descendant}_temp'
            jnt = cmds.joint(name=name)
            cmds.xform(jnt, ws=True, ro=cmds.xform(ancestor, q=True, ws=True, ro=True))
            cmds.xform(jnt, ws=True, t=pos)
            new_joints.append(jnt)

        cmds.parent(new_joints[1], ancestor)
        cmds.parent(descendant, new_joints[-2])
        cmds.delete(new_joints[-1])
        cmds.delete(new_joints[0])

    return count


def insert_joints_between_selection(parent_window=None):
    """Top-level driver for the Insert Joints Between action.

    Validates the current Maya selection (exactly two joints, one
    ancestor of the other, linear chain between them), prompts the user
    for the joint count via QInputDialog, dispatches to
    insert_joints_between.

    Args:
        parent_window: optional Qt parent for the prompt + message boxes.

    Returns:
        int: number of joints actually inserted (the user's count).
        None: user cancelled the prompt, OR validation failed and the
              user was shown an error dialog.

    Caller should report success/error via its own logger; this helper
    handles the validation + prompt UI inline and returns a clean result
    code.
    """
    from PySide6 import QtWidgets

    sel = cmds.ls(sl=True, type='joint') or []
    if len(sel) != 2:
        QtWidgets.QMessageBox.warning(
            parent_window, 'Insert Joints Between',
            'Insert Between requires exactly 2 joints selected.')
        return None

    ancestor, descendant = resolve_ancestor_descendant(sel[0], sel[1])
    if ancestor is None:
        QtWidgets.QMessageBox.warning(
            parent_window, 'Insert Joints Between',
            'Select two joints, one a descendant of the other.')
        return None

    ok, offender = is_linear_joint_chain(ancestor, descendant)
    if not ok:
        QtWidgets.QMessageBox.warning(
            parent_window, 'Insert Joints Between',
            f'Cannot insert between {ancestor} and {descendant} — '
            f'the chain branches at {offender!r}. '
            'Insert Between requires a single linear chain with no '
            'intermediate joints that have other joint children.')
        return None

    count, accepted = QtWidgets.QInputDialog.getInt(
        parent_window, 'Insert Joints',
        f'Number of joints to insert between {ancestor} and {descendant}:',
        1, 1, 100, 1)
    if not accepted:
        return None

    insert_joints_between(count, joints=(ancestor, descendant))
    return count


def _next_joint_name(parent: str) -> str:
    """Return parent's name with its trailing numeric suffix incremented.

    'spine_03' -> 'spine_04', 'joint99' -> 'joint100', 'foo' -> 'foo_01'.
    Width is preserved when a leading-zero pad is detected ('03' -> '04', not '4').
    Auto-increments past names that already exist in the scene.
    """
    base_name = parent.split('|')[-1].split(':')[-1]
    match = _TRAILING_DIGITS.match(base_name)
    if match:
        stem, digits = match.group(1), match.group(2)
        width = len(digits) if digits.startswith('0') else 0
        n = int(digits) + 1
    else:
        stem, width, n = f'{base_name}_', 2, 1

    while True:
        candidate = f'{stem}{n:0{width}d}' if width else f'{stem}{n}'
        if not cmds.objExists(candidate):
            return candidate
        n += 1
