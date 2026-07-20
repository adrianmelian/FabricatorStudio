# scene_cleanup_app.py
# AM Scene Cleanup — headless API. Building blocks for the cleanup/validator
# tool. The full tool UI is on the shelves as a "coming soon" stub; this
# module seeds the headless API so other tools (KS, exporters, skin IO)
# can validate scene state without waiting for the UI to ship.
__author__ = "Adrian Melian"

import maya.cmds as cmds
import maya.mel as mel

from maya_tools.framework.decorators import undo_chunk


def find_duplicate_short_names(nodes: list[str]) -> dict[str, list[str]]:
    """Group given node names by their short name; return only the duplicates.

    Each input is expanded via cmds.ls(node, long=True) — so passing a short
    name like 'rig_grp' surfaces every node in the scene with that short
    name; passing a full DAG path returns only that node. The leaf segment
    after '|' and ':' is the short name used for the duplicate check.

    Returns:
        Dict mapping each duplicated short name to the sorted list of full
        DAG paths that share it. Short names that appear once or zero times
        are omitted. Empty dict means no duplicates.

    Use this as the building block for any "is the scene clean?" validation
    that needs unique short names within a given scope (rig joints, exporter
    targets, skinClusters geometry, etc.).
    """
    short_to_paths: dict[str, set[str]] = {}
    for node in nodes:
        for p in (cmds.ls(node, long=True) or []):
            short = p.split('|')[-1].split(':')[-1]
            short_to_paths.setdefault(short, set()).add(p)
    return {k: sorted(v) for k, v in short_to_paths.items() if len(v) > 1}


def find_duplicate_transforms() -> dict[str, list[str]]:
    """Find every transform in the scene whose short name collides with another.

    Walks all 'transform' nodes (joints are transforms in Maya's node-type
    hierarchy, so this implicitly covers joints too). Shapes and non-DAG
    nodes are skipped — shape names conventionally follow parent transforms,
    so transform uniqueness implies shape uniqueness for reference workflows.

    Returns the same {short_name: [long_path, ...]} shape as
    find_duplicate_short_names. Useful for rig-file cleanliness checks
    where every scene-level transform must have a distinct short name so
    references (Hero:root etc.) resolve unambiguously.
    """
    transforms = cmds.ls(long=True, type='transform') or []
    return find_duplicate_short_names(transforms)


def fix_mesh_artifacts(meshes: list[str] = None) -> dict:
    """Clean QuadRemesher-style artifacts on the given meshes.

    Fixes per mesh:
      - Non-manifold edges + vertices (one cleanup flag covers both)
      - Lamina faces (two faces sharing all edges)
      - Zero-area faces (below 1e-5 tolerance)
      - Invalid components (stray malformed faces)

    Construction history is deleted after the cleanup pass.

    Args:
      meshes: list of mesh transform names. None reads the live Maya
        selection. Non-mesh transforms in the input are filtered out.

    Returns:
      dict of total counts across all meshes:
        {'non_manifold_edges': int,
         'non_manifold_verts': int,
         'lamina_faces': int}
      Zero-area faces are cleaned but Maya does not expose a clean
      pre-count query, so they are not reported in the dict.

    Raises:
      RuntimeError: nothing in the resolved mesh list (no selection or
        selection contains only non-mesh nodes).
    """
    saved_sel = cmds.ls(selection=True, long=True) or []

    if meshes is None:
        candidates = saved_sel
    else:
        candidates = meshes

    mesh_transforms = [
        n for n in candidates
        if cmds.listRelatives(n, shapes=True, type='mesh',
                               noIntermediate=True, fullPath=True)
    ]
    if not mesh_transforms:
        raise RuntimeError(
            'fix_mesh_artifacts: no mesh in selection. Select the mesh(es) '
            'you want to clean up.'
        )

    totals = {
        'non_manifold_edges': 0,
        'non_manifold_verts': 0,
        'lamina_faces': 0,
    }

    try:
        with undo_chunk('Scene Cleanup: Fix Mesh Artifacts'):
            for mesh in mesh_transforms:
                try:
                    nme = len(cmds.polyInfo(mesh, nonManifoldEdges=True) or [])
                    nmv = len(cmds.polyInfo(mesh, nonManifoldVertices=True) or [])
                    lam = len(cmds.polyInfo(mesh, laminaFaces=True) or [])
                    totals['non_manifold_edges'] += nme
                    totals['non_manifold_verts'] += nmv
                    totals['lamina_faces'] += lam

                    cmds.select(mesh, replace=True)
                    # polyCleanupArgList is MEL-only; not exposed on
                    # maya.cmds. Call via mel.eval with the 18-arg version-4
                    # signature. Toggled here: zeroGeom, zeroMap, sharedUVs,
                    # nonManifold, lamina, invalidComponents. The rest are
                    # off — those are user-intent decisions, not artifact-fix.
                    # sharedUVs splits UVs that are shared across shell
                    # boundaries — cure for "cut a seam but unfold won't
                    # separate", a common QuadRemesher UV-projection issue.
                    args = [
                        "0",          # allMeshes (0 = work on selection)
                        "0",          # selectOnly (0 = fix, 1 = select only)
                        "0",          # historyOn
                        "0",          # quads
                        "0",          # nsided
                        "0",          # concave
                        "0",          # holed
                        "0",          # nonplanar
                        "1",          # zeroGeom
                        "0.000010",   # zeroGeomTol
                        "0",          # zeroEdge
                        "0.000010",   # zeroEdgeTol
                        "1",          # zeroMap
                        "0.000010",   # zeroMapTol
                        "1",          # sharedUVs
                        "1",          # nonManifold
                        "1",          # lamina
                        "1",          # invalidComponents
                    ]
                    mel_array = '{' + ','.join(f'"{a}"' for a in args) + '}'
                    mel.eval(f'polyCleanupArgList 4 {mel_array};')
                    cmds.delete(mesh, constructionHistory=True)
                except Exception as e:
                    cmds.warning(
                        f'fix_mesh_artifacts: skipped {mesh}: {e}'
                    )
    finally:
        if saved_sel:
            cmds.select(saved_sel, replace=True)
        else:
            cmds.select(clear=True)

    n_meshes = len(mesh_transforms)
    summary = (
        f"[scene_cleanup] Fixed {totals['non_manifold_edges']} non-manifold "
        f"edges, {totals['non_manifold_verts']} non-manifold verts, "
        f"{totals['lamina_faces']} lamina faces across {n_meshes} mesh(es)."
    )
    print(summary)
    cmds.inViewMessage(
        msg=(
            f"Cleanup: {totals['non_manifold_edges']} non-manifold edges, "
            f"{totals['non_manifold_verts']} verts, "
            f"{totals['lamina_faces']} lamina"
        ),
        pos='midCenter',
        fade=True,
    )
    return totals


