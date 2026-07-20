"""RibbonSpine — hip + chest + N floating mids over the ribbon substrate.
Falloff skin gives free hip/chest roll interpolation (the twist distribution);
the board adds sculpted twist/sine/jiggle/volume. Spec:
docs/superpowers/specs/2026-07-05-ribbon-limbs-design.md."""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator.modules.component import (
    Component, Contract, JointRole, OptionField, Plug,
)
from maya_tools.rigging.fabricator.modules import _chain_common as cc
from maya_tools.rigging.fabricator.modules import _ribbon_common as rc


# Cap for the per-bind-joint parent-space outputs (joint_out_0..
# joint_out_{_MAX_JOINT_OUTPUTS - 1}). RibbonSpine's max_joints is unbounded
# (creature/tail spines can run long), but a static Contract must declare a
# finite set of Plugs. A biped spine is ~3-10 joints; 24 comfortably covers
# even long non-biped spines. Builds that exceed the cap register only the
# first _MAX_JOINT_OUTPUTS joints and cmds.warning the overflow.
_MAX_JOINT_OUTPUTS = 24

RIBBON_SPINE_CONTRACT = Contract(
    type='RibbonSpine',
    display_name='Ribbon Spine',
    description=('COG (whole-system mover) parenting Hip + Chest ends (full '
                 'TR, children of the COG) + N floating mid controls '
                 '(always-on aimers) driving a falloff-skinned ribbon. Free '
                 'twist distribution via the skin falloff; the dial board '
                 '(twist/sine/jiggle/volume) lives on the COG itself -- no '
                 'separate settings control. Inherent arc-length stretch. '
                 'Every SPINE BIND JOINT is also attachable: each live bind '
                 'joint exposes its own parent-space output (joint_out_0.. '
                 'joint_out_<N-1>, one per bind joint, up to the declared '
                 'cap) so a child component (e.g. a clavicle FK hanging off '
                 'an interior joint like spine04) has an exact attach point '
                 'to pick manually -- the joint follows the ribbon '
                 'deformation directly, which an interior joint has no '
                 'control node for.'),
    min_joints=3,
    max_joints=-1,
    parent_strategy='walk_up',
    inputs=(Plug(name='parent_in', kind='matrix', required=True,
                 description='Parent transform space (cog).'),),
    outputs=(
        Plug(name='start_out', kind='matrix', space_target=True,
             description='World matrix at the hip ctrl.'),
        Plug(name='tip_out', kind='matrix', space_target=True,
             description='World matrix at the chest ctrl (neck/arm parent).'),
    ) + tuple(
        Plug(name=f'joint_out_{i}', kind='matrix', space_target=True,
             description=(f'World matrix of bind joint index {i} in the '
                          f'spine chain (an exact attach point for a child '
                          f'component, e.g. a clavicle at an interior '
                          f'joint). Only registered as a space provider '
                          f'when the built spine has more than {i} '
                          f'joint(s).'))
        for i in range(_MAX_JOINT_OUTPUTS)
    ),
    options_schema={
        'mid_ctrl_count': OptionField('int', 1, range=(1, 8),
            description='Floating mid controls between hip and chest. '
                        'Build option: change in Edit Mode and rebuild.'),
        'ribbon_width': OptionField('float', 0.0, range=(0.0, 1000.0),
            description='Ribbon cross-width. 0.0 = auto. Resolved value '
                        'persisted so rebuilds match.'),
        'cog_ctrl_shape': OptionField('shape_enum', 'cog_ctrl'),
        # ctrl_shape drives the HIP (bottom) control; chest_ctrl_shape the
        # CHEST (top). Split 2026-07-08 so the two ends can differ (Adrian:
        # cube_open top, hip_ctrl bottom, cog_ctrl for the COG). ctrl_shape
        # keeps its name so existing rigs' persisted values still resolve.
        'ctrl_shape': OptionField('shape_enum', 'hip_ctrl'),
        'chest_ctrl_shape': OptionField('shape_enum', 'cube_open'),
        'mid_ctrl_shape': OptionField('shape_enum', 'circle'),
        'ctrl_color': OptionField('color_enum', 'forest'),
        'channels': OptionField('channels',
            {'keyable': ['tx', 'ty', 'tz', 'rx', 'ry', 'rz']}),
    },
    side_supported=False,
    color='#008C33',  # forest — ribbon family, deepest
    joint_roles=(
        JointRole('start', 'Hips (root of the spine chain)'),
        JointRole('end', 'Chest (tip of the spine chain)'),
    ),
    mirror_rules=(),
)


