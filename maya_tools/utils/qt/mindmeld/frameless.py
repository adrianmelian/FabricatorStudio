# maya_tools/utils/qt/mindmeld/frameless.py
"""Mindmeld frameless window shell — drops the OS title bar so full
tool windows wear the same face as the toolbar popovers (Adrian,
2026-07-05: "just remove the top bar, so it looks like the other
windows"). First customer: Fabricator; the rest of the fleet joins
during the Mindmeld 2.0 code sweep.

The shell replaces what the native frame used to provide:
  * move    — drag any dead space in the window (margins, labels, gaps
              between panels — adopt() installs _WindowDrag); the brand
              row, wrapped in a DragBar, is the guaranteed handle and
              adds double-click maximize
  * close   — the ember X from close_button(), placed after the brand
              row's stretch
  * resize  — a QSizeGrip pinned to the bottom-right corner by adopt()
  * an edge — a 1px iron border via the mindmeld="frameless" property
              (without it a carbon window bleeds into Maya's dark UI)

Usage, inside a tool window's __init__:

    frameless.adopt(self)                      # before apply()/show()
    ...
    self._brand_bar = frameless.DragBar()
    brand_row = QtWidgets.QHBoxLayout(self._brand_bar)
    ... brand_label / caps_label / addStretch ...
    brand_row.addWidget(frameless.close_button(self))
    root.addWidget(self._brand_bar)

setWindowFlags REPLACES flags, it never merges — any show_window()
that calls setWindowFlags(Qt.Window) after construction must OR
Qt.FramelessWindowHint back in or the title bar returns.
"""
__author__ = "Adrian Melian"

from PySide6 import QtCore, QtWidgets


class DragBar(QtWidgets.QWidget):
    """Brand-row wrapper that moves its top-level window when dragged —
    the popover drag-bar pattern lifted to full windows. Presses on the
    child labels propagate here (labels ignore mouse); buttons in the
    row consume their own clicks, so the X stays clickable."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._offset = None

    def _frameless_window(self):
        """The window to move — ONLY when it is a frameless-adopted
        one. A docked/embedded host's top-level is Maya's own main
        window, and dragging the brand row must never move THAT
        (adopt() tags its windows mindmeld="frameless")."""
        win = self.window()
        if win is not None and win.property('mindmeld') == 'frameless':
            return win
        return None

    def mousePressEvent(self, event):
        win = self._frameless_window()
        if win is not None and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._offset = (event.globalPosition().toPoint()
                            - win.frameGeometry().topLeft())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        win = self._frameless_window()
        if (win is not None and self._offset is not None
                and event.buttons() & QtCore.Qt.MouseButton.LeftButton
                and not win.isMaximized()):
            win.move(event.globalPosition().toPoint() - self._offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # The one title-bar affordance worth keeping beyond move/close.
        win = self._frameless_window()
        if win is not None and event.button() == QtCore.Qt.MouseButton.LeftButton:
            if win.isMaximized():
                win.showNormal()
            else:
                win.showMaximized()
        super().mouseDoubleClickEvent(event)


class _WindowDrag(QtCore.QObject):
    """Dead-space drag (Adrian, 2026-07-05: "drag from anywhere in the
    window"): a left press that no child widget claims propagates up to
    the window itself — grab it there and move the window. Interactive
    widgets (canvas, fields, lists, sliders, splitter handles, the size
    grip) consume their presses first, so they are unaffected; what's
    left is margins, labels, and the gaps between panels."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._offset = None

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QtCore.QEvent.Type.MouseButtonPress:
            if event.button() == QtCore.Qt.MouseButton.LeftButton:
                self._offset = (event.globalPosition().toPoint()
                                - self._window.frameGeometry().topLeft())
        elif t == QtCore.QEvent.Type.MouseMove:
            if (self._offset is not None
                    and event.buttons() & QtCore.Qt.MouseButton.LeftButton
                    and not self._window.isMaximized()):
                self._window.move(event.globalPosition().toPoint()
                                  - self._offset)
        elif t == QtCore.QEvent.Type.MouseButtonRelease:
            self._offset = None
        return False


class _GripKeeper(QtCore.QObject):
    """Pins the size grip to the window's bottom-right corner across
    resizes and keeps it above siblings added after adopt()."""

    def __init__(self, window, grip):
        super().__init__(window)
        self._grip = grip

    def eventFilter(self, obj, event):
        if event.type() in (QtCore.QEvent.Type.Resize,
                            QtCore.QEvent.Type.Show):
            self._grip.move(obj.width() - self._grip.width() - 1,
                            obj.height() - self._grip.height() - 1)
            self._grip.raise_()
        return False


def adopt(window: QtWidgets.QWidget) -> None:
    """Make `window` frameless. Call before the window is shown.

    Sets the FramelessWindowHint (OR'd — existing flags survive), tags
    the window mindmeld="frameless" for the QSS border, and floats a
    QSizeGrip in the bottom-right corner: a frameless window loses the
    native resize edges, and the grip is what gives resizing back.
    """
    window.setWindowFlags(window.windowFlags()
                          | QtCore.Qt.WindowType.FramelessWindowHint)
    window.setProperty('mindmeld', 'frameless')
    grip = QtWidgets.QSizeGrip(window)
    grip.setFixedSize(14, 14)
    window.installEventFilter(_GripKeeper(window, grip))
    window.installEventFilter(_WindowDrag(window))


def close_button(window: QtWidgets.QWidget) -> QtWidgets.QPushButton:
    """The popover's ember X, full-size: closes `window` through the
    normal close() path, so closeEvent teardown (callbacks, watchers,
    size persistence) runs exactly as it did with the native bar."""
    btn = QtWidgets.QPushButton('X', window)
    btn.setObjectName('mm_window_close')
    btn.setFixedSize(26, 20)
    btn.setToolTip('Close')
    btn.clicked.connect(window.close)
    return btn
