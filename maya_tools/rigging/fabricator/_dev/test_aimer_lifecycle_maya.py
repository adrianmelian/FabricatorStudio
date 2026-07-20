"""Aimer + bind-pose lifecycle fidelity (Adrian, 2026-07-14).

Root causes these cover (found on the manny round-trip):
  A. Unbuild face-plant — bind pose captured while the root sat under the
     UE import's -90 group, restored verbatim after the build reparented
     the root under the identity top group: group-relative locals replayed
     in the wrong frame pitched the skeleton 90 deg. Fix: the root
     (blueprint-parentless) captures/restores its WORLD matrix.
  B. Twist aimers flipped a further 180 on every Armature rebuild — the
     parent-flip creation default ran unconditionally, discarding
     authored/restored state. Fix: fresh aimers only.
  C. Aimer state was local-only and frame-relative; the bake-recreate-
     restore cycle folded deltas in. Fix: capture/restore the aimer ctl's
     WORLD orientation.

Run:
PYTHONNOUSERSITE=1 PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen mayapy maya_tools/rigging/fabricator/_dev/test_aimer_lifecycle_maya.py

PYTHONNOUSERSITE=1 is LOAD-BEARING (user-site numpy 2.x collides with Maya's).
"""
import math
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

import maya.standalone
maya.standalone.initialize(name='python')

import maya.cmds as cmds
import maya.api.OpenMaya as om2

from maya_tools.rigging.joint_orient import joint_orient_app as joa
from maya_tools.rigging.fabricator import armature as amt
from maya_tools.rigging.fabricator import nodes as fab_nodes
from maya_tools.rigging.fabricator import fs_app

FAILURES = []


def check(name, fn):
    try:
        fn()
        print(f'  ok: {name}', flush=True)
    except Exception as exc:
        import traceback
        FAILURES.append(f'{name}: {exc!r}')
        print(f'FAIL: {name}: {exc!r}', flush=True)
        traceback.print_exc()


def _world_rot(node):
    return cmds.xform(node, q=True, ws=True, rotation=True)


def _world_m(node):
    return cmds.xform(node, q=True, ws=True, matrix=True)


def _ori_delta_deg(ra, rb):
    qa = om2.MEulerRotation(*[math.radians(v) for v in ra]).asQuaternion()
    qb = om2.MEulerRotation(*[math.radians(v) for v in rb]).asQuaternion()
    d = qa.inverse() * qb
    return math.degrees(2.0 * math.acos(min(1.0, abs(d.w))))


# ── C: world-true aimer restore ───────────────────────────────────

def test_world_rotation_restores_frame_independently():
    """Capture an aimer's state, change the JOINT's frame, recreate the
    aimer, apply — the ctl's world orientation must come back, not a
    local rotation replayed against the new frame."""
    cmds.file(new=True, force=True)
    a = cmds.joint(name='jnt_a', p=(0, 0, 0))
    cmds.joint(name='jnt_b', p=(10, 0, 0))
    cmds.select(clear=True)

    joa.create_aimer(a, scale=10.0)
    ctl = joa.aimer_name(a)
    cmds.xform(ctl, ws=True, rotation=(12.0, 34.0, 56.0))
    state = joa.get_aimer_state(a)
    want = state['world_rotation']

    joa.delete_aimer(a)
    cmds.setAttr(f'{a}.rotate', 0, 0, 45)   # the joint frame changed
    joa.create_aimer(a, scale=10.0)
    joa.apply_aimer_state(a, aim_target=state['aim_target'],
                          aim_offset=state['aim_offset'],
                          world_rotation=state['world_rotation'])
    got = _world_rot(ctl)
    d = _ori_delta_deg(want, got)
    assert d < 0.01, f'aimer world rotation drifted {d:.3f} deg: ' \
                     f'{want} -> {got}'


# ── B: twist flip is a creation default only ──────────────────────

