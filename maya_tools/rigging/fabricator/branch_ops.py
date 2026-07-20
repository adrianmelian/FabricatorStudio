# maya_tools/rigging/fabricator/branch_ops.py
"""Smart branch mirror + duplicate for the Armature era (Adrian's
2026-07-05 design; toolbar Mirror / Duplicate groups).

Both operate on THE SELECTED BRANCH: whatever armature ctrl (or joint)
is selected resolves to a branch root joint; the op covers that joint
plus every descendant.

MIRROR (one-shot — no legacy live-constraint ksMirror network):
  * ALL counterpart joints exist on the far side → modules-only.
  * NONE exist → mirror joints (native mirrorJoint, YZ behavior
    mirror) + rename via side tokens + transfer aimer states through
    the convention flip (rx, -ry+180, -rz — the live-mirror rule) +
    mirror modules.
  * SOME exist → readable error. Adrian: check ALL children, not just
    the root — "people do crazy things in maya".

DUPLICATE (in place; the caller moves it via the Armature after):
  * Whole-subtree variant rename — Maya's duplicate only renames the
    subtree root, leaving every child name colliding; here EVERY joint
    gets the variant tag inserted before its side token
    (clavicle_l + 'bk' → clavicle_bk_l; appended when sideless).
  * Aimer states copy verbatim (child target labels follow the
    rename); modules re-create on the new joints with verbatim
    options, fresh default shapes (persisted CV data does not copy).
  * The new branch root's ctrl is selected on completion so the user
    can start moving immediately.

REPARENT (Adrian's 2026-07-05 design — armature-era joint reparent):
  * Move whole branches under a new parent joint. Two front doors —
    canvas MMB drag-drop and the DAG parent-watcher (ctrl-under-ctrl
    via p / outliner) — both funnel into reparent_branches().
  * Names never change, world transforms never change; only hierarchy.
    jointOrient is collapsed on the moved roots afterward (cmds.parent
    rebases parent rotation into JO — the export contract wants
    rotate-only), and aimers with stale child enums (old parents lose
    a slot, the new parent gains one) rebuild with state preserved.

DELETE (Adrian, 2026-07-05: "if the control was deleted, delete
everything associated with that joint, including the children (and
their modules)"):
  * Whole branches: root joint + subtree, their aimers, and every
    module touching those joints. Front doors: the ctrl-delete DAG
    watcher and the canvas right-click — both funnel into
    delete_branches(). The rig root is guarded (File > Delete Rig owns
    that).

The teardown→surgery→rebuild sandwich is shared: tear the Armature
down first (clean joints — no effectors or locks ride along), rebuild
after, so ctrls / aimers / guide lines / layers cover the new topology
automatically. armature_mirror.preserved() keeps Symmetry alive across
every sandwich.
"""
__author__ = "Adrian Melian"

import re

import maya.cmds as cmds

from maya_tools.rigging.fabricator import (armature, armature_mirror,
                                            modules, nodes)
from maya_tools.rigging.joint_orient import joint_orient_app as joa
from maya_tools.utils.maya import orientation_convention as oc
from maya_tools.utils.maya import side_tokens

# Variant-tag presets for the Duplicate prompt (editable — free text
# rides on top). Positional variants, distinct from the anatomical
# region tags in side_tokens._REGION_TOKENS.
VARIANT_PRESETS = ('up', 'dn', 'fr', 'bk', 'in', 'out')

# Side segments for variant-tag placement (mirrors the side_tokens
# vocabulary; lowercase compare).
_SIDE_SEGS = {'l', 'r', 'lf', 'rt', 'lt', 'left', 'right'}

_CTRL_SUFFIX = '_amt_CTL'


# ─────────────────────────────────────────────
# Selection / branch resolution
# ─────────────────────────────────────────────

def resolve_selected_branch() -> str:
    """Branch root joint from the current selection: an armature ctrl
    maps to its joint; a joint counts as itself. First match wins."""
    for node in cmds.ls(selection=True) or []:
        short = node.split('|')[-1].split(':')[-1]
        if short.endswith(_CTRL_SUFFIX):
            j = short[:-len(_CTRL_SUFFIX)]
            if cmds.objExists(j):
                return j
        elif cmds.objExists(node) and cmds.nodeType(node) == 'joint':
            return short
    raise RuntimeError(
        'Select an armature control (or a joint) to choose the branch '
        'first.')


