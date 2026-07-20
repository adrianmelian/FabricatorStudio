# maya_tools/skeleton/joint_mirror/joint_mirror_app.py
"""Smart joint mirror — selection-driven joint mirror with a live DG
constraint and permanent disconnect.

Mirror plane: YZ (negate X). Hardcoded for v1.

Preserves position + orientation (translation + rotation + jointOrient).
No scale. Network shape per (L_joint, R_joint) pair:

    L.worldMatrix -> mirror_mm (sandwich: mirror_matrix x L_world x mirror_matrix)
                   -> local_mm (mirror_world x R_parent.worldInverseMatrix)
                   -> dm (decomposeMatrix)
                   -> R.translate / R.rotate

Every mirror-network node carries a bool attr `ksMirror=true` for the
Disconnect walk. Disconnect = listHistory on each joint, filter for the
tag, delete those nodes. After disconnect, R joints keep their current
local TRS values (frozen at the disconnect moment).

The mirror is permanent on disconnect — no relink API. Re-mirror is a
fresh op (and refuses if R already exists).

Defensive against Fabricator builds: if any joint in the selection
chain belongs to a built fab_component, mirror_joints refuses with
a clear error. is_built is the single authoritative bool; the joint
must be disconnected from a built component first (via Edit Rig).
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.utils.maya import side_tokens


# YZ-plane mirror matrix as a flat 16-tuple for setAttr.
_MIRROR_MATRIX = (
    -1.0, 0.0, 0.0, 0.0,
     0.0, 1.0, 0.0, 0.0,
     0.0, 0.0, 1.0, 0.0,
     0.0, 0.0, 0.0, 1.0,
)

_MIRROR_TAG = 'ksMirror'


def _is_fabricator_built(joint: str) -> bool:
    """True if this joint belongs to a built Fabricator component.

    Deferred import — joint_mirror is a sibling of fabricator (no
    Fabricator dependency at package load).
    """
    try:
        from maya_tools.rigging.fabricator import nodes as fab_nodes
    except ImportError:
        return False
    cnode = fab_nodes.find_component_for_joint(joint)
    if not cnode:
        return False
    return fab_nodes.is_built(cnode)


def _collect_chain(roots: list) -> list:
    """Return roots + every descendant joint of each, deduplicated,
    sorted parent-before-child (by DAG depth).

    Guarantees that for any two joints in the result, if A is an
    ancestor of B then A appears before B. Required so the caller's
    reparent loop sees ancestors already mapped to their R counterparts
    by the time it processes a descendant.
    """
    collected = set()
    for root in roots:
        if not cmds.objExists(root):
            continue
        collected.add(root)
        for child in (cmds.listRelatives(root, allDescendents=True,
                                          type='joint', fullPath=False) or []):
            collected.add(child)
    # Sort by full DAG depth — shallower joints first.
    def _depth(j):
        full = cmds.ls(j, long=True)
        return full[0].count('|') if full else 0
    return sorted(collected, key=_depth)


def _create_mirror_matrix_holder(name: str) -> str:
    """Create a holdMatrix node carrying the static YZ mirror matrix."""
    n = cmds.createNode('holdMatrix', name=name)
    cmds.setAttr(f'{n}.inMatrix', _MIRROR_MATRIX, type='matrix')
    cmds.addAttr(n, ln=_MIRROR_TAG, at='bool', dv=True)
    cmds.setAttr(f'{n}.{_MIRROR_TAG}', True, lock=True)
    return n


def _tag(node: str) -> None:
    """Stamp a node with the mirror-network tag for Disconnect's walk."""
    if not cmds.attributeQuery(_MIRROR_TAG, node=node, exists=True):
        cmds.addAttr(node, ln=_MIRROR_TAG, at='bool', dv=True)
    cmds.setAttr(f'{node}.{_MIRROR_TAG}', True, lock=True)


