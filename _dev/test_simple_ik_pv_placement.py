# _dev/test_simple_ik_pv_placement.py
"""mayapy scene tests for SimpleIK pole-vector PLACEMENT — the crossed-PV
bug (GitHub issue #32) and the pv_position marker override.

Background: _position_pv_at_build projected the mid joint perpendicular to
the start->end line, NORMALIZED the perpendicular, and scaled it to
chain_len * pv_distance. On a near-straight leg (typical game bind pose)
the knee's deviation off the hip->ankle line is millimetres of modeling
noise whose lateral component routinely beats its forward component, so
normalization turned millimetres of knock-knee into a half-chain-length
lateral throw: the left leg's PV built in front of the RIGHT leg and vice
versa. Same root path feeds SimpleIK, IKLeg, RibbonIKLeg.

The fix (this suite locks it):
  - Chains bent >= PV_STRAIGHT_EPSILON_DEG at the mid joint keep the
    perpendicular projection (elbows in A-pose, quad legs — unchanged).
  - Near-straight chains ignore the noisy perpendicular and push along the
    MID JOINT'S OWN local axis that points most world-forward (selector:
    world +Z in Y-up; the push follows the joint's actual axis so Y-forward
    UE-style knees, mirrored -Y left knees, Z-forward conventions, and
    angled creature knees all place correctly). Near-straight HORIZONTAL
    chains (T-pose arm) sign the pick BACKWARD so a straight elbow's PV
    still builds behind the character.
  - The auto-PV ("magic_pv") bind perpendicular and the straight-chain
    preferredAngle seed both derive from the same resolved direction, so
    the live graph and the solver agree with the placement.
  - A 'pv' ExtraGuide marker (same rails as IKLeg's heel/toe pivots)
    captures into options['pv_position']; when set, build uses it verbatim
    and the auto-PV perpendicular follows the marker's side.

Run (userSetup prepends its FABRICATOR root to sys.path during standalone
init; this harness re-pins its target AFTER initialize; default is the
depot source, FS_TEST_TARGET=<path-to-Fabricator_Data> exercises a shipped
build copy for the red-on-shipped A/B proof):
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_simple_ik_pv_placement.py
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


# ── fixtures ─────────────────────────────────────────────────────────────
#
# All legs: Y-up, character faces +Z, ~88-unit chain, hip at x=±10.
# "Noisy" = knee 1.0 unit INWARD (toward x=0) and only 0.3 forward — the
# knock-knee modeling noise that reproduced the crossed placement. Bend at
# the knee measures well under 2 degrees: visually and numerically straight.

def _make_leg(prefix, side_x, knee_orient=None):
    """side_x: +10 = character-left leg, -10 = character-right leg.
    Knee is 1.0 inward / 0.3 forward of the hip->ankle line. Returns
    (root, hip, knee, ankle). knee_orient (degrees tuple) sets the knee's
    jointOrient AT CREATION — before the ankle exists — so the ankle's
    authored world position is preserved (a post-hoc jointOrient write
    would rotate the child and bend the fixture). None = world-aligned
    local axes, i.e. local +Z already points world-forward."""
    import maya.cmds as cmds
    inward = -1.0 if side_x > 0 else 1.0
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 92, 0), name=f'{prefix}_root')
    hip = cmds.joint(p=(side_x, 90, 0), name=f'{prefix}_hip')
    knee_kwargs = {'p': (side_x + inward, 45, 0.3), 'name': f'{prefix}_knee'}
    if knee_orient is not None:
        knee_kwargs['orientation'] = knee_orient
    knee = cmds.joint(**knee_kwargs)
    ankle = cmds.joint(p=(side_x, 2, 0), name=f'{prefix}_ankle')
    cmds.select(clear=True)
    return root, hip, knee, ankle


def _v(j):
    import maya.cmds as cmds
    return cmds.xform(j, q=True, ws=True, t=True)


def _position_pv(start, mid, end, factor=0.5):
    from maya_tools.rigging.fabricator.modules.simple_ik import (
        _position_pv_at_build)
    return _position_pv_at_build(start, mid, end, factor)


def _assert_forward_own_side(pv, knee_pos, side_x, label):
    """PV must land clearly in FRONT of the knee (world +Z) and stay on its
    own leg's side of the midline — never crossed past x=0."""
    assert pv[2] > knee_pos[2] + 5.0, (
        f'{label}: PV z={pv[2]:.2f} is not in front of the knee '
        f'(knee z={knee_pos[2]:.2f}) — pv={pv}')
    if side_x > 0:
        assert pv[0] > 0.0, (
            f'{label}: left-leg PV crossed the midline: x={pv[0]:.2f}, pv={pv}')
    else:
        assert pv[0] < 0.0, (
            f'{label}: right-leg PV crossed the midline: x={pv[0]:.2f}, pv={pv}')
    # And laterally close to its own knee: the old bug threw the PV tens of
    # units sideways; forward placement keeps |x - knee.x| small.
    assert abs(pv[0] - knee_pos[0]) < 10.0, (
        f'{label}: PV drifted laterally {abs(pv[0]-knee_pos[0]):.2f} units '
        f'off its knee — pv={pv}, knee={knee_pos}')


