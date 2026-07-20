# _dev/test_fab_migrate_scene.py
"""mayapy SCENE tests for fab_migrate_scene.migrate_scene() — the
KinematicSolutions -> FabricatorStudio rebrand sweep (S2, version.py
1.3.0) node/attr identifier upgrade.

Synthesizes a legacy scene IN-TEST (nodes/attrs/sets/groups authored
under the OLD ksfab_ / ks_*_grp / AM_ / am_ names, exactly as the
pre-rename code would have left them — no real old scene file needed),
then proves:
  - migrate_scene() renames every node/attr the D1/D2/D3 approved plan
    covers, to the new fab_/FAB_ names.
  - Post-migration, a real Build-Rig-level operation (nodes.get_registry
    + nodes.get_all_component_nodes + nodes.get_component_type/id — the
    NEW code's own discovery path, which only ever looks for the NEW
    marker names) resolves the migrated scene correctly.
  - The deliberate legacy exception (ksfab_guides_grp / ksfab_guides_marker)
    is left untouched.
  - migrate_scene() is idempotent: a second run reports zero changes.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_fab_migrate_scene.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


class Skip(Exception):
    """Raise from a test body to mark it SKIPPED."""


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


def _build_legacy_scene():
    """Synthesize a pre-rename scene by hand, using the OLD literal
    node/attr names exactly as pre-1.3.0 nodes.py / limb_node.py /
    rig_binding.py / export_core.py / anim_core.py would have created
    them. Returns a dict of the created node names for assertions.

    NOT going through the (now-renamed) real create_* functions on
    purpose — those write the NEW names, which is exactly what this
    fixture must NOT start from.
    """
    import maya.cmds as cmds

    cmds.file(new=True, force=True)

    # --- registry (old: ksfab_registry) ---
    registry = cmds.createNode('network', name='ksfab_registry')
    cmds.addAttr(registry, ln='ksfab_registry', dt='string')
    cmds.setAttr(f'{registry}.ksfab_registry', 'registry', type='string')
    cmds.addAttr(registry, ln='blueprint_name', dt='string')
    cmds.setAttr(f'{registry}.blueprint_name', 'LegacyBlueprint', type='string')
    cmds.addAttr(registry, ln='fabricator_version', dt='string')
    cmds.setAttr(f'{registry}.fabricator_version', '1.2.0', type='string')
    cmds.addAttr(registry, ln='components', at='message', multi=True,
                 indexMatters=False)

    # --- one skeleton joint + a component node (old: ksfab_<id>) ---
    cmds.select(clear=True)
    root_joint = cmds.joint(name='legacy_root')
    cmds.select(clear=True)

    component = cmds.createNode('network', name='ksfab_C0')
    cmds.addAttr(component, ln='ksfab_component', dt='string')
    cmds.setAttr(f'{component}.ksfab_component', 'World', type='string')
    cmds.addAttr(component, ln='component_id', dt='string')
    cmds.setAttr(f'{component}.component_id', 'C0', type='string')
    cmds.addAttr(component, ln='component_type', dt='string')
    cmds.setAttr(f'{component}.component_type', 'World', type='string')
    cmds.addAttr(component, ln='joints', at='message', multi=True,
                 indexMatters=False)
    cmds.connectAttr(f'{root_joint}.message', f'{component}.joints[0]')
    cmds.addAttr(component, ln='registry', at='message')
    cmds.connectAttr(f'{registry}.message', f'{component}.registry')
    cmds.connectAttr(f'{component}.message', f'{registry}.components',
                     nextAvailable=True)

    # --- a ctrl carrying the legacy tag attrs ---
    ctrl = cmds.circle(name='legacy_root_ctrl')[0]
    cmds.addAttr(ctrl, ln='ksfab_role', dt='string')
    cmds.setAttr(f'{ctrl}.ksfab_role', 'root_ctrl', type='string')
    cmds.addAttr(ctrl, ln='ksfab_joint_index', at='long', dv=0)
    cmds.addAttr(ctrl, ln='ksfab_bind_matrix', dt='matrix')
    cmds.setAttr(f'{ctrl}.ksfab_bind_matrix',
                *cmds.xform(ctrl, q=True, ws=True, matrix=True), type='matrix')
    cmds.addAttr(ctrl, ln='ksfab_owner', at='message')
    cmds.connectAttr(f'{component}.message', f'{ctrl}.ksfab_owner')

    # --- DAG groups (old: ks_controls_grp / ks_nulls_grp), + ksfab_armature_grp ---
    rig_grp = cmds.createNode('transform', name='rig_grp')
    controls_grp = cmds.createNode('transform', name='ks_controls_grp', parent=rig_grp)
    nulls_grp = cmds.createNode('transform', name='ks_nulls_grp', parent=rig_grp)
    armature_grp = cmds.createNode('transform', name='ksfab_armature_grp')
    cmds.parent(ctrl, controls_grp)

    # --- pivots grp (old: ksfab_pivots_grp / ksfab_pivots_marker) ---
    pivots_grp = cmds.createNode('transform', name='ksfab_pivots_grp')
    cmds.addAttr(pivots_grp, ln='ksfab_pivots_marker', dt='string')
    cmds.setAttr(f'{pivots_grp}.ksfab_pivots_marker', 'pivots', type='string')

    # --- selection sets master (old: ksfab_selection_sets) ---
    child_set = cmds.sets([ctrl], name='all_center_ctrls')
    master_set = cmds.sets([child_set], name='ksfab_selection_sets')

    # --- limb node (old: ksfab_limb marker, ksfab_limb_<name> node name) ---
    limb = cmds.createNode('network', name='ksfab_limb_legacy_root')
    cmds.addAttr(limb, ln='ksfab_limb', dt='string')
    cmds.setAttr(f'{limb}.ksfab_limb', 'limb', type='string')
    cmds.addAttr(limb, ln='top_joint', at='message')
    cmds.connectAttr(f'{root_joint}.message', f'{limb}.top_joint')

    # --- AM_RigBinding (old: am_rig_binding marker, AM_RigBinding_<label>) ---
    binding = cmds.createNode('network', name='AM_RigBinding_Legacy')
    cmds.addAttr(binding, ln='am_rig_binding', dt='string')
    cmds.setAttr(f'{binding}.am_rig_binding', 'binding', type='string')
    cmds.addAttr(binding, ln='rig_label', dt='string')
    cmds.setAttr(f'{binding}.rig_label', 'Legacy', type='string')
    cmds.addAttr(binding, ln='root_joint', at='message')
    cmds.connectAttr(f'{root_joint}.message', f'{binding}.root_joint')

    # --- AM_ExporterRegistry / AM_Export_<name> ---
    exp_registry = cmds.createNode('network', name='AM_ExporterRegistry')
    cmds.addAttr(exp_registry, ln='am_exporter_registry', dt='string')
    cmds.setAttr(f'{exp_registry}.am_exporter_registry', 'registry', type='string')
    exp_entry = cmds.createNode('network', name='AM_Export_LegacyMesh')
    cmds.addAttr(exp_entry, ln='am_export_entry', dt='string')
    cmds.setAttr(f'{exp_entry}.am_export_entry', 'entry', type='string')

    # --- AM_AnimRegistry / AM_AnimClip_<name> ---
    anim_registry = cmds.createNode('network', name='AM_AnimRegistry')
    cmds.addAttr(anim_registry, ln='am_anim_registry', dt='string')
    cmds.setAttr(f'{anim_registry}.am_anim_registry', 'registry', type='string')
    anim_clip = cmds.createNode('network', name='AM_AnimClip_LegacyWalk')
    cmds.addAttr(anim_clip, ln='am_anim_clip', dt='string')
    cmds.setAttr(f'{anim_clip}.am_anim_clip', 'clip', type='string')

    # --- deliberate legacy exception: pre-Armature stale guides group ---
    guides_grp = cmds.createNode('transform', name='ksfab_guides_grp')
    cmds.addAttr(guides_grp, ln='ksfab_guides_marker', dt='string')
    cmds.setAttr(f'{guides_grp}.ksfab_guides_marker', 'guides', type='string')

    return {
        'registry': registry, 'component': component, 'ctrl': ctrl,
        'root_joint': root_joint, 'rig_grp': rig_grp,
        'controls_grp': controls_grp, 'nulls_grp': nulls_grp,
        'armature_grp': armature_grp, 'pivots_grp': pivots_grp,
        'master_set': master_set, 'limb': limb, 'binding': binding,
        'exp_registry': exp_registry, 'exp_entry': exp_entry,
        'anim_registry': anim_registry, 'anim_clip': anim_clip,
        'guides_grp': guides_grp,
    }


def test_migrate_renames_every_node():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fab_migrate_scene as mig

    fixture = _build_legacy_scene()
    report = mig.migrate_scene()

    assert not cmds.objExists('ksfab_registry')
    assert cmds.objExists('fab_registry')
    assert not cmds.objExists('ksfab_C0')
    assert cmds.objExists('fab_C0')
    assert not cmds.objExists('ks_controls_grp')
    assert cmds.objExists('fab_controls_grp')
    assert not cmds.objExists('ks_nulls_grp')
    assert cmds.objExists('fab_nulls_grp')
    assert not cmds.objExists('ksfab_armature_grp')
    assert cmds.objExists('fab_armature_grp')
    assert not cmds.objExists('ksfab_pivots_grp')
    assert cmds.objExists('fab_pivots_grp')
    assert not cmds.objExists('ksfab_selection_sets')
    assert cmds.objExists('fab_selection_sets')
    assert not cmds.objExists('ksfab_limb_legacy_root')
    assert cmds.objExists('fab_limb_legacy_root')
    assert not cmds.objExists('AM_RigBinding_Legacy')
    assert cmds.objExists('FAB_RigBinding_Legacy')
    assert not cmds.objExists('AM_ExporterRegistry')
    assert cmds.objExists('FAB_ExporterRegistry')
    assert not cmds.objExists('AM_Export_LegacyMesh')
    assert cmds.objExists('FAB_Export_LegacyMesh')
    assert not cmds.objExists('AM_AnimRegistry')
    assert cmds.objExists('FAB_AnimRegistry')
    assert not cmds.objExists('AM_AnimClip_LegacyWalk')
    assert cmds.objExists('FAB_AnimClip_LegacyWalk')

    assert len(report['nodes_renamed']) >= 9, report['nodes_renamed']
    assert not report['errors'], report['errors']


def test_migrate_renames_ctrl_tag_attrs():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fab_migrate_scene as mig

    fixture = _build_legacy_scene()
    ctrl_before = fixture['ctrl']
    mig.migrate_scene()
    # The ctrl itself wasn't renamed (only its tag ATTRS were) — same
    # object, just under fab_* attr names now.
    ctrl = ctrl_before
    assert cmds.objExists(ctrl)
    assert not cmds.attributeQuery('ksfab_role', node=ctrl, exists=True)
    assert cmds.attributeQuery('fab_role', node=ctrl, exists=True)
    assert cmds.getAttr(f'{ctrl}.fab_role') == 'root_ctrl'
    assert not cmds.attributeQuery('ksfab_joint_index', node=ctrl, exists=True)
    assert cmds.attributeQuery('fab_joint_index', node=ctrl, exists=True)
    assert not cmds.attributeQuery('ksfab_bind_matrix', node=ctrl, exists=True)
    assert cmds.attributeQuery('fab_bind_matrix', node=ctrl, exists=True)
    assert not cmds.attributeQuery('ksfab_owner', node=ctrl, exists=True)
    assert cmds.attributeQuery('fab_owner', node=ctrl, exists=True)
    owner = cmds.listConnections(f'{ctrl}.fab_owner', source=True,
                                 destination=False) or []
    assert owner and owner[0] == 'fab_C0', owner


def test_legacy_guides_exception_untouched():
    """The dead pre-Armature guides group must NOT be renamed — nodes.py
    hunts for it BY THE OLD NAME on purpose (see its 'Guides group
    (LEGACY)' section)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fab_migrate_scene as mig
    from maya_tools.rigging.fabricator import nodes

    _build_legacy_scene()
    report = mig.migrate_scene()

    assert cmds.objExists('ksfab_guides_grp')
    assert not cmds.objExists('fab_guides_grp')
    assert cmds.attributeQuery('ksfab_guides_marker', node='ksfab_guides_grp',
                               exists=True)
    for old, new in report['nodes_renamed']:
        assert old != 'ksfab_guides_grp', report['nodes_renamed']

    # nodes.get_guides_grp() (the real reader) must still find it.
    assert nodes.get_guides_grp() == 'ksfab_guides_grp'


def test_migrated_scene_supports_registry_and_component_discovery():
    """Build-Rig-level proof: the NEW code's own discovery path (which
    only ever looks for the NEW marker names) resolves the migrated
    scene — registry lookup, component enumeration, component type/id
    reads."""
    from maya_tools.rigging.fabricator import fab_migrate_scene as mig
    from maya_tools.rigging.fabricator import nodes

    fixture = _build_legacy_scene()
    mig.migrate_scene()

    reg = nodes.get_registry()
    assert reg == 'fab_registry', reg
    assert nodes.get_blueprint_name(reg) == 'LegacyBlueprint'

    components = nodes.get_all_component_nodes(reg)
    assert components == ['fab_C0'], components
    assert nodes.get_component_type(components[0]) == 'World'
    assert nodes.get_component_id(components[0]) == 'C0'
    assert nodes.get_component_joints(components[0]) == [fixture['root_joint']]


def test_migrate_is_idempotent():
    from maya_tools.rigging.fabricator import fab_migrate_scene as mig

    _build_legacy_scene()
    first = mig.migrate_scene()
    assert first['nodes_renamed'], 'first run should have found legacy names to migrate'

    second = mig.migrate_scene()
    assert second['nodes_renamed'] == [], second['nodes_renamed']
    assert second['attrs_renamed'] == [], second['attrs_renamed']
    assert second['errors'] == [], second['errors']


def test_migrate_on_never_legacy_scene_is_a_noop():
    """A brand-new scene with nothing to migrate must report empty,
    never error."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fab_migrate_scene as mig

    cmds.file(new=True, force=True)
    report = mig.migrate_scene()
    assert report == {'nodes_renamed': [], 'attrs_renamed': [], 'errors': []}


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith('test_') and callable(fn)
    ]
    print(f'Running {len(tests)} tests from test_fab_migrate_scene.py...')
    for name, fn in tests:
        check(name, fn)

    print(f'\n{len(tests) - len(FAILURES) - len(SKIPS)} passed, '
          f'{len(FAILURES)} failed, {len(SKIPS)} skipped '
          f'(of {len(tests)})')
    if FAILURES:
        print('\nFAILURES:')
        for f in FAILURES:
            print(f'  - {f}')
    if SKIPS:
        print('\nSKIPS:')
        for s in SKIPS:
            print(f'  - {s}')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
