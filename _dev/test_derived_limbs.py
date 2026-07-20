# _dev/test_derived_limbs.py
"""mayapy tests for Derived Limbs (spec 2026-07-11): limb identity +
membership as a pure function of scene + component contract, re-derived
at every seam — never serialized, never hand-edited.

Replaces the retired coverage of the authored-limb era (finger dial
tests, limb-block round-trips, mirror membership flipping,
test_mirror_limb_integrity.py) with the new contracts:

  - blueprint load derives limbs (THE 'template load has no limbs' fix)
  - region-bounded adoption (a leg can never join a spine/pelvis limb;
    legacy region-matched ancestor limbs still adopt)
  - feature gating (a leg runs no finger discovery — toes never fingers)
  - derivation is idempotent and self-healing (bogus membership clears)
  - fragment YAML never contains a limb: block
  - copied-joint .message echo release (branch_ops, unchanged behavior)

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_derived_limbs.py
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


def _short(n):
    return n.split('|')[-1].split(':')[-1]


def _shorts(names):
    return sorted(_short(n) for n in (names or []))


def _chain(parent, *joints):
    import maya.cmds as cmds
    out = []
    prev = parent
    for j in joints:
        if prev:
            cmds.select(prev)
        else:
            cmds.select(clear=True)
        out.append(cmds.joint(name=j))
        prev = out[-1]
    return out


def _arm_scene():
    """Fresh scene: root, arm chain with one finger, registry, IKArm."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes

    cmds.file(new=True, force=True)
    nodes.create_registry('DerivedLimbsTest')
    _chain(None, 'root')
    _chain('root', 'clavicle_l', 'upperarm_l', 'lowerarm_l', 'hand_l')
    _chain('hand_l', 'index_metacarpal_l', 'index_01_l')
    cnode = nodes.create_component_node(
        component_id='upperarm_l_ikarm', component_type='IKArm',
        joints=['upperarm_l', 'lowerarm_l', 'hand_l'],
        parent_plug='', side='lf', options={}, persisted={},
        region='arm',
    )
    return cnode


def test_component_add_derives_own_limb_with_fingers():
    from maya_tools.rigging.fabricator import limb_node as ln
    cnode = _arm_scene()
    limb = ln.get_limb_node('upperarm_l')
    assert limb, 'expected a derived limb anchored at the component top'
    assert cnode in ln.list_components(limb)
    assert _shorts(ln.list_finger_roots(limb)) == ['index_metacarpal_l'], \
        _shorts(ln.list_finger_roots(limb))
    assert _shorts(ln.list_curl_excluded(limb)) == ['index_metacarpal_l']


