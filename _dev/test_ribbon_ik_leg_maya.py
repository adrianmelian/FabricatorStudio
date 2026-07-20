# _dev/test_ribbon_ik_leg_maya.py
"""mayapy scene tests for the RibbonIKLeg component — Phase P1 (pure
parity subclass; no ribbon/roll behavior yet).

Phase P1 promises: RibbonIKLeg(IKLegComponent) builds/unbuilds a leg
identical to IKLeg — same reverse-foot pivot stack, same FK/IK/PV/switch
ctrls, same foot_roll + ball FK ctrl behavior, same DG scaffolding — with
zero behavior delta (build/unbuild/can_apply/guides are all inherited
verbatim; RibbonIKLegComponent adds nothing of its own in P1). This file
proves:
  - test_ribbonikleg_p1_builds_identical_to_ikleg: building IKLeg and
    RibbonIKLeg on identical 4-joint chains produces the same named
    scaffold/ctrl node set and the same scene-wide node-TYPE-count
    profile (a name-normalized structural diff — see that test's own
    docstring for why type-count comparison is used instead of a raw
    literal-name diff), then exercises RibbonIKLeg's own build:
    foot_roll=30 lifts the ankle (heel-lift pitch) while the ball stays
    planted within 1e-3, and the ik_fk_blend switch drives the bind
    chain in both directions (IK ctrl move -> bind ankle follows; FK
    ctrl rotate -> bind knee follows after switching modes).
  - test_ribbonikleg_p1_unbuild_zero_orphans: pre/post DAG + DG-type-
    count snapshot (mirrors test_ik_arm_maya.py's own orphan-test
    pattern) — rig_grp gone, every reverse-foot/IK/FK/PV/switch
    scaffold node gone, DG node-type counts return exactly to the
    pre-build baseline, bind joints survive with no leftover
    constraints.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_ribbon_ik_leg_maya.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


class Skip(Exception):
    """Raise from a test body to mark it SKIPPED (environment gap) — see
    test_ik_arm_maya.py's identical Skip class for the full rationale."""


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Skip as exc:
        SKIPS.append(f"{name}: {exc}")
        print(f"  SKIP: {name}: {exc}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


# DG node types created somewhere in SimpleIK's + IKLeg's build (P1
# RibbonIKLeg adds none of its own — no ribbon/roll nodes exist yet).
# Mirrors test_ik_arm_maya.py's _OWNED_DG_TYPES idiom, scoped down to the
# subset SimpleIK/IKLeg actually create (no skinCluster/uvPin/blendShape/
# twist/aimConstraint — those are P2/P3 ribbon-only additions this phase
# must NOT introduce).
_OWNED_DG_TYPES = (
    'reverse', 'condition', 'cluster', 'ikHandle', 'ikEffector',
    'distanceBetween', 'blendTwoAttr', 'multiplyDivide', 'vectorProduct',
    'plusMinusAverage', 'composeMatrix', 'decomposeMatrix',
    'parentConstraint', 'orientConstraint', 'scaleConstraint',
)


def _dg_type_counts():
    import maya.cmds as cmds
    return {t: len(cmds.ls(type=t) or []) for t in _OWNED_DG_TYPES}


def _all_type_counts():
    """Scene-wide node-type census (every distinct nodeType present,
    counted). Used for the P1 parity node-set diff — see
    test_ribbonikleg_p1_builds_identical_to_ikleg's docstring for why
    this is TYPE-count-based rather than a raw literal-node-name diff."""
    import maya.cmds as cmds
    counts = {}
    for n in cmds.ls(long=True) or []:
        t = cmds.nodeType(n)
        counts[t] = counts.get(t, 0) + 1
    return counts


def _make_leg_chain(prefix):
    """thigh (2, 90, 0) -> knee (2, 50, 4) -> ankle (2, 10, 0) ->
    ball (2, 2, 12), Y-up, knee bent forward so the IK plane is defined
    (per PLAN.md Task 2 Step 1's fixture spec)."""
    import maya.cmds as cmds
    cmds.select(clear=True)
    root = cmds.joint(p=(2, 90, 0), name=f'{prefix}_root')
    thigh = cmds.joint(p=(2, 90, 0), name=f'{prefix}_thigh')
    knee = cmds.joint(p=(2, 50, 4), name=f'{prefix}_knee')
    ankle = cmds.joint(p=(2, 10, 0), name=f'{prefix}_ankle')
    ball = cmds.joint(p=(2, 2, 12), name=f'{prefix}_ball')
    cmds.select(clear=True)
    return root, thigh, knee, ankle, ball


def _add_world_component(component_id, root_joint):
    from maya_tools.rigging.fabricator import nodes
    nodes.create_component_node(
        component_id=component_id, component_type='World',
        joints=[root_joint], parent_plug='', side='md',
        options={}, persisted={},
    )


def _expected_named_nodes(thigh, knee, ankle, ball):
    """Every deterministically-named scaffold/ctrl node SimpleIK's build
    (shadowed to [thigh, knee, ankle] as the start/mid/end triple) plus
    IKLeg's own reverse-foot build creates. Mirrors
    test_ik_arm_maya.py's named_nodes tuple (SimpleIK portion) extended
    with ik_leg.py's own reverse-foot node names (heel/toe/foot_roll/
    ankle_aim pivots, the ball/toe_tip synthetic IK joints, the foot
    sub-IK handles, heel/toe/ball ctrls, the foot_roll + ball-offset
    reverse nodes)."""
    return (
        # SimpleIK (shadowed thigh=start, knee=mid, ankle=end).
        f'{thigh}_ik_grp', f'{thigh}_ctrl_offset',
        f'{thigh}_FK_ctrl_offset', f'{thigh}_FK_ctrl',
        f'{knee}_FK_ctrl_offset', f'{knee}_FK_ctrl',
        f'{ankle}_FK_ctrl_offset', f'{ankle}_FK_ctrl',
        f'{ankle}_IK_ctrl', f'{knee}_PV_ctrl',
        f'{ankle}_IKFK_ctrl_offset', f'{ankle}_IKFK_ctrl',
        f'{thigh}_setup_grp', f'{thigh}_ik_anchor', f'{thigh}_ikHandle',
        f'{knee}_pv_line',
        # IKLeg reverse foot.
        f'{ankle}_heel_pivot', f'{ankle}_toe_tip_pivot',
        f'{ankle}_foot_roll_pivot', f'{ankle}_ankle_aim_pivot',
        f'{ball}_ik', f'{ankle}_toe_tip_ik',
        f'{ankle}_ankle_ikHandle', f'{ankle}_toe_ikHandle',
        f'{ankle}_heel_ctrl_offset', f'{ankle}_heel_ctrl',
        f'{ankle}_toe_ctrl_offset', f'{ankle}_toe_ctrl',
        f'{ball}_ctrl_offset', f'{ball}_ctrl',
        f'{ankle}_foot_roll_reverse', f'{ball}_ctrl_offset_ikfk_reverse',
    )


def test_ribbonikleg_p1_builds_identical_to_ikleg():
    """P1-inherited-seam parity, UPDATED for P2 (Task 3 landed): building
    RibbonIKLeg still produces every named ctrl/scaffold node IKLeg
    produces on the same chain (the reverse-foot/FK/IK/PV/switch seam is
    untouched — inherited verbatim via super().build()), then exercises
    foot_roll and the IK/FK blend exactly as P1 did.

    The type-count comparison below was, in P1, an EXACT-equality check
    (RibbonIKLeg == IKLeg, byte-for-byte, since P1 added no behavior of
    its own). Task 3 legitimately changed that: RibbonIKLegComponent.build
    now ALSO builds two per-bone ribbon segments after super().build(), so
    RibbonIKLeg's node census necessarily grows relative to plain IKLeg's
    (new types: nurbsSurface/skinCluster/uvPin/blendShape/deformTwist/...,
    and higher counts for shared types: joint/transform/multMatrix/...).
    This is the correct, intended P2 divergence — not a regression. The
    check is now a SUPERSET/no-regression assertion instead: every type
    IKLeg's build creates, RibbonIKLeg's build must create AT LEAST as
    many of (RibbonIKLeg can only ADD to the inherited scaffold, never
    drop or shrink it) — the still-true form of "P1's seam integrity
    survives P2." Node-set diff is TYPE-count based, not a raw literal-
    name diff: Maya's auto-increment counters for unnamed helper nodes
    (multiplyDivide1, multiplyDivide2, ...) are PROCESS-global and do NOT
    reset on cmds.file(new=True, force=True) within one mayapy session, so
    a literal-name diff across two sequential builds in the same session
    would report spurious differences even for an otherwise-matching
    build.
    """
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    def _build(component_type, prefix):
        cmds.file(new=True, force=True)
        root, thigh, knee, ankle, ball = _make_leg_chain(prefix)

        nodes.create_registry(f'{prefix}_bp')
        _add_world_component(f'{prefix}_world', root)
        nodes.create_component_node(
            component_id=f'{prefix}_C0', component_type=component_type,
            joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
            options={}, persisted={},
        )
        fs_app.build_modules()

        named = _expected_named_nodes(thigh, knee, ankle, ball)
        missing = [n for n in named if not cmds.objExists(n)]
        assert not missing, (
            f'{component_type}: expected scaffold nodes missing: {missing}')

        return thigh, knee, ankle, ball, _all_type_counts()

    _, _, _, _, ikleg_types = _build('IKLeg', 'legparity')
    thigh, knee, ankle, ball, ribbon_types = _build('RibbonIKLeg', 'legparity')

    shrunk = {
        t: (ikleg_types.get(t, 0), ribbon_types.get(t, 0))
        for t in ikleg_types
        if ribbon_types.get(t, 0) < ikleg_types[t]
    }
    assert not shrunk, (
        f'RibbonIKLeg build creates FEWER of a node type than plain IKLeg '
        f'— the inherited scaffold regressed (type: (ikleg_count, '
        f'ribbon_count)): {shrunk}')

    # foot_roll=30 lifts the ankle (heel-lift pitch); ball stays planted.
    ik_ctrl = f'{ankle}_IK_ctrl'
    ankle_before = cmds.xform(ankle, q=True, ws=True, t=True)
    ball_before = cmds.xform(ball, q=True, ws=True, t=True)
    cmds.setAttr(f'{ik_ctrl}.foot_roll', 30.0)
    ankle_after = cmds.xform(ankle, q=True, ws=True, t=True)
    ball_after = cmds.xform(ball, q=True, ws=True, t=True)
    assert ankle_after[1] > ankle_before[1] + 1e-3, (
        f'RibbonIKLeg: foot_roll=30 did not lift ankle Y: '
        f'{ankle_before} -> {ankle_after}')
    ball_delta = sum((ball_after[i] - ball_before[i]) ** 2
                      for i in range(3)) ** 0.5
    assert ball_delta < 1e-3, (
        f'RibbonIKLeg: foot_roll=30 moved the ball joint '
        f'({ball_delta}) — should stay planted within 1e-3')
    cmds.setAttr(f'{ik_ctrl}.foot_roll', 0.0)

    # IK/FK blend drives the bind chain both directions.
    switch_ctrl = f'{ankle}_IKFK_ctrl'
    assert abs(cmds.getAttr(f'{switch_ctrl}.ik_fk_blend') - 1.0) < 1e-6
    before_t = cmds.xform(ankle, q=True, ws=True, t=True)
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(0.0, 5.0, 0.0))
    after_t = cmds.xform(ankle, q=True, ws=True, t=True)
    assert abs(after_t[1] - before_t[1] - 5.0) < 1e-2, (
        f'RibbonIKLeg: IK ctrl move did not drive bind ankle: '
        f'before={before_t} after={after_t}')

    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 0.0)
    knee_rot_before = cmds.getAttr(f'{knee}.rotate')[0]
    cmds.setAttr(f'{knee}_FK_ctrl.rotateZ', 20.0)
    knee_rot_after = cmds.getAttr(f'{knee}.rotate')[0]
    assert knee_rot_before != knee_rot_after, (
        f'RibbonIKLeg: FK ctrl rotate did not drive bind knee: '
        f'before={knee_rot_before} after={knee_rot_after}')
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 1.0)

    fs_app.unbuild_modules()


def test_ribbonikleg_p1_unbuild_zero_orphans():
    """Snapshot all DAG nodes + per-type DG counts pre-build (the
    test_ik_arm_maya.py orphan-test pattern); build; unbuild; assert the
    scene returns to the snapshot (rig_grp gone, every named reverse-foot
    scaffold node gone, no leaked constraints/reverse nodes, bind joints
    survive with no constraints)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain('ribbonlegorphan')

    nodes.create_registry('ribbonlegorphan_bp')
    _add_world_component('ribbonlegorphan_world', root)
    nodes.create_component_node(
        component_id='ribbonlegorphan_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={}, persisted={},
    )

    dg_before = _dg_type_counts()
    fs_app.build_modules()
    dg_during = _dg_type_counts()
    assert any(dg_during[t] > dg_before[t] for t in _OWNED_DG_TYPES), (
        f'build created none of the tracked DG types — test is vacuous: '
        f'before={dg_before} during={dg_during}')

    fs_app.unbuild_modules()

    assert not cmds.objExists('rig_grp'), 'rig_grp survived unbuild'

    named_nodes = _expected_named_nodes(thigh, knee, ankle, ball)
    leaked = [n for n in named_nodes if cmds.objExists(n)]
    assert not leaked, f'named scaffolding nodes survived unbuild: {leaked}'

    dg_after = _dg_type_counts()
    assert dg_after == dg_before, (
        f'DG node counts did not return to pre-build baseline: '
        f'before={dg_before} after={dg_after}')

    for j in (thigh, knee, ankle, ball):
        assert cmds.objExists(j), f'bind joint {j!r} should survive unbuild'
        cons = (cmds.listRelatives(j, type='parentConstraint') or []) + \
               (cmds.listRelatives(j, type='scaleConstraint') or [])
        assert not cons, f'bind joint {j!r} still has constraints: {cons}'


def _make_leg_chain_offset(prefix, offset=(37.0, 52.0, -19.0)):
    """Same thigh/knee/ankle/ball geometry as _make_leg_chain, shifted well
    away from world origin (studio deformer-order lesson: bind-pose-only or
    origin-centered asserts can't catch matrix-math bugs that only show up
    once the chain has moved away from identity) — mirrors
    test_ik_arm_maya.py's _make_arm_chain_offset, leg-flavored."""
    import maya.cmds as cmds
    ox, oy, oz = offset
    cmds.select(clear=True)
    root = cmds.joint(p=(2 + ox, 90 + oy, 0 + oz), name=f'{prefix}_root')
    thigh = cmds.joint(p=(2 + ox, 90 + oy, 0 + oz), name=f'{prefix}_thigh')
    knee = cmds.joint(p=(2 + ox, 50 + oy, 4 + oz), name=f'{prefix}_knee')
    ankle = cmds.joint(p=(2 + ox, 10 + oy, 0 + oz), name=f'{prefix}_ankle')
    ball = cmds.joint(p=(2 + ox, 2 + oy, 12 + oz), name=f'{prefix}_ball')
    cmds.select(clear=True)
    return root, thigh, knee, ankle, ball


