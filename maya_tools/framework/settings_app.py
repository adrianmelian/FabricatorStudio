"""App facade for the Settings tool: the per-machine face over the two
_user/ state families - the shared-configs-root pointer
(_user/settings.json) and per-project machine bindings (_user/<slug>.json).
Zero Qt at module scope and no Maya-session functions at all: everything
here runs under bare mayapy (settings_ui is a thin caller). Writes never
touch the shared project pair - bind_project is what makes binding work
against a read-only studio configs root, which the Mindmeld edit path
cannot do (it saves the pair before the bindings). See
docs/superpowers/specs/2026-07-18-settings-tool-design.md sec 6.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from maya_tools.framework import (config_validation, project_config_io,
                                  project_config_paths, project_config_resolve,
                                  project_setup_app)
from maya_tools.framework.config_validation import Issue
from maya_tools.framework.toolbar import toolbar_prefs
# Re-exported on purpose: the UI catches settings_app.ValidationError for
# both tiers, same shape as Mindmeld's.
from maya_tools.framework.project_setup_app import ValidationError  # noqa: F401


# ─────────────────────────────────────────────
# Shared configs root (the pointer)
# ─────────────────────────────────────────────

def configs_root_status() -> dict:
    """Which configs root is in effect and why: {'effective_root',
    'source': 'env'|'pointer'|'local', 'pointer_value'}. Feeds the panel's
    status line; precedence lives in project_config_paths.configs_dir and
    is only REPORTED here, never re-decided."""
    pointer = project_config_paths.pointer_configs_root()
    if os.environ.get("FABRICATOR_PROJECT_CONFIGS"):
        source = "env"
    elif pointer is not None:
        source = "pointer"
    else:
        source = "local"
    return {
        "effective_root": str(project_config_paths.configs_dir()).replace("\\", "/"),
        "source": source,
        "pointer_value": str(pointer).replace("\\", "/") if pointer else None,
    }


def _settings_path() -> Path:
    return project_config_resolve.user_dir() / project_config_paths.SETTINGS_FILE


def _read_settings() -> dict:
    """Tolerant read of _user/settings.json: absent/corrupt/non-object
    reads as {} (same posture as the resolver's pointer read)."""
    try:
        parsed = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_settings(data: dict) -> Path:
    target = _settings_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise project_config_io.ReadOnlyConfigError(
            f"Cannot create {target.parent} - it looks read-only. Check "
            "folder permissions and retry.") from exc
    project_config_io.write_json_atomic(target, data)
    return target


def set_configs_root(path) -> Path:
    """Persist the shared-configs pointer. Validates the target exists and
    is a directory; preserves every other key in settings.json."""
    value = str(path or "").strip()
    if not value or not Path(value).is_dir():
        raise ValidationError([Issue(
            "error", "configs_root",
            f"Shared configs root {value!r} does not exist or is not a "
            f"folder.")])
    data = _read_settings()
    data["configs_root"] = value.replace("\\", "/")
    return _write_settings(data)


def clear_configs_root() -> Path:
    """Remove the pointer (back to the local default). Other keys keep."""
    data = _read_settings()
    if "configs_root" not in data:
        return _settings_path()
    data.pop("configs_root")
    return _write_settings(data)


# ─────────────────────────────────────────────
# Maya integration toggles (spec sec 5b)
# ─────────────────────────────────────────────

# Key order = UI row order. Values live in fabricator_toolbar_prefs.json
# (the unified per-user prefs); the consumers are gate lines at the top of
# fs_menu.build_menu / fs_shelf.build_shelves / fs_hotkeys.build_hotkeys /
# maya_startup._register_anim_marking_menu.
INTEGRATION_KEYS = ("load_menu", "load_shelf", "load_hotkeys",
                    "anim_marking_menu")


def integration_prefs() -> dict:
    """{key: bool} for the four startup-integration toggles."""
    prefs = toolbar_prefs.load_prefs()
    return {key: bool(prefs.get(key, True)) for key in INTEGRATION_KEYS}


def set_integration_pref(key: str, value: bool) -> None:
    """Persist one toggle. Unknown keys are a programming error."""
    if key not in INTEGRATION_KEYS:
        raise ValueError(f"Unknown integration pref {key!r} "
                         f"(expected one of {INTEGRATION_KEYS})")
    prefs = toolbar_prefs.load_prefs()
    prefs[key] = bool(value)
    toolbar_prefs.save_prefs(prefs)


# ─────────────────────────────────────────────
# Project bindings
# ─────────────────────────────────────────────

def list_projects_with_status() -> list[dict]:
    """[{'slug','name','bound','derived'}] for every project at the
    effective configs root. bound = both roots present in the persisted
    bindings OR the v1 derivation (matching resolve_project's gate);
    derived = bound only via derive_v1_bindings (nothing persisted yet)."""
    out = []
    for meta in project_config_io.list_projects():
        persisted = project_config_resolve.load_bindings(meta["slug"])
        derived = {} if persisted else (
            project_config_io.derive_v1_bindings(meta["slug"]) or {})
        effective = persisted or derived
        bound = bool(effective.get("source_art_root")
                     and effective.get("content_root"))
        out.append({"slug": meta["slug"], "name": meta["name"],
                    "bound": bound, "derived": bound and not persisted})
    return out


def get_bindings(slug: str) -> dict:
    """Editor prefill: persisted bindings, else the v1 derivation, else {}."""
    return (project_config_resolve.load_bindings(slug)
            or project_config_io.derive_v1_bindings(slug) or {})


def binding_status(slug: str) -> dict:
    """Resolve-aware status for one project's EFFECTIVE bindings (persisted
    bindings, else the v1 derivation - same precedence as get_bindings):
    {'state': 'green'|'orange', 'reason': str}. 'green' iff both
    source_art_root and content_root are present AND Path(root).is_dir()
    on THIS machine. 'orange' otherwise, with 'unbound' when a root string
    is empty or 'missing:<path>' when a root is set but not a directory
    (whichever root fails first, source before content). Pure filesystem
    check, no Qt - feeds Mindmeld's row/right-pane color (spec sec 'Status
    color'). Today's plain 'bound' truthiness check (list_projects_with_status,
    above) never touches disk; this does."""
    bindings = get_bindings(slug)
    src = bindings.get("source_art_root") or ""
    dst = bindings.get("content_root") or ""
    if not src or not dst:
        return {"state": "orange", "reason": "unbound"}
    for root in (src, dst):
        if not Path(root).is_dir():
            return {"state": "orange", "reason": f"missing:{root}"}
    return {"state": "green", "reason": ""}


def bind_project(slug: str, bindings: dict) -> Path:
    """THE new seam: validate the bindings tier alone (against the other
    bound projects, excluding this slug) and persist ONLY
    _user/<slug>.json. The shared pair is never opened for write, so a
    read-only studio root binds fine. The reserved 'settings' slug is
    refused here via the unknown-slug gate (it can never appear in
    list_projects) and again in save_bindings (defense in depth)."""
    issues = []
    known = {meta["slug"] for meta in project_config_io.list_projects()}
    if slug not in known:
        issues.append(Issue(
            "error", "slug",
            f"Project {slug!r} does not exist at the current configs "
            f"root."))
    issues += config_validation.validate_bindings(
        bindings,
        existing_bindings=project_setup_app._existing_bindings(
            exclude_slug=slug))
    if any(issue.severity == "error" for issue in issues):
        raise ValidationError(issues)
    return project_config_resolve.save_bindings(slug, bindings)
