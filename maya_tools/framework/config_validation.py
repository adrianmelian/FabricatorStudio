"""The sole schema gate for the authored project-setup config. Pure Python:
no maya.cmds, no Qt -- runs headless, import-safe under bare mayapy.

validate_config() runs before every save (session.json + export_mapping.json).
Errors block save; warnings confirm-through. Returns errors first, then
warnings, so a caller can slice by severity without re-sorting. See
docs/superpowers/specs/2026-07-07-project-setup-tool-design.md sec 5.
"""
from __future__ import annotations

import os
import re
from collections import namedtuple
from pathlib import Path

from maya_tools.framework import config_generator
from maya_tools.framework import project_config_paths

Issue = namedtuple("Issue", "severity field message")


def _subpath_problem(value):
    """Why `value` is not a clean relative subpath, or None if it is.
    os.path.isabs alone is not enough on Windows: drive-relative forms
    ('C:poses', isabs()==False) make pathlib's join DISCARD the root
    entirely, and '..' segments escape it - both would break the
    'resolves under Source Art Root on every machine' contract."""
    if os.path.isabs(value):
        return "must be a relative subpath, got an absolute path"
    if os.path.splitdrive(value)[0]:
        return "must not carry a drive letter"
    if ".." in Path(value).parts:
        return "must not contain '..'"
    return None


def validate_config(authored, existing_projects=None):
    """authored -> list[Issue], errors first then warnings.

    existing_projects is a list of already-saved authored dicts (used for
    project_root overlap warnings and identity-slug collision errors).
    Exclude the project being edited from this list before calling an edit
    validation, or it will always collide with itself.
    """
    existing_projects = existing_projects or []
    issues = []
    issues += _validate_identity(authored, existing_projects)
    issues += _validate_units(authored)
    issues += _validate_playback(authored)
    issues += _validate_asset_classes(authored)
    issues += _validate_fbx(authored)
    issues += _validate_prefixes(authored)
    issues += _validate_subpaths(authored)
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    return errors + warnings


# --- units / fps (Maya's currentUnit tokens; see maya_startup.apply_units) ---
_LINEAR_UNITS = {"mm", "cm", "m", "km", "in", "ft", "yd", "mi"}
_ANGULAR_UNITS = {"deg", "rad"}
_NAMED_FRAME_RATES = {"game", "film", "pal", "ntsc", "show", "palf", "ntscf"}
# '<n>fps' (e.g. "24fps") and '<n>df' drop-frame tokens (e.g. "29.97df", "23.976df").
_FPS_TOKEN = re.compile(r"^\d+(\.\d+)?(fps|df)$")


def _validate_units(authored):
    issues = []
    linear = authored.get("linear_unit")
    if linear not in _LINEAR_UNITS:
        issues.append(Issue("error", "linear_unit",
            f"linear_unit {linear!r} is not a Maya linear unit token "
            f"({sorted(_LINEAR_UNITS)})"))
    angular = authored.get("angular_unit")
    if angular not in _ANGULAR_UNITS:
        issues.append(Issue("error", "angular_unit",
            f"angular_unit {angular!r} is not a Maya angular unit token "
            f"({sorted(_ANGULAR_UNITS)})"))
    frame_rate = authored.get("frame_rate")
    if not (isinstance(frame_rate, str)
            and (frame_rate in _NAMED_FRAME_RATES or _FPS_TOKEN.match(frame_rate))):
        issues.append(Issue("error", "frame_rate",
            f"frame_rate {frame_rate!r} is not a Maya time unit token "
            f"({sorted(_NAMED_FRAME_RATES)}) or '<n>fps'/'<n>df'"))
    return issues


def _validate_playback(authored):
    issues = []
    start = authored.get("playback_start")
    end = authored.get("playback_end")
    if (start is None) != (end is None):
        issues.append(Issue("error", "playback",
            f"playback_start and playback_end must be set together or both "
            f"left None (got start={start!r}, end={end!r})"))
    elif start is not None and end is not None and start > end:
        issues.append(Issue("error", "playback",
            f"playback_start ({start}) must be <= playback_end ({end})"))
    return issues


def _norm_path(path):
    return Path(os.path.normcase(os.path.normpath(path)))