def _branch_joints(root: str) -> list:
    """root + all descendant joints, short names, parents first."""
    desc = cmds.listRelatives(root, allDescendents=True, type='joint',
                              fullPath=True) or []
    ordered = sorted(desc, key=lambda p: p.count('|'))
    return [root] + [d.split('|')[-1] for d in ordered]


def _parallel_walk(src_root: str, dst_root: str) -> list:
    """[(src short name, dst FULL PATH)] over two topologically
    identical subtrees (duplicate/mirrorJoint preserve child order)."""
    pairs = [(src_root, dst_root)]
    s_kids = cmds.listRelatives(src_root, children=True,
                                type='joint') or []
    d_kids = cmds.listRelatives(dst_root, children=True, type='joint',
                                fullPath=True) or []
    for s, d in zip(s_kids, d_kids):
        pairs += _parallel_walk(s, d)
    return pairs


def _require_edit_mode(op: str) -> None:
    for cnode in nodes.get_all_component_nodes():
        if nodes.is_built(cnode):
            raise RuntimeError(
                f'{op}: rig is built. Edit Rig (unbuild) first — '
                f'editing operations live in Edit Mode only.')


def _wrap180(deg: float) -> float:
    while deg > 180.0:
        deg -= 360.0
    while deg <= -180.0:
        deg += 360.0
    return deg


def _components_in_branch(branch_joints: list) -> list:
    """Component nodes whose joints all live inside the branch."""
    bset = set(branch_joints)
    out = []
    for cnode in nodes.get_all_component_nodes():
        names = [j.split('|')[-1].split(':')[-1]
                 for j in (nodes.get_component_joint_names(cnode)
                           or nodes.get_component_joints(cnode) or [])]
        if names and set(names) <= bset:
            out.append((cnode, names))
    return out


# Multi attrs on fabricator network nodes that enroll a JOINT by its
# .message connection. Copied joints must never inherit these — see
# _release_copy_message_echo.
_ECHO_MULTI_ATTRS = ('joints', 'finger_roots', 'curl_excluded',
                     'twist_upper', 'twist_lower')


def _release_copy_message_echo(new_joints: list) -> int:
    """Disconnect fabricator-membership .message echo from freshly
    copied joints.

    cmds.mirrorJoint and cmds.duplicate copy each source joint's
    OUTGOING .message connections onto the fresh copies, silently
    enrolling the new joints in the SOURCE side's fabricator
    bookkeeping: the source component's joints[] multi (the build-time
    'Duplicated joints' auto-release) and — far worse — the source
    LIMB's finger_roots/curl_excluded/twist_upper/twist_lower multis
    (the 2026-07-11 corruption: after a branch mirror, one limb node
    carried BOTH hands' fingers and every side's twists, Properties
    called the mirrored leg a 10-fingered hand, and Build crashed on
    duplicate finger ctrl names).

    A joint that did not exist a moment ago cannot legitimately be a
    member of ANY pre-existing fabricator node, so every such
    connection is echo — release them all. Scoped tight: only
    connections into fab_* network nodes, only the membership multis
    (+ per-ctrl spaces_* multis). Returns the count released."""
    n = 0
    for j in new_joints:
        if not cmds.objExists(j):
            continue
        plugs = cmds.listConnections(f'{j}.message', source=False,
                                     destination=True, plugs=True) or []
        for plug in plugs:
            dst_node, dst_attr = plug.split('.', 1)
            attr_base = dst_attr.split('[')[0]
            if not (attr_base in _ECHO_MULTI_ATTRS
                    or attr_base.startswith('spaces_')):
                continue
            if (cmds.nodeType(dst_node) != 'network'
                    or not dst_node.startswith('fab_')):
                continue
            try:
                cmds.disconnectAttr(f'{j}.message', plug)
                n += 1
            except RuntimeError:
                pass
    if n:
        cmds.warning(
            f'[Fabricator] Released {n} copied .message connection(s) '
            f'from the new joints (mirror/duplicate echo).')
    return n


# ─────────────────────────────────────────────
# Mirror
# ─────────────────────────────────────────────

