# _dev/test_module_swap_parity_maya.py
"""mayapy scene tests for FREE <-> RIBBON module swap parity (launch
polish, Adrian 2026-07-10).

THE GUARANTEE UNDER TEST: a rig referenced into animation scenes can have
its free module (IKArm / IKLeg) swapped for the paid ribbon counterpart
(RibbonIKArm / RibbonIKLeg) with ZERO animation loss — every anim-facing
ctrl the free module builds must exist under the SAME NAME with the SAME
keyable channels after the swap, because referenced anim curves bind to
'<ctrl>.<attr>' plugs by name. Free naming is the template; the ribbon
may only ADD ctrls (mids, ribbon dials) — feature gain, never loss.

Why this holds today: both families inherit ctrl creation from
SimpleIKComponent (FK/IK/PV/IKFK, joint-derived names), RibbonIKLeg
subclasses IKLegComponent (heel/toe/ball reverse-foot set), and both arms
build fingers through _limb_common.build_hand_from_limb. This suite exists
so it can never silently drift — a rename on either side of any shared
ctrl breaks these tests, not a customer's shot.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_module_swap_parity_maya.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


class Skip(Exception):
    """Raise from a test body to mark it SKIPPED — never silently absorbed
    into the pass count (same contract as the sibling maya suites)."""


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Skip as exc:
        SKIPS.append(f"{name}: {exc}")
        print(f"  SKIP: {name}: {exc}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


# ─── Chain builders (copied verbatim from the sibling suites so both
#     module types build on IDENTICAL joint names/geometry) ──────────────

def _make_arm_chain(prefix):
    """Root -> shoulder -> elbow -> wrist (test_ik_arm_maya.py verbatim)."""
    import maya.cmds as cmds
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 10, 0), name=f'{prefix}_root')
    shoulder = cmds.joint(p=(0, 10, 0), name=f'{prefix}_shoulder')
    elbow = cmds.joint(p=(5, 7, 1), name=f'{prefix}_elbow')
    wrist = cmds.joint(p=(10, 10, 0), name=f'{prefix}_wrist')
    cmds.select(clear=True)
    return root, shoulder, elbow, wrist


def _make_leg_chain(prefix):
    """Root -> thigh -> knee -> ankle -> ball (reverse-foot 4-joint leg,
    mirroring test_ribbon_ik_leg_maya.py's geometry)."""
    import maya.cmds as cmds
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 10, 0), name=f'{prefix}_root')
    thigh = cmds.joint(p=(1, 9, 0), name=f'{prefix}_thigh')
    knee = cmds.joint(p=(1, 5, 1), name=f'{prefix}_knee')
    ankle = cmds.joint(p=(1, 1, 0), name=f'{prefix}_ankle')
    ball = cmds.joint(p=(1, 0, 1.5), name=f'{prefix}_ball')
    cmds.select(clear=True)
    return root, thigh, knee, ankle, ball


def _add_world_component(component_id, root_joint):
    from maya_tools.rigging.fabricator import nodes
    nodes.create_component_node(
        component_id=component_id, component_type='World',
        joints=[root_joint], parent_plug='', side='md',
        options={}, persisted={},
    )


