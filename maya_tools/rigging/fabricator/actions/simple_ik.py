# Python/maya_tools/rigging/fabricator/actions/simple_ik.py
"""SimpleIK-specific marking-menu handlers — IK/FK mode switching with
optional matching, FK-to-IK and IK-to-FK pose matching."""
__author__ = "Adrian Melian"

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.rigging.fabricator import nodes as ks_nodes


def _resolve_component_node(component_id: str, ctrl: str) -> str:
    """Resolve the component network node for a marking-menu invocation.

    Prefers walking the right-clicked ctrl's fab_owner connection (works
    in multi-rig animation scenes where get_all_component_nodes() would
    only return components from one rig's registry). Falls back to a
    marker-based scan that covers every fab_component-tagged node in
    the scene if the ctrl has no owner connection.
    """
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


def _resolve_simple_ik_ctrls(component_node: str) -> dict:
    """Find the SimpleIK ctrls owned by the given component network node.

    Returns {role: ctrl_name} dict, or empty if the component has no
    tagged ctrls in the scene. FK ctrls are grouped under key 'fk_ctrls'
    as a list (order: shoulder, elbow, wrist after sorting by DAG depth).
    """
    if not component_node:
        return {}

    out = {}
    # Walk all ctrls with fab_owner pointing at this component_node.
    # No glob pattern — cmds.ls('*', ...) is namespace-restricted and misses
    # referenced rigs in animation scenes.
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
    # Sort fk_ctrls by DAG depth (shoulder, elbow, wrist) for downstream zip
    if 'fk_ctrls' in out:
        out['fk_ctrls'] = sorted(
            out['fk_ctrls'],
            key=lambda c: len(cmds.ls(c, long=True)[0].split('|')),
        )
    return out


def switch_to_ik_match(component_id: str, _ctrl: str) -> None:
    """Snap IK end ctrl + PV ctrl to current bind chain, then flip
    ik_fk_blend → 1.

    Uses matchTransform (matrix-based) for the IK end ctrl — survives the
    offsetParentMatrix that the space-switch DG drives onto ik_end_ctrl.
    PV is positioned via perpendicular projection from current elbow onto
    the shoulder→wrist line, scaled by chain length.
    """
    component_node = _resolve_component_node(component_id, _ctrl)
    if not component_node:
        cmds.warning(
            f"switch_to_ik_match[{component_id}]: no component node found "
            f"(ctrl={_ctrl!r} has no fab_owner connection and no "
            f"marker-tagged component matches the id). Has the rig been "
            f"unbuilt, or is the ctrl untagged?"
        )
        return

    ctrls = _resolve_simple_ik_ctrls(component_node)
    switch_ctrl = ctrls.get('master_switch_ctrl')
    ik_end = ctrls.get('ik_end_ctrl')
    pv = ctrls.get('pv_ctrl')
    if not (switch_ctrl and ik_end and pv):
        missing = [k for k, v in (('master_switch_ctrl', switch_ctrl),
                                    ('ik_end_ctrl', ik_end),
                                    ('pv_ctrl', pv)) if not v]
        cmds.warning(
            f"switch_to_ik_match[{component_id}]: missing ctrl(s) for "
            f"component {component_node!r}: {', '.join(missing)}. Check "
            f"fab_owner connections + fab_role tags on the rig's ctrls."
        )
        return

    bind_joints = ks_nodes.get_component_joints(component_node)
    # Allow 3+ joints — IKLeg has 4 (thigh, calf, foot, ball); we only
    # need the first 3 here for the IK chain match (PV math + IK end ctrl).
    if len(bind_joints) < 3:
        cmds.warning(
            f"switch_to_ik_match[{component_id}]: needs >=3 bind joints, "
            f"got {len(bind_joints)} on {component_node!r}. Joint message "
            f"connections may be broken."
        )
        return
    start, mid, end = bind_joints[:3]

    # Match IK end ctrl world (translate + rotation) to bind end joint world.
    cmds.matchTransform(ik_end, end,
                        position=True, rotation=True, scale=False)

    # Match PV ctrl: project elbow perpendicular at chain length × 0.5
    s = cmds.xform(start, q=True, ws=True, t=True)
    m = cmds.xform(mid,   q=True, ws=True, t=True)
    e = cmds.xform(end,   q=True, ws=True, t=True)
    sv, mv, ev = om.MVector(*s), om.MVector(*m), om.MVector(*e)
    chain_len = (mv - sv).length() + (ev - mv).length()
    se = ev - sv
    sm = mv - sv
    se_len = se.length()
    if se_len > 1e-6:
        se_norm = se / se_len
        arrow = sm - (sm * se_norm) * se_norm
    else:
        arrow = om.MVector(0, 0, -1)
    if arrow.length() < 1e-3:
        arrow = om.MVector(0, 0, -1)
    arrow = arrow.normal() * (chain_len * 0.5)
    final = mv + arrow
    cmds.xform(pv, ws=True, t=(final.x, final.y, final.z))

    # Flip blend
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 1.0)