def test_leg_runs_no_finger_discovery():
    """Feature gating: toes under the ball are never fingers."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes, limb_node as ln

    cmds.file(new=True, force=True)
    nodes.create_registry('LegGateTest')
    _chain(None, 'pelvis')
    _chain('pelvis', 'thigh_l', 'calf_l', 'foot_l', 'ball_l')
    _chain('ball_l', 'toe_metacarpal_l', 'toe_01_l')   # finger-shaped bait
    nodes.create_component_node(
        component_id='thigh_l_ikleg', component_type='IKLeg',
        joints=['thigh_l', 'calf_l', 'foot_l', 'ball_l'],
        parent_plug='', side='lf', options={}, persisted={}, region='leg',
    )
    limb = ln.get_limb_node('thigh_l')
    assert limb, 'leg must still derive a limb (twists feature)'
    assert ln.list_finger_roots(limb) == [], (
        'a LEG ran finger discovery: %r' % ln.list_finger_roots(limb))


def test_region_bounded_adoption():
    """A leg must never join an ancestor (pelvis) limb; an arm DOES
    adopt a region-matched ancestor limb (legacy clavicle-anchored
    fragment limbs keep working)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes, limb_node as ln

    cmds.file(new=True, force=True)
    nodes.create_registry('RegionTest')
    _chain(None, 'pelvis')
    _chain('pelvis', 'thigh_l', 'calf_l', 'foot_l', 'ball_l')
    _chain('pelvis', 'clavicle_l', 'upperarm_l', 'lowerarm_l', 'hand_l')

    # A legacy spine-ish limb anchored at pelvis, with a memberless/
    # regionless profile (worst case for capture).
    ln.create_limb_node('RibbonSpine', 'pelvis')
    # A legacy arm limb anchored at the clavicle whose member declares
    # region 'arm' (the old fragment-drop shape).
    arm_limb = ln.create_limb_node('Advanced_Arm', 'clavicle_l')
    clav = nodes.create_component_node(
        component_id='clavicle_l_simple_fk', component_type='SimpleFK',
        joints=['clavicle_l'], parent_plug='', side='lf',
        options={}, persisted={}, region='arm',
    )
    ln.add_component(arm_limb, clav)

    nodes.create_component_node(
        component_id='thigh_l_ikleg', component_type='IKLeg',
        joints=['thigh_l', 'calf_l', 'foot_l', 'ball_l'],
        parent_plug='', side='lf', options={}, persisted={}, region='leg',
    )
    leg_limb = ln.get_limb_node('thigh_l')
    assert leg_limb, 'leg must create its OWN limb, not join pelvis'
    pelvis_limb = ln.get_limb_node('pelvis')
    assert ln.list_components(pelvis_limb) == [], (
        'the pelvis limb captured a component: %r'
        % ln.list_components(pelvis_limb))

    arm = nodes.create_component_node(
        component_id='upperarm_l_ikarm', component_type='IKArm',
        joints=['upperarm_l', 'lowerarm_l', 'hand_l'],
        parent_plug='', side='lf', options={}, persisted={}, region='arm',
    )
    assert arm in ln.list_components(arm_limb), (
        'region-matched ancestor limb was not adopted — got %r'
        % ln.list_components(arm_limb))
    assert ln.get_limb_node('upperarm_l') is None, (
        'arm created a duplicate limb instead of adopting the clavicle one')


def test_derive_is_idempotent_and_self_healing():
    from maya_tools.rigging.fabricator import nodes, limb_node as ln
    cnode = _arm_scene()
    limb = ln.get_limb_node('upperarm_l')
    before = (_shorts(ln.list_finger_roots(limb)),
              _shorts(ln.list_curl_excluded(limb)))

    nodes.derive_limb(cnode)
    nodes.derive_limb(cnode)
    after = (_shorts(ln.list_finger_roots(limb)),
             _shorts(ln.list_curl_excluded(limb)))
    assert before == after, (before, after)

    # Corruption heals: a bogus cross-limb member clears on re-derive.
    import maya.cmds as cmds
    cmds.select(clear=True)
    cmds.joint(name='not_a_finger')
    ln.add_finger_root(limb, 'not_a_finger')
    nodes.derive_limb(cnode)
    assert 'not_a_finger' not in _shorts(ln.list_finger_roots(limb)), \
        _shorts(ln.list_finger_roots(limb))


