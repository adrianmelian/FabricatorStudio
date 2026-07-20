# maya_tools/skinning/autoskin/runner_backend.py
"""AutoSkin backend pipeline — RUNS INSIDE THE INFERENCE VENV, never in Maya.

Maya cannot import this file: it needs bpy, numpy and scipy, which live in
the AutoSkin venv (%LOCALAPPDATA%/FabricatorStudio/autoskin/venv). The
Maya side talks to it through runner.py, which launches it as a
subprocess with a JSON payload and reads a JSON result off stdout.

    payload  {input_fbx, output_json, probe, generate_joints, repo, work_dir}
    result   {"ok": true, "weights_json": ..., "joints": N, "verts": N, ...}

The chain (spec §3, steps 3-4 — every step proven live on a real
character before it was written here):

  1. import the Maya-exported FBX (mesh + the user's skeleton)
  2. COVERAGE BIND: every joint must influence >=1 vertex. Upstream's
     trim_skeleton() DELETES any joint whose subtree carries no input
     skin — a pelvis-only bind once collapsed an 80-joint character to a
     single joint and the model happily returned garbage. Weight VALUES
     are irrelevant (Mode B regenerates them); only coverage matters.
  3. run the engine: demo.py --use_skeleton --use_transfer, which holds
     our skeleton FIXED and predicts weights onto it.
  4. harvest the engine's weights onto the user's vertices by POSITION.
     The ENGINE's round-trip is glTF, which seam-splits (Reggie: 7,559
     vertices in, 17,451 out) — its indices mean nothing to us. Skeletons
     are aligned with Umeyama similarity first (the engine returns its
     own scale and frame), then a KD-tree does closest-point lookups.
  5. hand the weights back to Maya AS DATA — {joint: {vertex: weight}} in
     a JSON file — for Maya to apply through the API.

Step 5 used to export a skinned proxy FBX for Maya to copySkinWeights
from. That worked, but it round-tripped an entire proxy RIG through Maya
just to carry numbers, and the artist paid for it: an FBX import dialog, a
"joint rotations are locked" prompt (the proxy's joints collide by name
with the real, rig-driven ones), a namespace dance to stop the proxy
replacing the real mesh, and a slow FBX write+read on the end of every
run. Weights are data. They travel as data now (Adrian, 2026-07-13).

THE VERTEX-INDEX CONTRACT. Handing back indices is only sound if bpy's
vertex i IS Maya's vertex i. That is true for the FBX we import here (it
is not true of the engine's glTF — hence step 4) and it was measured:
7,559/7,559 on a real character, worst index error 8e-6% of the model.
But "measured once on Reggie" is not a licence to trust it forever on
someone else's mesh, in a future Maya, against a future bpy. So Maya sends
its OWN vertex positions along with the FBX (`probe`), and fit_to_maya()
re-proves the correspondence on every run, against the artist's own mesh,
and REFUSES to write a skin it cannot vouch for. The same fit recovers the
bpy->Maya transform exactly, which is how generated joints come back in
Maya's coordinates without this side ever knowing what Maya's conventions
are.

Every print to stdout that is not the final result line is prefixed
[AUTOSKIN] so runner.py can surface progress without parsing noise.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

import json
import subprocess
import sys
import traceback
from pathlib import Path

MAX_INFLUENCES = 4          # what game engines take; matches the eval
_TAG = '[AUTOSKIN]'

# How far the FBX round-trip may move a vertex, as a fraction of the model's
# radius, before we stop believing the vertex order. Measured on a real
# character: 8e-8 (float32 FBX precision). A scrambled order scores ~1.0.
# Anything between those two numbers is a mesh we do not understand, and the
# honest response to a mesh we do not understand is to refuse.
FIDELITY_TOL = 1e-3


def log(stage: str, msg: str = '') -> None:
    print(f'{_TAG} {stage}{(": " + msg) if msg else ""}', flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# bpy helpers
# ─────────────────────────────────────────────────────────────────────────────

def _reset_scene():
    import bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _import_fbx(path: Path):
    """Import an FBX; return (armature_object, [mesh_objects])."""
    import bpy
    bpy.ops.import_scene.fbx(filepath=str(path))
    arms = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    if not meshes:
        raise RuntimeError(f'no mesh found in {path.name}')
    return (arms[0] if arms else None), meshes


def _bone_positions(arm) -> tuple:
    """(names, Nx3 world head positions) for every bone, in bone order."""
    import numpy as np
    names = [b.name for b in arm.data.bones]
    mat = arm.matrix_world
    pos = np.array([list(mat @ b.head_local) for b in arm.data.bones],
                   dtype=np.float64)
    return names, pos


def _mesh_world_verts(obj):
    import numpy as np
    mat = obj.matrix_world
    return np.array([list(mat @ v.co) for v in obj.data.vertices],
                    dtype=np.float64)


ARMATURE_NAME = 'AutoSkinRig'


def _normalize_armature_name(arm) -> None:
    """Force the armature's OBJECT name and its DATA-BLOCK name to agree.

    The engine resolves its armature with, in effect,
    `bpy.data.objects[bpy.data.armatures[0].name]` — it reads the name off
    the armature's DATA block and looks it up among OBJECTS. Blender makes
    no promise those two names match, and on a real rig they did not: the
    export died with `KeyError: key "reggie" not found` deep inside the
    engine, on a character whose skeleton imported with a data name its
    object did not share (Adrian, live, 2026-07-13).

    We cannot fix how the engine looks things up from here, but we can hand
    it a rig where the lookup cannot miss: one armature, one name, matching
    on both, chosen so it can never collide with a mesh.
    """
    if arm is None:
        return
    arm.name = ARMATURE_NAME
    if arm.data is not None:
        arm.data.name = ARMATURE_NAME


def _skinned_mesh(meshes):
    """The mesh that actually carries skin, out of whatever came back.

    The engine's output GLB ships a stray unweighted 'Icosphere' beside
    the real skinned body (seen live, 2026-07-12) — taking meshes[0] on
    faith harvests nothing and blames the engine for it. Pick by the only
    thing that matters: it has vertex groups. Ties break on vertex count.
    """
    skinned = [m for m in meshes if len(m.vertex_groups) > 0]
    if not skinned:
        raise RuntimeError(
            'the engine returned no weighted mesh (it produced '
            f'{len(meshes)} mesh(es), none carrying skin).')
    return max(skinned, key=lambda m: (len(m.vertex_groups),
                                       len(m.data.vertices)))


def _main_mesh(meshes):
    """The user's mesh: the biggest one. Maya combines a multi-mesh
    selection into ONE generation mesh before export, so a stray tiny
    object here is noise, not a second body."""
    return max(meshes, key=lambda m: len(m.data.vertices))


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — coverage bind
# ─────────────────────────────────────────────────────────────────────────────

def coverage_bind(arm, meshes) -> int:
    """Give every joint at least one influenced vertex.

    Nearest-joint assignment, weight 1.0. The values are throwaway — Mode
    B regenerates all of them — but the COVERAGE is load-bearing: without
    it, trim_skeleton() prunes joints whose subtree has no skin, and the
    engine receives a mutilated skeleton (the single root cause of the
    eval's only outright failure).

    Any joint the nearest-vertex pass misses is force-assigned its own
    closest vertex afterwards, so coverage is guaranteed rather than
    hoped for.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    names, bone_pos = _bone_positions(arm)
    tree = cKDTree(bone_pos)

    covered = set()
    for obj in meshes:
        for n in names:
            if n not in obj.vertex_groups:
                obj.vertex_groups.new(name=n)
        verts = _mesh_world_verts(obj)
        _, nearest = tree.query(verts, k=1)
        for vi, bi in enumerate(nearest):
            obj.vertex_groups[names[int(bi)]].add([vi], 1.0, 'REPLACE')
            covered.add(int(bi))

        # Ensure the armature actually deforms this mesh.
        if not any(m.type == 'ARMATURE' for m in obj.modifiers):
            mod = obj.modifiers.new(name='Armature', type='ARMATURE')
            mod.object = arm
        obj.parent = arm

    # Force-cover the stragglers: a joint no vertex was nearest to.
    missed = [i for i in range(len(names)) if i not in covered]
    if missed:
        obj = meshes[0]
        verts = _mesh_world_verts(obj)
        vtree = cKDTree(verts)
        for bi in missed:
            _, vi = vtree.query(bone_pos[bi], k=1)
            obj.vertex_groups[names[bi]].add([int(vi)], 1.0, 'REPLACE')
        log('coverage', f'force-covered {len(missed)} joint(s) no vertex was '
                        f'nearest to')

    log('coverage', f'{len(names)} joint(s) covered')
    return len(names)


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Umeyama alignment + position-keyed weight harvest
# ─────────────────────────────────────────────────────────────────────────────

def umeyama(src, dst) -> tuple:
    """Similarity transform (scale, R, t) mapping src -> dst, least
    squares. Returns (s, R, t) with dst ~= s * R @ src.T + t.

    The engine returns its rig in its OWN scale and frame; a raw
    closest-point lookup across that gap is nonsense. Umeyama recovers
    the similarity exactly (measured residual 0.0027 on the eval's
    80-joint character).
    """
    import numpy as np
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    cs, cd = src - mu_s, dst - mu_d
    cov = cd.T @ cs / len(src)
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1.0
    R = U @ S @ Vt
    var_s = (cs ** 2).sum() / len(src)
    s = float((D * np.diag(S)).sum() / var_s) if var_s > 0 else 1.0
    t = mu_d - s * (R @ mu_s)
    return s, R, t


def apply_similarity(pts, s, R, t):
    import numpy as np
    return (s * (R @ np.asarray(pts, dtype=np.float64).T)).T + t


def map_predicted_bones(pred_names, pred_pos, user_names, user_pos) -> dict:
    """{predicted bone name -> user joint name}, matched by POSITION.

    --use_transfer drops our joint names on the floor and hands back
    bone_0..bone_N. Mode B holds the skeleton fixed, so order and
    position survive — which makes position a sound key and names a
    useless one. Both skeletons are normalized to unit scale before
    matching so the pairing cannot be skewed by the engine's scale.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    def unit(p):
        p = np.asarray(p, dtype=np.float64)
        c = p.mean(axis=0)
        d = np.linalg.norm(p - c, axis=1).max() or 1.0
        return (p - c) / d

    tree = cKDTree(unit(user_pos))
    _, idx = tree.query(unit(pred_pos), k=1)
    mapping = {pred_names[i]: user_names[int(j)] for i, j in enumerate(idx)}
    collisions = len(pred_names) - len(set(mapping.values()))
    log('bone-map', f'{len(mapping)} bone(s) mapped, {collisions} collision(s)')
    if collisions:
        log('bone-map', 'WARNING: two predicted bones claimed one joint — '
                        'the skeletons may not correspond')
    return mapping


def harvest_weights(pred_mesh, pred_arm, user_mesh, user_arm) -> dict:
    """Pull the engine's weights onto the user's vertices by position.

    Returns {user_joint_name: {vertex_index: weight}} — already trimmed to
    MAX_INFLUENCES and renormalized.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    pred_names, pred_bones = _bone_positions(pred_arm)
    user_names, user_bones = _bone_positions(user_arm)

    # Align the engine's rig onto ours, then match vertices in OUR frame.
    s, R, t = umeyama(pred_bones, user_bones)
    resid = float(np.abs(apply_similarity(pred_bones, s, R, t) - user_bones).mean())
    log('align', f'umeyama residual {resid:.4f} (scale {s:.4f})')

    bone_map = map_predicted_bones(pred_names, pred_bones, user_names, user_bones)

    pred_verts = apply_similarity(_mesh_world_verts(pred_mesh), s, R, t)
    user_verts = _mesh_world_verts(user_mesh)
    tree = cKDTree(pred_verts)
    dist, nearest = tree.query(user_verts, k=1)
    log('match', f'{len(user_verts)} vertex/vertices matched, mean dist '
                 f'{float(dist.mean()):.4f}')

    # Predicted per-vertex weights, as {group_name: {vidx: w}}.
    pred_groups = {g.index: g.name for g in pred_mesh.vertex_groups}
    per_vertex = []
    for v in pred_mesh.data.vertices:
        per_vertex.append({pred_groups[g.group]: g.weight
                           for g in v.groups if g.group in pred_groups})

    out: dict = {}
    for user_vi, pred_vi in enumerate(nearest):
        w = per_vertex[int(pred_vi)]
        if not w:
            continue
        # Top-K by weight, then renormalize — engines take 4 influences.
        top = sorted(w.items(), key=lambda kv: kv[1], reverse=True)[:MAX_INFLUENCES]
        total = sum(v for _, v in top) or 1.0
        for pred_bone, weight in top:
            joint = bone_map.get(pred_bone)
            if not joint:
                continue
            out.setdefault(joint, {})[user_vi] = weight / total
    return out


def adopt_predicted_skeleton(pred_mesh, pred_arm, user_mesh):
    """GENERATE JOINTS (Mode A): there is no user skeleton — the engine
    invented one, and it is the output.

    The engine returns its work in a CANONICAL BOX, so the rig has to be moved
    back into the user's space before it is any use. The transform is a UNIFORM
    SCALE, a TRANSLATION, and a ROTATION THAT DEPENDS ON HOW THE CHARACTER WAS
    ORIENTED — the engine canonicalizes what it is given. Measured on two
    orientations of the SAME character:

        input  spans          output spans
        1.190 0.391 1.578  -> 1.510 0.496 2.000   (x1.268, axes unchanged)
        0.240 0.732 0.971  -> 0.496 2.000 1.510   (x2.063, Y and Z SWAPPED)

    Same three output numbers both times; only which axis they land on changed.

    SO THE ROTATION IS ENUMERATED, NOT SEARCHED FOR. This is the whole lesson of
    the bug it replaces. The old code ran an ICP: it seeded a similarity fit from
    the centroids, then "refined" it against closest-point pairs for six rounds.
    Fitting a SIMILARITY to closest-point pairs is a known degenerate — shrinking
    the source toward the middle of the target lowers the mean pair distance, so
    the scale slides toward zero while the residual still reads as converged. On
    one character it happened to hold. On Adrian's it collapsed a 50-joint
    skeleton into an 18-unit blob inside a 97-unit body and reported a clean
    residual while doing it (2026-07-13).

    A search can wander. An enumeration cannot. We try the 24 proper axis-aligned
    rotations, derive scale and translation from BOUNDING BOXES (never fitted, so
    never collapsible), score each by closest-point distance, and take the best —
    then refine with a RIGID ICP that holds the scale fixed, which has no
    degenerate direction to slide down. If the best candidate still does not land
    on the mesh, we refuse instead of inventing a skeleton.

    Bounding boxes, not centroids: the engine's mesh is seam-split by the glTF
    round-trip (17,422 vertices in, 34,737 out), and duplicated vertices drag a
    centroid around. A bounding box does not care how many times you name the
    same corner.

    Returns the predicted armature, now living in the user's space.
    """
    import itertools

    import numpy as np
    from scipy.spatial import cKDTree

    pred_verts = _mesh_world_verts(pred_mesh)
    user_verts = _mesh_world_verts(user_mesh)

    def box(v):
        lo, hi = v.min(axis=0), v.max(axis=0)
        return (lo + hi) / 2.0, (hi - lo)

    c_u, e_u = box(user_verts)
    r_u = float(np.linalg.norm(user_verts - user_verts.mean(axis=0),
                               axis=1).max()) or 1.0
    tree = cKDTree(user_verts)

    def axis_rotations():
        """The 24 proper rotations of a cube: every axis permutation, every sign
        pattern, keeping det = +1. A reflection would mirror the character."""
        for perm in itertools.permutations(range(3)):
            for signs in itertools.product((1.0, -1.0), repeat=3):
                R = np.zeros((3, 3))
                for row, col in enumerate(perm):
                    R[row, col] = signs[row]
                if np.linalg.det(R) > 0:
                    yield R

    best = None
    for R in axis_rotations():
        rotated = pred_verts @ R.T
        c_p, e_p = box(rotated)
        live = (e_p > 1e-9) & (e_u > 1e-9)
        if not live.any():
            continue
        ratios = e_u[live] / e_p[live]
        # A candidate whose axes disagree about the scale is the wrong candidate.
        # (The RIGHT one has a uniform scale, by construction.)
        if float(ratios.max() / ratios.min()) > 1.10:
            continue
        s = float(np.median(ratios))
        t = c_u - s * c_p
        dist, _ = tree.query((s * rotated) + t, k=1)
        resid = float(dist.mean())
        if best is None or resid < best[0]:
            best = (resid, s, R, t)

    if best is None:
        raise RuntimeError(
            'the engine returned a mesh whose proportions do not match yours at '
            'any orientation. AutoSkin will not guess a skeleton from it.')

    resid, s, R, t = best
    log('align', f'engine orientation resolved: uniform scale {s:.4f}, '
                 f'residual {100 * resid / r_u:.3f}% of the model')

    # Refine RIGIDLY — scale held fixed at the value the bounding boxes gave us.
    # Rigid ICP cannot collapse: there is no scale for it to slide toward zero.
    for _ in range(6):
        moved = (s * (pred_verts @ R.T)) + t
        dist, nearest = tree.query(moved, k=1)
        # umeyama also returns a SCALE, and we deliberately throw it away: only
        # its rotation and translation are wanted here. Letting that scale back
        # in is precisely the bug this function exists to not have.
        _, R2, t2 = umeyama(moved, user_verts[nearest])
        R, t = R2 @ R, R2 @ t + t2

    dist, _ = tree.query((s * (pred_verts @ R.T)) + t, k=1)
    resid = float(dist.mean())
    log('align', f'refined residual {100 * resid / r_u:.3f}% of the model')

    # PROVE it landed. The two meshes are the SAME GEOMETRY, so after the right
    # transform every predicted vertex must sit essentially on the user's
    # surface. A bad alignment is now a loud failure instead of a crumpled rig
    # nobody notices until they look at the viewport.
    if resid > 0.02 * r_u:
        raise RuntimeError(
            f'the engine\'s mesh does not sit on your mesh after alignment '
            f'(off by {100 * resid / r_u:.1f}% of the model). AutoSkin will not '
            f'place a skeleton it cannot line up.')

    from mathutils import Matrix
    M = Matrix(((s * R[0, 0], s * R[0, 1], s * R[0, 2], t[0]),
                (s * R[1, 0], s * R[1, 1], s * R[1, 2], t[1]),
                (s * R[2, 0], s * R[2, 1], s * R[2, 2], t[2]),
                (0.0, 0.0, 0.0, 1.0)))
    # Move the engine's MESH by the same transform, not just its
    # skeleton: the weight harvest that follows does closest-point
    # lookups in world space, so leaving the mesh behind would pair every
    # vertex against an unaligned cloud (measured: 1.42 units of drift).
    pred_arm.matrix_world = M @ pred_arm.matrix_world
    pred_mesh.matrix_world = M @ pred_mesh.matrix_world
    log('generate', f'{len(pred_arm.data.bones)} joint(s) generated')
    return pred_arm


def harvest_weights_same_bones(pred_mesh, user_mesh) -> dict:
    """Mode A harvest: the destination skeleton IS the engine's, so bone
    names carry across verbatim — no mapping, no skeleton alignment. Only
    the vertex correspondence still has to be by position."""
    from scipy.spatial import cKDTree

    pred_verts = _mesh_world_verts(pred_mesh)
    user_verts = _mesh_world_verts(user_mesh)
    tree = cKDTree(pred_verts)
    dist, nearest = tree.query(user_verts, k=1)
    log('match', f'{len(user_verts)} vertex/vertices matched, mean dist '
                 f'{float(dist.mean()):.4f}')

    gname = {g.index: g.name for g in pred_mesh.vertex_groups}
    per_vertex = [{gname[g.group]: g.weight for g in v.groups
                   if g.group in gname}
                  for v in pred_mesh.data.vertices]

    out: dict = {}
    for user_vi, pred_vi in enumerate(nearest):
        w = per_vertex[int(pred_vi)]
        if not w:
            continue
        top = sorted(w.items(), key=lambda kv: kv[1], reverse=True)[:MAX_INFLUENCES]
        total = sum(v for _, v in top) or 1.0
        for bone, weight in top:
            out.setdefault(bone, {})[user_vi] = weight / total
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — the vertex-index contract, and the way home
# ─────────────────────────────────────────────────────────────────────────────

def fit_to_maya(user_mesh, probe_path: Path) -> tuple:
    """Prove bpy's vertex i is Maya's vertex i, and recover bpy -> Maya.

    `probe_path` is a flat little-endian float32 array of the mesh's world
    positions AS MAYA SEES THEM, in Maya's vertex order. bpy's FBX importer
    moves the mesh by a similarity (a unit scale and an axis conversion) and
    NOTHING ELSE — it does not bend the model. So if the vertex order
    survived, the index pairing is the true pairing, and the best-fit
    similarity through it lands on machine zero. If the order did not
    survive, the pairing is garbage and no similarity can fit it: the
    residual blows up to the size of the model. The residual is therefore
    decisive on its own, and it is checked EVERY RUN.

    Returns ((s, R, t), {'residual', 'radius'}) mapping bpy world -> Maya
    world. Raises rather than return a mapping it cannot stand behind: a
    wrong skin that looks plausible is worse than a failure that explains
    itself.
    """
    import numpy as np

    P = np.fromfile(str(probe_path), dtype='<f4').reshape(-1, 3).astype(np.float64)
    Q = _mesh_world_verts(user_mesh)

    if len(P) != len(Q):
        raise RuntimeError(
            f'Maya sent {len(P)} vertices but the FBX came back with '
            f'{len(Q)}. AutoSkin applies weights by vertex index and will '
            f'not guess at a mapping.')
    if len(P) < 4:
        raise RuntimeError(f'{len(P)} vertices is too few to skin.')

    s, R, t = umeyama(Q, P)
    err = np.linalg.norm(apply_similarity(Q, s, R, t) - P, axis=1)
    radius = float(np.linalg.norm(P - P.mean(axis=0), axis=1).max()) or 1.0
    rel = float(err.max() / radius)

    if rel > FIDELITY_TOL:
        raise RuntimeError(
            f'the FBX round-trip did not preserve this mesh\'s vertex order '
            f'(worst vertex is {100 * rel:.2f}% of the model away from where '
            f'Maya put it). AutoSkin applies weights by vertex index, so it '
            f'is refusing to write a skin it cannot vouch for.')

    log('fidelity', f'vertex order verified on {len(P)} vertices '
                    f'(worst {100 * rel:.6f}% of the model)')
    return (s, R, t), {'residual': rel, 'radius': radius}


def dump_joints(arm, to_maya) -> list:
    """The generated skeleton as data, in MAYA world space, roots first.

    Positions and parentage only. bpy's bone ORIENTATIONS are not something
    Maya should inherit — Maya builds its own, which is the whole reason the
    old code rebuilt the joints rather than keeping the imported ones.
    """
    s, R, t = to_maya
    bones = list(arm.data.bones)
    mat = arm.matrix_world
    heads = apply_similarity([list(mat @ b.head_local) for b in bones],
                             s, R, t)

    depth: dict = {}

    def d(bone):
        if bone.name not in depth:
            depth[bone.name] = 0 if bone.parent is None else d(bone.parent) + 1
        return depth[bone.name]

    order = sorted(range(len(bones)), key=lambda i: d(bones[i]))
    return [{'name': bones[i].name,
             'position': [float(x) for x in heads[i]],
             'parent': bones[i].parent.name if bones[i].parent else None}
            for i in order]


# ─────────────────────────────────────────────────────────────────────────────
# The pipeline
# ─────────────────────────────────────────────────────────────────────────────

def _engine_failure(proc, work: Path) -> str:
    """Turn a dead engine process into something an artist can act on.

    This used to be `(p.stderr or p.stdout or '')` sliced to its last 15 lines,
    and that hid the cause of every failure it was meant to explain:

      * `or` means one byte on stderr DISCARDS all of stdout — and the engine
        prints its progress, and its bpy helper's output, to stdout.
      * the last 15 lines are the OUTERMOST frames of whatever raised. The
        actual event is usually far above them.

    Adrian, 2026-07-13, running Generate Joints on a prop: he got a `requests`
    ConnectionResetError and nothing else, which reads like a network outage.
    It is not. The engine spawns a LOCAL Blender HTTP server (bpy_server.py)
    and POSTs the mesh to it. That server catches its own Python exceptions and
    returns them as clean error dicts — so a RESET SOCKET means the helper
    PROCESS DIED. A hard crash, not an exception. The socket traceback is the
    corpse, never the cause.

    So: keep the whole log on disk, and name the one failure mode we know.
    """
    out = '\n'.join(part for part in ((proc.stdout or '').rstrip(),
                                      (proc.stderr or '').rstrip()) if part)

    where = ''
    try:
        log_path = work / 'engine_failure.log'
        log_path.write_text(out or '(the engine produced no output)',
                            encoding='utf-8', errors='replace')
        where = f'\n\nFull engine log: {log_path}'
    except OSError:
        pass

    if 'ConnectionResetError' in out or 'Connection aborted' in out:
        return (
            f"the engine's Blender helper crashed (exit {proc.returncode}).\n"
            f"This is NOT a network problem, despite the connection error in "
            f"the log: AutoSkin drives its Blender helper over a LOCAL socket, "
            f"and that helper died mid-export.\n"
            f"Reproduced on simple, unarticulated shapes — a plain crate does "
            f"it every time. Skeleton generation targets characters, not "
            f"props.{where}"
        )

    tail = '\n'.join(out.splitlines()[-25:]) if out else '(no output)'
    return f'the engine failed (exit {proc.returncode}):\n{tail}{where}'


def run(payload: dict) -> dict:
    import bpy   # noqa: F401  (proves we are in the venv before anything else)

    input_fbx = Path(payload['input_fbx'])
    output_json = Path(payload['output_json'])
    probe = Path(payload['probe'])
    repo = Path(payload['repo'])
    work = Path(payload['work_dir'])
    generate_joints = bool(payload.get('generate_joints'))
    work.mkdir(parents=True, exist_ok=True)

    # ── prep ────────────────────────────────────────────────────────────
    log('stage', 'prep')
    _reset_scene()
    arm, meshes = _import_fbx(input_fbx)
    if arm is None and not generate_joints:
        raise RuntimeError(
            'no skeleton in the input FBX. Select bind joints along with '
            'the mesh, or tick Generate Joints.')
    _normalize_armature_name(arm)   # the engine's lookup depends on this
    n_joints = 0
    if not generate_joints:
        n_joints = coverage_bind(arm, meshes)

    prepped = work / 'prepped.glb'
    bpy.ops.export_scene.gltf(filepath=str(prepped), export_format='GLB')
    log('prep', f'-> {prepped.name}')

    # ── engine ──────────────────────────────────────────────────────────
    log('stage', 'generating')
    skinned = work / 'skinned.glb'
    cmd = [sys.executable, 'demo.py',
           '--input', str(prepped), '--output', str(skinned)]
    if not generate_joints:
        cmd += ['--use_skeleton', '--use_transfer']
    p = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True,
                       timeout=1800)
    if p.returncode != 0 or not skinned.is_file():
        raise RuntimeError(_engine_failure(p, work))
    log('generating', f'-> {skinned.name}')

    # ── harvest ─────────────────────────────────────────────────────────
    log('stage', 'harvesting')
    # Re-import BOTH: the user's rig (clean, no coverage bind) and the
    # engine's result, into one scene so they share a coordinate frame.
    _reset_scene()
    user_arm, user_meshes = _import_fbx(input_fbx)
    before = set(o.name for o in bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(skinned))
    new = [o for o in bpy.data.objects if o.name not in before]
    pred_meshes = [o for o in new if o.type == 'MESH']
    pred_arms = [o for o in new if o.type == 'ARMATURE']
    if not pred_meshes or not pred_arms:
        raise RuntimeError('the engine returned no skinned mesh or skeleton.')

    pred_mesh = _skinned_mesh(pred_meshes)
    user_mesh = _main_mesh(user_meshes)
    log('harvest', f'engine mesh {pred_mesh.name!r} '
                   f'({len(pred_mesh.data.vertices)} verts) -> '
                   f'{user_mesh.name!r} ({len(user_mesh.data.vertices)} verts)')

    # Everything below hands Maya VERTEX INDICES. Earn the right to first.
    to_maya, fidelity = fit_to_maya(user_mesh, probe)

    if generate_joints:
        # Mode A: the engine's skeleton IS the deliverable. Move it into
        # the user's space, keep its bone names, and skin the user's mesh
        # to it.
        out_arm = adopt_predicted_skeleton(pred_mesh, pred_arms[0], user_mesh)
        weights = harvest_weights_same_bones(pred_mesh, user_mesh)
        joints_out = dump_joints(out_arm, to_maya)
    else:
        # Mode B: the user's skeleton is the only skeleton. Harvest the
        # engine's weights onto it and throw its rig away.
        out_arm = user_arm
        weights = harvest_weights(pred_mesh, pred_arms[0], user_mesh, user_arm)
        joints_out = None       # Maya already has the joints; they are its own

    if not weights:
        raise RuntimeError('no weights harvested — the engine output did not '
                           'correspond to the input mesh.')

    n_verts = len(user_mesh.data.vertices)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps({
        'vertex_count': n_verts,
        # {joint name: {vertex index: weight}}. Vertex indices are Maya's --
        # fit_to_maya() has just proved it. Keys are strings because JSON.
        'weights': {joint: {str(vi): round(float(w), 6) for vi, w in vw.items()}
                    for joint, vw in weights.items()},
        'joints': joints_out,
        'fidelity': fidelity,
    }), encoding='utf-8')
    log('stage', 'done')

    return {
        'ok': True,
        'weights_json': str(output_json),
        'joints': len(joints_out) if joints_out else len(out_arm.data.bones),
        'verts': n_verts,
        'influences': len(weights),        # joints that actually got weight
        'generated_joints': bool(generate_joints),
        'fidelity': fidelity,
    }


def main() -> int:
    try:
        payload = json.loads(sys.argv[1])
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': f'bad payload: {exc}'}))
        return 2
    try:
        result = run(payload)
    except Exception as exc:
        traceback.print_exc()
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
