"""FS Status Line button — the FabricatorStudio mark on Maya's native Status
Line (the top bar with the Modeling/Rigging/... menu-set dropdown), Adrian
2026-07-10. One click toggles the FS toolbar open/closed via
toolbar_app.toggle(); the long game is that this becomes THE entry point into
the toolset once shelves/menus retire in the final polish pass.

Same native-UI idiom as fs_shelf's $gShelfTopLevel grab: resolve the
$gStatusLine MEL global and parent a cmds.iconTextButton to it (it lands at
the right end of the Status Line, after Maya's own controls). Fixed control
name -> idempotent delete-first reinstall, the _build_shelf pattern.

Install from maya_startup.run() (evalDeferred at startup, so the Status Line
exists). The Status Line is built once per Maya session and survives scene
new/open; only a full "Restore Default UI" wipes the button, and the next
session's startup reinstalls it. Batch/headless Maya has no Status Line, so
install() is a guarded no-op there.

The click command is an IMPORT STRING, not a closure: it survives module
reloads and teardown, and keeps this module free of stale-pointer risk on a
session-lived native control.
"""
from __future__ import annotations

import os

import maya.cmds as cmds
import maya.mel as mel

BUTTON_NAME = 'fsToolbarStatusButton'   # fixed -> exists()/deleteUI idempotency

_ICON = 'fs_logo.png'
_COMMAND = ('from maya_tools.framework.toolbar import toolbar_app; '
            'toolbar_app.toggle()')


def _icon_path() -> str:
    """icons/fs_logo.png via the toolbar's own icon dir (the
    icon_button._ICON_DIR convention popovers.py already leans on)."""
    from maya_tools.framework.toolbar.widgets import icon_button
    return str(icon_button._ICON_DIR / _ICON)


def install() -> None:
    """Put the FS mark on the Status Line (delete-first, so re-running is a
    clean reinstall, never a duplicate). Silent no-op in batch; a missing
    icon or Status Line degrades to a warning, never a startup error."""
    if cmds.about(batch=True):
        return
    try:
        status_line = mel.eval('$_tmp = $gStatusLine')
        if not status_line:
            return
        remove()
        icon = _icon_path()
        if not os.path.isfile(icon):
            cmds.warning(f'[FS toolbar] Status Line icon missing: {icon}')
            return
        cmds.iconTextButton(
            BUTTON_NAME,
            parent=status_line,
            style='iconOnly',
            image1=icon,
            width=26, height=26,
            annotation='FabricatorStudio: show / hide the FS toolbar',
            command=_COMMAND,
        )
    except Exception as exc:
        # Startup must never die on a cosmetic button (matches
        # maya_startup's marking-menu guard posture).
        cmds.warning(f'[FS toolbar] Could not install Status Line button: {exc}')


def remove() -> None:
    """Take the button off the Status Line (uninstaller / reinstall path)."""
    try:
        if cmds.control(BUTTON_NAME, exists=True):
            cmds.deleteUI(BUTTON_NAME)
    except Exception:
        pass