# ── unit tests: _position_pv_at_build directly ───────────────────────────

def test_near_straight_left_and_right_legs_place_forward_uncrossed():
    import maya.cmds as cmds
    cmds.file(new=True, force=True)
    _, hipL, kneeL, ankleL = _make_leg('pvL', +10)
    _, hipR, kneeR, ankleR = _make_leg('pvR', -10)

    pvL = _position_pv(hipL, kneeL, ankleL)
    pvR = _position_pv(hipR, kneeR, ankleR)

    _assert_forward_own_side(pvL, _v(kneeL), +10, 'left')
    _assert_forward_own_side(pvR, _v(kneeR), -10, 'right')


def test_y_forward_knee_orients_place_forward():
    """UE-skeleton convention: right knee local +Y points world-forward,
    left knee local -Y points world-forward. jointOrient (90,0,0) maps
    local +Y onto world +Z; (-90,0,0) maps local -Y onto world +Z. Both
    must resolve to a world-forward PV — the joint's own axis is the push
    direction, chosen and signed by forward alignment."""
    import maya.cmds as cmds
    cmds.file(new=True, force=True)
    _, hipR, kneeR, ankleR = _make_leg('pvYfwdR', -10, knee_orient=(90, 0, 0))
    _, hipL, kneeL, ankleL = _make_leg('pvYfwdL', +10, knee_orient=(-90, 0, 0))

    pvR = _position_pv(hipR, kneeR, ankleR)
    pvL = _position_pv(hipL, kneeL, ankleL)

    _assert_forward_own_side(pvR, _v(kneeR), -10, 'right y-forward')
    _assert_forward_own_side(pvL, _v(kneeL), +10, 'left y-forward')


def test_angled_creature_knee_follows_joint_axis_not_world_z():
    """A knee whose forward axis is deliberately angled (creature limb):
    jointOrient (0, 30, 0) yaws the local frame 30 degrees, so local +Z
    points (sin30, 0, cos30) = (0.5, 0, 0.866) in world. The PV must push
    along THAT axis — clearly forward, but visibly off pure world +Z in
    the authored direction (world-Z-hardcoded placement would fail the
    angle assertion)."""
    import math
    import maya.cmds as cmds
    cmds.file(new=True, force=True)
    _, hip, knee, ankle = _make_leg('pvAngled', +10, knee_orient=(0, 30, 0))

    pv = _position_pv(hip, knee, ankle)
    knee_pos = _v(knee)

    off = [pv[i] - knee_pos[i] for i in range(3)]
    mag = math.sqrt(sum(c * c for c in off))
    assert mag > 1.0, f'PV did not leave the knee: pv={pv}'
    d = [c / mag for c in off]
    expected = (math.sin(math.radians(30)), 0.0, math.cos(math.radians(30)))
    dot = sum(d[i] * expected[i] for i in range(3))
    assert dot > 0.95, (
        f'PV direction {d} does not follow the knee\'s angled forward axis '
        f'{expected} (dot={dot:.3f}) — world-Z hardcode or noise direction?')


def test_bent_chain_keeps_perpendicular_projection():
    """A genuinely bent chain (elbow bent ~44 degrees toward -Z) must keep
    today's perpendicular-projection placement — behind the chain, on the
    elbow's own side. Locks the >= epsilon path against regressions."""
    import maya.cmds as cmds
    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    shoulder = cmds.joint(p=(0, 150, 0), name='pvBent_shoulder')
    elbow = cmds.joint(p=(14, 150, -4), name='pvBent_elbow')
    wrist = cmds.joint(p=(28, 150, 0), name='pvBent_wrist')
    cmds.select(clear=True)

    pv = _position_pv(shoulder, elbow, wrist)
    assert pv[2] < -5.0, (
        f'bent chain PV should follow the bend plane (behind, -Z): pv={pv}')
    assert abs(pv[1] - 150.0) < 2.0, (
        f'bent chain PV left the bend plane vertically: pv={pv}')


