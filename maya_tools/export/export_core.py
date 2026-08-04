"""Exporter foundation: project config loading, path resolution, network-node CRUD,
FBX preset application, and validation.

UI-free. Safe to call from headless / batch contexts.
"""
__author__ = "Adrian Melian"

import json
import logging
import os
import re
from pathlib import Path

import maya.cmds as cmds
import maya.mel as mel

from maya_tools.framework import project_config_paths


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# User-side location (<maya>/fabricator_project_configs). ensure_user_configs()
# creates + seeds/migrates on first touch; cheap no-op afterwards. Seeding can
# fail with OSError (AV file lock, disk full, unreadable source) — that is
# designed to be retryable on the next import, so it must never turn into an
# ImportError here (this module is imported by every export/library tool;
# same invariant as maya_startup). Fall back to the plain path:
# iter_project_dirs() returns [] for a missing dir and load_project_config()
# raises its normal RuntimeError. Logging only in the fallback — no cmds
# calls at import time.
try:
    _PROJECT_CONFIGS_DIR = project_config_paths.ensure_user_configs()
except OSError:
    _PROJECT_CONFIGS_DIR = project_config_paths.configs_dir()
    logging.getLogger(__name__).warning(
        '[FS] Could not seed user project configs at %s; '
        'continuing without seeding (will retry next session).',
        _PROJECT_CONFIGS_DIR, exc_info=True,
    )

_REGISTRY_MARKER = 'fab_exporter_registry'
_ENTRY_MARKER    = 'fab_export_entry'
_REGISTRY_NAME   = 'FAB_ExporterRegistry'
_ENTRY_PREFIX    = 'FAB_Export_'
_VERSION         = '1.0'

TYPE_STATIC   = 'static_mesh'
TYPE_SKELETAL = 'skeletal_mesh'
ENTRY_TYPES   = (TYPE_STATIC, TYPE_SKELETAL)


# ─────────────────────────────────────────────────────────────────────────────
# Project config loading
# ─────────────────────────────────────────────────────────────────────────────

def iter_project_dirs() -> list[Path]:
    """Return all project subdirectories under the LIVE configs root
    (env var -> in-tool pointer -> local default). Live, not the frozen
    import-time global, so the toolbar chooser and the matcher can never
    disagree about which configs root is in effect after a mid-session
    pointer change."""
    root = project_config_paths.configs_dir()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith('_'))


def _normalise(path: str) -> str:
    return Path(path).as_posix() if path else ''


def load_project_config(scene_path: str) -> dict:
    """Find and return the RESOLVED project config whose patterns match
    this scene.

    Scans every BOUND project via project_config_resolve (v2: project
    truth + this machine's root bindings). Returns the first resolved
    config whose Source Art Root contains the scene path AND whose
    path_patterns match the relative path. Unbound/corrupt projects are
    skipped inside resolve_all. Raises RuntimeError if nothing matches.
    """
    if not scene_path:
        raise RuntimeError("Scene path is empty.")
    scene_path_n = _normalise(scene_path)

    from maya_tools.framework import project_config_resolve
    for config in project_config_resolve.resolve_all():
        source_root = _normalise(config.get('source_art_root', ''))
        if not source_root or not scene_path_n.lower().startswith(source_root.lower()):
            continue

        rel = scene_path_n[len(source_root):].lstrip('/')
        for pattern in config.get('path_patterns', []):
            if re.match(pattern.get('match', ''), rel):
                config['_source_file'] = str(
                    project_config_paths.configs_dir()
                    / config.get('slug', '') / 'export_mapping.json')
                return config

    raise RuntimeError(
        f"No project export config matches scene path: {scene_path}\n"
        f"Searched user project configs: {project_config_paths.configs_dir()}\n"
        f"(configs live in <maya folder>/fabricator_project_configs/<project>/; "
        f"MAYA_APP_DIR relocates the maya folder; a project also needs its "
        f"Source Art Root bound on this machine - see Mindmeld)"
    )


def load_project_config_or_none(scene_path: str) -> dict | None:
    """load_project_config, but a scene outside every project returns None
    instead of raising. A project is an autofill convenience (auto output
    paths, prefixes, presets), never a gate on exporting: with no config the
    export falls back to the user's saved Output Dir and the built-in
    DEFAULT_FBX_PRESETS."""
    try:
        return load_project_config(scene_path)
    except RuntimeError:
        return None


