# maya_tools/animation/pose_library/app.py
"""Pose Studio v1 — headless save/load API.

No Qt imports. Safe to call from headless contexts. UI layer
(pose_library.ui) wires the buttons.

Lifecycle:
- save_pose(name, category) — capture active rig's full pose
- list_poses(rig_label) — return pose entries on disk for a rig
- list_rigs() — return rig labels with pose folders
- load_pose(pose_path, blend, set_filter) — apply pose to active rig
- load_pose_cross_rig(pose_path, target_binding, blend, set_filter) —
  apply pose from one rig to another via address matching
- delete_pose(pose_path) — remove pose JSON + thumbnail
"""
__author__ = "Adrian Melian"

import contextlib
import datetime as _dt
import getpass
import json
from pathlib import Path

import maya.cmds as cmds


@contextlib.contextmanager
def _synchronous_eval():
    """Force DG (synchronous) evaluation for the wrapped block, restoring
    the previous EM mode on exit.

    Parallel EM mode caches per-frame evaluated values; setKeyframe at
    the current time updates the animCurve but the cached display value
    can stay stale until time changes. DG mode evaluates each plug on
    demand so successive reads + the post-loop refresh see fresh values.
    """
    prev = cmds.evaluationManager(q=True, mode=True) or ['parallel']
    cmds.evaluationManager(mode='off')
    try:
        yield
    finally:
        cmds.evaluationManager(mode=prev[0])

from maya_tools.animation.pose_library.address import (
    Address, address_to_ctrl, ctrl_to_address,
)
from maya_tools.export import export_core
from maya_tools.framework.decorators import undo_chunk
from maya_tools.rigging.fabricator import nodes as fab_nodes
from maya_tools.utils.maya import rig_binding


SCHEMA_VERSION = '1.0'
DEFAULT_CATEGORY = 'animation'
CATEGORIES = ('animation', 'bind', 'rest', 'a-pose')


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _active_binding() -> str:
    """Return the binding for the currently-selected rig, or the only
    live binding if there's just one. Raises RuntimeError otherwise.
    """
    bindings = rig_binding.find_live_bindings()
    if not bindings:
        raise RuntimeError(
            'Pose Studio: no FAB_RigBinding found in scene. Build the '
            'rig first (Fabricator → Build Modules).'
        )
    if len(bindings) == 1:
        return bindings[0]
    # Multiple bindings — pick by current selection's owning rig.
    # Use long paths for namespace-resilient matching when the rig is
    # referenced (descendants come back with namespace prefixes).
    sel_long = cmds.ls(cmds.ls(sl=True) or [], long=True) or []
    for binding in bindings:
        controls_grp = rig_binding.get_controls_grp(binding)
        if not controls_grp:
            continue
        descendants = set(cmds.listRelatives(
            controls_grp, allDescendents=True, fullPath=True,
        ) or [])
        if any(s in descendants for s in sel_long):
            return binding
    raise RuntimeError(
        f'Pose Studio: scene has {len(bindings)} rigs; select a ctrl '
        f'on the rig you want to operate on (or use the Characters '
        f'panel to pick explicitly).'
    )


def _walk_rig_ctrls(binding: str) -> list:
    """Return every Fabricator-tagged ctrl under binding.controls_grp.

    Filters to transforms only (shapes never carry fab_role).
    Long DAG paths so attributeQuery resolves correctly when the rig
    is referenced (short names can drop namespace prefixes and become
    unresolvable in the root namespace).
    """
    controls_grp = rig_binding.get_controls_grp(binding)
    if not controls_grp or not cmds.objExists(controls_grp):
        return []
    out = []
    for node in cmds.listRelatives(controls_grp, allDescendents=True,
                                    fullPath=True,
                                    type='transform') or []:
        if cmds.attributeQuery('fab_role', node=node, exists=True):
            out.append(node)
    return out


