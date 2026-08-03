# Python/maya_tools/export/skeletal_pipeline.py
"""Rig (skeletal-mesh) exporter — subprocess-based export.

Spawns a mayapy subprocess that opens the saved scene, runs the export surgery
on a throwaway copy, and exits. The user's interactive Maya is never touched —
no in-scene unbuild/rebuild, no undo dance, and (the win Adrian called out) no
need to delete-then-recreate the Armature live: the subprocess deletes it and
just walks away. Same harness the anim exporter already uses (anim_pipeline).

Public:
    run_export(joints, meshes, out_path, fbx_preset) -> str

The runner script lives at skeletal_export_runner.py adjacent to this file.
"""
__author__ = "Adrian Melian"

import json
import os
import subprocess
from pathlib import Path

import maya.cmds as cmds

from maya_tools.export import anim_pipeline


def run_export(joints: list, meshes: list, out_path: str,
               fbx_preset: dict, engine_up_axis: str = 'y',
               format: str = 'fbx', character_name: str = '') -> str:
    """Execute the rig export in a mayapy subprocess.

    Caller is responsible for:
      - resolving out_path
      - SAVING the scene first (the subprocess opens the on-disk file, not the
        in-memory state). skeletal_mesh._export_ks_subprocess force-saves.

    engine_up_axis: resolved 'y'/'z' (see export_core.engine_up_axis); 'z'
    folds the Z-up engine conversion into the root joint in the subprocess
    (FBX only — the USD stage's upAxis metadata is the single axis mechanism).

    format: 'fbx' (default) or 'usd' — the Armature/Unreal delivery USD.
    character_name names the USD's root prim; '' falls back to the rig label
    then the scene stem inside the runner.

    Returns the path written. Raises RuntimeError if the scene is
    unsaved/dirty or the subprocess fails.
    """
    scene = cmds.file(q=True, sn=True)
    if not scene:
        raise RuntimeError('run_export: scene must be saved to disk.')
    if cmds.file(q=True, modified=True):
        raise RuntimeError(
            'run_export: scene has unsaved changes; the subprocess opens the '
            'on-disk file. Caller must save before calling.'
        )

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    mayapy = anim_pipeline._find_mayapy()
    runner = str(Path(__file__).parent / 'skeletal_export_runner.py')
    # The FS repo root (the directory CONTAINING maya_tools) — one level was
    # missing here since the runner was written, silently rescued by
    # userSetup.py putting the root on sys.path. With userSetup skipped
    # (below) the child depends on this being right.
    repo_python_root = str(Path(__file__).parent.parent.parent)

    payload = json.dumps({
        'scene_path': scene,
        'joints': list(joints),
        'meshes': list(meshes),
        'out_path': out_path,
        'fbx_preset': fbx_preset,
        'engine_up_axis': engine_up_axis,
        'format': format,
        'character_name': character_name,
        'repo_python_root': repo_python_root,
    })

    # The child must NOT run userSetup.py: the FS startup restores the
    # toolbar, which imports PySide6 into a batch process. Field-crashed
    # 2026-08-03 on a USD export — Qt loaded next to pxr took the child
    # down with a GIL fatal at interpreter shutdown, and it adds ~10s of
    # toolbar/menu load to every export. The runner sets up its own
    # sys.path and plugins; nothing in userSetup is needed here.
    env = dict(os.environ)
    env['MAYA_SKIP_USERSETUP_PY'] = '1'

    result = subprocess.run(
        [mayapy, runner, payload],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,  # 10 min max — teardown/orient on a heavy rig can be slow
    )

    # Surface stdout so callers see runner progress messages.
    if result.stdout:
        for line in result.stdout.splitlines():
            print(f'[mayapy] {line}')

    if result.returncode != 0:
        raise RuntimeError(
            f'mayapy skeletal export failed (returncode={result.returncode}):\n'
            f'STDOUT:\n{result.stdout}\n'
            f'STDERR:\n{result.stderr}'
        )

    if not os.path.isfile(out_path):
        raise RuntimeError(
            f'mayapy reported success but the export is missing: {out_path}'
        )

    return out_path
