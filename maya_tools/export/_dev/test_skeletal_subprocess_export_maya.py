"""Rig (skeletal) export via the throwaway subprocess (Adrian, 2026-07-13).

The KS skeletal export moved out of the live scene into a mayapy subprocess
(skeletal_pipeline / skeletal_export_runner): open the saved scene, disconnect
skins, break the Armature + orient joints to the game contract, strip to
joints + mesh, reconnect skins at the FINAL pose, write the FBX, exit. Nothing
is restored — the scene is a throwaway copy.

Covered:
  - the runner's surgery, in-process: skins reconnect at the oriented pose so
    the mesh is NOT candy-wrapped (bbox preserved), helpers stripped, Armature
    deleted, FBX is joints + mesh only;
  - the launcher's save guards (raises on unsaved / dirty);
  - a real end-to-end spawn: minimal KS rig -> saved -> subprocess -> FBX.

Run:
PYTHONNOUSERSITE=1 PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen mayapy maya_tools/export/_dev/test_skeletal_subprocess_export_maya.py

PYTHONNOUSERSITE=1 is LOAD-BEARING (user-site numpy 2.x collides with Maya's).
"""
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

import maya.standalone
maya.standalone.initialize(name='python')

import maya.cmds as cmds
cmds.loadPlugin('fbxmaya', quiet=True)

from maya_tools.export import skeletal_export_runner as runner
from maya_tools.export import skeletal_pipeline
from maya_tools.skinning import skin_connect_app
from maya_tools.rigging.joint_orient import joint_orient_app as joa
from maya_tools.rigging.fabricator import nodes as fab_nodes
from maya_tools.rigging.fabricator import armature as amt

FAILURES = []
OUT_DIR = Path(tempfile.mkdtemp(prefix='fs_test_skexport_'))

# ASCII FBX so the writeout is greppable.
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


def _bbox_span(node):
    b = cmds.exactWorldBoundingBox(node)
    return (b[3] - b[0], b[4] - b[1], b[5] - b[2])


def _build_ks_scene():
    """registry + root -> thigh_r chain, a cube skinned to BOTH joints, an
    aimer on thigh_r (so the orient bake actually reorients a joint and parks
    thigh_r_localRef_LOC under it), and a standing Armature — Adrian's real
    export state. Returns (root, thigh, cube, skinCluster)."""
    cmds.file(new=True, force=True)
    root = cmds.joint(name='root_jnt', p=(0, 0, 0))
    thigh = cmds.joint(name='thigh_r', p=(0, -5, 0))
    cmds.select(clear=True)

    cube = cmds.polyCube(name='body_geo', h=10)[0]
    cmds.xform(cube, ws=True, t=(0, -2.5, 0))       # spans both joints
    sc = cmds.skinCluster(root, thigh, cube, toSelectedBones=True)[0]

    joa.create_aimer(thigh, scale=10.0)             # -> thigh_r_localRef_LOC
    fab_nodes.create_registry('skexport_test')
    amt.build_armature(root=root)
    return root, thigh, cube, sc


# ── the runner surgery, in-process ────────────────────────────────

def test_surgery_reconnects_skin_no_candywrap_and_strips_to_joints_mesh():
    root, thigh, cube, sc = _build_ks_scene()
    assert cmds.objExists('thigh_r_localRef_LOC'), 'setup: aimer localRef missing'
    span_before = _bbox_span(cube)

    fbx = OUT_DIR / 'SK_inprocess.fbx'
    runner._prepare_and_export([root], [cube], str(fbx), PRESET)

    # Mesh is un-candy-wrapped: the skin reconnected at the oriented pose, so
    # the mesh is at rest — its extent is unchanged (a stale bind would twist
    # it, a lost influence would collapse it to a point).
    span_after = _bbox_span(cube)
    for a, b in zip(span_before, span_after):
        assert abs(a - b) < 1e-2, \
            f'mesh extent changed {span_before}->{span_after}: candy-wrap / collapse'

    # Skin is live again (reconnected, not left dormant).
    assert skin_connect_app._is_live(sc), 'skin not reconnected after export'
    # Armature was deleted and (throwaway scene) never rebuilt.
    assert not amt.armature_exists(), 'Armature not broken for the export'

    assert fbx.is_file(), 'no FBX written'
    text = fbx.read_text(encoding='utf-8', errors='ignore')
    assert 'localRef_LOC' not in text, 'aimer localRef leaked into the FBX'
    assert 'body_geo' in text, 'skinned mesh missing from the FBX'
    assert 'thigh_r' in text, 'skeleton missing from the FBX'


