# maya_tools/animation/anim_library/app.py
"""Headless save/load/list/delete API for Animation Studio.

No Qt imports — safe for headless callers. UI layer (ui.py) wires the
buttons. Cross-rig portability via the shared Fabricator address tuples
(re-exported in anim_library.address).

Schema reference: docs/superpowers/specs/2026-05-20-anim-library-design.md
"""
__author__ = "Adrian Melian"

import datetime as _dt
import getpass
import json
from pathlib import Path

import maya.cmds as cmds

from maya_tools.animation.anim_library import keyframes
from maya_tools.animation.anim_library.address import (
    Address, address_to_ctrl, ctrl_to_address,
)
from maya_tools.animation.pose_library import app as pose_app
from maya_tools.export import export_core
from maya_tools.framework.decorators import undo_chunk
from maya_tools.utils.maya import rig_binding


SCHEMA_VERSION = '1.0'
DEFAULT_CATEGORY = 'animation'
CATEGORIES = ('animation',)

MODE_PLACE   = 'place'
MODE_INSERT  = 'insert'
MODE_REPLACE = 'replace'
MODES = (MODE_PLACE, MODE_INSERT, MODE_REPLACE)


# ─── Time-range resolver ────────────────────────────────────────────────────

def resolve_time_range() -> tuple:
    """Return (start, end) as floats.

    Prefers Maya's highlighted time-slider range when the highlight
    spans more than one frame. Falls back to the playback range
    otherwise (highlighted ranges of <=1 frame are treated as "no real
    selection" since a one-frame clip is degenerate for the anim
    library — use Pose Studio for single-frame snapshots).

    Highlighted range is detected via `timeControl rangeVisible`;
    `rangeArray` returns `[start, end+1]` so we subtract 1 from end.
    """
    try:
        ts = 'timeControl1'
        if cmds.timeControl(ts, q=True, rangeVisible=True):
            r = cmds.timeControl(ts, q=True, rangeArray=True) or []
            if len(r) == 2 and (r[1] - r[0]) > 1:
                return (float(r[0]), float(r[1] - 1))
    except Exception:
        pass
    return (float(cmds.playbackOptions(q=True, min=True)),
            float(cmds.playbackOptions(q=True, max=True)))


# ─── Helpers ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _current_fps() -> float:
    unit = cmds.currentUnit(q=True, time=True)
    fps_map = {
        'film': 24.0, 'ntsc': 30.0, 'pal': 25.0, 'ntscf': 60.0,
        'palf': 50.0, '15fps': 15.0, '24fps': 24.0, '25fps': 25.0,
        '30fps': 30.0, '48fps': 48.0, '50fps': 50.0, '60fps': 60.0,
        '120fps': 120.0,
    }
    if unit in fps_map:
        return fps_map[unit]
    # Custom frame rates surface as '23.976fps', '100fps', etc.
    if isinstance(unit, str) and unit.endswith('fps'):
        try:
            return float(unit[:-3])
        except ValueError:
            pass
    return 24.0


# ─── Save ───────────────────────────────────────────────────────────────────