def _build_mirror_network(l_joint: str, r_joint: str) -> None:
    """Wire the live DG mirror from L to R. Caller has already created
    r_joint at the (one-shot) mirrored pose; this hookup sustains the
    sync going forward.
    """
    mirror_holder = _create_mirror_matrix_holder(f'{r_joint}_mirror_mat')

    # World-mirror: M_mirror x L_world x M_mirror
    world_mm = cmds.createNode('multMatrix', name=f'{r_joint}_mirror_world_mm')
    _tag(world_mm)
    cmds.connectAttr(f'{mirror_holder}.outMatrix', f'{world_mm}.matrixIn[0]', f=True)
    cmds.connectAttr(f'{l_joint}.worldMatrix[0]', f'{world_mm}.matrixIn[1]', f=True)
    cmds.connectAttr(f'{mirror_holder}.outMatrix', f'{world_mm}.matrixIn[2]', f=True)

    # Convert to R's local space: world_mirrored x R_parent.worldInverseMatrix
    local_mm = cmds.createNode('multMatrix', name=f'{r_joint}_mirror_local_mm')
    _tag(local_mm)
    cmds.connectAttr(f'{world_mm}.matrixSum', f'{local_mm}.matrixIn[0]', f=True)

    r_parent = (cmds.listRelatives(r_joint, parent=True, fullPath=True) or [None])[0]
    if r_parent:
        cmds.connectAttr(f'{r_parent}.worldInverseMatrix[0]',
                         f'{local_mm}.matrixIn[1]', f=True)
    # If R has no parent (root joint), local == world; matrixIn[1] stays identity.

    # Decompose to TRS.
    dm = cmds.createNode('decomposeMatrix', name=f'{r_joint}_mirror_dm')
    _tag(dm)
    cmds.connectAttr(f'{local_mm}.matrixSum', f'{dm}.inputMatrix', f=True)
    cmds.connectAttr(f'{dm}.outputTranslate', f'{r_joint}.translate', f=True)
    cmds.connectAttr(f'{dm}.outputRotate', f'{r_joint}.rotate', f=True)

    # jointOrient is absorbed into rotate by the live network — zero it
    # on R so there's no double-application.
    if cmds.attributeQuery('jointOrient', node=r_joint, exists=True):
        for axis in 'XYZ':
            cmds.setAttr(f'{r_joint}.jointOrient{axis}', 0.0)


