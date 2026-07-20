# Python/maya_tools/export/anim_core.py
"""Anim exporter — network-node CRUD + validation.

Pure data layer. No Maya scene mutation beyond node attribute reads/writes.
No UI. Save-and-reopen pipeline lives in anim_pipeline.py; orchestration
lives in anim_app.py.

Schema:
    FAB_AnimRegistry — one per scene
    FAB_AnimClip_<safe_name> — one per clip
    FAB_RigBinding — one per rig (managed by export_core, read here)
"""
__author__ = "Adrian Melian"

import re
from pathlib import Path

import maya.cmds as cmds

from maya_tools.export import export_core
from maya_tools.utils.maya import network_nodes as nn
from maya_tools.utils.maya import rig_binding


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_REGISTRY_MARKER = 'fab_anim_registry'
_REGISTRY_NAME   = 'FAB_AnimRegistry'
_CLIP_MARKER     = 'fab_anim_clip'
_CLIP_PREFIX     = 'FAB_AnimClip_'
_VERSION         = '1.0'

KIND_GAMEPLAY  = 'gameplay'
KIND_CINEMATIC = 'cinematic'
OUTPUT_KINDS   = (KIND_GAMEPLAY, KIND_CINEMATIC)


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

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
    nn.ensure_message_attr(node, 'clips', multi=True)
    return node


def get_source_scene_stem() -> str:
    reg = get_registry()
    return cmds.getAttr(f'{reg}.source_scene_stem') if reg else ''


def set_source_scene_stem(stem: str) -> None:
    reg = get_or_create_registry()
    cmds.setAttr(f'{reg}.source_scene_stem', stem, type='string')


def get_output_dir_override() -> str:
    """The user's per-scene saved anim output directory. Empty string means
    'use auto' — the UI resolves the project-config anim destination instead."""
    reg = get_registry()
    if not reg or not cmds.attributeQuery('output_dir_override', node=reg,
                                          exists=True):
        return ''
    return cmds.getAttr(f'{reg}.output_dir_override') or ''


def set_output_dir_override(path: str) -> None:
    """Persist the per-scene anim output directory. Empty clears it (back to
    auto); clearing when no registry exists yet is a no-op."""
    path = path or ''
    reg = get_registry()
    if not reg and not path:
        return
    reg = reg or get_or_create_registry()
    nn.ensure_string_attr(reg, 'output_dir_override', default='')
    cmds.setAttr(f'{reg}.output_dir_override', path, type='string')


def list_clips() -> list[str]:
    reg = get_registry()
    if not reg:
        return []
    return nn.get_message_targets(reg, 'clips')


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_node_name(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9_]', '_', name).strip('_') or 'clip'


# ─────────────────────────────────────────────────────────────────────────────
# Clip CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_clip(name: str,
                prefix: str,
                start: int,
                end: int,
                output_kind: str = KIND_GAMEPLAY,
                rig_binding_nodes: list[str] | None = None) -> str:
    """Create an FAB_AnimClip_<name> network node, link to registry, connect rigs."""
    if output_kind not in OUTPUT_KINDS:
        raise RuntimeError(f'Invalid output_kind: {output_kind!r}; expected {OUTPUT_KINDS}')

    reg = get_or_create_registry()
    base = _CLIP_PREFIX + _safe_node_name(name or 'unnamed')
    node_name = base
    i = 2
    while cmds.objExists(node_name):
        node_name = f'{base}_{i}'
        i += 1

    node = nn.create_marked_node(_CLIP_MARKER, node_name, marker_value='clip')
    nn.ensure_string_attr(node, 'clip_name',     default=name)
    nn.ensure_string_attr(node, 'clip_prefix',   default=prefix)
    nn.ensure_string_attr(node, 'output_kind',   default=output_kind)

    # frame_start / frame_end are long attrs — nn doesn't have a long helper,
    # so add them inline. Single-purpose; not worth a new helper.
    if not cmds.attributeQuery('frame_start', node=node, exists=True):
        cmds.addAttr(node, ln='frame_start', at='long')
        cmds.setAttr(f'{node}.frame_start', int(start))
    if not cmds.attributeQuery('frame_end', node=node, exists=True):
        cmds.addAttr(node, ln='frame_end', at='long')
        cmds.setAttr(f'{node}.frame_end', int(end))

    nn.ensure_bool_attr(  node, 'enabled',       default=True)
    nn.ensure_string_attr(node, 'dest_override',  default='')

    nn.ensure_bool_attr(  node, 'root_motion_enabled', default=False)
    nn.ensure_string_attr(node, 'root_motion_axes',    default='xz')
    nn.ensure_message_attr(node, 'root_motion_sources', multi=True)

    nn.ensure_message_attr(node, 'rigs', multi=True)
    nn.ensure_message_attr(node, 'registry')

    nn.connect_message(reg, node, 'registry')
    nn.connect_message_multi(node, reg, 'clips')

    for binding in (rig_binding_nodes or []):
        if cmds.objExists(binding):
            nn.connect_message_multi(binding, node, 'rigs')

    return node