def mirror_branch(root: str = None, include_modules: bool = True) -> dict:
    """Smart one-shot branch mirror (see module docstring).

    Returns {'mode': 'full'|'modules_only'|'nothing',
             'joints': int, 'components': int, 'skipped': int}.
    """
    _require_edit_mode('Mirror')
    root = root or resolve_selected_branch()
    joints = _branch_joints(root)

    root_flip = side_tokens.flip_side_token(root)
    if not root_flip or root_flip == root:
        raise RuntimeError(
            f'Mirror: branch root {root!r} has no side token — select '
            f'a sided branch (e.g. clavicle_l).')

    fmap = {j: side_tokens.flip_side_token(j) for j in joints}
    flipped = [f for f in fmap.values() if f]
    existing = [f for f in flipped if cmds.objExists(f)]

    if existing and len(existing) != len(flipped):
        missing = sorted(set(flipped) - set(existing))
        raise RuntimeError(
            f'Mirror: the far side is PARTIALLY built — '
            f'{len(existing)}/{len(flipped)} counterpart joints exist. '
            f'Missing: {", ".join(missing[:8])}'
            f'{"…" if len(missing) > 8 else ""}. Complete or delete '
            f'the far side, then re-run.')

    # preserved(): delete_armature knocks the live mirror off; rebuild
    # inside the same span so Symmetry survives the sandwich (and the
    # fresh far side pairs up on re-enable).
    made = []
    done = skipped = 0
    with armature_mirror.preserved():
        if not existing:
            made = _mirror_branch_joints(root, joints, fmap)

        if include_modules:
            done, skipped = _mirror_branch_modules(joints)

        # Rebuild only when joints were created (the full path tears the
        # Armature down). Modules-only mirrors touch network nodes alone —
        # skipping the rebuild keeps them instant (Adrian, 2026-07-05:
        # the branch mirror felt slow).
        if made:
            armature.build_armature()
            from maya_tools.rigging.fabricator import fs_app
            fs_app.add_joints_to_reference_layer(made)

    mode = ('full' if made else
            'modules_only' if done or skipped else 'nothing')
    return {'mode': mode, 'joints': len(made),
            'components': done, 'skipped': skipped}


def _mirror_branch_joints(root: str, joints: list, fmap: dict) -> list:
    """Native one-shot behavior mirror + token rename + aimer-state
    transfer. Returns the new joints (short names)."""
    # Source aimer states BEFORE teardown (teardown keeps aimers, but
    # capture early is free and safe).
    states = {j: joa.get_aimer_state(j) for j in joints}

    # Clean joints for the copy: no effectors, no locks.
    if armature.armature_exists():
        armature.delete_armature()

    # mirrorBehavior is ALWAYS True, deliberately — it is not the
    # convention switch. Measured 2026-07-19: mirrorBehavior=False does
    # not lay +X down the mirrored bone at all, it emits the raw
    # geometric reflection, whose aimer offsets come out joint-dependent
    # (probe: (180, 28.24, 139.18) on one joint, (0, 256.23, 14.25) on
    # the next). Behavior mode gives clean, predictable frames; the
    # convention is expressed ENTIRELY in the aimer offset, which the
    # orientation bake then writes into the joints.
    conv = oc.resolve(nodes.get_registry() or '')
    created = cmds.mirrorJoint(
        root,
        **{f'mirror{conv.mirror_plane}': True},
        mirrorBehavior=True)
    new_root = cmds.ls(created[0], long=True)[0]

    pairs = _parallel_walk(root, new_root)
    # Deepest-first rename keeps ancestor paths valid.
    renames = []
    for s, d in sorted(pairs, key=lambda p: p[1].count('|'),
                       reverse=True):
        target = fmap.get(s)
        if target:
            d_new = cmds.rename(d, target)
        else:
            d_new = d
            cmds.warning(
                f'Mirror: {s!r} has no side token — keeping auto name '
                f'{d.split("|")[-1]!r}.')
        renames.append((s, d_new.split('|')[-1]))

    # Strip the membership .message echo mirrorJoint copied onto the
    # fresh side BEFORE any module/limb mirroring reads scene state.
    _release_copy_message_echo([d for _, d in renames])

    # Aimer states through the convention flip — the exact rule the
    # live armature mirror wires (rx direct, -ry+180, -rz; child
    # target labels token-flipped; Local/World copy as-is).
    #
    # The +180 belongs to a REVERSING frame only. A child- (or Parent-)
    # aimed offset node points down the bone, and on the mirrored side
    # the joint's +X points the other way, so the 180 is what describes
    # that correct state. A Local aimer's frame IS the joint's own
    # orientation, which cmds.mirrorJoint already behavior-mirrored, so
    # there is nothing to reverse and the 180 is spurious — and it does
    # not stay in the channel: build_armature's orientation bake writes
    # the aimer's WORLD orientation into the joint, so the bad 180 lands
    # in the joint and the Local channel collapses back to 0 behind it.
    # That un-flipped every childless tip (finger tips read
    # rotate=(180, 0, -167.48) against their source's (0, 0, 12.52))
    # while every child-aimed parent mirrored correctly.
    # Found 2026-07-19 on Adrian's mirrored hand.
    made = []
    for s, d in renames:
        made.append(d)
        st = states.get(s)
        if not st:
            continue
        if not joa.aimer_exists(d):
            joa.create_aimer(d)
        tgt = st['aim_target']
        if tgt not in ('', 'Local', 'World'):
            tgt = side_tokens.flip_side_token(tgt) or tgt
        rx, ry, rz = st['aim_offset']
        reverses = (tgt != 'Local')
        mrx, mry, mrz = conv.mirror_aimer_rotation(
            rx, ry, rz, frame_reverses=reverses)
        # Only the flipped path is wrapped, as before: a verbatim copy
        # must reproduce the source's channel values exactly.
        offset = ([mrx, _wrap180(mry), _wrap180(mrz)] if reverses
                  else [mrx, mry, mrz])
        joa.apply_aimer_state(d, aim_target=tgt, aim_offset=offset)
    return made


