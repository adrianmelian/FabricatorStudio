# Python/maya_tools/rigging/fabricator/components/simple_ik.py
"""SimpleIKComponent — three-chain IK/FK on a 3-joint chain.

Spec 1.5 — substrate for the IK family. Specializations (IK Arm, IK Leg
in Spec 1.6) inherit from this and add their specifics (clavicle FK +
hand + fingers; reverse foot pivot stack).

Build creates three parallel joint chains:
- Bind chain (the existing skinned joints — permanent).
- FK chain — duplicate of bind, parented under setup_grp.
- IK chain — duplicate of bind, parented under setup_grp; driven by IK handle.
- BLEND chain — duplicate of bind, parented under setup_grp; driven by
  parent+scale constraints from both FK and IK joints, weighted by
  ik_fk_blend. Bind joints constrain from blend joints (mo=True).

Blending follows v1 simple_ik_arm.py make_ikfk_switch pattern:
  ik_fk_blend = 1 → full IK, ik_fk_blend = 0 → full FK.
  IK weight = ik_fk_blend directly; FK weight = reverse(ik_fk_blend).

See docs/superpowers/specs/2026-05-08-ks-v2-simple-ik-spaces-marking-menu-design.md
"""
__author__ = "Adrian Melian"

import math

import maya.cmds as cmds

from maya_tools.rigging.fabricator.modules.component import (
    Action, Component, Contract, JointRole, MirrorRule, OptionField, Plug,
    SpaceConsumer,
)


SIMPLE_IK_CONTRACT = Contract(
    type='SimpleIK',
    display_name='IK',
    description=(
        'Three-joint IK/FK chain. Bind + FK + IK + BLEND joint chains; bind '
        'driven by BLEND chain which is parent+scale constrained from both FK '
        'and IK with weights driven by ik_fk_blend. Auto-tracked polevector. '
        'IK/FK switch on a dedicated ctrl near the end joint.'
    ),
    min_joints=3, max_joints=3,
    parent_strategy='walk_up',
    inputs=(
        Plug(name='parent_in', kind='matrix', required=True,
             description='Parent transform space (e.g. clavicle ctrl for arm).'),
    ),
    outputs=(
        Plug(name='start_out', kind='matrix', space_target=True,
             description='BIND start joint world matrix.'),
        Plug(name='mid_out', kind='matrix', space_target=True,
             description='BIND mid joint world matrix.'),
        Plug(name='end_out', kind='matrix', space_target=True,
             description='BIND end joint world matrix.'),
        Plug(name='ik_ctrl_out', kind='matrix', space_target=True,
             description='IK end ctrl world matrix.'),
        Plug(name='anchor_out', kind='matrix', space_target=True,
             description=('Cycle-free anchor for internal consumers (PV + IK end ctrls). '
                          'Follows the resolved parent ctrl without dependence on the IK '
                          'solve, avoiding the bind-joint feedback loop.')),
    ),
    options_schema={
        'ctrl_shape':        OptionField(type='shape_enum', default='capsule',
                                         description='Shape for the FK ctrls (chain).'),
        'ik_ctrl_shape':     OptionField(type='shape_enum', default='cube',
                                         description='Shape for the IK hand/foot ctrl.'),
        'ctrl_color':        OptionField(type='color_enum', default='peach'),
        'pv_shape':          OptionField(type='shape_enum', default='diamond'),
        'pv_distance':       OptionField(type='float', default=0.5,
                                         description='PV distance as fraction of chain length.'),
        'switch_ctrl_shape': OptionField(type='shape_enum', default='ikfk',
                                         description=('Shape for the IK/FK switch ctrl '
                                          '(arms and legs alike). Defaults to the '
                                          "CtrlEditor 'ikfk' library shape (Adrian's "
                                          'gear-plus-lettering mark, authored for this '
                                          'ctrl, 2026-07-09). Still a plain shape_enum — '
                                          'pick any other library shape here if preferred.')),
        'stretchy':          OptionField(type='bool', default=True,
                                         description='Classic stretchy IK — chain extends when the IK end ctrl is pulled past rest length. Stretch only, no squash. Affects IK mode only; FK pose unchanged. Default ON (2026-07-09, Task 4) for every NEW build — fs_app.build_modules/unbuild_modules only setdefault() this from the contract when the component node\'s own options_json is missing the key, so a rig where the animator has explicitly toggled it off keeps that captured value across rebuilds (this OptionField.default is the single source of truth per fs_app.py\'s own comment at the setdefault call site — never re-default this elsewhere).'),
        'channels':          OptionField(type='channels',
                                         default={'keyable': ['tx', 'ty', 'tz',
                                                              'rx', 'ry', 'rz']}),
    },
    side_supported=True,
    color='#FFD999',  # peach — IK family, lightest
    joint_roles=(
        JointRole('start', 'Start joint (shoulder/hip)'),
        JointRole('mid',   'Mid joint (elbow/knee)',  descendant_of='start'),
        JointRole('end',   'End joint (wrist/ankle)', descendant_of='mid'),
    ),
    space_consumers=(
        SpaceConsumer(
            ctrl_role='pv_ctrl',
            attr_name='space',
            defaults=('auto', 'world', '<id>.ik_ctrl_out', '<id>.anchor_out'),
            default='auto',
        ),
        SpaceConsumer(
            ctrl_role='ik_end_ctrl',
            attr_name='space',
            defaults=('root', 'world', '<id>.anchor_out'),
            default='root',
        ),
    ),
    actions=(
        Action('Match to IK',  'master_switch_ctrl',
               'maya_tools.rigging.fabricator.actions.simple_ik.switch_to_ik_match',  section='Mode'),
        Action('Match to FK',  'master_switch_ctrl',
               'maya_tools.rigging.fabricator.actions.simple_ik.switch_to_fk_match',  section='Mode'),
        Action('Switch to IK', 'master_switch_ctrl',
               'maya_tools.rigging.fabricator.actions.simple_ik.switch_to_ik',        section='Mode'),
        Action('Switch to FK', 'master_switch_ctrl',
               'maya_tools.rigging.fabricator.actions.simple_ik.switch_to_fk',        section='Mode'),
    ),
    mirror_rules=(
        # FK ctrls: rotations copy verbatim (pre-mirrored joint frames),
        # translations flip across the pair.
        MirrorRule(
            ctrl_role='fk_ctrl',
            negate=frozenset({'translateX', 'translateY', 'translateZ'}),
        ),
        # IK end ctrl (wrist/hand): SimpleIK keeps the wrist's joint-orient
        # OPM at bind. Rotations copy verbatim across L↔R; translations
        # flip all three to produce a mirrored world position.
        MirrorRule(
            ctrl_role='ik_end_ctrl',
            negate=frozenset({'translateX', 'translateY', 'translateZ'}),
        ),
        # PV ctrl: placed in world space via perpendicular projection;
        # behaves like the IK end ctrl for mirror purposes.
        MirrorRule(
            ctrl_role='pv_ctrl',
            negate=frozenset({'translateX', 'translateY', 'translateZ'}),
        ),
        # master_switch_ctrl: no TRS to mirror (the ik_fk_blend lives in
        # custom attrs which are swapped verbatim by the dispatcher).
        # Empty rule suppresses sign flips on any stray TRS animation.
        MirrorRule(
            ctrl_role='master_switch_ctrl',
            negate=frozenset(),
        ),
    ),
)


def _create_chain_duplicate(bind_joints: list, suffix: str, parent: str) -> list:
    """Duplicate a chain of bind joints with `suffix` (e.g. '_fk', '_ik')
    appended to each name. Parent the chain root under `parent`. Returns
    the list of created joint names in chain order.

    Uses cmds.duplicate(parentOnly=True) so every joint attr is copied
    verbatim — rotateOrder, jointOrient, rotate, translate, scale,
    segmentScaleCompensate. Then cmds.matchTransform (matrix-based,
    rotateOrder-agnostic) re-snaps the world transform to bind after the
    reparent. The post-reparent snap is load-bearing: cmds.parent on joints
    rebases the parent-rotation delta into jointOrient via FP-noisy
    arithmetic, drifting mid-chain joints by small but visible amounts.

    Then makeIdentity bakes rotate into jointOrient so each duplicate has
    rotate=0 at rest. This is critical for the IK solver: with non-zero
    rotate at bind, the solver produces a different rotate value for the
    same world position but a different world rotation (twist mismatch
    around the bone axis on aimed joints). With rotate=0 at bind, the
    solver leaves the chain undisturbed and the IK chain reproduces bind
    world rotations exactly. Matches the legacy v1 simple_ik_arm.make_jnts
    pattern.
    """
    created = []
    parent_jnt = parent
    for bind in bind_joints:
        short = bind.split('|')[-1].split(':')[-1]
        new_name = f'{short}{suffix}'
        new_jnt = cmds.duplicate(bind, parentOnly=True, name=new_name)[0]
        # cmds.duplicate auto-propagates outgoing .message connections from
        # the source — so a bind joint connected to fab_<id>.joints[N]
        # results in the duplicate also landing in that multi-attr at the
        # next index, polluting get_component_joints() with FK/IK/BLEND
        # scaffolding. Disconnect defensively before going further.
        for dest in cmds.listConnections(f'{new_jnt}.message',
                                         source=False, destination=True,
                                         plugs=True) or []:
            cmds.disconnectAttr(f'{new_jnt}.message', dest)
        cmds.parent(new_jnt, parent_jnt)
        cmds.matchTransform(new_jnt, bind,
                            position=True, rotation=True, scale=True)
        # Bake rotate into jointOrient so the duplicate has rotate=0 at
        # rest. Required for the IK chain to reproduce bind world rotations
        # exactly (without this, the IK solver picks rotate values that
        # achieve the same world position but a different world rotation —
        # 90° twist mismatch around the bone axis on aimed joints, which
        # breaks the IK→FK switch on the arm).
        #
        # Side effect: at ikHandle creation, current rotate is 0, so
        # ikHandle's documented auto-setPreferredAngles=True writes 0 onto
        # the chain — the solver then has no bend hint and CAN lock dead-
        # straight on near-straight chains (Manny's T-pose leg, where the
        # natural knee bend is too small to escape this). Trade-off
        # accepted for now because the orchestrator's bind-pose capture
        # at build start + restore at unbuild end (in fs_app.py) makes
        # the lock-up non-destructive on the bind chain. Future fix path
        # is PV-direction-aware preferredAngle seeding on the mid joint
        # post-ikHandle.
        cmds.makeIdentity(new_jnt, apply=True,
                          translate=False, rotate=True, scale=False, normal=0)
        # Internal scaffolding — hide from animator surfaces.
        from maya_tools.rigging.fabricator import nodes as _ks_nodes
        _ks_nodes.hide_internal(new_jnt)
        created.append(new_jnt)
        parent_jnt = new_jnt
    return created


