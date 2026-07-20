"""Hash Renamer — rename selected objects using # as a digit placeholder.

    joint_###  →  joint_001, joint_002, joint_003 …

Each consecutive # adds one digit of zero-padding.  start_index controls
the first number in the sequence (default 1).
"""
__author__ = "Adrian Melian"

import re

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.framework.decorators import undo_chunk


# ──────────────────────────────────────────────────────────────────────────────

def validate_pattern(pattern: str) -> None:
    """Raise ValueError if pattern contains no '#' characters."""
    if not pattern:
        raise ValueError("Name pattern is empty.")
    if '#' not in pattern:
        raise ValueError("Pattern must contain at least one '#' character.")


def hash_rename(objects: list[str], pattern: str, start_index: int = 1) -> list[str]:
    """Rename *objects* using *pattern*, replacing the '#' run with a zero-padded index.

    Returns the list of final names as Maya resolved them (may differ from
    the pattern if there were naming conflicts).

    Raises:
        ValueError: pattern is invalid or objects list is empty.
    """
    validate_pattern(pattern)
    if not objects:
        raise ValueError("Nothing to rename — list is empty.")

    match = re.search(r'#+', pattern)
    pad_width = len(match.group())
    prefix = pattern[:match.start()]
    suffix = pattern[match.end():]

    # Numbers follow selection order, but renames execute deepest-first so
    # renaming an ancestor never invalidates a stored descendant path
    # (long paths go stale once a parent is renamed).
    jobs: list[tuple[int, str, str]] = []
    for i, obj in enumerate(objects):
        num_str = str(start_index + i).zfill(pad_width)
        jobs.append((i, obj, f'{prefix}{num_str}{suffix}'))

    new_names: list[str] = [''] * len(objects)
    with undo_chunk('hash_rename'):
        for i, obj, desired in sorted(jobs, key=lambda j: j[1].count('|'), reverse=True):
            new_names[i] = cmds.rename(obj, desired)

    om.MGlobal.displayInfo(f'[HashRenamer] Renamed {len(new_names)} object(s).')
    return new_names


def rename_selection(pattern: str, start_index: int = 1) -> list[str]:
    """Convenience wrapper — renames the current Maya selection."""
    sel = cmds.ls(sl=True, long=True)
    if not sel:
        raise RuntimeError("Nothing selected.")
    return hash_rename(sel, pattern, start_index)