def _paths_overlap(a, b):
    """True if a is under b, b is under a, or they are the same path."""
    return a.is_relative_to(b) or b.is_relative_to(a)


def _validate_subpaths(authored):
    """v2: the three library/blueprints locations in the PROJECT tier are
    relative subpaths under the user's Source Art Root. Absolute paths
    belong in the per-machine bindings overrides (validate_bindings), never
    in the shared config. A blank blueprints_subpath means factory
    blueprints only (warning, same confirm-through as v1's blank dir)."""
    issues = []
    for field in ("pose_library_subpath", "anim_library_subpath", "blueprints_subpath"):
        value = authored.get(field) or ""
        problem = _subpath_problem(value) if value else None
        if problem:
            issues.append(Issue("error", field,
                f"{field} {problem} (got {value!r}; use the per-machine "
                f"override for absolute locations)"))
    if not (authored.get("blueprints_subpath") or ""):
        issues.append(Issue("warning", "blueprints_subpath",
            "blueprints_subpath is blank; factory blueprints only"))
    axis = authored.get("engine_up_axis") or ""
    if axis not in ("", "y", "z"):
        issues.append(Issue("error", "engine_up_axis",
            f"engine_up_axis must be '', 'y', or 'z', got {axis!r}"))
    return issues


# --- asset_classes ---
_ID_RE = re.compile(r"^[a-z0-9_]+$")
_PLACEHOLDER_VOCAB = {"name", "path"}
_ANIM_KINDS = ("gameplay", "cinematic")


def _check_vocab_and_source_coverage(field, template, source_dir, source_tokens):
    """template's placeholders must be in {name,path} (vocab), and every
    valid-vocab token it uses must also appear in source_dir (coverage) --
    the exporter's named capture group only exists if source_dir declared
    it. Called for source_dir itself (trivially clean), dest_dir, and each
    anim dest."""
    issues = []
    tokens = set(config_generator.placeholders_in(template))
    for bad in sorted(tokens - _PLACEHOLDER_VOCAB):
        issues.append(Issue("error", field,
            f"{{{bad}}} is not a supported placeholder (only {{name}}/{{path}})"))
    valid_tokens = tokens & _PLACEHOLDER_VOCAB
    for tok in sorted(valid_tokens - source_tokens):
        issues.append(Issue("error", field,
            f"uses {{{tok}}} which is not present in source_dir {source_dir!r}"))
    return issues


def _validate_asset_classes(authored):
    issues = []
    classes = authored.get("asset_classes") or []
    if not classes:
        issues.append(Issue("error", "asset_classes",
            "asset_classes must contain at least one class"))
        return issues

    seen_ids = set()
    for i, ac in enumerate(classes):
        prefix = f"asset_classes[{i}]"
        cid = ac.get("id", "")
        if not isinstance(cid, str) or not cid or not _ID_RE.match(cid):
            issues.append(Issue("error", f"{prefix}.id",
                f"asset class id {cid!r} must be a non-empty, slug-safe "
                f"string (lowercase letters, digits, underscore)"))
        elif cid in seen_ids:
            issues.append(Issue("error", f"{prefix}.id",
                f"duplicate asset class id {cid!r}"))
        else:
            seen_ids.add(cid)

        # `or ""` coerces an explicit JSON null (key present, value None) the
        # same as a missing key, BEFORE any placeholder/coverage check runs --
        # config_generator.placeholders_in(None) raises TypeError otherwise.
        source_dir = ac.get("source_dir") or ""
        dest_dir = ac.get("dest_dir") or ""
        if not source_dir:
            issues.append(Issue("error", f"{prefix}.source_dir",
                "source_dir must not be empty"))
        else:
            problem = _subpath_problem(source_dir)
            if problem:
                issues.append(Issue("error", f"{prefix}.source_dir",
                    f"source_dir {problem} (relative to Source Art Root; "
                    f"got {source_dir!r})"))
        if not dest_dir:
            issues.append(Issue("error", f"{prefix}.dest_dir",
                "dest_dir must not be empty"))
        else:
            problem = _subpath_problem(dest_dir)
            if problem:
                issues.append(Issue("error", f"{prefix}.dest_dir",
                    f"dest_dir {problem} (relative to Content Root; "
                    f"got {dest_dir!r})"))

        source_tokens = set(config_generator.placeholders_in(source_dir))
        issues += _check_vocab_and_source_coverage(
            f"{prefix}.source_dir", source_dir, source_dir, source_tokens)
        issues += _check_vocab_and_source_coverage(
            f"{prefix}.dest_dir", dest_dir, source_dir, source_tokens)

        anim = ac.get("anim") or {}
        for kind in _ANIM_KINDS:
            dest = anim.get(kind, "")
            if dest:
                issues += _check_vocab_and_source_coverage(
                    f"{prefix}.anim.{kind}", dest, source_dir, source_tokens)

    if not issues:
        issues += _assert_patterns_resolve(classes)
    return issues


