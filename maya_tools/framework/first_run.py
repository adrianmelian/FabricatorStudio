# maya_tools/framework/first_run.py
"""First-run onboarding — the one-time first-welcome tour (Adrian,
2026-07-19; supersedes the two t34 moments).

A welcome gate plus four coachmark steps (styled per Adrian's
coachmark mockup, 2026-07-19), each chained off the previous card and
each individually skippable or absent without ending the tour:

WELCOME: centered branded card, the consent gate — Show Me Around or
   Skip. The tour's ONE seen-flag ('welcome_tour') is spent the moment
   this card shows, so a skipped tour is as final as a finished one
   and Maya closing mid-tour never re-nags.
1. FS BUTTON: coachmark at the Status Line button — "press it". No
   Next: it retires itself when the toolbar actually becomes visible
   (a 5x/s probe of toolbar_app.is_visible). Auto-skips when the
   toolbar is already open.
2. SETTINGS: coachmark at the toolbar's Settings entry; pressing the
   real button also advances.
3. ANNOTATIONS: coachmark at the Fabricator button plus a live pinned
   HoverAnnotation — teaches "hover any tool for its card".
4. PROJECT SETUP: the exit. Coachmark at the toolbar's Project Setup
   button; pressing it opens Project Setup and completes the tour —
   the last click is the first real action.

The tour fires from maya_startup.run(), which the installer's live
build also invokes — so the welcome appears seconds after Install
completes, in both copy and wire-only modes.

Decision logic is pure (pending_moments) and offscreen-testable; Maya
and Qt are touched only at the show seam (maybe_run), which is
batch-guarded and deferred so the Status Line and main window exist.
Legacy t34 flags ('project_prompt', 'toolbar_callout') are neither read
nor written: no public installs predate the tour.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

WELCOME_TOUR = 'welcome_tour'

_ONBOARDING_KEY = 'onboarding'          # dict inside toolbar prefs
_SETTLE_MS = 1800                       # let Maya's UI finish standing up


# ─────────────────────────────────────────────
# Decision core (pure — no Maya, no Qt)
# ─────────────────────────────────────────────

def pending_moments(prefs: dict) -> list:
    """The tour pends while its flag is unseen. Seen-flags live in
    prefs['onboarding'][<moment>] (missing = unseen)."""
    seen = (prefs.get(_ONBOARDING_KEY) or {}) if isinstance(prefs, dict) else {}
    if isinstance(seen, dict) and seen.get(WELCOME_TOUR):
        return []
    return [WELCOME_TOUR]


def mark_seen(moment: str) -> None:
    """Persist a moment's seen-flag (atomic prefs write)."""
    from maya_tools.framework.toolbar import toolbar_prefs
    prefs = toolbar_prefs.load_prefs()
    onboarding = dict(prefs.get(_ONBOARDING_KEY) or {})
    onboarding[moment] = True
    prefs[_ONBOARDING_KEY] = onboarding
    toolbar_prefs.save_prefs(prefs)


# ─────────────────────────────────────────────
# Cards (Qt — GUI sessions only)
# ─────────────────────────────────────────────

def _maya_main_window():
    try:
        from maya_tools.utils.maya.gui import get_maya_window
        return get_maya_window()
    except Exception:
        return None


