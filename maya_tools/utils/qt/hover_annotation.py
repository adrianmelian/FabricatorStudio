"""HoverAnnotation + AnnotationController — rich AnimBot-style hover popovers.

Qt's built-in QToolTip cannot animate a gif, so this is a small frameless
Mindmeld card: a title, a wrapped description, and an aspect-scaled looping
gif. A hover timer shows it after a short dwell; a distance-check timer then
lets the cursor move onto the card without it vanishing (so a gif stays
readable), and hides it once the cursor leaves both the source and the card.

Design reference: the AnimBot tooltip UX (rich card + gif + move-onto-popover),
reimplemented independently in the Mindmeld visual language.

An AnnotationController wires the shared card to plain widgets (toolbar buttons,
property option rows) and to tree items (the Fabricator palette). Every attach
is meant to be called inside a guarded try/except so a failure degrades to no
popover, never a broken panel.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6 import QtWidgets, QtCore, QtGui

from maya_tools.utils.qt.coach_card import fit_movie
from maya_tools.utils.qt.mindmeld import mindmeld_style as _mm


HOVER_DELAY_MS = 450          # dwell before the card appears
GIF_MAX_W = 420               # gif display cap (aspect-preserved, never upscaled)
GIF_MAX_H = 260
CARD_MAX_W = 460
BANNER_MAX_H = 48             # brand-banner header cap (aspect-preserved)

# The standard coach card (Adrian, 2026-07-19): every hover annotation
# wears the same frame + typography as the first-run tour cards and the
# toasts, sourced from mindmeld_style so the look has one home.
_QSS = (
    _mm.coach_frame_qss("QFrame#ksAnnoCard")
    + f"""
