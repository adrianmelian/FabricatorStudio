"""Project-config storage: read/write the two files a project config
authors (session.json + export_mapping.json) at configs_dir()/<slug>/.
Pure Python: no maya, no Qt - importable and testable under bare mayapy
(or py -3).

The setup tool holds ONE in-memory "authored" union dict (see
docs/superpowers/specs/2026-07-07-project-setup-tool-design.md sec 4.3);
this module is the sole translator between that union and the on-disk
pair. No cached globals here (unlike maya_startup/export_core), so tests
need only monkeypatch MAYA_APP_DIR - no importlib.reload.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from maya_tools.framework import config_generator
from maya_tools.framework import project_config_paths

# The session.json key list (session half of the authored union). See the
# shared build contract sec "The authored-config union".
SESSION_FIELDS = (
    "name", "engine",
    "linear_unit", "angular_unit", "frame_rate",
    "playback_start", "playback_end",
    "undo_depth",
    "camera_near_clip", "camera_far_clip",
    "grid", "blueprints_subpath",
    # t48: the mirror convention stamped onto NEW rigs at creation
    # ('unreal' | 'standard'; absent/None resolves as unreal). Session
    # tier: a project standard, not an export-mapping fact.
    "orient_convention",
)

# The export half of the authored union (v2): the keys load_project reads
# back from export_mapping.json, with their empty defaults. Loader and
# generator MUST stay in lockstep on this list - the io suite's full-dict
# round-trip assert is the net.
_EXPORT_AUTHORED_KEYS = (
    ("asset_classes", []), ("prefixes", {}), ("anim_prefix", ""),
    ("pose_library_subpath", ""), ("anim_library_subpath", ""),
    ("mesh_group_name", ""), ("engine_up_axis", ""), ("fbx_presets", {}),
)

_SLUG_UNSAFE = re.compile(r"[^a-z0-9_]+")


def slugify(name):
    """name -> lowercase, safe-chars folder slug.

    Mirrors config_validation._slugify EXACTLY (same regex, same "never
    strip/dedupe a leading underscore, no reserved-word fallback"
    behavior) - both compute the project IDENTITY slug, and any
    divergence would make the validator's slug-collision check disagree
    with the actual on-disk folder name this module creates for the same
    name. A name that folds to a leading-'_' or empty slug is rejected
    upstream by config_validation.validate_config before save_project is
    ever called; save_project()'s own leading-'_' guard is a second line
    of defense for an explicit slug= override. So slugify() itself stays
    dumb: no stripping, no deduping, no fallback.

    `(name or "")` tolerates a missing/None name without raising (unlike
    config_validation._slugify, whose only caller already guards against
    an empty name before calling it) - for any non-empty string input the
    two produce identical output."""
    return _SLUG_UNSAFE.sub("_", (name or "").strip().lower())


def split_authored(authored):
    """authored union -> (session_dict, export_mapping_dict). session_dict
    is the literal SESSION_FIELDS subset (session.json shape); export_mapping
    is the full generate_export_mapping() superset (export_mapping.json
    shape - derived keys regenerated from asset_classes, never persisted
    from a prior save)."""
    session = {field: authored.get(field) for field in SESSION_FIELDS}
    export_mapping = config_generator.generate_export_mapping(authored)
    return session, export_mapping


_LOG = logging.getLogger(__name__)

_SESSION_FILE = "session.json"
_EXPORT_MAPPING_FILE = "export_mapping.json"


class ReadOnlyConfigError(OSError):
    """Raised instead of a raw PermissionError when a project-config write
    target is read-only (the Perforce-unopened-file case). The message is
    actionable: check the file/folder out in source control and retry."""


def _cleanup_tmp(tmp_name):
    try:
        os.remove(tmp_name)
    except OSError:
        pass


def write_json_atomic(path, data):
    """Write `data` as JSON to `path` atomically: stage a temp file in the
    same directory (same filesystem, so os.replace is one atomic, network-
    safe rename), then replace it into place. A read-only destination
    (Perforce-unopened) raises PermissionError on the replace, which we
    surface as the actionable ReadOnlyConfigError. Public: the resolver's
    bindings IO and the template regen script reuse it."""
    payload = json.dumps(data, indent=2) + "\n"
    tmp_name = None
    try:
        # mkstemp sits INSIDE the try: a write-denied directory fails at
        # temp-file creation, and that too must surface as the actionable
        # ReadOnlyConfigError, not a raw PermissionError.
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except PermissionError as exc:
        if tmp_name:
            _cleanup_tmp(tmp_name)
        raise ReadOnlyConfigError(
            f"Cannot write {path.name} in {path.parent} - it looks "
            "read-only (likely checked into Perforce and not checked out). "
            "Check the file out and retry."
        ) from exc
    except OSError:
        if tmp_name:
            _cleanup_tmp(tmp_name)
        raise


def _create_atomic(target, root, session, export_mapping):
    """Create-only atomic publish: build both files in a staging dir inside
    `root` (mirrors project_config_paths._copy_config_atomic's staging+
    rename idiom - never-overwrite, race-safe, network-safe), then publish
    with one os.rename. dest.exists() up front is the fast-path guard; a
    race that creates dest between the check and the rename still surfaces
    as FileExistsError - unlike template seeding, a user create action must
    be told plainly if it lost the race, never silently discarded."""
    if target.exists():
        raise FileExistsError(f"Project '{target.name}' already exists at {target}")
    # staging is created INSIDE this try (not before it): the staging
    # mkdtemp call itself lands under `root` and must be covered by the
    # same PermissionError -> ReadOnlyConfigError translation as the rest
    # of the publish, or a read-only root would raise a raw PermissionError
    # at this first step instead of the actionable error. staging starts
    # as None so the finally below can tell "never got a staging dir" apart
    # from "got one, must clean it up" without a NameError.
    staging = None
    try:
        staging = tempfile.mkdtemp(prefix=f"_stage-{target.name}-", dir=str(root))
        staged = Path(staging) / target.name
        staged.mkdir()
        write_json_atomic(staged / _SESSION_FILE, session)
        write_json_atomic(staged / _EXPORT_MAPPING_FILE, export_mapping)
        os.rename(staged, target)
    except PermissionError as exc:
        raise ReadOnlyConfigError(
            f"Cannot create a project under {root} - it looks read-only "
            "(likely checked into Perforce and not checked out). Check it "
            "out and retry."
        ) from exc
    except OSError:
        if target.exists():   # a concurrent save won the race
            raise FileExistsError(
                f"Project '{target.name}' already exists at {target}"
            )
        raise
    finally:
        if staging is not None:
            try:
                shutil.rmtree(staging)
            except OSError as exc:
                # Never swallow this silently: a stuck staging dir (AV
                # lock, open handle) is worth knowing about even though it
                # doesn't fail the save itself (the target already
                # published above).
                _LOG.warning("Failed to clean up staging dir %s: %s", staging, exc)


def save_project(authored, *, overwrite=False, slug=None):
    """Write session.json + export_mapping.json for `authored` to
    configs_dir()/<slug>/.

    Target dir = `slug` if given, else `slugify(authored['name'])`. `slug`
    PINS the folder: pass the ORIGINAL slug on an edit even when the
    cosmetic name has changed, and the save still lands on that original
    folder - identity is the slug, never the 'name' field, so a rename can
    never orphan a second folder alongside the first.

    overwrite=False (create): atomic, never-overwrite - FileExistsError if
    the slug is already taken.
    overwrite=True (edit): writes each file atomically in place
    (os.replace), creating the directory on first save under that slug.

    A read-only target (Perforce-unopened) raises ReadOnlyConfigError, not
    a raw PermissionError, on either path. Returns the project dir Path.

    Raises ValueError if `slug` is given and starts with '_' - that prefix
    is reserved for packaged templates and the _trash archive and must
    never be a live project's identity.
    """
    target_slug = slug if slug is not None else slugify(authored.get("name", ""))
    if target_slug.startswith("_"):
        raise ValueError(
            f"'{target_slug}' is a reserved template/trash slug (leading "
            "'_') and cannot be used as a project slug."
        )
    root = project_config_paths.configs_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise ReadOnlyConfigError(
            f"Cannot create {root} - it looks read-only (likely checked "
            "into Perforce and not checked out). Check it out and retry."
        ) from exc

    target = root / target_slug
    session, export_mapping = split_authored(authored)

    if not overwrite:
        _create_atomic(target, root, session, export_mapping)
        return target

    try:
        target.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise ReadOnlyConfigError(
            f"Cannot write to {target} - it looks read-only (likely "
            "checked into Perforce and not checked out). Check it out and "
            "retry."
        ) from exc
    write_json_atomic(target / _SESSION_FILE, session)
    write_json_atomic(target / _EXPORT_MAPPING_FILE, export_mapping)
    return target


def _rel_under(abs_path: str, root: str) -> str | None:
    """abs_path expressed relative to root (forward slashes), or None if it
    is not strictly under root. Case-insensitive compare (Windows drives)."""
    if not abs_path or not root:
        return None
    a = os.path.normpath(abs_path)
    r = os.path.normpath(root)
    if not os.path.normcase(a).startswith(os.path.normcase(r).rstrip("\\/") + os.sep):
        return None
    return os.path.relpath(a, r).replace("\\", "/")


def _upgrade_v1(session: dict, export_mapping: dict) -> dict:
    """v1 on-disk pair -> the v2 authored union, in memory only (read-side
    migration; the disk pair upgrades on the next save). Absolute library/
    blueprints paths under the old project_root become relative subpaths;
    off-root absolutes become per-user overrides instead (see
    derive_v1_bindings), so their subpath here stays blank."""
    root = export_mapping.get("project_root", "")
    authored = {field: session.get(field) for field in SESSION_FIELDS}
    for key, default in _EXPORT_AUTHORED_KEYS:
        authored[key] = export_mapping.get(key, default)
    # Prefer an already-present v2 subpath over recomputing from the v1
    # keys: a crash between the pair's two sequential writes can leave a
    # v2 session.json beside a v1 export_mapping.json, and recomputing
    # from the missing v1 key would silently wipe the value.
    authored["blueprints_subpath"] = (
        session.get("blueprints_subpath")
        or _rel_under(session.get("blueprints_dir", ""), root) or "")
    authored["pose_library_subpath"] = (
        authored["pose_library_subpath"]
        or _rel_under(export_mapping.get("pose_library_root", ""), root) or "")
    authored["anim_library_subpath"] = (
        authored["anim_library_subpath"]
        or _rel_under(export_mapping.get("anim_library_root", ""), root) or "")
    return authored


def derive_v1_bindings(slug) -> dict | None:
    """v1 config on disk -> proposed machine bindings; None when already v2
    (or unreadable / rootless). The other half of read-side migration:
    project_root seeds both roots, off-root absolute library/blueprints
    paths become per-user overrides. Nothing on disk changes here."""
    target = project_config_paths.configs_dir() / slug
    try:
        session = json.loads((target / _SESSION_FILE).read_text(encoding="utf-8"))
        export_mapping = json.loads((target / _EXPORT_MAPPING_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if export_mapping.get("schema_version", 1) >= 2:
        return None
    root = export_mapping.get("project_root", "")
    if not root or not os.path.isabs(root):
        # A relative v1 root can't seed machine bindings - surface as
        # unbound (actionable) rather than emitting CWD-relative "roots".
        return None

    def _override(abs_path):
        if not abs_path or _rel_under(abs_path, root):
            return ""
        return abs_path.replace("\\", "/")

    return {
        "source_art_root": root.replace("\\", "/"),
        "content_root": root.replace("\\", "/"),
        "pose_library_root_override": _override(export_mapping.get("pose_library_root", "")),
        "anim_library_root_override": _override(export_mapping.get("anim_library_root", "")),
        "blueprints_dir_override": _override(session.get("blueprints_dir", "")),
    }


def load_project(slug):
    """configs_dir()/<slug>/{session,export_mapping}.json -> the authored
    union dict, ALWAYS v2 shape (a v1 pair upgrades on read via
    _upgrade_v1; disk is untouched until the next save). asset_classes
    from export_mapping.json is the TRUTH; the persisted path_patterns/
    anim_path_patterns/schema_version/_note are generated artifacts and
    are dropped here (config_generator regenerates them on the next save,
    never round-tripped)."""
    target = project_config_paths.configs_dir() / slug
    session_path = target / _SESSION_FILE
    export_path = target / _EXPORT_MAPPING_FILE
    if not session_path.is_file() or not export_path.is_file():
        raise FileNotFoundError(f"Project '{slug}' not found at {target}")

    session = json.loads(session_path.read_text(encoding="utf-8"))
    export_mapping = json.loads(export_path.read_text(encoding="utf-8"))

    if export_mapping.get("schema_version", 1) < 2:
        return _upgrade_v1(session, export_mapping)
    authored = {field: session.get(field) for field in SESSION_FIELDS}
    for key, default in _EXPORT_AUTHORED_KEYS:
        authored[key] = export_mapping.get(key, default)
    return authored


def list_projects():
    """[{'slug','name','engine'}] for every saved project, '_'-prefixed
    (template/trash) dirs excluded. A dir with a missing/unparsable
    session.json is skipped rather than raising - one corrupt config must
    not blank the whole manager list."""
    root = project_config_paths.configs_dir()
    if not root.is_dir():
        return []
    out = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        session_path = entry / _SESSION_FILE
        if not session_path.is_file():
            continue
        try:
            session = json.loads(session_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({
            "slug": entry.name,
            "name": session.get("name", entry.name),
            "engine": session.get("engine", ""),
        })
    return out


_TRASH_DIRNAME = "_trash"


def delete_project(slug):
    """Soft delete: move configs_dir()/<slug>/ to configs_dir()/_trash/
    <slug>_<timestamp>/ (never rm). Returns the new Path. A read-only
    target raises ReadOnlyConfigError."""
    root = project_config_paths.configs_dir()
    target = root / slug
    if not target.is_dir():
        raise FileNotFoundError(f"Project '{slug}' not found at {target}")

    trash_root = root / _TRASH_DIRNAME
    trash_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = trash_root / f"{slug}_{stamp}"
    suffix = 1
    while dest.exists():
        dest = trash_root / f"{slug}_{stamp}-{suffix}"
        suffix += 1

    try:
        os.rename(target, dest)
    except PermissionError as exc:
        raise ReadOnlyConfigError(
            f"Cannot delete project '{slug}' - its config looks read-only "
            "(likely checked into Perforce and not checked out). Check it "
            "out and retry."
        ) from exc

    # The machine bindings soft-delete alongside the project. Local mode:
    # they ride into the trash bundle so a restore brings them back. Shared
    # mode: the bundle lives in the SHARED tree, and machine-absolute
    # bindings must never land there (P4/git would pick them up) - they
    # soft-delete into the local _user/_trash/ instead, which also keeps
    # the move same-volume (a cross-volume os.rename would fail). Either
    # way _user/<slug>.json is cleared, so a future project reusing the
    # slug can never silently inherit the dead project's roots. Lazy
    # import: resolve imports THIS module, so a top-level import here
    # would be a cycle.
    from maya_tools.framework import project_config_resolve
    bindings_path = project_config_resolve.user_dir() / f"{slug}.json"
    if bindings_path.is_file():
        if root == project_config_paths.local_configs_dir():
            bindings_dest = dest / "user_bindings.json"
        else:
            local_trash = project_config_resolve.user_dir() / "_trash"
            bindings_dest = local_trash / f"{slug}_{stamp}.json"
            try:
                local_trash.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                _LOG.warning("Deleted '%s' but could not create the local "
                             "bindings trash: %s", slug, exc)
                bindings_dest = None
        if bindings_dest is not None:
            try:
                os.rename(bindings_path, bindings_dest)
            except OSError as exc:
                _LOG.warning("Deleted '%s' but could not move its bindings: %s",
                             slug, exc)
    return dest


def list_templates():
    """[{'id','label'}] for every packaged engine-template dir ('_'-prefixed,
    e.g. _unreal5) under project_config_paths.packaged_templates_dir() -
    the in-tree template source, NEVER user projects (configs_dir()). A
    dir with a missing/unparsable session.json is skipped rather than
    raising, same policy as list_projects()."""
    root = project_config_paths.packaged_templates_dir()
    if not root.is_dir():
        return []
    out = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir() or not entry.name.startswith("_"):
            continue
        if entry.name.startswith("__"):   # __pycache__ and friends: never templates
            continue
        template_id = entry.name[1:]
        if not template_id:
            continue
        session_path = entry / _SESSION_FILE
        if not session_path.is_file():
            continue
        try:
            session = json.loads(session_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        label = session.get("engine") or template_id.replace("_", " ").title()
        out.append({"id": template_id, "label": label})
    return out


def load_template(template_id):
    """Fresh authored-union skeleton from the packaged template
    _<template_id>/ (project_config_paths.packaged_templates_dir(), NEVER
    a user project): every field the template carries, EXCEPT name and
    project_root, which always come back blank - a template is a starting
    point, not a saved project, so create_project() can never accidentally
    inherit the template's own identity fields."""
    target = project_config_paths.packaged_templates_dir() / f"_{template_id}"
    session_path = target / _SESSION_FILE
    export_path = target / _EXPORT_MAPPING_FILE
    if not session_path.is_file() or not export_path.is_file():
        raise FileNotFoundError(f"Template '{template_id}' not found at {target}")

    session = json.loads(session_path.read_text(encoding="utf-8"))
    export_mapping = json.loads(export_path.read_text(encoding="utf-8"))

    if export_mapping.get("schema_version", 1) < 2:
        authored = _upgrade_v1(session, export_mapping)
    else:
        authored = {field: session.get(field) for field in SESSION_FIELDS}
        for key, default in _EXPORT_AUTHORED_KEYS:
            authored[key] = export_mapping.get(key, default)

    # Blank identity last, after the merge above, so nothing downstream can
    # accidentally re-populate it from the template. (v2: machine roots are
    # user-tier bindings and never appear in a template.)
    authored["name"] = ""
    return authored
