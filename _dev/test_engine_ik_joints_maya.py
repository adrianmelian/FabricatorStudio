# _dev/test_engine_ik_joints_maya.py
"""mayapy scene tests for Build Engine IK Joints (fabricator/engine_ik.py)
and the FollowJoint set-but-missing-target softening it depends on.

Design record: workspace/2026-07-10_engine-ik-joints/SPEC.md (MrMiata
depot). The action creates the UE5 engine-reference joint set snapped to
live counterparts with FollowJoint components — chosen over a limb
fragment because a fragment bakes template proportions while the snap
reads the actual skeleton.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_engine_ik_joints_maya.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


class Skip(Exception):
    """Raise from a test body to mark it SKIPPED — never silently
    absorbed into the pass count (same contract as the sibling suites)."""


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


def _make_ue_named_skeleton(with_hands=True):
    """Minimal UE-named deform skeleton with DISTINCT world positions so
    snap assertions are meaningful. Registry + World per the standard
    fixture shape."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 0, 0), name='root')
    pelvis = cmds.joint(p=(0, 90, 2), name='pelvis')
    cmds.select(root)
    cmds.joint(p=(12, 8, 3), name='foot_l')
    cmds.select(root)
    cmds.joint(p=(-12, 8, 3), name='foot_r')
    if with_hands:
        cmds.select(root)
        cmds.joint(p=(55, 140, 7), name='hand_l')
        cmds.select(root)
        cmds.joint(p=(-55, 140, 7), name='hand_r')
    cmds.select(clear=True)

    nodes.create_registry('engineik_bp')
    nodes.set_registry_root_joint(root)
    nodes.create_component_node(
        component_id='engineik_world', component_type='World',
        joints=[root], parent_plug='', side='md',
        options={}, persisted={},
    )
    return root


def _world_pos(node):
    import maya.cmds as cmds
    return cmds.xform(node, q=True, ws=True, t=True)


def _close(a, b, tol=1e-4):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def test_action_creates_hierarchy_snaps_and_follow_components():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import engine_ik, nodes

    _make_ue_named_skeleton()
    report = engine_ik.build_engine_ik_joints()

    expected_parents = {
        'center_of_mass': 'root',
        'ik_foot_root':   'root',
        'ik_foot_l':      'ik_foot_root',
        'ik_foot_r':      'ik_foot_root',
        'ik_hand_root':   'root',
        'ik_hand_gun':    'ik_hand_root',
        'ik_hand_l':      'ik_hand_gun',
        'ik_hand_r':      'ik_hand_gun',
        'interaction':    'root',
    }
    for j, want_parent in expected_parents.items():
        assert cmds.objExists(j), f'{j} not created'
        got = (cmds.listRelatives(j, parent=True) or [None])[0]
        assert got == want_parent, f'{j}: parent {got!r}, want {want_parent!r}'
    assert sorted(report['created']) == sorted(expected_parents), (
        report['created'])

    # Snap correctness: each followed joint sits ON its counterpart.
    for ik_j, counterpart in (
            ('ik_foot_l', 'foot_l'), ('ik_foot_r', 'foot_r'),
            ('ik_hand_l', 'hand_l'), ('ik_hand_r', 'hand_r'),
            ('ik_hand_gun', 'hand_r'), ('center_of_mass', 'pelvis')):
        assert _close(_world_pos(ik_j), _world_pos(counterpart)), (
            f'{ik_j} not snapped to {counterpart}: '
            f'{_world_pos(ik_j)} vs {_world_pos(counterpart)}')

    # Unfollowed entries sit at the root.
    for parked in ('ik_foot_root', 'ik_hand_root', 'interaction'):
        assert _close(_world_pos(parked), _world_pos('root')), parked

    # FollowJoint components with the right targets, followed set only.
    follows = {}
    for cnode in nodes.get_all_component_nodes():
        if nodes.get_component_type(cnode) == 'FollowJoint':
            j = nodes.get_component_joints(cnode)[0].split('|')[-1]
            follows[j] = nodes.get_component_options(cnode).get(
                'follow_target', '')
    assert follows == {
        'center_of_mass': 'pelvis',
        'ik_foot_l': 'foot_l', 'ik_foot_r': 'foot_r',
        'ik_hand_gun': 'hand_r',
        'ik_hand_l': 'hand_l', 'ik_hand_r': 'hand_r',
    }, follows
    assert not report['missing_targets'], report['missing_targets']