def select_mesh_artifacts(meshes: list[str] = None) -> dict:
    """Read-only diagnostic counterpart to fix_mesh_artifacts.

    Finds non-manifold edges + verts + lamina faces on the given meshes
    via cmds.polyInfo and selects them. Does NOT modify mesh topology.

    Scope is narrower than fix_mesh_artifacts: shared UVs, zero-area UV
    faces, zero-area faces, and invalid components don't have clean
    polyInfo queries, so they are not detected here. Those are still
    handled by fix_mesh_artifacts when you run the auto-fix; for a
    non-destructive A/B, duplicate the mesh first and run fix on the
    duplicate.

    Earlier versions of this function tried to use polyCleanupArgList
    with selectOnly=1, but Maya 2025's polyCleanupArgList runs the
    destructive cleanup regardless of that flag — meshes collapsed to a
    single triangle on Select. polyInfo is the safe, documented path.

    The user's selection is REPLACED with the problem components on exit
    (no restore) — press F afterwards to frame them in the viewport.

    Args:
      meshes: list of mesh transform names. None reads the live Maya
        selection. Non-mesh transforms in the input are filtered out.

    Returns:
        {'non_manifold_edges': int,
         'non_manifold_verts': int,
         'lamina_faces': int}

    Raises:
      RuntimeError: nothing in the resolved mesh list (no selection or
        selection contains only non-mesh nodes).
    """
    if meshes is None:
        candidates = cmds.ls(selection=True, long=True) or []
    else:
        candidates = meshes

    mesh_transforms = [
        n for n in candidates
        if cmds.listRelatives(n, shapes=True, type='mesh',
                               noIntermediate=True, fullPath=True)
    ]
    if not mesh_transforms:
        raise RuntimeError(
            'select_mesh_artifacts: no mesh in selection. Select the mesh(es) '
            'you want to inspect.'
        )

    totals = {
        'non_manifold_edges': 0,
        'non_manifold_verts': 0,
        'lamina_faces': 0,
    }
    components: list[str] = []

    for mesh in mesh_transforms:
        try:
            nme = cmds.polyInfo(mesh, nonManifoldEdges=True) or []
            nmv = cmds.polyInfo(mesh, nonManifoldVertices=True) or []
            lam = cmds.polyInfo(mesh, laminaFaces=True) or []
            totals['non_manifold_edges'] += len(nme)
            totals['non_manifold_verts'] += len(nmv)
            totals['lamina_faces'] += len(lam)
            components.extend(nme)
            components.extend(nmv)
            components.extend(lam)
        except Exception as e:
            cmds.warning(
                f'select_mesh_artifacts: skipped {mesh}: {e}'
            )

    if components:
        cmds.select(components, replace=True)
    else:
        cmds.select(clear=True)

    n_meshes = len(mesh_transforms)
    summary = (
        f"[scene_cleanup] Selected {totals['non_manifold_edges']} non-manifold "
        f"edges, {totals['non_manifold_verts']} non-manifold verts, "
        f"{totals['lamina_faces']} lamina faces across {n_meshes} mesh(es). "
        f"Press F to frame."
    )
    print(summary)
    cmds.inViewMessage(
        msg=(
            f"Selected: {totals['non_manifold_edges']} non-manifold edges, "
            f"{totals['non_manifold_verts']} verts, "
            f"{totals['lamina_faces']} lamina (press F to frame)"
        ),
        pos='midCenter',
        fade=True,
    )
    return totals
