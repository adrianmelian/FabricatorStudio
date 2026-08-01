"""Import an Armature USD export and stand up a Fabricator rig around it.

THE SHAPE OF THIS TOOL
----------------------
It does NOT translate the Armature blueprint into a skeleton and build that. It imports
the USD, keeps the skeleton and skinCluster Maya's own importer produced, and writes a
Fabricator blueprint DESCRIBING those joints so `fs_app.load()` adopts them.

That works because `_create_skeleton_from_blueprint()` skips joints that already exist
("existing joints are skipped so additive loads stay legal"). So load() spawns the
registry and component nodes, restores aimers from the blueprint, registers the root and
stands the Armature up, and never touches the imported joints. The skinCluster is never
rebuilt and no weights are transferred.

The consequence worth stating: this tool derives no world transforms of its own. Joint
positions come from Maya's USD import and are captured back out with `xform -ws`. The
one exception is IKLeg's foot pivots, which are blueprint coordinates and therefore need
the unit scale (see classify.foot_pivots_for). BRIEF trap 2 is disarmed by construction
everywhere else.

WATCHERS ARE SUSPENDED FOR THE WHOLE OPERATION. A wholesale scene load with the Armature
watchers live is the known headless-green/GUI-dies crash class, and this tool is exactly
that shape.
"""
__author__ = "Adrian Melian"

import os

from maya import cmds

from maya_tools.rigging.armature_import import blueprint_source, classify
from maya_tools.rigging.armature_import.blueprint_source import ArmatureImportError

ROTATE_ORDERS = ('xyz', 'yzx', 'zxy', 'xzy', 'yxz', 'zyx')

# The UsdSkel Skeleton prim imports as a joint above the real root. It is scaffolding,
# not a rig joint, and is removed once the root is reparented to world.
SKEL_JOINT = 'skel'


def _noop(_msg):
    pass


class ImportReport(object):
    """What happened, for the log and for the tests."""

    def __init__(self):
        self.source = None
        self.blueprint_path = ''
        self.root_joint = ''
        self.mesh_transforms = []
        self.scene_joint_count = 0
        self.blueprint_joint_count = 0
        self.engine_joints = []
        self.components = {}
        self.notes = []
        self.up_axis = ''

    def as_lines(self):
        out = ['%s imported' % (self.source.name if self.source else '?'),
               '  %d blueprint joints, %d scene joints (%d engine extras)'
               % (self.blueprint_joint_count, self.scene_joint_count,
                  len(self.engine_joints)),
               '  stage %s-up, root joint "%s"' % (self.up_axis, self.root_joint)]
        if self.components:
            out.append('  components: ' + ', '.join(
                '%s x%d' % (k, v) for k, v in sorted(self.components.items())))
        if self.mesh_transforms:
            out.append('  mesh: ' + ', '.join(self.mesh_transforms))
        if self.blueprint_path:
            out.append('  blueprint: %s' % self.blueprint_path)
        for n in self.notes:
            out.append('  note: %s' % n)
        return out


def advanced_available():
    """True when every ribbon component type is registered.

    This is the honest entitlement signal: the free/paid split is a build-time file
    split (paid_manifest.json), and `modules/__init__` discovers types by walking the
    package. If the Advanced Ribbon Modules pack is not installed, the types simply are
    not there. No license check exists and none is added.
    """
    try:
        return set(classify.RIBBON_TYPES).issubset(set(registered_types()))
    except Exception:
        return False


def registered_types():
    from maya_tools.rigging.fabricator import modules as fab_modules
    reg = getattr(fab_modules, '_REGISTRY', None)
    if reg is None:
        getter = getattr(fab_modules, 'get_registry', None)
        reg = getter() if callable(getter) else {}
    return set(reg.keys())


# ── the pipeline ─────────────────────────────────────────────────────────────