def _position_pv_at_build(start_joint: str, mid_joint: str, end_joint: str,
                          distance_factor: float) -> tuple:
    """Initial PV position from the bind pose. Returns a (x, y, z) tuple.

    Perpendicular projection from elbow onto shoulder→wrist line, scaled
    by chain length. Falls back to a per-side world axis on fully-straight
    chains where the perpendicular collapses.
    """
    import maya.api.OpenMaya as om
    s = cmds.xform(start_joint, q=True, ws=True, t=True)
    m = cmds.xform(mid_joint,   q=True, ws=True, t=True)
    e = cmds.xform(end_joint,   q=True, ws=True, t=True)
    sv, mv, ev = om.MVector(*s), om.MVector(*m), om.MVector(*e)
    chain_len = (mv - sv).length() + (ev - mv).length()
    se = ev - sv
    sm = mv - sv
    se_len = se.length()
    if se_len < 1e-6:
        # Folded chain — pick a default direction
        arrow = om.MVector(0, 0, -1)
    else:
        se_norm = se / se_len
        arrow = sm - (sm * se_norm) * se_norm
    if arrow.length() < 1e-3:
        # Straight chain — use cross-product fallback
        normal = se ^ sm
        if normal.length() < 1e-3:
            arrow = om.MVector(0, 0, -1)  # arm default; flip per-side later
        else:
            arrow = (normal ^ se).normal()
    arrow = arrow.normal() * (chain_len * distance_factor)
    final = mv + arrow
    return (final.x, final.y, final.z)


def _build_auto_pv_dg(start_joint: str,
                      ik_end_ctrl: str,
                      bind_perp_vec: tuple,
                      bind_limb_length: float,
                      pv_distance: float,
                      name_prefix: str) -> tuple:
    """Build the auto-PV node graph. Returns (compose_node, [created_nodes]).
    The compose_node's outputMatrix is the auto-PV world position as a matrix.

    Uses bind_limb_length (static, captured at build) for the PV's
    perpendicular offset magnitude — keeps the PV at a constant distance
    from the limb axis as the chain folds, avoiding the IK flip near
    singular points. Math: read shoulder + IK end world positions; midpoint
    and limb axis via plusMinusAverage; reproject bind_perp_vec onto the
    perpendicular of the live limb axis (cross-product double-cross); scale
    by bind_limb_length × pv_distance; add to midpoint.

    All nodes are message-tracked by the caller via space_switch_nodes
    (since the auto-PV graph is part of the PV ctrl's space-switching DG).
    """
    p = name_prefix
    decompose_start = cmds.createNode('decomposeMatrix', name=f'{p}_auto_decomp_start')
    decompose_end = cmds.createNode('decomposeMatrix', name=f'{p}_auto_decomp_end')
    cmds.connectAttr(f'{start_joint}.worldMatrix[0]', f'{decompose_start}.inputMatrix')
    cmds.connectAttr(f'{ik_end_ctrl}.worldMatrix[0]', f'{decompose_end}.inputMatrix')

    # midpoint = (start + end) / 2
    mid_pma = cmds.createNode('plusMinusAverage', name=f'{p}_auto_mid')
    cmds.setAttr(f'{mid_pma}.operation', 3)  # average
    cmds.connectAttr(f'{decompose_start}.outputTranslate', f'{mid_pma}.input3D[0]')
    cmds.connectAttr(f'{decompose_end}.outputTranslate', f'{mid_pma}.input3D[1]')

    # limb_axis = end - start
    axis_pma = cmds.createNode('plusMinusAverage', name=f'{p}_auto_axis')
    cmds.setAttr(f'{axis_pma}.operation', 2)  # subtract
    cmds.connectAttr(f'{decompose_end}.outputTranslate', f'{axis_pma}.input3D[0]')
    cmds.connectAttr(f'{decompose_start}.outputTranslate', f'{axis_pma}.input3D[1]')

    # bind_perp_vec is a static vector — store on a multiplyDivide as inputs
    bind_perp_node = cmds.createNode('multiplyDivide', name=f'{p}_auto_bind_perp')
    cmds.setAttr(f'{bind_perp_node}.input1X', bind_perp_vec[0])
    cmds.setAttr(f'{bind_perp_node}.input1Y', bind_perp_vec[1])
    cmds.setAttr(f'{bind_perp_node}.input1Z', bind_perp_vec[2])
    cmds.setAttr(f'{bind_perp_node}.input2X', 1.0)
    cmds.setAttr(f'{bind_perp_node}.input2Y', 1.0)
    cmds.setAttr(f'{bind_perp_node}.input2Z', 1.0)
    # output1 = bind_perp (passes through; we just need a vector source)

    # bend_axis = limb_axis × bind_perp (cross product)
    cross1 = cmds.createNode('vectorProduct', name=f'{p}_auto_cross1')
    cmds.setAttr(f'{cross1}.operation', 2)  # cross
    cmds.connectAttr(f'{axis_pma}.output3D', f'{cross1}.input1')
    cmds.connectAttr(f'{bind_perp_node}.output', f'{cross1}.input2')

    # elbow_out = bend_axis × limb_axis (cross product, normalized)
    cross2 = cmds.createNode('vectorProduct', name=f'{p}_auto_cross2')
    cmds.setAttr(f'{cross2}.operation', 2)  # cross
    cmds.setAttr(f'{cross2}.normalizeOutput', 1)
    cmds.connectAttr(f'{cross1}.output', f'{cross2}.input1')
    cmds.connectAttr(f'{axis_pma}.output3D', f'{cross2}.input2')

    # offset = elbow_out * (bind_limb_length * pv_distance)
    scale_node = cmds.createNode('multiplyDivide', name=f'{p}_auto_scale')
    cmds.setAttr(f'{scale_node}.operation', 1)  # multiply
    cmds.connectAttr(f'{cross2}.output', f'{scale_node}.input1')
    # bind_limb_length × pv_distance — static product, no live distance node
    length_scale = cmds.createNode('multiplyDivide', name=f'{p}_auto_lenmul')
    cmds.setAttr(f'{length_scale}.input1X', bind_limb_length)
    cmds.setAttr(f'{length_scale}.input2X', pv_distance)
    cmds.connectAttr(f'{length_scale}.outputX', f'{scale_node}.input2X')
    cmds.connectAttr(f'{length_scale}.outputX', f'{scale_node}.input2Y')
    cmds.connectAttr(f'{length_scale}.outputX', f'{scale_node}.input2Z')

    # final = midpoint + offset
    final_pma = cmds.createNode('plusMinusAverage', name=f'{p}_auto_final')
    cmds.setAttr(f'{final_pma}.operation', 1)  # sum
    cmds.connectAttr(f'{mid_pma}.output3D', f'{final_pma}.input3D[0]')
    cmds.connectAttr(f'{scale_node}.output', f'{final_pma}.input3D[1]')

    # Compose into a matrix
    compose = cmds.createNode('composeMatrix', name=f'{p}_auto_compose')
    cmds.connectAttr(f'{final_pma}.output3D', f'{compose}.inputTranslate')

    # Return all created nodes for tracking + the output node
    created = [decompose_start, decompose_end, mid_pma, axis_pma, bind_perp_node,
               cross1, cross2, scale_node, length_scale, final_pma, compose]
    return compose, created


