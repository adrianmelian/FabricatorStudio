"""The build-time bind_joints selection set (Adrian, 2026-07-13).

On build, a 'bind_joints' objectSet is baked with the whole deform skeleton —
every joint under the rig root (root excluded) — so skinning has one click to
select exactly what gets skinned.

    PYTHONNOUSERSITE=1 mayapy _dev/test_selection_sets_bind_joints_maya.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import maya.standalone

maya.standalone.initialize(name='python')

import maya.cmds as cmds  # noqa: E402

from maya_tools.rigging.fabricator import selection_sets as ss  # noqa: E402
from maya_tools.rigging.fabricator import nodes  # noqa: E402

PASSED = []
FAILED = []


def check(name, fn):
    cmds.file(new=True, force=True)
    _root = nodes.get_registry_root_joint
    _comps = nodes.get_all_component_nodes
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
    finally:
        nodes.get_registry_root_joint = _root
        nodes.get_all_component_nodes = _comps


def _skeleton():
    """root -> pelvis -> {spine, thigh_l(+twist), thigh_r}. The root is the
    origin anchor; everything below it is the deform skeleton."""
    cmds.select(clear=True)
    root = cmds.joint(name='root', position=(0, 0, 0))
    pelvis = cmds.joint(name='pelvis', position=(0, 10, 0))
    cmds.joint(name='spine_01', position=(0, 15, 0))
    cmds.select(pelvis)
    thigh = cmds.joint(name='thigh_l', position=(2, 9, 0))
    cmds.joint(name='calf_l', position=(2, 4, 0))
    cmds.select(thigh)
    cmds.joint(name='thigh_twist_01_l', position=(2, 7, 0))   # childless twist
    cmds.select(clear=True)
    # Point the module's root lookup at our root, no full registry needed.
    nodes.get_registry_root_joint = lambda registry=None: root
    return root


def test_bind_joints_is_the_root_subtree_excluding_root():
    _skeleton()
    got = {j.split('|')[-1] for j in ss._bind_joints()}
    assert got == {'pelvis', 'spine_01', 'thigh_l', 'calf_l',
                   'thigh_twist_01_l'}, f'unexpected members: {sorted(got)}'
    assert 'root' not in got, 'the root anchor must NOT be a bind joint'


def test_bind_joints_excludes_transient_rig_helpers():
    """THE fix (Adrian, 2026-07-13): a ribbon roll joint sits UNDER a bind joint,
    so the root-subtree walk sweeps it in — but it is deleted+recreated every
    build, and a mesh bound to it explodes on rebuild. It is tracked in a
    `*_nodes` cleanup bucket, and must be excluded from the bind set."""
    root = _skeleton()
    # A roll joint parented under a bind joint (calf_l), tracked as a rig helper.
    cmds.select('calf_l')
    roll = cmds.joint(name='thigh_l_calf_l_roll_jnt', position=(0, -3, 0))
    cmds.select(clear=True)
    from maya_tools.utils.maya import network_nodes as nn
    comp = cmds.createNode('network', name='comp1')
    nn.connect_message_multi(roll, comp, 'roll_dg_nodes')
    nodes.get_all_component_nodes = lambda registry=None: [comp]

    got = {j.split('|')[-1] for j in ss._bind_joints()}
    assert 'calf_l' in got, 'a real bind joint must stay in the set'
    assert 'thigh_l_calf_l_roll_jnt' not in got, \
        'the roll joint (rig helper) must be EXCLUDED — binding to it explodes'


def test_rebuild_bakes_a_bind_joints_set():
    _skeleton()
    ss.rebuild_selection_sets()
    assert cmds.objExists('bind_joints'), 'no bind_joints set was created'
    assert cmds.nodeType('bind_joints') == 'objectSet'
    members = {m.split('|')[-1] for m in (cmds.sets('bind_joints', q=True) or [])}
    assert members == {'pelvis', 'spine_01', 'thigh_l', 'calf_l',
                       'thigh_twist_01_l'}, f'set members: {sorted(members)}'


def test_bind_joints_rides_the_master_set_and_its_lifecycle():
    _skeleton()
    ss.rebuild_selection_sets()
    assert cmds.objExists(ss.MASTER_SET), 'master set missing'
    assert 'bind_joints' in (cmds.sets(ss.MASTER_SET, q=True) or []), \
        'bind_joints is not under the master set'
    ss.delete_selection_sets()
    assert not cmds.objExists('bind_joints'), \
        'bind_joints survived unbuild — it must be torn down with the rig'


def test_no_root_joint_is_a_safe_no_op():
    nodes.get_registry_root_joint = lambda registry=None: ''
    assert ss._bind_joints() == []
    ss.rebuild_selection_sets()   # must not raise
    assert not cmds.objExists('bind_joints')


if __name__ == '__main__':
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'\nbind_joints selection set — {len(tests)} tests\n')
    for name, fn in tests:
        check(name, fn)
    print()
    if FAILED:
        print(f'FAILED ({len(FAILED)}/{len(tests)}):')
        for name, err in FAILED:
            print(f'  {name}: {err}')
        sys.exit(1)
    print(f'ALL PASS ({len(PASSED)})')
