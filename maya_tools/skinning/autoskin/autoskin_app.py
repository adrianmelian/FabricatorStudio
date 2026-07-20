# maya_tools/skinning/autoskin/autoskin_app.py
"""AutoSkin — Maya-side application layer (headless; no Qt).

The tool's whole gesture lives here:

    select bind joints + mesh(es)  ->  bind_skin()  ->  the mesh is skinned

Steps 1-2 and 5-6 of the pipeline (spec §3). Steps 3-4 — the engine — are
on the far side of a subprocess, in runner.py.

    1. validate   selection, GPU, backend health, that the mesh sits on the
                  skeleton, and that the skeleton is at its bind pose. Fail
                  early, name the fix.
    2. export     duplicate the mesh, write a temp FBX with the joints, and
                  a probe of Maya's own vertex order. The user's nodes are
                  never selected, moved or edited.
    5. apply      read the weights back AS DATA and write them straight onto
                  a skinCluster through the API.
    6. cleanup    delete the temp files. The scene ends with the user's mesh
                  skinned and nothing else added.

Everything runs in ONE undo chunk: a single Ctrl+Z puts the scene back.

WEIGHTS COME HOME AS DATA, NOT AS A RIG. The backend used to hand Maya a
skinned proxy FBX to copySkinWeights from. It worked, and the artist paid
for it every run: an FBX import dialog, a "joint rotations are locked"
prompt (the proxy's joints collide by name with the real, rig-driven ones),
a namespace dance to stop the proxy replacing the real mesh in place, and a
slow FBX write+read on the end of a job that was already two minutes long.
Weights are numbers. They travel as numbers now (Adrian, 2026-07-13).

Rules here that are contractual, each bought with a real incident or a
measurement:

WE ONLY EVER WRITE INTO A skinCluster WE CREATED IN THIS UNDO CHUNK. The
API weight write (MFnSkinCluster.setWeights) is ~100x faster than any
command-based path — 30ms against 3.5s on a 7.5k mesh, and ~0.8s against
~92s at 200k — but it is NOT on Maya's undo queue (measured; Ctrl+Z simply
does not see it). Writing into a cluster that already existed would
therefore break undo silently. Creating the cluster inside the chunk fixes
that completely: undoing the CREATION deletes the node and takes the
weights with it. A rebind unbinds the old cluster first, so undo restores
the original cluster, its influences, its weights and its locks, exactly
(all measured, 2026-07-13). If you ever make this write into a pre-existing
skinCluster, you have broken Ctrl+Z and no test will tell you.

VERTEX INDICES ARE MAYA'S — AND THAT IS PROVED, NOT ASSUMED. The weights
come back keyed by vertex index, which is only sound because Maya's vertex
i survives the FBX round-trip as bpy's vertex i (7,559/7,559 on a real
character). Rather than trust that forever, write_vertex_probe() sends
Maya's own vertex positions with every job and the backend re-proves the
correspondence on the artist's actual mesh, refusing to return weights it
cannot vouch for. A scrambled skin that looks plausible is far worse than a
job that stops and says why.

AN ARTIST'S LOCKS ARE HONORED, never worked around. A locked influence
means "I weighted this by hand, leave it alone" — so its weights are read
BEFORE the rebind, carried across untouched, and the new prediction is
normalized into whatever weight is left over.

ONE MESH PER GENERATION. Multi-mesh loops rather than combining: a combined
proxy bleeds weights between meshes that share space, and the engine mangles
a skeleton it is asked to explain across disjoint bodies.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

import array
import json
import shutil
import tempfile
from pathlib import Path

import maya.cmds as cmds
from maya.api import OpenMaya as om
from maya.api import OpenMayaAnim as oma

from maya_tools.framework.decorators import undo_chunk
from maya_tools.skinning.autoskin import backend, runner

MAX_INFLUENCES = 4

# How far the skeleton may drift from the pose a skinCluster was bound at
# before we call the rig "posed". Unitless on the rotation part; scaled by
# the rig's size on the translation part, so it means the same thing on a
# 2-unit prop and a 200-unit character.
BIND_POSE_TOL = 1e-3


class AutoSkinError(RuntimeError):
    """User-facing failure. The message names the fix wherever one exists."""


# ─────────────────────────────────────────────────────────────────────────────
# API handles
# ─────────────────────────────────────────────────────────────────────────────

def _shape_of(node: str) -> str:
    if cmds.nodeType(node) == 'mesh':
        return node
    shapes = cmds.listRelatives(node, shapes=True, type='mesh',
                                noIntermediate=True, fullPath=True) or []
    return shapes[0] if shapes else ''


def _dag_shape(mesh: str) -> om.MDagPath:
    shape = _shape_of(mesh)
    if not shape:
        raise AutoSkinError(f'{mesh.split("|")[-1]!r} has no mesh shape.')
    sel = om.MSelectionList()
    sel.add(shape)
    return sel.getDagPath(0)


def _skin_fn(skin: str) -> oma.MFnSkinCluster:
    sel = om.MSelectionList()
    sel.add(skin)
    return oma.MFnSkinCluster(sel.getDependNode(0))


def _all_vertices(dag: om.MDagPath) -> om.MObject:
    cf = om.MFnSingleIndexedComponent()
    comp = cf.create(om.MFn.kMeshVertComponent)
    cf.setCompleteData(om.MFnMesh(dag).numVertices)
    return comp


def _influences(fn: oma.MFnSkinCluster) -> tuple:
    """(dag paths, PHYSICAL index array, {leaf name: column}) for a skinCluster.

    THE INDEX SPACE IS PHYSICAL — 0..n-1, the position in influenceObjects() —
    and NOT the logical matrix-array index that indexForInfluenceObject()
    returns. On a cluster built in one shot the two are identical, which is why
    getting this wrong survives every test written against a fresh bind. On the
    ARTIST'S cluster they are not: a single removeInfluence leaves a hole, and
    then the two disagree forever.

    Measured on Maya 2025 with influences [j0 j1 j3 j4 j5] (logical [0,1,3,4,5]):
      - getWeights given logical index 3 returned j4's weight, not j3's. It
        indexes PHYSICALLY, and it does so SILENTLY — the wrong joint's weights,
        no error. That shipped as a real bug in preserve_locked.
      - setWeights given the same logical array raised kInvalidParameter, since
        5 is out of range for 5 influences. Loud, at least.

    One helper, one index space, so the mistake cannot be made twice.
    """
    infl = fn.influenceObjects()
    columns = {}
    for k, i in enumerate(infl):
        columns[_leaf(i.fullPathName())] = k
    return infl, om.MIntArray(range(len(infl))), columns


def _leaf(node: str) -> str:
    return node.split('|')[-1].split(':')[-1]


def _skin_cluster_of(mesh: str) -> str:
    shape = _shape_of(mesh)
    if not shape:
        return ''
    for node in cmds.listHistory(shape, pruneDagObjects=True) or []:
        if cmds.nodeType(node) == 'skinCluster':
            return node
    return ''


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — validate
# ─────────────────────────────────────────────────────────────────────────────

def _joints_bbox(joints: list) -> list:
    """The world box enclosing the joint PIVOTS.

    Built from xform positions, not exactWorldBoundingBox: a joint has no
    geometry of its own, and its DAG bounding box is whatever happens to
    hang beneath it (or nothing at all) — which made an honest skeleton
    read as 'somewhere else' and rejected a mesh that sat right on it.
    Pivots are what a skeleton actually IS.
    """
    pts = [cmds.xform(j, q=True, ws=True, t=True) for j in joints]
    return [min(p[i] for p in pts) for i in range(3)] + \
           [max(p[i] for p in pts) for i in range(3)]


def _bbox_overlap(a: list, b: list, tol: float = 1e-4) -> bool:
    return not (a[3] < b[0] - tol or b[3] < a[0] - tol or
                a[4] < b[1] - tol or b[4] < a[1] - tol or
                a[5] < b[2] - tol or b[5] < a[2] - tol)


def bind_pose_drift(mesh: str, skin: str) -> float:
    """How far the skeleton has moved since `skin` was bound.

    A skinCluster records each influence's world INVERSE matrix at bind time
    (bindPreMatrix), so `world x bindPreMatrix` is the identity at bind pose
    and drifts away from it as the rig is posed. Returns the worst deviation,
    with the translation part scaled by the rig's size so the number is
    comparable across characters.

    This matters twice over. The engine is given the mesh as it stands, so a
    posed character produces weights for a pose rather than a rest shape —
    that was always true. And a rebind now unbinds and re-creates the
    skinCluster (the price of a fast, undoable weight write), which on a
    posed rig would bake the POSE in as the new bind pose. Better to stop and
    say so.
    """
    fn = _skin_fn(skin)
    infl = fn.influenceObjects()
    if not infl:
        return 0.0

    bb = _joints_bbox([i.fullPathName() for i in infl])
    size = max(bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]) or 1.0

    ident = om.MMatrix()
    worst = 0.0
    for i in infl:
        idx = fn.indexForInfluenceObject(i)
        try:
            pre = cmds.getAttr(f'{skin}.bindPreMatrix[{idx}]')
        except (RuntimeError, ValueError):
            continue
        if not pre or len(pre) != 16:
            continue
        world = om.MMatrix(cmds.xform(i.fullPathName(), q=True, ws=True,
                                      matrix=True))
        delta = world * om.MMatrix(pre)
        for k in range(16):
            d = abs(delta[k] - ident[k])
            # Row 3 (12..15) is the translation row in Maya's convention.
            worst = max(worst, d / size if k >= 12 else d)
    return worst


def split_selection(selection=None) -> tuple:
    """(joints, meshes) from a selection, in the smooth-bind convention:
    the user selects their bind joints and their mesh(es), in any order."""
    sel = selection if selection is not None else (cmds.ls(sl=True, long=True) or [])
    joints, meshes = [], []
    for node in sel:
        if cmds.nodeType(node) == 'joint':
            joints.append(node)
        elif _shape_of(node):
            meshes.append(node)
    return joints, meshes


def validate(selection=None, generate_joints: bool = False) -> tuple:
    """Return (joints, meshes) or raise AutoSkinError naming the fix."""
    health = backend.health()
    if not health['ready']:
        raise AutoSkinError(health['reason'])

    joints, meshes = split_selection(selection)
    if not meshes:
        raise AutoSkinError(
            'Select a mesh to skin (and the bind joints, unless Generate '
            'Joints is ticked).')
    if not generate_joints and not joints:
        raise AutoSkinError(
            'Select your bind joints along with the mesh — the same way you '
            'would smooth-bind. Or tick Generate Joints to have AutoSkin '
            'build a skeleton for you.')
    if generate_joints and joints:
        raise AutoSkinError(
            'Generate Joints builds a NEW skeleton, so select only the '
            'mesh — not joints.')

    # Generate Joints handles exactly ONE mesh. The multi-mesh loop below only
    # runs in bind mode, so a two-mesh Generate Joints would export BOTH meshes
    # into the generation FBX while probing only the first — a guaranteed vertex
    # -count mismatch that surfaces after the ~2-minute GPU run, having wasted
    # it. There is also no meaningful answer to give: one skeleton explaining
    # two disjoint bodies is what mangled an 80-joint character into 78.
    if generate_joints and len(meshes) > 1:
        raise AutoSkinError(
            'Generate Joints builds one skeleton for one mesh. Select a single '
            'mesh, or combine them first.')

    # Bind joint names must be unique: the engine hands weights back keyed by
    # bone NAME, and two joints called 'wrist' make that ambiguous.
    if joints:
        seen = {}
        for j in joints:
            seen.setdefault(_leaf(j), []).append(j)
        dupes = sorted(n for n, js in seen.items() if len(js) > 1)
        if dupes:
            raise AutoSkinError(
                'These bind joints share a name, so AutoSkin cannot tell them '
                'apart: ' + ', '.join(dupes[:6]) +
                ('...' if len(dupes) > 6 else '') + '. Rename them first.')

    # The mesh and the skeleton must actually occupy the same space. Hand
    # the engine a mesh that sits away from its joints and it does not
    # fail — it prunes joints it cannot explain and returns a mangled
    # skeleton (an 80-joint character came back as 78, live 2026-07-12).
    # Catch it here, where we can say something useful, instead of letting
    # the artist discover it in the deformation.
    if joints:
        jb = _joints_bbox(joints)
        for m in meshes:
            if not _bbox_overlap(jb, cmds.exactWorldBoundingBox(m)):
                raise AutoSkinError(
                    f'{m.split("|")[-1]!r} does not overlap the selected '
                    f'joints — AutoSkin cannot skin a mesh to a skeleton '
                    f'that is somewhere else. Check that you selected the '
                    f'right skeleton, and that the mesh is not offset from '
                    f'it.')

    # A rebind on a POSED rig would learn the pose and bake it in. See
    # bind_pose_drift.
    for m in meshes:
        skin = _skin_cluster_of(m)
        if skin and bind_pose_drift(m, skin) > BIND_POSE_TOL:
            raise AutoSkinError(
                f'{m.split("|")[-1]!r} is already skinned and its skeleton is '
                f'not at the bind pose. AutoSkin reads the character\'s rest '
                f'shape, so put the rig back at bind pose before regenerating '
                f'the weights.')

    return joints, meshes


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — export the generation mesh + the vertex probe
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_fbx_plugin() -> None:
    if not cmds.pluginInfo('fbxmaya', q=True, loaded=True):
        cmds.loadPlugin('fbxmaya', quiet=True)


def export_generation_fbx(joints: list, meshes: list, path: Path) -> None:
    """Write the temp FBX the engine reads: the user's mesh(es), combined
    into ONE generation mesh, plus the joints.

    The mesh is DUPLICATED first — the engine gets a copy, and the user's
    own geometry is never selected, moved, combined, or otherwise touched
    by this.

    bind_skin sends ONE mesh per call (multi-mesh loops), so the combine
    below is a safety net for a direct caller, not the normal path — see
    bind_skin for why combining proved to be the wrong idea.
    """
    import maya.mel as mel
    _ensure_fbx_plugin()

    dupes = []
    combined = ''
    try:
        for m in meshes:
            d = cmds.duplicate(m, name=f'autoskin_gen_{len(dupes)}',
                               renameChildren=True)[0]
            # Leave the duplicate under its original parent. World-parenting
            # it (as this first did) compensates the MESH into world space
            # while the joints are exported still sitting under whatever
            # group they live in — so a rig under a non-identity group
            # exported mesh and skeleton in two different spaces, and the
            # engine received a character whose bones did not match its
            # body (caught in review, 2026-07-12). Both must be handled the
            # same way, and leaving both alone is the way that cannot skew.
            cmds.delete(d, constructionHistory=True)   # a deformer stack would export deformed
            dupes.append(d)

        if len(dupes) > 1:
            combined = cmds.polyUnite(dupes, name='autoskin_gen_combined',
                                      constructionHistory=False)[0]
            gen_meshes = [combined]
        else:
            gen_meshes = dupes

        cmds.select(gen_meshes + joints, replace=True)

        mel.eval('FBXResetExport;')
        mel.eval('FBXExportSkins -v false;')
        mel.eval('FBXExportSkeletonDefinitions -v true;')
        mel.eval('FBXExportShapes -v false;')
        mel.eval('FBXExportConstraints -v false;')
        mel.eval('FBXExportCameras -v false;')
        mel.eval('FBXExportLights -v false;')
        mel.eval('FBXExportInputConnections -v false;')
        mel.eval('FBXExportInAscii -v false;')
        path.parent.mkdir(parents=True, exist_ok=True)
        mel.eval('FBXExport -f "{}" -s;'.format(str(path).replace('\\', '/')))
    finally:
        for n in ([combined] if combined else []) + dupes:
            if n and cmds.objExists(n):
                cmds.delete(n)
        cmds.select(clear=True)

    if not path.is_file():
        raise AutoSkinError('Could not export the mesh for generation.')


def write_vertex_probe(mesh: str, path: Path) -> int:
    """Maya's own world-space vertex positions, flat little-endian float32.

    The backend needs this for two things, both load-bearing. It re-proves,
    on THIS mesh, that the FBX round-trip kept the vertex order — the weights
    come home keyed by vertex INDEX, and an order that silently shifted would
    scramble the skin without a word. And the same fit recovers the bpy ->
    Maya transform exactly, which is how generated joints arrive in Maya's
    coordinates without either side having to know the other's conventions.

    Sending the positions, rather than agreeing a convention, is the whole
    trick: there is nothing here to drift out of date when Maya or Blender
    changes its mind about axes or units.
    """
    pts = om.MFnMesh(_dag_shape(mesh)).getPoints(om.MSpace.kWorld)
    flat = array.array('f')
    for p in pts:
        flat.extend((p.x, p.y, p.z))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as fh:
        # array('f') is native float32, and every platform Maya ships on is
        # little-endian — which is what the backend reads it back as.
        flat.tofile(fh)
    return len(pts)


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — apply the weights
# ─────────────────────────────────────────────────────────────────────────────

def preserve_locked(mesh: str, skin: str) -> dict:
    """{influence full path: [weight per vertex]} for the influences the
    artist LOCKED — read BEFORE the rebind destroys them.

    A locked influence means "I weighted this by hand, leave it alone".
    AutoSkin replaces every other weight on the mesh, so the only honest way
    to honor a lock is to carry its weights across untouched and normalize
    the new prediction into whatever is left over.
    """
    fn = _skin_fn(skin)
    dag = _dag_shape(mesh)
    comp = _all_vertices(dag)
    infl, _, _ = _influences(fn)

    # PHYSICAL indices — the position in influenceObjects(). See _influences.
    locked = []
    for k, i in enumerate(infl):
        p = i.fullPathName()
        if not cmds.attributeQuery('liw', node=p, exists=True):
            continue
        try:
            if cmds.getAttr(f'{p}.liw'):
                locked.append((k, i))
        except RuntimeError:
            pass
    if not locked:
        return {}

    idx = om.MIntArray([k for k, _ in locked])
    flat = fn.getWeights(dag, comp, idx)
    n = len(locked)
    n_verts = len(flat) // n

    cmds.warning(
        '[AutoSkin] Keeping your locked influences exactly as they are: '
        + ', '.join(_leaf(i.fullPathName()) for _, i in locked[:6])
        + ('...' if len(locked) > 6 else '')
        + '. AutoSkin weights everything else around them.')

    return {i.fullPathName(): [flat[v * n + slot] for v in range(n_verts)]
            for slot, (_, i) in enumerate(locked)}


def create_skin_cluster(mesh: str, joints: list) -> str:
    """A FRESH skinCluster over `joints`. Always fresh, never reused.

    See the module docstring: the API weight write is not undoable, and the
    one thing that makes it safe is that the node holding those weights was
    created inside the caller's undo chunk. Reuse a pre-existing cluster and
    Ctrl+Z stops restoring the old skin, silently.
    """
    cmds.select(joints + [mesh], replace=True)
    sc = cmds.skinCluster(
        toSelectedBones=True, bindMethod=0, skinMethod=0,
        maximumInfluences=MAX_INFLUENCES, normalizeWeights=1,
        obeyMaxInfluences=False,
    )[0]
    cmds.select(clear=True)
    return sc


def apply_weights(mesh: str, skin: str, weights: dict, vertex_count: int,
                  preserved: dict = None) -> dict:
    """Write AutoSkin's weights onto `skin` through the API.

    weights: {joint leaf name: {vertex index (str): weight}}, straight from
        the backend — already trimmed to MAX_INFLUENCES and normalized. The
        vertex indices are Maya's; the backend proved that on this mesh
        before it sent them (runner_backend.fit_to_maya).
    preserved: {influence path: [old weight per vertex]} from preserve_locked.

    Vertices the engine had nothing to say about keep the closest-distance
    weights of the bind that created the cluster — a sane fallback, and a
    normalized one, which all-zero weights would not be.

    MUST be called on a skinCluster created inside the caller's undo chunk.
    See the module docstring.
    """
    dag = _dag_shape(mesh)
    n_verts = om.MFnMesh(dag).numVertices
    if n_verts != vertex_count:
        raise AutoSkinError(
            f'AutoSkin generated weights for {vertex_count} vertices but '
            f'{mesh.split("|")[-1]!r} has {n_verts}. Refusing to apply them.')

    fn = _skin_fn(skin)
    comp = _all_vertices(dag)
    infl, physical, column = _influences(fn)
    n_inf = len(infl)

    # Start from the cluster's own default bind, so any vertex the engine
    # skipped stays weighted and normalized instead of collapsing to origin.
    values = om.MDoubleArray(fn.getWeights(dag, comp, physical))

    # The locked influences keep exactly what they had; the prediction gets
    # whatever weight is left over.
    preserved = preserved or {}
    locked_cols = {}
    for path, old in preserved.items():
        k = column.get(_leaf(path))
        if k is not None and len(old) == n_verts:
            locked_cols[k] = [float(w) for w in old]

    rows = [[] for _ in range(n_verts)]
    unknown = []
    for joint, vw in weights.items():
        k = column.get(joint)
        if k is None:
            unknown.append(joint)
            continue
        if k in locked_cols:
            continue                       # locked: not ours to write
        for vi, w in vw.items():
            rows[int(vi)].append((k, float(w)))

    fallback = 0
    written = 0
    for v, row in enumerate(rows):
        base = v * n_inf

        # Locked first: they are a fixed cost, and what is left over is the
        # budget everything else gets to spend. EVERY row pays it, predicted or
        # not — a lock means "this weight, on this vertex, do not touch it", and
        # honoring it only where the engine happened to speak would be honoring
        # it nowhere in particular.
        share = 1.0
        for k, old in locked_cols.items():
            values[base + k] = old[v]
            share -= old[v]
        share = max(0.0, min(1.0, share))

        if not row:
            # The engine said nothing about this vertex (or spoke only about
            # locked joints). Keep the default bind's SHAPE for the unlocked
            # columns, rescaled into whatever the locks left — so the row still
            # sums to 1. Zeroing it would drag the vertex to the origin; leaving
            # it raw would leave the row unnormalized, and with interactive
            # normalization off (below) nobody would catch that.
            fallback += 1
            if locked_cols:
                rest = sum(values[base + k] for k in range(n_inf)
                           if k not in locked_cols)
                scale = (share / rest) if rest > 1e-12 else 0.0
                for k in range(n_inf):
                    if k not in locked_cols:
                        values[base + k] *= scale
            continue

        total = sum(w for _, w in row) or 1.0
        for k in range(n_inf):
            if k not in locked_cols:
                values[base + k] = 0.0
        for k, w in row:
            values[base + k] = w * share / total
        written += 1

    # NOT ONE of the engine's joints exists on this skinCluster: its answer does
    # not correspond to this mesh at all. Silently leaving the artist with a
    # plain closest-distance bind, reported as an ML skin, is the worst outcome
    # available — so refuse.
    #
    # This is deliberately NOT "we wrote nothing". Writing nothing is a valid
    # outcome when every joint the engine weighted is one the artist LOCKED:
    # the locks simply won, which is what a lock is for. Conflating the two
    # would turn honoring the artist into an error.
    if weights and not any(j in column for j in weights):
        raise AutoSkinError(
            f'AutoSkin generated no usable weights for '
            f'{mesh.split("|")[-1]!r} — none of the engine\'s joints matched '
            f'this skinCluster. Nothing was applied.')
    if not written:
        cmds.warning('[AutoSkin] Every generated weight landed on an influence '
                     'you locked, so nothing changed. Unlock them if you want '
                     'AutoSkin to weight them.')

    # Every row above sums to 1.0 in double precision, by construction. The
    # skinCluster's INTERACTIVE normalization does not know that: it re-adds
    # the row in float32, finds it a part in ten million off, nudges it, and
    # announces "Specified weights required modification for normalization" —
    # on every single bind, about nothing. That is a tool crying wolf, and a
    # tool that cries wolf teaches its user to ignore it. So the write is done
    # with normalization off and the mode put straight back: what we computed
    # is what gets stored, and Maya has nothing to complain about.
    #
    # This is only defensible because the rows ARE normalized — verified
    # against the engine's own numbers on a real character (worst 9e-7 across
    # 30,236 weights, all 7,559 vertices summing to 1). Turning this off to
    # silence a warning we had NOT earned the right to silence would be how a
    # broken skin ships quietly.
    mode = cmds.getAttr(f'{skin}.normalizeWeights')
    cmds.setAttr(f'{skin}.normalizeWeights', 0)
    try:
        fn.setWeights(dag, comp, physical, values, False)
    finally:
        cmds.setAttr(f'{skin}.normalizeWeights', mode)

    if unknown:
        cmds.warning('[AutoSkin] The engine weighted joints this skinCluster '
                     'does not have, and they were skipped: '
                     + ', '.join(sorted(unknown)[:6]))
    if fallback:
        cmds.warning(f'[AutoSkin] {fallback} vertex/vertices got no generated '
                     f'weight and kept their closest-joint bind.')

    return {'influences': n_inf, 'fallback': fallback, 'unknown': unknown}


# ─────────────────────────────────────────────────────────────────────────────
# Generate Joints — build the predicted skeleton as clean Maya joints
# ─────────────────────────────────────────────────────────────────────────────

def rebuild_joints(specs: list) -> tuple:
    """Build the engine's generated skeleton as fresh Maya joints.

    specs: [{'name', 'position', 'parent'}], roots first, positions already in
    MAYA world space — the backend put them there using the transform it
    recovered from the vertex probe.

    Positions and parentage only. bpy's bone ORIENTATIONS came out of a mesh,
    not out of a rigger; Maya orients its own, which is the whole reason this
    rebuilds rather than importing.

    Returns (joints, renames) where `renames` maps the BACKEND'S bone name to
    the name Maya actually gave the joint. Maya does not promise you the name
    you asked for: ask for 'bone_0' when a 'bone_0' already exists and you get
    'bone_1' (or 'bone_01'). The engine's weights are keyed by ITS names, so
    without this map every weight for a renamed joint would be silently
    discarded as "a joint this skinCluster does not have" — and Generate Joints
    would hand back a skeleton with holes in the skin. The map costs one return
    value; not returning it cost a bug.
    """
    made: dict = {}
    for spec in specs:
        cmds.select(clear=True)
        parent = made.get(spec.get('parent'))
        if parent:
            cmds.select(parent, replace=True)
        made[spec['name']] = cmds.joint(name=spec['name'],
                                        position=spec['position'])
    for j in made.values():
        cmds.joint(j, edit=True, orientJoint='xyz', secondaryAxisOrient='yup',
                   zeroScaleOrient=True, children=False)
    cmds.select(clear=True)

    joints = [made[s['name']] for s in specs]
    renames = {s['name']: _leaf(made[s['name']]) for s in specs}
    changed = [f'{k} -> {v}' for k, v in renames.items() if k != v]
    if changed:
        cmds.warning(f'[AutoSkin] Maya renamed {len(changed)} generated '
                     f'joint(s) to keep names unique (e.g. {changed[0]}); '
                     f'their weights were remapped to follow.')
    return joints, renames


# ─────────────────────────────────────────────────────────────────────────────
# The public gesture
# ─────────────────────────────────────────────────────────────────────────────

def _overlapping(meshes: list) -> bool:
    """Do any two of these meshes share space?

    They matter because the engine is asked about one body at a time: where
    two meshes overlap (a jacket over a torso), a combined generation mesh
    lets one body's weights bleed into the other. Overlapping meshes are
    therefore generated ONE AT A TIME — slower, but a wrong skin is not a
    saving.
    """
    for i, a in enumerate(meshes):
        for b in meshes[i + 1:]:
            if _bbox_overlap(cmds.exactWorldBoundingBox(a),
                             cmds.exactWorldBoundingBox(b)):
                return True
    return False


def bind_skin(generate_joints: bool = False, selection=None,
              progress=None, keep_temp: bool = False,
              cancel_check=None) -> dict:
    """Skin the selected mesh(es) to the selected joints, with ML weights.

    generate_joints: build a skeleton too (select mesh(es) only).
    progress: callable(stage: str, message: str) — forwarded from the
        backend, plus this layer's own stages.

    Everything runs in ONE undo chunk: a single Ctrl+Z restores the scene
    exactly, including the skinCluster this creates.
    """
    def say(stage, msg=''):
        if progress:
            try:
                progress(stage, msg)
            except Exception:
                pass    # a UI hiccup must never kill a generation

    joints, meshes = validate(selection, generate_joints)

    # MULTI-MESH IS BOUND ONE MESH AT A TIME — one engine run each.
    #
    # The spec called for combining the selection into a single generation
    # mesh, and that is what this did until it was tried on two meshes for
    # real (2026-07-12). Two failures, both silent-ish:
    #   - the engine's harvest is a closest-point lookup against a mesh that
    #     is the UNION of the selection, so wherever two meshes share space (a
    #     jacket over a torso) a vertex of one inherits the other's weights;
    #   - handed a combined mesh of disjoint bodies, the engine's own trim
    #     step pruned joints and returned an 80-joint character as 78, which
    #     is not a skin at all.
    # Binding them separately costs one engine run per mesh (~2 min) and
    # removes both failure modes outright. A wrong skin is not a saving.
    if len(meshes) > 1 and not generate_joints:
        if _overlapping(meshes):
            cmds.warning('[AutoSkin] The selected meshes overlap; binding '
                         'them one at a time so their weights cannot bleed '
                         'into each other.')
        out = {'meshes': [], 'joints': joints, 'generated_joints': False,
               'verts': 0, 'rebound': [], 'fidelity': {}}
        for m in meshes:
            r = bind_skin(generate_joints=False, selection=joints + [m],
                          progress=progress, keep_temp=keep_temp,
                          cancel_check=cancel_check)
            out['meshes'].extend(r['meshes'])
            out['rebound'].extend(r['rebound'])
            out['verts'] += (r['verts'] or 0)
            out['fidelity'] = r['fidelity']
        cmds.select(meshes, replace=True)
        return out

    mesh = meshes[0]
    old_skin = _skin_cluster_of(mesh)
    if old_skin:
        cmds.warning(f'[AutoSkin] Replacing the existing skin on '
                     f'{mesh.split("|")[-1]}. Undo restores it.')

    # The temp dir holds the generation FBX and the probe — hundreds of MB on a
    # dense character. It is created BEFORE the undo chunk and every failure
    # inside the chunk (a cancel, a backend crash, a refused fidelity check)
    # propagates straight past the cleanup at the bottom. try/finally, or the
    # artist's temp drive fills up one abandoned run at a time.
    tmp = Path(tempfile.mkdtemp(prefix='autoskin_'))
    gen_fbx = tmp / 'generation.fbx'
    probe = tmp / 'probe.f32'
    weights_json = tmp / 'weights.json'

    try:
        with undo_chunk('AutoSkin Bind Skin'):
            # Read the artist's locked weights BEFORE anything disturbs them.
            preserved = preserve_locked(mesh, old_skin) if old_skin else {}

            say('exporting', f'{len(meshes)} mesh(es)')
            export_generation_fbx(joints, meshes, gen_fbx)
            write_vertex_probe(mesh, probe)

            # The engine runs OUTSIDE the undo chunk's concerns — it only reads
            # files and writes files.
            result = runner.run(gen_fbx, weights_json, probe,
                                generate_joints=generate_joints,
                                progress=progress, cancel_check=cancel_check)

            say('applying')
            data = json.loads(weights_json.read_text(encoding='utf-8'))
            weights = data['weights']

            if generate_joints:
                specs = data.get('joints') or []
                if not specs:
                    raise AutoSkinError('AutoSkin generated no joints for this '
                                        'mesh.')
                joints, renames = rebuild_joints(specs)
                # Maya may not have granted the names the engine asked for, and
                # the weights are keyed by the engine's names. Follow the rename
                # or every weight for a renamed joint is dropped on the floor.
                weights = {renames.get(k, k): v for k, v in weights.items()}
                say('applying', f'{len(joints)} joint(s) built')

            # THE RULE: the cluster we write into is created in THIS chunk, so
            # undoing its creation takes the API-written weights with it. On a
            # rebind that means unbinding first — undo then pops the creation
            # and the unbind, and the artist's original skin comes back exactly.
            if old_skin:
                cmds.skinCluster(old_skin, edit=True, unbind=True)
            skin = create_skin_cluster(mesh, joints)

            applied = apply_weights(mesh, skin, weights,
                                    int(data['vertex_count']), preserved)

            # The artist's locks go back on the new cluster.
            for path in preserved:
                if cmds.objExists(path) and \
                        cmds.attributeQuery('liw', node=path, exists=True):
                    try:
                        cmds.setAttr(f'{path}.liw', 1)
                    except RuntimeError:
                        pass

            # Restore the selection INSIDE the chunk. Left outside, this select
            # is its own undoable operation, so the artist's first Ctrl+Z pops
            # the selection change and the skin survives — the bind looks
            # un-undoable when it is merely one step further back (caught by the
            # live undo test, 2026-07-12).
            cmds.select(meshes, replace=True)
            say('done')
    finally:
        if not keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)

    return {
        'meshes': [mesh],
        'joints': joints,
        'generated_joints': bool(generate_joints),
        'verts': result.get('verts'),
        'rebound': [mesh] if old_skin else [],
        'influences': applied['influences'],
        'fidelity': result.get('fidelity') or {},
        'temp_dir': str(tmp) if keep_temp else '',
    }
