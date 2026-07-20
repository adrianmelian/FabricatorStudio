# _dev/test_limb_units.py
"""Tests for the follower-joint primitive (maya_tools/rigging/fabricator/
follow_rules.py) that don't need scene save/reload or rename churn — CRUD
round-trip, validation, and resolve_position/resolve_orientation math on
fresh, off-origin fixtures.

SPEC: workspace/2026-07-09_limbs-unit-follower-joints/SPEC.md section 3.1.
Companion suite: _dev/test_limb_units_maya.py (rename-proofness,
scene-save/reload persistence, evaluate() posed-displacement).

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_limb_units.py
"""
import sys
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


# Off-origin fixtures throughout (studio deformer-order lesson: never
# assert near world origin — it hides matrix-math bugs that only show up
# once the chain has moved away from identity).
_OFFSET = (37.0, 52.0, -19.0)


def _make_offset_joint(name, pos):
    import maya.cmds as cmds
    ox, oy, oz = _OFFSET
    x, y, z = pos
    cmds.select(clear=True)
    j = cmds.joint(p=(x + ox, y + oy, z + oz), name=name)
    cmds.select(clear=True)
    return j


def _make_elbow_wrist_fixture(prefix):
    """Three unconnected (siblings-at-origin-of-scene, not parented to
    each other) off-origin joints: elbow, wrist, and a twist joint to be
    ruled. Kept unparented on purpose — resolve_position must work off
    plain world position, no hierarchy dependency required."""
    import maya.cmds as cmds
    elbow = _make_offset_joint(f'{prefix}_elbow', (0.0, 0.0, 0.0))
    wrist = _make_offset_joint(f'{prefix}_wrist', (10.0, 0.0, 0.0))
    twist = _make_offset_joint(f'{prefix}_twist', (0.0, 0.0, 0.0))
    return elbow, wrist, twist


def test_module_imports_and_public_api_shape():
    from maya_tools.rigging.fabricator import follow_rules as fr
    for name in ('set_follow_rule', 'get_follow_rule', 'clear_follow_rule',
                 'resolve_position', 'resolve_orientation', 'evaluate'):
        assert hasattr(fr, name), f'follow_rules missing public API: {name}'


def test_crud_round_trip_distribute():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('crud_dist')

    fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=0.25)
    rule = fr.get_follow_rule(twist)
    assert rule is not None
    assert rule['kind'] == 'distribute'
    assert rule['targets'] == [elbow, wrist], rule['targets']
    assert abs(rule['t'] - 0.25) < 1e-9
    assert rule['linked'] is False

    fr.clear_follow_rule(twist)
    assert fr.get_follow_rule(twist) is None


def test_crud_round_trip_match():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('crud_match')

    fr.set_follow_rule(twist, 'match', (wrist,))
    rule = fr.get_follow_rule(twist)
    assert rule is not None
    assert rule['kind'] == 'match'
    assert rule['targets'] == [wrist], rule['targets']
    assert rule['t'] is None, f"match rule should carry t=None, got {rule['t']!r}"
    assert rule['linked'] is False

    fr.clear_follow_rule(twist)
    assert fr.get_follow_rule(twist) is None


def test_reset_replaces_rule():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('reset')

    fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=0.1)
    first_node = fr._find_rule_node(twist)
    assert cmds.objExists(first_node)
    # UUID, not name: the replacement node is very likely to be handed
    # the SAME node name back by Maya (name freed by the delete below),
    # so a name-based existence check would be trivially satisfied by
    # the NEW node. UUID identity is the only reliable "is this the same
    # node" signal here.
    first_uuid = cmds.ls(first_node, uuid=True)[0]

    # Re-set to the OTHER kind entirely — old rule must be fully gone,
    # not merged/left dangling.
    fr.set_follow_rule(twist, 'match', (wrist,))
    rule = fr.get_follow_rule(twist)
    assert rule['kind'] == 'match'
    assert rule['targets'] == [wrist]
    # The old node must not have survived as an orphan (checked by UUID
    # — see note above on why a name check would be unreliable here).
    assert not cmds.ls(first_uuid), (
        f'old rule node {first_node!r} (uuid {first_uuid}) should be '
        f'deleted on replace, but a node with that uuid still exists')

    # And re-setting again to distribute must fully replace again (not
    # leave a stray 'match'-shaped single target index around).
    fr.set_follow_rule(twist, 'distribute', (wrist, elbow), t=0.75)
    rule2 = fr.get_follow_rule(twist)
    assert rule2['kind'] == 'distribute'
    assert rule2['targets'] == [wrist, elbow]
    assert abs(rule2['t'] - 0.75) < 1e-9