def _user_app_library_root(library_name: str) -> Path:
    """Maya prefs fallback when no project config resolves.

    `library_name` is 'pose_library' or 'anim_library'. Returns
    `<userAppDir>/<library_name>` — typically
    `~/Documents/maya/<library_name>` on Windows.

    Used when the scene is unsaved OR is outside any known project root.
    Per-rig subfolders work identically to the configured root, so test
    saves land in a discoverable spot without blocking the workflow.

    Raises RuntimeError if Maya's userAppDir is unresolvable — same
    posture as load_project_config when no config matches.
    """
    user_app_dir = cmds.internalVar(userAppDir=True)
    if not user_app_dir:
        raise RuntimeError(
            'Cannot resolve library root: '
            'cmds.internalVar(userAppDir=True) returned empty.'
        )
    return Path(user_app_dir) / library_name


def _library_root_from_config(config: dict, library_name: str) -> Path:
    """Resolve a library root from a RESOLVED project config dict.

    Prefers the resolved `<library_name>_root` key (override-first, else
    Source Art Root + the project subpath - project_config_resolve did
    that merge); otherwise falls back to `source_art_root/<library_name>`.
    `library_name` is 'pose_library' or 'anim_library'.
    """
    raw = config.get(f'{library_name}_root', '').strip()
    if raw:
        return Path(raw)
    return Path(config.get('source_art_root', '')) / library_name


def _single_project_library_root(library_name: str) -> Path:
    """When the scene matches no project config, fall back to the sole
    project's library root — but only when EXACTLY one project config
    exists, so the choice is unambiguous.

    This keeps the Pose / Anim libraries visible while working in a
    throwaway out-of-project scene (e.g. a Desktop test file) instead of
    silently pointing at an empty userAppDir folder. Returns None when
    there are zero or multiple configs (ambiguous → caller uses the
    userAppDir fallback).
    """
    from maya_tools.framework import project_config_resolve
    configs = project_config_resolve.resolve_all()
    if len(configs) == 1:
        return _library_root_from_config(configs[0], library_name)
    if not configs and len(iter_project_dirs()) == 1:
        # Exactly one project exists on DISK but it is unbound on this
        # machine: warn loudly instead of silently parking poses/anims in
        # userAppDir (they would "vanish" from the library once the
        # project gets bound and the root moves).
        logging.getLogger(__name__).warning(
            "[FS] The only project config on disk is not bound on this "
            "machine; %s falls back to the Maya user folder until you "
            "bind Source Art Root + Content Root in Mindmeld.",
            library_name)
    return None


def get_pose_library_root(scene_path: str = '') -> Path:
    """Return the `pose_library_root` from the matching project config,
    or the userAppDir fallback if no config matches.

    scene_path='' reads cmds.file(q=True, sn=True) for the current scene.
    """
    if not scene_path:
        scene_path = cmds.file(q=True, sn=True) or ''
    # load_project_config raises RuntimeError when no config matches; all other
    # exceptions (JSON parse errors, etc.) are already swallowed inside it.
    try:
        config = load_project_config(scene_path)
    except RuntimeError:
        # Out-of-project scene (Desktop test file, etc.): keep the library
        # visible by using the sole project's root when there's exactly one.
        single = _single_project_library_root('pose_library')
        return single if single is not None else _user_app_library_root('pose_library')
    return _library_root_from_config(config, 'pose_library')


def get_anim_library_root(scene_path: str = '') -> Path:
    """Return the `anim_library_root` from the matching project config,
    or the userAppDir fallback if no config matches.

    scene_path='' reads cmds.file(q=True, sn=True) for the current scene.
    """
    if not scene_path:
        scene_path = cmds.file(q=True, sn=True) or ''
    # load_project_config raises RuntimeError when no config matches; all other
    # exceptions (JSON parse errors, etc.) are already swallowed inside it.
    try:
        config = load_project_config(scene_path)
    except RuntimeError:
        # Out-of-project scene (Desktop test file, etc.): keep the library
        # visible by using the sole project's root when there's exactly one.
        single = _single_project_library_root('anim_library')
        return single if single is not None else _user_app_library_root('anim_library')
    return _library_root_from_config(config, 'anim_library')