def mirror_joints(joints: list = None) -> list:
    """Mirror the given joints + their descendants across the YZ plane.
    Creates R joints (named via side_tokens.flip_side_token), wires the
    live DG constraint, returns the list of created R joint names.

    joints=None reads the current Maya selection.

    Raises RuntimeError on:
    - empty selection
    - any joint without a side token in its name
    - any joint belonging to a built Fabricator component
    - any R joint name colliding with an existing scene node
    """
    if joints is None:
        joints = cmds.ls(sl=True, type='joint') or []
    if not joints:
        raise RuntimeError('Mirror Joints: select at least one joint.')

    chain = _collect_chain(joints)
    if not chain:
        raise RuntimeError('Mirror Joints: selection contains no joints.')

    # Defensive checks — bail BEFORE any scene mutation.
    for j in chain:
        side = side_tokens.detect_side(j)
        if side == 'md':
            raise RuntimeError(
                f"Mirror Joints: {j!r} has no recognized side token. "
                f"Rename to include a side token (e.g. L_arm, arm_l, "
                f"lf_arm, left_arm) before mirroring."
            )
        r_name = side_tokens.flip_side_token(j)
        if r_name is None:
            raise RuntimeError(
                f"Mirror Joints: cannot flip side token in {j!r}."
            )
        if cmds.objExists(r_name):
            raise RuntimeError(
                f"Mirror Joints: R joint {r_name!r} already exists. "
                f"Delete it first, or use Disconnect Mirror if you want "
                f"to refresh the link."
            )
        if _is_fabricator_built(j):
            raise RuntimeError(
                f"Mirror Joints: joint {j!r} belongs to a built Fabricator "
                f"component. Unbuild the rig (Edit Rig) first, or mirror a "
                f"different joint."
            )

    # Build R joints top-down (parents before children) so reparenting
    # finds the right new-parent node.
    created = []
    l_to_r = {}
    for l in chain:
        r = side_tokens.flip_side_token(l)
        # Mirrored worldspace position + orientation (one-shot at creation).
        l_world_pos = cmds.xform(l, q=True, ws=True, t=True)
        l_world_rot = cmds.xform(l, q=True, ws=True, ro=True)
        mirrored_pos = side_tokens.mirror_point(l_world_pos)
        # Rotation mirror across YZ: negate Y, negate Z, keep X. This is
        # the Maya "Mirror Behavior" semantic for Euler XYZ rotation order.
        mirrored_rot = (l_world_rot[0], -l_world_rot[1], -l_world_rot[2])

        cmds.select(clear=True)
        cmds.joint(name=r)
        cmds.xform(r, ws=True, t=mirrored_pos)
        cmds.xform(r, ws=True, ro=mirrored_rot)
        # Copy joint visual radius so R matches L's display size.
        cmds.setAttr(f'{r}.radius', cmds.getAttr(f'{l}.radius'))

        # Reparent under the mirrored parent. Resolution order:
        # 1. If the L parent is in this mirror op → use its just-created R.
        # 2. Else compute the flipped name (sided parent → flipped; center
        #    parent → same name, since flip_side_token returns None).
        # 3. If the flipped name exists in scene → parent there.
        # 4. Else fall back to L_parent itself. Matches Maya's stock
        #    joint-mirror "mistake" behavior: mirror a mid-chain joint
        #    without first mirroring its parent → new R is a sibling of
        #    the source under the same L parent.
        l_parent = (cmds.listRelatives(l, parent=True) or [None])[0]
        if l_parent and l_parent in l_to_r:
            cmds.parent(r, l_to_r[l_parent])
        elif l_parent:
            flipped = side_tokens.flip_side_token(l_parent) or l_parent
            target = flipped if cmds.objExists(flipped) else l_parent
            cmds.parent(r, target)

        l_to_r[l] = r
        created.append(r)

    # Now wire each pair's live DG mirror. Order doesn't matter for the
    # wiring itself, but doing it after the whole hierarchy exists means
    # R_parent.worldInverseMatrix is already correctly evaluated.
    for l, r in l_to_r.items():
        _build_mirror_network(l, r)

    return created


