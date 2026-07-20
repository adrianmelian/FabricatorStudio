"""Engine up-axis (Z-up / Unreal) export conversion (Adrian, 2026-07-14).

Root cause this covers: the export contract oriented the root joint to
Maya-world identity, so the FBX skeleton was Y-up native to the bone. A Z-up
engine (Unreal) folds its Y-up import conversion into the root bone, leaving
a +90 the engine-authored skeletons (SKM_Manny) don't have — every engine
animation then played the character pitched 90 deg head-first ("swimming").
The fix folds RX(-90) into the root joint's frame at export
(export_core.orient_root_for_z_up_engine), children re-expressed, world pose
untouched — verified against the SKM_Manny_Simple source (root global =
RX(-90) in a Y-up file, joint locals Z-up native).

Covered:
  - engine_up_axis resolver precedence (override > config > 'y') + validation;
  - static conversion: root frame lands at RX(-90), children's world poses
    and the root's position don't move;
  - animated conversion: same invariants on every frame of a keyed clip;
  - the runner surgery end-to-end with engine_up_axis='z': the re-imported
    FBX root carries the -90 arrangement and the mesh is not candy-wrapped.

Run:
PYTHONNOUSERSITE=1 PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen mayapy maya_tools/export/_dev/test_engine_up_axis_maya.py

PYTHONNOUSERSITE=1 is LOAD-BEARING (user-site numpy 2.x collides with Maya's).
"""
import math
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

import maya.standalone
maya.standalone.initialize(name='python')

import maya.cmds as cmds
import maya.api.OpenMaya as om2
cmds.loadPlugin('fbxmaya', quiet=True)

from maya_tools.export import export_core
from maya_tools.export import skeletal_export_runner as runner

FAILURES = []
OUT_DIR = Path(tempfile.mkdtemp(prefix='fs_test_upaxis_'))

PRESET = {
    'FBXExportSkins': True,
    'FBXExportSkeletonDefinitions': True,
    'FBXExportInAscii': True,
    'FBXExportUpAxis': 'y',
}


def check(name, fn):
    try:
        fn()
        print(f'  ok: {name}', flush=True)
    except Exception as exc:
        import traceback
        FAILURES.append(f'{name}: {exc!r}')
        print(f'FAIL: {name}: {exc!r}', flush=True)
        traceback.print_exc()


def _world(node):
    return cmds.xform(node, q=True, ws=True, matrix=True)


def _assert_matrix_close(a, b, tol, label):
    worst = max(abs(x - y) for x, y in zip(a, b))
    assert worst < tol, f'{label}: matrices differ (worst delta {worst:.5f})'


def _orient_delta_deg(wm_a, wm_b):
    """Single rotation angle between two world orientations."""
    ra = om2.MTransformationMatrix(om2.MMatrix(wm_a)).rotation(asQuaternion=True)
    rb = om2.MTransformationMatrix(om2.MMatrix(wm_b)).rotation(asQuaternion=True)
    d = ra.inverse() * rb
    return math.degrees(2.0 * math.acos(min(1.0, abs(d.w))))


def _build_chain():
    """root at origin (identity), pelvis-like child 90 up +Y, grandchild
    off to the side — the Y-up-native arrangement the old export produced."""
    cmds.file(new=True, force=True)
    root = cmds.joint(name='root', p=(0, 0, 0))
    child = cmds.joint(name='pelvis', p=(0, 90, 2))
    grand = cmds.joint(name='spine_01', p=(0, 100, 4))
    cmds.select(clear=True)
    return root, child, grand


# ── resolver ──────────────────────────────────────────────────────

def test_engine_up_axis_resolver():
    assert export_core.engine_up_axis(None) == 'y'
    assert export_core.engine_up_axis({}) == 'y'
    assert export_core.engine_up_axis({'engine_up_axis': 'z'}) == 'z'
    assert export_core.engine_up_axis({'engine_up_axis': 'Z'}) == 'z'
    assert export_core.engine_up_axis({'engine_up_axis': 'z'}, 'y') == 'y'
    assert export_core.engine_up_axis(None, 'z') == 'z'
    try:
        export_core.engine_up_axis({'engine_up_axis': 'w'})
    except RuntimeError:
        pass
    else:
        raise AssertionError('invalid axis did not raise')


# ── static conversion ─────────────────────────────────────────────

