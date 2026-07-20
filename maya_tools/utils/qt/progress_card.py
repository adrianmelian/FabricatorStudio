# maya_tools/utils/qt/progress_card.py
"""ProgressCard — the standard coach-card progress window.

The template for long-running FabricatorStudio operations (rig builds,
exports, batch runs): the same card language as the first-run tour,
hover annotations, and toasts (Adrian, 2026-07-19) with a title, a
status line, and a Mindmeld progress bar. Frameless, non-modal,
centered over Maya; the operation drives it, the card never drives the
operation.

Usage (from a tool's UI layer, always guarded — a progress card must
never wound the operation it reports):

    from maya_tools.utils.qt.progress_card import ProgressCard

    card = ProgressCard.start('Building rig', 'Preparing scene...')
    card.update(30, 'Building components...')
    card.update(80, 'Wiring spaces...')
    card.finish('Rig built')            # flips to success, auto-fades
    # or, on failure:
    card.fail('Build failed. See the log.')

    # Unknown-length work: ProgressCard.start('Exporting', busy=True)
    # shows an indeterminate bar until update()/finish().

Maya-free: pure PySide6, imports and lays out offscreen; the default
parent resolves to Maya's main window at call time, guarded.
"""
from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from maya_tools.utils.qt.mindmeld import mindmeld_style


_CARD_W = 380
_VFRAC = 0.42           # sits a touch above center, like the tour cards
_DONE_HOLD_MS = 1400    # how long finish()/fail() lingers before fading
_FADE_OUT_MS = 420


class ProgressCard(QtWidgets.QWidget):
    """One coach-card progress window. Create via ProgressCard.start()."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(
            parent,
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)

        t = mindmeld_style.TOKENS

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)   # room for the shadow

        self._card = QtWidgets.QFrame(self)
        self._card.setObjectName("mmProgressCard")
        self._card.setFixedWidth(_CARD_W)
        outer.addWidget(self._card)

        lay = QtWidgets.QVBoxLayout(self._card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)

        self._title = QtWidgets.QLabel(self._card)
        self._title.setObjectName("mmProgressTitle")
        lay.addWidget(self._title)

        self._status = QtWidgets.QLabel(self._card)
        self._status.setObjectName("mmProgressStatus")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        self._bar = QtWidgets.QProgressBar(self._card)
        self._bar.setObjectName("mmProgressBar")
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        lay.addWidget(self._bar)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setColor(QtGui.QColor(0, 0, 0, 180))
        shadow.setOffset(0, 6)
        self._card.setGraphicsEffect(shadow)

        self._fade = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
        self._hold = QtCore.QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._begin_fade_out)

        self._apply_qss(mindmeld_style.PLASMA)

    # --- styling ---------------------------------------------------------
    def _apply_qss(self, accent: str) -> None:
        t = mindmeld_style.TOKENS
        self.setStyleSheet(
            mindmeld_style.coach_frame_qss("QFrame#mmProgressCard", accent)
            + f"""
            QLabel#mmProgressTitle {{ {mindmeld_style.COACH_TITLE_QSS} }}
            QLabel#mmProgressStatus {{ {mindmeld_style.COACH_BODY_QSS} }}
            QProgressBar#mmProgressBar {{
                background-color: {t['iron_2']};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar#mmProgressBar::chunk {{
                background-color: {accent};
                border-radius: 4px;
            }}
        """)

    # --- lifecycle -------------------------------------------------------
    @classmethod
    def start(cls, title: str, status: str = '', *, busy: bool = False,
              parent: Optional[QtWidgets.QWidget] = None) -> 'ProgressCard':
        """Show a new progress card. busy=True renders an indeterminate
        bar until the first update()."""
        if parent is None:
            parent = _maya_main_window()
        card = cls(parent)
        card._title.setText(title or '')
        card._status.setText(status or '')
        card._status.setVisible(bool(status))
        if busy:
            card._bar.setRange(0, 0)       # Qt indeterminate
        else:
            card._bar.setRange(0, 100)
            card._bar.setValue(0)
        card._place()
        card.setWindowOpacity(1.0)
        card.show()
        card.raise_()
        QtWidgets.QApplication.processEvents()   # long ops block the loop
        return card

    def update(self, value: int, status: str = None) -> None:
        """Set progress 0-100 (flips an indeterminate bar to determinate)
        and optionally replace the status line. Processes events so the
        card repaints inside a blocking operation."""
        if self._bar.maximum() == 0:
            self._bar.setRange(0, 100)
        self._bar.setValue(max(0, min(100, int(value))))
        if status is not None:
            self._status.setText(status)
            self._status.setVisible(bool(status))
        QtWidgets.QApplication.processEvents()

    def finish(self, message: str = '') -> None:
        """Success close: fill the bar, flip to the success frame, then
        fade out on its own."""
        self._close_with(mindmeld_style.PLASMA, message)

    def fail(self, message: str = '') -> None:
        """Failure close: error frame, then fade. The caller still logs
        the real error — this card is a signal, not the record."""
        self._close_with(mindmeld_style.FLARE, message)

    # --- internals -------------------------------------------------------
    def _close_with(self, accent: str, message: str) -> None:
        self._bar.setRange(0, 100)
        self._bar.setValue(100)
        if message:
            self._status.setText(message)
            self._status.setVisible(True)
        self._apply_qss(accent)
        QtWidgets.QApplication.processEvents()
        self._hold.start(_DONE_HOLD_MS)

    def _place(self) -> None:
        self.adjustSize()
        host = self.parentWidget()
        if host is not None and host.isVisible():
            geo = host.frameGeometry()
            cx = geo.x() + geo.width() // 2
            cy = geo.y() + int(geo.height() * _VFRAC)
        else:
            r = QtGui.QGuiApplication.primaryScreen().availableGeometry()
            cx = r.x() + r.width() // 2
            cy = r.y() + int(r.height() * _VFRAC)
        self.move(cx - self.width() // 2, cy - self.height() // 2)

    def _begin_fade_out(self) -> None:
        self._fade.stop()
        try:
            self._fade.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._fade.setDuration(_FADE_OUT_MS)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.close)
        self._fade.start()


def _maya_main_window() -> Optional[QtWidgets.QWidget]:
    try:
        from maya_tools.utils.maya.gui import get_maya_window
        return get_maya_window()
    except Exception:
        return None