def mirror_module(root: str = None) -> dict:
    """Mirror ONLY the module(s) sitting on the selected joint — e.g.
    the clavicle FK without the shoulder IK below it (Adrian,
    2026-07-05). Far-side joints must already exist
    (fs_app.mirror_component enforces it with a readable error).
    Network-node work only — no armature rebuild, instant.

    Returns {'components': int, 'ids': [new component ids]}.
    """
    _require_edit_mode('Mirror Module')
    from maya_tools.rigging.fabricator import fs_app
    joint = root or resolve_selected_branch()

    owners = []
    for cnode in nodes.get_all_component_nodes():
        names = [j.split('|')[-1].split(':')[-1]
                 for j in (nodes.get_component_joint_names(cnode)
                           or nodes.get_component_joints(cnode) or [])]
        if joint in names:
            owners.append(nodes.get_component_id(cnode))
    if not owners:
        raise RuntimeError(
            f'Mirror Module: no module owns joint {joint!r}. Select a '
            f'joint (or its ctrl) that carries a module.')

    new_ids = [fs_app.mirror_component(cid) for cid in owners]
    return {'components': len(new_ids), 'ids': new_ids}


def _mirror_branch_modules(branch_joints: list) -> tuple:
    """fs_app.mirror_component for every component inside the branch.
    Already-mirrored / conflicting components skip with a warning so
    re-running Mirror All never dies mid-branch."""
    from maya_tools.rigging.fabricator import fs_app
    done = skipped = 0
    for cnode, _names in _components_in_branch(branch_joints):
        cid = nodes.get_component_id(cnode)
        try:
            fs_app.mirror_component(cid)
            done += 1
        except RuntimeError as e:
            cmds.warning(f'Mirror: skipped {cid!r}: {e}')
            skipped += 1
    return done, skipped


# ─────────────────────────────────────────────
# Duplicate
# ─────────────────────────────────────────────

def sanitize_tag(raw: str) -> str:
    tag = re.sub(r'[^A-Za-z0-9_]', '_', (raw or '').strip()).strip('_')
    if not tag:
        raise RuntimeError('Duplicate: variant tag is empty.')
    return tag


def variant_name(name: str, tag: str) -> str:
    """Insert the tag before the side token; append when sideless.
    clavicle_l + 'bk' → clavicle_bk_l; extra01 + 'bk' → extra01_bk."""
    segs = name.split('_')
    for i, s in enumerate(segs):
        if s.lower() in _SIDE_SEGS:
            return '_'.join(segs[:i] + [tag] + segs[i:])
    return f'{name}_{tag}'


