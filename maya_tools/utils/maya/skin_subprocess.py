# maya_tools/utils/maya/skin_subprocess.py
"""Cross-tool subprocess + weight-write helpers for KS skinning solver tools.

The pattern is: a Maya-side orchestrator reads scene state, serializes inputs
to a temp .npz, spawns mayapy.exe as a subprocess that runs the solver
(libigl, py-dem-bones, etc.), and reads optimized weights back from a second
.npz. The subprocess pattern bypasses Maya GUI's bundled numpy 1.24.4 conflict
with packages built against numpy 2.x — mayapy launched fresh picks up the
user-scope numpy 2.4.6 instead.

V1/V2 (BBW) baked these helpers inside bbw_skin_app.py. V3 (DemBones) wants
them too. Promoted here to avoid duplication and to lock the seam shape
for future solver tools.

NOTE: this module intentionally does NOT import maya.cmds at module top so it
remains importable from mayapy subprocesses (which run outside Maya GUI and
do not have maya.cmds). Functions that require Maya API use deferred imports
inside the function body.
"""
__author__ = "Adrian Melian"

import subprocess
import sys
import tempfile
from pathlib import Path


def resolve_mayapy() -> str:
    """Locate mayapy.exe alongside the running maya.exe.

    Maya GUI's sys.executable is `<bin>/maya.exe`; mayapy.exe sits in the same
    bin directory. Raises RuntimeError if not found there.
    """
    maya_bin_dir = Path(sys.executable).parent
    mayapy = maya_bin_dir / 'mayapy.exe'
    if not mayapy.exists():
        raise RuntimeError(
            f'mayapy.exe not found at {mayapy}. '
            f'Expected it alongside Maya\'s executable ({sys.executable}).'
        )
    return str(mayapy)


def run_solver_subprocess(runner_path, input_arrays: dict,
                           output_keys: list, timeout_s: int = 300,
                           tool_name: str = 'solver') -> dict:
    """Spawn mayapy.exe to run the given runner script with input_arrays
    serialized to a temp .npz. Read the output .npz and return a dict of
    the requested output_keys.

    Args:
      runner_path: Path to the runner .py file (will be sys.argv[0] equiv).
      input_arrays: dict[str, np.ndarray | scalar] — written via np.savez.
      output_keys: list[str] — keys to read back from the runner's output.npz.
      timeout_s: subprocess timeout in seconds.
      tool_name: used in error messages so callers know which tool failed.

    Returns: dict[str, np.ndarray] containing each output_key's array.

    Forwards subprocess stdout + stderr to Maya's script editor.
    Raises RuntimeError on non-zero return code; the underlying stderr is
    visible in the script editor for diagnosis.
    """
    import numpy as np
    mayapy = resolve_mayapy()
    runner_path = str(runner_path)

    with tempfile.TemporaryDirectory(prefix=f'{tool_name}_') as tmp:
        input_path = Path(tmp) / 'input.npz'
        output_path = Path(tmp) / 'output.npz'
        np.savez(str(input_path), **{k: np.asarray(v) for k, v in input_arrays.items()})

        result = subprocess.run(
            [mayapy, runner_path, str(input_path), str(output_path)],
            cwd=tmp,
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )
        if result.stdout.strip():
            print(result.stdout.rstrip())
        if result.stderr.strip():
            print(result.stderr.rstrip())
        if result.returncode != 0:
            raise RuntimeError(
                f'{tool_name}: subprocess failed (exit {result.returncode}). '
                f'Check the script editor for the subprocess stderr output above.'
            )
        if not output_path.exists():
            raise RuntimeError(
                f'{tool_name}: subprocess returned exit 0 but did not write '
                f'output.npz. Bug in the runner script.'
            )
        out = {}
        with np.load(str(output_path)) as data:
            for key in output_keys:
                if key not in data.files:
                    raise RuntimeError(
                        f'{tool_name}: output.npz missing required key {key!r}. '
                        f'Keys present: {sorted(data.files)}.'
                    )
                out[key] = data[key]
        return out


def write_weights_back(mesh: str, skin_cluster: str, W) -> None:
    """Replace the weights on the existing skinCluster with W.

      W: (n_verts, n_joints) float64 — must match the skinCluster's
         influence count and ordering. The caller is responsible for
         passing W with columns in the order returned by
         cmds.skinCluster(skin_cluster, q=True, influence=True).

    Uses maya.api.OpenMaya 2.0's MFnSkinCluster.setWeights for batch write.
    cmds.skinPercent per-vertex is ~100x slower and not appropriate for
    production use.
    """
    from maya.api import OpenMaya as om
    from maya.api import OpenMayaAnim as oma

    sel_list = om.MSelectionList()
    sel_list.add(mesh)
    dag = sel_list.getDagPath(0)
    dag.extendToShape()

    sel_list2 = om.MSelectionList()
    sel_list2.add(skin_cluster)
    sc_obj = sel_list2.getDependNode(0)
    fn_skin = oma.MFnSkinCluster(sc_obj)

    n_verts, n_joints = W.shape

    fn_comp = om.MFnSingleIndexedComponent()
    comp_obj = fn_comp.create(om.MFn.kMeshVertComponent)
    fn_comp.addElements(range(n_verts))

    influence_indices = om.MIntArray(list(range(n_joints)))
    weights = om.MDoubleArray(W.flatten().tolist())

    fn_skin.setWeights(dag, comp_obj, influence_indices, weights, normalize=False)


def postprocess_weights(W, joint_positions, V, max_influences: int = 4):
    """Enforce non-negativity, partition-of-unity, and a max-influences cap.

    Args:
      W: (n_verts, n_joints) — raw weights from a solver
      joint_positions: (n_joints, 3) — used by nearest-joint fallback
      V: (n_verts, 3) — used by nearest-joint fallback (looks up vert position
         when row sums to 0 after clamp)
      max_influences: cap per vertex (default 4 — game convention)

    Returns: (n_verts, n_joints) float64, non-negative, row-sums-to-1,
             at most max_influences non-zero entries per row.

    Nearest-joint fallback: if a row sums to 0 after the negative-clamp,
    assign weight 1.0 to the joint closest to V[v_idx]. Prints fallback
    count to stderr.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    W = np.clip(W.copy(), 0.0, None)
    n_verts, n_joints = W.shape
    K = max_influences
    if n_joints > K:
        smallest_indices = np.argpartition(W, n_joints - K, axis=1)[:, :n_joints - K]
        rows = np.arange(n_verts)[:, None]
        W[rows, smallest_indices] = 0.0

    row_sums = W.sum(axis=1, keepdims=True)
    fallback_mask = (row_sums.flatten() == 0.0)
    n_fallback = int(fallback_mask.sum())
    if n_fallback > 0:
        tree = cKDTree(joint_positions)
        for v_idx in np.where(fallback_mask)[0]:
            _, j_idx = tree.query(V[v_idx], k=1)
            W[v_idx] = 0.0
            W[v_idx, int(j_idx)] = 1.0
        sys.stderr.write(
            f'postprocess_weights: {n_fallback} vertex(es) fell back to '
            f'nearest-joint assignment (no positive weight after clamp).\n'
        )
        row_sums = W.sum(axis=1, keepdims=True)

    W /= row_sums
    return W
