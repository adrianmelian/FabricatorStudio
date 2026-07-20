"""Tier merge: project config (shared truth) + user bindings (per machine)
-> ONE resolved config carrying only absolute paths. Every consumer reads
the resolved view; none touch raw tiers. Bindings live at
local_configs_dir()/_user/<slug>.json - ALWAYS local, never inside a
shared configs root (the shared tree may be read-only P4/git and must
never receive machine-absolute paths). Pure Python: no maya, no Qt -
importable and testable under bare mayapy (or py -3). See
docs/superpowers/specs/2026-07-18-project-setup-two-tier-roots-design.md sec 5.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from maya_tools.framework import config_generator
from maya_tools.framework import project_config_io
from maya_tools.framework import project_config_paths

_LOG = logging.getLogger(__name__)

BINDING_KEYS = ("source_art_root", "content_root",
                "pose_library_root_override", "anim_library_root_override",
                "blueprints_dir_override")

# _user/ holds one <slug>.json per project PLUS the configs-root pointer
# (project_config_paths.SETTINGS_FILE) - so that filename's stem can never
# be a project slug, or save_bindings would overwrite the pointer.
# config_validation._validate_identity rejects the name at author time;
# these guards are defense-in-depth for direct callers.
_RESERVED_SLUGS = {Path(project_config_paths.SETTINGS_FILE).stem}


class UnboundProjectError(RuntimeError):
    """Project config exists but this machine has no root bindings for it."""


def user_dir() -> Path:
    return project_config_paths.local_configs_dir() / project_config_paths.USER_DIRNAME


def _bindings_path(slug: str) -> Path:
    return user_dir() / f"{slug}.json"


def load_bindings(slug: str) -> dict:
    """The machine bindings for slug, or {} when absent/unreadable/not a
    JSON object (a corrupt bindings file reads as unbound, never raises
    into a caller). The reserved pointer filename never reads as
    bindings."""
    if slug in _RESERVED_SLUGS:
        return {}
    try:
        parsed = json.loads(_bindings_path(slug).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def save_bindings(slug: str, bindings: dict) -> Path:
    """Persist the 5-key bindings dict for slug (missing keys land as "").
    Returns the written path. A read-only local root surfaces as the
    actionable ReadOnlyConfigError, matching every other write path in
    this family."""
    if slug in _RESERVED_SLUGS:
        raise ValueError(
            f"'{slug}' is reserved for the configs-root pointer file "
            f"({project_config_paths.SETTINGS_FILE}) and cannot be a "
            "project slug.")
    out = {key: (bindings.get(key) or "") for key in BINDING_KEYS}
    path = _bindings_path(slug)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise project_config_io.ReadOnlyConfigError(
            f"Cannot create {path.parent} - it looks read-only. Check "
            "folder permissions and retry.") from exc
    project_config_io.write_json_atomic(path, out)
    return path


def _join(root: str, subpath: str) -> str:
    return str(Path(root) / subpath).replace("\\", "/") if (root and subpath) else ""


def _persisted_patterns(slug: str) -> tuple[list, dict]:
    """The on-disk patterns for a legacy (no-asset-classes) config, or
    empty shapes when unreadable. Legacy group names ({asset}, {subpath})
    work verbatim: each dest template pairs with its own match's groups."""
    target = (project_config_paths.configs_dir() / slug / "export_mapping.json")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], {"gameplay": [], "cinematic": []}
    if not isinstance(raw, dict):
        return [], {"gameplay": [], "cinematic": []}
    anim = raw.get("anim_path_patterns")
    if not isinstance(anim, dict):
        anim = {"gameplay": [], "cinematic": []}
    return (raw.get("path_patterns") or [], anim)


def resolve_project(slug: str) -> dict:
    """authored union + machine bindings -> the resolved config: everything
    a consumer needs with only absolute paths (source_art_root,
    content_root, pose/anim_library_root, blueprints_dir) alongside the
    project-tier fields. A v1 config with no bindings file resolves through
    derive_v1_bindings (transparent solo migration); a v2 config with no
    bindings raises the actionable UnboundProjectError."""
    authored = project_config_io.load_project(slug)
    bindings = load_bindings(slug) or project_config_io.derive_v1_bindings(slug) or {}
    src = (bindings.get("source_art_root") or "").replace("\\", "/")
    dst = (bindings.get("content_root") or "").replace("\\", "/")
    if not src or not dst:
        raise UnboundProjectError(
            f"Project '{slug}' has no machine bindings on this machine. "
            f"Open Settings (the toolbar gear) and bind Source Art Root + "
            f"Content Root (writes {_bindings_path(slug)})."
        )
    resolved = dict(authored)
    resolved["slug"] = slug
    resolved["source_art_root"] = src
    resolved["content_root"] = dst
    resolved["pose_library_root"] = (bindings.get("pose_library_root_override")
                                     or _join(src, authored.get("pose_library_subpath", "")))
    resolved["anim_library_root"] = (bindings.get("anim_library_root_override")
                                     or _join(src, authored.get("anim_library_subpath", "")))
    resolved["blueprints_dir"] = (bindings.get("blueprints_dir_override")
                                  or _join(src, authored.get("blueprints_subpath", "")))
    # Derived matching/routing patterns ride along: asset_classes is the
    # truth and the persisted copies are never round-tripped, so consumers
    # (export_core) always see patterns in lockstep with the classes.
    # LEGACY fallback: a pre-asset-classes config (the real 2026 archangel
    # shape - hand-authored patterns, no classes) keeps matching via its
    # persisted patterns until it is re-authored through Mindmeld (which
    # requires classes at save time).
    classes = authored.get("asset_classes") or []
    if classes:
        derived = config_generator.generate_patterns(classes)
        resolved["path_patterns"] = derived["path_patterns"]
        resolved["anim_path_patterns"] = derived["anim_path_patterns"]
    else:
        persisted = _persisted_patterns(slug)
        resolved["path_patterns"] = persisted[0]
        resolved["anim_path_patterns"] = persisted[1]
    return resolved


def resolve_all() -> list[dict]:
    """Resolved config per bound project; unbound projects are skipped with
    a log line (never an exception - one unbound project must not blank the
    exporter's config matching for every other project)."""
    out = []
    for meta in project_config_io.list_projects():
        try:
            out.append(resolve_project(meta["slug"]))
        except UnboundProjectError:
            # WARNING, not info: Maya's default root logger suppresses
            # info, and a bound-yesterday project silently vanishing from
            # export matching is a support trap.
            _LOG.warning("Skipping unbound project '%s' (no machine "
                         "bindings - bind it in Settings, the toolbar "
                         "gear)", meta["slug"])
        except Exception as exc:
            _LOG.warning("Could not resolve project '%s': %s", meta["slug"], exc)
    return out