def import_armature(usd_path, advanced=False, blueprint_path=None, load=True,
                    log=None, rig_name=None):
    """Import an Armature USD export and build the Fabricator blueprint around it.

    Args:
        usd_path:       .usd / .usdc / .usda written by Armature.
        advanced:       use the ribbon component set where it applies.
        blueprint_path: where to write the .blueprint.yaml. Defaults to beside the USD.
        load:           run fs_app.load() at the end (set False for pure-import tests).
        log:            callable(str) for progress.
        rig_name:       rig label for the FS window's Rig Name field. Defaults to the
                        export's character name; the UI exposes it as an editable field.

    Returns ImportReport. Raises ArmatureImportError for every refusal, having left the
    scene as it found it.
    """
    log = log or _noop
    from maya_tools.rigging.fabricator import armature_watch

    source = blueprint_source.read(usd_path)
    log(source.summary())

    problems = blueprint_source.check_export_guarantees(source)
    if problems:
        raise ArmatureImportError(
            'This export violates Armature\'s own export guarantees, so it is damaged '
            'rather than merely unusual:\n  - ' + '\n  - '.join(problems))

    report = ImportReport()
    report.source = source
    report.up_axis = source.up_axis
    report.blueprint_joint_count = len(source.joints)

    with armature_watch.suspended():
        created = _usd_import(source, log)
        uuids = cmds.ls(created, uuid=True) or []
        try:
            _reparent_out_of_wrapper(source, created, report, log)
            _strip_prefix(source, created, log)
            _drop_skel_joint(report, log)
            engine, root = _join_scene_to_blueprint(source, report, log)
            report.engine_joints = engine
            report.root_joint = root
        except Exception:
            # A refusal must not leave half a character in the scene.
            _cleanup(uuids)
            raise

        classification = classify.classify(
            source,
            advanced=advanced,
            available_types=registered_types(),
            axis_convert=_axis_converter(source),
        )
        report.components = classification.by_type()
        report.notes.extend(classification.notes)
        for note in classification.notes:
            log('note: %s' % note)

        path = _write_blueprint(source, classification, blueprint_path, report, log)
        report.blueprint_path = path

        # Posed verification, HERE and not later, because this is the only moment it
        # can run: the skeleton is imported and skinned but the blueprint is not yet
        # loaded, so nothing is driven. The moment fs_app.load() stands the Armature up,
        # every aim edge is solved by a single-chain IK and no joint answers a direct
        # rotation (measured: 37 phantom failures). Advisory only — findings are logged
        # and reported, never a refusal; every probe restores what it touched.
        try:
            from maya_tools.rigging.armature_import import orientation_check
            findings, summary = orientation_check.run(
                log=log, blueprint=source.blueprint)
            for line in summary:
                log(line)
            report.notes.extend(str(f) for f in findings)
        except Exception as exc:
            log('posed verification could not run: %s' % exc)

    if load:
        _load_blueprint(path, rig_name or source.name, log)

    for line in report.as_lines():
        log(line)
    return report


def _usd_import(source, log):
    """Import the USD and return the nodes it created (long names)."""
    if not cmds.pluginInfo('mayaUsdPlugin', q=True, loaded=True):
        cmds.loadPlugin('mayaUsdPlugin', quiet=True)

    before = set(cmds.ls(long=True))
    log('importing %s' % os.path.basename(source.path))
    cmds.file(source.path, i=True, type='USD Import', ignoreVersion=True,
              ra=True, mergeNamespacesOnClash=False, options=';', pr=True)
    created = sorted(set(cmds.ls(long=True)) - before)
    log('  %d nodes created' % len(created))
    return created


def _cleanup(uuids):
    """Delete whatever the import made. Used when a refusal fires mid-pipeline.

    Tracked by UUID rather than by DAG path: the pipeline reparents and renames nodes
    before some refusals fire, which invalidates every long name captured at import
    time. Cleaning up by stale paths silently left the whole character in the scene.
    """
    names = []
    for uuid in uuids:
        found = cmds.ls(uuid) or []
        names.extend(found)
    for node in sorted(set(names), key=lambda n: -n.count('|')):
        if cmds.objExists(node):
            try:
                cmds.delete(node)
            except (RuntimeError, ValueError):
                pass


