"""The Reggie chat dock: a Maya workspaceControl hosting ReggiePanel.

Mirrors fabricator_dock.py, the shipped pattern: spawn DOCKED by TABBING
into the Attribute Editor (interactive drag-docking silently fails when
the workspace is locked, and dockToMainWindow('right') can materialize a
collapsed pane that "just vanishes"); retain=False so closing deletes the
control; uiScript restores it with workspace layouts at Maya startup.

Toolbar-lifecycle-independent (like the Fabricator dock): the toolbar
button calls toggle(); toolbar relaunch does not touch this dock.

Rebrand cleanup (2026-07-11, Tools Engineer S3): the old
'KSReggieDock' workspaceControl name is retired to 'FSReggieDock'. A
stale KSReggieDock control can be sitting in a user's saved workspace
layout from before the rename — open_docked() deletes it once on open
before creating the new-named control.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

DOCK_NAME = "FSReggieDock"
DOCK_LABEL = "AI.TA"
_OLD_DOCK_NAME = "KSReggieDock"   # pre-rebrand name — remove after v1

_UI_SCRIPT = ("from maya_tools.framework.toolbar.reggie import reggie_dock; "
              "reggie_dock._populate()")

_DOCK_PANEL = None   # the live ReggiePanel (or a dead wrapper)


def is_open() -> bool:
    import maya.cmds as cmds
    return bool(cmds.workspaceControl(DOCK_NAME, q=True, exists=True))


def toggle() -> None:
    """Open/close pair. The toolbar button now calls open_docked() (show
    or raise, never close) instead - kept here as a future keybind
    target."""
    if is_open():
        close_dock()
    else:
        open_docked()


def open_docked() -> None:
    import maya.cmds as cmds
    # The panel needs the bridge; start is idempotent and cheap. This
    # explicit call is LOAD-BEARING for the already-open restore branch
    # below (where _populate never re-runs), not redundant with
    # ReggiePanel's own _ensure_bridge — do not "clean it up".
    try:
        from maya_tools.framework.toolbar import ai_bridge
        ai_bridge.start()
    except Exception:
        cmds.warning("Reggie: AI bridge start failed (port may be in use)")
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
    # Spawn DOCKED rather than floating, and dock by TABBING into the
    # Attribute Editor — a control every session has, always visible —
    # not by edge-docking (see the Fabricator dock precedent for why).
    kwargs = dict(label=DOCK_LABEL, retain=False, uiScript=_UI_SCRIPT,
                  initialWidth=420, initialHeight=760)
    if cmds.workspaceControl("AttributeEditor", q=True, exists=True):
        kwargs["tabToControl"] = ("AttributeEditor", -1)
    else:
        kwargs["dockToMainWindow"] = ("right", 1)
    cmds.workspaceControl(DOCK_NAME, **kwargs)
    cmds.workspaceControl(DOCK_NAME, e=True, restore=True)


def _control_widget():
    import maya.OpenMayaUI as omui
    from PySide6 import QtWidgets
    from shiboken6 import wrapInstance
    ptr = omui.MQtUtil.findControl(DOCK_NAME)
    if not ptr:
        return None
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def _populate() -> None:
    """Build the ReggiePanel inside the control. Called by the
    workspaceControl's uiScript (creation + workspace restore)."""
    global _DOCK_PANEL
    from PySide6 import QtWidgets
    from maya_tools.framework.toolbar.reggie.panel import ReggiePanel
    host = _control_widget()
    if host is None:
        return
    lay = host.layout()
    if lay is None:
        lay = QtWidgets.QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
    panel = ReggiePanel()
    lay.addWidget(panel)
    _DOCK_PANEL = panel
    # Dock death path: deleting the control destroys children WITHOUT
    # closeEvent; ride the destroyed signal to stop any in-flight turn.
    stopper = panel.stop_current_turn      # captured ref; idempotent
    panel.destroyed.connect(lambda *_: stopper())


def close_dock() -> None:
    """Tear the dock down cleanly: child first (stop any live turn +
    closeEvent while alive), then the control. Deliberately does NOT
    stop the bridge: external BYO clients may be using it, and the
    bridge lifecycle belongs to the toolbar."""
    global _DOCK_PANEL
    import maya.cmds as cmds
    if not is_open():
        _DOCK_PANEL = None
        return
    try:
        if _DOCK_PANEL is not None:
            _DOCK_PANEL.stop_current_turn()
            _DOCK_PANEL.close()
    except Exception:
        pass
    _DOCK_PANEL = None
    try:
        cmds.deleteUI(DOCK_NAME)
    except Exception:
        pass
