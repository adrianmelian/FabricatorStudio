# Python/maya_tools/rigging/fabricator/components/ik_leg.py
"""IKLeg — 4-joint IK leg with reverse foot baked in.

Subclass of SimpleIKComponent. Architecture (port of legacy
simple_ik_leg.build_foot_rig):
  - 4-joint bind chain: thigh, knee, ankle, ball.
  - 5-joint IK chain: super's 3 (thigh_ik, knee_ik, ankle_ik) plus 2
    synthetic IK-only joints (ball_ik, toe_tip_ik) appended for the
    foot sub-IK handles.
  - 3 IK handles: main (hip→ankle, ikRPsolver) inside foot_roll_pivot;
    sub-IKs (ankle→ball, ball→toe-tip, both ikSCsolver) inside
    ankle_aim_pivot.
  - 4-pivot stack under ik_end_ctrl: heel → toe_tip → [foot_roll, ankle_aim],
    foot-frame oriented from heel + toe_tip extra guide positions.
  - Heel + toe viewport ctrls (sphere) + foot_roll scalar attr.
  - Auto-spawned ball FK ctrl for toe wiggle, parented under ankle_aim_pivot.

Heel/toe ctrls drive their pivots via parentConstraint(mo=True); foot_roll
drives foot_roll_pivot.rotateZ via reverse node (matches legacy semantics).
ankle_aim_pivot is a SIBLING of foot_roll_pivot — when foot_roll fires,
the leg lifts but ball + toe-tip stay planted via the sub-IK handles.

See docs/superpowers/plans/2026-05-07-ks-v2-ikleg-reverse-foot-legacy-port.md
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator.modules.component import (
    Contract, ExtraGuide, JointRole, MirrorRule, OptionField, Plug,
    SpaceConsumer,
)
from maya_tools.rigging.fabricator.modules.simple_ik import (
    SimpleIKComponent, SIMPLE_IK_CONTRACT,
)


def _build_foot_frame(heel_pos: tuple, toe_tip_pos: tuple) -> tuple:
    """Return (forward, up, side) MVectors for the foot's local frame.

    forward = (toe_tip - heel) projected to the horizontal plane. side =
    forward × world_up. The frame is character-orient invariant — works
    regardless of which world axis the character faces, so long as the
    heel/toe-tip guides are placed correctly.

    Respects the scene's actual up axis (cmds.upAxis). UE5 imports often
    keep Z-up, in which case world Y is horizontal — hardcoding up_v=
    (0,1,0) made `side` come out vertical and foot_roll rotated around
    the vertical (yaw) instead of around the side axis (pitch).
    """
    import maya.api.OpenMaya as om
    z_up = cmds.upAxis(q=True, axis=True) == 'z'
    up_v = om.MVector(0.0, 0.0, 1.0) if z_up else om.MVector(0.0, 1.0, 0.0)

    forward = om.MVector(*toe_tip_pos) - om.MVector(*heel_pos)
    # Project forward to the horizontal plane (zero out the up component).
    if z_up:
        forward.z = 0.0
    else:
        forward.y = 0.0
    if forward.length() < 1e-6:
        # Heel and toe-tip are vertically co-located — fall back to a
        # sensible horizontal default.
        forward = om.MVector(1.0, 0.0, 0.0) if z_up else om.MVector(0.0, 0.0, 1.0)
    forward.normalize()

    side = (forward ^ up_v).normal()
    up_v = (side ^ forward).normal()
    return forward, up_v, side


def _build_pivot(name: str, world_pos: tuple, frame: tuple, parent: str) -> str:
    """Create a transform pivot at world_pos with foot-frame rotation, then
    move the rotation onto rotateAxis so `rotate` reads (0,0,0) at rest.

    `frame` is a (forward, up, side) tuple from _build_foot_frame. After this
    runs, the pivot's local rotate is 0 and the foot-frame rotation lives on
    rotateAxis. Driving rotateZ then rotates around the foot's side axis
    (correct for ball-roll, heel-roll-via-ctrl-rotation, etc.).

    NOTE: an earlier version of this function used `makeIdentity(rotate=True)`
    to do the freeze. That works on JOINTS (it bakes into jointOrient) but on
    plain transforms it just zeroes the rotation outright — losing the foot-
    frame orientation entirely. Pivots ended up world-axis-aligned, and
    foot_roll rotated around world Z instead of the foot's side axis,
    producing yaw on toed-out feet. Manual rotate→rotateAxis transfer below.
    """
    pivot = cmds.createNode('transform', name=name, parent=parent)
    forward, up_v, side = frame
    matrix = [
        forward.x, forward.y, forward.z, 0.0,
        up_v.x,    up_v.y,    up_v.z,    0.0,
        side.x,    side.y,    side.z,    0.0,
        world_pos[0], world_pos[1], world_pos[2], 1.0,
    ]
    cmds.xform(pivot, ws=True, matrix=matrix)
    rot = cmds.getAttr(f'{pivot}.rotate')[0]
    cmds.setAttr(f'{pivot}.rotateAxis', rot[0], rot[1], rot[2], type='double3')
    cmds.setAttr(f'{pivot}.rotate', 0, 0, 0, type='double3')
    return pivot


# Build the IKLeg contract by extending SimpleIK's. Most fields carry forward;
# we override min/max joints, joint_roles (4 roles), outputs (+ ball_out),
# options_schema (+ ball_ctrl_shape, heel_position, toe_tip_position),
# extra_guides, and the type/display_name/description.

IK_LEG_CONTRACT = Contract(
    type='IKLeg',
    display_name='IK Leg',
    description=(
        'Four-joint IK leg with reverse foot. SimpleIK on the first 3 joints '
        '(thigh, knee, ankle); ball joint gets a separate FK ctrl (toe '
        'wiggle); reverse foot pivot stack drives the IK handle via heel '
        '+ toe_tip extra guides + ball joint position.'
    ),
    min_joints=4, max_joints=4,
    parent_strategy=SIMPLE_IK_CONTRACT.parent_strategy,
    inputs=SIMPLE_IK_CONTRACT.inputs,
    outputs=SIMPLE_IK_CONTRACT.outputs + (
        Plug(name='ball_out', kind='matrix', space_target=True,
             description='BIND ball joint world matrix.'),
    ),
    options_schema={
        **SIMPLE_IK_CONTRACT.options_schema,
        # IK family color: orange (all IKs are orange shades).
        'ctrl_color': OptionField(type='color_enum', default='orange'),
        'ik_ctrl_shape':    OptionField('shape_enum', 'foot',
                                         description=('IK foot ctrl shape. '
                                          'Curve origin should sit at the ankle in XZ; '
                                          'IKLeg.build positions the ctrl at (ankle.x, 0, ankle.z) '
                                          'and mirrors X-CVs on the right side.')),
        'ball_ctrl_shape':  OptionField('shape_enum', 'circle',
                                         description='Shape for the toe-wiggle ball ctrl.'),
        'foot_roll_axis':   OptionField('enum', 'z', choices=('x', 'y', 'z'),
                                         description=('Pivot axis the foot_roll attr drives. '
                                          'Pick whichever produces a heel-lift pitch on your '
                                          'rig — the foot frame is computed from heel/toe '
                                          'guide positions and the right axis depends on the '
                                          "rig's joint orientation conventions. If foot_roll "
                                          'yaws or rolls instead of pitching, change this.')),
        'heel_position':    OptionField('vector3', None,
                                         description='Heel pivot worldspace position (set via guide).'),
        'toe_tip_position': OptionField('vector3', None,
                                         description='Toe-tip pivot worldspace position (set via guide).'),
    },
    side_supported=True,
    color='#FF8000',                       # orange — IK family
    default_region='leg',                   # pose library auto-set tag
    limb_features=('twists',),              # Derived Limbs: legs twist, never finger
    joint_roles=(
        JointRole('start', 'Hip joint'),
        JointRole('mid',   'Knee joint',  descendant_of='start'),
        JointRole('end',   'Ankle joint', descendant_of='mid'),
        JointRole('ball',  'Ball joint',  descendant_of='end'),
    ),
    extra_guides=(
        ExtraGuide(name='heel',     display_name='Heel pivot',
                   description='Floor contact at the back of the foot.',
                   shape='reticle', side_aware=True),
        ExtraGuide(name='toe_tip',  display_name='Toe-tip pivot',
                   description='Floor contact at the front of the foot.',
                   shape='reticle', side_aware=True,
                   parent='heel'),
    ),
    space_consumers=SIMPLE_IK_CONTRACT.space_consumers,
    actions=SIMPLE_IK_CONTRACT.actions,
    # Inherit SimpleIK's fk_ctrl / pv_ctrl / master_switch_ctrl rules and
    # OVERRIDE ik_end_ctrl with the foot-frame variant. IKLeg rebinds the
    # ik_end_ctrl's OPM to an axis-aligned identity-rotation matrix at
    # floor level (see ik_leg.py:_rebind_ik_end_ctrl), so the foot ctrl
    # mirrors as the standard YZ-plane reflection — translateX, rotateY,
    # and rotateZ flip on swap. Reverse-foot pivot ctrls (heel/toe/ball)
    # follow the same foot-frame convention.
    mirror_rules=(
        tuple(
            rule for rule in SIMPLE_IK_CONTRACT.mirror_rules
            if rule.ctrl_role != 'ik_end_ctrl'
        )
        + (
            MirrorRule(
                ctrl_role='ik_end_ctrl',
                negate=frozenset({'translateX', 'rotateY', 'rotateZ'}),
            ),
            MirrorRule(
                ctrl_role='heel_ctrl',
                negate=frozenset({'translateX', 'rotateY', 'rotateZ'}),
            ),
            MirrorRule(
                ctrl_role='toe_ctrl',
                negate=frozenset({'translateX', 'rotateY', 'rotateZ'}),
            ),
            MirrorRule(
                ctrl_role='ball_ctrl',
                negate=frozenset({'translateX', 'rotateY', 'rotateZ'}),
            ),
        )
    ),
)


class IKLegComponent(SimpleIKComponent):
    CONTRACT = IK_LEG_CONTRACT
    # (Twist adoption rides CONTRACT.limb_features=('twists',) — Derived
    # Limbs spec 2026-07-11; the old creates_implicit_limb bool is
    # retired. A bare add onto a twist-carrying skeleton — UE5.8 Manny
    # thigh_twist_*/calf_twist_* — still adopts at the same seam.)
    # Free-tier shin twist (build tail): RibbonIKLeg inherits this build
    # verbatim but overrides the gate False — its ribbon riders own the
    # twist joints.
    free_fractional_lower_twist = True

    @classmethod
    def can_apply(cls, joints, blueprint) -> tuple:
        """4-joint chain: first 3 are SimpleIK chain; 4th (ball) is direct
        child of 3rd."""
        # Don't call SimpleIK.can_apply — it expects exactly 3 joints.
        # Re-run the joint-count + chain checks with 4-joint expectations.
        from maya_tools.rigging.fabricator.fs_app import _sort_by_depth

        if not joints or blueprint is None:
            return False, 'No joint(s) selected or no blueprint loaded.'

        sorted_joints = _sort_by_depth(list(joints), blueprint)
        if len(sorted_joints) < 4:
            return False, 'IKLeg needs 4 joints (hip, knee, ankle, ball).'

        parent_map = {j.name: j.parent for j in blueprint.skeleton_joints}
        for i in range(1, 4):
            if parent_map.get(sorted_joints[i]) != sorted_joints[i - 1]:
                return False, (
                    f'{sorted_joints[i]!r} is not a direct child of '
                    f'{sorted_joints[i-1]!r}; IKLeg requires a 4-joint '
                    f'parent→child chain.'
                )
        return True, ''

    @classmethod
    def resolve_extra_guide_default(cls, guide_name, joints, blueprint):
        """Heel + toe-tip locator defaults, projected to ground (Y=0 in
        Y-up scenes / Z=0 in Z-up).

        - Heel: at ankle's horizontal position, pushed backward a small
          amount along the foot-forward axis.
        - Toe-tip: at ball's horizontal position, pushed forward a small
          amount along the foot-forward axis.

        Foot-forward is derived from ankle→ball horizontal direction, so
        this works for any character orientation (not just Manny facing
        +X). The user typically only needs to nudge the locators along
        the foot's forward axis to fine-tune.
        """
        from maya_tools.rigging.fabricator.fs_app import _bp_world_translate, _bp_world_distance
        import maya.api.OpenMaya as om

        thigh, knee, ankle, ball = joints
        ankle_pos = _bp_world_translate(ankle, blueprint)
        ball_pos  = _bp_world_translate(ball,  blueprint)
        chain_len = (
            _bp_world_distance(thigh, knee, blueprint)
            + _bp_world_distance(knee, ankle, blueprint)
        )

        # Foot-forward = horizontal projection of ankle→ball.
        z_up = cmds.upAxis(q=True, axis=True) == 'z'
        foot_fwd = om.MVector(
            ball_pos[0] - ankle_pos[0],
            ball_pos[1] - ankle_pos[1],
            ball_pos[2] - ankle_pos[2],
        )
        if z_up:
            foot_fwd.z = 0.0
        else:
            foot_fwd.y = 0.0
        if foot_fwd.length() < 1e-6:
            foot_fwd = om.MVector(1.0, 0.0, 0.0)
        foot_fwd.normalize()

        # ~8% of leg length — small nudge behind heel / forward of toe.
        # Animator drags the locators precisely after the initial spawn.
        offset = 0.08 * chain_len

        if guide_name == 'heel':
            anchor = ankle_pos
            sign = -1  # behind the ankle
        elif guide_name == 'toe_tip':
            anchor = ball_pos
            sign = +1  # forward of the ball
        else:
            raise ValueError(f'Unknown extra guide: {guide_name!r}')

        if z_up:
            return (
                anchor[0] + sign * foot_fwd.x * offset,
                anchor[1] + sign * foot_fwd.y * offset,
                0.0,  # ground plane in Z-up
            )
        return (
            anchor[0] + sign * foot_fwd.x * offset,
            0.0,  # ground plane in Y-up
            anchor[2] + sign * foot_fwd.z * offset,
        )

    @classmethod
    def build(cls, instance, context) -> None:
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator.modules.world import _apply_color, _apply_channels
        from maya_tools.rigging.fabricator import nodes as ks_nodes
        from maya_tools.utils.maya import network_nodes as nn

        full_joints = list(instance.joints)
        if len(full_joints) != 4:
            raise RuntimeError(
                f"IKLeg on {instance.id!r} needs 4 joints; got {full_joints}."
            )
        thigh, knee, ankle, ball = full_joints
        for j in full_joints:
            if not cmds.objExists(j):
                raise RuntimeError(f"IKLeg: joint {j!r} doesn't exist.")

        # Run SimpleIKComponent.build on the first 3 joints. Shadow
        # instance.joints across the super call — SimpleIK.build expects
        # exactly 3 (start, mid, end = joints).
        instance.joints = [thigh, knee, ankle]
        try:
            super().build(instance, context)
        finally:
            instance.joints = full_joints

        # ─── Fix IK/FK blend ankle spin (super blend constraint interp) ──
        # super created parentConstraints on the 3 blend joints with default
        # interpType=1 (Average), which blends Euler components weighted-wise
        # and can take long-path interp when IK and FK ankle Euler reps
        # differ. We replaced super's orientConstraint(ik_end_ctrl, ik_chain[-1])
        # with sub-IK A in Task 5/6 — sub-IK aim can produce a different Euler
        # representation than the FK bind orient even at the same world
        # rotation, exposing this issue. Switch to interpType=2 (Shortest /
        # quaternion blend) — always takes the geodesic path on the rotation
        # sphere, eliminating the wrap-around spin during 1→0 transitions.
        for blend_jnt in instance._blend_chain:
            for con in cmds.listRelatives(blend_jnt, type='parentConstraint') or []:
                cmds.setAttr(f'{con}.interpType', 2)

        # ─── Resolve build artifacts produced by SimpleIK ───────────────
        short_ankle = ankle.split('|')[-1].split(':')[-1]
        short_ball  = ball.split('|')[-1].split(':')[-1]
        short_thigh = thigh.split('|')[-1].split(':')[-1]

        ik_end_ctrl = f'{short_ankle}_IK_ctrl'  # foot ctrl (named for ankle)
        if not cmds.objExists(ik_end_ctrl):
            raise RuntimeError(
                f"IKLeg expected SimpleIK to have created {ik_end_ctrl!r}."
            )
        ikhandle = f'{short_thigh}_ikHandle'
        if not cmds.objExists(ikhandle):
            raise RuntimeError(
                f"IKLeg expected SimpleIK to have created {ikhandle!r}."
            )

        # Locate the component network node for tracking owned DG nodes.
        component_node = None
        for cn in ks_nodes.get_all_component_nodes():
            if ks_nodes.get_component_id(cn) == instance.id:
                component_node = cn
                break

        # ─── Reverse foot pivot positions ────────────────────────────────
        opts = instance.options
        heel_pos = opts.get('heel_position') or list(
            cls.resolve_extra_guide_default('heel', full_joints, context.blueprint)
        )
        toe_tip_pos = opts.get('toe_tip_position') or list(
            cls.resolve_extra_guide_default('toe_tip', full_joints, context.blueprint)
        )
        ankle_pos = cmds.xform(ankle, q=True, ws=True, t=True)
        ball_pos  = cmds.xform(ball,  q=True, ws=True, t=True)

        # ─── Reposition IK foot ctrl: ground level at ankle XZ ───────────
        # The default ik_ctrl_shape ('foot') has its origin authored at the
        # ankle in XZ; placing the ctrl at (ankle.x, 0, ankle.z) at identity
        # rotation lets the foot curve sit on the ground with the ankle
        # joint above it, foot extending forward to toes. Mirroring along
        # X for right-side legs flips the left-foot curve into a right-foot
        # curve.
        #
        # Order matters: we change ik_end_ctrl's bind matrix BEFORE the
        # pivot stack gets parented under it (pivots' world matrices are
        # set via _build_pivot — they don't drift when the parent moves
        # later, but creating them under a stable parent matrix is cleaner).
        # The orientConstraint super created on ik_chain[-1] needs to be
        # rebuilt so its mo offset reflects the new ctrl rotation; super's
        # parentConstraint on ikhandle is deleted here too (IKLeg reroutes
        # the handle through the pivot stack below — same result but doing
        # the delete before the move avoids the handle being yanked to
        # ground when ik_end_ctrl shifts down).
        ankle_ik = instance._ik_chain[-1]
        for c in cmds.listRelatives(ankle_ik, type='orientConstraint') or []:
            cmds.delete(c)
        for c in cmds.listRelatives(ikhandle, type='parentConstraint') or []:
            cmds.delete(c)

        new_bind_matrix = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            ankle_pos[0], 0.0, ankle_pos[2], 1.0,
        ]
        cmds.setAttr(f'{ik_end_ctrl}.fab_bind_matrix',
                     *new_bind_matrix, type='matrix')
        cmds.setAttr(f'{ik_end_ctrl}.offsetParentMatrix',
                     *new_bind_matrix, type='matrix')

        # Mirror CVs on right-side leg so a left-authored foot curve flips
        # to a right foot. Only applies on first build (when persisted CV
        # data is empty); rebuilds restore the user's saved CV layout
        # instead.
        if instance.side == 'rt' and not (
                instance.persisted or {}).get('cv_data', {}).get(ik_end_ctrl):
            ks_nodes.mirror_ctrl_cvs_x(ik_end_ctrl)

        # Recreate orientConstraint with the new ctrl rotation. mo=True
        # captures the offset between ankle_ik (still at bind world rot)
        # and ik_end_ctrl (now at identity rot) — that offset is bind
        # ankle's world rotation, so animating the ctrl rotation drives
        # the IK ankle correctly.
        cmds.orientConstraint(ik_end_ctrl, ankle_ik, mo=True)

        # ─── Foot-frame + pivot stack ────────────────────────────────────
        # foot_roll_pivot and ankle_aim_pivot are siblings under
        # toe_tip_pivot — required for ball-roll behavior. foot_roll attr
        # rotates only foot_roll_pivot (lifts the leg), leaving
        # ankle_aim_pivot to pin the foot via the upcoming sub-IKs.
        frame = _build_foot_frame(heel_pos, toe_tip_pos)

        heel_pivot = _build_pivot(
            f'{short_ankle}_heel_pivot', heel_pos, frame, ik_end_ctrl)
        toe_tip_pivot = _build_pivot(
            f'{short_ankle}_toe_tip_pivot', toe_tip_pos, frame, heel_pivot)
        foot_roll_pivot = _build_pivot(
            f'{short_ankle}_foot_roll_pivot', ball_pos, frame, toe_tip_pivot)
        ankle_aim_pivot = _build_pivot(
            f'{short_ankle}_ankle_aim_pivot', ball_pos, frame, toe_tip_pivot)
        for piv in (heel_pivot, toe_tip_pivot, foot_roll_pivot, ankle_aim_pivot):
            ks_nodes.hide_internal(piv)

        # ─── Extend IK chain with synthetic ball + toe-tip joints ────────
        # super().build created [thigh_ik, knee_ik, ankle_ik]. We append
        # ball_ik (at bind ball pos) and toe_tip_ik (at toe-tip extra guide
        # pos) so the foot sub-IK handles can run on a single 5-joint chain
        # — same structure as legacy simple_ik_leg's IK chain. Bind chain
        # stays 4 joints (these synthetics aren't bind targets). toe_tip_ik
        # is keyed on short_ankle (no bind joint exists for the tip — it's
        # positional scaffolding for the foot's owning ankle).
        ankle_ik = instance._ik_chain[-1]

        # No makeIdentity / jointOrient copy: ikSCsolver consumes world
        # position only — these joints aren't skinned.
        cmds.select(clear=True)
        ball_ik = cmds.joint(name=f'{short_ball}_ik')
        cmds.parent(ball_ik, ankle_ik)
        cmds.xform(ball_ik, ws=True, t=ball_pos)

        cmds.select(clear=True)
        toe_tip_ik = cmds.joint(name=f'{short_ankle}_toe_tip_ik')
        cmds.parent(toe_tip_ik, ball_ik)
        cmds.xform(toe_tip_ik, ws=True, t=toe_tip_pos)

        instance._ik_chain.extend([ball_ik, toe_tip_ik])

        # ─── Reroute main IK handle into foot_roll_pivot ─────────────────
        # super().build parented the handle under setup_grp and pinned it
        # to ik_end_ctrl via parentConstraint. We delete that constraint —
        # the pivot stack DAG drives the handle's world position now.
        # The pivot stack DAG drives the handle's world position now —
        # super's parentConstraint(ik_end_ctrl, ikhandle) was already
        # deleted above as part of the foot ctrl reposition. Reparent
        # the handle under foot_roll_pivot and snap to ankle_pos.
        cmds.parent(ikhandle, foot_roll_pivot)
        cmds.xform(ikhandle, ws=True, t=ankle_pos)
        ks_nodes.hide_internal(ikhandle)

        # ─── Foot sub-IK handles ─────────────────────────────────────────
        # ankle_ik_handle: rotates ankle_ik so ball_ik reaches the handle's
        # world. Handle parented under ankle_aim_pivot at ball world. When
        # pivots rotate (heel/toe rolls), handle world updates → ball_ik
        # follows; foot_roll doesn't touch ankle_aim_pivot, so ball stays
        # planted while the leg lifts.
        # toe_ik_handle: rotates ball_ik so toe_tip_ik reaches handle world
        # (= toe-tip world). Same pivot parent so it follows the rolls.
        ankle_ik_handle, _ = cmds.ikHandle(
            sj=ankle_ik, ee=ball_ik,
            sol='ikSCsolver', name=f'{short_ankle}_ankle_ikHandle')
        cmds.parent(ankle_ik_handle, ankle_aim_pivot)
        cmds.xform(ankle_ik_handle, ws=True, t=ball_pos)
        cmds.setAttr(f'{ankle_ik_handle}.visibility', 0)
        ks_nodes.hide_internal(ankle_ik_handle)

        toe_ik_handle, _ = cmds.ikHandle(
            sj=ball_ik, ee=toe_tip_ik,
            sol='ikSCsolver', name=f'{short_ankle}_toe_ikHandle')
        cmds.parent(toe_ik_handle, ankle_aim_pivot)
        cmds.xform(toe_ik_handle, ws=True, t=toe_tip_pos)
        cmds.setAttr(f'{toe_ik_handle}.visibility', 0)
        ks_nodes.hide_internal(toe_ik_handle)

        # ─── Heel + toe viewport ctrls ───────────────────────────────────
        # Animator rotates these directly in viewport. heel_ctrl drives
        # heel_pivot (rotateZ = heel roll, rotateX = bank, rotateY = twist).
        # toe_ctrl drives toe_tip_pivot (rotateZ = toe roll). Both are
        # spheres from Curve-O-Matic (matches legacy heel_ctrl/toe_ctrl).
        ctrl_color = opts.get('ctrl_color', 'yellow')
        channels = opts.get('channels', {})

        heel_ctrl_offset = cmds.createNode(
            'transform', name=f'{short_ankle}_heel_ctrl_offset',
            parent=ik_end_ctrl)
        cmds.matchTransform(heel_ctrl_offset, heel_pivot,
                            position=True, rotation=True, scale=False)
        heel_ctrl = com.build_shape('sphere', f'{short_ankle}_heel_ctrl',
                                    radius=cmds.getAttr(f'{ankle}.radius'))
        cmds.parent(heel_ctrl, heel_ctrl_offset)
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{heel_ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{heel_ctrl}.{attr}', 1)
        _apply_color(heel_ctrl, ctrl_color)
        _apply_channels(heel_ctrl, channels)
        cmds.parentConstraint(heel_ctrl, heel_pivot, mo=True)
        ks_nodes.tag_ctrl(heel_ctrl, 'heel_ctrl',
                          component_node=component_node, joint_index=-1)

        # toe_ctrl is parented under heel_ctrl (matches legacy DAG; toe ctrl
        # follows the heel ctrl's world).
        toe_ctrl_offset = cmds.createNode(
            'transform', name=f'{short_ankle}_toe_ctrl_offset',
            parent=heel_ctrl)
        cmds.matchTransform(toe_ctrl_offset, toe_tip_pivot,
                            position=True, rotation=True, scale=False)
        toe_ctrl = com.build_shape('sphere', f'{short_ankle}_toe_ctrl',
                                   radius=cmds.getAttr(f'{ankle}.radius'))
        cmds.parent(toe_ctrl, toe_ctrl_offset)
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{toe_ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{toe_ctrl}.{attr}', 1)
        _apply_color(toe_ctrl, ctrl_color)
        _apply_channels(toe_ctrl, channels)
        cmds.parentConstraint(toe_ctrl, toe_tip_pivot, mo=True)
        ks_nodes.tag_ctrl(toe_ctrl, 'toe_ctrl',
                          component_node=component_node, joint_index=-1)

        # Restore CV data for heel and toe ctrls if previously persisted;
        # otherwise scale by the ankle joint's radius so the ctrls are
        # right-sized on first build.
        prev_cv = (instance.persisted or {}).get('cv_data', {})
        for ctrl_name in (heel_ctrl, toe_ctrl):
            cv = prev_cv.get(ctrl_name)
            if cv:
                # Backward compat: legacy schema = single shape dict;
                # current schema = list of shape dicts so multi-shape
                # library curves (sphere) survive the roundtrip.
                shapes_list = cv if isinstance(cv, list) else [cv]
                try:
                    com.deserialize_shape_to(ctrl_name, {'shapes': shapes_list})
                except Exception:
                    pass

        # ─── Visibility: heel/toe ctrls hidden in FK mode ─────────────────
        # Tie to super's existing IK visibility condition node — when
        # ik_fk_blend > 0.5 (IK mode), shapes visible; FK mode, hidden.
        # Matches how super already handles ik_end_ctrl + pv_ctrl visibility.
        short_thigh = thigh.split('|')[-1].split(':')[-1]
        ik_vis_cond = f'{short_thigh}_ik_vis_cond'
        if cmds.objExists(ik_vis_cond):
            for ctrl_name in (heel_ctrl, toe_ctrl):
                shapes = cmds.listRelatives(
                    ctrl_name, shapes=True, fullPath=True) or []
                for s in shapes:
                    cmds.connectAttr(
                        f'{ik_vis_cond}.outColorR',
                        f'{s}.visibility', force=True)

        # ─── foot_roll scalar attr → foot_roll_pivot rotate<axis> ────────
        # Reverse node so positive foot_roll lifts the heel (rotates
        # foot_roll_pivot around the picked axis). ankle_aim_pivot (sibling)
        # doesn't rotate, so ball + toe-tip stay planted while the leg lifts.
        # Math: reverse outputs 1 - input. So foot_roll=30 gives rotate=-29
        # (sign depends on axis convention). At rest (foot_roll=0), rotate=1°
        # — a deliberate legacy parity quirk (simple_ik_leg.py:454-459);
        # the bind pose at frame 0 captures the offset.
        #
        # The driving axis is rigger-configurable via the foot_roll_axis
        # option — different rigs land on different axes depending on
        # joint orientation conventions, scene up axis, and where heel/toe
        # guides ended up. Z is the legacy default that worked on UE4 Manny
        # in Y-up; UE5 Z-up imports often need X or Y.
        cmds.addAttr(ik_end_ctrl, ln='foot_roll', at='double',
                     min=0.0, dv=0.0, k=True)
        foot_roll_reverse = cmds.createNode(
            'reverse', name=f'{short_ankle}_foot_roll_reverse')
        cmds.connectAttr(f'{ik_end_ctrl}.foot_roll',
                         f'{foot_roll_reverse}.inputX')
        roll_axis = (opts.get('foot_roll_axis') or 'z').lower()
        if roll_axis not in ('x', 'y', 'z'):
            roll_axis = 'z'
        cmds.connectAttr(f'{foot_roll_reverse}.outputX',
                         f'{foot_roll_pivot}.rotate{roll_axis.upper()}')

        # Restore previously-captured foot_roll value, if any.
        prev_foot_roll = (instance.persisted or {}).get('foot_roll_value')
        if prev_foot_roll is not None:
            cmds.setAttr(f'{ik_end_ctrl}.foot_roll', float(prev_foot_roll))

        # ─── Ball FK ctrl ────────────────────────────────────────────────
        # ball_ctrl_offset must follow the bind ankle's current driver —
        # ankle_aim_pivot in IK mode (so foot_roll carries the ball ctrl
        # along), fk_ankle_ctrl in FK mode (so the ball ctrl stays at the
        # foot when the user switches to FK). Previously the offset was
        # DAG-parented under ankle_aim_pivot, which left the ball ctrl
        # floating in the IK chain's pose after switching to FK.
        # Solution: park the offset under fab_controls_grp (neutral parent)
        # and drive it with a blended parentConstraint, weights driven by
        # the same ik_fk_blend attr the bind chain uses.
        controls_grp = ks_nodes.ensure_controls_grp()
        ball_ctrl_offset = cmds.createNode(
            'transform', name=f'{short_ball}_ctrl_offset',
            parent=controls_grp,
        )
        cmds.matchTransform(ball_ctrl_offset, ball,
                             position=True, rotation=True, scale=False)

        fk_ankle_ctrl = instance._fk_ctrls[-1]
        switch_blend_attr = f'{instance._switch_ctrl}.ik_fk_blend'
        ball_ctrl_offset_pcon = cmds.parentConstraint(
            ankle_aim_pivot, fk_ankle_ctrl, ball_ctrl_offset,
            mo=True,
        )[0]
        cmds.setAttr(f'{ball_ctrl_offset_pcon}.interpType', 2)
        pweights = cmds.parentConstraint(
            ball_ctrl_offset_pcon, q=True, weightAliasList=True)
        ball_ctrl_offset_rev = cmds.createNode(
            'reverse', name=f'{short_ball}_ctrl_offset_ikfk_reverse')
        cmds.connectAttr(switch_blend_attr, f'{ball_ctrl_offset_rev}.inputX')
        # IK weight (W0) = blend directly; FK weight (W1) = 1 - blend.
        cmds.connectAttr(switch_blend_attr,
                         f'{ball_ctrl_offset_pcon}.{pweights[0]}')
        cmds.connectAttr(f'{ball_ctrl_offset_rev}.outputX',
                         f'{ball_ctrl_offset_pcon}.{pweights[1]}')

        ball_ctrl = com.build_shape(opts.get('ball_ctrl_shape', 'circle'),
                                     f'{short_ball}_ctrl',
                                     radius=cmds.getAttr(f'{ball}.radius'))
        cmds.parent(ball_ctrl, ball_ctrl_offset)
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{ball_ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{ball_ctrl}.{attr}', 1)
        _apply_color(ball_ctrl, opts.get('ctrl_color', 'yellow'))

        # Restore persisted ball CV data when present; build_shape(radius=)
        # already sized the default shape to the ball joint's radius.
        # Backward compat: legacy schema = single shape dict; current schema
        # = list of shape dicts (full multi-shape preservation).
        ball_cv = (instance.persisted or {}).get('cv_data', {}).get(ball_ctrl)
        if ball_cv:
            shapes_list = ball_cv if isinstance(ball_cv, list) else [ball_cv]
            try:
                com.deserialize_shape_to(ball_ctrl, {'shapes': shapes_list})
            except Exception:
                pass

        # Constraint chain for ball: ctrl → ball_connector_null → ball_jnt.
        ball_offset_null, ball_connector_null = ks_nodes.build_null_pair(ball)
        cmds.parentConstraint(ball_ctrl, ball_connector_null, mo=True)
        cmds.scaleConstraint(ball_ctrl,  ball_connector_null, mo=True)
        cmds.parentConstraint(ball_connector_null, ball, mo=True)
        cmds.scaleConstraint(ball_connector_null,  ball, mo=True)

        # Tag ball ctrl — joint_index=3 since it drives the 4th joint slot
        # (the ball joint, after thigh/calf/foot).
        ks_nodes.tag_ctrl(ball_ctrl, 'ball_ctrl',
                          component_node=component_node, joint_index=3)

        # Register ball_out plug.
        context.register_output(instance.id, 'ball_out',
                                f'{ball}.worldMatrix[0]')

        # ─── Track reverse-foot DG nodes for unbuild cleanup ─────────────
        # ball_ctrl_offset is now parented under fab_controls_grp (instead of
        # ankle_aim_pivot) so unbuild needs to delete it explicitly — the
        # controls_grp itself survives builds. ball_ctrl + the constraint
        # cascade from ball_ctrl_offset's deletion.
        if component_node:
            tracked = (
                heel_pivot, toe_tip_pivot, foot_roll_pivot, ankle_aim_pivot,
                ball_ik, toe_tip_ik,
                ankle_ik_handle, toe_ik_handle,
                heel_ctrl_offset, heel_ctrl,
                toe_ctrl_offset, toe_ctrl,
                ball_ctrl_offset, ball_ctrl_offset_rev,
                foot_roll_reverse,
            )
            for n in tracked:
                nn.connect_message_multi(n, component_node, 'reverse_foot_nodes')
        else:
            cmds.warning(
                f'IKLeg: no component node for {instance.id!r} — '
                f'reverse-foot nodes not tracked; unbuild will leak them.'
            )

        # ─── Free-leg fractional twist (2026-07-10, Adrian-blessed) ─────
        # The free arm's forearm twist applied to the shin: each
        # twist_lower member (limb-node membership — dialed or adopted)
        # rotates t x the ANKLE bind's bone-axis rotation, post-blend so
        # IK and FK both work; thigh (upper) twists ride rigid, exact
        # free-arm parity. RibbonIKLeg inherits this build but sets the
        # class gate False — its uvpin segment riders own the twist
        # joints there, and two drivers on one channel is a fight the
        # already-driven guard should never have to referee.
        if getattr(cls, 'free_fractional_lower_twist', False):
            from maya_tools.rigging.fabricator import limb_node as ln
            from maya_tools.rigging.fabricator.modules import (
                _limb_common as lc)
            limb = ln.find_limb_for_joint(thigh)
            members = ln.list_twist_lower(limb) if limb else []
            lc.build_fractional_twist(
                members, knee, ankle, ankle, component_node,
                f"IKLeg {instance.id!r}")

    @classmethod
    def unbuild(cls, instance) -> dict:
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator import nodes as ks_nodes
        from maya_tools.utils.maya import network_nodes as nn

        full_joints = list(instance.joints)
        if len(full_joints) != 4:
            return super().unbuild(instance)
        thigh, knee, ankle, ball = full_joints
        short_ankle = ankle.split('|')[-1].split(':')[-1]
        short_ball  = ball.split('|')[-1].split(':')[-1]

        # Capture before super tears down: ball + heel + toe ctrl CV data,
        # and foot_roll attr value. (super().unbuild captures FK/IK/PV/switch
        # CV data already; we merge ours into its returned cv_data dict.)
        ctrls_to_capture = (
            f'{short_ball}_ctrl',
            f'{short_ankle}_heel_ctrl',
            f'{short_ankle}_toe_ctrl',
        )
        captured_cv = {}
        for ctrl_name in ctrls_to_capture:
            if not cmds.objExists(ctrl_name):
                continue
            try:
                shape_data = com.serialize_shape(ctrl_name)
                if shape_data.get('shapes'):
                    captured_cv[ctrl_name] = shape_data['shapes']
            except RuntimeError:
                pass

        foot_roll_value = None
        foot_ctrl_name = f'{short_ankle}_IK_ctrl'
        if cmds.objExists(foot_ctrl_name) and cmds.attributeQuery(
                'foot_roll', node=foot_ctrl_name, exists=True):
            foot_roll_value = cmds.getAttr(f'{foot_ctrl_name}.foot_roll')

        # Shadow joints to 3 for super().unbuild (matches build pattern).
        instance.joints = [thigh, knee, ankle]
        try:
            captured = super().unbuild(instance)
        finally:
            instance.joints = full_joints

        # Merge ball + heel + toe CV data into the captured cv_data dict.
        captured.setdefault('cv_data', {}).update(captured_cv)
        if foot_roll_value is not None:
            captured['foot_roll_value'] = float(foot_roll_value)

        # Delete reverse-foot tracked nodes (pivots, sub-IK handles, synthetic
        # IK joints, ctrls, reverse node).
        component_node = None
        for cn in ks_nodes.get_all_component_nodes():
            if ks_nodes.get_component_id(cn) == instance.id:
                component_node = cn
                break
        if component_node:
            for n in nn.get_message_targets(component_node, 'reverse_foot_nodes'):
                if cmds.objExists(n):
                    cmds.delete(n)
            # Free-leg twist scalar sweep (2026-07-10) — unconditional,
            # same lesson as the arm's bucket sweep: never gate DG
            # cleanup on joint-unpack success.
            for n in nn.get_message_targets(component_node, 'twist_dg_nodes'):
                if cmds.objExists(n):
                    cmds.delete(n)

        # Delete ball joint constraints (children of bind ball joint, not
        # under rig_grp). Same pattern as SimpleFK's unbuild.
        if cmds.objExists(ball):
            constraints = (
                (cmds.listRelatives(ball, type='parentConstraint') or [])
                + (cmds.listRelatives(ball, type='scaleConstraint') or [])
            )
            if constraints:
                cmds.delete(constraints)

        return captured