def _reparent_out_of_wrapper(source, created, report, log):
    """Move the skeleton and mesh to world, folding the axis conversion into the root.

    Maya's USD import puts everything under a single `<Prim>_<Prim>` transform that
    carries the stage's axis conversion: rotate [-90,0,0] for a Z-up stage, identity for
    Y-up. Reparenting to world with `cmds.parent` preserves each child's WORLD transform,
    so the -90 lands on the root joint's own local values.

    That is not a workaround, it is the shape FS already ships: the root of
    Simple_Biped.blueprint.yaml is rotate [-90,0,0] with world_rotate [-90,0,0].
    """
    tops = [n for n in created if n.count('|') == 1 and cmds.objExists(n)]
    wrappers = [n for n in tops if cmds.nodeType(n) == 'transform']
    if not wrappers:
        log('  no wrapper transform (already at world)')
        return

    for wrapper in wrappers:
        children = cmds.listRelatives(wrapper, children=True, fullPath=True) or []
        moved = []
        for child in children:
            if cmds.nodeType(child) in ('joint', 'transform'):
                moved.extend(cmds.parent(child, world=True) or [])
        rot = cmds.getAttr('%s.rotate' % wrapper)[0]
        log('  unwrapped %s (rotate %s) -> %d node(s) to world'
            % (wrapper.split('|')[-1], [round(v, 3) for v in rot], len(moved)))
        if cmds.objExists(wrapper) and not (
                cmds.listRelatives(wrapper, children=True) or []):
            cmds.delete(wrapper)


def _derive_prefix(source, log):
    """Work out what Maya prefixed the imported nodes with, by measurement.

    Do NOT assume the default prim name. Maya's USD import prefixes with the FILE STEM,
    and on every sample we ship the stem and the default prim happen to be identical
    (Samurai.usdc -> prim Samurai), which hides the difference completely. Rename the
    file to Samurai_v2.usdc and a default-prim assumption silently matches nothing, at
    which point every blueprint joint reads as missing and the file looks damaged.

    So: score each candidate prefix by how many blueprint joints it actually resolves in
    the scene, and take the winner. Measurement beats assumption, and it costs one pass.
    """
    shorts = set()
    for node in cmds.ls(type='joint', long=False) or []:
        shorts.add(node)

    stem = os.path.splitext(os.path.basename(source.path))[0]
    names = [j.get('name') for j in source.joints if j.get('name')]

    candidates = ['', '%s_' % stem]
    if source.default_prim:
        candidates.append('%s_' % source.default_prim)

    best, best_score = '', -1
    for prefix in candidates:
        score = sum(1 for n in names if (prefix + n) in shorts)
        if score > best_score:
            best, best_score = prefix, score

    log('  prefix "%s" resolves %d/%d blueprint joints'
        % (best, best_score, len(names)))
    return best


def _strip_prefix(source, created, log):
    """Rename `<prefix>name` back to `name`.

    Maya's USD import prefixes every node and uses no namespace, so `root` arrives as
    `Samurai_root`. The blueprint-to-scene join is by name, and the prefix breaks it
    silently.

    Renames deepest-first so parent renames never invalidate a child's path. Aborts on a
    collision instead of letting Maya auto-suffix: importing a second character into a
    populated scene is a real workflow and it must fail loudly rather than half-succeed.
    """
    prefix = _derive_prefix(source, log)
    if not prefix:
        log('  no prefix to strip')
        return

    live = [n for n in cmds.ls(long=True) if cmds.objExists(n)]
    ours = set()
    for n in live:
        short = n.split('|')[-1]
        if short.startswith(prefix):
            ours.add(short)

    collisions = []
    for short in sorted(ours):
        target = short[len(prefix):]
        if not target:
            continue
        existing = cmds.ls(target, long=True) or []
        if existing:
            collisions.append(target)
    if collisions:
        raise ArmatureImportError(
            'These names already exist in the scene, so the import would silently '
            'rename its own joints and break the blueprint join: %s. Import into an '
            'empty scene, or rename the existing nodes first.'
            % ', '.join(sorted(collisions)[:8]))

    targets = [n for n in live if n.split('|')[-1].startswith(prefix)]
    targets.sort(key=lambda n: -n.count('|'))
    renamed = 0
    for node in targets:
        if not cmds.objExists(node):
            continue
        short = node.split('|')[-1]
        new = short[len(prefix):]
        if not new:
            continue
        cmds.rename(node, new)
        renamed += 1
    log('  stripped "%s" from %d nodes' % (prefix, renamed))


def _drop_skel_joint(report, log):
    """Remove the UsdSkel Skeleton prim, which imports as a joint above the real root."""
    if not cmds.objExists(SKEL_JOINT):
        return
    if cmds.nodeType(SKEL_JOINT) != 'joint':
        return
    kids = cmds.listRelatives(SKEL_JOINT, children=True, fullPath=True) or []
    for kid in kids:
        cmds.parent(kid, world=True)
    cmds.delete(SKEL_JOINT)
    log('  removed the UsdSkel "%s" scaffold joint' % SKEL_JOINT)


