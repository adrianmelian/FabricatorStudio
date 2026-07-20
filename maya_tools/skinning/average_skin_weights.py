# Average skin weights across a set of vertices and apply the same
# averaged weights back to all of them.
# Works best on hard-surface edges/panels that must deform uniformly.
#
# Usage:
#   average_skin_weights(["pCube1.vtx[0:10]"])
#   # or, to run on your current selection (any components -> verts):
#   average_weights_on_current_selection()

import maya.cmds as cmds
import maya.api.OpenMaya as om

def _as_dag_and_component(verts):
    """
    Build a single DAG path and a single vertex component from a list
    of vertex strings (can include ranges). Ensures all verts are on the same mesh.
    """
    expanded = cmds.ls(verts, fl=True) or []
    expanded = [v for v in expanded if ".vtx[" in v]
    if not expanded:
        raise ValueError("No valid mesh vertices found in input.")

    # Use the first vert to get the shape dagPath
    sel = om.MSelectionList()
    sel.add(expanded[0])
    dagPath0, comp0 = sel.getComponent(0)  # comp0 is a vertex component
    # Ensure we point to the shape
    dagPath0 = dagPath0  # already the shape path for a vertex component

    # Confirm all verts belong to the same mesh
    for v in expanded[1:]:
        s = om.MSelectionList()
        s.add(v)
        dp, _ = s.getComponent(0)
        if dp.fullPathName() != dagPath0.fullPathName():
            raise ValueError("All vertices must be on the same mesh/shape.")

    # Build a single component that contains all indices
    fn_comp = om.MFnSingleIndexedComponent()
    comp = fn_comp.create(om.MFn.kMeshVertComponent)

    # Add indices from the expanded list
    # (each is a single-index vtx string like "meshShape.vtx[123]")
    idxs = om.MIntArray()
    for v in expanded:
        idx = int(v.split('[')[-1].rstrip(']'))
        idxs.append(idx)
    fn_comp.addElements(idxs)

    return dagPath0, comp, list(idxs)

def _find_skin_cluster_mobj(mesh_shape_path):
    """
    Find the first skinCluster upstream of the mesh shape and return its MObject.
    """
    mesh_name = mesh_shape_path.fullPathName()
    hist = cmds.listHistory(mesh_name, pdo=True) or []
    skins = [n for n in hist if cmds.nodeType(n) == "skinCluster"]
    if not skins:
        return None
    sc = skins[0]  # nearest in history
    sel = om.MSelectionList()
    sel.add(sc)
    return sel.getDependNode(0)

def average_skin_weights(verts, normalize=True):
    """
    Average weights across the given vertex components and apply back to all
    those vertices. Returns a dict of {influenceName: averagedWeight}.
    """
    dagPath, comp, idxs = _as_dag_and_component(verts)
    skin_mobj = _find_skin_cluster_mobj(dagPath)
    if skin_mobj is None:
        raise RuntimeError("No skinCluster found on mesh: {}".format(dagPath.fullPathName()))

    skin_fn = om.MFnSkinCluster(skin_mobj)

    # Get influence objects & their logical indices on the skinCluster
    inf_paths = skin_fn.influenceObjects()
    inf_count = len(inf_paths)
    if inf_count == 0:
        raise RuntimeError("SkinCluster has no influences.")

    inf_indices = om.MIntArray([skin_fn.indexForInfluenceObject(p) for p in inf_paths])

    # Query weights for the component (weights flattened: [v0_i0, v0_i1, ..., v1_i0, ...])
    weights, inf_count_got = skin_fn.getWeights(dagPath, comp)
    if inf_count_got != inf_count:
        # Rare, but keep defensively aligned with what the SK returns
        inf_count = inf_count_got

    num_verts = len(idxs)
    if num_verts * inf_count != len(weights):
        raise RuntimeError("Unexpected weights array size from getWeights().")

    # Sum per-influence and average
    sums = [0.0] * inf_count
    for v in range(num_verts):
        base = v * inf_count
        for i in range(inf_count):
            sums[i] += weights[base + i]

    avg = [s / float(num_verts) for s in sums]

    # Optional normalization for safety (usually already sums ~ 1.0)
    if normalize:
        total = sum(avg)
        if total > 1e-12:
            avg = [w / total for w in avg]
        else:
            raise RuntimeError("Averaged weights sum to zero; cannot normalize.")

    # Build the new weight array by repeating the averaged vector for each vertex
    new_weights = om.MDoubleArray()
    for _ in range(num_verts):
        for w in avg:
            new_weights.append(w)

    # Apply in one call (fast). normalize=True to ensure perfect sum per vertex.
    skin_fn.setWeights(dagPath, comp, inf_indices, new_weights, True)

    # Return a friendly mapping for feedback/logging
    result = {p.fullPathName(): w for p, w in zip(inf_paths, avg)}
    return result

def average_weights_on_current_selection():
    """
    Convenience wrapper: converts current selection to vertices and averages.
    """
    sel = cmds.ls(sl=True) or []
    if not sel:
        raise ValueError("Nothing selected.")
    verts = cmds.ls(cmds.polyListComponentConversion(sel, tv=True), fl=True) or []
    if not verts:
        raise ValueError("Selection contains no mesh vertices.")
    return average_skin_weights(verts)