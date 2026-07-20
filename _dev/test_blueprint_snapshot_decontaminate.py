# _dev/test_blueprint_snapshot_decontaminate.py
"""mayapy test: the blueprint snapshot de-contaminates the mirror
.message over-connection (CLAUDE.md Fabricator gotcha #1) so a saved
template never bakes a doubled joints[] set.

Regression for the 2026-07-11 BaseMale_Rig reload failure: mirroring
appended the opposite side's joints onto the SOURCE component's joints[]
multi; the raw snapshot serialized all of them, so on reload joint_names
went over the contract bound and Build aborted with 'N module(s) need
manual joint repair'. The fix bounds an over-connected multi to the
component's contract at snapshot time (the originals are first, the
mirror echo is appended).

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_blueprint_snapshot_decontaminate.py
"""
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


def _make_component(ctype, real_joints, echo_joints):
    """Fresh scene + registry + one component holding real_joints, then
    simulate the mirror gotcha by appending echo_joints' .message onto the
    joints[] multi at the tail (exactly what mirrorJoint/duplicate does)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes

    cmds.file(new=True, force=True)
    nodes.create_registry('SnapshotTest')

    for j in real_joints + echo_joints:
        cmds.select(clear=True)
        cmds.joint(name=j)

    cnode = nodes.create_component_node(
        component_id=f'{real_joints[0]}_{ctype.lower()}',
        component_type=ctype,
        joints=real_joints,
        parent_plug='',
        side='l',
        options={},
        persisted={},
    )
    # Append the echo joints at the next free multi indices (the gotcha).
    idx = len(real_joints)
    for j in echo_joints:
        cmds.connectAttr(f'{j}.message', f'{cnode}.joints[{idx}]')
        idx += 1
    return cnode


def test_overconnected_ikarm_snapshots_to_contract():
    """6 live (3 real + 3 mirror echo) -> serialized as the 3 real only."""
    from maya_tools.rigging.fabricator import fs_app
    cnode = _make_component(
        'IKArm',
        ['upperarm_l', 'lowerarm_l', 'hand_l'],
        ['upperarm_r', 'lowerarm_r', 'hand_r'],
    )
    spec = fs_app._snapshot_single_component_spec(cnode)
    assert spec.joints == ['upperarm_l', 'lowerarm_l', 'hand_l'], spec.joints


def test_overconnected_simplefk_snapshots_to_one():
    """2 live (clavicle_l + clavicle_r echo) -> serialized as clavicle_l."""
    from maya_tools.rigging.fabricator import fs_app
    cnode = _make_component('SimpleFK', ['clavicle_l'], ['clavicle_r'])
    spec = fs_app._snapshot_single_component_spec(cnode)
    assert spec.joints == ['clavicle_l'], spec.joints


def test_clean_component_snapshot_unchanged():
    """A correctly-connected component (no echo) serializes verbatim —
    the fix only trims when the multi exceeds the contract."""
    from maya_tools.rigging.fabricator import fs_app
    cnode = _make_component('IKArm', ['upperarm_l', 'lowerarm_l', 'hand_l'], [])
    spec = fs_app._snapshot_single_component_spec(cnode)
    assert spec.joints == ['upperarm_l', 'lowerarm_l', 'hand_l'], spec.joints


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'Running {len(tests)} tests from test_blueprint_snapshot_decontaminate.py...')
    for name, fn in tests:
        check(name, fn)

    print(f'\n{len(tests) - len(FAILURES)} passed, {len(FAILURES)} failed '
          f'(of {len(tests)})')
    if FAILURES:
        print('\nFAILURES:')
        for f in FAILURES:
            print(f'  - {f}')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