def test_t_clamps():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('clamp')

    fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=-0.3)
    assert fr.get_follow_rule(twist)['t'] == 0.0

    fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=1.7)
    assert fr.get_follow_rule(twist)['t'] == 1.0

    fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=None)
    assert abs(fr.get_follow_rule(twist)['t'] - 0.5) < 1e-9, (
        'omitted t should default to 0.5')


def test_bad_kind_raises_cleanly():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('badkind')

    raised = False
    try:
        fr.set_follow_rule(twist, 'average', (elbow, wrist))
    except (ValueError, RuntimeError):
        raised = True
    assert raised, 'set_follow_rule should raise on an unknown kind'
    assert fr.get_follow_rule(twist) is None, (
        'a failed set_follow_rule must not leave a partial rule behind')


def test_bad_targets_raises_cleanly():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('badtargets')

    # Wrong count for distribute (needs exactly 2).
    raised = False
    try:
        fr.set_follow_rule(twist, 'distribute', (elbow,))
    except (ValueError, RuntimeError):
        raised = True
    assert raised, 'distribute with 1 target should raise'

    # Wrong count for match (needs exactly 1).
    raised = False
    try:
        fr.set_follow_rule(twist, 'match', (elbow, wrist))
    except (ValueError, RuntimeError):
        raised = True
    assert raised, 'match with 2 targets should raise'

    # Nonexistent target.
    raised = False
    try:
        fr.set_follow_rule(twist, 'match', ('does_not_exist_xyz',))
    except (ValueError, RuntimeError):
        raised = True
    assert raised, 'match with a nonexistent target should raise'

    assert fr.get_follow_rule(twist) is None, (
        'failed set_follow_rule calls must not leave a partial rule')


def test_resolve_position_distribute_off_origin():
    import cmath  # noqa: F401  (nothing exotic; keeps import style consistent)
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('resolvepos')
    ox, oy, oz = _OFFSET

    for t, expected_x in ((0.0, 0.0), (0.5, 5.0), (1.0, 10.0)):
        fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=t)
        pos = fr.resolve_position(twist)
        assert abs(pos[0] - (expected_x + ox)) < 1e-6, (
            f't={t}: expected x={expected_x + ox}, got {pos[0]}')
        assert abs(pos[1] - oy) < 1e-6
        assert abs(pos[2] - oz) < 1e-6


def test_resolve_position_and_orientation_match():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('resolvematch')
    # Give the match target (wrist) a distinctive world rotation so
    # orientation resolution isn't vacuously identity.
    cmds.setAttr(f'{wrist}.rotate', 12.0, 34.0, 56.0, type='double3')

    fr.set_follow_rule(twist, 'match', (wrist,))

    pos = fr.resolve_position(twist)
    wrist_pos = cmds.xform(wrist, q=True, ws=True, t=True)
    for i in range(3):
        assert abs(pos[i] - wrist_pos[i]) < 1e-6

    orient = fr.resolve_orientation(twist)
    assert orient is not None
    wrist_mat = list(cmds.xform(wrist, q=True, ws=True, matrix=True))
    wrist_mat[12] = wrist_mat[13] = wrist_mat[14] = 0.0
    for a, b in zip(orient, wrist_mat):
        assert abs(a - b) < 1e-6, (orient, wrist_mat)


def test_resolve_orientation_none_for_distribute():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('noorient')

    fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=0.5)
    assert fr.resolve_orientation(twist) is None, (
        'distribute orientation is the aimers\' job — must stay None')


def test_resolve_position_raises_without_rule():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('norule')

    raised = False
    try:
        fr.resolve_position(twist)
    except RuntimeError:
        raised = True
    assert raised, 'resolve_position on an unruled joint should raise'


