# skinSmooshCmd.py
# Neighbor-average skin weight smoother as an undoable MPx command.
# Uses classic MPx wrapper (maya.OpenMayaMPx) + API 2.0 data calls for speed.

import maya.cmds as cmds
import maya.OpenMaya as om1
import maya.OpenMayaMPx as ompx           # classic MPx (more compatible)
import maya.api.OpenMaya as om            # API 2.0
import maya.api.OpenMayaAnim as oma       # API 2.0

# ----------------- helpers -----------------
def _get_shape_dag_from_component(component_str):
    sel = om.MSelectionList()
    sel.add(component_str)
    dag, comp = sel.getComponent(0)
    if dag.apiType() == om.MFn.kTransform:
        dag.extendToShape()
    return dag, comp

def _find_skin_cluster_for_mesh(mesh_shape_name):
    hist = cmds.listHistory(mesh_shape_name, pdo=True) or []
    skins = cmds.ls(hist, type='skinCluster') or []
    return skins[0] if skins else None

def _mobject_from_name(node_name):
    sl = om.MSelectionList()
    sl.add(node_name)
    return sl.getDependNode(0)

def _get_influence_info(mfn_skin):
    infs = mfn_skin.influenceObjects()
    idxs = om.MIntArray()
    for i in range(len(infs)):
        idxs.append(mfn_skin.indexForInfluenceObject(infs[i]))
    return infs, idxs

def _single_index_component(kind, index):
    comp_obj = om.MFnSingleIndexedComponent().create(kind)
    om.MFnSingleIndexedComponent(comp_obj).addElement(index)
    return comp_obj

def _neighbor_vertex_indices(dag_shape, vtx_index):
    comp_obj = _single_index_component(om.MFn.kMeshVertComponent, vtx_index)
    return om.MItMeshVertex(dag_shape, comp_obj).getConnectedVertices()

def _component_from_indices(indices):
    comp = om.MFnSingleIndexedComponent().create(om.MFn.kMeshVertComponent)
    om.MFnSingleIndexedComponent(comp).addElements(indices)
    return comp

def _weights_snapshot_dict(mfn_skin, dag_shape, indices, num_infs):
    comp = _component_from_indices(indices)
    flat, _ = mfn_skin.getWeights(dag_shape, comp)
    flat = [flat[i] for i in range(len(flat))]
    snap = {}
    for row, vtx in enumerate(indices):
        start = row * num_infs
        snap[vtx] = flat[start:start + num_infs]
    return snap

