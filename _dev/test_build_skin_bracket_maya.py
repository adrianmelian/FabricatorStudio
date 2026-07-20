"""The build's skin bracket: disconnect at start, reconnect at the end
(Adrian, 2026-07-13).

A mesh bound in EDIT mode is live. The build then moves joints (the orient bake,
the ribbon, the twist aimers). If the skin stayed live it would ride that motion
and candy-wrap, or lose an influence and collapse to origin. So build_modules
brackets the whole build: disconnect_all_skins() up front detaches every skin,
and reconnect_all_skins() dead last re-activates each at the FINAL joint pose.

This exercises the mechanism the bracket relies on, at the skin_connect level:
bind live -> disconnect -> move a joint (the user reorienting) -> reconnect, and
the mesh must come back at rest (no candy-wrap, no origin collapse) and still
deform from the new bind.

    PYTHONNOUSERSITE=1 mayapy _dev/test_build_skin_bracket_maya.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import maya.standalone

maya.standalone.initialize(name='python')

import maya.cmds as cmds  # noqa: E402

from maya_tools.skinning import skin_connect_app as scn  # noqa: E402

PASSED = []
FAILED = []


def check(name, fn):
    cmds.file(new=True, force=True)
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


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _vtx(mesh):
    n = cmds.polyEvaluate(mesh, vertex=True)
    return [tuple(cmds.xform(f'{mesh}.vtx[{i}]', q=True, ws=True, t=True))
            for i in range(n)]


def _skinned_plane():
    """Two joints (j0 at origin, j1 up +Y10) and a plane spanning them, bound
    smooth — stands in for a mesh the user skinned while in edit mode. Returns
    (j0, j1, plane, skinCluster)."""
    cmds.select(clear=True)
    j0 = cmds.joint(name='j0', position=(0, 0, 0))
    j1 = cmds.joint(name='j1', position=(0, 10, 0))
    cmds.select(clear=True)
    plane = cmds.polyPlane(name='edit_bound', axis=(0, 0, 1),
                           width=4, height=10, sx=1, sy=6)[0]
    cmds.xform(plane, ws=True, t=(0, 5, 0))          # span y=0..10
    sc = cmds.skinCluster(j0, j1, plane, toSelectedBones=True,
                          maximumInfluences=2, dropoffRate=4.0)[0]
    return j0, j1, plane, sc


def test_disconnect_detaches_a_live_edit_mode_bind():
    """Opening bracket: a freshly (edit-mode) bound skin is live; the build's
    up-front disconnect must make it dormant so joint moves don't deform it."""
    _j0, _j1, _plane, sc = _skinned_plane()
    assert scn._is_live(sc), 'a fresh bind should be live'
    scn.disconnect_all_skins()
    assert not scn._is_live(sc), \
        'disconnect must leave the skin dormant for the build'


def test_bracket_preserves_rest_shape_across_a_joint_reorient():
    """The whole point: bind live -> disconnect -> move a joint (an edit-mode
    reorient) -> reconnect. The bind is reset to the moved pose, so the mesh is
    back at REST — every vert unchanged. A moved vert means a stale bind
    (candy-wrap) or a lost influence (origin collapse), the two bugs the
    bracket exists to kill."""
    _j0, j1, plane, sc = _skinned_plane()
    before = _vtx(plane)

    scn.disconnect_all_skins()
    cmds.xform(j1, ws=True, t=(6, 10, 0))            # user drags the joint...
    cmds.xform(j1, ws=True, ro=(0, 0, 30))           # ...and reorients it
    scn.reconnect_all_skins()

    after = _vtx(plane)
    worst = max(_dist(b, a) for b, a in zip(before, after))
    assert worst < 1e-3, \
        f'mesh moved after the bracket (worst vert {worst:.4f}): stale bind ' \
        f'(candy-wrap) or lost influence (origin collapse)'
    assert scn._is_live(sc), 'reconnect must leave the skin live again'


def test_reconnected_skin_deforms_from_the_new_bind():
    """After the bracket the skin is not merely frozen — posing the joint past
    the new bind still deforms the mesh."""
    _j0, j1, plane, _sc = _skinned_plane()
    scn.disconnect_all_skins()
    cmds.xform(j1, ws=True, t=(6, 10, 0))
    scn.reconnect_all_skins()
    rest = _vtx(plane)

    cmds.xform(j1, ws=True, relative=True, t=(5, 0, 0))   # pose past the bind
    posed = _vtx(plane)
    worst = max(_dist(r, p) for r, p in zip(rest, posed))
    assert worst > 0.5, \
        f'the reconnected skin does not deform when the joint moves ({worst:.3f})'


def _bind_worst(sc):
    """Worst bindPreMatrix-vs-joint-worldInverse element delta for a cluster."""
    conns = cmds.listConnections(sc + '.matrix', connections=True, plugs=True,
                                 source=True, destination=False) or []
    worst = 0.0
    for k in range(0, len(conns), 2):
        j = conns[k + 1].split('.')[0]
        if not cmds.objExists(j):
            continue
        idx = int(conns[k][conns[k].index('[') + 1:conns[k].index(']')])
        bpm = cmds.getAttr(sc + '.bindPreMatrix[' + str(idx) + ']')
        wim = cmds.getAttr(j + '.worldInverseMatrix[0]')
        worst = max(worst, max(abs(a - b) for a, b in zip(bpm, wim)))
    return worst


def test_reset_fixes_a_live_reoriented_cluster_that_reconnect_skips():
    """The export candy-wrapper root cause (Adrian, 2026-07-14): a cluster left
    LIVE while its joint is re-oriented keeps a stale bind. reconnect_all_skins
    SKIPS live clusters, so the stale bind survives; reset_all_binds_to_pose
    (disconnect+reconnect) re-derives it. This is the twist-joint case exactly
    — the twist got its Parent+180 while the skin stayed live."""
    _j0, j1, _plane, sc = _skinned_plane()
    assert scn._is_live(sc), 'fresh bind should be live'
    assert _bind_worst(sc) < 1e-3, 'fresh bind should be consistent'

    cmds.setAttr(j1 + '.rotate', 180, 0, 0)          # re-orient WHILE live
    assert _bind_worst(sc) > 0.1, 'a live re-orient must stale the bind'

    scn.reconnect_all_skins()
    assert _bind_worst(sc) > 0.1, \
        'reconnect_all_skins must SKIP the live cluster (bind stays stale)'

    scn.reset_all_binds_to_pose()
    assert _bind_worst(sc) < 1e-3, \
        'reset_all_binds_to_pose must re-derive the stale bind'
    assert scn._is_live(sc), 'reset leaves the cluster live'


if __name__ == '__main__':
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'\nbuild skin bracket (disconnect start / reconnect end) — '
          f'{len(tests)} tests\n')
    for name, fn in tests:
        check(name, fn)
    print()
    if FAILED:
        print(f'FAILED ({len(FAILED)}/{len(tests)}):')
        for name, err in FAILED:
            print(f'  {name}: {err}')
        sys.exit(1)
    print(f'ALL PASS ({len(PASSED)})')
