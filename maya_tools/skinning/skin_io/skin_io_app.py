
import json
import maya.cmds as cmds
import maya.mel as mel

from maya_tools.skinning import combine_skinned as cs

try:
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaAnim as oma
except Exception:
    om  = None
    oma = None


class JsonSkinIO:
    """
    Save/load skin weights to/from JSON with optional transfer via reconstructed source mesh.

    Save captures:
      - Mesh name
      - Vert positions (world space)
      - Topology (polyCounts, polyConnects)
      - Influences (full dag path, short name, type, worldMatrix)
      - Per-vertex weights (dense, ordered by saved influence list)
      - Basic skinCluster settings

    Load modes:
      - mode="direct": write weights 1:1 (requires same vertex count/order)
      - mode="transfer": rebuild saved source mesh, apply saved weights to it, then copySkinWeights to target

    Example:
        io = JsonSkinIO()
        io.save("pSphere1", "C:/tmp/sphere_weights.json")
        io.load("myDestMesh", "C:/tmp/sphere_weights.json", mode="transfer",
                surface_association="closestPoint", influence_association=("name","label"))
    """

    # ---------------------- Public API ----------------------

    def save(self, mesh, json_path):
        shape = self._get_mesh_shape(mesh)
        if not shape:
            raise RuntimeError("No mesh shape found on: {}".format(mesh))

        sc = self._find_skin_cluster(shape)
        if not sc:
            raise RuntimeError("No skinCluster found for: {}".format(shape))

        data = {}
        data["schema_version"] = 1
        data["mesh"] = cmds.ls(shape, l=True)[0]
        data["skinCluster"] = sc

        # skinCluster settings
        data["skin_settings"] = self._get_skincluster_settings(sc)

        # Mesh topology + points
        topo = self._get_mesh_topology_and_points(shape)
        data.update(topo)  # adds: vertex_count, poly_counts, poly_connects, points_ws

        # Influences (order matters for weights!)
        infl_info, infl_paths = self._get_influences(sc)
        data["influences"] = infl_info  # list of dicts with fullPath, shortName, type, worldMatrix

        # Weights (dense per-vtx, ordered by influences list)
        weights_by_vtx = self._get_weights_dense(sc, shape, len(infl_paths), data["vertex_count"])
        data["weights"] = weights_by_vtx  # [[w_i for infls] for each vertex]

        # Write file
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print("Saved skin JSON:", json_path)

    def load(self, target_mesh, json_path, mode="direct",
             surface_association="closestPoint",
             influence_association=("name", "label"),
             create_missing_influences=False,
             vert_indices=None):
        """
        Args:
            target_mesh (str): transform or shape of the destination mesh
            json_path (str): path to saved JSON
            mode (str): "direct" or "transfer"
            surface_association (str): for copySkinWeights ("closestPoint", "rayCast", "closestComponent")
            influence_association (tuple[str]): methods for pairing influences on copy ("name","label","closestJoint","oneToOne")
            create_missing_influences (bool): if True, create placeholder joints when a saved influence name can’t be found
            vert_indices (list[int] | None): if provided, scope the load to only these
                vertex indices on the target. None = whole mesh (default).
        """
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        t_shape = self._get_mesh_shape(target_mesh)
        if not t_shape:
            raise RuntimeError("No mesh shape found on: {}".format(target_mesh))

        if mode not in ("direct", "transfer"):
            raise ValueError('mode must be "direct" or "transfer"')

        if mode == "direct":
            self._load_direct(t_shape, data,
                              create_missing_influences=create_missing_influences,
                              vert_indices=vert_indices)
        else:
            self._load_transfer(t_shape, data,
                                surface_association=surface_association,
                                influence_association=influence_association,
                                create_missing_influences=create_missing_influences,
                                vert_indices=vert_indices)

    # ---------------------- Save helpers ----------------------

    def _get_mesh_shape(self, node):
        if not cmds.objExists(node):
            return None
        shapes = cmds.listRelatives(node, s=True, ni=True, f=True) or []
        if shapes:
            # Prefer the first visible mesh shape
            for s in shapes:
                if cmds.nodeType(s) == "mesh":
                    return s
        # Maybe node is the shape already
        if cmds.nodeType(node) == "mesh":
            return cmds.ls(node, l=True)[0]
        return None

    def _find_skin_cluster(self, shape):
        # mel findRelatedSkinCluster is reliable
        try:
            sc = mel.eval('findRelatedSkinCluster "{}"'.format(shape))
        except Exception:
            sc = None
        if sc and cmds.objExists(sc):
            return sc
        # Fallback: history search
        history = cmds.listHistory(shape, pdo=True) or []
        for h in history:
            if cmds.nodeType(h) == "skinCluster":
                return h
        return None

    def _get_skincluster_settings(self, sc):
        def _ga(attr, default=None):
            a = "{}.{}".format(sc, attr)
            return cmds.getAttr(a) if cmds.objExists(a) else default

        return {
            "normalizeWeights": _ga("normalizeWeights", 1),
            "maintainMaxInfluences": _ga("maintainMaxInfluences", True),
            "maxInfluences": _ga("maxInfluences", 4),
            "skinningMethod": _ga("skinningMethod", 0),  # 0: Classic Linear, 1: Dual Quaternion, 2: Weight Blended
        }

    def _get_mesh_topology_and_points(self, shape):
        if not om:
            # Slow fallback via cmds (not recommended for big meshes)
            vtx_ids = cmds.getAttr(shape + ".vrts", multiIndices=True) or []
            points = []
            for i in vtx_ids:
                wp = cmds.xform("{}.vtx[{}]".format(shape, i), q=True, ws=True, t=True)
                points.append([wp[0], wp[1], wp[2]])
            poly_counts = cmds.getAttr(shape + ".fc", s=True)
            # Reconstructing polyCounts/polyConnects via cmds is cumbersome; rely on API for correctness.
            raise RuntimeError("OpenMaya API required for topology export. Please use Maya 2016+ with maya.api.OpenMaya.")
        # API path
        dag = self._as_dag_path(shape)
        fn = om.MFnMesh(dag)
        vcount = fn.numVertices
        pts = fn.getPoints(om.MSpace.kWorld)
        points_ws = [[p.x, p.y, p.z] for p in pts]

        poly_counts, poly_connects = fn.getVertices()  # returns (MIntArray, MIntArray)
        return {
            "vertex_count": vcount,
            "points_ws": points_ws,
            "poly_counts": list(poly_counts),
            "poly_connects": list(poly_connects),
        }

    def _get_influences(self, sc):
        sc_mobj = self._as_mobject(sc)
        fn_skin = oma.MFnSkinCluster(sc_mobj)
        infl_paths = fn_skin.influenceObjects()
        info = []
        for ipath in infl_paths:
            dag_fn = om.MFnDagNode(ipath)
            full = dag_fn.fullPathName()
            short = dag_fn.name()
            wmat = ipath.inclusiveMatrix()
            # Store as flat 16 list (row-major)
            wm = [wmat[r * 4 + c] for r in range(4) for c in range(4)]
            info.append({
                "fullPath": full,
                "shortName": short,
                "type": cmds.nodeType(short),
                "worldMatrix": wm,
            })
        return info, infl_paths

    def _get_weights_dense(self, sc, shape, infl_count, vtx_count):
        sc_mobj = self._as_mobject(sc)
        fn_skin = oma.MFnSkinCluster(sc_mobj)
        mesh_dag = self._as_dag_path(shape)

        comp_fn = om.MFnSingleIndexedComponent()
        comp = comp_fn.create(om.MFn.kMeshVertComponent)
        comp_fn.addElements(list(range(vtx_count)))

        weights, inf_count_from_fn = fn_skin.getWeights(mesh_dag, comp)
        if inf_count_from_fn != infl_count:
            raise RuntimeError("Influence count mismatch while reading weights.")

        # Flattened: [v0_i0, v0_i1, ... v0_iN, v1_i0, ...]
        out = []
        idx = 0
        for _ in range(vtx_count):
            row = [float(weights[idx + j]) for j in range(infl_count)]
            out.append(row)
            idx += infl_count
        return out

    # ---------------------- Load: DIRECT ----------------------

    def _load_direct(self, t_shape, data, create_missing_influences=False,
                     vert_indices=None):
        saved_vcount = data["vertex_count"]
        t_vcount = self._mesh_vertex_count(t_shape)

        if vert_indices is None:
            # Whole-mesh path requires identical vert counts.
            if t_vcount != saved_vcount:
                raise RuntimeError(
                    "Direct mode requires identical vertex count/order.\nSaved: {}  Target: {}".format(
                        saved_vcount, t_vcount
                    )
                )
        else:
            # Subset path: every selected vert index must be in-range on both
            # saved and target meshes. Mesh vert counts don't need to match
            # so long as the selected indices fit on each side.
            if not vert_indices:
                raise RuntimeError("vert_indices is empty — nothing to load.")
            bad = [i for i in vert_indices if i < 0 or i >= saved_vcount or i >= t_vcount]
            if bad:
                raise RuntimeError(
                    "Direct (subset) mode: {} vertex index/indices out of range "
                    "(saved={}, target={}). First bad: {}".format(
                        len(bad), saved_vcount, t_vcount, bad[:5]
                    )
                )

        # Ensure skinCluster
        sc = self._find_skin_cluster(t_shape)
        if not sc:
            # Bind with saved influences
            infl_paths_resolved = self._resolve_influences(data["influences"], create=create_missing_influences)
            if not infl_paths_resolved:
                raise RuntimeError("No matching influences found to bind the target.")
            sc = cmds.skinCluster(infl_paths_resolved, t_shape,
                                  tsb=True,
                                  mi=data["skin_settings"].get("maxInfluences", 4),
                                  nw=data["skin_settings"].get("normalizeWeights", 1),
                                  sm=data["skin_settings"].get("skinningMethod", 0))[0]
        else:
            # Optionally align basic settings
            self._apply_skin_settings(sc, data["skin_settings"])

        # Map saved influences -> current skinCluster influences
        saved_infl_names = [inf["fullPath"] for inf in data["influences"]]
        current_infl_paths = self._skin_influences_paths(sc)
        mapping = self._map_influences_by_name(saved_infl_names, current_infl_paths)

        cur_inf_count = len(current_infl_paths)
        target_indices = list(vert_indices) if vert_indices is not None else list(range(saved_vcount))
        n_rows = len(target_indices)
        flat = [0.0] * (n_rows * cur_inf_count)

        for row_idx, vi in enumerate(target_indices):
            saved_row = data["weights"][vi]
            for saved_idx, cur_idx in mapping.items():
                if cur_idx is None:
                    continue
                flat[row_idx * cur_inf_count + cur_idx] = float(saved_row[saved_idx])

        sc_mobj = self._as_mobject(sc)
        fn_skin = oma.MFnSkinCluster(sc_mobj)
        mesh_dag = self._as_dag_path(t_shape)

        comp_fn = om.MFnSingleIndexedComponent()
        comp = comp_fn.create(om.MFn.kMeshVertComponent)
        comp_fn.addElements(target_indices)

        inf_indices = om.MIntArray(list(range(cur_inf_count)))
        weights = om.MDoubleArray(flat)
        fn_skin.setWeights(mesh_dag, comp, inf_indices, weights, True)
        if vert_indices is not None:
            print("Applied direct weights to {} vert(s) on: {}".format(n_rows, t_shape))
        else:
            print("Applied direct weights to:", t_shape)

    # ---------------------- Load: TRANSFER ----------------------

    def _load_transfer(self, t_shape, data,
                       surface_association="closestPoint",
                       influence_association=("name", "label"),
                       create_missing_influences=False,
                       vert_indices=None):
        # 1) Rebuild saved source mesh under a temp transform
        src_xform = cmds.createNode("transform", name="jsonSkinSrc#")
        src_shape = self._rebuild_mesh_under_transform(src_xform, data["points_ws"],
                                                       data["poly_counts"], data["poly_connects"])

        # 2) Bind & apply saved weights to the reconstructed mesh
        infl_paths_resolved = self._resolve_influences(data["influences"], create=create_missing_influences)
        if not infl_paths_resolved:
            cmds.delete(src_xform)
            raise RuntimeError("No matching influences found to bind the reconstructed source mesh.")

        # Bind source
        src_sc = cmds.skinCluster(infl_paths_resolved, src_shape,
                                  tsb=True,
                                  mi=data["skin_settings"].get("maxInfluences", 4),
                                  nw=data["skin_settings"].get("normalizeWeights", 1),
                                  sm=data["skin_settings"].get("skinningMethod", 0))[0]

        # Write saved weights to source
        self._apply_dense_weights(src_sc, src_shape, data["weights"], data["influences"])

        # 3) Ensure target has a skinCluster (bind if missing)
        dst_sc = self._find_skin_cluster(t_shape)
        if not dst_sc:
            # Try to bind with *existing* joints that match saved names; otherwise bind with all resolved
            # This makes copySkinWeights influence matching via "name"/"label" work.
            dst_infls = self._resolve_influences(data["influences"], create=create_missing_influences)
            if not dst_infls:
                cmds.delete(src_xform)
                raise RuntimeError("No matching influences found to bind the destination mesh.")
            dst_sc = cmds.skinCluster(dst_infls, t_shape,
                                      tsb=True,
                                      mi=data["skin_settings"].get("maxInfluences", 4),
                                      nw=data["skin_settings"].get("normalizeWeights", 1),
                                      sm=data["skin_settings"].get("skinningMethod", 0))[0]
        else:
            self._apply_skin_settings(dst_sc, data["skin_settings"])

        # 4) Copy weights: reconstructed source -> target
        ia = list(influence_association) if isinstance(influence_association, (list, tuple)) else [influence_association]
        if vert_indices:
            # copySkinWeights with explicit ss/ds operates on the whole mesh
            # regardless of component selection. To scope to a vert subset,
            # snapshot the complement verts' weights, do the whole-mesh copy,
            # then restore the complement. Deterministic — no reliance on
            # Maya's selection-based source/destination inference.
            t_vcount = self._mesh_vertex_count(t_shape)
            bad = [i for i in vert_indices if i < 0 or i >= t_vcount]
            if bad:
                cmds.delete(src_xform)
                raise RuntimeError(
                    "Transfer (subset) mode: {} vert index/indices out of range "
                    "on target ({} verts). First bad: {}".format(
                        len(bad), t_vcount, bad[:5]
                    )
                )
            keep_indices = sorted(set(range(t_vcount)) - set(vert_indices))
            keep_inf_count, keep_flat = self._read_subset_weights(
                dst_sc, t_shape, keep_indices,
            )
            cmds.copySkinWeights(ss=src_sc, ds=dst_sc,
                                 noMirror=True,
                                 surfaceAssociation=surface_association,
                                 influenceAssociation=ia)
            if keep_indices:
                self._write_subset_weights(
                    dst_sc, t_shape, keep_indices, keep_inf_count, keep_flat,
                )
        else:
            cmds.copySkinWeights(ss=src_sc,
                                 ds=dst_sc,
                                 noMirror=True,
                                 surfaceAssociation=surface_association,
                                 influenceAssociation=ia)

        # 5) Cleanup
        cmds.delete(src_xform)
        if vert_indices:
            print("Transferred weights to {} vert(s) on: {}".format(len(vert_indices), t_shape))
        else:
            print("Transferred weights to:", t_shape)

    # ---------------------- Internals ----------------------

    def _apply_skin_settings(self, sc, settings):
        try:
            if "normalizeWeights" in settings:
                cmds.setAttr(sc + ".normalizeWeights", settings["normalizeWeights"])
            if "maintainMaxInfluences" in settings:
                cmds.setAttr(sc + ".maintainMaxInfluences", bool(settings["maintainMaxInfluences"]))
            if "maxInfluences" in settings:
                cmds.setAttr(sc + ".maxInfluences", int(settings["maxInfluences"]))
            if "skinningMethod" in settings:
                cmds.setAttr(sc + ".skinningMethod", int(settings["skinningMethod"]))
        except Exception:
            pass

    def _mesh_vertex_count(self, shape):
        if om:
            return om.MFnMesh(self._as_dag_path(shape)).numVertices
        return len(cmds.getAttr(shape + ".vrts", multiIndices=True) or [])

    def _apply_dense_weights(self, sc, shape, dense_rows, saved_infls):
        """Apply [[w...], ...] ordered by saved_infls onto (sc,shape) using influence name mapping."""
        sc_mobj = self._as_mobject(sc)
        fn_skin = oma.MFnSkinCluster(sc_mobj)
        mesh_dag = self._as_dag_path(shape)

        cur_paths = self._skin_influences_paths(sc)
        saved_names = [i["fullPath"] for i in saved_infls]
        mapping = self._map_influences_by_name(saved_names, cur_paths)

        vtx_count = self._mesh_vertex_count(shape)
        cur_inf_count = len(cur_paths)

        flat = [0.0] * (vtx_count * cur_inf_count)
        for vi in range(vtx_count):
            row = dense_rows[vi]
            for saved_idx, cur_idx in mapping.items():
                if cur_idx is None:
                    continue
                flat[vi * cur_inf_count + cur_idx] = float(row[saved_idx])

        comp_fn = om.MFnSingleIndexedComponent()
        comp = comp_fn.create(om.MFn.kMeshVertComponent)
        comp_fn.addElements(list(range(vtx_count)))

        inf_indices = om.MIntArray(list(range(cur_inf_count)))
        weights = om.MDoubleArray(flat)
        fn_skin.setWeights(mesh_dag, comp, inf_indices, weights, True)

    def _skin_influences_paths(self, sc):
        sc_mobj = self._as_mobject(sc)
        fn_skin = oma.MFnSkinCluster(sc_mobj)
        return fn_skin.influenceObjects()

    def _read_subset_weights(self, sc, shape, vert_indices):
        """Read flat per-vertex weights for `vert_indices` on (sc, shape).
        Returns (inf_count, flat_weights_list). Empty vert_indices => (0, [])."""
        if not vert_indices:
            return 0, []
        sc_mobj = self._as_mobject(sc)
        fn_skin = oma.MFnSkinCluster(sc_mobj)
        mesh_dag = self._as_dag_path(shape)

        comp_fn = om.MFnSingleIndexedComponent()
        comp = comp_fn.create(om.MFn.kMeshVertComponent)
        comp_fn.addElements(list(vert_indices))

        weights, inf_count = fn_skin.getWeights(mesh_dag, comp)
        return inf_count, list(weights)

    def _write_subset_weights(self, sc, shape, vert_indices, inf_count, flat_weights):
        """Write `flat_weights` back to `vert_indices` on (sc, shape).

        Assumes the skinCluster's influence list/order is unchanged from when
        the weights were read (which is true after copySkinWeights — it does
        not add or reorder influences on the destination)."""
        if not vert_indices or inf_count == 0:
            return
        sc_mobj = self._as_mobject(sc)
        fn_skin = oma.MFnSkinCluster(sc_mobj)
        mesh_dag = self._as_dag_path(shape)

        comp_fn = om.MFnSingleIndexedComponent()
        comp = comp_fn.create(om.MFn.kMeshVertComponent)
        comp_fn.addElements(list(vert_indices))

        inf_indices = om.MIntArray(list(range(inf_count)))
        weights = om.MDoubleArray(flat_weights)
        fn_skin.setWeights(mesh_dag, comp, inf_indices, weights, True)

    def _map_influences_by_name(self, saved_full_paths, current_paths):
        """
        Returns dict: saved_idx -> current_idx (or None if not found).
        Matches by exact full path first; falls back to basename match (namespace tolerant).
        """
        cur_full = [om.MFnDagNode(p).fullPathName() for p in current_paths]
        cur_base_to_index = {}
        for i, p in enumerate(current_paths):
            base = om.MFnDagNode(p).name().split(":")[-1]
            cur_base_to_index.setdefault(base, i)

        mapping = {}
        for sidx, sname in enumerate(saved_full_paths):
            # exact
            try:
                exact_idx = cur_full.index(sname)
                mapping[sidx] = exact_idx
                continue
            except ValueError:
                pass
            # basename fallback
            base = sname.split("|")[-1].split(":")[-1]
            mapping[sidx] = cur_base_to_index.get(base, None)
        return mapping

    def _resolve_influences(self, saved_infl_list, create=False):
        """
        Given data["influences"], try to find joints in scene.
        Returns list of full dag paths. If create=True, make missing joints at saved worldMatrix.
        """
        out = []
        for inf in saved_infl_list:
            full = inf["fullPath"]
            short = inf["shortName"]
            base = short.split(":")[-1]

            found = None
            # Try exact
            if cmds.objExists(full):
                found = cmds.ls(full, l=True)[0]
            # Try by short (with namespace)
            if not found and cmds.objExists(short):
                found = cmds.ls(short, l=True)[0]
            # Try by basename
            if not found:
                cands = cmds.ls(base, l=True, type="joint") or []
                if cands:
                    found = cands[0]

            if not found and create:
                # Create a joint at saved worldMatrix (if provided)
                j = cmds.createNode("joint", name=base + "_fromJson#")
                wm = inf.get("worldMatrix")
                if wm and len(wm) == 16:
                    self._apply_world_matrix(j, wm)
                found = cmds.ls(j, l=True)[0]

            if found:
                out.append(found)
        return out

    def _apply_world_matrix(self, node, mat16):
        """Set world matrix by decomposing to TRS (simple, robust)."""
        # 4x4 row-major
        # Extract translate
        tx, ty, tz = mat16[12], mat16[13], mat16[14]
        cmds.xform(node, ws=True, t=(tx, ty, tz))
        # Approximate rotation/scale via API if available
        if om:
            xform = om.MTransformationMatrix(om.MMatrix(mat16))
            eul   = xform.rotation(asQuaternion=False)
            cmds.xform(node, ws=True, ro=(
                om.MAngle(eul.x).asDegrees(),
                om.MAngle(eul.y).asDegrees(),
                om.MAngle(eul.z).asDegrees(),
            ))
            cmds.xform(node, r=False, s=xform.scale(om.MSpace.kWorld))

    def _rebuild_mesh_under_transform(self, parent_xform, points_ws, poly_counts, poly_connects):
        if not om:
            raise RuntimeError("OpenMaya API required to reconstruct mesh.")
        sel = om.MSelectionList()
        sel.add(parent_xform)
        parent_obj = sel.getDependNode(0)

        mpoints = om.MPointArray()
        for p in points_ws:
            mpoints.append(om.MPoint(p[0], p[1], p[2], 1.0))

        counts = om.MIntArray(poly_counts)
        connects = om.MIntArray(poly_connects)

        fn_mesh = om.MFnMesh()
        mesh_obj = fn_mesh.create(mpoints, counts, connects, parent=parent_obj)
        # Name it nicely
        new_shape = om.MFnDagNode(mesh_obj).fullPathName()
        # Return a *shape* long path string Maya understands
        return cmds.ls(new_shape, l=True)[0]

    # ---------------------- OM helpers ----------------------

    def _as_mobject(self, node):
        sel = om.MSelectionList()
        sel.add(node)
        return sel.getDependNode(0)

    def _as_dag_path(self, node):
        sel = om.MSelectionList()
        sel.add(node)
        return sel.getDagPath(0)


