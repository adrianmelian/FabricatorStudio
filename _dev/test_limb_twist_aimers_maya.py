# _dev/test_limb_twist_aimers_maya.py
"""mayapy test: twist-joint aimers survive the limb fragment round-trip.

Regression for the 2026-07-20 "ribbon + simple arm limbs come in with
broken twist aimers" bug (Adrian, live scene). Two independent gaps, both
covered here:

1. CAPTURE (deep): _snapshot_limb_fragment_from_scene built each JointSpec
   WITHOUT aim_target/aim_offset — unlike the blueprint snapshot, which
   captures both. limbs/io.py already wrote, read, and restored those
   fields; only the save side never populated them, so every fragment on
   disk carries zero aimer state.

2. TWIST DEFAULT (narrow): with no authored target, _setup_limb_aimers
   falls back to joa.seed_aimer_from_detection, which only ever targets a
   CHILD. A twist joint is childless, so the seeder no-ops and the aimer
   sits at enum index 0 = Local — pointing nowhere useful. The
   negative-parent convention (aim at Parent, flip 180 RZ so it lands on
   the segment end — Adrian's 2026-07-13 trick) lived ONLY in
   armature._bake_and_restore_aimers, gated to aimers created fresh during
   that build. A fragment drop creates its aimers first, so by build time
   they aren't fresh and the convention never applied.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_limb_twist_aimers_maya.py
"""
import sys
import tempfile
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
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


def _arm_with_twists(prefix):
    """clavicle -> upperarm -> (twist_01, twist_02, lowerarm -> hand).

    Twists are childless, carry the 'twist' token, and are NOT component
    members — the three conditions limb_node twist adoption requires.
    Returns (clavicle, upperarm, lowerarm, hand, [twists]).
    """
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes

    cmds.file(new=True, force=True)
    nodes.create_registry(f'{prefix}_bp')

    cmds.select(clear=True)
    clav = cmds.joint(name=f'{prefix}_clavicle_l', position=(5, 100, 0))
    upper = cmds.joint(name=f'{prefix}_upperarm_l', position=(15, 100, 0))
    lower = cmds.joint(name=f'{prefix}_lowerarm_l', position=(35, 100, 0))
    hand = cmds.joint(name=f'{prefix}_hand_l', position=(55, 100, 0))

    twists = []
    for i, x in ((1, 22.0), (2, 29.0)):
        cmds.select(clear=True)
        t = cmds.joint(name=f'{prefix}_upperarm_twist_0{i}_l',
                       position=(x, 100, 0))
        twists.append(cmds.parent(t, upper)[0])
    cmds.select(clear=True)

    nodes.set_registry_root_joint(clav)
    nodes.create_component_node(
        component_id=f'{prefix}_world', component_type='World',
        joints=[clav], parent_plug='', side='md', options={}, persisted={},
    )
    # IKArm on the main chain only — derive_limb adopts the twists and
    # gives each a 'distribute' follow rule (armature.is_twist_joint's gate).
    nodes.create_component_node(
        component_id=f'{prefix}_ikarm', component_type='IKArm',
        joints=[upper, lower, hand], parent_plug='', side='lf',
        options={}, persisted={}, region='arm',
    )
    return clav, upper, lower, hand, twists


def _aim_label(jnt):
    """The aimer's CURRENT enum label ('Local' | 'World' | 'Parent' | a
    child name) — what the user sees in the aimTarget dropdown."""
    import maya.cmds as cmds
    from maya_tools.rigging.joint_orient import joint_orient_app as joa
    aimer = joa.aimer_name(jnt)
    assert cmds.objExists(aimer), f'no aimer on {jnt}'
    enum = (cmds.attributeQuery('aimTarget', node=aimer,
                                listEnum=True) or [''])[0].split(':')
    return enum[cmds.getAttr(f'{aimer}.aimTarget')]


def test_twist_gate_sees_dropped_twists():
    """Fixture sanity + the load-bearing ordering claim: derive_limb runs
    inside create_component_node, so the distribute rule (which
    armature.is_twist_joint gates on) exists before aimer setup."""
    from maya_tools.rigging import fabricator  # noqa: F401
    from maya_tools.rigging.fabricator import armature, follow_rules as fr

    _, _, _, _, twists = _arm_with_twists('gate')
    for t in twists:
        rule = fr.get_follow_rule(t)
        assert rule and rule['kind'] == 'distribute', (
            f'{t} did not get a distribute follow rule: {rule}')
        assert armature.is_twist_joint(t), f'{t} not seen as a twist joint'


def test_save_limb_captures_twist_aimer_state():
    """CAPTURE gap: a fragment saved from a scene whose twist aimers carry
    the negative-parent convention must persist that state."""
    import yaml
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.joint_orient import joint_orient_app as joa

    clav, _, _, _, twists = _arm_with_twists('cap')
    for j in (clav,) + tuple(twists):
        joa.create_aimer(j)
    for t in twists:
        assert joa.point_aimer_at_parent_flipped(t), f'setup failed on {t}'

    tmp = Path(tempfile.mkdtemp()) / 'cap_arm.limb.yaml'
    fs_app.save_limb(clav, str(tmp))
    data = yaml.safe_load(tmp.read_text(encoding='utf-8'))
    by_name = {j['name']: j for j in data['skeleton']['joints']}

    for t in twists:
        spec = by_name[t]
        assert spec.get('aim_target') == 'Parent', (
            f"{t} aimer state not captured into the fragment: "
            f"aim_target={spec.get('aim_target')!r}")
        rz = (spec.get('aim_offset') or [0, 0, 0])[2]
        assert abs(abs(rz) - 180.0) < 1e-3, (
            f'{t} lost the 180 RZ flip: aim_offset={spec.get("aim_offset")}')


