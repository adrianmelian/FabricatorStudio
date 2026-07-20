"""Dock-target resolution + insertion for the FabricatorStudio toolbar.

Fallback chain (spec 3.1): requested home -> floating, with a warning on
every degrade. Modes: top | left | right | bottom | floating. Legacy names
from earlier Phase A cuts (timeline_bottom, shelf_bottom, viewport_top,
side_left, side_right) normalize onto that vocabulary; persist-the-achieved
migrates saved prefs on the next launch. dock() never raises; it returns
the mode actually achieved.

Maya-internals notes:
- top and bottom are full-width rows inserted into the CENTRAL VERTICAL
  SPLITTER, found by walking up from the playback slider (the CP-A-verified
  walk; Animo's timeline embed, buildTimelineUI Animo_Launcher.py:2618-2726,
  QSplitter-only, depth 15). Index 0 = directly below the shelf, above the
  outliner/viewport/channel-box pane group; index-of-timeline-member =
  between the pane group and the timeline. Both span the splitter's full
  width (shelf/timeline length) — Adrian's 2026-07-02 placement calls.
- The shelf is NOT in the main window's top toolbar area: a QToolBar row
  appended there renders ABOVE the shelf (observed live 2026-07-02). Do
  not revive the toolbar-area approach for below-shelf placement.
- workspaceControl positions are STICKY by name: Maya restores the
  remembered spot and ignores create-time dockToMainWindow (observed live
  2026-07-02: left reopened at the prior right position), so side docking
  forces placement with an edit-time dockToMainWindow.
- getUIComponentDockControl only knows components registered through
  createUIComponentDockControl (Attribute Editor, Channel Box / Layer
  Editor, Tool Settings, Outliner...). The Tool Box and the Shelf are
  toolbars, NOT workspaceControls — they are not valid adjacency targets.
"""
from __future__ import annotations

import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMayaUI as mui
from PySide6 import QtWidgets
from PySide6.QtCore import QPoint, Qt
from shiboken6 import wrapInstance

from maya_tools.framework.toolbar import dpi, toolbar_prefs
from maya_tools.framework.toolbar.toolbar_ui import CROSS_AXIS_V

WORKSPACE_NAME = "fabricatorToolbar"

LEGACY_MODES = {
    "timeline_bottom": "bottom",
    "shelf_bottom": "top",
    "viewport_top": "top",
    # Side docks retired (Adrian, 2026-07-03: left/right looked bad); the old
    # names, and any stale left/right pref, fall through to bottom below.
    "side_left": "bottom",
    "side_right": "bottom",
}

_SUPPORTED_MODES = ("top", "bottom", "floating")


def normalize_mode(mode: str) -> str:
    """Map legacy names onto the current vocabulary and RETIRE the side docks:
    anything unsupported - including a stale 'left'/'right' pref - collapses to
    bottom, so a machine that was last docked to a side heals on next launch."""
    mode = LEGACY_MODES.get(mode, mode)
    return mode if mode in _SUPPORTED_MODES else "bottom"


def maya_main_window() -> QtWidgets.QMainWindow | None:
    ptr = mui.MQtUtil.mainWindow()
    if not ptr:
        return None
    return wrapInstance(int(ptr), QtWidgets.QMainWindow)


def _find_control_widget(control_name: str) -> QtWidgets.QWidget | None:
    ptr = mui.MQtUtil.findControl(control_name)
    if not ptr:
        return None
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def _timeline_widget() -> QtWidgets.QWidget | None:
    try:
        playback = mel.eval("$tmp = $gPlayBackSlider")
        return _find_control_widget(playback)
    except Exception as exc:
        cmds.warning(f"FabricatorStudio toolbar: timeline lookup failed: {exc}")
        return None


def _central_splitter() -> tuple[QtWidgets.QSplitter | None, int]:
    """Find the central vertical splitter and the index of its
    timeline-containing member, walking up from the playback slider.

    This is the CP-A-verified walk (Animo's timeline embed, buildTimelineUI
    Animo_Launcher.py:2618-2726): QSplitter ancestors ONLY, depth 15 —
    accepting a layout first tends to land inside the fixed-height
    TimeSlider workspaceControl host and squashes the strip. Returns
    (None, -1) when the layout differs; the caller degrades to floating."""
    current = _timeline_widget()
    if current is None:
        return None, -1
    for _ in range(15):
        parent = current.parent()
        if parent is None:
            return None, -1
        if isinstance(parent, QtWidgets.QSplitter):
            return parent, parent.indexOf(current)
        current = parent
    return None, -1


def _delete_workspace_control() -> None:
    """Remove the wc if present (also heals partial-failure husks)."""
    try:
        if cmds.workspaceControl(WORKSPACE_NAME, exists=True):
            cmds.deleteUI(WORKSPACE_NAME, control=True)
    except Exception:
        cmds.warning("FabricatorStudio toolbar: workspaceControl cleanup failed")


