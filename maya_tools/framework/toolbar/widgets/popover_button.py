"""PopoverButton — anchored, MOVABLE popover panel (spec 3.2 + CP-B/CP-C).

CP-B (Adrian, 2026-07-02): panels are Animo-style frameless tool windows —
a slim drag bar with a close button, movable anywhere, and screen-aware
placement (opens ABOVE the button when the strip sits at the bottom of the
screen; never off-screen). The content factory runs lazily on every open
(fresh selection-dependent content, no stale popovers).

CP-C (Adrian, 2026-07-02): HOVER is the default trigger — sustained hover
(~350ms) opens every panel.

Trigger law (Adrian, 2026-07-05): popovers that ARE full GUI tools —
Renamer, Export, Curve-O-Matic, the pose/anim Library today;
Fabricator when it gets the treatment — open on CLICK only
(trigger='click'). Option/action panels (Create, Constraints, Select,
Mirror, Skeleton, Skin, Scene, ...) keep the hover trigger. A tool
appearing under your cursor uninvited is noise; options volunteering
on hover is the point.

Close model (Adrian, 2026-07-03 brain-dump — items 1 & 2):
  * A HOVER-opened panel is PROVISIONAL: it closes when the user clicks
    OUTSIDE it (not on pointer-leave — that leave-close was twitchy).
  * The panel becomes COMMITTED the moment the user commits to it:
    pressing the toolbar button to open it, focusing a field inside it, or
    dragging it. A committed panel closes ONLY via its X (or its owner
    button reopening it). Committed panels are exempt from the
    one-at-a-time rule, so several can float at once.
  * Pressing the toolbar button ALWAYS launches a fresh panel — never a
    toggle-close. A hover panel that opened a beat earlier is replaced,
    not dismissed, so a press can never race the 350ms timer shut.
  * Smoosh keeps its split: click fires the command, hover tunes."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, QPoint, QEvent, QObject
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QStyle, QVBoxLayout, QWidget)

from maya_tools.framework.toolbar import dpi
from maya_tools.framework.toolbar.widgets.icon_button import IconButton

# CP-B (Adrian, 2026-07-02): ONE popover at a time - opening any popover
# closes whichever other one is open. CP-C verdict (2026-07-02) + 2026-07-03:
# COMMITTED panels are EXEMPT - committing releases a panel from this
# registry, so it floats free until its own X (or its owner reopening it).
_OPEN_OWNER: "PopoverButton | None" = None


def _claim_open(owner: "PopoverButton") -> None:
    global _OPEN_OWNER
    if _OPEN_OWNER is not None and _OPEN_OWNER is not owner:
        try:
            _OPEN_OWNER.close_popover()
        except RuntimeError:
            pass                      # owner already deleted with its strip
    _OPEN_OWNER = owner


def _release_open(owner: "PopoverButton") -> None:
    global _OPEN_OWNER
    if _OPEN_OWNER is owner:
        _OPEN_OWNER = None


# Edit-mode switch (Phase D drag-to-reorder): while True, hover NEVER opens
# a popover - the 350ms timer must not fire mid-edit. Module-level on
# purpose: one flag covers every button on the strip. TWO layers: enterEvent
# refuses to ARM the timer, and _open_popover carries a final gate - a timer
# armed BEFORE suppression flips on still fires, and only the gate stops it.
# Click-open paths are separately dead in edit mode (the reorder filter
# consumes the clicks). REMEMBER: _hover_timer only exists when trigger ==
# "hover" - the enterEvent check lives inside the existing trigger guard.
_HOVER_SUPPRESSED = False

# Sustained-hover delay (ms): the SAME value opens a provisional panel and,
# after the pointer leaves, closes it (Adrian, 2026-07-03 - symmetric grace).
_HOVER_DELAY_MS = 350


def set_hover_suppressed(flag: bool) -> None:
    global _HOVER_SUPPRESSED
    _HOVER_SUPPRESSED = bool(flag)


def hover_suppressed() -> bool:
    return _HOVER_SUPPRESSED


class _OutsideDismisser(QObject):
    """A single, permanent QApplication event filter that closes the one
    PROVISIONAL popover on a click outside it (item 1, 2026-07-03). ONE
    long-lived filter object - never one-per-frame - so a destroyed frame
    can never leave a dangling filter pointer for Qt to crash on. Only one
    provisional panel exists at a time (one-at-a-time + committed panels
    leave the registry), so tracking a single frame is enough."""
    _instance: "_OutsideDismisser | None" = None

    def __init__(self):
        super().__init__()
        self._frame: "_PopoverFrame | None" = None

    @classmethod
    def instance(cls) -> "_OutsideDismisser":
        if cls._instance is None:
            cls._instance = cls()
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(cls._instance)
        return cls._instance

    def watch(self, frame: "_PopoverFrame") -> None:
        self._frame = frame

    def unwatch(self, frame: "_PopoverFrame") -> None:
        if self._frame is frame:
            self._frame = None

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.MouseButtonPress
                and self._frame is not None):
            from shiboken6 import isValid
            if not isValid(self._frame):
                # Frame's C++ object is gone (destroyed without unwatch) -
                # NEVER dereference it: shiboken may have reused the memory,
                # so a bare method call would hit the wrong object. Drop it.
                self._frame = None
                return False
            try:
                gp = event.globalPosition().toPoint()
            except AttributeError:
                return False              # not a positioned mouse event
            try:
                self._frame._dismiss_if_outside(gp)
            except RuntimeError:
                self._frame = None        # frame died mid-dispatch; drop it
        return False                      # never consume the press


class PopoverButton(IconButton):
    def __init__(self, glyph: str, tooltip: str = "",
                 popover_factory: Optional[Callable[[], QWidget]] = None,
                 trigger: str = "hover",
                 command: Optional[Callable[[], object]] = None,
                 menu: Optional[list[tuple[str, Callable]]] = None,
                 icon: Optional[str] = None,
                 title: Optional[str] = None,
                 can_open: Optional[Callable[[], bool]] = None,
                 width: Optional[int] = None,
                 height: Optional[int] = None, parent=None):
        # hover-trigger (DEFAULT, CP-C): hover opens; click launches fresh +
        # commits when there is no command, else click fires `command`
        # (Smoosh). click-trigger (opt-in): click launches; no hover.
        if trigger not in ("click", "hover"):
            raise ValueError(f"PopoverButton trigger {trigger!r}")
        # menu: optional static right-click rows riding the IconButton seam
        # (B24), same as Toggle. First user: the library button's Mirror Pose
        # (Adrian, 2026-07-18). Left/hover popover behavior is untouched.
        kwargs = {"tooltip": tooltip,
                  "command": command if trigger == "hover" else None,
                  "menu": menu,
                  "width": width, "height": height,
                  "parent": parent}
        if icon is not None:
            kwargs["icon"] = icon
        super().__init__(glyph, **kwargs)
        self._factory = popover_factory
        self._popover: _PopoverFrame | None = None
        self._trigger = trigger
        # Optional gate (item 4): when set and it returns False, the popover
        # never opens (hover or command-less click). The click COMMAND, if
        # any, still fires - the Fabricator window stays reachable even when
        # its Build/Unbuild panel is suppressed.
        self._can_open = can_open
        # Panel drag-bar title. Item 4 (2026-07-03) strips the hover tooltip
        # off popover buttons in the manifest, so `title` is the clean short
        # name; fall back to the (now usually empty) tooltip, then the glyph.
        self._title = title or tooltip or glyph
        if trigger == "click" or command is None:
            # command-less popover buttons: click ALWAYS launches fresh +
            # commits (item 2). Never a toggle-close - X is the close.
            self.clicked.connect(self._on_popover_clicked)
        if trigger == "hover":
            # Decision B18: sustained hover (~350ms) opens (provisional).
            from PySide6.QtCore import QTimer
            self._hover_timer = QTimer(self)
            self._hover_timer.setSingleShot(True)
            self._hover_timer.setInterval(_HOVER_DELAY_MS)
            self._hover_timer.timeout.connect(self._open_popover)

    def enterEvent(self, event):
        # Back on the button: cancel a pending leave-close of its own panel
        # (moving button <-> panel must not dismiss a provisional popover).
        if self._popover is not None:
            self._popover.cancel_close()
        if (self._trigger == "hover" and not _HOVER_SUPPRESSED
                and not self.is_open()):
            self._hover_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._trigger == "hover":
            self._hover_timer.stop()
        # Leaving the button arms the provisional panel's delayed close (the
        # panel's own leaveEvent covers leaving the panel); moving onto the
        # panel cancels it via the panel's enterEvent.
        if self._popover is not None:
            self._popover.arm_close()
        super().leaveEvent(event)

    def is_open(self) -> bool:
        return self._popover is not None and self._popover.isVisible()

    def close_popover(self) -> None:
        if self._popover is not None:
            self._popover.close()
            # Detach from this button (the frame's Qt parent) BEFORE
            # deleteLater: otherwise the frame has TWO death paths - the
            # queued DeferredDelete and its parent's destructor when this
            # button is freed - and if the button is garbage-collected while
            # the DeferredDelete is still queued the frame is torn down twice
            # (offscreen: exit 127/139). setParent(None) makes deleteLater the
            # sole owner of the teardown.
            self._popover.setParent(None)
            self._popover.deleteLater()
            self._popover = None
        _release_open(self)

    def _on_popover_clicked(self) -> None:
        # The button always launches a FRESH panel (item 2) - never a toggle-
        # close - but no longer COMMITS it (Adrian, 2026-07-03: dropped the
        # press-to-persist rule). The panel opens provisional; only giving it
        # focus (or dragging it) commits it to X-only. So a press can't race
        # the 350ms timer shut, yet a stray press doesn't pin the panel open.
        self._open_popover(commit=False)

    def _open_popover(self, commit: bool = False) -> None:
        if _HOVER_SUPPRESSED:              # final gate: a hover timer armed
            return                         # BEFORE suppression still fires;
                                           # every open path is dead mid-edit
        if self._can_open is not None and not self._can_open():
            return                         # item 4: contextually suppressed
        if self._popover is not None:      # reap any closed-but-referenced frame
            self.close_popover()
        if self._factory is None:
            return
        _claim_open(self)                  # one popover at a time
        frame = _PopoverFrame(self, title=self._title)
        frame.set_content(self._factory())
        frame.move(self._placement(frame))
        frame.show()
        self._popover = frame
        if commit:
            # Button-open (item 1): committed immediately -> X-only close,
            # exempt from one-at-a-time.
            frame._set_pinned()
        else:
            # Hover-open: provisional -> a click outside dismisses it.
            _OutsideDismisser.instance().watch(frame)

    def _placement(self, frame: "_PopoverFrame") -> QPoint:
        """Below the button when it fits, ABOVE when the strip is at the
        bottom of the screen; always clamped inside the available screen
        (CP-B round 2: long panels were diving off-screen)."""
        frame.adjustSize()
        size = frame.sizeHint()
        screen = (self.screen() or frame.screen()).availableGeometry()
        below = self.mapToGlobal(QPoint(0, self.height() + 2))
        x = min(max(below.x(), screen.left()), screen.right() - size.width())
        y = below.y()
        if y + size.height() > screen.bottom():
            y = self.mapToGlobal(QPoint(0, 0)).y() - size.height() - 2
        y = max(y, screen.top())
        return QPoint(x, y)


def _expand_to_full(opener, owner) -> None:
    """Expand a mini popover to its full tool window: read the panel's live
    state (e.g. Renamer's current tab) FIRST, then dismiss the popover.
    Module-level (not a frame method) so the expand button's connection never
    has to close over the frame - see the cycle note in set_content."""
    try:
        opener()
    finally:
        owner.close_popover()


class _PopoverFrame(QFrame):
    """Frameless movable panel (Animo launcher pattern): slim drag bar with
    an X close and (when the panel has a bigger tool) an EXPAND button that
    opens the full window. COMMITTING a provisional panel stops its click-
    outside dismissal: dragging it, any click inside, or focus landing on a
    child field commits it — typing in the Renamer must never lose its panel
    to a stray mouse."""

    def __init__(self, owner: PopoverButton, title: str = ""):
        super().__init__(owner, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName("fabricator_toolbar_popover")
        self._owner = owner
        self._pinned = False              # "committed" (see class doc / item 1)
        self._drag_offset: QPoint | None = None
        # Delayed leave-close (Adrian, 2026-07-03): a provisional panel closes
        # when the pointer leaves it AND the owner button for _HOVER_DELAY_MS -
        # the same grace as the open. enter cancels, leave (re)arms; committed
        # panels never arm it (X only).
        from PySide6.QtCore import QTimer
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.setInterval(_HOVER_DELAY_MS)
        self._close_timer.timeout.connect(self._on_close_timeout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 6)
        outer.setSpacing(4)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setObjectName("fabricator_popover_title")
        bar.addWidget(label, 1)
        # X, not × (U+00D7): the display font lacks the multiply glyph, so the
        # old button read empty (Adrian, 2026-07-03). Plain ASCII X renders.
        close_btn = QPushButton("X")
        close_btn.setObjectName("fabricator_popover_close")
        close_btn.setFixedSize(dpi.scaled(26), dpi.scaled(20))   # easier hit target
        close_btn.clicked.connect(self._owner.close_popover)
        bar.addWidget(close_btn)
        self._bar = bar
        outer.addLayout(bar)
        self._content_slot = outer

        # Commit when focus lands anywhere inside the panel (field click,
        # tab-in). Disconnected in closeEvent; the guard tolerates the
        # app-level signal outliving a half-dead frame.
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)

    def set_content(self, widget: QWidget) -> None:
        self._content_slot.addWidget(widget)
        # Item 3 (2026-07-03): popovers are mini-tools; when the content
        # exposes open_full_window(), add an EXPAND button to the drag bar
        # that opens the real window. Standard icon, not a text glyph, so it
        # can never read empty the way × did. Sits left of the X.
        opener = getattr(widget, "open_full_window", None)
        if callable(opener):
            expand_btn = QPushButton()
            expand_btn.setObjectName("fabricator_popover_expand")
            expand_btn.setFixedSize(dpi.scaled(26), dpi.scaled(20))
            expand_btn.setToolTip("Open the full tool window")
            expand_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton))
            # Capture the OWNER button + opener, NOT self (the frame). A lambda
            # that closed over `self` kept the frame's Python wrapper alive in a
            # reference cycle (frame -> expand_btn -> clicked -> lambda -> self),
            # so after the frame's C++ object was destroyed via its parent
            # button, gc collecting that cycle touched freed memory and crashed
            # (offscreen suite, exit 127/139). The owner button is the frame's
            # parent, not a back-reference, so closing over it breaks the cycle.
            owner = self._owner
            expand_btn.clicked.connect(
                lambda *_, o=opener, w=owner: _expand_to_full(o, w))
            self._bar.insertWidget(1, expand_btn)   # after the title, before X

    # --- committed vs provisional -------------------------------------------
    def _set_pinned(self) -> None:
        # "Commit" the panel (CP-C verdict + 2026-07-03 item 1): a committed
        # panel stays up while other popovers come and go and closes only via
        # its X or its owner reopening it. Committing also stops the
        # click-outside dismissal (unwatch) - a committed panel is X-only.
        if not self._pinned:
            self._pinned = True
            _release_open(self._owner)
        _OutsideDismisser.instance().unwatch(self)
        self._close_timer.stop()          # committed: X only, no leave-close

    def _dismiss_if_outside(self, gp: QPoint) -> None:
        if self._pinned:
            return                        # committed: X-only
        if self.frameGeometry().contains(gp):
            return                        # inside the panel -> mousePress commits
        owner = self._owner
        try:
            if owner.rect().contains(owner.mapFromGlobal(gp)):
                return                    # on the owner button -> it relaunches
        except RuntimeError:
            pass                          # owner gone with its strip
        self._owner.close_popover()

    def _on_focus_changed(self, _old, new) -> None:
        try:
            if new is not None and self.isAncestorOf(new):
                self._set_pinned()
        except RuntimeError:
            pass                        # frame already deleted under the signal

    def closeEvent(self, event):
        _OutsideDismisser.instance().unwatch(self)
        app = QApplication.instance()
        if app is not None:
            try:
                app.focusChanged.disconnect(self._on_focus_changed)
            except (RuntimeError, TypeError):
                pass                    # already disconnected / app going down
        super().closeEvent(event)

    # --- drag-to-move (press anywhere that isn't a child button) ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            self._set_pinned()             # touching the panel = commit it
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._set_pinned()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    # --- delayed leave-close (provisional panels) ---------------------------
    def arm_close(self) -> None:
        if not self._pinned:
            self._close_timer.start()

    def cancel_close(self) -> None:
        self._close_timer.stop()

    def _on_close_timeout(self) -> None:
        if self._pinned:
            return
        # Guard against a missed enter: if the pointer is back over the panel
        # or its owner button, keep it (belt-and-suspenders over enter/leave).
        from PySide6.QtGui import QCursor
        gp = QCursor.pos()
        if self.frameGeometry().contains(gp):
            return
        try:
            if self._owner.rect().contains(self._owner.mapFromGlobal(gp)):
                return
        except RuntimeError:
            pass
        self._owner.close_popover()

    def enterEvent(self, event):
        self.cancel_close()               # pointer back on the panel: keep it
        super().enterEvent(event)

    def leaveEvent(self, event):
        # 2026-07-03: a provisional panel closes on leave after a grace delay
        # (symmetric with the open) - not instantly (that was the twitchy
        # behavior) - AND still on a click outside. Committed panels: X only.
        self.arm_close()
        super().leaveEvent(event)