class _WelcomeCard:
    """Tour stop 1 — the consent gate. Centered, branded, one warm
    sentence; Skip means skip (the flag is already spent at show time,
    so a skipped tour never returns)."""

    def __init__(self, on_tour=None, on_skip=None):
        from PySide6 import QtCore, QtWidgets
        from maya_tools.utils.qt.mindmeld import mindmeld_style

        self._on_tour = on_tour
        self._on_skip = on_skip
        self.dialog = QtWidgets.QDialog(_maya_main_window())
        d = self.dialog
        mindmeld_style.apply(d)
        d.setObjectName('FSFirstRunWelcome')
        d.setWindowFlags(QtCore.Qt.WindowType.Dialog
                         | QtCore.Qt.WindowType.FramelessWindowHint)
        d.setMinimumWidth(430)

        root = QtWidgets.QVBoxLayout(d)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        # Fullname banner (Adrian, 2026-07-19); text lockup fallback.
        banner = None
        try:
            from PySide6 import QtGui
            from maya_tools.framework.toolbar.widgets import icon_button
            path = icon_button._ICON_DIR / 'fs_fullname_banner.png'
            if path.is_file():
                pix = QtGui.QPixmap(str(path))
                if not pix.isNull():
                    banner = QtWidgets.QLabel()
                    banner.setPixmap(pix.scaledToWidth(
                        394, QtCore.Qt.TransformationMode.SmoothTransformation))
        except Exception:
            banner = None
        if banner is not None:
            root.addWidget(banner)
        else:
            brand_row = QtWidgets.QHBoxLayout()
            brand_row.setSpacing(10)
            brand_row.addWidget(mindmeld_style.brand_label('FabricatorStudio'))
            brand_row.addWidget(mindmeld_style.caps_label('// welcome'),
                                0, QtCore.Qt.AlignmentFlag.AlignBottom)
            brand_row.addStretch()
            root.addLayout(brand_row)
        root.addWidget(mindmeld_style.horizontal_rule())

        body = QtWidgets.QLabel(
            'Installed and ready. Would you like a quick tour?')
        body.setWordWrap(True)
        root.addWidget(body)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        skip = mindmeld_style.button('Skip', kind='ghost')
        tour = mindmeld_style.button('Show Me Around', kind='primary')
        buttons.addWidget(skip)
        buttons.addWidget(tour)
        root.addLayout(buttons)

        skip.clicked.connect(lambda: self._close(self._on_skip))
        tour.clicked.connect(lambda: self._close(self._on_tour))

    def _close(self, then):
        try:
            self.dialog.close()
            self.dialog.deleteLater()
        finally:
            if then:
                then()

    def show(self):
        from PySide6 import QtGui
        d = self.dialog
        d.adjustSize()
        host = d.parent()
        if host is not None:
            geo = host.frameGeometry()
            d.move(geo.x() + (geo.width() - d.width()) // 2,
                   geo.y() + int(geo.height() * 0.38))
        else:
            screen = QtGui.QGuiApplication.primaryScreen().availableGeometry()
            d.move(screen.center().x() - d.width() // 2,
                   screen.center().y() - d.height() // 2)
        d.show()
        d.raise_()


class _PointerCard:
    """A frameless card with a painted notch pointing at an anchor
    widget. Sits below the anchor (notch up) when there is room, flips
    above it (notch down) otherwise — the toolbar docks at any edge.
    Advances on its Next button, or via advance_probe: a zero-arg
    callable polled 5x/s; when it returns True the card retires itself
    and the tour moves on (stop 2's press-the-real-button mechanic —
    with a probe there is no Next button). advance_on_anchor_press=True
    also advances when the user clicks the pointed-at widget itself
    (the press still reaches the widget — doing the thing IS Next).
    Every card carries a quiet 'skip tour' link."""

    _NOTCH_H = 10
    _PROBE_MS = 200

    def __init__(self, anchor_widget, title, body_text,
                 on_next=None, on_skip=None, advance_probe=None,
                 advance_on_anchor_press=False, next_label='Next',
                 step=None, total=None):
        from PySide6 import QtCore, QtGui, QtWidgets
        from maya_tools.utils.qt.mindmeld import mindmeld_style

        self._on_next = on_next
        self._on_skip = on_skip
        self._anchor = anchor_widget
        self._probe = advance_probe
        self._notch_down = False        # decided in show()
        self._closed = False
        self._press_filter = None
        t = mindmeld_style.TOKENS
        radius = int(str(t.get('radius_md', '8px')).rstrip('px'))

        outer_self = self

        class _Card(QtWidgets.QWidget):
            notch_x = 40    # px from left edge; retargeted in show()

            def paintEvent(self, event):
                p = QtGui.QPainter(self)
                p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
                w, h, n = self.width(), self.height(), _PointerCard._NOTCH_H
                down = outer_self._notch_down
                body = (QtCore.QRectF(0.5, 0.5, w - 1, h - n - 1) if down
                        else QtCore.QRectF(0.5, n + 0.5, w - 1, h - n - 1))
                path = QtGui.QPainterPath()
                path.addRoundedRect(body, radius, radius)
                nx = max(radius + 10, min(self.notch_x, w - radius - 10))
                if down:
                    path.moveTo(nx - 8, h - n - 1)
                    path.lineTo(nx, h - 1)
                    path.lineTo(nx + 8, h - n - 1)
                else:
                    path.moveTo(nx - 8, n + 1)
                    path.lineTo(nx, 1)
                    path.lineTo(nx + 8, n + 1)
                path.closeSubpath()
                p.fillPath(path, QtGui.QColor(t['iron']))
                pen = QtGui.QPen(QtGui.QColor(t['plasma']))
                pen.setWidthF(1.0)
                p.setPen(pen)
                p.drawPath(path)
                p.end()

        self.card = _Card(_maya_main_window())
        c = self.card
        c.setWindowFlags(QtCore.Qt.WindowType.Tool
                         | QtCore.Qt.WindowType.FramelessWindowHint
                         | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        c.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

        lay = QtWidgets.QVBoxLayout(c)
        lay.setContentsMargins(18, self._NOTCH_H + 14, 18, self._NOTCH_H + 14)
        lay.setSpacing(8)

        # Eyebrow: STEP N OF M in ember, with a thin rule running right
        # (Adrian's coachmark mockup, 2026-07-19).
        if step is not None and total is not None:
            eyebrow = QtWidgets.QHBoxLayout()
            eyebrow.setSpacing(10)
            self.step_label = mindmeld_style.caps_label(
                f'STEP {step} OF {total}')
            self.step_label.setStyleSheet(mindmeld_style.COACH_EYEBROW_QSS)
            eyebrow.addWidget(self.step_label)
            eyebrow.addWidget(mindmeld_style.horizontal_rule(), 1)
            lay.addLayout(eyebrow)

        title_label = QtWidgets.QLabel(title)
        title_label.setStyleSheet(mindmeld_style.COACH_TITLE_QSS)
        lay.addWidget(title_label)

        body = QtWidgets.QLabel(body_text)
        body.setWordWrap(True)
        body.setStyleSheet(mindmeld_style.COACH_BODY_QSS)
        lay.addWidget(body)

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
        skip = mindmeld_style.button('Skip', kind='ghost')
        skip.clicked.connect(lambda: self._close(self._on_skip))
        row.addWidget(skip)
        if self._probe is None:
            nxt = mindmeld_style.button(next_label, kind='primary')
            nxt.clicked.connect(lambda: self._close(self._on_next))
            row.addWidget(nxt)
        lay.addLayout(row)

        c.setFixedWidth(320)

        self._poll = None
        if self._probe is not None:
            self._poll = QtCore.QTimer(c)
            self._poll.setInterval(self._PROBE_MS)
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
        from PySide6 import QtCore, QtGui
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
                self._notch_down = below_y + c.height() > r.bottom()
                center_x = top.x() + anchor.width() // 2
                # Clamp the card fully on-screen; the notch chases the
                # anchor instead of the card sliding off the edge (a
                # right-docked Settings button was pushing the whole
                # card off-screen, Adrian 2026-07-19).
                x = center_x - 40
                x = max(r.left() + 8, min(x, r.right() - c.width() - 8))
                c.notch_x = center_x - x    # instance attr shadows class
                y = (top.y() - c.height() - 6 if self._notch_down
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
        host = _maya_main_window()
        if host is not None:
            geo = host.frameGeometry()
            self.card.move(geo.x() + geo.width() - self.card.width() - 40,
                           geo.y() + 90)


class _Tour:
    """Four coachmark steps, each chained off the previous card and each
    skippable or absent without ending the tour (a missing anchor skips
    its stop, Skip ends everything). The exit is step 4: the Project
    Setup card — pressing the real button opens Project Setup and
    completes the tour (Adrian, 2026-07-19)."""

    _TOTAL = 4

    def __init__(self):
        self._anno = None

    # stop 2 — the FS Status Line button
    def start(self):
        try:
            from maya_tools.framework.toolbar import toolbar_app
            if toolbar_app.is_visible():
                self._stop_settings()      # never instruct a done action
                return
            anchor = _status_button_widget()
            if anchor is None:
                self._stop_settings()
                return
            card = _PointerCard(
                anchor, 'Your FabricatorStudio button',
                'Every tool lives behind this button. '
                'Press it to open the toolbar.',
                advance_probe=toolbar_app.is_visible,
                on_next=self._stop_settings_soon, on_skip=self._end,
                step=1, total=self._TOTAL)
            _live.append(card)
            card.show()
        except Exception:
            import traceback
            traceback.print_exc()

    def _stop_settings_soon(self):
        # Give the freshly launched strip a breath before pointing at it.
        from PySide6 import QtCore
        QtCore.QTimer.singleShot(400, self._stop_settings)

    # stop 3 — Settings
    def _stop_settings(self):
        try:
            from maya_tools.framework.toolbar import toolbar_app
            anchor = toolbar_app.widget_for('settings')
            if anchor is None:
                self._stop_annotations()
                return
            card = _PointerCard(
                anchor, 'Settings',
                'Dock side, scale, and Maya integration options live here.',
                on_next=self._stop_annotations, on_skip=self._end,
                advance_on_anchor_press=True,
                step=2, total=self._TOTAL)
            _live.append(card)
            card.show()
        except Exception:
            import traceback
            traceback.print_exc()

    # stop 4 — every tool explains itself (live annotation demo)
    def _stop_annotations(self):
        try:
            from maya_tools.framework.toolbar import toolbar_app
            anchor = toolbar_app.widget_for('fabricator')
            if anchor is None:
                self._stop_project()
                return
            try:
                from maya_tools.framework import annotations
                from maya_tools.utils.qt.hover_annotation import HoverAnnotation
                ann = annotations.for_tool('fabricator')
                if ann:
                    self._anno = HoverAnnotation()
                    self._anno.show_annotation(ann, anchor, pinned=True)
            except Exception:
                self._anno = None   # demo card is a bonus, never a gate
            card = _PointerCard(
                anchor, 'Every tool explains itself',
                'Hover any tool for a card like this one, with a summary '
                'and a demo.',
                on_next=self._after_annotations, on_skip=self._end,
                step=3, total=self._TOTAL)
            _live.append(card)
            card.show()
        except Exception:
            import traceback
            traceback.print_exc()

    def _after_annotations(self):
        self._hide_anno()
        self._stop_project_setup()

    # step 4 — Project Setup, the tour's exit
    def _stop_project_setup(self):
        try:
            from maya_tools.framework.toolbar import toolbar_app
            anchor = toolbar_app.widget_for('project_setup')
            if anchor is None:
                self._end()
                return
            card = _PointerCard(
                anchor, 'Project Setup',
                'Start here. Setting up a project unlocks export paths, '
                'FBX presets, and per-project session settings.',
                on_next=self._end, on_skip=self._end,
                # Pressing the real button opens Project Setup AND
                # completes the tour — the exit is the first real action.
                advance_on_anchor_press=True,
                next_label='Done',
                step=4, total=self._TOTAL)
            _live.append(card)
            card.show()
        except Exception:
            import traceback
            traceback.print_exc()

    def _hide_anno(self):
        if self._anno is not None:
            try:
                self._anno.hide_annotation()
            except Exception:
                pass
            self._anno = None

    def _end(self):
        self._hide_anno()


def _status_button_widget():
    """The FS Status Line button as a Qt widget, or None (button not
    installed / batch / lookup failure)."""
    try:
        from maya import OpenMayaUI as omui
        from shiboken6 import wrapInstance
        from PySide6 import QtWidgets
        from maya_tools.framework.toolbar import status_button
        ptr = omui.MQtUtil.findControl(status_button.BUTTON_NAME)
        if not ptr:
            return None
        return wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception:
        return None


# ─────────────────────────────────────────────
# The startup seam
# ─────────────────────────────────────────────

_live = []   # keep card refs alive for the session (Qt parenting backup)


def maybe_run() -> None:
    """Show the first-welcome tour if it is pending, once, after the UI
    settles. Called from maya_startup.run() — which the installer's live
    build also invokes, so the welcome fires seconds after Install
    completes. Batch-guarded; every failure is swallowed (onboarding
    must never wound a startup or an install)."""
    try:
        import maya.cmds as cmds
        if cmds.about(batch=True):
            return
        from maya_tools.framework.toolbar import toolbar_prefs
        moments = pending_moments(toolbar_prefs.load_prefs())
        if not moments:
            return

        from PySide6 import QtCore
        QtCore.QTimer.singleShot(_SETTLE_MS, lambda: _show_chain(moments))
    except Exception:
        import traceback
        traceback.print_exc()


def _show_chain(moments: list) -> None:
    """Start the tour. The flag is spent AT SHOW TIME — one-time means
    one-time even if Maya closes with a card still up, and Skip on the
    welcome card is as final as finishing."""
    try:
        if WELCOME_TOUR not in moments:
            return
        mark_seen(WELCOME_TOUR)
        tour = _Tour()
        card = _WelcomeCard(on_tour=tour.start, on_skip=tour._end)
        _live.append(card)
        card.show()
    except Exception:
        import traceback
        traceback.print_exc()
