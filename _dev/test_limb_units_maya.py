# _dev/test_limb_units_maya.py
"""mayapy SCENE tests for the follower-joint primitive
(maya_tools/rigging/fabricator/follow_rules.py) that specifically need a
live, saveable scene: rename-proofness, scene save/reload persistence,
and evaluate()'s posed-displacement behavior (including evaluation
order — parents before children).

SPEC: workspace/2026-07-09_limbs-unit-follower-joints/SPEC.md section 3.1.
Companion suite: _dev/test_limb_units.py (CRUD, validation, plain
resolve_position/resolve_orientation math).

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_limb_units_maya.py
"""
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


class Skip(Exception):
    """Raise from a test body to mark it SKIPPED — never silently folded
    into the pass count (see test_ribbon_ik_arm.py's identical class for the
    full rationale)."""


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


# Off-origin fixtures throughout (studio deformer-order lesson).
_OFFSET = (37.0, 52.0, -19.0)


def _make_offset_joint(name, pos):
    import maya.cmds as cmds
    ox, oy, oz = _OFFSET
    x, y, z = pos
    j = cmds.joint(p=(x + ox, y + oy, z + oz), name=name)
    return j


def _make_elbow_wrist_chain(prefix):
    """elbow -> wrist, a real Maya parent/child chain, off-origin."""
    import maya.cmds as cmds
    cmds.select(clear=True)
    elbow = _make_offset_joint(f'{prefix}_elbow', (0.0, 0.0, 0.0))
    wrist = _make_offset_joint(f'{prefix}_wrist', (12.0, 0.0, 0.0))
    cmds.select(clear=True)
    return elbow, wrist


def test_rename_proof_distribute():
    """Rename BOTH the ruled joint and its distribute targets after
    set_follow_rule — get_follow_rule and resolve_position must still
    resolve correctly (this is the entire point of message-connected
    persistence: no name string is ever the source of truth)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist = _make_elbow_wrist_chain('rntest')
    cmds.select(clear=True)
    twist = _make_offset_joint('rntest_twist', (4.0, 0.0, 0.0))
    cmds.select(clear=True)

    fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=0.5)

    elbow2 = cmds.rename(elbow, 'rntest_elbow_RENAMED')
    wrist2 = cmds.rename(wrist, 'rntest_wrist_RENAMED')
    twist2 = cmds.rename(twist, 'rntest_twist_RENAMED')

    rule = fr.get_follow_rule(twist2)
    assert rule is not None, 'rule vanished after renaming the ruled joint'
    assert rule['kind'] == 'distribute'
    assert rule['targets'] == [elbow2, wrist2], (
        f'targets did not track the rename: {rule["targets"]}')

    pos = fr.resolve_position(twist2)
    p_elbow = cmds.xform(elbow2, q=True, ws=True, t=True)
    p_wrist = cmds.xform(wrist2, q=True, ws=True, t=True)
    expected = [(p_elbow[i] + p_wrist[i]) / 2.0 for i in range(3)]
    for i in range(3):
        assert abs(pos[i] - expected[i]) < 1e-6, (pos, expected)

    # Old names must no longer resolve to anything.
    assert not cmds.objExists(elbow)
    assert fr.get_follow_rule(twist) is None if cmds.objExists(twist) else True


def test_rename_proof_match():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist = _make_elbow_wrist_chain('rnmatch')
    cmds.select(clear=True)
    ikhand = _make_offset_joint('rnmatch_ikhand', (12.0, 0.0, 0.0))
    cmds.select(clear=True)

    fr.set_follow_rule(ikhand, 'match', (wrist,))
    wrist2 = cmds.rename(wrist, 'rnmatch_wrist_RENAMED')
    ikhand2 = cmds.rename(ikhand, 'rnmatch_ikhand_RENAMED')

    rule = fr.get_follow_rule(ikhand2)
    assert rule is not None
    assert rule['targets'] == [wrist2]

    cmds.xform(wrist2, ws=True, t=(100.0, 5.0, -3.0))
    pos = fr.resolve_position(ikhand2)
    expected = cmds.xform(wrist2, q=True, ws=True, t=True)
    for i in range(3):
        assert abs(pos[i] - expected[i]) < 1e-6


def test_persistence_survives_save_reload():
    """Rules must round-trip through cmds.file(save) -> cmds.file(open)
    exactly as any other network-node state does."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist = _make_elbow_wrist_chain('persist')
    cmds.select(clear=True)
    twist = _make_offset_joint('persist_twist', (6.0, 0.0, 0.0))
    ikhand = _make_offset_joint('persist_ikhand', (12.0, 0.0, 0.0))
    cmds.select(clear=True)

    fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=0.3)
    fr.set_follow_rule(ikhand, 'match', (wrist,), linked=True)

    tmp_dir = Path(tempfile.mkdtemp(prefix='ks_follow_rules_test_'))
    scene_path = tmp_dir / 'follow_rules_persist_test.ma'
    cmds.file(rename=str(scene_path))
    cmds.file(save=True, type='mayaAscii', force=True)

    cmds.file(new=True, force=True)
    assert fr.get_follow_rule(twist) is None or not cmds.objExists(twist)

    cmds.file(str(scene_path), open=True, force=True)

    rule_dist = fr.get_follow_rule(twist)
    assert rule_dist is not None, 'distribute rule did not survive save/reload'
    assert rule_dist['kind'] == 'distribute'
    assert set(rule_dist['targets']) == {elbow, wrist}
    assert abs(rule_dist['t'] - 0.3) < 1e-6
    assert rule_dist['linked'] is False

    rule_match = fr.get_follow_rule(ikhand)
    assert rule_match is not None, 'match rule did not survive save/reload'
    assert rule_match['kind'] == 'match'
    assert rule_match['targets'] == [wrist]
    assert rule_match['t'] is None
    assert rule_match['linked'] is True, (
        'linked flag did not survive save/reload')

    pos = fr.resolve_position(twist)
    assert pos is not None


def test_evaluate_twist_siblings_move_wrist_and_match_snap():
    """Two sibling twist joints (t=1/3, 2/3) between elbow->wrist; move
    the wrist joint; evaluate(); both re-sit at exact fractions measured
    off the POSED (moved) chain, not bind. A match(wrist) joint snaps
    position AND orientation."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist = _make_elbow_wrist_chain('evaltwist')
    cmds.select(clear=True)
    twist_a = _make_offset_joint('evaltwist_twist_a', (4.0, 0.0, 0.0))
    twist_b = _make_offset_joint('evaltwist_twist_b', (8.0, 0.0, 0.0))
    ikhand = _make_offset_joint('evaltwist_ikhand', (12.0, 1.0, 0.0))
    cmds.select(clear=True)

    fr.set_follow_rule(twist_a, 'distribute', (elbow, wrist), t=1.0 / 3.0)
    fr.set_follow_rule(twist_b, 'distribute', (elbow, wrist), t=2.0 / 3.0)
    fr.set_follow_rule(ikhand, 'match', (wrist,))

    cmds.setAttr(f'{wrist}.rotate', 5.0, 15.0, -20.0, type='double3')

    # Pose the driver: move the wrist joint (posed displacement, per the
    # studio deformer-order lesson — never assert only at bind).
    cmds.xform(wrist, ws=True, relative=True, t=(3.0, -7.0, 2.0))

    evaluated = fr.evaluate([twist_a, twist_b, ikhand])
    assert set(evaluated) == {twist_a, twist_b, ikhand}

    p_elbow = cmds.xform(elbow, q=True, ws=True, t=True)
    p_wrist = cmds.xform(wrist, q=True, ws=True, t=True)

    for jnt, frac in ((twist_a, 1.0 / 3.0), (twist_b, 2.0 / 3.0)):
        expected = [p_elbow[i] + (p_wrist[i] - p_elbow[i]) * frac
                    for i in range(3)]
        got = cmds.xform(jnt, q=True, ws=True, t=True)
        for i in range(3):
            assert abs(got[i] - expected[i]) < 1e-5, (
                f'{jnt}: expected {expected}, got {got} (frac={frac})')

    # match joint: position AND orientation snap to the (posed) wrist.
    got_pos = cmds.xform(ikhand, q=True, ws=True, t=True)
    for i in range(3):
        assert abs(got_pos[i] - p_wrist[i]) < 1e-5

    got_mat = list(cmds.xform(ikhand, q=True, ws=True, matrix=True))
    wrist_mat = list(cmds.xform(wrist, q=True, ws=True, matrix=True))
    for i in range(16):
        assert abs(got_mat[i] - wrist_mat[i]) < 1e-5, (
            f'match joint orientation did not snap to wrist at index {i}: '
            f'{got_mat} vs {wrist_mat}')


def test_evaluate_orders_parents_before_children():
    """A child ruled joint's match target is its own ruled PARENT — the
    only way the child ends up exactly at the external anchor (rather
    than at the parent's pre-evaluate position) is if evaluate()
    processes the parent first. Passed in child-then-parent order on
    purpose to prove evaluate() itself re-orders, not the caller."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    anchor = _make_offset_joint('ordertest_anchor', (50.0, 5.0, 9.0))
    cmds.select(clear=True)
    parent_j = _make_offset_joint('ordertest_parent', (0.0, 0.0, 0.0))
    child_j = cmds.joint(p=(
        _OFFSET[0] + 2.0, _OFFSET[1], _OFFSET[2]), name='ordertest_child')
    cmds.select(clear=True)

    assert (cmds.listRelatives(child_j, parent=True, type='joint') or
            [None])[0] == parent_j, 'fixture setup: child must be a real Maya child of parent'

    fr.set_follow_rule(parent_j, 'match', (anchor,))
    fr.set_follow_rule(child_j, 'match', (parent_j,))

    fr.evaluate([child_j, parent_j])  # deliberately reversed order

    anchor_pos = cmds.xform(anchor, q=True, ws=True, t=True)
    child_pos = cmds.xform(child_j, q=True, ws=True, t=True)
    for i in range(3):
        assert abs(child_pos[i] - anchor_pos[i]) < 1e-5, (
            f'child did not resolve against the PARENT\'S NEW position — '
            f'evaluate() likely processed child before parent: '
            f'child={child_pos} anchor={anchor_pos}')


def test_evaluate_orders_parents_before_children_separated_by_unruled():
    """Same guarantee as test_evaluate_orders_parents_before_children,
    but with an UNRULED joint separating the ruled grandparent from the
    ruled child. _sort_parents_first's docstring claims correct
    ordering 'regardless of how many unruled joints separate them' —
    nothing exercised that SEPARATED case until now. Passed in
    child-then-grandparent order on purpose: if evaluate() ordered by
    plain insertion order (rather than true hierarchy depth), the
    child would resolve against the grandparent's PRE-evaluate
    position instead of its NEW one."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    anchor = _make_offset_joint('sepordertest_anchor', (50.0, 5.0, 9.0))
    cmds.select(clear=True)
    grandparent = _make_offset_joint('sepordertest_grandparent', (0.0, 0.0, 0.0))
    parent_unruled = _make_offset_joint('sepordertest_parent_unruled', (1.0, 0.0, 0.0))
    child = _make_offset_joint('sepordertest_child', (2.0, 0.0, 0.0))
    cmds.select(clear=True)

    assert (cmds.listRelatives(parent_unruled, parent=True, type='joint') or
            [None])[0] == grandparent, (
        'fixture setup: parent_unruled must be a real Maya child of grandparent')
    assert (cmds.listRelatives(child, parent=True, type='joint') or
            [None])[0] == parent_unruled, (
        'fixture setup: child must be a real Maya child of parent_unruled')

    fr.set_follow_rule(grandparent, 'match', (anchor,))
    fr.set_follow_rule(child, 'match', (grandparent,))

    fr.evaluate([child, grandparent])  # deliberately reversed order

    anchor_pos = cmds.xform(anchor, q=True, ws=True, t=True)
    child_pos = cmds.xform(child, q=True, ws=True, t=True)
    for i in range(3):
        assert abs(child_pos[i] - anchor_pos[i]) < 1e-5, (
            f'child did not resolve against the grandparent\'s NEW '
            f'position across the unruled middle joint — evaluate() '
            f'likely ordered by insertion order instead of true '
            f'hierarchy depth: child={child_pos} anchor={anchor_pos}')


def test_evaluate_resolves_each_rule_exactly_once_per_joint():
    """Perf hot-path guard (SPEC 3.1; this feeds the armature
    live-update hook, Task 1.2): evaluate() must fetch each candidate
    joint's rule from the network node exactly ONCE per call, not
    redundantly re-resolve it 3-4x while computing position and
    orientation."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist = _make_elbow_wrist_chain('callcount')
    cmds.select(clear=True)
    twist = _make_offset_joint('callcount_twist', (4.0, 0.0, 0.0))
    ikhand = _make_offset_joint('callcount_ikhand', (12.0, 0.0, 0.0))
    cmds.select(clear=True)

    fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=0.5)
    fr.set_follow_rule(ikhand, 'match', (wrist,))

    calls = {'n': 0}
    real_get_follow_rule = fr.get_follow_rule

    def _counting_get_follow_rule(joint):
        calls['n'] += 1
        return real_get_follow_rule(joint)

    fr.get_follow_rule = _counting_get_follow_rule
    try:
        evaluated = fr.evaluate([twist, ikhand])
    finally:
        fr.get_follow_rule = real_get_follow_rule

    assert set(evaluated) == {twist, ikhand}
    assert calls['n'] == 2, (
        f"evaluate() should resolve each ruled joint's rule exactly "
        f'once per call (2 joints => 2 calls), got {calls["n"]} calls '
        f'— a redundant get_follow_rule() re-fetch regressed the hot path')


def _make_live_armature_fixture(prefix):
    """3-joint arm (shoulder/elbow/wrist) + 2 sibling twist joints
    (distribute t=1/3, 2/3 between elbow->wrist) + 1 sibling match(wrist)
    joint, all real Maya parent/child, off-origin. Twist/match joints
    sit off elbow's aim axis (Y-offset) so elbow's aimer unambiguously
    targets wrist, not one of them."""
    import maya.cmds as cmds
    ox, oy, oz = _OFFSET
    cmds.select(clear=True)
    shoulder = cmds.joint(p=(0 + ox, 0 + oy, 0 + oz), name=f'{prefix}_shoulder')
    elbow = cmds.joint(p=(12 + ox, 0 + oy, 0 + oz), name=f'{prefix}_elbow')
    wrist = cmds.joint(p=(24 + ox, 0 + oy, 0 + oz), name=f'{prefix}_wrist')
    cmds.select(elbow, replace=True)
    twist_a = cmds.joint(p=(16 + ox, 5 + oy, 0 + oz), name=f'{prefix}_twist_a')
    cmds.select(elbow, replace=True)
    twist_b = cmds.joint(p=(20 + ox, 5 + oy, 0 + oz), name=f'{prefix}_twist_b')
    cmds.select(elbow, replace=True)
    ikhand = cmds.joint(p=(24 + ox, 5 + oy, 0 + oz), name=f'{prefix}_ikhand')
    cmds.select(clear=True)
    return shoulder, elbow, wrist, twist_a, twist_b, ikhand


