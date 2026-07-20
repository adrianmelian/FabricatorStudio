# maya_tools/rigging/fabricator/blueprint_library.py
"""Blueprint library — the palette's two content sources (2026-07-04).

FACTORY: maya_tools/rigging/fabricator/templates/ — the blueprint
templates and limb fragments that ship with Fabricator. Read-only:
never renamed or deleted in place; duplicating one drops the copy into
the project library.

PROJECT: the active project's blueprints folder — user-made templates
and limbs. Resolution chain:
  1. active project name: toolbar prefs 'active_project' (the RigBot
     ProjectChooser — this module is its first consumer), falling back
     to the session-startup config (maya_startup.get_active_config)
     when the chooser has no pick yet;
  2. <configs>/<name>/session.json['blueprints_dir'] — an absolute
     path. Archangel's points into the source-art depot so user
     blueprints ride the project's VCS (P4-friendly: a delete is
     revertable from the depot).

Pure Python except the lazy maya_startup fallback (which needs cmds):
scans and file ops are headless-testable.
"""
__author__ = "Adrian Melian"

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from maya_tools.framework import project_config_paths
from maya_tools.framework.toolbar import toolbar_prefs

FACTORY = 'factory'
PROJECT = 'project'

TEMPLATE_SUFFIX = '.blueprint.yaml'
LIMB_SUFFIX = '.limb.yaml'

_FACTORY_DIR = Path(__file__).resolve().parent / 'templates'
_BLUEPRINTS_KEY = 'blueprints_dir'


class LibraryError(RuntimeError):
    """User-actionable library failure — message is shown verbatim."""


@dataclass
class Entry:
    name: str      # file stem without the double suffix
    path: Path     # absolute path to the .blueprint.yaml / .limb.yaml
    source: str    # FACTORY | PROJECT


# ─────────────────────────────────────────────
# Locations
# ─────────────────────────────────────────────

def factory_dir() -> Path:
    return _FACTORY_DIR


def active_project_name() -> str:
    """Toolbar-chooser pick first; session-startup config as fallback."""
    name = (toolbar_prefs.load_prefs().get('active_project') or '').strip()
    if name:
        return name
    try:
        from maya_tools.framework import maya_startup
        return maya_startup.get_active_config() or ''
    except Exception:
        return ''


def project_dir(create: bool = False) -> Path | None:
    """The active project's blueprints folder, or None when unset.

    Resolves through project_config_resolve (v2): the RESOLVED
    'blueprints_dir' is the per-machine override when set, else
    source_art_root + the project's blueprints_subpath; v1 configs keep
    working through the resolver's derive path. None when: no active
    project, the project is missing/corrupt/unbound on this machine, or
    it authors no blueprints location (factory lane only) - degrade,
    never raise. create=True mkdirs the folder (parents included) so the
    first save just works.
    """
    name = active_project_name()
    if not name:
        return None
    from maya_tools.framework import project_config_resolve
    try:
        resolved = project_config_resolve.resolve_project(name)
    except (project_config_resolve.UnboundProjectError, FileNotFoundError,
            OSError, ValueError):
        # Data problems (unbound machine, missing/corrupt config, bad
        # asset-class placeholders) degrade to the factory lane;
        # programming errors are NOT swallowed.
        return None
    raw = (resolved.get(_BLUEPRINTS_KEY) or '').strip()
    if not raw:
        return None
    folder = Path(raw)
    if create:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LibraryError(
                f'Could not create the project blueprints folder '
                f'"{folder}" — check it exists and is writable (Perforce: '
                f'sync and check it out) and retry. ({exc})') from exc
    return folder


def is_factory(path) -> bool:
    return Path(path).resolve().parent == _FACTORY_DIR.resolve()


# ─────────────────────────────────────────────
# Names / suffixes
# ─────────────────────────────────────────────

def strip_suffix(name: str) -> str:
    """Drop a trailing blueprint/limb suffix the user typed by habit."""
    for suffix in (TEMPLATE_SUFFIX, LIMB_SUFFIX, '.yaml'):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def _suffix_of(path: Path) -> str:
    return (TEMPLATE_SUFFIX if path.name.endswith(TEMPLATE_SUFFIX)
            else LIMB_SUFFIX)


def _stem_of(path: Path) -> str:
    return path.name.removesuffix(_suffix_of(path))


