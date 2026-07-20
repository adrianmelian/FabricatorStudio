# maya_tools/framework/scene_loader.py
"""Shelf-button helpers for opening scenes.

- open_scene_in_current_dir(): file-open dialog starting in the current
  scene's directory (or Maya's default if no current scene).
- open_source_rig_sibling(): jumps between the same-named scene in
  sibling Rig/ and Source/ folders (typical project layout for KS work).
"""
__author__ = "Adrian Melian"

from pathlib import Path

import maya.cmds as cmds


def open_scene_in_current_dir() -> None:
    """Open a file-picker rooted at the current scene's directory.

    Falls back to Maya's default Open Scene (no starting dir) when the
    current scene was never saved. Prompts the user about unsaved
    changes before clobbering.
    """
    current = cmds.file(q=True, sceneName=True) or ''
    start_dir = str(Path(current).parent) if current else ''

    if not start_dir:
        cmds.OpenScene()
        return

    paths = cmds.fileDialog2(
        fileMode=1,  # existing file, single selection
        startingDirectory=start_dir,
        fileFilter=(
            'Maya Files (*.ma *.mb);;'
            'Maya ASCII (*.ma);;'
            'Maya Binary (*.mb);;'
            'All Files (*.*)'
        ),
        caption='Load Scene',
        okCaption='Open',
    )
    if not paths:
        return
    _open_with_unsaved_prompt(paths[0])


def open_source_rig_sibling() -> None:
    """Open the same-name scene in the sibling Rig/ or Source/ folder.

    Looks for a 'Rig' or 'Source' segment in the current scene's path
    (case-insensitive) and swaps it for the other. cmds.warning on
    failure — no scene change attempted.
    """
    current = cmds.file(q=True, sceneName=True) or ''
    if not current:
        cmds.warning('Source <-> Rig: current scene is not saved to disk.')
        return

    parts = Path(current).parts
    idx, swap_to = _find_rig_or_source(parts)
    if idx is None:
        cmds.warning(
            "Source <-> Rig: current scene is not under a 'Rig' or 'Source' folder."
        )
        return

    sibling_parts = list(parts)
    sibling_parts[idx] = swap_to
    sibling_path = Path(*sibling_parts)

    if not sibling_path.exists():
        cmds.warning(f'Source <-> Rig: sibling scene not found at: {sibling_path}')
        return

    _open_with_unsaved_prompt(str(sibling_path))


# ─── private ─────────────────────────────────────────────────────────────────

def _find_rig_or_source(parts: tuple) -> tuple:
    """Walk path parts from the right (most-specific to root) looking for
    a 'Rig' or 'Source' segment, case-insensitive. Returns (idx, swap_to)
    or (None, None) if neither found.
    """
    for i in range(len(parts) - 1, -1, -1):
        name_lower = parts[i].lower()
        if name_lower == 'rig':
            return i, 'Source'
        if name_lower == 'source':
            return i, 'Rig'
    return None, None


def _open_with_unsaved_prompt(target_path: str) -> None:
    """Open target_path. If the current scene has unsaved changes, prompt
    the user first. No-op if the user cancels.
    """
    if cmds.file(q=True, modified=True):
        result = cmds.confirmDialog(
            title='Unsaved Changes',
            message='Current scene has unsaved changes. Open anyway?',
            button=['Open Anyway', 'Cancel'],
            defaultButton='Cancel',
            cancelButton='Cancel',
        )
        if result != 'Open Anyway':
            return
    cmds.file(target_path, open=True, force=True)
