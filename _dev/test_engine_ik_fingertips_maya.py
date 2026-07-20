# _dev/test_engine_ik_fingertips_maya.py
"""mayapy SCENE test: Build Engine IK must not move any pre-existing
skeleton joint (the 2026-07-14 fingertip-collapse bug).

Bug record: build_engine_ik_joints created six FollowJoint components,
and each add's _engage_armature() ran a FULL armature rebuild against a
scene still mid-mutation. During those back-to-back rebuilds every
finger *_03_l/r joint's local translate collapsed to [0,0,0] (snapped
onto its parent knuckle). Deterministic against the factory
Advanced_Biped template; caught via Adrian's template-save bisection
(clean 13:58 save vs corrupted 14:36/14:41 saves, 2026-07-14). Fix:
armature.engage_suspended() batches the six adds into ONE rebuild after
the loop (engine_ik.build_engine_ik_joints).

This test drives the real user flow — load the factory template, press
the button — and asserts the whole skeleton's local translates are
byte-identical (within FP noise) before/after, not just the fingertips:
the bug class is "engine IK moved a joint it doesn't own", tips were
merely the symptom.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_engine_ik_fingertips_maya.py
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
        traceback.print_exc()
        FAILURES.append(f"{name}: {exc}")
        print(f"  FAIL: {name}: {exc}")


import maya.standalone
maya.standalone.initialize(name='python')

import maya.cmds as cmds
from maya_tools.rigging.fabricator import fs_app, engine_ik

TEMPLATE = (REPO_ROOT / 'maya_tools' / 'rigging' / 'fabricator'
            / 'templates' / 'Advanced_Biped.blueprint.yaml')

TIP_TOKENS = ('thumb_03', 'index_03', 'middle_03', 'ring_03', 'pinky_03')


def _skeleton_locals():
    """name -> local translate for every joint currently in the scene."""
    out = {}
    for j in cmds.ls(type='joint') or []:
        short = j.split('|')[-1]
        out[short] = cmds.getAttr(f'{short}.translate')[0]
    return out


def test_engine_ik_preserves_every_preexisting_joint():
    cmds.file(new=True, force=True)
    fs_app.load(str(TEMPLATE))

    before = _skeleton_locals()
    tips = [n for n in before if any(n.startswith(t) for t in TIP_TOKENS)]
    assert len(tips) == 10, f'expected 10 finger tips in template, got {len(tips)}'
    for n in tips:
        assert max(abs(v) for v in before[n]) > 0.5, \
            f'template itself is corrupt: {n} translate {before[n]}'

    report = engine_ik.build_engine_ik_joints()
    assert report['components_created'], 'engine IK created no components'

    after = _skeleton_locals()
    moved = []
    for n, t in before.items():
        if n not in after:
            moved.append(f'{n}: joint disappeared')
            continue
        delta = max(abs(a - b) for a, b in zip(after[n], t))
        if delta > 1e-3:
            moved.append(f'{n}: moved by {delta:.4f} (was {t}, now {after[n]})')
    assert not moved, ('engine IK moved pre-existing joints:\n  '
                       + '\n  '.join(moved[:12]))


def test_engine_ik_rerun_is_still_lossless():
    # Idempotence: the re-snap path (created: 0) must hold the same contract.
    before = _skeleton_locals()
    engine_ik.build_engine_ik_joints()
    after = _skeleton_locals()
    engine_names = {n for n, _, _ in engine_ik.ENGINE_IK_SPEC}
    moved = [n for n, t in before.items()
             if n not in engine_names
             and max(abs(a - b) for a, b in zip(after[n], t)) > 1e-3]
    assert not moved, f're-run moved non-engine joints: {moved[:12]}'


check('test_engine_ik_preserves_every_preexisting_joint',
      test_engine_ik_preserves_every_preexisting_joint)
check('test_engine_ik_rerun_is_still_lossless',
      test_engine_ik_rerun_is_still_lossless)

print(f"ENGINE IK FINGERTIP TESTS: "
      f"{'FAIL - ' + '; '.join(FAILURES) if FAILURES else 'OK - 0 SKIP'}")
maya.standalone.uninitialize()
sys.exit(1 if FAILURES else 0)