def switch_to_fk_match(component_id: str, _ctrl: str) -> None:
    """Snap each FK ctrl's world transform to its bind joint, then flip
    ik_fk_blend → 0.

    matchTransform copies BOTH position and rotation — non-stretchy case
    is a no-op on translate (bind joint world = FK_offset world = zero
    local translate), stretchy case correctly carries the stretched
    chain length over to FK mode (FK ctrl translates are unlocked by
    default, so the captured world translate lands cleanly).

    Uses matchTransform (matrix-based, parent-aware) instead of copying
    bind.rotate directly — the FK ctrls are nested under FK_ctrl_offsets
    that already carry bind's world rotation, so a raw rotate-copy would
    double-apply the bone orientation on aimed joints. Applying down the
    chain (parent → child) lets each ctrl see its parent's just-applied
    match before its own world matrix is solved.
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

    ctrls = _resolve_simple_ik_ctrls(component_node)
    switch_ctrl = ctrls.get('master_switch_ctrl')
    fk_ctrls = ctrls.get('fk_ctrls') or []
    # IKLeg has 4 bind joints but only 3 super-FK ctrls (no FK ball ctrl yet —
    # that's the deferred ball-into-blend-chain refactor). Match whatever's
    # available, paired by chain order.
    if not (switch_ctrl and len(fk_ctrls) >= 3):
        cmds.warning(
            f"switch_to_fk_match[{component_id}]: for component "
            f"{component_node!r}: switch_ctrl={switch_ctrl!r}, fk_ctrls "
            f"count={len(fk_ctrls)} (need >=3). Check fab_owner "
            f"connections + fab_role tags on the rig's ctrls."
        )
        return

    bind_joints = ks_nodes.get_component_joints(component_node)
    if len(bind_joints) < 3:
        cmds.warning(
            f"switch_to_fk_match[{component_id}]: needs >=3 bind joints, "
            f"got {len(bind_joints)} on {component_node!r}. Joint message "
            f"connections may be broken."
        )
        return

    n = min(len(bind_joints), len(fk_ctrls))
    for bind_jnt, fk_ctrl in zip(bind_joints[:n], fk_ctrls[:n]):
        cmds.matchTransform(fk_ctrl, bind_jnt,
                            position=True, rotation=True, scale=False)

    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 0.0)


def switch_to_ik(component_id: str, _ctrl: str) -> None:
    """Just flip blend → 1; visual pose may pop."""
    component_node = _resolve_component_node(component_id, _ctrl)
    ctrls = _resolve_simple_ik_ctrls(component_node)
    s = ctrls.get('master_switch_ctrl')
    if s:
        cmds.setAttr(f'{s}.ik_fk_blend', 1.0)
    else:
        cmds.warning(
            f"switch_to_ik[{component_id}]: no master_switch_ctrl for "
            f"component {component_node!r}."
        )


def switch_to_fk(component_id: str, _ctrl: str) -> None:
    """Just flip blend → 0; visual pose may pop."""
    component_node = _resolve_component_node(component_id, _ctrl)
    ctrls = _resolve_simple_ik_ctrls(component_node)
    s = ctrls.get('master_switch_ctrl')
    if s:
        cmds.setAttr(f'{s}.ik_fk_blend', 0.0)
    else:
        cmds.warning(
            f"switch_to_fk[{component_id}]: no master_switch_ctrl for "
            f"component {component_node!r}."
        )


def match_fk_chain_to_ik(component_id: str, _ctrl: str) -> None:
    """Snap FK ctrls to current bind pose; do NOT flip blend.

    For Spec 1.5 this reuses switch_to_fk_match's snap logic; the blend-
    flip side effect is accepted (animator using this expects to be moving
    toward FK anyway). A future cleanup could extract _snap_fk_to_bind
    helper to keep blend untouched.
    """
    switch_to_fk_match(component_id, _ctrl)


def match_ik_to_fk_chain(component_id: str, _ctrl: str) -> None:
    """Snap IK end ctrl + PV ctrl to current bind pose; do NOT flip blend.

    Same Spec 1.5 simplification as match_fk_chain_to_ik — reuses
    switch_to_ik_match including the blend flip.
    """
    switch_to_ik_match(component_id, _ctrl)
