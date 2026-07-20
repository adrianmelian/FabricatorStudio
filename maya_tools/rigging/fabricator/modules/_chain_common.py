"""Shared substrate for chain-of-FK-control builds (ribbon segment FK
layers via _ribbon_common/_limb_common; original consumers FKChain,
SplineFK and Ribbon left the shipped set 2026-07-12).

Side-effect-free helpers + parameterized builders: the FK control cascade (a
cube ctrl + free sphere offset ctrl per row), control-joints that skin a
deformer, the offset-ctrl-drives-control-joint chain, axis resolution, the
center curve, and the deformed-shape filter. Neither component owns the
other's role strings / name prefixes / message-bucket names — those are args.
"""
__author__ = "Adrian Melian"

import math

import maya.cmds as cmds


def chain_name(joints):
    return joints[0].split('|')[-1].split(':')[-1]


def resolve_control_count(opts, joints):
    count = opts.get('control_count') or len(joints)
    return max(4, int(count))


def resolve_component_node(instance_id):
    from maya_tools.rigging.fabricator import nodes
    return next(
        (cn for cn in nodes.get_all_component_nodes()
         if nodes.get_component_id(cn) == instance_id),
        '',
    )


def resolve_axes(joints):
    """Aim = +X down bone, up = +Y (UE5), read defensively off the chain.
    Returns (aim_vec, up_vec, up_world); up_world = averaged world +Y."""
    aim_vec = (1.0, 0.0, 0.0)
    up_vec = (0.0, 1.0, 0.0)
    ys = []
    for j in joints:
        m = cmds.xform(j, q=True, ws=True, matrix=True)  # rows 4-6 = local +Y
        ys.append((m[4], m[5], m[6]))
    ux = sum(y[0] for y in ys) / len(ys)
    uy = sum(y[1] for y in ys) / len(ys)
    uz = sum(y[2] for y in ys) / len(ys)
    mag = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
    return aim_vec, up_vec, (ux / mag, uy / mag, uz / mag)


def disconnect_outgoing_message(node):
    """Gotcha 1: cmds.duplicate/createNode can propagate .message into multis."""
    for dest in cmds.listConnections(f'{node}.message', source=False,
                                     destination=True, plugs=True) or []:
        cmds.disconnectAttr(f'{node}.message', dest)


def deformed_shape(node, type_str):
    """The live (deformed) shape of type_str under node, skipping the
    intermediate 'Orig' a deformer leaves behind. Serves nurbsCurve AND
    nurbsSurface — pointOnCurveInfo / follicle must read the deformed geo."""
    shapes = cmds.listRelatives(node, shapes=True, type=type_str,
                                fullPath=True) or []
    live = [s for s in shapes if not cmds.getAttr(f'{s}.intermediateObject')]
    return (live or shapes)[0]


def build_center_curve(joints, control_count, setup_grp, name, component_node, bucket):
    """Degree-3 curve fit through the bind joints, rebuilt to exactly
    control_count CVs, parented identity-world under setup_grp, tracked in
    `bucket`. Returns the curve transform."""
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.utils.maya import network_nodes as nn

    pts = [cmds.xform(j, q=True, ws=True, t=True) for j in joints]
    fit_degree = min(3, len(pts) - 1)
    crv = cmds.curve(degree=fit_degree, point=pts, name=name)
    cmds.rebuildCurve(crv, ch=0, rpo=1, rt=0, end=1, kr=0, kcp=0, kep=1,
                      kt=0, s=max(1, control_count - 3), d=3)
    shape = deformed_shape(crv, 'nurbsCurve')
    cv_count = cmds.getAttr(f'{shape}.spans') + cmds.getAttr(f'{shape}.degree')
    if cv_count != control_count:
        raise RuntimeError(
            f'_chain_common: curve has {cv_count} CVs, expected {control_count}')
    cmds.parent(crv, setup_grp)
    cmds.setAttr(f'{crv}.inheritsTransform', 0)
    cmds.xform(crv, ws=True, t=(0, 0, 0))
    nodes.hide_internal(crv)
    nn.connect_message_multi(crv, component_node, bucket)
    return crv