def test_resolve_position_raises_clear_error_on_deleted_distribute_target():
    """Deleting a distribute target joint (B) must not silently shrink
    rule['targets'] into a cryptic unpack error — get_follow_rule (and
    therefore resolve_position) must raise a RuntimeError naming the
    ruled joint, the rule kind, and which target (by its documented
    label, A or B) is missing."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('deltarget_dist')

    fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=0.5)
    cmds.delete(wrist)

    raised = None
    try:
        fr.resolve_position(twist)
    except RuntimeError as exc:
        raised = exc
    assert raised is not None, (
        'resolve_position should raise a clear RuntimeError when a '
        'distribute target has been deleted, not an unpack error')
    msg = str(raised)
    assert twist in msg, f'error should name the ruled joint: {msg!r}'
    assert 'distribute' in msg, f'error should name the rule kind: {msg!r}'
    assert 'B' in msg, f'error should name the missing target (B): {msg!r}'


def test_resolve_position_raises_clear_error_on_deleted_match_target():
    """Same guarantee as the distribute case, for 'match': deleting X
    must raise a clear RuntimeError, not an IndexError from an empty
    targets list."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('deltarget_match')

    fr.set_follow_rule(twist, 'match', (wrist,))
    cmds.delete(wrist)

    raised = None
    try:
        fr.resolve_position(twist)
    except RuntimeError as exc:
        raised = exc
    assert raised is not None, (
        'resolve_position should raise a clear RuntimeError when the '
        'match target has been deleted, not an IndexError')
    msg = str(raised)
    assert twist in msg, f'error should name the ruled joint: {msg!r}'
    assert 'match' in msg, f'error should name the rule kind: {msg!r}'
    assert 'X' in msg, f'error should name the missing target (X): {msg!r}'


def test_notify_change_listener_exception_does_not_propagate():
    """A change-listener that raises must never break the rule edit
    that triggered it (set_follow_rule/clear_follow_rule must still
    succeed and return normally)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('listenerraise')

    def _bad_listener(joint):
        raise ValueError('boom')

    fr.add_change_listener(_bad_listener)
    try:
        # Must not raise, despite the listener blowing up.
        fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=0.5)
        rule = fr.get_follow_rule(twist)
        assert rule is not None, (
            'the rule edit itself must still have gone through even '
            'though a listener raised')
    finally:
        fr.remove_change_listener(_bad_listener)


def test_notify_change_listener_exception_is_logged_not_swallowed():
    """A listener exception must leave a diagnostic trail (traceback.
    print_exc, matching every sibling except-block added alongside this
    hook in armature.py) instead of vanishing with a bare `pass` —
    otherwise a broken listener (e.g. the armature retrofit cache
    invalidation) fails completely silently with zero console output,
    with no way to tell the cache/retrofit never ran."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import follow_rules as fr

    cmds.file(new=True, force=True)
    elbow, wrist, twist = _make_elbow_wrist_fixture('listenerlog')

    def _bad_listener(joint):
        raise ValueError('boom')

    calls = {'n': 0}
    real_print_exc = fr.traceback.print_exc

    def _counting_print_exc(*args, **kwargs):
        calls['n'] += 1
        # Swallow the actual traceback text -- this test only needs to
        # know it was CALLED, not see it printed to stderr.

    fr.traceback.print_exc = _counting_print_exc
    fr.add_change_listener(_bad_listener)
    try:
        fr.set_follow_rule(twist, 'distribute', (elbow, wrist), t=0.5)
    finally:
        fr.remove_change_listener(_bad_listener)
        fr.traceback.print_exc = real_print_exc

    assert calls['n'] >= 1, (
        '_notify_change must call traceback.print_exc() when a listener '
        'raises, not swallow it with a bare `pass`')


# ─────────────────────────────────────────────────────────────────────────
# fab_limb network node CRUD (SPEC 3.2, Task 2.1) — CRUD round-trip,
# ordered finger_roots, and pure hierarchy-walk resolution that don't need
# scene save/reload or rename churn. Companion suite:
# _dev/test_limb_units_maya.py (rename-proofness, persistence).
# ─────────────────────────────────────────────────────────────────────────

def _make_limb_chain(prefix):
    """shoulder -> elbow -> wrist (a real Maya parent chain), plus two
    finger roots under wrist (each with a child so 'deep descendant'
    resolution has somewhere to walk from) and two twist joints as
    elbow's children. Off-origin throughout. Returns a dict keyed by
    role -> joint name."""
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
    J['twist_b'] = j(f'{p}_twist_b', (16, 2, 0), J['elbow'])
    J['finger1_root'] = j(f'{p}_finger1_root', (22, 1, 0), J['wrist'])
    J['finger1_tip'] = j(f'{p}_finger1_tip', (24, 1, 0), J['finger1_root'])
    J['finger2_root'] = j(f'{p}_finger2_root', (22, -1, 0), J['wrist'])
    cmds.select(clear=True)
    return J


