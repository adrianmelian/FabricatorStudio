"""Exporter — headless API.

Public entry points:
    export_one_button()              -> dispatch from viewport selection
    export_entry(entry_node)          -> export one persisted entry
    export_all()                      -> export every enabled entry
    export_selected_entries(nodes)    -> export the given list of entries
    fix_paths()                       -> rename entries from old scene stem to new
"""
__author__ = "Adrian Melian"

import os
from pathlib import Path

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.export import export_core, static_mesh, skeletal_mesh


# ─────────────────────────────────────────────────────────────────────────────
# One-button dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def export_one_button(*, engine_up_axis_override: str = '') -> str:
    """Dispatch based on viewport selection. Returns the FBX path written.

    Output dir: the scene's saved Output Dir override wins, then the
    project-config auto path. No project needed — see _resolve_out_dir.

    engine_up_axis_override: '' follows the project config ('y' when no
    config); 'y'/'z' forces the skeletal bone-space target (Export Options).
    """
    sel = cmds.ls(sl=True, long=False) or []
    if not sel:
        raise RuntimeError("Nothing selected. Select meshes (static) or root joint + meshes (skeletal).")

    scene_path = cmds.file(q=True, sn=True)
    if not scene_path:
        raise RuntimeError("Scene must be saved before exporting.")
    if cmds.file(q=True, modified=True):
        cmds.warning("[Exporter] Scene has unsaved changes — exporting current in-memory state.")

    config   = export_core.load_project_config_or_none(scene_path)
    out_dir  = _resolve_out_dir(scene_path, config)
    base     = Path(scene_path).stem
    prefixes = (config or {}).get('prefixes') or {}

    joints = [s for s in sel if cmds.nodeType(s) == 'joint']
    meshes = [s for s in sel if export_core.is_mesh_or_group(s)]

    if joints:
        if not meshes:
            raise RuntimeError("Skeletal export needs at least one mesh selected alongside the joint(s).")
        out_name = f'{prefixes.get("skeletal_mesh", "SK_")}{base}.fbx'
        preset   = export_core.fbx_preset(config, export_core.TYPE_SKELETAL)
        out_path = _ensure_dir(out_dir / out_name)
        axis = export_core.engine_up_axis(config, engine_up_axis_override)
        skeletal_mesh.export_skeletal(joints, meshes, str(out_path), preset,
                                      engine_up_axis=axis)
    else:
        if not meshes:
            raise RuntimeError("Selection contains no meshes or joints.")
        out_name = f'{prefixes.get("static_mesh", "SM_")}{base}.fbx'
        preset   = export_core.fbx_preset(config, export_core.TYPE_STATIC)
        out_path = _ensure_dir(out_dir / out_name)
        static_mesh.export_static(meshes, str(out_path), preset)

    om.MGlobal.displayInfo(f'[Exporter] Exported: {out_path}')
    return str(out_path)


