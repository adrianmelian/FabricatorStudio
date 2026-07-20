# maya_tools/skinning/dem_bones_bake/dem_bones_bake_app.py
"""DemBones Bake — Maya-side orchestrator.

Public entrypoints:
  delta_mush(iterations, step)  — add the teacher deformer to selection.
  dem_bones_bake(max_influences=4) — bake that deform stack into weights.

dem_bones_bake reads current Maya selection (one mesh with an existing
skinCluster), samples the deformed mesh + joint transforms across the
active timeline range, spawns mayapy.exe to run DemBones, replaces the
skinCluster's weights in place.

Designed to follow a bind + a teacher deformer (typically deltaMush)
added by the rigger — hence delta_mush() living here, so the smooth and
the bake that consumes it ship as one tool. Weights-only DemBones run
(joints locked) preserves the UE5 mannequin skeleton contract.

Spec: docs/superpowers/specs/2026-05-26-dem-bones-bake-design.md
"""
__author__ = "Adrian Melian"

import sys
from pathlib import Path

import maya.cmds as cmds

from maya_tools.framework.decorators import undo_chunk
from maya_tools.utils.maya.skin_helpers import get_skin_influences
from maya_tools.utils.maya.skin_subprocess import (
    resolve_mayapy,
    run_solver_subprocess,
    write_weights_back,
)


# Resolved at import time — the runner module lives next to this file.
_DEM_BONES_RUNNER_PATH = Path(__file__).parent / 'dem_bones_runner.py'

# Sys-attr cache for the per-Maya-session import-deps check.
_DEPS_VERIFIED_SYS_ATTR = '_ks_dem_bones_deps_verified'


def delta_mush(smoothing_iterations: int,
                smoothing_step: float) -> list[str]:
    """Add a deltaMush deformer to each mesh in the selection.

    The teacher deformer dem_bones_bake() is designed to consume: smooth
    the bound mesh here, then bake that result back into skinCluster
    weights.

    Args:
      smoothing_iterations: int, Maya default 10.
      smoothing_step: float in [0, 1], Maya default 0.5.

    Returns:
      List of created deltaMush deformer node names.

    Raises:
      RuntimeError: selection contains no mesh transform.
    """
    sel = cmds.ls(selection=True, long=True) or []
    meshes = [
        s for s in sel
        if cmds.listRelatives(s, shapes=True, type='mesh',
                               noIntermediate=True, fullPath=True)
    ]
    if not meshes:
        raise RuntimeError(
            'delta_mush: no mesh transform in selection. Select the bound '
            'mesh(es) you want to add deltaMush to.'
        )

    created: list[str] = []
    with undo_chunk('DemBones Bake: Delta Mush'):
        for mesh in meshes:
            # cmds.deltaMush returns the deformer name as a single-item list.
            created.extend(cmds.deltaMush(
                mesh,
                smoothingIterations=smoothing_iterations,
                smoothingStep=smoothing_step,
            ))
    return created


def _verify_dem_bones_deps() -> None:
    """Spawn mayapy.exe and confirm py_dem_bones + numpy + scipy import.

    Caches success on `sys` so subsequent clicks don't re-pay the ~5s cold
    start. Raises RuntimeError with a clear pip install hint on failure.
    """
    import subprocess
    if getattr(sys, _DEPS_VERIFIED_SYS_ATTR, False):
        return
    mayapy = resolve_mayapy()
    probe = (
        'import py_dem_bones, numpy, scipy; '
        'assert hasattr(py_dem_bones, "DemBones"), "DemBones class missing"; '
        'print("ok")'
    )
    try:
        result = subprocess.run(
            [mayapy, '-c', probe],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f'dem_bones_bake: mayapy subprocess timed out during deps check. '
            f'Path: {mayapy}'
        )
    if result.returncode != 0 or 'ok' not in result.stdout:
        raise RuntimeError(
            f'dem_bones_bake: py_dem_bones not importable in mayapy.\n'
            f'  Install: "{mayapy}" -m pip install --user py-dem-bones\n'
            f'  subprocess stdout: {result.stdout.strip()!r}\n'
            f'  subprocess stderr: {result.stderr.strip()!r}'
        )
    setattr(sys, _DEPS_VERIFIED_SYS_ATTR, True)


