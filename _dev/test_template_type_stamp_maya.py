# _dev/test_template_type_stamp_maya.py
"""mayapy test: template save/load round-trips the free IKArm component
type — the version-gated legacy map fires on the FILE's age, never
unconditionally.

Regression for the 2026-07-19 ribbon-arms bug: fs_app.load() blanked the
registry's fabricator_version for the component-spawn window so old YAML
could route through _VERSION_GATED_LEGACY_TYPE_MAP ('IKArm' ->
'RibbonIKArm', gate 1.1.0), but the blueprint file carried no version of
its own — every load read as legacy, and a template saved minutes earlier
with the free basic IKArm (which reclaimed the vacated type string) came
back with ribbon arms on both sides. Fix (1.4.0): save stamps
fabricator_version into the YAML; load holds the registry at the FILE's
stamp for the whole raw-YAML resolution window (spawn loop + pivot sync).

Existing-coverage gap this closes: test_derived_limbs.py's
test_blueprint_save_load_derives_limbs round-trips an IKArm but never
asserts the loaded TYPE — it stayed green through the bug.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_template_type_stamp_maya.py
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


ARM_JOINTS = {
    'lf': ('upperarm_l', 'lowerarm_l', 'hand_l'),
    'rt': ('upperarm_r', 'lowerarm_r', 'hand_r'),
}


def _chain(names, base_tx):
    import maya.cmds as cmds
    cmds.select(clear=True)
    parent = None
    for i, n in enumerate(names):
        j = cmds.joint(name=n, position=(base_tx + i * 10.0, 100.0, 0.0),
                       absolute=True)
        parent = j
    return parent


def _biped_arms_scene():
    """Fresh scene: registry + a 3-joint arm chain per side + one free
    IKArm component on each — the exact shape of Adrian's repro."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes

    cmds.file(new=True, force=True)
    nodes.create_registry('TypeStampTest')
    _chain(ARM_JOINTS['lf'], 10.0)
    _chain(ARM_JOINTS['rt'], -30.0)

    for side, joints in ARM_JOINTS.items():
        nodes.create_component_node(
            component_id=f'{joints[0]}_ikarm',
            component_type='IKArm',
            joints=list(joints),
            parent_plug='', side=side,
            options={}, persisted={}, region='arm',
        )


def _loaded_types():
    """Arm component types in the scene ('World' root excluded — load
    spawns it on every blueprint; it is not under test here)."""
    from maya_tools.rigging.fabricator import nodes
    return sorted(t for t in (nodes.get_component_type(c)
                              for c in nodes.get_all_component_nodes())
                  if t != 'World')


def test_save_stamps_file_and_ikarm_survives_roundtrip():
    import maya.cmds as cmds
    import yaml
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION

    _biped_arms_scene()
    tmp = Path(tempfile.mkdtemp()) / 'type_stamp_test.blueprint.yaml'
    fs_app.save(str(tmp))

    data = yaml.safe_load(tmp.read_text(encoding='utf-8'))
    assert data.get('fabricator_version') == FABRICATOR_VERSION, \
        f"save did not stamp the file: {data.get('fabricator_version')!r}"
    saved_types = [c['type'] for c in data['components']]
    assert saved_types == ['IKArm', 'IKArm'], saved_types

    cmds.file(new=True, force=True)
    fs_app.load(str(tmp))

    # THE regression: both arms must still be the free basic arm.
    assert _loaded_types() == ['IKArm', 'IKArm'], (
        f'ribbon-arms regression: loaded types {_loaded_types()}')

    # The file-stamp window must be closed again after load.
    assert nodes.get_registry_fabricator_version() == FABRICATOR_VERSION, \
        'load left the registry off-CURRENT'