def save_anim(name: str, category: str = DEFAULT_CATEGORY,
              binding: str = '', time_range: tuple = None) -> Path:
    """Capture animation on the active rig and write to disk.

    Returns the path of the written .anim.json. Raises RuntimeError on
    invalid input (empty name, bad category, no binding, bad time range).
    """
    if not name or not name.strip():
        raise RuntimeError('Animation Studio: name is empty.')
    name = name.strip()
    if category not in CATEGORIES:
        raise RuntimeError(
            f'Animation Studio: category {category!r} not in {CATEGORIES}.'
        )

    if not binding:
        binding = pose_app._active_binding()
    rig_label = rig_binding.get_rig_label(binding) or 'unnamed_rig'

    if time_range is None:
        time_range = resolve_time_range()
    start, end = float(time_range[0]), float(time_range[1])
    if start >= end:
        raise RuntimeError(
            f'Animation Studio: invalid time range [{start}, {end}] — '
            f'start must be less than end (use Pose Studio for '
            f'single-frame snapshots).'
        )

    anim_root = export_core.get_anim_library_root()
    rig_folder = anim_root / rig_label
    rig_folder.mkdir(parents=True, exist_ok=True)
    out_path = rig_folder / f'{name}.anim.json'

    channels = []
    components_touched = set()
    sides_touched = set()

    for ctrl in pose_app._walk_rig_ctrls(binding):
        addr = ctrl_to_address(ctrl)
        if addr is None:
            continue
        attrs_data = {}
        for attr in keyframes.list_animated_attrs(ctrl):
            curve = keyframes.capture_attr_curve(ctrl, attr, start, end)
            if curve['keys']:
                attrs_data[attr] = curve
        if not attrs_data:
            continue
        channels.append({
            'address': addr.to_dict(),
            'attrs': attrs_data,
        })
        components_touched.add((addr.component_type, addr.side, addr.role))
        sides_touched.add(addr.side)

    ts = _now_iso()
    data = {
        'schema_version': SCHEMA_VERSION,
        'name': name,
        'category': category,
        'created': ts, 'modified': ts,
        'author': getpass.getuser() or 'unknown',
        'source_rig': rig_label,
        'source_fps': _current_fps(),
        'time_range': {
            'start': start, 'end': end, 'length': end - start,
        },
        'components_touched': [
            {'type': t, 'side': s, 'role': r}
            for (t, s, r) in sorted(components_touched)
        ],
        'sides_touched': sorted(sides_touched),
        'channels': channels,
    }

    out_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return out_path


# ─── List / read / delete ───────────────────────────────────────────────────

def list_rigs() -> list:
    """Rig labels (folders) with at least one anim file."""
    root = export_core.get_anim_library_root()
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and not p.name.startswith('_')
        and any(p.glob('*.anim.json'))
    )


def list_anims(rig_label: str) -> list:
    """Anim JSON paths under a rig, sorted alphabetically by name
    (case-insensitive)."""
    root = export_core.get_anim_library_root()
    rig_folder = root / rig_label
    if not rig_folder.is_dir():
        return []
    return sorted(
        rig_folder.glob('*.anim.json'),
        key=lambda p: p.name.lower(),
    )


def read_anim(path) -> dict:
    """Parse an anim JSON file. Raises on malformed JSON or missing file."""
    return json.loads(Path(path).read_text(encoding='utf-8'))


def delete_anim(path) -> None:
    """Delete an anim file and its sibling .gif / .png."""
    p = Path(path)
    if p.exists():
        p.unlink()
    stem = p.name.removesuffix('.anim.json')
    parent = p.parent
    for ext in ('.gif', '.png'):
        sibling = parent / f'{stem}{ext}'
        if sibling.exists():
            sibling.unlink()


# ─── Load ───────────────────────────────────────────────────────────────────