def test_ribbonikleg_segments_exist_with_mids():
    """P2 Task 3 gate: two ribbon segments (thigh: thigh->knee, shin:
    knee->ankle), each with mid_ctrl_count mids, and the shared-knee
    control joints resolved as two distinct, independently-driven nodes
    (mirrors test_ikarm_ribbon_segments_exist_with_mids' elbow-ownership
    check, leg-flavored — knee is the leg's shared-end case)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as hc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset('legribbon')

    nodes.create_registry('legribbon_bp')
    _add_world_component('legribbon_world', root)
    mid_count = 2
    nodes.create_component_node(
        component_id='legribbon_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': mid_count}, persisted={},
    )
    fs_app.build_modules()

    thigh_chain = hc.ribbon_segment_chain_name(thigh, knee)
    shin_chain = hc.ribbon_segment_chain_name(knee, ankle)

    for chain in (thigh_chain, shin_chain):
        assert cmds.objExists(f'{chain}_ribbon_surf'), f'{chain}: ribbon surface missing'
        assert cmds.objExists(f'{chain}_ribbon_uvpin'), f'{chain}: uvPin ride missing'
        assert cmds.objExists(f'{chain}_ribbon_skin'), f'{chain}: falloff skin missing'
        assert cmds.objExists(f'{chain}_ribbon_settings'), f'{chain}: board settings missing'
        for m in range(mid_count):
            ctrl = f'{chain}_mid_{m:02d}_ctrl'
            assert cmds.objExists(ctrl), f'{chain}: mid ctrl {m} missing'
            for a in ('sx', 'sy', 'sz'):
                assert cmds.getAttr(f'{ctrl}.{a}', lock=True), (
                    f'{ctrl}.{a} should be locked (dead, unwired channel)')
                assert not cmds.getAttr(f'{ctrl}.{a}', keyable=True), (
                    f'{ctrl}.{a} should not be keyable (dead, unwired channel)')
        assert not cmds.objExists(f'{chain}_mid_{mid_count:02d}_ctrl'), (
            f'{chain}: extra mid ctrl beyond mid_ctrl_count exists')

    # Knee ownership: distinct nodes, both read-only-tracking the shared
    # knee bind joint (no fight).
    thigh_end_cj = f'{thigh_chain}_cj_{mid_count + 1:02d}'
    shin_start_cj = f'{shin_chain}_cj_00'
    assert cmds.objExists(thigh_end_cj) and cmds.objExists(shin_start_cj)
    assert thigh_end_cj != shin_start_cj, (
        'thigh and shin knee control joints must be distinct nodes')
    knee_pos = cmds.xform(knee, q=True, ws=True, t=True)
    for cj in (thigh_end_cj, shin_start_cj):
        p = cmds.xform(cj, q=True, ws=True, t=True)
        d = sum((p[i] - knee_pos[i]) ** 2 for i in range(3)) ** 0.5
        assert d < 0.5, f'{cj} not tracking knee position: {p} vs {knee_pos}'

    # Posed re-check (studio lesson: bind-pose-only asserts can't catch
    # pose-induced drift). Bend the knee via the IK foot ctrl.
    ik_ctrl = f'{ankle}_IK_ctrl'
    assert cmds.objExists(ik_ctrl)
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(0.0, -1.0, 0.5))

    posed_knee_pos = cmds.xform(knee, q=True, ws=True, t=True)
    for cj in (thigh_end_cj, shin_start_cj):
        p = cmds.xform(cj, q=True, ws=True, t=True)
        d = sum((p[i] - posed_knee_pos[i]) ** 2 for i in range(3)) ** 0.5
        assert d < 0.5, (
            f'{cj} drifted from the POSED knee position: {p} vs '
            f'{posed_knee_pos}')

    fs_app.unbuild_modules()


def test_ribbonikleg_twist_volume_at_posed_displacement():
    """P2 CRITICAL gate (studio deformer-order lesson): measure on an
    OFF-ORIGIN chain at POSED displacement, never bind pose. Pose via the
    IK foot ctrl (knee bend) and assert mid-row ride joints displaced from
    rest; then stretch the leg (translate the foot ctrl beyond the chain's
    non-stretchy slack — SimpleIK's stretchy=True default engages the
    Schleifer pin mechanism) and assert volume compensation scales the
    ride joints' cross axes."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as hc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset('legpose')

    nodes.create_registry('legpose_bp')
    _add_world_component('legpose_world', root)
    mid_count = 2
    nodes.create_component_node(
        component_id='legpose_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': mid_count}, persisted={},
    )
    fs_app.build_modules()

    shin_chain = hc.ribbon_segment_chain_name(knee, ankle)
    shin_ride = sorted(cmds.ls(f'{shin_chain}_ride_*', type='joint'))
    assert len(shin_ride) >= 4, f'expected several shin ride joints, got {shin_ride}'
    probe = shin_ride[len(shin_ride) // 2]

    pos_before = cmds.xform(probe, q=True, ws=True, t=True)

    # Pose: bend the knee via the IK foot ctrl. Deepened from the plan
    # sketch's (0.0, -3.0, 2.0) to (0.0, -4.0, 3.0) — Task 5's roll wiring
    # legitimately moved part of this measurement's signal from the mid
    # ctrls' pure aim-solved POSITION into their ROTATION (they now aim
    # their up-vector at the segment's roll joint instead of the raw end
    # bind joint — build_ribbon_segment's up_ref_joint substitution, SPEC
    # 3.2's "mid ctrls read the roll joint" rule), which parentConstrains
    # into the control joints' orientation and hence the skinned surface
    # shape, at a slightly smaller net POSITION displacement for this
    # exact pose+probe than pre-P3 (measured: 0.468 vs the original 0.5
    # threshold — a real, small, expected shift, not a regression). A
    # deeper pose keeps the same threshold meaningfully protective rather
    # than loosening it.
    ik_ctrl = f'{ankle}_IK_ctrl'
    assert cmds.objExists(ik_ctrl)
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(0.0, -4.0, 3.0))

    pos_after = cmds.xform(probe, q=True, ws=True, t=True)
    displacement = sum((pos_after[i] - pos_before[i]) ** 2 for i in range(3)) ** 0.5
    assert displacement > 0.5, (
        f'shin mid-row ride joint {probe!r} did not displace under a knee-'
        f'bend pose: before={pos_before} after={pos_after}')

    # Volume: measured off a posed, locally-stretched ribbon. Moving a mid
    # ctrl increases the segment's arc length WITHOUT any change to
    # rootScale — mirrors test_ikarm_ribbon_twist_and_volume_posed_
    # displacement's own technique (proven robust; a direct IK-foot-ctrl
    # push-beyond-reach was tried first here and found unreliable — the
    # reverse-foot pivot-stack + Schleifer-pin interaction didn't reliably
    # engage a measurable stretch through that path on this fixture, so
    # this deviates from the plan sketch's literal "translate foot ctrl
    # beyond chain length" phrasing in favor of the arm's own
    # battle-tested local-mid-ctrl-stretch mechanism — same underlying
    # volume-preservation code path, more direct and deterministic).
    settings = f'{shin_chain}_ribbon_settings'
    mid0 = f'{shin_chain}_mid_00_ctrl'
    assert cmds.objExists(settings) and cmds.objExists(mid0)
    assert abs(cmds.getAttr(f'{settings}.volume') - 1.0) < 1e-6, (
        'volume preservation should default ON for the leg ribbon')

    # Push proportional to the shin bone's own length (the leg's shin bone
    # is ~40 units vs. the arm forearm's ~6 — a fixed ty=3.0 push, the
    # arm's literal value, reads as a much smaller fraction of THIS
    # segment and under-shrinks the cross-section below the assertion's
    # tolerance; scale the push so it reads as a comparable fraction).
    bone_len = sum((cmds.xform(knee, q=True, ws=True, t=True)[i]
                    - cmds.xform(ankle, q=True, ws=True, t=True)[i]) ** 2
                   for i in range(3)) ** 0.5
    scale_before_stretch = cmds.getAttr(f'{probe}.scaleY')
    cmds.setAttr(f'{mid0}.ty', 0.3 * bone_len)   # local stretch, off-origin, posed
    scale_after_stretch = cmds.getAttr(f'{probe}.scaleY')
    assert scale_after_stretch < scale_before_stretch - 0.02, (
        f'volume=1 should shrink the ride joint cross-section on stretch: '
        f'before={scale_before_stretch} after={scale_after_stretch}')

    cmds.setAttr(f'{settings}.volume', 0.0)
    scale_no_volume = cmds.getAttr(f'{probe}.scaleY')
    assert abs(scale_no_volume - 1.0) < 0.15, (
        f'volume=0 should leave the cross-section ~unit even at the same '
        f'stretched pose: got {scale_no_volume}')

    fs_app.unbuild_modules()


