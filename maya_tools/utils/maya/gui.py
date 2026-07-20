# utils/maya/gui.py
# Maya-specific GUI helpers.
from PySide6 import QtWidgets
from maya import OpenMayaUI
from shiboken6 import wrapInstance


def get_maya_window() -> QtWidgets.QWidget:
    """Return the Maya main window as a QWidget for use as a dialog parent."""
    ptr = OpenMayaUI.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def close_windows_by_name(object_name: str) -> None:
    """Close + schedule-delete every Maya-main-window child whose
    objectName matches `object_name`.

    Reload-proof singleton helper. The shelf launches tools with
    `importlib.reload(module); module.Win.show_window()`, which resets the
    module's `_win` global to None on every press — so the prior window
    can't be found by Python reference and gets orphaned (duplicate
    windows). Finding it by its stable objectName string closes it
    regardless of the lost global. Call this at the top of show_window
    before constructing the new instance.
    """
    maya_win = get_maya_window()
    if maya_win is None:
        return
    for w in maya_win.findChildren(QtWidgets.QWidget, object_name):
        try:
            w.close()
            w.deleteLater()
        except Exception:
            pass