def get_clip_data(node: str) -> dict:
    """Read all clip attrs + resolve rigs[] connections (preserving order)."""
    return {
        'node':          node,
        'name':          cmds.getAttr(f'{node}.clip_name') or '',
        'prefix':        cmds.getAttr(f'{node}.clip_prefix') or '',
        'output_kind':   cmds.getAttr(f'{node}.output_kind') or KIND_GAMEPLAY,
        'frame_start':   int(cmds.getAttr(f'{node}.frame_start')),
        'frame_end':     int(cmds.getAttr(f'{node}.frame_end')),
        'enabled':       bool(cmds.getAttr(f'{node}.enabled')),
        'dest_override': cmds.getAttr(f'{node}.dest_override') or '',
        'root_motion_enabled': bool(cmds.getAttr(f'{node}.root_motion_enabled'))
            if cmds.attributeQuery('root_motion_enabled', node=node, exists=True) else False,
        'root_motion_axes':    (cmds.getAttr(f'{node}.root_motion_axes') or 'xz')
            if cmds.attributeQuery('root_motion_axes', node=node, exists=True) else 'xz',
        'root_motion_sources': get_root_motion_sources(node),
        'rigs':          _ordered_rigs_connections(node),
    }


def _ordered_rigs_connections(node: str) -> list[str]:
    """Return rigs[] connections preserving slot index order."""
    indices = cmds.getAttr(f'{node}.rigs', multiIndices=True) or []
    out = []
    for i in sorted(indices):
        plug = f'{node}.rigs[{i}]'
        srcs = cmds.listConnections(plug, source=True, destination=False) or []
        if srcs:
            out.append(srcs[0])
    return out


def set_clip_name(node: str, name: str) -> None:
    cmds.setAttr(f'{node}.clip_name', name, type='string')


def set_clip_prefix(node: str, prefix: str) -> None:
    cmds.setAttr(f'{node}.clip_prefix', prefix, type='string')


def set_clip_enabled(node: str, enabled: bool) -> None:
    cmds.setAttr(f'{node}.enabled', bool(enabled))


def set_clip_range(node: str, start: int, end: int) -> None:
    cmds.setAttr(f'{node}.frame_start', int(start))
    cmds.setAttr(f'{node}.frame_end', int(end))


def set_clip_kind(node: str, output_kind: str) -> None:
    if output_kind not in OUTPUT_KINDS:
        raise RuntimeError(f'Invalid output_kind: {output_kind!r}; expected {OUTPUT_KINDS}')
    cmds.setAttr(f'{node}.output_kind', output_kind, type='string')


def set_clip_dest_override(node: str, dest: str) -> None:
    cmds.setAttr(f'{node}.dest_override', dest or '', type='string')


def set_clip_rigs(node: str, binding_nodes: list[str]) -> None:
    """Replace rigs[] connections with the given list, preserving order.

    Also clears root_motion_sources[] — sources are rig-specific, and after a
    rig change the old sources reference ctrls on a different rig. Re-pick
    sources after reassigning rigs.
    """
    nn.replace_message_multi(node, 'rigs', binding_nodes)
    if cmds.attributeQuery('root_motion_sources', node=node, exists=True):
        nn.replace_message_multi(node, 'root_motion_sources', [])


