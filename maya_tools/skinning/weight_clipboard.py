"""
Vertex-level skin weight clipboard for Maya.

  record_weights()  — store weights from selected verts to JSON file
                      single vert: exact values; multi-vert: averaged
  paste_weights()   — apply stored weights to selected verts (undoable)
  average_weights() — average selected verts' weights and apply back (undoable, no JSON)
"""

import os
import json

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

from maya_tools.framework.decorators import undo_chunk

def _clipboard_path() -> str:
    """Return the clipboard JSON path, rooted in Maya's user app directory."""
    return os.path.join(cmds.internalVar(userAppDir=True), 'am_weight_clipboard.json')


# ── private helpers ──────────────────────────────────────────────────────────

def _basename(full_path: str) -> str:
    """Strip DAG path and namespace — return leaf name."""
    return full_path.rsplit('|', 1)[-1].rsplit(':', 1)[-1]


def _verts_from_selection() -> list[str]:
    sel = cmds.ls(sl=True) or []
    if not sel:
        raise ValueError("Nothing selected. Select at least one mesh vertex.")
    converted = cmds.polyListComponentConversion(sel, toVertex=True) or []
    verts = cmds.ls(converted, fl=True) or []
    if not verts:
        raise ValueError("Selection contains no mesh vertices.")
    return verts


def _dag_and_comp(verts: list[str]) -> tuple[om.MDagPath, om.MObject]:
    """Build MDagPath (mesh shape) and component for a flat vertex list.

    Only processes verts belonging to the first mesh found in the list.
    """
    mesh_str = verts[0].split('.vtx[')[0]

    sl = om.MSelectionList()
    sl.add(mesh_str)
    dag = sl.getDagPath(0)
    if dag.apiType() == om.MFn.kTransform:
        dag.extendToShape()

    indices = [
        int(v.split('[')[1].rstrip(']'))
        for v in verts
        if v.split('.vtx[')[0] == mesh_str
    ]

    fn_comp = om.MFnSingleIndexedComponent()
    comp_obj = fn_comp.create(om.MFn.kMeshVertComponent)
    fn_comp.addElements(indices)
    return dag, comp_obj


def _skin_fn_for_dag(dag: om.MDagPath) -> oma.MFnSkinCluster:
    hist = cmds.listHistory(dag.fullPathName(), pdo=True) or []
    skins = cmds.ls(hist, type='skinCluster') or []
    if not skins:
        raise RuntimeError(f"No skinCluster found on '{_basename(dag.fullPathName())}'.")
    sl = om.MSelectionList()
    sl.add(skins[0])
    return oma.MFnSkinCluster(sl.getDependNode(0))


def _compute_average(
    mfn_skin: oma.MFnSkinCluster,
    dag: om.MDagPath,
    comp: om.MObject,
) -> tuple[list[str], list[float]]:
    """Return (inf_basenames, averaged_weight_vector) for the given component."""
    inf_paths = mfn_skin.influenceObjects()
    num_infs = len(inf_paths)
    inf_names = [_basename(p.fullPathName()) for p in inf_paths]

    flat, _ = mfn_skin.getWeights(dag, comp)
    flat_list = [flat[i] for i in range(len(flat))]

    num_verts = om.MFnSingleIndexedComponent(comp).elementCount

    sums = [0.0] * num_infs
    for v in range(num_verts):
        base = v * num_infs
        for i in range(num_infs):
            sums[i] += flat_list[base + i]

    avg = [s / num_verts for s in sums]
    return inf_names, avg


# ── public API ───────────────────────────────────────────────────────────────

def record_weights() -> dict:
    """Store skin weights from the current vertex selection to disk.

    Single vert → exact values. Multiple verts → per-influence average.
    Returns the stored {influence_basename: weight} dict.
    """
    verts = _verts_from_selection()
    dag, comp = _dag_and_comp(verts)
    mfn_skin = _skin_fn_for_dag(dag)
    mesh_name = _basename(dag.fullPathName())

    inf_names, avg = _compute_average(mfn_skin, dag, comp)

    filtered = {n: w for n, w in zip(inf_names, avg) if w > 1e-6}

    data = {'mesh': mesh_name, 'influences': filtered}
    with open(_clipboard_path(), 'w') as fh:
        json.dump(data, fh, indent=2)

    label = "1 vert" if len(verts) == 1 else f"{len(verts)} verts (averaged)"
    cmds.inViewMessage(
        msg=f"Recorded weights from {label} — {len(filtered)} active influences",
        pos='midCenter', fade=True
    )
    return filtered