def build_control_joints(name_prefix, positions, up_world, setup_grp, component_node, bucket):
    """One control-joint per position, oriented +X-down/+Y-up along the chain
    (last copies previous), flat under setup_grp, tracked in `bucket`."""
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.utils.maya import network_nodes as nn

    control_joints = []
    for k, p in enumerate(positions):
        cmds.select(clear=True)
        cj = cmds.joint(name=f'{name_prefix}{k:02d}', position=p)
        disconnect_outgoing_message(cj)
        cmds.parent(cj, setup_grp)
        cmds.xform(cj, ws=True, t=p)
        control_joints.append(cj)
    for k, cj in enumerate(control_joints):
        if k < len(control_joints) - 1:
            ac = cmds.aimConstraint(control_joints[k + 1], cj, aimVector=(1, 0, 0),
                                    upVector=(0, 1, 0), worldUpType='vector',
                                    worldUpVector=up_world)
            cmds.delete(ac)
        else:
            cmds.xform(cj, ws=True, rotation=cmds.xform(
                control_joints[k - 1], q=True, ws=True, rotation=True))
        cmds.makeIdentity(cj, apply=True, t=0, r=1, s=0, n=0)
    for cj in control_joints:
        nodes.hide_internal(cj)
        nn.connect_message_multi(cj, component_node, bucket)
    return control_joints


def _restore_shape(com, ctrl, saved):
    if not saved:
        return
    shapes_list = saved if isinstance(saved, list) else [saved]
    try:
        com.deserialize_shape_to(ctrl, {'shapes': shapes_list})
    except Exception:
        pass


def build_fk_cascade(role_main, role_offset, name_prefix, control_joints,
                     parent_ctrl, opts, cv_block, component_node):
    """Per control-joint: a cube FK ctrl (chain-parented) + a free sphere
    offset child ctrl. Tags role_main / role_offset. Returns
    (fk_ctrls, offset_ctrls)."""
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
    from maya_tools.rigging.fabricator.modules.world import _apply_color, _apply_channels
    from maya_tools.rigging.fabricator import nodes

    shape = opts.get('ctrl_shape', 'cube')
    offset_shape = opts.get('offset_ctrl_shape', 'sphere')
    color = opts.get('ctrl_color', 'yellow')
    channels = opts.get('channels', {})

    fk_ctrls, offset_ctrls = [], []
    prev_parent = parent_ctrl
    for k, cj in enumerate(control_joints):
        buf = cmds.createNode('transform', name=f'{name_prefix}{k:02d}_offset')
        cmds.matchTransform(buf, cj, position=True, rotation=True, scale=False)
        cmds.parent(buf, prev_parent)
        cmds.matchTransform(buf, cj, position=True, rotation=True, scale=False)

        fk_ctrl = com.build_shape(shape, f'{name_prefix}{k:02d}')
        cmds.parent(fk_ctrl, buf)
        for a in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{fk_ctrl}.{a}', 0)
        for a in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{fk_ctrl}.{a}', 1)
        _apply_color(fk_ctrl, color)
        _restore_shape(com, fk_ctrl, cv_block.get(fk_ctrl))

        offset_ctrl = com.build_shape(offset_shape, f'{name_prefix}{k:02d}_offset_ctrl')
        cmds.parent(offset_ctrl, fk_ctrl)
        for a in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{offset_ctrl}.{a}', 0)
        for a in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{offset_ctrl}.{a}', 1)
        _apply_color(offset_ctrl, color)
        _restore_shape(com, offset_ctrl, cv_block.get(offset_ctrl))

        _apply_channels(fk_ctrl, channels)
        nodes.tag_ctrl(fk_ctrl, role_main, component_node=component_node, joint_index=k)
        nodes.tag_ctrl(offset_ctrl, role_offset, component_node=component_node, joint_index=k)

        fk_ctrls.append(fk_ctrl)
        offset_ctrls.append(offset_ctrl)
        prev_parent = fk_ctrl
    return fk_ctrls, offset_ctrls


def drive_control_joints(dg_bucket, offset_ctrls, control_joints, component_node):
    """offset_ctrl -> parent/scaleConstraint(mo) -> connector_null -> ditto ->
    control_joint. Drive nulls tracked in dg_bucket."""
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.utils.maya import network_nodes as nn
    for offset_ctrl, cj in zip(offset_ctrls, control_joints):
        cj_off, cj_con = nodes.build_null_pair(cj)
        cmds.parentConstraint(offset_ctrl, cj_con, maintainOffset=True)
        cmds.scaleConstraint(offset_ctrl, cj_con, maintainOffset=True)
        cmds.parentConstraint(cj_con, cj, maintainOffset=True)
        cmds.scaleConstraint(cj_con, cj, maintainOffset=True)
        for n in (cj_off, cj_con):
            nn.connect_message_multi(n, component_node, dg_bucket)