def _is_pose_writable(plug: str) -> bool:
    """True if `plug` is unconnected OR driven only by animCurve nodes.

    animCurve-driven channels are keyable — pose library captures their
    current evaluated value and applies via setKeyframe at the current
    time. Non-animCurve drivers (constraints, expressions, multMatrix
    outputs) compute the value from elsewhere and can't be overridden,
    so those channels skip.
    """
    sources = cmds.listConnections(plug, source=True, destination=False,
                                    plugs=False) or []
    if not sources:
        return True
    return all(cmds.nodeType(s).startswith('animCurve') for s in sources)


def _capture_ctrl_attrs(ctrl: str) -> dict:
    """Capture TRS + custom keyable user attrs on a ctrl.

    Returns dict of {attr_name: value}. Skips locked attrs and attrs
    driven by non-animCurve sources (constraints, expressions, etc.).
    animCurve-driven attrs ARE captured — their evaluated value at the
    current time is the keyed pose value the animator wants saved.
    """
    out = {}
    standard = ('translateX', 'translateY', 'translateZ',
                'rotateX', 'rotateY', 'rotateZ',
                'scaleX', 'scaleY', 'scaleZ')
    short = {'translateX': 'tx', 'translateY': 'ty', 'translateZ': 'tz',
             'rotateX': 'rx', 'rotateY': 'ry', 'rotateZ': 'rz',
             'scaleX': 'sx', 'scaleY': 'sy', 'scaleZ': 'sz'}
    for attr in standard:
        if not cmds.attributeQuery(attr, node=ctrl, exists=True):
            continue
        if cmds.getAttr(f'{ctrl}.{attr}', lock=True):
            continue
        if not _is_pose_writable(f'{ctrl}.{attr}'):
            continue
        out[short[attr]] = cmds.getAttr(f'{ctrl}.{attr}')

    # Custom keyable user attrs (anything declared via Add Attribute
    # that's not standard TRS).
    custom = cmds.listAttr(ctrl, userDefined=True, keyable=True) or []
    for attr in custom:
        # Skip Fabricator-internal metadata.
        if attr.startswith('fab_'):
            continue
        if cmds.getAttr(f'{ctrl}.{attr}', lock=True):
            continue
        if not _is_pose_writable(f'{ctrl}.{attr}'):
            continue
        try:
            val = cmds.getAttr(f'{ctrl}.{attr}')
        except RuntimeError:
            continue
        if isinstance(val, list) and len(val) == 1 and isinstance(val[0], tuple):
            # compound — skip in v1 (no compound user attrs on KS ctrls today)
            continue
        # Enum attrs: capture by display label, not index (v1 only
        # applies this to attrs named 'space' for now; broader enum
        # support is v2 work).
        if attr == 'space' and cmds.getAttr(
                f'{ctrl}.{attr}', type=True) == 'enum':
            enum_labels = (cmds.attributeQuery(attr, node=ctrl,
                                                listEnum=True) or [''])[0].split(':')
            # Strip Maya's optional '=N' index-assignment syntax per label.
            enum_labels = [lab.split('=')[0] for lab in enum_labels]
            idx = int(val)
            if 0 <= idx < len(enum_labels):
                out[attr] = enum_labels[idx]
            else:
                cmds.warning(
                    f"Pose Studio: 'space' enum on {ctrl!r} has index "
                    f"{idx} but only {len(enum_labels)} labels; skipping."
                )
            continue   # always continue once the 'space' enum branch is taken
        out[attr] = val
    return out


def _capture_fk_equivalent(cnode: str) -> list:
    """For IK components, capture rotation values on the IK chain
    duplicate joints (which encode the FK-equivalent pose). Used at
    cross-rig load when proportions don't match world-matrix paste.

    Returns list of {joint_index, rx, ry, rz} entries. Empty for
    non-IK components (no ik_chain_joints attr or empty multi).
    """
    ik_joints = fab_nodes.get_component_ik_chain_joints(cnode)
    if not ik_joints:
        return []
    out = []
    for i, j in enumerate(ik_joints):
        if not cmds.objExists(j):
            continue
        r = cmds.getAttr(f'{j}.rotate')[0]
        out.append({
            'joint_index': i,
            'rx': r[0], 'ry': r[1], 'rz': r[2],
        })
    return out


