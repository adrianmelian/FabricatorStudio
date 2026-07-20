"""Twist joints: aim at the elbow, and survive undo (Adrian, 2026-07-13).

  ORIENTATION — a twist joint has no child, so its aimer had nothing to point
  at. The aimer system gained a global "Parent" target; a twist aimer aims at
  the parent and flips 180 RZ, which — because the twist sits on the
  parent->segment-end line — lands the aim on the segment end (the elbow).

  UNDO — the live callback writes twist positions OUTSIDE the drag's undo step,
  so undo restored the ctrl but not them. Fix: a non-undoable re-evaluate on
  every undo/redo.

    PYTHONNOUSERSITE=1 mayapy _dev/test_follow_twist_orient_maya.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import maya.standalone

maya.standalone.initialize(name='python')

import maya.cmds as cmds  # noqa: E402
import maya.api.OpenMaya as om  # noqa: E402  (was missing — _world_x below
#                                always referenced om; latent NameError)

from maya_tools.rigging.fabricator import follow_rules as fr  # noqa: E402
from maya_tools.rigging.fabricator import armature as amt  # noqa: E402
from maya_tools.rigging.joint_orient import joint_orient_app as joa  # noqa: E402


def _world_x(node):
    m = om.MMatrix(cmds.xform(node, q=True, ws=True, matrix=True))
    return om.MVector(om.MVector(1, 0, 0) * m).normalize()

PASSED = []
FAILED = []


def check(name, fn):
    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True, infinity=True)
    try:
        fn()
    except AssertionError as e:
        FAILED.append((name, str(e)))
        print(f'  FAIL  {name}\n        {e}', flush=True)
    except Exception as e:                                    # noqa: BLE001
        import traceback
        FAILED.append((name, f'{type(e).__name__}: {e}'))
        print(f'  ERROR {name}\n        {type(e).__name__}: {e}', flush=True)
        traceback.print_exc()
    else:
        PASSED.append(name)
        print(f'  ok    {name}', flush=True)


def _segment(end=(7.0, 7.0, 0.0)):
    """seg_top at origin aimed at seg_end (Maya auto-orients the chain), plus a
    childless twist joint parented under seg_top. Returns (top, end, twist)."""
    cmds.select(clear=True)
    top = cmds.joint(name='seg_top', position=(0, 0, 0))
    end_j = cmds.joint(name='seg_end', position=end)     # child -> top aims at it
    cmds.select(top)
    twist = cmds.joint(name='twist_01',                  # childless, under top
                       position=(end[0] / 2, end[1] / 2, end[2] / 2))
    cmds.select(clear=True)
    return top, end_j, twist


# ── orientation: the aimer "Parent" target + flip ─────────────

def test_aimer_gains_a_parent_target():
    """The global addition: a joint with a parent joint gets a 'Parent' aim
    target (last, so Local/World indices are unchanged). A root does not."""
    cmds.select(clear=True)
    root = cmds.joint(name='root_jnt', position=(0, 0, 0))
    child = cmds.joint(name='child_jnt', position=(0, -10, 0))
    cmds.select(clear=True)

    joa.create_aimer(child)
    enum = cmds.attributeQuery('aimTarget', node=joa.aimer_name(child),
                               listEnum=True)[0].split(':')
    assert enum[-1] == 'Parent', f'child aimer enum lacks Parent: {enum}'

    joa.create_aimer(root)
    root_enum = cmds.attributeQuery('aimTarget', node=joa.aimer_name(root),
                                    listEnum=True)[0].split(':')
    assert 'Parent' not in root_enum, \
        f'the root has no parent joint, so no Parent target: {root_enum}'


def test_parent_flip_points_the_aimer_away_from_the_parent():
    """The trick: Parent aims up at the parent; the 180 RZ flips it to point
    the other way. A twist sits on the parent->end line, so 'the other way' is
    straight at the segment end (the elbow)."""
    cmds.select(clear=True)
    cmds.joint(name='calf', position=(0, 0, 0))              # parent, above
    twist = cmds.joint(name='calf_twist_01', position=(0, -10, 0))  # below it
    cmds.select(clear=True)
    joa.create_aimer(twist)

    ok = joa.point_aimer_at_parent_flipped(twist)
    assert ok, 'point_aimer_at_parent_flipped reported no-op'
    x = _world_x(joa.aimer_name(twist))
    # parent is at +Y from the twist; flipped, the aimer must point -Y (down),
    # i.e. toward where the segment end / elbow sits.
    assert x[1] < -0.9, f'aimer X did not flip away from the parent: {tuple(round(c,2) for c in x)}'


def test_bake_points_twist_aimers_at_the_segment_end():
    """End to end through the build's aimer bake: a FRESH childless twist
    joint's aimer ends up in Parent mode with the 180 flip, so it aims at
    the segment end (the elbow). Confirmed correct on a real rig, both
    sides. (Trued 2026-07-14 to the creation-default-only contract of
    15bfd9d: the flip applies ONLY to aimers passed in `fresh` — an aimer
    carrying authored/restored state is never touched. This fixture's
    aimers are freshly created, so it passes them as fresh, matching what
    build_armature does via _ensure_aimers.)"""
    cmds.select(clear=True)
    root = cmds.joint(name='calf', position=(0, 0, 0))
    end_j = cmds.joint(name='foot', position=(0, -10, 0))    # seg end (elbow)
    cmds.select(root)
    twist = cmds.joint(name='calf_twist_01', position=(0, -5, 0))  # childless
    cmds.select(clear=True)

    joints = [root, end_j, twist]
    joa.create_aimers(joints)
    fr.set_follow_rule(twist, 'distribute', (root, end_j), t=0.5)

    amt._bake_and_restore_aimers(joints, scale=10.0, fresh=[twist])

    st = joa.get_aimer_state(twist)
    assert st and st['aim_target'] == 'Parent', \
        f'twist aimer is not in Parent mode after the bake: {st}'
    # Assert the flip's PURPOSE, not its euler shape: the aimer's aim
    # axis (ctl +X) points from the twist at the segment end. The
    # world-rotation restore (15bfd9d) may express the same orientation
    # as RY 180 instead of RZ 180 — channel asserts are brittle.
    m = om.MMatrix(cmds.xform(joa.aimer_name(twist), q=True, ws=True,
                              matrix=True))
    aim = om.MVector(m[0], m[1], m[2]).normalize()
    tw = om.MVector(*cmds.xform(twist, q=True, ws=True, t=True))
    end = om.MVector(*cmds.xform(end_j, q=True, ws=True, t=True))
    want = (end - tw).normalize()
    assert aim * want > 0.99, \
        f'twist aimer does not point at the segment end (dot={aim * want:.3f}, state={st})'


# ── undo/redo re-sync ─────────────────────────────────────────

def test_undo_redo_resync_repositions_a_stale_twist():
    """The re-sync function pulls a ruled joint back onto its rule. Stand in for
    the real undo by leaving the joint stale (as a drag callback would after an
    undo it did not fire on) and calling the handler directly."""
    # armature_exists() gates the handler — give it the group it looks for.
    if not cmds.objExists(amt._GRP):
        cmds.createNode('transform', name=amt._GRP)
    top, end_j, twist = _segment(end=(10.0, 0.0, 0.0))
    fr.set_follow_rule(twist, 'distribute', (top, end_j), t=0.5)
    fr.evaluate([twist])
    assert abs(cmds.xform(twist, q=True, ws=True, t=True)[0] - 5.0) < 1e-3

    # Move the end, but DO NOT re-evaluate — the twist is now stale, exactly the
    # post-undo state (ctrl moved, twist left behind).
    cmds.xform(end_j, ws=True, t=(20, 0, 0))
    stale = cmds.xform(twist, q=True, ws=True, t=True)
    assert abs(stale[0] - 5.0) < 1e-3, 'precondition: twist should still be stale'

    amt._follow_on_undo_redo()

    synced = cmds.xform(twist, q=True, ws=True, t=True)
    assert abs(synced[0] - 10.0) < 1e-3, \
        f'the re-sync did not pull the twist onto its rule: {synced}'


def test_undo_resync_is_safe_with_no_armature():
    """Fires on every undo in the session, most with no Armature present."""
    assert not cmds.objExists(amt._GRP)
    amt._follow_on_undo_redo()   # must be a silent no-op, not a raise


def test_real_undo_brings_the_twist_back():
    """End to end: an actual cmds.undo() re-syncs the twist. Proves the
    MEventMessage 'Undo' wiring, not just the handler in isolation."""
    if not cmds.objExists(amt._GRP):
        cmds.createNode('transform', name=amt._GRP)
    # Arm the real undo/redo event callbacks (idempotent module-global install).
    amt._follow_watch_ctrls([])

    top, end_j, twist = _segment(end=(10.0, 0.0, 0.0))
    fr.set_follow_rule(twist, 'distribute', (top, end_j), t=0.5)
    fr.evaluate([twist])

    # seg_top is at the origin, end_j at x=10, so the twist sits at x=5.
    assert abs(cmds.xform(twist, q=True, ws=True, t=True)[0] - 5.0) < 1e-3

    # A drag = an UNDOABLE move of the end to x=20...
    cmds.undoInfo(openChunk=True)
    cmds.xform(end_j, ws=True, t=(20, 0, 0))
    cmds.undoInfo(closeChunk=True)
    # ...plus the follow callback's twist write, made NON-undoable exactly as
    # the live callback's is. This is the whole bug: the twist write is not on
    # the undo stack, so undoing the drag reverts the end but not the twist.
    _prev = cmds.undoInfo(q=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    fr.evaluate([twist])
    cmds.undoInfo(stateWithoutFlush=_prev)
    assert abs(cmds.xform(twist, q=True, ws=True, t=True)[0] - 10.0) < 1e-3

    cmds.undo()   # reverts end_j to x=10; the twist is left stale at x=10, so
                  # the Undo event must re-sync it to lerp(0, 10, 0.5) = 5.
    synced = cmds.xform(twist, q=True, ws=True, t=True)[0]
    assert abs(synced - 5.0) < 1e-2, \
        f'after undo the twist did not re-sync to its rule (x={synced})'


def test_stale_restored_aimer_state_is_detected():
    """find_stale_restored_aimers flags a live aimer whose WORLD
    orientation disagrees with its joint — the 'aimers flipped on
    unbuild' incident class (stale/contaminated registry aimer state
    replayed faithfully by the restore; Adrian's Reggie right leg,
    2026-07-14). Healthy aimers agree with their joints and are never
    flagged; the check is detection-only (validators WARN, never fix)."""
    from maya_tools.rigging.fabricator import fs_app

    cmds.select(clear=True)
    a = cmds.joint(name='stalechk_thigh', position=(0, 10, 0))
    cmds.joint(name='stalechk_calf', position=(0, 0, 0))
    cmds.select(clear=True)
    joa.create_aimers([a, 'stalechk_calf'])

    assert fs_app.find_stale_restored_aimers() == [], \
        'freshly created aimers must not be flagged'

    # Tamper one aimer ctl 180 deg in world — the stale-state shape.
    ctl = joa.aimer_name(a)
    rot = cmds.xform(ctl, q=True, ws=True, ro=True)
    cmds.xform(ctl, ws=True, ro=(rot[0], rot[1], rot[2] + 180.0))

    stale = fs_app.find_stale_restored_aimers()
    names = [j for j, _ in stale]
    assert names == [a], f'expected exactly {a!r} flagged, got {stale}'
    assert stale[0][1] > 90, f'expected a ~180 disagreement, got {stale}'


if __name__ == '__main__':
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'\nfollow twist orientation + undo re-sync — {len(tests)} tests\n')
    for name, fn in tests:
        check(name, fn)

    print()
    if FAILED:
        print(f'FAILED ({len(FAILED)}/{len(tests)}):')
        for name, err in FAILED:
            print(f'  {name}: {err}')
        sys.exit(1)
    print(f'ALL PASS ({len(PASSED)})')
