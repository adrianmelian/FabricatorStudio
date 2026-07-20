# Python/maya_tools/export/anim_app.py
"""Anim exporter — public headless API.

Public entry points:
    add_clip_from_range()                       create a clip from current playback
    duplicate_clip(clip_node)                   copy attrs + rigs[] connections
    delete_clip(clip_node)                      remove and disconnect
    fix_paths()                                 update names + clear dest_overrides
    needs_fix_paths()                           True if scene was renamed/copied

    export_clip(clip_node)                      single-clip export
    export_clips(clip_nodes)                    batch export specific nodes
    export_all_clips()                          every clip
    export_checked_clips()                      clips with enabled=True
"""
__author__ = "Adrian Melian"

import re
from pathlib import Path

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.export import export_core, anim_core, anim_pipeline
from maya_tools.utils.maya import rig_binding


# ─────────────────────────────────────────────────────────────────────────────
# CRUD orchestrators
# ─────────────────────────────────────────────────────────────────────────────

def add_clip_from_range() -> str:
    """Create a new clip prefilled with playback range + a default rig binding.

    Rig defaults to:
      1. The binding(s) resolved from the current viewport selection (joint clicks),
      2. otherwise the first live FAB_RigBinding in the scene (single-rig case),
      3. otherwise empty (the user picks one in the detail dialog).
    """
    scene = cmds.file(q=True, sn=True)
    if not scene:
        raise RuntimeError('Scene must be saved before adding clips.')

    config = export_core.load_project_config_or_none(scene)
    prefix = export_core.get_anim_prefix(config or {})
    start  = int(cmds.playbackOptions(q=True, min=True))
    end    = int(cmds.playbackOptions(q=True, max=True))

    bindings = bindings_from_selection()
    if not bindings:
        live = rig_binding.find_live_bindings()
        if live:
            bindings = live[:1]

    # Sync registry source_scene_stem if not set
    if anim_core.get_registry() is None or not anim_core.get_source_scene_stem():
        anim_core.set_source_scene_stem(Path(scene).stem)

    return anim_core.create_clip(
        name='',
        prefix=prefix,
        start=start,
        end=end,
        output_kind=anim_core.KIND_GAMEPLAY,
        rig_binding_nodes=bindings,
    )


def duplicate_clip(clip_node: str) -> str:
    """Copy a clip and its rigs[] connections. New name = <original>_2 auto-incrementing."""
    src = anim_core.get_clip_data(clip_node)
    new_name = _unique_clip_name(f"{src['name']}_2" if src['name'] else 'unnamed_2')
    new_node = anim_core.create_clip(
        name=new_name,
        prefix=src['prefix'],
        start=src['frame_start'],
        end=src['frame_end'],
        output_kind=src['output_kind'],
        rig_binding_nodes=src['rigs'],
    )
    if src['dest_override']:
        anim_core.set_clip_dest_override(new_node, src['dest_override'])
    if not src['enabled']:
        anim_core.set_clip_enabled(new_node, False)
    if src.get('root_motion_enabled'):
        anim_core.set_clip_root_motion(new_node, True, src.get('root_motion_axes', 'xz'))
    for rig_idx, source_ctrl in (src.get('root_motion_sources') or {}).items():
        anim_core.set_clip_root_motion_source(new_node, rig_idx, source_ctrl)
    return new_node


def delete_clip(clip_node: str) -> None:
    anim_core.delete_clip(clip_node)


def needs_fix_paths() -> tuple[bool, str, str]:
    """(needs_fix, source_stem, current_stem)."""
    scene   = cmds.file(q=True, sn=True)
    current = Path(scene).stem if scene else ''
    source  = anim_core.get_source_scene_stem()
    return (bool(source) and bool(current) and source != current, source, current)