def disconnect_mirror(joints: list = None) -> int:
    """Sever live mirror network on the given joints + descendants.
    Joints keep their current local TRS values (frozen at this moment).

    joints=None reads the current Maya selection. Disconnect operates
    on both L and R sides of selected pairs — if you select an R joint,
    the mirror feeding it is severed. If you select an L joint, any
    mirror reading FROM it (i.e. feeding its R counterpart) is severed.

    Returns the count of mirror-network nodes deleted. 0 = no mirrors
    found; not an error.
    """
    if joints is None:
        joints = cmds.ls(sl=True, type='joint') or []
    if not joints:
        return 0

    chain = _collect_chain(joints)
    to_delete = set()
    for j in chain:
        history = cmds.listHistory(j) or []
        future = cmds.listHistory(j, future=True, allFuture=True) or []
        for node in set(history) | set(future):
            if cmds.attributeQuery(_MIRROR_TAG, node=node, exists=True):
                to_delete.add(node)

    if not to_delete:
        return 0

    # Capture every joint currently driven by one of the mirror nodes.
    # `cmds.getAttr` returns the LIVE EVALUATED value while the network
    # is still wired; after cmds.delete the translate/rotate input plugs
    # revert to their stored values (typically 0,0,0 for joints created
    # via cmds.joint() then driven by connectAttr), making the R chain
    # collapse to origin. Re-stamp the captured values as static after
    # the delete so the joints freeze at their current mirrored pose.
    frozen = {}
    for n in to_delete:
        if cmds.nodeType(n) != 'decomposeMatrix':
            continue
        for src_attr in ('outputTranslate', 'outputRotate'):
            targets = cmds.listConnections(f'{n}.{src_attr}', source=False,
                                            destination=True, plugs=True) or []
            for plug in targets:
                target_node = plug.split('.')[0]
                if target_node in frozen or not cmds.objExists(target_node):
                    continue
                t = cmds.getAttr(f'{target_node}.translate')[0]
                r = cmds.getAttr(f'{target_node}.rotate')[0]
                frozen[target_node] = (t, r)

    # Unlock the tag attr so cmds.delete proceeds cleanly. (locked attrs
    # don't usually block deletion but be defensive.)
    for n in to_delete:
        try:
            cmds.setAttr(f'{n}.{_MIRROR_TAG}', lock=False)
        except Exception:
            pass
    cmds.delete(list(to_delete))

    # Re-stamp the captured TRS so the freed joints don't collapse.
    for j, (t, r) in frozen.items():
        if not cmds.objExists(j):
            continue
        for axis, val in zip('XYZ', t):
            cmds.setAttr(f'{j}.translate{axis}', val)
        for axis, val in zip('XYZ', r):
            cmds.setAttr(f'{j}.rotate{axis}', val)

    return len(to_delete)


# ---------------------------------------------------------------------------
# Bulk mirror constrain / break (operates on the whole scene, no selection)
# ---------------------------------------------------------------------------

_GHOSTS_GROUP = 'ksMirror_ghosts_grp'


def _r_has_mirror_input(r_joint: str) -> bool:
    """True if r_joint.translate / .rotate / .scale has a direct ksMirror-
    tagged source. Direct means the immediate input node on the plug — NOT
    DG ancestors. listHistory walks both DG and DAG, so it would return
    True for a child joint whose parent is already mirrored, incorrectly
    skipping the child."""
    for attr in ('translate', 'rotate', 'scale'):
        srcs = cmds.listConnections(f'{r_joint}.{attr}', source=True,
                                     destination=False, plugs=False) or []
        for src in srcs:
            if cmds.attributeQuery(_MIRROR_TAG, node=src, exists=True):
                return True
    return False


def _ensure_ghosts_group() -> str:
    """Create or find the hidden world-root group that holds all mirror
    ghost transforms. Ghosts must be world-anchored so their worldMatrix
    equals their driven local matrix (no parent multiplication)."""
    if cmds.objExists(_GHOSTS_GROUP):
        return _GHOSTS_GROUP
    g = cmds.createNode('transform', name=_GHOSTS_GROUP, skipSelect=True)
    cmds.setAttr(f'{g}.visibility', 0)
    _tag(g)
    return g


