# maya_tools/skinning/dem_bones_bake/dem_bones_runner.py
"""DemBones Bake subprocess runner — pure-Python solver.

Runs inside `mayapy.exe` as a SUBPROCESS (NOT in Maya GUI). Required
because py-dem-bones is a Cython wrapper around the DemBones C++ library
built against numpy 2.x, and Maya GUI pre-loads numpy 1.24.4 (ABI conflict
fails any compiled-binding library). mayapy.exe launched as a fresh
process picks up the --user-scope numpy 2.4.6 instead.

Reads: input.npz with keys
  V_rest, V_animated, joint_transforms, W_initial, max_influences, F
Writes: output.npz with key W (n_verts x n_joints, float64)

Pipeline:
  1. py_dem_bones.DemBones() instance
  2. Configure: nnz=max_influences, nIters=30, nTransIters=0 (lock joints)
  3. set_rest_pose / set_animated_poses / set_transformations / set_weights
  4. compute() then get_weights()
  5. top-K trim + renormalize (defensive; DemBones nnz constraint is soft)
  6. Save W to output.npz

Usage:
  "C:\\Program Files\\Autodesk\\Maya2025\\bin\\mayapy.exe" dem_bones_runner.py input.npz output.npz

Spec: docs/superpowers/specs/2026-05-26-dem-bones-bake-design.md
"""
__author__ = "Adrian Melian"

import sys
from pathlib import Path

# Bootstrap repo root onto sys.path so `maya_tools` is importable when this
# module is run directly as a subprocess. __file__ is:
#   <repo>/maya_tools/skinning/dem_bones_bake/dem_bones_runner.py
# Four levels up (.parent x 4) reaches the repo root.
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent.parent  # dem_bones_bake → skinning → maya_tools → repo
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import scipy  # noqa: F401  (cKDTree is used by postprocess_weights fallback)
import py_dem_bones as pdb

from maya_tools.utils.maya.skin_subprocess import postprocess_weights


