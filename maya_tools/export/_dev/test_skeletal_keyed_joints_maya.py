"""Keyed export joints must not fight the export surgery (Adrian, 2026-07-14).

Root cause this covers: a joint carrying live animCurves (e.g. thigh_l keyed
at frame 0 in the Reggie source scene) snaps back to its STALE keyed rotation
on the next time evaluation after the orient/collapse writes, while its
unkeyed children keep their re-expressed locals — the exported left leg swung
90 deg out of the hip. The legacy in-scene path always cut keys
(_cut_keys_on_chain); the subprocess path must too.

Covered:
  - a rotate-keyed joint whose curve disagrees with the post-orient value
    exports with every joint's WORLD pose identical to the scene's rest pose
    (re-imported FBX compared against the pre-export scene).

Run:
PYTHONNOUSERSITE=1 PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen mayapy maya_tools/export/_dev/test_skeletal_keyed_joints_maya.py

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
from maya_tools.rigging.joint_orient import joint_orient_app as joa
from maya_tools.rigging.fabricator import nodes as fab_nodes
from maya_tools.rigging.fabricator import armature as amt

FAILURES = []
OUT_DIR = Path(tempfile.mkdtemp(prefix='fs_test_keyedjnt_'))

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


def test_keyed_joint_does_not_snap_back():
    """thigh chain with an aimer (so the orient bake rewrites the keyed
    joint's rotation) and a stale rotate key at frame 0. Without the key
    cut, the FBX captures the snapped-back parent + re-expressed child:
    the child's world position moves. With it, the rest pose round-trips."""
    cmds.file(new=True, force=True)
    root = cmds.joint(name='root', p=(0, 0, 0))
    thigh = cmds.joint(name='thigh_l', p=(7, 90, 0))
    calf = cmds.joint(name='calf_l', p=(7, 45, 0))
    cmds.joint(name='foot_l', p=(7, 8, 0))
    cmds.select(clear=True)
    cube = cmds.polyCube(name='body_geo', h=10)[0]
    cmds.xform(cube, ws=True, t=(7, 45, 0))
    cmds.skinCluster(root, thigh, calf, cube, toSelectedBones=True)

    # An aimer on the thigh so the export's orient bake genuinely rewrites
    # its rotation (rotated aim target = new frame, like the real rigs).
    joa.create_aimer(thigh, scale=10.0)
    aimer = cmds.ls('thigh_l_aimer*', type='transform') or []
    if aimer:
        cmds.setAttr(f'{aimer[0]}.rotateX', 35)

    # The bug trigger: a stale rotate key at frame 0 (the runner's rest
    # frame) that disagrees with whatever the orient bake writes.
    cmds.currentTime(0)
    cmds.setKeyframe(thigh, attribute='rotate', time=0)

    fab_nodes.create_registry('keyedjnt_test')
    amt.build_armature(root=root)

    # Ground truth: every joint's rest world position before export.
    cmds.currentTime(0)
    want = {j: cmds.xform(j, q=True, ws=True, t=True)
            for j in ('root', 'thigh_l', 'calf_l', 'foot_l')}

    fbx = OUT_DIR / 'SK_keyed.fbx'
    runner._prepare_and_export([root], [cube], str(fbx), PRESET)
    assert fbx.is_file(), 'no FBX written'

    cmds.file(new=True, force=True)
    cmds.file(str(fbx), i=True, type='FBX', ignoreVersion=True, options='fbx')
    cmds.currentTime(0)
    for j, w in want.items():
        got = cmds.xform(j, q=True, ws=True, t=True)
        worst = max(abs(a - b) for a, b in zip(got, w))
        assert worst < 0.01, (
            f'{j} moved in the FBX: scene {tuple(round(v, 2) for v in w)} '
            f'-> fbx {tuple(round(v, 2) for v in got)} (delta {worst:.2f}) '
            f'— keyed-joint snap-back')
    # And no curves ride into a rest-pose skeletal FBX.
    assert not (cmds.keyframe('thigh_l', q=True, keyframeCount=True) or 0), \
        'anim curves leaked into the skeletal FBX'


check('keyed joint does not snap back through export',
      test_keyed_joint_does_not_snap_back)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print('ALL PASS (1)')
