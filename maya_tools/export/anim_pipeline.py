# Python/maya_tools/export/anim_pipeline.py
"""Anim exporter — subprocess-based export.

Spawns a mayapy subprocess that opens the saved scene, runs the export
pipeline, and exits. The user's interactive Maya is never touched, which
avoids the viewport corruption that occurs when mutating + reopening files
in a session with Arnold loaded.

Public:
    run_export(clip_data, out_path, config) -> str

The runner script lives at anim_export_runner.py adjacent to this file.
"""
__author__ = "Adrian Melian"

import json
import os
import subprocess
import sys
from pathlib import Path

import maya.cmds as cmds


def run_export(clip_data: dict, out_path: str, config: dict) -> str:
    """Execute the anim export in a mayapy subprocess.

    Caller is responsible for:
      - validating clip_data via anim_core.validate_clip
      - prompting the user about unsaved changes (and saving) BEFORE calling
      - resolving out_path

    The scene must be saved to disk — the subprocess opens the on-disk file,
    not the in-memory state. anim_app.export_clip handles the save_callback
    flow before this runs.

    Returns the FBX path written.
    Raises RuntimeError if the scene is unsaved/dirty or the subprocess fails.
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

    mayapy = _find_mayapy()
    runner = str(Path(__file__).parent / 'anim_export_runner.py')
    repo_python_root = str(Path(__file__).parent.parent)

    payload = json.dumps({
        'scene_path': scene,
        'clip_data': clip_data,
        'out_path': out_path,
        'config': config,
        'repo_python_root': repo_python_root,
    })

    result = subprocess.run(
        [mayapy, runner, payload],
        capture_output=True,
        text=True,
        timeout=600,  # 10 min max — bake on a heavy rig can be slow
    )

    # Surface stdout so callers see runner progress messages
    if result.stdout:
        for line in result.stdout.splitlines():
            print(f'[mayapy] {line}')

    if result.returncode != 0:
        raise RuntimeError(
            f'mayapy export failed (returncode={result.returncode}):\n'
            f'STDOUT:\n{result.stdout}\n'
            f'STDERR:\n{result.stderr}'
        )

    if not os.path.isfile(out_path):
        raise RuntimeError(
            f'mayapy reported success but FBX is missing: {out_path}'
        )

    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────

def _find_mayapy() -> str:
    """Locate mayapy.exe by walking common candidates."""
    maya_loc = os.environ.get('MAYA_LOCATION')
    if maya_loc:
        candidate = os.path.join(maya_loc, 'bin', 'mayapy.exe')
        if os.path.isfile(candidate):
            return candidate

    if sys.executable:
        bin_dir = os.path.dirname(sys.executable)
        candidate = os.path.join(bin_dir, 'mayapy.exe')
        if os.path.isfile(candidate):
            return candidate

    candidate = r'C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe'
    if os.path.isfile(candidate):
        return candidate

    raise RuntimeError(
        'Could not locate mayapy.exe. Set MAYA_LOCATION or check that '
        'Maya 2025 is installed at the standard location.'
    )