def test_blueprint_save_load_derives_limbs():
    """THE bug (Adrian 2026-07-11): a loaded template must carry the
    same limb identity a live add does — limbs re-derive at load."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    _arm_scene()
    tmp = Path(tempfile.mkdtemp()) / 'derived_limbs_test.blueprint.yaml'
    fs_app.save(str(tmp))

    cmds.file(new=True, force=True)
    fs_app.load(str(tmp))

    limb = ln.get_limb_node('upperarm_l')
    assert limb, 'loaded blueprint has NO derived limb — the load pass failed'
    assert _shorts(ln.list_finger_roots(limb)) == ['index_metacarpal_l'], \
        _shorts(ln.list_finger_roots(limb))
    cnode = nodes.find_component_node_by_id('upperarm_l_ikarm')
    assert cnode in ln.list_components(limb)

    # A fresh load re-stamps CURRENT once its spawn loop has resolved
    # every YAML type to canonical (Adrian 2026-07-12: no 'Rig predates
    # FS version stamping' notice on a template's first build).
    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION
    assert nodes.get_registry_fabricator_version() == FABRICATOR_VERSION, \
        'loaded blueprint left the registry unstamped'


def test_fragment_yaml_never_contains_limb_block():
    import yaml
    from maya_tools.rigging.fabricator import fs_app
    _arm_scene()
    tmp = Path(tempfile.mkdtemp()) / 'arm_test.limb.yaml'
    fs_app.save_limb('clavicle_l', str(tmp))
    data = yaml.safe_load(tmp.read_text(encoding='utf-8'))
    assert 'limb' not in data, 'fragment YAML still carries a limb: block'


def test_build_preflight_rederives_membership_end_to_end():
    """The build_modules preflight derive-all seam, end-to-end: fingers
    authored (as joints) AFTER the component was created join at build;
    bogus hand-wired membership clears (2026-07-11 review finding: the
    seam itself was untested)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cnode = _arm_scene()
    nodes.set_registry_root_joint('root')
    nodes.create_component_node(
        component_id='world', component_type='World',
        joints=['root'], parent_plug='', side='md',
        options={}, persisted={},
    )
    # Author a SECOND finger after component creation (Armature-phase
    # authoring), plus a bogus membership entry.
    _chain('hand_l', 'pinky_metacarpal_l', 'pinky_01_l')
    cmds.select(clear=True)
    cmds.joint(name='bogus_member')
    limb = ln.get_limb_node('upperarm_l')
    ln.add_finger_root(limb, 'bogus_member')

    fs_app.build_modules()
    try:
        roots = _shorts(ln.list_finger_roots(limb))
        assert 'pinky_metacarpal_l' in roots, (
            'build preflight did not pick up the newly authored finger: %r'
            % roots)
        assert 'bogus_member' not in roots, (
            'build preflight did not clear bogus membership: %r' % roots)
        assert cmds.objExists('pinky_metacarpal_l_ctrl'), (
            'the newly derived finger did not BUILD')
    finally:
        fs_app.unbuild_modules()


def test_limb_display_label_maps_type_to_display_name():
    from maya_tools.rigging.fabricator import limb_node as ln
    assert ln.limb_display_label('RibbonIKArm') == 'Ribbon IK Arm'
    assert ln.limb_display_label('IKLeg') == 'IK Leg'
    # legacy fragment-named limb_type passes through verbatim
    assert ln.limb_display_label('Advanced_Arm') == 'Advanced_Arm'
    assert ln.limb_display_label('') == ''


def test_release_copy_message_echo_strips_only_echo():
    """branch_ops helper (unchanged): a 'copied' joint wired into the
    source limb's finger_roots and the source component's joints[] gets
    released; the source joint's own connections survive."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import branch_ops, nodes
    from maya_tools.rigging.fabricator import limb_node as ln

    cnode = _arm_scene()
    limb = ln.get_limb_node('upperarm_l')
    # Simulate mirrorJoint's echo: fresh R joints arrive pre-wired into
    # the L limb + L component multis.
    _chain('root', 'clavicle_r', 'upperarm_r')
    ln.add_finger_root(limb, 'upperarm_r')      # bogus echo entry
    idx = len(cmds.getAttr(f'{cnode}.joints', multiIndices=True) or [])
    cmds.connectAttr('upperarm_r.message', f'{cnode}.joints[{idx}]')

    released = branch_ops._release_copy_message_echo(
        ['clavicle_r', 'upperarm_r'])
    assert released >= 2, released
    assert _shorts(ln.list_finger_roots(limb)) == ['index_metacarpal_l'], \
        _shorts(ln.list_finger_roots(limb))
    assert 'upperarm_r' not in _shorts(nodes.get_component_joints(cnode))


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'Running {len(tests)} tests from test_derived_limbs.py...')
    for name, fn in tests:
        check(name, fn)

    print(f'\n{len(tests) - len(FAILURES)} passed, {len(FAILURES)} failed '
          f'(of {len(tests)})')
    if FAILURES:
        print('\nFAILURES:')
        for f in FAILURES:
            print(f'  - {f}')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