def _assert_patterns_resolve(asset_classes):
    """Post-generate assert: calls config_generator.generate_patterns and
    str.format-resolves each generated dest against its own match's named
    capture groups. The per-class checks above catch the common authoring
    mistakes declaratively; this is the regression net against
    generator/validator drift, using dummy values for each capture group."""
    issues = []
    try:
        generated = config_generator.generate_patterns(asset_classes)
    except (ValueError, KeyError) as exc:
        issues.append(Issue("error", "asset_classes",
            f"config_generator.generate_patterns failed: {exc!r}"))
        return issues
    entries = list(generated["path_patterns"])
    for kind in _ANIM_KINDS:
        entries += generated["anim_path_patterns"][kind]
    for entry in entries:
        names = re.compile(entry["match"]).groupindex.keys()
        dummy = {name: "x" for name in names}
        try:
            entry["dest"].format(**dummy)
        except (KeyError, IndexError) as exc:
            issues.append(Issue("error", "asset_classes",
                f"generated dest {entry['dest']!r} does not resolve against "
                f"match {entry['match']!r}: {exc!r}"))
    return issues


# --- fbx_presets: a BROAD canonical FBXExport* whitelist (not tied to any
# one engine template's key set). An unrecognized key is a WARNING, not an
# error, so a vetted Unity/Godot preset carrying extra FBXExport* options is
# never blocked from saving; a genuine typo still surfaces to the author as
# an actionable message. Downstream code indexes fbx_presets with raw dict
# access (no .get()), so this is the only thing that catches a typo'd key
# before it becomes a silently-broken MEL call -- but as a warning, it
# confirms-through rather than blocking save. A missing fbx type
# (static_mesh/skeletal_mesh/animation) or an empty preset IS still an error.
_FBX_KEY_WHITELIST = {
    "FBXExportSkins",
    "FBXExportSkeletonDefinitions",
    "FBXExportSmoothingGroups",
    "FBXExportTangents",
    "FBXExportSmoothMesh",
    "FBXExportTriangulate",
    "FBXExportShapes",
    "FBXExportInstances",
    "FBXExportReferencedAssetsContent",
    "FBXExportInputConnections",
    "FBXExportConstraints",
    "FBXExportCameras",
    "FBXExportLights",
    "FBXExportEmbeddedTextures",
    "FBXExportUseSceneName",
    "FBXExportInAscii",
    "FBXExportFileVersion",
    "FBXExportUpAxis",
    "FBXExportBakeComplexAnimation",
    "FBXExportAxisConversionMethod",
    "FBXExportConvertUnitString",
    "FBXExportBakeComplexStart",
    "FBXExportBakeComplexEnd",
    "FBXExportBakeComplexStep",
    "FBXExportGenerateLog",
    "FBXExportQuaternion",
}
_FBX_PRESET_TYPES = ("static_mesh", "skeletal_mesh", "animation")


def _validate_fbx(authored):
    issues = []
    presets = authored.get("fbx_presets") or {}
    for kind in _FBX_PRESET_TYPES:
        preset = presets.get(kind)
        if not preset:
            issues.append(Issue("error", f"fbx_presets.{kind}",
                f"fbx_presets.{kind} is required and must be non-empty"))
            continue
        for key in preset:
            if key not in _FBX_KEY_WHITELIST:
                issues.append(Issue("warning", f"fbx_presets.{kind}.{key}",
                    f"{key!r} is not a recognized FBXExport* option "
                    f"(unrecognized preset key, passed through)"))
    return issues