def _validate_selection() -> tuple:
    """Return (mesh_transform_long, skin_cluster_name) from current selection.

    Raises RuntimeError on any invalid input shape. Same contract as V2's
    bbw_skin_app._validate_selection — kept local rather than shared because
    the error messages mention the tool name explicitly.
    """
    sel = cmds.ls(selection=True, long=True) or []
    if len(sel) != 1:
        raise RuntimeError(
            f'dem_bones_bake: select exactly one mesh transform. Got '
            f'{len(sel)}: {sel}'
        )
    mesh = sel[0]
    shapes = cmds.listRelatives(mesh, shapes=True, type='mesh',
                                 fullPath=True) or []
    if not shapes:
        raise RuntimeError(
            f'dem_bones_bake: {mesh!r} is not a mesh transform.'
        )
    history = cmds.listHistory(mesh, pruneDagObjects=True) or []
    skin_clusters = cmds.ls(history, type='skinCluster') or []
    if not skin_clusters:
        raise RuntimeError(
            f'dem_bones_bake: {mesh!r} has no skinCluster. Run bindSkin '
            f'or BBW Bind first; DemBones Bake polishes existing weights.'
        )
    skin_cluster = skin_clusters[0]
    influences = get_skin_influences(mesh) or set()
    if len(influences) < 2:
        raise RuntimeError(
            f'dem_bones_bake: skinCluster {skin_cluster!r} has only '
            f'{len(influences)} influence(s). DemBones needs at least 2.'
        )
    return mesh, skin_cluster


def _read_skin_state(skin_cluster: str):
    """Return (influences, W_initial) for the skinCluster.

    influences: list[str] long-path joint names in skinCluster's index order
    W_initial: (n_verts, n_joints) float64 — current weights, used as
               DemBones warm-start
    """
    import numpy as np
    from maya.api import OpenMaya as om
    from maya.api import OpenMayaAnim as oma

    short_infs = cmds.skinCluster(skin_cluster, q=True, influence=True) or []
    influences = []
    for short in short_infs:
        longs = cmds.ls(short, long=True) or []
        if not longs:
            raise RuntimeError(
                f'dem_bones_bake: influence {short!r} on skinCluster '
                f'{skin_cluster!r} could not be resolved to a long path.'
            )
        influences.append(longs[0])

    # Read current weights via MFnSkinCluster.getWeights — batch + fast.
    sel_list = om.MSelectionList()
    sel_list.add(skin_cluster)
    sc_obj = sel_list.getDependNode(0)
    fn_skin = oma.MFnSkinCluster(sc_obj)

    # Need the mesh DAG + a component covering all verts.
    sc_outputs = cmds.skinCluster(skin_cluster, q=True, geometry=True) or []
    if not sc_outputs:
        raise RuntimeError(
            f'dem_bones_bake: skinCluster {skin_cluster!r} has no output geometry.'
        )
    mesh_short = sc_outputs[0]
    mesh_sel = om.MSelectionList()
    mesh_sel.add(mesh_short)
    dag = mesh_sel.getDagPath(0)
    dag.extendToShape()

    n_verts = cmds.polyEvaluate(mesh_short, vertex=True)
    fn_comp = om.MFnSingleIndexedComponent()
    comp_obj = fn_comp.create(om.MFn.kMeshVertComponent)
    fn_comp.addElements(range(n_verts))

    influence_indices = om.MIntArray(list(range(len(influences))))
    weights_flat = fn_skin.getWeights(dag, comp_obj, influence_indices)
    W_initial = np.asarray(weights_flat, dtype=np.float64).reshape(
        n_verts, len(influences)
    )
    return influences, W_initial


