"""User-side project-config location. Pure Python on purpose: no cmds, no
Qt — importable in bare mayapy (or any Python), testable without a Maya
session.

Project configs (session.json + export_mapping.json per project) live
OUTSIDE the repo/install tree so branch checkouts and the uninstaller can
never destroy user-authored configs:

    <maya_root>/fabricator_project_configs/<name>/

<maya_root> resolution (single source — toolbar_prefs delegates here):
the MAYA_APP_DIR env var if set, else <Documents>/maya where Documents
is the user's REAL, redirect-aware Documents folder — the un-versioned
shared maya folder, so one location serves every Maya version and
matches the installer layout. Windows lets Documents live off
%USERPROFILE% (e.g. D:\\Documents); Maya resolves that redirect when it
sets MAYA_APP_DIR, so headless runs must ask the shell for the known
folder too or they read/write a stale ~/Documents copy (bug found
2026-07-04 — blueprints_dir written to the wrong session.json).

The in-tree framework/project_configs/ dir remains ONLY as the packaged
template source: '_'-prefixed dirs (e.g. _unreal5) are starter templates,
seeded to the user dir under the un-prefixed name on first run (see
ensure_user_configs()).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path

CONFIGS_DIRNAME = "fabricator_project_configs"

_log = logging.getLogger(__name__)


def _documents_dir() -> Path:
    """The user's real Documents folder, redirect-aware on Windows.

    CSIDL_PERSONAL (5) follows a redirected Documents (e.g.
    D:\\Documents) the same way Maya does when it sets MAYA_APP_DIR.
    Falls back to ~/Documents when the shell query is unavailable
    (non-Windows, restricted environment)."""
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            res = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None,
                                                         0, buf)
            if res == 0 and buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"


def maya_root() -> Path:
    """Shared un-versioned maya folder: MAYA_APP_DIR when set (Maya
    sets it in-session), else the redirect-aware <Documents>/maya."""
    base = os.environ.get("MAYA_APP_DIR")
    if base:
        return Path(base)
    return _documents_dir() / "maya"


USER_DIRNAME = "_user"
SETTINGS_FILE = "settings.json"


def local_configs_dir() -> Path:
    """The per-machine configs dir. NEVER redirected by the shared-configs
    pointer or FABRICATOR_PROJECT_CONFIGS: user bindings (_user/<slug>.json)
    and the pointer itself (_user/settings.json) live here even when
    project configs come from a shared root. See the v2 spec sec 4."""
    return maya_root() / CONFIGS_DIRNAME


def pointer_configs_root() -> Path | None:
    """The in-tool persisted shared-configs pointer, or None when unset,
    blank, or unreadable. Public: the Settings tool reads it to report
    which source won (env > pointer > local). Corrupt settings must fall
    back local, never raise into a consumer - that includes VALID JSON
    that is not an object (the natural hand-edit for a one-value file)
    and a non-string value, so everything through Path() stays inside
    the net."""
    settings = local_configs_dir() / USER_DIRNAME / SETTINGS_FILE
    try:
        parsed = json.loads(settings.read_text(encoding="utf-8"))
        value = parsed.get("configs_root", "") if isinstance(parsed, dict) else ""
        if not isinstance(value, str) or not value:
            return None
        return Path(value)
    except (OSError, ValueError):
        return None


def configs_dir() -> Path:
    """Where PROJECT configs (the session/export_mapping pairs) live.
    Resolution order: FABRICATOR_PROJECT_CONFIGS env var (automation
    override for launchers / Maya.env / login scripts) -> the in-tool
    persisted pointer (_user/settings.json, set per user per machine) ->
    the local default. Every consumer funnels through here (maya_startup,
    export_core, blueprint_library, project_config_io).

    Per-user state (active_project, window/dock/scale/layout prefs, the
    _user/ bindings dir) lives off maya_root()/local_configs_dir() and is
    deliberately NOT affected by this resolution — it must stay local even
    when the configs root is shared.

    May not exist yet — callers that need it on disk go through
    ensure_user_configs().
    """
    override = os.environ.get("FABRICATOR_PROJECT_CONFIGS")
    if override:
        return Path(override)
    return pointer_configs_root() or local_configs_dir()


def _is_writable_dir(path: Path) -> bool:
    """Best-effort probe: True if a file can be created inside path right
    now. Gates auto-seed/migrate to writable configs roots (spec §4.4) — a
    shared/read-only root (network share, unmounted Perforce workspace)
    must never hit a permission error or get churned on every artist's
    first launch; the TA publishes its templates there once. The whole
    probe (create, close, remove) is wrapped in one try/except: a mid-probe
    failure (e.g. the temp file is created but a network hiccup makes the
    close/unlink fail) must still read as "not writable," not crash the
    caller."""
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".ks_writecheck_", dir=str(path))
        os.close(fd)
        os.unlink(tmp_path)
        return True
    except OSError:
        return False


def packaged_templates_dir() -> Path:
    """The in-tree / in-package template source (framework/project_configs)."""
    return Path(__file__).resolve().parent / "project_configs"


def _clear_readonly(root: Path) -> None:
    """Clear the read-only file-mode bit on every file under root
    (recursively). shutil.copytree's copy2 preserves source mode bits; a
    legacy config sourced from a Perforce workspace can ship read-only, and
    the local seeded copy must always be freely editable."""
    for p in root.rglob("*"):
        if p.is_file():
            mode = p.stat().st_mode
            p.chmod(mode | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)


def _copy_config_atomic(src: Path, dest: Path, user_dir: Path) -> None:
    """All-or-nothing copy of one config dir: stage into a '_'-prefixed
    temp dir inside user_dir (the enumeration helpers skip '_' dirs, so
    even a leftover from a hard crash stays invisible), then publish with
    a single os.rename — dest appears fully formed or not at all.

    A partway copy failure (AV file lock, disk full, unreadable source)
    never leaves a half-seeded dest: the staging dir is removed and the
    error re-raised, so the next run retries cleanly. If a concurrent
    session created dest first (the rename fails because dest now
    exists), that copy is authoritative and this staged one is discarded
    — the never-overwrite contract holds under races too.
    """
    staging = tempfile.mkdtemp(prefix=f"_stage-{dest.name}-", dir=user_dir)
    staged = Path(staging) / dest.name
    try:
        shutil.copytree(src, staged)   # copy ONLY — the source is never deleted/moved
        _clear_readonly(staged)        # local copy must always be freely editable (spec §4.4)
        os.rename(staged, dest)        # atomic publish
    except OSError:
        if dest.exists():              # a concurrent run won the race; its copy wins
            return
        raise
    finally:
        try:
            shutil.rmtree(staging)
        except OSError:
            _log.warning(
                "Could not remove staging dir %s after seeding %s into %s; "
                "a leftover '_stage-*' folder may remain and should be "
                "removed manually.", staging, dest.name, user_dir, exc_info=True)


def ensure_user_configs(packaged_dir: Path | None = None) -> Path:
    """Create configs_dir if missing, seed packaged templates, and migrate
    legacy in-tree configs.

    SEED: each '_'-prefixed template dir in the packaged dir (e.g.
    _unreal5) is copied to configs_dir under the un-prefixed name IF that
    name is absent there.

    MIGRATE: each legacy NON-underscore config dir still sitting in the
    packaged dir (e.g. archangel on a dev machine) is copied to
    configs_dir IF absent there. COPY ONLY — the source is never deleted
    or moved.

    PRECEDENCE: when the packaged dir holds BOTH a template and its legacy
    live config (_unreal5 AND unreal5, the installer-upgrade case), the
    legacy user-authored content wins: migration runs before seeding.

    Existing user configs are never overwritten by either step. Each copy
    is all-or-nothing (staged then renamed into place, see
    _copy_config_atomic), so a partway failure or a concurrent session
    can never leave a half-seeded config behind.

    Idempotent; cheap when everything already exists. Returns configs_dir.
    `packaged_dir` overrides the template source for tests only.
    """
    user_dir = configs_dir()
    user_dir.mkdir(parents=True, exist_ok=True)

    if not _is_writable_dir(user_dir):
        return user_dir   # shared/read-only root: skip auto-seed/migrate (spec §4.4)

    packaged = Path(packaged_dir) if packaged_dir is not None else packaged_templates_dir()
    if not packaged.is_dir():
        return user_dir

    # Order constraint: a legacy live config (e.g. unreal5) outranks its
    # template (_unreal5) for the same dest name — the installer's
    # preserve/restore step can leave BOTH in the packaged dir, and the
    # user-authored legacy content must win. Plain ASCII sort would put
    # '_unreal5' first (0x5F < letters) and seed the pristine template,
    # then skip the legacy dir at the dest.exists() guard. So sort legacy
    # dirs (migrate) before '_'-prefixed templates (seed).
    ordered = sorted(
        (p for p in packaged.iterdir() if p.is_dir()),
        key=lambda p: (p.name.startswith("_"), p.name.lower()),
    )
    for src in ordered:
        name = src.name
        if name.startswith("__"):      # __pycache__ and friends: never configs
            continue
        dest_name = name[1:] if name.startswith("_") else name
        if not dest_name:
            continue
        dest = user_dir / dest_name
        if dest.exists():              # user data always wins; never overwrite
            continue
        _copy_config_atomic(src, dest, user_dir)

    return user_dir