def resolve_destination(scene_path: str, config: dict) -> Path:
    """Return the absolute output directory for this scene under the
    matching pattern: the scene matches under the Source Art Root, the
    output lands under the Content Root (v2 split; the same root for a
    migrated v1 project)."""
    scene_path_n = _normalise(scene_path)
    source_root = _normalise(config.get('source_art_root', ''))
    if not source_root or not scene_path_n.lower().startswith(source_root.lower()):
        raise RuntimeError(
            f"Scene path is outside the project's Source Art Root: {scene_path}")

    rel = scene_path_n[len(source_root):].lstrip('/')
    for pattern in config.get('path_patterns', []):
        match = re.match(pattern.get('match', ''), rel)
        if match:
            dest_template = pattern.get('dest', '')
            dest_relative = dest_template.format(**match.groupdict())
            return Path(config.get('content_root', '')) / dest_relative

    raise RuntimeError(f"No path pattern matched relative scene path: {rel}")


def get_anim_prefix(config: dict) -> str:
    """Default anim prefix used by the GUI to pre-fill new clips."""
    return config.get('anim_prefix', '')


def resolve_anim_destination(scene_path: str, clip_data: dict, config: dict) -> Path:
    """Return the absolute output directory for an anim clip under the matching pattern.

    Picks anim_path_patterns[output_kind] from the config; substitutes named
    regex groups against the scene's relative path.

    Raises RuntimeError if:
      - clip_data is missing 'output_kind'
      - scene path is outside project_root
      - output_kind has no patterns configured
      - no pattern matches the scene's relative path
    """
    if 'output_kind' not in clip_data:
        raise RuntimeError("clip_data is missing required key 'output_kind'.")
    output_kind = clip_data['output_kind']
    scene_path_n = _normalise(scene_path)
    source_root = _normalise(config.get('source_art_root', ''))
    if not source_root or not scene_path_n.lower().startswith(source_root.lower()):
        raise RuntimeError(
            f"Scene path is outside the project's Source Art Root: {scene_path}")

    patterns = (config.get('anim_path_patterns') or {}).get(output_kind) or []
    if not patterns:
        raise RuntimeError(
            f"Anim path patterns not configured for output_kind={output_kind!r}. "
            f"Add a {output_kind!r} anim destination to the asset class in Mindmeld."
        )

    rel = scene_path_n[len(source_root):].lstrip('/')
    for pattern in patterns:
        match = re.match(pattern.get('match', ''), rel)
        if match:
            dest_template = pattern.get('dest', '')
            dest_relative = dest_template.format(**match.groupdict())
            return Path(config.get('content_root', '')) / dest_relative

    raise RuntimeError(
        f"No anim_path_patterns.{output_kind} entry matched relative scene path: {rel}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FBX preset application
# ─────────────────────────────────────────────────────────────────────────────

# FBX commands that take a positional argument instead of -v <value>.
# Most FBX MEL setters use `FBXExportFoo -v <val>` but a handful are positional only.
_POSITIONAL_FBX = {'FBXExportUpAxis', 'FBXExportQuaternion'}

# Built-in presets used when the scene matches no project config, or the
# matching config's preset for that type is empty. Same game-ready settings
# the packaged project templates ship with, except up-axis stays 'y' (Maya
# native): engines convert up-axis on import, so Y-up is the neutral choice
# when no project has declared a target engine.
_DEFAULT_PRESET_COMMON = {
    'FBXExportSmoothingGroups': True,
    'FBXExportTangents': True,
    'FBXExportSmoothMesh': True,
    'FBXExportTriangulate': False,
    'FBXExportInstances': False,
    'FBXExportReferencedAssetsContent': False,
    'FBXExportInputConnections': False,
    'FBXExportConstraints': False,
    'FBXExportCameras': False,
    'FBXExportLights': False,
    'FBXExportEmbeddedTextures': False,
    'FBXExportUseSceneName': False,
    'FBXExportInAscii': False,
    'FBXExportFileVersion': 'FBX202000',
    'FBXExportUpAxis': 'y',
}

DEFAULT_FBX_PRESETS = {
    TYPE_STATIC: {
        **_DEFAULT_PRESET_COMMON,
        'FBXExportSkins': False,
        'FBXExportSkeletonDefinitions': False,
        'FBXExportShapes': True,
    },
    TYPE_SKELETAL: {
        **_DEFAULT_PRESET_COMMON,
        'FBXExportSkins': True,
        'FBXExportSkeletonDefinitions': True,
        'FBXExportShapes': True,
        'FBXExportBakeComplexAnimation': False,
    },
    'animation': {
        **_DEFAULT_PRESET_COMMON,
        'FBXExportSkins': False,
        'FBXExportSkeletonDefinitions': True,
        'FBXExportShapes': False,
        'FBXExportSmoothingGroups': False,
        'FBXExportTangents': False,
        'FBXExportSmoothMesh': False,
        'FBXExportBakeComplexAnimation': True,
    },
}


def fbx_preset(config: dict | None, type_: str) -> dict:
    """The FBX preset for an entry/clip type: the project config's when it
    defines a non-empty one, else the built-in default. `type_` is
    TYPE_STATIC, TYPE_SKELETAL, or 'animation'."""
    preset = ((config or {}).get('fbx_presets') or {}).get(type_)
    return preset if preset else DEFAULT_FBX_PRESETS[type_]


# ─────────────────────────────────────────────────────────────────────────────
# Engine bone-space (up-axis) conversion
# ─────────────────────────────────────────────────────────────────────────────

ENGINE_UP_AXIS_KEY = 'engine_up_axis'
ENGINE_UP_AXES = ('y', 'z')


def engine_up_axis(config: dict | None, override: str = '') -> str:
    """Resolve the engine bone-space up axis for a skeletal/anim export.

    Precedence: explicit override (the UI's Export Options pick) > the
    project config's 'engine_up_axis' > 'y'.

    'y' = Maya-native, no conversion — correct for Y-up engines (Unity,
    Godot) and the pre-2026-07-14 behavior. 'z' targets Z-up engines
    (Unreal): the exporters fold a -90° world-X rotation into the root
    joint's frame (orient_root_for_z_up_engine) so the engine's FBX import
    conversion lands the root bone at identity — matching how the engine's
    own exporter writes FBX (verified against SKM_Manny_Simple: root global
    = RX(-90) in a Y-up file, joint locals Z-up native). Without it, the
    import leaves a +90 in the root bone and every engine-authored
    animation plays the character pitched 90° head-first.
    """
    axis = (override or (config or {}).get(ENGINE_UP_AXIS_KEY) or 'y')
    axis = str(axis).strip().lower()
    if axis not in ENGINE_UP_AXES:
        raise RuntimeError(
            f"Invalid engine up axis {axis!r} — expected one of "
            f"{ENGINE_UP_AXES}. Check '{ENGINE_UP_AXIS_KEY}' in "
            f"export_mapping.json or the Export Options override.")
    return axis


def orient_root_for_z_up_engine(root: str,
                                frame_range: tuple[int, int] | None = None,
                                rest_world_matrix: list | None = None) -> None:
    """Drive the root joint's frame TO the Z-up engine arrangement:
    rest world orientation = RX(-90) — the same arrangement the engine's
    own FBX exporter writes (the -90 group node Maya shows on an
    SKM_Manny_Simple import, folded into the root instead of a group so no
    extra node rides into the FBX).

    TARGET-BASED AND IDEMPOTENT (2026-07-14): the applied delta is
    computed from the root's REST orientation to the target, so a root
    already carrying the -90 arrangement (a manny-import-derived scene, or
    a second run) is a no-op, and an identity-authored root gets the full
    -90. The earlier delta-only version drove an already-converted root to
    -180 (verified live on the manny round-trip).

    The root's translation is untouched (root motion preserved), direct
    children get their locals re-expressed against the converted parent
    frame (world unchanged), and deeper joints ride along in unchanged
    parent frames. The visible character does not move; only the invisible
    root bone frame changes.

    Static skeleton: frame_range=None applies the single current pose; the
    rest orientation is the root's current orientation (the skeletal
    export runs at rest). Baked animation: pass (start, end) — the root
    and its direct children are re-keyed on every frame with the SAME
    constant delta, then euler-filtered. For animation the rest
    orientation cannot be sampled from a posed clip, so pass
    rest_world_matrix (the bind root world matrix, e.g. from the
    Fabricator registry's bind-pose snapshot); when omitted the rest is
    sampled from the current/static frame.

    Skins must be dormant or re-bound AFTER this call (the skeletal export
    runner resets all binds at the final pose downstream; the legacy
    in-scene path brackets skins around it).
    """
    import math
    import maya.api.OpenMaya as om2

    def rot3(world_flat: list) -> om2.MMatrix:
        m = om2.MMatrix(world_flat)
        out = om2.MMatrix()
        for r in range(3):
            v = om2.MVector(m.getElement(r, 0), m.getElement(r, 1),
                            m.getElement(r, 2))
            v.normalize()
            for c in range(3):
                out.setElement(r, c, v[c])
        return out

    target = om2.MEulerRotation(math.radians(-90.0), 0.0, 0.0).asMatrix()
    if rest_world_matrix is None:
        # Static: the current pose IS the rest. Animated fallback: the
        # clip's first frame (documented caveat: wrong if the clip starts
        # posed off-rest — callers should pass the bind snapshot).
        if frame_range is not None:
            cmds.currentTime(int(frame_range[0]))
        rest_world_matrix = cmds.xform(root, q=True, ws=True, matrix=True)
    rest = rot3(rest_world_matrix)
    # Constant delta driving the rest frame onto the target (row-vector:
    # rest * delta == target).
    delta = rest.inverse() * target

    # Idempotency gate: already at the target — nothing to do.
    q = om2.MTransformationMatrix(delta).rotation(asQuaternion=True)
    delta_deg = math.degrees(2.0 * math.acos(min(1.0, abs(q.w))))
    if delta_deg < 1e-3:
        print(f'[export_core] {root}: rest frame already at the Z-up '
              f'engine target — no conversion applied')
        return
    print(f'[export_core] {root}: driving rest frame to the Z-up engine '
          f'target (delta {delta_deg:.2f} deg)')

    def converted_world(world_flat: list) -> list:
        # Rotation composes with the constant delta; translation row
        # restored so the frame rotates in place.
        m = om2.MMatrix(world_flat) * delta
        for col in range(3):
            m.setElement(3, col, world_flat[12 + col])
        return [m.getElement(r, c) for r in range(4) for c in range(4)]

    children = cmds.listRelatives(root, children=True, type='transform',
                                  fullPath=True) or []

    if frame_range is None:
        saved = [(c, cmds.xform(c, q=True, ws=True, matrix=True))
                 for c in children]
        cmds.xform(root, ws=True, matrix=converted_world(
            cmds.xform(root, q=True, ws=True, matrix=True)))
        for c, m in saved:
            cmds.xform(c, ws=True, matrix=m)
        return

    start, end = int(frame_range[0]), int(frame_range[1])
    # Pass 1: sample every frame first — keying while sampling would let
    # already-modified curves contaminate later frames' world reads.
    samples = []
    for f in range(start, end + 1):
        cmds.currentTime(f)
        samples.append((f,
                        cmds.xform(root, q=True, ws=True, matrix=True),
                        [cmds.xform(c, q=True, ws=True, matrix=True)
                         for c in children]))
    # Pass 2: apply + key. Root first each frame so the children's locals
    # re-express against the converted parent.
    for f, root_w, child_ws in samples:
        cmds.currentTime(f)
        cmds.xform(root, ws=True, matrix=converted_world(root_w))
        cmds.setKeyframe(root, attribute=('translate', 'rotate'), time=f)
        for c, m in zip(children, child_ws):
            cmds.xform(c, ws=True, matrix=m)
            cmds.setKeyframe(c, attribute=('translate', 'rotate'), time=f)
    # Euler continuity on the re-keyed rotation curves.
    curves = []
    for n in [root] + children:
        curves.extend(cmds.keyframe(n, attribute='rotate', q=True,
                                    name=True) or [])
    if curves:
        cmds.filterCurve(*curves)


def apply_fbx_preset(preset: dict) -> None:
    """Apply an FBX export preset by issuing the corresponding FBX MEL setters.

    Each key in `preset` is a MEL function name like 'FBXExportSkins'; values are
    booleans, numbers, or strings. Booleans become 'true'/'false'. Commands listed
    in _POSITIONAL_FBX use a positional argument instead of -v.
    """
    if not cmds.pluginInfo('fbxmaya', q=True, loaded=True):
        cmds.loadPlugin('fbxmaya', quiet=True)

    for key, value in preset.items():
        if key in _POSITIONAL_FBX:
            mel.eval(f'{key} {_format_positional(value)};')
        else:
            mel.eval(f'{key} {_format_flag(value)};')


def _format_flag(value) -> str:
    if isinstance(value, bool):
        return f'-v {"true" if value else "false"}'
    if isinstance(value, (int, float)):
        return f'-v {value}'
    return f'-v "{value}"'


def _format_positional(value) -> str:
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)  # bare token, no quotes — FBXExportUpAxis y; not "y"