def test_unstamped_legacy_file_still_maps_to_ribbon():
    """A pre-stamping file ('' fabricator_version) with 'IKArm' data is
    ribbon-era by definition — the legacy map must still fire for it."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator.blueprint import io as blueprint_io
    from maya_tools.rigging.fabricator.blueprint.schema import (
        Blueprint, JointSpec, ComponentSpec,
    )

    joints, specs, prev = ARM_JOINTS['lf'], [], None
    for i, n in enumerate(joints):
        specs.append(JointSpec(
            name=n, parent=prev,
            translate=[10.0 if prev else 0.0, 0.0 if prev else 100.0, 0.0],
            world_translate=[10.0 + i * 10.0, 100.0, 0.0],
        ))
        prev = n

    bp = Blueprint(
        name='LegacyRibbonEra',
        skeleton_joints=specs,
        components=[ComponentSpec(
            type='IKArm', joints=list(joints),
            id=f'{joints[0]}_ikarm', side='lf', region='arm',
        )],
        # fabricator_version deliberately unset — pre-stamping file.
    )
    tmp = Path(tempfile.mkdtemp()) / 'legacy_era_test.blueprint.yaml'
    blueprint_io.write_yaml(bp, tmp)

    cmds.file(new=True, force=True)
    fs_app.load(str(tmp))
    assert _loaded_types() == ['RibbonIKArm'], (
        f'legacy migration broke: loaded types {_loaded_types()}')


# ── Fragment parity (2026-07-20): .limb.yaml gets the same file stamp ──

def _ikarm_fragment(prefix, stamp):
    """Minimal 3-joint fragment carrying ONE IKArm component — the shape
    canvas 'save as .limb.yaml' produces for a free arm (mirrors
    test_limb_units_maya.py's _ikarm_component_fragment, per that file's
    copy-per-suite convention)."""
    from maya_tools.rigging.fabricator.limbs.schema import (
        LimbFragment, ExternalAnchor,
    )
    from maya_tools.rigging.fabricator.blueprint.schema import (
        JointSpec, ComponentSpec,
    )
    frag = LimbFragment(
        name='arm',
        external_anchor=ExternalAnchor(plug_kind='matrix'),
        skeleton_joints=[
            JointSpec(name=f'{prefix}_shoulder', parent='<EXTERNAL>',
                      translate=[0.0, 0.0, 0.0], radius=0.5),
            JointSpec(name=f'{prefix}_elbow', parent=f'{prefix}_shoulder',
                      translate=[10.0, 0.0, 0.0], radius=0.4),
            JointSpec(name=f'{prefix}_wrist', parent=f'{prefix}_elbow',
                      translate=[10.0, 0.0, 0.0], radius=0.35),
        ],
        components=[
            ComponentSpec(id=f'{prefix}_C0', type='IKArm',
                          joints=[f'{prefix}_shoulder', f'{prefix}_elbow',
                                  f'{prefix}_wrist'],
                          parent_plug='<EXTERNAL>.joint_out',
                          side='lf', role='', region='arm', options={},
                          persisted={}),
        ],
    )
    frag.fabricator_version = stamp
    return frag


def _fragment_host_scene(prefix):
    """Fresh scene with a stamped (CURRENT) registry + a root joint
    hosting a World component — the minimal drop target."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(name=f'{prefix}_root')
    cmds.select(clear=True)
    nodes.create_registry(f'{prefix}_bp')
    nodes.set_registry_root_joint(root)
    nodes.create_component_node(
        component_id=f'{prefix}_world', component_type='World',
        joints=[root], parent_plug='', side='md',
        options={}, persisted={},
    )
    return root


def _dropped_type(prefix):
    from maya_tools.rigging.fabricator import nodes
    for c in nodes.get_all_component_nodes():
        if nodes.get_component_id(c) == f'{prefix}_C0':
            return nodes.get_component_type(c)
    raise AssertionError(f'{prefix}_C0 component node not found after drop')


def test_save_limb_stamps_file():
    import maya.cmds as cmds
    import yaml
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION

    # Anchor (clavicle) stays outside the fragment; its descendant chain
    # + owning component become the limb (save_limb's HOST contract).
    cmds.file(new=True, force=True)
    nodes.create_registry('SaveLimbStampTest')
    cmds.select(clear=True)
    cmds.joint(name='clav_l', position=(5.0, 100.0, 0.0))
    for i, n in enumerate(ARM_JOINTS['lf']):
        cmds.joint(name=n, position=(10.0 + i * 10.0, 100.0, 0.0),
                   absolute=True)
    nodes.create_component_node(
        component_id='upperarm_l_ikarm', component_type='IKArm',
        joints=list(ARM_JOINTS['lf']), parent_plug='', side='lf',
        options={}, persisted={}, region='arm',
    )
    tmp = Path(tempfile.mkdtemp()) / 'stamped_arm.limb.yaml'
    fs_app.save_limb('clav_l', str(tmp))
    data = yaml.safe_load(tmp.read_text(encoding='utf-8'))
    assert data.get('fabricator_version') == FABRICATOR_VERSION, \
        f"save_limb did not stamp the file: {data.get('fabricator_version')!r}"


def test_stamped_fragment_ikarm_stays_free_on_drop():
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment
    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION

    root = _fragment_host_scene('fragnew')
    apply_limb_fragment(_ikarm_fragment('fragnew', FABRICATOR_VERSION), root)
    assert _dropped_type('fragnew') == 'IKArm', (
        f"stamped fragment's free arm ribbon-swapped: "
        f"{_dropped_type('fragnew')}")


def test_unstamped_fragment_ikarm_maps_to_ribbon_on_drop():
    """THE parity gap: an unstamped fragment is pre-stamping data — its
    'IKArm' is ribbon-era and must migrate, even though the LIVE scene's
    registry is stamped CURRENT (which is why the scene-stamp gate can't
    see it)."""
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment

    root = _fragment_host_scene('fragold')
    apply_limb_fragment(_ikarm_fragment('fragold', ''), root)
    assert _dropped_type('fragold') == 'RibbonIKArm', (
        f"unstamped ribbon-era fragment loaded as the free arm: "
        f"{_dropped_type('fragold')}")


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')
    try:
        check("test_save_stamps_file_and_ikarm_survives_roundtrip",
              test_save_stamps_file_and_ikarm_survives_roundtrip)
        check("test_unstamped_legacy_file_still_maps_to_ribbon",
              test_unstamped_legacy_file_still_maps_to_ribbon)
        check("test_save_limb_stamps_file", test_save_limb_stamps_file)
        check("test_stamped_fragment_ikarm_stays_free_on_drop",
              test_stamped_fragment_ikarm_stays_free_on_drop)
        check("test_unstamped_fragment_ikarm_maps_to_ribbon_on_drop",
              test_unstamped_fragment_ikarm_maps_to_ribbon_on_drop)
    finally:
        try:
            maya.standalone.uninitialize()
        except Exception:
            pass

    if FAILURES:
        print(f"TEMPLATE TYPE STAMP TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("TEMPLATE TYPE STAMP TESTS: OK")


if __name__ == "__main__":
    main()
