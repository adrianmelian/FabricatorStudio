"""Read the Armature rig blueprint out of a USD export.

The `.rig.json` sidecar is DEPRECATED (Adrian, 2026-07-31). The USD is the single
carrier: Armature embeds the blueprint JSON in the root layer's customLayerData under
'fabricator_blueprint', with a read-back verify at write time. Verified present on every
current export (Samurai, Pupper, Squid, Ninja).

This module has no maya.cmds dependency on purpose — it is the half of the importer that
can be tested without a Maya scene.

REFUSALS ARE LOUD AND NAMED. A file we cannot read is never best-effort imported; the
user gets a sentence saying which file and what is wrong with it. Silent partial imports
are worse than nothing, because the rig looks present and is not.
"""
__author__ = "Adrian Melian"

import json
import os

# The blueprint format tag Armature writes. Anything else is not our file.
BLUEPRINT_FORMAT = 'fabricator-armature-skeleton'

# Highest blueprint schema version this importer understands.
MAX_BLUEPRINT_VERSION = 1

# customLayerData key Armature writes the blueprint under (sidecar.py A51).
EMBED_KEY = 'fabricator_blueprint'

# Containers we accept. .usdz is deliberately excluded: Pupper.usdz carries empty
# customLayerData, metersPerUnit=1.0 and a default prim of 'root' rather than the export
# name — it is a repackage, not an Armature export, and importing it would produce a
# rigless character with no obvious cause.
USD_EXTENSIONS = ('.usd', '.usdc', '.usda')


class ArmatureImportError(RuntimeError):
    """Raised for every refusal. The message is shown to the user verbatim."""


# os.add_dll_directory returns a handle that REMOVES the directory again when it is
# closed or garbage collected. Dropping it on the floor makes the fix evaporate before
# the import runs, so the handles are parked here for the life of the process.
_DLL_HANDLES = []


def _ensure_pxr():
    """Import pxr.Usd, adding MayaUSD's DLL directories when running under bare mayapy.

    Inside Maya with mayaUsdPlugin loaded, `from pxr import Usd` just works. Under a
    headless mayapy the extension modules fail with 'DLL load failed while importing
    _tf' because Windows (py3.8+) ignores PATH for extension DLL search.

    Two traps, both hit while building this:

    * **Match the MayaUSD build to the running Maya.** Several are installed side by
      side (2025/0.32.0 and 2027/0.35.0 on this box). Taking the highest version loads
      binaries built for a different Python ABI and _tf fails to load.
    * **Purge a poisoned `pxr` between attempts.** A failed import leaves a partial
      `pxr` package in sys.modules pointing at the wrong root, so every later attempt
      fails the same way even once the correct root is on the path.
    """
    try:
        from pxr import Usd  # noqa: F401
        return
    except ImportError:
        pass

    import glob
    import re
    import sys

    match = re.search(r'Maya(\d{4})', sys.executable or '')
    version = match.group(1) if match else '20*'

    roots = sorted(glob.glob(
        r'C:\Program Files\Autodesk\MayaUSD\Maya%s\*\mayausd\USD' % version),
        reverse=True)
    for root in roots:
        py_dir = os.path.join(root, 'lib', 'python')
        if not os.path.isdir(py_dir):
            continue
        for sub in ('lib', 'bin'):
            d = os.path.join(root, sub)
            if os.path.isdir(d):
                try:
                    _DLL_HANDLES.append(os.add_dll_directory(d))
                except (AttributeError, OSError):
                    pass
        if py_dir not in sys.path:
            sys.path.insert(0, py_dir)
        for name in [k for k in sys.modules if k == 'pxr' or k.startswith('pxr.')]:
            del sys.modules[name]
        try:
            from pxr import Usd  # noqa: F401
            return
        except ImportError:
            continue

    raise ArmatureImportError(
        'Could not import the USD python bindings (pxr). Inside Maya, load the '
        'mayaUsdPlugin first. Headless, check that a MayaUSD build matching Maya %s is '
        r'installed under C:\Program Files\Autodesk\MayaUSD.' % version)