# ─────────────────────────────────────────────────────────────────────────────
# Selection helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_mesh_or_group(node: str) -> bool:
    """True if node is a mesh transform, a group containing meshes, or any transform.

    Joints are excluded — they're handled separately by the dispatcher.
    """
    if not cmds.objExists(node):
        return False
    if cmds.nodeType(node) == 'joint':
        return False
    if cmds.nodeType(node) != 'transform':
        return False
    shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
    if any(cmds.nodeType(s) == 'mesh' for s in shapes):
        return True
    descendants = cmds.listRelatives(node, allDescendents=True, type='mesh', fullPath=True) or []
    return bool(descendants)


def non_joint_helpers_under(joints: list[str]) -> list[str]:
    """DAG transforms under an export joint chain that must not ride into
    an FBX. `FBXExport -s` exports each selected joint's whole subtree, so
    helper nodes parented under joints (the aimers' per-joint
    `<joint>_localRef_LOC` locators — delete_armature leaves aimers live
    on purpose — plus any stray null/curve) land in the file as bogus
    bones unless stripped first. Shared by the skeletal exporter (strip +
    undo-restore) and the anim export runner (throwaway scene, plain
    delete).

    Conservative keep-rules; when in doubt, keep:
      - joints (the skeleton itself)
      - constraint nodes (FBX presets already exclude constraints;
        deleting a live one has rig side effects)
      - ikEffectors (deleting one cascades to its ikHandle)
      - any transform with a joint below it (deleting it would take the
        skeleton with it)
      - any transform with a mesh below it (a deliberately rigid-parented
        prop must survive)
    """
    helpers = []
    seen = set()
    for j in joints:
        for t in (cmds.listRelatives(j, allDescendents=True,
                                     type='transform', fullPath=True) or []):
            if t in seen:
                continue
            seen.add(t)
            if cmds.nodeType(t) == 'joint':
                continue
            if cmds.ls(t, type='constraint') or cmds.nodeType(t) == 'ikEffector':
                continue
            if cmds.listRelatives(t, allDescendents=True, type='joint'):
                continue
            if cmds.listRelatives(t, allDescendents=True, type='mesh'):
                continue
            helpers.append(t)
    return helpers


