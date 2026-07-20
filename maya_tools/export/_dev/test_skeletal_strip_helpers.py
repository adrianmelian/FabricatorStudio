"""Skeletal-export helper-strip — LEGACY (non-KS) path. Run:
PYTHONNOUSERSITE=1 PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen mayapy maya_tools/export/_dev/test_skeletal_strip_helpers.py

Bug (Adrian, 2026-07-12): skeletal FBX exports contained the aimers'
per-joint `<joint>_localRef_LOC` locators as bogus bones. `FBXExport -s`
exports the selected root joint's entire DAG subtree, so anything parented
under a joint rides along.

As of 2026-07-13 the KS (fab_registry) export runs in a throwaway subprocess
(skeletal_pipeline / skeletal_export_runner — see
test_skeletal_subprocess_export_maya.py). The IN-SCENE strip+undo path that
this file used to cover for KS rigs is gone. What remains here is the LEGACY
path for hand-built rigs (no registry): a small in-scene surgery, undone after
export, that strips ONLY aimer refs — an artist's own under-joint locators
(sockets) are theirs and must ride into the FBX.

PYTHONNOUSERSITE=1 is LOAD-BEARING: a user-site numpy 2.x collides with
Maya's bundled numpy inside the shiboken6 import (see test_phase_a.py)."""
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

import maya.standalone
maya.standalone.initialize(name='python')

import maya.cmds as cmds

from maya_tools.export import skeletal_mesh
from maya_tools.rigging.joint_orient import joint_orient_app as joa

FAILURES = []
OUT_DIR = Path(tempfile.mkdtemp(prefix='fs_test_strip_'))

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
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


def test_legacy_path_strips_aimer_refs_keeps_artist_locators():
    """Joint Orient runs without a fab_registry, so hand-built rigs leak
    localRefs through the LEGACY (in-scene) path. Legacy strips ONLY aimer
    refs; an artist's own under-joint locators (sockets) must ride into the
    FBX, and every helper is undo-restored in-scene afterward."""
    cmds.file(new=True, force=True)     # no registry -> legacy path
    root = cmds.joint(name='root_jnt', p=(0, 0, 0))
    hand = cmds.joint(name='hand_l', p=(0, -5, 0))
    cmds.select(clear=True)
    cube = cmds.polyCube(name='body_geo')[0]
    cmds.skinCluster(root, cube)
    joa.create_aimer(hand, scale=10.0)
    sock = cmds.spaceLocator(name='SOCKET_grip')[0]
    cmds.parent(sock, hand)

    fbx = OUT_DIR / 'SK_legacy.fbx'
    skeletal_mesh.export_skeletal([root, hand], [cube], str(fbx), PRESET)

    text = fbx.read_text(encoding='utf-8', errors='ignore')
    assert 'localRef_LOC' not in text, 'localRef leaked through legacy path'
    assert 'SOCKET_grip' in text, "artist's socket locator wrongly stripped"
    assert cmds.objExists('hand_l_localRef_LOC'), 'localRef not undo-restored'
    assert cmds.objExists('SOCKET_grip')


def test_legacy_export_is_repeatable():
    """The in-scene strip is undone after each export, so a second export
    straight after is identical — not a one-shot."""
    fbx2 = OUT_DIR / 'SK_legacy_2.fbx'
    skeletal_mesh.export_skeletal(['root_jnt', 'hand_l'], ['body_geo'],
                                  str(fbx2), PRESET)
    text = fbx2.read_text(encoding='utf-8', errors='ignore')
    assert 'localRef_LOC' not in text
    assert cmds.objExists('hand_l_localRef_LOC')


check('legacy strips aimer refs, keeps artist locators',
      test_legacy_path_strips_aimer_refs_keeps_artist_locators)
check('legacy export is repeatable', test_legacy_export_is_repeatable)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print('ALL PASS (2)')