def _fresh_registry(name='limbtest_bp'):
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    cmds.file(new=True, force=True)
    return nodes.create_registry(name)


def test_limb_module_imports_and_public_api_shape():
    from maya_tools.rigging.fabricator import limb_node as ln
    for name in (
        'create_limb_node', 'find_limb_for_joint', 'get_limb_node',
        'all_limb_nodes', 'delete_limb_node', 'get_limb_type',
        'set_limb_type', 'is_implicit',
        'add_component', 'remove_component', 'list_components',
        'add_finger_root', 'remove_finger_root', 'list_finger_roots',
        'add_twist_upper', 'remove_twist_upper', 'list_twist_upper',
        'add_twist_lower', 'remove_twist_lower', 'list_twist_lower',
        'add_curl_excluded', 'remove_curl_excluded', 'list_curl_excluded',
    ):
        assert hasattr(ln, name), f'limb_node missing public API: {name}'


def test_create_limb_node_basic_and_get_limb_type():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('crudbasic')

    node = ln.create_limb_node('Arm_RibbonIK', J['shoulder'])
    assert node and cmds.objExists(node)
    assert ln.get_limb_type(node) == 'Arm_RibbonIK'
    assert ln.is_implicit(node) is False
    assert ln.get_limb_node(J['shoulder']) == node


def test_create_limb_node_implicit_flag():
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('implicitflag')

    node = ln.create_limb_node('TestArm', J['shoulder'], implicit=True)
    assert ln.is_implicit(node) is True

    node2_joint = J['wrist']
    node2 = ln.create_limb_node('Other', node2_joint, implicit=False)
    assert ln.is_implicit(node2) is False


def test_create_limb_node_duplicate_top_joint_raises():
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('duptop')

    ln.create_limb_node('Arm', J['shoulder'])
    raised = False
    try:
        ln.create_limb_node('Arm2', J['shoulder'])
    except RuntimeError:
        raised = True
    assert raised, 'creating a second limb node on the same top_joint should raise'


def test_create_limb_node_requires_registry():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    cmds.file(new=True, force=True)
    J = _make_limb_chain('noregistry')

    raised = False
    try:
        ln.create_limb_node('Arm', J['shoulder'])
    except RuntimeError:
        raised = True
    assert raised, 'create_limb_node without a fab_registry should raise'


def test_get_limb_node_direct_no_walk():
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('directnowalk')

    ln.create_limb_node('Arm', J['shoulder'])
    # elbow is a DESCENDANT of shoulder, not itself a top_joint — the
    # direct accessor must NOT walk up to find shoulder's node.
    assert ln.get_limb_node(J['elbow']) is None
    assert ln.get_limb_node(J['wrist']) is None


def test_set_limb_type_editable():
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('settype')

    node = ln.create_limb_node('Arm_RibbonIK', J['shoulder'])
    ln.set_limb_type(node, 'Arm_FKIK')
    assert ln.get_limb_type(node) == 'Arm_FKIK'


def test_components_accessor_crud():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator import nodes
    _fresh_registry()
    J = _make_limb_chain('compcrud')

    limb = ln.create_limb_node('TestArm', J['shoulder'])
    comp = nodes.create_component_node(
        component_id='compcrud_C0', component_type='SimpleFK',
        joints=[J['shoulder']], parent_plug='', side='md',
        options={}, persisted={},
    )

    assert ln.list_components(limb) == []
    assert ln.add_component(limb, comp) is True
    assert ln.list_components(limb) == [comp]
    # Idempotent: re-adding the same component is a no-op, not a dup.
    assert ln.add_component(limb, comp) is False
    assert ln.list_components(limb) == [comp]

    ln.remove_component(limb, comp)
    assert ln.list_components(limb) == []
    # comp itself must survive — remove is a connection edit only.
    assert cmds.objExists(comp)


def test_add_component_rejects_non_component_target():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('compnoncomp')

    limb = ln.create_limb_node('TestArm', J['shoulder'])

    # A bare network node (no fab_component marker) must be rejected.
    stray_network = cmds.createNode('network', name='compnoncomp_stray')
    assert ln.add_component(limb, stray_network) is False, (
        'add_component must reject a node without the fab_component marker')
    assert ln.list_components(limb) == []

    # A joint is not a component node either.
    assert ln.add_component(limb, J['wrist']) is False
    assert ln.list_components(limb) == []