def has_skin_cluster(mesh: str) -> bool:
    """True if there's a skinCluster anywhere in this mesh's history."""
    history = cmds.listHistory(mesh, pruneDagObjects=True) or []
    return bool(cmds.ls(history, type='skinCluster'))


def find_skin_cluster_meshes(joints: list[str]) -> list[str]:
    """Return mesh transforms skinned to any of the given joints."""
    found = set()
    for j in joints:
        clusters = cmds.listConnections(j, type='skinCluster') or []
        for sc in clusters:
            for geo in cmds.skinCluster(sc, q=True, geometry=True) or []:
                found.add(geo)
    return sorted(found)


# ─────────────────────────────────────────────────────────────────────────────
# Network-node CRUD — built on utils/maya/network_nodes
# ─────────────────────────────────────────────────────────────────────────────

from maya_tools.utils.maya import network_nodes as nn


def get_registry() -> str | None:
    return nn.find_one_marked_node(_REGISTRY_MARKER)


def get_or_create_registry() -> str:
    reg = get_registry()
    if reg:
        return reg

    node = nn.create_marked_node(_REGISTRY_MARKER, _REGISTRY_NAME, marker_value='registry')
    scene = cmds.file(q=True, sn=True) or ''
    stem  = Path(scene).stem if scene else ''
    nn.ensure_string_attr(node, 'source_scene_stem', default=stem)
    nn.ensure_string_attr(node, 'version',           default=_VERSION)
    nn.ensure_message_attr(node, 'entries', multi=True)
    return node