def test_armature_live_follow_rules_evaluate_on_ctrl_move():
    """Build a REAL armature over the fixture; move the wrist ARMATURE
    CTRL via cmds (simulating a drag); assert the twists re-sit at exact
    fractions between the POSED elbow-wrist and the match joint snaps
    position+orientation — WITHOUT calling follow_rules.evaluate()
    manually. The armature itself must trigger it."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr
    from maya_tools.rigging.fabricator import armature

    cmds.file(new=True, force=True)
    shoulder, elbow, wrist, twist_a, twist_b, ikhand = (
        _make_live_armature_fixture('livetest'))

    fr.set_follow_rule(twist_a, 'distribute', (elbow, wrist), t=1.0 / 3.0)
    fr.set_follow_rule(twist_b, 'distribute', (elbow, wrist), t=2.0 / 3.0)
    fr.set_follow_rule(ikhand, 'match', (wrist,))

    armature.build_armature(root=shoulder)

    wrist_ctrl = f'{wrist}_amt_CTL'
    assert cmds.objExists(wrist_ctrl), 'wrist should still get a normal ctrl'
    assert not cmds.objExists(f'{twist_a}_amt_CTL'), (
        'a ruled joint must not get its own ctrl')
    assert not cmds.objExists(f'{ikhand}_amt_CTL'), (
        'a ruled joint must not get its own ctrl')

    # Simulate the drag: move the wrist ctrl. No follow_rules.evaluate()
    # call anywhere in this test — the armature must trigger it.
    cmds.xform(wrist_ctrl, ws=True, relative=True, t=(3.0, -7.0, 2.0))

    p_elbow = cmds.xform(elbow, q=True, ws=True, t=True)
    p_wrist = cmds.xform(wrist, q=True, ws=True, t=True)
    # The ctrl move must have actually landed the wrist joint on the
    # ctrl (SC-IK + stretch) -- otherwise this test would pass for the
    # wrong reason (twists sitting at the OLD elbow/wrist fractions).
    p_wrist_ctrl = cmds.xform(wrist_ctrl, q=True, ws=True, t=True)
    for i in range(3):
        assert abs(p_wrist[i] - p_wrist_ctrl[i]) < 1e-4, (
            'fixture sanity: wrist joint did not follow its ctrl')

    for jnt, frac in ((twist_a, 1.0 / 3.0), (twist_b, 2.0 / 3.0)):
        expected = [p_elbow[i] + (p_wrist[i] - p_elbow[i]) * frac
                    for i in range(3)]
        got = cmds.xform(jnt, q=True, ws=True, t=True)
        for i in range(3):
            assert abs(got[i] - expected[i]) < 1e-4, (
                f'{jnt}: expected {expected}, got {got} (frac={frac}) — '
                f'the armature did not trigger follow_rules.evaluate() '
                f'on the ctrl move')

    got_pos = cmds.xform(ikhand, q=True, ws=True, t=True)
    for i in range(3):
        assert abs(got_pos[i] - p_wrist[i]) < 1e-4
    got_mat = list(cmds.xform(ikhand, q=True, ws=True, matrix=True))
    wrist_mat = list(cmds.xform(wrist, q=True, ws=True, matrix=True))
    for i in range(16):
        assert abs(got_mat[i] - wrist_mat[i]) < 1e-4, (
            f'match joint did not re-orient live at index {i}: '
            f'{got_mat} vs {wrist_mat}')


def test_armature_no_rules_regression():
    """An armature over an UNRULED skeleton must behave identically with
    the Task 1.2 hook in place: capture a full transform snapshot before
    and after a ctrl move (no rules exist anywhere), and separately
    assert the live-follow callback path early-outs (call-count guard on
    follow_rules.evaluate) rather than silently doing scene work."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr
    from maya_tools.rigging.fabricator import armature

    cmds.file(new=True, force=True)
    shoulder, elbow, wrist, twist_a, twist_b, ikhand = (
        _make_live_armature_fixture('noruletest'))
    # Deliberately NO follow rules anywhere in this scene.

    armature.build_armature(root=shoulder)

    calls = {'n': 0}
    real_evaluate = fr.evaluate

    def _counting_evaluate(joints=None):
        calls['n'] += 1
        return real_evaluate(joints)

    fr.evaluate = _counting_evaluate
    try:
        wrist_ctrl = f'{wrist}_amt_CTL'
        before = {
            j: cmds.xform(j, q=True, ws=True, t=True)
            for j in (shoulder, elbow, wrist, twist_a, twist_b, ikhand)
        }
        cmds.xform(wrist_ctrl, ws=True, relative=True, t=(5.0, 1.0, -2.0))
        after = {
            j: cmds.xform(j, q=True, ws=True, t=True)
            for j in (shoulder, elbow, wrist, twist_a, twist_b, ikhand)
        }
    finally:
        fr.evaluate = real_evaluate

    # The unruled siblings (twist_a/b, ikhand normally-shaped ctrls in a
    # no-rules scene) must not have been touched by anything follow-rule
    # related; only the ctrl move's own native DG (SC-IK / elbow's
    # pointConstraint-preserved children) is allowed to touch them, and
    # only within ordinary FP re-solve noise (elbow's rotate changes to
    # aim at the moved wrist ctrl, and each sibling's OWN pointConstraint
    # re-solves its local translate to hold world position through
    # that — a ~1e-14 FP wobble, not a real move).
    def _same(a, b, tol=1e-6):
        return all(abs(a[i] - b[i]) < tol for i in range(3))

    assert _same(before[shoulder], after[shoulder])
    assert _same(before[twist_a], after[twist_a]), (before[twist_a], after[twist_a])
    assert _same(before[twist_b], after[twist_b]), (before[twist_b], after[twist_b])
    assert _same(before[ikhand], after[ikhand]), (before[ikhand], after[ikhand])

    # The hook must have been reached (registered) but early-out cheaply
    # -- it must NEVER call follow_rules.evaluate() when no rules exist
    # anywhere in the scene.
    assert calls['n'] == 0, (
        f'follow_rules.evaluate() was called {calls["n"]}x on a ctrl '
        f'move with zero rules in the scene -- the live hook must '
        f'early-out on an empty ruled-joint cache, not call evaluate()')