def _sample_timeline(mesh: str, influences: list) -> tuple:
    """Scrub the active Maya timeline and sample deformed mesh + joint
    transforms at each frame.

    Returns:
      V_rest: (n_verts, 3) float64 — rest pose vertices (frame 0 if in range,
                                    else timeline start)
      V_animated: (n_frames, n_verts, 3) float64
      joint_transforms: (n_frames, n_joints, 4, 4) float64 — world matrices
      start_frame: int — first sampled frame (for status messages)
      end_frame: int — last sampled frame

    Raises RuntimeError if the timeline range is <2 frames.
    """
    import numpy as np
    from maya.api import OpenMaya as om

    start_frame = int(cmds.playbackOptions(q=True, min=True))
    end_frame = int(cmds.playbackOptions(q=True, max=True))
    n_frames = end_frame - start_frame + 1
    if n_frames < 2:
        raise RuntimeError(
            f'dem_bones_bake: timeline range is {n_frames} frame(s). Load a '
            f'ROM animation (at least 2 frames) before clicking bake.'
        )

    # Resolve rest frame. Frame 0 is the default T-pose (raw ROM anims start on
    # frame 1). If frame 0 is in [start, end], use it. Otherwise use start_frame and warn.
    if start_frame <= 0 <= end_frame:
        rest_frame = 0
    else:
        rest_frame = start_frame
        cmds.warning(
            f'dem_bones_bake: bind frame 0 outside playback range '
            f'[{start_frame}, {end_frame}]. Using frame {start_frame} as rest '
            f'pose — verify it matches the bindPose.'
        )

    # MFnMesh DAG path for getPoints
    mesh_sel = om.MSelectionList()
    mesh_sel.add(mesh)
    dag = mesh_sel.getDagPath(0)
    dag.extendToShape()
    fn_mesh = om.MFnMesh(dag)

    # Sample rest pose first
    cmds.currentTime(rest_frame, edit=True)
    rest_points = fn_mesh.getPoints(om.MSpace.kWorld)
    V_rest = np.array([[p.x, p.y, p.z] for p in rest_points], dtype=np.float64)
    n_verts = V_rest.shape[0]

    n_joints = len(influences)
    V_animated = np.zeros((n_frames, n_verts, 3), dtype=np.float64)
    joint_transforms = np.zeros((n_frames, n_joints, 4, 4), dtype=np.float64)

    for i, f in enumerate(range(start_frame, end_frame + 1)):
        cmds.currentTime(f, edit=True)
        pts = fn_mesh.getPoints(om.MSpace.kWorld)
        V_animated[i] = np.array([[p.x, p.y, p.z] for p in pts], dtype=np.float64)
        for j_idx, j_name in enumerate(influences):
            mat_flat = cmds.xform(j_name, q=True, ws=True, m=True)
            # cmds.xform returns row-major 16-float list. Reshape to 4x4.
            # Maya matrices are row-major (translation in row 3); DemBones
            # convention is column-major (translation in column 3). Transpose
            # at sample time so downstream consumers see column-major xforms.
            joint_transforms[i, j_idx] = np.array(
                mat_flat, dtype=np.float64
            ).reshape(4, 4).T

    # Convert raw world matrices to RELATIVE-TO-BIND skin matrices:
    #   S_b(f) = M_world_b(f) @ inv(M_world_b(bind))
    # DemBones reconstructs vertex positions as
    #   v_world(f) = sum_b w_b * S_b(f) * v_world(bind)
    # so the bind-frame skin matrix must be identity (any other value makes
    # the bind-pose reconstruction impossible to satisfy with any weights,
    # and the QP solver converges back to the warm-start as a poor local
    # optimum — which is exactly the "DemBones not learning anything" symptom
    # Adrian hit with raw world matrices on the dybbuk character).
    rest_index = rest_frame - start_frame
    M_bind = joint_transforms[rest_index]                # (n_joints, 4, 4)
    M_bind_inv = np.linalg.inv(M_bind)                   # (n_joints, 4, 4)
    # Broadcast batched matmul: (n_frames, n_joints, 4, 4) @ (1, n_joints, 4, 4)
    joint_transforms = joint_transforms @ M_bind_inv[np.newaxis, ...]

    return V_rest, V_animated, joint_transforms, start_frame, end_frame


def _apply_to_compare_duplicate(mesh: str, influences: list, W,
                                  max_influences: int) -> tuple:
    """Debug path: duplicate the input mesh and bind the duplicate to the
    same joints, then write DemBones weights to the duplicate's skinCluster.
    The ORIGINAL mesh + its skinCluster + any deformer stack (deltaMush etc.)
    are left untouched. Lets the rigger A/B by hiding/showing each mesh.

    Returns (dupe_transform_path, dupe_skin_cluster) — both stay in the scene.
    """
    # Scrub to bind pose so the duplicate captures the rest-pose geometry +
    # the joint world matrices match bind. The current frame is whatever
    # _sample_timeline left us at (end_frame); we need frame 0.
    cmds.currentTime(0, edit=True)

    short_name = mesh.split('|')[-1]
    dupe = cmds.duplicate(mesh, name=f'{short_name}_dembones_compare#')[0]
    # cmds.duplicate by default does NOT bring input connections — the dupe
    # is a fresh mesh with no skinCluster, no deltaMush. We bind it cleanly
    # below and overwrite the bind weights with DemBones output.

    cmds.select(influences + [dupe], replace=True)
    dupe_sc = cmds.skinCluster(
        toSelectedBones=True, bindMethod=0, skinMethod=0,
        maximumInfluences=max_influences, normalizeWeights=1,
        obeyMaxInfluences=True,
    )[0]
    write_weights_back(dupe, dupe_sc, W)
    return dupe, dupe_sc