def save_pose(name: str, category: str = DEFAULT_CATEGORY,
              binding: str = '',
              _capture_thumbnail: bool = True) -> Path:
    """Capture the active rig's full pose and write it to disk.

    Writes <pose_library_root>/<rig_label>/<name>.pose.json plus a
    sibling .png thumbnail (best-effort; logs a warning on failure).
    Set _capture_thumbnail=False to skip the playblast — used by
    internal snapshot saves in thumbnails.recapture_for_rig.

    Returns the written file path. Raises RuntimeError on no rig /
    bad name / missing project config.
    """
    if not name or not name.strip():
        raise RuntimeError('Pose Studio: pose name is empty.')
    name = name.strip()
    if category not in CATEGORIES:
        raise RuntimeError(
            f'Pose Studio: category {category!r} not in {CATEGORIES}.'
        )

    if not binding:
        binding = _active_binding()
    rig_label = rig_binding.get_rig_label(binding) or 'unnamed_rig'

    # Resolve output path.
    pose_root = export_core.get_pose_library_root()
    rig_folder = pose_root / rig_label
    rig_folder.mkdir(parents=True, exist_ok=True)
    out_path = rig_folder / f'{name}.pose.json'

    # Walk ctrls + collect channel data.
    channels = []
    components_touched_set = set()
    sides_touched = set()
    # Cache per-cnode FK-equivalent (computed once per IK component).
    fk_eq_cache = {}
    for ctrl in _walk_rig_ctrls(binding):
        addr = ctrl_to_address(ctrl)
        if addr is None:
            continue
        attrs = _capture_ctrl_attrs(ctrl)
        if not attrs:
            continue
        entry = {
            'address': addr.to_dict(),
            'attrs': attrs,
        }
        # IK ctrls: include FK-equivalent fallback.
        if addr.ctrl_role in ('ik_end_ctrl',):
            owners = cmds.listConnections(f'{ctrl}.fab_owner',
                                           source=True, destination=False) or []
            if owners:
                cnode = owners[0]
                if cnode not in fk_eq_cache:
                    fk_eq_cache[cnode] = _capture_fk_equivalent(cnode)
                if fk_eq_cache[cnode]:
                    entry['fk_equivalent'] = fk_eq_cache[cnode]
        channels.append(entry)
        components_touched_set.add(
            (addr.component_type, addr.side, addr.role)
        )
        sides_touched.add(addr.side)

    ts = _now_iso()
    data = {
        'schema_version': SCHEMA_VERSION,
        'name': name,
        'category': category,
        'created': ts,
        'modified': ts,
        'author': getpass.getuser() or 'unknown',
        'source_rig': rig_label,
        'components_touched': [
            {'type': t, 'side': s, 'role': r}
            for (t, s, r) in sorted(components_touched_set)
        ],
        'sides_touched': sorted(sides_touched),
        'channels': channels,
    }

    out_path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    # Thumbnail capture (best-effort — won't block save). Skipped when
    # callers (e.g. thumbnails.recapture_for_rig's snapshot) explicitly
    # opt out via _capture_thumbnail=False.
    if _capture_thumbnail:
        try:
            from maya_tools.animation.pose_library import thumbnails
            thumbnails.capture(thumbnails.thumb_path_for(out_path))
        except Exception as exc:
            cmds.warning(
                f'Pose Studio: thumbnail capture failed for {name!r}: {exc}'
            )

    return out_path


def list_rigs() -> list:
    """Return rig_labels with at least one pose on disk under pose_root."""
    pose_root = export_core.get_pose_library_root()
    if not pose_root.is_dir():
        return []
    return sorted(
        p.name for p in pose_root.iterdir()
        if p.is_dir() and not p.name.startswith('_')
        and any(p.glob('*.pose.json'))
    )


def list_poses(rig_label: str) -> list:
    """Return pose JSON paths for a rig, sorted alphabetically by name
    (case-insensitive)."""
    pose_root = export_core.get_pose_library_root()
    rig_folder = pose_root / rig_label
    if not rig_folder.is_dir():
        return []
    return sorted(
        rig_folder.glob('*.pose.json'),
        key=lambda p: p.name.lower(),
    )