def duplicate_branch(tag: str, root: str = None,
                     include_modules: bool = True) -> dict:
    """Smart in-place duplicate of the selected branch (see module
    docstring). Selects the new branch root's ctrl on completion.

    Returns {'root': str, 'joints': int, 'components': int}.
    """
    _require_edit_mode('Duplicate')
    root = root or resolve_selected_branch()
    tag = sanitize_tag(tag)
    joints = _branch_joints(root)

    nmap = {j: variant_name(j, tag) for j in joints}
    clashes = sorted(n for n in nmap.values() if cmds.objExists(n))
    if clashes:
        raise RuntimeError(
            f'Duplicate: {len(clashes)} name(s) already exist with tag '
            f'{tag!r}: {", ".join(clashes[:6])}'
            f'{"…" if len(clashes) > 6 else ""}. Pick another tag.')

    states = {j: joa.get_aimer_state(j) for j in joints}

    # preserved(): Symmetry survives the teardown→rebuild sandwich.
    comps = 0
    with armature_mirror.preserved():
        if armature.armature_exists():
            armature.delete_armature()

        new_root = cmds.ls(cmds.duplicate(root, renameChildren=True)[0],
                           long=True)[0]

        pairs = _parallel_walk(root, new_root)
        renames = []
        for s, d in sorted(pairs, key=lambda p: p[1].count('|'),
                           reverse=True):
            d_new = cmds.rename(d, nmap[s])
            renames.append((s, d_new.split('|')[-1]))

        # Strip the membership .message echo cmds.duplicate copied onto
        # the new branch (same vector as the mirror path).
        _release_copy_message_echo([d for _, d in renames])

        # Aimer states copy verbatim — same side, same offsets; child
        # target labels follow the rename.
        for s, d in renames:
            st = states.get(s)
            if not st:
                continue
            if not joa.aimer_exists(d):
                joa.create_aimer(d)
            tgt = nmap.get(st['aim_target'], st['aim_target'])
            joa.apply_aimer_state(d, aim_target=tgt,
                                  aim_offset=list(st['aim_offset']))

        if include_modules:
            comps = _duplicate_branch_modules(joints, nmap)

        armature.build_armature()
        from maya_tools.rigging.fabricator import fs_app
        fs_app.add_joints_to_reference_layer(list(nmap.values()))

    # Adrian: land in place, auto-select the new root ctrl so the user
    # can start moving immediately.
    new_ctrl = f'{nmap[root]}{_CTRL_SUFFIX}'
    if cmds.objExists(new_ctrl):
        cmds.select(new_ctrl, replace=True)

    return {'root': nmap[root], 'joints': len(nmap),
            'components': comps}


# ─────────────────────────────────────────────
# Delete
# ─────────────────────────────────────────────

def delete_branches(roots: list) -> dict:
    """Delete whole branches: each root joint + its subtree, their
    aimers, and every module touching those joints — one sandwich for
    the whole batch.

    A component fully inside a deleted branch goes silently; one that
    also touches OUTSIDE joints goes too (its branch joints are dying
    — a half-connected module is worse than none) with a warning
    naming what it reached. The rig root is refused: deleting the
    whole rig is File > Delete Rig's deliberate, prompted job.

    Returns {'roots': [...], 'joints': int, 'components': int}.
    """
    _require_edit_mode('Delete')

    seen = set()
    branch_roots = []
    for r in roots or []:
        r = _short(r)
        if not r or r in seen:
            continue
        seen.add(r)
        if not cmds.objExists(r) or cmds.nodeType(r) != 'joint':
            continue  # already gone (nested roots) — nothing to do
        branch_roots.append(r)
    if not branch_roots:
        raise RuntimeError('Delete: no branch joints to delete.')

    reg_root = _short(nodes.get_registry_root_joint() or '')
    if reg_root and reg_root in branch_roots:
        raise RuntimeError(
            f'Delete: {reg_root!r} is the rig root — use File > '
            f'Delete Rig to remove the whole rig.')

    jset = set()
    for r in branch_roots:
        jset.update(_branch_joints(r))

    # Components touching the doomed joints — fully-contained ones go
    # quietly, boundary-crossers go with a warning.
    comp_nodes = []
    for cnode in nodes.get_all_component_nodes():
        names = [j.split('|')[-1].split(':')[-1]
                 for j in (nodes.get_component_joint_names(cnode)
                           or nodes.get_component_joints(cnode) or [])]
        inside = set(names) & jset
        if not inside:
            continue
        comp_nodes.append(cnode)
        outside = sorted(set(names) - jset)
        if outside:
            cmds.warning(
                f'Delete: module '
                f'{nodes.get_component_id(cnode)!r} also touched '
                f'{", ".join(outside)} — removed with the branch.')

    had_armature = armature.armature_exists()
    with armature_mirror.preserved():
        if had_armature:
            armature.delete_armature()

        from maya_tools.rigging.fabricator import fs_app
        n_comps = fs_app.delete_components(comp_nodes)

        # Aimers before joints — delete_aimer resolves fragments by
        # the joint's name and is a safe no-op where none exists.
        for j in sorted(jset):
            joa.delete_aimer(j)

        for r in branch_roots:
            if cmds.objExists(r):
                cmds.delete(r)

        if had_armature:
            armature.build_armature()

    return {'roots': branch_roots, 'joints': len(jset),
            'components': n_comps}