def fix_paths() -> dict:
    """Replace old scene stem with current in every clip's name; clear dest overrides."""
    needs, old_stem, new_stem = needs_fix_paths()
    if not needs:
        return {'renamed': [], 'cleared_overrides': []}

    report = {'renamed': [], 'cleared_overrides': []}
    for clip_node in anim_core.list_clips():
        data     = anim_core.get_clip_data(clip_node)
        old_name = data['name']
        new_name = old_name.replace(old_stem, new_stem) if old_stem in old_name else old_name
        if new_name != old_name:
            anim_core.set_clip_name(clip_node, new_name)
            report['renamed'].append((old_name, new_name))
        if data['dest_override']:
            anim_core.set_clip_dest_override(clip_node, '')
            report['cleared_overrides'].append(new_name)

    anim_core.set_source_scene_stem(new_stem)
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def bindings_from_selection() -> list[str]:
    """Look up FAB_RigBinding for every joint in the current selection.
    Walks each selected node up to the first joint with a binding.
    """
    sel = cmds.ls(sl=True, long=True) or []
    seen: list[str] = []
    for node in sel:
        joint = node if cmds.nodeType(node) == 'joint' else None
        if joint is None:
            continue
        # Walk parents up the chain looking for a binding
        cur = joint
        while cur:
            binding = rig_binding.find_binding_for_root(cur)
            if binding and binding not in seen:
                seen.append(binding)
                break
            parents = cmds.listRelatives(cur, parent=True, fullPath=True) or []
            cur = parents[0] if parents and cmds.nodeType(parents[0]) == 'joint' else None
    return seen


def _unique_clip_name(base: str) -> str:
    existing = {anim_core.get_clip_data(n)['name'] for n in anim_core.list_clips()}
    if base not in existing:
        return base
    m = re.match(r'^(.*?)_(\d+)$', base)
    stem = m.group(1) if m else base
    i = 2
    while f'{stem}_{i}' in existing:
        i += 1
    return f'{stem}_{i}'


# ─────────────────────────────────────────────────────────────────────────────
# Export dispatch
# ─────────────────────────────────────────────────────────────────────────────

class UnsavedChangesError(Exception):
    """Raised by export_clip when scene has unsaved changes and the caller
    needs to prompt the user. UI catches this and shows a Save/Cancel dialog.
    """


class ExportCancelled(Exception):
    """Raised when the user cancels the export at the unsaved-changes prompt.

    Distinct from a real failure — the UI logs this as info, not error.
    """


def export_clip(clip_node: str, *, save_callback=None, log=None,
                dest_override: str = '',
                engine_up_axis_override: str = '') -> str:
    """Export one clip. Returns the FBX path written.

    Parameters
    ----------
    clip_node : the AM_AnimClip network node to export
    save_callback : optional callable taking no args, returns True if scene
        should be saved + export should continue, False to cancel. If None
        and the scene has unsaved changes, raises UnsavedChangesError. Defaults
        to None — UI provides the callback; headless callers pre-save.
    log : optional object with .info/.warning/.error/.success methods. If
        None, falls back to cmds.warning / om.MGlobal.displayInfo.
    dest_override : optional UI-level output directory. When non-empty,
        overrides BOTH the per-clip ``dest_override`` field and the
        project-config auto-resolved destination, sending every clip
        through this caller-chosen path. UI uses this for the "Output
        Directory" text box at the top of the exporter.
    engine_up_axis_override : '' follows the project config ('y' when no
        config); 'y'/'z' forces the bone-space target (Export Options).
        The anim's skeleton must match how the SK_ mesh was exported or
        the clip plays rotated in engine — leave both on the project
        default unless deliberately diverging.
    """
    log = log or _NullLog()

    data = anim_core.get_clip_data(clip_node)
    scene = cmds.file(q=True, sn=True)
    if not scene:
        raise RuntimeError(f"Clip {data['name']!r}: scene must be saved before exporting.")

    config = export_core.load_project_config_or_none(scene)
    blocks, warnings = anim_core.validate_clip(data, config)
    for w in warnings:
        log.warning(w)
    if blocks:
        msg = '; '.join(blocks)
        log.error(msg)
        raise RuntimeError(msg)

    # Resolve out_path. Caller-level dest_override wins over per-clip
    # dest_override, then the scene's saved Output Dir, then project-config
    # resolution. No project config is required to export.
    if dest_override:
        out_dir = Path(dest_override)
    elif data['dest_override']:
        out_dir = Path(data['dest_override'])
    elif anim_core.get_output_dir_override():
        out_dir = Path(anim_core.get_output_dir_override())
    elif config:
        out_dir = export_core.resolve_anim_destination(scene, data, config)
    else:
        raise RuntimeError(
            f"Clip {data['name']!r}: scene is outside any project and has "
            f"no saved Output Dir. Set Output Dir in the Anim Exporter "
            f"(type or Browse a folder — it saves with the scene), or "
            f"register a project in Project Setup for automatic paths."
        )
    file_stem = anim_core.get_clip_file_stem(data)
    out_path  = str(out_dir / f'{file_stem}.fbx')

    # Unsaved-changes prompt
    if cmds.file(q=True, modified=True):
        if save_callback is None:
            raise UnsavedChangesError('Scene has unsaved changes.')
        if not save_callback():
            log.warning(f"Clip {data['name']!r}: export cancelled by user.")
            raise ExportCancelled(f"Clip {data['name']!r}: cancelled — unsaved changes.")

    log.info(f"Started export of {file_stem}")
    # Resolve the engine bone-space target here (UI override > project
    # config > 'y') and stamp it into the payload — the runner is
    # config-agnostic about the precedence.
    data['engine_up_axis'] = export_core.engine_up_axis(
        config, engine_up_axis_override)
    written = anim_pipeline.run_export(data, out_path, config)
    log.success(f"Exported: {written}")
    return written


