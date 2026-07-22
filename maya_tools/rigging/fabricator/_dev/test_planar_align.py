# maya_tools/rigging/fabricator/_dev/test_planar_align.py
"""Offscreen contract tests for planar_align.py's pure math (no Maya
scene, no maya module needed at all — see planar_align.py's "Pure math"
section marker for why every function under test has zero Maya imports).

This is the Maya-free half of the house test idiom
(_dev/test_blueprint_version_stamp.py, testing blueprint/schema.py's
zero-maya-import dataclasses the same way), as opposed to simple_ik.py's
own isolated-but-still-mayapy classmethod tests
(_dev/test_simple_ik_pref_angle.py) — those import maya.cmds at module
level so they always need mayapy even for an "isolated" case; this file
needs neither mayapy nor a maya install at all.

Proves the key property align_mid_joint's spec requires: after
correction the chain is straight on the CHOSEN axis (deviation from the
parent->child line on that axis becomes 0) AND the other two axes are
bit-identical to the input M — a knee's deliberate forward bend must
survive untouched.

Run:
py -3 maya_tools/rigging/fabricator/_dev/test_planar_align.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


def test_module_imports_with_no_maya_on_sys_path():
    """The whole point of the pure-math split: importing planar_align
    must never require a `maya` module. This test running at all (under
    plain py -3, confirmed to have no maya installed) already proves
    this, but assert explicitly that 'maya' never made it into
    sys.modules as a side effect of the import."""
    assert 'maya' not in sys.modules, (
        "importing planar_align pulled in a maya module -- a top-level "
        "`import maya...` snuck into the pure-math section")
    import maya_tools.rigging.fabricator.planar_align as pa
    assert 'maya' not in sys.modules, (
        "planar_align import pulled in maya as a side effect")
    assert hasattr(pa, 'align_mid_joint')
    assert hasattr(pa, 'solve_projection_param')


def test_solve_projection_param_midpoint():
    from maya_tools.rigging.fabricator.planar_align import solve_projection_param
    p, c = (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)
    m = (5.0, 3.0, 0.0)   # halfway along X, offset on Y -- t should be 0.5
    t = solve_projection_param(p, m, c)
    assert abs(t - 0.5) < 1e-9, f'expected t=0.5, got {t}'


def test_solve_projection_param_clamps_below_zero():
    from maya_tools.rigging.fabricator.planar_align import solve_projection_param
    p, c = (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)
    m = (-5.0, 2.0, 0.0)   # behind the parent -- raw t would be negative
    t = solve_projection_param(p, m, c)
    assert t == 0.0, f'expected clamp to 0.0, got {t}'


def test_solve_projection_param_clamps_above_one():
    from maya_tools.rigging.fabricator.planar_align import solve_projection_param
    p, c = (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)
    m = (15.0, 2.0, 0.0)   # past the child -- raw t would be > 1
    t = solve_projection_param(p, m, c)
    assert t == 1.0, f'expected clamp to 1.0, got {t}'


def test_solve_projection_param_degenerate_raises():
    from maya_tools.rigging.fabricator.planar_align import solve_projection_param
    p = c = (1.0, 2.0, 3.0)   # coincident parent/child -- zero-length chain
    m = (1.0, 5.0, 3.0)
    try:
        solve_projection_param(p, m, c)
        raise AssertionError('expected ValueError for a degenerate chain')
    except ValueError:
        pass


def test_axis_deviations_matches_manual_calc():
    from maya_tools.rigging.fabricator.planar_align import axis_deviations
    p, c = (0.0, 10.0, 0.0), (10.0, 10.0, 0.0)   # chain runs along world X
    # Knee-like: bent forward in Z (deliberate, large) and drifted a
    # little sideways in Y (the error, small).
    m = (5.0, 10.3, 2.0)
    t = 0.5   # exact midpoint by construction
    dev = axis_deviations(p, m, c, t)
    # proj = (5.0, 10.0, 0.0) at t=0.5
    assert abs(dev['x'] - 0.0) < 1e-9, dev
    assert abs(dev['y'] - 0.3) < 1e-9, dev
    assert abs(dev['z'] - 2.0) < 1e-9, dev


def test_pick_correction_axis_picks_smallest_excluding_bone():
    from maya_tools.rigging.fabricator.planar_align import pick_correction_axis
    # Bone runs along x. y is the smallest of the two REMAINING axes --
    # the sideways rig error, per the module docstring's "knee pokes
    # forward a lot, sideways a little".
    p, c = (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)
    dev = {'x': 0.0, 'y': 0.02, 'z': 4.5}
    assert pick_correction_axis(p, c, dev) == 'y', dev


def test_pick_correction_axis_ties_favor_x_then_y_then_z():
    from maya_tools.rigging.fabricator.planar_align import pick_correction_axis
    p, c = (0.0, 0.0, 0.0), (0.0, 0.0, 10.0)      # bone along z
    assert pick_correction_axis(p, c, {'x': 1.0, 'y': 1.0, 'z': 1.0}) == 'x'
    assert pick_correction_axis(p, c, {'x': 9.0, 'y': 1.0, 'z': 1.0}) == 'y'


def test_pick_correction_axis_regression_axis_aligned_bone_is_not_a_noop():
    """REGRESSION (found in review 2026-07-21, before this ever ran in
    Maya). The original heuristic was a plain least-deviation min() over
    all three axes. Deviation on the axis the bone runs along is
    STRUCTURALLY zero, not incidentally small: when the P->C segment is
    axis-aligned, t solves so lerp(P, C, t) reproduces M's component on
    that axis exactly. So min() always selected the bone axis and the
    correction was a guaranteed no-op -- on the single most common input
    there is, a leg or arm whose bone runs down a world axis.

    This pins the real case: a leg down world Y, knee poking forward 15
    (deliberate) with a 2.0 sideways error (the thing to remove)."""
    from maya_tools.rigging.fabricator.planar_align import (
        solve_projection_param, axis_deviations, pick_correction_axis,
        corrected_mid_position,
    )
    p = (10.0, 90.0, 0.0)     # hip
    m = (12.0, 50.0, 15.0)    # knee: +2.0 sideways error, +15.0 forward bend
    c = (10.0, 10.0, 0.0)     # ankle

    t = solve_projection_param(p, m, c)
    dev = axis_deviations(p, m, c, t)
    # The structural zero that broke the original heuristic:
    assert dev['y'] == 0.0, f'expected bone-axis deviation 0, got {dev}'

    axis = pick_correction_axis(p, c, dev)
    assert axis == 'x', f'must pick the sideways error axis, got {axis!r}: {dev}'

    corrected = corrected_mid_position(p, m, c, t, axis)
    # The 2.0 sideways error is actually GONE (the old code left it).
    assert corrected[0] == 10.0, (
        f'sideways error not removed: x={corrected[0]}, expected 10.0')
    # The deliberate 15.0 forward bend is bit-identical.
    assert corrected[2] == m[2], (
        f'forward bend disturbed: {corrected[2]} != {m[2]}')


def test_corrected_mid_position_invalid_axis_raises():
    from maya_tools.rigging.fabricator.planar_align import corrected_mid_position
    try:
        corrected_mid_position((0, 0, 0), (1, 1, 1), (2, 2, 2), 0.5, 'w')
        raise AssertionError('expected ValueError for an unknown axis')
    except ValueError:
        pass


def test_corrected_mid_position_key_property_straight_on_chosen_axis_only():
    """THE key property the spec requires: after correction, the chain
    is straight on the CHOSEN axis (deviation becomes exactly 0 there)
    AND the other two axes are returned bit-identical (==, not
    approx-equal) to the input M -- a knee's deliberate forward bend
    must survive untouched."""
    from maya_tools.rigging.fabricator.planar_align import (
        solve_projection_param, axis_deviations, corrected_mid_position,
    )
    p = (0.0, 10.0, 0.0)
    c = (10.0, 10.0, 0.0)
    # Deliberate forward (Z) bend of 2.0, plus a small sideways (Y)
    # rigging error of 0.3 -- the auto-pick scenario from the module
    # docstring, X itself sits exactly on the line already.
    m = (5.0, 10.3, 2.0)

    t = solve_projection_param(p, m, c)
    corrected = corrected_mid_position(p, m, c, t, 'y')

    # Y (the chosen/error axis) must land exactly on the P->C line --
    # deviation recomputed against the CORRECTED point is 0.
    dev_after = axis_deviations(p, corrected, c, t)
    assert dev_after['y'] == 0.0, (
        f'chosen axis y is not straight after correction: {dev_after}')

    # X and Z must be BIT-IDENTICAL to the input -- not just close.
    assert corrected[0] == m[0], (
        f'x changed: input={m[0]} corrected={corrected[0]}')
    assert corrected[2] == m[2], (
        f'z (the deliberate forward bend) changed: input={m[2]} '
        f'corrected={corrected[2]}')

    # And the deliberate bend on Z is UNCHANGED in magnitude too (not
    # just bit-identical raw value -- belt and suspenders on the same
    # fact, re-derived independently from dev_after rather than the
    # corrected tuple directly).
    assert abs(dev_after['z'] - 2.0) < 1e-9, (
        f'forward bend deviation was disturbed: {dev_after}')


def test_corrected_mid_position_explicit_axis_overrides_auto_pick():
    """An explicit axis must be honored even when it is NOT the axis
    'auto' would choose -- 'auto' is a default, not the only path.

    The P->C line here is deliberately NOT axis-aligned (seg=(6,8,0)) so
    the setup exercises the diagonal-bone case; the axis-aligned case is
    covered by its own regression test above."""
    from maya_tools.rigging.fabricator.planar_align import (
        solve_projection_param, axis_deviations, corrected_mid_position,
        pick_correction_axis,
    )
    p = (0.0, 10.0, 0.0)
    c = (6.0, 18.0, 0.0)   # seg=(6,8,0), diagonal in the XY plane
    m = (2.6, 14.3, 2.0)   # y is the smallest deviation, x medium, z largest

    t = solve_projection_param(p, m, c)
    dev = axis_deviations(p, m, c, t)
    assert pick_correction_axis(p, c, dev) == 'x'  # sanity: bone axis y excluded

    # Force z instead -- the deliberate-bend axis -- and confirm THAT
    # one goes flat, while y (the "error" axis) is left untouched.
    corrected = corrected_mid_position(p, m, c, t, 'z')
    dev_after = axis_deviations(p, corrected, c, t)
    assert dev_after['z'] == 0.0, dev_after
    assert corrected[1] == m[1], (
        f'y should be untouched when axis is explicitly z: '
        f'input={m[1]} corrected={corrected[1]}')


def main():
    check('test_module_imports_with_no_maya_on_sys_path',
          test_module_imports_with_no_maya_on_sys_path)
    check('test_solve_projection_param_midpoint',
          test_solve_projection_param_midpoint)
    check('test_solve_projection_param_clamps_below_zero',
          test_solve_projection_param_clamps_below_zero)
    check('test_solve_projection_param_clamps_above_one',
          test_solve_projection_param_clamps_above_one)
    check('test_solve_projection_param_degenerate_raises',
          test_solve_projection_param_degenerate_raises)
    check('test_axis_deviations_matches_manual_calc',
          test_axis_deviations_matches_manual_calc)
    check('test_pick_correction_axis_picks_smallest_excluding_bone',
          test_pick_correction_axis_picks_smallest_excluding_bone)
    check('test_pick_correction_axis_ties_favor_x_then_y_then_z',
          test_pick_correction_axis_ties_favor_x_then_y_then_z)
    check('test_pick_correction_axis_regression_axis_aligned_bone_is_not_a_noop',
          test_pick_correction_axis_regression_axis_aligned_bone_is_not_a_noop)
    check('test_corrected_mid_position_invalid_axis_raises',
          test_corrected_mid_position_invalid_axis_raises)
    check('test_corrected_mid_position_key_property_straight_on_chosen_axis_only',
          test_corrected_mid_position_key_property_straight_on_chosen_axis_only)
    check('test_corrected_mid_position_explicit_axis_overrides_auto_pick',
          test_corrected_mid_position_explicit_axis_overrides_auto_pick)

    if FAILURES:
        print(f"PLANAR ALIGN TESTS: {len(FAILURES)} FAILED ({len(SKIPS)} SKIP)")
        sys.exit(1)
    print("PLANAR ALIGN TESTS: OK - 0 SKIP")


if __name__ == "__main__":
    main()
