# Python/maya_tools/rigging/fabricator/components/simple_fk.py
"""SimpleFKComponent — single-joint FK control with offset buffer.

Single joint. Has parent_in matrix input. Exposes ctrl_out.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator.modules.component import (
    Component, Contract, MirrorRule, OptionField, Plug,
)


SIMPLE_FK_CONTRACT = Contract(
    type='SimpleFK',
    display_name='FK',
    description='Single-joint FK control with offset buffer.',
    min_joints=1,
    max_joints=1,
    parent_strategy='walk_up',
    inputs=(
        Plug(name='parent_in', kind='matrix', required=True,
             description='Parent transform space.'),
    ),
    outputs=(
        Plug(name='ctrl_out', kind='matrix', space_target=True,
             description='World matrix at the ctrl.'),
    ),
    options_schema={
        'ctrl_shape': OptionField(type='shape_enum', default='capsule',
                                  description='Curve shape for the FK ctrl.'),
        'ctrl_color': OptionField(type='color_enum', default='ice_blue',
                                  description='Override color for the ctrl.'),
        'free_float_space': OptionField(type='bool', default=False),
        'anim_pivot':       OptionField(type='bool', default=False),
        'channels': OptionField(type='channels',
                                default={'keyable': ['tx', 'ty', 'tz',
                                                     'rx', 'ry', 'rz']},
                                description='Channelbox setup.'),
    },
    side_supported=True,
    color='#C7D9F2',  # ice blue — FK family, lightest
    mirror_rules=(
        # FK ctrls live in pre-mirrored joint frames: same Euler values
        # on L and R produce mirror poses, so rotations copy verbatim
        # across the pair. Translations flip — the joint frame is pre-
        # mirrored for rotations but translates resolve in world axes.
        MirrorRule(
            ctrl_role='fk_ctrl',
            negate=frozenset({'translateX', 'translateY', 'translateZ'}),
        ),
    ),
)


class SimpleFKComponent(Component):
    CONTRACT = SIMPLE_FK_CONTRACT

    @classmethod
    def build(cls, instance, context) -> None:
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator.modules.world import _apply_color, _apply_channels

        from maya_tools.rigging.fabricator import nodes

        joint = instance.joints[0]
        if not cmds.objExists(joint):
            raise RuntimeError(f"SimpleFK: joint {joint!r} doesn't exist.")

        opts = instance.options
        shape = opts.get('ctrl_shape', 'circle')
        color = opts.get('ctrl_color', 'yellow')
        short = joint.split('|')[-1].split(':')[-1]

        # Null pair under fab_nulls_grp.
        offset_null, connector_null = nodes.build_null_pair(joint)

        # offset_ctrl + ctrl. offset_ctrl parents under the parent's ctrl
        # (resolved via parent_plug), falling back to fab_controls_grp if the
        # parent ctrl isn't around (defensive — validator should catch).
        offset_ctrl_name = f'{short}_ctrl_offset'
        ctrl_name = f'{short}_ctrl'

        parent_matrix_plug = context.resolve_plug(instance.parent_plug)
        parent_ctrl = parent_matrix_plug.split('.')[0]
        if not cmds.objExists(parent_ctrl):
            parent_ctrl = nodes.ensure_controls_grp()

        offset_ctrl = cmds.createNode('transform', name=offset_ctrl_name)
        cmds.matchTransform(offset_ctrl, joint, position=True, rotation=True, scale=False)

        # If parent_ctrl is a joint (e.g. resolved from a SimpleIK bind output
        # like arm_l.end_out → hand_l), don't parent under it — that pollutes
        # the joint hierarchy and survives unbuild's rig_grp delete cascade.
        # Instead parent under controls_grp and constrain to follow the joint.
        if cmds.nodeType(parent_ctrl) == 'joint':
            cmds.parent(offset_ctrl, nodes.ensure_controls_grp())
            cmds.matchTransform(offset_ctrl, joint, position=True, rotation=True, scale=False)
            cmds.parentConstraint(parent_ctrl, offset_ctrl, mo=True)
            cmds.scaleConstraint(parent_ctrl, offset_ctrl, mo=True)
        else:
            cmds.parent(offset_ctrl, parent_ctrl)
            # Re-snap after parenting — local transforms shift relative to new parent.
            cmds.matchTransform(offset_ctrl, joint, position=True, rotation=True, scale=False)

        ctrl = com.build_shape(shape, ctrl_name,
                               radius=cmds.getAttr(f'{joint}.radius'))
        cmds.parent(ctrl, offset_ctrl)
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{ctrl}.{attr}', 1)

        _apply_color(ctrl, color)

        # Apply persisted CVs. Backward compat: legacy schema = single
        # shape dict; current schema = list of shape dicts (preserves
        # multi-shape library curves like sphere on roundtrip).
        # reset_ctrl_shapes build option suppresses the restore so the
        # ctrl gets the default library shape.
        persisted = instance.persisted
        cv_data = (persisted.get('cv_data') or {}).get(shape)
        if context.options.get('reset_ctrl_shapes'):
            cv_data = None
        if cv_data:
            shapes_list = cv_data if isinstance(cv_data, list) else [cv_data]
            try:
                com.deserialize_shape_to(ctrl, {'shapes': shapes_list})
            except Exception:
                pass

        # Constraint chain: ctrl → connector_null → joint.
        cmds.parentConstraint(ctrl, connector_null, maintainOffset=True)
        cmds.scaleConstraint(ctrl, connector_null, maintainOffset=True)
        cmds.parentConstraint(connector_null, joint, maintainOffset=True)
        cmds.scaleConstraint(connector_null, joint, maintainOffset=True)

        _apply_channels(ctrl, opts.get('channels', {}))

        # Tag for marking-menu / pose library lookup.
        component_node = next(
            (cn for cn in nodes.get_all_component_nodes()
             if nodes.get_component_id(cn) == instance.id),
            '',
        )
        nodes.tag_ctrl(ctrl, 'fk_ctrl',
                       component_node=component_node, joint_index=0)

        context.register_output(instance.id, 'ctrl_out', f'{ctrl}.worldMatrix[0]')

    @classmethod
    def unbuild(cls, instance) -> dict:
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator.modules.world import _capture_channels

        joint = instance.joints[0]
        short = joint.split('|')[-1].split(':')[-1]
        ctrl = f'{short}_ctrl'
        current_shape = instance.options.get('ctrl_shape', 'circle')

        cv_data_block = {}
        if cmds.objExists(ctrl):
            try:
                shape_data = com.serialize_shape(ctrl)
                if shape_data.get('shapes'):
                    cv_data_block[current_shape] = shape_data['shapes']
            except RuntimeError:
                pass

        captured = {
            'starting_shape': current_shape,
            'cv_data': cv_data_block,
            'channels': _capture_channels(ctrl) if cmds.objExists(ctrl) else {},
            'enum_orders': {},
        }

        # Delete constraints on the joint — children of the joint, not under
        # rig_grp, so the rig_grp cascade leaves them dangling otherwise.
        if cmds.objExists(joint):
            constraints = (
                (cmds.listRelatives(joint, type='parentConstraint') or [])
                + (cmds.listRelatives(joint, type='scaleConstraint') or [])
            )
            if constraints:
                cmds.delete(constraints)

        return captured