def read_pose(pose_path: Path) -> dict:
    """Read a pose JSON. Raises on JSON parse errors / missing file."""
    return json.loads(Path(pose_path).read_text(encoding='utf-8'))


def delete_pose(pose_path) -> None:
    """Delete a pose JSON + sibling .png thumbnail (if present)."""
    p = Path(pose_path)
    if p.exists():
        p.unlink()
    png = p.with_suffix('').with_suffix('.png')
    if png.exists():
        png.unlink()


def _write_pose_value(plug: str, value) -> bool:
    """Write `value` to `plug`. Routes through setKeyframe when the plug
    is animCurve-driven (so the value lands as a key at the current
    time, preserving the rest of the animation), or setAttr otherwise.
    Returns True on success, False on non-animCurve driver (constraint,
    expression, etc.).
    """
    sources = cmds.listConnections(plug, source=True, destination=False,
                                    plugs=False) or []
    if sources:
        if not all(cmds.nodeType(s).startswith('animCurve') for s in sources):
            return False
        cmds.setKeyframe(plug, value=value)
        return True
    cmds.setAttr(plug, value)
    return True


def _apply_ctrl_attrs(ctrl: str, attrs: dict, blend: float = 1.0) -> int:
    """Apply attrs dict to a ctrl with optional blend factor.

    blend=1.0: absolute set. blend<1.0: linear interpolation from
    current value toward target. Skips attrs that don't exist on the
    target ctrl, are locked, or driven by a non-animCurve source.
    animCurve-driven channels land as keys at the current time.

    Returns the count of attrs applied (informational).
    """
    long_map = {
        'tx': 'translateX', 'ty': 'translateY', 'tz': 'translateZ',
        'rx': 'rotateX',    'ry': 'rotateY',    'rz': 'rotateZ',
        'sx': 'scaleX',     'sy': 'scaleY',     'sz': 'scaleZ',
    }
    applied = 0
    for short, value in attrs.items():
        attr = long_map.get(short, short)
        if not cmds.attributeQuery(attr, node=ctrl, exists=True):
            continue
        plug = f'{ctrl}.{attr}'
        if cmds.getAttr(plug, lock=True):
            continue
        # Enum 'space' captured as display label — resolve back to index.
        if (attr == 'space' and cmds.getAttr(plug, type=True) == 'enum'
                and isinstance(value, str)):
            raw_labels = (cmds.attributeQuery(attr, node=ctrl,
                                               listEnum=True) or [''])[0].split(':')
            # Strip Maya's optional '=N' index-assignment syntax per label
            # (must match the save-side strip in _capture_ctrl_attrs).
            labels = [lab.split('=')[0] for lab in raw_labels]
            if value in labels:
                if _write_pose_value(plug, labels.index(value)):
                    applied += 1
            continue
        if not isinstance(value, (int, float)):
            continue
        if blend >= 1.0 - 1e-6:
            if _write_pose_value(plug, value):
                applied += 1
        else:
            try:
                current = cmds.getAttr(plug)
            except RuntimeError:
                continue
            if not isinstance(current, (int, float)):
                continue
            if _write_pose_value(plug, current + (value - current) * blend):
                applied += 1
    return applied


