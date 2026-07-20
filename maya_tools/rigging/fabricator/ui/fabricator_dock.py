# maya_tools/rigging/fabricator/ui/fabricator_dock.py
"""Docked Fabricator — the second face of the two-window design
(Adrian, 2026-07-05/06: the floating frameless window is the flagship
and never docks; this workspaceControl hosts the same FSWindow for
users who want Fabricator living in a Maya pane, where a docked
control shows only a slim tab — the Mindmeld look survives).

Entry points: right-click Fabricator's brand row -> "Dock Fabricator"
(and "Undock Fabricator" from the docked face). Mutual exclusion is
law: opening either face closes the other — two live FSWindows would
fight over the Armature watchers and the LiveBackend.

Lifecycle notes:
  * retain=False — closing the dock deletes the control (fresh state
    next open), but a control left docked in the workspace layout is
    restored by Maya at startup via uiScript, so Fabricator comes
    back docked where you left it.
  * Closing the dock via its tab X destroys children WITHOUT
    closeEvent (deleteLater path) — FSWindow(docked=True) wires the
    Maya-side teardown to its destroyed signal for that route.
    close_dock() closes the child first, so the full closeEvent
    teardown runs while the widget is alive.
  * Build/unbuild reconstruct the UI: FSWindow._force_reopen calls
    rebuild_if_open() when no top-level instance exists (a docked
    FSWindow is a child, invisible to the top-level finder).
  * Rebrand cleanup (2026-07-11, Tools Engineer S3): the old
    'KSFabDock' workspaceControl name is retired to 'FSFabDock'. A
    stale KSFabDock control can be sitting in a user's saved workspace
    layout from before the rename — open_docked() deletes it once on
    open before creating the new-named control.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds
import maya.OpenMayaUI as omui

from PySide6 import QtCore, QtWidgets
from shiboken6 import wrapInstance

DOCK_NAME = 'FSFabDock'
DOCK_LABEL = 'Fabricator'
_OLD_DOCK_NAME = 'KSFabDock'   # pre-rebrand name — remove after v1

_UI_SCRIPT = ('from maya_tools.rigging.fabricator.ui import '
              'fabricator_dock; fabricator_dock._populate()')

_DOCK_WIN = None      # the live docked FSWindow (or a dead wrapper)


def is_open() -> bool:
    return bool(cmds.workspaceControl(DOCK_NAME, q=True, exists=True))


def open_docked() -> None:
    """Open (or restore) the docked Fabricator. Closes any floating
    instance first — one Fabricator at a time."""
    from maya_tools.rigging.fabricator.ui.fs_window import FSWindow
    for w in QtWidgets.QApplication.topLevelWidgets():
        try:
            if w.objectName() == FSWindow.WINDOW_NAME:
                w.hide()
                w.close()
                w.deleteLater()
        except Exception:
            pass
    # One-time cleanup: a workspace layout saved before the rebrand may
    # still carry the old-named control. Drop it so it doesn't linger
    # alongside the new one.
    try:
        if cmds.workspaceControl(_OLD_DOCK_NAME, q=True, exists=True):
            cmds.deleteUI(_OLD_DOCK_NAME)
    except Exception:
        pass
    if is_open():
        cmds.workspaceControl(DOCK_NAME, e=True, restore=True)
        return
    # Spawn DOCKED rather than floating: interactive drag-docking is
    # suppressed when the workspace is locked (2026-07-06: "did not
    # dock, nor become dockable"). And dock by TABBING into the
    # Attribute Editor — a control every session has, always visible —
    # not by edge-docking: dockToMainWindow('right') materialized a
    # brand-new right dock area COLLAPSED on Adrian's layout ("it just
    # vanishes"). A tab on a live control cannot collapse; restore()
    # then raises our tab to the front. Drag the tab anywhere after.
    kwargs = dict(label=DOCK_LABEL, retain=False, uiScript=_UI_SCRIPT,
                  initialWidth=1100, initialHeight=720)
    if cmds.workspaceControl('AttributeEditor', q=True, exists=True):
        kwargs['tabToControl'] = ('AttributeEditor', -1)
    else:
        kwargs['dockToMainWindow'] = ('right', 1)
    cmds.workspaceControl(DOCK_NAME, **kwargs)
    cmds.workspaceControl(DOCK_NAME, e=True, restore=True)


def close_dock() -> None:
    """Tear the dock down cleanly: child first (full closeEvent
    teardown while alive), then the control."""
    global _DOCK_WIN
    if not is_open():
        _DOCK_WIN = None
        return
    try:
        if _DOCK_WIN is not None:
            _DOCK_WIN.close()
    except Exception:
        pass
    _DOCK_WIN = None
    try:
        cmds.deleteUI(DOCK_NAME)
    except Exception:
        pass


def rebuild_if_open() -> bool:
    """Replace the dock's FSWindow with a fresh one — the docked
    equivalent of _force_reopen's close-and-reconstruct. Returns True
    when a docked instance was rebuilt."""
    if not is_open():
        return False
    _populate(rebuild=True)
    return True


def _control_widget():
    ptr = omui.MQtUtil.findControl(DOCK_NAME)
    if not ptr:
        return None
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def _populate(rebuild: bool = False) -> None:
    """Build the FSWindow child inside the control. Called by the
    workspaceControl's uiScript (creation + workspace restore at Maya
    startup) and by rebuild_if_open()."""
    global _DOCK_WIN
    from maya_tools.rigging.fabricator.ui.fs_window import FSWindow
    host = _control_widget()
    if host is None:
        return
    lay = host.layout()
    if lay is None:
        lay = QtWidgets.QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
    if rebuild:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.close()          # closeEvent teardown while alive
                w.setParent(None)
                w.deleteLater()
    win = FSWindow(docked=True)
    win.setWindowFlags(QtCore.Qt.WindowType.Widget)
    lay.addWidget(win)
    _DOCK_WIN = win