def get_source_scene_stem() -> str:
    reg = get_registry()
    return cmds.getAttr(f'{reg}.source_scene_stem') if reg else ''


def set_source_scene_stem(stem: str) -> None:
    reg = get_or_create_registry()
    cmds.setAttr(f'{reg}.source_scene_stem', stem, type='string')


def get_output_dir_override() -> str:
    """The user's per-scene saved output directory. Empty string means
    'use auto' — the UI resolves the project-config destination instead."""
    reg = get_registry()
    if not reg or not cmds.attributeQuery('output_dir_override', node=reg,
                                          exists=True):
        return ''
    return cmds.getAttr(f'{reg}.output_dir_override') or ''


def set_output_dir_override(path: str) -> None:
    """Persist the per-scene output directory. Empty clears it (back to auto);
    clearing when no registry exists yet is a no-op (empty is the default)."""
    path = path or ''
    reg = get_registry()
    if not reg and not path:
        return
    reg = reg or get_or_create_registry()
    nn.ensure_string_attr(reg, 'output_dir_override', default='')
    cmds.setAttr(f'{reg}.output_dir_override', path, type='string')


def list_entries() -> list[str]:
    reg = get_registry()
    if not reg:
        return []
    return nn.get_message_targets(reg, 'entries')


def _default_prefix_for_type(type_: str) -> str:
    """Return the project config's default prefix for the given entry type.
    Empty string if no scene / no matching project / no prefix configured.
    The TYPE_STATIC / TYPE_SKELETAL constant values already match the
    config's 'static_mesh' / 'skeletal_mesh' prefix keys, so type_ is the
    lookup key directly.
    """
    scene = cmds.file(q=True, sn=True) or ''
    if not scene:
        return ''
    try:
        config = load_project_config(scene)
    except Exception:
        return ''
    return (config.get('prefixes') or {}).get(type_, '')