def _run_dem_bones(V_rest, V_animated, joint_transforms, W_initial,
                    max_influences: int):
    """Run py_dem_bones with joints locked (weights-only refinement).

    Args:
      V_rest: (n_verts, 3) float64 — rest pose (bind) vertex positions
      V_animated: (n_frames, n_verts, 3) float64 — deformed positions per frame
      joint_transforms: (n_frames, n_joints, 4, 4) float64 — RELATIVE-TO-BIND
                         skin matrices per frame, in COLUMN-major Maya/DemBones
                         convention (translation in column 3). Maya-side caller
                         is responsible for the M_world(f) @ inv(M_world(bind))
                         conversion + the row→column-major transpose. Bind-frame
                         transforms must be identity for DemBones' bind-pose
                         reconstruction to satisfy V_animated[bind] = V_rest.
      W_initial: (n_verts, n_joints) float64 — warm-start weights (e.g. BBW V2)
      max_influences: int — cap per vertex (top-K trim happens in postprocess)

    Returns: W (n_verts, n_joints) float64 — DemBones-optimized weights.

    Raises RuntimeError if py_dem_bones.compute() fails.

    Eigen layout notes (py-dem-bones C++ binding is column-major Eigen):
      set_rest_pose   expects (3, n_verts)            — V_rest.T
      set_animated_poses expects (3, n_verts*n_frames) — frames concat horizontally
      set_transformations expects (n_frames*3, 4)     — each frame's first 3 rows stacked
      set_weights     expects (n_joints, n_verts)     — W_initial.T
      get_weights()   returns (n_joints, n_verts)     — transpose back to (n_verts, n_joints)
    """
    n_verts = V_rest.shape[0]
    n_frames = V_animated.shape[0]
    n_joints = joint_transforms.shape[1]

    db = pdb.DemBones()
    # Counts must be set BEFORE the set_* arrays so the internal Eigen
    # matrices are sized correctly. Subject metadata = 1 subject covering
    # all frames (we're not doing multi-subject training).
    db.nV = int(n_verts)
    db.nB = int(n_joints)
    db.nF = int(n_frames)
    db.nS = 1
    db.fStart = np.array([0], dtype=np.int32)
    db.subjectID = np.zeros(int(n_frames), dtype=np.int32)
    db.nIters = 30           # total iterations
    db.nInitIters = 10       # initialization phase iterations
    db.nTransIters = 0       # 0 = lock joint transforms (UE5 skeleton contract)
    db.nWeightsIters = 3     # per-iteration weights-refinement count
    db.nnz = int(max_influences)
    db.weightsSmooth = 1e-4  # weight-field smoothness regularizer (DemBones default)

    # --- reshape inputs to Eigen 2D column-major layout ---
    # rest pose: (3, n_verts)
    V_rest_eigen = np.ascontiguousarray(
        V_rest.astype(np.float64).T  # (n_verts, 3) → (3, n_verts)
    )
    db.set_rest_pose(V_rest_eigen)

    # animated poses: (3, n_verts * n_frames) — frames concatenated horizontally
    # V_animated is (n_frames, n_verts, 3); we need (3, n_verts*n_frames)
    V_anim_eigen = np.ascontiguousarray(
        V_animated.astype(np.float64)          # (n_frames, n_verts, 3)
            .transpose(2, 1, 0)                 # → (3, n_verts, n_frames)
            .reshape(3, n_verts * n_frames)      # → (3, n_verts*n_frames)
    )
    db.set_animated_poses(V_anim_eigen)

    # transforms: (n_frames * n_joints * 3, 4) — each (bone, frame) first 3 rows stacked.
    # DemBones internal layout: for each frame, n_joints bone matrices each contributing
    # 3 rows → shape (n_frames * n_joints * 3, 4). We extract first 3 rows of each 4x4.
    # joint_transforms is (n_frames, n_joints, 4, 4).
    xf = joint_transforms.astype(np.float64)        # (n_frames, n_joints, 4, 4)
    xf_3 = xf[:, :, :3, :]                          # (n_frames, n_joints, 3, 4)
    xf_eigen = np.ascontiguousarray(
        xf_3.reshape(n_frames * n_joints * 3, 4)    # (n_frames*n_joints*3, 4)
    )
    db.set_transformations(xf_eigen)

    # weights: (n_joints, n_verts) — W_initial is (n_verts, n_joints)
    W_eigen = np.ascontiguousarray(W_initial.astype(np.float64).T)
    db.set_weights(W_eigen)

    try:
        db.compute()
    except Exception as e:
        raise RuntimeError(
            f'dem_bones_runner: py_dem_bones.compute() raised: {e}. '
            f'Try a shorter ROM (fewer frames) or fewer joints to diagnose. '
            f'Full traceback above.'
        )
    # get_weights() returns (n_joints, n_verts); transpose to (n_verts, n_joints)
    W = np.asarray(db.get_weights(), dtype=np.float64).T
    return W


def main(input_npz_path: str, output_npz_path: str) -> None:
    """Top-level subprocess entrypoint: load input, run DemBones, save output.

    See module docstring for the .npz contract.
    """
    data = np.load(input_npz_path)
    V_rest = np.asarray(data['V_rest'], dtype=np.float64)
    V_animated = np.asarray(data['V_animated'], dtype=np.float64)
    joint_transforms = np.asarray(data['joint_transforms'], dtype=np.float64)
    W_initial = np.asarray(data['W_initial'], dtype=np.float64)
    max_influences = int(data['max_influences'])

    n_verts = V_rest.shape[0]
    n_joints = joint_transforms.shape[1]
    n_frames = V_animated.shape[0]

    sys.stderr.write(
        f'dem_bones_runner: solving {n_verts} verts x {n_joints} joints x '
        f'{n_frames} frames (max_influences={max_influences}).\n'
    )

    W_raw = _run_dem_bones(V_rest, V_animated, joint_transforms, W_initial,
                            max_influences)

    # Defensive: DemBones nnz is a soft constraint; enforce top-K trim and
    # partition-of-unity via the shared postprocess helper. The nearest-joint
    # fallback uses the first-frame joint positions (extracted from the rest-
    # pose transforms' translation columns) since DemBones doesn't return a
    # joint_positions array directly.
    joint_positions = joint_transforms[0, :, :3, 3]   # (n_joints, 3) rest translations
    W = postprocess_weights(W_raw, joint_positions, V_rest,
                             max_influences=max_influences)

    np.savez(output_npz_path, W=W)
    print(
        f'[dem_bones_runner] {n_verts} verts x {n_joints} joints x '
        f'{n_frames} frames, top-{max_influences} capped. '
        f'Wrote {output_npz_path}.'
    )


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.stderr.write(
            'usage: mayapy dem_bones_runner.py <input.npz> <output.npz>\n'
        )
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
