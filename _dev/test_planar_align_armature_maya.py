# _dev/test_planar_align_armature_maya.py
"""Straighten Mid Joint against a LIVE Armature.

The pure-math suite (maya_tools/rigging/fabricator/_dev/test_planar_align.py)
proves the projection is right. It cannot catch the bug Adrian found in
Maya on 2026-07-21, because that bug was entirely about WHICH NODE gets
written: driving the mid joint's ctrl drags every child ctrl parented
under it, so the knee straightened and the whole lower leg slid sideways
with it. The chain ended up just as bent, only offset.

So every assertion here measures the DESCENDANT as well as the mid
joint, in world space. "The knee moved correctly" is not evidence; "the
knee moved and the ankle held station" is.

Run: mayapy _dev/test_planar_align_armature_maya.py
"""
__author__ = "Adrian Melian"

import sys

import maya.standalone
maya.standalone.initialize(name='python')

import maya.cmds as cmds  # noqa: E402

sys.path.insert(0, 'D:/Documents/FabricatorStudio')

from maya_tools.rigging.fabricator import armature as amt      # noqa: E402
from maya_tools.rigging.fabricator import planar_align as pa    # noqa: E402

PASSED, FAILED = [], []


def check(name, fn):
    cmds.file(new=True, force=True)
    try:
        fn()
    except AssertionError as e:
        FAILED.append((name, str(e)))
        print(f'  FAIL  {name}\n        {e}', flush=True)
    except Exception as e:  # noqa: BLE001
        import traceback
        FAILED.append((name, f'{type(e).__name__}: {e}'))
        print(f'  ERROR {name}\n        {type(e).__name__}: {e}', flush=True)
        traceback.print_exc()
    else:
        PASSED.append(name)
        print(f'  ok    {name}', flush=True)


def _leg(with_twists=False):
    """root -> hip -> knee -> ankle -> ball, bone down world Y.

    The knee carries a 2.0 sideways (X) error and a 15.0 deliberate
    forward (Z) bend, so the tool must remove exactly the first and
    leave exactly the second. with_twists adds calf twists as extra
    children of the knee — the UE-style shape that used to make the
    one-node selection form raise 'ambiguous'.
    """
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 100, 0), name='root_jnt')
    cmds.joint(p=(10, 90, 0), name='hip_jnt')
    knee = cmds.joint(p=(12, 50, 15), name='knee_jnt')
    cmds.joint(p=(10, 10, 0), name='ankle_jnt')
    cmds.joint(p=(10, 5, 6), name='ball_jnt')
    if with_twists:
        cmds.select(knee)
        cmds.joint(p=(11, 37, 10), name='calf_twist_01_jnt')
        cmds.select(knee)
        cmds.joint(p=(11, 24, 5), name='calf_twist_02_jnt')
    cmds.select(clear=True)
    amt.build_armature(root=root)
    return root


def _w(node):
    return cmds.xform(node, q=True, ws=True, t=True)


def test_the_ankle_holds_station_while_the_knee_straightens():
    """THE regression. Before the fix this failed on the ankle, not the
    knee: the knee landed correctly and the ankle came along for the
    ride, so the leg was offset rather than straightened."""
    _leg()
    knee_before, ankle_before = _w('knee_jnt'), _w('ankle_jnt')
    ball_before = _w('ball_jnt')

    r = pa.align_mid_joint('knee_jnt')

    knee_after, ankle_after = _w('knee_jnt'), _w('ankle_jnt')
    ball_after = _w('ball_jnt')

    assert r['axis'] == 'x', f"expected the sideways axis, got {r['axis']!r}"
    assert r['drove'] == 'solo', (
        f"must drive the solo handle so the subtree stays put, "
        f"drove {r['drove']!r}")
    # The knee actually moved, and moved on X only.
    assert abs(knee_after[0] - knee_before[0]) > 1e-3, 'the knee never moved'
    assert abs(knee_after[2] - knee_before[2]) < 1e-3, (
        'the deliberate forward bend was disturbed')
    # ... and everything BELOW it stayed exactly where it was.
    for name, before, after in (('ankle', ankle_before, ankle_after),
                                ('ball', ball_before, ball_after)):
        assert all(abs(a - b) < 1e-3 for a, b in zip(before, after)), (
            f'{name} was dragged along by the straighten: '
            f'{before} -> {after}')