def create_entry(name: str, type_: str, nodes: list[str]) -> str:
    """Create an FAB_Export_<name> network node with message connections to `nodes`."""
    if type_ not in ENTRY_TYPES:
        raise RuntimeError(f"Invalid entry type: {type_!r}. Expected one of {ENTRY_TYPES}.")

    reg = get_or_create_registry()
    base = _ENTRY_PREFIX + _safe_node_name(name)
    node_name = base
    i = 2
    while cmds.objExists(node_name):
        node_name = f'{base}_{i}'
        i += 1

    node = nn.create_marked_node(_ENTRY_MARKER, node_name, marker_value='entry')
    nn.ensure_string_attr(node, 'entry_name',    default=name)
    nn.ensure_string_attr(node, 'entry_type',    default=type_)
    nn.ensure_bool_attr(  node, 'enabled',       default=True)
    nn.ensure_string_attr(node, 'dest_override', default='')
    nn.ensure_string_attr(node, 'entry_prefix',  default=_default_prefix_for_type(type_))
    nn.ensure_message_attr(node, 'nodes', multi=True)
    nn.ensure_message_attr(node, 'registry')

    nn.connect_message(reg, node, 'registry')
    nn.connect_message_multi(node, reg, 'entries')

    for n in nodes:
        if cmds.objExists(n):
            nn.connect_message_multi(n, node, 'nodes')

    return node


def get_entry_data(entry_node: str) -> dict:
    """Read all entry attrs + resolve message connections to current node names."""
    return {
        'node':          entry_node,
        'name':          cmds.getAttr(f'{entry_node}.entry_name') or '',
        'prefix':        get_entry_prefix(entry_node),
        'type':          cmds.getAttr(f'{entry_node}.entry_type') or TYPE_STATIC,
        'enabled':       bool(cmds.getAttr(f'{entry_node}.enabled')),
        'dest_override': cmds.getAttr(f'{entry_node}.dest_override') or '',
        'nodes':         nn.get_message_targets(entry_node, 'nodes'),
    }


def get_entry_prefix(entry_node: str) -> str:
    """Read the entry's stored prefix. Returns empty string for legacy entries
    that pre-date the entry_prefix attr — the user fills the PREFIX cell in
    the UI to populate it. No auto-migration: Adrian strips legacy SM_/SK_
    from entry names manually before opening the new UI."""
    if cmds.attributeQuery('entry_prefix', node=entry_node, exists=True):
        return cmds.getAttr(f'{entry_node}.entry_prefix') or ''
    return ''


def set_entry_prefix(entry_node: str, prefix: str) -> None:
    """Write the entry's prefix attr. Creates the attr first if the entry is
    legacy and hasn't had its prefix touched yet."""
    if not cmds.attributeQuery('entry_prefix', node=entry_node, exists=True):
        nn.ensure_string_attr(entry_node, 'entry_prefix', default='')
    cmds.setAttr(f'{entry_node}.entry_prefix', prefix or '', type='string')


