# maya_tools/animation/pose_library/sets.py
"""User-authored and auto-derived pose sets.

A "set" is a callable predicate `f(Address) -> bool`. Since Adrian's
2026-07-05 redesign, sets are SELECTION HELPERS first: clicking one
in Pose Studio selects its ctrls in the viewport
(select_set_controls), and 'Apply to Selected Controls' finishes the
job. The load pipeline still takes a predicate as `set_filter` — the
Apply-to-Selected button feeds it the SELECTED_CONTROLS predicate
(that entry no longer appears in the list; it became the button).

Built-in (auto-derived) sets come from the rig's component metadata
+ KS contracts at runtime. Region sets carry side + kind variants
with BARE names (Adrian, 2026-07-05: no region_ prefix):
<region>[_l|_r]_all always, plus _fk/_ik when the region actually
has FK/IK components (kind matched by 'fk'/'ik' in the component
type); legacy 'region:' keys still parse. User-authored sets are
persisted as component-address tuple lists under
`<pose_root>/_sets/<name>.set.json`.

Public API:
- list_builtin_sets() -> list[str]    — auto-derived set names
- list_user_sets() -> list[str]       — names of *.set.json under _sets/
- builtin_set(name) -> callable       — predicate for an auto set
- user_set(name) -> callable          — predicate for a saved user set
- resolve_set(raw) -> callable        — UI-list text ('user:x' aware)
- select_set_controls(raw, binding)   — the selection helper
- save_user_set(name, addresses)      — write _sets/<name>.set.json
- delete_user_set(name)
"""
__author__ = "Adrian Melian"

import json
from pathlib import Path

import maya.cmds as cmds

from maya_tools.animation.pose_library.address import Address, ctrl_to_address
from maya_tools.export import export_core
from maya_tools.rigging.fabricator import nodes as fab_nodes
from maya_tools.utils.maya import rig_binding


ALL_CONTROLS = 'all_controls'
SELECTED_CONTROLS = 'selected_controls'


def _sets_dir() -> Path:
    return export_core.get_pose_library_root() / '_sets'


_SIDE_SUFFIX = {'lf': '_l', 'rt': '_r'}


def _region_combos() -> dict:
    """(region, side) -> {'fk': bool, 'ik': bool} over built components.
    Side is the component's side tag ('lf'/'rt'/'' for center)."""
    combos = {}
    for cnode in fab_nodes.get_all_component_nodes():
        region = fab_nodes.get_component_region(cnode)
        if not region:
            continue
        side = fab_nodes.get_component_side(cnode) or ''
        ctype = (fab_nodes.get_component_type(cnode) or '').lower()
        d = combos.setdefault((region, side), {'fk': False, 'ik': False})
        if 'fk' in ctype:
            d['fk'] = True
        if 'ik' in ctype:
            d['ik'] = True
    return combos


def list_builtin_sets() -> list:
    """Auto-derived set names available on the current scene.
    SELECTED_CONTROLS is deliberately absent — it became the 'Apply to
    Selected Controls' button (Adrian, 2026-07-05). Region sets carry
    side + kind variants; _fk/_ik appear only where the region has
    FK/IK components (a variant that selects nothing is noise)."""
    out = [ALL_CONTROLS, 'all_left', 'all_right', 'all_center']
    for (region, side), kinds in sorted(_region_combos().items()):
        # Bare region names (Adrian, 2026-07-05: no region_ prefix) —
        # matches the baked objectSet naming.
        base = f'{region}{_SIDE_SUFFIX.get(side, "")}'
        out.append(f'{base}_all')
        if kinds['fk']:
            out.append(f'{base}_fk')
        if kinds['ik']:
            out.append(f'{base}_ik')
    return out


def _parse_region_key(body: str) -> tuple:
    """'arm_l_all' -> ('arm', 'lf', 'all'). Region names may carry
    underscores, so parse suffixes from the right; a bare legacy
    'region:arm' reads as ('arm', '', 'all')."""
    kind = 'all'
    for k in ('_all', '_fk', '_ik'):
        if body.endswith(k):
            kind = k[1:]
            body = body[:-len(k)]
            break
    side = ''
    for suffix, tok in (('_l', 'lf'), ('_r', 'rt')):
        if body.endswith(suffix):
            side = tok
            body = body[:-len(suffix)]
            break
    return body, side, kind


def builtin_set(name: str):
    """Return a predicate callable for an auto-derived set."""
    if name == ALL_CONTROLS:
        return lambda addr: True
    if name == SELECTED_CONTROLS:
        # Snapshot the current Maya selection's ctrl-addresses into the
        # closure so the predicate is stable across the apply pass even
        # if the apply itself nudges the selection.
        selected_tuples = set()
        for ctrl in cmds.ls(sl=True, long=True) or []:
            addr = ctrl_to_address(ctrl)
            if addr is not None:
                selected_tuples.add(addr.as_tuple())
        return lambda addr: addr.as_tuple() in selected_tuples
    if name == 'all_left':
        return lambda addr: addr.side == 'lf'
    if name == 'all_right':
        return lambda addr: addr.side == 'rt'
    if name == 'all_center':
        return lambda addr: addr.side == 'md'
    # Region sets — BARE names since 2026-07-05 ('arm_l_all', no
    # region_ prefix, matching the baked objectSets); the legacy
    # 'region:' prefix still parses. An unknown region simply selects
    # nothing (the UI reports it honestly).
    body = name.split(':', 1)[1] if name.startswith('region:') else name
    region, side, kind = _parse_region_key(body)
    # Build a (type, side, role) -> region map once.
    region_map = {}
    for cnode in fab_nodes.get_all_component_nodes():
        key = (
            fab_nodes.get_component_type(cnode),
            fab_nodes.get_component_side(cnode),
            fab_nodes.get_component_role(cnode),
        )
        region_map[key] = fab_nodes.get_component_region(cnode)

    def _matches(addr, _region=region, _side=side, _kind=kind,
                 _map=region_map):
        # The root anchor never joins region sets (Adrian,
        # 2026-07-05: "root should not be selected with
        # region:spine") — mirrored in the baked sets.
        if (addr.component_type or '').lower() == 'world':
            return False
        if _map.get((addr.component_type, addr.side, addr.role),
                    '') != _region:
            return False
        if _side and addr.side != _side:
            return False
        if _kind != 'all' and _kind not in (
                addr.component_type or '').lower():
            return False
        return True
    return _matches