def test_the_chain_is_actually_straight_on_the_corrected_axis():
    """Not just 'the knee moved' — the hip/knee/ankle triple must be
    colinear on X afterwards, which is what 'straight head-on' means."""
    _leg()
    pa.align_mid_joint('knee_jnt')
    hip, knee, ankle = _w('hip_jnt'), _w('knee_jnt'), _w('ankle_jnt')
    t = ((knee[1] - hip[1]) / (ankle[1] - hip[1]))
    expected_x = hip[0] + t * (ankle[0] - hip[0])
    assert abs(knee[0] - expected_x) < 1e-3, (
        f'knee x={knee[0]} is not on the hip->ankle line (want '
        f'{expected_x}) — the chain is not straight head-on')


def test_one_node_selection_works_on_a_knee_with_twists():
    """A UE-style knee's children are the ankle AND its calf twists.
    The one-node form used to raise 'ambiguous' on exactly the joint
    this tool exists for."""
    _leg(with_twists=True)
    r = pa.align_mid_joint('knee_jnt')
    assert r['child'] == 'ankle_jnt', (
        f"chain continuation should be the ankle, got {r['child']!r} "
        f"(a twist stub won, or it raised)")


def test_selecting_the_solo_cage_resolves_to_the_joint():
    """During a marking-menu swap the cage is the only visible
    manipulator, so it is what the rigger has selected."""
    _leg()
    solo = amt.solo_handle_for_joint('knee_jnt')
    r = pa.align_mid_joint(solo)
    assert r['mid'] == 'knee_jnt', f"resolved to {r['mid']!r}"


def test_selection_driven_call_with_nothing_passed():
    _leg()
    cmds.select(amt.ctrl_for_joint('knee_jnt'), replace=True)
    r = pa.align_mid_joint()
    assert r['mid'] == 'knee_jnt', f"resolved to {r['mid']!r}"


def test_straighten_then_commit_bakes_it_into_placement():
    """The UI commits after a straighten (Adrian, 2026-07-21), same
    rule as putting the cages away: the correction lands in a solo
    handle, and an uncommitted handle is real but invisible state.

    This is the app-level pair the toolbar handler runs. The joints
    must not budge across the commit, and the handles must re-zero.
    """
    _leg()
    r = pa.align_mid_joint('knee_jnt')
    assert r['drove'] == 'solo'
    assert amt.pending_solo_nudges() == ['knee_jnt'], \
        amt.pending_solo_nudges()

    straightened = {j: _w(j) for j in
                    ('hip_jnt', 'knee_jnt', 'ankle_jnt', 'ball_jnt')}

    assert amt.commit_solo_nudges(root='root_jnt') == 1

    for j, before in straightened.items():
        after = _w(j)
        assert all(abs(a - b) < 1e-3 for a, b in zip(before, after)), (
            f'{j} moved during the commit: {before} -> {after}')
    assert amt.pending_solo_nudges() == [], 'the handle did not re-zero'
    # And the chain is still straight afterwards — the commit preserved
    # the correction rather than quietly undoing it.
    hip, knee, ankle = _w('hip_jnt'), _w('knee_jnt'), _w('ankle_jnt')
    t = (knee[1] - hip[1]) / (ankle[1] - hip[1])
    assert abs(knee[0] - (hip[0] + t * (ankle[0] - hip[0]))) < 1e-3, \
        'the commit lost the straighten'


def test_a_plain_skeleton_with_no_armature_moves_the_joint():
    """No Armature at all (the Joint Aimer case) — the bare joint is
    the only thing to write."""
    cmds.select(clear=True)
    cmds.joint(p=(10, 90, 0), name='hip_jnt')
    cmds.joint(p=(12, 50, 15), name='knee_jnt')
    cmds.joint(p=(10, 10, 0), name='ankle_jnt')
    cmds.select(clear=True)
    r = pa.align_mid_joint('knee_jnt')
    assert r['drove'] == 'joint', f"drove {r['drove']!r}"
    assert abs(_w('knee_jnt')[0] - 10.0) < 1e-3, 'the joint did not move'


if __name__ == '__main__':
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'\nStraighten Mid Joint x live Armature — {len(tests)} tests\n')
    for name, fn in tests:
        check(name, fn)

    print()
    if FAILED:
        print(f'FAILED ({len(FAILED)}/{len(tests)}):')
        for name, err in FAILED:
            print(f'  {name}: {err}')
        sys.exit(1)
    print(f'ALL PASS ({len(PASSED)})')