# ---------------------- Quick usage helpers ----------------------

def save_skin(mesh, path):
    JsonSkinIO().save(mesh, path)


def load_skin(mesh, path, mode="direct",
              surface_association="closestPoint",
              influence_association=("name", "label"),
              create_missing_influences=False,
              vert_indices=None):
    JsonSkinIO().load(mesh, path, mode=mode,
                      surface_association=surface_association,
                      influence_association=influence_association,
                      create_missing_influences=create_missing_influences,
                      vert_indices=vert_indices)


def save_skin_from_meshes(meshes: list[str], path: str) -> None:
    """Save weights for one or more meshes to `path`.

    Single-mesh: delegates to `save_skin`.
    Multi-mesh: combines the meshes (originals preserved), saves the combined
    mesh, then deletes the combined mesh. Selection is restored on exit.
    """
    if not meshes:
        raise ValueError("Select at least one mesh.")

    # Pre-validate: scan for unskinned meshes so the user can see all of them at once
    io = JsonSkinIO()
    unskinned = []
    for m in meshes:
        shape = io._get_mesh_shape(m)
        if not shape or not io._find_skin_cluster(shape):
            unskinned.append(m)
    if unskinned:
        print("Unskinned meshes (skinCluster required before saving):")
        for m in unskinned:
            print(f"  {m}")
        cmds.select(unskinned, replace=True)
        raise RuntimeError(
            f"{len(unskinned)} mesh(es) have no skinCluster — "
            "see Script Editor for the list, selection updated to highlight them."
        )

    if len(meshes) == 1:
        save_skin(meshes[0], path)
        return

    saved_sel = cmds.ls(sl=True) or []
    try:
        cmds.select(meshes, replace=True)
        combined = cs.combine_skinned_meshes()
        try:
            save_skin(combined, path)
        finally:
            if cmds.objExists(combined):
                cmds.delete(combined)
    finally:
        still_alive = cmds.ls(saved_sel) or []
        if still_alive:
            cmds.select(still_alive, replace=True)
        else:
            cmds.select(clear=True)


def load_skin_to_meshes(targets: list[str], path: str, mode: str = "direct",
                        surface_association: str = "closestPoint",
                        influence_association=("name", "label"),
                        create_missing_influences: bool = False,
                        vert_indices=None) -> None:
    """Load weights from `path` onto one or more target meshes.

    Single-target: delegates to `load_skin`.
    Multi-target: iterates `load_skin` per target. `mode="direct"` is rejected
    for multi-target — use Transfer for round-tripping a combined-mesh save.

    vert_indices: when set, restricts the load to those vertex indices on the
    single target mesh. Multi-target is rejected — vert-scope is a single-mesh
    operation.
    """
    if not targets:
        raise ValueError("Select at least one target mesh.")
    if mode == "direct" and len(targets) > 1:
        raise ValueError(
            "Direct mode requires a single target — use Transfer for multi-mesh loads."
        )
    if vert_indices and len(targets) > 1:
        raise ValueError(
            "Vert-scoped load is single-mesh only — select verts on one mesh."
        )
    for t in targets:
        load_skin(t, path, mode=mode,
                  surface_association=surface_association,
                  influence_association=influence_association,
                  create_missing_influences=create_missing_influences,
                  vert_indices=vert_indices)