def _validate_prefixes(authored):
    issues = []
    prefixes = authored.get("prefixes") or {}
    if not prefixes.get("static_mesh"):
        issues.append(Issue("error", "prefixes.static_mesh",
            "prefixes.static_mesh is required"))
    if not prefixes.get("skeletal_mesh"):
        issues.append(Issue("error", "prefixes.skeletal_mesh",
            "prefixes.skeletal_mesh is required"))
    if not authored.get("anim_prefix"):
        issues.append(Issue("error", "anim_prefix", "anim_prefix is required"))
    return issues


_BINDING_ROOT_FIELDS = ("source_art_root", "content_root")
_BINDING_OVERRIDE_FIELDS = ("pose_library_root_override",
                            "anim_library_root_override",
                            "blueprints_dir_override")


def validate_bindings(bindings, existing_bindings=None):
    """The per-machine bindings gate (user tier; see the v2 spec sec 3).
    Both roots are required and absolute; overrides are optional but must
    be absolute when set. existing_bindings: [{'slug', 'source_art_root',
    'content_root'}] for the OTHER bound projects on this machine - a root
    overlapping another project's root warns (confirm-through), mirroring
    v1's project_root overlap warning. Errors first, then warnings."""
    bindings = bindings or {}
    issues = []
    for field in _BINDING_ROOT_FIELDS:
        value = bindings.get(field, "")
        if not value:
            issues.append(Issue("error", field, f"{field} is required"))
        elif not os.path.isabs(value):
            issues.append(Issue("error", field,
                f"{field} must be an absolute path, got {value!r}"))
    for field in _BINDING_OVERRIDE_FIELDS:
        value = bindings.get(field, "")
        if value and not os.path.isabs(value):
            issues.append(Issue("error", field,
                f"{field} must be an absolute path when set, got {value!r}"))
    for other in existing_bindings or []:
        for field in _BINDING_ROOT_FIELDS:
            mine = bindings.get(field, "")
            theirs = other.get(field, "")
            if (mine and theirs and os.path.isabs(mine) and os.path.isabs(theirs)
                    and _paths_overlap(_norm_path(mine), _norm_path(theirs))):
                issues.append(Issue("warning", field,
                    f"{field} {mine!r} overlaps project "
                    f"{other.get('slug', '?')!r} at {theirs!r}"))
    errors = [i for i in issues if i.severity == "error"]
    return errors + [i for i in issues if i.severity == "warning"]


def _slugify(name):
    """Local mirror of the future project_config_io.slugify (Plan 3):
    lowercase, safe chars. Deliberately does NOT strip a leading '_' --
    that is exactly what _validate_identity checks for below, since
    project_config_paths treats a '_'-prefixed folder name as an invisible
    template, not a real project (see maya_startup.list_configs)."""
    return re.sub(r"[^a-z0-9_]+", "_", name.strip().lower())


def _validate_identity(authored, existing_projects):
    issues = []
    name = authored.get("name", "")
    if not name:
        issues.append(Issue("error", "name", "name is required"))
        return issues
    slug = _slugify(name)
    if not slug:
        issues.append(Issue("error", "name",
            f"name {name!r} does not produce a valid identity slug"))
        return issues
    if slug.startswith("_"):
        issues.append(Issue("error", "name",
            f"derived slug {slug!r} must not start with '_' "
            f"(reserved for templates)"))
    if slug == os.path.splitext(project_config_paths.SETTINGS_FILE)[0]:
        # _user/ holds one <slug>.json per project PLUS the configs-root
        # pointer file; this slug would overwrite the pointer.
        issues.append(Issue("error", "name",
            f"derived slug {slug!r} is a reserved name (the configs-root "
            f"pointer file) and cannot be a project"))
    for existing in existing_projects:
        other_name = existing.get("name", "")
        if other_name and _slugify(other_name) == slug:
            issues.append(Issue("error", "name",
                f"derived slug {slug!r} collides with existing project "
                f"{other_name!r}"))
    return issues