def resolve_set(raw: str):
    """Predicate for a UI-list entry: 'user:<name>' → user set,
    anything else → builtin."""
    if raw.startswith('user:'):
        return user_set(raw[5:])
    return builtin_set(raw)


# ─── Baked per-rig sets (Adrian, 2026-07-05) ────────────────────────
# Build Modules bakes real Maya objectSets onto the rig
# (fabricator.selection_sets); a referenced rig carries them along
# namespaced, so every rig owns its own true sets. The pose library
# READS these when present and only falls back to runtime derivation
# for rigs built before the bake existed (rebuild to upgrade).

_MASTER_SET = 'fab_selection_sets'   # mirrors fabricator.selection_sets


def _binding_namespace(binding: str) -> str:
    return binding.rsplit(':', 1)[0] if ':' in binding else ''


def _baked_master(binding: str) -> str:
    ns = _binding_namespace(binding)
    name = f'{ns}:{_MASTER_SET}' if ns else _MASTER_SET
    return name if cmds.objExists(name) else ''


def rig_baked_sets(binding: str) -> list:
    """Short (namespace-free) names of the binding's baked sets, or []
    when the rig predates the bake."""
    master = _baked_master(binding)
    if not master:
        return []
    out = []
    for child in cmds.sets(master, q=True) or []:
        if cmds.objExists(child) and cmds.nodeType(child) == 'objectSet':
            out.append(child.rsplit(':', 1)[-1])
    return sorted(out)


def select_set_controls(raw: str, binding: str) -> int:
    """The selection helper (Adrian, 2026-07-05): select the set's
    ctrls on the given live binding in the viewport. Returns the
    match count (0 clears the selection — an honest 'nothing here').

    Resolution order: user sets by prefix; then the binding's BAKED
    objectSet of that name (namespace-scoped — always the right rig);
    else the runtime-derived builtin predicate (pre-bake rigs)."""
    if not binding:
        raise RuntimeError(
            'Selection helpers need a live rig — pick a live character '
            'first.')
    if not raw.startswith('user:'):
        master = _baked_master(binding)
        if master:
            ns = _binding_namespace(binding)
            node = f'{ns}:{raw}' if ns else raw
            if cmds.objExists(node) and cmds.nodeType(node) == 'objectSet':
                members = cmds.sets(node, q=True) or []
                members = [m for m in members if cmds.objExists(m)]
                if members:
                    cmds.select(members, replace=True)
                else:
                    cmds.select(clear=True)
                return len(members)
    pred = resolve_set(raw)
    grp = rig_binding.get_controls_grp(binding)
    if not grp:
        raise RuntimeError(
            f'Binding {binding!r} has no controls group.')
    matches = []
    for ctrl in cmds.listRelatives(grp, allDescendents=True,
                                   type='transform',
                                   fullPath=True) or []:
        addr = ctrl_to_address(ctrl)
        if addr is not None and pred(addr):
            matches.append(ctrl)
    if matches:
        cmds.select(matches, replace=True)
    else:
        cmds.select(clear=True)
    return len(matches)


def list_user_sets() -> list:
    """Names of user-authored sets under <pose_root>/_sets/."""
    d = _sets_dir()
    if not d.is_dir():
        return []
    return sorted(p.name.removesuffix('.set.json') for p in d.glob('*.set.json'))


def user_set(name: str):
    """Return a predicate callable for a user-authored set.

    A user set is a list of (component_type, side, role, ctrl_role,
    joint_index) tuples. The predicate matches an Address against
    any tuple in the list.
    """
    path = _sets_dir() / f'{name}.set.json'
    if not path.is_file():
        raise RuntimeError(f'Pose Studio: user set {name!r} not found.')
    raw = json.loads(path.read_text(encoding='utf-8'))
    tuples = {tuple(t) for t in raw.get('addresses', [])}
    def _matches(addr):
        return addr.as_tuple() in tuples
    return _matches


def save_user_set(name: str, addresses: list) -> Path:
    """Persist a user-authored set under _sets/<name>.set.json.

    addresses: list of Address instances OR (type, side, role,
    ctrl_role, joint_index) tuples.
    """
    if not name or not name.strip():
        raise RuntimeError('Pose Studio: user set name is empty.')
    name = name.strip()
    d = _sets_dir()
    d.mkdir(parents=True, exist_ok=True)
    serialized = []
    for a in addresses:
        if isinstance(a, Address):
            serialized.append(list(a.as_tuple()))
        else:
            serialized.append(list(a))
    payload = {
        'schema_version': '1.0',
        'name': name,
        'addresses': serialized,
    }
    out = d / f'{name}.set.json'
    out.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return out


def delete_user_set(name: str) -> None:
    path = _sets_dir() / f'{name}.set.json'
    if path.exists():
        path.unlink()
