"""CoachCard — the pointer card the guided tours are built from.

Promoted out of framework/first_run.py (2026-07-21) unchanged in
behavior, so a second tour (Fabricator's) can use it without copying it.
The install tour was its only consumer and stays byte-identical on
screen.

A frameless Mindmeld card with a painted notch pointing at an anchor
widget. Sits below the anchor (notch up) when there is room and flips
above it (notch down) otherwise, since the toolbar docks at any edge,
and clamps itself fully on-screen with the notch chasing the anchor
rather than letting the card slide off.

Three ways to advance, all optional and combinable:
  - the Next button (present whenever there is no advance_probe)
  - advance_probe: a zero-arg callable polled 5x/s; True retires the card
  - advance_on_anchor_press: clicking the pointed-at widget IS Next, and
    the press still reaches the widget, so doing the thing advances

Every card carries a quiet Skip. `act` turns the eyebrow from
'STEP 1 OF 4' into 'THE LAYOUT / 1 OF 4' for multi-act tours; step and
total are per-act, so the dot row scopes to the act too.

`gif` renders a looping clip under the body text through fit_movie, the
same scale-to-fit-never-upscale path hover_annotation uses.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from maya_tools.utils.qt.mindmeld import mindmeld_style as _mm


NOTCH_H = 10
PROBE_MS = 200
CARD_W = 320            # text-only default (the install tour's width)
CARD_W_GIF = 380        # widened when a card carries a gif
GIF_MAX_W = 340
GIF_MAX_H = 190


def fit_movie(path: str, max_w: int, max_h: int):
    """(QMovie, w, h) scaled to fit the caps, aspect-preserved and NEVER
    upscaled, or None when the path is empty / unreadable / zero-sized.

    Single gif renderer for the whole Qt layer: HoverAnnotation and
    CoachCard both come through here, so the two cards can never drift
    on scaling or cache policy.
    """
    if not path:
        return None
    probe = QtGui.QMovie(path)
    probe.jumpToFrame(0)
    size = probe.currentImage().size()
    probe.deleteLater()
    w, h = size.width(), size.height()
    if w <= 0 or h <= 0:
        return None
    scale = min(max_w / float(w), max_h / float(h), 1.0)
    dw, dh = int(w * scale), int(h * scale)
    movie = QtGui.QMovie(path)
    movie.setCacheMode(QtGui.QMovie.CacheAll)
    movie.setScaledSize(QtCore.QSize(dw, dh))
    return movie, dw, dh


class _NotchFrame(QtWidgets.QWidget):
    """The card body: a rounded iron rect with a plasma outline and one
    triangular notch. `notch_x` is a px offset from the card's left edge,
    retargeted by CoachCard.show() so it lands under the anchor even
    after the card has been clamped on-screen."""

    def __init__(self, radius: int, parent=None):
        super().__init__(parent)
        self._radius = radius
        self.notch_x = 40
        self.notch_down = False

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        w, h, n, r = self.width(), self.height(), NOTCH_H, self._radius
        down = self.notch_down
        body = (QtCore.QRectF(0.5, 0.5, w - 1, h - n - 1) if down
                else QtCore.QRectF(0.5, n + 0.5, w - 1, h - n - 1))
        path = QtGui.QPainterPath()
        path.addRoundedRect(body, r, r)
        nx = max(r + 10, min(self.notch_x, w - r - 10))
        if down:
            path.moveTo(nx - 8, h - n - 1)
            path.lineTo(nx, h - 1)
            path.lineTo(nx + 8, h - n - 1)
        else:
            path.moveTo(nx - 8, n + 1)
            path.lineTo(nx, 1)
            path.lineTo(nx + 8, n + 1)
        path.closeSubpath()
        p.fillPath(path, QtGui.QColor(_mm.TOKENS['iron']))
        pen = QtGui.QPen(QtGui.QColor(_mm.TOKENS['plasma']))
        pen.setWidthF(1.0)
        p.setPen(pen)
        p.drawPath(path)
        p.end()


class CoachCard:
    """One pointer card. Construct, then show(); it retires itself and
    calls on_next / on_skip. Never raises out of its own timers."""

    def __init__(self, anchor_widget, title, body_text,
                 on_next=None, on_skip=None, advance_probe=None,
                 advance_on_anchor_press=False, next_label='Next',
                 step=None, total=None, act=None, gif='',
                 parent=None):
        self._on_next = on_next
        self._on_skip = on_skip
        self._anchor = anchor_widget
        self._probe = advance_probe
        self._closed = False
        self._press_filter = None
        self._movie = None
        t = _mm.TOKENS
        radius = int(str(t.get('radius_md', '8px')).rstrip('px'))

        self.card = _NotchFrame(radius, parent)
        c = self.card
        c.setWindowFlags(QtCore.Qt.WindowType.Tool
                         | QtCore.Qt.WindowType.FramelessWindowHint
                         | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        c.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

        lay = QtWidgets.QVBoxLayout(c)
        lay.setContentsMargins(18, NOTCH_H + 14, 18, NOTCH_H + 14)
        lay.setSpacing(8)

        # Eyebrow: the act name (or STEP) in ember, with a thin rule
        # running right (Adrian's coachmark mockup, 2026-07-19).
        if step is not None and total is not None:
            eyebrow = QtWidgets.QHBoxLayout()
            eyebrow.setSpacing(10)
            text = (f'{act} / {step} OF {total}' if act
                    else f'STEP {step} OF {total}')
            self.step_label = _mm.caps_label(text)
            self.step_label.setStyleSheet(_mm.COACH_EYEBROW_QSS)
            eyebrow.addWidget(self.step_label)
            eyebrow.addWidget(_mm.horizontal_rule(), 1)
            lay.addLayout(eyebrow)

        title_label = QtWidgets.QLabel(title)
        title_label.setStyleSheet(_mm.COACH_TITLE_QSS)
        lay.addWidget(title_label)

        body = QtWidgets.QLabel(body_text)
        body.setWordWrap(True)
        body.setStyleSheet(_mm.COACH_BODY_QSS)
        lay.addWidget(body)

        # Gif well. A missing or broken file degrades to a text-only
        # card, never a gap and never a raise: the clips ship as content
        # and content can be absent.
        has_gif = False
        if gif:
            try:
                fitted = fit_movie(gif, GIF_MAX_W, GIF_MAX_H)
            except Exception:
                fitted = None
            if fitted is not None:
                self._movie, gw, gh = fitted
                well = QtWidgets.QLabel()
                well.setStyleSheet('background: transparent;')
                well.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                well.setFixedSize(gw, gh)
                well.setMovie(self._movie)
                self._movie.start()
                lay.addWidget(well, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
                has_gif = True

        lay.addSpacing(4)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        if step is not None and total is not None:
            dots = ''.join(
                f'<span style="color:{t["plasma"] if i == step else t["iron_3"]};'
                f'">&#9679;</span>&nbsp;'
                for i in range(1, total + 1))
            dots_label = QtWidgets.QLabel(dots)
            dots_label.setStyleSheet(
                'background: transparent; font-size: 11px;')
            row.addWidget(dots_label)
        row.addStretch()
        skip = _mm.button('Skip', kind='ghost')
        skip.clicked.connect(lambda: self._close(self._on_skip))
        row.addWidget(skip)
        if self._probe is None:
            nxt = _mm.button(next_label, kind='primary')
            nxt.clicked.connect(lambda: self._close(self._on_next))
            row.addWidget(nxt)
        lay.addLayout(row)

        c.setFixedWidth(CARD_W_GIF if has_gif else CARD_W)

        self._poll = None
        if self._probe is not None:
            self._poll = QtCore.QTimer(c)
            self._poll.setInterval(PROBE_MS)
            self._poll.timeout.connect(self._check_probe)

        if advance_on_anchor_press and anchor_widget is not None:
            outer = self

            class _PressWatch(QtCore.QObject):
                def eventFilter(self, obj, event):
                    if event.type() == QtCore.QEvent.Type.MouseButtonPress:
                        # Defer: let the press reach the widget first
                        # (e.g. Settings opens its menu), then advance.
                        QtCore.QTimer.singleShot(
                            0, lambda: outer._close(outer._on_next))
                    return False        # never consume the press

            self._press_filter = _PressWatch(c)
            anchor_widget.installEventFilter(self._press_filter)

    def _check_probe(self):
        try:
            hit = bool(self._probe())
        except Exception:
            hit = False
        if hit:
            self._close(self._on_next)

    def _close(self, then):
        if self._closed:        # press + Next + probe can race; fire once
            return
        self._closed = True
        try:
            if self._poll is not None:
                self._poll.stop()
            if self._movie is not None:
                self._movie.stop()
                self._movie = None
            if self._press_filter is not None and self._anchor is not None:
                try:
                    self._anchor.removeEventFilter(self._press_filter)
                except RuntimeError:
                    pass
            self.card.close()
            self.card.deleteLater()
        finally:
            if then:
                then()

    def show(self):
        c = self.card
        c.adjustSize()
        anchor = self._anchor
        if anchor is not None:
            try:
                top = anchor.mapToGlobal(QtCore.QPoint(0, 0))
                screen = (QtGui.QGuiApplication.screenAt(top)
                          or QtGui.QGuiApplication.primaryScreen())
                r = screen.availableGeometry()
                below_y = top.y() + anchor.height() + 6
                c.notch_down = below_y + c.height() > r.bottom()
                center_x = top.x() + anchor.width() // 2
                # Clamp the card fully on-screen; the notch chases the
                # anchor instead of the card sliding off the edge (a
                # right-docked Settings button was pushing the whole
                # card off-screen, Adrian 2026-07-19).
                x = center_x - 40
                x = max(r.left() + 8, min(x, r.right() - c.width() - 8))
                c.notch_x = center_x - x
                y = (top.y() - c.height() - 6 if c.notch_down
                     else below_y)
                c.move(x, y)
            except RuntimeError:
                self._fallback_place()
        else:
            self._fallback_place()
        c.show()
        c.raise_()
        if self._poll is not None:
            self._poll.start()

    def _fallback_place(self):
        host = self.card.parent()
        if host is not None:
            geo = host.frameGeometry()
            self.card.move(geo.x() + geo.width() - self.card.width() - 40,
                           geo.y() + 90)


# Cards are top-level Tool windows; Qt parenting alone has let them be
# collected mid-tour, so every live card is also held here for the
# session. Tours append and never prune: a tour is a handful of cards
# and the window closing takes them with it.
_LIVE: list = []


def keep(card) -> None:
    """Hold a reference for the session (Qt parenting backup)."""
    _LIVE.append(card)