def _build_ctrl_world_position_reader(ctrl: str, name_prefix: str) -> tuple:
    """Build a small DG graph that outputs `ctrl`'s world-space PIVOT
    POSITION as a point, with NO dependency edge on `ctrl`'s own local
    rotate channels.

    2026-07-09 pin-cycle fix (Schleifer elbow pin, Task 1): the pin
    mechanism below used to connect `ctrl.worldMatrix[0]` (the FULL
    composed matrix) directly into distanceBetween.inMatrix. Maya's
    attributeAffects graph declares translate, rotate, AND scale as
    upstream of `matrix`/`worldMatrix` together — a single opaque
    attribute — so ANY consumer of worldMatrix picks up a dependency edge
    on rotate too, even though a node's own rotate mathematically never
    moves its own pivot (rotation happens AROUND the pivot, not to it).
    That extra edge was harmless in isolation, but it was exactly the
    dependency that closed a same-frame cycle once something downstream
    of an arm's own IK solve got wired to drive `pv_ctrl.rotate` (see
    _limb_common.aim_pv_at_mid's docstring for the full empirical
    writeup this fix resolves) — the arm's own bind elbow would silently
    freeze (0.00 deg response) with no warning in mayapy/batch mode.

    Fix: read `ctrl.translate` (a leaf attribute — no attributeAffects
    edge from rotate) combined with `ctrl.offsetParentMatrix` and
    `ctrl.parentMatrix[0]` (both upstream of / independent from ctrl's
    own local TRS) via multMatrix + pointMatrixMult. This reproduces the
    exact world-position VALUE `cmds.xform(ctrl, ws=True, q=True, t=True)`
    would read (holding for every ctrl this helper targets, since they
    all keep rotatePivot/scalePivot at the studio's usual zero), while
    genuinely severing the DG edge from ctrl.rotate — pv_ctrl.rotate can
    now be driven from anywhere (including this same arm's own blend
    chain) without closing a cycle back into the pin distances.

    Returns (multMatrix_node, pointMatrixMult_node). Feed the
    pointMatrixMult node's `.output` plug into distanceBetween's
    `point1`/`point2` — NOT `inMatrix1`/`inMatrix2` (a connected inMatrix
    takes priority over point on that node and would silently undo this
    fix). Both nodes are plain DG nodes (no DAG parent) — caller is
    responsible for message-tracking them for unbuild cleanup, same as
    every other node this pin mechanism creates.
    """
    mm = cmds.createNode('multMatrix', name=f'{name_prefix}_wpos_mm')
    cmds.connectAttr(f'{ctrl}.offsetParentMatrix', f'{mm}.matrixIn[0]')
    cmds.connectAttr(f'{ctrl}.parentMatrix[0]', f'{mm}.matrixIn[1]')
    pmm = cmds.createNode('pointMatrixMult', name=f'{name_prefix}_wpos_pmm')
    cmds.connectAttr(f'{ctrl}.translate', f'{pmm}.inPoint')
    cmds.connectAttr(f'{mm}.matrixSum', f'{pmm}.inMatrix')
    return mm, pmm


