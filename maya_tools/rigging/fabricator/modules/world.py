# Python/maya_tools/rigging/fabricator/components/world.py
"""WorldComponent — root world ctrl. First component in any rig.

Single joint (the rig root). No parent_plug. Exposes 'ctrl_out' for
downstream components to attach to.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator.modules.component import (
    Component, Contract, Plug, OptionField,
)


WORLD_CONTRACT = Contract(
    type='World',
    display_name='World',
    description=(
        'Root world control. Must be the first component added — anchors '
        'the rig in worldspace. No parent plug.'
    ),
    min_joints=1,
    max_joints=1,
    parent_strategy='walk_up',  # not actually used; World has no parent_plug
    inputs=(),                  # no parent_plug
    outputs=(
        Plug(name='ctrl_out', kind='matrix', space_target=True,
             description='World ctrl matrix (animator-displaced).'),
        Plug(name='joint_out', kind='matrix', space_target=False,
             description='Root joint matrix (animation-baked).'),
    ),
    options_schema={
        'ctrl_shape': OptionField(type='shape_enum', default='circle_arrow',
                                  description='Curve shape for the world ctrl.'),
        'ctrl_color': OptionField(type='color_enum', default='yellow',
                                  description='Override color for the ctrl.'),
        'channels': OptionField(type='channels',
                                default={'keyable': ['tx', 'ty', 'tz', 'rx', 'ry', 'rz']},
                                description='Channelbox setup.'),
    },
    side_supported=False,  # World is always center
    color='#FFC857',       # plasma yellow — matches the world ctrl default
)


class WorldComponent(Component):
    CONTRACT = WORLD_CONTRACT

    @classmethod
    def build(cls, instance, context) -> None:
        """Build the world ctrl with full null-pair + offset_ctrl architecture.

        Produces:
            fab_nulls_grp/<jnt>_offset_null/<jnt>_connector_null
            fab_controls_grp/<jnt>_ctrl_offset/<jnt>_ctrl

        Constraint chain:
            ctrl  → parentConstraint(mo) + scaleConstraint(mo) → connector_null
            connector_null → parentConstraint(mo) + scaleConstraint(mo) → joint

        Wires worldMatrix outputs: 'ctrl_out' (ctrl) + 'joint_out' (root joint).
        """
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator import nodes

        joint = instance.joints[0]
        if not cmds.objExists(joint):
            raise RuntimeError(f"World: joint {joint!r} doesn't exist.")

        opts = instance.options
        shape = opts.get('ctrl_shape', 'circle')
        color = opts.get('ctrl_color', 'yellow')
        short = joint.split('|')[-1].split(':')[-1]

        # Rig containers (idempotent — first component creates, others find).
        controls_grp = nodes.ensure_controls_grp()

        # Null pair under fab_nulls_grp.
        offset_null, connector_null = nodes.build_null_pair(joint)

        # offset_ctrl + ctrl under fab_controls_grp (World has no parent_plug;
        # 'parent_strategy = world' parents directly under controls_grp).
        offset_ctrl_name = f'{short}_ctrl_offset'
        ctrl_name = f'{short}_ctrl'

        offset_ctrl = cmds.createNode('transform', name=offset_ctrl_name)
        # Position from the root joint, but rotation stays at world identity.
        # The world ctrl is conceptually a worldspace driver — its axes should
        # align with world axes regardless of how the root joint is oriented
        # (e.g. UE5 root at rotate=-90). Rotation difference between ctrl and
        # joint is captured by parentConstraint mo=True below, so rotating
        # the ctrl still drives the joint correctly.
        cmds.matchTransform(offset_ctrl, joint, position=True, rotation=False, scale=False)
        cmds.parent(offset_ctrl, controls_grp)
        cmds.matchTransform(offset_ctrl, joint, position=True, rotation=False, scale=False)

        ctrl = com.build_shape(shape, ctrl_name,
                               radius=cmds.getAttr(f'{joint}.radius'))
        cmds.parent(ctrl, offset_ctrl)
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{ctrl}.{attr}', 1)

        _apply_color(ctrl, color)

        # Restore persisted CV deltas, if any. Backward compat: legacy
        # schema = single shape dict; current schema = list of shape
        # dicts (preserves multi-shape library curves on roundtrip).
        # Skip the restore when build_modules was invoked with the
        # reset_ctrl_shapes option (Build Options bar checkbox) — the
        # ctrl gets the default library shape instead of the saved CVs.
        persisted = instance.persisted
        cv_data = (persisted.get('cv_data') or {}).get(shape)
        if context.options.get('reset_ctrl_shapes'):
            cv_data = None
        if cv_data:
            shapes_list = cv_data if isinstance(cv_data, list) else [cv_data]
            try:
                com.deserialize_shape_to(ctrl, {'shapes': shapes_list})
            except Exception:
                pass  # fall back to default shape

        # Constraint chain: ctrl → connector_null → joint.
        cmds.parentConstraint(ctrl, connector_null, maintainOffset=True)
        cmds.scaleConstraint(ctrl, connector_null, maintainOffset=True)
        cmds.parentConstraint(connector_null, joint, maintainOffset=True)
        cmds.scaleConstraint(connector_null, joint, maintainOffset=True)

        _apply_channels(ctrl, opts.get('channels', {}))

        # Tag ctrl with KS metadata for marking-menu / pose library lookup.
        component_node = next(
            (cn for cn in nodes.get_all_component_nodes()
             if nodes.get_component_id(cn) == instance.id),
            '',
        )
        nodes.tag_ctrl(ctrl, 'world_ctrl',
                       component_node=component_node, joint_index=0)

        context.register_output(instance.id, 'ctrl_out', f'{ctrl}.worldMatrix[0]')
        context.register_output(instance.id, 'joint_out', f'{joint}.worldMatrix[0]')

    @classmethod
    def unbuild(cls, instance) -> dict:
        """Capture ctrl state, then delete the joint constraints so the
        rig_grp delete cascade leaves the joint clean.

        Constraints that live under the joint (parentConstraint, scaleConstraint
        children of the joint) are NOT under rig_grp and must be deleted
        explicitly here. The ctrl/offset/null hierarchy IS under rig_grp and
        dies with the cascade.
        """
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

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

        # Delete constraints on the joint — they're children of the joint, NOT
        # under rig_grp, so the rig_grp cascade leaves them dangling otherwise.
        if cmds.objExists(joint):
            constraints = (
                (cmds.listRelatives(joint, type='parentConstraint') or [])
                + (cmds.listRelatives(joint, type='scaleConstraint') or [])
            )
            if constraints:
                cmds.delete(constraints)

        return captured


# ─── Local helpers ───────────────────────────────────────────────────────────

# Ctrl color palette moved to side_tokens.CTRL_COLOR_RGB so build-time
# joint coloring and ctrl coloring share a single source of truth.
from maya_tools.utils.maya.side_tokens import CTRL_COLOR_RGB as _COLOR_MAP

_KEYABLE_TRANSFORM_ATTRS = ('tx', 'ty', 'tz', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz', 'visibility')


def _apply_color(ctrl: str, color: str) -> None:
    """Apply override color to ctrl shapes."""
    rgb = _COLOR_MAP.get(color, _COLOR_MAP['yellow'])
    shapes = cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []
    for s in shapes:
        cmds.setAttr(f'{s}.overrideEnabled', True)
        cmds.setAttr(f'{s}.overrideRGBColors', True)
        cmds.setAttr(f'{s}.overrideColorR', rgb[0])
        cmds.setAttr(f'{s}.overrideColorG', rgb[1])
        cmds.setAttr(f'{s}.overrideColorB', rgb[2])


def _apply_channels(ctrl: str, channels: dict) -> None:
    """Apply channelbox setup.

    channels schema:
        {'keyable': ['rx', 'ry', 'rz']}  # whitelist; everything else locked + hidden

    Empty / missing 'keyable' falls back to all transforms keyable.

    Uses one setAttr flag per call. Stacking lock + keyable + channelBox
    in a single setAttr can leave the channel in an inconsistent state on
    rotations specifically (Maya quirk — observed across multiple rig
    systems). One flag at a time is reliable.
    """
    keyable = set(channels.get('keyable') or ['tx', 'ty', 'tz', 'rx', 'ry', 'rz'])
    for attr in _KEYABLE_TRANSFORM_ATTRS:
        plug = f'{ctrl}.{attr}'
        if attr in keyable:
            cmds.setAttr(plug, lock=False)
            cmds.setAttr(plug, keyable=True)
        else:
            cmds.setAttr(plug, lock=True)
            cmds.setAttr(plug, keyable=False)
            cmds.setAttr(plug, channelBox=False)


def _capture_channels(ctrl: str) -> dict:
    """Read current channelbox state into a 'channels' dict."""
    keyable = []
    for attr in _KEYABLE_TRANSFORM_ATTRS:
        plug = f'{ctrl}.{attr}'
        if cmds.getAttr(plug, lock=True):
            continue
        if not cmds.getAttr(plug, keyable=True):
            continue
        keyable.append(attr)
    return {'keyable': keyable}
