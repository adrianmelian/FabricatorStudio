# maya_tools/scene_batch/scene_batch_runner.py
"""Runs INSIDE the spawned `mayapy.exe` process.

Called from the parent (`scene_batch_app.run_one_scene`) via:
    <mayapy> <this-file> <payload-json-string>

argv[1] is a JSON string (matches the anim_pipeline.py pattern):
    {
        "schema_version":   "1.0",
        "scene_path":       str,   # absolute path to the .ma/.mb to open
        "user_script":      str,   # python source, may be empty
        "export":           bool,
        "save":             bool,
        "log_prefix":       str,   # marker prefix, default "[batch]"
        "repo_python_root": str    # added to sys.path so maya_tools imports work
    }

The runner reads the payload, initializes `maya.standalone` (matching
anim_pipeline), loads fbxmaya (needed for exporter_app.export_all),
opens the scene, optionally execs the user's Python, optionally calls
`exporter_app.export_all()`, optionally saves the scene. Prints
`[batch] STAGE OK/ERROR <detail>` markers to stdout so the parent can
parse outcomes without depending on exit codes.

Exits 0 if it reached the final `[batch] DONE` marker. Stage-level
errors do NOT propagate to the exit code — the parent reads them from
the markers so per-scene failures don't abort the batch.
"""
__author__ = "Adrian Melian"

import json
import sys
import traceback


def main() -> int:
    payload = json.loads(sys.argv[1])

    # sys.path: prepend the repo root BEFORE we import maya_tools.* anything.
    # The parent always passes this — fallback is a no-op only if a caller
    # invokes the runner manually with a stripped payload.
    repo = payload.get('repo_python_root') or ''
    if repo and repo not in sys.path:
        sys.path.insert(0, repo)

    prefix = payload.get('log_prefix') or '[batch]'
    print(f'{prefix} PAYLOAD argv', flush=True)

    # Initialize maya.standalone BEFORE importing maya.cmds (mayapy contract).
    import maya.standalone
    maya.standalone.initialize(name='python')

    import maya.cmds as cmds
    # Pre-load fbxmaya — exporter_app.export_all() needs it. User scripts
    # that need additional plugins (mtoa, ngSkinTools, etc.) should
    # cmds.loadPlugin them at the top of their own script.
    try:
        cmds.loadPlugin('fbxmaya', quiet=True)
    except RuntimeError:
        pass

    # Stage 1: open the scene.
    scene_path = payload.get('scene_path') or ''
    try:
        cmds.file(scene_path, open=True, force=True)
    except Exception as exc:
        print(f'{prefix} OPEN_FAIL {exc}', flush=True)
        traceback.print_exc()
        print(f'{prefix} DONE', flush=True)
        return 0
    print(f'{prefix} OPENED {scene_path}', flush=True)

    # Stage 2: user script (optional).
    user_script = payload.get('user_script') or ''
    if user_script.strip():
        # __file__ defaults to the scene path so user scripts that probe
        # `os.path.dirname(__file__)` get a useful sibling directory
        # (typical animator pattern: write a sidecar next to the scene).
        script_globals = {'__name__': '__main__', '__file__': scene_path}
        try:
            exec(user_script, script_globals)
            print(f'{prefix} SCRIPT_OK', flush=True)
        except Exception as exc:
            print(f'{prefix} SCRIPT_ERROR {exc}', flush=True)
            traceback.print_exc()
            # Continue to export/save anyway — user's script erroring
            # doesn't necessarily mean the export is wrong.

    # Stage 3: export (optional).
    if payload.get('export'):
        try:
            from maya_tools.export.exporter_app import export_all
            paths = export_all() or []
            print(f'{prefix} EXPORT_OK {",".join(str(p) for p in paths)}',
                  flush=True)
        except Exception as exc:
            print(f'{prefix} EXPORT_ERROR {exc}', flush=True)
            traceback.print_exc()

    # Stage 4: save (optional).
    if payload.get('save'):
        try:
            cmds.file(save=True, force=True)
            print(f'{prefix} SAVE_OK', flush=True)
        except Exception as exc:
            print(f'{prefix} SAVE_ERROR {exc}', flush=True)
            traceback.print_exc()

    print(f'{prefix} DONE', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