class RibbonSpineComponent(Component):
    CONTRACT = RIBBON_SPINE_CONTRACT

    @classmethod
    def build(cls, instance, context) -> None:
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator.modules.world import (
            _apply_color, _apply_channels)
        from maya_tools.rigging.fabricator import nodes

        joints = list(instance.joints)
        for j in joints:
            if not cmds.objExists(j):
                raise RuntimeError(f'RibbonSpine: bind joint not found: {j}')
        opts = instance.options
        chain = cc.chain_name(joints)
        mid_count = max(1, min(8, int(opts.get('mid_ctrl_count') or 1)))
        row_count = max(4, len(joints))
        component_node = cc.resolve_component_node(instance.id)

        nulls_grp = nodes.ensure_nulls_grp()
        setup_grp = f'{chain}_spine_setup_grp'
        if cmds.objExists(setup_grp):
            cmds.delete(setup_grp)
        setup_grp = cmds.createNode('transform', name=setup_grp,
                                    parent=nulls_grp)
        cmds.setAttr(f'{setup_grp}.inheritsTransform', 0)
        cmds.setAttr(f'{setup_grp}.visibility', 0)

        # Surface (substrate; identical recipe to Ribbon).
        _, _, up_world = cc.resolve_axes(joints)
        width = rc.resolve_ribbon_width(opts, joints)
        center_crv = cc.build_center_curve(joints, row_count, setup_grp,
                                           f'{chain}_spine_center_crv',
                                           component_node, 'ribbon_dg_nodes')
        cvs = [cmds.pointPosition(f'{center_crv}.cv[{k}]', world=True)
               for k in range(row_count)]
        side_world = rc.side_world_per_cv(joints, cvs)
        surf = rc.build_surface(chain, center_crv, row_count, width,
                                side_world, setup_grp, up_world,
                                component_node)

        # Control joints at even arc fractions: hip, mids..., chest.
        crv_shape = cc.deformed_shape(center_crv, 'nurbsCurve')
        total = mid_count + 2
        positions = []
        for i in range(total):
            param = i / (total - 1)
            positions.append(cmds.pointOnCurve(crv_shape, top=True,
                                               parameter=param, position=True))
        control_joints = cc.build_control_joints(
            f'{chain}_spine_cj_', positions, up_world, setup_grp,
            component_node, 'control_joints')
        skin = rc.skin_surface(chain, control_joints, surf, row_count,
                               component_node, mode='falloff')

        # Controls: hip + chest under the parent plug; mids float on aimers.
        persisted = instance.persisted or {}
        cv_block = ({} if context.options.get('reset_ctrl_shapes')
                    else (persisted.get('cv_data') or {}))
        parent_ctrl = context.resolve_plug(instance.parent_plug).split('.')[0]
        if not cmds.objExists(parent_ctrl):
            parent_ctrl = nodes.ensure_controls_grp()

        # --- COG: whole-system mover, built FIRST among controls (Task 12
        # item 2, "yes COG exactly"). COLLISION GUARD: 'cog_ctrl' is a bare
        # name with no chain prefix -- if another instance (or a hand-made
        # transform) already holds it, fall back to the chain-prefixed name
        # with a loud warning. Positioned/oriented at joints[0] (pelvis, the
        # raw bind joint -- not a control joint), parented under the
        # RESOLVED PARENT PLUG (same target the other ctrls used to parent
        # to directly). Scale locked (global scale lives upstream);
        # translate/rotate stay keyable -- the settings-ctrl idiom, scoped
        # to scale + visibility only.
        cog_name = 'cog_ctrl'
        if cmds.objExists(cog_name):
            cog_name = f'{chain}_spine_cog_ctrl'
            cmds.warning(f"RibbonSpine: 'cog_ctrl' already exists in the "
                        f"scene; this spine's COG control falls back to "
                        f"{cog_name!r}.")
        cog_buf = cmds.createNode('transform', name=f'{cog_name}_offset')
        cmds.matchTransform(cog_buf, joints[0], position=True, rotation=True,
                            scale=False)
        cmds.parent(cog_buf, parent_ctrl)
        cmds.matchTransform(cog_buf, joints[0], position=True, rotation=True,
                            scale=False)
        cog_ctrl = com.build_shape(opts.get('cog_ctrl_shape', 'circle'),
                                   cog_name)
        cmds.parent(cog_ctrl, cog_buf)
        for a in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{cog_ctrl}.{a}', 0)
        for a in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{cog_ctrl}.{a}', 1)
        _apply_color(cog_ctrl, opts.get('ctrl_color', 'yellow'))
        for a in ('scaleX', 'scaleY', 'scaleZ', 'visibility'):
            cmds.setAttr(f'{cog_ctrl}.{a}', lock=True, keyable=False,
                        channelBox=False)
        nodes.tag_ctrl(cog_ctrl, 'spine_cog_ctrl', component_node=component_node,
                       joint_index=-1)
        # World-flat shape (Adrian 2026-07-11): the COG buffer is oriented
        # to the pelvis, so the library shape would draw tilted with it.
        # Counter-rotate the fresh CVs so the DRAWN shape reads axis-
        # aligned in world; the ctrl transform keeps its orientation. An
        # authored cv_block restore (below) overwrites verbatim and wins —
        # captured CVs are object-space and already carry any prior
        # flatten, so rebuilds never double-rotate.
        com.flatten_ctrl_shape_to_world(cog_ctrl)
        cc._restore_shape(com, cog_ctrl, cv_block.get(cog_ctrl))

        # Persist the resolved name immediately -- not only via unbuild's
        # return -- so a collision-fallback name survives to the very next
        # unbuild even without an intervening rebuild cycle. Without this,
        # unbuild has no record of which of the two candidate names THIS
        # instance actually built (it falls back to trying both, but the
        # persisted name is authoritative when present).
        if component_node:
            persist_now = dict(persisted)
            persist_now['cog_ctrl_name'] = cog_name
            nodes.set_component_persisted(component_node, persist_now)

        def _make_ctrl(tag, shape_key, pos_joint, role, k, parent_node,
                       world_flat=False):
            buf = cmds.createNode('transform',
                                  name=f'{chain}_spine_{tag}_ctrl_offset')
            cmds.matchTransform(buf, pos_joint, position=True, rotation=True,
                                scale=False)
            cmds.parent(buf, parent_node)
            cmds.matchTransform(buf, pos_joint, position=True, rotation=True,
                                scale=False)
            ctrl = com.build_shape(opts.get(shape_key, 'cube'),
                                   f'{chain}_spine_{tag}_ctrl')
            cmds.parent(ctrl, buf)
            for a in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
                cmds.setAttr(f'{ctrl}.{a}', 0)
            for a in ('sx', 'sy', 'sz'):
                cmds.setAttr(f'{ctrl}.{a}', 1)
            _apply_color(ctrl, opts.get('ctrl_color', 'yellow'))
            _apply_channels(ctrl, opts.get('channels', {}))
            nodes.tag_ctrl(ctrl, role, component_node=component_node,
                           joint_index=k)
            if world_flat:
                # See the COG's flatten note above — same contract: fresh
                # library CVs counter-rotated to read world-flat, authored
                # cv_block restore below wins verbatim.
                com.flatten_ctrl_shape_to_world(ctrl)
            cc._restore_shape(com, ctrl, cv_block.get(ctrl))
            return buf, ctrl

        # Hip/chest/mid buffers route through the COG now, not the parent
        # plug directly -- the COG is the new whole-system mover between
        # them and whatever this component's own parent plug is.
        _, hip_ctrl = _make_ctrl('hip', 'ctrl_shape', control_joints[0],
                                 'spine_hip_ctrl', 0, cog_ctrl,
                                 world_flat=True)
        _, chest_ctrl = _make_ctrl('chest', 'chest_ctrl_shape', control_joints[-1],
                                   'spine_chest_ctrl', total - 1, cog_ctrl,
                                   world_flat=True)
        mid_ctrls = []
        for m in range(mid_count):
            # Mids joined the world-flat set 2026-07-10 (Adrian: circle
            # shape, drawn world-flat like hip/COG/chest — the sphere
            # exemption retired with the shape change).
            mbuf, mctrl = _make_ctrl(f'mid_{m:02d}', 'mid_ctrl_shape',
                                     control_joints[m + 1], 'spine_mid_ctrl',
                                     m + 1, cog_ctrl, world_flat=True)
            # Always-on aimer: position between neighbors, orientation aimed
            # with object-rotation up (orient constraints flip; banned here).
            t = (m + 1) / (total - 1)
            # Add targets ONE CALL AT A TIME, each with its final weight= and
            # maintainOffset=True, while mbuf still rests at its bind spot
            # (fix round, Task 8 panel): a single two-target pointConstraint
            # call captures maintainOffset against the CREATION-DEFAULT
            # weights (normalized 0.5/0.5 == the hip/chest midpoint), then
            # the weightAliasList setAttrs happen AFTER — every mid with
            # t != 0.5 rests off its bind row by the (midpoint - weighted)
            # delta (measured: mid_00 at y=4 vs bind row y=6 on a 12-unit
            # spine). Adding incrementally makes Maya recompute the offset
            # against the CURRENT (bind) position at each add, with the
            # live weight already set, so the final rest == bind.
            pc = cmds.pointConstraint(hip_ctrl, mbuf, weight=1.0 - t,
                                      maintainOffset=True)[0]
            cmds.pointConstraint(chest_ctrl, mbuf, weight=t,
                                 maintainOffset=True)
            # Verify (not set) the weights landed on the right targets —
            # weightAliasList is still the only robust way to resolve which
            # attr is which (never string-built '<name>W<index>' aliases).
            aliases = cmds.pointConstraint(pc, q=True, weightAliasList=True)
            targets = cmds.pointConstraint(pc, q=True, targetList=True)
            weights = [cmds.getAttr(f'{pc}.{a}') for a in aliases]
            expected = {hip_ctrl: 1.0 - t, chest_ctrl: t}
            for tgt, w in zip(targets, weights):
                exp_w = expected.get(tgt)
                if exp_w is not None and abs(w - exp_w) > 1e-6:
                    raise RuntimeError(
                        f'{mbuf}: pointConstraint weight for {tgt} is {w}, '
                        f'expected {exp_w}')
            cmds.aimConstraint(chest_ctrl, mbuf, maintainOffset=True,
                               aimVector=(1, 0, 0), upVector=(0, 1, 0),
                               worldUpType='objectrotation',
                               worldUpVector=(0, 1, 0),
                               worldUpObject=chest_ctrl)
            mid_ctrls.append(mctrl)

        # Register every LIVE bind joint as a joint_out_i space provider --
        # these point at the BIND JOINTS (not controls), because an interior
        # joint (e.g. spine04) has no control of its own; a child component
        # (a clavicle FK, say) needs the joint's own deformation to follow.
        # Capped at _MAX_JOINT_OUTPUTS (the Contract only declares that many
        # Plugs); joint_out_k for k >= len(joints) stays declared-but-
        # unregistered, which context.has_plug's filter (build_modules'
        # space-provider enumeration) already treats as "not a provider"
        # without any extra guard here.
        registered_joint_count = min(len(joints), _MAX_JOINT_OUTPUTS)
        for i in range(registered_joint_count):
            context.register_output(instance.id, f'joint_out_{i}',
                                    f'{joints[i]}.worldMatrix[0]')
        if len(joints) > _MAX_JOINT_OUTPUTS:
            cmds.warning(
                f'RibbonSpine: {len(joints)} bind joints exceeds the '
                f'joint_out cap of {_MAX_JOINT_OUTPUTS}; only '
                f'joint_out_0..joint_out_{_MAX_JOINT_OUTPUTS - 1} were '
                f'registered ({len(joints) - _MAX_JOINT_OUTPUTS} trailing '
                f'joint(s) have no parent-space output).')

        all_ctrls = [hip_ctrl] + mid_ctrls + [chest_ctrl]
        cc.drive_control_joints('ribbon_dg_nodes', all_ctrls, control_joints,
                                component_node)

        # Board + dynamics + ride. The board lives on the COG now (no
        # dedicated settings ctrl for the spine -- see the contract
        # description); add_board_attrs adds the 10 BOARD_ATTRS onto the
        # already-built cog_ctrl in place. cog_ctrl keeps its own
        # 'spine_cog_ctrl' role tag (fab_role is a single scalar --
        # add_board_attrs leaves an existing, different role alone).
        rc.add_board_attrs(cog_ctrl, component_node, role='spine_settings_ctrl')
        settings = cog_ctrl
        t_copy, t_def, t_handle = rc.build_sculpt_target(
            chain, surf, setup_grp, 'twist', component_node, joints)
        s_copy, s_def, s_handle = rc.build_sculpt_target(
            chain, surf, setup_grp, 'sine', component_node, joints)
        bs = rc.add_board_blend(chain, surf, [t_copy, s_copy], skin,
                                component_node)
        rc.wire_board(settings, bs, t_def, s_def, s_handle,
                      {'twist': 0, 'sine': 1})
        rc.build_jiggle(chain, surf, row_count, skin, settings,
                        component_node)

        # Ride BEFORE volume (delta from the plan's literal Step 8.2 draft,
        # which ordered build_ride_uvpin last): build_volume wires its
        # compensating scale onto the ride's per-joint {bj}_ribbon_pin_dm
        # nodes and raises RuntimeError if they don't exist yet (guard
        # added in Task 7's build_volume). The codebase's precondition is
        # authoritative over the plan's ordering.
        rc.build_ride_uvpin(chain, joints, surf, setup_grp, component_node)

        # CP3 Manny feedback: the falloff skin hard-pins END ROW POSITIONS
        # to the end control joints, but the ride's rotate path still reads
        # the uvPin surface tangent at the V boundary -- shaped by the
        # falloff-blended penultimate row, which does not rotate perfectly
        # rigidly with the end control. Rotating the hip/chest ctrl left
        # the tip/root bind joint's position fixed but tilted its
        # ORIENTATION in place, arcing whatever is parented under it
        # (clavicles/neck off the tip, legs off the root). Lock both end
        # bind joints' rotation rigidly to their own end CONTROL joints
        # instead -- the standard spine contract. Interior joints (the free
        # twist distribution) and generic Ribbon (tail tip should follow
        # the surface frame) are untouched.
        rc.lock_joint_orient_to(joints[0], control_joints[0], component_node)
        rc.lock_joint_orient_to(joints[-1], control_joints[-1], component_node)

        rc.build_volume(
            chain, surf, setup_grp, joints, settings,
            (persisted.get('spine_settings') or {}).get('rest_len'),
            component_node,
            root_matrix_plug=f'{cog_ctrl}.worldMatrix[0]')
        rc.apply_board(settings, persisted.get('spine_settings') or {})

        # Global-scale: deliberately NO root.scale -> setup_grp wire (antCGi
        # needed it for unconstrained control groups; here the controls carry
        # scale to the control joints via drive_control_joints' constraints,
        # the skin scales the surface, and the pins follow). Step 4.4 finding:
        # adding that wire COMPOUNDS scale (4x positions). The Task 8 headless
        # check asserts the scale contract instead; only add wiring if the
        # check proves it necessary.

        context.register_output(instance.id, 'start_out',
                                f'{hip_ctrl}.worldMatrix[0]')
        context.register_output(instance.id, 'tip_out',
                                f'{chest_ctrl}.worldMatrix[0]')

    @classmethod
    def unbuild(cls, instance) -> dict:
        from maya_tools.utils.maya import network_nodes as nn
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator.modules.world import _capture_channels

        joints = list(instance.joints)
        chain = cc.chain_name(joints)
        component_node = cc.resolve_component_node(instance.id)
        mid_count = max(1, min(8, int(instance.persisted.get('mid_ctrl_count')
                                      or instance.options.get('mid_ctrl_count')
                                      or 1)))

        # COG name resolution (Task 12 item 2): persisted first -- build()
        # writes 'cog_ctrl_name' the moment it resolves the name, collision
        # or not, so this is authoritative whenever present. Fallback tries
        # both candidates: the chain-prefixed fallback name first (it can
        # only ever belong to THIS instance's chain, so it disambiguates
        # cleanly), then the bare global name.
        cog_fallback_name = f'{chain}_spine_cog_ctrl'
        cog_name = instance.persisted.get('cog_ctrl_name') or ''
        if not (cog_name and cmds.objExists(cog_name)):
            if cmds.objExists(cog_fallback_name):
                cog_name = cog_fallback_name
            elif cmds.objExists('cog_ctrl'):
                cog_name = 'cog_ctrl'
            else:
                cog_name = ''

        # The board lives on the COG now (no separate settings ctrl for the
        # spine -- see build()); capture_board reads cog_ctrl directly. The
        # persistence KEY stays 'spine_settings' below so an in-place
        # rebuild of a Manny spine built under the OLD separate-settings-
        # ctrl scheme still round-trips its dial values.
        board = (rc.capture_board(cog_name)
                 if cog_name and cmds.objExists(cog_name) else {})
        ratio = f'{chain}_ribbon_vol_ratio'
        rest_node = f'{chain}_ribbon_vol_rest'
        if cmds.objExists(rest_node):
            # True constant rest_len lives on the multDoubleLinear's input1
            # (build_volume wires rest_len * rootScaleX -> ratio.input2X when
            # root_matrix_plug is given -- RibbonSpine always passes one).
            # Reading ratio.input2X directly would persist rest_len x
            # rootScaleX whenever unbuilt while the rig is globally scaled
            # (fix round, Task 8 panel; measured 24.0 vs true 12.0 at root
            # scale 2).
            board['rest_len'] = float(cmds.getAttr(f'{rest_node}.input1'))
        elif cmds.objExists(ratio):
            board['rest_len'] = float(cmds.getAttr(f'{ratio}.input2X'))

        names = ([f'{chain}_spine_hip_ctrl', f'{chain}_spine_chest_ctrl']
                 + [f'{chain}_spine_mid_{m:02d}_ctrl' for m in range(mid_count)])
        if cog_name:
            names.append(cog_name)
        cv_data, channels = {}, {}
        for name in names:
            if not cmds.objExists(name):
                continue
            try:
                sd = com.serialize_shape(name)
                if sd.get('shapes'):
                    cv_data[name] = sd['shapes']
            except RuntimeError:
                pass
        hip = f'{chain}_spine_hip_ctrl'
        if cmds.objExists(hip):
            channels = _capture_channels(hip)

        if component_node:
            for j in joints:
                if not cmds.objExists(j):
                    continue
                for ch in ('translate', 'rotate', 'scaleX', 'scaleY', 'scaleZ'):
                    for src in cmds.listConnections(f'{j}.{ch}', s=True,
                                                    d=False, plugs=True) or []:
                        cmds.disconnectAttr(src, f'{j}.{ch}')
                cmds.setAttr(f'{j}.scale', 1, 1, 1)
                # Restore Maya's joint default; the ride forced this off.
                cmds.setAttr(f'{j}.segmentScaleCompensate', 1)
            for bucket in ('ribbon_dg_nodes', 'ribbon_skin_nodes',
                           'ribbon_board_nodes', 'control_joints'):
                for n in nn.get_message_targets(component_node, bucket):
                    if cmds.objExists(n) and cmds.nodeType(n) == 'skinCluster':
                        for dp in cmds.listConnections(n, type='dagPose') or []:
                            if cmds.objExists(dp):
                                cmds.delete(dp)
                    if cmds.objExists(n):
                        cmds.delete(n)
            # Ctrl buffers live under the parent plug, not in a bucket.
            for name in names:
                buf = f'{name}_offset'
                if cmds.objExists(buf):
                    cmds.delete(buf)

        return {
            'starting_shape': instance.options.get('ctrl_shape', 'hip_ctrl'),
            'cv_data': cv_data,
            'channels': channels,
            'mid_ctrl_count': mid_count,
            'ribbon_width': float(instance.persisted.get('ribbon_width')
                                  or instance.options.get('ribbon_width')
                                  or 0.0),
            'spine_settings': board,
            'cog_ctrl_name': cog_name,
            'enum_orders': {},
        }
