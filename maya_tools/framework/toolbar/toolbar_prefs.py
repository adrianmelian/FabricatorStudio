"""FabricatorStudio toolbar — unified prefs (spec section 3.5).

One JSON file holds all toolbar state. Lives in the un-versioned shared maya
folder so one file serves every Maya version, matching the installer layout.
Pure Python on purpose: testable without Maya or Qt.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from maya_tools.framework import project_config_paths

PREFS_FILENAME = "fabricator_toolbar_prefs.json"

_DEFAULTS = {
    "dock_mode": "bottom",   # top | left | right | bottom | floating
    "last_achieved": "",     # diagnostics only — dock_mode is the user's wish and
                             # is always retried; spec §3.5 amendment lands in Task 14
    "visible": True,
    "scale": 1.0,
    "active_project": "",    # Wave B2 chooser: remembered project-config folder
                             # name. Scaffold - no consumer reads it yet (the
                             # exporter still resolves per-scene); the project
                             # workstream wires it up later.
    "per_widget": {},
    "floating_pos": None,    # [x, y] of the floating window, recorded at
                             # teardown/mode-switch, restored by _float (C9)
    "layout": {              # Phase D drag-to-reorder (2026-07-02): per-group
        "version": 1,        # user layout. groups: group_id -> {"order": [item
        "groups": {},        # ids], "hidden": [item ids]}. "hidden" ships empty
    },                       # and UI-less in v1; the resolver already honors it
                             # so show/hide later is a prefs-only feature.
    "onboarding": {},        # t34 first-run moments: {moment_name: True} once
                             # shown (first_run.py). Missing key = unseen.
    # Settings > MAYA INTEGRATION (2026-07-18): gate the userSetup build_*
    # calls + the anim marking menu registration at Maya startup.
    # menu/shelf default OFF (Adrian, 2026-07-20): the TOOLBAR is the front
    # door, and a fresh install should not scatter a menu and two shelf tabs
    # across Maya uninvited. Both are one checkbox away in Settings, which
    # builds them live on enable. Only machines with no prefs file yet (a
    # new install) see the new default; existing prefs keep their values.
    "load_menu": False,
    "load_shelf": False,
    "load_hotkeys": True,    # accelerators add no visual clutter: stay on
    "anim_marking_menu": True,
}


def default_prefs() -> dict:
    return copy.deepcopy(_DEFAULTS)


def prefs_path() -> Path:
    """Shared un-versioned maya folder (same root the installer
    targets). Resolution delegated to project_config_paths.maya_root
    so a redirected Documents folder (e.g. D:\\Documents) resolves
    identically inside Maya and headless."""
    return project_config_paths.maya_root() / PREFS_FILENAME


def load_prefs(path: Path | None = None) -> dict:
    p = Path(path) if path else prefs_path()
    if not p.exists():
        return default_prefs()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # ValueError covers JSONDecodeError AND UnicodeDecodeError (torn UTF-8 write)
        return default_prefs()
    prefs = default_prefs()
    if isinstance(data, dict):
        for key, value in data.items():
            if key in _DEFAULTS:
                prefs[key] = _coerce(key, value, _DEFAULTS[key])
            else:
                prefs[key] = value       # forward-compat: unknown keys pass through
    return prefs


def _coerce(key: str, value, default):
    """Per-key type coercion (Phase C hardening): a wrong-typed value in a
    hand-edited or torn prefs file falls back to that key's default instead
    of poisoning every consumer downstream."""
    if key == "floating_pos":            # default None carries no type: shape-check
        if (isinstance(value, (list, tuple)) and len(value) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                        for v in value)):
            return [int(value[0]), int(value[1])]
        return None
    if isinstance(default, bool):        # bool BEFORE float: True is an int
        return value if isinstance(value, bool) else default
    if isinstance(default, float):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, dict):
        return value if isinstance(value, dict) else copy.deepcopy(default)
    return value


def save_prefs(prefs: dict, path: Path | None = None) -> Path:
    """Atomic write (Phase C convention, deliberate): same-dir '.tmp' then
    os.replace - a crash mid-write can no longer tear the file (the torn-
    UTF-8 fallback in load_prefs stays as the last line of defense)."""
    p = Path(path) if path else prefs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return p
