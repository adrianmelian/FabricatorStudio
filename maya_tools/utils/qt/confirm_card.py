"""confirm() — a small Mindmeld yes/no card.

The toolset's confirmations were raw QMessageBox until now: native OS
chrome dropped into the middle of a Mindmeld window, with the system
font and the system button order. This is the same frame every other
floating card in the toolset wears (coach_frame_qss), so a confirmation
looks like it belongs to the tool asking the question.

    from maya_tools.utils.qt.confirm_card import confirm
    if confirm('Skip the tour?', 'You can restart it any time from '
               'File > Take the Tour.',
               ok_label='Skip Tour', cancel_label='Keep Going',
               recommend_cancel=True, parent=win):
        ...

Modal and blocking, like QMessageBox.question, so it drops into existing
call sites without restructuring them.

Colour law: the RECOMMENDED button takes plasma, never whichever one
happens to be affirmative. Cancelling out of a destructive prompt is the
safe move, so `recommend_cancel=True` makes Cancel the plasma button and
leaves the destructive answer quiet.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from maya_tools.utils.qt.mindmeld import mindmeld_style as _mm


CARD_W = 380


class _ConfirmDialog(QtWidgets.QDialog):
    """Frameless Mindmeld confirm. Esc cancels, Enter takes the
    recommended answer."""

    def __init__(self, title, body, ok_label, cancel_label,
                 recommend_cancel=False, danger=False, parent=None):
        super().__init__(parent)
        _mm.apply(self)
        self.setObjectName('mmConfirm')
        self.setWindowFlags(QtCore.Qt.WindowType.Dialog
                            | QtCore.Qt.WindowType.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QtWidgets.QFrame(self)
        card.setObjectName('mmConfirmCard')
        card.setStyleSheet(
            _mm.coach_frame_qss('QFrame#mmConfirmCard',
                                accent=_mm.EMBER if danger else None)
            + f"QLabel#mmConfirmTitle {{ {_mm.COACH_TITLE_QSS} }}"
            + f"QLabel#mmConfirmBody {{ {_mm.COACH_BODY_QSS} }}")
        card.setFixedWidth(CARD_W)
        outer.addWidget(card)

        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)

        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName('mmConfirmTitle')
        title_label.setWordWrap(True)
        title_label.setVisible(bool(title))
        lay.addWidget(title_label)

        body_label = QtWidgets.QLabel(body)
        body_label.setObjectName('mmConfirmBody')
        body_label.setWordWrap(True)
        body_label.setVisible(bool(body))
        lay.addWidget(body_label)

        lay.addSpacing(6)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()

        # The recommended answer is the plasma one, whichever side it is.
        cancel_kind = 'primary' if recommend_cancel else 'ghost'
        ok_kind = 'ghost' if recommend_cancel else (
            'danger' if danger else 'primary')

        self._cancel = _mm.button(cancel_label, kind=cancel_kind)
        self._ok = _mm.button(ok_label, kind=ok_kind)
        # Recommended sits on the right, where the eye finishes.
        if recommend_cancel:
            row.addWidget(self._ok)
            row.addWidget(self._cancel)
            self._cancel.setDefault(True)
        else:
            row.addWidget(self._cancel)
            row.addWidget(self._ok)
            self._ok.setDefault(True)
        lay.addLayout(row)

        self._ok.clicked.connect(self.accept)
        self._cancel.clicked.connect(self.reject)

    def showEvent(self, event):
        super().showEvent(event)
        self.adjustSize()
        host = self.parent()
        w, h = self.width(), self.height()
        if host is not None:
            geo = host.frameGeometry()
            x = geo.x() + (geo.width() - w) // 2
            y = geo.y() + int(geo.height() * 0.33)
        else:
            geo = QtGui.QGuiApplication.primaryScreen().availableGeometry()
            x, y = geo.center().x() - w // 2, geo.center().y() - h // 2
        screen = (QtGui.QGuiApplication.screenAt(QtCore.QPoint(x, y))
                  or QtGui.QGuiApplication.primaryScreen())
        r = screen.availableGeometry()
        self.move(max(r.left() + 8, min(x, r.right() - w - 8)),
                  max(r.top() + 8, min(y, r.bottom() - h - 8)))


def confirm(title: str, body: str = '', *, ok_label: str = 'OK',
            cancel_label: str = 'Cancel', recommend_cancel: bool = False,
            danger: bool = False,
            parent: Optional[QtWidgets.QWidget] = None) -> bool:
    """Show a modal Mindmeld confirm. True when the user takes `ok_label`.

    Never raises: if Qt cannot show the dialog for any reason, returns
    False. A confirmation that fails open would let a destructive action
    through on a broken UI, so the safe default is 'the user did not
    agree'.
    """
    try:
        dlg = _ConfirmDialog(title, body, ok_label, cancel_label,
                             recommend_cancel=recommend_cancel,
                             danger=danger, parent=parent)
        return dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted
    except Exception:
        import traceback
        traceback.print_exc()
        return False
