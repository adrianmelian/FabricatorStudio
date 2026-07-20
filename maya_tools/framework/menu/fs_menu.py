# fs_menu.py
# FabricatorStudio Maya menu — reads fs_menu.json and builds the main menu
# bar entry. Call build_menu() from userSetup.py via evalDeferred.
__author__ = "Adrian Melian"

import json
import os
import traceback

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om

_JSON_PATH = os.path.join(os.path.dirname(__file__), 'fs_menu.json')
_MENU_NAME = 'FSToolsMenu'


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _make_runner(cmd: str):
    """Return a callback that executes cmd as Python when a menu item is clicked."""
    def _run(*_):
        try:
            exec(cmd, {})  # noqa: S102
        except Exception as exc:
            traceback.print_exc()
            om.MGlobal.displayError(f'[FS] {exc}')
    return _run


def _build_items(items: list[dict], parent: str) -> None:
    """Recursively create menu items under parent."""
    for item in items:
        kind = item.get('type', 'item')

        if kind == 'divider':
            cmds.menuItem(divider=True, parent=parent)

        elif kind == 'submenu':
            sub = cmds.menuItem(
                label=item['label'],
                subMenu=True,
                tearOff=False,
                parent=parent,
            )
            _build_items(item.get('items', []), sub)
            cmds.setParent('..', menu=True)

        else:  # 'item'
            cmd = item.get('command', '')
            cmds.menuItem(
                label=item['label'],
                command=_make_runner(cmd),
                parent=parent,
            )


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def build_menu() -> None:
    """Read fs_menu.json and build (or rebuild) the FabricatorStudio menu in Maya's main menu bar.

    Safe to call multiple times — removes the existing menu before rebuilding.
    Gated by the Settings > MAYA INTEGRATION 'load_menu' pref (2026-07-18);
    the gate sits before any Maya call so it is headless-testable.
    """
    from maya_tools.framework.toolbar import toolbar_prefs
    if not toolbar_prefs.load_prefs().get('load_menu', True):
        om.MGlobal.displayInfo('[FS] Menu load disabled in Settings.')
        return
    # Remove stale menu if it exists
    if cmds.menu(_MENU_NAME, exists=True):
        cmds.deleteUI(_MENU_NAME, menu=True)

    with open(_JSON_PATH, 'r') as fh:
        data = json.load(fh)

    main_window = mel.eval('$_tmp = $gMainWindow')
    menu = cmds.menu(
        _MENU_NAME,
        label=data['label'],
        parent=main_window,
        tearOff=False,
    )

    _build_items(data.get('items', []), menu)
    om.MGlobal.displayInfo('[FS] Menu built.')


def rebuild_menu() -> None:
    """Convenience alias — reload JSON and rebuild. Useful during development."""
    build_menu()
