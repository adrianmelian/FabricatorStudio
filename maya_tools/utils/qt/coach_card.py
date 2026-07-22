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

`media` renders a clip OR a still under the body text through
load_media, the same scale-to-fit-never-upscale path hover_annotation
uses. Not everything worth showing moves: a still of a panel beats a
clip of a cursor wandering across it.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from maya_tools.utils.qt.mindmeld import mindmeld_style as _mm


NOTCH_H = 10
PROBE_MS = 200
CARD_W = 320            # text-only default (the install tour's width)

# Gif well. 420x378 is the house capture size (Adrian, 2026-07-21), and
# the caps match it EXACTLY so a clip shot to spec renders 1:1 with no
# resampling at all. HoverAnnotation caps to the same box, so one file
# looks identical on a tour card and on a hover card.
#
# The height is generous on purpose: half these clips frame a tall Maya
# panel (the Rig Outliner, the Components list), and a landscape-only cap
# would letterbox them down to a stamp. A landscape clip is unaffected,
# it stays width-bound.
GIF_MAX_W = 420
GIF_MAX_H = 378
CARD_W_GIF = GIF_MAX_W + 36     # 18px margin either side of a full-width gif
BANNER_MAX_H = 64               # brand wordmark header on the bookend cards


def fit_size(w: int, h: int, max_w: int, max_h: int):
    """(w, h) scaled to fit the caps, aspect-preserved, NEVER upscaled.

    Pure, so the never-upscale and exact-fit contracts are testable
    without an encoder to hand.
    """
    if w <= 0 or h <= 0:
        return None
    scale = min(max_w / float(w), max_h / float(h), 1.0)
    return int(w * scale), int(h * scale)


# Above this frame count a clip is decoded on the fly instead of held in
# memory. CacheAll on a 420x378 clip costs ~635 KB per frame decoded, so a
# long one runs into tens of MB sitting behind a tooltip. Short clips keep
# the cache, since that is the smoother playback.
_CACHE_FRAME_LIMIT = 80


def load_media(path: str, max_w: int, max_h: int):
    """(kind, obj, w, h) fitted per fit_size, or None when the path is
    empty / unreadable / zero-sized.

    kind is 'movie' (QMovie, animated) or 'pixmap' (QPixmap, static).
    Not everything worth putting on a card moves: a still of a panel
    says what the panel holds better than a clip of a cursor wandering
    over it (Adrian shipped PNGs for exactly that, 2026-07-21). Format
    follows the file, so .gif / .webp / .png all just work.

    Single media loader for the whole Qt layer: HoverAnnotation and
    CoachCard both come through here, so the two cards can never drift
    on scaling, animation detection, or cache policy.
    """
    if not path:
        return None
    reader = QtGui.QImageReader(path)
    if not reader.canRead():
        return None
    size = reader.size()
    fitted = fit_size(size.width(), size.height(), max_w, max_h)
    if fitted is None:
        return None
    dw, dh = fitted

    # A static PNG through QMovie reports frameCount() == 0 and renders
    # nothing, so animation support is checked rather than assumed.
    frames = reader.imageCount()
    if reader.supportsAnimation() and frames > 1:
        movie = QtGui.QMovie(path)
        movie.setCacheMode(QtGui.QMovie.CacheAll if frames <= _CACHE_FRAME_LIMIT
                           else QtGui.QMovie.CacheNone)
        movie.setScaledSize(QtCore.QSize(dw, dh))
        return 'movie', movie, dw, dh

    pix = QtGui.QPixmap(path)
    if pix.isNull():
        return None
    return ('pixmap',
            pix.scaled(dw, dh, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                       QtCore.Qt.TransformationMode.SmoothTransformation),
            dw, dh)