def test_add_finger_root_rejects_non_joint_target():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('fingernonjoint')

    limb = ln.create_limb_node('TestArm', J['shoulder'])
    non_joint = cmds.group(empty=True, name='fingernonjoint_transform')
    assert ln.add_finger_root(limb, non_joint) is False, (
        'add_finger_root must reject a non-joint transform')
    assert ln.list_finger_roots(limb) == []


def test_add_twist_upper_rejects_non_joint_target():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('twistuppernonjoint')

    limb = ln.create_limb_node('Arm_RibbonIK', J['shoulder'])
    non_joint = cmds.group(empty=True, name='twistuppernonjoint_transform')
    assert ln.add_twist_upper(limb, non_joint) is False
    assert ln.list_twist_upper(limb) == []


def test_add_twist_lower_rejects_non_joint_target():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('twistlowernonjoint')

    limb = ln.create_limb_node('Arm_RibbonIK', J['shoulder'])
    non_joint = cmds.group(empty=True, name='twistlowernonjoint_transform')
    assert ln.add_twist_lower(limb, non_joint) is False
    assert ln.list_twist_lower(limb) == []


def test_add_curl_excluded_rejects_non_joint_target():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('curlnonjoint')

    limb = ln.create_limb_node('TestArm', J['shoulder'])
    non_joint = cmds.group(empty=True, name='curlnonjoint_transform')
    assert ln.add_curl_excluded(limb, non_joint) is False
    assert ln.list_curl_excluded(limb) == []


def test_finger_roots_ordered_add_preserves_authoring_order():
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('fingerorder')

    limb = ln.create_limb_node('TestArm', J['shoulder'])
    ln.add_finger_root(limb, J['finger2_root'])
    ln.add_finger_root(limb, J['finger1_root'])

    assert ln.list_finger_roots(limb) == [J['finger2_root'], J['finger1_root']], (
        'finger_roots must preserve AUTHORING order, not alphabetical or '
        'hierarchy order')


def test_finger_roots_remove_middle_preserves_remaining_order():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('fingerremovemiddle')

    limb = ln.create_limb_node('TestArm', J['shoulder'])
    # Five ad hoc finger roots authored in this exact order.
    roots = []
    for i in range(5):
        cmds.select(J['wrist'], replace=True)
        r = cmds.joint(name=f'fingerremovemiddle_root_{i}')
        roots.append(r)
    cmds.select(clear=True)
    for r in roots:
        ln.add_finger_root(limb, r)

    assert ln.list_finger_roots(limb) == roots

    # Remove the MIDDLE one (index 2).
    ln.remove_finger_root(limb, roots[2])
    expected = [roots[0], roots[1], roots[3], roots[4]]
    assert ln.list_finger_roots(limb) == expected, (
        f'removing a middle entry must preserve the remaining order: '
        f'expected {expected}, got {ln.list_finger_roots(limb)}')

    # A further add appends after the survivors, not into the freed slot.
    cmds.select(J['wrist'], replace=True)
    new_root = cmds.joint(name='fingerremovemiddle_root_new')
    cmds.select(clear=True)
    ln.add_finger_root(limb, new_root)
    assert ln.list_finger_roots(limb) == expected + [new_root]


def test_twist_upper_lower_crud():
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('twistcrud')

    limb = ln.create_limb_node('Arm_RibbonIK', J['shoulder'])
    assert ln.add_twist_upper(limb, J['twist_a']) is True
    assert ln.add_twist_lower(limb, J['twist_b']) is True
    assert ln.list_twist_upper(limb) == [J['twist_a']]
    assert ln.list_twist_lower(limb) == [J['twist_b']]

    ln.remove_twist_upper(limb, J['twist_a'])
    assert ln.list_twist_upper(limb) == []
    # twist_lower untouched by a twist_upper removal.
    assert ln.list_twist_lower(limb) == [J['twist_b']]


