# maya_tools/rigging/fabricator/selection_sets.py
"""Baked per-rig selection sets — real Maya objectSets (Adrian,
2026-07-05: "I would rather it be something directly on the character
rig network itself... so that this data lives on the rig, and then
pose library just reads it").

Build Modules creates one objectSet per membership under a master set
(MASTER_SET), from the built ctrls' own tags (fab_owner → component
type / side / region). The pose library — and anything else — READS
these instead of re-deriving from scene-wide component walks, which
merged maps across rigs and guessed wrong (root landed in
region:spine). Because they are ordinary Maya sets:

  * a REFERENCED rig brings its sets along, namespaced — every rig in
    an anim scene has its own true selection sets by construction;
  * they are visible and selectable in the Outliner like any set;
  * they are exactly as true as the build that made them — rebuild
    the rig, the sets rebuild.

Set inventory (created only when non-empty — a set that selects
nothing is noise; bare region names per Adrian 2026-07-05, no
region_ prefix):
  all_fk_ctrls / all_ik_ctrls        kind via 'fk'/'ik' in the type
  all_left_ctrls / all_right_ctrls / all_center_ctrls
  <region>[_l|_r]_all                per (region, side) present
  <region>[_l|_r]_fk / _ik           only where that kind exists
  bind_joints                        the deform skeleton (JOINTS, for skinning);
                                      every joint under the rig root, root excl.

The World component joins NOTHING (Adrian: "root should not be
selected with region:spine") — the root anchor is not a posing
target; all_controls remains the pose library's whole-rig entry.

Lifecycle: rebuild_selection_sets() at the end of build_modules
(idempotent — prior sets deleted first); delete_selection_sets() at
unbuild (set nodes are not DAG children of rig_grp and would survive
its deletion).
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes

MASTER_SET = 'fab_selection_sets'

_SIDE_SUFFIX = {'lf': '_l', 'rt': '_r'}
_SIDE_SET = {'lf': 'all_left_ctrls', 'rt': 'all_right_ctrls'}


def _rig_helper_joints() -> set:
    """Joints the modules create as TRANSIENT rig helpers — ribbon roll joints,
    IK helpers, reverse-foot, ribbon boards. Each component tracks its generated
    nodes in `*_nodes` cleanup buckets (roll_dg_nodes, ribbon_dg_nodes,
    reverse_foot_nodes, ...) so unbuild can delete them; the bind-joint buckets
    (joints, twist_upper, twist_lower) do NOT carry the `_nodes` suffix, so this
    catches exactly the helpers.

    They MUST be excluded from the bind set: they sit UNDER bind joints (the
    roll joints parent to their segment joint), so a naive root-subtree walk
    sweeps them in — and they are deleted+recreated EVERY build. A mesh skinned
    to one loses its influence connection the moment the node dies and those
    verts collapse to origin (Adrian, 2026-07-13: exactly what exploded Reggie's
    limbs on rebuild)."""
    from maya_tools.utils.maya import network_nodes as nn
    helpers = set()
    for cn in nodes.get_all_component_nodes():
        for bucket in (cmds.listAttr(cn, userDefined=True) or []):
            if not bucket.endswith('_nodes'):
                continue
            for n in (nn.get_message_targets(cn, bucket) or []):
                if cmds.objExists(n) and cmds.nodeType(n) == 'joint':
                    helpers.update(cmds.ls(n, long=True) or [])
    return helpers


def _bind_joints() -> list:
    """Every DEFORM joint of the rig: the joint descendants of the registry root
    (root itself excluded — the origin anchor is never skinned), MINUS the
    transient rig helpers.

    The subtree gives the main chain, the twists, and the fingers. Subtracting
    the `_nodes`-bucket helpers drops the ribbon roll joints and IK helpers that
    live under bind joints but must never be skinned to (Adrian, 2026-07-13 —
    binding to them exploded the limbs on rebuild)."""
    root = nodes.get_registry_root_joint()
    if not root or not cmds.objExists(root):
        return []
    subtree = cmds.listRelatives(root, allDescendents=True, type='joint',
                                 fullPath=True) or []
    helpers = _rig_helper_joints()
    return [j for j in subtree if j not in helpers]


def _tagged_ctrls() -> list:
    """(ctrl, component_node) for every built ctrl in the scene —
    build-time scope is single-rig by construction (one registry;
    referenced rigs never build in-scene).

    Belt-and-braces: fab_owner preferred, ksfab_owner fallback for a rig
    not yet rebuilt/migrated past the 2026-07 rename — remove after v1.
    """
    out = []
    for ctrl in cmds.ls('*', type='transform', long=True) or []:
        owner_attr = nodes.resolve_ctrl_tag_attr(ctrl, 'fab_owner')
        if owner_attr is None:
            continue
        owners = cmds.listConnections(f'{ctrl}.{owner_attr}',
                                      source=True, destination=False) or []
        if owners:
            out.append((ctrl, owners[0]))
    return out


def rebuild_selection_sets() -> int:
    """Delete + re-create the baked sets from the live build.
    Returns the number of sets created."""
    delete_selection_sets()

    memberships = {}   # set name -> [ctrl long names]

    def _add(name, ctrl):
        memberships.setdefault(name, []).append(ctrl)

    for ctrl, cnode in _tagged_ctrls():
        ctype = nodes.get_component_type(cnode) or ''
        if ctype.lower() == 'world':
            continue  # the root anchor poses nothing
        side = nodes.get_component_side(cnode) or ''
        region = nodes.get_component_region(cnode) or ''
        low = ctype.lower()
        is_fk = 'fk' in low
        is_ik = 'ik' in low

        if is_fk:
            _add('all_fk_ctrls', ctrl)
        if is_ik:
            _add('all_ik_ctrls', ctrl)
        _add(_SIDE_SET.get(side, 'all_center_ctrls'), ctrl)
        if region:
            base = f'{region}{_SIDE_SUFFIX.get(side, "")}'
            _add(f'{base}_all', ctrl)
            if is_fk:
                _add(f'{base}_fk', ctrl)
            if is_ik:
                _add(f'{base}_ik', ctrl)

    # bind_joints: the deform skeleton (Adrian, 2026-07-13). Members are JOINTS,
    # not ctrls — the one set here for skinning rather than posing. Rides the
    # same lifecycle: baked under the master, rebuilt each build, gone at
    # unbuild.
    binds = _bind_joints()
    if binds:
        memberships['bind_joints'] = binds

    if not memberships:
        return 0

    children = []
    for name in sorted(memberships):
        children.append(cmds.sets(memberships[name], name=name))
    master = cmds.sets(children, name=MASTER_SET)
    _ = master
    return len(children)


def delete_selection_sets() -> None:
    """Remove the master set and its child sets. Safe no-op when
    nothing is baked."""
    if not cmds.objExists(MASTER_SET):
        return
    children = cmds.sets(MASTER_SET, q=True) or []
    for child in children:
        if cmds.objExists(child) and cmds.nodeType(child) == 'objectSet':
            cmds.delete(child)
    if cmds.objExists(MASTER_SET):
        cmds.delete(MASTER_SET)