def export_clips(
    clip_nodes: list[str],
    *,
    save_callback=None,
    log=None,
    progress_callback=None,
    cancel_check=None,
    dest_override: str = '',
    engine_up_axis_override: str = '',
) -> list[str]:
    """Export the given clip nodes in order. Returns FBX paths written.

    Continues on per-clip failures, logging each error. Aborts entirely only
    if the user cancels at the unsaved-changes prompt (ExportCancelled) or
    the caller didn't provide a save_callback when one was needed
    (UnsavedChangesError).

    progress_callback : optional callable(idx, total, label). Called before
        each clip starts (idx is 1-based). When passed
        ExportProgressDialog.on_clip_started, this also calls
        QApplication.processEvents(), letting any ESC keypress buffered
        during the previous clip fire the dialog's reject().
    cancel_check : optional callable() -> bool. Polled after
        progress_callback every iteration. If True, log
        "Cancelled. {i-1} of {n} written." and break out of the loop.
    dest_override : optional UI-level output directory applied to every
        clip in this run. See export_clip for precedence.
    """
    log = log or _NullLog()
    written: list[str] = []
    total = len(clip_nodes)
    for i, node in enumerate(clip_nodes, start=1):
        try:
            label = anim_core.get_clip_data(node)['name'] or '(unnamed)'
        except Exception:
            label = node
        if progress_callback is not None:
            progress_callback(i, total, label)
        if cancel_check is not None and cancel_check():
            log.info(f'Cancelled. {i - 1} of {total} written.')
            break
        try:
            written.append(export_clip(
                node, save_callback=save_callback, log=log,
                dest_override=dest_override,
                engine_up_axis_override=engine_up_axis_override,
            ))
        except (UnsavedChangesError, ExportCancelled):
            raise
        except Exception as exc:
            log.error(f"Skipped {label}: {exc}")
    return written


def export_all_clips(*, save_callback=None, log=None,
                     progress_callback=None, cancel_check=None,
                     dest_override: str = '') -> list[str]:
    return export_clips(
        anim_core.list_clips(),
        save_callback=save_callback, log=log,
        progress_callback=progress_callback, cancel_check=cancel_check,
        dest_override=dest_override,
    )


def export_checked_clips(*, save_callback=None, log=None,
                         progress_callback=None, cancel_check=None,
                         dest_override: str = '') -> list[str]:
    enabled = [n for n in anim_core.list_clips()
               if anim_core.get_clip_data(n)['enabled']]
    return export_clips(
        enabled,
        save_callback=save_callback, log=log,
        progress_callback=progress_callback, cancel_check=cancel_check,
        dest_override=dest_override,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Null logger for headless calls
# ─────────────────────────────────────────────────────────────────────────────

class _NullLog:
    def info(self, msg: str) -> None:
        om.MGlobal.displayInfo(msg)

    def warning(self, msg: str) -> None:
        om.MGlobal.displayWarning(msg)

    def error(self, msg: str) -> None:
        om.MGlobal.displayError(msg)

    def success(self, msg: str) -> None:
        om.MGlobal.displayInfo(msg)