def test_action_is_idempotent_and_re_snaps():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import engine_ik, nodes

    _make_ue_named_skeleton()
    engine_ik.build_engine_ik_joints()

    n_joints_before = len(cmds.ls('ik_*', 'center_of_mass', 'interaction',
                                  type='joint'))
    n_comps_before = len(nodes.get_all_component_nodes())

    # Proportions change mid-edit: the foot moves; re-run must re-snap.
    cmds.setAttr('foot_l.translate', 20, 9, -4)
    report = engine_ik.build_engine_ik_joints()

    assert not report['created'], (
        f're-run must create nothing: {report["created"]}')
    assert not report['components_created'], (
        f're-run must add no components: {report["components_created"]}')
    assert len(cmds.ls('ik_*', 'center_of_mass', 'interaction',
                       type='joint')) == n_joints_before
    assert len(nodes.get_all_component_nodes()) == n_comps_before
    assert _close(_world_pos('ik_foot_l'), _world_pos('foot_l')), (
        'ik_foot_l did not re-snap to the moved foot_l')


def test_missing_counterparts_park_warn_and_never_crash_build():
    """Handless skeleton: hand entries are created at their parent with
    the follow target recorded; the report names every gap; and — the
    softening under test — Build Rig COMPLETES with the ik hand joints
    simply unconstrained."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import engine_ik, fs_app

    _make_ue_named_skeleton(with_hands=False)
    report = engine_ik.build_engine_ik_joints()

    assert report['missing_targets'] == {
        'ik_hand_gun': 'hand_r',
        'ik_hand_l': 'hand_l',
        'ik_hand_r': 'hand_r',
    }, report['missing_targets']
    for j in ('ik_hand_gun', 'ik_hand_l', 'ik_hand_r'):
        assert cmds.objExists(j), f'{j} must exist despite the gap'

    # The set-but-missing targets must not kill Build Rig.
    fs_app.build_modules()
    for j in ('ik_hand_l', 'ik_hand_r', 'ik_hand_gun'):
        cons = cmds.listRelatives(j, type='constraint') or []
        assert not cons, (
            f'{j}: no constraint expected for a missing target, got {cons}')
    # Feet DID have counterparts — their constraints must exist.
    cons = cmds.listRelatives('ik_foot_l', type='constraint') or []
    assert cons, 'ik_foot_l: expected live follow constraints'
    fs_app.unbuild_modules()


def test_follow_joint_set_but_missing_target_is_warned_noop():
    """Direct FollowJoint softening pin (independent of the action): a
    component whose follow_target names a joint absent from the scene
    builds as an unconstrained no-op instead of raising."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(name='softroot')
    follower = cmds.joint(name='softfollower')
    cmds.select(clear=True)
    nodes.create_registry('soft_bp')
    nodes.set_registry_root_joint(root)
    nodes.create_component_node(
        component_id='soft_world', component_type='World',
        joints=[root], parent_plug='', side='md', options={}, persisted={},
    )
    nodes.create_component_node(
        component_id='soft_follow', component_type='FollowJoint',
        joints=[follower], parent_plug='', side='md',
        options={'follow_target': 'joint_that_does_not_exist'},
        persisted={},
    )

    fs_app.build_modules()   # must not raise
    cons = cmds.listRelatives(follower, type='constraint') or []
    assert not cons, f'expected an unconstrained no-op, got {cons}'
    fs_app.unbuild_modules()


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    check("test_action_creates_hierarchy_snaps_and_follow_components",
          test_action_creates_hierarchy_snaps_and_follow_components)
    check("test_action_is_idempotent_and_re_snaps",
          test_action_is_idempotent_and_re_snaps)
    check("test_missing_counterparts_park_warn_and_never_crash_build",
          test_missing_counterparts_park_warn_and_never_crash_build)
    check("test_follow_joint_set_but_missing_target_is_warned_noop",
          test_follow_joint_set_but_missing_target_is_warned_noop)

    if FAILURES:
        print(f"ENGINE IK JOINTS TESTS: {len(FAILURES)} FAILED "
              f"({len(SKIPS)} SKIP)")
        sys.exit(1)
    if SKIPS:
        print(f"ENGINE IK JOINTS TESTS: OK - {len(SKIPS)} SKIP "
              f"(not counted as pass): {SKIPS}")
    else:
        print("ENGINE IK JOINTS TESTS: OK - 0 SKIP")


if __name__ == "__main__":
    main()