# ─────────────────────────────────────────────
# Reparent
# ─────────────────────────────────────────────

def _short(node: str) -> str:
    return str(node).split('|')[-1].split(':')[-1]


def reparent_branches(roots: list, new_parent: str) -> dict:
    """Move each branch (root joint + its whole subtree) under
    new_parent. The armature-era reparent: canvas MMB drag-drop and
    ctrl-under-ctrl parenting both land here.

    The sandwich: delete Armature (unlocks joints, clean hierarchy) →
    cmds.parent each root (world transforms preserved — joints stay
    exactly where they are) → collapse jointOrient on the moved roots
    (cmds.parent rebases parent rotation into JO; the export contract
    wants rotate-only) → rebuild aimers whose child enums went stale
    (each old parent lost a slot, the new parent gained one; an old
    parent that was AIMING at a moved branch re-seeds from geometric
    detection — another on-axis child or Local, never a guess) →
    rebuild Armature → select the moved ctrls.

    Multiple roots move in ONE sandwich (select clav_l + clav_r,
    target spine3 — one teardown, one rebuild). Roots already under
    new_parent are skipped and reported. Joint names and components
    are untouched — modules ride along and the next rig build follows
    the new topology.

    Returns {'moved': [roots], 'new_parent': str,
             'old_parents': {root: old_parent_short_or_''},
             'already': [roots skipped — already under new_parent]}.
    """
    _require_edit_mode('Reparent')

    new_parent = _short(new_parent)
    if not cmds.objExists(new_parent):
        raise RuntimeError(
            f'Reparent: target joint {new_parent!r} does not exist.')
    if cmds.nodeType(new_parent) != 'joint':
        raise RuntimeError(
            f'Reparent: target {new_parent!r} is not a joint.')

    seen = set()
    branch_roots = []
    for r in roots or []:
        r = _short(r)
        if not r or r in seen:
            continue
        seen.add(r)
        if not cmds.objExists(r):
            raise RuntimeError(f'Reparent: joint {r!r} does not exist.')
        if cmds.nodeType(r) != 'joint':
            raise RuntimeError(f'Reparent: {r!r} is not a joint.')
        branch_roots.append(r)
    if not branch_roots:
        raise RuntimeError('Reparent: nothing to move.')

    reg_root = nodes.get_registry_root_joint() or ''
    if reg_root and _short(reg_root) in branch_roots:
        raise RuntimeError(
            f'Reparent: {_short(reg_root)!r} is the rig root — it '
            f'anchors the registry and cannot be reparented.')

    np_long = cmds.ls(new_parent, long=True)[0]
    for r in branch_roots:
        r_long = cmds.ls(r, long=True)[0]
        if np_long == r_long or np_long.startswith(r_long + '|'):
            raise RuntimeError(
                f'Reparent: cannot parent {r!r} under {new_parent!r} — '
                f'the target is inside the branch being moved.')

    # Split off no-ops so an all-no-op call skips the sandwich entirely.
    old_parents = {}
    already = []
    todo = []
    for r in branch_roots:
        cur = (cmds.listRelatives(r, parent=True) or [''])[0]
        cur = _short(cur) if cur else ''
        if cur == new_parent:
            already.append(r)
            continue
        old_parents[r] = cur
        todo.append(r)
    if not todo:
        return {'moved': [], 'new_parent': new_parent,
                'old_parents': {}, 'already': already}

    had_armature = armature.armature_exists()
    with armature_mirror.preserved():
        if had_armature:
            armature.delete_armature()

        for r in todo:
            cmds.parent(r, new_parent)
        # cmds.parent compensated through jointOrient on each moved
        # root — fold it back into rotate (local matrix preserved, so
        # children are unaffected).
        joa.collapse_joint_orient(todo)

        _refresh_stale_aimers(old_parents, new_parent)

        if had_armature:
            armature.build_armature()

    if had_armature:
        ctrls = [f'{r}{_CTRL_SUFFIX}' for r in todo
                 if cmds.objExists(f'{r}{_CTRL_SUFFIX}')]
        if ctrls:
            cmds.select(ctrls, replace=True)

    return {'moved': todo, 'new_parent': new_parent,
            'old_parents': old_parents, 'already': already}