def dem_bones_bake(max_influences: int = 4,
                    debug_compare: bool = False) -> dict:
    """Public entrypoint — replace the selected mesh's skinCluster weights
    with a DemBones-optimized solution that reproduces the deformation
    seen over the active Maya timeline.

    Workflow:
      1. Preflight: confirm py_dem_bones + scipy + numpy import in the
         mayapy subprocess.
      2. Validate selection: one mesh with existing skinCluster + >=2 influences.
      3. Read existing skinCluster's influences + current weights (warm-start).
      4. Sample timeline range - V_rest + V_animated + joint_transforms.
      5. Spawn mayapy subprocess to run py_dem_bones.
      6. Write returned weights back via MFnSkinCluster.setWeights.

    Args:
      max_influences: per-vertex influence cap. Default 4 (game convention).
      debug_compare: when True, leave the input mesh + its existing
        skinCluster + any deformer chain (deltaMush etc.) untouched, and
        instead duplicate the input + bind the duplicate to the same joints +
        write DemBones weights to the duplicate. Both meshes stay in the
        scene at the same world position so the rigger can hide/show each
        to A/B compare. Useful when validating bake quality before
        committing to overwrite the original.

    Returns:
      {'n_verts': int, 'n_joints': int, 'n_frames': int,
       'solve_time_s': float, 'skin_cluster': str,
       'compare_mesh': str | None, 'compare_skin_cluster': str | None}

    Raises RuntimeError on any precondition failure or subprocess error.
    """
    import time
    import numpy as np

    _verify_dem_bones_deps()
    mesh, skin_cluster = _validate_selection()
    influences, W_initial = _read_skin_state(skin_cluster)
    V_rest, V_animated, joint_transforms, start_frame, end_frame = (
        _sample_timeline(mesh, influences)
    )
    n_frames = V_animated.shape[0]

    print(
        f'[dem_bones_bake] Sampling {V_rest.shape[0]} verts x '
        f'{len(influences)} joints x {n_frames} frames '
        f'(range {start_frame}-{end_frame}).'
    )

    t0 = time.time()
    out = run_solver_subprocess(_DEM_BONES_RUNNER_PATH,
        input_arrays={
            'V_rest': V_rest,
            'V_animated': V_animated,
            'joint_transforms': joint_transforms,
            'W_initial': W_initial,
            'max_influences': np.asarray(int(max_influences), dtype=np.int32),
            'F': np.zeros((0, 3), dtype=np.int32),  # placeholder; runner doesn't use topology
        },
        output_keys=['W'],
        timeout_s=600,   # ROM bakes can be slower than BBW (more frames * iters)
        tool_name='dem_bones_bake',
    )
    W = out['W']
    solve_time_s = time.time() - t0

    compare_mesh = None
    compare_skin_cluster = None
    if debug_compare:
        compare_mesh, compare_skin_cluster = _apply_to_compare_duplicate(
            mesh, influences, W, max_influences,
        )
        print(
            f'[dem_bones_bake] debug_compare=True - DemBones weights '
            f'written to duplicate {compare_mesh!r}. Original mesh + '
            f'skinCluster + deformer stack untouched. Hide/show each '
            f'mesh to A/B the deformation.'
        )
    else:
        write_weights_back(mesh, skin_cluster, W)
        print(
            f'[dem_bones_bake] {V_rest.shape[0]} verts x {len(influences)} '
            f'joints x {n_frames} frames in {solve_time_s:.1f}s. Weights '
            f'replaced on skinCluster {skin_cluster!r}.'
        )

    return {
        'n_verts': int(V_rest.shape[0]),
        'n_joints': int(len(influences)),
        'n_frames': int(n_frames),
        'solve_time_s': float(solve_time_s),
        'skin_cluster': skin_cluster,
        'compare_mesh': compare_mesh,
        'compare_skin_cluster': compare_skin_cluster,
    }
