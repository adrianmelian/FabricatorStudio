# Python/maya_tools/rigging/fabricator/actions/quad_leg.py
"""QuadLeg-specific marking-menu handlers — IK/FK mode switching, plus
FK-side pose matching. Mirrors the resolver pattern in
actions/simple_ik.py (same fab_owner-walk, same warning style).

Scope note (CP2 follow-up): "Match to FK" is implemented — it's the same
snap-FK-ctrls-to-bind-joints pattern SimpleIK/IKLeg already use, just
generalized from 3/4 joints to QuadLeg's 4 FK ctrls (upper, knee, ankle,
toe). "Match to IK" is NOT implemented. QuadLeg layers a hock ctrl on top
of the IK solve (hock_ctrl.rotate feeds hock_pivot_offset.rotate via a
live connectAttr, not a constraint with an invertible bind target) to
give the animator manual hock bend independent of the 3 RP IK handles.
Reproducing the CURRENT bind pose exactly when switching FK-to-IK would
require solving for the hock_ctrl rotation that reconciles the pivot
offset with the ankle's current world orientation -- a genuine inverse
decomposition problem, not a mechanical copy of SimpleIK's PV-projection
match. Left unimplemented rather than guessed at; see
WAVE1-REPORT.md's CP2-fix section for the recommendation.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes as ks_nodes


def _resolve_component_node(component_id: str, ctrl: str) -> str:
    """Resolve the component network node for a marking-menu invocation.
    Same pattern as actions/simple_ik.py's resolver."""
    if ctrl and cmds.objExists(ctrl) and \
            cmds.attributeQuery('fab_owner', node=ctrl, exists=True):
        owner = cmds.listConnections(f'{ctrl}.fab_owner',
                                      source=True, destination=False) or []
        if owner:
            return owner[0]
    for cn in ks_nodes.find_all_component_nodes_by_marker():
        if ks_nodes.get_component_id(cn) == component_id:
            return cn
    return ''


def _resolve_quad_leg_ctrls(component_node: str) -> dict:
    """Find the QuadLeg ctrls owned by the given component network node.

    Returns {role: ctrl_name} dict, fk_ctrls grouped as a sorted-by-DAG-
    depth list under key 'fk_ctrls' (order: upper, knee, ankle, toe).
    """
    if not component_node:
        return {}

    out = {}
    for ctrl in cmds.ls(type='transform') or []:
        if not cmds.attributeQuery('fab_owner', node=ctrl, exists=True):
            continue
        owner = cmds.listConnections(f'{ctrl}.fab_owner',
                                     source=True, destination=False) or []
        if component_node not in owner:
            continue
        if not cmds.attributeQuery('fab_role', node=ctrl, exists=True):
            continue
        role = cmds.getAttr(f'{ctrl}.fab_role')
        if role == 'fk_ctrl':
            out.setdefault('fk_ctrls', []).append(ctrl)
        else:
            out[role] = ctrl
    if 'fk_ctrls' in out:
        out['fk_ctrls'] = sorted(
            out['fk_ctrls'],
            key=lambda c: len(cmds.ls(c, long=True)[0].split('|')),
        )
    return out


def switch_to_ik(component_id: str, _ctrl: str) -> None:
    """Just flip blend -> 1; visual pose may pop."""
    component_node = _resolve_component_node(component_id, _ctrl)
    ctrls = _resolve_quad_leg_ctrls(component_node)
    s = ctrls.get('master_switch_ctrl')
    if s:
        cmds.setAttr(f'{s}.ik_fk_blend', 1.0)
    else:
        cmds.warning(
            f"switch_to_ik[{component_id}]: no master_switch_ctrl for "
            f"component {component_node!r}."
        )


def switch_to_fk(component_id: str, _ctrl: str) -> None:
    """Just flip blend -> 0; visual pose may pop."""
    component_node = _resolve_component_node(component_id, _ctrl)
    ctrls = _resolve_quad_leg_ctrls(component_node)
    s = ctrls.get('master_switch_ctrl')
    if s:
        cmds.setAttr(f'{s}.ik_fk_blend', 0.0)
    else:
        cmds.warning(
            f"switch_to_fk[{component_id}]: no master_switch_ctrl for "
            f"component {component_node!r}."
        )


def switch_to_fk_match(component_id: str, _ctrl: str) -> None:
    """Snap each FK ctrl's world transform to its bind joint, then flip
    ik_fk_blend -> 0. Same matchTransform pattern as SimpleIK's
    switch_to_fk_match (actions/simple_ik.py), generalized to QuadLeg's
    4 FK ctrls (upper, knee, ankle, toe -- toe_end has no FK counterpart,
    it follows the target chain directly). Applied parent-first down the
    chain so each ctrl sees its parent's just-applied match.
    """
    component_node = _resolve_component_node(component_id, _ctrl)
    if not component_node:
        cmds.warning(
            f"switch_to_fk_match[{component_id}]: no component node found "
            f"(ctrl={_ctrl!r} has no fab_owner connection and no "
            f"marker-tagged component matches the id). Has the rig been "
            f"unbuilt, or is the ctrl untagged?"
        )
        return

    ctrls = _resolve_quad_leg_ctrls(component_node)
    switch_ctrl = ctrls.get('master_switch_ctrl')
    fk_ctrls = ctrls.get('fk_ctrls') or []
    if not (switch_ctrl and len(fk_ctrls) >= 4):
        cmds.warning(
            f"switch_to_fk_match[{component_id}]: for component "
            f"{component_node!r}: switch_ctrl={switch_ctrl!r}, fk_ctrls "
            f"count={len(fk_ctrls)} (need >=4). Check fab_owner "
            f"connections + fab_role tags on the rig's ctrls."
        )
        return

    bind_joints = ks_nodes.get_component_joints(component_node)
    if len(bind_joints) < 4:
        cmds.warning(
            f"switch_to_fk_match[{component_id}]: needs >=4 bind joints, "
            f"got {len(bind_joints)} on {component_node!r}. Joint message "
            f"connections may be broken."
        )
        return

    n = min(len(bind_joints), len(fk_ctrls))
    for bind_jnt, fk_ctrl in zip(bind_joints[:n], fk_ctrls[:n]):
        cmds.matchTransform(fk_ctrl, bind_jnt,
                            position=True, rotation=True, scale=False)

    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 0.0)
