# _dev/test_aim_all_at_child_maya.py
"""mayapy scene tests for FABRICATE-mode aiming
(maya_tools/rigging/joint_orient/joint_orient_app.py: aim_all_at_child).

Companion offscreen suite: _dev/test_aim_all_at_child.py (the pure-data
_pick_chain_continuation / _is_default_aimer_state coverage — no scene
needed there). This file needs real joints + real aimConstraint
networks to prove: the multi-child deepest-subtree pick against actual
hierarchy depth, the end-joint parent-frame copy (aim AND roll) via
live DG reads, the co-located/tie skip guards, the joints=None vs
explicit-list scoping difference, and the overwrite-honesty reasons.

Every assertion reads WORLD rotation/matrix (cmds.xform(..., ws=True,
...)), never local rotate channels — a local-channel assert would pass
against code that is actually wrong (recorded lesson:
armature-bake-assert-world-not-local). All fixtures are off-origin
(recorded lesson: never assert near world origin, it hides matrix-math
bugs).

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_aim_all_at_child_maya.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import maya.standalone

maya.standalone.initialize(name='python')

import maya.cmds as cmds  # noqa: E402
import maya.api.OpenMaya as om  # noqa: E402

from maya_tools.rigging.joint_orient import joint_orient_app as joa  # noqa: E402

PASSED = []
FAILED = []

_OFFSET = (37.0, 52.0, -19.0)


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


# ─── fixtures ─────────────────────────────────────────────────────────

def _p(x, y, z):
    ox, oy, oz = _OFFSET
    return (x + ox, y + oy, z + oz)


def _pos(node):
    return om.MVector(*cmds.xform(node, q=True, ws=True, t=True))


def _world_rot(node):
    return tuple(cmds.xform(node, q=True, ws=True, ro=True))


def _world_aim_dir(node):
    """The direction the aimer's own aim axis points in world space —
    reads the WORLD MATRIX, never a local channel."""
    m = om.MMatrix(cmds.xform(node, q=True, ws=True, matrix=True))
    return (om.MVector(*joa.CONVENTION.aim_vector) * m).normalize()


def _assert_dir_close(actual, expected, tol=1e-3, msg=''):
    d = (actual - expected).length()
    assert d < tol, f'{msg}: dir mismatch {actual} vs {expected} (d={d})'


def _assert_rot_close(a_node, b_node, tol=1e-2, msg=''):
    ra, rb = _world_rot(a_node), _world_rot(b_node)
    for i, (x, y) in enumerate(zip(ra, rb)):
        assert abs(x - y) < tol, f'{msg}: axis {i} {ra} vs {rb}'


def _make_upperarm_rig(prefix='ua'):
    """root -> upperarm -> {lowerarm -> hand, upperarm_twist_01 (leaf)}.

    lowerarm's branch is one level deeper than the twist leaf, so
    'lowerarm' is the unambiguous deepest-subtree winner at upperarm —
    the exact UE-style shape the multi-child rule targets."""
    # Deliberately bent (never colinear with a world axis — a straight
    # chain can legitimately solve to an identity aim rotation, which
    # would make an "is this still identity" assertion a false
    # negative rather than a real check).
    cmds.select(clear=True)
    root = cmds.joint(p=_p(0, 0, 0), name=f'{prefix}_root')
    upperarm = cmds.joint(p=_p(10, 2, 0), name=f'{prefix}_upperarm')
    lowerarm = cmds.joint(p=_p(19, 6, 4), name=f'{prefix}_lowerarm')
    hand = cmds.joint(p=_p(28, 3, 9), name=f'{prefix}_hand')
    cmds.select(upperarm)
    twist = cmds.joint(p=_p(14, -1, -2), name=f'{prefix}_upperarm_twist_01')
    cmds.select(clear=True)
    joints = [root, upperarm, lowerarm, hand, twist]
    joa.create_aimers(joints)
    return dict(root=root, upperarm=upperarm, lowerarm=lowerarm,
               hand=hand, twist=twist)


# ─── A: full pass — multi-child pick, single-child aim, end-joint copy ─

def test_full_pass_aims_chain_and_copies_twist_frame():
    r = _make_upperarm_rig()

    # Non-default authored state on the twist BEFORE the pass — must be
    # named in reasons as replaced.
    joa.apply_aimer_state(r['twist'], aim_target='World')
    pre_twist_rot = _world_rot(joa.aimer_name(r['twist']))
    assert pre_twist_rot == (0.0, 0.0, 0.0), pre_twist_rot  # World == identity

    result = joa.aim_all_at_child([r['root']])

    assert result['skipped'] == 0, result
    assert result['aimed'] == 5, result
    assert any('multi-child pick' in s and r['upperarm'] in s
              for s in result['reasons']), result['reasons']
    assert any(r['twist'] in s and 'replaced authored state' in s
              for s in result['reasons']), result['reasons']

    # root aims at upperarm.
    _assert_dir_close(
        _world_aim_dir(joa.aimer_name(r['root'])),
        (_pos(r['upperarm']) - _pos(r['root'])).normalize(),
        msg='root aim')

    # upperarm picked lowerarm (deeper subtree), NOT the twist leaf.
    _assert_dir_close(
        _world_aim_dir(joa.aimer_name(r['upperarm'])),
        (_pos(r['lowerarm']) - _pos(r['upperarm'])).normalize(),
        msg='upperarm aim (must pick lowerarm over twist)')

    # lowerarm aims at hand.
    _assert_dir_close(
        _world_aim_dir(joa.aimer_name(r['lowerarm'])),
        (_pos(r['hand']) - _pos(r['lowerarm'])).normalize(),
        msg='lowerarm aim')

    # hand (leaf) copies lowerarm's full frame (aim AND roll).
    _assert_rot_close(joa.aimer_name(r['hand']), joa.aimer_name(r['lowerarm']),
                      msg='hand should copy lowerarm frame verbatim')

    # twist (leaf) copies upperarm's full frame — and that is NOT the
    # pre-pass World/identity rotation it started with, proving the
    # overwrite actually happened rather than the reason being a
    # false positive.
    _assert_rot_close(joa.aimer_name(r['twist']), joa.aimer_name(r['upperarm']),
                      msg='twist should copy upperarm frame verbatim')
    post_twist_rot = _world_rot(joa.aimer_name(r['twist']))
    assert sum(abs(v) for v in post_twist_rot) > 1.0, (
        f'twist rotation looks unchanged from identity: {post_twist_rot}')


# ─── B: co-located child -> skip, untouched ────────────────────────────

def test_colocated_single_child_is_skipped_and_untouched():
    cmds.select(clear=True)
    a = cmds.joint(p=_p(0, 0, 0), name='coloc_a')
    b = cmds.joint(p=_p(0, 0, 0), name='coloc_b')  # co-located with a, leaf
    cmds.select(clear=True)
    joa.create_aimers([a, b])
    joa.apply_aimer_state(a, aim_target='World')  # distinctive pre-state
    pre = joa.get_aimer_state(a)

    # [a] scopes to {a, b} (a plus its descendant) — b is a separate
    # leaf joint and legitimately still gets aimed (it copies a's
    # frame per rule 3); only a itself is skipped for the co-located
    # guard. aim_all_at_child does not stop processing descendants
    # just because an ancestor's own pick was skipped.
    result = joa.aim_all_at_child([a])

    assert result['skipped'] == 1, result
    assert result['aimed'] == 1, result
    assert any('coloc_a' in s and 'co-located' in s
              for s in result['reasons']), result['reasons']
    post = joa.get_aimer_state(a)
    assert post == pre, f'skipped joint must be left untouched: {pre} -> {post}'


# ─── C: genuine multi-child tie -> skip, untouched ─────────────────────

def test_genuine_multichild_tie_is_skipped_and_untouched():
    cmds.select(clear=True)
    p = cmds.joint(p=_p(0, 0, 0), name='tie_parent')
    cmds.select(p)
    c1 = cmds.joint(p=_p(10, 0, 0), name='tie_child_a')
    cmds.select(p)
    c2 = cmds.joint(p=_p(0, 10, 0), name='tie_child_b')  # same length, both leaves
    cmds.select(clear=True)
    joa.create_aimers([p, c1, c2])
    joa.apply_aimer_state(p, aim_target='World')
    pre = joa.get_aimer_state(p)

    result = joa.aim_all_at_child([p])

    assert result['skipped'] == 1, result
    assert any('tie' in s for s in result['reasons']), result['reasons']
    post = joa.get_aimer_state(p)
    assert post == pre, f'tied joint must be left untouched: {pre} -> {post}'


# ─── D: joints=None scopes to the aimer set, never fabricates new aimers

def test_none_scopes_to_existing_aimers_only():
    cmds.select(clear=True)
    a = cmds.joint(p=_p(0, 0, 0), name='scope_a')
    b = cmds.joint(p=_p(10, 0, 0), name='scope_b')
    c = cmds.joint(p=_p(20, 0, 0), name='scope_c')  # deliberately no aimer
    cmds.select(clear=True)
    joa.create_aimers([a, b])  # c excluded on purpose

    result = joa.aim_all_at_child(None)

    assert result['aimed'] == 2, result
    assert result['skipped'] == 0, result
    assert not cmds.objExists(joa.aimer_name(c)), (
        'aim_all_at_child must never fabricate a NEW aimer for a joint '
        'that had none')
    _assert_dir_close(
        _world_aim_dir(joa.aimer_name(b)),
        (_pos(c) - _pos(b)).normalize(),
        msg='scope_b aim (single child, aimer-less child is still a '
           'valid geometric target)')


# ─── E: end-joint guards — no parent joint / parent has no aimer ──────

def test_end_joint_with_no_parent_joint_is_skipped():
    cmds.select(clear=True)
    lone = cmds.joint(p=_p(0, 0, 0), name='lone_leaf')
    cmds.select(clear=True)
    joa.create_aimers([lone])
    pre = joa.get_aimer_state(lone)

    result = joa.aim_all_at_child([lone])

    assert result['skipped'] == 1, result
    assert any('no parent joint' in s for s in result['reasons']), result['reasons']
    assert joa.get_aimer_state(lone) == pre


def test_end_joint_whose_parent_has_no_aimer_is_skipped():
    cmds.select(clear=True)
    parent = cmds.joint(p=_p(0, 0, 0), name='noaimer_parent')
    cmds.select(parent)
    leaf = cmds.joint(p=_p(10, 0, 0), name='noaimer_leaf')
    cmds.select(clear=True)
    joa.create_aimers([leaf])  # parent gets none
    pre = joa.get_aimer_state(leaf)

    result = joa.aim_all_at_child([leaf])

    assert result['skipped'] == 1, result
    assert any('has no aimer' in s for s in result['reasons']), result['reasons']
    assert joa.get_aimer_state(leaf) == pre


# ─── F: stale enum (child added after aimer creation) -> skip, not crash

def test_stale_enum_after_child_added_is_skipped_not_fabricated():
    cmds.select(clear=True)
    a = cmds.joint(p=_p(0, 0, 0), name='stale_a')
    cmds.select(clear=True)
    joa.create_aimer(a)  # built with ZERO children in the enum

    cmds.select(a)
    b = cmds.joint(p=_p(10, 0, 0), name='stale_b')  # added after the fact
    cmds.select(clear=True)
    # a's aimer enum is now stale: it has no 'stale_b' target slot. [a]
    # scopes to {a, b}; b is a leaf with no aimer of its own (never
    # created one for it), so it is ALSO skipped, independently, for
    # that reason — this test is about a's stale-enum guard
    # specifically, not the total skip count.
    pre_a = joa.get_aimer_state(a)

    result = joa.aim_all_at_child([a])

    assert result['aimed'] == 0, result
    assert any('target slot' in s and 'stale_a' in s
              for s in result['reasons']), result['reasons']
    assert any('stale_b' in s and 'no aimer' in s
              for s in result['reasons']), result['reasons']
    # The failed apply must not have partially mutated a's own state.
    assert joa.get_aimer_state(a) == pre_a, (
        'a failed apply_aimer_state must leave the aimer exactly as it was')


def test_end_joint_still_matches_parent_AFTER_the_bake():
    """THE property the feature was actually requested for, measured on
    the JOINTS after Orient All Joints — not on the aimers before it.

    Added in review 2026-07-21: every other test here asserts the frame
    copy on the AIMER, immediately after the pass. That is the mechanism,
    not the deliverable. What was asked for is that an end joint ends up
    oriented like its parent once the bake has run, and the bake is a
    real transform: it unparents every aimed joint to world, writes
    rotate, zeroes jointOrient/rotateAxis, and reparents. A copy that is
    correct pre-bake and lost by it would pass all seven other tests and
    fail the user. Baked state hides its own cause, so this measures
    the joint afterward rather than the channel that participates."""
    r = _make_upperarm_rig(prefix='bake')

    result = joa.aim_all_at_child()
    assert result['aimed'] > 0, result

    joa.orient_all_aimers(force=True)

    # The twist is an end joint hanging off upperarm; upperarm is a
    # multi-child joint that just got re-aimed at lowerarm. So this also
    # proves the copy read the parent's FRESH frame, not its stale one.
    _assert_rot_close(
        r['twist'], r['upperarm'], tol=1e-2,
        msg='end joint lost its parent frame through the bake')

    # Export contract: the bake must leave rotate carrying the
    # orientation with jointOrient zeroed (a nonzero jointOrient
    # alongside rotateAxis silently world-aligns on UE/Unity import).
    jo = cmds.getAttr(f"{r['twist']}.jointOrient")[0]
    assert all(abs(v) < 1e-6 for v in jo), (
        f'jointOrient not zeroed on the copied end joint: {jo}')


if __name__ == '__main__':
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'\naim_all_at_child (FABRICATE mode) — {len(tests)} tests\n')
    for name, fn in tests:
        check(name, fn)

    print()
    if FAILED:
        print(f'FAILED ({len(FAILED)}/{len(tests)}):')
        for name, err in FAILED:
            print(f'  {name}: {err}')
        sys.exit(1)
    print(f'ALL PASS ({len(PASSED)})')
