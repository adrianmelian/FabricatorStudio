# Python/maya_tools/rigging/fabricator/modules/fk_aim.py
"""FKAimComponent — single-joint FK ctrl driven by an aim target in front of it.

Eye-rig substrate. Each FKAim creates:
  - An FK ctrl on the joint (standard offset_ctrl + ctrl pair, like SimpleFK).
  - An aim ctrl positioned `aim_distance` in front of the joint along
    `aim_axis` (joint local axis, expressed in worldspace via the joint's
    world matrix column).
  - An aimConstraint from the aim ctrl onto the FK ctrl.
  - The usual connector_null chain driving the joint from the FK ctrl.

Multiple FKAim instances can share a `link_group` string. Matching
instances get a single shared "link ctrl" (named `<link_group>_aim_ctrl`)
parented under fab_controls_grp (so it sits at the top of the rig
alongside the World ctrl). Each instance's aim ctrl is parented under
that shared link ctrl instead of under its own parent ctrl, so moving
the link ctrl moves every aim target together.

The shared link ctrl gets a post-build space switch (world / root /
each unique parent component) wired in fs_app._apply_post_build_fk_aim_links.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator.modules.component import (
    Component, Contract, MirrorRule, OptionField, Plug,
)


FK_AIM_CONTRACT = Contract(
    type='FKAim',
    display_name='FK Aim',
    description=(
        'FK ctrl driven by an aim ctrl; multiple instances can share a '
        'link_group parent ctrl with space switching.'
    ),
    min_joints=1,
    max_joints=1,
    parent_strategy='walk_up',
    inputs=(
        Plug(name='parent_in', kind='matrix', required=True,
             description='Parent transform space.'),
    ),
    outputs=(
        Plug(name='ctrl_out', kind='matrix', space_target=True,
             description='World matrix at the FK ctrl.'),
        Plug(name='aim_ctrl_out', kind='matrix', space_target=True,
             description='World matrix at the aim ctrl.'),
    ),
    options_schema={
        'ctrl_shape':   OptionField(type='shape_enum', default='capsule',
                                    description='Curve shape for the FK ctrl.'),
        'aim_shape':    OptionField(type='shape_enum', default='crosshair',
                                    description='Curve shape for the aim ctrl.'),
        'link_shape':   OptionField(type='shape_enum', default='eye_mask',
                                    description='Curve shape for the linked '
                                                'parent ctrl (e.g. eyes_ctrl). '
                                                'Used only when link_group is set.'),
        'ctrl_color':   OptionField(type='color_enum', default='blue',
                                    description='Override color for the ctrls.'),
        'aim_distance': OptionField(type='float', default=30.0,
                                    description='How far in front of the joint '
                                                'the aim ctrl sits (scene units).'),
        # 'enum' (not 'string') so the Properties panel surfaces a dropdown
        # with the 6 valid axis values. Validator enforces choices on both
        # types, but only 'enum' wires choices into the widget itself.
        'aim_axis':     OptionField(type='enum', default='+z',
                                    choices=('+x', '-x', '+y', '-y', '+z', '-z'),
                                    description='Which local axis on the joint '
                                                'aims at the target.'),
        'link_group':   OptionField(
            type='string_choices', default='',
            suggestions_source=(
                'maya_tools.rigging.fabricator.modules.fk_aim.'
                'discover_link_groups'
            ),
            description='Shared name across FKAim instances that should be '
                        'moved together by a common parent ctrl. Leave empty '
                        'for solo.',
        ),
        'channels':     OptionField(type='channels',
                                    default={'keyable': ['tx', 'ty', 'tz',
                                                         'rx', 'ry', 'rz']},
                                    description='Channelbox setup.'),
    },
    side_supported=True,
    color='#2659FF',  # blue — FK family, deepest
    mirror_rules=(
        # Same convention as SimpleFK — FK ctrls live in pre-mirrored
        # joint frames, so rotations copy verbatim across the pair and
        # translations flip.
        MirrorRule(
            ctrl_role='fk_ctrl',
            negate=frozenset({'translateX', 'translateY', 'translateZ'}),
        ),
        MirrorRule(
            ctrl_role='aim_ctrl',
            negate=frozenset({'translateX', 'translateY', 'translateZ'}),
        ),
    ),
)


# Map of aim_axis string -> (column_index, sign). The column index picks
# which row of the joint's world matrix to use as the aim direction; the
# sign flips for '-' axes.
_AXIS_TO_COLUMN = {
    '+x': (0,  1.0),
    '-x': (0, -1.0),
    '+y': (1,  1.0),
    '-y': (1, -1.0),
    '+z': (2,  1.0),
    '-z': (2, -1.0),
}

# For each aim axis, pick a sensible perpendicular as the up axis. +y up
# is the natural choice for the common '+z' eye-aim convention; other
# axes get a non-collinear default. Format: 'aim_axis' -> ('up_axis',
# upVector_tuple, aimVector_tuple).
_AXIS_VECTORS = {
    '+x': ((1, 0, 0),  (0, 1, 0)),   # aim X, up Y
    '-x': ((-1, 0, 0), (0, 1, 0)),
    '+y': ((0, 1, 0),  (0, 0, 1)),   # aim Y, up Z (Y/Y would be collinear)
    '-y': ((0, -1, 0), (0, 0, 1)),
    '+z': ((0, 0, 1),  (0, 1, 0)),   # aim Z, up Y — classic eye convention
    '-z': ((0, 0, -1), (0, 1, 0)),
}


def _joint_axis_world_vector(joint: str, aim_axis: str) -> tuple:
    """Extract the world-space direction of the joint's local aim axis.

    Maya stores worldMatrix in row-major form (16 floats); the first
    three rows are the local X/Y/Z basis vectors expressed in worldspace.
    Row N (4 floats, drop the last 0) is the worldspace direction of
    local axis N. For '-' axes, negate.
    """
    col, sign = _AXIS_TO_COLUMN.get(aim_axis, (2, 1.0))
    wm = cmds.xform(joint, q=True, ws=True, matrix=True)
    # Rows 0/1/2 = local X/Y/Z basis. Row N starts at index N*4.
    start = col * 4
    vec = (wm[start] * sign, wm[start + 1] * sign, wm[start + 2] * sign)
    # Normalize (matrix rows are already unit length under standard
    # transforms, but be defensive against non-uniform scale).
    mag = (vec[0] ** 2 + vec[1] ** 2 + vec[2] ** 2) ** 0.5
    if mag <= 1e-9:
        return (0.0, 0.0, 1.0)
    return (vec[0] / mag, vec[1] / mag, vec[2] / mag)


class FKAimComponent(Component):
    CONTRACT = FK_AIM_CONTRACT

    @classmethod
    def build(cls, instance, context) -> None:
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator.modules.world import (
            _apply_color, _apply_channels,
        )
        from maya_tools.rigging.fabricator import nodes

        joint = instance.joints[0]
        if not cmds.objExists(joint):
            raise RuntimeError(f"FKAim: joint {joint!r} doesn't exist.")

        opts = instance.options
        fk_shape  = opts.get('ctrl_shape', 'capsule')
        aim_shape = opts.get('aim_shape', 'crosshair')
        link_shape = opts.get('link_shape', 'eye_mask')
        color     = opts.get('ctrl_color', 'yellow')
        aim_distance = float(opts.get('aim_distance', 30.0) or 30.0)
        aim_axis  = str(opts.get('aim_axis', '+z') or '+z')
        link_group = (opts.get('link_group') or '').strip()
        short = joint.split('|')[-1].split(':')[-1]

        # Resolve world-space aim direction up front (used for both ctrl
        # positions AND the aimConstraint vector).
        aim_world_vec = _joint_axis_world_vector(joint, aim_axis)
        aim_vec_local, up_vec_local = _AXIS_VECTORS.get(
            aim_axis, ((0, 0, 1), (0, 1, 0))
        )

        # Null pair under fab_nulls_grp — same as SimpleFK.
        offset_null, connector_null = nodes.build_null_pair(joint)

        # ── FK ctrl ───────────────────────────────────────────────────
        fk_offset_name = f'{short}_fk_ctrl_offset'
        fk_ctrl_name   = f'{short}_fk_ctrl'

        parent_matrix_plug = context.resolve_plug(instance.parent_plug)
        parent_ctrl = parent_matrix_plug.split('.')[0]
        if not cmds.objExists(parent_ctrl):
            parent_ctrl = nodes.ensure_controls_grp()

        fk_offset = cmds.createNode('transform', name=fk_offset_name)
        cmds.matchTransform(fk_offset, joint, position=True, rotation=True,
                            scale=False)

        # Mirror SimpleFK's defensive "if parent is a joint, parent under
        # controls_grp + parentConstraint instead" — pollutes the joint
        # hierarchy otherwise.
        if cmds.nodeType(parent_ctrl) == 'joint':
            cmds.parent(fk_offset, nodes.ensure_controls_grp())
            cmds.matchTransform(fk_offset, joint, position=True,
                                rotation=True, scale=False)
            cmds.parentConstraint(parent_ctrl, fk_offset, mo=True)
            cmds.scaleConstraint(parent_ctrl, fk_offset, mo=True)
        else:
            cmds.parent(fk_offset, parent_ctrl)
            cmds.matchTransform(fk_offset, joint, position=True,
                                rotation=True, scale=False)

        fk_ctrl = com.build_shape(fk_shape, fk_ctrl_name,
                                  radius=cmds.getAttr(f'{joint}.radius'))
        cmds.parent(fk_ctrl, fk_offset)
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{fk_ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{fk_ctrl}.{attr}', 1)
        _apply_color(fk_ctrl, color)

        # Apply persisted CVs (FK ctrl). Backward compat: legacy schema
        # = single shape dict; current schema = list of shape dicts.
        persisted = instance.persisted
        cv_data_block = persisted.get('cv_data') or {}
        fk_cv = cv_data_block.get(fk_shape)
        if context.options.get('reset_ctrl_shapes'):
            fk_cv = None
        if fk_cv:
            shapes_list = fk_cv if isinstance(fk_cv, list) else [fk_cv]
            try:
                com.deserialize_shape_to(fk_ctrl, {'shapes': shapes_list})
            except Exception:
                pass

        # ── Aim ctrl + (optional) link ctrl ───────────────────────────
        joint_pos = cmds.xform(joint, q=True, ws=True, translation=True)
        aim_target_pos = (
            joint_pos[0] + aim_world_vec[0] * aim_distance,
            joint_pos[1] + aim_world_vec[1] * aim_distance,
            joint_pos[2] + aim_world_vec[2] * aim_distance,
        )

        aim_offset_name = f'{short}_aim_ctrl_offset'
        aim_ctrl_name   = f'{short}_aim_ctrl'

        # Resolve the parent for the aim_offset:
        #   solo (link_group empty)   → the same parent_ctrl as the FK ctrl
        #   linked                    → the link_ctrl (find or create).
        component_node = next(
            (cn for cn in nodes.get_all_component_nodes()
             if nodes.get_component_id(cn) == instance.id),
            '',
        )

        link_ctrl_created_this_build = False
        link_ctrl = ''
        if link_group:
            link_ctrl_name = f'{link_group}_aim_ctrl'
            if cmds.objExists(link_ctrl_name):
                link_ctrl = link_ctrl_name
            else:
                link_ctrl = com.build_shape(link_shape, link_ctrl_name,
                                            radius=cmds.getAttr(f'{joint}.radius'))
                cmds.parent(link_ctrl, nodes.ensure_controls_grp())
                # Position at the same spot the aim ctrl would go — first
                # builder owns this; subsequent builders accept whatever
                # is there. The post-build sweep re-anchors to the
                # midpoint of all linked members.
                cmds.xform(link_ctrl, ws=True, translation=aim_target_pos)
                # No CV scaling — the shape's authored size in
                # curve_data/ is what's rendered. Author shapes at
                # the size they should appear.
                _apply_color(link_ctrl, 'yellow')

                # Tag via the standard helper so fab_role,
                # fab_joint_index, fab_bind_matrix, AND the
                # fab_owner message connection all get set up
                # consistently with other ctrls. The pose library
                # walks fab_owner to find which component owns each
                # ctrl — without it, ctrl_to_address raises on the
                # listConnections call.
                #
                # Ownership note: the link_ctrl is logically shared
                # across every linked FKAim, but fab_owner is a
                # 1:1 message link. Pin it to whichever FKAim built
                # this link_ctrl first; the address library only needs
                # SOME owner pointer to derive component_type / side /
                # role for cross-rig matching, and the first builder
                # is a deterministic-enough choice given the build's
                # topological order.
                nodes.tag_ctrl(link_ctrl, 'link_ctrl',
                               component_node=component_node,
                               joint_index=-1)
                # Separate attr for the link_group string — tag_ctrl
                # doesn't know about this and the post-build sweep
                # uses it to re-group members.
                if not cmds.attributeQuery('fab_link_group',
                                           node=link_ctrl, exists=True):
                    cmds.addAttr(link_ctrl, ln='fab_link_group',
                                 dt='string')
                cmds.setAttr(f'{link_ctrl}.fab_link_group', link_group,
                             type='string')

                # Zero the link_ctrl's local TRS. The captured
                # fab_bind_matrix above holds the worldspace pose; the
                # post-build space switch wires offsetParentMatrix to drive
                # the ctrl's world transform. Leaving local TRS non-zero
                # double-applies the offset (worldMatrix = localMatrix ×
                # offsetParentMatrix), and the ctrl ends up at 2× the
                # intended position.
                for attr in ('translate', 'rotate'):
                    cmds.setAttr(f'{link_ctrl}.{attr}', 0, 0, 0)

                # Apply persisted CV for the link ctrl, if the current
                # instance carries one (first-build instances will carry
                # nothing — that's fine, the default envelope sits there).
                link_cv = cv_data_block.get(f'__link__{link_shape}')
                if context.options.get('reset_ctrl_shapes'):
                    link_cv = None
                if link_cv:
                    shapes_list = (
                        link_cv if isinstance(link_cv, list) else [link_cv]
                    )
                    try:
                        com.deserialize_shape_to(
                            link_ctrl, {'shapes': shapes_list}
                        )
                    except Exception:
                        pass

                link_ctrl_created_this_build = True

            aim_offset_parent = link_ctrl
        else:
            aim_offset_parent = parent_ctrl
            if cmds.nodeType(aim_offset_parent) == 'joint':
                aim_offset_parent = nodes.ensure_controls_grp()

        aim_offset = cmds.createNode('transform', name=aim_offset_name)
        cmds.parent(aim_offset, aim_offset_parent)
        # Position at the eye's individual aim_target_pos in worldspace.
        # In the linked case, the post-build sweep re-anchors the link_ctrl
        # to the midpoint of all linked aim_offsets and adjusts each
        # offset's local to keep it at this world position. Re-apply
        # after parenting since inheritsTransform may be False on the
        # controls_grp ancestor.
        cmds.xform(aim_offset, ws=True, translation=aim_target_pos)
        cmds.xform(aim_offset, ws=True, translation=aim_target_pos)

        aim_ctrl = com.build_shape(aim_shape, aim_ctrl_name,
                                   radius=cmds.getAttr(f'{joint}.radius'))
        cmds.parent(aim_ctrl, aim_offset)
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{aim_ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{aim_ctrl}.{attr}', 1)
        _apply_color(aim_ctrl, color)

        aim_cv = cv_data_block.get(f'__aim__{aim_shape}')
        if context.options.get('reset_ctrl_shapes'):
            aim_cv = None
        if aim_cv:
            shapes_list = aim_cv if isinstance(aim_cv, list) else [aim_cv]
            try:
                com.deserialize_shape_to(aim_ctrl, {'shapes': shapes_list})
            except Exception:
                pass

        # ── aimConstraint: aim_ctrl pulls fk_ctrl to face it ──────────
        # worldUpType='vector' with worldUpVector=(0,1,0) gives a stable
        # world-up reference regardless of any rotation in the parent
        # chain. The earlier 'objectrotation' mode tracked the parent
        # ctrl's up axis (natural for head roll), but parent-chain
        # rotation or offsetParentMatrix transforms in the head/spine
        # could leak through as roll on the eye — yaw moves of the aim
        # ctrl produced Z rotation instead of pure Y. Static world up
        # is the simpler, more predictable default.
        cmds.aimConstraint(
            aim_ctrl, fk_ctrl,
            aimVector=aim_vec_local,
            upVector=up_vec_local,
            worldUpType='vector',
            worldUpVector=(0, 1, 0),
            maintainOffset=False,
        )

        # ── Drive chain: fk_ctrl → connector_null → joint ─────────────
        cmds.parentConstraint(fk_ctrl, connector_null, maintainOffset=True)
        cmds.scaleConstraint(fk_ctrl, connector_null, maintainOffset=True)
        cmds.parentConstraint(connector_null, joint, maintainOffset=True)
        cmds.scaleConstraint(connector_null, joint, maintainOffset=True)

        _apply_channels(fk_ctrl, opts.get('channels', {}))
        # Aim ctrl gets the same default channels — translation-only is
        # the usual aim convention, but the user can override via the
        # channels option (which applies to both ctrls).
        _apply_channels(aim_ctrl, opts.get('channels', {}))

        # Tag ctrls.
        nodes.tag_ctrl(fk_ctrl, 'fk_ctrl',
                       component_node=component_node, joint_index=0)
        nodes.tag_ctrl(aim_ctrl, 'aim_ctrl',
                       component_node=component_node, joint_index=0)

        # Register outputs.
        context.register_output(instance.id, 'ctrl_out',
                                f'{fk_ctrl}.worldMatrix[0]')
        context.register_output(instance.id, 'aim_ctrl_out',
                                f'{aim_ctrl}.worldMatrix[0]')

    @classmethod
    def unbuild(cls, instance) -> dict:
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator.modules.world import _capture_channels

        joint = instance.joints[0]
        short = joint.split('|')[-1].split(':')[-1]
        fk_ctrl  = f'{short}_fk_ctrl'
        aim_ctrl = f'{short}_aim_ctrl'
        current_fk_shape   = instance.options.get('ctrl_shape', 'capsule')
        current_aim_shape  = instance.options.get('aim_shape', 'crosshair')
        current_link_shape = instance.options.get('link_shape', 'eye_mask')
        link_group = (instance.options.get('link_group') or '').strip()

        # Capture CVs for both ctrls. Aim CVs go under a name-spaced
        # key so they don't collide with the FK shape's CVs when both
        # options happen to share the same shape name (the common case
        # — both default to 'circle').
        cv_data_block = {}
        if cmds.objExists(fk_ctrl):
            try:
                shape_data = com.serialize_shape(fk_ctrl)
                if shape_data.get('shapes'):
                    cv_data_block[current_fk_shape] = shape_data['shapes']
            except RuntimeError:
                pass
        if cmds.objExists(aim_ctrl):
            try:
                shape_data = com.serialize_shape(aim_ctrl)
                if shape_data.get('shapes'):
                    cv_data_block[f'__aim__{current_aim_shape}'] = (
                        shape_data['shapes']
                    )
            except RuntimeError:
                pass

        # Capture link ctrl CVs too — first-built instance of the group
        # gets to persist them; deletion happens with rig_grp delete
        # cascade regardless of which member captures.
        if link_group:
            link_ctrl_name = f'{link_group}_aim_ctrl'
            if cmds.objExists(link_ctrl_name):
                try:
                    shape_data = com.serialize_shape(link_ctrl_name)
                    if shape_data.get('shapes'):
                        cv_data_block[f'__link__{current_link_shape}'] = (
                            shape_data['shapes']
                        )
                except RuntimeError:
                    pass

        captured = {
            'starting_shape': current_fk_shape,
            'cv_data': cv_data_block,
            'channels': _capture_channels(fk_ctrl) if cmds.objExists(fk_ctrl) else {},
            'enum_orders': {},
        }

        # Delete constraints on the joint and on the FK ctrl. The aim
        # constraint lives on the FK ctrl (children of it), not under
        # rig_grp's null hierarchy, so the rig_grp delete cascade
        # wouldn't necessarily clean them — explicit delete is safer.
        if cmds.objExists(joint):
            constraints = (
                (cmds.listRelatives(joint, type='parentConstraint') or [])
                + (cmds.listRelatives(joint, type='scaleConstraint') or [])
            )
            if constraints:
                cmds.delete(constraints)
        if cmds.objExists(fk_ctrl):
            aim_constraints = (
                cmds.listRelatives(fk_ctrl, type='aimConstraint') or []
            )
            if aim_constraints:
                cmds.delete(aim_constraints)

        # NOTE: do NOT delete the link_ctrl here. It's owned by the
        # link_group, not by any individual instance — multiple
        # instances would race to delete it. rig_grp cascade handles
        # it during full unbuild.

        return captured


# ─── Helpers ────────────────────────────────────────────────────────────────

def discover_link_groups() -> list:
    """Return sorted unique non-empty 'link_group' option values across
    all FKAim component network nodes in the scene.

    Used as the suggestions_source for the link_group OptionField. Once
    one FKAim is named (say) 'eyes', every other FKAim's Properties
    panel surfaces 'eyes' as a dropdown suggestion.
    """
    from maya_tools.rigging.fabricator import nodes as ks_nodes
    seen = set()
    for cnode in ks_nodes.get_all_component_nodes():
        if ks_nodes.get_component_type(cnode) != 'FKAim':
            continue
        opts = ks_nodes.get_component_options(cnode) or {}
        v = (opts.get('link_group') or '').strip()
        if v:
            seen.add(v)
    return sorted(seen)
