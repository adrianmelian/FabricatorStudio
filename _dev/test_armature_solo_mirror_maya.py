# _dev/test_armature_solo_mirror_maya.py
"""Live Mirror x SOLO handles (Kris request, 2026-07-21).

A solo nudge moves only its own joint. Without mirror wiring, a
symmetric armature goes ASYMMETRIC the moment the rigger nudges one side
with Symmetry on: the far side simply stays behind. Silently. That is
worse than the feature not existing, because the rigger trusts Symmetry.

Every assertion here measures the JOINTS in world space, never the
handle's local channels. The deliverable is "a symmetric armature stays
symmetric through a nudge", and the armature legitimately rewrites local
translate during its aim-axis normalize, so a local assert would fail
against correct code.

Run: mayapy _dev/test_armature_solo_mirror_maya.py
"""
__author__ = "Adrian Melian"

import sys

import maya.standalone
maya.standalone.initialize(name='python')

import maya.cmds as cmds  # noqa: E402

sys.path.insert(0, 'D:/Documents/FabricatorStudio')

from maya_tools.rigging.fabricator import armature          # noqa: E402
from maya_tools.rigging.fabricator import armature_mirror   # noqa: E402

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


def _symmetric_arms():
    """root -> {lf,rt}_shoulder -> elbow -> wrist, mirrored across YZ.

    The elbow sits off the shoulder->wrist line (z=1) so the chain is
    genuinely bent: a straight chain can solve to an identity aim, which
    would make a "did the far side follow" assertion pass for the wrong
    reason.
    """
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 0, 0), name='root')
    for side, sign in (('lf', 1.0), ('rt', -1.0)):
        cmds.select(root)
        cmds.joint(p=(sign * 5, 10, 0), name=f'{side}_shoulder')
        cmds.joint(p=(sign * 14, 10, 1), name=f'{side}_elbow')
        cmds.joint(p=(sign * 22, 10, 0), name=f'{side}_wrist')
    cmds.select(clear=True)
    armature.build_armature(root=root)
    return root


def _wpos(node):
    return cmds.xform(node, q=True, ws=True, t=True)


def test_a_solo_nudge_mirrors_to_the_far_side():
    _symmetric_arms()
    armature_mirror.enable()
    lf_solo = armature.solo_handle_for_joint('lf_elbow')
    assert lf_solo, 'no solo handle on lf_elbow'

    cmds.select(lf_solo, replace=True)
    armature_mirror._wire_direction('lf')

    before_rt = _wpos('rt_elbow')
    cmds.setAttr(f'{lf_solo}.tx', 2.5)
    cmds.setAttr(f'{lf_solo}.ty', 1.5)
    cmds.setAttr(f'{lf_solo}.tz', -0.75)

    lf, rt = _wpos('lf_elbow'), _wpos('rt_elbow')
    assert any(abs(a - b) > 1e-6 for a, b in zip(rt, before_rt)), (
        'rt_elbow never moved — the mirror is not wired at all')
    # Mirrored across YZ: x opposite, y and z matched.
    assert abs(lf[0] + rt[0]) < 1e-3, f'x not mirrored: {lf} vs {rt}'
    assert abs(lf[1] - rt[1]) < 1e-3, f'y not matched: {lf} vs {rt}'
    assert abs(lf[2] - rt[2]) < 1e-3, f'z not matched: {lf} vs {rt}'


def test_the_mirrored_nudge_does_not_drag_the_far_subtree():
    """Solo means solo on BOTH sides. If the far side followed via its
    main ctrl instead of its own handle, the wrist below it would move
    too — which is exactly the bug this feature exists to avoid."""
    _symmetric_arms()
    armature_mirror.enable()
    lf_solo = armature.solo_handle_for_joint('lf_elbow')
    cmds.select(lf_solo, replace=True)
    armature_mirror._wire_direction('lf')

    before_wrist = _wpos('rt_wrist')
    cmds.setAttr(f'{lf_solo}.tx', 3.0)
    cmds.setAttr(f'{lf_solo}.ty', 2.0)

    after_wrist = _wpos('rt_wrist')
    assert all(abs(a - b) < 1e-3 for a, b in zip(before_wrist, after_wrist)), (
        f'rt_wrist was dragged by the mirrored nudge: '
        f'{before_wrist} -> {after_wrist}')


def test_grabbing_the_far_solo_flips_the_driver_side():
    """_side_of_selection has to recognise a solo handle. If it does
    not, selecting the right handle leaves the LEFT driving, and the
    rigger's first nudge is overwritten the instant they let go."""
    _symmetric_arms()
    armature_mirror.enable()
    rt_solo = armature.solo_handle_for_joint('rt_elbow')
    cmds.select(rt_solo, replace=True)
    assert armature_mirror._side_of_selection() == 'rt', (
        'selecting a right solo handle did not read as the rt side')


def test_disable_severs_the_solo_link_and_leaves_no_nodes():
    _symmetric_arms()
    armature_mirror.enable()
    lf_solo = armature.solo_handle_for_joint('lf_elbow')
    rt_solo = armature.solo_handle_for_joint('rt_elbow')
    cmds.select(lf_solo, replace=True)
    armature_mirror._wire_direction('lf')

    armature_mirror.disable()

    leftover = cmds.ls('*_amtMirror_*') or []
    assert not leftover, f'mirror DG nodes survived disable: {leftover}'
    for plug in ('tx', 'ty', 'tz'):
        incoming = cmds.listConnections(f'{rt_solo}.{plug}', source=True,
                                        destination=False) or []
        assert not incoming, (
            f'rt solo {plug} still driven after disable: {incoming}')


def test_an_armature_with_no_solo_handles_still_mirrors():
    """Backwards compatibility: a scene built before solo handles
    existed has none, and the ctrl mirror must not care."""
    _symmetric_arms()
    for h in armature.solo_handles():
        cmds.delete(h)
    armature_mirror.enable()
    lf_ctrl = armature.ctrl_for_joint('lf_elbow')
    cmds.select(lf_ctrl, replace=True)
    armature_mirror._wire_direction('lf')   # must not raise

    cmds.setAttr(f'{lf_ctrl}.ty', 3.0)
    lf, rt = _wpos('lf_elbow'), _wpos('rt_elbow')
    assert abs(lf[1] - rt[1]) < 1e-3, (
        f'ctrl mirror broke when solo handles were absent: {lf} vs {rt}')


if __name__ == '__main__':
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'\nLive Mirror x solo handles — {len(tests)} tests\n')
    for name, fn in tests:
        check(name, fn)

    print()
    if FAILED:
        print(f'FAILED ({len(FAILED)}/{len(tests)}):')
        for name, err in FAILED:
            print(f'  {name}: {err}')
        sys.exit(1)
    print(f'ALL PASS ({len(PASSED)})')