def _join_scene_to_blueprint(source, report, log):
    """Match blueprint joints to scene joints and police the difference.

    THIS REPLACES BRIEF TRAP 6. The brief says blueprint joint count must equal skeleton
    joint count and a mismatch means a damaged file. Measured false against the USD: the
    skeleton legitimately carries Unreal's IK bones on top of the authored rig (87 vs 79
    on Samurai), so the literal check refuses every file we ship.

    The real rule has two halves:
      * every blueprint joint must exist in the scene — a missing one is genuine damage
      * scene joints not in the blueprint must be declared engine joints — anything else
        means the file is not what we think it is

    Returns (engine_joint_names, root_joint_name).
    """
    scene = set(cmds.ls(type='joint', long=False) or [])
    wanted = [j.get('name') for j in source.joints if j.get('name')]

    missing = [n for n in wanted if n not in scene]
    if missing:
        raise ArmatureImportError(
            'The blueprint names %d joint(s) the USD skeleton does not contain, so the '
            'file is damaged: %s%s'
            % (len(missing), ', '.join(missing[:8]),
               ' ...' if len(missing) > 8 else ''))

    allowed = blueprint_source.engine_joint_names(source.blueprint)
    extras = sorted(scene - set(wanted))
    unexpected = [n for n in extras if n not in allowed]
    if unexpected:
        raise ArmatureImportError(
            'The USD skeleton carries %d joint(s) that are neither in the blueprint nor '
            'declared engine joints: %s%s. This importer will not guess what they are.'
            % (len(unexpected), ', '.join(unexpected[:8]),
               ' ...' if len(unexpected) > 8 else ''))

    report.scene_joint_count = len(scene)

    roots = [j.get('name') for j in source.joints if not j.get('parent')]
    root = roots[0] if roots else ''
    log('  joined %d blueprint joints, %d engine extras' % (len(wanted), len(extras)))
    return extras, root


def _axis_converter(source):
    """Convert a blueprint world coordinate into Maya's frame for a Z-up stage.

    Only blueprint-sourced coordinates need this; everything else comes from the scene
    and is already correct. Z-up (x, y, z) maps to Maya Y-up (x, z, -y), matching the
    rotate[-90,0,0] the importer folds into the root.
    """
    if str(source.up_axis).upper() != 'Z':
        return None

    def convert(v):
        return [v[0], v[2], -v[1]]
    return convert


def _snapshot_skeleton(root_joint):
    """Capture the scene skeleton under `root_joint` as JointSpec records.

    Mirrors fs_app._snapshot_skeleton_from_scene field for field, but scoped to one root
    rather than the whole scene, because at import time there is no registry to scope
    against and a populated scene would otherwise be swept in wholesale.

    world_rotate is captured on the root for the same reason fs_app captures it: the
    root's locals are meaningless without the frame they were authored in.
    """
    from maya_tools.rigging.fabricator.blueprint.schema import JointSpec

    descendants = cmds.listRelatives(root_joint, allDescendents=True, type='joint',
                                     fullPath=False) or []
    ordered = [root_joint] + list(reversed(descendants))

    out = []
    for j in ordered:
        par_list = cmds.listRelatives(j, parent=True, type='joint') or []
        par = par_list[0] if par_list else None

        ro_idx = cmds.getAttr('%s.rotateOrder' % j) or 0
        ro = ROTATE_ORDERS[ro_idx] if 0 <= ro_idx < len(ROTATE_ORDERS) else 'xyz'
        radius = cmds.getAttr('%s.radius' % j)
        if radius is None:
            radius = 1.0

        world_r = None
        if par is None:
            world_r = list(cmds.xform(j, q=True, ws=True, rotation=True))

        out.append(JointSpec(
            name=j,
            parent=par,
            translate=list(cmds.getAttr('%s.translate' % j)[0]),
            rotate=list(cmds.getAttr('%s.rotate' % j)[0]),
            joint_orient=list(cmds.getAttr('%s.jointOrient' % j)[0]),
            rotate_order=ro,
            radius=float(radius),
            world_translate=list(cmds.xform(j, q=True, ws=True, t=True)),
            world_rotate=world_r,
        ))
    return out