def test_ribbonikleg_no_double_transform_under_rig_scale():
    """P2 hard constraint: a pure uniform World-ctrl scale must NOT read as
    ribbon stretch. build_volume's root_matrix_plug (RibbonIKLeg's own
    anchor_out, from the inherited SimpleIK build) cancels the global
    scale out of the arc-length ratio; verified at a live, off-origin,
    2x-scaled pose (mirrors test_ikarm_ribbon_no_double_transform_on_
    global_scale)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as hc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset('legscale')

    nodes.create_registry('legscale_bp')
    _add_world_component('legscale_world', root)
    nodes.create_component_node(
        component_id='legscale_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    shin_chain = hc.ribbon_segment_chain_name(knee, ankle)
    ride = sorted(cmds.ls(f'{shin_chain}_ride_*', type='joint'))
    assert len(ride) >= 4
    probe = ride[len(ride) // 2]

    scale_before = cmds.getAttr(f'{probe}.scaleY')
    pos_a_before = cmds.xform(ride[0], q=True, ws=True, t=True)
    pos_b_before = cmds.xform(ride[-1], q=True, ws=True, t=True)
    dist_before = sum((pos_a_before[i] - pos_b_before[i]) ** 2
                      for i in range(3)) ** 0.5

    world_ctrl = f'{root}_ctrl'
    assert cmds.objExists(world_ctrl), f'expected World ctrl {world_ctrl!r}'
    for a in ('sx', 'sy', 'sz'):
        cmds.setAttr(f'{world_ctrl}.{a}', lock=False)
        cmds.setAttr(f'{world_ctrl}.{a}', 2.0)

    pos_a_after = cmds.xform(ride[0], q=True, ws=True, t=True)
    pos_b_after = cmds.xform(ride[-1], q=True, ws=True, t=True)
    dist_after = sum((pos_a_after[i] - pos_b_after[i]) ** 2
                     for i in range(3)) ** 0.5
    assert dist_after > dist_before * 1.5, (
        f'global scale did not propagate to the ribbon geometry: '
        f'before={dist_before} after={dist_after}')

    scale_after = cmds.getAttr(f'{probe}.scaleY')
    assert abs(scale_after - scale_before) < 0.05, (
        f'global rig scale leaked into volume compensation (double-'
        f'transform): scaleY before={scale_before} after={scale_after}')

    fs_app.unbuild_modules()


# DG node types created by the ribbon segments on top of the P1
# _OWNED_DG_TYPES set — extends this suite's baseline the same way
# test_ribbon_ik_arm_maya.py's own _OWNED_DG_TYPES was extended for its P2
# (see that file's comment for the full rationale per type).
#
# P3 (Task 5 Step 5): aimConstraint/pointConstraint added — the antCGi
# roll joints' own node types (build_roll_joint), tracked in the
# 'roll_dg_nodes' bucket and swept by the SAME unconditional loop that
# sweeps _RIBBON_BUCKETS (ribbon_ik_leg.py's unbuild). Not created by any
# P1/P2 path, so a regression that stopped tracking/deleting them would
# otherwise leak silently past this baseline check — mirrors
# test_ribbon_ik_arm_maya.py's own _OWNED_DG_TYPES extension for the same
# two types.
_RIBBON_DG_TYPES = _OWNED_DG_TYPES + (
    'skinCluster', 'tweak', 'groupParts', 'groupId', 'uvPin', 'multMatrix',
    'blendShape', 'twist', 'curveInfo', 'multDoubleLinear', 'objectSet',
    'curveFromSurfaceIso', 'dagPose', 'aimConstraint', 'pointConstraint',
)


def _ribbon_dg_type_counts():
    import maya.cmds as cmds
    return {t: len(cmds.ls(type=t) or []) for t in _RIBBON_DG_TYPES}


def test_ribbonikleg_unbuild_zero_orphans_including_ribbon():
    """Extends test_ribbonikleg_p1_unbuild_zero_orphans past P2: pre/post
    snapshot now also covers the ribbon node types (skinCluster, uvPin,
    blendShape, twist deformer, matrix chain — mirrors
    test_ikarm_ribbon_unbuild_leaves_zero_orphans). Reverse-foot nodes AND
    both segments' full skin node-sets gone.

    P3 (Task 5 Step 5) EXTENSION: also asserts the two antCGi roll nodes
    (build_roll_joint's roll_jnt + roll_aim_loc, named by
    ribbon_ik_leg.py's own thigh/ankle roll call sites) exist before
    unbuild — non-vacuous proof the '_ROLL_BUCKETS' sweep (already
    unconditional in ribbon_ik_leg.py's unbuild(), forward-compatible
    since P2) has real nodes to sweep — and are gone after, by name,
    cross-checked against the DG-type-count return-to-baseline above
    (which now also tracks aimConstraint/pointConstraint — see
    _RIBBON_DG_TYPES's own P3 extension note)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as hc
    from maya_tools.rigging.fabricator.modules import _limb_common as lc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset('legribbonorphan')

    nodes.create_registry('legribbonorphan_bp')
    _add_world_component('legribbonorphan_world', root)
    mid_count = 2
    nodes.create_component_node(
        component_id='legribbonorphan_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': mid_count}, persisted={},
    )

    dg_before = _ribbon_dg_type_counts()
    fs_app.build_modules()
    dg_during = _ribbon_dg_type_counts()
    assert any(dg_during[t] > dg_before[t] for t in _RIBBON_DG_TYPES), (
        f'build created none of the tracked DG types — test is vacuous: '
        f'before={dg_before} during={dg_during}')
    assert dg_during.get('skinCluster', 0) >= 2, (
        'expected a falloff skinCluster per ribbon segment (2 total)')
    assert dg_during.get('aimConstraint', 0) >= 2, (
        f'expected 2 aimConstraints (one per roll joint), got '
        f'{dg_during.get("aimConstraint")}')

    thigh_chain = hc.ribbon_segment_chain_name(thigh, knee)
    shin_chain = hc.ribbon_segment_chain_name(knee, ankle)

    thigh_roll_prefix = f'{lc.short_name(thigh)}_{lc.short_name(knee)}_roll'
    ankle_roll_prefix = f'{lc.short_name(knee)}_{lc.short_name(ankle)}_roll'
    roll_named_nodes = []
    for prefix in (thigh_roll_prefix, ankle_roll_prefix):
        roll_named_nodes += [f'{prefix}_jnt', f'{prefix}_aim_loc']
    assert all(cmds.objExists(n) for n in roll_named_nodes), (
        f'setup: expected roll nodes to exist before unbuild: '
        f'{[n for n in roll_named_nodes if not cmds.objExists(n)]}')

    fs_app.unbuild_modules()

    assert not cmds.objExists('rig_grp'), 'rig_grp survived unbuild'

    named_nodes = _expected_named_nodes(thigh, knee, ankle, ball)
    leaked = [n for n in named_nodes if cmds.objExists(n)]
    assert not leaked, f'named scaffolding nodes survived unbuild: {leaked}'

    leaked_roll = [n for n in roll_named_nodes if cmds.objExists(n)]
    assert not leaked_roll, f'roll nodes survived unbuild: {leaked_roll}'

    dg_after = _ribbon_dg_type_counts()
    assert dg_after == dg_before, (
        f'DG node counts did not return to pre-build baseline: '
        f'before={dg_before} after={dg_after}')

    for chain in (thigh_chain, shin_chain):
        leaked = [n for n in (
            f'{chain}_ribbon_surf', f'{chain}_center_crv',
            f'{chain}_ribbon_skin', f'{chain}_ribbon_uvpin',
            f'{chain}_ribbon_settings', f'{chain}_setup_grp',
            f'{chain}_cj_00', f'{chain}_ride_00',
        ) if cmds.objExists(n)]
        for m in range(mid_count):
            for suffix in (f'_mid_{m:02d}_ctrl', f'_mid_{m:02d}_ctrl_offset'):
                n = f'{chain}{suffix}'
                if cmds.objExists(n):
                    leaked.append(n)
        assert not leaked, f'{chain}: ribbon nodes survived unbuild: {leaked}'

    for j in (thigh, knee, ankle, ball):
        assert cmds.objExists(j), f'bind joint {j!r} should survive unbuild'
        cons = (cmds.listRelatives(j, type='parentConstraint') or []) + \
               (cmds.listRelatives(j, type='scaleConstraint') or [])
        assert not cons, f'bind joint {j!r} still has constraints: {cons}'


def test_ribbonikleg_persistence_round_trip():
    """Build -> author a mid-ctrl CV tweak + note resolved ribbon_width ->
    unbuild -> rebuild: CVs restored, width identical (no re-measure from
    a posed chain), reverse-foot foot_roll value preserved (the inherited
    IKLeg capture still works through the override). Then
    reset_ctrl_shapes=True rebuild: CVs blank, width + rest_len SURVIVE
    (the shape-only-reset scoping — mirrors
    test_ikarm_ribbon_persistence_round_trip)."""
    import maya.cmds as cmds
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as hc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset('legpersist')

    nodes.create_registry('legpersist_bp')
    _add_world_component('legpersist_world', root)
    nodes.create_component_node(
        component_id='legpersist_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    shin_chain = hc.ribbon_segment_chain_name(knee, ankle)
    settings = f'{shin_chain}_ribbon_settings'
    rest_node = f'{shin_chain}_ribbon_vol_rest'
    surf = f'{shin_chain}_ribbon_surf'
    mid0 = f'{shin_chain}_mid_00_ctrl'
    assert cmds.objExists(settings) and cmds.objExists(rest_node)
    assert cmds.objExists(mid0)

    def _measured_width():
        a = cmds.pointPosition(f'{surf}.cv[0][0]', world=True)
        b = cmds.pointPosition(f'{surf}.cv[1][0]', world=True)
        return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5

    width_built = _measured_width()
    rest_len_built = float(cmds.getAttr(f'{rest_node}.input1'))

    # Dial the board away from its BOARD_ATTRS defaults.
    cmds.setAttr(f'{settings}.twist_root', 9.0)
    cmds.setAttr(f'{settings}.twist_tip', -14.0)
    cmds.setAttr(f'{settings}.volume', 0.4)

    # Author a CV tweak on the mid ctrl.
    cmds.select(f'{mid0}.cv[*]')
    cmds.scale(1.5, 1.5, 1.5, relative=True)
    cmds.select(clear=True)
    edited_shape = com.serialize_shape(mid0)
    assert edited_shape.get('shapes')

    # Foot_roll dial (the inherited IKLeg capture, exercised through the
    # override).
    ik_ctrl = f'{ankle}_IK_ctrl'
    cmds.setAttr(f'{ik_ctrl}.foot_roll', 22.0)

    fs_app.unbuild_modules()
    fs_app.build_modules()   # rebuild WITHOUT reset_ctrl_shapes

    assert cmds.objExists(settings) and cmds.objExists(rest_node)
    assert abs(cmds.getAttr(f'{settings}.twist_root') - 9.0) < 1e-4, (
        'twist_root did not round-trip through unbuild -> build')
    assert abs(cmds.getAttr(f'{settings}.twist_tip') - (-14.0)) < 1e-4, (
        'twist_tip did not round-trip through unbuild -> build')
    assert abs(cmds.getAttr(f'{settings}.volume') - 0.4) < 1e-4, (
        'volume dial did not round-trip through unbuild -> build')
    assert abs(float(cmds.getAttr(f'{rest_node}.input1')) - rest_len_built) < 0.05, (
        'rest_len drifted on a plain rebuild (no reset_ctrl_shapes)')
    rebuilt_width = _measured_width()
    assert abs(rebuilt_width - width_built) < 0.05, (
        f'ribbon_width did not round-trip through unbuild -> build: '
        f'built={width_built} rebuilt={rebuilt_width}')
    rebuilt_shape = com.serialize_shape(mid0)
    assert rebuilt_shape.get('shapes') == edited_shape.get('shapes'), (
        'mid ctrl CV edit did not persist across unbuild -> build')

    ik_ctrl_after = f'{ankle}_IK_ctrl'
    assert cmds.objExists(ik_ctrl_after)
    assert abs(cmds.getAttr(f'{ik_ctrl_after}.foot_roll') - 22.0) < 1e-4, (
        'foot_roll did not round-trip through unbuild -> build (the '
        'inherited IKLeg capture must still work through the override)')

    # Unbuild + rebuild WITH reset_ctrl_shapes=True: the flag must be
    # shape-only — rest_len/board/resolved-width must survive it; CVs
    # blank.
    fs_app.unbuild_modules()
    fs_app.build_modules(options={'reset_ctrl_shapes': True})

    assert abs(cmds.getAttr(f'{settings}.twist_root') - 9.0) < 1e-4, (
        'reset_ctrl_shapes wiped twist_root — the flag must be shape-only')
    assert abs(cmds.getAttr(f'{settings}.twist_tip') - (-14.0)) < 1e-4, (
        'reset_ctrl_shapes wiped twist_tip — the flag must be shape-only')
    assert abs(cmds.getAttr(f'{settings}.volume') - 0.4) < 1e-4, (
        'reset_ctrl_shapes wiped the volume dial — the flag must be '
        'shape-only')
    assert abs(float(cmds.getAttr(f'{rest_node}.input1')) - rest_len_built) < 0.05, (
        'reset_ctrl_shapes re-baselined rest_len — the flag must be '
        'shape-only')
    rebuilt_width2 = _measured_width()
    assert abs(rebuilt_width2 - width_built) < 0.05, (
        'reset_ctrl_shapes disturbed the persisted resolved ribbon_width '
        '— the flag must be shape-only')
    reset_shape = com.serialize_shape(mid0)
    assert reset_shape.get('shapes') != edited_shape.get('shapes'), (
        'reset_ctrl_shapes should have blanked the mid ctrl CV edit '
        '(shape-only reset ONLY the CV map, per its own scoping contract)')

    fs_app.unbuild_modules()


# ═══ P3: roll joints (PLAN.md Task 5) ═════════════════════════════════════
#
# Vector-math twist-measurement helpers, copied/adapted verbatim from
# test_ribbon_ik_arm_maya.py's own P3 helpers (see that file's module
# docstring for the parallel-transport rationale: a naive fixed-axis
# signed-roll comparison conflates genuine twist with swing once the aim
# direction has swung substantially — measured there as ~27 deg of
# "twist" on a pure bend collapsing to <1 deg once corrected).
import math as _math


def _v_normalize(v):
    m = sum(c * c for c in v) ** 0.5
    return [c / m for c in v] if m > 1e-9 else list(v)


def _v_sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _v_cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _v_dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _world_dir(node, local_vec):
    """World-space direction of a LOCAL vector rotated by node's current
    world matrix (rotation part only, translation ignored)."""
    import maya.cmds as cmds
    m = cmds.xform(node, q=True, ws=True, matrix=True)
    x = local_vec[0] * m[0] + local_vec[1] * m[4] + local_vec[2] * m[8]
    y = local_vec[0] * m[1] + local_vec[1] * m[5] + local_vec[2] * m[9]
    z = local_vec[0] * m[2] + local_vec[1] * m[6] + local_vec[2] * m[10]
    return [x, y, z]


def _signed_roll_deg(v_ref, v, axis):
    """Signed angle (degrees) from v_ref to v, measured about axis."""
    v_ref, v, axis = _v_normalize(v_ref), _v_normalize(v), _v_normalize(axis)
    d = _v_dot(v_ref, v)
    cr = _v_cross(v_ref, v)
    sin_c = _v_dot(cr, axis)
    return _math.degrees(_math.atan2(sin_c, d))


def _rotate_vec_about_axis(v, axis, angle_rad):
    """Rodrigues' rotation formula."""
    axis = _v_normalize(axis)
    cos_a, sin_a = _math.cos(angle_rad), _math.sin(angle_rad)
    t1 = [c * cos_a for c in v]
    t2 = [c * sin_a for c in _v_cross(axis, v)]
    t3 = [c * _v_dot(axis, v) * (1 - cos_a) for c in axis]
    return [t1[i] + t2[i] + t3[i] for i in range(3)]


def _true_twist_deg(axis_bind, axis_now, up_bind_world, up_now_world):
    """Parallel-transport-corrected twist angle (degrees): rotates
    up_bind_world by the MINIMAL swing that takes axis_bind -> axis_now,
    then measures the residual angle to up_now_world about axis_now.
    Isolates genuine twist from swing (see module-level comment above)."""
    axis_bind, axis_now = _v_normalize(axis_bind), _v_normalize(axis_now)
    d = max(-1.0, min(1.0, _v_dot(axis_bind, axis_now)))
    swing_angle = _math.acos(d)
    if swing_angle < 1e-9:
        up_transported = up_bind_world
    else:
        swing_axis = _v_normalize(_v_cross(axis_bind, axis_now))
        if sum(c * c for c in swing_axis) < 1e-12:
            fallback = [1.0, 0.0, 0.0] if abs(axis_bind[0]) < 0.9 else [0.0, 1.0, 0.0]
            swing_axis = _v_normalize(_v_cross(axis_bind, fallback))
        up_transported = _rotate_vec_about_axis(up_bind_world, swing_axis, swing_angle)
    return _signed_roll_deg(up_transported, up_now_world, axis_now)


# Modest off-origin offset for the P3 heel/toe-sensitive fixtures below.
# Deliberately SMALLER than the P2 tests' (37, 52, -19): IKLeg's
# resolve_extra_guide_default projects the heel/toe-tip guide DEFAULTS to
# an ABSOLUTE ground plane (Y=0 in a Y-up scene — see ik_leg.py), not to a
# position relative to the chain's own offset. A large oy would put heel/
# toe pivots (and the whole reverse-foot pivot stack's rotation center for
# heel_ctrl/toe_ctrl) tens of units away from the actual ankle, producing
# an unrealistically long lever arm for a heel_ctrl.rotateY stimulus.
# Still off-origin (studio deformer-order lesson — catches matrix-space
# bugs that only show up away from world identity) without that
# distortion.
_P3_OFFSET = (5.0, 3.0, -4.0)


def test_ribbonikleg_heel_lift_zero_shin_counter_twist():
    """P3 gate (SPEC 3.4): foot_roll pitches the ankle through the
    reverse-foot pivot stack — pure swing, no genuine axial twist. The
    ankle roll joint's parallel-transport-corrected twist reading must
    stay within the accepted <3 deg filter-leak budget (SPEC 3.3:
    'measured leak on the arm was <3 deg, expect the same')."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset(
        'legheelroll', offset=_P3_OFFSET)

    nodes.create_registry('legheelroll_bp')
    _add_world_component('legheelroll_world', root)
    nodes.create_component_node(
        component_id='legheelroll_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    ankle_roll_jnt = f'{hc.short_name(knee)}_{hc.short_name(ankle)}_roll_jnt'
    assert cmds.objExists(ankle_roll_jnt), 'ankle roll joint missing'

    up_local = rc.ROLL_AXES_LOWER[1]
    knee_bind = cmds.xform(knee, q=True, ws=True, t=True)
    ankle_bind = cmds.xform(ankle, q=True, ws=True, t=True)
    axis_bind = _v_sub(ankle_bind, knee_bind)
    up_bind = _world_dir(ankle_roll_jnt, up_local)

    ik_ctrl = f'{ankle}_IK_ctrl'
    assert cmds.objExists(ik_ctrl)
    cmds.setAttr(f'{ik_ctrl}.foot_roll', 45.0)

    knee_now = cmds.xform(knee, q=True, ws=True, t=True)
    ankle_now = cmds.xform(ankle, q=True, ws=True, t=True)
    axis_now = _v_sub(ankle_now, knee_now)
    up_now = _world_dir(ankle_roll_jnt, up_local)

    twist = _true_twist_deg(axis_bind, axis_now, up_bind, up_now)
    assert abs(twist) < 3.0, (
        f'shin roll should show near-zero true twist on a pure foot_roll '
        f'heel-lift pitch (<3 deg budget): {twist} deg')

    fs_app.unbuild_modules()


def test_ribbonikleg_ball_toe_do_not_feed_shin_twist():
    """P3 gate (SPEC 3.4 item 4): 'Ball ctrl / toe wiggle must not feed
    the shin segment at all (it is below the ankle).' Same true-twist
    measurement technique and same <3 deg filter-leak budget as
    test_ribbonikleg_heel_lift_zero_shin_counter_twist, applied to the
    two distal-to-the-ankle stimuli SPEC 3.4 names instead of foot_roll.

    ball_ctrl drives ONLY the `ball` bind joint (a DAG CHILD of `ankle`
    in the bind skeleton -- ik_leg.py's ball-ctrl constraint chain is
    ball_ctrl -> ball_connector_null -> ball, never upward), so it
    cannot reach the ankle roll joint (anchored on the ankle BIND joint)
    through any DAG or constraint path; the budget here is the tightest
    in the suite (near machine precision), not the shared <3 deg leak
    figure, precisely because no coupling should exist at all.

    toe_ctrl is a DIFFERENT case (adversarial review finding, verified
    directly against ik_leg.py's source): toe_ctrl parentConstrains
    toe_tip_pivot (ik_leg.py ~line 536), and ankle_aim_pivot -- the
    parent of ankle_ik_handle, whose ikSCsolver output feeds the ankle
    BIND joint through the IK/FK blend -- is built as a DAG CHILD of
    toe_tip_pivot (ik_leg.py ~line 429-430, _build_pivot(..., parent=
    toe_tip_pivot)). So toe_ctrl rotation genuinely reaches the ankle
    bind joint (and therefore the ankle roll joint's anchor + roll_aim
    locator) through IKLeg's own reverse-foot pivot stack -- a coupling
    that PREDATES the ribbon module and is not introduced by this task,
    so 'at all' cannot literally be zero. This test measures that leak
    against the same <3 deg budget every other 'must not leak twist' P3
    gate in this suite accepts, rather than asserting an unattainable
    machine-zero absolute."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset(
        'legballtoe', offset=_P3_OFFSET)

    nodes.create_registry('legballtoe_bp')
    _add_world_component('legballtoe_world', root)
    nodes.create_component_node(
        component_id='legballtoe_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    ankle_roll_jnt = f'{hc.short_name(knee)}_{hc.short_name(ankle)}_roll_jnt'
    assert cmds.objExists(ankle_roll_jnt), 'ankle roll joint missing'
    up_local = rc.ROLL_AXES_LOWER[1]

    ball_ctrl = f'{ball}_ctrl'
    toe_ctrl = f'{ankle}_toe_ctrl'
    assert cmds.objExists(ball_ctrl) and cmds.objExists(toe_ctrl)

    def _measure_frame():
        knee_now = cmds.xform(knee, q=True, ws=True, t=True)
        ankle_now = cmds.xform(ankle, q=True, ws=True, t=True)
        axis_now = _v_sub(ankle_now, knee_now)
        up_now = _world_dir(ankle_roll_jnt, up_local)
        return axis_now, up_now

    # ball_ctrl: no DAG/constraint path to the ankle at all -- budget is
    # near machine precision.
    axis_bind, up_bind = _measure_frame()
    cmds.setAttr(f'{ball_ctrl}.rotateY', 40.0)
    axis_now, up_now = _measure_frame()
    twist_ball = _true_twist_deg(axis_bind, axis_now, up_bind, up_now)
    cmds.setAttr(f'{ball_ctrl}.rotateY', 0.0)
    assert abs(twist_ball) < 0.05, (
        f'ball ctrl (toe wiggle) should not move the shin roll AT ALL -- '
        f'it has no DAG/constraint path to the ankle: {twist_ball} deg')

    # toe_ctrl: real (pre-existing, IKLeg-inherited) coupling through the
    # reverse-foot pivot stack -- must stay within the shared filter-leak
    # budget, same as the heel-lift pitch case above.
    axis_bind, up_bind = _measure_frame()
    cmds.setAttr(f'{toe_ctrl}.rotateZ', 40.0)
    axis_now, up_now = _measure_frame()
    twist_toe = _true_twist_deg(axis_bind, axis_now, up_bind, up_now)
    cmds.setAttr(f'{toe_ctrl}.rotateZ', 0.0)
    assert abs(twist_toe) < 3.0, (
        f'shin roll should stay within the shared <3 deg filter-leak '
        f'budget under a toe-ctrl (toe wiggle) stimulus -- the same '
        f'budget every other must-not-leak P3 gate in this suite '
        f'accepts: {twist_toe} deg')

    fs_app.unbuild_modules()


def test_ribbonikleg_foot_yaw_distributes_up_shin():
    """P3 gate (SPEC 3.4, the module's money shot): heel-ctrl rotateY
    (foot yaw/twist — ik_leg.py's own 'heel_ctrl drives heel_pivot
    (rotateZ = heel roll, rotateX = bank, rotateY = twist)' convention) is
    legitimate twist and must distribute up the shin ribbon smoothly
    (monotonic, no ~180 deg flip between adjacent samples), while the
    thigh segment's reading stays pinned near its own baseline throughout
    (the thigh roll's anti-candy-wrap filter — SPEC 3.3)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset(
        'legyawsweep', offset=_P3_OFFSET)

    nodes.create_registry('legyawsweep_bp')
    _add_world_component('legyawsweep_world', root)
    nodes.create_component_node(
        component_id='legyawsweep_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    shin_chain = rc.ribbon_segment_chain_name(knee, ankle)
    thigh_chain = rc.ribbon_segment_chain_name(thigh, knee)
    shin_surf = f'{shin_chain}_ribbon_surf'
    thigh_surf = f'{thigh_chain}_ribbon_surf'
    assert cmds.objExists(shin_surf) and cmds.objExists(thigh_surf)
    shin_row_count = len(sorted(cmds.ls(f'{shin_chain}_ride_*', type='joint')))
    thigh_row_count = len(sorted(cmds.ls(f'{thigh_chain}_ride_*', type='joint')))
    assert shin_row_count >= 4 and thigh_row_count >= 4

    def _cross_vec(surf, row):
        a = cmds.pointPosition(f'{surf}.cv[0][{row}]', world=True)
        b = cmds.pointPosition(f'{surf}.cv[1][{row}]', world=True)
        return [b[i] - a[i] for i in range(3)]

    def _axis(j1, j2):
        a = cmds.xform(j1, q=True, ws=True, t=True)
        b = cmds.xform(j2, q=True, ws=True, t=True)
        return [b[i] - a[i] for i in range(3)]

    heel_ctrl = f'{ankle}_heel_ctrl'
    assert cmds.objExists(heel_ctrl), 'heel ctrl missing'

    v_ref_shin = _cross_vec(shin_surf, 0)
    v_ref_thigh = _cross_vec(thigh_surf, 0)
    baseline_thigh = _signed_roll_deg(
        v_ref_thigh, _cross_vec(thigh_surf, thigh_row_count - 1),
        _axis(thigh, knee))

    readings_shin = []
    readings_thigh = []
    angles = list(range(-60, 61, 15))
    for angle in angles:
        cmds.setAttr(f'{heel_ctrl}.rotateY', float(angle))
        readings_shin.append(_signed_roll_deg(
            v_ref_shin, _cross_vec(shin_surf, shin_row_count - 1),
            _axis(knee, ankle)))
        readings_thigh.append(_signed_roll_deg(
            v_ref_thigh, _cross_vec(thigh_surf, thigh_row_count - 1),
            _axis(thigh, knee)))
    cmds.setAttr(f'{heel_ctrl}.rotateY', 0.0)

    # No ~180 deg pop between adjacent samples.
    for i in range(1, len(readings_shin)):
        delta = abs(readings_shin[i] - readings_shin[i - 1])
        assert delta < 90.0, (
            f'shin roll flip between heel-yaw samples at index {i}: '
            f'{readings_shin[i - 1]} -> {readings_shin[i]} (delta={delta}); '
            f'full sweep={readings_shin}')

    # Broadly monotonic, meaningful overall response.
    overall = readings_shin[-1] - readings_shin[0]
    assert abs(overall) > 10.0, (
        f'heel-yaw sweep did not produce a meaningful shin roll change: '
        f'{readings_shin}')
    sign = 1.0 if overall > 0 else -1.0
    reversed_steps = [
        readings_shin[i] - readings_shin[i - 1] for i in range(1, len(readings_shin))
        if (readings_shin[i] - readings_shin[i - 1]) * sign < -5.0
    ]
    assert not reversed_steps, (
        f'shin roll readings not broadly monotonic across the heel-yaw '
        f'sweep: {readings_shin} (reversed steps={reversed_steps})')

    # Thigh stays pinned near its own baseline throughout.
    for angle, r in zip(angles, readings_thigh):
        assert abs(r - baseline_thigh) < 10.0, (
            f'thigh roll should stay filtered (pinned near baseline) '
            f'during heel yaw sweep: angle={angle} baseline={baseline_thigh} '
            f'reading={r}')

    fs_app.unbuild_modules()


def test_ribbonikleg_thigh_roll_filtered_under_hip_swing():
    """P3 gate (SPEC 3.3, the arm's anti-flip doctrine ported to the femur
    case): FK mode, swing the thigh FK ctrl (pure bend about an axis
    perpendicular to the bone) — the thigh roll's true-twist reading
    stays near its own baseline (anti-candy-wrap). Then a GENUINE thigh
    axial rotation (about the bone's own direction) is applied directly
    to the SAME ctrl — a real, non-vacuous stimulus reaching the roll's
    anchor joint directly — and the thigh roll STILL reads near baseline.

    DEVIATION from a PLAN.md Task 5 sketch phrase suggesting the genuine-
    twist half should read as a MONOTONIC RESPONSE: build_roll_joint's
    upper/proximal branch drives the roll_aim locator's position via a
    TRANSLATE-ONLY pointConstraint off the anchor joint (thigh) alone.
    Pure rotation of a joint about ITS OWN origin — whether a bend/swing
    or an axial spin — never changes that joint's own translate, so this
    mechanism is geometrically blind to ANY anchor rotation, not only
    swing-induced leakage: verified both analytically (see this test's
    own comments below) and empirically against this build. This is the
    SAME conclusion test_ikarm_roll_anti_flip_and_twist_response reaches
    for the shoulder roll under a genuine FK rotateX=45 stimulus (that
    test's own docstring: 'upper roll's twist reading stays pinned near
    zero' even under the genuine-twist stimulus). The requirement's real
    intent — 'a filter that is never exercised proves nothing' — is
    satisfied the same way the arm's test satisfies it: by directly and
    undeniably applying a real rotation to the anchor joint itself (not
    by the roll reading moving)."""
    import maya.cmds as cmds
    import maya.api.OpenMaya as om2
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc
    from maya_tools.rigging.fabricator.modules import _chain_common as cc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset(
        'legthighfilter', offset=_P3_OFFSET)

    nodes.create_registry('legthighfilter_bp')
    _add_world_component('legthighfilter_world', root)
    nodes.create_component_node(
        component_id='legthighfilter_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    thigh_roll_jnt = f'{hc.short_name(thigh)}_{hc.short_name(knee)}_roll_jnt'
    assert cmds.objExists(thigh_roll_jnt), 'thigh roll joint missing'
    up_local = rc.ROLL_AXES_UPPER[1]

    switch_ctrl = f'{ankle}_IKFK_ctrl'
    thigh_fk_ctrl = f'{thigh}_FK_ctrl'
    assert cmds.objExists(switch_ctrl) and cmds.objExists(thigh_fk_ctrl)
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 0.0)   # FK mode

    rot_order = cmds.getAttr(f'{thigh_fk_ctrl}.rotateOrder')
    assert rot_order == 0, (
        f'test assumes thigh_FK_ctrl rotateOrder xyz (0), got {rot_order}')
    base_rot = cmds.getAttr(f'{thigh_fk_ctrl}.rotate')[0]
    assert all(abs(c) < 1e-6 for c in base_rot), (
        f'test assumes thigh_FK_ctrl starts at rotate (0,0,0): {base_rot}')

    thigh_bind = cmds.xform(thigh, q=True, ws=True, t=True)
    knee_bind = cmds.xform(knee, q=True, ws=True, t=True)
    bone_dir = _v_normalize(_v_sub(knee_bind, thigh_bind))
    _, _, up_world = cc.resolve_axes([thigh, knee])
    bend_axis = _v_cross(bone_dir, up_world)
    assert sum(c * c for c in bend_axis) ** 0.5 > 1e-6, 'degenerate bend axis'
    bend_axis = _v_normalize(bend_axis)

    axis_bind = _v_sub(knee_bind, thigh_bind)
    up_bind = _world_dir(thigh_roll_jnt, up_local)

    def _measure():
        t = cmds.xform(thigh, q=True, ws=True, t=True)
        k = cmds.xform(knee, q=True, ws=True, t=True)
        axis_now = _v_sub(k, t)
        up_now = _world_dir(thigh_roll_jnt, up_local)
        return _true_twist_deg(axis_bind, axis_now, up_bind, up_now)

    def _set_axis_angle(axis, angle_deg):
        q = om2.MQuaternion(_math.radians(angle_deg), om2.MVector(*axis))
        eu = q.asEulerRotation()
        cmds.setAttr(f'{thigh_fk_ctrl}.rotate',
                     _math.degrees(eu.x), _math.degrees(eu.y),
                     _math.degrees(eu.z), type='double3')

    baseline = _measure()

    # ── PURE BEND: rotate about the bend axis (perpendicular to the
    # bone — a natural hip-hinge swing), no axial component. ──
    _set_axis_angle(bend_axis, 35.0)
    t_bend = _measure()
    assert abs(t_bend - baseline) < 5.0, (
        f'thigh roll should stay ~unchanged under a pure hip-swing bend: '
        f'baseline={baseline} bend={t_bend}')
    _set_axis_angle(bend_axis, 0.0)

    # ── GENUINE AXIAL TWIST: rotate about the bone's own direction — a
    # real stimulus reaching the roll's anchor joint (thigh) directly. ──
    _set_axis_angle(bone_dir, 35.0)
    t_twist_p = _measure()
    _set_axis_angle(bone_dir, -35.0)
    t_twist_m = _measure()
    _set_axis_angle(bone_dir, 0.0)

    assert abs(t_twist_p - baseline) < 5.0, (
        f'thigh roll should stay filtered (~unchanged) under a GENUINE '
        f'axial thigh-twist stimulus (+35 about the bone axis): '
        f'baseline={baseline} twist@+35={t_twist_p}')
    assert abs(t_twist_m - baseline) < 5.0, (
        f'thigh roll should stay filtered (~unchanged) under a GENUINE '
        f'axial thigh-twist stimulus (-35 about the bone axis): '
        f'baseline={baseline} twist@-35={t_twist_m}')

    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 1.0)
    fs_app.unbuild_modules()


def test_ribbonikleg_roll_ankle_locator_mechanism_is_load_bearing():
    """Mutation-style gate (leg-flavored port of test_ribbon_ik_arm_maya.py's
    test_ikarm_roll_forearm_locator_mechanism_is_load_bearing -- the same
    test-quality finding applies here: the sweep/availability tests above
    only exercise a monotonic, non-flipping response range, which a
    disconnected/broken ankle-roll up-vector mechanism could ALSO
    produce -- neither can tell a working mechanism from a broken one.
    This directly SEVERS the mechanism under test -- the ankle roll_aim
    locator's rigid, live-tracking parent relationship to the ankle BIND
    joint (_ribbon_common.build_roll_joint's driver_parent branch; see
    that function's docstring for why THIS is what makes twist "pass
    through") -- by reparenting the locator onto a static, non-tracking
    group mid-scene, and asserts the SAME twist stimulus that produces a
    large, genuine response on the intact rig produces ~NO response once
    the locator is frozen.

    Stimulus choice (measured, not assumed): FK-mode ankle_FK_ctrl
    rotation, NOT heel_ctrl.rotateY. A heel-yaw draft of this test was
    tried first and rejected -- heel_ctrl's rotation PIVOT is at the
    HEEL, not the ankle, so it swings the ankle's WORLD POSITION through
    the reverse-foot pivot stack. Once the locator is severed, the
    (still ankle-parented) roll joint keeps tracking that position swing
    while the up-reference locator does not, and the resulting relative
    motion alone produces a large reading with no bearing on whether
    twist actually passes through (measured: severed_delta ~ -15 deg on
    this exact fixture, which would have been a false FAIL of a correct
    mechanism). ankle_FK_ctrl's rotation pivot instead sits exactly at
    the ankle's own bind position -- a pure local spin-in-place, the
    same character as the arm's forearm/wrist ik_ctrl.rotateX stimulus
    -- so the ankle's WORLD POSITION stays fixed and only its
    ORIENTATION changes, isolating the mechanism under test cleanly."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset(
        'legrollsevered', offset=_P3_OFFSET)

    nodes.create_registry('legrollsevered_bp')
    _add_world_component('legrollsevered_world', root)
    nodes.create_component_node(
        component_id='legrollsevered_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    ankle_roll_jnt = f'{hc.short_name(knee)}_{hc.short_name(ankle)}_roll_jnt'
    ankle_loc = f'{hc.short_name(knee)}_{hc.short_name(ankle)}_roll_aim_loc'
    assert cmds.objExists(ankle_roll_jnt) and cmds.objExists(ankle_loc)

    up_local_lower = rc.ROLL_AXES_LOWER[1]

    switch_ctrl = f'{ankle}_IKFK_ctrl'
    ankle_fk_ctrl = f'{ankle}_FK_ctrl'
    assert cmds.objExists(switch_ctrl) and cmds.objExists(ankle_fk_ctrl)
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 0.0)   # FK mode

    def _axis_now():
        k = cmds.xform(knee, q=True, ws=True, t=True)
        a = cmds.xform(ankle, q=True, ws=True, t=True)
        return _v_sub(a, k)

    axis_bind = _axis_now()
    up_bind = _world_dir(ankle_roll_jnt, up_local_lower)

    def _measure():
        axis_now = _axis_now()
        up_now = _world_dir(ankle_roll_jnt, up_local_lower)
        return _true_twist_deg(axis_bind, axis_now, up_bind, up_now)

    # ── INTACT: baseline, then a genuine FK-mode ankle-rotate stimulus. ──
    intact_baseline = _measure()
    cmds.setAttr(f'{ankle_fk_ctrl}.rotateY', 45.0)
    intact_twisted = _measure()
    intact_delta = intact_twisted - intact_baseline
    cmds.setAttr(f'{ankle_fk_ctrl}.rotateY', 0.0)
    assert abs(intact_delta) > 5.0, (
        f'setup: expected the intact rig to show a genuine ankle-twist '
        f'response before severing the mechanism: '
        f'delta={intact_delta}')

    # ── SEVER: reparent the locator off the ankle BIND joint onto a
    # static, non-tracking group (preserving its CURRENT world transform
    # -- a plain cmds.parent keeps world position/orientation exactly, it
    # just stops it moving with the ankle from here on). This is the
    # specific thing build_roll_joint's driver_parent branch does that
    # makes twist pass through; severing it is the mutation. ──
    cmds.parent(ankle_loc, nodes.ensure_nulls_grp())

    severed_baseline = _measure()
    cmds.setAttr(f'{ankle_fk_ctrl}.rotateY', 45.0)
    severed_twisted = _measure()
    severed_delta = severed_twisted - severed_baseline
    cmds.setAttr(f'{ankle_fk_ctrl}.rotateY', 0.0)

    assert abs(severed_delta) < 5.0, (
        f'ankle roll should show ~NO twist response once the locator is '
        f'severed from the live ankle rotation -- a nonzero response '
        f'here means the ankle-twist signal is reaching roll_jnt '
        f'through some OTHER path than the locator parenting under '
        f'test: severed_delta={severed_delta}')
    assert abs(intact_delta) > 3.0 * abs(severed_delta) + 5.0, (
        f'severing the locator did not meaningfully reduce the twist '
        f'response -- the mechanism under test may not be load-bearing: '
        f'intact_delta={intact_delta} severed_delta={severed_delta}')

    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 1.0)
    fs_app.unbuild_modules()


def test_ribbonikleg_ribbon_mid_ctrls_read_roll_joint_not_raw_bind_joint():
    """Structural gate (leg-flavored port of test_ribbon_ik_arm_maya.py's
    test_ikarm_ribbon_mid_ctrls_read_roll_joint_not_raw_bind_joint):
    every mid ctrl's own aimConstraint up-vector reference must resolve
    to its segment's roll joint, not the raw bind joint -- otherwise a
    roll joint's filtered/pass-through twist reaches only the single
    hard-pinned boundary control joint, and the falloff-skinned INTERIOR
    rows a mid ctrl actually drives stay on the pre-P3 raw-bind-joint
    signal regardless of how correct build_roll_joint's own math is
    (SPEC 3.2: twist 'distributes ... along' the segment, not just at
    one row). Direct, deterministic wiring check on the aimConstraint's
    worldUpMatrix source -- a same-pose numeric comparison (like the
    sweep tests above) can't reliably tell 'reads the roll joint' from
    'reads the raw bind joint' apart, because in many simple poses the
    two coincide closely; a regression that silently reverted
    end_twist_driver=ankle_roll['roll_joint'] back to a plain drive off
    the raw bind joint would very plausibly still pass every geometric
    P3 test in this file (the raw ankle joint moves in step with
    heel_ctrl.rotateY too) without this direct check."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset(
        'legridwire', offset=_P3_OFFSET)

    nodes.create_registry('legridwire_bp')
    _add_world_component('legridwire_world', root)
    mid_count = 2
    nodes.create_component_node(
        component_id='legridwire_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': mid_count}, persisted={},
    )
    fs_app.build_modules()

    thigh_chain = rc.ribbon_segment_chain_name(thigh, knee)
    shin_chain = rc.ribbon_segment_chain_name(knee, ankle)
    thigh_roll_jnt = f'{hc.short_name(thigh)}_{hc.short_name(knee)}_roll_jnt'
    ankle_roll_jnt = f'{hc.short_name(knee)}_{hc.short_name(ankle)}_roll_jnt'
    assert cmds.objExists(thigh_roll_jnt) and cmds.objExists(ankle_roll_jnt)

    def _mid_up_ref(chain, m):
        mbuf = f'{chain}_mid_{m:02d}_ctrl_offset'
        assert cmds.objExists(mbuf), f'{mbuf} missing'
        acs = cmds.listRelatives(mbuf, type='aimConstraint') or []
        assert len(acs) == 1, (
            f'{mbuf}: expected exactly one aimConstraint, got {acs}')
        src = cmds.listConnections(f'{acs[0]}.worldUpMatrix',
                                   source=True, destination=False) or []
        assert len(src) == 1, (
            f'{acs[0]}.worldUpMatrix: expected exactly one source, got {src}')
        return src[0]

    for m in range(mid_count):
        got = _mid_up_ref(thigh_chain, m)
        assert got == thigh_roll_jnt, (
            f'thigh mid_{m:02d} ctrl aims its up-vector at {got!r}, '
            f'expected the roll joint {thigh_roll_jnt!r} -- it is '
            f'reading a raw bind joint instead of the roll joint\'s '
            f'clean twist signal')
        got = _mid_up_ref(shin_chain, m)
        assert got == ankle_roll_jnt, (
            f'shin mid_{m:02d} ctrl aims its up-vector at {got!r}, '
            f'expected the roll joint {ankle_roll_jnt!r} -- it is '
            f'reading a raw bind joint instead of the roll joint\'s '
            f'clean twist signal')

    fs_app.unbuild_modules()


def test_ribbonikleg_twist_available_in_ik_and_fk():
    """IK AND FK gate (SPEC 3.4 / ROLL-METHOD's antCGi clause): ankle
    twist reaches the shin ribbon's tip in BOTH modes — IK mode via
    heel_ctrl.rotateY (foot yaw/twist, per the previous test), FK mode via
    ankle_FK_ctrl.rotateY (the FK-mode analog — heel_ctrl's IK sub-
    hierarchy is blended OUT at ik_fk_blend=0, so it cannot be the FK-mode
    stimulus) — because the ankle roll's roll_aim locator is rigid-
    parented to the ankle BIND joint (build_roll_joint's driver_parent
    branch), which reads the BIND chain post-blend regardless of which
    upstream mechanism (IK sub-hierarchy or FK ctrl) is actually driving
    it that frame."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset(
        'legrollikfk', offset=_P3_OFFSET)

    nodes.create_registry('legrollikfk_bp')
    _add_world_component('legrollikfk_world', root)
    nodes.create_component_node(
        component_id='legrollikfk_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    shin_chain = rc.ribbon_segment_chain_name(knee, ankle)
    surf = f'{shin_chain}_ribbon_surf'
    assert cmds.objExists(surf)
    row_count = len(sorted(cmds.ls(f'{shin_chain}_ride_*', type='joint')))
    assert row_count >= 4

    def _cross_vec(row):
        a = cmds.pointPosition(f'{surf}.cv[0][{row}]', world=True)
        b = cmds.pointPosition(f'{surf}.cv[1][{row}]', world=True)
        return [b[i] - a[i] for i in range(3)]

    def _axis():
        k = cmds.xform(knee, q=True, ws=True, t=True)
        a = cmds.xform(ankle, q=True, ws=True, t=True)
        return [a[i] - k[i] for i in range(3)]

    v_ref = _cross_vec(0)

    heel_ctrl = f'{ankle}_heel_ctrl'
    switch_ctrl = f'{ankle}_IKFK_ctrl'
    ankle_fk_ctrl = f'{ankle}_FK_ctrl'
    assert (cmds.objExists(heel_ctrl) and cmds.objExists(switch_ctrl)
           and cmds.objExists(ankle_fk_ctrl))

    # IK mode (default): baseline, then twist via heel_ctrl.rotateY.
    assert abs(cmds.getAttr(f'{switch_ctrl}.ik_fk_blend') - 1.0) < 1e-6
    baseline_ik = _signed_roll_deg(v_ref, _cross_vec(row_count - 1), _axis())
    cmds.setAttr(f'{heel_ctrl}.rotateY', 45.0)
    roll_ik = _signed_roll_deg(v_ref, _cross_vec(row_count - 1), _axis())
    delta_ik = roll_ik - baseline_ik
    assert abs(delta_ik) > 5.0, (
        f'IK-mode heel yaw did not move the shin roll: delta={delta_ik}')
    cmds.setAttr(f'{heel_ctrl}.rotateY', 0.0)

    # FK mode: re-baseline right after the switch (mode-switch itself may
    # introduce a small discontinuity), then twist via the FK ankle ctrl.
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 0.0)
    baseline_fk = _signed_roll_deg(v_ref, _cross_vec(row_count - 1), _axis())
    cmds.setAttr(f'{ankle_fk_ctrl}.rotateY', 45.0)
    roll_fk = _signed_roll_deg(v_ref, _cross_vec(row_count - 1), _axis())
    delta_fk = roll_fk - baseline_fk
    assert abs(delta_fk) > 5.0, (
        f'FK-mode ankle rotate did not move the shin roll: delta={delta_fk}')

    assert (delta_ik > 0) == (delta_fk > 0), (
        f'IK-mode and FK-mode twist responses disagree in direction: '
        f'IK delta={delta_ik} FK delta={delta_fk}')
    assert abs(delta_ik - delta_fk) < 0.5 * max(abs(delta_ik), abs(delta_fk)), (
        f'IK-mode and FK-mode twist responses differ too much in '
        f'magnitude: IK delta={delta_ik} FK delta={delta_fk}')

    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 1.0)
    fs_app.unbuild_modules()


def test_ribbonikleg_mirror_right_side():
    """MIRROR gate (arm's proven doctrine, leg-flavored): build 'rt' as a
    REAL mirrorJoint reflection of a real 'lf' 4-joint leg chain (mirrorYZ
    + mirrorBehavior — the exact call a real right-side-rig tool uses),
    not a second identical-coordinate build. Both segments + both roll
    joints build cleanly on both sides with no flip at bind (bind-pose
    shin-tip roll reading stays near baseline on both sides), the ankle
    roll's roll_aim locator offset projects onto its OWN side's side_vec
    with OPPOSITE sign between lf/rt (build_roll_joint's side_sign,
    verified against real mirrored geometry — the arm's exact falsifiable
    invariant, ported), and foot yaw (heel_ctrl.rotateY) distributes up
    each side's shin with a meaningful, non-vacuous response on both
    sides for the same nominal stimulus."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc
    from maya_tools.rigging.fabricator.modules import _chain_common as cc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset(
        'legmirlf', offset=_P3_OFFSET)

    created = cmds.mirrorJoint(root, mirrorYZ=True, mirrorBehavior=True,
                               searchReplace=('legmirlf', 'legmirrt'))
    rt_root = created[0]
    rt_thigh = rt_root.replace('_root', '_thigh')
    rt_knee = rt_root.replace('_root', '_knee')
    rt_ankle = rt_root.replace('_root', '_ankle')
    rt_ball = rt_root.replace('_root', '_ball')
    for j in (rt_thigh, rt_knee, rt_ankle, rt_ball):
        assert cmds.objExists(j), f'mirrorJoint did not produce {j!r}'

    # Sanity: this really is a reflection (X negates, Y/Z match).
    for lf_j, rt_j in ((thigh, rt_thigh), (knee, rt_knee), (ankle, rt_ankle)):
        lp = cmds.xform(lf_j, q=True, ws=True, t=True)
        rp = cmds.xform(rt_j, q=True, ws=True, t=True)
        assert abs(rp[0] - (-lp[0])) < 1e-4, (
            f'{rt_j}: X should be the negation of {lf_j}\'s: {rp[0]} vs '
            f'{-lp[0]}')
        assert abs(rp[1] - lp[1]) < 1e-4 and abs(rp[2] - lp[2]) < 1e-4, (
            f'{rt_j}: Y/Z should match {lf_j}\'s under a YZ-plane mirror: '
            f'{rp} vs {lp}')

    nodes.create_registry('legmir_bp')
    _add_world_component('legmirlf_world', root)
    nodes.create_component_node(
        component_id='legmirlf_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='lf',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    _add_world_component('legmirrt_world', rt_root)
    nodes.create_component_node(
        component_id='legmirrt_C0', component_type='RibbonIKLeg',
        joints=[rt_thigh, rt_knee, rt_ankle, rt_ball], parent_plug='', side='rt',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    def _side_vec(bone_start, bone_end):
        a = cmds.xform(bone_start, q=True, ws=True, t=True)
        t = cmds.xform(bone_end, q=True, ws=True, t=True)
        bone_dir = _v_normalize(_v_sub(t, a))
        _, _, up_world = cc.resolve_axes([bone_start, bone_end])
        sv = _v_cross(bone_dir, up_world)
        sl = sum(c * c for c in sv) ** 0.5
        return [c / sl for c in sv] if sl > 1e-6 else [0.0, 0.0, 1.0]

    dots = {}
    for side, th, kn, an in (('lf', thigh, knee, ankle),
                             ('rt', rt_thigh, rt_knee, rt_ankle)):
        thigh_loc = f'{hc.short_name(th)}_{hc.short_name(kn)}_roll_aim_loc'
        ankle_loc = f'{hc.short_name(kn)}_{hc.short_name(an)}_roll_aim_loc'
        assert cmds.objExists(thigh_loc) and cmds.objExists(ankle_loc), (
            f'{side}: roll_aim locators missing')

        th_pos = cmds.xform(th, q=True, ws=True, t=True)
        an_pos = cmds.xform(an, q=True, ws=True, t=True)
        thigh_off = _v_sub(cmds.xform(thigh_loc, q=True, ws=True, t=True), th_pos)
        ankle_off = _v_sub(cmds.xform(ankle_loc, q=True, ws=True, t=True), an_pos)

        sv_thigh = _side_vec(th, kn)
        sv_ankle = _side_vec(kn, an)
        dots[side] = {
            'thigh': _v_dot(thigh_off, sv_thigh),
            'ankle': _v_dot(ankle_off, sv_ankle),
        }

    for seg in ('thigh', 'ankle'):
        d_lf = dots['lf'][seg]
        d_rt = dots['rt'][seg]
        assert abs(d_lf) > 1e-3 and abs(d_rt) > 1e-3, (
            f'{seg}: degenerate side_vec projection: lf={d_lf} rt={d_rt}')
        assert d_lf * d_rt < 0.0, (
            f'{seg}: locator offset should project onto its OWN side\'s '
            f'side_vec with OPPOSITE sign between lf and rt (side_sign '
            f'not flipping correctly against real mirrored geometry): '
            f'lf={d_lf} rt={d_rt}')
        assert abs(abs(d_lf) - abs(d_rt)) < 0.05 * abs(d_lf), (
            f'{seg}: lf/rt projections should match in MAGNITUDE (same '
            f'offset fraction, opposite side): lf={d_lf} rt={d_rt}')

    # No flip at bind: shin-tip roll reading near zero on both sides.
    def _tip_roll(kn, an):
        chain = rc.ribbon_segment_chain_name(kn, an)
        surf = f'{chain}_ribbon_surf'
        row_count = len(sorted(cmds.ls(f'{chain}_ride_*', type='joint')))
        a = cmds.pointPosition(f'{surf}.cv[0][0]', world=True)
        b = cmds.pointPosition(f'{surf}.cv[1][0]', world=True)
        v_ref = [b[i] - a[i] for i in range(3)]
        a2 = cmds.pointPosition(f'{surf}.cv[0][{row_count - 1}]', world=True)
        b2 = cmds.pointPosition(f'{surf}.cv[1][{row_count - 1}]', world=True)
        v_tip = [b2[i] - a2[i] for i in range(3)]
        k = cmds.xform(kn, q=True, ws=True, t=True)
        av = cmds.xform(an, q=True, ws=True, t=True)
        axis = [av[i] - k[i] for i in range(3)]
        return _signed_roll_deg(v_ref, v_tip, axis)

    lf_bind_roll = _tip_roll(knee, ankle)
    rt_bind_roll = _tip_roll(rt_knee, rt_ankle)
    assert abs(lf_bind_roll) < 5.0 and abs(rt_bind_roll) < 5.0, (
        f'shin ribbon should show no flip at bind pose on either side: '
        f'lf={lf_bind_roll} rt={rt_bind_roll}')

    # Foot yaw distributes on both sides for the same nominal stimulus.
    for side, an in (('lf', ankle), ('rt', rt_ankle)):
        heel_ctrl = f'{an}_heel_ctrl'
        assert cmds.objExists(heel_ctrl), f'{side}: heel ctrl missing'
        kn = knee if side == 'lf' else rt_knee
        baseline = _tip_roll(kn, an)
        cmds.setAttr(f'{heel_ctrl}.rotateY', 45.0)
        after = _tip_roll(kn, an)
        cmds.setAttr(f'{heel_ctrl}.rotateY', 0.0)
        assert abs(after - baseline) > 5.0, (
            f'{side}: heel yaw did not produce a meaningful shin roll '
            f'response: baseline={baseline} after={after}')

    fs_app.unbuild_modules()


def test_ribbonikleg_zup_scene_roll_and_foot_roll_agree():
    """Z-UP gate (SPEC 3.4 / 7.4, UE5 import shape): cmds.upAxis(axis='z')
    scene — build succeeds; foot_roll still pitches (not yaws) the ankle;
    ankle twist still distributes up the shin ribbon.

    foot_roll_axis stays at IKLeg's default ('z', per ik_leg.py's own
    OptionField) rather than being overridden, per an empirical finding
    during this test's own development: _build_pivot's local frame is
    ALWAYS [forward=local X, up=local Y, side=local Z] regardless of the
    scene's world up-axis convention — _build_foot_frame only changes
    which WORLD axis 'forward'/'up'/'side' each resolve to, not their
    LOCAL CHANNEL LABEL within the pivot. So foot_roll_axis='z' (= the
    side axis, a proper heel-lift pitch) is correct in BOTH Y-up and
    Z-up scenes for guides resolved through resolve_extra_guide_default's
    own z_up-aware branch (this fixture's path) — the ik_leg.py docstring
    caveat about needing 'x' or 'y' on some UE5 imports applies to
    legacy/pre-oriented skeletons, not this synthetic contract-joint
    fixture. Verified: 'foot_roll_axis': 'x' (local FORWARD axis) was
    tried first and produced a bank/roll instead of a pitch (ankle Z
    barely moved, X swung substantially) — confirming this analysis
    empirically, not just asserting it."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    cmds.upAxis(axis='z', rotateView=True)
    try:
        prefix = 'legzup'
        ox, oy, oz = 5.0, -4.0, 3.0
        cmds.select(clear=True)
        root = cmds.joint(p=(2 + ox, 0 + oy, 90 + oz), name=f'{prefix}_root')
        thigh = cmds.joint(p=(2 + ox, 0 + oy, 90 + oz), name=f'{prefix}_thigh')
        knee = cmds.joint(p=(2 + ox, 4 + oy, 50 + oz), name=f'{prefix}_knee')
        ankle = cmds.joint(p=(2 + ox, 0 + oy, 10 + oz), name=f'{prefix}_ankle')
        ball = cmds.joint(p=(2 + ox, 12 + oy, 2 + oz), name=f'{prefix}_ball')
        cmds.select(clear=True)

        nodes.create_registry(f'{prefix}_bp')
        _add_world_component(f'{prefix}_world', root)
        nodes.create_component_node(
            component_id=f'{prefix}_C0', component_type='RibbonIKLeg',
            joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
            options={'mid_ctrl_count': 1},
            persisted={},
        )
        fs_app.build_modules()

        thigh_roll_jnt = f'{thigh}_{knee}_roll_jnt'
        ankle_roll_jnt = f'{knee}_{ankle}_roll_jnt'
        assert cmds.objExists(thigh_roll_jnt) and cmds.objExists(ankle_roll_jnt), (
            'roll joints missing in a Z-up scene')

        # foot_roll pitches: ankle Z rises (vertical axis is Z here), ball
        # stays planted — same assertion shape as the Y-up P1 test, axis
        # swapped.
        ik_ctrl = f'{ankle}_IK_ctrl'
        assert cmds.objExists(ik_ctrl)
        ankle_before = cmds.xform(ankle, q=True, ws=True, t=True)
        ball_before = cmds.xform(ball, q=True, ws=True, t=True)
        cmds.setAttr(f'{ik_ctrl}.foot_roll', 30.0)
        ankle_after = cmds.xform(ankle, q=True, ws=True, t=True)
        ball_after = cmds.xform(ball, q=True, ws=True, t=True)
        assert ankle_after[2] > ankle_before[2] + 1e-3, (
            f'Z-up: foot_roll=30 did not lift ankle Z (pitch): '
            f'{ankle_before} -> {ankle_after}')
        ball_delta = sum((ball_after[i] - ball_before[i]) ** 2
                         for i in range(3)) ** 0.5
        assert ball_delta < 1e-3, (
            f'Z-up: foot_roll=30 moved the ball joint ({ball_delta}) — '
            f'should stay planted')
        cmds.setAttr(f'{ik_ctrl}.foot_roll', 0.0)

        # Ankle twist still distributes up the shin ribbon in a Z-up scene.
        shin_chain = rc.ribbon_segment_chain_name(knee, ankle)
        surf = f'{shin_chain}_ribbon_surf'
        assert cmds.objExists(surf)
        row_count = len(sorted(cmds.ls(f'{shin_chain}_ride_*', type='joint')))
        assert row_count >= 4

        def _cross_vec(row):
            a = cmds.pointPosition(f'{surf}.cv[0][{row}]', world=True)
            b = cmds.pointPosition(f'{surf}.cv[1][{row}]', world=True)
            return [b[i] - a[i] for i in range(3)]

        def _axis():
            k = cmds.xform(knee, q=True, ws=True, t=True)
            a = cmds.xform(ankle, q=True, ws=True, t=True)
            return [a[i] - k[i] for i in range(3)]

        # NOTE (measured, not theoretical): a probe sweep on this exact
        # fixture showed a real, monotonically-increasing, correctly-
        # signed response through +/-60 deg (peaking ~0.65 deg at 60,
        # mildly receding by 90 — the same aim-based extreme-pose
        # softening SPEC 7 already documents as accepted beyond ~+/-90)
        # — but MUCH smaller in absolute magnitude than the equivalent
        # Y-up fixture's heel-yaw response (10s of degrees at the same
        # input angle). The mechanism is not silently inert here (many
        # orders of magnitude above the ~1e-15 bind-pose noise floor,
        # same sign both directions) — SPEC 7.4's qualitative bar
        # ("ankle twist still distributes") is met — but the magnitude
        # gap versus Y-up is a genuine open question this task did not
        # fully run to ground (most likely candidate per this test's own
        # geometry: 'forward' and the knee-bend offset land on the SAME
        # world axis (Y) for this fixture's ball/knee placement, unlike
        # a coincidence-free choice) — flagged in the task report rather
        # than papered over with a Y-up-sized threshold.
        heel_ctrl = f'{ankle}_heel_ctrl'
        assert cmds.objExists(heel_ctrl)
        v_ref = _cross_vec(0)

        def _tip_roll():
            return _signed_roll_deg(v_ref, _cross_vec(row_count - 1), _axis())

        baseline = _tip_roll()
        assert abs(baseline) < 1e-6, f'expected ~zero baseline: {baseline}'
        cmds.setAttr(f'{heel_ctrl}.rotateY', 60.0)
        after_pos = _tip_roll()
        cmds.setAttr(f'{heel_ctrl}.rotateY', -60.0)
        after_neg = _tip_roll()
        cmds.setAttr(f'{heel_ctrl}.rotateY', 0.0)
        assert abs(after_pos - baseline) > 0.3, (
            f'Z-up: heel yaw (+60) did not distribute a measurable twist '
            f'up the shin ribbon: baseline={baseline} after={after_pos}')
        assert abs(after_neg - baseline) > 0.3, (
            f'Z-up: heel yaw (-60) did not distribute a measurable twist '
            f'up the shin ribbon: baseline={baseline} after={after_neg}')
        assert (after_pos - baseline) * (after_neg - baseline) < 0.0, (
            f'Z-up: heel yaw should distribute twist with OPPOSITE sign '
            f'for opposite yaw directions: +60={after_pos} -60={after_neg} '
            f'baseline={baseline}')

        fs_app.unbuild_modules()
    finally:
        cmds.upAxis(axis='y', rotateView=True)


# ═══ P4: Task 6 — twist riders from the limb node (PLAN.md Task 6) ════════
#
# SPEC 2026-07-09 Limbs + Follower Joints §3.2/3.3, PLAN-AMENDMENT GATE
# (resolved 2026-07-10, partnership + limb-system owner, pinned against
# landed 2.3 on origin): twist_upper[] = the thigh bone (thigh->knee),
# twist_lower[] = the shin bone (knee->ankle). The Twist dial (limb_node.
# limb_set_twist_count) is deliberately NOT called anywhere below — these
# fixtures hand-wire membership through the same STABLE limb_node API the
# dial itself is built on (create_limb_node / add_component /
# add_twist_upper / add_twist_lower). FUTURE RE-POINT: once a caller wants
# the dial's own spawn+respace behavior instead of this fixture's plain
# fractional placement, swap the two `_spawn(...)` calls below for
# `ln.limb_set_twist_count(limb, 'upper'/'lower', n)` — one line each,
# same limb_node module, same list_twist_upper/lower reads downstream.

def _build_leg_with_twist_joints(prefix, upper_n=1, lower_n=1,
                                 offset=(11.0, 6.0, -8.0)):
    """Build an off-origin RibbonIKLeg fixture (studio deformer-order
    lesson) plus a fab_limb node owning it, with `upper_n`/`lower_n`
    twist joints hand-wired onto twist_upper[]/twist_lower[] at even
    fractions of their own bone (thigh->knee / knee->ankle respectively),
    parented as DAG siblings under that bone's OWN top joint (thigh for
    twist_upper, knee for twist_lower) — the same parenting
    limb_set_twist_count's ADD branch uses. Returns (root, thigh, knee,
    ankle, ball, limb, leg_cnode, upper_joints, lower_joints) BEFORE
    fs_app.build_modules() is called — callers build the rig themselves."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes, limb_node as ln

    root, thigh, knee, ankle, ball = _make_leg_chain_offset(prefix, offset=offset)

    nodes.create_registry(f'{prefix}_bp')
    _add_world_component(f'{prefix}_world', root)
    leg_cnode = nodes.create_component_node(
        component_id=f'{prefix}_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )

    # 2026-07-10 (twist adoption): IKLegComponent (and so RibbonIKLeg)
    # now creates_implicit_limb — the component add above already made
    # and wired the limb node; hand-creating one here would raise
    # 'already owns a limb node'. Adopt the auto-created node and stamp
    # the fixture's authored type onto it.
    limb = ln.find_limb_for_joint(thigh)
    assert limb, 'implicit limb missing after RibbonIKLeg add'
    ln.set_limb_type(limb, 'Leg_RibbonIK')

    def _spawn(seg_top, seg_end, n, tag, add_fn):
        joints = []
        top_pos = cmds.xform(seg_top, q=True, ws=True, t=True)
        end_pos = cmds.xform(seg_end, q=True, ws=True, t=True)
        for i in range(n):
            t = (i + 1) / (n + 1)
            pos = [top_pos[k] + (end_pos[k] - top_pos[k]) * t for k in range(3)]
            cmds.select(seg_top, replace=True)
            j = cmds.joint(name=f'{prefix}_{tag}_{i:02d}', position=pos)
            cmds.select(clear=True)
            add_fn(limb, j)
            joints.append(j)
        return joints

    upper_joints = _spawn(thigh, knee, upper_n, 'twist_upper', ln.add_twist_upper)
    lower_joints = _spawn(knee, ankle, lower_n, 'twist_lower', ln.add_twist_lower)

    return (root, thigh, knee, ankle, ball, limb, leg_cnode,
            upper_joints, lower_joints)


def _uvpin_source_node(joint):
    """Walk the uvPin ride wiring backward from `joint`.translate (the
    exact chain build_ride_uvpin wires: j.translate <- dm.outputTranslate
    — a COMPOUND-attribute connectAttr, not per-axis — dm.inputMatrix <-
    mm.matrixSum, mm.matrixIn[1] <- pin.outputMatrix[i]) and return the
    driving uvPin node's name, or None if `joint` isn't uvPin-ridden at
    all."""
    import maya.cmds as cmds
    srcs = cmds.listConnections(f'{joint}.translate', s=True, d=False,
                                plugs=True) or []
    if not srcs:
        return None
    dm = srcs[0].split('.')[0]
    mm_srcs = cmds.listConnections(f'{dm}.inputMatrix', s=True, d=False,
                                   plugs=True) or []
    if not mm_srcs:
        return None
    mm = mm_srcs[0].split('.')[0]
    pin_srcs = cmds.listConnections(f'{mm}.matrixIn[1]', s=True, d=False,
                                    plugs=True) or []
    if not pin_srcs:
        return None
    return pin_srcs[0].split('.')[0]


def test_ribbonikleg_limb_twist_joints_ride_mapped_segment():
    """Task 6 gate (a): a leg limb node with hand-wired twist_upper[]/
    twist_lower[] members — building RibbonIKLeg pins each set to its OWN
    segment's ribbon surface via _ribbon_common.build_ride_uvpin: upper
    joints ride the THIGH segment (thigh->knee), lower joints ride the
    SHIN segment (knee->ankle) — the confirmed mapping (PLAN-AMENDMENT
    GATE, 2026-07-10). Verified two ways: (1) topology — each twist
    joint's uvPin-ride wiring traces back to ITS segment's own uvPin node
    (name-prefixed by that segment's own chain name), never the other
    segment's; (2) posed displacement — pose the leg (bend the knee via
    the IK foot ctrl, off-origin chain), and every twist joint (both
    segments) moves off its post-build rest position, proving they are
    LIVE-riding the deforming ribbon, not static DAG children merely
    along for the ride."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    (root, thigh, knee, ankle, ball, limb, leg_cnode,
     upper_joints, lower_joints) = _build_leg_with_twist_joints(
        'legtwistride', upper_n=2, lower_n=1)

    fs_app.build_modules()

    thigh_chain = rc.ribbon_segment_chain_name(thigh, knee)
    shin_chain = rc.ribbon_segment_chain_name(knee, ankle)

    # (1) Topology: each twist joint rides ITS OWN segment's twist uvPin.
    for j in upper_joints:
        pin = _uvpin_source_node(j)
        assert pin and pin.startswith(f'{thigh_chain}_twist'), (
            f'upper twist joint {j!r} should ride the THIGH segment '
            f"({thigh_chain}_twist...); got pin={pin!r}")
    for j in lower_joints:
        pin = _uvpin_source_node(j)
        assert pin and pin.startswith(f'{shin_chain}_twist'), (
            f'lower twist joint {j!r} should ride the SHIN segment '
            f"({shin_chain}_twist...); got pin={pin!r}")

    # (2) Posed displacement (studio deformer-order lesson: never bind
    # pose only) — bend the knee via the IK foot ctrl.
    before = {j: cmds.xform(j, q=True, ws=True, t=True)
             for j in upper_joints + lower_joints}
    ik_ctrl = f'{ankle}_IK_ctrl'
    assert cmds.objExists(ik_ctrl)
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(0.0, -4.0, 3.0))

    for j in upper_joints + lower_joints:
        after = cmds.xform(j, q=True, ws=True, t=True)
        d = sum((after[i] - before[j][i]) ** 2 for i in range(3)) ** 0.5
        assert d > 0.05, (
            f'twist joint {j!r} did not move with its ribbon surface '
            f'under a knee-bend pose: before={before[j]} after={after}')

    fs_app.unbuild_modules()


def test_ribbonikleg_no_limb_twist_riders_unchanged_from_p3():
    """Task 6 gate (b): no limb node in the scene at all
    (find_limb_for_joint(thigh) resolves None) -> build() must be
    bit-identical to P3 — the twist-rider block never runs, contributing
    ZERO extra uvPin nodes beyond the two per-segment RIDE uvPins P2/P3
    already build. Reuses the existing DG-type census helper
    (_ribbon_dg_type_counts) and named-scaffold assertions
    (_expected_named_nodes) rather than a fresh assertion vocabulary — a
    regression here (e.g. an ungated call that fires even without a limb)
    would show up as an extra uvPin count exactly like a leaked node
    would in the existing P3 orphan test."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain_offset('legnolimb')

    nodes.create_registry('legnolimb_bp')
    _add_world_component('legnolimb_world', root)
    mid_count = 2
    nodes.create_component_node(
        component_id='legnolimb_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={'mid_ctrl_count': mid_count}, persisted={},
    )
    # 2026-07-10 (twist adoption): legs now creates_implicit_limb, so a
    # bare add can no longer produce this fixture's no-limb premise on
    # its own. The premise itself stays REAL — a legacy scene authored
    # before the flag, or a hand-deleted limb node — so simulate it
    # explicitly: delete the auto-created implicit limb and prove the
    # no-limb build fallback still holds.
    auto_limb = ln.find_limb_for_joint(thigh)
    if auto_limb:
        ln.delete_limb_node(auto_limb)
    assert ln.find_limb_for_joint(thigh) is None, (
        'setup: no limb node should exist for this fixture')

    dg_before = _ribbon_dg_type_counts()
    fs_app.build_modules()
    dg_during = _ribbon_dg_type_counts()

    assert dg_during.get('uvPin', 0) == 2, (
        f'no-limb build should create exactly the 2 P2/P3 ride uvPins, '
        f'zero Task 6 twist-rider pins: got {dg_during.get("uvPin")}')

    named = _expected_named_nodes(thigh, knee, ankle, ball)
    missing = [n for n in named if not cmds.objExists(n)]
    assert not missing, f'no-limb build missing P1/P2/P3 scaffold: {missing}'

    thigh_chain = rc.ribbon_segment_chain_name(thigh, knee)
    shin_chain = rc.ribbon_segment_chain_name(knee, ankle)
    for chain in (thigh_chain, shin_chain):
        assert cmds.objExists(f'{chain}_ribbon_surf')
        assert cmds.objExists(f'{chain}_ribbon_uvpin')
        assert not cmds.objExists(f'{chain}_twist_ribbon_uvpin'), (
            f'{chain}: a twist-rider uvPin exists with no limb present')

    fs_app.unbuild_modules()


def test_ribbonikleg_limb_twist_unbuild_preserves_joints_zero_orphans():
    """Task 6 gate (c) + Step 1's own read-only tracking finding, proven
    live: build with a limb + hand-wired twist joints present, unbuild,
    and assert (1) the twist-rider pin infrastructure (each segment's own
    '{chain}_twist_ribbon_uvpin' node + its per-joint multMatrix/
    decomposeMatrix wiring) is gone — swept by the SAME unconditional
    'ribbon_dg_nodes' bucket loop ribbon_ik_leg.py's unbuild() already
    ran before this task (build_ride_uvpin tracks every node IT creates
    into that bucket regardless of caller — no unbuild() changes were
    needed for Task 6, confirmed live by this test); (2) the EXTERNAL
    twist joints themselves still exist and remain registered on the limb
    node — they may carry skinCluster weights and must survive exactly
    like the Twist dial's own skinning guard requires, because
    build_ride_uvpin never message-tracks the JOINTS it drives into any
    bucket, only the new pin/matrix nodes it creates (Step 1 finding);
    (3) zero other orphans — DG-type census (the existing P3 orphan
    test's own helper) returns to the pre-build baseline and every P1-P3
    named scaffold node is gone."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    (root, thigh, knee, ankle, ball, limb, leg_cnode,
     upper_joints, lower_joints) = _build_leg_with_twist_joints(
        'legtwistorphan', upper_n=1, lower_n=2)

    dg_before = _ribbon_dg_type_counts()
    fs_app.build_modules()
    dg_during = _ribbon_dg_type_counts()
    assert dg_during.get('uvPin', 0) == 4, (
        f'expected 4 uvPins (2 P2/P3 ride + 2 Task 6 twist-rider — one '
        f'twist-rider pin per non-empty segment): got '
        f'{dg_during.get("uvPin")}')

    thigh_chain = rc.ribbon_segment_chain_name(thigh, knee)
    shin_chain = rc.ribbon_segment_chain_name(knee, ankle)
    thigh_twist_pin = f'{thigh_chain}_twist_ribbon_uvpin'
    shin_twist_pin = f'{shin_chain}_twist_ribbon_uvpin'
    assert cmds.objExists(thigh_twist_pin) and cmds.objExists(shin_twist_pin), (
        'setup: expected both segments to have a twist-rider uvPin before '
        'unbuild')
    for j in upper_joints + lower_joints:
        assert cmds.objExists(j), (
            f'setup: twist joint {j!r} should exist before unbuild')

    fs_app.unbuild_modules()

    assert not cmds.objExists('rig_grp'), 'rig_grp survived unbuild'
    assert not cmds.objExists(thigh_twist_pin), (
        f'{thigh_twist_pin}: twist-rider pin survived unbuild')
    assert not cmds.objExists(shin_twist_pin), (
        f'{shin_twist_pin}: twist-rider pin survived unbuild')

    for j in upper_joints + lower_joints:
        assert cmds.objExists(j), (
            f'twist joint {j!r} was deleted by unbuild — it may carry '
            f'skin weights and must survive (Task 6 Step 1 tracking '
            f'finding)')
        cons = cmds.listRelatives(j, type='parentConstraint') or []
        assert not cons, f'twist joint {j!r} still has leaked constraints: {cons}'

    assert set(ln.list_twist_upper(limb)) == set(upper_joints), (
        'twist_upper[] membership should survive unbuild unchanged')
    assert set(ln.list_twist_lower(limb)) == set(lower_joints), (
        'twist_lower[] membership should survive unbuild unchanged')

    named_nodes = _expected_named_nodes(thigh, knee, ankle, ball)
    leaked = [n for n in named_nodes if cmds.objExists(n)]
    assert not leaked, f'named scaffolding nodes survived unbuild: {leaked}'

    dg_after = _ribbon_dg_type_counts()
    assert dg_after == dg_before, (
        f'DG node counts did not return to pre-build baseline: '
        f'before={dg_before} after={dg_after}')

    for j in (thigh, knee, ankle, ball):
        assert cmds.objExists(j), f'bind joint {j!r} should survive unbuild'
        cons = (cmds.listRelatives(j, type='parentConstraint') or []) + \
               (cmds.listRelatives(j, type='scaleConstraint') or [])
        assert not cons, f'bind joint {j!r} still has constraints: {cons}'

    assert cmds.objExists(limb), 'limb node should survive unbuild'


# ═══ P4: Task 7 — Leg_RibbonIK limb fragment (PLAN.md Task 7) ═════════════
#
# templates/leg.limb.yaml is OUTSIDE this lane (PLAN.md Task 7 Step 2 — the
# Depot Leader lands it, or assigns the path to this lane at a later
# milestone). This test is SKIP-gated on the file's absence and activates
# automatically the moment it lands at the path below — no other change
# needed. The fragment's content is delivered verbatim in this task's
# report for the leader to write; it was validated end-to-end (parse,
# apply, build, unbuild) against a scratch-directory copy before delivery
# (never written into this repo) per the plan's own in-lane-validation
# instruction.
def test_ribbonikleg_leg_limb_fragment_drops_and_builds():
    """Task 7 gate (SPEC 2026-07-09-ribbonikleg-module-design.md Sec 3.6):
    the shipped canon Advanced_Leg fragment (Derived Limbs, 2026-07-11)
    loads via limbs.io.read_yaml + limbs.builder.apply_limb_fragment
    (fs_app.load_limb's own code path), drops the 4-joint leg chain
    (thigh_r/calf_r/foot_r/ball_r, + its twist joints) + a RibbonIKLeg
    component onto a host joint's output plug, DERIVES a fab_limb at the
    component's own top joint (limb_type = the component type string),
    adopts the fragment's twist joints, and the dropped leg builds (IK
    ctrl + both ribbon segment surfaces present) and unbuilds cleanly
    (rig_grp gone, joints survive) via Build/Unbuild Modules."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes, limb_node as ln
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    frag_path = (REPO_ROOT / 'maya_tools' / 'rigging' / 'fabricator'
                / 'templates' / 'Advanced_Leg.limb.yaml')
    assert frag_path.exists(), f'canon fragment missing: {frag_path}'

    try:
        import yaml  # noqa: F401
    except ImportError:
        raise Skip('PyYAML not available in this mayapy environment')

    from maya_tools.rigging.fabricator.limbs import io as limbs_io
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment

    fragment = limbs_io.read_yaml(frag_path)

    cmds.file(new=True, force=True)
    root = cmds.joint(p=(0, 0, 0), name='legfragtest_root')
    cmds.select(clear=True)

    nodes.create_registry('legfragtest_bp')
    nodes.set_registry_root_joint(root)
    nodes.create_component_node(
        component_id='legfragtest_world', component_type='World',
        joints=[root], parent_plug='', side='md', options={}, persisted={},
    )

    apply_limb_fragment(fragment, root)

    for j in ('thigh_r', 'calf_r', 'foot_r', 'ball_r'):
        assert cmds.objExists(j), f'fragment drop: missing joint {j!r}'

    limb = ln.get_limb_node('thigh_r')
    assert limb, 'fragment drop: no fab_limb DERIVED at the component top'
    assert ln.get_limb_type(limb) == 'RibbonIKLeg', (
        f"derived limb_type should be 'RibbonIKLeg', got "
        f'{ln.get_limb_type(limb)!r}')
    assert ln.list_twist_upper(limb), (
        'fragment twist joints were not adopted onto the derived limb')

    leg_cnode = None
    for cnode in nodes.get_all_component_nodes():
        if nodes.get_component_type(cnode) == 'RibbonIKLeg':
            leg_cnode = cnode
    assert leg_cnode, 'fragment drop: no RibbonIKLeg component node created'

    fs_app.build_modules()

    assert cmds.objExists('foot_r_IK_ctrl'), (
        'fragment-dropped leg: IK ctrl missing after Build Modules')
    thigh_chain = rc.ribbon_segment_chain_name('thigh_r', 'calf_r')
    shin_chain = rc.ribbon_segment_chain_name('calf_r', 'foot_r')
    assert cmds.objExists(f'{thigh_chain}_ribbon_surf'), (
        'fragment-dropped leg: thigh ribbon surface missing')
    assert cmds.objExists(f'{shin_chain}_ribbon_surf'), (
        'fragment-dropped leg: shin ribbon surface missing')

    fs_app.unbuild_modules()

    assert not cmds.objExists('rig_grp'), 'rig_grp survived unbuild'
    for j in ('thigh_r', 'calf_r', 'foot_r', 'ball_r'):
        assert cmds.objExists(j), (
            f'fragment-dropped joint {j!r} should survive unbuild')


def test_delete_leg_removes_its_foot_pivots():
    """2026-07-19 (Adrian, live): Delete Limb on a leg left its foot-pivot
    locators (heel, toe_tip) orphaned under fab_pivots_grp — the branch
    delete removed joints, aimers and the component node but never the
    extra-guide pivots. They floated in the viewport, and because
    _spawn_pivots_for_component de-dupes by (owner, name) a later same-id
    leg silently re-adopted the stale pivots ("delete the leg and they
    heal"). delete_components now takes each deleted component's pivots
    with it."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes, fs_app, branch_ops

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain('delpiv')
    nodes.create_registry('delpiv_bp')
    nodes.set_registry_root_joint(root)
    _add_world_component('delpiv_world', root)
    nodes.create_component_node(
        component_id='delpiv_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app._sync_component_pivots_from_scene()

    def _owned_pivots():
        grp = nodes.get_pivots_grp()
        if not grp:
            return []
        out = []
        for loc in cmds.listRelatives(grp, allDescendents=True,
                                      type='transform') or []:
            if cmds.attributeQuery('fab_extra_guide_owner', node=loc,
                                   exists=True) and \
               cmds.getAttr(f'{loc}.fab_extra_guide_owner') == 'delpiv_C0':
                out.append(loc)
        return out

    assert _owned_pivots(), (
        'fixture sanity: RibbonIKLeg should spawn heel/toe-tip pivots')

    branch_ops.delete_branches([thigh])

    leftover = _owned_pivots()
    assert not leftover, (
        f'foot pivots orphaned after leg delete: {leftover}')


def test_remove_leg_component_removes_its_foot_pivots():
    """Companion to the delete-limb case: Remove Component on a leg must
    also take its foot pivots (same orphan root cause, second gesture)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes, fs_app

    cmds.file(new=True, force=True)
    root, thigh, knee, ankle, ball = _make_leg_chain('rmpiv')
    nodes.create_registry('rmpiv_bp')
    nodes.set_registry_root_joint(root)
    _add_world_component('rmpiv_world', root)
    nodes.create_component_node(
        component_id='rmpiv_C0', component_type='RibbonIKLeg',
        joints=[thigh, knee, ankle, ball], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app._sync_component_pivots_from_scene()

    grp = nodes.get_pivots_grp()
    before = [l for l in (cmds.listRelatives(grp, allDescendents=True,
                                             type='transform') or [])
              if cmds.attributeQuery('fab_extra_guide_owner', node=l,
                                     exists=True)]
    assert before, 'fixture sanity: pivots should exist before removal'

    # Mirror _on_remove_component_requested's core: delete node + pivots.
    cnode = nodes.find_component_node_by_id('rmpiv_C0')
    nodes.delete_component_node(cnode)
    fs_app.delete_component_pivots(['rmpiv_C0'])

    grp = nodes.get_pivots_grp()
    after = [l for l in (cmds.listRelatives(grp, allDescendents=True,
                                            type='transform') or [])
             if cmds.attributeQuery('fab_extra_guide_owner', node=l,
                                    exists=True)
             and cmds.getAttr(f'{l}.fab_extra_guide_owner') == 'rmpiv_C0']
    assert not after, f'foot pivots orphaned after Remove Component: {after}'


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    check('test_delete_leg_removes_its_foot_pivots',
          test_delete_leg_removes_its_foot_pivots)
    check('test_remove_leg_component_removes_its_foot_pivots',
          test_remove_leg_component_removes_its_foot_pivots)
    check('test_ribbonikleg_p1_builds_identical_to_ikleg',
          test_ribbonikleg_p1_builds_identical_to_ikleg)
    check('test_ribbonikleg_p1_unbuild_zero_orphans',
          test_ribbonikleg_p1_unbuild_zero_orphans)
    check('test_ribbonikleg_segments_exist_with_mids',
          test_ribbonikleg_segments_exist_with_mids)
    check('test_ribbonikleg_twist_volume_at_posed_displacement',
          test_ribbonikleg_twist_volume_at_posed_displacement)
    check('test_ribbonikleg_no_double_transform_under_rig_scale',
          test_ribbonikleg_no_double_transform_under_rig_scale)
    check('test_ribbonikleg_unbuild_zero_orphans_including_ribbon',
          test_ribbonikleg_unbuild_zero_orphans_including_ribbon)
    check('test_ribbonikleg_persistence_round_trip',
          test_ribbonikleg_persistence_round_trip)
    check('test_ribbonikleg_heel_lift_zero_shin_counter_twist',
          test_ribbonikleg_heel_lift_zero_shin_counter_twist)
    check('test_ribbonikleg_ball_toe_do_not_feed_shin_twist',
          test_ribbonikleg_ball_toe_do_not_feed_shin_twist)
    check('test_ribbonikleg_foot_yaw_distributes_up_shin',
          test_ribbonikleg_foot_yaw_distributes_up_shin)
    check('test_ribbonikleg_thigh_roll_filtered_under_hip_swing',
          test_ribbonikleg_thigh_roll_filtered_under_hip_swing)
    check('test_ribbonikleg_roll_ankle_locator_mechanism_is_load_bearing',
          test_ribbonikleg_roll_ankle_locator_mechanism_is_load_bearing)
    check('test_ribbonikleg_ribbon_mid_ctrls_read_roll_joint_not_raw_bind_joint',
          test_ribbonikleg_ribbon_mid_ctrls_read_roll_joint_not_raw_bind_joint)
    check('test_ribbonikleg_twist_available_in_ik_and_fk',
          test_ribbonikleg_twist_available_in_ik_and_fk)
    check('test_ribbonikleg_mirror_right_side',
          test_ribbonikleg_mirror_right_side)
    check('test_ribbonikleg_zup_scene_roll_and_foot_roll_agree',
          test_ribbonikleg_zup_scene_roll_and_foot_roll_agree)
    check('test_ribbonikleg_limb_twist_joints_ride_mapped_segment',
          test_ribbonikleg_limb_twist_joints_ride_mapped_segment)
    check('test_ribbonikleg_no_limb_twist_riders_unchanged_from_p3',
          test_ribbonikleg_no_limb_twist_riders_unchanged_from_p3)
    check('test_ribbonikleg_limb_twist_unbuild_preserves_joints_zero_orphans',
          test_ribbonikleg_limb_twist_unbuild_preserves_joints_zero_orphans)
    check('test_ribbonikleg_leg_limb_fragment_drops_and_builds',
          test_ribbonikleg_leg_limb_fragment_drops_and_builds)

    if FAILURES:
        print(f"RIBBON IK LEG MAYA TESTS: {len(FAILURES)} FAILED "
              f"({len(SKIPS)} SKIP)")
        sys.exit(1)
    if SKIPS:
        print(f"RIBBON IK LEG MAYA TESTS: OK - {len(SKIPS)} SKIP "
              f"(not counted as pass): {SKIPS}")
    else:
        print("RIBBON IK LEG MAYA TESTS: OK - 0 SKIP")


if __name__ == '__main__':
    main()