def _build_mirror_constraint_with_offset(l_joint: str, r_joint: str) -> None:
    """Drive R to follow the mirror of L, preserving R's existing bind
    orientation via parentConstraint(maintainOffset=True).

    Topology:
        L.worldMatrix
            ↓
        multMatrix(M, L.WM, M)            -- ksMirror, world-mirror of L
            ↓
        decomposeMatrix → ghost.t/r/s     -- hidden world-root transform
            ↓
        parentConstraint(ghost, R, mo)    -- ksMirror, captures R bind offset
        scaleConstraint(ghost, R, mo)     -- ksMirror

    Why the ghost + mo=True instead of driving R.t/r directly? UE5 rigs
    have R joints with X = -X world (ry(180°) from L's X = +X world). A
    direct driver writing R.WM = mirror(L.WM) would FLIP R away from its
    existing bind, because mirror(L.bind.WM) ≠ R.bind.WM under that
    convention. The constraint captures the bind delta between mirror(L)
    and R at creation time; only L's motion-from-bind propagates to R.
    """
    leaf = r_joint.split('|')[-1].split(':')[-1]
    ghost_grp = _ensure_ghosts_group()
    ghost = cmds.createNode('transform', name=f'{leaf}_mirror_ghost',
                             parent=ghost_grp, skipSelect=True)
    _tag(ghost)

    # World mirror of L's worldMatrix: M · L.WM · M.
    mirror_holder = _create_mirror_matrix_holder(f'{ghost}_mat')
    world_mm = cmds.createNode('multMatrix', name=f'{ghost}_world_mm')
    _tag(world_mm)
    cmds.connectAttr(f'{mirror_holder}.outMatrix', f'{world_mm}.matrixIn[0]', f=True)
    cmds.connectAttr(f'{l_joint}.worldMatrix[0]', f'{world_mm}.matrixIn[1]', f=True)
    cmds.connectAttr(f'{mirror_holder}.outMatrix', f'{world_mm}.matrixIn[2]', f=True)

    # Decompose to TRS and drive the ghost. Ghost sits under the world-
    # root ghosts_grp, so ghost.worldMatrix = ghost.matrix (no parent
    # composition) and we can drive its local TRS directly.
    dm = cmds.createNode('decomposeMatrix', name=f'{ghost}_dm')
    _tag(dm)
    cmds.connectAttr(f'{world_mm}.matrixSum', f'{dm}.inputMatrix', f=True)
    cmds.connectAttr(f'{dm}.outputTranslate', f'{ghost}.translate', f=True)
    cmds.connectAttr(f'{dm}.outputRotate',    f'{ghost}.rotate',    f=True)
    cmds.connectAttr(f'{dm}.outputScale',     f'{ghost}.scale',     f=True)

    # Constrain R with maintainOffset=True. R keeps its existing UE5 bind;
    # only L's motion-from-bind propagates through the mirror to R.
    pcon = cmds.parentConstraint(ghost, r_joint, maintainOffset=True)[0]
    _tag(pcon)
    scon = cmds.scaleConstraint(ghost, r_joint, maintainOffset=True)[0]
    _tag(scon)


def mirror_constrain_pairs() -> dict:
    """Wire the live DG mirror network for every L/R joint pair already
    present in the scene.

    Discovery: walk every joint, filter to L-side (side_tokens.detect_side
    == 'lf'), flip the side token to derive the R-side name, look up the
    R-side joint as a sibling under the same DAG parent.

    Differs from mirror_joints() in that R joints must ALREADY exist —
    this is for rigs imported with both sides (UE5 mannequin etc.) where
    you want a live mirror without recreating R from scratch. Uses
    _build_mirror_constraint_with_offset (NOT _build_mirror_network) so
    R keeps its existing bind orientation — essential for UE5 where R
    joints have X = -X world by convention and the world mirror of L's
    bind would otherwise flip R.

    Skips, with reasons reported in the return dict:
    - L joint belongs to a built Fabricator component (must unbuild first,
      same rule as mirror_joints).
    - L joint has no matching R sibling.
    - R joint already has a ksMirror network driving it (idempotent re-run).

    Returns: {
        'constrained': [...],                # L joint names wired this call
        'skipped_built': [...],              # L joints in built components
        'skipped_no_r': [...],               # L joints without an R sibling
        'skipped_already_mirrored': [...],   # L joints whose R was already wired
    }
    """
    all_joints = cmds.ls(type='joint', long=True) or []

    constrained = []
    skipped_built = []
    skipped_no_r = []
    skipped_already_mirrored = []

    for l_joint in all_joints:
        if side_tokens.detect_side(l_joint) != 'lf':
            continue

        l_short = l_joint.split('|')[-1]
        r_short = side_tokens.flip_side_token(l_short)
        if not r_short or r_short == l_short:
            continue

        # Resolve R. Same-parent sibling lookup first (clavicle_l/r under
        # spine_03, ik_foot_l/r under ik_foot_root — the unambiguous cases),
        # then fall back to scene-wide name search. Most joints in a
        # mirrored skeleton are NOT siblings of their counterpart —
        # upperarm_l lives under clavicle_l, upperarm_r under clavicle_r —
        # so the fallback is essential, not exceptional. r_short already
        # carries any namespace from flip_side_token, so cmds.ls(r_short)
        # is namespace-correct for referenced rigs.
        l_parent = (cmds.listRelatives(l_joint, parent=True,
                                        fullPath=True) or [None])[0]
        r_joint = None
        if l_parent:
            sibling = f'{l_parent}|{r_short}'
            if cmds.objExists(sibling) and cmds.nodeType(sibling) == 'joint':
                r_joint = cmds.ls(sibling, long=True)[0]
        if r_joint is None:
            matches = cmds.ls(r_short, type='joint', long=True) or []
            if matches:
                r_joint = matches[0]
        if r_joint is None:
            skipped_no_r.append(l_joint)
            continue

        if _is_fabricator_built(l_joint) or _is_fabricator_built(r_joint):
            skipped_built.append(l_joint)
            continue

        if _r_has_mirror_input(r_joint):
            skipped_already_mirrored.append(l_joint)
            continue

        _build_mirror_constraint_with_offset(l_joint, r_joint)
        constrained.append(l_joint)

    return {
        'constrained': constrained,
        'skipped_built': skipped_built,
        'skipped_no_r': skipped_no_r,
        'skipped_already_mirrored': skipped_already_mirrored,
    }