def test_straight_horizontal_arm_places_behind():
    """An EXACTLY straight T-pose arm (horizontal chain) is near-straight
    but arm-like: the fallback must sign the picked axis BACKWARD (-Z) so
    a straight elbow's PV never builds in front of the character."""
    import maya.cmds as cmds
    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    shoulder = cmds.joint(p=(2, 150, 0), name='pvTarm_shoulder')
    elbow = cmds.joint(p=(16, 150, 0), name='pvTarm_elbow')
    wrist = cmds.joint(p=(30, 150, 0), name='pvTarm_wrist')
    cmds.select(clear=True)

    pv = _position_pv(shoulder, elbow, wrist)
    assert pv[2] < -5.0, (
        f'straight T-pose arm PV should build behind the character: pv={pv}')


# ── end-to-end: full build ───────────────────────────────────────────────

def _add_world_component(component_id, root_joint):
    from maya_tools.rigging.fabricator import nodes
    nodes.create_component_node(
        component_id=component_id, component_type='World',
        joints=[root_joint], parent_plug='', side='md',
        options={}, persisted={},
    )


def test_full_build_near_straight_legs_pv_uncrossed():
    """Full fs_app build on two mirrored noisy legs. The PV ctrls' world
    positions AFTER build (i.e. through the space-switch/auto-PV wiring,
    not just the initial xform) must be forward and uncrossed — this locks
    _bind_perp_vec/magic_pv to the same resolved direction as placement."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    rootL, hipL, kneeL, ankleL = _make_leg('e2eL', +10)
    rootR, hipR, kneeR, ankleR = _make_leg('e2eR', -10)
    # One skeleton root for the World component; parent the right hip chain
    # under the left fixture's root so both legs share one hierarchy.
    cmds.parent(hipR, rootL)
    cmds.delete(rootR)

    nodes.create_registry('pv_placement_bp')
    _add_world_component('pvplace_world', rootL)
    nodes.create_component_node(
        component_id='pvplace_L', component_type='SimpleIK',
        joints=[hipL, kneeL, ankleL], parent_plug='', side='lf',
        options={}, persisted={},
    )
    nodes.create_component_node(
        component_id='pvplace_R', component_type='SimpleIK',
        joints=[hipR, kneeR, ankleR], parent_plug='', side='rt',
        options={}, persisted={},
    )
    fs_app.build_modules()

    for knee, side_x, label in ((kneeL, +10, 'left'), (kneeR, -10, 'right')):
        pv_ctrl = f'{knee}_PV_ctrl'
        assert cmds.objExists(pv_ctrl), f'missing {pv_ctrl}'
        pv = cmds.xform(pv_ctrl, q=True, ws=True, t=True)
        _assert_forward_own_side(pv, _v(knee), side_x, f'e2e {label}')

    fs_app.unbuild_modules()


def test_pv_position_option_overrides_auto_placement():
    """options['pv_position'] (captured from the pv marker guide) must win
    over auto placement, and the auto-PV graph must follow the marker's
    side. The override deliberately points BEHIND the leg (digitigrade /
    backward-bending creature limb) — a side the fixed auto placement
    would never choose on this fixture — so this test cannot pass unless
    the option is actually consumed."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, hip, knee, ankle = _make_leg('ovr', +10)

    override = (11.0, 47.0, -35.0)  # behind the left leg
    nodes.create_registry('pv_override_bp')
    _add_world_component('pvovr_world', root)
    nodes.create_component_node(
        component_id='pvovr_C0', component_type='SimpleIK',
        joints=[hip, knee, ankle], parent_plug='', side='lf',
        options={'pv_position': list(override)}, persisted={},
    )
    fs_app.build_modules()

    pv_ctrl = f'{knee}_PV_ctrl'
    assert cmds.objExists(pv_ctrl), f'missing {pv_ctrl}'
    pv = cmds.xform(pv_ctrl, q=True, ws=True, t=True)
    # Post-build position rides the auto-PV space (midpoint + reprojected
    # bind perpendicular at chain_len * pv_distance), so it need not equal
    # the override verbatim — but it MUST sit on the override's BACKWARD
    # side of the chain, proving both placement and _bind_perp_vec obeyed
    # the marker rather than the auto math.
    knee_pos = _v(knee)
    assert pv[2] < knee_pos[2] - 5.0, (
        f'override behind the leg was ignored (PV not behind): pv={pv}')
    assert pv[0] > 0.0, f'override: PV crossed the midline: pv={pv}'

    fs_app.unbuild_modules()