def build_fk_joint_ctrl(joint, offset_parent, opts, cv_block, component_node,
                        role, joint_index):
    """One joint's offset+ctrl+null-pair FK cascade — the per-joint unit
    FKChain and RibbonIKArm's finger-FK builder both share (PLAN.md 2026-07-08
    Task 4.4: 'reusing FKChain's per-joint offset+ctrl+null builder... via
    a shared helper — factor it, DRY'). Lifted verbatim from
    FKChainComponent.build()'s per-joint loop body — behavior-identical,
    just parameterized on WHERE the offset ctrl physically parents
    (`offset_parent`) so callers with different chain-start conventions
    (FKChain's under-an-existing-joint special case; RibbonIKArm's fingers,
    which always chain-parent under the wrist ctrl) can reuse it as-is.

    Builds, in order:
      - a null pair (nodes.build_null_pair(joint)) — the constraint
        intermediary between `ctrl` and `joint` (offset_null +
        connector_null; only connector_null is wired further here,
        matching the original FKChain loop).
      - `<short>_ctrl_offset`: a plain transform, world-matched to
        `joint`, rigidly parented under `offset_parent`.
      - `<short>_ctrl`: opts['ctrl_shape'] curve, parented under the
        offset ctrl, zeroed TRS, colored opts['ctrl_color'], persisted
        CV shape restored from cv_block[ctrl_name] if present, channels
        applied per opts['channels'], tagged `role` via nodes.tag_ctrl
        with `component_node` + `joint_index` (pose-library lookup).
      - the drive chain: ctrl -> connector_null -> `joint`
        (parentConstraint + scaleConstraint, maintainOffset=True at both
        stages).

    opts: dict with 'ctrl_shape' (shape_enum key), 'ctrl_color'
    (color_enum key), 'channels' (channels-field dict) — the same keys
    FKChain's own options_schema declares; RibbonIKArm's finger builder passes
    its own equivalent dict (see _limb_common.build_finger_fk_chain).
    cv_block: {ctrl_name: shapes_list} persisted CV map, same schema as
    every other component's cv_data persistence.
    role: fab_role tag value for nodes.tag_ctrl (FKChain passes
    'fk_ctrl'; RibbonIKArm's fingers pass 'finger_fk_ctrl').
    joint_index: passed through to nodes.tag_ctrl.

    Returns (offset_ctrl, ctrl) — callers thread `ctrl` in as the next
    joint's offset_parent for chain-parenting. A chain-start special case
    (e.g. FKChain's is_chain_start_under_joint branch, which needs a
    DIFFERENT offset_parent than the natural prev_parent PLUS an extra
    follow-constraint on top) stays in the caller: this function only
    ever rigid-parents offset_ctrl under whatever `offset_parent` it's
    given.
    """
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
    from maya_tools.rigging.fabricator.modules.world import _apply_color, _apply_channels
    from maya_tools.rigging.fabricator import nodes

    short = joint.split('|')[-1].split(':')[-1]
    shape = opts.get('ctrl_shape', 'circle')
    color = opts.get('ctrl_color', 'yellow')

    offset_null, connector_null = nodes.build_null_pair(joint)

    offset_ctrl_name = f'{short}_ctrl_offset'
    offset_ctrl = cmds.createNode('transform', name=offset_ctrl_name)
    cmds.matchTransform(offset_ctrl, joint, position=True, rotation=True, scale=False)
    cmds.parent(offset_ctrl, offset_parent)
    cmds.matchTransform(offset_ctrl, joint, position=True, rotation=True, scale=False)

    ctrl_name = f'{short}_ctrl'
    ctrl = com.build_shape(shape, ctrl_name,
                           radius=cmds.getAttr(f'{joint}.radius'))
    cmds.parent(ctrl, offset_ctrl)
    for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
        cmds.setAttr(f'{ctrl}.{attr}', 0)
    for attr in ('sx', 'sy', 'sz'):
        cmds.setAttr(f'{ctrl}.{attr}', 1)

    _apply_color(ctrl, color)

    saved = (cv_block or {}).get(ctrl_name)
    if saved:
        shapes_list = saved if isinstance(saved, list) else [saved]
        try:
            com.deserialize_shape_to(ctrl, {'shapes': shapes_list})
        except Exception:
            pass

    cmds.parentConstraint(ctrl, connector_null, maintainOffset=True)
    cmds.scaleConstraint(ctrl, connector_null, maintainOffset=True)
    cmds.parentConstraint(connector_null, joint, maintainOffset=True)
    cmds.scaleConstraint(connector_null, joint, maintainOffset=True)

    _apply_channels(ctrl, opts.get('channels', {}))

    nodes.tag_ctrl(ctrl, role, component_node=component_node, joint_index=joint_index)

    return offset_ctrl, ctrl
