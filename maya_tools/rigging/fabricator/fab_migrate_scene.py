# Python/maya_tools/rigging/fabricator/fab_migrate_scene.py
"""KinematicSolutions -> FabricatorStudio rebrand sweep (S2, version.py
1.3.0): standalone, headless-callable migration that upgrades an OPEN
legacy scene's node/attr identifiers IN PLACE.

Every scene-persisted identifier the Fabricator/exporter packages create
or query moved from the ksfab_ / ks_*_grp prefixes (and the sibling
AM_ / am_ export-contract prefixes) to fab_ / FAB_ — see version.py's
1.3.0 changelog entry for the full rationale, including why this is a
STANDALONE contract rather than a load-time version-gate hook (the
existing gate reads the registry via nodes.get_registry(), which itself
now looks for the NEW fab_registry marker — on a not-yet-migrated scene
that lookup finds nothing, so the gate can never fire to trigger its own
prerequisite).

Call migrate_scene() once against an OPEN scene (any point after file
open, before relying on nodes.py / limb_node.py / rig_binding.py /
export_core.py / anim_core.py to find anything). Idempotent — a second
call on an already-migrated (or never-legacy) scene finds nothing to do
and returns an all-empty report.

What it renames:
  * Node NAMES: the fab_registry node, every fab_<id> component node,
    every fab_limb_<name> node, fab_pivots_grp, fab_armature_grp, the
    fab_selection_sets master objectSet (all via the generic ksfab_ ->
    fab_ node-name sweep), plus the DAG groups fab_controls_grp /
    fab_nulls_grp (no ksfab_ prefix, matched by exact old name), plus
    the AM_RigBinding_* / AM_ExporterRegistry / AM_Export_* /
    AM_AnimRegistry / AM_AnimClip_* export-contract nodes.
  * Attr NAMES: every user-defined attr on every node whose name starts
    with 'ksfab_' (marker attrs AND ctrl tag attrs fab_role / fab_owner
    / fab_joint_index / fab_bind_matrix alike — one generic sweep
    covers both), plus the five AM_ family marker attrs (am_rig_binding,
    am_exporter_registry, am_export_entry, am_anim_registry,
    am_anim_clip) via an exact-name list ('am_' is too short/generic a
    prefix to blanket-sweep safely — a hand-authored 'amount' or
    similar custom attr must never be touched).

Deliberately UNTOUCHED (see nodes.py's "Guides group (LEGACY)" section
and the ksfab_ -> fab_ rename plan's own D2 exception): the retired
pre-Armature ksfab_guides_grp node and its ksfab_guides_marker attr.
Nothing has created that group since 2026-07-04; it exists in scene
data only as a stale leftover that nodes.get_guides_grp/delete_guides_grp
hunt for BY THE OLD NAME ON PURPOSE. Renaming it here would break that
cleanup path for the exact old scenes it's meant to serve.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds


# ─────────────────────────────────────────────────────────────────────────────
# Constants — mirrors the D1/D2/D3 approved rename map (KS -> FS rebrand S2)
# ─────────────────────────────────────────────────────────────────────────────

# Old names intentionally excluded from the generic ksfab_ sweep below —
# the dead pre-Armature guide-layer leftover. See module docstring.
_LEGACY_UNTOUCHED_NODE_NAMES = frozenset({'ksfab_guides_grp'})
_LEGACY_UNTOUCHED_ATTR_NAMES = frozenset({'ksfab_guides_marker'})

# Exact old node name -> new node name (no ksfab_/AM_ prefix relationship,
# or a prefix relationship too generic to safely wildcard-match).
_EXACT_NODE_RENAMES = {
    'ks_controls_grp':     'fab_controls_grp',
    'ks_nulls_grp':        'fab_nulls_grp',
    'AM_ExporterRegistry': 'FAB_ExporterRegistry',
    'AM_AnimRegistry':     'FAB_AnimRegistry',
}

# Old node-name PREFIX -> new node-name prefix. Applied to every node
# whose current short name starts with the old prefix (after excluding
# _LEGACY_UNTOUCHED_NODE_NAMES). Order doesn't matter — the prefixes are
# mutually exclusive by construction.
_PREFIX_NODE_RENAMES = (
    ('ksfab_',        'fab_'),        # registry, fab_<id>, fab_limb_*,
                                       # fab_pivots_grp, fab_armature_grp,
                                       # fab_selection_sets — one generic
                                       # sweep, since node NAME is cosmetic
                                       # everywhere it's used (message
                                       # connections carry real identity).
    ('AM_RigBinding_', 'FAB_RigBinding_'),
    ('AM_Export_',     'FAB_Export_'),
    ('AM_AnimClip_',   'FAB_AnimClip_'),
)

# Exact old attr name -> new attr name for the AM_ family markers. 'am_' is
# too short/generic a prefix to blanket-sweep (risk of touching an
# unrelated hand-authored custom attr) — enumerate the five real ones.
_EXACT_ATTR_RENAMES = {
    'am_rig_binding':        'fab_rig_binding',
    'am_exporter_registry':  'fab_exporter_registry',
    'am_export_entry':       'fab_export_entry',
    'am_anim_registry':      'fab_anim_registry',
    'am_anim_clip':          'fab_anim_clip',
}

# Old attr-name PREFIX -> new attr-name prefix, for the generic sweep.
# 'ksfab_' is a distinctive branded prefix — safe to blanket-match across
# every user-defined attr in the scene (marker attrs AND ctrl tags alike:
# ksfab_role/owner/joint_index/bind_matrix, ksfab_extra_guide_owner/name,
# ksfab_joint_name, ksfab_display_name, ksfab_rotate_order, ksfab_radius,
# ksfab_orient_convention, ksfab_link_group, ksfab_registry, ksfab_component,
# ksfab_limb, ksfab_pivots_marker — one mechanism, no per-attr special-casing).
_ATTR_PREFIX_RENAME = ('ksfab_', 'fab_')


def migrate_scene() -> dict:
    """Upgrade the OPEN scene's Fabricator/exporter identifiers from the
    ksfab_ / ks_*_grp / AM_ prefixes to fab_ / FAB_, in place.

    Never raises: a per-item failure is caught, logged into the report's
    'errors' list, and the sweep continues — one bad node must not abort
    the rest of an otherwise-successful migration.

    Returns a report dict:
        {
            'nodes_renamed': [(old_name, new_name), ...],
            'attrs_renamed': [(node_new_name, old_attr, new_attr), ...],
            'errors': [str, ...],
        }
    Idempotent: running this twice in a row on the same scene yields an
    empty report the second time (nothing left matching an old name).
    """
    report = {'nodes_renamed': [], 'attrs_renamed': [], 'errors': []}

    _migrate_node_names(report)
    _migrate_attr_names(report)

    n_nodes = len(report['nodes_renamed'])
    n_attrs = len(report['attrs_renamed'])
    n_errors = len(report['errors'])
    if n_nodes or n_attrs:
        cmds.warning(
            f'[Fabricator] fab_migrate_scene: renamed {n_nodes} node(s) '
            f'and {n_attrs} attr(s) from KinematicSolutions-era names to '
            f'FabricatorStudio names.'
            + (f' {n_errors} error(s) — see below.' if n_errors else '')
        )
        for old, new in report['nodes_renamed']:
            cmds.warning(f'[Fabricator]   node: {old!r} -> {new!r}')
        for node, old_attr, new_attr in report['attrs_renamed']:
            cmds.warning(f'[Fabricator]   attr: {node}.{old_attr!r} -> {new_attr!r}')
    else:
        cmds.warning('[Fabricator] fab_migrate_scene: nothing to migrate '
                     '(scene already current, or was never legacy).')
    for err in report['errors']:
        cmds.warning(f'[Fabricator] fab_migrate_scene ERROR: {err}')

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Node-name migration
# ─────────────────────────────────────────────────────────────────────────────

def _migrate_node_names(report: dict) -> None:
    # Exact-match renames (no safe prefix relationship).
    for old_name, new_name in _EXACT_NODE_RENAMES.items():
        if old_name in _LEGACY_UNTOUCHED_NODE_NAMES:
            continue
        _rename_node_if_present(old_name, new_name, report)

    # Prefix-match renames.
    for old_prefix, new_prefix in _PREFIX_NODE_RENAMES:
        for old_name in _find_nodes_by_prefix(old_prefix):
            if old_name in _LEGACY_UNTOUCHED_NODE_NAMES:
                continue
            suffix = old_name[len(old_prefix):]
            new_name = f'{new_prefix}{suffix}'
            _rename_node_if_present(old_name, new_name, report)


def _find_nodes_by_prefix(prefix: str) -> list:
    """All node short names in the scene starting with `prefix`, matched
    via a wildcard cmds.ls (works across DAG + non-DAG node types alike:
    transforms, joints, network nodes, objectSets)."""
    try:
        found = cmds.ls(f'{prefix}*') or []
    except Exception:
        found = []
    # cmds.ls may return long/ambiguous paths for DAG nodes with duplicate
    # short names elsewhere in the hierarchy; keep only the short name for
    # the prefix/suffix arithmetic above, resolved back to a real object
    # via _rename_node_if_present's own cmds.objExists guard.
    return sorted({n.split('|')[-1] for n in found})


def _rename_node_if_present(old_name: str, new_name: str, report: dict) -> None:
    if not cmds.objExists(old_name):
        return
    if cmds.objExists(new_name):
        report['errors'].append(
            f'Cannot rename node {old_name!r} -> {new_name!r}: a node '
            f'already exists at {new_name!r}. Left {old_name!r} untouched.')
        return
    try:
        actual_new = cmds.rename(old_name, new_name)
        report['nodes_renamed'].append((old_name, actual_new))
    except Exception as exc:
        report['errors'].append(
            f'Failed to rename node {old_name!r} -> {new_name!r}: {exc}')


# ─────────────────────────────────────────────────────────────────────────────
# Attr-name migration
# ─────────────────────────────────────────────────────────────────────────────

def _migrate_attr_names(report: dict) -> None:
    """Sweep every node's user-defined attrs for an old name, renaming in
    place. One pass over DAG nodes (transforms/joints — ctrl tags, guide
    tags) plus one over network nodes and objectSets (registry/component/
    limb/pivots markers, exporter/anim registries and entries).
    """
    candidates = []
    try:
        candidates.extend(cmds.ls(dag=True, long=True) or [])
    except Exception as exc:
        report['errors'].append(f'Could not list DAG nodes: {exc}')
    try:
        candidates.extend(cmds.ls(type=('network', 'objectSet'), long=True) or [])
    except Exception as exc:
        report['errors'].append(f'Could not list network/objectSet nodes: {exc}')

    for node in candidates:
        if not cmds.objExists(node):
            continue
        try:
            attrs = cmds.listAttr(node, userDefined=True) or []
        except Exception:
            continue
        for old_attr in attrs:
            new_attr = _resolve_new_attr_name(old_attr)
            if new_attr is None:
                continue
            _rename_attr_if_present(node, old_attr, new_attr, report)


def _resolve_new_attr_name(old_attr: str) -> str | None:
    """Return the new name for `old_attr`, or None if it isn't one of the
    renamed identifiers (or is a deliberately untouched legacy shim)."""
    if old_attr in _LEGACY_UNTOUCHED_ATTR_NAMES:
        return None
    if old_attr in _EXACT_ATTR_RENAMES:
        return _EXACT_ATTR_RENAMES[old_attr]
    old_prefix, new_prefix = _ATTR_PREFIX_RENAME
    if old_attr.startswith(old_prefix):
        return f'{new_prefix}{old_attr[len(old_prefix):]}'
    return None


def _rename_attr_if_present(node: str, old_attr: str, new_attr: str,
                            report: dict) -> None:
    if not cmds.attributeQuery(old_attr, node=node, exists=True):
        return  # already migrated (or renamed away) since listAttr snapshot
    if cmds.attributeQuery(new_attr, node=node, exists=True):
        report['errors'].append(
            f'Cannot rename {node}.{old_attr!r} -> {new_attr!r}: that attr '
            f'already exists on {node}. Left {old_attr!r} untouched.')
        return
    try:
        cmds.renameAttr(f'{node}.{old_attr}', new_attr)
        report['attrs_renamed'].append((node, old_attr, new_attr))
    except Exception as exc:
        report['errors'].append(
            f'Failed to rename attr {node}.{old_attr!r} -> {new_attr!r}: {exc}')