def test_static_conversion_root_minus90_children_hold():
    root, child, grand = _build_chain()
    child_w = _world(child)
    grand_w = _world(grand)

    export_core.orient_root_for_z_up_engine(root)

    # Root frame is now RX(-90); its position never moved.
    expected = [1, 0, 0, 0,
                0, 0, -1, 0,
                0, 1, 0, 0,
                0, 0, 0, 1]
    _assert_matrix_close(_world(root), expected, 1e-6, 'root frame')
    # Children did not move in world space (the visible pose is unchanged).
    _assert_matrix_close(_world(child), child_w, 1e-6, 'child world')
    _assert_matrix_close(_world(grand), grand_w, 1e-6, 'grandchild world')
    # And the child's LOCAL translate re-expressed Y-up -> Z-up native
    # (the SKM_Manny arrangement: pelvis rides +Z of the root frame).
    t = cmds.getAttr(f'{child}.translate')[0]
    assert abs(t[1] - (-2.0)) < 1e-4 and abs(t[2] - 90.0) < 1e-4, \
        f'child local translate not Z-up native: {t}'


def test_static_conversion_preserves_offset_root():
    """A root that isn't at the origin keeps its position (root motion)."""
    root, child, grand = _build_chain()
    cmds.setAttr(f'{root}.translate', 10, 0, 5)
    child_w = _world(child)

    export_core.orient_root_for_z_up_engine(root)

    t = cmds.getAttr(f'{root}.translate')[0]
    assert max(abs(a - b) for a, b in zip(t, (10, 0, 5))) < 1e-6, \
        f'root position moved: {t}'
    _assert_matrix_close(_world(child), child_w, 1e-6, 'child world (offset root)')


def test_static_conversion_idempotent_and_target_based():
    """Converting twice equals converting once, and a root ALREADY at the
    manny arrangement (world RX(-90)) is a no-op — the delta-only version
    drove such roots to -180 (found live on the manny round-trip)."""
    root, child, grand = _build_chain()
    export_core.orient_root_for_z_up_engine(root)
    after_once = (_world(root), _world(child), _world(grand))
    export_core.orient_root_for_z_up_engine(root)   # second run: no-op
    for want, node in zip(after_once, (root, child, grand)):
        _assert_matrix_close(_world(node), want, 1e-6,
                             f'{node} moved on second conversion')

    # A manny-import-derived scene: root authored at RX(-90) already.
    cmds.file(new=True, force=True)
    root = cmds.joint(name='root', p=(0, 0, 0))
    cmds.setAttr(f'{root}.rotate', -90, 0, 0)
    child = cmds.joint(name='pelvis', p=(0, 0, 0))
    cmds.setAttr(f'{child}.translate', 0, -2, 90)   # Z-native local
    cmds.select(clear=True)
    before = (_world(root), _world(child))
    export_core.orient_root_for_z_up_engine(root)
    for want, node in zip(before, (root, child)):
        _assert_matrix_close(_world(node), want, 1e-6,
                             f'{node} moved though already at target')


# ── animated conversion ───────────────────────────────────────────

def test_animated_conversion_holds_world_pose_every_frame():
    root, child, grand = _build_chain()
    # A little locomotion: yaw + travel on the root, child wiggle.
    for f, ty, tz, ry in ((1, 0, 0, 0), (5, 0, 20, 45), (10, 0, 50, 90)):
        cmds.setKeyframe(root, at='translateZ', time=f, value=tz)
        cmds.setKeyframe(root, at='rotateY', time=f, value=ry)
        cmds.setKeyframe(child, at='rotateX', time=f, value=ry * 0.1)
    cmds.setKeyframe(grand, at='rotateZ', time=1, value=0)

    baseline = {}
    for f in range(1, 11):
        cmds.currentTime(f)
        baseline[f] = (_world(root), _world(child), _world(grand))

    export_core.orient_root_for_z_up_engine(root, frame_range=(1, 10))

    for f in range(1, 11):
        cmds.currentTime(f)
        root_w0, child_w0, grand_w0 = baseline[f]
        # Root: same position, frame rotated by exactly 90.
        rw = _world(root)
        assert max(abs(rw[12 + i] - root_w0[12 + i]) for i in range(3)) < 1e-4, \
            f'frame {f}: root position drifted'
        delta = _orient_delta_deg(root_w0, rw)
        assert abs(delta - 90.0) < 0.01, \
            f'frame {f}: root frame delta {delta:.3f}, expected 90'
        # Children: world pose identical.
        _assert_matrix_close(_world(child), child_w0, 1e-4, f'frame {f} child')
        _assert_matrix_close(_world(grand), grand_w0, 1e-4, f'frame {f} grandchild')