# ----------------- MPx command -----------------
class SkinSmooshCmd(ompx.MPxCommand):
    """
    Command: skinSmoosh
      -a / -alpha       : blend toward neighbor average (0..1, default 0.3)
      -i / -iterations  : smoothing passes per call (>=1, default 1)
      -n / -normalize   : renormalize each result to sum 1.0 (default True)
    Works on current selection; faces/edges are converted to vertices automatically.
    """
    kCmdName = "skinSmoosh"
    fA, fAL = "-a", "-alpha"
    fN, fNL = "-n", "-normalize"
    fI, fIL = "-i", "-iterations"

    def __init__(self):
        ompx.MPxCommand.__init__(self)
        self._payload = []

    @staticmethod
    def newSyntax():
        syn = om1.MSyntax()
        syn.addFlag(SkinSmooshCmd.fA, SkinSmooshCmd.fAL, om1.MSyntax.kDouble)
        syn.addFlag(SkinSmooshCmd.fN, SkinSmooshCmd.fNL, om1.MSyntax.kBoolean)
        syn.addFlag(SkinSmooshCmd.fI, SkinSmooshCmd.fIL, om1.MSyntax.kLong)
        syn.enableEdit(False); syn.enableQuery(False)
        return syn

    def isUndoable(self):
        return True

    def doIt(self, args):
        alpha = 0.3
        normalize = True
        iterations = 1
        adb = om1.MArgDatabase(self.syntax(), args)
        if adb.isFlagSet(self.fA): alpha = adb.flagArgumentDouble(self.fA, 0)
        if adb.isFlagSet(self.fN): normalize = bool(adb.flagArgumentBool(self.fN, 0))
        if adb.isFlagSet(self.fI): iterations = adb.flagArgumentInt(self.fI, 0)
        alpha = max(0.0, min(1.0, alpha))
        iterations = max(1, iterations)

        selected_components = cmds.ls(selection=True, flatten=True) or []
        verts_any = cmds.polyListComponentConversion(selected_components, toVertex=True) or []
        verts = cmds.ls(verts_any, fl=True) or []
        if not verts:
            om1.MGlobal.displayError("Select one or more mesh vertices (or convert-able components).")
            return

        by_shape = {}
        for v in verts:
            dag_shape, comp = _get_shape_dag_from_component(v)
            shape_name = cmds.ls(dag_shape.fullPathName(), sn=True)[0]
            idxs = om.MFnSingleIndexedComponent(comp).getElements()
            if not idxs:
                continue
            key = dag_shape.fullPathName()
            if key not in by_shape:
                by_shape[key] = {"dag": dag_shape, "name": shape_name, "indices": []}
            by_shape[key]["indices"].append(idxs[0])

        if not by_shape:
            om1.MGlobal.displayError("No valid mesh vertices found.")
            return

        self._payload = []

        for key, data in by_shape.items():
            dag_shape = data["dag"]
            shape_name = data["name"]
            indices_sel = sorted(set(data["indices"]))

            skin = _find_skin_cluster_for_mesh(shape_name)
            if not skin:
                om1.MGlobal.displayWarning("No skinCluster on {}. Skipping.".format(shape_name))
                continue

            mfn_skin = oma.MFnSkinCluster(_mobject_from_name(skin))
            infs, inf_idxs = _get_influence_info(mfn_skin)
            num_infs = len(infs)

            comp_sel = _component_from_indices(indices_sel)
            orig_flat, _ = mfn_skin.getWeights(dag_shape, comp_sel)
            orig_flat = om.MDoubleArray(orig_flat)

            nbr_union = set(indices_sel)
            for i in indices_sel:
                nbr_union.update(_neighbor_vertex_indices(dag_shape, i))
            snap = _weights_snapshot_dict(mfn_skin, dag_shape, sorted(nbr_union), num_infs)

            # Per-vert original (orig_flat already holds these flat for undo;
            # keep a copy to report total change after all passes).
            orig_sel = {i: list(snap[i]) for i in indices_sel}

            # Iterative Jacobi smooth: each pass moves every selected vert toward
            # its CURRENT neighbor average, then commits for the next pass. N passes
            # = a real smoosh; one pass on an already-smooth vert barely moves it.
            for _ in range(iterations):
                updated = {}
                for i in indices_sel:
                    neighbors = _neighbor_vertex_indices(dag_shape, i)
                    present = [snap[n] for n in neighbors if snap.get(n) is not None]
                    if not present:
                        updated[i] = list(snap[i])
                        continue
                    sums = [0.0] * num_infs
                    for wn in present:
                        for k in range(num_infs):
                            sums[k] += wn[k]
                    cnt = float(len(present))            # divide by PRESENT count -> avg sums to 1.0
                    avg = [s / cnt for s in sums]
                    w_orig = snap[i]
                    w_new = [(1.0 - alpha) * o + alpha * a for o, a in zip(w_orig, avg)]
                    if normalize:
                        s = sum(w_new)
                        if s > 1e-12:
                            w_new = [w / s for w in w_new]   # exact sum=1 -> no Maya normalize warning
                    updated[i] = w_new
                snap.update(updated)

            new_flat = om.MDoubleArray()
            total_delta = 0.0
            for i in indices_sel:
                w_final = snap[i]
                w_orig = orig_sel[i]
                for k in range(num_infs):
                    total_delta += abs(w_final[k] - w_orig[k])
                    new_flat.append(w_final[k])

            if total_delta == 0.0:
                om1.MGlobal.displayWarning(
                    "skinSmoosh: No change on {} (neighbors identical or fully locked).".format(shape_name)
                )

            self._payload.append({
                "dag": dag_shape,
                "skinObj": _mobject_from_name(skin),
                "comp_sel": comp_sel,
                "inf_idxs": inf_idxs,
                "orig_flat": orig_flat,
                "new_flat": new_flat,
            })

        self.redoIt()

    def redoIt(self):
        # normalize=False: the plugin already renormalized each result to sum 1.0,
        # so Maya has nothing to modify (kills the "required modification for
        # normalization" warning) and won't re-touch the weights.
        for item in self._payload:
            oma.MFnSkinCluster(item["skinObj"]).setWeights(
                item["dag"], item["comp_sel"], item["inf_idxs"],
                item["new_flat"], False
            )

    def undoIt(self):
        for item in self._payload:
            oma.MFnSkinCluster(item["skinObj"]).setWeights(
                item["dag"], item["comp_sel"], item["inf_idxs"],
                item["orig_flat"], False
            )

# --- creator ---
def _cmdCreator():
    if hasattr(ompx, "asMPxPtr"):
        return ompx.asMPxPtr(SkinSmooshCmd())
    return SkinSmooshCmd()

# --- plugin boilerplate ---
def initializePlugin(mobject):
    try:
        plugin = ompx.MFnPlugin(mobject)
        plugin.registerCommand(SkinSmooshCmd.kCmdName, _cmdCreator, SkinSmooshCmd.newSyntax)
    except Exception as e:
        om1.MGlobal.displayError("Failed to register skinSmoosh: {}".format(e))
        raise

def uninitializePlugin(mobject):
    try:
        plugin = ompx.MFnPlugin(mobject)
        plugin.deregisterCommand(SkinSmooshCmd.kCmdName)
    except Exception as e:
        om1.MGlobal.displayError("Failed to deregister skinSmoosh: {}".format(e))
        raise