class SimpleIKComponent(Component):
    CONTRACT = SIMPLE_IK_CONTRACT

    # Bend-deviation threshold (degrees, measured at the mid joint between
    # the start->mid and mid->end segment directions) below which the
    # 3-joint chain is treated as "near-straight" for the preferredAngle
    # seeding in _compute_straight_chain_bend_hint. Chosen well above ordinary
    # float noise from the matchTransform/duplicate/makeIdentity round-trip
    # in _create_chain_duplicate (fractions of a degree) but well below a
    # normal working bend (tens of degrees on a typical arm/leg): a chain
    # bent by 2 degrees or less reads as visually and numerically straight,
    # so its own joint positions can't be trusted to hand the RP solver an
    # unambiguous bend plane. Anything bent 2 degrees or more already has a
    # working bend plane from its own geometry, so it is left untouched —
    # per docs/components/simple-ik.md Gotchas, a genuinely bent chain must
    # keep whatever preferredAngle it already has.
    STRAIGHT_CHAIN_EPSILON_DEG = 2.0

    # Magnitude (degrees) of the synthetic preferredAngle hint written onto
    # the mid IK joint's chosen axis when a chain qualifies as near-straight.
    # Only needs to be big enough to break the RP solver's degenerate tie at
    # exact bind time — once the animator moves the chain off the straight
    # line by more than a hair, the actual IK target position dominates the
    # solve and this seed stops mattering.
    STRAIGHT_CHAIN_SEED_DEG = 5.0

    @classmethod
    def _compute_straight_chain_bend_hint(cls, ik_chain: list, pv_ctrl: str):
        """Compute the mid IK joint's preferredAngle hint from the PV
        position when the chain is near-straight. Returns a `[x, y, z]`
        degrees triple to `cmds.setAttr(mid_ik + '.preferredAngle', ...)`
        onto the mid joint, or `None` if the chain doesn't qualify (see
        below) — the caller is responsible for the actual write (see
        build()'s call sites: this is read-only / has no side effects).

        Context (docs/components/simple-ik.md Gotchas; CLAUDE.md's Fabricator
        gotcha 3): _create_chain_duplicate bakes the IK chain's rotate to 0
        via makeIdentity so the IK/FK switch never introduces a twist. That
        means cmds.ikHandle's auto setPreferredAngles=True (which reads
        current rotate at handle-creation time) always writes (0, 0, 0) onto
        the chain, regardless of how bent it actually is. That's harmless on
        a normally bent chain — the mid joint's own bind POSITION already
        hands the RP solver an unambiguous bend plane — but on a near-
        straight chain there is no bend deviation to read a plane from, and
        the solver can lock the chain dead-straight with no way to escape it.

        Fix: measure the actual bend deviation at the mid joint (angle
        between the start->mid and mid->end segment directions, in world
        space). Below STRAIGHT_CHAIN_EPSILON_DEG, treat the chain as
        straight and derive a synthetic bend hint from the resolved PV ctrl
        position instead: project the PV's offset from the mid joint onto
        the plane perpendicular to the chain axis to get the desired bend
        direction, cross that with the chain axis to get the world-space
        rotation axis that bends the chain toward the PV, then find which of
        the mid joint's own local rotation axes (X/Y/Z) is most aligned with
        that bend axis, and return a small signed hint on just that one
        channel. At or above the epsilon, the chain already has a working
        bend plane from its own geometry, so `None` is returned and the
        caller must leave preferredAngle exactly as cmds.ikHandle's auto-spa
        wrote it — a genuinely bent chain's existing (working) path is
        never clobbered.

        MUST be called BEFORE cmds.ikHandle exists on this chain (build()
        calls it right after the whole-chain `cmds.makeIdentity(..., r=1,
        ...)` that immediately precedes ikHandle creation) — at that point
        every joint in ik_chain is guaranteed rotate=0 (freshly baked), so
        "read the mid joint's current world matrix rows as its local
        rotate/preferredAngle axes" is exact. Once an ikHandle exists and
        has been evaluated even once, the RP solver owns that joint's
        rotate and a live query can no longer be trusted to reflect the
        pure jointOrient frame — for a chain that's not just near-straight
        but EXACTLY collinear, the solver's own handling of that
        degenerate case is exactly the kind of ambiguity this method exists
        to preempt, so reading through it would reintroduce the same
        problem one level down. pv_ctrl is already fully placed by this
        point in build() (built well before the IK chain's ikHandle), so
        its position is already the final "resolved PV position".

        Overridable: RibbonIKArm and IKLeg both inherit this verbatim today — the
        knee/elbow mid joint isn't touched by either subclass's own
        specialization (IKLeg's reverse-foot pivot stack only rewires the
        ankle/ball/toe-tip end of the chain). A future subclass with a
        different mid-joint axis convention can override this classmethod
        without touching SimpleIKComponent.build's shared call sites.
        """
        import maya.api.OpenMaya as om

        start_ik, mid_ik, end_ik = ik_chain
        s = om.MVector(*cmds.xform(start_ik, q=True, ws=True, t=True))
        m = om.MVector(*cmds.xform(mid_ik,   q=True, ws=True, t=True))
        e = om.MVector(*cmds.xform(end_ik,   q=True, ws=True, t=True))

        v1 = m - s
        v2 = e - m
        len1, len2 = v1.length(), v2.length()
        if len1 < 1e-6 or len2 < 1e-6:
            return None  # degenerate zero-length segment — nothing to seed

        cos_angle = (v1 * v2) / (len1 * len2)
        cos_angle = max(-1.0, min(1.0, cos_angle))
        bend_deg = math.degrees(math.acos(cos_angle))
        if bend_deg >= cls.STRAIGHT_CHAIN_EPSILON_DEG:
            return None  # genuinely bent — its own geometry already disambiguates

        chain_axis = e - s
        if chain_axis.length() < 1e-6:
            return None  # fully folded chain — no axis to bend around either
        chain_axis = chain_axis.normal()

        pv_pos = om.MVector(*cmds.xform(pv_ctrl, q=True, ws=True, t=True))
        offset = pv_pos - m
        pv_perp = offset - (offset * chain_axis) * chain_axis
        if pv_perp.length() < 1e-6:
            return None  # PV sits exactly on the chain axis — no side to bend to
        pv_perp = pv_perp.normal()

        bend_axis = chain_axis ^ pv_perp
        if bend_axis.length() < 1e-6:
            return None
        bend_axis = bend_axis.normal()

        # mid_ik's rotate is guaranteed 0 at this call site (see the
        # docstring's ordering requirement), so its current world matrix's
        # rotation rows ARE its local rotate/preferredAngle axes expressed
        # in world space (row 0 = local X, row 1 = local Y, row 2 = local Z
        # — Maya's row-vector convention, v' = v * M).
        mat = cmds.xform(mid_ik, q=True, ws=True, matrix=True)
        local_axes = (
            om.MVector(mat[0], mat[1], mat[2]),    # local X
            om.MVector(mat[4], mat[5], mat[6]),    # local Y
            om.MVector(mat[8], mat[9], mat[10]),   # local Z
        )
        dots = [axis * bend_axis for axis in local_axes]
        best = max(range(3), key=lambda i: abs(dots[i]))
        sign = 1.0 if dots[best] >= 0 else -1.0

        seed = [0.0, 0.0, 0.0]
        seed[best] = sign * cls.STRAIGHT_CHAIN_SEED_DEG
        return seed

    @classmethod
    def can_apply(cls, joints, blueprint) -> tuple:
        """Override: SimpleIK validates that the 3 user-selected joints form
        a direct parent chain in the blueprint. Selection order doesn't
        matter — joints are sorted by hierarchy depth first (mirrors
        fs_app._resolve_initial_joints). The first-child-walk approach used
        previously failed on branched skeletons (e.g. biped_male's
        upperarm_l having both lowerarm_l and pauldron_l as children)
        because it picked the first child by skeleton_joints order, which
        could land on a non-chain branch.
        """
        ok, reason = super().can_apply(joints, blueprint)
        if not ok:
            return False, reason
        if not joints or blueprint is None:
            return False, 'No joint(s) selected or no blueprint loaded.'

        from maya_tools.rigging.fabricator.fs_app import _sort_by_depth
        sorted_joints = _sort_by_depth(list(joints), blueprint)
        if len(sorted_joints) < 3:
            return False, 'Selection did not produce a 3-joint chain after depth sort.'

        parent_map = {j.name: j.parent for j in blueprint.skeleton_joints}
        if parent_map.get(sorted_joints[1]) != sorted_joints[0]:
            return False, (f'{sorted_joints[1]!r} is not a direct child of '
                           f'{sorted_joints[0]!r}; select 3 joints that form '
                           f'a parent chain.')
        if parent_map.get(sorted_joints[2]) != sorted_joints[1]:
            return False, (f'{sorted_joints[2]!r} is not a direct child of '
                           f'{sorted_joints[1]!r}; select 3 joints that form '
                           f'a parent chain.')
        return True, ''

    @classmethod
    def build(cls, instance, context) -> None:
        """Build SimpleIK rig. Three duplicate chains (FK, IK, BLEND) plus
        ctrls and constraint-based IKFK blending."""
        from maya_tools.rigging.fabricator import nodes

        joints = instance.joints
        if len(joints) != 3 or not all(cmds.objExists(j) for j in joints):
            raise RuntimeError(
                f"SimpleIK on {instance.id!r} needs 3 existing joints; "
                f"got {joints}."
            )
        start, mid, end = joints

        # Resolve component network node early so _tag_ctrl can use it
        # during ctrl creation (before the wiring block).
        from maya_tools.utils.maya import network_nodes as nn
        from maya_tools.rigging.fabricator import nodes as ks_nodes
        component_node = None
        for cn in ks_nodes.get_all_component_nodes():
            if ks_nodes.get_component_id(cn) == instance.id:
                component_node = cn
                break

        def _tag_ctrl(ctrl_name: str, role: str, joint_index: int = -1):
            """Thin wrapper over ks_nodes.tag_ctrl — captures the closure's
            component_node so the call sites don't repeat it.
            """
            ks_nodes.tag_ctrl(ctrl_name, role,
                              component_node=component_node or '',
                              joint_index=joint_index)

        # Containers
        controls_grp = nodes.ensure_controls_grp()
        nulls_grp = nodes.ensure_nulls_grp()

        # Null pair on start (existing pattern, anchors the bind chain)
        offset_null, connector_null = nodes.build_null_pair(start)

        # SimpleIK setup_grp (FK + IK + BLEND chains live here)
        short_start = start.split('|')[-1].split(':')[-1]
        setup_grp_name = f'{short_start}_setup_grp'
        if cmds.objExists(setup_grp_name):
            cmds.delete(setup_grp_name)  # idempotent if a prior build orphaned one
        setup_grp = cmds.createNode('transform', name=setup_grp_name, parent=nulls_grp)
        cmds.setAttr(f'{setup_grp}.inheritsTransform', False)
        # Hide the duplicate joint chains (FK/IK/BLEND) — they're scaffolding,
        # animators interact via ctrls only.
        cmds.setAttr(f'{setup_grp}.visibility', 0)

        # Three duplicate chains under setup_grp: FK, IK, BLEND
        fk_chain    = _create_chain_duplicate([start, mid, end], '_fk',    setup_grp)
        ik_chain    = _create_chain_duplicate([start, mid, end], '_ik',    setup_grp)
        blend_chain = _create_chain_duplicate([start, mid, end], '_blend', setup_grp)

        instance._fk_chain    = fk_chain
        instance._ik_chain    = ik_chain
        instance._blend_chain = blend_chain
        instance._setup_grp   = setup_grp

        # Connect IK chain duplicates to the component node via a dedicated
        # message-multi. The pose library (Spec 5) uses this to capture
        # FK-equivalent rotations on IK ctrls at save time without resorting
        # to name-pattern lookup. FK chain too — same reason (FK ctrls hand
        # off TRS to fk_chain joints, which are message-tracked here).
        if component_node:
            for j in ik_chain:
                nn.connect_message_multi(j, component_node, 'ik_chain_joints')
            for j in fk_chain:
                nn.connect_message_multi(j, component_node, 'fk_chain_joints')

        # NOTE: preferredAngle starts out at cmds.ikHandle's documented
        # auto-setPreferredAngles=True at handle creation. Because we DO
        # makeIdentity on the IK chain (rotate=0 post-bake), the auto-spa
        # writes 0 onto the chain regardless of how bent it actually is —
        # fine for chains with enough geometric bend (arm in T-pose), since
        # the mid joint's own bind position already disambiguates the bend
        # plane, but it leaves near-straight chains (Manny's leg) with no
        # hint at all. _compute_straight_chain_bend_hint (computed just
        # before ikHandle, written just after the poleVectorConstraint,
        # below) re-stamps preferredAngle from the resolved PV direction
        # ONLY when the chain measures near-straight at build time — a
        # genuinely bent chain's preferredAngle is left exactly as auto-spa
        # wrote it.

        # Capture bind-pose perpendicular for auto-PV graph (Phase 1.5c).
        # This is the static "elbow points away from chain" direction at bind,
        # used by _build_auto_pv_dg to keep the PV tracking the limb's bend
        # plane as the chain animates.
        import maya.api.OpenMaya as om
        s = cmds.xform(start, q=True, ws=True, t=True)
        m = cmds.xform(mid,   q=True, ws=True, t=True)
        e = cmds.xform(end,   q=True, ws=True, t=True)
        sv, mv, ev = om.MVector(*s), om.MVector(*m), om.MVector(*e)
        se = ev - sv
        sm = mv - sv
        se_len = se.length()
        if se_len > 1e-6:
            se_norm = se / se_len
            arrow = sm - (sm * se_norm) * se_norm
            if arrow.length() < 1e-3:
                arrow = om.MVector(0, 0, -1)
            else:
                arrow = arrow.normal()
        else:
            arrow = om.MVector(0, 0, -1)
        instance._bind_perp_vec = (arrow.x, arrow.y, arrow.z)
        # Bind-pose limb length for auto-PV's static perpendicular distance.
        # Live distance would collapse the PV onto the limb axis as the chain
        # folds, causing the IK to flip near singular points.
        instance._bind_limb_length = (mv - sv).length() + (ev - mv).length()

        # ─── Controls ───────────────────────────────────────────────────────
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator.modules.world import _apply_color, _apply_channels

        # First-build vs rebuild ctrl shape policy: if persisted has CV data
        # for a ctrl, restore it (preserves animator tweaks). Otherwise scale
        # the default shape by the corresponding bind joint's radius so ctrls
        # are sized to their bones out of the box.
        # reset_ctrl_shapes build option: blank the per-ctrl CV map so the
        # _apply_or_scale closure below falls through to the radius-scale
        # branch, giving every ctrl the default library shape.
        prev_cv = (instance.persisted or {}).get('cv_data', {})
        if context.options.get('reset_ctrl_shapes'):
            prev_cv = {}

        def _apply_or_scale(ctrl_name: str, radius_joint: str) -> None:
            # Restore the animator's persisted CV shape when present. Otherwise
            # the build_shape(radius=) call already sized the default shape to
            # radius_joint's radius, so there is nothing left to scale here.
            cv = prev_cv.get(ctrl_name)
            if cv:
                # Backward compat: cv was historically a single shape dict;
                # current schema is a list of shape dicts so multi-shape
                # library curves (sphere, sphere, …) survive the
                # unbuild→build roundtrip with all their shapes intact.
                shapes_list = cv if isinstance(cv, list) else [cv]
                try:
                    com.deserialize_shape_to(ctrl_name, {'shapes': shapes_list})
                except Exception:
                    pass

        opts = instance.options
        ctrl_shape = opts.get('ctrl_shape', 'capsule')
        ik_ctrl_shape = opts.get('ik_ctrl_shape', 'cube')
        ctrl_color = opts.get('ctrl_color', 'yellow')
        pv_shape = opts.get('pv_shape', 'diamond')
        pv_distance = float(opts.get('pv_distance', 0.5))
        switch_shape = opts.get('switch_ctrl_shape', 'cog')

        short_start = start.split('|')[-1].split(':')[-1]
        short_mid   = mid.split('|')[-1].split(':')[-1]
        short_end   = end.split('|')[-1].split(':')[-1]

        # Resolve parent for offset_ctrl via existing pattern.
        parent_matrix_plug = context.resolve_plug(instance.parent_plug)
        parent_ctrl = parent_matrix_plug.split('.')[0]
        if not cmds.objExists(parent_ctrl):
            parent_ctrl = controls_grp

        # Per-arm outliner container — holds offset_ctrl, ik_end_ctrl, pv_ctrl.
        # Mirrors v1's <start>_ik_grp pattern (rigger MechRig.ma reference).
        ik_grp_name = f'{short_start}_ik_grp'
        ik_grp = cmds.createNode('transform', name=ik_grp_name, parent=controls_grp)

        # offset_ctrl lives under ik_grp (NOT under parent_ctrl's DAG). It
        # follows parent_ctrl via parent+scale constraint instead — v1's pattern.
        # This decouples outliner organization from animation tracking.
        offset_ctrl_name = f'{short_start}_ctrl_offset'
        offset_ctrl = cmds.createNode('transform', name=offset_ctrl_name)
        cmds.matchTransform(offset_ctrl, start, position=True, rotation=True, scale=False)
        cmds.parent(offset_ctrl, ik_grp)
        cmds.matchTransform(offset_ctrl, start, position=True, rotation=True, scale=False)
        if cmds.objExists(parent_ctrl) and parent_ctrl != controls_grp:
            cmds.parentConstraint(parent_ctrl, offset_ctrl, mo=True)
            cmds.scaleConstraint(parent_ctrl, offset_ctrl, mo=True)
            # Stamp animator-friendly display name on offset_ctrl. The
            # space-switching enum builder reads this so '<id>.anchor_out'
            # shows as the parent ctrl's name (e.g. 'clavicle_ctrl') in
            # the channel box.
            if not cmds.attributeQuery('fab_display_name',
                                        node=offset_ctrl, exists=True):
                cmds.addAttr(offset_ctrl, ln='fab_display_name', dt='string')
            cmds.setAttr(f'{offset_ctrl}.fab_display_name',
                         parent_ctrl, type='string')

        # FK ctrls — each FK joint gets a per-joint offset buffer (snapped to
        # joint world transform) plus a child ctrl at local zero. Buffers are
        # parented in chain order: start_offset under offset_ctrl, mid_offset
        # under start_FK_ctrl, end_offset under mid_FK_ctrl.
        fk_ctrls = []
        fk_ctrl_offsets = []
        prev_parent = offset_ctrl
        for i, (jnt, short) in enumerate(zip(joints, (short_start, short_mid, short_end))):
            fk_offset = cmds.createNode('transform', name=f'{short}_FK_ctrl_offset')
            cmds.matchTransform(fk_offset, jnt, position=True, rotation=True, scale=False)
            cmds.parent(fk_offset, prev_parent)
            cmds.matchTransform(fk_offset, jnt, position=True, rotation=True, scale=False)

            fk_ctrl = com.build_shape(ctrl_shape, f'{short}_FK_ctrl',
                                      radius=cmds.getAttr(f'{jnt}.radius'))
            cmds.parent(fk_ctrl, fk_offset)
            for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
                cmds.setAttr(f'{fk_ctrl}.{attr}', 0)
            for attr in ('sx', 'sy', 'sz'):
                cmds.setAttr(f'{fk_ctrl}.{attr}', 1)
            _apply_color(fk_ctrl, ctrl_color)
            _apply_or_scale(fk_ctrl, jnt)

            fk_ctrls.append(fk_ctrl)
            fk_ctrl_offsets.append(fk_offset)
            _tag_ctrl(fk_ctrl, 'fk_ctrl', joint_index=i)
            prev_parent = fk_ctrl

        instance._fk_ctrl_offsets = fk_ctrl_offsets

        # IK end ctrl — parented directly under fab_controls_grp (NOT under
        # the chain offset_ctrl) so its parentMatrix is constant. The bind
        # world matrix is stamped by _tag_ctrl below; offsetParentMatrix
        # carries the actual placement via space-switching (build_space_switch_dg).
        ik_end_ctrl = com.build_shape(ik_ctrl_shape, f'{short_end}_IK_ctrl',
                                      radius=cmds.getAttr(f'{end}.radius'))
        cmds.parent(ik_end_ctrl, ik_grp)
        cmds.matchTransform(ik_end_ctrl, end, position=True, rotation=True, scale=False)
        ik_end_bind = cmds.xform(ik_end_ctrl, q=True, ws=True, matrix=True)
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{ik_end_ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{ik_end_ctrl}.{attr}', 1)
        # Static offsetParentMatrix at bind so the ctrl is visually at the
        # wrist during the constraint pass below — otherwise mo=True captures
        # offset from world origin and the IK solve drifts. The space-switch
        # wiring pass force-connects this attr to a blendMatrix output later.
        cmds.setAttr(f'{ik_end_ctrl}.offsetParentMatrix', *ik_end_bind, type='matrix')
        _apply_color(ik_end_ctrl, ctrl_color)
        _apply_or_scale(ik_end_ctrl, end)
        _tag_ctrl(ik_end_ctrl, 'ik_end_ctrl', joint_index=2)

        # PV ctrl at perpendicular projection — same world-anchored pattern.
        pv_pos = _position_pv_at_build(start, mid, end, pv_distance)
        pv_ctrl = com.build_shape(pv_shape, f'{short_mid}_PV_ctrl',
                                  radius=cmds.getAttr(f'{mid}.radius'))
        cmds.parent(pv_ctrl, ik_grp)
        cmds.xform(pv_ctrl, ws=True, t=pv_pos)
        pv_bind = cmds.xform(pv_ctrl, q=True, ws=True, matrix=True)
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{pv_ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{pv_ctrl}.{attr}', 1)
        # Static offsetParentMatrix at bind for the same reason as IK end ctrl
        # (poleVectorConstraint reads the PV ctrl's worldMatrix at creation;
        # if it's at origin we get a wrong polevector reference).
        cmds.setAttr(f'{pv_ctrl}.offsetParentMatrix', *pv_bind, type='matrix')
        _apply_color(pv_ctrl, ctrl_color)
        _apply_or_scale(pv_ctrl, mid)
        _tag_ctrl(pv_ctrl, 'pv_ctrl', joint_index=1)

        # IK/FK switch ctrl — buffer parented under ik_grp (world-anchored),
        # parent+scale constrained from wrist_blend so it always stays at the
        # wrist's current world position regardless of IK/FK mode.
        #
        # Orientation + offset (Task 3, Adrian 2026-07-09: "love the fist
        # ctrl orientation — same on the IK/FK controls, arm and leg, offset
        # 10 in Z"): matches build_fingers_ctrl's own pattern (below,
        # _limb_common.py) — the offset PARENT carries the end joint's
        # (wrist/ankle) world ORIENTATION (matchTransform, position AND
        # rotation), then gets a +10-unit nudge along its own local Z via an
        # OBJECT-SPACE move (not a world-fixed axis), so it auto-mirrors
        # correctly on a real mirrorJoint reflection the same way the fist
        # ctrl's offset does. switch_ctrl itself stays at local rotate=0 —
        # the orientation lives entirely on switch_offset.
        switch_offset = cmds.createNode('transform', name=f'{short_end}_IKFK_ctrl_offset')
        cmds.matchTransform(switch_offset, end, position=True, rotation=True, scale=False)
        cmds.parent(switch_offset, ik_grp)
        cmds.matchTransform(switch_offset, end, position=True, rotation=True, scale=False)
        cmds.move(0.0, 0.0, 10.0, switch_offset, relative=True, objectSpace=True)
        # Constrain to wrist_blend with mo=True — captures the wrist-oriented
        # +10Z offset as the maintained offset, so switch ctrl follows
        # wrist_blend during animation while preserving the visual side-step.
        wrist_blend = instance._blend_chain[-1]
        cmds.parentConstraint(wrist_blend, switch_offset, mo=True)
        cmds.scaleConstraint(wrist_blend, switch_offset, mo=True)

        switch_ctrl = com.build_shape(switch_shape, f'{short_end}_IKFK_ctrl',
                                      radius=cmds.getAttr(f'{end}.radius'))
        cmds.parent(switch_ctrl, switch_offset)
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{switch_ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{switch_ctrl}.{attr}', 1)
        _apply_color(switch_ctrl, ctrl_color)
        _apply_or_scale(switch_ctrl, end)
        # Add the IK/FK blend attribute (default 1.0 = IK on after first build)
        cmds.addAttr(switch_ctrl, ln='ik_fk_blend', at='float',
                     min=0.0, max=1.0, dv=1.0, k=True)
        # Switch ctrl doesn't drive a specific joint slot — joint_index=-1.
        _tag_ctrl(switch_ctrl, 'master_switch_ctrl', joint_index=-1)

        # Stash on instance for next tasks
        instance._fk_ctrls = fk_ctrls
        instance._ik_end_ctrl = ik_end_ctrl
        instance._pv_ctrl = pv_ctrl
        instance._switch_ctrl = switch_ctrl
        instance._switch_ctrl_offset = switch_offset

        # Register outputs
        context.register_output(instance.id, 'start_out', f'{start}.worldMatrix[0]')
        context.register_output(instance.id, 'mid_out',   f'{mid}.worldMatrix[0]')
        context.register_output(instance.id, 'end_out',   f'{end}.worldMatrix[0]')
        context.register_output(instance.id, 'ik_ctrl_out', f'{ik_end_ctrl}.worldMatrix[0]')
        context.register_output(instance.id, 'anchor_out', f'{offset_ctrl}.worldMatrix[0]')

        # FK ctrls follow the component's channels option (rotation-only by
        # default — FK chains drive joints via rotation, no translation).
        for ctrl in fk_ctrls:
            _apply_channels(ctrl, opts.get('channels', {}))

        # IK end ctrl gets translation AND rotation keyable regardless of
        # the FK channels option — the whole point of an IK end ctrl is
        # that the animator translates it through space (foot stepping,
        # hand reaching). Locking translates would defeat IK.
        _apply_channels(ik_end_ctrl,
                        {'keyable': ['tx', 'ty', 'tz', 'rx', 'ry', 'rz']})

        # ─── Wiring ────────────────────────────────────────────────────────
        # (component_node resolved at top of build; nn and ks_nodes imported there)

        # FK chain: each FK ctrl drives its FK joint via parent+scale constraint.
        for ctrl, fk_jnt in zip(fk_ctrls, instance._fk_chain):
            cmds.parentConstraint(ctrl, fk_jnt, mo=True)
            cmds.scaleConstraint(ctrl, fk_jnt, mo=True)

        # IK chain anchor: parent-constrained from parent_ctrl so the IK chain
        # follows the rig's parent (e.g. clavicle) when ik_fk_blend > 0. Without
        # this, the IK chain stays at static bind position while FK chain follows,
        # causing bind drift in IK mode. Mirrors v1's jnt_parent_null pattern.
        ik_anchor_name = f'{short_start}_ik_anchor'
        ik_anchor = cmds.createNode('transform', name=ik_anchor_name, parent=setup_grp)
        cmds.matchTransform(ik_anchor, start, position=True, rotation=True, scale=False)
        if cmds.objExists(parent_ctrl) and parent_ctrl != controls_grp:
            cmds.parentConstraint(parent_ctrl, ik_anchor, mo=True)
            cmds.scaleConstraint(parent_ctrl, ik_anchor, mo=True)
        ks_nodes.hide_internal(ik_anchor)
        # Reparent IK chain root under the anchor (preserve world position).
        cmds.parent(instance._ik_chain[0], ik_anchor)

        # IK chain: build IK handle (RP solver), parent under setup_grp.
        cmds.makeIdentity(instance._ik_chain, apply=True, t=1, r=1, s=1, n=0, pn=1)

        # Straight-chain bend hint (docs/components/simple-ik.md Gotchas):
        # COMPUTE now, immediately after the whole-chain makeIdentity above
        # and BEFORE cmds.ikHandle — every joint in the chain is guaranteed
        # rotate=0 at this exact point, which _compute_straight_chain_bend_
        # hint's axis math depends on (see its docstring). The actual
        # cmds.setAttr write is deferred until after ikHandle + the
        # poleVectorConstraint below, so it's the last word over
        # cmds.ikHandle's own auto-setPreferredAngles pass.
        straight_seed = cls._compute_straight_chain_bend_hint(
            instance._ik_chain, pv_ctrl)

        ik_handle, ik_effector = cmds.ikHandle(
            sj=instance._ik_chain[0], ee=instance._ik_chain[-1],
            sol='ikRPsolver', name=f'{short_start}_ikHandle',
        )
        cmds.parent(ik_handle, setup_grp)
        cmds.setAttr(f'{ik_handle}.visibility', 0)
        ks_nodes.hide_internal(ik_handle)
        # IK end ctrl drives the IK handle position.
        cmds.parentConstraint(ik_end_ctrl, ik_handle, mo=True)
        # IK end ctrl ALSO drives the IK end joint orientation (animator-friendly:
        # rotating the IK ctrl rotates the wrist).
        cmds.orientConstraint(ik_end_ctrl, instance._ik_chain[-1], mo=True)
        # Polevector
        cmds.poleVectorConstraint(pv_ctrl, ik_handle)

        if straight_seed is not None:
            cmds.setAttr(f'{instance._ik_chain[1]}.preferredAngle',
                         *straight_seed, type='double3')

        # ─── Stretchy IK + Schleifer Elbow Pin ───────────────────────────
        # Per-joint translateX is driven by a blendTwoAttr that lerps
        # between the "stretchy" path (rest, optionally stretched to wrist)
        # and the "pin" path (raw signed distance shoulder→PV for mid, PV→
        # wrist for end). Blend weight is pv_ctrl.pin (0..1, default 0).
        #
        # Pin attr is always added regardless of the stretchy option:
        #   stretchy=False, pin=0: chain at rest length (rigid IK).
        #   stretchy=False, pin>0: chain pins elbow to PV (becomes stretchy
        #     on pin demand only — animator's choice).
        #   stretchy=True,  pin=0: classic stretchy IK (rest until wrist
        #     exceeds rest_total, then ratio-driven length).
        #   stretchy=True,  pin>0: blend between the two stretch paths.
        #
        # Pin path uses raw distance (no clamp) — the whole point is the
        # chain matches the PV's hand-placed location. Schleifer "Animator
        # Friendly Rig" convention.
        #
        # Driving translateX directly (vs joint scale) sidesteps Maya's
        # segmentScaleCompensate behavior, which would otherwise counteract
        # parent-scale propagation.
        stretchy_nodes: list = []
        pin_nodes: list = []

        ik_mid_jnt = instance._ik_chain[1]
        ik_end_jnt = instance._ik_chain[-1]
        rest_tx_mid = float(cmds.getAttr(f'{ik_mid_jnt}.translateX'))
        rest_tx_end = float(cmds.getAttr(f'{ik_end_jnt}.translateX'))
        rest_total = abs(rest_tx_mid) + abs(rest_tx_end)

        if rest_total > 1e-6:
            sign_mid = 1.0 if rest_tx_mid >= 0 else -1.0
            sign_end = 1.0 if rest_tx_end >= 0 else -1.0

            # --- Stretchy branch (optional) ---
            # Output is per-joint scale_node.outputX = clamped_ratio * rest_tx.
            # When stretchy is off, stretchy_out_{mid,end} stay None and the
            # blend's input[0] falls back to the constant rest_tx.
            stretchy_out_mid = None
            stretchy_out_end = None
            if opts.get('stretchy', False):
                # We measure against the ikhandle (not ik_end_ctrl) so this
                # works for both arms (ikhandle parentConstrained to wrist
                # ik_end_ctrl, co-located → no-op) AND legs (ikhandle DAG-
                # parented under foot_roll_pivot, snapped to ankle_pos at
                # build, moves with the foot ctrl through the pivot stack).
                # ik_end_ctrl on legs is repositioned to floor level so the
                # foot curve sits on the ground — using it would make
                # distance > rest_total at rest and kick stretch on
                # immediately.
                dist_node = cmds.createNode('distanceBetween',
                                             name=f'{short_start}_stretch_dist')
                cmds.connectAttr(
                    f'{instance._ik_chain[0]}.worldMatrix[0]',
                    f'{dist_node}.inMatrix1')
                cmds.connectAttr(
                    f'{ik_handle}.worldMatrix[0]',
                    f'{dist_node}.inMatrix2')
                ratio_node = cmds.createNode('multiplyDivide',
                                              name=f'{short_start}_stretch_ratio')
                cmds.setAttr(f'{ratio_node}.operation', 2)  # divide
                cmds.connectAttr(f'{dist_node}.distance',
                                  f'{ratio_node}.input1X')
                cmds.setAttr(f'{ratio_node}.input2X', rest_total)
                clamp_node = cmds.createNode('condition',
                                              name=f'{short_start}_stretch_clamp')
                cmds.setAttr(f'{clamp_node}.operation', 2)  # greater than
                cmds.connectAttr(f'{ratio_node}.outputX',
                                  f'{clamp_node}.firstTerm')
                cmds.setAttr(f'{clamp_node}.secondTerm', 1.0)
                cmds.connectAttr(f'{ratio_node}.outputX',
                                  f'{clamp_node}.colorIfTrueR')
                cmds.setAttr(f'{clamp_node}.colorIfFalseR', 1.0)
                short_mid_jnt = ik_mid_jnt.split('|')[-1].split(':')[-1]
                short_end_jnt = ik_end_jnt.split('|')[-1].split(':')[-1]
                scale_mid = cmds.createNode('multiplyDivide',
                                             name=f'{short_mid_jnt}_stretch_tx')
                cmds.setAttr(f'{scale_mid}.operation', 1)  # multiply
                cmds.connectAttr(f'{clamp_node}.outColorR',
                                  f'{scale_mid}.input1X')
                cmds.setAttr(f'{scale_mid}.input2X', rest_tx_mid)
                scale_end = cmds.createNode('multiplyDivide',
                                             name=f'{short_end_jnt}_stretch_tx')
                cmds.setAttr(f'{scale_end}.operation', 1)  # multiply
                cmds.connectAttr(f'{clamp_node}.outColorR',
                                  f'{scale_end}.input1X')
                cmds.setAttr(f'{scale_end}.input2X', rest_tx_end)
                stretchy_out_mid = f'{scale_mid}.outputX'
                stretchy_out_end = f'{scale_end}.outputX'
                stretchy_nodes.extend([dist_node, ratio_node, clamp_node,
                                       scale_mid, scale_end])

            # --- Pin branch (always) ---
            # distA: shoulder → PV (upper arm length when pin=1).
            # distB: PV → ik_handle (lower arm length when pin=1).
            # Signed multiply preserves the joint's translateX sign convention
            # (which can be negative depending on chain axis orientation).
            #
            # pv_ctrl's contribution reads its world POSITION ONLY (via
            # _build_ctrl_world_position_reader), never its full worldMatrix
            # — see that helper's docstring for the 2026-07-09 cycle fix
            # this is (Task 1): the pin distances must not carry a DG
            # dependency edge on pv_ctrl.rotate, so pv_ctrl.rotate is free
            # to be driven from elsewhere (e.g. the aim-at-blend-elbow
            # wiring below) without closing a same-frame cycle back here.
            cmds.addAttr(pv_ctrl, ln='pin', at='float',
                          min=0.0, max=1.0, dv=0.0, k=True)
            pv_wpos_mm, pv_wpos_pmm = _build_ctrl_world_position_reader(
                pv_ctrl, f'{short_mid}_pv_pin')
            dist_a = cmds.createNode('distanceBetween',
                                      name=f'{short_start}_pin_distA')
            cmds.connectAttr(
                f'{instance._ik_chain[0]}.worldMatrix[0]',
                f'{dist_a}.inMatrix1')
            cmds.connectAttr(
                f'{pv_wpos_pmm}.output',
                f'{dist_a}.point2')
            dist_b = cmds.createNode('distanceBetween',
                                      name=f'{short_start}_pin_distB')
            cmds.connectAttr(
                f'{pv_wpos_pmm}.output',
                f'{dist_b}.point1')
            cmds.connectAttr(
                f'{ik_handle}.worldMatrix[0]',
                f'{dist_b}.inMatrix2')
            signed_a = cmds.createNode('multiplyDivide',
                                        name=f'{short_start}_pin_signed_mid')
            cmds.connectAttr(f'{dist_a}.distance', f'{signed_a}.input1X')
            cmds.setAttr(f'{signed_a}.input2X', sign_mid)
            signed_b = cmds.createNode('multiplyDivide',
                                        name=f'{short_start}_pin_signed_end')
            cmds.connectAttr(f'{dist_b}.distance', f'{signed_b}.input1X')
            cmds.setAttr(f'{signed_b}.input2X', sign_end)
            pin_nodes.extend([pv_wpos_mm, pv_wpos_pmm,
                              dist_a, dist_b, signed_a, signed_b])

            # --- Per-joint blend ---
            for ik_jnt, rest_tx, stretchy_out, pin_out_attr in (
                (ik_mid_jnt, rest_tx_mid, stretchy_out_mid, f'{signed_a}.outputX'),
                (ik_end_jnt, rest_tx_end, stretchy_out_end, f'{signed_b}.outputX'),
            ):
                short_jnt = ik_jnt.split('|')[-1].split(':')[-1]
                blend = cmds.createNode('blendTwoAttr',
                                         name=f'{short_jnt}_pin_blend')
                if stretchy_out:
                    cmds.connectAttr(stretchy_out, f'{blend}.input[0]')
                else:
                    cmds.setAttr(f'{blend}.input[0]', rest_tx)
                cmds.connectAttr(pin_out_attr, f'{blend}.input[1]')
                cmds.connectAttr(f'{pv_ctrl}.pin',
                                  f'{blend}.attributesBlender')
                cmds.connectAttr(f'{blend}.output',
                                  f'{ik_jnt}.translateX', force=True)
                pin_nodes.append(blend)

        # Track stretchy + pin DG nodes on the component network node so
        # unbuild can clean them up (they live at world root, NOT under
        # rig_grp). pin_nodes always exist when the chain has non-zero
        # rest length; stretchy_nodes only when the option is on.
        if stretchy_nodes and component_node:
            for n in stretchy_nodes:
                nn.connect_message_multi(n, component_node, 'stretchy_nodes')
        if pin_nodes and component_node:
            for n in pin_nodes:
                nn.connect_message_multi(n, component_node, 'pin_nodes')

        # BLEND chain: each blend joint parent+scale constrained from BOTH IK and
        # FK joint, with weights driven by switch_ctrl.ik_fk_blend (IK weight gets
        # blend value directly; FK weight gets reverse(blend) so they sum to 1).
        switch_blend_attr = f'{switch_ctrl}.ik_fk_blend'
        rev_node = cmds.createNode('reverse', name=f'{short_start}_ikfk_reverse')
        cmds.connectAttr(switch_blend_attr, f'{rev_node}.inputX')

        owned_dg_nodes = [rev_node]
        for blend_jnt, fk_jnt, ik_jnt in zip(instance._blend_chain, instance._fk_chain, instance._ik_chain):
            # Note constraint target order: IK first, FK second. So the W0 alias
            # corresponds to IK and W1 to FK.
            pcon = cmds.parentConstraint(ik_jnt, fk_jnt, blend_jnt, mo=False)[0]
            scon = cmds.scaleConstraint(ik_jnt, fk_jnt, blend_jnt, mo=False)[0]
            # interpType=2 (Shortest / quaternion blend). Default is 1 (Average)
            # which interpolates Euler components weight-wise — produces
            # wrap-around twist when IK and FK chains have different Euler
            # representations of the same world rotation, which is unavoidable
            # on aimed joints (non-identity world rotation). Quaternion blend
            # always takes the geodesic path on the rotation sphere.
            cmds.setAttr(f'{pcon}.interpType', 2)
            # Use weightAliasList to find the correct weight attrs (handles the
            # case where Maya appends 1, 2, etc. on name collisions).
            pweights = cmds.parentConstraint(pcon, q=True, weightAliasList=True)
            sweights = cmds.scaleConstraint(scon,  q=True, weightAliasList=True)
            # Direct: ik_fk_blend → IK weight (W0)
            cmds.connectAttr(switch_blend_attr, f'{pcon}.{pweights[0]}')
            cmds.connectAttr(switch_blend_attr, f'{scon}.{sweights[0]}')
            # Reversed: 1 - ik_fk_blend → FK weight (W1)
            cmds.connectAttr(f'{rev_node}.outputX', f'{pcon}.{pweights[1]}')
            cmds.connectAttr(f'{rev_node}.outputX', f'{scon}.{sweights[1]}')

        # Bind chain ← BLEND chain. Drive bind joints via parent+scale constraint
        # mo=True so the bind chain follows the blend chain in worldspace without
        # any matrix offset gymnastics.
        for bind_jnt, blend_jnt in zip(joints, instance._blend_chain):
            cmds.parentConstraint(blend_jnt, bind_jnt, mo=True)
            cmds.scaleConstraint(blend_jnt, bind_jnt, mo=True)

        # Track owned DG nodes (currently just the reverse) on component network
        # node for unbuild cleanup. Constraints under blend/bind joints cascade
        # via setup_grp delete + bind joint constraint cleanup respectively.
        if component_node:
            for n in owned_dg_nodes:
                nn.connect_message_multi(n, component_node, 'bind_blend_nodes')
        else:
            cmds.warning(
                f'SimpleIK: no component node for {instance.id!r} — '
                f'reverse node not tracked; unbuild will leak it.'
            )

        instance._owned_dg_nodes = owned_dg_nodes

        # ─── Visibility switching ──────────────────────────────────────────
        # ik_fk_blend = 0 → FK ctrls visible, IK + PV hidden
        # ik_fk_blend = 1 → FK ctrls hidden, IK + PV visible
        # Driven via condition nodes so the boolean threshold is hard-edged
        # at 0.5 (no half-visible ctrls during animated mode transitions).
        cond_fk = cmds.createNode('condition', name=f'{short_start}_fk_vis_cond')
        cmds.connectAttr(switch_blend_attr, f'{cond_fk}.firstTerm')
        cmds.setAttr(f'{cond_fk}.secondTerm', 0.5)
        cmds.setAttr(f'{cond_fk}.operation', 4)  # less than
        cmds.setAttr(f'{cond_fk}.colorIfTrueR', 1)
        cmds.setAttr(f'{cond_fk}.colorIfFalseR', 0)
        for ctrl in fk_ctrls:
            shapes = cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []
            for s in shapes:
                cmds.connectAttr(f'{cond_fk}.outColorR', f'{s}.visibility', force=True)

        cond_ik = cmds.createNode('condition', name=f'{short_start}_ik_vis_cond')
        cmds.connectAttr(switch_blend_attr, f'{cond_ik}.firstTerm')
        cmds.setAttr(f'{cond_ik}.secondTerm', 0.5)
        cmds.setAttr(f'{cond_ik}.operation', 2)  # greater than
        cmds.setAttr(f'{cond_ik}.colorIfTrueR', 1)
        cmds.setAttr(f'{cond_ik}.colorIfFalseR', 0)
        for ctrl in (ik_end_ctrl, pv_ctrl):
            shapes = cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []
            for s in shapes:
                cmds.connectAttr(f'{cond_ik}.outColorR', f'{s}.visibility', force=True)

        # Track condition nodes on the component for cleanup
        if component_node:
            for c in (cond_fk, cond_ik):
                nn.connect_message_multi(c, component_node, 'bind_blend_nodes')
        instance._owned_dg_nodes.extend([cond_fk, cond_ik])

        # ─── PV guide line ────────────────────────────────────────────────
        # Greyed-out template line from the mid bind joint to the PV ctrl.
        # Visible only in IK mode. Helps animators locate the PV ctrl in
        # the viewport — small QoL touch that adds up.
        mid_pos = cmds.xform(mid, q=True, ws=True, t=True)
        pv_pos_ws = cmds.xform(pv_ctrl, q=True, ws=True, t=True)
        pv_line = cmds.curve(
            name=f'{short_mid}_pv_line',
            degree=1, point=[mid_pos, pv_pos_ws],
        )
        cmds.parent(pv_line, ik_grp)
        ks_nodes.hide_internal(pv_line)

        # CV[0] follows mid bind joint via cluster + parentConstraint
        # (cluster handle parented under setup_grp so it cascades with
        # rig_grp deletion; parentConstraint to mid drives the handle).
        cmds.select(f'{pv_line}.cv[0]', replace=True)
        cluster_mid_node, cluster_mid_handle = cmds.cluster(
            name=f'{short_mid}_pv_line_cluster_mid')
        cmds.parent(cluster_mid_handle, setup_grp)
        cmds.matchTransform(cluster_mid_handle, mid)
        cmds.parentConstraint(mid, cluster_mid_handle, mo=True)
        ks_nodes.hide_internal(cluster_mid_handle)

        # CV[1] follows PV ctrl.
        cmds.select(f'{pv_line}.cv[1]', replace=True)
        cluster_pv_node, cluster_pv_handle = cmds.cluster(
            name=f'{short_mid}_pv_line_cluster_pv')
        cmds.parent(cluster_pv_handle, setup_grp)
        cmds.matchTransform(cluster_pv_handle, pv_ctrl)
        cmds.parentConstraint(pv_ctrl, cluster_pv_handle, mo=True)
        ks_nodes.hide_internal(cluster_pv_handle)
        cmds.select(clear=True)

        # Template the line shape (greyed + non-selectable) and tie its
        # visibility to IK mode.
        for s in cmds.listRelatives(pv_line, shapes=True, fullPath=True) or []:
            cmds.setAttr(f'{s}.overrideEnabled', 1)
            cmds.setAttr(f'{s}.overrideDisplayType', 1)  # 1 = template
            cmds.connectAttr(f'{cond_ik}.outColorR', f'{s}.visibility',
                             force=True)

        # Track cluster deformer nodes — they're DG-only (not under rig_grp)
        # and would leak otherwise. Cluster handles + curve transform DO
        # cascade with rig_grp's delete.
        if component_node:
            for n in (cluster_mid_node, cluster_pv_node):
                nn.connect_message_multi(n, component_node, 'bind_blend_nodes')
        instance._owned_dg_nodes.extend([cluster_mid_node, cluster_pv_node])

        # ─── PV ctrl aim-at-elbow (promoted to the base 2026-07-09) ───────
        # Purely cosmetic — the PV ctrl's actual function
        # (poleVectorConstraint on the ikHandle, wired above) reads only
        # translate/parentMatrix, never rotate, so this changes nothing
        # about how the chain solves BY ITSELF.
        #
        # Target is instance._blend_chain[1] — the TRUE blend mid joint
        # (elbow/knee), which live-tracks in BOTH IK and FK, the behavior
        # originally asked for. This was previously impossible: SimpleIK's
        # pin mechanism just above used to read pv_ctrl's FULL worldMatrix
        # (translate AND rotate together), so driving pv_ctrl.rotate from
        # anything downstream of this same chain's own IK solve (which the
        # blend joint is, once IK weight > 0) closed a same-frame DG cycle
        # that silently froze the elbow/knee — invisible in mayapy/batch
        # mode because cycle-check-on-evaluation defaults off there. See
        # _limb_common.aim_pv_at_mid's docstring for the full empirical
        # writeup of that finding, and _build_ctrl_world_position_reader's
        # docstring (used by the pin branch above) for the fix that makes
        # this safe: the pin distances now read pv_ctrl's world POSITION
        # only, never its worldMatrix, so pv_ctrl.rotate carries no DG edge
        # back into the pin/IK-solve path — the blend-elbow target is
        # provably cycle-free. Promoting this call here (out of RibbonIKArm) also
        # gives IKLeg's knee and any other SimpleIK-family limb the same
        # affordance for free. See _dev/test_ik_arm_maya.py's
        # test_ikarm_pv_ctrl_aims_at_fk_elbow_and_tracks_fk_poses (renamed
        # target, still the elbow-still-bends regression gate) and
        # test_ikarm_pv_ctrl_aim_no_cycle_and_unbuild_clean.
        from maya_tools.rigging.fabricator.modules import _limb_common as hc
        hc.aim_pv_at_mid(pv_ctrl, instance._blend_chain[1], component_node)

        # ─── Magic PV graph ────────────────────────────────────────────────
        # Build the DG graph that tracks the elbow's bend axis as the chain
        # animates. Expose its compose output as the per-component 'auto'
        # provider keyword (the orchestrator special-cases this); the
        # animator-facing enum label is 'magic_pv' (set on the compose node
        # via fab_display_name below).
        #
        # Limitation: the cross-cross math hits a singularity when live
        # limb_axis aligns with bind_perp_vec (e.g. IK hand traces a path
        # that crosses the shoulder's plane). At those extreme poses the
        # normalized cross amplifies noise and the PV swings erratically.
        # Animator escape hatch: switch PV.space to 'world', pose manually,
        # key it. Marking-menu match (Phase 1.5d) makes the switch+match a
        # single click. Production rigs (mGear etc.) hit the same wall and
        # accept it; fixing the singularity adds significant DG complexity
        # without fully eliminating the flicker.
        auto_compose, auto_nodes = _build_auto_pv_dg(
            start_joint=offset_ctrl,
            ik_end_ctrl=ik_end_ctrl,
            bind_perp_vec=instance._bind_perp_vec,
            bind_limb_length=instance._bind_limb_length,
            pv_distance=float(opts.get('pv_distance', 0.5)),
            name_prefix=f'{short_start}_pv',
        )
        context.register_output(instance.id, 'auto',
                                f'{auto_compose}.outputMatrix')
        # Animator-facing label override — the enum will read 'magic_pv'.
        if not cmds.attributeQuery('fab_display_name',
                                    node=auto_compose, exists=True):
            cmds.addAttr(auto_compose, ln='fab_display_name', dt='string')
        cmds.setAttr(f'{auto_compose}.fab_display_name',
                     'magic_pv', type='string')
        # Track the auto-PV nodes on the component for unbuild cleanup.
        if component_node:
            for n in auto_nodes:
                nn.connect_message_multi(n, component_node, 'space_switch_nodes')

    @classmethod
    def unbuild(cls, instance) -> dict:
        """Capture state, delete bind constraints + owned DG nodes.
        rig_grp delete (fs_app.unbuild_modules orchestrator) handles the
        ctrl/null/setup_grp cascade.
        """
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.rigging.fabricator.modules.world import _capture_channels
        from maya_tools.rigging.fabricator import nodes as ks_nodes
        from maya_tools.utils.maya import network_nodes as nn

        joints = instance.joints
        if len(joints) != 3:
            return {}
        start, mid, end = joints
        short_start = start.split('|')[-1].split(':')[-1]
        short_mid   = mid.split('|')[-1].split(':')[-1]
        short_end   = end.split('|')[-1].split(':')[-1]

        # Capture CV state from each ctrl that exists
        cv_data_block = {}
        ctrls_to_capture = [
            (f'{short_start}_FK_ctrl', instance.options.get('ctrl_shape', 'capsule')),
            (f'{short_mid}_FK_ctrl',   instance.options.get('ctrl_shape', 'capsule')),
            (f'{short_end}_FK_ctrl',   instance.options.get('ctrl_shape', 'capsule')),
            (f'{short_end}_IK_ctrl',   instance.options.get('ik_ctrl_shape', 'cube')),
            (f'{short_mid}_PV_ctrl',   instance.options.get('pv_shape', 'diamond')),
            (f'{short_end}_IKFK_ctrl', instance.options.get('switch_ctrl_shape', 'cog')),
        ]
        for ctrl_name, shape_key in ctrls_to_capture:
            if not cmds.objExists(ctrl_name):
                continue
            try:
                shape_data = com.serialize_shape(ctrl_name)
                if shape_data.get('shapes'):
                    cv_data_block[ctrl_name] = shape_data['shapes']
            except RuntimeError:
                pass

        # Capture ik_fk_blend value
        switch_ctrl_name = f'{short_end}_IKFK_ctrl'
        ik_fk_blend = 0.0
        if cmds.objExists(switch_ctrl_name) and cmds.attributeQuery(
                'ik_fk_blend', node=switch_ctrl_name, exists=True):
            ik_fk_blend = float(cmds.getAttr(f'{switch_ctrl_name}.ik_fk_blend'))

        # Channels capture from FK ctrls (representative)
        channels = {}
        for ctrl_name, _ in ctrls_to_capture[:3]:
            if cmds.objExists(ctrl_name):
                channels = _capture_channels(ctrl_name)
                break

        captured = {
            'starting_shape': instance.options.get('ctrl_shape', 'circle'),
            'cv_data': cv_data_block,
            'channels': channels,
            'ik_fk_blend': ik_fk_blend,
            'enum_orders': {},
        }

        # Delete bind-joint constraints (NOT under rig_grp; cascade leaves them).
        for jnt in joints:
            if not cmds.objExists(jnt):
                continue
            constraints = (
                (cmds.listRelatives(jnt, type='parentConstraint') or [])
                + (cmds.listRelatives(jnt, type='scaleConstraint') or [])
            )
            if constraints:
                cmds.delete(constraints)

        # Delete owned DG nodes (reverse + condition nodes tracked on
        # the component network node).
        component_node = None
        for cn in ks_nodes.get_all_component_nodes():
            if ks_nodes.get_component_id(cn) == instance.id:
                component_node = cn
                break
        if component_node:
            owned = nn.get_message_targets(component_node, 'bind_blend_nodes')
            for n in owned:
                if cmds.objExists(n):
                    cmds.delete(n)
            owned_ss = nn.get_message_targets(component_node, 'space_switch_nodes')
            for n in owned_ss:
                if cmds.objExists(n):
                    cmds.delete(n)
            owned_stretch = nn.get_message_targets(component_node, 'stretchy_nodes')
            for n in owned_stretch:
                if cmds.objExists(n):
                    cmds.delete(n)
            owned_pin = nn.get_message_targets(component_node, 'pin_nodes')
            for n in owned_pin:
                if cmds.objExists(n):
                    cmds.delete(n)

        return captured