def test_armature_rule_created_after_build_still_live():
    """A rule created AFTER the armature already exists must still
    evaluate on the NEXT ctrl move (cache invalidation)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr
    from maya_tools.rigging.fabricator import armature

    cmds.file(new=True, force=True)
    shoulder, elbow, wrist, twist_a, twist_b, ikhand = (
        _make_live_armature_fixture('postbuildtest'))

    # Build with NO rules yet.
    armature.build_armature(root=shoulder)

    # Rules created AFTER the Armature already exists.
    fr.set_follow_rule(twist_a, 'distribute', (elbow, wrist), t=0.5)

    wrist_ctrl = f'{wrist}_amt_CTL'
    cmds.xform(wrist_ctrl, ws=True, relative=True, t=(2.0, 4.0, -1.0))

    p_elbow = cmds.xform(elbow, q=True, ws=True, t=True)
    p_wrist = cmds.xform(wrist, q=True, ws=True, t=True)
    expected = [p_elbow[i] + (p_wrist[i] - p_elbow[i]) * 0.5
                for i in range(3)]
    got = cmds.xform(twist_a, q=True, ws=True, t=True)
    for i in range(3):
        assert abs(got[i] - expected[i]) < 1e-4, (
            f'rule created after the armature already existed did not '
            f'evaluate on the next ctrl move (cache invalidation): '
            f'expected {expected}, got {got}')


def test_armature_rule_created_after_build_on_nonleaf_preserves_child_ctrls():
    """A rule created on a NON-LEAF joint after the Armature already
    exists must not cascade-delete that joint's descendant ctrls.

    The ctrl tree mirrors the joint tree one-to-one (_make_ctrl parents
    every ctrl under its PARENT joint's ctrl), so elbow's ctrl is the
    Maya parent of wrist's ctrl -- and, in this fixture, of twist_a's,
    twist_b's and ikhand's ctrls too (all four are elbow's children).
    Ruling elbow post-build must reparent those descendant ctrls up to
    elbow's own former parent (the armature group here) BEFORE deleting
    elbow's own ctrl, not take the whole subtree down with it."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr
    from maya_tools.rigging.fabricator import armature

    cmds.file(new=True, force=True)
    shoulder, elbow, wrist, twist_a, twist_b, ikhand = (
        _make_live_armature_fixture('nonleaftest'))
    cmds.select(clear=True)
    anchor = _make_offset_joint('nonleaftest_anchor', (99.0, 4.0, -6.0))
    cmds.select(clear=True)

    # Build with NO rules yet -- elbow, wrist, twist_a, twist_b and
    # ikhand all get real ctrls; elbow's ctrl is the Maya parent of the
    # other four.
    armature.build_armature(root=shoulder)

    elbow_ctrl = f'{elbow}_amt_CTL'
    wrist_ctrl = f'{wrist}_amt_CTL'
    twist_a_ctrl = f'{twist_a}_amt_CTL'
    twist_b_ctrl = f'{twist_b}_amt_CTL'
    ikhand_ctrl = f'{ikhand}_amt_CTL'
    child_ctrls = (wrist_ctrl, twist_a_ctrl, twist_b_ctrl, ikhand_ctrl)

    for c in (elbow_ctrl,) + child_ctrls:
        assert cmds.objExists(c), f'fixture sanity: {c} missing at build'
    for c in child_ctrls:
        par = cmds.listRelatives(c, parent=True) or [None]
        assert par[0] == elbow_ctrl, (
            f'fixture sanity: {c} must be parented under {elbow_ctrl} '
            f'before the retrofit (ctrl tree mirrors joint tree)')

    before_pos = {c: cmds.xform(c, q=True, ws=True, t=True)
                  for c in child_ctrls}

    # Rule created AFTER the Armature already exists, on a NON-LEAF
    # joint (elbow has four ctrl children in this fixture).
    fr.set_follow_rule(elbow, 'match', (anchor,))

    assert not cmds.objExists(elbow_ctrl), (
        'elbow gained a follow rule post-build -- its own ctrl must be '
        'retired')
    for c in child_ctrls:
        assert cmds.objExists(c), (
            f"{c} was cascade-deleted along with its former parent "
            f"elbow's ctrl -- _follow_retrofit_joint must reparent "
            f"descendant ctrls before deleting a non-leaf ruled "
            f"joint's own ctrl")

    # Reparented to elbow's own former parent (the armature group,
    # since shoulder -- elbow's joint parent -- is the root and gets
    # no ctrl).
    for c in child_ctrls:
        par = cmds.listRelatives(c, parent=True) or [None]
        assert par[0] == armature._GRP, (
            f'{c} should have been reparented to the armature group '
            f'after the rescue, got {par[0]!r}')

    # World position preserved through the rescue-reparent (cmds.parent
    # keeps world transform by default).
    for c in child_ctrls:
        got = cmds.xform(c, q=True, ws=True, t=True)
        exp = before_pos[c]
        for i in range(3):
            assert abs(got[i] - exp[i]) < 1e-5, (
                f'{c} moved during the rescue-reparent: {exp} -> {got}')

    # wrist's own SC-IK edge (its ikHandle is parented under wrist's own
    # ctrl, untouched by elbow's retrofit) must still be intact --
    # dragging wrist's ctrl still lands the wrist joint on it.
    cmds.xform(wrist_ctrl, ws=True, relative=True, t=(3.0, -2.0, 1.0))
    p_wrist = cmds.xform(wrist, q=True, ws=True, t=True)
    p_wrist_ctrl = cmds.xform(wrist_ctrl, q=True, ws=True, t=True)
    for i in range(3):
        assert abs(p_wrist[i] - p_wrist_ctrl[i]) < 1e-4, (
            "wrist's SC-IK edge broke after the elbow retrofit")


# ─────────────────────────────────────────────────────────────────────────
# Adrian's live repro, 2026-07-09: a REAL UE-style arm (upperarm_l ->
# lowerarm_l -> hand_l) with TWO sibling twist joints parented to
# lowerarm_l and sitting exactly ON its aim axis (real UE twist bones sit
# on the straight line from elbow to wrist, unlike _make_live_armature_
# fixture's deliberate Y-offset, which sidesteps aim-axis ambiguity on
# purpose). set_follow_rule('lowerarm_twist_01_l', 'distribute',
# (lowerarm_l, hand_l), t=1/3) + the same for twist_02_l at t=2/3 — BOTH
# ruled, BOTH targeting their own shared parent as A — produced "a ton of
# cycle warnings, then it broke" on the retrofit ordering (rules set
# AFTER the Armature already exists).
#
# Root cause (see armature.py's "Mirror-image case" note on
# _follow_retrofit_joint): on-axis geometry makes ONE of the two twist
# joints — not hand_l — win lowerarm_l's aimer classification at
# first-build aimer-seed time (Maya's listRelatives order breaks the
# geometric tie). That twist joint gets the real SC-IK aim edge (its
# ikHandle, startJoint=lowerarm_l, lives under ITS OWN ctrl); hand_l and
# the other twist fall back to plain translate-only ctrls. Retrofitting
# that ONE twist joint (tearing down its ctrl because it's now
# follow-ruled) destroys the ONLY thing driving lowerarm_l's rotation —
# confirmed via mayapy repro: lowerarm_l's rotate froze at its build-time
# value and never budged again across further ctrl drags, silently (no
# exception) — "it broke". _follow_retrofit_reaim_parent (Task 1.2
# follow-up) now re-detects a valid replacement aim child among
# lowerarm_l's REMAINING non-ruled children and rewires the edge to it.
#
# The literal Maya cycle-warning TEXT could not be captured here —
# mayapy/batch is documented elsewhere in this codebase (_limb_common.py)
# as unable to surface it even for a directly forced DG cycle
# (cmds.cycleCheck(query=True, evaluation=True) stays False in batch
# regardless of how it's set) — so these fixtures assert the batch-
# visible fingerprint of the SAME class of bug instead: no messages
# containing "cycle" (the channel that WOULD carry them if Maya's
# evaluator ever did emit one), the arm never silently freezing (the
# parent's rotate must visibly respond to further drags once the
# orphaned edge is rewired), and clean re-spacing (including a stretch
# test) across 3 consecutive drags.
# ─────────────────────────────────────────────────────────────────────────

def _make_ue_twin_arm_fixture(prefix, exact_colinear=True):
    """upperarm_l -> lowerarm_l -> hand_l (a real Maya parent/child UE-
    named chain) + lowerarm_twist_01_l/lowerarm_twist_02_l as SIBLINGS
    parented to lowerarm_l, sitting ON lowerarm_l's aim axis at t=1/3
    and 2/3 toward hand_l — real UE twist-bone geometry, off-origin
    (studio deformer-order lesson).

    exact_colinear=True (default): twists sit EXACTLY on the axis —
    a genuine three-way geometric TIE for lowerarm_l's aim-child slot
    (hand_l and both twists all score an identical dot product), which
    joint_orient_app._detect_aim_child_signed's '>=' comparison breaks
    arbitrarily by Maya's listRelatives iteration order rather than
    consistently toward hand_l. This is Adrian's actual broken repro
    shape: real bind-pose UE forearms genuinely place hand_l and the
    twist bones exactly colinear, so the tie is not a fixture
    contrivance.

    exact_colinear=False: twists carry a tiny (0.05 unit) Y nudge —
    ordinary floating-point noise real DCC-exported skeletons always
    carry even when visually "on the line" — which keeps them
    comfortably within the aim tolerance (well under 0.75 degrees at
    this bone length) while making hand_l's dot product strictly
    highest, so it wins lowerarm_l's aim-child slot DETERMINISTICALLY
    regardless of iteration order. Used by the build-first regression
    fixture (confirmed already-working, per Adrian's live validation)
    so that guard doesn't itself ride on an arbitrary tie-break."""
    import maya.cmds as cmds
    ox, oy, oz = _OFFSET
    twist_y = 0.0 if exact_colinear else 0.05
    cmds.select(clear=True)
    upperarm = cmds.joint(p=(0 + ox, 0 + oy, 0 + oz), name=f'{prefix}upperarm_l')
    lowerarm = cmds.joint(p=(15 + ox, 0 + oy, 0 + oz), name=f'{prefix}lowerarm_l')
    hand = cmds.joint(p=(30 + ox, 0 + oy, 0 + oz), name=f'{prefix}hand_l')
    cmds.select(lowerarm, replace=True)
    twist01 = cmds.joint(p=(20 + ox, twist_y + oy, 0 + oz),
                         name=f'{prefix}lowerarm_twist_01_l')
    cmds.select(lowerarm, replace=True)
    twist02 = cmds.joint(p=(25 + ox, twist_y + oy, 0 + oz),
                         name=f'{prefix}lowerarm_twist_02_l')
    cmds.select(clear=True)
    return upperarm, lowerarm, hand, twist01, twist02


def _capture_command_output(fn):
    """Same channel Script Editor/native cycle warnings route through
    (MGlobal::displayWarning) — the established pattern from
    _dev/test_ik_arm_maya.py's test_ikarm_pv_ctrl_aim_no_cycle_and_
    unbuild_clean."""
    import maya.api.OpenMaya as om2
    captured = []

    def _on_output(message, msg_type, client_data):
        captured.append(message)

    cb_id = om2.MCommandMessage.addCommandOutputCallback(_on_output)
    try:
        fn()
    finally:
        om2.MMessage.removeCallback(cb_id)
    return captured


def _assert_twists_respace(lowerarm, hand, twist01, twist02, label):
    import maya.cmds as cmds
    p_low = cmds.xform(lowerarm, q=True, ws=True, t=True)
    p_hand = cmds.xform(hand, q=True, ws=True, t=True)
    for jnt, frac in ((twist01, 1.0 / 3), (twist02, 2.0 / 3)):
        exp = [p_low[i] + (p_hand[i] - p_low[i]) * frac for i in range(3)]
        got = cmds.xform(jnt, q=True, ws=True, t=True)
        for i in range(3):
            assert abs(got[i] - exp[i]) < 1e-3, (
                f'{label}: {jnt} expected {exp}, got {got} (frac={frac})')


def _assert_arm_still_live(lowerarm_rot_before, lowerarm, label):
    """The discriminating check for the retrofit bug: a frozen/orphaned
    lowerarm_l rotate is a SILENT break (no exception, no warning) --
    the distribute math alone can look correct even with a dead elbow
    (A stays fixed, only B moves). This must actually change across
    real drags, proving lowerarm_l's aim edge is genuinely live, not
    just mathematically self-consistent against a frozen A."""
    import maya.cmds as cmds
    after = cmds.getAttr(f'{lowerarm}.rotate')[0]
    assert after != lowerarm_rot_before, (
        f'{label}: {lowerarm}.rotate never moved off {lowerarm_rot_before} '
        f'-- the elbow is frozen/orphaned (the exact "it broke" symptom)')


def test_ue_twin_arm_retrofit_after_build_no_cycle_and_stays_live():
    """Adrian's exact repro, retrofit ordering (armature built FIRST,
    rules set AFTER -- the ordering confirmed broken): no cycle-shaped
    warnings, the arm never freezes, and both twists re-space cleanly
    (including a stretch test) across 3 consecutive hand-ctrl drags."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr
    from maya_tools.rigging.fabricator import armature

    cmds.file(new=True, force=True)
    upperarm, lowerarm, hand, twist01, twist02 = (
        _make_ue_twin_arm_fixture('retrouetest_'))

    cap = _capture_command_output(lambda: armature.build_armature(root=upperarm))
    cyc = [m for m in cap if 'cycle' in m.lower()]
    assert not cyc, f'cycle warning(s) during build: {cyc}'

    hand_ctrl = f'{hand}_amt_CTL'
    assert cmds.objExists(hand_ctrl), 'fixture sanity: hand should get a ctrl'
    # No rules exist yet at this point (retrofit ordering) -- both
    # twists are perfectly ordinary joints and DO get a normal ctrl
    # each (one of the two, per real UE on-axis geometry, may in fact
    # be the one that wins lowerarm_l's aim classification over hand_l
    # -- see this section's header comment; either shape is fixture-
    # valid here, only ctrl PRESENCE is asserted).
    assert cmds.objExists(f'{twist01}_amt_CTL'), (
        'fixture sanity: twist_01 should have a normal ctrl before any '
        'rule exists')
    assert cmds.objExists(f'{twist02}_amt_CTL'), (
        'fixture sanity: twist_02 should have a normal ctrl before any '
        'rule exists')

    def _rule01():
        fr.set_follow_rule(twist01, 'distribute', (lowerarm, hand), t=1.0 / 3)

    def _rule02():
        fr.set_follow_rule(twist02, 'distribute', (lowerarm, hand), t=2.0 / 3)

    cap = _capture_command_output(_rule01)
    cyc = [m for m in cap if 'cycle' in m.lower()]
    assert not cyc, f'cycle warning(s) retrofitting twist_01: {cyc}'

    cap = _capture_command_output(_rule02)
    cyc = [m for m in cap if 'cycle' in m.lower()]
    assert not cyc, f'cycle warning(s) retrofitting twist_02: {cyc}'

    assert not cmds.objExists(f'{twist01}_amt_CTL'), (
        'a ruled joint must not keep its own ctrl after retrofit')
    assert not cmds.objExists(f'{twist02}_amt_CTL'), (
        'a ruled joint must not keep its own ctrl after retrofit')
    assert cmds.objExists(hand_ctrl), (
        "hand's ctrl must survive the twist siblings' retrofit")

    lowerarm_rot_before = cmds.getAttr(f'{lowerarm}.rotate')[0]

    for i, delta in enumerate(
            [(3.0, -2.0, 1.5), (-1.0, 4.0, -2.0), (2.0, -1.0, 5.0)], 1):
        cap = _capture_command_output(
            lambda d=delta: cmds.xform(hand_ctrl, ws=True, relative=True, t=d))
        cyc = [m for m in cap if 'cycle' in m.lower()]
        assert not cyc, f'cycle warning(s) on drag {i}: {cyc}'
        _assert_twists_respace(lowerarm, hand, twist01, twist02,
                               label=f'drag {i}')

    # The discriminating check: lowerarm_l must have genuinely
    # reoriented across those drags (a rewired, LIVE aim edge), not
    # just held a frozen A while B moved (a "correct-looking" but dead
    # elbow -- the exact silent break _follow_retrofit_reaim_parent
    # fixes).
    _assert_arm_still_live(lowerarm_rot_before, lowerarm, label='post-retrofit drags')

    # Stretch test: pull the hand far away -- distribute joints must
    # re-space along the NEW, much longer elbow-wrist span (proving live
    # re-lerp, not a stale fixed offset).
    cap = _capture_command_output(
        lambda: cmds.xform(hand_ctrl, ws=True, relative=True, t=(80.0, 40.0, -30.0)))
    cyc = [m for m in cap if 'cycle' in m.lower()]
    assert not cyc, f'cycle warning(s) on the stretch drag: {cyc}'
    _assert_twists_respace(lowerarm, hand, twist01, twist02, label='stretch test')


def test_ue_twin_arm_rules_before_build_no_cycle_and_stays_live():
    """Same exact fixture, OTHER ordering: rules authored on plain
    joints, THEN the Armature is built (confirmed-working per Adrian's
    live validation) -- kept as a permanent regression guard so a future
    change can't quietly break the ordering that already works while
    fixing the one that didn't."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr
    from maya_tools.rigging.fabricator import armature

    cmds.file(new=True, force=True)
    upperarm, lowerarm, hand, twist01, twist02 = (
        _make_ue_twin_arm_fixture('buildfirsttest_', exact_colinear=False))

    fr.set_follow_rule(twist01, 'distribute', (lowerarm, hand), t=1.0 / 3)
    fr.set_follow_rule(twist02, 'distribute', (lowerarm, hand), t=2.0 / 3)

    cap = _capture_command_output(lambda: armature.build_armature(root=upperarm))
    cyc = [m for m in cap if 'cycle' in m.lower()]
    assert not cyc, f'cycle warning(s) during build: {cyc}'

    hand_ctrl = f'{hand}_amt_CTL'
    assert cmds.objExists(hand_ctrl), 'fixture sanity: hand should get a ctrl'
    assert not cmds.objExists(f'{twist01}_amt_CTL')
    assert not cmds.objExists(f'{twist02}_amt_CTL')

    lowerarm_rot_before = cmds.getAttr(f'{lowerarm}.rotate')[0]

    for i, delta in enumerate(
            [(3.0, -2.0, 1.5), (-1.0, 4.0, -2.0), (2.0, -1.0, 5.0)], 1):
        cap = _capture_command_output(
            lambda d=delta: cmds.xform(hand_ctrl, ws=True, relative=True, t=d))
        cyc = [m for m in cap if 'cycle' in m.lower()]
        assert not cyc, f'cycle warning(s) on drag {i}: {cyc}'
        _assert_twists_respace(lowerarm, hand, twist01, twist02,
                               label=f'drag {i}')

    _assert_arm_still_live(lowerarm_rot_before, lowerarm, label='build-first drags')

    cap = _capture_command_output(
        lambda: cmds.xform(hand_ctrl, ws=True, relative=True, t=(80.0, 40.0, -30.0)))
    cyc = [m for m in cap if 'cycle' in m.lower()]
    assert not cyc, f'cycle warning(s) on the stretch drag: {cyc}'
    _assert_twists_respace(lowerarm, hand, twist01, twist02, label='stretch test')


# ─────────────────────────────────────────────────────────────────────────
# P1 REGRESSION, Adrian's LIVE GUI repro 2026-07-09: "a ton of cycle
# warnings, then it broke" was actually TWO bugs sharing one root cause.
# armature_watch's delete-sync (armature_watch.py) is only muted during
# build_armature/delete_armature (_suspends_armature_watch); Task 1.2's
# retrofit path (_follow_retrofit_joint, reached from set_follow_rule via
# follow_rules._notify_change -> armature._follow_cache_dirty) called
# cmds.delete(ctrl) UNSUSPENDED. armature_watch read that programmatic
# ctrl delete as a user branch-delete gesture and, once Maya went idle,
# ran branch_ops.delete_branches() on the very joint that had just been
# ruled -- which (a) actually deleted the ruled twist joints (the literal
# "# Delete: removed lowerarm_twist_01_l, lowerarm_twist_02_l -- 2
# joint(s), 0 module(s)." from Adrian's Script Editor) and (b), because
# delete_branches() always wraps a live Armature in its own
# delete_armature() + build_armature() pair, fired a COMPLETE re-entrant
# teardown/rebuild of every ctrl/SC-IK/pointConstraint in the whole rig
# mid-retrofit -- which is why the cycle warnings covered essentially
# every joint (fingers, spine, neck, legs), not just the two twists.
#
# Verified directly against a live mayapy process (not asserted from
# theory): with the suspend guard removed, cmds.delete(ctrl) inside
# _follow_retrofit_joint synchronously re-enters armature_watch's
# _on_node_removed -> _schedule_flush -> cmds.evalDeferred(_flush) --
# and Maya 2025.3.2's evalDeferred runs that callback IMMEDIATELY in
# mayapy batch (there is no GUI idle loop to defer to; confirmed by
# printing markers either side of the evalDeferred call -- the deferred
# callback prints BEFORE the "scheduled" line does). So batch was never
# blind because deferred events don't flush -- it was blind because
# armature_watch.install() is only ever called from fs_window.py (the
# live GUI) and no test in this file installed it, so the watcher's DAG
# callbacks were never registered in the first place and had nothing to
# race regardless of evalDeferred timing. _pump_deferred_queue below is
# kept as a defensive net against a FUTURE Maya version reintroducing
# true deferral in batch, not because this Maya version needs it.
# ─────────────────────────────────────────────────────────────────────────

def _pump_deferred_queue():
    """Force any pending armature_watch idle-flush to run right now.

    Empirically a no-op against Maya 2025.3.2 (its evalDeferred already
    runs synchronously in mayapy batch -- see the header comment above).
    Kept anyway: reaches into armature_watch's private flush state so
    that if a future Maya version restores real deferral in batch, a
    pending flush still gets forced here instead of the whole battery
    silently going blind again the way it did before this fix existed.
    """
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import armature_watch as aw
    cmds.evalDeferred('pass', lowestPriority=True)
    if aw._STATE.get('flush_scheduled'):
        aw._flush()


def _make_full_skeleton_fixture(prefix):
    """A FULLER skeleton than _make_ue_twin_arm_fixture: spine chain
    (pelvis -> spine_01..03 -> neck -> head), BOTH arms off spine_03
    (only the LEFT carries the twist siblings, on-axis, exact_colinear
    -- the same real broken geometry as _make_ue_twin_arm_fixture), a
    2-chain hand (index + middle, 3 joints each) off hand_l, and both
    legs off pelvis (thigh -> calf -> foot). Matches the surface of
    Adrian's actual UE mannequin repro (fingers both sides in spirit,
    spine_01..0N, calf/foot both sides) closely enough to reproduce the
    "essentially every joint" cycle-warning storm shape, while staying
    small enough to build/assert quickly.

    Returns a dict keyed by role name -> joint name.
    """
    import maya.cmds as cmds
    ox, oy, oz = _OFFSET

    def j(name, pos, parent=None):
        if parent is not None:
            cmds.select(parent, replace=True)
        else:
            cmds.select(clear=True)
        x, y, z = pos
        return cmds.joint(p=(x + ox, y + oy, z + oz), name=name)

    p = f'{prefix}'
    joints = {}
    joints['pelvis'] = j(f'{p}pelvis', (0, 0, 0))
    joints['spine_01'] = j(f'{p}spine_01', (0, 5, 0), joints['pelvis'])
    joints['spine_02'] = j(f'{p}spine_02', (0, 10, 0), joints['spine_01'])
    joints['spine_03'] = j(f'{p}spine_03', (0, 15, 0), joints['spine_02'])
    joints['neck_01'] = j(f'{p}neck_01', (0, 20, 0), joints['spine_03'])
    joints['head'] = j(f'{p}head', (0, 24, 0), joints['neck_01'])

    # Left arm: real UE twist-bone geometry (exact on-axis colinear tie,
    # same scale as _make_ue_twin_arm_fixture: upperarm->lowerarm=15,
    # lowerarm->hand=15, twists at +5/+10 from lowerarm).
    joints['clavicle_l'] = j(f'{p}clavicle_l', (2, 15, 0), joints['spine_03'])
    joints['upperarm_l'] = j(f'{p}upperarm_l', (5, 15, 0), joints['clavicle_l'])
    joints['lowerarm_l'] = j(f'{p}lowerarm_l', (20, 15, 0), joints['upperarm_l'])
    joints['hand_l'] = j(f'{p}hand_l', (35, 15, 0), joints['lowerarm_l'])
    joints['lowerarm_twist_01_l'] = j(
        f'{p}lowerarm_twist_01_l', (25, 15, 0), joints['lowerarm_l'])
    joints['lowerarm_twist_02_l'] = j(
        f'{p}lowerarm_twist_02_l', (30, 15, 0), joints['lowerarm_l'])

    # Right arm: plain, no twists -- part of "the rest of the armature"
    # that must come out untouched.
    joints['clavicle_r'] = j(f'{p}clavicle_r', (-2, 15, 0), joints['spine_03'])
    joints['upperarm_r'] = j(f'{p}upperarm_r', (-5, 15, 0), joints['clavicle_r'])
    joints['lowerarm_r'] = j(f'{p}lowerarm_r', (-20, 15, 0), joints['upperarm_r'])
    joints['hand_r'] = j(f'{p}hand_r', (-35, 15, 0), joints['lowerarm_r'])

    # 2 finger chains off hand_l, 3 joints each.
    joints['index_01_l'] = j(f'{p}index_01_l', (37, 16, 0), joints['hand_l'])
    joints['index_02_l'] = j(f'{p}index_02_l', (39, 16, 0), joints['index_01_l'])
    joints['index_03_l'] = j(f'{p}index_03_l', (41, 16, 0), joints['index_02_l'])
    joints['middle_01_l'] = j(f'{p}middle_01_l', (37, 15, -1), joints['hand_l'])
    joints['middle_02_l'] = j(f'{p}middle_02_l', (39, 15, -1), joints['middle_01_l'])
    joints['middle_03_l'] = j(f'{p}middle_03_l', (41, 15, -1), joints['middle_02_l'])

    # Both legs off pelvis.
    joints['thigh_l'] = j(f'{p}thigh_l', (3, 0, 0), joints['pelvis'])
    joints['calf_l'] = j(f'{p}calf_l', (3, -15, 0), joints['thigh_l'])
    joints['foot_l'] = j(f'{p}foot_l', (3, -30, 0), joints['calf_l'])
    joints['thigh_r'] = j(f'{p}thigh_r', (-3, 0, 0), joints['pelvis'])
    joints['calf_r'] = j(f'{p}calf_r', (-3, -15, 0), joints['thigh_r'])
    joints['foot_r'] = j(f'{p}foot_r', (-3, -30, 0), joints['calf_r'])

    cmds.select(clear=True)
    return joints


def test_full_skeleton_twist_retrofit_survives_armature_watch_race():
    """Adrian's exact GUI shape at full scale: a live Armature over a
    FULL skeleton (spine, both arms, a 2-chain hand, both legs), then
    BOTH lowerarm_l twists ruled AFTER the build with armature_watch
    actually INSTALLED (the condition every prior test in this file
    missed -- fs_window.py is the only installer, so no earlier test
    here ever gave armature_watch a live callback to race with).

    RED before the fix (verified live against this exact fixture, not
    asserted from theory): both twist joints get deleted by
    armature_watch's delete-sync misreading _follow_retrofit_joint's own
    cmds.delete(ctrl) as a user branch delete, and _follow_retrofit_joint
    then crashes (RuntimeError: setAttr ... no object matches name) when
    it goes on to unlock translate channels on a joint that watch just
    deleted out from under it. GREEN after: joints survive, no crash, no
    Delete: report, no cycle-shaped command output, the elbow stays
    genuinely live across drags (not a frozen-but-correct-looking edge),
    twists re-space including a stretch test, and every joint OUTSIDE
    the retrofitted branch (spine, fingers, right arm, both legs) is
    untouched in position AND still functionally drivable by its ctrl.
    """
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr
    from maya_tools.rigging.fabricator import armature
    from maya_tools.rigging.fabricator import armature_watch as aw
    from maya_tools.rigging.fabricator import nodes

    cmds.file(new=True, force=True)
    J = _make_full_skeleton_fixture('fullskeltest_')
    nodes.create_registry('fullskeltest_bp')
    nodes.set_registry_root_joint(J['pelvis'])

    cap = _capture_command_output(
        lambda: armature.build_armature(root=J['pelvis']))
    cyc = [m for m in cap if 'cycle' in m.lower()]
    assert not cyc, f'cycle warning(s) during build: {cyc}'

    twist01, twist02 = J['lowerarm_twist_01_l'], J['lowerarm_twist_02_l']
    lowerarm, hand = J['lowerarm_l'], J['hand_l']

    # "The rest of the armature" untouched-and-functional witnesses:
    # every joint OUTSIDE the twist branch. Snapshot ctrl world position
    # now, before either rule is set.
    untouched_roles = [
        'spine_01', 'spine_02', 'spine_03', 'neck_01', 'head',
        'clavicle_l', 'upperarm_l', 'hand_l',
        'clavicle_r', 'upperarm_r', 'lowerarm_r', 'hand_r',
        'index_01_l', 'index_02_l', 'index_03_l',
        'middle_01_l', 'middle_02_l', 'middle_03_l',
        'thigh_l', 'calf_l', 'foot_l', 'thigh_r', 'calf_r', 'foot_r',
    ]
    untouched_ctrls = {role: f'{J[role]}_amt_CTL' for role in untouched_roles}
    for role, ctrl in untouched_ctrls.items():
        assert cmds.objExists(ctrl), f'fixture sanity: missing ctrl for {role}'
    before_pos = {
        role: cmds.xform(ctrl, q=True, ws=True, t=True)
        for role, ctrl in untouched_ctrls.items()
    }

    reports = []
    aw.set_reporter(lambda m: reports.append(m))
    aw.install()
    try:
        def _rule01():
            fr.set_follow_rule(twist01, 'distribute', (lowerarm, hand), t=1.0 / 3)

        def _rule02():
            fr.set_follow_rule(twist02, 'distribute', (lowerarm, hand), t=2.0 / 3)

        cap = _capture_command_output(_rule01)
        _pump_deferred_queue()
        cyc = [m for m in cap if 'cycle' in m.lower()]
        assert not cyc, f'cycle warning(s) retrofitting twist_01: {cyc}'

        cap = _capture_command_output(_rule02)
        _pump_deferred_queue()
        cyc = [m for m in cap if 'cycle' in m.lower()]
        assert not cyc, f'cycle warning(s) retrofitting twist_02: {cyc}'

        deletes = [m for m in reports if m.startswith('Delete:')]
        assert not deletes, (
            f'armature_watch treated the retrofit as a user branch '
            f'delete: {deletes}')

        # THE red check: the ruled joints themselves must still exist.
        assert cmds.objExists(twist01), (
            f'{twist01} was deleted -- armature_watch raced the '
            f'retrofit\'s ctrl teardown')
        assert cmds.objExists(twist02), (
            f'{twist02} was deleted -- armature_watch raced the '
            f'retrofit\'s ctrl teardown')
        assert not cmds.objExists(f'{twist01}_amt_CTL'), (
            'a ruled joint must not keep its own ctrl after retrofit')
        assert not cmds.objExists(f'{twist02}_amt_CTL'), (
            'a ruled joint must not keep its own ctrl after retrofit')

        hand_ctrl = f'{hand}_amt_CTL'
        assert cmds.objExists(hand_ctrl), "hand's ctrl must survive"

        # Untouched-and-functional check #1: position. Snapshotted here,
        # right after the retrofit and before any hand-ctrl drag -- the
        # fingers and hand_l are legitimate DESCENDANTS of hand_l's ctrl
        # in the ctrl hierarchy (SPEC: ctrl tree mirrors joint tree) and
        # are EXPECTED to move once the drag loop below drags hand_ctrl;
        # what must NOT happen is any of them moving from the retrofit
        # itself, before any drag ever occurs.
        after_retrofit_pos = {
            role: cmds.xform(ctrl, q=True, ws=True, t=True)
            for role, ctrl in untouched_ctrls.items()
        }
        for role in untouched_roles:
            b, a = before_pos[role], after_retrofit_pos[role]
            for i in range(3):
                assert abs(b[i] - a[i]) < 1e-6, (
                    f'{role}\'s ctrl moved during the twist retrofit '
                    f'itself (nothing outside the retrofitted branch '
                    f'should ever move from ruling a sibling twist): '
                    f'{b} -> {a}')

        # Untouched-and-functional check #2: a spine ctrl and a finger
        # ctrl must still genuinely drive their joints (pointConstraint
        # intact), not just sit at an unchanged-but-dead position.
        # Also before any hand-ctrl drag, so index_02_l's baseline here
        # is still its pre-drag position.
        for role in ('spine_02', 'index_02_l'):
            ctrl = untouched_ctrls[role]
            jnt = J[role]
            cmds.xform(ctrl, ws=True, relative=True, t=(1.0, 0.5, -0.5))
            p_ctrl = cmds.xform(ctrl, q=True, ws=True, t=True)
            p_jnt = cmds.xform(jnt, q=True, ws=True, t=True)
            for i in range(3):
                assert abs(p_ctrl[i] - p_jnt[i]) < 1e-4, (
                    f'{role}: joint did not follow its ctrl after the '
                    f'twist retrofit -- the rest of the armature is no '
                    f'longer functional ({p_jnt} vs ctrl {p_ctrl})')

        # A spine ctrl NOT downstream of hand_l must also stay put while
        # the elbow/twist drags below happen (spine_03 is the arms'
        # shared parent -- the strongest witness that no rebuild is
        # sneaking in during the drag sequence either).
        spine_03_ctrl = untouched_ctrls['spine_03']
        spine_03_before_drags = cmds.xform(
            spine_03_ctrl, q=True, ws=True, t=True)

        lowerarm_rot_before = cmds.getAttr(f'{lowerarm}.rotate')[0]
        for i, delta in enumerate(
                [(3.0, -2.0, 1.5), (-1.0, 4.0, -2.0), (2.0, -1.0, 5.0)], 1):
            cap = _capture_command_output(
                lambda d=delta: cmds.xform(hand_ctrl, ws=True, relative=True, t=d))
            _pump_deferred_queue()
            cyc = [m for m in cap if 'cycle' in m.lower()]
            assert not cyc, f'cycle warning(s) on drag {i}: {cyc}'
            _assert_twists_respace(lowerarm, hand, twist01, twist02,
                                   label=f'drag {i}')
        _assert_arm_still_live(lowerarm_rot_before, lowerarm,
                               label='full-skeleton post-retrofit drags')

        cap = _capture_command_output(
            lambda: cmds.xform(hand_ctrl, ws=True, relative=True,
                              t=(80.0, 40.0, -30.0)))
        _pump_deferred_queue()
        cyc = [m for m in cap if 'cycle' in m.lower()]
        assert not cyc, f'cycle warning(s) on the stretch drag: {cyc}'
        _assert_twists_respace(lowerarm, hand, twist01, twist02,
                               label='full-skeleton stretch test')

        spine_03_after_drags = cmds.xform(
            spine_03_ctrl, q=True, ws=True, t=True)
        for i in range(3):
            assert abs(spine_03_before_drags[i] - spine_03_after_drags[i]) < 1e-6, (
                f'spine_03\'s ctrl moved while dragging the hand ctrl '
                f'through the retrofitted twists: {spine_03_before_drags} '
                f'-> {spine_03_after_drags}')
    finally:
        aw.remove()


def test_follow_retrofit_unsuspended_reproduces_deletion_regression_canary():
    """RED-before/GREEN-after proof that this battery actually exercises
    the failure mode, not a fixture that happens to pass either way.

    Bypasses armature._run_follow_retrofit's armature_watch.suspended()
    guard on purpose (calls the raw, pre-fix _follow_retrofit_joint
    directly, monkeypatched in for exactly one retrofit) and confirms
    the OLD bug still reproduces on demand: armature_watch's delete-sync
    reads the unsuspended cmds.delete(ctrl) as a user branch delete and
    removes the just-ruled joint. If this canary ever stops failing
    (i.e. the joint survives even WITHOUT the guard), the mechanism the
    rest of this battery relies on has changed out from under it and the
    green results above can no longer be trusted at face value.
    """
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr
    from maya_tools.rigging.fabricator import armature
    from maya_tools.rigging.fabricator import armature_watch as aw
    from maya_tools.rigging.fabricator import nodes

    cmds.file(new=True, force=True)
    upperarm, lowerarm, hand, twist01, twist02 = (
        _make_ue_twin_arm_fixture('canarytest_'))
    nodes.create_registry('canarytest_bp')
    nodes.set_registry_root_joint(upperarm)
    armature.build_armature(root=upperarm)

    aw.install()
    real_run_retrofit = armature._run_follow_retrofit
    try:
        armature._run_follow_retrofit = (
            lambda joint, rule: armature._follow_retrofit_joint(joint, rule))
        fr.set_follow_rule(twist01, 'distribute', (lowerarm, hand), t=1.0 / 3)
        _pump_deferred_queue()
    finally:
        armature._run_follow_retrofit = real_run_retrofit
        aw.remove()

    assert not cmds.objExists(twist01), (
        'regression canary did not reproduce: with the suspend guard '
        'bypassed, armature_watch should still misread the retrofit '
        'ctrl delete as a user branch delete and remove the joint -- '
        'if it survived anyway, this battery is not exercising the '
        'real failure mode and its green results are not meaningful')


# ─────────────────────────────────────────────────────────────────────────
# FollowJoint component linkage (Task 1.3, SPEC 3.1 rule source (c)):
# adding a FollowJoint targeting X seeds a linked match(X) rule; removing
# the component clears ONLY that linked rule; a target-option edit
# post-add re-seeds it; a rigger-authored rule is never clobbered either
# way.
# ─────────────────────────────────────────────────────────────────────────

def _add_follow_joint_component(component_id, joint, follow_target=''):
    from maya_tools.rigging.fabricator import nodes
    return nodes.create_component_node(
        component_id=component_id, component_type='FollowJoint',
        joints=[joint], parent_plug='', side='md',
        options={'follow_target': follow_target}, persisted={},
    )


def test_follow_joint_add_seeds_linked_match_rule():
    """Adding a FollowJoint component with follow_target already set
    seeds a linked match(target) rule on its own joint — the Edit-mode
    armature preview mirrors what the built rig will do."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    joint = _make_offset_joint('fjaddtest_joint', (0.0, 0.0, 0.0))
    target = _make_offset_joint('fjaddtest_target', (10.0, 0.0, 0.0))
    cmds.select(clear=True)

    nodes.create_registry('fjaddtest_bp')
    _add_follow_joint_component('fjaddtest_C0', joint, follow_target=target)

    rule = fr.get_follow_rule(joint)
    assert rule is not None, 'FollowJoint add did not seed a follow rule'
    assert rule['kind'] == 'match'
    assert rule['targets'] == [target]
    assert rule['linked'] is True, 'seeded rule must be linked=True'


def test_follow_joint_engages_standing_armature_immediately():
    """Derived-authoring polish (Adrian 2026-07-12): with an Armature
    STANDING, setting a FollowJoint's target must rebuild the Armature
    so the joint sheds its ctrl right away (rule + old ctrl must never
    coexist); clearing the target brings the ctrl back the same way."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes, armature
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = _make_offset_joint('fjarm_root', (0.0, 0.0, 0.0))
    cmds.select(root)
    joint = cmds.joint(name='fjarm_follower', p=(5.0, 0.0, 0.0))
    cmds.select(root)
    target = cmds.joint(name='fjarm_target', p=(0.0, 5.0, 0.0))
    cmds.select(clear=True)

    nodes.create_registry('fjarm_bp')
    nodes.set_registry_root_joint(root)
    armature.build_armature()
    ctrl = armature.ctrl_for_joint(joint)
    assert ctrl and cmds.objExists(ctrl), 'fixture: follower needs a ctrl first'

    # Add the component with a target: rule seeded + Armature re-engaged
    # in the same call — the follower's ctrl is GONE without any manual
    # rebuild.
    cnode = _add_follow_joint_component('fjarm_C0', joint,
                                        follow_target=target)
    assert fr.get_follow_rule(joint) is not None
    ctrl_after = armature.ctrl_for_joint(joint)
    assert not ctrl_after or not cmds.objExists(ctrl_after), (
        'ruled joint still carries an Armature ctrl — engage-now failed')

    # Clear the target through the options seam: rule gone, ctrl back.
    opts = nodes.get_component_options(cnode)
    opts['follow_target'] = ''
    nodes.set_component_options(cnode, opts)
    assert fr.get_follow_rule(joint) is None
    ctrl_back = armature.ctrl_for_joint(joint)
    assert ctrl_back and cmds.objExists(ctrl_back), (
        'cleared joint did not get its Armature ctrl back')


def test_follow_joint_add_with_empty_target_is_noop():
    """The common UI add path creates a FollowJoint with follow_target
    still at its contract default ('') — must NOT seed any rule (nothing
    to match yet); seeding happens later via the option-change hook once
    the rigger picks a target."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    joint = _make_offset_joint('fjemptytest_joint', (0.0, 0.0, 0.0))
    cmds.select(clear=True)

    nodes.create_registry('fjemptytest_bp')
    _add_follow_joint_component('fjemptytest_C0', joint, follow_target='')

    assert fr.get_follow_rule(joint) is None, (
        'a FollowJoint added with an empty follow_target must not seed a rule')


def test_follow_joint_add_with_missing_target_warns_and_does_not_seed():
    """follow_target points at a joint that doesn't exist (e.g. a stale
    persisted value) — must warn, not crash, and not seed a broken rule."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    joint = _make_offset_joint('fjmissingtest_joint', (0.0, 0.0, 0.0))
    cmds.select(clear=True)

    nodes.create_registry('fjmissingtest_bp')
    _add_follow_joint_component('fjmissingtest_C0', joint,
                                follow_target='fjmissingtest_no_such_joint')

    assert fr.get_follow_rule(joint) is None, (
        'a missing follow_target must not seed a rule')


def test_follow_joint_add_with_empty_target_near_authored_rule_is_silent():
    """An empty-target add must stay a true no-op even when an unrelated
    authored rule already sits on the joint — no spurious warning/
    clobber, since there is nothing for this component to seed yet."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    joint = _make_offset_joint('fjemptyauthtest_joint', (0.0, 0.0, 0.0))
    author_target = _make_offset_joint(
        'fjemptyauthtest_author_target', (5.0, 0.0, 0.0))
    cmds.select(clear=True)

    fr.set_follow_rule(joint, 'match', (author_target,), linked=False)

    nodes.create_registry('fjemptyauthtest_bp')
    _add_follow_joint_component('fjemptyauthtest_C0', joint, follow_target='')

    rule = fr.get_follow_rule(joint)
    assert rule is not None
    assert rule['linked'] is False
    assert rule['targets'] == [author_target], (
        'empty-target add must leave the authored rule fully untouched')


def test_follow_joint_remove_clears_linked_rule():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    joint = _make_offset_joint('fjremovetest_joint', (0.0, 0.0, 0.0))
    target = _make_offset_joint('fjremovetest_target', (10.0, 0.0, 0.0))
    cmds.select(clear=True)

    nodes.create_registry('fjremovetest_bp')
    node = _add_follow_joint_component('fjremovetest_C0', joint,
                                       follow_target=target)
    assert fr.get_follow_rule(joint) is not None, (
        'fixture sanity: rule not seeded at add time')

    nodes.delete_component_node(node)

    assert fr.get_follow_rule(joint) is None, (
        'removing the FollowJoint component must clear its linked rule')


def test_follow_joint_add_does_not_clobber_authored_rule():
    """A rigger-authored (linked=False) rule already sitting on the
    joint before the FollowJoint component is added must survive the
    add untouched (component warns instead of overwriting)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    joint = _make_offset_joint('fjauthoredtest_joint', (0.0, 0.0, 0.0))
    author_target = _make_offset_joint(
        'fjauthoredtest_author_target', (5.0, 0.0, 0.0))
    fj_target = _make_offset_joint('fjauthoredtest_fj_target', (10.0, 0.0, 0.0))
    cmds.select(clear=True)

    fr.set_follow_rule(joint, 'distribute', (author_target, fj_target),
                       t=0.25, linked=False)

    nodes.create_registry('fjauthoredtest_bp')
    _add_follow_joint_component('fjauthoredtest_C0', joint,
                                follow_target=fj_target)

    rule = fr.get_follow_rule(joint)
    assert rule is not None
    assert rule['linked'] is False, (
        'FollowJoint add must never clobber a rigger-authored rule')
    assert rule['kind'] == 'distribute'
    assert rule['targets'] == [author_target, fj_target]
    assert abs(rule['t'] - 0.25) < 1e-6


def test_follow_joint_remove_does_not_clear_authored_rule():
    """Removing a FollowJoint component that never got to seed (because
    an authored rule was already there) must not clear that authored
    rule either — only a LINKED rule is ever touched on remove."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    joint = _make_offset_joint('fjauthrmtest_joint', (0.0, 0.0, 0.0))
    author_target = _make_offset_joint(
        'fjauthrmtest_author_target', (5.0, 0.0, 0.0))
    fj_target = _make_offset_joint('fjauthrmtest_fj_target', (10.0, 0.0, 0.0))
    cmds.select(clear=True)

    fr.set_follow_rule(joint, 'match', (author_target,), linked=False)

    nodes.create_registry('fjauthrmtest_bp')
    node = _add_follow_joint_component('fjauthrmtest_C0', joint,
                                       follow_target=fj_target)

    rule_after_add = fr.get_follow_rule(joint)
    assert rule_after_add is not None and rule_after_add['linked'] is False, (
        'fixture sanity: add must have left the authored rule in place')

    nodes.delete_component_node(node)

    rule_after_remove = fr.get_follow_rule(joint)
    assert rule_after_remove is not None, (
        'removing the FollowJoint component must not clear a '
        'rigger-authored rule')
    assert rule_after_remove['linked'] is False
    assert rule_after_remove['targets'] == [author_target]


def test_follow_joint_target_option_change_reseeds_linked_rule():
    """A follow_target edit AFTER add (Properties panel option change)
    must re-seed the linked rule to the new target."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    joint = _make_offset_joint('fjretargettest_joint', (0.0, 0.0, 0.0))
    target_a = _make_offset_joint('fjretargettest_target_a', (10.0, 0.0, 0.0))
    target_b = _make_offset_joint('fjretargettest_target_b', (0.0, 10.0, 0.0))
    cmds.select(clear=True)

    nodes.create_registry('fjretargettest_bp')
    node = _add_follow_joint_component('fjretargettest_C0', joint,
                                       follow_target=target_a)

    rule_a = fr.get_follow_rule(joint)
    assert rule_a is not None and rule_a['targets'] == [target_a]

    options = nodes.get_component_options(node)
    options['follow_target'] = target_b
    nodes.set_component_options(node, options)

    rule_b = fr.get_follow_rule(joint)
    assert rule_b is not None, 'option change must have re-seeded a rule'
    assert rule_b['kind'] == 'match'
    assert rule_b['targets'] == [target_b], (
        f'follow_target change did not re-seed the linked rule: {rule_b}')
    assert rule_b['linked'] is True


def test_follow_joint_unrelated_option_change_does_not_touch_rule():
    """Editing an option OTHER than follow_target must not re-seed or
    otherwise disturb the existing linked rule."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    joint = _make_offset_joint('fjunrelatedtest_joint', (0.0, 0.0, 0.0))
    target = _make_offset_joint('fjunrelatedtest_target', (10.0, 0.0, 0.0))
    cmds.select(clear=True)

    nodes.create_registry('fjunrelatedtest_bp')
    node = _add_follow_joint_component('fjunrelatedtest_C0', joint,
                                       follow_target=target)
    rule_before = fr.get_follow_rule(joint)
    assert rule_before is not None

    options = nodes.get_component_options(node)
    options['maintain_offset'] = False
    nodes.set_component_options(node, options)

    rule_after = fr.get_follow_rule(joint)
    assert rule_after is not None
    assert rule_after['targets'] == rule_before['targets']
    assert rule_after['linked'] == rule_before['linked']


def test_follow_joint_component_linked_joint_live_follows_on_ctrl_move():
    """A joint whose linked rule came from a FollowJoint component (not
    hand-authored via fr.set_follow_rule directly) still live-follows
    through the Task 1.2 armature hook exactly like an authored rule
    does — same fixture/assertions as
    test_armature_live_follow_rules_evaluate_on_ctrl_move's match case."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import follow_rules as fr
    from maya_tools.rigging.fabricator import armature

    cmds.file(new=True, force=True)
    shoulder, elbow, wrist, twist_a, twist_b, ikhand = (
        _make_live_armature_fixture('fjarmaturetest'))

    nodes.create_registry('fjarmaturetest_bp')
    _add_follow_joint_component('fjarmaturetest_C0', ikhand,
                                follow_target=wrist)

    rule = fr.get_follow_rule(ikhand)
    assert rule is not None and rule['linked'] is True, (
        'fixture sanity: component add must have seeded the linked rule')

    armature.build_armature(root=shoulder)

    assert not cmds.objExists(f'{ikhand}_amt_CTL'), (
        'a component-linked ruled joint must not get its own ctrl either')

    wrist_ctrl = f'{wrist}_amt_CTL'
    cmds.xform(wrist_ctrl, ws=True, relative=True, t=(3.0, -7.0, 2.0))

    p_wrist = cmds.xform(wrist, q=True, ws=True, t=True)
    got_pos = cmds.xform(ikhand, q=True, ws=True, t=True)
    for i in range(3):
        assert abs(got_pos[i] - p_wrist[i]) < 1e-4, (
            'component-linked joint did not live-follow on ctrl move')
    got_mat = list(cmds.xform(ikhand, q=True, ws=True, matrix=True))
    wrist_mat = list(cmds.xform(wrist, q=True, ws=True, matrix=True))
    for i in range(16):
        assert abs(got_mat[i] - wrist_mat[i]) < 1e-4, (
            f'component-linked joint did not re-orient live at index {i}')


def test_follow_joint_add_does_not_clobber_broken_authored_rule():
    """A rigger-authored rule whose target has since been deleted (an
    unrelated scene edit, NOT a Follow-block action) must still count
    as an authored rule for the never-clobber guard — NOT be mistaken
    for "no rule at all" because get_follow_rule happens to raise on
    it. get_follow_rule always raises on a broken-target rule by
    design (see its docstring); a FollowJoint add on the same joint
    must leave that broken rule fully in place rather than silently
    replacing it with a resolvable linked rule."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    joint = _make_offset_joint('fjbrokenauthtest_joint', (0.0, 0.0, 0.0))
    cmds.select(clear=True)
    author_a = _make_offset_joint('fjbrokenauthtest_author_a', (5.0, 0.0, 0.0))
    cmds.select(clear=True)
    author_b = _make_offset_joint('fjbrokenauthtest_author_b', (8.0, 0.0, 0.0))
    cmds.select(clear=True)
    fj_target = _make_offset_joint('fjbrokenauthtest_fj_target', (10.0, 0.0, 0.0))
    cmds.select(clear=True)

    fr.set_follow_rule(joint, 'distribute', (author_a, author_b), t=0.4,
                       linked=False)

    # Unrelated cleanup deletes one of the authored rule's targets —
    # keep every fixture joint a sibling (clear selection between
    # cmds.joint() calls above) so this delete can't cascade onto
    # fj_target via an accidental parent/child chain. The rule NODE
    # itself survives (Maya just drops the connection) but
    # get_follow_rule(joint) now raises (see _resolve_targets_or_raise).
    cmds.delete(author_a)
    try:
        fr.get_follow_rule(joint)
        assert False, 'fixture sanity: rule should be broken (target A deleted)'
    except RuntimeError:
        pass

    nodes.create_registry('fjbrokenauthtest_bp')
    _add_follow_joint_component('fjbrokenauthtest_C0', joint,
                                follow_target=fj_target)

    # Must STILL raise: the broken authored rule must survive the
    # FollowJoint add untouched, not get silently deleted/replaced by
    # a resolvable match(fj_target) rule (never-clobber, SPEC 3.1).
    try:
        fr.get_follow_rule(joint)
        assert False, (
            'FollowJoint add clobbered a broken-but-authored follow '
            'rule instead of leaving it in place (never-clobber, SPEC 3.1)')
    except RuntimeError:
        pass

    assert fr.rule_linked_flag(joint) is False, (
        'the surviving rule must still be reported as authored '
        '(linked=False), not replaced by a linked one')


def test_follow_joint_remove_clears_broken_linked_rule():
    """Removing a FollowJoint whose own seeded linked rule has since
    gone broken (its target joint was deleted elsewhere) must still
    clear that rule -- a linked rule is always the component's own, so
    it's always safe to remove regardless of whether it currently
    resolves. Parallel case to the never-clobber fix above, lower
    severity: here the bug only leaves a stale rule node behind on
    remove rather than destroying authored rig data."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    joint = _make_offset_joint('fjbrokenrmtest_joint', (0.0, 0.0, 0.0))
    target = _make_offset_joint('fjbrokenrmtest_target', (10.0, 0.0, 0.0))
    cmds.select(clear=True)

    nodes.create_registry('fjbrokenrmtest_bp')
    node = _add_follow_joint_component('fjbrokenrmtest_C0', joint,
                                       follow_target=target)
    assert fr.rule_linked_flag(joint) is True, (
        'fixture sanity: rule not seeded at add time')

    cmds.delete(target)
    try:
        fr.get_follow_rule(joint)
        assert False, 'fixture sanity: rule should be broken (target deleted)'
    except RuntimeError:
        pass

    nodes.delete_component_node(node)

    assert fr.rule_linked_flag(joint) is None, (
        'removing the FollowJoint component must still clear its own '
        'linked rule even after it went broken (dangling target) — '
        'found a leftover rule node')


# ─────────────────────────────────────────────────────────────────────────
# fab_limb network node CRUD (SPEC 3.2, Task 2.1) — rename-proofness and
# scene save/reload persistence, which need a real saveable scene. Plain
# CRUD/ordering/hierarchy-walk coverage lives in
# _dev/test_limb_units.py instead.
# ─────────────────────────────────────────────────────────────────────────

def _make_limb_node_chain(prefix):
    """Same shape as test_limb_units.py's _make_limb_chain (kept as a
    separate copy on purpose — this file's fixtures are all real Maya
    parent/child chains built with cmds.joint directly, matching this
    file's existing convention, rather than importing across the two
    test files)."""
    import maya.cmds as cmds
    ox, oy, oz = _OFFSET

    def j(name, pos, parent=None):
        if parent is not None:
            cmds.select(parent, replace=True)
        else:
            cmds.select(clear=True)
        x, y, z = pos
        return cmds.joint(p=(x + ox, y + oy, z + oz), name=name)

    p = prefix
    J = {}
    J['shoulder'] = j(f'{p}_shoulder', (0, 0, 0))
    J['elbow'] = j(f'{p}_elbow', (10, 0, 0), J['shoulder'])
    J['wrist'] = j(f'{p}_wrist', (20, 0, 0), J['elbow'])
    J['twist_a'] = j(f'{p}_twist_a', (13, 2, 0), J['elbow'])
    J['finger1_root'] = j(f'{p}_finger1_root', (22, 1, 0), J['wrist'])
    cmds.select(clear=True)
    return J


def test_limb_node_rename_proof_top_joint_finger_root_and_twist():
    """Rename the top joint, a finger root, AND a twist joint after
    wiring a limb node up with all three — every accessor (get_limb_node,
    list_finger_roots, list_twist_upper, find_limb_for_joint) must still
    resolve correctly. This is the entire point of the feature: no name
    string is ever the source of truth."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    nodes.create_registry('lnrenametest_bp')
    J = _make_limb_node_chain('lnrenametest')

    limb = ln.create_limb_node('Arm_RibbonIK', J['shoulder'])
    ln.add_finger_root(limb, J['finger1_root'])
    ln.add_twist_upper(limb, J['twist_a'])

    new_shoulder = cmds.rename(J['shoulder'], 'lnrenametest_shoulder_RENAMED')
    new_finger = cmds.rename(J['finger1_root'], 'lnrenametest_finger1_RENAMED')
    new_twist = cmds.rename(J['twist_a'], 'lnrenametest_twist_a_RENAMED')

    # Direct lookup by the NEW name must find the same limb node.
    assert ln.get_limb_node(new_shoulder) == limb
    # The OLD name no longer exists at all.
    assert not cmds.objExists(J['shoulder'])

    assert ln.list_finger_roots(limb) == [new_finger], (
        f'finger_roots did not track the rename: {ln.list_finger_roots(limb)}')
    assert ln.list_twist_upper(limb) == [new_twist], (
        f'twist_upper did not track the rename: {ln.list_twist_upper(limb)}')

    # find_limb_for_joint from a descendant of the RENAMED wrist chain
    # must still resolve, via the renamed top_joint.
    found = ln.find_limb_for_joint(new_finger)
    assert found == limb, f'find_limb_for_joint broke after rename: {found}'

    assert ln.get_limb_type(limb) == 'Arm_RibbonIK', (
        'limb_type must be unaffected by unrelated joint renames')


def test_limb_node_persistence_survives_save_reload():
    """A limb node, its top_joint connection, an ordered finger_roots
    list, twist_upper/twist_lower/curl_excluded, a connected component,
    and the limb_type/implicit attrs must all round-trip through
    cmds.file(save) -> cmds.file(open) exactly like any other
    network-node state."""
    import tempfile
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    nodes.create_registry('lnpersisttest_bp')
    J = _make_limb_node_chain('lnpersisttest')

    limb = ln.create_limb_node('Arm_RibbonIK', J['shoulder'], implicit=True)
    comp = nodes.create_component_node(
        component_id='lnpersisttest_C0', component_type='SimpleFK',
        joints=[J['shoulder']], parent_plug='', side='md',
        options={}, persisted={},
    )
    ln.add_component(limb, comp)
    # Two finger roots in a deliberate, checkable authoring order.
    cmds.select(J['wrist'], replace=True)
    finger_b = cmds.joint(name='lnpersisttest_finger_b')
    cmds.select(J['wrist'], replace=True)
    finger_a = cmds.joint(name='lnpersisttest_finger_a')
    cmds.select(clear=True)
    ln.add_finger_root(limb, finger_b)
    ln.add_finger_root(limb, finger_a)
    ln.add_twist_upper(limb, J['twist_a'])
    ln.add_curl_excluded(limb, finger_b)

    tmp_dir = Path(tempfile.mkdtemp(prefix='ks_limb_node_test_'))
    scene_path = tmp_dir / 'limb_node_persist_test.ma'
    cmds.file(rename=str(scene_path))
    cmds.file(save=True, type='mayaAscii', force=True)

    cmds.file(new=True, force=True)
    assert ln.all_limb_nodes() == []

    cmds.file(str(scene_path), open=True, force=True)

    reloaded = ln.all_limb_nodes()
    assert len(reloaded) == 1, f'expected exactly 1 limb node, got {reloaded}'
    reloaded_limb = reloaded[0]

    assert ln.get_limb_type(reloaded_limb) == 'Arm_RibbonIK'
    assert ln.is_implicit(reloaded_limb) is True
    assert ln.get_limb_node(J['shoulder']) == reloaded_limb

    assert ln.list_finger_roots(reloaded_limb) == [finger_b, finger_a], (
        'finger_roots authoring order did not survive save/reload: '
        f'{ln.list_finger_roots(reloaded_limb)}')
    assert ln.list_twist_upper(reloaded_limb) == [J['twist_a']]
    assert ln.list_curl_excluded(reloaded_limb) == [finger_b]
    assert ln.list_components(reloaded_limb) == [comp], (
        'component connection did not survive save/reload')

    found = ln.find_limb_for_joint(finger_a)
    assert found == reloaded_limb


# ─────────────────────────────────────────────────────────────────────────
# Task 2.2 (SPEC 3.2 / 3.4): limb creation paths — fragment drop (a) and
# implicit standalone-component add (b). Companion offscreen coverage
# (CRUD/ordering only, no live builder or nodes.py plumbing) stays in
# _dev/test_limb_units.py; this file owns everything that needs a real
# scene + apply_limb_fragment / nodes.create_component_node.
# ─────────────────────────────────────────────────────────────────────────

def _add_world_component(component_id, root_joint):
    from maya_tools.rigging.fabricator import nodes
    nodes.create_component_node(
        component_id=component_id, component_type='World',
        joints=[root_joint], parent_plug='', side='md',
        options={}, persisted={},
    )


def _make_ikarm_chain(prefix):
    """shoulder -> elbow -> wrist under a plain root, off-origin — same
    shape as test_ik_arm_maya.py's own _make_arm_chain (kept as a
    separate copy per this file's established convention — see
    _make_limb_node_chain's own docstring)."""
    import maya.cmds as cmds
    ox, oy, oz = _OFFSET
    cmds.select(clear=True)
    root = cmds.joint(p=(0 + ox, 0 + oy, 0 + oz), name=f'{prefix}_root')
    shoulder = cmds.joint(p=(0 + ox, 0 + oy, 0 + oz), name=f'{prefix}_shoulder')
    elbow = cmds.joint(p=(10 + ox, 0 + oy, 0 + oz), name=f'{prefix}_elbow')
    wrist = cmds.joint(p=(20 + ox, 0 + oy, 0 + oz), name=f'{prefix}_wrist')
    cmds.select(clear=True)
    return root, shoulder, elbow, wrist


def _simple_component_fragment(prefix):
    """A minimal 2-joint LimbFragment carrying ONE component (FollowJoint
    — the simplest single-joint type with a real output plug). Stands in
    for a real component-carrying fragment (an arm, say) for
    _create_limb_node_for_fragment's own purposes — apply_limb_fragment
    never calls build() on the created component, only creates its
    network node, so the component's actual behavior is irrelevant here.

    NOTE: FollowJointComponent.creates_implicit_limb is False, so this
    fragment does NOT exercise the interaction between
    _create_limb_node_for_fragment and nodes._maybe_create_implicit_limb
    — see _ikarm_component_fragment, below, for the fragment shape that
    does (2026-07-09 P2 review finding: this helper's own docstring used
    to claim the component's actual TYPE was irrelevant here, which is
    false for that specific interaction)."""
    from maya_tools.rigging.fabricator.limbs.schema import LimbFragment, ExternalAnchor
    from maya_tools.rigging.fabricator.blueprint.schema import JointSpec, ComponentSpec

    return LimbFragment(
        name='TestLimbType',
        external_anchor=ExternalAnchor(plug_kind='matrix'),
        skeleton_joints=[
            JointSpec(name=f'{prefix}_root', parent='<EXTERNAL>',
                     translate=[0.0, 0.0, 0.0], radius=0.5),
            JointSpec(name=f'{prefix}_child', parent=f'{prefix}_root',
                     translate=[5.0, 0.0, 0.0], radius=0.4),
        ],
        components=[
            ComponentSpec(id=f'{prefix}_C0', type='FollowJoint',
                         joints=[f'{prefix}_root'],
                         parent_plug='<EXTERNAL>.joint_out',
                         side='md', role='', region='', options={},
                         persisted={}),
        ],
    )


def _ikarm_component_fragment(prefix):
    """A minimal 3-joint (shoulder/elbow/wrist) LimbFragment carrying
    ONE real RibbonIKArm-typed component — the SPEC's own flagship
    'Arm_RibbonIK' example, and the one component type whose class
    actually opts into Component.creates_implicit_limb == True
    (ribbon_ik_arm.py). This is the realistic fragment-drop shape
    canvas_panel.py's "save as .limb.yaml" flow is designed to produce
    (save a built arm, with its RibbonIKArm component, as a reusable
    fragment) — unlike _simple_component_fragment's FollowJoint stand-
    in, it's what actually exercises the interaction between
    _create_limb_node_for_fragment and
    nodes._maybe_create_implicit_limb. Same "apply_limb_fragment never
    calls build()" caveat applies — only the network node gets created,
    so no other RibbonIKArm behavior is exercised.

    Uses the literal current type string 'RibbonIKArm', not the retired
    'IKArm' alias (Task 2.3 closing sweep, item 2): nodes.create_registry
    now stamps FABRICATOR_VERSION at creation, so every fresh registry
    this fixture is dropped onto reads as current-version, not legacy —
    'IKArm' would no longer resolve through modules._VERSION_GATED_
    LEGACY_TYPE_MAP and get_component_class('IKArm') would KeyError,
    silently short-circuiting _maybe_create_implicit_limb before it ever
    exercises the interaction this fragment exists to test. The legacy-
    alias resolution itself is covered separately, on genuinely
    simulated-legacy (unstamped) registries, by test_ribbon_ik_arm_maya.
    py's test_legacy_ikarm_type_migration_is_version_gated."""
    from maya_tools.rigging.fabricator.limbs.schema import LimbFragment, ExternalAnchor
    from maya_tools.rigging.fabricator.blueprint.schema import JointSpec, ComponentSpec

    return LimbFragment(
        name='TestLimbType',
        external_anchor=ExternalAnchor(plug_kind='matrix'),
        skeleton_joints=[
            JointSpec(name=f'{prefix}_shoulder', parent='<EXTERNAL>',
                     translate=[0.0, 0.0, 0.0], radius=0.5),
            JointSpec(name=f'{prefix}_elbow', parent=f'{prefix}_shoulder',
                     translate=[10.0, 0.0, 0.0], radius=0.4),
            JointSpec(name=f'{prefix}_wrist', parent=f'{prefix}_elbow',
                     translate=[10.0, 0.0, 0.0], radius=0.35),
        ],
        components=[
            ComponentSpec(id=f'{prefix}_C0', type='RibbonIKArm',
                         joints=[f'{prefix}_shoulder', f'{prefix}_elbow',
                                f'{prefix}_wrist'],
                         parent_plug='<EXTERNAL>.joint_out',
                         side='md', role='', region='', options={},
                         persisted={}),
        ],
    )


def _finger_fragment(prefix):
    """Skeleton-only 4-joint finger fragment (metacarpal + 3 phalanges) —
    same shape as test_ik_arm_maya.py's own _finger_fragment (kept as a
    separate copy, same convention)."""
    from maya_tools.rigging.fabricator.limbs.schema import LimbFragment, ExternalAnchor
    from maya_tools.rigging.fabricator.blueprint.schema import JointSpec

    return LimbFragment(
        name='finger',
        external_anchor=ExternalAnchor(plug_kind='matrix'),
        skeleton_joints=[
            JointSpec(name=f'{prefix}_metacarpal', parent='<EXTERNAL>',
                     translate=[0.0, 0.0, 0.0], radius=0.5),
            JointSpec(name=f'{prefix}_01', parent=f'{prefix}_metacarpal',
                     translate=[1.0, 0.0, 0.0], radius=0.4),
            JointSpec(name=f'{prefix}_02', parent=f'{prefix}_01',
                     translate=[1.0, 0.0, 0.0], radius=0.35),
            JointSpec(name=f'{prefix}_03', parent=f'{prefix}_02',
                     translate=[1.0, 0.0, 0.0], radius=0.3),
        ],
        components=[],
    )
def test_limb_fragment_skeleton_only_registers_into_host_limb_no_duplicate():
    """DO (a) + the documented skeleton-only-fragment choice: dropping a
    skeleton-only finger fragment onto a wrist already owned by an
    IKArm (which, per DO (b), already carries its own implicit limb
    node from the standalone add below) registers the new finger root
    into that HOST limb's finger_roots[] — and creates NO second limb
    node.

    Task 2.3 update: the 'fingers' option this test used to ALSO check
    (builder.py's _auto_register_finger_chain used to append onto it
    alongside the finger_roots[] connection, "until Task 2.3 retires the
    option") is now gone — limb connection is the ONLY membership path,
    so this only asserts the finger_roots[] side."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_ikarm_chain('lnfraghost')
    nodes.create_registry('lnfraghost_bp')
    nodes.set_registry_root_joint(root)
    _add_world_component('lnfraghost_world', root)

    host_cnode = nodes.create_component_node(
        component_id='lnfraghost_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )

    limbs_before = ln.all_limb_nodes()
    assert len(limbs_before) == 1, (
        f'fixture sanity: standalone IKArm add should already have '
        f'created exactly one implicit limb, got {limbs_before}')
    host_limb = limbs_before[0]

    frag = _finger_fragment('lnfraghost_finger')
    apply_limb_fragment(frag, wrist)

    assert cmds.objExists('lnfraghost_finger_metacarpal'), (
        'skeleton-only fragment did not create its joints')

    limbs_after = ln.all_limb_nodes()
    assert limbs_after == [host_limb], (
        f'skeleton-only finger fragment drop onto an IKArm-owned wrist '
        f'must register into the HOST limb, never create a second one: '
        f'before={limbs_before} after={limbs_after}')

    assert 'lnfraghost_finger_metacarpal' in ln.list_finger_roots(host_limb), (
        f'new finger root not registered into host limb finger_roots: '
        f'{ln.list_finger_roots(host_limb)}')

    # Task 2.3: the 'fingers' option is retired — a fresh add/drop must
    # never write it, limb connection is the only membership path now.
    host_options = nodes.get_component_options(host_cnode)
    assert 'fingers' not in host_options, (
        f"host IKArm's options still carry a 'fingers' key after a "
        f"finger fragment drop — the retired option must never be "
        f"(re)written: {host_options.keys()}")


def test_limb_fragment_drop_survives_post_create_wiring_failure():
    """2026-07-09 P2 review blocker fix: _create_limb_node_for_fragment
    (phase 1) and _link_fragment_components_to_limb (phase 2, the post-
    create component/finger wiring loop) both promise "never raises
    past a successful drop" — but pre-fix, only the ln.create_limb_node
    call in phase 1 was actually guarded; the phase-2 wiring loop (which
    calls ks_nodes.get_component_options, among others) was completely
    unguarded and could raise straight out of apply_limb_fragment, even
    though the fragment's joints and component nodes were already
    committed to the scene by that point.

    Forces a failure inside that loop by monkeypatching
    nodes.get_component_options to raise, then asserts
    apply_limb_fragment still completes without raising, with the
    fragment's joints landing in the scene, in the `_Joints` reference
    layer, and the Armature rebuilt — exactly the documented no-op-
    degrade contract, not an unhandled exception."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment
    from maya_tools.rigging.fabricator import armature

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(name='lnfragfail_root')
    cmds.select(clear=True)
    nodes.create_registry('lnfragfail_bp')
    nodes.set_registry_root_joint(root)
    _add_world_component('lnfragfail_world', root)

    frag = _ikarm_component_fragment('lnfragfail_frag')

    orig_get_component_options = nodes.get_component_options

    def _boom(node):
        raise RuntimeError('forced failure for post-create-wiring regression test')

    nodes.get_component_options = _boom
    try:
        apply_limb_fragment(frag, root)  # must NOT raise
    finally:
        nodes.get_component_options = orig_get_component_options

    assert cmds.objExists('lnfragfail_frag_shoulder'), (
        'fragment joints must exist even though post-create wiring failed')
    assert cmds.objExists('lnfragfail_frag_wrist')

    layer_members = cmds.editDisplayLayerMembers(
        '_Joints', query=True, fullNames=True) or []
    assert any(m.endswith('lnfragfail_frag_shoulder') for m in layer_members), (
        f'joints must still land in the _Joints reference layer despite '
        f'the post-create wiring failure: {layer_members}')

    assert armature.armature_exists(), (
        'Armature must still be rebuilt despite the post-create wiring failure')


def test_implicit_limb_created_on_standalone_ikarm_add_with_finger_roots():
    """DO (b): adding an IKArm STANDALONE (no pre-existing limb anywhere
    up its joint chain) auto-creates an implicit=True fab_limb,
    connects the component, and connects finger roots discovered off
    the wrist's live child subtree. A SECOND component added under the
    same top joint reuses the existing implicit limb (no duplicate)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_ikarm_chain('implicittest')
    cmds.select(wrist, replace=True)
    finger_root = cmds.joint(name='implicittest_finger_root')
    cmds.joint(name='implicittest_finger_tip')
    cmds.select(clear=True)

    nodes.create_registry('implicittest_bp')
    comp = nodes.create_component_node(
        component_id='implicittest_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )

    limbs = ln.all_limb_nodes()
    assert len(limbs) == 1, f'expected exactly 1 implicit limb, got {limbs}'
    limb = limbs[0]

    assert ln.is_implicit(limb) is True, (
        'a standalone-add-created limb must be implicit=True')
    assert ln.get_limb_node(shoulder) == limb, (
        'implicit limb top_joint must be the component\'s own joints[0]')
    assert ln.list_components(limb) == [comp]
    assert ln.list_finger_roots(limb) == [finger_root], (
        f'wrist-discovered finger root not connected to the implicit '
        f'limb: {ln.list_finger_roots(limb)}')

    # A second component under the SAME top joint reuses the existing
    # implicit limb rather than spawning a duplicate.
    comp2 = nodes.create_component_node(
        component_id='implicittest_C1', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )
    limbs_after = ln.all_limb_nodes()
    assert limbs_after == [limb], (
        f'a second component under the same top joint must reuse the '
        f'existing implicit limb, not create a duplicate: {limbs_after}')
    assert set(ln.list_components(limb)) == {comp, comp2}, (
        f'second component did not connect into the reused limb: '
        f'{ln.list_components(limb)}')


def test_ikarm_add_reuses_existing_fragment_created_limb_no_implicit_duplicate():
    """DO (b): standalone-add resolution walks find_limb_for_joint FIRST
    — an IKArm added under a top joint that already owns a (fragment-
    created, implicit=False) limb node connects into THAT limb instead
    of spawning a second, implicit one."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_ikarm_chain('reusetest')
    nodes.create_registry('reusetest_bp')

    # Simulate a fragment-created limb (implicit=False) already owning
    # this subtree's top joint — same shape
    # _create_limb_node_for_fragment produces, without re-exercising the
    # fragment-drop machinery itself (covered separately, above).
    frag_limb = ln.create_limb_node('Arm_RibbonIK', shoulder, implicit=False)

    comp = nodes.create_component_node(
        component_id='reusetest_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )

    limbs = ln.all_limb_nodes()
    assert limbs == [frag_limb], (
        f'standalone IKArm add under an existing fragment-created limb '
        f'must connect into it, not spawn a second (implicit) one: '
        f'{limbs}')
    assert ln.is_implicit(frag_limb) is False, (
        'connecting an IKArm into an existing fragment-created limb '
        'must not flip it to implicit')
    assert comp in ln.list_components(frag_limb), (
        f'IKArm component did not connect into the existing limb: '
        f'{ln.list_components(frag_limb)}')


# ─────────────────────────────────────────────────────────────────────────
# P4.2a (limb/fragment lifecycle correctness): BUG 1 — implicit-limb
# orphan resurrection. nodes.delete_component_node never used to clean
# up its component's implicit fab_limb; the orphan kept its old
# membership and _maybe_create_implicit_limb's already-exists branch
# would silently reconnect a re-added component into it. Ruling
# (adopted): implicit limbs are lifecycle-bound to their components —
# on delete, if the resolved limb is implicit AND now has zero live
# components, delete the limb too. Explicit (fragment-dropped) limbs
# survive component churn unchanged.
# ─────────────────────────────────────────────────────────────────────────

def test_delete_component_deletes_orphaned_implicit_limb_no_resurrection():
    """The regression fixture (BasicIKArm thread's live-validation find):
    add an IKArm -> edit its limb membership via the SAME dial primitive
    the Properties panel's Curl dial calls (limb_node.add_curl_excluded)
    -> delete the component -> the orphaned implicit limb must be GONE,
    not merely disconnected -> re-add a component on the SAME joints ->
    the new implicit limb must come from a FRESH discovery pass, never
    the old node's stale, dial-edited state ("old membership returns
    from the grave" — the bug this pins shut).

    RED (pre-fix): the orphaned implicit limb survived the delete,
    finger_root/curl_excluded intact; find_limb_for_joint(shoulder) on
    re-add found it and _maybe_create_implicit_limb's already-exists
    branch reconnected the new component straight into the old, stale
    state. GREEN (this fix): the limb is deleted with its last
    component, so re-add always starts from a genuinely fresh node."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_ikarm_chain('resurrecttest')
    cmds.select(wrist, replace=True)
    finger_root = cmds.joint(name='resurrecttest_finger_root')
    finger_tip = cmds.joint(name='resurrecttest_finger_tip')
    cmds.select(clear=True)

    nodes.create_registry('resurrecttest_bp')
    comp = nodes.create_component_node(
        component_id='resurrecttest_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )

    limb = ln.find_limb_for_joint(shoulder)
    assert limb is not None, 'fixture sanity: no implicit limb created'
    assert ln.is_implicit(limb) is True
    assert finger_root in ln.list_finger_roots(limb), (
        'fixture sanity: wrist-discovered finger root missing')

    # "Edit fingers/exclusions via dials": mark the finger TIP as
    # curl_excluded. A 2-joint chain's own metacarpal_excluded()
    # heuristic never produces this on its own (len(joints) < 4), so its
    # presence/absence after re-add cleanly distinguishes "resurrected
    # old state" from "genuinely fresh discovery".
    ln.add_curl_excluded(limb, finger_tip)
    assert finger_tip in ln.list_curl_excluded(limb), (
        'fixture sanity: dial edit did not take')

    # --- delete ---
    nodes.delete_component_node(comp)

    assert not cmds.objExists(limb), (
        f'orphaned implicit limb {limb!r} must be deleted when its last '
        f'component is deleted — it survived the delete instead')
    assert ln.find_limb_for_joint(shoulder) is None, (
        'a resolvable limb still exists on the top joint after its only '
        'component was deleted')

    # --- re-add on the SAME joints ---
    comp2 = nodes.create_component_node(
        component_id='resurrecttest_C1', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )

    new_limb = ln.find_limb_for_joint(shoulder)
    assert new_limb is not None, 're-add did not create a fresh implicit limb'
    assert ln.is_implicit(new_limb) is True
    assert ln.list_components(new_limb) == [comp2]

    # Fresh discovery only: the live finger root is found again...
    assert finger_root in ln.list_finger_roots(new_limb), (
        'fresh discovery failed to rediscover a still-live finger root')
    # ...but the dial-edited curl exclusion from the DELETED component's
    # limb must NOT have come back from the grave.
    assert finger_tip not in ln.list_curl_excluded(new_limb), (
        f'resurrection bug: the deleted limb\'s dial-edited curl_excluded '
        f'state ({finger_tip!r}) reappeared on the fresh limb instead of '
        f'being re-derived from scratch: {ln.list_curl_excluded(new_limb)}')
def test_delete_one_of_two_components_does_not_delete_shared_implicit_limb():
    """The cleanup only fires when the limb's components[] is EMPTY
    after the delete — a limb still owned by a second, surviving
    component must not be touched. Deleting the SECOND (last) component
    afterward does, symmetrically, delete the now-truly-orphaned limb —
    the same single-component case the first test above already pins,
    reached here via the two-component path instead."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_ikarm_chain('sharedlimbtest')
    nodes.create_registry('sharedlimbtest_bp')
    comp1 = nodes.create_component_node(
        component_id='sharedlimbtest_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )
    comp2 = nodes.create_component_node(
        component_id='sharedlimbtest_C1', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )

    limb = ln.find_limb_for_joint(shoulder)
    assert limb is not None
    assert set(ln.list_components(limb)) == {comp1, comp2}, (
        'fixture sanity: both components should share one implicit limb')

    nodes.delete_component_node(comp1)

    assert cmds.objExists(limb), (
        'deleting ONE of two components on a shared implicit limb must '
        'NOT delete the limb — it still has a live component')
    assert ln.list_components(limb) == [comp2], (
        f'surviving component missing from the limb after the other '
        f'was deleted: {ln.list_components(limb)}')

    nodes.delete_component_node(comp2)

    assert not cmds.objExists(limb), (
        'deleting the LAST remaining component must delete the now-'
        'truly-orphaned implicit limb')


# ─────────────────────────────────────────────────────────────────────────
# P4.2a: BUG 2 — apply_limb_fragment crashed on ComponentSpec.id=None
# despite the field's own docstring promising auto-derivation ("None =
# auto-derive at load", blueprint/schema.py). Fix: auto-derive via the
# component class's default_id(joints) — same pattern fs_app.load uses
# — uniquified against existing host component ids.
# ─────────────────────────────────────────────────────────────────────────

def test_apply_limb_fragment_id_none_auto_derives_and_uniquifies():
    """id=None applies cleanly (no crash), the id is auto-derived per
    default_id, and — when that default collides with an existing host
    component id — it's uniquified with a numeric suffix rather than
    raising (an AUTO-derived id has nothing forcing a specific name, so
    silently picking the next free one is correct; an EXPLICIT id
    colliding is still a hard error, covered by the existing
    _collision_check_component_ids tests elsewhere)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment
    from maya_tools.rigging.fabricator.modules import get_component_class

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(name='idnonetest_root')
    cmds.select(clear=True)
    nodes.create_registry('idnonetest_bp')
    nodes.set_registry_root_joint(root)
    _add_world_component('idnonetest_world', root)

    frag = _ikarm_component_fragment('idnonetest_frag')
    frag.components[0].id = None   # exercise the documented auto-derive path

    expected_base = get_component_class('RibbonIKArm').default_id(
        ['idnonetest_frag_shoulder'])

    # Pre-occupy the base id on an UNRELATED host component (own joint,
    # so it doesn't interfere with root's own primary-joint ownership)
    # so the fragment's auto-derived id is FORCED to uniquify, not just
    # derive.
    cmds.select(clear=True)
    collider_joint = cmds.joint(name='idnonetest_collider_joint')
    cmds.select(clear=True)
    nodes.create_component_node(
        component_id=expected_base, component_type='World',
        joints=[collider_joint], parent_plug='', side='md',
        options={}, persisted={},
    )

    apply_limb_fragment(frag, root)   # must NOT raise despite id=None

    assert cmds.objExists('idnonetest_frag_shoulder'), (
        'fragment did not apply at all')

    uniquified_id = f'{expected_base}1'
    new_comp = nodes.find_component_node_by_id(uniquified_id)
    assert new_comp, (
        f'auto-derived id did not uniquify against the colliding host id '
        f'{expected_base!r} — expected {uniquified_id!r} to exist')
    assert nodes.get_component_type(new_comp) == 'RibbonIKArm', (
        nodes.get_component_type(new_comp))
    assert frag.components[0].id == uniquified_id, (
        'ComponentSpec.id was not written back with the resolved id')


# ─────────────────────────────────────────────────────────────────────────
# Task 3.1 (SPEC 2026-07-09 Limbs + Follower Joints §3.3): the Fingers
# dial — limb_node.limb_add_finger / limb_remove_finger, and the
# Properties limb-dials surface's app-layer contract (limb_node.py
# alone; the Qt widget itself is covered offscreen in
# test_limb_units_ui.py).
# ─────────────────────────────────────────────────────────────────────────

def _make_bare_ikarm(prefix):
    """A shoulder/elbow/wrist RibbonIKArm with NO fingers on the hand yet
    — the empty-hand starting point every test below builds on. Mirrors
    test_ribbon_ik_arm_maya.py's own _build_ikarm_with_fingers scaffold
    (kept as a separate copy per this file's established convention —
    see _make_limb_node_chain's own docstring), minus the hand skeleton:
    this file only needs a bare wrist to drop fingers onto via the dial.

    Returns (root, shoulder, elbow, wrist, limb) — `limb` is the
    RibbonIKArm's own auto-created implicit limb node
    (RibbonIKArm.creates_implicit_limb == True)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_ikarm_chain(prefix)
    nodes.create_registry(f'{prefix}_bp')
    nodes.set_registry_root_joint(root)
    _add_world_component(f'{prefix}_world', root)
    nodes.create_component_node(
        component_id=f'{prefix}_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    limb = ln.find_limb_for_joint(shoulder)
    assert limb is not None, (
        f'{prefix}: fixture sanity — implicit limb not auto-created for '
        f'the standalone RibbonIKArm add')
    return root, shoulder, elbow, wrist, limb
def test_implicit_limb_discovery_excludes_single_joint_weapon_child():
    """A wrist with one real (2-joint) finger AND a single-joint weapon_l
    child: the live create-time discovery pass must register ONLY the
    real finger — weapon_l must never reach finger_roots[]."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_ikarm_chain('weapondiscover')
    cmds.select(wrist, replace=True)
    finger_root = cmds.joint(name='weapondiscover_finger_root')
    cmds.joint(name='weapondiscover_finger_tip')
    cmds.select(wrist, replace=True)
    weapon = cmds.joint(name='weapondiscover_weapon_l')
    cmds.select(clear=True)

    nodes.create_registry('weapondiscover_bp')
    nodes.create_component_node(
        component_id='weapondiscover_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )

    limb = ln.find_limb_for_joint(shoulder)
    assert limb is not None
    roots = ln.list_finger_roots(limb)
    assert weapon not in roots, (
        f'single-joint weapon_l must never be auto-registered as a '
        f'finger via the live discovery path: {roots}')
    assert roots == [finger_root], roots
    # weapon_l itself is completely untouched — still a live joint, just
    # not a finger.
    assert cmds.objExists(weapon)


# ─────────────────────────────────────────────────────────────────────────
# 2026-07-09 checkpoint-blocker fix, item 2b: the Unregister affordance's
# app-layer contract — limb_node.remove_finger_root disconnects
# membership and KEEPS every joint, unlike limb_remove_finger (already
# covered above) which deletes the chain.
# ─────────────────────────────────────────────────────────────────────────
def test_follow_state_scene_reset_clears_stale_cache_and_watch_ids():
    """Simulates the leak the flake hunt suspected: register stale
    armature._FOLLOW_STATE (a dirty=False cache full of joint-name
    strings + a nonzero watch_ids list, exactly the shape a
    build_armature() call leaves behind), wipe the scene WITHOUT going
    through delete_armature() (a bare cmds.file(new=True), same as
    _make_bare_ikarm's own fixture helper does between every test in
    this file), and assert the module-level state comes back clean —
    matching armature_mirror.py's own established scene-reset
    discipline for its analogous per-scene state."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import armature

    cmds.file(new=True, force=True)

    # Guarantee the scene-reset hook is actually live via the REAL
    # installation code path (not a hand-rolled duplicate registration)
    # — idempotent no matter how many earlier tests in this process
    # already triggered it via a real build_armature() call.
    armature._follow_watch_ctrls([])

    # Simulate exactly the leaked state: a dirty=False cache full of
    # stale joint-name strings, plus a nonzero watch_ids list, as if a
    # build_armature() had just armed the live hook and the scene then
    # got wiped by a bare file(new) WITHOUT delete_armature()
    # (_follow_unwatch_all) ever running.
    armature._FOLLOW_STATE['ruled_cache'] = [
        'someoldscene_twist_l', 'someoldscene_elbow_l', 'someoldscene_wrist_l']
    armature._FOLLOW_STATE['dirty'] = False
    armature._FOLLOW_STATE['watch_ids'] = [123456789, 234567890]

    cmds.file(new=True, force=True)

    assert armature._FOLLOW_STATE['ruled_cache'] is None, (
        'ruled_cache must be cleared by a scene reset, not carry stale '
        'joint names into the new scene')
    assert armature._FOLLOW_STATE['dirty'] is True, (
        'dirty must be forced True by a scene reset so the next real '
        'caller re-derives the cache from the live scene')
    assert armature._FOLLOW_STATE['watch_ids'] == [], (
        'watch_ids must be cleared by a scene reset, not accumulate '
        'dead callback ids forever across a session')

    # The real caller path: a fresh scene has zero ruled joints, so the
    # cache must rebuild to an EMPTY list, never resolve any of the old
    # (now-gone, coincidentally-named) joint strings.
    assert armature._follow_ruled_joints() == [], (
        'a post-reset cache rebuild must reflect the NEW (empty) scene, '
        'not the stale names left in ruled_cache')


# ─────────────────────────────────────────────────────────────────────────
# Task 3.2 item 1+2 (SPEC 2026-07-09 Limbs + Follower Joints §3.3): the
# Twist dial — limb_node.limb_set_twist_count. Companion to the Fingers
# dial battery above (Task 3.1). A twist joint is a pure follow_rules
# primitive: no ctrl of its own, position entirely rule-driven — see
# limb_node.py's own module comment on this section for the full
# rationale.
# ─────────────────────────────────────────────────────────────────────────

def test_twist_lower_add_0_to_2_then_2_to_3_spaces_and_respaces_exactly():
    """0->2->3 add: joints spawn as SIBLINGS under the segment's TOP
    joint (elbow, for 'lower') with distribute rules at exact k/(n+1)
    fractions; growing the count re-spaces the EXISTING (unskinned)
    twists too (measure t + position). Posed positions are measured
    after building the Armature and dragging the wrist ctrl — the
    armature-live path from P1 (Task 1.2) must drive them, not just a
    one-time creation-time snapshot."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator import follow_rules as fr
    from maya_tools.rigging.fabricator import armature

    _root, _shoulder, elbow, wrist, limb = _make_bare_ikarm('twist02test')

    ln.limb_set_twist_count(limb, 'lower', 2)
    twists = ln.list_twist_lower(limb)
    assert len(twists) == 2, twists
    for t in twists:
        assert cmds.objExists(t)
        parent = (cmds.listRelatives(t, parent=True, type='joint') or [None])[0]
        assert parent == elbow, (
            f'{t} must be a sibling parented directly to {elbow} (not '
            f'chained to another twist), got parent={parent}')
        rule = fr.get_follow_rule(t)
        assert rule is not None and rule['kind'] == 'distribute'
        assert rule['targets'] == [elbow, wrist]
    ts = sorted(fr.get_follow_rule(t)['t'] for t in twists)
    assert abs(ts[0] - 1.0 / 3) < 1e-6, ts
    assert abs(ts[1] - 2.0 / 3) < 1e-6, ts

    ln.limb_set_twist_count(limb, 'lower', 3)
    twists3 = ln.list_twist_lower(limb)
    assert len(twists3) == 3, twists3
    assert twists3[:2] == twists, (
        'growing the count must preserve the existing twists\' authoring order')
    expected = [1.0 / 4, 2.0 / 4, 3.0 / 4]
    got_ts = [fr.get_follow_rule(t)['t'] for t in twists3]
    for got, exp in zip(got_ts, expected):
        assert abs(got - exp) < 1e-6, (got_ts, expected)

    armature.build_armature()
    wrist_ctrl = f'{wrist}_amt_CTL'
    assert cmds.objExists(wrist_ctrl), 'fixture sanity: arm must build'
    cmds.xform(wrist_ctrl, ws=True, relative=True, t=(5.0, 3.0, -2.0))

    p_elbow = cmds.xform(elbow, q=True, ws=True, t=True)
    p_wrist = cmds.xform(wrist, q=True, ws=True, t=True)
    for t, frac in zip(twists3, expected):
        exp_pos = [p_elbow[i] + (p_wrist[i] - p_elbow[i]) * frac for i in range(3)]
        got_pos = cmds.xform(t, q=True, ws=True, t=True)
        for i in range(3):
            assert abs(got_pos[i] - exp_pos[i]) < 1e-3, (
                f'{t}: expected {exp_pos}, got {got_pos} (frac={frac})')


def test_twist_add_connects_multi_and_reference_layer():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln

    _root, _shoulder, _elbow, _wrist, limb = _make_bare_ikarm('twistrefltest')
    ln.limb_set_twist_count(limb, 'upper', 2)
    twists = ln.list_twist_upper(limb)
    assert len(twists) == 2

    assert cmds.objExists('_Joints'), 'reference layer must be created'
    members = cmds.editDisplayLayerMembers('_Joints', query=True, fullNames=True) or []
    short_members = {m.split('|')[-1] for m in members}
    for t in twists:
        assert t in short_members, f'{t} must join the _Joints reference layer'

    for t in twists:
        conns = cmds.listConnections(f'{t}.message', source=False,
                                     destination=True, plugs=True) or []
        assert any(c.startswith(f'{limb}.twist_upper[') for c in conns), (
            f'{t} must connect into {limb}.twist_upper[]: {conns}')


def test_twist_add_skinned_existing_guard_errors_naming_joint_and_mutates_nothing():
    """Skinned guard, ADD direction: binding a skinCluster to an
    EXISTING twist blocks a further add (which would re-space that
    joint's t) — errors naming it, mutates nothing. Assert ZERO
    mutations: joint count, connection count, and t values unchanged
    before/after."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator import follow_rules as fr

    _root, _shoulder, _elbow, _wrist, limb = _make_bare_ikarm('twistskinaddtest')
    ln.limb_set_twist_count(limb, 'lower', 2)
    twists = ln.list_twist_lower(limb)
    target = twists[0]

    mesh = cmds.polyCube(name='twistskinaddtest_geo')[0]
    cmds.skinCluster([target], mesh, toSelectedBones=True,
                     name='twistskinaddtest_skinCluster')

    before_joints = set(cmds.ls(type='joint'))
    before_t = {t: fr.get_follow_rule(t)['t'] for t in twists}
    before_conns = set(cmds.listConnections(f'{limb}.twist_lower', source=True,
                                            destination=False) or [])

    raised = False
    try:
        ln.limb_set_twist_count(limb, 'lower', 3)
    except RuntimeError as e:
        raised = True
        assert target in str(e), str(e)
    assert raised, 'add must raise when an existing twist is skinned'

    after_joints = set(cmds.ls(type='joint'))
    assert after_joints == before_joints, 'no joint may be created on a guarded add'
    after_t = {t: fr.get_follow_rule(t)['t'] for t in twists}
    assert after_t == before_t, 'no t value may change on a guarded add'
    after_conns = set(cmds.listConnections(f'{limb}.twist_lower', source=True,
                                           destination=False) or [])
    assert after_conns == before_conns
    assert ln.list_twist_lower(limb) == twists, 'membership must be unchanged'


def test_twist_remove_skinned_guard_errors_naming_joint_and_mutates_nothing():
    """Skinned guard, REMOVE direction: the skinned joint is a SURVIVOR
    (not the doomed tail) — proves 're-space survivors evenly (same
    skinned-guard applies)' blocks the whole op, not just a direct
    delete of a skinned joint."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator import follow_rules as fr

    _root, _shoulder, _elbow, _wrist, limb = _make_bare_ikarm('twistskinrmtest')
    ln.limb_set_twist_count(limb, 'lower', 3)
    twists = ln.list_twist_lower(limb)
    target = twists[0]  # survives under n=1 — the doomed ones are twists[1:]

    mesh = cmds.polyCube(name='twistskinrmtest_geo')[0]
    cmds.skinCluster([target], mesh, toSelectedBones=True,
                     name='twistskinrmtest_skinCluster')

    before_joints = set(cmds.ls(type='joint'))
    before_t = {t: fr.get_follow_rule(t)['t'] for t in twists}

    raised = False
    try:
        ln.limb_set_twist_count(limb, 'lower', 1)
    except RuntimeError as e:
        raised = True
        assert target in str(e), str(e)
    assert raised, (
        'remove must raise when ANY current twist (survivor or doomed) is skinned')

    after_joints = set(cmds.ls(type='joint'))
    assert after_joints == before_joints, 'no joint may be deleted on a guarded remove'
    after_t = {t: fr.get_follow_rule(t)['t'] for t in twists}
    assert after_t == before_t
    assert ln.list_twist_lower(limb) == twists


def test_twist_remove_from_end_cleans_aimers_rules_multi_and_respaces_survivors():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator import follow_rules as fr
    from maya_tools.rigging.fabricator import armature
    from maya_tools.rigging.joint_orient import joint_orient_app as joa

    _root, _shoulder, _elbow, _wrist, limb = _make_bare_ikarm('twistrmtest')
    ln.limb_set_twist_count(limb, 'lower', 3)
    twists = ln.list_twist_lower(limb)
    doomed = twists[-1]
    survivors_expected = twists[:-1]

    # Aimers are lazily created by build_armature()'s own _ensure_aimers
    # — build once here so `doomed` genuinely carries one to clean up
    # (a bare Edit-Mode limb_set_twist_count call, with no Armature
    # built yet this session, would have none at all to test against).
    armature.build_armature()
    assert joa.aimer_exists(doomed), 'fixture sanity: every joint gets an aimer'
    rule_node_before = fr._find_rule_node(doomed)
    assert rule_node_before is not None, (
        'fixture sanity: doomed twist must carry a rule node')

    ln.limb_set_twist_count(limb, 'lower', 2)

    assert not cmds.objExists(doomed), f'{doomed} must be deleted'
    assert not cmds.objExists(rule_node_before), (
        "the doomed joint's follow-rule node must be deleted "
        "(clear_follow_rule), not orphaned")
    assert not cmds.ls(f'{doomed}_JntOrient*'), (
        'aimer DG fragments must not survive the joint delete '
        '(delete_aimer-before-delete)')

    survivors = ln.list_twist_lower(limb)
    assert survivors == survivors_expected, survivors
    conns = cmds.listConnections(f'{limb}.twist_lower', source=True,
                                 destination=False) or []
    assert doomed not in conns, 'removed joint must be disconnected from the multi'

    for t, frac in zip(survivors, (1.0 / 3, 2.0 / 3)):
        rule = fr.get_follow_rule(t)
        assert abs(rule['t'] - frac) < 1e-6, (t, rule, frac)


def test_twist_edit_mode_gate_rejects_add_and_remove_when_built():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator import limb_node as ln

    _root, _shoulder, _elbow, _wrist, limb = _make_bare_ikarm('twistgatetest')
    ln.limb_set_twist_count(limb, 'lower', 2)
    fs_app.build_modules()

    raised = False
    try:
        ln.limb_set_twist_count(limb, 'lower', 3)
    except RuntimeError as e:
        raised = True
        assert 'Edit Mode' in str(e), str(e)
    assert raised, 'add must reject once modules are built'
    assert len(ln.list_twist_lower(limb)) == 2, 'a rejected add must change nothing'

    raised = False
    try:
        ln.limb_set_twist_count(limb, 'lower', 1)
    except RuntimeError as e:
        raised = True
        assert 'Edit Mode' in str(e), str(e)
    assert raised, 'remove must reject once modules are built'
    assert len(ln.list_twist_lower(limb)) == 2, 'a rejected remove must change nothing'

    fs_app.unbuild_modules()


def test_twist_upper_and_lower_independent():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln

    _root, shoulder, elbow, _wrist, limb = _make_bare_ikarm('twistindependenttest')
    ln.limb_set_twist_count(limb, 'upper', 2)
    ln.limb_set_twist_count(limb, 'lower', 1)

    upper = ln.list_twist_upper(limb)
    lower = ln.list_twist_lower(limb)
    assert len(upper) == 2, upper
    assert len(lower) == 1, lower
    for t in upper:
        parent = (cmds.listRelatives(t, parent=True, type='joint') or [None])[0]
        assert parent == shoulder, (t, parent)
    for t in lower:
        parent = (cmds.listRelatives(t, parent=True, type='joint') or [None])[0]
        assert parent == elbow, (t, parent)

    # Removing all of 'upper' must not touch 'lower'.
    ln.limb_set_twist_count(limb, 'upper', 0)
    assert ln.list_twist_upper(limb) == []
    assert ln.list_twist_lower(limb) == lower
    for t in lower:
        assert cmds.objExists(t)


def test_twist_invalid_segment_and_negative_n_raise_value_error():
    from maya_tools.rigging.fabricator import limb_node as ln

    _root, _shoulder, _elbow, _wrist, limb = _make_bare_ikarm('twistvalidatetest')

    raised = False
    try:
        ln.limb_set_twist_count(limb, 'middle', 2)
    except ValueError as e:
        raised = True
        assert 'upper' in str(e) and 'lower' in str(e), str(e)
    assert raised, 'an invalid segment must raise ValueError'

    raised = False
    try:
        ln.limb_set_twist_count(limb, 'upper', -1)
    except ValueError:
        raised = True
    assert raised, 'a negative n must raise ValueError'


def test_twist_componentless_limb_fallback_resolves_linear_chain():
    """_resolve_twist_segment's fallback for a bare skeleton-only limb
    (no connected component) — a linear top -> mid -> end chain. Same
    "bare skeleton, no component" shape
    test_limb_add_finger_registers_membership_on_componentless_limb_via_
    fallback exercises for the Fingers dial's own attach-point
    fallback (including the SAME world-component-elsewhere trick to
    get detect_mode() reading MODE_SKELETON)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    world_root = cmds.joint(name='barelimbtest_world_root')
    cmds.select(clear=True)
    top = _make_offset_joint('barelimbtest_top', (0.0, 0.0, 0.0))
    mid = _make_offset_joint('barelimbtest_mid', (10.0, 0.0, 0.0))
    _end = _make_offset_joint('barelimbtest_end', (20.0, 0.0, 0.0))
    cmds.select(clear=True)

    nodes.create_registry('barelimbtest_bp')
    nodes.set_registry_root_joint(world_root)
    _add_world_component('barelimbtest_world', world_root)

    limb = ln.create_limb_node('BareLimb', top, implicit=False)
    assert ln.list_components(limb) == [], 'fixture sanity: no component on this limb'

    ln.limb_set_twist_count(limb, 'upper', 1)
    upper = ln.list_twist_upper(limb)
    assert len(upper) == 1, upper
    parent = (cmds.listRelatives(upper[0], parent=True, type='joint') or [None])[0]
    assert parent == top, (upper[0], parent)

    ln.limb_set_twist_count(limb, 'lower', 1)
    lower = ln.list_twist_lower(limb)
    assert len(lower) == 1, lower
    parent = (cmds.listRelatives(lower[0], parent=True, type='joint') or [None])[0]
    assert parent == mid, (lower[0], parent)


def test_resolve_twist_segment_lower_is_knee_ankle_on_4joint_leg():
    """RibbonIKLeg finding (2026-07-10): on a 4-joint leg
    (hip/knee/ankle/ball), 'lower' must resolve (knee, ankle) — the
    shin bone — NOT (knee, ball). names[-1] was the bug; names[2] is
    the fix. A foot is not a shin. Also pins 'upper' == (hip, knee)
    and the 3-joint arm equivalence (names[2] == names[-1] there,
    covered implicitly by every existing arm twist test)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(name='legseg_root', position=(0, 10, 0))
    hip = cmds.joint(name='legseg_hip', position=(0, 9, 0))
    knee = cmds.joint(name='legseg_knee', position=(0, 5, 0.5))
    ankle = cmds.joint(name='legseg_ankle', position=(0, 1, 0))
    ball = cmds.joint(name='legseg_ball', position=(0, 0, 1))
    cmds.select(clear=True)

    nodes.create_registry('legseg_bp')
    nodes.set_registry_root_joint(root)
    _add_world_component('legseg_world', root)
    cnode = nodes.create_component_node(
        component_id='legseg_C0', component_type='RibbonIKLeg',
        joints=[hip, knee, ankle, ball], parent_plug='', side='md',
        options={}, persisted={},
    )
    limb = ln.find_limb_for_joint(hip)
    if limb is None:
        # In case the leg component doesn't opt into implicit limbs:
        # wire one explicitly — this test pins segment resolution, not
        # implicit-limb creation.
        limb = ln.create_limb_node('RibbonIKLeg', hip)
        ln.add_component(limb, cnode)

    upper = ln._resolve_twist_segment(limb, 'upper')
    lower = ln._resolve_twist_segment(limb, 'lower')
    assert upper == (hip, knee), (
        f"'upper' must span the thigh (hip->knee); got {upper}")
    assert lower == (knee, ankle), (
        f"'lower' must span the shin (knee->ankle), never knee->ball — "
        f"a foot is not a shin; got {lower}")


# ─────────────────────────────────────────────────────────────────────────
# P4.2b (SPEC 2026-07-09 Limbs + Follower Joints §3.6): the limb color
# cascade. The limb node's own `color` attr (empty sentinel = auto,
# side-aware — same idiom ribbon_ik_arm.py's 'fingers_ctrl_color' uses one
# level down, at the single-component scale) cascades onto member
# components' own 'ctrl_color' option at the choke points that already
# assign it — nodes._maybe_create_implicit_limb (choke point 1: standalone
# add / a fragment component that opts into Component.creates_implicit_
# limb) and limbs.builder._link_fragment_components_to_limb (choke point
# 2: EVERY fragment component, opted-in or not) — never at
# Component.build() time. See limb_node.cascade_color_to_component's own
# docstring for the full no-op/override contract.
# ─────────────────────────────────────────────────────────────────────────

def test_limb_color_get_resolves_side_aware_default_when_unset():
    """Pure accessor test: an unset limb color ('' raw) resolves through
    get_limb_color() to the SAME side-aware default fs_window.
    _auto_detect_component_meta already computes for a member
    component's own ctrl_color at add time (side_tokens.side_to_ctrl_
    color: lf -> blue, rt -> red, md -> yellow) — no components
    involved."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    nodes.create_registry('colorresolvetest_bp')
    cmds.select(clear=True)
    top_lf = cmds.joint(name='colorresolvetest_shoulder_l')
    cmds.select(clear=True)
    top_rt = cmds.joint(name='colorresolvetest_shoulder_r')
    cmds.select(clear=True)
    top_md = cmds.joint(name='colorresolvetest_shoulder_md')
    cmds.select(clear=True)

    limb_lf = ln.create_limb_node('Arm_RibbonIK', top_lf)
    limb_rt = ln.create_limb_node('Arm_RibbonIK', top_rt)
    limb_md = ln.create_limb_node('Arm_RibbonIK', top_md)

    assert ln.get_limb_color_raw(limb_lf) == ''
    assert ln.get_limb_color_raw(limb_rt) == ''
    assert ln.get_limb_color_raw(limb_md) == ''

    assert ln.get_limb_color(limb_lf) == 'blue', ln.get_limb_color(limb_lf)
    assert ln.get_limb_color(limb_rt) == 'red', ln.get_limb_color(limb_rt)
    assert ln.get_limb_color(limb_md) == 'yellow', ln.get_limb_color(limb_md)


def test_limb_color_set_get_raw_round_trip_and_explicit_wins_over_side():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    nodes.create_registry('colorsettest_bp')
    cmds.select(clear=True)
    top = cmds.joint(name='colorsettest_shoulder_l')  # would auto-resolve 'blue'
    cmds.select(clear=True)
    limb = ln.create_limb_node('Arm_RibbonIK', top)

    ln.set_limb_color(limb, 'cyan')
    assert ln.get_limb_color_raw(limb) == 'cyan'
    assert ln.get_limb_color(limb) == 'cyan', (
        "an EXPLICIT limb color must win over the side-aware default "
        "even when the top_joint's own side would resolve to something "
        "else")

    # Clearing back to '' falls through to the side default again.
    ln.set_limb_color(limb, '')
    assert ln.get_limb_color_raw(limb) == ''
    assert ln.get_limb_color(limb) == 'blue'


def test_limb_color_cascade_noop_when_limb_color_unset():
    """Requirement: 'limb color empty -> members get side-aware
    default'. An implicit limb created fresh (color unset) must leave a
    newly-added member component's own ctrl_color EXACTLY as supplied —
    the SAME value fs_window._auto_detect_component_meta would already
    have computed for that component's side — proving the cascade is a
    genuine no-op here, not accidentally clobbering anything."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_ikarm_chain('colornoop_l')
    nodes.create_registry('colornoop_bp')

    comp = nodes.create_component_node(
        component_id='colornoop_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='lf',
        options={'ctrl_color': 'blue'}, persisted={},
    )

    limb = ln.find_limb_for_joint(shoulder)
    assert limb is not None, 'fixture sanity: no implicit limb created'
    assert ln.get_limb_color_raw(limb) == '', (
        'fixture sanity: a freshly implicit-created limb must start unset')
    assert ln.get_limb_color(limb) == 'blue', (
        "the limb's own resolved default must match its top_joint's side")

    assert nodes.get_component_options(comp)['ctrl_color'] == 'blue', (
        'an unset limb color must leave a member ctrl_color untouched')


def test_limb_color_explicit_cascades_to_new_member_component():
    """Requirement: 'limb color set -> members inherit it'. An EXISTING
    limb with an EXPLICIT color, when a new member component is added
    under its own top_joint (choke point 1 — nodes._maybe_create_
    implicit_limb resolving the EXISTING limb rather than spawning a
    fresh implicit one), must have its side-auto-default ctrl_color
    overridden to the limb's color."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_ikarm_chain('colorinherit_l')
    nodes.create_registry('colorinherit_bp')

    limb = ln.create_limb_node('Arm_RibbonIK', shoulder, implicit=False)
    ln.set_limb_color(limb, 'cyan')

    # 'blue' is the side-derived auto-default for this lf chain (side
    # seeding RESTORED 2026-07-14, retiring the 2026-07-12 family-color
    # seeding) — i.e. NOT an explicit override, so the limb's explicit
    # color cascades over it.
    comp = nodes.create_component_node(
        component_id='colorinherit_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='lf',
        options={'ctrl_color': 'blue'}, persisted={},
    )

    assert ln.find_limb_for_joint(shoulder) == limb, (
        'fixture sanity: the new component must connect into the '
        'PRE-EXISTING explicit limb, not spawn a second implicit one')
    assert nodes.get_component_options(comp)['ctrl_color'] == 'cyan', (
        "a member component's own type-default ctrl_color must be "
        "cascaded to the limb's explicit color")


def test_limb_color_component_explicit_override_still_wins():
    """Requirement: 'component explicit override still wins'. Same
    explicit-colored limb as above, but the new component's own
    ctrl_color was deliberately set to something OTHER than its side's
    auto-default — the cascade must leave it alone."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_ikarm_chain('coloroverride_l')
    nodes.create_registry('coloroverride_bp')

    limb = ln.create_limb_node('Arm_RibbonIK', shoulder, implicit=False)
    ln.set_limb_color(limb, 'cyan')

    # The lf side auto-default is 'blue' (side seeding restored
    # 2026-07-14) — 'pink' here is a deliberate, non-default override
    # (e.g. hand-authored into a saved fragment, or picked in
    # Properties before this add).
    comp = nodes.create_component_node(
        component_id='coloroverride_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='lf',
        options={'ctrl_color': 'pink'}, persisted={},
    )

    assert nodes.get_component_options(comp)['ctrl_color'] == 'pink', (
        "an explicit per-component ctrl_color override must always win "
        "over the limb's own color")
def test_limb_color_cascade_noop_when_component_has_no_ctrl_color_option():
    """A component whose options dict carries no 'ctrl_color' key at all
    (its contract doesn't declare the option) must be left completely
    untouched by the cascade — no KeyError, no spurious key added."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    top = cmds.joint(name='colornoopnokey_top')
    cmds.select(clear=True)
    nodes.create_registry('colornoopnokey_bp')

    limb = ln.create_limb_node('ColorLinkArm', top, implicit=False)
    ln.set_limb_color(limb, 'green')

    comp = nodes.create_component_node(
        component_id='colornoopnokey_C0', component_type='FollowJoint',
        joints=[top], parent_plug='', side='lf',
        options={'follow_target': ''}, persisted={},
    )

    ln.cascade_color_to_component(limb, comp)

    assert 'ctrl_color' not in nodes.get_component_options(comp), (
        "cascade must never ADD a 'ctrl_color' key to a component whose "
        "options never had one")


# ─────────────────────────────────────────────────────────────────────────
# P4.3 (SPEC 2026-07-09 Limbs + Follower Joints §3.2/§4): the LimbFragment
# `limb:` block — Save Limb currently captures joints + components but NOT
# limb-level authorship (finger membership, curl exclusions, twist follow
# rules, limb_type, color), so a saved limb with any hand-tuned dial state
# came back at heuristic/discovery defaults on the next Load Limb drop
# (silent data loss). Companion coverage: limbs/schema.py (LimbBlock/
# TwistJointSpec), limbs/io.py (_limb_block_to_dict/_limb_block_from_dict),
# limbs/builder.py (_apply_limb_block), fs_app.py
# (_snapshot_limb_block_from_scene).
# ─────────────────────────────────────────────────────────────────────────
def test_save_limb_round_trips_authored_ctrl_placement():
    """2026-07-10 (Adrian, live P4 checkpoint): the joint TRS snapshot is
    DRIVEN state — the Armature ctrls are the authored truth, and a limb
    must drop back in the EXACT place it was saved from. Build an arm,
    stand the Armature, author an edit by dragging the elbow ctrl
    somewhere no re-derivation pass would ever compute, save the limb,
    fresh scene, load onto a same-placed root — every subtree joint's
    world position must match the source scene, and the saved YAML must
    carry the armature_ctrls block that makes it possible
    (LimbFragment.armature_ctrls docstring)."""
    import yaml as _yaml
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator import armature
    from maya_tools.rigging.fabricator import fs_app

    root, shoulder, elbow, wrist, limb = _make_bare_ikarm('ctrlplacetest')
    armature.build_armature()

    elbow_ctrl = armature.ctrl_for_joint(elbow)
    assert elbow_ctrl, 'fixture sanity: elbow has an Armature ctrl'
    cmds.setAttr(f'{elbow_ctrl}.translate', 3.7, -1.2, 2.9)

    subtree = [shoulder, elbow, wrist]
    want = {j: cmds.xform(j, q=True, ws=True, t=True) for j in subtree}
    root_ws = cmds.xform(root, q=True, ws=True, t=True)

    tmp_dir = Path(tempfile.mkdtemp(prefix='ks_ctrl_place_test_'))
    yaml_path = tmp_dir / 'ctrlplacetest_arm.limb.yaml'
    fs_app.save_limb(root, str(yaml_path), name='CtrlPlaceArm')

    raw = _yaml.safe_load(yaml_path.read_text(encoding='utf-8'))
    assert raw.get('armature_ctrls'), (
        'armature_ctrls block missing from the saved YAML')
    assert elbow in raw['armature_ctrls'], (
        f'elbow ctrl transform not captured: '
        f'{sorted(raw["armature_ctrls"])}')

    # ---- fresh scene: same-placed root, drop the fragment back ----
    # SAME-PLACED is load-bearing, and was this test's own defect (#13):
    # the fixture rig is built off-origin (_OFFSET) but this root used to
    # be minted with a bare cmds.joint(), i.e. at the ORIGIN. A limb
    # fragment stores LOCAL transforms and is parented under the drop
    # target, so the limb landed at the origin - correct behavior, wrong
    # yardstick. The assertion below compared it against world positions
    # captured under a root at _OFFSET and failed by exactly _OFFSET.
    # Red since the day it was written (ba86739, 2026-07-10): the
    # off-origin fixture (6b05ab2) already predated it, so this never
    # passed and gave false coverage on the armature_ctrls round-trip.
    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    new_root = cmds.joint(p=tuple(root_ws), name='ctrlplacetest2_root')
    cmds.select(clear=True)
    nodes.create_registry('ctrlplacetest2_bp')
    nodes.set_registry_root_joint(new_root)
    _add_world_component('ctrlplacetest2_world', new_root)

    fs_app.load_limb(str(yaml_path), new_root)

    for j in subtree:
        got = cmds.xform(j, q=True, ws=True, t=True)
        assert all(abs(a - b) < 1e-4 for a, b in zip(got, want[j])), (
            f'{j}: world position did not round-trip — saved '
            f'{[round(v, 4) for v in want[j]]}, loaded '
            f'{[round(v, 4) for v in got]}')


def test_save_limb_at_component_start_joint_re_anchors_to_include_it():
    """KS #4: right-clicking the limb's OWN first joint (pelvis for a
    spine, shoulder for an arm) and choosing Save Limb raised 'No
    Fabricator components found' — _walk_limb_subtree excludes the
    anchor, so a component whose joints[0] IS the anchor could never be
    found. Fix: when the anchor starts a component AND has a joint
    parent, save_limb silently re-anchors to that parent so the clicked
    limb (joint + component) is included in the fragment. The gesture
    then matches Delete Limb's inclusive read of the clicked row."""
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator.limbs import io as limbs_io
    from maya_tools.rigging.fabricator.limbs.schema import (
        EXTERNAL_PLACEHOLDER,
    )

    root, shoulder, elbow, wrist, _limb = _make_bare_ikarm('anchorstart')

    tmp_dir = Path(tempfile.mkdtemp(prefix='ks_save_limb_anchor_test_'))
    yaml_path = tmp_dir / 'anchorstart_arm.limb.yaml'
    # THE #4 GESTURE: anchor at the component's own first joint.
    fs_app.save_limb(shoulder, str(yaml_path), name='AnchorStartArm')

    fragment = limbs_io.read_yaml(yaml_path)
    skel_names = [s.name for s in fragment.skeleton_joints]
    assert shoulder in skel_names, (
        f'clicked joint must be INSIDE the fragment after re-anchor: '
        f'{skel_names}')
    assert root not in skel_names, (
        f'the re-anchor host (root) must stay OUTSIDE the fragment: '
        f'{skel_names}')
    shoulder_spec = next(s for s in fragment.skeleton_joints
                         if s.name == shoulder)
    assert shoulder_spec.parent == EXTERNAL_PLACEHOLDER, (
        f'clicked joint must become a fragment root (parent '
        f'{EXTERNAL_PLACEHOLDER}), got {shoulder_spec.parent!r}')
    comp_types = [c.type for c in fragment.components]
    assert 'RibbonIKArm' in comp_types, (
        f'the component starting at the clicked joint must ride the '
        f'fragment: {comp_types}')


def test_save_limb_at_root_host_semantics_unchanged():
    """Guard pin for the #4 fix: the skeleton ROOT is the World
    component's own joints[0], but it has no joint parent — the
    re-anchor must NOT fire (nothing to re-anchor to), preserving the
    host-anchored gesture every existing fragment and test uses: the
    fragment excludes root, includes the limb below, and never captures
    the World component."""
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator.limbs import io as limbs_io

    root, shoulder, elbow, wrist, _limb = _make_bare_ikarm('anchorroot')

    tmp_dir = Path(tempfile.mkdtemp(prefix='ks_save_limb_root_test_'))
    yaml_path = tmp_dir / 'anchorroot_arm.limb.yaml'
    fs_app.save_limb(root, str(yaml_path), name='RootAnchoredArm')

    fragment = limbs_io.read_yaml(yaml_path)
    skel_names = [s.name for s in fragment.skeleton_joints]
    assert root not in skel_names, (
        f'host anchor must stay excluded from the fragment: {skel_names}')
    assert shoulder in skel_names, skel_names
    comp_types = [c.type for c in fragment.components]
    assert 'RibbonIKArm' in comp_types, comp_types
    assert 'World' not in comp_types, (
        f'World must never ride a limb fragment: {comp_types}')


def test_save_limb_at_component_start_joint_excludes_sibling_limbs():
    """2026-07-18 (Adrian, live): Save Limb on the left clavicle also
    saved the right arm and the neck/head. The #4 re-anchor moved the
    walk up to the parent (spine_05) and then took the PARENT's whole
    subtree, so every sibling branch rode along; loading the fragment
    into a scene that already had a right arm errored on the duplicate.

    The two #4 tests above run on _make_bare_ikarm, a single chain with
    no siblings under root — the sweep is invisible there. The second
    branch below is the entire point of this fixture."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.limbs import io as limbs_io
    from maya_tools.rigging.fabricator.limbs.schema import (
        EXTERNAL_PLACEHOLDER,
    )

    root, shoulder, elbow, wrist, _limb = _make_bare_ikarm('siblingsweep')

    # A SECOND limb hanging off the same parent as `shoulder`.
    cmds.select(root, replace=True)
    sib_shoulder = cmds.joint(name='siblingsweep_sib_shoulder')
    sib_elbow = cmds.joint(name='siblingsweep_sib_elbow')
    sib_wrist = cmds.joint(name='siblingsweep_sib_wrist')
    cmds.select(clear=True)
    nodes.create_component_node(
        component_id='siblingsweep_C1', component_type='RibbonIKArm',
        joints=[sib_shoulder, sib_elbow, sib_wrist], parent_plug='',
        side='md', options={'mid_ctrl_count': 1}, persisted={},
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix='ks_save_limb_sibling_test_'))
    yaml_path = tmp_dir / 'siblingsweep_arm.limb.yaml'
    fs_app.save_limb(shoulder, str(yaml_path), name='SiblingSweepArm')

    fragment = limbs_io.read_yaml(yaml_path)
    skel_names = [s.name for s in fragment.skeleton_joints]

    # The clicked limb still rides (KS #4 holds) ...
    assert shoulder in skel_names, skel_names
    assert wrist in skel_names, skel_names
    # ... and the sibling limb does not.
    for j in (sib_shoulder, sib_elbow, sib_wrist):
        assert j not in skel_names, (
            f'sibling branch joint {j!r} leaked into the fragment: '
            f'{skel_names}')
    comp_ids = [c.id for c in fragment.components]
    assert 'siblingsweep_C1' not in comp_ids, (
        f'sibling component leaked into the fragment: {comp_ids}')

    # Exactly one <EXTERNAL> root — the loader's contract.
    ext_roots = [s.name for s in fragment.skeleton_joints
                 if s.parent == EXTERNAL_PLACEHOLDER]
    assert ext_roots == [shoulder], (
        f'fragment must have exactly one external root (the clicked '
        f'joint), got {ext_roots}')


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith('test_') and callable(fn)
    ]
    print(f'Running {len(tests)} tests from test_limb_units_maya.py...')
    for name, fn in tests:
        check(name, fn)

    print(f'\n{len(tests) - len(FAILURES) - len(SKIPS)} passed, '
          f'{len(FAILURES)} failed, {len(SKIPS)} skipped '
          f'(of {len(tests)})')
    if FAILURES:
        print('\nFAILURES:')
        for f in FAILURES:
            print(f'  - {f}')
    if SKIPS:
        print('\nSKIPS:')
        for s in SKIPS:
            print(f'  - {s}')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