def _refresh_stale_aimers(old_parents: dict, new_parent: str) -> None:
    """Rebuild the aimers whose child enums the reparent changed: every
    old parent (lost a child slot) and the new parent (gained one).
    rebuild_aimer preserves state by label; when an old parent's target
    was the branch that just left, the label is gone — re-seed from
    geometric detection (finds another on-axis child, else stays Local;
    authored orientation is never rewritten). Joints without aimers are
    left without aimers."""
    stale = {p for p in old_parents.values()
             if p and cmds.objExists(p) and cmds.nodeType(p) == 'joint'}
    stale.add(new_parent)
    for j in sorted(stale):
        if not joa.aimer_exists(j):
            continue
        state = joa.get_aimer_state(j)
        joa.rebuild_aimer(j)
        tgt = (state or {}).get('aim_target', '')
        if tgt and tgt not in ('Local', 'World'):
            kids = cmds.listRelatives(j, children=True,
                                      type='joint') or []
            if tgt not in kids:
                joa.seed_aimer_from_detection(j)


def _duplicate_branch_modules(branch_joints: list, nmap: dict) -> int:
    """Re-create every branch component on the renamed joints: verbatim
    options (ctrl_color keeps its side — same side as the source; the
    library ctrl shape rides in options, so dupes wear the same
    shapes), fresh sculpts (persisted CV data intentionally not
    copied — Adrian ratified 2026-07-05), parent_plug verbatim (the
    duplicate hangs off the same provider — e.g. a duplicated arm
    still follows the spine), and per-instance space targets COPIED
    (Adrian, 2026-07-05: duplicates keep their spaces; mirrors start
    empty by design). Space targets inside the duplicated branch
    follow the rename; outside targets point at the same joints."""
    made = 0
    for cnode, names in _components_in_branch(branch_joints):
        ctype = nodes.get_component_type(cnode)
        try:
            cls = modules.get_component_class(ctype)
        except KeyError:
            cmds.warning(f'Duplicate: unknown component type '
                         f'{ctype!r}; skipped.')
            continue
        new_joints = [nmap[n] for n in names]
        new_node = nodes.create_component_node(
            component_id=cls.default_id(new_joints),
            component_type=ctype,
            joints=new_joints,
            joint_names=new_joints,
            parent_plug=nodes.get_component_parent_plug(cnode) or '',
            side=nodes.get_component_side(cnode) or '',
            role=nodes.get_component_role(cnode) or '',
            region=nodes.get_component_region(cnode) or '',
            options=nodes.get_component_options(cnode) or {},
            persisted={},
        )
        # Spaces are spaces_<ctrl_role> message-multis on the network
        # node, not options — copy them explicitly.
        for attr in cmds.listAttr(cnode, string='spaces_*') or []:
            if '[' in attr:
                continue  # multi elements; the parent attr is enough
            role = attr[len('spaces_'):]
            for jn in nodes.get_ctrl_space_names(cnode, role):
                nodes.add_ctrl_space(new_node, role, nmap.get(jn, jn))
        made += 1
    return made
