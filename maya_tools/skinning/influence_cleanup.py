# maya_tools/skinning/influence_cleanup.py
"""Skinning utilities — prune influences from skinned meshes.

Public API:
    remove_unused_influences(meshes=None, threshold=0.001) -> dict
        Per-mesh removal of influences whose max per-vertex weight is at or
        below `threshold`. Bulk-reads weights via OpenMaya for speed.

    remove_unused_from_selection_with_feedback(threshold=0.001) -> None
        Shelf-friendly wrapper: runs against the current selection, prints
        a per-mesh summary, and surfaces totals via cmds.inViewMessage.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as omAnim


DEFAULT_THRESHOLD = 0.001


def remove_unused_influences(meshes: list[str] | None = None,
                             threshold: float = DEFAULT_THRESHOLD) -> dict:
    """For each skinned mesh, remove influences whose max per-vertex weight
    is at or below `threshold` (default 0.001).

    meshes: list of mesh transforms or shapes. When None, resolves from the
        current viewport selection (transforms descend to their mesh shapes).
    threshold: max-weight cutoff. An influence is removed when its max weight
        across all vertices is ≤ threshold.

    Returns:
        {mesh_short_name: {'removed': [influences], 'note': str}}

    Skinned meshes only — non-skinned meshes are recorded in the summary
    with `note='no skinCluster'` and skipped. cmds.skinCluster errors during
    removal are captured in `note` rather than raised, so one bad mesh
    doesn't abort the rest.
    """
    if meshes is None:
        meshes = _meshes_from_selection()
    if not meshes:
        raise RuntimeError(
            'remove_unused_influences: select one or more skinned meshes first.'
        )

    summary: dict = {}
    for mesh in meshes:
        short = mesh.split('|')[-1]
        sc = _find_skin_cluster(mesh)
        if not sc:
            summary[short] = {'removed': [], 'note': 'no skinCluster'}
            continue
        unused = _find_unused_influences(sc, threshold)
        if not unused:
            summary[short] = {'removed': [], 'note': ''}
            continue
        try:
            cmds.skinCluster(sc, edit=True, removeInfluence=unused)
            summary[short] = {'removed': unused, 'note': ''}
        except Exception as exc:
            summary[short] = {'removed': [], 'note': f'error: {exc}'}
    return summary


def remove_unused_from_selection_with_feedback(
        threshold: float = DEFAULT_THRESHOLD) -> None:
    """Shelf-friendly wrapper around remove_unused_influences.

    Runs against the current viewport selection, prints per-mesh detail to
    the script editor, and posts a midCenter inViewMessage with the total
    count. cmds.warning on a no-selection RuntimeError instead of raising.
    """
    try:
        summary = remove_unused_influences(threshold=threshold)
    except RuntimeError as exc:
        cmds.warning(str(exc))
        return

    total = 0
    for mesh, info in summary.items():
        if info['removed']:
            print(
                f'[remove_unused_influences] {mesh}: removed '
                f'{len(info["removed"])} -> {", ".join(info["removed"])}'
            )
            total += len(info['removed'])
        elif info['note']:
            print(f'[remove_unused_influences] {mesh}: {info["note"]}')

    msg = (f'<hl>Removed {total} unused influence(s)</hl>'
           if total else 'No unused influences found.')
    cmds.inViewMessage(amg=msg, pos='midCenter', fade=True)


# ─── private ─────────────────────────────────────────────────────────────────

def _meshes_from_selection() -> list[str]:
    """Selection → mesh shape long-paths. Transforms descend to their mesh shapes."""
    sel = cmds.ls(selection=True, long=True) or []
    out: list[str] = []
    for n in sel:
        if cmds.nodeType(n) == 'mesh':
            out.append(n)
        elif cmds.nodeType(n) == 'transform':
            shapes = cmds.listRelatives(n, shapes=True, type='mesh',
                                        fullPath=True) or []
            out.extend(shapes)
    return out


def _find_skin_cluster(mesh: str) -> str | None:
    """Return the skinCluster bound to `mesh`, or None."""
    history = cmds.listHistory(mesh, pruneDagObjects=True) or []
    clusters = cmds.ls(history, type='skinCluster') or []
    return clusters[0] if clusters else None


def _find_unused_influences(skin_cluster: str, threshold: float) -> list[str]:
    """Return influence names whose max weight across all vertices is ≤ threshold."""
    max_weights = _max_weight_per_influence(skin_cluster)
    return [inf for inf, mw in max_weights.items() if mw <= threshold]


def _max_weight_per_influence(skin_cluster: str) -> dict[str, float]:
    """Bulk-read every weight via OpenMaya; return {influence_name: max_weight}.

    Single API getWeights call is dramatically faster than per-vertex
    cmds.skinPercent loops on dense meshes.
    """
    sel = om.MSelectionList()
    sel.add(skin_cluster)
    sc_fn = omAnim.MFnSkinCluster(sel.getDependNode(0))

    geom = cmds.skinCluster(skin_cluster, query=True, geometry=True) or []
    if not geom:
        return {}
    geo_sel = om.MSelectionList()
    geo_sel.add(geom[0])
    geo_dag = geo_sel.getDagPath(0)
    geo_dag.extendToShape()
    if not geo_dag.hasFn(om.MFn.kMesh):
        # NURBS / lattice / subdiv skinning could be added later; rare for us.
        return {}

    mesh_fn = om.MFnMesh(geo_dag)
    n_verts = mesh_fn.numVertices

    comp_fn = om.MFnSingleIndexedComponent()
    comp = comp_fn.create(om.MFn.kMeshVertComponent)
    comp_fn.addElements(om.MIntArray(list(range(n_verts))))

    weights, n_inf = sc_fn.getWeights(geo_dag, comp)
    # weights is a flat MDoubleArray of length n_verts * n_inf.

    inf_paths = sc_fn.influenceObjects()
    max_w = [0.0] * n_inf
    for v in range(n_verts):
        base = v * n_inf
        for i in range(n_inf):
            w = weights[base + i]
            if w > max_w[i]:
                max_w[i] = w
    return {
        inf_paths[i].partialPathName(): max_w[i]
        for i in range(n_inf)
    }