def _write_blueprint(source, classification, blueprint_path, report, log):
    """Capture the scene skeleton, attach components, stamp, and write the YAML."""
    from maya_tools.rigging.fabricator.blueprint import io as blueprint_io
    from maya_tools.rigging.fabricator.blueprint.schema import (
        Blueprint, ComponentSpec, CURRENT_SCHEMA_VERSION)
    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION

    specs = _snapshot_skeleton(report.root_joint)

    # Overlay the authored aimer targets. fs_app.load() calls
    # restore_aimers_from_blueprint(), so writing aim_target here is what actually
    # applies the aimers — the importer never drives joint_orient directly.
    # 'Local' is written through UNCHANGED. It is correct and must never be normalized
    # to Parent: Parent plus the 180 flip rolls Y off the authored frame (BRIEF trap 1).
    aims = {j.get('name'): (j.get('aim_target') or '') for j in source.joints}
    for spec in specs:
        spec.aim_target = aims.get(spec.name, '')

    components = [ComponentSpec(
        type=c['type'],
        joints=list(c['joints']),
        id=c.get('id'),
        parent_plug=c.get('parent_plug', ''),
        side=c.get('side', 'md'),
        region=c.get('region', ''),
        options=dict(c.get('options', {})),
    ) for c in classification.components]

    blueprint = Blueprint(
        name=source.name,
        schema_version=CURRENT_SCHEMA_VERSION,
        description='Imported from %s' % os.path.basename(source.path),
        skeleton_joints=specs,
        components=components,
        wiring=[],
        orient_convention=source.blueprint.get('orient_convention') or 'unreal',
        # MANDATORY. Without this stamp the file reads as pre-1.1.0 data and every free
        # IKArm loads as the paid RibbonIKArm — the 2026-07-19 ribbon-arms bug, reachable
        # through this new door. fs_app.load() holds the registry at the FILE's stamp for
        # its type-resolution window, so an unstamped file genuinely resolves as legacy.
        fabricator_version=FABRICATOR_VERSION,
    )

    if not blueprint_path:
        blueprint_path = os.path.join(
            os.path.dirname(source.path), '%s.blueprint.yaml' % source.name)

    blueprint_io.write_yaml(blueprint, blueprint_path)
    log('  wrote %s (%d joints, %d components, stamped %s)'
        % (blueprint_path, len(specs), len(components), FABRICATOR_VERSION))
    return blueprint_path


def _load_blueprint(path, character_name, log):
    """Hand off to the shipped loader, which adopts the joints already in the scene.

    Then name the rig. The loader sets the registry's blueprint NAME from the filename
    stem, but the FS window's "Rig Name" field reads a different attribute, `rig_label`,
    which is lazily derived by `rig_binding.derive_rig_label`: namespace, then scene stem,
    then the root joint's basename. An imported scene has no namespace and is unsaved, so
    that chain falls all the way through to the literal string "root".

    That left the rig effectively unnamed and blocked Build until the user typed one
    (Adrian, 2026-07-31). The importer holds the authoritative answer — the USD's default
    prim, i.e. the character name — so it sets it rather than letting a generic joint name
    win a guess.
    """
    from maya_tools.rigging.fabricator import fs_app, nodes
    log('loading blueprint into the scene')
    fs_app.load(path)

    if character_name and nodes.get_registry():
        nodes.set_rig_label(character_name)
        log('  rig named "%s"' % character_name)

    # An open FS window keeps displaying its pre-load read (name field included) until
    # it is reopened — the window's OWN load action ends with _force_reopen() for
    # exactly this reason, and calling fs_app.load() directly skips that step. That is
    # why the rig name "didn't auto-fill" even though the registry held it (Adrian,
    # 2026-07-31). Poke only a window that already exists: sys.modules, not an import,
    # so headless runs never pull Qt in.
    import sys as _sys
    fs_window = _sys.modules.get('maya_tools.rigging.fabricator.ui.fs_window')
    if fs_window is not None and getattr(fs_window, '_win', None) is not None:
        try:
            fs_window.FSWindow._force_reopen()
            log('  refreshed the open Fabricator window')
        except Exception as exc:                       # a stale window must never
            log('  window refresh failed: %s' % exc)   # fail the import itself
    log('  loaded')