# ── runner surgery end-to-end ─────────────────────────────────────

def test_runner_z_up_export_reimports_with_manny_arrangement():
    cmds.file(new=True, force=True)
    root = cmds.joint(name='root', p=(0, 0, 0))
    child = cmds.joint(name='pelvis', p=(0, 90, 2))
    cmds.select(clear=True)
    cube = cmds.polyCube(name='body_geo', h=10)[0]
    cmds.xform(cube, ws=True, t=(0, 45, 0))
    cmds.skinCluster(root, child, cube, toSelectedBones=True)

    def _span(node):
        b = cmds.exactWorldBoundingBox(node)
        return (b[3] - b[0], b[4] - b[1], b[5] - b[2])

    span_before = _span(cube)
    fbx = OUT_DIR / 'SK_zup.fbx'
    runner._prepare_and_export([root], [cube], str(fbx), PRESET,
                               engine_up_axis='z')

    # The live (throwaway) scene: mesh un-candy-wrapped at the converted pose.
    span_after = _span(cube)
    for a, b in zip(span_before, span_after):
        assert abs(a - b) < 1e-2, \
            f'mesh extent changed {span_before}->{span_after}: candy-wrap'

    # Re-import the FBX fresh: the manny arrangement must round-trip.
    assert fbx.is_file(), 'no FBX written'
    cmds.file(new=True, force=True)
    cmds.file(str(fbx), i=True, type='FBX', ignoreVersion=True, options='fbx')
    r = cmds.getAttr('root.rotate')[0]
    jo = cmds.getAttr('root.jointOrient')[0]
    total_x = r[0] + jo[0]
    assert abs(total_x - (-90.0)) < 0.01 and abs(r[1] + jo[1]) < 0.01 \
        and abs(r[2] + jo[2]) < 0.01, \
        f'imported root not at RX(-90): rotate={r} jointOrient={jo}'
    t = cmds.getAttr('pelvis.translate')[0]
    assert abs(t[1] - (-2.0)) < 1e-3 and abs(t[2] - 90.0) < 1e-3, \
        f'imported pelvis local not Z-up native: {t}'


def test_runner_default_y_unchanged():
    """engine_up_axis='y' (and old payloads with no key) leave the skeleton
    exactly as before — regression guard on the default path."""
    cmds.file(new=True, force=True)
    root = cmds.joint(name='root', p=(0, 0, 0))
    cmds.joint(name='pelvis', p=(0, 90, 2))
    cmds.select(clear=True)
    cube = cmds.polyCube(name='body_geo', h=10)[0]
    cmds.xform(cube, ws=True, t=(0, 45, 0))
    cmds.skinCluster(root, 'pelvis', cube, toSelectedBones=True)

    fbx = OUT_DIR / 'SK_yup.fbx'
    runner._prepare_and_export([root], [cube], str(fbx), PRESET)

    cmds.file(new=True, force=True)
    cmds.file(str(fbx), i=True, type='FBX', ignoreVersion=True, options='fbx')
    r = cmds.getAttr('root.rotate')[0]
    jo = cmds.getAttr('root.jointOrient')[0]
    assert all(abs(v) < 1e-4 for v in r) and all(abs(v) < 1e-4 for v in jo), \
        f'y-up root gained rotation: rotate={r} jointOrient={jo}'
    t = cmds.getAttr('pelvis.translate')[0]
    assert abs(t[1] - 90.0) < 1e-3, f'y-up pelvis local changed: {t}'


check('engine_up_axis resolver precedence + validation',
      test_engine_up_axis_resolver)
check('static conversion: root RX(-90), children world-held',
      test_static_conversion_root_minus90_children_hold)
check('static conversion: off-origin root keeps its position',
      test_static_conversion_preserves_offset_root)
check('static conversion: idempotent + no-op at target',
      test_static_conversion_idempotent_and_target_based)
check('animated conversion: world pose held on every frame',
      test_animated_conversion_holds_world_pose_every_frame)
check('runner z-up export re-imports with the manny arrangement',
      test_runner_z_up_export_reimports_with_manny_arrangement)
check('runner default y-up path unchanged (regression)',
      test_runner_default_y_unchanged)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print('ALL PASS (7)')
