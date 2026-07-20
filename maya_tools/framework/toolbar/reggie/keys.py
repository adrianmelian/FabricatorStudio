"""API key detection for the Reggie panel.

Env var ONLY, by design (Adrian, 2026-07-06): nothing is ever stored to
disk, prefs, or Maya settings by us. `setx` writes HKCU\\Environment for
FUTURE processes, so refresh_from_registry() pulls the value into this
running session; no Maya restart needed on Windows.

The key must never appear in any log, error message, transcript render,
bug-report URL, or build report (enforced by test_reggie_core.py).
"""
from __future__ import annotations

import os
import sys

__author__ = "Adrian Melian"

ENV_VAR = "ANTHROPIC_API_KEY"


def get_api_key() -> str | None:
    val = os.environ.get(ENV_VAR, "").strip()
    return val or None


def masked(key: str) -> str:
    """Display form: last 4 chars only. Never show more."""
    if len(key) >= 12:
        return f"...{key[-4:]}"
    return "(set)"


def _read_user_env_var(name: str) -> str | None:
    """HKCU\\Environment read (Windows). None elsewhere or when absent."""
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            val, _type = winreg.QueryValueEx(k, name)
    except OSError:
        return None
    val = str(val).strip()
    return val or None


def refresh_from_registry() -> str | None:
    """Return the key, injecting a freshly-setx'd value into this session."""
    val = get_api_key()
    if val:
        return val
    val = _read_user_env_var(ENV_VAR)
    if val:
        os.environ[ENV_VAR] = val
    return val