def load_anim(path, mode: str = MODE_PLACE, *,
              apply_start_anchor: bool = True,
              apply_end_anchor: bool = True,
              apply_root_motion: bool = True,
              set_filter=None,
              binding: str = '') -> dict:
    """Apply an anim file to the active rig at current time.

    mode:
        'place'   — apply curves at current time; existing keys at the
                    same frames are overwritten (Maya default setKeyframe).
                    No shifting, no cutting.
        'insert'  — shift all rig keys at time >= current_time forward by
                    `length`, then place. Used to splice a clip into the
                    middle of an existing animation.
        'replace' — cut existing keys on every (ctrl, attr) the clip
                    touches inside [current_time, current_time + length],
                    then place. Used to overwrite a section.

    apply_start_anchor / apply_end_anchor: drop the synthetic anchor key
        at the clip's start / end frame when False. Default True both.
    apply_root_motion: drop channels with ctrl_role == 'world_ctrl'
        when False. Default True (most anims have intentional root motion).
    set_filter: optional predicate (Address) -> bool. When given, only
        channels whose ctrl passes are applied — the 'Apply to Selected
        Controls' button feeds the viewport-selection predicate. None
        (default) applies every channel.

    Returns a summary dict {applied_channels, skipped_missing}.
    """
    if mode not in MODES:
        raise RuntimeError(f'Animation Studio: invalid mode {mode!r}.')

    path = Path(path)
    data = read_anim(path)
    if not binding:
        binding = pose_app._active_binding()

    current_time = float(cmds.currentTime(q=True))
    tr = data.get('time_range') or {}
    saved_start = float(tr.get('start', 0.0))
    length = float(tr.get('length', 0.0))
    offset = current_time - saved_start
    new_start = current_time
    new_end = current_time + length

    src_fps = float(data.get('source_fps', _current_fps()))
    if abs(src_fps - _current_fps()) > 0.1:
        cmds.warning(
            f'Animation Studio: source fps {src_fps} != scene fps '
            f'{_current_fps()}; loading without retiming.'
        )

    summary = {'applied_channels': 0, 'skipped_missing': [], 'skipped_filter': 0}

    # Resolve all target ctrls up front.
    resolved = []  # list of (target_ctrl_path, addr, attrs_data)
    for entry in data.get('channels') or []:
        addr = Address.from_dict(entry.get('address') or {})
        if not apply_root_motion and addr.ctrl_role == 'world_ctrl':
            continue
        if set_filter is not None and not set_filter(addr):
            summary['skipped_filter'] += 1
            continue          # Apply to Selected: skip ctrls not in the set
        ctrl = address_to_ctrl(addr, binding)
        if ctrl is None:
            summary['skipped_missing'].append(addr.as_tuple())
            continue
        attrs_data = entry.get('attrs') or {}
        if isinstance(attrs_data, dict) and attrs_data:
            resolved.append((ctrl, addr, attrs_data))

    if not resolved:
        cmds.warning(
            f'Animation Studio: no channels resolved to the target rig.'
        )
        return summary

    with undo_chunk(f'Load Anim: {path.stem}'):
        if mode == MODE_INSERT and length > 0:
            # Apply to Selected: scope the shift to the resolved (filtered)
            # ctrls too, so it isn't a whole-rig timeline edit (matches how
            # Replace's _cut_clip_window already receives `resolved`).
            shift_ctrls = ([c for c, _a, _d in resolved]
                           if set_filter is not None else None)
            _shift_rig_keys_forward(binding, current_time, length, shift_ctrls)
        elif mode == MODE_REPLACE and length > 0:
            _cut_clip_window(resolved, current_time, length)

        for ctrl, addr, attrs_data in resolved:
            for attr, attr_data in attrs_data.items():
                n = keyframes.apply_attr_curve(
                    ctrl, attr, attr_data,
                    time_offset=offset,
                    filter_start_anchor=not apply_start_anchor,
                    filter_end_anchor=not apply_end_anchor,
                    start_frame=new_start, end_frame=new_end,
                )
                if n:
                    summary['applied_channels'] += 1

        # Viewport refresh — same pattern Pose Studio uses for setKeyframe loads.
        cmds.currentTime(current_time, edit=True)
        cmds.refresh(force=True)

    if summary['skipped_missing']:
        cmds.warning(
            f'Animation Studio: {len(summary["skipped_missing"])} ctrl(s) on '
            f'the target rig did not match the clip; first few: '
            f'{summary["skipped_missing"][:3]}'
        )
    return summary


def _shift_rig_keys_forward(binding: str, from_time: float,
                            by_frames: float, ctrls=None) -> None:
    """Insert-mode shift pass — shift keys at time >= from_time forward by
    by_frames. When `ctrls` is given (Apply to Selected), shift ONLY those so
    the insert stays scoped to the selection; otherwise every Fabricator-tagged
    ctrl on `binding` (whole-rig insert). Keeps the rig in sync across the
    insertion seam.
    """
    targets = ctrls if ctrls is not None else pose_app._walk_rig_ctrls(binding)
    for ctrl in targets:
        for attr in keyframes.list_animated_attrs(ctrl):
            plug = f'{ctrl}.{attr}'
            try:
                cmds.keyframe(plug, edit=True,
                              time=(from_time, None),
                              relative=True, timeChange=by_frames)
            except RuntimeError:
                continue


def _cut_clip_window(resolved: list, current_time: float,
                     length: float) -> None:
    """Replace-mode cut pass — delete keys in
    [current_time, current_time + length] on every (ctrl, attr) the
    saved clip touches. Channels not in the clip are untouched.
    """
    end_time = current_time + length
    for ctrl, _addr, attrs_data in resolved:
        for attr in attrs_data.keys():
            plug = f'{ctrl}.{attr}'
            try:
                cmds.cutKey(plug, time=(current_time, end_time),
                            option='keys', clear=True)
            except RuntimeError:
                continue