def set_clip_root_motion(node: str, enabled: bool, axes: str = 'xz') -> None:
    """Set the per-clip root-motion enable flag + axes spec."""
    _ensure_root_motion_attrs(node)
    cmds.setAttr(f'{node}.root_motion_enabled', bool(enabled))
    cmds.setAttr(f'{node}.root_motion_axes', axes or 'xz', type='string')


def set_clip_root_motion_source(node: str, rig_index: int, source_ctrl: str) -> None:
    """Set the source ctrl for one rig slot (parallel to rigs[]).

    Pass empty source_ctrl to clear the slot. Disconnects any existing source
    on that index before connecting (idempotent).
    """
    _ensure_root_motion_attrs(node)
    plug = f'{node}.root_motion_sources[{rig_index}]'
    existing = cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
    for src in existing:
        try:
            cmds.disconnectAttr(src, plug)
        except Exception:
            pass
    if source_ctrl and cmds.objExists(source_ctrl):
        cmds.connectAttr(f'{source_ctrl}.message', plug, force=True)


def get_root_motion_sources(node: str) -> dict[int, str]:
    """Return {rig_index: source_ctrl_node} for the clip's source connections."""
    if not cmds.attributeQuery('root_motion_sources', node=node, exists=True):
        return {}
    indices = cmds.getAttr(f'{node}.root_motion_sources', multiIndices=True) or []
    out: dict[int, str] = {}
    for i in indices:
        srcs = cmds.listConnections(f'{node}.root_motion_sources[{i}]',
                                    source=True, destination=False) or []
        if srcs:
            out[int(i)] = srcs[0]
    return out


def _ensure_root_motion_attrs(node: str) -> None:
    """Lazy migration for clips created before root motion existed."""
    nn.ensure_bool_attr(  node, 'root_motion_enabled', default=False)
    nn.ensure_string_attr(node, 'root_motion_axes',    default='xz')
    nn.ensure_message_attr(node, 'root_motion_sources', multi=True)


def delete_clip(node: str) -> None:
    if cmds.objExists(node):
        cmds.delete(node)


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def get_clip_file_stem(clip_data: dict) -> str:
    """Compute the FBX file stem for a clip: '<prefix><rig_label>_<clip_name>'.

    The rig label is read from the first binding in clip_data['rigs']. If no
    rig is set (validation blocks this at export time, but it can occur
    transiently during clip authoring), the stem falls back to '<prefix><clip_name>'.
    """
    prefix = clip_data.get('prefix', '')
    name = clip_data.get('name', '')
    rigs = clip_data.get('rigs') or []
    rig_label = ''
    if rigs:
        binding = rigs[0]
        if cmds.objExists(binding):
            bd = rig_binding.get_binding_data(binding)
            rig_label = bd['rig_label'] or ''
    if rig_label:
        return f'{prefix}{rig_label}_{name}'
    return f'{prefix}{name}'


