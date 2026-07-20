# maya_tools/skinning/autoskin/runner.py
"""AutoSkin runner — the Maya side of the subprocess boundary.

Pure Python: no bpy, no torch, no maya.cmds. It hands the inference venv a
temp FBX and gets WEIGHTS BACK AS DATA, which is the whole contract:

    temp FBX (mesh + the user's joints)   ->   weights.json
    probe.f32 (Maya's own vertex order)        {joint: {vertex: weight}}

Everything heavy — bpy, CUDA, the model — lives on the far side of that
boundary, in a process that can crash, hang or OOM without taking Maya
with it. That isolation is the point; it is why AutoSkin can ship a 7 GB
torch stack inside a Maya tool at all.

The return leg used to be a skinned proxy FBX that Maya imported and
copySkinWeights'd from. Weights are numbers, not a rig: sending them as
numbers costs the artist no FBX import dialog, no "joint rotations are
locked" prompt, no namespace defence against the proxy replacing their
mesh, and no FBX write+read on the end of every run.

Progress arrives as `[AUTOSKIN] stage: msg` lines on the child's stdout
and is forwarded to a callback, so the UI can show real stages rather
than a spinner that lies.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from maya_tools.skinning.autoskin import backend

_TAG = '[AUTOSKIN]'
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# How often the main thread wakes to pump the UI, poll Cancel, and check the
# deadline — regardless of whether the backend has said anything. Short enough
# that Cancel feels instant; long enough that it costs nothing over a 2-minute
# run (~600 ticks).
_TICK = 0.2

# Stages the backend reports, in order — the UI uses this to show a real
# plan instead of an indeterminate bar.
STAGES = ('prep', 'generating', 'harvesting', 'done')


class RunnerError(RuntimeError):
    """The generation failed. The message is user-facing."""


class RunnerCancelled(RunnerError):
    """The user cancelled. Not an error to apologise for — just stop."""


def backend_script() -> Path:
    return Path(__file__).with_name('runner_backend.py')


def run(input_fbx, output_json, probe, generate_joints: bool = False,
        progress=None, timeout: int = 1800, cancel_check=None) -> dict:
    """Skin `input_fbx` and write the weights to `output_json`.

    input_fbx: a Maya-exported FBX carrying the mesh(es) and, unless
        generate_joints is set, the user's bind joints.
    probe: the mesh's world-space vertex positions as MAYA ordered them,
        flat little-endian float32. The backend uses it to re-prove, on
        this mesh, that the FBX round-trip kept the vertex order — because
        the weights come home as vertex INDICES — and to recover the
        bpy -> Maya transform for any joints it generates.
    progress: optional callable(stage: str, message: str).
    cancel_check: optional callable() -> bool, polled while the engine
        runs. Returning True kills the subprocess and raises
        RunnerCancelled. A 100-second GPU job the artist has walked away
        from must be stoppable.

    Returns the backend's result dict: {'ok', 'weights_json', 'joints',
    'verts', 'influences', 'fidelity'}. Raises RunnerError on any failure,
    with the real reason — never a bare 'subprocess failed'.
    """
    health = backend.health()
    if not health['ready']:
        raise RunnerError(health['reason'])

    input_fbx, output_json, probe = Path(input_fbx), Path(output_json), Path(probe)
    if not input_fbx.is_file():
        raise RunnerError(f'input FBX not found: {input_fbx}')
    if not probe.is_file():
        raise RunnerError(f'vertex probe not found: {probe}')

    work = backend.backend_root() / 'work'
    payload = {
        'input_fbx': str(input_fbx),
        'output_json': str(output_json),
        'probe': str(probe),
        'generate_joints': bool(generate_joints),
        'repo': str(backend.repo_dir()),
        'work_dir': str(work),
    }

    cmd = [str(backend.venv_python()), str(backend_script()),
           json.dumps(payload)]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=_NO_WINDOW,
        )
    except OSError as exc:
        raise RunnerError(f'could not start the AutoSkin backend: {exc}')

    # Drain stdout on a THREAD and poll it on a WALL CLOCK.
    #
    # The obvious loop — `for line in proc.stdout:` — blocks in readline()
    # between lines, and everything the artist depends on was hanging off that
    # loop body: the Cancel poll, the UI event pump, and the timeout. The child
    # is SILENT for the entire engine run (runner_backend launches demo.py with
    # capture_output=True, so ~100 seconds pass with no stdout at all). The
    # consequences were exactly as bad as they sound, and all three were
    # confirmed: Maya sat in "Not Responding" for the whole generation, the
    # Cancel button could not even RECEIVE its click (no processEvents ran, so
    # the click never reached the widget that sets the flag), and `timeout`
    # could never fire because it only guarded proc.wait() AFTER the loop — so
    # a backend that hung while holding stdout open hung Maya forever, with no
    # escape but killing the application.
    #
    # A reader thread decouples liveness from the child's chattiness: the main
    # thread wakes on a fixed tick whether or not anything was printed, pumps
    # the UI, polls the cancel flag, and enforces its own deadline.
    q = queue.Queue()

    def _drain():
        try:
            for raw in proc.stdout:
                q.put(raw.rstrip('\n'))
        except Exception:
            pass
        finally:
            q.put(None)             # EOF sentinel

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    lines = []
    stage, msg = 'prep', ''
    deadline = time.monotonic() + timeout
    eof = False

    while not eof:
        try:
            line = q.get(timeout=_TICK)
        except queue.Empty:
            line = ''               # nothing said; we still owe the UI a tick
        else:
            if line is None:
                eof = True
                line = ''

        if line:
            lines.append(line)
            if line.startswith(_TAG):
                body = line[len(_TAG):].strip()
                head, _, tail = body.partition(':')
                stage, msg = head.strip(), tail.strip()

        # Pump the UI EVERY tick, not only when the child speaks. Re-sending the
        # last known stage is deliberate: the UI's pump is idempotent, and this
        # is what keeps Maya responsive (and Cancel clickable) through the long
        # silent stretch of the engine run.
        if progress:
            try:
                progress(stage, msg)
            except Exception:
                pass                # a UI hiccup must never kill a generation

        if cancel_check is not None:
            try:
                cancelled = bool(cancel_check())
            except Exception:
                cancelled = False
            if cancelled:
                proc.kill()
                proc.wait(timeout=30)
                raise RunnerCancelled('AutoSkin cancelled.')

        if time.monotonic() > deadline:
            proc.kill()
            proc.wait(timeout=30)
            raise RunnerError(f'AutoSkin timed out after {timeout}s.')

    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RunnerError('AutoSkin did not exit after closing its output.')

    stderr = proc.stderr.read() if proc.stderr else ''

    # The last non-tagged stdout line is the JSON result.
    result = None
    for line in reversed(lines):
        if line.startswith(_TAG) or not line.strip():
            continue
        try:
            result = json.loads(line)
        except ValueError:
            continue
        break

    if result is None:
        tail = '\n'.join((stderr or '\n'.join(lines)).strip().splitlines()[-15:])
        raise RunnerError(
            f'AutoSkin backend exited {proc.returncode} without a result.\n{tail}')
    if not result.get('ok'):
        raise RunnerError(result.get('error') or 'AutoSkin failed.')
    if not Path(result.get('weights_json') or '').is_file():
        raise RunnerError('AutoSkin reported success but wrote no weights.')
    return result


def _cli() -> int:
    """Dev entry point: run the pipeline from a shell.

    py -3 -m maya_tools.skinning.autoskin.runner <in.fbx> <out.json> <probe.f32>
    """
    if len(sys.argv) < 4:
        print(_cli.__doc__)
        return 2
    try:
        r = run(sys.argv[1], sys.argv[2], sys.argv[3],
                generate_joints='--generate-joints' in sys.argv,
                progress=lambda s, m: print(f'  {s}: {m}' if m else f'  {s}'))
    except RunnerError as exc:
        print(f'FAILED: {exc}')
        return 1
    print(json.dumps(r, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(_cli())
