# maya_tools/skinning/skin_connect_app.py
"""Disconnect / reconnect skin clusters — scene-wide, skinCluster-first.

Workflow this enables:

  1. Rigger has an edit-mode rig with skinned meshes (anywhere in the
     scene — no container requirement).
  2. Disconnect Skins — every live skinCluster is detached via
     `skinCluster -unbindKeepHistory`. Maya (2025) keeps the whole
     deformer graph wired and flips the cluster's nodeState to
     HasNoEffect, so weights survive untouched and the mesh stops
     following joint motion.
  3. Rigger moves joints around to fix placement.
  4. Reconnect Skins — every detached (HasNoEffect) skinCluster is
     re-activated at the joints' CURRENT positions: each influence's
     bindPreMatrix is reset to the inverse of its present worldMatrix,
     then nodeState returns to Normal. Painted weights are never
     touched; no new cluster nodes are created.

Discovery is skinCluster-first: we find the clusters and resolve their
geometry, never walk a named container. (The previous geo_grp-scoped
walk missed any skinned mesh living outside `geo_grp`.) An optional
`scope` transform restricts either operation to geometry under it.

Fallback: a dormant cluster that is genuinely disconnected from its
shape chain (not the -ubk envelope state) is re-bound fresh with the
cluster's influence list, then bakePartialHistory scrubs the leftover
non-deformer input — the legacy path ported from
`rigging/rigger/rigger_skin_utils.py:52-96` (pymel → cmds).

NOT auto-run on Fabricator unbuild/rebuild yet — the Armature
lifecycle (spec 2026-07-04) wires these into build/unbuild.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds
import maya.api.OpenMaya as om


# ─────────────────────────────────────────────
# State predicates
# ─────────────────────────────────────────────

def _is_connected(sc: str) -> bool:
    """True when the skinCluster's outputGeometry feeds a shape chain.
    -ubk detachment does NOT break this connection (it zeroes the
    envelope), so connected != deforming."""
    return bool(cmds.listConnections(f'{sc}.outputGeometry',
                                     source=False, destination=True))


def _is_live(sc: str) -> bool:
    """True when the skinCluster is actually deforming: connected,
    envelope on, AND nodeState Normal. Maya 2025's unbindKeepHistory
    leaves the graph fully wired with envelope 1.0 and flips
    nodeState to 1 (HasNoEffect) — verified live 2026-07-04. Envelope
    is still checked to catch tools that disable the old way."""
    if not _is_connected(sc):
        return False
    try:
        return (cmds.getAttr(f'{sc}.envelope') > 0.0
                and cmds.getAttr(f'{sc}.nodeState') == 0)
    except Exception:
        return False


# ─────────────────────────────────────────────
# Geometry resolution
# ─────────────────────────────────────────────

def _transforms_of_shapes(shapes: list) -> list:
    """Parent transforms (fullPath, deduped, sorted) of shape nodes."""
    transforms = set()
    for shape in shapes:
        parents = cmds.listRelatives(shape, parent=True,
                                     fullPath=True) or []
        if parents:
            transforms.add(parents[0])
    return sorted(transforms)


def _cluster_geometry(sc: str) -> list:
    """Mesh transforms a skinCluster is wired to. Falls back to a
    history walk (via the intermediate Orig shape) for clusters whose
    output connection is gone entirely."""
    shapes = cmds.skinCluster(sc, query=True, geometry=True) or []
    if not shapes:
        shapes = cmds.ls(cmds.listHistory(sc) or [], type='mesh',
                         long=True) or []
    return _transforms_of_shapes(shapes)


def _in_scope(geo: str, scope: str) -> bool:
    """True when geo (fullPath) is scope itself or a descendant."""
    if not scope:
        return True
    scopes = cmds.ls(scope, long=True) or []
    if not scopes:
        return True  # bad scope never silently empties the operation
    root = scopes[0]
    return geo == root or geo.startswith(root + '|')


# ─────────────────────────────────────────────
# Disconnect
# ─────────────────────────────────────────────

def disconnect_all_skins(scope: str = None) -> list:
    """Detach every live skinCluster in the scene (optionally only
    those deforming geometry under `scope`). Returns the list of mesh
    transforms that were detached."""
    detached = []
    for sc in cmds.ls(type='skinCluster') or []:
        if not _is_live(sc):
            continue
        for geo in _cluster_geometry(sc):
            if not _in_scope(geo, scope):
                continue
            try:
                cmds.skinCluster(geo, edit=True, unbindKeepHistory=True)
                detached.append(geo)
            except Exception as exc:
                cmds.warning(
                    f'Disconnect Skins: {geo!r} failed — {exc}. '
                    f'Continuing.'
                )
    return sorted(set(detached))


# ─────────────────────────────────────────────
# Reconnect
# ─────────────────────────────────────────────

def _reactivate_cluster(sc: str) -> None:
    """Re-activate a detached-but-wired skinCluster at the influences'
    CURRENT world positions. bindPreMatrix[i] = inverse(worldMatrix)
    per influence, then nodeState back to Normal (and envelope to 1
    for the legacy disable style). Weights are untouched."""
    indices = cmds.getAttr(f'{sc}.matrix', multiIndices=True) or []
    for i in indices:
        sources = cmds.listConnections(f'{sc}.matrix[{i}]',
                                       source=True, destination=False) or []
        if not sources:
            cmds.warning(
                f'Reconnect Skins: {sc}.matrix[{i}] has no influence '
                f'connected — skipping that slot.')
            continue
        wm = cmds.getAttr(f'{sources[0]}.worldMatrix[0]')
        inv = om.MMatrix(wm).inverse()
        cmds.setAttr(f'{sc}.bindPreMatrix[{i}]', list(inv), type='matrix')
    cmds.setAttr(f'{sc}.nodeState', 0)
    cmds.setAttr(f'{sc}.envelope', 1.0)


def _rebind_fresh(sc: str, geo: str) -> bool:
    """Legacy fallback for a cluster fully disconnected from its shape
    chain: fresh bind with the cluster's influence list + settings.
    Returns True when a bind happened."""
    influences = cmds.skinCluster(sc, query=True, influence=True) or []
    if not influences:
        return False
    max_inf = cmds.getAttr(f'{sc}.maxInfluences')
    use_max = cmds.getAttr(f'{sc}.maintainMaxInfluences')
    cmds.skinCluster(
        geo, influences,
        dropoffRate=4.0,
        maximumInfluences=max_inf,
        obeyMaxInfluences=use_max,
        toSelectedBones=True,
    )
    return True


def reconnect_all_skins(scope: str = None) -> list:
    """Re-attach every detached skinCluster (optionally only geometry
    under `scope`) at the joints' current positions. Envelope-0
    clusters are re-activated in place (weights untouched); fully
    disconnected clusters fall back to a fresh bind + history cleanup.
    Returns the list of mesh transforms re-attached."""
    reattached = []
    fresh_bound = []

    # Meshes already driven by a live cluster are never touched — a
    # dormant cluster left behind must not trigger a double bind.
    already_live = set()
    for sc in cmds.ls(type='skinCluster') or []:
        if _is_live(sc):
            already_live.update(_cluster_geometry(sc))

    for sc in cmds.ls(type='skinCluster') or []:
        if _is_live(sc):
            continue
        geos = [g for g in _cluster_geometry(sc)
                if g not in already_live and _in_scope(g, scope)]
        if not geos:
            continue
        try:
            if _is_connected(sc):
                # -ubk state: graph wired, envelope 0. Re-activate.
                _reactivate_cluster(sc)
                reattached.extend(geos)
            else:
                # Orphaned cluster: legacy fresh-bind path.
                for geo in geos:
                    if _rebind_fresh(sc, geo):
                        reattached.append(geo)
                        fresh_bound.append(geo)
            already_live.update(geos)
        except Exception as exc:
            cmds.warning(
                f'Reconnect Skins: {sc!r} failed — {exc}. Continuing.'
            )

    # Fresh binds leave a redundant non-deformer input on the mesh
    # transform; scrub it. Re-activated clusters create no such junk.
    for geo in sorted(set(fresh_bound)):
        try:
            cmds.bakePartialHistory(geo, prePostDeformers=True)
        except Exception as exc:
            cmds.warning(
                f'Reconnect Skins: non-deformer history cleanup failed '
                f'for {geo!r} — {exc}.'
            )
    return sorted(set(reattached))


# ─────────────────────────────────────────────
# Reset bind to the current pose (refreshes LIVE clusters too)
# ─────────────────────────────────────────────

def reset_all_binds_to_pose(scope: str = None) -> list:
    """Reset every skinCluster's bind so the CURRENT joint pose IS the bind
    pose, scene-wide (optionally scoped) — a full disconnect then reconnect.

    Why not just reconnect_all_skins(): reconnect SKIPS a cluster that is
    already live (it only re-activates dormant ones). So a rig whose joints
    were re-oriented while the skin stayed live keeps its STALE bind — the
    exact export candy-wrapper: reconnect_all_skins() left the twist joints'
    bindPreMatrix at the pre-orient value (verified 2026-07-14: a live
    re-oriented cluster stays at delta 16.0 through reconnect, and drops to 0.0
    only after a disconnect+reconnect).

    disconnect_all_skins first forces every cluster dormant (its mesh passes
    through to its rest/Orig shape via -ubk HasNoEffect); reconnect_all_skins
    then re-derives every bindPreMatrix from the joints' present world and
    leaves the cluster live. Net: bind matches the current skeleton, mesh at
    rest, regardless of whether the cluster was live or dormant going in.

    Returns the mesh transforms refreshed."""
    disconnect_all_skins(scope)
    return reconnect_all_skins(scope)