def set_entry_name(entry_node: str, name: str) -> None:
    cmds.setAttr(f'{entry_node}.entry_name', name, type='string')


def set_entry_enabled(entry_node: str, enabled: bool) -> None:
    cmds.setAttr(f'{entry_node}.enabled', bool(enabled))


def set_entry_dest_override(entry_node: str, dest: str) -> None:
    cmds.setAttr(f'{entry_node}.dest_override', dest or '', type='string')


def set_entry_nodes(entry_node: str, nodes: list[str]) -> None:
    """Replace all message connections on entry_node.nodes with the given list."""
    nn.replace_message_multi(entry_node, 'nodes', nodes)


def delete_entry(entry_node: str) -> None:
    if cmds.objExists(entry_node):
        cmds.delete(entry_node)


def _safe_node_name(name: str) -> str:
    """Strip characters that aren't valid in a Maya node name."""
    return re.sub(r'[^A-Za-z0-9_]', '_', name).strip('_') or 'entry'


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_entry(entry_data: dict, config: dict | None,
                   format: str = 'fbx') -> tuple[list[str], list[str]]:
    """Run validation rules. Returns (blocks, warnings).

    Blocks abort the export. Warnings are reported but the export still runs.
    config is None for out-of-project scenes — config-dependent checks skip.
    """
    blocks: list[str] = []
    warns: list[str] = []

    scene = cmds.file(q=True, sn=True)
    if not scene:
        blocks.append("Scene has never been saved to disk.")
    elif cmds.file(q=True, modified=True):
        warns.append("Scene has unsaved changes (export proceeds; sidecar entries will not persist until saved).")

    nodes = entry_data.get('nodes') or []
    if not nodes:
        blocks.append("Entry has no nodes connected.")

    missing = [n for n in nodes if not cmds.objExists(n)]
    if missing:
        blocks.append(f"Entry references missing nodes: {', '.join(missing)}")

    type_ = entry_data.get('type')
    joints = [n for n in nodes if cmds.objExists(n) and cmds.nodeType(n) == 'joint']
    meshes = [n for n in nodes if cmds.objExists(n) and is_mesh_or_group(n)]

    if type_ == TYPE_SKELETAL:
        if not joints:
            blocks.append("Skeletal export requires at least one joint in the entry's nodes.")
        else:
            # Walk into descendants — entries often contain a group transform
            # (geo_grp) rather than individual mesh transforms. has_skin_cluster
            # uses cmds.listHistory(pruneDagObjects=True), which doesn't see
            # through groups, so checking only the top-level entry nodes gives
            # a false negative.
            mesh_shapes: list[str] = []
            for m in meshes:
                mesh_shapes.append(m)
                mesh_shapes.extend(
                    cmds.listRelatives(m, allDescendents=True, type='mesh',
                                       fullPath=True) or []
                )
            if not any(has_skin_cluster(m) for m in mesh_shapes):
                # USD: a deliberate workflow, not a mistake — model + skeleton
                # travel to Armature and the skinning happens THERE (first
                # field request, Jarrod 2026-08-04). FBX keeps the block: an
                # unskinned skeletal FBX in the engine pipeline is almost
                # always an error.
                if format == 'usd':
                    warns.append(
                        "No skinCluster bound — exporting model + skeleton "
                        "without skinning (bind in Armature).")
                else:
                    blocks.append("Skeletal export: no mesh in selection has a skinCluster bound.")

    if scene and config:
        try:
            out_dir = resolve_destination(scene, config)
            if not out_dir.parent.exists() and not drive_exists(out_dir):
                blocks.append(f"Target drive does not exist: {out_dir}")
        except RuntimeError as exc:
            blocks.append(str(exc))

    for m in meshes:
        for shape in (cmds.listRelatives(m, allDescendents=True, type='mesh', fullPath=True) or []):
            history = cmds.listHistory(shape, pruneDagObjects=True) or []
            non_construction = [h for h in history if cmds.nodeType(h) not in ('mesh', 'shadingEngine', 'lambert', 'blinn', 'phong')]
            if len(non_construction) > 1:
                warns.append(f"Construction history present on {shape}.")
                break

    return blocks, warns


def drive_exists(path: Path) -> bool:
    drive = path.drive
    if not drive:
        return True
    return Path(drive + os.sep).exists()