class ArmatureSource(object):
    """A validated Armature USD export, ready to import.

    Attributes:
        path:            absolute path to the USD.
        blueprint:       the parsed blueprint dict (joints, modules, units, ...).
        joints:          blueprint['joints'], list of dicts.
        modules:         blueprint['modules'], list of dicts.
        up_axis:         'Y' or 'Z' as authored on the stage.
        meters_per_unit: stage metersPerUnit (0.01 on every Armature export = cm).
        default_prim:    the stage's default prim name. Maya's USD import prefixes every
                         node with '<default_prim>_', so this drives the name join.
        blueprint_scale: multiplier taking blueprint coordinates into stage/Maya units.
    """

    def __init__(self, path, blueprint, up_axis, meters_per_unit, default_prim):
        self.path = path
        self.blueprint = blueprint
        self.joints = blueprint.get('joints') or []
        self.modules = blueprint.get('modules') or []
        self.up_axis = up_axis
        self.meters_per_unit = meters_per_unit
        self.default_prim = default_prim
        self.blueprint_scale = _blueprint_scale(blueprint, meters_per_unit)

    @property
    def name(self):
        return self.default_prim or os.path.splitext(os.path.basename(self.path))[0]

    def joint_by_name(self):
        return {j.get('name'): j for j in self.joints if j.get('name')}

    def module_by_name(self):
        return {m.get('name'): m for m in self.modules if m.get('name')}

    def summary(self):
        return ('%s: %d joints, %d modules, %s-up, blueprint scale x%g'
                % (self.name, len(self.joints), len(self.modules),
                   self.up_axis, self.blueprint_scale))


def _blueprint_scale(blueprint, meters_per_unit):
    """Multiplier taking blueprint coordinates into stage units.

    Armature stamps metersPerUnit=0.01 (geometry in centimetres) while the blueprint
    says units:'m' and its numbers ARE metres — head.translate on Samurai is
    [0.028782, 0, 0]. So blueprint coordinates need x100 against the geometry sitting in
    the same file. Measured on all four current exports; see FINDINGS F4.

    Honours an explicit 'blueprint_to_stage_scale' if a future Armature declares one
    (requested as R3 in ARMATURE-EXPORT-SPEC.md), so the deduction below dies quietly
    the day the export stops needing it.
    """
    declared = blueprint.get('blueprint_to_stage_scale')
    if isinstance(declared, (int, float)) and declared > 0:
        return float(declared)

    units = str(blueprint.get('units') or '').lower()
    mpu = float(meters_per_unit or 1.0)
    if units in ('m', 'meter', 'meters') and mpu > 0:
        return 1.0 / mpu          # metres -> stage units (0.01 -> x100)
    if units in ('cm', 'centimeter', 'centimeters'):
        return 0.01 / mpu if mpu else 1.0
    return 1.0


def engine_joint_names(blueprint):
    """Joints the exporter adds to the USD that are NOT authored rig joints.

    The USD skeleton legitimately carries more joints than the blueprint: on Samurai, 87
    vs 79. The extras are the UsdSkel Skeleton prim plus Unreal's IK bones. They belong
    in the file and must never be treated as damage — but they also must never be
    confused with a genuinely unexpected joint, which IS damage.

    Prefers an 'engine_joints' array if the export declares one (requested as R5 in
    ARMATURE-EXPORT-SPEC.md). Falls back to the measured UE set, which is the part that
    will rot if Armature ever adds a bone, hence the request.
    """
    declared = blueprint.get('engine_joints')
    if isinstance(declared, list) and declared:
        return set(str(n) for n in declared)

    return {
        'ik_foot_root', 'ik_foot_l', 'ik_foot_r',
        'ik_hand_root', 'ik_hand_gun', 'ik_hand_l', 'ik_hand_r',
    }


def read(path):
    """Open a USD export and return a validated ArmatureSource.

    Raises ArmatureImportError with a user-facing sentence for every refusal.
    """
    path = os.path.abspath(str(path))

    if not os.path.isfile(path):
        raise ArmatureImportError('No such file: %s' % path)

    ext = os.path.splitext(path)[1].lower()
    if ext == '.usdz':
        raise ArmatureImportError(
            '.usdz files do not carry an Armature rig blueprint and are not supported. '
            'Export a .usd or .usdc from Armature instead. (%s)' % path)
    if ext not in USD_EXTENSIONS:
        raise ArmatureImportError(
            'Expected a USD export (%s), got "%s". (%s)'
            % (', '.join(USD_EXTENSIONS), ext or 'no extension', path))

    _ensure_pxr()
    from pxr import Usd

    stage = Usd.Stage.Open(path)
    if stage is None:
        raise ArmatureImportError('USD refused to open this file: %s' % path)

    layer_data = dict(stage.GetRootLayer().customLayerData or {})
    raw = layer_data.get(EMBED_KEY)
    if not raw:
        raise ArmatureImportError(
            'This USD carries no Armature rig blueprint. It was either exported before '
            'the blueprint was embedded, or it is not an Armature export at all. (%s)'
            % path)

    if isinstance(raw, str):
        try:
            blueprint = json.loads(raw)
        except ValueError as exc:
            raise ArmatureImportError(
                'The embedded Armature blueprint is not valid JSON (%s): %s'
                % (path, exc))
    elif isinstance(raw, dict):
        blueprint = raw
    else:
        raise ArmatureImportError(
            'The embedded Armature blueprint has an unexpected type (%s). (%s)'
            % (type(raw).__name__, path))

    fmt = blueprint.get('format')
    if fmt != BLUEPRINT_FORMAT:
        raise ArmatureImportError(
            'Expected an Armature blueprint tagged "%s", found "%s". (%s)'
            % (BLUEPRINT_FORMAT, fmt, path))

    version = blueprint.get('version')
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise ArmatureImportError(
            'The embedded blueprint has no readable version. (%s)' % path)
    if version > MAX_BLUEPRINT_VERSION:
        raise ArmatureImportError(
            'This file was written by a newer Armature (blueprint version %d; this '
            'importer understands up to %d). Update FabricatorStudio. (%s)'
            % (version, MAX_BLUEPRINT_VERSION, path))

    joints = blueprint.get('joints')
    if not joints:
        raise ArmatureImportError(
            'The embedded blueprint carries no joints. (%s)' % path)

    default_prim = stage.GetDefaultPrim()
    default_prim = default_prim.GetName() if default_prim else ''

    up_axis = stage.GetMetadata('upAxis') or 'Y'
    try:
        mpu = float(stage.GetMetadata('metersPerUnit') or 1.0)
    except (TypeError, ValueError):
        mpu = 1.0

    return ArmatureSource(path, blueprint, str(up_axis), mpu, str(default_prim))