def test_twist_flip_fresh_only_and_rebuild_stable():
    """The parent-flip lands once on a FRESH twist aimer; repeated
    bake/restore cycles (the Unbuild epilogue path) leave the aimer's
    world orientation and the joint's orientation unchanged."""
    cmds.file(new=True, force=True)
    p = cmds.joint(name='seg_start', p=(0, 0, 0))
    t = cmds.joint(name='seg_twist', p=(5, 0, 0))   # childless rider
    cmds.select(clear=True)

    real = amt.is_twist_joint
    amt.is_twist_joint = lambda j: j.split('|')[-1] == 'seg_twist'
    try:
        joints = [p, t]
        fresh = amt._ensure_aimers(joints, 10.0)
        assert t in fresh, 'twist aimer not reported fresh'
        amt._bake_and_restore_aimers(joints, 10.0, fresh=fresh)

        ctl = joa.aimer_name(t)
        aimer_w0 = _world_rot(ctl)
        joint_w0 = _world_rot(t)

        # Rebuild cycles with NO fresh aimers — nothing may move.
        for cycle in (1, 2, 3):
            amt._bake_and_restore_aimers(joints, 10.0, fresh=[])
            da = _ori_delta_deg(aimer_w0, _world_rot(joa.aimer_name(t)))
            dj = _ori_delta_deg(joint_w0, _world_rot(t))
            assert da < 0.01, f'cycle {cycle}: twist AIMER moved {da:.2f} deg'
            assert dj < 0.01, f'cycle {cycle}: twist JOINT moved {dj:.2f} deg'
    finally:
        amt.is_twist_joint = real


# ── A: bind-pose root restores by world ───────────────────────────

def test_bind_pose_root_world_survives_reparent():
    """Capture the bind pose with the root under a -90 group (the UE
    import layout), reparent the root under an identity group (what
    _organize_scene does during build), restore — the root's WORLD
    orientation must come back; the old code face-planted the skeleton."""
    cmds.file(new=True, force=True)
    grp = cmds.group(empty=True, name='import_grp')
    cmds.setAttr(f'{grp}.rotateX', -90)
    root = cmds.joint(name='root', p=(0, 0, 0))
    pelvis = cmds.joint(name='pelvis', p=(0, 0, 96))   # Z-native under -90 grp
    cmds.select(clear=True)
    cmds.parent(root, grp)
    fab_nodes.create_registry('bindpose_test')

    blueprint = SimpleNamespace(skeleton_joints=[
        SimpleNamespace(name='root', parent=None),
        SimpleNamespace(name='pelvis', parent='root'),
    ])

    root_w0 = _world_m(root)
    pelvis_w0 = _world_m(pelvis)
    fs_app._capture_skeleton_bind_pose(blueprint)

    # The build's reparent: root leaves the -90 group for an identity top.
    top = cmds.group(empty=True, name='top_grp')
    cmds.parent(root, top)
    # Perturb, as components would.
    cmds.setAttr('pelvis.rotate', 10, 20, 30)

    fs_app._restore_skeleton_bind_pose(blueprint)

    for node, want in (('root', root_w0), ('pelvis', pelvis_w0)):
        got = _world_m(node)
        worst = max(abs(a - b) for a, b in zip(want, got))
        assert worst < 1e-4, (
            f'{node} world drifted after restore (worst {worst:.5f}) — '
            f'face-plant class')


# ── E: template round-trip keeps the root's world orientation ─────

def test_blueprint_roundtrip_root_world_orientation():
    """Snapshot a skeleton whose root sits under a -90 group (the UE
    import layout), recreate it in a fresh scene from the specs — the
    recreated skeleton must stand exactly where the original did (the old
    schema dropped the group's rotation: template came back sideways)."""
    cmds.file(new=True, force=True)
    grp = cmds.group(empty=True, name='import_grp')
    cmds.setAttr(f'{grp}.rotateX', -90)
    root = cmds.joint(name='root', p=(0, 0, 0))
    cmds.joint(name='pelvis', p=(0, 0, 96))       # Z-native under the group
    cmds.select(clear=True)
    cmds.parent(root, grp)
    fab_nodes.create_registry('bp_roundtrip_src')
    fab_nodes.set_registry_root_joint('root')

    want = {j: _world_m(j) for j in ('root', 'pelvis')}
    specs = fs_app._snapshot_skeleton_from_scene()
    by_name = {s.name: s for s in specs}
    assert by_name['root'].world_rotate is not None, \
        'root spec did not capture world_rotate'

    cmds.file(new=True, force=True)
    fab_nodes.create_registry('bp_roundtrip_dst')
    fs_app._create_skeleton_from_blueprint(
        SimpleNamespace(skeleton_joints=specs))
    for j, m in want.items():
        got = _world_m(j)
        worst = max(abs(a - b) for a, b in zip(m, got))
        assert worst < 1e-4, (
            f'{j} world drifted through the template round-trip '
            f'(worst {worst:.5f}) — sideways-template class')


check('C: aimer world rotation restores frame-independently',
      test_world_rotation_restores_frame_independently)
check('E: blueprint round-trip keeps root world orientation',
      test_blueprint_roundtrip_root_world_orientation)
check('B: twist flip fresh-only; rebuild cycles are stable',
      test_twist_flip_fresh_only_and_rebuild_stable)
check('A: bind-pose root restores by world across reparent',
      test_bind_pose_root_world_survives_reparent)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print('ALL PASS (4)')