class _NotchFrame(QtWidgets.QWidget):
    """The card body: a rounded iron rect with a plasma outline and one
    triangular notch on whichever edge faces the anchor.

    `notch_side` is 'top' | 'bottom' | 'left' | 'right' | 'none', and
    `notch_pos` is a px offset along that edge from the card's origin,
    both set by CoachCard.show(). 'none' draws a plain card: when the
    card has had to be pushed away from its anchor to stay on-screen, a
    notch would point at nothing, and a pointer aimed at the wrong thing
    is worse than no pointer.
    """

    _SIDES = ('top', 'bottom', 'left', 'right', 'none')

    def __init__(self, radius: int, parent=None):
        super().__init__(parent)
        self._radius = radius
        self.notch_pos = 40
        self.notch_side = 'top'

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        w, h, n, r = self.width(), self.height(), NOTCH_H, self._radius
        side = self.notch_side

        # The body insets by the notch depth on the notched edge only, so
        # the triangle has room to sit outside it.
        li = n if side == 'left' else 0
        ti = n if side == 'top' else 0
        ri = n if side == 'right' else 0
        bi = n if side == 'bottom' else 0
        body = QtCore.QRectF(li + 0.5, ti + 0.5,
                             w - li - ri - 1, h - ti - bi - 1)
        path = QtGui.QPainterPath()
        path.addRoundedRect(body, r, r)

        if side in ('top', 'bottom'):
            nx = max(r + 10, min(self.notch_pos, w - r - 10))
            if side == 'top':
                path.moveTo(nx - 8, n + 1); path.lineTo(nx, 1)
                path.lineTo(nx + 8, n + 1)
            else:
                path.moveTo(nx - 8, h - n - 1); path.lineTo(nx, h - 1)
                path.lineTo(nx + 8, h - n - 1)
            path.closeSubpath()
        elif side in ('left', 'right'):
            ny = max(r + 10, min(self.notch_pos, h - r - 10))
            if side == 'left':
                path.moveTo(n + 1, ny - 8); path.lineTo(1, ny)
                path.lineTo(n + 1, ny + 8)
            else:
                path.moveTo(w - n - 1, ny - 8); path.lineTo(w - 1, ny)
                path.lineTo(w - n - 1, ny + 8)
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
                 step=None, total=None, act=None, media='', banner='',
                 centered=False, anchor_rect=None, hint='',
                 parent=None):
        self._on_next = on_next
        self._on_skip = on_skip
        self._anchor = anchor_widget
        self._anchor_rect = anchor_rect
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

        self._centered = bool(centered)

        lay = QtWidgets.QVBoxLayout(c)
        lay.setContentsMargins(18, NOTCH_H + 14, 18, NOTCH_H + 14)
        lay.setSpacing(8)

        # Brand banner ABOVE everything — the tour's bookend cards (the
        # welcome gate, the outro) lead with the Fabricator wordmark
        # rather than a title, same as HoverAnnotation's About card.
        has_banner = False
        if banner:
            try:
                loaded = load_media(banner, GIF_MAX_W, BANNER_MAX_H)
            except Exception:
                loaded = None
            if loaded is not None:
                _, obj, bw, bh = loaded
                lbl = QtWidgets.QLabel()
                lbl.setStyleSheet('background: transparent;')
                lbl.setFixedSize(bw, bh)
                if isinstance(obj, QtGui.QPixmap):
                    lbl.setPixmap(obj)
                else:
                    lbl.setMovie(obj)
                    obj.start()
                lay.addWidget(lbl, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
                has_banner = True

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
        title_label.setVisible(bool(title))
        lay.addWidget(title_label)

        body = QtWidgets.QLabel(body_text)
        body.setWordWrap(True)
        body.setStyleSheet(_mm.COACH_BODY_QSS)
        lay.addWidget(body)

        # Media well — an animated clip or a still, whichever the file is.
        # A missing or broken file degrades to a text-only card, never a
        # gap and never a raise: these ship as content, and content can
        # be absent.
        has_media = False
        if media:
            try:
                loaded = load_media(media, GIF_MAX_W, GIF_MAX_H)
            except Exception:
                loaded = None
            if loaded is not None:
                kind, obj, gw, gh = loaded
                well = QtWidgets.QLabel()
                well.setStyleSheet('background: transparent;')
                well.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                well.setFixedSize(gw, gh)
                if kind == 'movie':
                    self._movie = obj
                    well.setMovie(obj)
                    obj.start()
                else:
                    well.setPixmap(obj)
                lay.addWidget(well, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
                has_media = True

        # What advances this card, spelled out. A probe step has no Next
        # button by design, which leaves Skip as the only thing that
        # LOOKS clickable — users read that as "skip or nothing" and miss
        # that the card is waiting on them (Adrian, 2026-07-21). Plasma,
        # because it names the action.
        if hint:
            hint_label = QtWidgets.QLabel(hint)
            hint_label.setWordWrap(True)
            hint_label.setStyleSheet(
                f"color: {t['plasma']}; background: transparent; "
                f"font-size: 12px; font-weight: 700;")
            lay.addSpacing(2)
            lay.addWidget(hint_label)

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

        c.setFixedWidth(CARD_W_GIF if (has_media or has_banner) else CARD_W)

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

    _GAP = 6            # breathing room between anchor edge and card

    def show(self):
        c = self.card
        c.adjustSize()
        anchor = self._anchor
        if self._centered or anchor is None:
            self._center_on_parent()
        else:
            try:
                self._place_against(anchor)
            except RuntimeError:        # anchor died between build and show
                self._center_on_parent()
        c.show()
        c.raise_()
        if self._poll is not None:
            self._poll.start()

    def _place_against(self, anchor) -> None:
        """Put the card beside the anchor, fully on-screen, always.

        Tries below, above, right, then left, taking the first side the
        card fits on WHOLE. Anything else is a bug the user experiences
        as a card they cannot read: pointing at a tall widget (a
        Fabricator panel) used to flip the card above the anchor and off
        the top of the screen, because only the horizontal axis was ever
        clamped (Adrian, 2026-07-21).

        If no side fits, the card is clamped into the screen and drops
        its notch rather than aiming it at nothing.
        """
        c = self.card
        w, h = c.width(), c.height()
        g = self._GAP
        # An explicit rect points at a REGION inside the widget (a tree
        # section, a row) instead of the widget's whole box. Pointing at
        # a full-height panel aims the notch at its middle, nowhere near
        # the rows the card is describing.
        rect = self._anchor_rect
        if rect is not None:
            origin = anchor.mapToGlobal(rect.topLeft())
            aw, ah = rect.width(), rect.height()
        else:
            origin = anchor.mapToGlobal(QtCore.QPoint(0, 0))
            aw, ah = anchor.width(), anchor.height()
        ax, ay = origin.x(), origin.y()
        screen = (QtGui.QGuiApplication.screenAt(origin)
                  or QtGui.QGuiApplication.primaryScreen())
        r = screen.availableGeometry()
        cx, cy = ax + aw // 2, ay + ah // 2

        def clamp(v, lo, hi):
            return max(lo, min(v, hi))

        # (side the NOTCH sits on, fixed coord, is_vertical_placement)
        for side, fixed, vertical in (
                ('top',    ay + ah + g, True),    # card below the anchor
                ('bottom', ay - h - g,  True),    # card above the anchor
                ('left',   ax + aw + g, False),   # card right of the anchor
                ('right',  ax - w - g,  False)):  # card left of the anchor
            if vertical:
                if fixed < r.top() or fixed + h > r.bottom():
                    continue
                x = clamp(cx - 40, r.left() + 8, r.right() - w - 8)
                c.notch_side, c.notch_pos = side, cx - x
                c.move(x, fixed)
                return
            if fixed < r.left() or fixed + w > r.right():
                continue
            y = clamp(cy - 40, r.top() + 8, r.bottom() - h - 8)
            c.notch_side, c.notch_pos = side, cy - y
            c.move(fixed, y)
            return

        # Nothing fits cleanly (a card taller than the screen, a fully
        # covered anchor). Stay readable and stop pretending to point.
        c.notch_side = 'none'
        c.move(clamp(cx - w // 2, r.left() + 8, r.right() - w - 8),
               clamp(cy - h // 2, r.top() + 8, r.bottom() - h - 8))

    def _center_on_parent(self):
        """Anchorless cards (the welcome gate, the outro) sit centered on
        the host window, a little above middle so they read as a
        statement rather than a dialog. Clamped like every other
        placement: nothing ever lands off-screen."""
        c = self.card
        c.notch_side = 'none'
        host = c.parent()
        w, h = c.width(), c.height()
        if host is not None:
            geo = host.frameGeometry()
            x = geo.x() + (geo.width() - w) // 2
            y = geo.y() + int(geo.height() * 0.30)
        else:
            geo = QtGui.QGuiApplication.primaryScreen().availableGeometry()
            x = geo.center().x() - w // 2
            y = geo.center().y() - h // 2
        screen = (QtGui.QGuiApplication.screenAt(QtCore.QPoint(x, y))
                  or QtGui.QGuiApplication.primaryScreen())
        r = screen.availableGeometry()
        c.move(max(r.left() + 8, min(x, r.right() - w - 8)),
               max(r.top() + 8, min(y, r.bottom() - h - 8)))


# Cards are top-level Tool windows; Qt parenting alone has let them be
# collected mid-tour, so every live card is also held here for the
# session. Tours append and never prune: a tour is a handful of cards
# and the window closing takes them with it.
_LIVE: list = []


def keep(card) -> None:
    """Hold a reference for the session (Qt parenting backup)."""
    _LIVE.append(card)
