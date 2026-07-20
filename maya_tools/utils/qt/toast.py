# maya_tools/utils/qt/toast.py
"""Mindmeld toast — a centered, self-dismissing confirmation card.

A small frameless popover that fades in over the middle of the Maya
window, holds for a beat, then fades out. It confirms the big,
low-frequency actions where a Script-Editor line is too easy to miss:
Build / Unbuild Rig, rig + anim export, and project switch (Adrian,
2026-07-11 — "a little nicely designed popover in the middle of the
maya screen: Success! Rig Built / Exported to: <path> / Project
changed to <name>"; kept dead simple, exact copy, no decoration glyphs).

Maya-free: pure PySide6 so it imports and lays out offscreen (the
default parent resolves to Maya's main window at call time, guarded).
One reused instance — rapid-fire calls replace, never stack.

A fade is the one animation these tools earn: the toast dismisses
itself, and the fade is how that reads as intentional, not a flicker.
"""
from __future__ import annotations

from typing import Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from maya_tools.utils.qt.mindmeld import mindmeld_style


# Placement + timing (module constants — tune live).
_VFRAC = 0.5            # vertical center of the host (0=top, 1=bottom)
_FADE_IN_MS = 120
_HOLD_MS = 1900
_FADE_OUT_MS = 420
_MAX_W = 480

# kind -> accent stripe token
_ACCENTS = {
    "success": mindmeld_style.PLASMA,
    "info":    mindmeld_style.EMBER,
    "warn":    mindmeld_style.AMBER,
    "error":   mindmeld_style.FLARE,
}


class _Toast(QtWidgets.QWidget):
    """One reused frameless card: a thin accent stripe and one line of text."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(
            parent,
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)

        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)   # breathing room for the shadow

        self._card = QtWidgets.QFrame(self)
        self._card.setObjectName("mmToast")
        self._card.setMaximumWidth(_MAX_W)
        outer.addWidget(self._card)

        card = QtWidgets.QHBoxLayout(self._card)
        card.setContentsMargins(0, 0, 0, 0)
        card.setSpacing(0)

        # (The 1.0-era accent stripe is gone — under the standard coach
        # card the border itself carries the kind accent.)
        self._msg = QtWidgets.QLabel(self._card)
        self._msg.setObjectName("mmToastMsg")
        self._msg.setWordWrap(True)   # long export paths wrap; no ellipsis glyph
        self._msg.setContentsMargins(18, 13, 20, 14)
        card.addWidget(self._msg)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setColor(QtGui.QColor(0, 0, 0, 180))
        shadow.setOffset(0, 6)
        self._card.setGraphicsEffect(shadow)

        self._fade = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
        self._hold = QtCore.QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._begin_fade_out)

    # --- styling ---------------------------------------------------------
    def _apply_qss(self, accent: str) -> None:
        # The standard coach card (Adrian, 2026-07-19): same frame as the
        # tour cards and hover annotations, with the border carrying the
        # kind accent (success = plasma = literally the coachmark frame;
        # warn/error borders match their message per the color law).
        # VT323 retired with Mindmeld 1.0.
        t = mindmeld_style.TOKENS
        self.setStyleSheet(
            mindmeld_style.coach_frame_qss("QFrame#mmToast", accent)
            + f"""
            QLabel#mmToastMsg {{
                color: {t['bone']};
                font-family: '{t['font_display']}','Consolas',monospace;
                font-size: 16px;
                font-weight: 700;
                background: transparent;
            }}
        """)

    # --- present ---------------------------------------------------------
    def present(self, message: str, kind: str) -> None:
        accent = _ACCENTS.get(kind, _ACCENTS["success"])
        self._apply_qss(accent)
        self._msg.setText(message or "")

        self._card.adjustSize()
        self.adjustSize()
        self._center()

        self._hold.stop()
        self._fade.stop()
        self._disconnect_fade()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._fade.setDuration(_FADE_IN_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._hold.start(_HOLD_MS)

    def _center(self) -> None:
        host = self.parentWidget()
        if host is not None and host.isVisible():
            geo = host.frameGeometry()
            cx = geo.x() + geo.width() // 2
            cy = geo.y() + int(geo.height() * _VFRAC)
        else:
            screen = QtGui.QGuiApplication.primaryScreen()
            r = screen.availableGeometry()
            cx = r.x() + r.width() // 2
            cy = r.y() + int(r.height() * _VFRAC)
        self.move(cx - self.width() // 2, cy - self.height() // 2)

    def _begin_fade_out(self) -> None:
        self._fade.stop()
        self._disconnect_fade()
        self._fade.setDuration(_FADE_OUT_MS)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.hide)
        self._fade.start()

    def _disconnect_fade(self) -> None:
        try:
            self._fade.finished.disconnect()
        except (RuntimeError, TypeError):
            pass


_instance: Optional[_Toast] = None


def _maya_main_window() -> Optional[QtWidgets.QWidget]:
    try:
        from maya_tools.utils.maya.gui import get_maya_window
        return get_maya_window()
    except Exception:
        return None


def show_toast(message: str, *,
               kind: str = "success",
               parent: Optional[QtWidgets.QWidget] = None) -> Optional[_Toast]:
    """Fade a centered Mindmeld toast over the Maya window. `message` is
    shown verbatim on one line (wraps if long). Guarded by the caller —
    a toast never blocks the action it confirms."""
    global _instance
    if parent is None:
        parent = _maya_main_window()
    if _instance is None:
        _instance = _Toast(parent)
    _instance.present(message, kind)
    return _instance


def exported(written: Sequence[str], *,
             parent: Optional[QtWidgets.QWidget] = None) -> Optional[_Toast]:
    """`Exported to: <path>` for a single file, or the destination folder
    when several files were written."""
    import os
    paths = list(written or [])
    if not paths:
        return None
    where = str(paths[0]) if len(paths) == 1 else os.path.dirname(str(paths[0]))
    return show_toast(f"Exported to: {where}", parent=parent)