def _dock_workspace_control(strip: QtWidgets.QWidget, side: str) -> bool:
    """Vertical side dock via workspaceControl (Animo :2564-2586 pattern).
    side is "left" or "right".

    Placement is FORCED with an edit-time dockToMainWindow after create:
    wc positions are sticky by name (see module docstring; observed live
    2026-07-02 — side_left reopened at the prior side_right spot). The
    right side then snugs up to the Channel Box when that component
    exists; the left side has no registered dock-control neighbour (the
    Tool Box is a toolbar, not a workspaceControl), so the forced edge
    placement is final."""
    try:
        _delete_workspace_control()
        width = dpi.scaled(CROSS_AXIS_V)
        cmds.workspaceControl(WORKSPACE_NAME, label="FabricatorStudio",
                              initialWidth=width, minimumWidth=width,
                              widthProperty="fixed", retain=False)
        cmds.workspaceControl(WORKSPACE_NAME, edit=True,
                              dockToMainWindow=[side, True])
        if side == "right":
            try:
                chan = mel.eval(
                    'getUIComponentDockControl("Channel Box / Layer Editor", false)')
                if chan:
                    cmds.workspaceControl(WORKSPACE_NAME, edit=True,
                                          dockToControl=(chan, "left"))
                else:
                    cmds.warning("FabricatorStudio toolbar: channel-box adjacency "
                                 "unavailable, docked to main window edge")
            except Exception:
                cmds.warning("FabricatorStudio toolbar: channel-box adjacency "
                             "unavailable, docked to main window edge")
        ptr = mui.MQtUtil.findControl(WORKSPACE_NAME)
        if not ptr:
            _delete_workspace_control()
            return False
        host = wrapInstance(int(ptr), QtWidgets.QWidget)
        lay = host.layout()
        if lay is None:
            lay = QtWidgets.QVBoxLayout(host)
            lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(strip)
        return True
    except Exception as exc:
        cmds.warning(f"FabricatorStudio toolbar: workspaceControl dock failed: {exc}")
        _delete_workspace_control()
        return False


def set_host_visible(visible: bool) -> None:
    """Show/hide the side dock's workspaceControl along with the strip, so
    a hidden toolbar leaves no empty panel behind. Embedded rows (top /
    bottom) need nothing: hiding the strip collapses the splitter member.
    Host ownership stays inside dock_targets; toggle owns the strip."""
    try:
        if cmds.workspaceControl(WORKSPACE_NAME, exists=True):
            cmds.workspaceControl(WORKSPACE_NAME, edit=True, visible=visible)
    except Exception:
        cmds.warning("FabricatorStudio toolbar: workspaceControl visibility "
                     "edit failed")


def _float(strip: QtWidgets.QWidget) -> str:
    strip.setParent(maya_main_window(), Qt.Tool)
    _restore_floating_pos(strip)
    strip.show()
    return "floating"


def _restore_floating_pos(strip: QtWidgets.QWidget) -> None:
    """Floating memory (C9): put the window back where the user left it
    (toolbar_app records floating_pos at teardown/mode-switch). Clamped
    inside the available screen with the popover placement's logic
    (popover_button._placement) so a monitor-layout change can't strand
    the strip off-screen. Restore failures degrade to Qt's default spot."""
    pos = toolbar_prefs.load_prefs().get("floating_pos")
    if not pos:
        return
    try:
        x, y = int(pos[0]), int(pos[1])
        win = strip.window()
        target = (QtWidgets.QApplication.screenAt(QPoint(x, y))
                  or win.screen() or strip.screen())
        if target is None:
            return
        screen = target.availableGeometry()
        win.adjustSize()
        size = win.sizeHint()
        x = min(max(x, screen.left()), screen.right() - size.width())
        y = min(max(y, screen.top()), screen.bottom() - size.height())
        win.move(x, y)
    except Exception as exc:
        cmds.warning(f"FabricatorStudio toolbar: floating position restore "
                     f"skipped: {exc}")


def dock(strip: QtWidgets.QWidget, mode: str) -> str:
    """Dock the strip; return the mode actually achieved (normalized)."""
    mode = normalize_mode(mode)
    if mode in ("top", "bottom"):
        splitter, timeline_idx = _central_splitter()
        if splitter is not None and timeline_idx >= 0:
            # top: row 0 = below the shelf, above the pane group.
            # bottom: before the timeline member = between the pane group
            # and the timeline. Both full splitter width.
            splitter.insertWidget(0 if mode == "top" else timeline_idx, strip)
            strip.show()
            return mode
        cmds.warning(f"FabricatorStudio toolbar: {mode} embed unavailable, "
                     "falling back to floating")
        return _float(strip)
    if mode in ("left", "right"):
        if _dock_workspace_control(strip, mode):
            return mode
        cmds.warning(f"FabricatorStudio toolbar: side dock ({mode}) unavailable, "
                     "falling back to floating")
        return _float(strip)
    return _float(strip)