def test_dropped_twist_without_aimer_data_gets_parent_flip():
    """TWIST DEFAULT gap: THE bug Adrian hit. A fragment carrying no aimer
    state at all (every fragment saved before the capture fix) must still
    land its twists on the negative-parent convention, not Local."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator.limbs import io as limbs_io
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment
    from maya_tools.rigging.joint_orient import joint_orient_app as joa

    # Author the fragment on disk, then strip every aimer key from it —
    # byte-for-byte the shape of Adrian's existing library files.
    clav, _, _, _, twists = _arm_with_twists('drop')
    tmp = Path(tempfile.mkdtemp()) / 'drop_arm.limb.yaml'
    fs_app.save_limb(clav, str(tmp))
    frag = limbs_io.read_yaml(tmp)
    for j in frag.skeleton_joints:
        j.aim_target = ''
        j.aim_offset = [0.0, 0.0, 0.0]

    # Fresh host scene, drop it.
    cmds.file(new=True, force=True)
    from maya_tools.rigging.fabricator import nodes
    cmds.select(clear=True)
    host = cmds.joint(name='hostroot')
    cmds.select(clear=True)
    nodes.create_registry('drophost_bp')
    nodes.set_registry_root_joint(host)
    nodes.create_component_node(
        component_id='drophost_world', component_type='World',
        joints=[host], parent_plug='', side='md', options={}, persisted={},
    )
    apply_limb_fragment(frag, host)

    seg_end = 'drop_lowerarm_l'
    for t in twists:
        assert cmds.objExists(t), f'{t} did not land in the drop'
        assert _aim_label(t) == 'Parent', (
            f'{t} aimer came in as {_aim_label(t)!r} — the negative-parent '
            f'convention did not apply on drop (this is the reported bug)')
        # Assert the frame-independent OUTCOME, not the aimer's local rz:
        # apply_limb_fragment ends in build_armature, which bakes the
        # aimer's orientation into the joint and restores aimers by WORLD
        # orientation — so the 180 RZ correctly reads ~0 afterward (it
        # lives in the joint now). Reading rz here would be reading the
        # bake's residual instead of its input. The real invariant: the
        # twist's aim axis points DOWN-segment at the end (the elbow),
        # which is the whole point of aiming at Parent and flipping.
        wm = cmds.xform(t, q=True, ws=True, matrix=True)
        tp = cmds.xform(t, q=True, ws=True, t=True)
        ep = cmds.xform(seg_end, q=True, ws=True, t=True)
        pp = cmds.xform('drop_upperarm_l', q=True, ws=True, t=True)
        to_end = [ep[i] - tp[i] for i in range(3)]
        to_parent = [pp[i] - tp[i] for i in range(3)]
        aim = wm[0:3]

        def dot(a, b):
            return sum(a[i] * b[i] for i in range(3))

        assert dot(aim, to_end) > 0, (
            f'{t} aim axis does not point at the segment end: '
            f'dot={dot(aim, to_end):.3f}')
        assert dot(aim, to_parent) < 0, (
            f'{t} aim axis points back at the parent — the 180 flip was '
            f'lost: dot={dot(aim, to_parent):.3f}')


def test_dropped_nontwist_keeps_geometric_seeding():
    """Guard: the twist branch must not disturb ordinary joints — a
    non-twist childless joint still routes through geometric detection
    and stays Local (authored orientation is never rewritten)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment
    from maya_tools.rigging.fabricator.limbs.schema import (
        LimbFragment, ExternalAnchor,
    )
    from maya_tools.rigging.fabricator.blueprint.schema import JointSpec

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    host = cmds.joint(name='plainhost_root')
    cmds.select(clear=True)
    nodes.create_registry('plainhost_bp')
    nodes.set_registry_root_joint(host)
    nodes.create_component_node(
        component_id='plainhost_world', component_type='World',
        joints=[host], parent_plug='', side='md', options={}, persisted={},
    )

    frag = LimbFragment(
        name='plain',
        external_anchor=ExternalAnchor(plug_kind='matrix'),
        skeleton_joints=[
            JointSpec(name='plain_prop', parent='<EXTERNAL>',
                      translate=[5.0, 0.0, 0.0], radius=0.5),
        ],
        components=[],
    )
    apply_limb_fragment(frag, host)
    assert _aim_label('plain_prop') == 'Local', (
        f'a plain childless joint must stay Local, got '
        f'{_aim_label("plain_prop")!r}')


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')
    try:
        check("test_twist_gate_sees_dropped_twists",
              test_twist_gate_sees_dropped_twists)
        check("test_save_limb_captures_twist_aimer_state",
              test_save_limb_captures_twist_aimer_state)
        check("test_dropped_twist_without_aimer_data_gets_parent_flip",
              test_dropped_twist_without_aimer_data_gets_parent_flip)
        check("test_dropped_nontwist_keeps_geometric_seeding",
              test_dropped_nontwist_keeps_geometric_seeding)
    finally:
        try:
            maya.standalone.uninitialize()
        except Exception:
            pass

    if FAILURES:
        print(f"LIMB TWIST AIMER TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("LIMB TWIST AIMER TESTS: OK")


if __name__ == "__main__":
    main()