def validate_clip(clip_data: dict, config: dict | None) -> tuple[list[str], list[str]]:
    """Run validation rules against a clip + project config.

    Returns (blocks, warnings). Blocks abort export; warnings are reported
    but the export still runs. config is None for out-of-project scenes —
    config-dependent checks skip.
    """
    blocks:   list[str] = []
    warnings: list[str] = []
    name = clip_data.get('name') or '(unnamed)'

    # ── Scene state ────────────────────────────────────────────────────────
    scene = cmds.file(q=True, sn=True)
    if not scene:
        blocks.append('Scene has never been saved to disk.')

    # ── Output kind / path patterns ────────────────────────────────────────
    # config is None for out-of-project scenes: skip config-shape checks —
    # the destination then comes from an Output Dir override, and export_clip
    # raises its own instruction when none is set.
    output_kind = clip_data.get('output_kind')
    if output_kind == KIND_CINEMATIC and config is not None:
        cinematic_patterns = (config.get('anim_path_patterns') or {}).get(KIND_CINEMATIC) or []
        if not cinematic_patterns:
            blocks.append(
                f"Clip {name!r}: cinematic path not configured for this project. "
                f"Set a Cinematic anim destination on the asset class in "
                f"Mindmeld (hand-edits to anim_path_patterns are regenerated "
                f"away on save)."
            )

    # ── Frame range ────────────────────────────────────────────────────────
    start = int(clip_data.get('frame_start', 0))
    end   = int(clip_data.get('frame_end', 0))
    if end <= start:
        blocks.append(f'Clip {name!r}: frame_end ({end}) must be greater than frame_start ({start}).')
    if start < 1:
        blocks.append(
            f'Clip {name!r}: frame_start ({start}) is less than 1. '
            f'Animation files must start at frame 1 or later.'
        )

    # Outside-of-playback warning (cosmetic only)
    if scene:
        try:
            pb_min = int(cmds.playbackOptions(q=True, min=True))
            pb_max = int(cmds.playbackOptions(q=True, max=True))
            if start < pb_min or end > pb_max:
                warnings.append(
                    f'Clip {name!r}: range {start}-{end} extends outside playback {pb_min}-{pb_max}.'
                )
        except Exception:
            pass

    # ── Rigs ───────────────────────────────────────────────────────────────
    rigs = clip_data.get('rigs') or []
    if not rigs:
        blocks.append(f'Clip {name!r}: no rigs assigned.')

    dead_rigs   = []
    empty_rigs  = []
    dead_joints = {}
    for binding in rigs:
        if not cmds.objExists(binding):
            dead_rigs.append(binding)
            continue
        bdata = rig_binding.get_binding_data(binding)
        joints = bdata['export_joints']
        if not joints:
            empty_rigs.append(bdata['rig_label'] or binding)
        missing = [j for j in joints if not cmds.objExists(j)]
        if missing:
            dead_joints[bdata['rig_label'] or binding] = missing

    if dead_rigs:
        blocks.append(f'Clip {name!r}: rig binding nodes deleted: {dead_rigs}.')
    for rig_label in empty_rigs:
        blocks.append(
            f"Clip {name!r}: rig {rig_label!r} has empty export_joints[]. "
            f"Re-run SK_ export."
        )
    for rig_label, missing in dead_joints.items():
        blocks.append(
            f"Clip {name!r}: rig {rig_label!r} references {len(missing)} missing joint(s): "
            f"{', '.join(missing[:3])}{' …' if len(missing) > 3 else ''}"
        )

    # Cinematic-with-single-rig (warning, not block)
    if output_kind == KIND_CINEMATIC and len(rigs) == 1:
        warnings.append(
            f'Clip {name!r}: cinematic kind with only one rig — usually means gameplay. '
            f'Switch output_kind to gameplay if intended.'
        )

    # Overlapping export_joints across multiple bindings (warning)
    if len(rigs) > 1:
        seen_joints: dict[str, str] = {}
        overlap = False
        for binding in rigs:
            if not cmds.objExists(binding):
                continue
            for j in rig_binding.get_binding_data(binding)['export_joints']:
                short = j.split('|')[-1].split(':')[-1]
                if short in seen_joints and seen_joints[short] != binding:
                    overlap = True
                seen_joints[short] = binding
        if overlap:
            warnings.append(
                f'Clip {name!r}: bindings share joint short-names; '
                f'FBX may have name collisions.'
            )

    # ── Filename validity / collision ─────────────────────────────────────
    file_stem  = get_clip_file_stem(clip_data)
    if not file_stem.strip():
        blocks.append(f'Clip {name!r}: prefix + clip_name produces an empty filename.')
    elif any(c in file_stem for c in '/\\:*?"<>|'):
        blocks.append(f'Clip {name!r}: filename contains invalid characters: {file_stem!r}')

    # Collision against other clips (regardless of enabled state)
    self_node = clip_data.get('node')
    for other_node in list_clips():
        if other_node == self_node:
            continue
        try:
            other = get_clip_data(other_node)
        except Exception:
            continue
        other_stem = get_clip_file_stem(other)
        if other_stem == file_stem:
            blocks.append(
                f'Clip {name!r}: filename {file_stem!r} collides with another clip '
                f'({other.get("name","?")!r}). File overwrite risk.'
            )
            break

    # ── Destination resolvability ─────────────────────────────────────────
    if scene and config:
        try:
            out_dir = export_core.resolve_anim_destination(scene, clip_data, config)
            if not export_core.drive_exists(out_dir):
                blocks.append(f'Clip {name!r}: target drive does not exist: {out_dir}')
        except RuntimeError as exc:
            blocks.append(f'Clip {name!r}: {exc}')

    # ── Root motion ────────────────────────────────────────────────────────
    if clip_data.get('root_motion_enabled'):
        from maya_tools.export import anim_root_motion
        sources = clip_data.get('root_motion_sources') or {}
        if not sources:
            warnings.append(
                f'Clip {name!r}: root motion enabled but no source ctrls set; '
                f'export will leave the world ctrl at origin.'
            )
        for rig_index, source_ctrl in sources.items():
            if rig_index < 0 or rig_index >= len(rigs):
                blocks.append(
                    f'Clip {name!r}: root motion source index {rig_index} '
                    f'out of range (rigs[] has {len(rigs)} entries).'
                )
                continue
            binding = rigs[rig_index]
            if not cmds.objExists(binding):
                continue  # already reported by dead_rigs above
            if not cmds.objExists(source_ctrl):
                blocks.append(
                    f'Clip {name!r}: root motion source {source_ctrl!r} does not exist.'
                )
                continue
            world_ctrl = anim_root_motion.find_world_ctrl(binding)
            if not world_ctrl:
                bd = rig_binding.get_binding_data(binding)
                blocks.append(
                    f'Clip {name!r}: rig {bd["rig_label"] or binding!r} has no world ctrl '
                    f"(no transform tagged fab_role='world_ctrl' under controls_grp)."
                )
                continue
            if cmds.ls(source_ctrl, long=True) == cmds.ls(world_ctrl, long=True):
                blocks.append(
                    f'Clip {name!r}: root motion source equals the world ctrl '
                    f'({source_ctrl!r}); pick a different ctrl (typically the hip).'
                )

    # ── Construction history (warning, non-KS only) ───────────────────────
    # KS rigs strip constraints during the pipeline, so any constraint-on-joint
    # at validate-time is normal there. For hand-built rigs the same nodes might
    # signal real issues, so we run the check only when KS is absent.
    if not _ks_rig_present():
        _benign_history = {
            'joint', 'animCurveTL', 'animCurveTA', 'animCurveTU', 'animCurveUU',
            'unitConversion', 'pairBlend', 'character',
            'parentConstraint', 'pointConstraint', 'orientConstraint',
            'scaleConstraint', 'aimConstraint', 'poleVectorConstraint',
            'objectSet', 'displayLayer', 'skinCluster',
        }
        for binding in rigs:
            if not cmds.objExists(binding):
                continue
            bdata = rig_binding.get_binding_data(binding)
            for j in bdata['export_joints']:
                if not cmds.objExists(j):
                    continue
                history = cmds.listHistory(j, pruneDagObjects=True) or []
                if any(cmds.nodeType(h) not in _benign_history for h in history):
                    warnings.append(f'Clip {name!r}: construction history present on {j}.')
                    break

    # ── Output FBX exists (warning) ───────────────────────────────────────
    if scene and config and not blocks:
        try:
            out_dir = export_core.resolve_anim_destination(scene, clip_data, config)
            target = out_dir / f'{file_stem}.fbx'
            if target.exists():
                warnings.append(f'Clip {name!r}: output {target} exists; will be overwritten.')
        except Exception:
            pass

    return blocks, warnings


def _ks_rig_present() -> bool:
    """True if a v2 fab_registry network node exists in the scene."""
    try:
        from maya_tools.rigging.fabricator import nodes
        return bool(nodes.get_registry())
    except Exception:
        return False
