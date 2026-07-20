"""
Combine and separate skinned meshes while preserving exact skin weights.

  combine_skinned_meshes() — unite selected skinned meshes into one, exact weights preserved
  separate_skinned_mesh()  — split a multi-shell skinned mesh into pieces, exact weights preserved
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

from maya_tools.framework.decorators import undo_chunk


# ── private helpers ──────────────────────────────────────────────────────────

def _basename(full_path: str) -> str:
    return full_path.rsplit('|', 1)[-1].rsplit(':', 1)[-1]


def _get_shape(transform: str) -> str:
    shapes = cmds.listRelatives(transform, shapes=True, type='mesh', noIntermediate=True) or []
    if not shapes:
        raise RuntimeError(f"'{transform}' has no mesh shape.")
    return shapes[0]


def _find_skin(shape: str) -> str:
    hist = cmds.listHistory(shape, pdo=True) or []
    skins = cmds.ls(hist, type='skinCluster') or []
    if not skins:
        raise RuntimeError(f"No skinCluster on '{shape}'.")
    return skins[0]


def _read_mesh_data(transform: str) -> dict:
    """Read all skinning data for a mesh transform into memory."""
    shape = _get_shape(transform)
    skin = _find_skin(shape)

    vtx_count = cmds.polyEvaluate(transform, vertex=True)
    max_infs = cmds.skinCluster(skin, q=True, maximumInfluences=True)
    normalize = cmds.skinCluster(skin, q=True, normalizeWeights=True)
    skin_method = cmds.skinCluster(skin, q=True, skinMethod=True)

    sl = om.MSelectionList()
    sl.add(skin)
    mfn_skin = oma.MFnSkinCluster(sl.getDependNode(0))

    sl2 = om.MSelectionList()
    sl2.add(shape)
    dag = sl2.getDagPath(0)

    fn_comp = om.MFnSingleIndexedComponent()
    comp = fn_comp.create(om.MFn.kMeshVertComponent)
    fn_comp.addElements(list(range(vtx_count)))

    inf_paths = list(mfn_skin.influenceObjects())
    num_infs = len(inf_paths)
    inf_names = [_basename(p.fullPathName()) for p in inf_paths]

    flat, _ = mfn_skin.getWeights(dag, comp)
    weights = [
        [float(flat[v * num_infs + i]) for i in range(num_infs)]
        for v in range(vtx_count)
    ]

    return {
        'shape': shape,
        'skin': skin,
        'inf_paths': inf_paths,       # list[MDagPath]
        'inf_names': inf_names,       # list[str] — basenames
        'vtx_count': vtx_count,
        'weights': weights,           # list[list[float]] — [vtx][inf_idx]
        'max_infs': max_infs,
        'normalize': normalize,
        'skin_method': skin_method,
    }


def _bind_and_set_weights(
    transform: str,
    inf_paths: list,
    weight_rows: list,
    skin_settings: dict,
) -> str:
    """Bind transform to inf_paths, apply weight_rows (one list[float] per vertex)."""
    inf_dag_names = [p.fullPathName() for p in inf_paths]
    vtx_count = len(weight_rows)

    skin = cmds.skinCluster(
        inf_dag_names + [transform],
        tsb=True,
        mi=skin_settings['max_infs'],
        nw=skin_settings['normalize'],
        sm=skin_settings['skin_method'],
    )[0]

    sl = om.MSelectionList()
    sl.add(skin)
    new_mfn = oma.MFnSkinCluster(sl.getDependNode(0))

    shape = _get_shape(transform)
    sl2 = om.MSelectionList()
    sl2.add(shape)
    dag = sl2.getDagPath(0)

    fn_comp = om.MFnSingleIndexedComponent()
    comp = fn_comp.create(om.MFn.kMeshVertComponent)
    fn_comp.addElements(list(range(vtx_count)))

    # Query influence logical indices from the NEW skinCluster
    inf_indices = om.MIntArray([new_mfn.indexForInfluenceObject(p) for p in inf_paths])

    flat = []
    for row in weight_rows:
        flat.extend(row)

    new_mfn.setWeights(dag, comp, inf_indices, om.MDoubleArray(flat), True)
    return skin


# ── public API ───────────────────────────────────────────────────────────────

def combine_skinned_meshes() -> str:
    """Combine all selected skinned meshes into one, preserving exact skin weights.

    Originals are left bound and untouched. The combine operates on duplicates,
    which polyUnite then consumes into the new combined mesh. Time is held at
    frame 0 (the default T-pose) for the duplicate + bind so the new
    skinCluster captures correct bind matrices from joints at rest.
    Undoable as a single step.
    """
    sel = cmds.ls(sl=True, type='transform') or []
    meshes = [s for s in sel if cmds.listRelatives(s, shapes=True, type='mesh', noIntermediate=True)]
    if len(meshes) < 2:
        raise ValueError("Select at least two skinned meshes to combine.")

    mesh_data = [_read_mesh_data(m) for m in meshes]

    # Union influence list — ordered, deduplicated
    seen: dict = {}
    for data in mesh_data:
        for path, name in zip(data['inf_paths'], data['inf_names']):
            if name not in seen:
                seen[name] = path
    all_inf_names = list(seen.keys())
    all_inf_paths = list(seen.values())
    num_all_infs = len(all_inf_names)
    inf_name_to_idx = {n: i for i, n in enumerate(all_inf_names)}

    with undo_chunk("Combine Skinned Meshes"):
        saved_time = cmds.currentTime(q=True)
        cmds.currentTime(0)
        try:
            duplicates = [cmds.duplicate(m, returnRootsOnly=True)[0] for m in meshes]

            result = cmds.polyUnite(*duplicates, ch=False, mergeUVSets=True, centerPivot=True)
            combined = result[0]
            # polyUnite ch=False consumes the duplicates — originals remain bound and untouched.

            # Build concatenated weight rows remapped to union influence ordering
            combined_rows = []
            for data in mesh_data:
                for vtx_weights in data['weights']:
                    row = [0.0] * num_all_infs
                    for local_i, name in enumerate(data['inf_names']):
                        row[inf_name_to_idx[name]] = vtx_weights[local_i]
                    combined_rows.append(row)

            first = mesh_data[0]
            _bind_and_set_weights(combined, all_inf_paths, combined_rows, {
                'max_infs': first['max_infs'],
                'normalize': first['normalize'],
                'skin_method': first['skin_method'],
            })
        finally:
            cmds.currentTime(saved_time)

        cmds.select(combined)
        total_vtx = sum(d['vtx_count'] for d in mesh_data)
        cmds.inViewMessage(
            msg=f"Combined {len(meshes)} meshes - {total_vtx} verts, {num_all_infs} influences",
            pos='midCenter', fade=True,
        )

    return combined


def separate_skinned_mesh() -> list:
    """Separate a multi-shell skinned mesh into individual meshes, preserving exact weights.

    Unbinds first (geometry snaps to bind pose), then captures vertex world positions to
    build a position→original-index map. After polySeparate, each new mesh's vertices are
    matched back to original indices by position — exact to 5 decimal places.

    Note: meshes with coincident vertices (two verts at the same world position)
    are not supported; the second vert will get zero weights.
    Undoable as a single step.
    """
    sel = cmds.ls(sl=True, type='transform') or []
    mesh_sel = [s for s in sel if cmds.listRelatives(s, shapes=True, type='mesh', noIntermediate=True)]
    if len(mesh_sel) != 1:
        raise ValueError("Select exactly one skinned mesh to separate.")

    mesh = mesh_sel[0]
    num_shells = cmds.polyEvaluate(mesh, shell=True)
    if num_shells < 2:
        raise ValueError(f"'{mesh}' has only {num_shells} shell — nothing to separate.")

    data = _read_mesh_data(mesh)
    skin_settings = {
        'max_infs': data['max_infs'],
        'normalize': data['normalize'],
        'skin_method': data['skin_method'],
    }

    def _round_key(p):
        return (round(p[0], 5), round(p[1], 5), round(p[2], 5))

    with undo_chunk("Separate Skinned Mesh"):
        # Unbind first — geometry snaps to bind pose
        cmds.skinCluster(data['skin'], edit=True, unbind=True)

        # Capture bind pose vertex positions AFTER unbind, BEFORE separating
        pos_to_orig = {
            _round_key(cmds.pointPosition(f"{mesh}.vtx[{i}]", world=True)): i
            for i in range(data['vtx_count'])
        }

        raw_result = cmds.polySeparate(mesh, ch=False) or []

        # Normalise result to unique transform nodes that have mesh shapes
        pieces = []
        seen_transforms: set = set()
        for item in raw_result:
            if not cmds.objExists(item):
                continue
            t = item if cmds.objectType(item) == 'transform' else \
                (cmds.listRelatives(item, parent=True, type='transform') or [None])[0]
            if t and t not in seen_transforms and \
               cmds.listRelatives(t, shapes=True, type='mesh', noIntermediate=True):
                pieces.append(t)
                seen_transforms.add(t)

        # Delete original if it was not returned as one of the pieces
        if cmds.objExists(mesh) and mesh not in seen_transforms:
            if not cmds.listRelatives(mesh, shapes=True, noIntermediate=True):
                cmds.delete(mesh)

        num_infs = len(data['inf_paths'])
        zero_row = [0.0] * num_infs

        for piece in pieces:
            piece_vtx_count = cmds.polyEvaluate(piece, vertex=True)
            rows = []
            for v in range(piece_vtx_count):
                pos = cmds.pointPosition(f"{piece}.vtx[{v}]", world=True)
                orig_idx = pos_to_orig.get(_round_key(pos))
                rows.append(data['weights'][orig_idx] if orig_idx is not None else zero_row)

            _bind_and_set_weights(piece, data['inf_paths'], rows, skin_settings)

        cmds.select(pieces)
        cmds.inViewMessage(
            msg=f"Separated into {len(pieces)} meshes",
            pos='midCenter', fade=True,
        )

    return pieces