def test_surgery_no_registry_still_brackets_skin_and_strips():
    """A non-KS scene (no registry): no orient, but the skin bracket + strip
    still run, so an under-joint helper is gone from the FBX and the skin is
    live."""
    cmds.file(new=True, force=True)
    root = cmds.joint(name='root_jnt', p=(0, 0, 0))
    hand = cmds.joint(name='hand_l', p=(0, -5, 0))
    cmds.select(clear=True)
    cube = cmds.polyCube(name='body_geo', h=10)[0]
    cmds.xform(cube, ws=True, t=(0, -2.5, 0))
    sc = cmds.skinCluster(root, hand, cube, toSelectedBones=True)[0]
    joa.create_aimer(hand, scale=10.0)              # -> hand_l_localRef_LOC

    fbx = OUT_DIR / 'SK_noreg.fbx'
    runner._prepare_and_export([root], [cube], str(fbx), PRESET)

    assert skin_connect_app._is_live(sc), 'skin not reconnected (no-registry path)'
    text = fbx.read_text(encoding='utf-8', errors='ignore')
    assert 'localRef_LOC' not in text, 'localRef leaked (no-registry path)'
    assert 'body_geo' in text, 'mesh missing (no-registry path)'


# ── the launcher's save guards ────────────────────────────────────

def test_run_export_raises_on_unsaved_scene():
    cmds.file(new=True, force=True)               # never saved -> no scene name
    try:
        skeletal_pipeline.run_export(['root_jnt'], ['body_geo'],
                                     str(OUT_DIR / 'never.fbx'), PRESET)
    except RuntimeError as exc:
        assert 'saved' in str(exc).lower()
        return
    raise AssertionError('run_export did not raise on an unsaved scene')


def test_run_export_raises_on_dirty_scene():
    cmds.file(new=True, force=True)
    scene = OUT_DIR / 'dirty_scene.ma'
    cmds.file(rename=str(scene))
    cmds.file(save=True, type='mayaAscii')
    cmds.createNode('transform', name='makes_it_dirty')   # now modified
    assert cmds.file(q=True, modified=True), 'precondition: scene should be dirty'
    try:
        skeletal_pipeline.run_export(['makes_it_dirty'], ['makes_it_dirty'],
                                     str(OUT_DIR / 'dirty.fbx'), PRESET)
    except RuntimeError as exc:
        assert 'unsaved' in str(exc).lower() or 'save' in str(exc).lower()
        return
    raise AssertionError('run_export did not raise on a dirty scene')


# ── real end-to-end: spawn the subprocess ─────────────────────────

def test_full_subprocess_roundtrip_writes_joints_only_fbx():
    """The money test: a saved KS rig exported through the actual mayapy
    subprocess. Proves the spawn/open/surgery/writeout wiring end to end, and
    that the LIVE (calling) scene is left untouched."""
    root, thigh, cube, sc = _build_ks_scene()
    scene = OUT_DIR / 'reggie_stub.ma'
    cmds.file(rename=str(scene))
    cmds.file(save=True, type='mayaAscii')

    fbx = OUT_DIR / 'SK_subprocess.fbx'
    skeletal_pipeline.run_export([root], [cube], str(fbx), PRESET)

    assert fbx.is_file(), 'subprocess produced no FBX'
    text = fbx.read_text(encoding='utf-8', errors='ignore')
    assert 'localRef_LOC' not in text, 'localRef leaked through the subprocess'
    assert 'body_geo' in text, 'mesh missing from the subprocess FBX'
    assert 'thigh_r' in text, 'skeleton missing from the subprocess FBX'
    # The subprocess opened its OWN copy — the calling scene is untouched.
    assert amt.armature_exists(), 'live scene Armature was touched by the export'
    assert cmds.objExists('thigh_r_localRef_LOC'), 'live scene helper was touched'


check('surgery reconnects skin (no candy-wrap) + strips to joints/mesh',
      test_surgery_reconnects_skin_no_candywrap_and_strips_to_joints_mesh)
check('no-registry scene still brackets skin + strips',
      test_surgery_no_registry_still_brackets_skin_and_strips)
check('run_export raises on unsaved scene', test_run_export_raises_on_unsaved_scene)
check('run_export raises on dirty scene', test_run_export_raises_on_dirty_scene)
check('full subprocess roundtrip -> joints-only FBX',
      test_full_subprocess_roundtrip_writes_joints_only_fbx)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print('ALL PASS (5)')
