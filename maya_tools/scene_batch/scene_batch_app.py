# maya_tools/scene_batch/scene_batch_app.py
"""Headless API for the Scene Batch tool.

Two responsibilities:

1. Per-scene subprocess orchestration (`run_one_scene`) — locates
   mayapy.exe, runs the runner script with a JSON payload on argv,
   polls the child while pumping Qt events so the parent UI stays
   responsive (Cancel button + ESC fire during the subprocess, not
   just between scenes). Parses [batch] markers from the captured
   output and returns a result dict.

   Uses mayapy.exe (matching anim_pipeline.py's proven pattern) rather
   than `maya.exe -batch -command "python(...)"` — the latter has
   intractable Windows-cmd × MEL-quoting × Python-escape issues that
   silently drop the -command argument on some configurations.
   The runner initializes maya.standalone and loads fbxmaya upfront;
   user scripts that need additional plugins should cmds.loadPlugin
   them at the top of their script.

2. State persistence (`save_state` / `load_state`) — writes the UI's
   editor state (scene list, script, checkboxes) to
   <userAppDir>/scene_batch_state.json so the next session restores
   automatically.
"""
__author__ = "Adrian Melian"

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from PySide6 import QtWidgets

import maya.cmds as cmds


SCHEMA_VERSION = '1.0'
STATE_FILENAME = 'scene_batch_state.json'
LOG_PREFIX = '[batch]'
POLL_INTERVAL_MS = 50   # how often the parent pumps Qt events during a subprocess


# ─── Subprocess orchestration ────────────────────────────────────────────────

def run_one_scene(scene_path: Path, user_script: str, export: bool, save: bool,
                  log_callback: Optional[Callable[[str], None]] = None,
                  cancel_check: Optional[Callable[[], bool]] = None) -> dict:
    """Run one scene through a spawned `mayapy.exe` subprocess.

    Polls the child via subprocess.Popen + readline rather than blocking
    on subprocess.run(). Between polls it calls
    QApplication.processEvents() so the parent Qt UI (progress dialog
    Cancel button, ESC keypress, paint events) stays interactive while
    mayapy is locked waiting for the child.

    `log_callback`, if provided, is called once per output line as it
    arrives (live streaming into a LoggerWidget). `cancel_check`, if
    provided, is polled each tick — when it returns True the loop
    keeps waiting for the current subprocess to finish naturally
    (graceful stop) but the caller is expected to skip launching the
    next scene.

    Returns a result dict:
        {
            'scene':     Path,
            'ok':        bool,            # reached [batch] DONE without OPEN_FAIL
            'opened':    bool,
            'scripted':  Optional[bool],  # None = no user script
            'exported':  Optional[bool],
            'saved':     Optional[bool],
            'errors':    list[str],       # one entry per [batch] *_ERROR line
            'cancelled': bool,            # True if cancel_check returned True at any point
        }
    """
    scene_path = Path(scene_path)
    if not scene_path.is_file():
        msg = f'scene file not found: {scene_path}'
        if log_callback:
            log_callback(msg)
        return _result_failure(scene_path, msg)

    mayapy = _find_mayapy()
    runner = str(Path(__file__).parent / 'scene_batch_runner.py')
    repo_python_root = str(Path(__file__).resolve().parents[2])
    payload_json = json.dumps({
        'schema_version': SCHEMA_VERSION,
        'scene_path':       str(scene_path),
        'user_script':      user_script or '',
        'export':           bool(export),
        'save':             bool(save),
        'log_prefix':       LOG_PREFIX,
        'repo_python_root': repo_python_root,
    })

    # mayapy invocation: <mayapy> <runner.py> <payload-json-on-argv>.
    # Matches anim_pipeline.py exactly — no MEL, no -command escape
    # gymnastics, payload travels via argv[1].
    cmd = [mayapy, runner, payload_json]

    collected_lines: list[str] = []
    cancel_seen = False
    proc = None
    reader_thread = None
    line_queue: 'queue.Queue[Optional[str]]' = queue.Queue()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,         # line-buffered so each [batch] marker arrives promptly
        )

        # Background reader pushes lines into a queue. readline() is
        # blocking — running it on the main thread would freeze the Qt
        # event loop during Maya's silent boot window (~5-20s of plugin
        # loading). The daemon thread terminates with the process; the
        # sentinel `None` signals EOF to the main loop.
        def _reader(stdout, q):
            try:
                for line in iter(stdout.readline, ''):
                    q.put(line)
            finally:
                q.put(None)

        reader_thread = threading.Thread(
            target=_reader, args=(proc.stdout, line_queue), daemon=True,
        )
        reader_thread.start()

        # Main pump loop: non-blocking queue poll + processEvents + cancel
        # check. Empty-ticks fire processEvents so Cancel/ESC stay live
        # even when the child is mid-boot and silent.
        sleep_s = max(POLL_INTERVAL_MS, 1) / 1000.0
        eof_seen = False
        while not eof_seen:
            try:
                item = line_queue.get(timeout=sleep_s)
            except queue.Empty:
                item = '__no_data__'

            if item == '__no_data__':
                # No line this tick — pump events, check cancel, loop.
                pass
            elif item is None:
                # Reader hit EOF; drain any remaining queue, then break.
                eof_seen = True
            else:
                stripped = item.rstrip('\n').rstrip('\r')
                collected_lines.append(stripped)
                if log_callback:
                    try:
                        log_callback(stripped)
                    except Exception:
                        traceback.print_exc()

            try:
                QtWidgets.QApplication.processEvents()
            except Exception:
                traceback.print_exc()

            if cancel_check is not None:
                try:
                    if cancel_check():
                        cancel_seen = True
                except Exception:
                    traceback.print_exc()
    finally:
        # Payload travels via argv — no temp files to clean up.
        if proc is not None and proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
        if reader_thread is not None:
            # Daemon thread will be reaped on interpreter exit; join briefly
            # so we don't leave it spinning on the next subprocess.
            reader_thread.join(timeout=1.0)

    result = _parse_output('\n'.join(collected_lines), scene_path)
    result['cancelled'] = cancel_seen
    return result