def _build_and_snapshot(component_type, chain_builder, prefix):
    """Fresh scene -> chain -> World + component -> build_modules ->
    snapshot {ctrl_name: sorted(keyable attrs)} for every '*_ctrl'
    transform in the scene -> unbuild. Same prefix across calls so
    ctrl names are directly comparable."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    joints = chain_builder(prefix)
    root, limb_joints = joints[0], list(joints[1:])
    nodes.create_registry(f'{prefix}_bp')
    _add_world_component(f'{prefix}_world', root)
    nodes.create_component_node(
        component_id=f'{prefix}_C0', component_type=component_type,
        joints=limb_joints, parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()

    snapshot = {}
    for t in cmds.ls(type='transform') or []:
        short = t.split('|')[-1]
        if not short.endswith('_ctrl'):
            continue
        keyable = cmds.listAttr(t, keyable=True) or []
        snapshot[short] = sorted(keyable)
    fs_app.unbuild_modules()
    return snapshot


# ─── The parity gates ────────────────────────────────────────────────────

# Anim-facing ctrls the FREE module guarantees; exact names, free naming
# is the template (world/offset internals excluded — animators don't own
# them, and the World component is shared scaffolding in both builds).
def _expected_arm_ctrls(shoulder, elbow, wrist):
    return {
        f'{shoulder}_FK_ctrl', f'{elbow}_FK_ctrl', f'{wrist}_FK_ctrl',
        f'{wrist}_IK_ctrl', f'{elbow}_PV_ctrl', f'{wrist}_IKFK_ctrl',
    }


def _expected_leg_ctrls(thigh, knee, ankle, ball):
    return {
        f'{thigh}_FK_ctrl', f'{knee}_FK_ctrl', f'{ankle}_FK_ctrl',
        f'{ankle}_IK_ctrl', f'{knee}_PV_ctrl', f'{ankle}_IKFK_ctrl',
        f'{ankle}_heel_ctrl', f'{ankle}_toe_ctrl', f'{ball}_ctrl',
    }


def _assert_swap_parity(free_snap, ribbon_snap, expected, label):
    # 1. The free module actually builds every expected anim ctrl.
    missing_free = expected - set(free_snap)
    assert not missing_free, (
        f'{label}: FREE module missing expected ctrls: {sorted(missing_free)}')

    # 2. Every free anim ctrl exists in the ribbon build under the SAME
    #    name — the swap loses nothing.
    missing_ribbon = set(free_snap) - set(ribbon_snap)
    assert not missing_ribbon, (
        f'{label}: ribbon build DROPS free ctrls (anim loss on swap): '
        f'{sorted(missing_ribbon)}')

    # 3. Channel parity: every keyable attr on a free ctrl is keyable on
    #    the ribbon ctrl of the same name — referenced anim curves bind
    #    to plugs, so names alone are not enough.
    for ctrl, free_attrs in free_snap.items():
        ribbon_attrs = set(ribbon_snap[ctrl])
        lost = set(free_attrs) - ribbon_attrs
        assert not lost, (
            f'{label}: {ctrl} loses keyable channels on swap: {sorted(lost)}')

    # 4. Feature gain is allowed and expected: the ribbon adds ctrls
    #    (mid ctrls) — assert the gain direction just to document it.
    gained = set(ribbon_snap) - set(free_snap)
    assert all(g not in free_snap for g in gained)


def test_arm_swap_parity_free_ctrls_survive_ribbon_swap():
    prefix = 'swaparm'
    free = _build_and_snapshot('IKArm', _make_arm_chain, prefix)
    ribbon = _build_and_snapshot('RibbonIKArm', _make_arm_chain, prefix)
    expected = _expected_arm_ctrls(
        f'{prefix}_shoulder', f'{prefix}_elbow', f'{prefix}_wrist')
    _assert_swap_parity(free, ribbon, expected, 'arm')


def test_leg_swap_parity_free_ctrls_survive_ribbon_swap():
    prefix = 'swapleg'
    free = _build_and_snapshot('IKLeg', _make_leg_chain, prefix)
    ribbon = _build_and_snapshot('RibbonIKLeg', _make_leg_chain, prefix)
    expected = _expected_leg_ctrls(
        f'{prefix}_thigh', f'{prefix}_knee', f'{prefix}_ankle',
        f'{prefix}_ball')
    _assert_swap_parity(free, ribbon, expected, 'leg')


def test_arm_switch_attr_name_parity():
    """The IK/FK blend attr is the single most-keyed plug on the switch —
    pin its exact name on both builds (ik_fk_blend on <wrist>_IKFK_ctrl)."""
    import maya.cmds as cmds
    prefix = 'swapattr'
    for ctype in ('IKArm', 'RibbonIKArm'):
        snap = _build_and_snapshot(ctype, _make_arm_chain, prefix)
        switch = f'{prefix}_wrist_IKFK_ctrl'
        assert switch in snap, (ctype, sorted(snap))
        assert 'ik_fk_blend' in snap[switch], (ctype, snap[switch])


def test_leg_foot_roll_attr_name_parity():
    """foot_roll rides the IK foot ctrl in both leg builds, same plug name."""
    prefix = 'swaproll'
    for ctype in ('IKLeg', 'RibbonIKLeg'):
        snap = _build_and_snapshot(ctype, _make_leg_chain, prefix)
        foot = f'{prefix}_ankle_IK_ctrl'
        assert foot in snap, (ctype, sorted(snap))
        assert 'foot_roll' in snap[foot], (ctype, snap[foot])


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    check("test_arm_swap_parity_free_ctrls_survive_ribbon_swap",
          test_arm_swap_parity_free_ctrls_survive_ribbon_swap)
    check("test_leg_swap_parity_free_ctrls_survive_ribbon_swap",
          test_leg_swap_parity_free_ctrls_survive_ribbon_swap)
    check("test_arm_switch_attr_name_parity",
          test_arm_switch_attr_name_parity)
    check("test_leg_foot_roll_attr_name_parity",
          test_leg_foot_roll_attr_name_parity)

    if SKIPS:
        print(f"MODULE SWAP PARITY TESTS: {len(SKIPS)} SKIPPED (not counted "
              f"as pass): {SKIPS}")
    if FAILURES:
        print(f"MODULE SWAP PARITY TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("MODULE SWAP PARITY TESTS: OK"
          + (f" - {len(SKIPS)} SKIP" if SKIPS else " - 0 SKIP"))


if __name__ == "__main__":
    main()