def paste_weights() -> list[str]:
    """Apply clipboard weights to the current vertex selection.

    If the target skinCluster is missing any stored influences, selects the
    missing joints in the scene and warns — no weights are applied until they
    are added as influences (Skin > Edit Influences > Add Influence).
    Undoable.
    """
    if not os.path.exists(_clipboard_path()):
        raise RuntimeError(
            f"No weight clipboard at '{_clipboard_path()}'. Run Record Weights first."
        )

    with open(_clipboard_path()) as fh:
        data = json.load(fh)
    stored: dict[str, float] = data['influences']

    verts = _verts_from_selection()
    dag, comp = _dag_and_comp(verts)
    mfn_skin = _skin_fn_for_dag(dag)

    inf_paths = mfn_skin.influenceObjects()
    current_names = {_basename(p.fullPathName()) for p in inf_paths}

    # Check for missing influences (only those with non-trivial stored weight)
    missing = [n for n, w in stored.items() if w > 1e-6 and n not in current_names]
    if missing:
        missing_nodes = []
        for name in missing:
            hits = cmds.ls(name, type='joint') or cmds.ls(f'*:{name}', type='joint') or []
            if hits:
                missing_nodes.append(hits[0])
        if missing_nodes:
            cmds.select(missing_nodes)
        cmds.warning(
            f"paste_weights: {len(missing)} influence(s) not bound to target mesh: "
            f"{missing}. Missing joint(s) selected — go to bind pose and run "
            "Skin > Edit Influences > Add Influence."
        )
        return []

    # Build weight vector across ALL current influences (0.0 for non-clipboard ones)
    weight_row = [stored.get(_basename(p.fullPathName()), 0.0) for p in inf_paths]
    total = sum(weight_row)
    if total < 1e-12:
        raise RuntimeError("Clipboard weights sum to zero — clipboard may be corrupt.")
    weight_row = [w / total for w in weight_row]

    inf_indices = om.MIntArray([mfn_skin.indexForInfluenceObject(p) for p in inf_paths])
    num_verts = len(verts)
    new_weights = om.MDoubleArray()
    for _ in range(num_verts):
        for w in weight_row:
            new_weights.append(w)

    with undo_chunk("Paste Skin Weights"):
        mfn_skin.setWeights(dag, comp, inf_indices, new_weights, True)

    cmds.inViewMessage(
        msg=f"Pasted weights to {num_verts} vert(s)",
        pos='midCenter', fade=True
    )
    return verts


def average_weights() -> dict:
    """Average skin weights across selected vertices and apply the result back
    to all of them. Nothing written to disk. Undoable.

    Returns the averaged {influence_basename: weight} dict.
    """
    verts = _verts_from_selection()
    dag, comp = _dag_and_comp(verts)
    mfn_skin = _skin_fn_for_dag(dag)

    inf_paths = mfn_skin.influenceObjects()
    inf_names, avg = _compute_average(mfn_skin, dag, comp)

    inf_indices = om.MIntArray([mfn_skin.indexForInfluenceObject(p) for p in inf_paths])
    num_verts = len(verts)
    new_weights = om.MDoubleArray()
    for _ in range(num_verts):
        for w in avg:
            new_weights.append(w)

    with undo_chunk("Average Skin Weights"):
        mfn_skin.setWeights(dag, comp, inf_indices, new_weights, True)

    filtered = {n: w for n, w in zip(inf_names, avg) if w > 1e-6}
    cmds.inViewMessage(
        msg=f"Averaged weights across {num_verts} vert(s) — {len(filtered)} active influences",
        pos='midCenter', fade=True
    )
    return filtered