def check_export_guarantees(source):
    """Validate the guarantees Armature's own exportChecks promise (BRIEF trap 7).

    Returns a list of human-readable problems; empty means clean. These are export-time
    invariants, so a violation means the file is damaged and the caller refuses rather
    than best-effort importing.

    NOTE what is deliberately NOT checked here: blueprint joint count against the USD
    skeleton joint count. BRIEF trap 6 asserts they are equal and that a mismatch means
    damage. Measured false — the USD legitimately carries the UsdSkel root plus Unreal's
    IK bones (87 vs 79 on Samurai), so that check refuses every file we ship. The real
    join and extras rule lives in armature_import_app._join_scene_to_blueprint.
    """
    problems = []
    center_regions = {'root', 'pelvis', 'spine', 'neck', 'head'}

    # Joint names must be unique. The whole import joins the blueprint to the scene BY
    # NAME, so a duplicate is not a cosmetic problem: two blueprint joints claim one
    # scene joint and the other is silently orphaned.
    #
    # This is real and it ships today. Pupper's blueprint carries `thigh_twist_01_l`
    # twice (the front limb was made by duplicating the rear one), so 50 entries hold
    # only 46 unique names. USD cannot have duplicate sibling paths, so the exporter
    # disambiguated its copies to `thigh_twist_01_l_` — meaning the two artifacts in the
    # same file disagree about what the joints are called.
    #
    # Refused rather than guessed. Picking which duplicate owns which scene joint is
    # exactly the best-effort behaviour that produces a rig that looks right and
    # deforms wrong. The fix belongs in Armature: see ARMATURE-EXPORT-SPEC R9.
    seen = {}
    duplicates = []
    for j in source.joints:
        name = j.get('name') or ''
        if not name:
            continue
        seen[name] = seen.get(name, 0) + 1
        if seen[name] == 2:
            duplicates.append(name)
    if duplicates:
        problems.append(
            'the blueprint uses %d joint name(s) more than once (%s%s). Joint names must '
            'be unique: the import matches the blueprint to the skeleton by name, and '
            'the USD has already renamed its own copies (e.g. "%s_"), so the two halves '
            'of this file disagree. Rename the duplicated joints in Armature and '
            're-export.'
            % (len(duplicates), ', '.join(duplicates[:6]),
               ' ...' if len(duplicates) > 6 else '', duplicates[0]))

    for j in source.joints:
        name = j.get('name') or '<unnamed>'
        region = j.get('region') or ''
        side = j.get('side') or ''

        if region in center_regions:
            if side != 'C':
                problems.append(
                    'centre-column joint "%s" exports with side "%s", expected "C"'
                    % (name, side))
            if name.lower().endswith(('_l', '_r')):
                problems.append(
                    'centre-column joint "%s" exports with a side token in its name'
                    % name)

        if region == 'root':
            rot = j.get('rotate') or [0.0, 0.0, 0.0]
            try:
                worst = max(abs(float(v)) for v in rot)
            except (TypeError, ValueError):
                worst = 0.0
            if worst > 0.01:
                problems.append(
                    'root joint "%s" exports a non-identity rotation %s'
                    % (name, list(rot)))

    roots = [j for j in source.joints if not j.get('parent')]
    if len(roots) != 1:
        problems.append(
            'expected exactly one parentless root joint, found %d (%s)'
            % (len(roots), ', '.join(str(j.get('name')) for j in roots) or 'none'))

    return problems