def load_pose(pose_path, blend: float = 1.0,
              set_filter=None, binding: str = '',
              move_world: bool = False,
              world_follows_filter: bool = False) -> dict:
    """Apply a pose JSON to the active (or specified) rig.

    blend: 0-1 interpolation between current pose and target. 1.0 = absolute.
    set_filter: optional callable(Address) -> bool. None = apply all entries.
    binding: target rig binding; defaults to the active rig.
    move_world: if False (default), the World component's master ctrl
                (fab_role='world_ctrl') is excluded from the apply —
                the character stays planted in the scene and only the
                pose-shape is restored. Set True to also move the
                world ctrl to its saved position.
    world_follows_filter: when True and a set_filter is provided, the
                world ctrl is gated by that filter instead of the
                move_world flag. Used by the UI for explicit-selection
                sets (e.g. "Selected Controls") so a user who included
                the world ctrl in their selection gets it applied.

    Returns a summary dict {applied: int, skipped_missing: list,
    skipped_filter: int, skipped_world: int, skipped_other: int}.
    """
    pose_path = Path(pose_path)
    data = read_pose(pose_path)
    if data.get('schema_version', '') != SCHEMA_VERSION:
        cmds.warning(
            f"Pose Studio: schema version mismatch — file is "
            f"{data.get('schema_version', '?')!r}, expected {SCHEMA_VERSION!r}. "
            f"Loading best-effort."
        )

    if not binding:
        binding = _active_binding()

    summary = {
        'applied': 0,
        'skipped_missing': [],
        'skipped_filter': 0,
        'skipped_world': 0,
        'skipped_other': 0,
    }
    # Wrap the whole apply pass in a single undo chunk so an explosion
    # (bad address resolution, joint-mirror surprise, etc.) is one Ctrl+Z
    # away from the pre-load pose. Force synchronous evaluation so
    # animCurve-driven channels are re-evaluated immediately after each
    # setKeyframe — parallel EM's per-frame cache would otherwise leave
    # the viewport stale until the user scrubs.
    with undo_chunk(f'Load Pose: {pose_path.stem}'), _synchronous_eval():
        for entry in data.get('channels', []):
            addr = Address.from_dict(entry.get('address') or {})
            if set_filter is not None and not set_filter(addr):
                summary['skipped_filter'] += 1
                continue
            # World ctrl gate: when move_world=False, skip the master
            # World mover so the character stays in place. Exception:
            # world_follows_filter=True (used by explicit-selection sets)
            # means the set_filter check above already authorized this
            # entry — trust it and apply.
            if (not move_world and addr.ctrl_role == 'world_ctrl'
                    and not (world_follows_filter and set_filter is not None)):
                summary['skipped_world'] += 1
                continue
            # Same-rig resolution: try address match against this binding.
            ctrl = address_to_ctrl(addr, binding)
            if ctrl is None:
                summary['skipped_missing'].append(addr.as_tuple())
                continue
            attrs = entry.get('attrs') or {}
            if not isinstance(attrs, dict):
                summary['skipped_other'] += 1
                continue
            n = _apply_ctrl_attrs(ctrl, attrs, blend=blend)
            if n:
                summary['applied'] += 1

        # Re-set the current time before refresh. Even with EM in DG mode
        # during the apply, restoring parallel mode at context exit can
        # leave Maya's display cache pointing at pre-load values until the
        # next time change. A no-op currentTime edit (set to the value it
        # already is) is the documented idiom for forcing a fresh
        # evaluation at the current frame.
        t = cmds.currentTime(q=True)
        cmds.currentTime(t, edit=True)
        cmds.refresh(force=True)

    if summary['skipped_missing']:
        cmds.warning(
            f"Pose Studio: {len(summary['skipped_missing'])} ctrl(s) on "
            f"the target rig didn't match the pose; first few: "
            f"{summary['skipped_missing'][:3]}"
        )
    return summary


def load_pose_cross_rig(pose_path, target_binding: str = '',
                         blend: float = 1.0, set_filter=None,
                         move_world: bool = False,
                         world_follows_filter: bool = False) -> dict:
    """Apply a pose JSON to a rig DIFFERENT from the one it was saved on.

    Identical to load_pose; the address-resolution path is rig-binding-
    agnostic by design (address_to_ctrl uses cross-rig identity tuples,
    not ctrl names). This wrapper exists so the UI has a distinct
    entry point and so future Pass B mirror logic can hook here
    independently of same-rig load.

    target_binding: required when scene has multiple rigs and you want
    to load Michael's pose onto Capsule. Defaults to the active rig
    binding (which equals same-rig load when source==target).
    move_world: see load_pose. Default False — target character stays
    planted, only the pose-shape transfers.

    Returns the same summary shape as load_pose.
    """
    if not target_binding:
        target_binding = _active_binding()
    return load_pose(pose_path, blend=blend, set_filter=set_filter,
                     binding=target_binding, move_world=move_world,
                     world_follows_filter=world_follows_filter)