def _resolve_out_dir(scene_path: str, config: dict | None, *,
                     dest_override: str = '', entry_override: str = '') -> Path:
    """Output dir precedence: caller/UI dest_override > per-entry override >
    the scene's saved Output Dir > project-config auto path.

    A project is only the auto-path convenience. Without one, the user's
    Output Dir (persisted per scene by the exporter UI) carries the export;
    only when that is also missing does the export stop, with instructions.
    """
    if dest_override:
        return Path(dest_override)
    if entry_override:
        return Path(entry_override)
    saved = export_core.get_output_dir_override()
    if saved:
        return Path(saved)
    if config:
        return export_core.resolve_destination(scene_path, config)
    raise RuntimeError(
        "Scene is outside any project and has no saved Output Dir. "
        "Set Output Dir in the Rig Exporter (type or Browse a folder — it "
        "saves with the scene), or register a project in Project Setup for "
        "automatic paths."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Multi-entry export
# ─────────────────────────────────────────────────────────────────────────────

def export_entry(entry_node: str, *, dest_override: str = '',
                 engine_up_axis_override: str = '') -> str:
    """Export a single persisted entry. Returns the FBX path written.

    dest_override: UI-level output directory. When non-empty, wins over the
    per-entry dest_override, the scene's saved Output Dir, and the project
    config default — matches the anim exporter's Output Dir pattern. Full
    precedence in _resolve_out_dir; no project config is required.

    engine_up_axis_override: '' follows the project config ('y' when no
    config); 'y'/'z' forces the skeletal bone-space target (Export Options).
    """
    entry = export_core.get_entry_data(entry_node)
    scene_path = cmds.file(q=True, sn=True)
    if not scene_path:
        raise RuntimeError("Scene must be saved before exporting.")
    if cmds.file(q=True, modified=True):
        cmds.warning("[Exporter] Scene has unsaved changes — exporting current in-memory state.")

    config = export_core.load_project_config_or_none(scene_path)
    blocks, warns = export_core.validate_entry(entry, config)
    for w in warns:
        cmds.warning(f'[Exporter] {entry["name"]}: {w}')
    if blocks:
        raise RuntimeError(f'{entry["name"]}: ' + '; '.join(blocks))

    out_dir = _resolve_out_dir(scene_path, config,
                               dest_override=dest_override,
                               entry_override=entry['dest_override'])
    out_path = _ensure_dir(out_dir / f'{entry["prefix"]}{entry["name"]}.fbx')

    preset = export_core.fbx_preset(config, entry['type'])
    nodes  = entry['nodes']

    if entry['type'] == export_core.TYPE_SKELETAL:
        joints = [n for n in nodes if cmds.nodeType(n) == 'joint']
        meshes = [n for n in nodes if export_core.is_mesh_or_group(n)]
        axis = export_core.engine_up_axis(config, engine_up_axis_override)
        skeletal_mesh.export_skeletal(joints, meshes, str(out_path), preset,
                                      engine_up_axis=axis)
    else:
        meshes = [n for n in nodes if export_core.is_mesh_or_group(n)]
        static_mesh.export_static(meshes, str(out_path), preset)

    om.MGlobal.displayInfo(f'[Exporter] Exported: {out_path}')
    return str(out_path)


def export_all(*, dest_override: str = '',
               engine_up_axis_override: str = '',
               progress_cb=None) -> list[str]:
    """Export every enabled entry. Returns list of FBX paths written.

    Overrides forwarded to export_entry — see that function for resolution.

    progress_cb, if provided, is called as progress_cb(index, total,
    entry_name) before each entry exports (index is 1-based). Exceptions
    from it are swallowed — a UI hiccup never wounds the export.
    """
    enabled = [n for n in export_core.list_entries()
               if export_core.get_entry_data(n)['enabled']]
    return export_selected_entries(
        enabled, dest_override=dest_override,
        engine_up_axis_override=engine_up_axis_override,
        progress_cb=progress_cb)


def export_selected_entries(entry_nodes: list[str], *,
                            dest_override: str = '',
                            engine_up_axis_override: str = '',
                            progress_cb=None) -> list[str]:
    """Export the given entry nodes (regardless of enabled flag).

    Overrides forwarded to export_entry — see that function for resolution.

    progress_cb, if provided, is called as progress_cb(index, total,
    entry_name) before each entry exports (index is 1-based). Exceptions
    from it are swallowed — a UI hiccup never wounds the export.
    """
    written = []
    total = len(entry_nodes)
    for index, node in enumerate(entry_nodes, 1):
        data = (export_core.get_entry_data(node)
                if cmds.objExists(node) else {'name': node})
        if progress_cb:
            try:
                progress_cb(index, total, data.get('name', node))
            except Exception:
                pass
        try:
            written.append(export_entry(
                node, dest_override=dest_override,
                engine_up_axis_override=engine_up_axis_override))
        except Exception as exc:
            cmds.warning(f'[Exporter] Skipped {data.get("name", node)}: {exc}')
    return written


# ─────────────────────────────────────────────────────────────────────────────
# Add entry from current selection
# ─────────────────────────────────────────────────────────────────────────────

def add_entry_from_selection() -> str:
    """Create a new entry from the current viewport selection.

    Type inferred from selection (joint present = skeletal). Name prefilled as
    <scene_stem>; auto-suffixed if a network node already exists. Prefix is
    auto-filled into the entry's entry_prefix attr inside create_entry from
    the project config's prefixes.<type_>. Returns the new entry network
    node name.
    """
    sel = cmds.ls(sl=True, long=False) or []
    if not sel:
        raise RuntimeError("Nothing selected.")

    scene_path = cmds.file(q=True, sn=True)
    if not scene_path:
        raise RuntimeError("Scene must be saved before adding entries.")

    base = Path(scene_path).stem

    has_joint = any(cmds.nodeType(s) == 'joint' for s in sel)
    type_     = export_core.TYPE_SKELETAL if has_joint else export_core.TYPE_STATIC

    name = _unique_entry_name(base)
    return export_core.create_entry(name, type_, sel)


def _unique_entry_name(base: str) -> str:
    """Return base, or base_2/_3/... if an entry with that name already exists."""
    existing = {export_core.get_entry_data(n)['name'] for n in export_core.list_entries()}
    if base not in existing:
        return base
    i = 2
    while f'{base}_{i}' in existing:
        i += 1
    return f'{base}_{i}'


# ─────────────────────────────────────────────────────────────────────────────
# Create Entry (auto-seeded from the rig — no manual selection)
# ─────────────────────────────────────────────────────────────────────────────

def _short_leaf(name: str) -> str:
    return name.split('|')[-1].split(':')[-1]


def resolve_mesh_group() -> str:
    """The scene's mesh group transform: the project config's 'mesh_group_name'
    when set and present, else a scan for a group named 'geo_grp' / 'mesh_grp'.
    Namespace-tolerant (matches the leaf name). '' when none is found."""
    scene = cmds.file(q=True, sn=True) or ''
    configured = ''
    if scene:
        try:
            configured = (export_core.load_project_config(scene)
                          or {}).get('mesh_group_name', '').strip()
        except Exception:
            configured = ''
    candidates = ([configured] if configured else []) + ['geo_grp', 'mesh_grp']
    transforms = cmds.ls(type='transform', long=False) or []
    for want in candidates:
        want_leaf = _short_leaf(want)
        if not want_leaf:
            continue
        for t in transforms:
            if _short_leaf(t) == want_leaf:
                return t
    return ''


def resolve_world_root_joint() -> str:
    """The Fabricator World component's joint, i.e. the rig registry's root
    joint. '' when there is no Fabricator rig in the scene."""
    try:
        from maya_tools.rigging.fabricator import nodes as fab_nodes
        root = fab_nodes.get_registry_root_joint()
        return root if root and cmds.objExists(root) else ''
    except Exception:
        return ''


def create_seeded_entry() -> str:
    """'+ Create Entry': seed a new entry straight from the rig, no manual
    selection. Root joint (World / registry root) + mesh group become the
    entry's nodes; skeletal when a root joint is present, else static (mesh
    only). Returns the new entry node name; raises when nothing is seedable."""
    scene_path = cmds.file(q=True, sn=True)
    if not scene_path:
        raise RuntimeError("Scene must be saved before adding entries.")

    root = resolve_world_root_joint()
    mesh = resolve_mesh_group()
    seed = [n for n in (root, mesh) if n]
    if not seed:
        raise RuntimeError(
            "Nothing to seed: found no World/root joint and no mesh group. "
            "Set 'Mesh Group Name' in project setup, or name a group "
            "'geo_grp' / 'mesh_grp'.")

    type_ = export_core.TYPE_SKELETAL if root else export_core.TYPE_STATIC
    name = _unique_entry_name(Path(scene_path).stem)
    return export_core.create_entry(name, type_, seed)


# ─────────────────────────────────────────────────────────────────────────────
# Fix Paths
# ─────────────────────────────────────────────────────────────────────────────

def needs_fix_paths() -> tuple[bool, str, str]:
    """(needs_fix, source_stem, current_stem) — True if scene was renamed/copied."""
    scene_path = cmds.file(q=True, sn=True)
    current = Path(scene_path).stem if scene_path else ''
    source  = export_core.get_source_scene_stem()
    return (bool(source) and bool(current) and source != current, source, current)


def fix_paths() -> dict:
    """Rename all entries by replacing old scene stem with current. Clear dest overrides.

    Returns a report dict: {'renamed': [...], 'cleared_overrides': [...], 'missing_nodes': {...}}
    """
    needs, old_stem, new_stem = needs_fix_paths()
    if not needs:
        return {'renamed': [], 'cleared_overrides': [], 'missing_nodes': {}}

    report = {'renamed': [], 'cleared_overrides': [], 'missing_nodes': {}}

    for entry_node in export_core.list_entries():
        data     = export_core.get_entry_data(entry_node)
        old_name = data['name']
        new_name = old_name.replace(old_stem, new_stem) if old_stem in old_name else old_name

        if new_name != old_name:
            export_core.set_entry_name(entry_node, new_name)
            report['renamed'].append((old_name, new_name))

        if data['dest_override']:
            export_core.set_entry_dest_override(entry_node, '')
            report['cleared_overrides'].append(new_name)

        missing = [n for n in data['nodes'] if not cmds.objExists(n)]
        if missing:
            report['missing_nodes'][new_name] = missing

    export_core.set_source_scene_stem(new_stem)
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_dir(out_path: Path) -> Path:
    os.makedirs(out_path.parent, exist_ok=True)
    return out_path