QLabel#ksAnnoTitle {{ {_mm.COACH_TITLE_QSS} }}
QLabel#ksAnnoDesc {{ {_mm.COACH_BODY_QSS} }}
QFrame#ksAnnoRule {{ background-color: {_mm.TOKENS['iron_3']}; border: none; }}
QLabel#ksAnnoGif {{ background: transparent; }}
QLabel#ksAnnoBanner {{ background: transparent; }}
""")


class HoverAnnotation(QtWidgets.QWidget):
    """One reused frameless card: title, thin rule, wrapped description, gif."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent, QtCore.Qt.ToolTip
                         | QtCore.Qt.FramelessWindowHint
                         | QtCore.Qt.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self._movie: Optional[QtGui.QMovie] = None
        self._anchor: Optional[QtWidgets.QWidget] = None

        # Polls the cursor while shown: keep the card alive while the cursor is
        # over the source widget or the card (+margin), otherwise hide.
        self._watch = QtCore.QTimer(self)
        self._watch.setInterval(100)
        self._watch.timeout.connect(self._check_distance)

        self._build()
        self.setStyleSheet(_QSS)

    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._card = QtWidgets.QFrame(self)
        self._card.setObjectName("ksAnnoCard")
        self._card.setMaximumWidth(CARD_MAX_W)
        outer.addWidget(self._card)

        lay = QtWidgets.QVBoxLayout(self._card)
        lay.setContentsMargins(11, 9, 11, 10)
        lay.setSpacing(6)

        self._banner = QtWidgets.QLabel(self._card)
        self._banner.setObjectName("ksAnnoBanner")
        self._title = QtWidgets.QLabel(self._card)
        self._title.setObjectName("ksAnnoTitle")
        self._rule = QtWidgets.QFrame(self._card)
        self._rule.setObjectName("ksAnnoRule")
        self._rule.setFixedHeight(1)
        self._desc = QtWidgets.QLabel(self._card)
        self._desc.setObjectName("ksAnnoDesc")
        self._desc.setWordWrap(True)
        self._desc.setMaximumWidth(CARD_MAX_W - 24)
        self._gif = QtWidgets.QLabel(self._card)
        self._gif.setObjectName("ksAnnoGif")
        self._gif.setAlignment(QtCore.Qt.AlignCenter)

        lay.addWidget(self._banner)
        lay.addWidget(self._title)
        lay.addWidget(self._rule)
        lay.addWidget(self._desc)
        lay.addWidget(self._gif, 0, QtCore.Qt.AlignHCenter)

    # --- content ---------------------------------------------------------
    def _set_gif(self, path: str) -> bool:
        # Scale-to-fit-never-upscale lives in coach_card.fit_movie so this
        # card and the tour cards can never drift apart on it.
        self._stop_movie()
        fitted = fit_movie(path, GIF_MAX_W, GIF_MAX_H)
        if fitted is None:
            self._gif.hide()
            return False
        self._movie, dw, dh = fitted
        self._gif.setFixedSize(dw, dh)
        self._gif.setMovie(self._movie)
        self._movie.start()
        self._gif.show()
        return True

    def _set_banner(self, path: str) -> bool:
        """Brand-image header above the title (Annotation.banner). Scaled to
        fit the card width and BANNER_MAX_H, aspect-preserved, never
        upscaled. Missing/broken file degrades to no banner."""
        if not path:
            self._banner.hide()
            return False
        pix = QtGui.QPixmap(path)
        if pix.isNull() or pix.width() <= 0 or pix.height() <= 0:
            self._banner.hide()
            return False
        scale = min((CARD_MAX_W - 24) / float(pix.width()),
                    BANNER_MAX_H / float(pix.height()), 1.0)
        self._banner.setPixmap(pix.scaled(
            int(pix.width() * scale), int(pix.height() * scale),
            QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        self._banner.show()
        return True

    def show_annotation(self, ann, anchor: Optional[QtWidgets.QWidget],
                        pinned: bool = False) -> None:
        title = getattr(ann, "title", "") or ""
        text = getattr(ann, "text", "") or ""
        has_banner = self._set_banner(getattr(ann, "banner", "") or "")
        self._title.setText(title)
        self._title.setVisible(bool(title))
        self._desc.setText(text)
        self._desc.setVisible(bool(text))
        has_gif = self._set_gif(getattr(ann, "gif", "") or "")
        self._rule.setVisible((bool(title) or has_banner)
                              and (bool(text) or has_gif))
        self._card.adjustSize()
        self.adjustSize()
        self._anchor = anchor
        self.move(self._placement(anchor))
        self.show()
        self.raise_()
        if not pinned:
            self._watch.start()
        # pinned: the caller owns the lifetime (hide_annotation()) — the
        # first-run tour shows a demo card the cursor is nowhere near.

    def _placement(self, anchor) -> QtCore.QPoint:
        """Prefer just below the anchor; flip ABOVE it when a below-card would
        run off the screen bottom (the same rule the toolbar menus use, so a
        bottom-docked strip shows its cards upward). Falls back to the cursor
        when there's no anchor."""
        w, h = self.width(), self.height()
        # Default: below the anchor (or just off the cursor).
        below = QtGui.QCursor.pos() + QtCore.QPoint(16, 16)
        anchor_top = None
        if anchor is not None:
            try:
                below = anchor.mapToGlobal(QtCore.QPoint(0, anchor.height() + 6))
                anchor_top = anchor.mapToGlobal(QtCore.QPoint(0, 0)).y()
            except RuntimeError:
                pass
        screen = (QtGui.QGuiApplication.screenAt(below)
                  or QtGui.QGuiApplication.primaryScreen())
        r = screen.availableGeometry()
        x, y = below.x(), below.y()
        if x + w > r.right():
            x = r.right() - w - 8
        if y + h > r.bottom():
            # Flip above the anchor, but only if the card actually fits there
            # (else keep it below and let the final clamp keep it on-screen).
            top = anchor_top if anchor_top is not None else QtGui.QCursor.pos().y()
            above_y = top - h - 6
            if above_y >= r.top() + 8:
                y = above_y
            else:
                y = r.bottom() - h - 8
        return QtCore.QPoint(max(r.left() + 8, x), max(r.top() + 8, y))

    def _check_distance(self) -> None:
        if not self.isVisible():
            self._watch.stop()
            return
        pos = QtGui.QCursor.pos()
        margin = 24
        if self.geometry().adjusted(-margin, -margin, margin, margin).contains(pos):
            return
        if self._anchor is not None:
            try:
                a = QtCore.QRect(self._anchor.mapToGlobal(QtCore.QPoint(0, 0)),
                                 self._anchor.size())
                if a.contains(pos):
                    return
            except RuntimeError:
                pass
        self.hide_annotation()

    def _stop_movie(self) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._gif.setMovie(None)
            self._movie = None

    def hide_annotation(self) -> None:
        self._watch.stop()
        self._stop_movie()
        self._anchor = None
        self.hide()


class AnnotationController(QtCore.QObject):
    """Wires the shared card to widgets and tree items.

    Usage (guarded at each call site):
        self._anno = AnnotationController(self)
        self._anno.attach_widget(btn, lambda: annotations.for_tool('exporter'))
        self._anno.attach_tree(palette_tree, self._annotation_for_item)
    """

    def __init__(self, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self._popover = HoverAnnotation()
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)
        self._pending: Optional[tuple] = None  # (getter, anchor)

    # --- plain widgets (toolbar buttons, property option rows) ---
    def attach_widget(self, widget: QtWidgets.QWidget, getter: Callable[[], object]) -> None:
        # The getter lives on the widget itself (no id-keyed map): destroyed on
        # a rebuild, its registration goes too, so a reused id never resolves to
        # a stale getter.
        widget._ks_anno_getter = getter
        widget.setAttribute(QtCore.Qt.WA_Hover, True)
        widget.installEventFilter(self)

    # --- QTreeWidget items (the Fabricator palette) ---
    def attach_tree(self, tree: QtWidgets.QTreeWidget,
                    item_getter: Callable[[QtWidgets.QTreeWidgetItem], object]) -> None:
        vp = tree.viewport()
        vp._ks_tree = tree
        vp._ks_tree_getter = item_getter
        tree.setMouseTracking(True)
        vp.setMouseTracking(True)
        tree.itemEntered.connect(
            lambda item, _col, g=item_getter, v=vp: self._on_tree_item(item, g, v))
        vp.installEventFilter(self)

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        et = event.type()
        # Suppress the native tooltip wherever our card owns the hover.
        if et == QtCore.QEvent.ToolTip:
            if getattr(obj, "_ks_anno_getter", None) is not None:
                return True
            tree = getattr(obj, "_ks_tree", None)
            getter = getattr(obj, "_ks_tree_getter", None)
            if tree is not None and getter is not None:
                try:
                    item = tree.itemAt(event.pos())
                    if item is not None and getter(item):
                        return True
                except Exception:
                    pass
            return False
        if et == QtCore.QEvent.MouseButtonPress:
            # Any press on an annotated widget supersedes the hover card
            # (Adrian, 2026-07-10): a right-click is opening the options
            # menu, a left-click is firing the command - either way the
            # card is done. Drop a shown card, cancel a pending one.
            self._timer.stop()
            self._pending = None
            self._popover.hide_annotation()
        elif et in (QtCore.QEvent.Enter, QtCore.QEvent.HoverEnter):
            getter = getattr(obj, "_ks_anno_getter", None)
            if getter is not None:
                self._popover.hide_annotation()
                anchor = obj if isinstance(obj, QtWidgets.QWidget) else None
                self._arm(getter, anchor)
        elif et in (QtCore.QEvent.Leave, QtCore.QEvent.HoverLeave):
            # Cancel a not-yet-shown card; a shown one is governed by the card's
            # own distance-check, so the cursor can move onto it.
            self._timer.stop()
            self._pending = None
        return False

    def _on_tree_item(self, item, getter, viewport) -> None:
        # New row under the cursor: drop the current card, then arm for this row
        # (the viewport is the anchor, so the card lives while over the palette).
        self._popover.hide_annotation()
        self._arm(lambda: getter(item), viewport)

    def _arm(self, getter: Callable[[], object],
             anchor: Optional[QtWidgets.QWidget]) -> None:
        self._pending = (getter, anchor)
        self._timer.start(HOVER_DELAY_MS)

    def _fire(self) -> None:
        if self._pending is None:
            return
        getter, anchor = self._pending
        try:
            ann = getter()
        except Exception:
            ann = None
        if ann:  # Annotation.__bool__ is False when title, text, gif all empty
            self._popover.show_annotation(ann, anchor)
        else:
            self._popover.hide_annotation()