def test_curl_excluded_crud():
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('curlexcl')

    limb = ln.create_limb_node('TestArm', J['shoulder'])
    assert ln.list_curl_excluded(limb) == []
    ln.add_curl_excluded(limb, J['finger1_root'])
    ln.add_curl_excluded(limb, J['finger2_root'])
    assert set(ln.list_curl_excluded(limb)) == {J['finger1_root'], J['finger2_root']}

    ln.remove_curl_excluded(limb, J['finger1_root'])
    assert ln.list_curl_excluded(limb) == [J['finger2_root']]


def test_delete_limb_node_leaves_joints_and_components_intact_and_registry_clean():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator import nodes
    _fresh_registry()
    J = _make_limb_chain('deleteclean')

    limb = ln.create_limb_node('TestArm', J['shoulder'])
    comp = nodes.create_component_node(
        component_id='deleteclean_C0', component_type='SimpleFK',
        joints=[J['shoulder']], parent_plug='', side='md',
        options={}, persisted={},
    )
    ln.add_component(limb, comp)
    ln.add_finger_root(limb, J['finger1_root'])
    ln.add_twist_upper(limb, J['twist_a'])

    ln.delete_limb_node(limb)

    assert not cmds.objExists(limb)
    assert ln.all_limb_nodes() == []
    # Everything the limb referenced must still be alive.
    for j in (J['shoulder'], J['wrist'], J['finger1_root'], J['twist_a']):
        assert cmds.objExists(j), f'{j} must survive delete_limb_node'
    assert cmds.objExists(comp), 'component node must survive delete_limb_node'
    # Registry clean: no dangling reference to the deleted node.
    reg = nodes.get_registry()
    assert limb not in (nn_get_targets(reg, 'limbs')), (
        'registry must not still report the deleted limb node')


def nn_get_targets(node, attr):
    from maya_tools.utils.maya import network_nodes as nn
    return nn.get_message_targets(node, attr)


def test_find_limb_for_joint_resolves_from_deep_descendant():
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('deepdescend')

    limb = ln.create_limb_node('Arm_RibbonIK', J['shoulder'])
    # finger1_tip is several hops below shoulder (shoulder->wrist is
    # already 2 hops, finger1_root->finger1_tip 2 more).
    found = ln.find_limb_for_joint(J['finger1_tip'])
    assert found == limb, (
        f'a deep descendant should resolve to its arm limb: {found} != {limb}')

    # The top_joint itself must also resolve (self-match, no walk needed).
    assert ln.find_limb_for_joint(J['shoulder']) == limb


def test_find_limb_for_joint_returns_none_off_limb():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('offlimb')
    ln.create_limb_node('Arm_RibbonIK', J['shoulder'])

    cmds.select(clear=True)
    stray = cmds.joint(name='offlimb_unrelated_stray')
    cmds.select(clear=True)

    assert ln.find_limb_for_joint(stray) is None
    assert ln.find_limb_for_joint('does_not_exist_xyz') is None


def test_find_limb_for_joint_nested_returns_nearest():
    """A joint that sits under TWO limbs' top_joints (one nested inside
    the other's subtree) must resolve to the NEAREST (innermost) one —
    the documented nested-limb choice (see limb_node.py's module
    docstring; nested limbs are otherwise out of scope, SPEC 5)."""
    from maya_tools.rigging.fabricator import limb_node as ln
    _fresh_registry()
    J = _make_limb_chain('nestedtest')

    outer = ln.create_limb_node('Spine', J['shoulder'])
    inner = ln.create_limb_node('Arm_RibbonIK', J['wrist'])
    assert outer != inner

    # finger1_tip sits under BOTH: shoulder (outer, several hops up) and
    # wrist (inner, the nearer ancestor).
    found = ln.find_limb_for_joint(J['finger1_tip'])
    assert found == inner, (
        f'nested limbs must resolve to the NEAREST (innermost) one: '
        f'expected {inner} (wrist), got {found}')

    # wrist itself resolves to its own (inner) node too, never the outer.
    assert ln.find_limb_for_joint(J['wrist']) == inner


# ─────────────────────────────────────────────────────────────────────────
# 2026-07-09 checkpoint-blocker fix, item 2a: discover_fingers structural
# >=2-joint exclusion. Pure logic (no cmds calls in discover_fingers
# itself) — synthetic joint trees via the real blueprint.schema
# dataclasses, same duck-type + fixture idiom test_ribbon_ik_arm.py's own
# discover_fingers tests use (_fake_blueprint/_ue5_hand_pairs/
# _cartoon_12finger_pairs), reproduced locally here since this file is
# the companion suite discover_fingers's own docstring names for
# offscreen-testable logic.
# ─────────────────────────────────────────────────────────────────────────