def _result_failure(scene_path: Path, message: str) -> dict:
    return {
        'scene': scene_path, 'ok': False, 'opened': False,
        'scripted': None, 'exported': None, 'saved': None,
        'errors': [message], 'cancelled': False,
    }


def _parse_output(stdout: str, scene_path: Path) -> dict:
    """Walk lines, look for [batch] markers, build the result dict.

    Maya batch mode decorates every stdout line with a `HH:MM:SS NMB | `
    timestamp+memory prefix, so our `[batch] STAGE` markers don't sit at
    the line start — search for the marker anywhere in the line.
    """
    result = {
        'scene': scene_path, 'ok': False, 'opened': False,
        'scripted': None, 'exported': None, 'saved': None,
        'errors': [], 'cancelled': False,
    }
    marker = LOG_PREFIX + ' '
    for raw in stdout.splitlines():
        pos = raw.find(marker)
        if pos < 0:
            continue
        stage = raw[pos + len(marker):].strip()
        if stage.startswith('OPENED'):
            result['opened'] = True
        elif stage.startswith('OPEN_FAIL'):
            result['errors'].append(stage)
        elif stage == 'SCRIPT_OK':
            result['scripted'] = True
        elif stage.startswith('SCRIPT_ERROR'):
            result['scripted'] = False
            result['errors'].append(stage)
        elif stage.startswith('EXPORT_OK'):
            result['exported'] = True
        elif stage.startswith('EXPORT_ERROR'):
            result['exported'] = False
            result['errors'].append(stage)
        elif stage == 'SAVE_OK':
            result['saved'] = True
        elif stage.startswith('SAVE_ERROR'):
            result['saved'] = False
            result['errors'].append(stage)
        elif stage == 'DONE':
            result['ok'] = result['opened']
    return result


def _userapp_dir() -> Path:
    """Resolve <userAppDir> from Maya, raising on empty."""
    base = cmds.internalVar(userAppDir=True)
    if not base or not str(base).strip():
        raise RuntimeError(
            'scene_batch_app: cmds.internalVar(userAppDir=True) returned '
            'empty — cannot resolve userAppDir.'
        )
    return Path(base)


def _find_mayapy() -> str:
    """Resolve mayapy.exe via env var → sys.executable's dir → known fallbacks."""
    maya_loc = os.environ.get('MAYA_LOCATION') or ''
    if maya_loc:
        candidate = os.path.join(maya_loc, 'bin', 'mayapy.exe')
        if os.path.isfile(candidate):
            return candidate

    if sys.executable:
        bin_dir = os.path.dirname(sys.executable)
        candidate = os.path.join(bin_dir, 'mayapy.exe')
        if os.path.isfile(candidate):
            return candidate

    for fallback in (
        r'C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe',
        r'C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe',
    ):
        if os.path.isfile(fallback):
            return fallback

    raise RuntimeError(
        'Could not locate mayapy.exe. Set the MAYA_LOCATION env var to your '
        'Maya install root (the folder containing bin/mayapy.exe).'
    )


# ─── State persistence ───────────────────────────────────────────────────────

def state_path() -> Path:
    """Absolute path to the per-user state JSON."""
    return _userapp_dir() / STATE_FILENAME


def save_state(scenes: list, user_script: str, export: bool, save: bool) -> None:
    """Persist the editor state. Silent-skip on IO failure (state restore
    is convenience, not load-bearing)."""
    payload = {
        'schema_version': SCHEMA_VERSION,
        'scenes':         [str(s) for s in scenes],
        'user_script':    user_script or '',
        'export':         bool(export),
        'save':           bool(save),
    }
    try:
        with open(state_path(), 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
    except OSError:
        pass


def load_state() -> dict:
    """Return the persisted state dict, or an empty default if none.

    Always returns a dict with the keys scenes / user_script / export /
    save populated; defaults match the UI's clean-slate state.

    Discards state files whose schema_version doesn't match
    SCHEMA_VERSION — if the schema evolves, a stale state from an old
    install would otherwise silently load with possibly-wrong defaults.
    """
    default = {'scenes': [], 'user_script': '', 'export': True, 'save': False}
    try:
        p = state_path()
    except RuntimeError:
        return default
    if not p.is_file():
        return default
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return default
    if data.get('schema_version') != SCHEMA_VERSION:
        return default
    return {
        'scenes':      list(data.get('scenes') or []),
        'user_script': str(data.get('user_script') or ''),
        'export':      bool(data.get('export', True)),
        'save':        bool(data.get('save', False)),
    }
