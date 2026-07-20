# Python/maya_tools/rigging/fabricator/modules/advanced_fk.py
"""AdvancedFKComponent — world-anchored single-joint FK with space switching.

Same drive chain as SimpleFK (offset_null + connector_null + ctrl) but
the ctrl is parented directly under fab_controls_grp (parent_strategy
'world'), not walked up to an ancestor ctrl. The ctrl captures
fab_bind_matrix at build time; the post-build space-switch wiring
pass (in fs_app.build_modules) reads the component's `spaces`
message-multi, assembles providers (World + Parent + user-added
joints), and connects build_space_switch_dg → ctrl.offsetParentMatrix.

Tag: fab_role='advfk_ctrl' so the marking menu's Switch Space /
Match Other Space actions appear on right-click in the viewport.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator.modules.component import (
    Component, Contract, MirrorRule, OptionField, Plug, SpaceConsumer,
)


ADVANCED_FK_CONTRACT = Contract(
    type='AdvancedFK',
    display_name='FK Advanced',
    description='World-anchored FK with user-configurable space switching. '
                'Defaults to World + Parent; the rigger adds more joints '
                'by name in the Properties Spaces section.',
    min_joints=1,
    max_joints=1,
    parent_strategy='world',
    inputs=(),
    outputs=(
        Plug(name='ctrl_out', kind='matrix', space_target=True,
             description='AdvancedFK ctrl worldMatrix — usable as a '
                         'space target by other AdvancedFKs.'),
    ),
    options_schema={
        'ctrl_shape': OptionField(type='shape_enum', default='capsule',
                                  description='Curve shape for the ctrl.'),
        'ctrl_color': OptionField(type='color_enum', default='sky_blue',
                                  description='Override color for the ctrl.'),
        'channels': OptionField(type='channels',
                                default={'keyable': ['tx', 'ty', 'tz',
                                                     'rx', 'ry', 'rz']},
                                description='Channelbox setup. '
                                            'AdvancedFKs default to keyable '
                                            'translation AND rotation since '
                                            'space-switched ctrls are typically '
                                            'positionable accessories.'),
    },
    side_supported=True,
    color='#73A6FF',  # sky blue — FK family
    space_consumers=(
        SpaceConsumer(
            ctrl_role='advfk_ctrl',
            attr_name='space',
            defaults=('parent', 'world'),
            default='parent',
        ),
    ),
    # No Contract.actions needed — animation_menu._synthesize_space_actions
    # walks space_consumers and synthesizes marking-menu entries from the
    # live ctrl enum at trigger time. The 'parent' and 'world' defaults
    # plus any user-added joints all appear in the menu automatically.
    actions=(),
    mirror_rules=(
        # Same convention as SimpleFK: rotations copy verbatim across
        # the pair; translations flip.
        MirrorRule(
            ctrl_role='advfk_ctrl',
            negate=frozenset({'translateX', 'translateY', 'translateZ'}),
        ),
    ),
)


class AdvancedFKComponent(Component):
    CONTRACT = ADVANCED_FK_CONTRACT

    @classmethod
    def build(cls, instance, context) -> None:
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator.modules.world import (
            _apply_color, _apply_channels,
        )
        from maya_tools.rigging.fabricator import nodes

        joint = instance.joints[0]
        if not cmds.objExists(joint):
            raise RuntimeError(f"AdvancedFK: joint {joint!r} doesn't exist.")

        opts = instance.options
        shape = opts.get('ctrl_shape', 'capsule')
        color = opts.get('ctrl_color', 'yellow')
        short = joint.split('|')[-1].split(':')[-1]

        # Null pair under fab_nulls_grp — same as SimpleFK.
        offset_null, connector_null = nodes.build_null_pair(joint)

        # Single world-anchored ctrl (no offset_ctrl). Matches the
        # SimpleIK end-ctrl pattern: ctrl parented under controls_grp,
        # bind matrix captured, local TRS zeroed. The wtAddMatrix from
        # the space-switch wiring pass drives ctrl.offsetParentMatrix
        # so the ctrl appears at the joint position by default and
        # animator-zero-out returns it to bind.
        ctrl_name = f'{short}_ctrl'
        ctrl = com.build_shape(shape, ctrl_name,
                               radius=cmds.getAttr(f'{joint}.radius'))
        cmds.parent(ctrl, nodes.ensure_controls_grp())

        cmds.matchTransform(ctrl, joint, position=True, rotation=True, scale=False)
        ctrl_bind = cmds.xform(ctrl, q=True, ws=True, matrix=True)
        # fab_bind_matrix is stamped by nodes.tag_ctrl below; ctrl_bind is
        # captured here for the OPM setAttr that follows (same value, but the
        # ctrl gets repositioned after this snapshot via TRS-zero + OPM-set).

        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{ctrl}.{attr}', 1)

        # Static offsetParentMatrix at bind so the ctrl is visually at
        # the joint during the constraint pass below. The space-switch
        # wiring pass (in fs_app.build_modules) replaces this with a
        # wtAddMatrix output force-connection.
        cmds.setAttr(f'{ctrl}.offsetParentMatrix', *ctrl_bind, type='matrix')

        _apply_color(ctrl, color)

        # Apply persisted CVs if present (matches SimpleFK). Backward
        # compat: legacy schema = single shape dict; current schema =
        # list of shape dicts (preserves multi-shape library curves).
        # reset_ctrl_shapes build option suppresses the restore.
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

        # Tag for marking-menu + pose-library lookup.
        component_node = next(
            (cn for cn in nodes.get_all_component_nodes()
             if nodes.get_component_id(cn) == instance.id),
            '',
        )
        nodes.tag_ctrl(ctrl, 'advfk_ctrl',
                       component_node=component_node, joint_index=0)

        context.register_output(instance.id, 'ctrl_out',
                                f'{ctrl}.worldMatrix[0]')

    @classmethod
    def unbuild(cls, instance) -> dict:
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator.modules.world import _capture_channels

        joint = instance.joints[0]
        short = joint.split('|')[-1].split(':')[-1]
        ctrl = f'{short}_ctrl'
        current_shape = instance.options.get('ctrl_shape', 'capsule')

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

        # Delete constraints on the joint — children of the joint, not
        # under rig_grp, so the rig_grp cascade leaves them dangling.
        if cmds.objExists(joint):
            constraints = (
                (cmds.listRelatives(joint, type='parentConstraint') or [])
                + (cmds.listRelatives(joint, type='scaleConstraint') or [])
            )
            if constraints:
                cmds.delete(constraints)

        return captured