# ─────────────────────────────────────────────
# Scans
# ─────────────────────────────────────────────

def _scan(pattern: str) -> list[Entry]:
    """Factory entries first (the shipped kit stays put at the top),
    then project entries; each block name-sorted."""
    out = []
    for folder, source in ((_FACTORY_DIR, FACTORY),
                           (project_dir(), PROJECT)):
        if not folder or not folder.is_dir():
            continue
        for p in sorted(folder.glob(pattern)):
            out.append(Entry(name=_stem_of(p), path=p, source=source))
    return out


def scan_templates() -> list[Entry]:
    return _scan(f'*{TEMPLATE_SUFFIX}')


def scan_limbs() -> list[Entry]:
    return _scan(f'*{LIMB_SUFFIX}')


# ─────────────────────────────────────────────
# File ops (caller confirms; errors are user-readable)
# ─────────────────────────────────────────────

def require_project_dir() -> Path:
    """project_dir(create=True) or a user-actionable LibraryError that
    names the resolved project and the exact file to fix."""
    dest = project_dir(create=True)
    if dest is not None:
        return dest
    name = active_project_name()
    if not name:
        raise LibraryError(
            'No active project. Pick one in the FabricatorStudio '
            'toolbar project chooser.')
    from maya_tools.framework import project_config_resolve
    try:
        project_config_resolve.resolve_project(name)
    except project_config_resolve.UnboundProjectError:
        raise LibraryError(
            f'Project "{name}" is not bound on this machine. Open '
            f'Settings (the toolbar gear) and set Source Art Root + '
            f'Content Root first.') from None
    except Exception:
        pass   # fall through to the no-blueprints message below
    raise LibraryError(
        f'Project "{name}" has no blueprints folder configured. Set the '
        f'Blueprints subpath in Mindmeld, or a per-machine override in '
        f'Settings.')


def duplicate(path, new_name: str) -> Path:
    """Copy a blueprint/limb into the project library under new_name.
    Works on factory AND project sources — this is the only way factory
    content enters the project library."""
    src = Path(path)
    if not src.is_file():
        raise LibraryError(f'Source no longer exists: {src.name}')
    new_name = strip_suffix((new_name or '').strip())
    if not new_name:
        raise LibraryError('Duplicate: empty name.')
    dest = require_project_dir() / f'{new_name}{_suffix_of(src)}'
    if dest.exists():
        raise LibraryError(
            f'"{dest.name}" already exists in the project library.')
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        raise LibraryError(
            f'Could not write "{dest.name}" — it may be under source '
            f'control and not checked out. Check it out in Perforce (or '
            f'clear its read-only flag) and retry. ({exc})') from exc
    return dest


def rename(path, new_name: str) -> Path:
    src = Path(path)
    if is_factory(src):
        raise LibraryError(
            'Factory blueprints are read-only — duplicate into the '
            'project library instead.')
    if not src.is_file():
        raise LibraryError(f'File no longer exists: {src.name}')
    new_name = strip_suffix((new_name or '').strip())
    if not new_name:
        raise LibraryError('Rename: empty name.')
    dest = src.with_name(f'{new_name}{_suffix_of(src)}')
    if dest == src:
        return src
    # dest.samefile guard keeps case-only renames legal on Windows.
    if dest.exists() and not dest.samefile(src):
        raise LibraryError(f'"{dest.name}" already exists.')
    try:
        src.rename(dest)
    except OSError as exc:
        raise LibraryError(
            f'Could not rename "{src.name}" — it may be under source '
            f'control and not checked out. Check it out in Perforce (or '
            f'clear its read-only flag) and retry. ({exc})') from exc
    return dest


def delete(path) -> None:
    """Permanent file delete — the CALLER shows the confirmation
    prompt. Factory refuses. (Project libraries living in a VCS depot
    keep their own safety net: the delete is revertable there.)"""
    src = Path(path)
    if is_factory(src):
        raise LibraryError('Factory blueprints are read-only — cannot '
                           'delete.')
    if src.is_file():
        try:
            src.unlink()
        except OSError as exc:
            raise LibraryError(
                f'Could not delete "{src.name}" — it may be under source '
                f'control and not checked out. Check it out in Perforce '
                f'(or clear its read-only flag) and retry. ({exc})') from exc