def _fake_blueprint(pairs):
    """pairs: list of (name, parent_or_None). Returns a
    blueprint.schema.Blueprint with skeleton_joints built from `pairs`."""
    from maya_tools.rigging.fabricator.blueprint.schema import Blueprint, JointSpec
    joints = [JointSpec(name=n, parent=p) for n, p in pairs]
    return Blueprint(name='test', skeleton_joints=joints)


def _ue5_hand_plus_weapon_pairs(wrist='wrist'):
    """UE mannequin shape: 5 real fingers (thumb 3-joint, index/middle/
    ring/pinky 4-joint metacarpal-first) PLUS a single-joint weapon_l
    socket hung directly off the wrist — the exact false-positive
    Adrian's real scene hit (2026-07-09 checkpoint-blocker report)."""
    pairs = [('thumb_01', wrist), ('thumb_02', 'thumb_01'), ('thumb_03', 'thumb_02')]
    for finger in ('index', 'middle', 'ring', 'pinky'):
        mc = f'{finger}_metacarpal'
        pairs += [
            (mc, wrist),
            (f'{finger}_01', mc),
            (f'{finger}_02', f'{finger}_01'),
            (f'{finger}_03', f'{finger}_02'),
        ]
    pairs.append(('weapon_l', wrist))  # single joint, no children
    return pairs


def test_discover_fingers_excludes_single_joint_weapon_child():
    """The false-positive itself: weapon_l (a single-joint child of the
    wrist, no descendants) must NOT appear in discover_fingers' output —
    excluded structurally by chain length, not by name. The 5 real
    fingers are unaffected."""
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    bp = _fake_blueprint([('wrist', None)] + _ue5_hand_plus_weapon_pairs())
    fingers = hc.discover_fingers('wrist', bp)

    roots = {f['root_joint'] for f in fingers}
    assert 'weapon_l' not in roots, (
        f'a single-joint wrist-child must never be discovered as a '
        f'finger: {roots}')
    assert roots == {
        'thumb_01', 'index_metacarpal', 'middle_metacarpal',
        'ring_metacarpal', 'pinky_metacarpal'}, roots
    assert len(fingers) == 5, fingers


def test_discover_fingers_12finger_cartoon_unaffected_by_min_joint_fix():
    """Regression guard: the >=2-joint structural exclusion must not
    touch any REAL finger — every one of the 12 cartoon fingers (3
    joints each) still discovers, unchanged from test_ribbon_ik_arm.py's
    own test_discover_fingers_12finger_cartoon_no_metacarpal."""
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    pairs = [('wrist', None)]
    for i in range(12):
        root = f'finger{i:02d}_01'
        pairs += [
            (root, 'wrist'),
            (f'finger{i:02d}_02', root),
            (f'finger{i:02d}_03', f'finger{i:02d}_02'),
        ]
    bp = _fake_blueprint(pairs)
    fingers = hc.discover_fingers('wrist', bp)
    assert len(fingers) == 12, len(fingers)
    for i, f in enumerate(fingers):
        assert f['root_joint'] == f'finger{i:02d}_01'


def test_discover_fingers_2joint_stub_finger_still_counts():
    """The shortest chain that IS still a finger: root + exactly one
    child (2 joints total, no further descendants) — the >=2 threshold
    is inclusive, not '>2'."""
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    bp = _fake_blueprint([
        ('wrist', None),
        ('stub_01', 'wrist'),
        ('stub_02', 'stub_01'),
        ('weapon_l', 'wrist'),  # single-joint sibling, still excluded
    ])
    fingers = hc.discover_fingers('wrist', bp)
    roots = {f['root_joint'] for f in fingers}
    assert roots == {'stub_01'}, roots
    stub = next(f for f in fingers if f['root_joint'] == 'stub_01')
    assert stub['joints'] == ['stub_01', 'stub_02'], stub


# ─────────────────────────────────────────────────────────────────────────
# 2026-07-09 checkpoint-blocker fix, item 1: limb_node.limb_can_add_finger
# — the '+ Add Finger' button's enabled-state contract.
# ─────────────────────────────────────────────────────────────────────────
def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith('test_') and callable(fn)
    ]
    print(f'Running {len(tests)} tests from test_limb_units.py...')
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
