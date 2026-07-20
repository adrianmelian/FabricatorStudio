"""AM Renamer — app layer for find/replace, prefix/suffix, and renumber operations."""
__author__ = "Adrian Melian"

import re

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.framework.decorators import undo_chunk


def _short_name(path: str) -> str:
    return path.split('|')[-1]


# ── Find / Replace ────────────────────────────────────────────────────────────

def find_replace(objects: list[str], find: str, replace: str,
                 case_sensitive: bool = True) -> list[str]:
    """Rename *objects* by substituting *find* with *replace* in each short name."""
    if not find:
        raise ValueError("Find string is empty.")

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(find), flags)

    changed = 0
    new_names: list[str] = []
    with undo_chunk('find_replace'):
        for obj in objects:
            short = _short_name(obj)
            new_short = pattern.sub(replace, short)
            if new_short != short:
                result = cmds.rename(obj, new_short)
                new_names.append(result)
                changed += 1
            else:
                new_names.append(short)

    om.MGlobal.displayInfo(f'[Renamer] find/replace: {changed} of {len(objects)} renamed.')
    return new_names


# ── Prefix / Suffix ───────────────────────────────────────────────────────────

def add_prefix(objects: list[str], prefix: str) -> list[str]:
    if not prefix:
        raise ValueError("Prefix is empty.")
    new_names: list[str] = []
    with undo_chunk('add_prefix'):
        for obj in objects:
            result = cmds.rename(obj, f'{prefix}{_short_name(obj)}')
            new_names.append(result)
    om.MGlobal.displayInfo(f'[Renamer] Added prefix to {len(new_names)} object(s).')
    return new_names


def remove_prefix(objects: list[str], prefix: str) -> list[str]:
    if not prefix:
        raise ValueError("Prefix is empty.")
    new_names: list[str] = []
    skipped = 0
    with undo_chunk('remove_prefix'):
        for obj in objects:
            short = _short_name(obj)
            if not short.startswith(prefix):
                cmds.warning(f'[Renamer] "{short}" does not start with "{prefix}" — skipped.')
                new_names.append(short)
                skipped += 1
                continue
            stripped = short[len(prefix):]
            if not stripped:
                cmds.warning(f'[Renamer] "{short}" would be empty after stripping — skipped.')
                new_names.append(short)
                skipped += 1
                continue
            result = cmds.rename(obj, stripped)
            new_names.append(result)

    if skipped:
        om.MGlobal.displayWarning(f'[Renamer] {skipped} object(s) skipped (prefix not matched).')
    else:
        om.MGlobal.displayInfo(f'[Renamer] Removed prefix from {len(new_names)} object(s).')
    return new_names


def add_suffix(objects: list[str], suffix: str) -> list[str]:
    if not suffix:
        raise ValueError("Suffix is empty.")
    new_names: list[str] = []
    with undo_chunk('add_suffix'):
        for obj in objects:
            result = cmds.rename(obj, f'{_short_name(obj)}{suffix}')
            new_names.append(result)
    om.MGlobal.displayInfo(f'[Renamer] Added suffix to {len(new_names)} object(s).')
    return new_names


def remove_suffix(objects: list[str], suffix: str) -> list[str]:
    if not suffix:
        raise ValueError("Suffix is empty.")
    new_names: list[str] = []
    skipped = 0
    with undo_chunk('remove_suffix'):
        for obj in objects:
            short = _short_name(obj)
            if not short.endswith(suffix):
                cmds.warning(f'[Renamer] "{short}" does not end with "{suffix}" — skipped.')
                new_names.append(short)
                skipped += 1
                continue
            stripped = short[:-len(suffix)]
            if not stripped:
                cmds.warning(f'[Renamer] "{short}" would be empty after stripping — skipped.')
                new_names.append(short)
                skipped += 1
                continue
            result = cmds.rename(obj, stripped)
            new_names.append(result)

    if skipped:
        om.MGlobal.displayWarning(f'[Renamer] {skipped} object(s) skipped (suffix not matched).')
    else:
        om.MGlobal.displayInfo(f'[Renamer] Removed suffix from {len(new_names)} object(s).')
    return new_names


# ── Renumber ──────────────────────────────────────────────────────────────────

def renumber(objects: list[str], separator: str = '_', padding: int = 2,
             start: int = 1) -> list[str]:
    """Strip trailing <sep><digits> from each name and reapply sequential numbering."""
    if not objects:
        raise ValueError("Nothing to renumber — list is empty.")

    sep_esc = re.escape(separator)
    strip_pat = re.compile(rf'^(.*?){sep_esc}\d+$')

    new_names: list[str] = []
    with undo_chunk('renumber'):
        for i, obj in enumerate(objects):
            short = _short_name(obj)
            m = strip_pat.match(short)
            base = m.group(1) if m else short
            desired = f'{base}{separator}{str(start + i).zfill(padding)}'
            result = cmds.rename(obj, desired)
            new_names.append(result)

    om.MGlobal.displayInfo(f'[Renamer] Renumbered {len(new_names)} object(s).')
    return new_names


# ── Selection convenience wrappers ────────────────────────────────────────────

def _require_selection() -> list[str]:
    sel = cmds.ls(sl=True, long=True)
    if not sel:
        raise RuntimeError("Nothing selected.")
    return sel


def find_replace_selection(find: str, replace: str, case_sensitive: bool = True) -> list[str]:
    return find_replace(_require_selection(), find, replace, case_sensitive)


def prefix_suffix_selection(mode: str, side: str, text: str) -> list[str]:
    """mode: 'add'|'remove'   side: 'prefix'|'suffix'"""
    sel = _require_selection()
    dispatch = {
        ('add',    'prefix'): add_prefix,
        ('add',    'suffix'): add_suffix,
        ('remove', 'prefix'): remove_prefix,
        ('remove', 'suffix'): remove_suffix,
    }
    fn = dispatch.get((mode, side))
    if fn is None:
        raise ValueError(f"Invalid mode/side: {mode!r}/{side!r}")
    return fn(sel, text)


def renumber_selection(separator: str = '_', padding: int = 2, start: int = 1) -> list[str]:
    return renumber(_require_selection(), separator, padding, start)