def test_pv_marker_guide_declared_across_ik_family():
    """Every IK-family contract must declare the 'pv' extra guide AND the
    pv_position option it captures into — a fresh Contract() that forgets
    the carry-over silently loses the marker (extra_guides defaults to
    an empty tuple)."""
    from maya_tools.rigging.fabricator.modules.simple_ik import SIMPLE_IK_CONTRACT
    from maya_tools.rigging.fabricator.modules.ik_arm import IK_ARM_CONTRACT
    from maya_tools.rigging.fabricator.modules.ik_leg import IK_LEG_CONTRACT
    from maya_tools.rigging.fabricator.modules.ribbon_ik_arm import RIBBON_IK_ARM_CONTRACT
    from maya_tools.rigging.fabricator.modules.ribbon_ik_leg import RIBBON_IK_LEG_CONTRACT
    from maya_tools.rigging.fabricator.modules.quad_leg import QUAD_LEG_CONTRACT

    for c in (SIMPLE_IK_CONTRACT, IK_ARM_CONTRACT, IK_LEG_CONTRACT,
              RIBBON_IK_ARM_CONTRACT, RIBBON_IK_LEG_CONTRACT,
              QUAD_LEG_CONTRACT):
        names = [eg.name for eg in c.extra_guides]
        assert 'pv' in names, f'{c.type}: pv marker guide missing ({names})'
        assert 'pv_position' in c.options_schema, (
            f'{c.type}: pv_position option missing')


def test_quad_leg_pv_position_option_overrides_placement():
    """QuadLeg consumes pv_position too. Unlike SimpleIK there is no
    auto-PV graph (deferred with space switching), so the built PV ctrl
    must sit at the override VERBATIM. Override goes behind the leg —
    a side the automatic forward-bent-quad-knee projection would never
    choose, so this cannot pass unless the option is consumed."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 62, 0), name='quadovr_root')
    upper = cmds.joint(p=(10, 60, 0), name='quadovr_upper')
    knee = cmds.joint(p=(10, 38, 6), name='quadovr_knee')
    ankle = cmds.joint(p=(10, 16, -4), name='quadovr_ankle')
    toe = cmds.joint(p=(10, 3, 5), name='quadovr_toe')
    toe_end = cmds.joint(p=(10, 3, 13), name='quadovr_toe_end')
    cmds.select(clear=True)

    override = (12.0, 40.0, -30.0)  # behind the quad leg
    nodes.create_registry('quad_pv_bp')
    _add_world_component('quadovr_world', root)
    nodes.create_component_node(
        component_id='quadovr_C0', component_type='QuadLeg',
        joints=[upper, knee, ankle, toe, toe_end], parent_plug='', side='lf',
        options={'pv_position': list(override)}, persisted={},
    )
    fs_app.build_modules()

    pv_ctrl = f'{knee}_PV_ctrl'
    assert cmds.objExists(pv_ctrl), f'missing {pv_ctrl}'
    pv = cmds.xform(pv_ctrl, q=True, ws=True, t=True)
    for i, (got, want) in enumerate(zip(pv, override)):
        assert abs(got - want) < 1e-4, (
            f'quad override ignored on axis {i}: built at {pv}, '
            f'wanted {override}')

    fs_app.unbuild_modules()


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')
    # userSetup's FABRICATOR block prepends its own root during initialize
    # and maya_tools may already be imported from it; purge and re-pin so
    # the test exercises the intended target. FS_TEST_TARGET=<path> pins a
    # shipped build's Fabricator_Data instead (red-on-shipped A/B proof).
    target = os.environ.get('FS_TEST_TARGET')
    pin = Path(target) if target else REPO_ROOT
    for mod in [m for m in sys.modules if m.split('.')[0] == 'maya_tools']:
        del sys.modules[mod]
    sys.path.insert(0, str(pin))
    import maya_tools.rigging.fabricator.modules.simple_ik as _sik
    print(f"  target simple_ik: {_sik.__file__}")

    check('test_near_straight_left_and_right_legs_place_forward_uncrossed',
          test_near_straight_left_and_right_legs_place_forward_uncrossed)
    check('test_y_forward_knee_orients_place_forward',
          test_y_forward_knee_orients_place_forward)
    check('test_angled_creature_knee_follows_joint_axis_not_world_z',
          test_angled_creature_knee_follows_joint_axis_not_world_z)
    check('test_bent_chain_keeps_perpendicular_projection',
          test_bent_chain_keeps_perpendicular_projection)
    check('test_straight_horizontal_arm_places_behind',
          test_straight_horizontal_arm_places_behind)
    check('test_full_build_near_straight_legs_pv_uncrossed',
          test_full_build_near_straight_legs_pv_uncrossed)
    check('test_pv_position_option_overrides_auto_placement',
          test_pv_position_option_overrides_auto_placement)
    check('test_pv_marker_guide_declared_across_ik_family',
          test_pv_marker_guide_declared_across_ik_family)
    check('test_quad_leg_pv_position_option_overrides_placement',
          test_quad_leg_pv_position_option_overrides_placement)

    if FAILURES:
        print(f"SIMPLE IK PV PLACEMENT TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("SIMPLE IK PV PLACEMENT TESTS: OK")


if __name__ == '__main__':
    main()
