"""Compile the authored `asset_classes` model into the exporter's derived
`path_patterns` / `anim_path_patterns`. Pure Python: no maya, no Qt, so it is
import-safe and unit-testable under bare mayapy (or py -3).

The setup tool authors `asset_classes` (the truth); this module regenerates
the exporter-facing keys on every save. See
docs/superpowers/specs/2026-07-07-project-setup-tool-design.md sec 4.2.
"""
from __future__ import annotations

import re

# The only two placeholders the authoring model exposes, mapped to the
# named-capture-group the exporter's re.match consumes.
_PLACEHOLDERS = {
    "name": r"(?P<name>[^/]+)",   # exactly one path segment
    "path": r"(?P<path>.+)",       # one or more path segments
}
_TOKEN = re.compile(r"\{([a-z_]+)\}")


def placeholders_in(template):
    """Ordered placeholder names used in a source_dir / dest template."""
    return _TOKEN.findall(template)


def _ends_with_placeholder(source_dir):
    """True if the final '/'-segment of source_dir is a bare placeholder
    (the asset folder itself), False if it is a literal segment."""
    last = source_dir.rstrip("/").rsplit("/", 1)[-1]
    return _TOKEN.fullmatch(last) is not None


def source_to_match(source_dir):
    """Authored source_dir (relative to project_root, using {name}/{path}) ->
    the exporter path_patterns 'match' regex tested against a scene path
    relative to project_root.

    Literal runs are regex-escaped; placeholders become named groups; a file
    tail is appended. Tail rule (reproduces the shipped configs' behavior): if
    source_dir ends in a placeholder the asset folder is the placeholder and
    files may nest, so the tail is r'/.*\\.ma$'; if it ends in a literal
    segment files sit directly in it, so the tail is r'/[^/]+\\.ma$'.
    """
    out, pos = [], 0
    for m in _TOKEN.finditer(source_dir):
        out.append(re.escape(source_dir[pos:m.start()]))
        key = m.group(1)
        if key not in _PLACEHOLDERS:
            raise ValueError("Unknown placeholder {%s} in %r" % (key, source_dir))
        out.append(_PLACEHOLDERS[key])
        pos = m.end()
    out.append(re.escape(source_dir[pos:]))
    tail = r"/.*\.ma$" if _ends_with_placeholder(source_dir) else r"/[^/]+\.ma$"
    return "".join(out) + tail


# Engine export types that produce a mesh path_patterns entry (the anim
# destinations are handled separately, keyed by output_kind).
MESH_TYPES = ("static_mesh", "skeletal_mesh")
ANIM_KINDS = ("gameplay", "cinematic")


def generate_patterns(asset_classes):
    """asset_classes -> {'path_patterns': [...],
    'anim_path_patterns': {'gameplay': [...], 'cinematic': [...]}}.

    A class emits a path_patterns entry only if it has a mesh export_type;
    every class contributes anim entries for whichever output_kinds it sets.
    All entries for one class share the same 'match' regex (derived from its
    source_dir); only the dest differs.
    """
    path_patterns = []
    anim = {kind: [] for kind in ANIM_KINDS}
    for ac in asset_classes:
        match = source_to_match(ac["source_dir"])
        if any(t in MESH_TYPES for t in ac.get("export_types", [])):
            path_patterns.append({"match": match, "dest": ac["dest_dir"]})
        anim_map = ac.get("anim") or {}
        for kind in ANIM_KINDS:
            dest = anim_map.get(kind, "")
            if dest:
                anim[kind].append({"match": match, "dest": dest})
    return {"path_patterns": path_patterns, "anim_path_patterns": anim}


SCHEMA_VERSION = 2
_GENERATED_NOTE = (
    "path_patterns / anim_path_patterns / prefixes are GENERATED from "
    "asset_classes by the project setup tool. Edit asset_classes and re-save; "
    "do not hand-edit the generated keys."
)


def generate_export_mapping(authored):
    """Authored config -> the full export_mapping.json superset dict the
    exporter consumes. IO-free: returns a dict; the file write lands in a
    later plan. The exporter only .get()s its own keys, so the added
    asset_classes / schema_version / _note are ignored by it.
    """
    derived = generate_patterns(authored.get("asset_classes", []))
    return {
        "schema_version": SCHEMA_VERSION,
        "_note": _GENERATED_NOTE,
        "name": authored.get("name", ""),
        "asset_classes": authored.get("asset_classes", []),
        # --- generated (do not hand-edit) ---
        "path_patterns": derived["path_patterns"],
        "anim_path_patterns": derived["anim_path_patterns"],
        "prefixes": authored.get("prefixes", {}),
        "anim_prefix": authored.get("anim_prefix", ""),
        # --- kept exporter keys (v2: machine-absolute roots live in the
        # user bindings, never here; subpaths resolve under the user's
        # Source Art Root via project_config_resolve) ---
        "pose_library_subpath": authored.get("pose_library_subpath", ""),
        "anim_library_subpath": authored.get("anim_library_subpath", ""),
        "mesh_group_name": authored.get("mesh_group_name", ""),
        # Engine bone-space up axis for skeletal/anim exports ('' = follow
        # the exporter default 'y'; 'z' = Unreal root-frame conversion, the
        # 2026-07-14 swimming-skeleton fix). Consumed by
        # export_core.engine_up_axis via the resolved config.
        "engine_up_axis": authored.get("engine_up_axis", ""),
        "fbx_presets": authored.get("fbx_presets", {}),
    }