def break_all_mirror_constraints() -> int:
    """Delete every ksMirror-tagged node in the scene. Freezes each driven
    R joint at its current mirrored TRS (same freeze logic as
    disconnect_mirror) so chains don't collapse to origin when the
    decomposeMatrix outputs unhook.

    Returns the count of mirror-network nodes deleted.
    """
    # cmds.ls('*.<attr>', objectsOnly=True) returns every node that owns
    # the named attribute — no need to scan all node types manually.
    tagged = cmds.ls(f'*.{_MIRROR_TAG}', objectsOnly=True, long=True) or []
    # Dedupe (referenced rigs occasionally surface duplicates).
    mirror_nodes = list(dict.fromkeys(tagged))
    if not mirror_nodes:
        return 0

    # Freeze: capture each R joint's current evaluated TRS before delete.
    frozen = {}
    for n in mirror_nodes:
        if cmds.nodeType(n) != 'decomposeMatrix':
            continue
        for src_attr in ('outputTranslate', 'outputRotate'):
            targets = cmds.listConnections(f'{n}.{src_attr}', source=False,
                                            destination=True, plugs=True) or []
            for plug in targets:
                target_node = plug.split('.')[0]
                if target_node in frozen or not cmds.objExists(target_node):
                    continue
                t = cmds.getAttr(f'{target_node}.translate')[0]
                r = cmds.getAttr(f'{target_node}.rotate')[0]
                frozen[target_node] = (t, r)

    # Unlock the tag attr before delete — defensive against locked-attr
    # edge cases that would otherwise refuse the delete.
    for n in mirror_nodes:
        try:
            cmds.setAttr(f'{n}.{_MIRROR_TAG}', lock=False)
        except Exception:
            pass

    cmds.delete(mirror_nodes)

    # Re-stamp the captured TRS so the freed R joints don't snap to origin.
    for j, (t, r) in frozen.items():
        if not cmds.objExists(j):
            continue
        for axis, val in zip('XYZ', t):
            try:
                cmds.setAttr(f'{j}.translate{axis}', val)
            except Exception:
                pass
        for axis, val in zip('XYZ', r):
            try:
                cmds.setAttr(f'{j}.rotate{axis}', val)
            except Exception:
                pass

    return len(mirror_nodes)
