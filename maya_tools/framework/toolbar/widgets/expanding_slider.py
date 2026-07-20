"""ExpandingSlider — a compact slider at rest that widens when clicked into
(spec section 3.2; the Animo/AnimBot signature, Animo_Launcher.py:1506).

Rework (Adrian, 2026-07-03): it now READS as a slider even when idle — a
narrow groove with its notches — and expands to full width the moment you
click or tab into it, contracting again when you release or focus leaves.

`center_return=True` makes it a spring-loaded jog (the true AnimBot feel):
the handle rests at track center, drag right/left for a live relative value,
and on release the handle snaps back to center. A small pip above the handle
shows the live integer while you drag.

Phase B (decisions B15/B16): one undo chunk per GESTURE — the app layer
passes `gesture_begin`/`gesture_end` callables that open/close the chunk
at expand/collapse; `live=True` fires the command on every drag tick
inside that chunk; the spring-return reset never re-fires the command
(the guarded valueChanged is inert while idle)."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPropertyAnimation,
                            QTimer, Qt, Signal)
from PySide6.QtWidgets import (QAbstractSpinBox, QFrame, QHBoxLayout, QLabel,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

from maya_tools.framework.toolbar import dpi
from maya_tools.utils.qt.mindmeld import mindmeld_style

_STEPS = 1000

COLLAPSED_W = 110       # idle slider width, px pre-DPI (reads as a mini-slider)
EXPANDED_W = 220        # width once clicked/focused into
_EXPAND_MS = 130        # expand/contract animation duration
_HANDLE_W = 22          # handle width, px pre-DPI (fits the IN/OS label)


class _NotchedSlider(QSlider):
    """QSlider that PAINTS its own tick marks and its handle LABEL: the mindmeld
    QSS styles the groove, and a stylesheet-styled slider suppresses Qt's
    native ticks (Adrian, 2026-07-03 - they never showed). The handle itself is
    the REAL QSS handle - just enlarged and recolored via a per-instance rule
    (`handle_color`) so there's exactly one handle; paintEvent only stamps a
    short `handle_label` ('IN'/'OS') onto it, positioned by the same value->x
    math the handle rides, so the text tracks it (Adrian, 2026-07-03: an
    overpainted second handle read slim and lagged - gone)."""
    _TRAVEL_MARGIN = 6                          # px inset the handle center travels within

    def __init__(self, orientation, notches: int = 0, handle_label: str = "",
                 handle_color: str = "", parent=None):
        super().__init__(orientation, parent)
        self._notches = int(notches)
        self._handle_label = handle_label
        self._handle_color = handle_color
        # Enlarge (easy grab, room for the label) and, when asked, recolor the
        # ACTUAL handle - no second handle is drawn.
        css = ("QSlider::handle:horizontal { width: %dpx; margin: -%dpx 0;"
               " border-radius: %dpx;"
               % (dpi.scaled(_HANDLE_W), dpi.scaled(7), dpi.scaled(4)))
        if handle_color:
            css += (" background-color: %s; border: %dpx solid %s;"
                    % (handle_color, dpi.scaled(1), mindmeld_style.TOKENS["carbon"]))
        css += " }"
        self.setStyleSheet(css)

    def _handle_center_x(self) -> int:
        margin = dpi.scaled(self._TRAVEL_MARGIN)
        usable = max(1, self.width() - 2 * margin)
        span = self.maximum() - self.minimum() or 1
        return int(margin + usable * (self.value() - self.minimum()) / span)

    def paintEvent(self, event):
        super().paintEvent(event)
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QColor, QFont, QPainter, QPen
        p = QPainter(self)
        if self._notches > 0:
            p.setPen(QPen(QColor(mindmeld_style.BONE_DIM), dpi.scaled(1)))
            margin = dpi.scaled(self._TRAVEL_MARGIN)
            usable = max(1, self.width() - 2 * margin)
            y0, y1 = self.height() - dpi.scaled(7), self.height() - dpi.scaled(1)
            for i in range(self._notches + 1):
                x = int(margin + usable * i / self._notches)
                p.drawLine(x, y0, x, y1)
        if self._handle_label:
            f = QFont("JetBrains Mono")
            f.setPixelSize(dpi.scaled(9))
            f.setBold(True)
            p.setFont(f)
            # dark text on the bright handle when colored, else bone
            p.setPen(QColor(mindmeld_style.TOKENS["carbon"]
                            if self._handle_color else mindmeld_style.BONE))
            cx = self._handle_center_x()
            tw = dpi.scaled(_HANDLE_W + 8)      # a touch past the handle for kerning
            p.drawText(QRect(int(cx - tw / 2), 0, tw, self.height()),
                       Qt.AlignCenter, self._handle_label)
        p.end()


class _ValuePip(QLabel):
    """A tiny, non-focusing readout that floats above the handle while you
    drag, so the live integer is legible without hunting for it. Top-level +
    mouse-transparent so it can't steal the drag or the slider's focus."""
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool
                         | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setObjectName("fs_slider_pip")
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "QLabel#fs_slider_pip {"
            f" background: {mindmeld_style.TOKENS['carbon']};"
            f" color: {mindmeld_style.PLASMA};"
            f" border: 1px solid {mindmeld_style.EMBER};"
            " padding: 2px 7px; font-family: 'JetBrains Mono'; font-size: 12px; }")


class _OptionsFlyout(QFrame):
    """A small, non-focusing companion panel that rides just under the slider,
    holding whatever options the tool exposes (idea B: axis + Offset/Spread).
    Non-activating so summoning it never steals the slider's focus or the
    drag; hover/expand driven, so clicking the slider can't dismiss it (the
    provisional-popover click-outside model would)."""
    def __init__(self, content: QWidget, owner: QWidget = None):
        # Parented to the owning slider (the _PopoverFrame pattern):
        # stylesheets only cascade to CHILDREN, so a parentless frame
        # sat outside the styled tree and the borrowed panel skin
        # matched nothing — the flyout rendered stock Maya grey
        # (Adrian, 2026-07-05). Qt.Tool keeps it a floating window.
        super().__init__(owner, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setObjectName("fabricator_toolbar_popover")   # reuse panel skin
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(dpi.scaled(8), dpi.scaled(6),
                               dpi.scaled(8), dpi.scaled(6))
        lay.addWidget(content)


class ExpandingSlider(QWidget):
    valueApplied = Signal(float)

    def __init__(self, glyph: str, minimum: float = 0.0, maximum: float = 1.0,
                 default: float = 0.5, tooltip: str = "",
                 command: Optional[Callable[[float], None]] = None,
                 live: bool = False, reset_on_apply: bool = False,
                 center_return: bool = False,
                 options_factory: Optional[Callable[[], QWidget]] = None,
                 handle_label: str = "", handle_color: str = "",
                 notches: Optional[int] = None, range_inputs: bool = False,
                 gesture_begin: Optional[Callable[[], None]] = None,
                 gesture_end: Optional[Callable[[], None]] = None,
                 parent=None):
        super().__init__(parent)
        self._min = float(minimum)
        self._max = float(maximum)
        self._default = float(default)
        self._range_inputs = bool(range_inputs)
        self._notches_override = notches
        self._min_spin = None
        self._max_spin = None
        self._command = command
        self._live = bool(live)
        self._center_return = bool(center_return)
        # a spring-loaded jog always returns home; standalone reset_on_apply
        # keeps the same no-refire reset for relative (non-centered) sliders.
        self._reset_on_apply = bool(reset_on_apply) or self._center_return
        self._gesture_begin = gesture_begin
        self._gesture_end = gesture_end
        self._gesture_open = False
        self._expanded = False
        self._last_live_fired: Optional[float] = None
        self._anim: Optional[QPropertyAnimation] = None
        self._pip: Optional[_ValuePip] = None
        self._options_factory = options_factory
        self._flyout: Optional[_OptionsFlyout] = None
        self._flyout_timer = QTimer(self)
        self._flyout_timer.setSingleShot(True)
        self._flyout_timer.setInterval(250)
        self._flyout_timer.timeout.connect(self._flyout_timeout)
        # programmatic sets keep the exact float (the 0..1000 raw grid
        # can't represent every default, e.g. 1.0 in a 0.5..2.0 range);
        # a user drag invalidates it and the raw mapping takes over.
        self._exact_value: Optional[float] = None
        self._syncing = False

        self._collapsed_w = dpi.scaled(COLLAPSED_W)
        self._expanded_w = dpi.scaled(EXPANDED_W)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Notches (Adrian, 2026-07-03): a small integer span (e.g. insert-joints
        # 0..10) paints a tick at each integer so the count is legible; a big
        # span (offset +/-100) passes an explicit `notches` count instead (an
        # integer tick per unit would be a smear). The command already
        # int(round)s the value, so the effective value lands on these ticks
        # even though the handle glides.
        if self._notches_override is not None:
            self._notches = int(self._notches_override)
        else:
            span = self._max - self._min
            self._notches = (int(round(span)) if (span > 0 and abs(span - round(span)) < 1e-6
                                                  and span <= 20) else 0)
        # handle_color is a mindmeld TOKEN NAME (plasma/ember/...) -> hex here,
        # so the manifest never carries a raw color.
        handle_hex = mindmeld_style.TOKENS.get(handle_color, "") if handle_color else ""
        self._slider = _NotchedSlider(Qt.Horizontal, notches=self._notches,
                                      handle_label=handle_label, handle_color=handle_hex)
        self._slider.setRange(0, _STEPS)
        self._slider.setFixedHeight(dpi.scaled(26))
        # Idle = compact; width is driven by the expand/contract animation.
        self._slider.setMinimumWidth(self._collapsed_w)
        self._slider.setMaximumWidth(self._collapsed_w)
        if tooltip:
            self._slider.setToolTip(tooltip)
            self.setToolTip(tooltip)
        self._set_slider_from_value(default)
        # Connected always, guarded on expansion state inside the handler:
        # the ctor set above and any collapsed programmatic set must never
        # reach the scene (they run while the widget is idle).
        self._slider.valueChanged.connect(self._on_value_changed)
        self._slider.sliderReleased.connect(self._apply_and_collapse)
        # Clicking or tabbing INTO the slider expands it (and owns the gesture
        # open); focus leaving collapses it. The container never takes focus
        # (the child does), so we watch the slider itself.
        self._slider.installEventFilter(self)

        # Optional editable range: an int field on each side of the slider so
        # the min/max can be dialed in place (Adrian, 2026-07-03, offset jog).
        if self._range_inputs:
            self._min_spin = self._make_range_spin(int(round(self._min)))
            self._max_spin = self._make_range_spin(int(round(self._max)))
            self._min_spin.valueChanged.connect(self._on_min_changed)
            self._max_spin.valueChanged.connect(self._on_max_changed)
            lay.addWidget(self._min_spin)
            lay.addWidget(self._slider)
            lay.addWidget(self._max_spin)
        else:
            lay.addWidget(self._slider)

    @staticmethod
    def _make_range_spin(value: int) -> QSpinBox:
        from PySide6.QtGui import QFont
        spin = QSpinBox()
        spin.setRange(-9999, 9999)
        spin.setValue(value)
        spin.setButtonSymbols(QAbstractSpinBox.NoButtons)   # plain int field, no arrows
        spin.setFixedWidth(dpi.scaled(40))
        spin.setAlignment(Qt.AlignCenter)
        f = QFont("JetBrains Mono")
        f.setPixelSize(dpi.scaled(9))                       # small enough that +/-100 fits
        spin.setFont(f)
        spin.setToolTip("Slider range")
        return spin

    def _on_min_changed(self, v: int) -> None:
        self._min = float(v)
        self._default = min(max(self._default, self._min), self._max)
        self._set_slider_from_value(self._default)   # re-home; no re-fire (idle)
        self._slider.update()

    def _on_max_changed(self, v: int) -> None:
        self._max = float(v)
        self._default = min(max(self._default, self._min), self._max)
        self._set_slider_from_value(self._default)
        self._slider.update()

    # -- value mapping -------------------------------------------------
    def _value_from_raw(self, raw: int) -> float:
        if not self._center_return:
            return self._min + (raw / _STEPS) * (self._max - self._min)
        center = _STEPS / 2.0
        if raw >= center:                    # right of home -> [default..max]
            t = (raw - center) / (_STEPS - center)
            return self._default + t * (self._max - self._default)
        t = (center - raw) / center          # left of home -> [default..min]
        return self._default - t * (self._default - self._min)

    def _raw_from_value(self, v: float) -> int:
        if not self._center_return:
            span = (self._max - self._min) or 1.0
            return int(round((v - self._min) / span * _STEPS))
        center = _STEPS / 2.0
        if v >= self._default:
            denom = (self._max - self._default) or 1.0
            return int(round(center + (v - self._default) / denom * (_STEPS - center)))
        denom = (self._default - self._min) or 1.0
        return int(round(center - (self._default - v) / denom * center))

    # -- state ---------------------------------------------------------
    def is_expanded(self) -> bool:
        return self._expanded

    def current_value(self) -> float:
        """The mapped slider value (Phase C per_widget persistence reads it)."""
        if self._exact_value is not None:
            return self._exact_value
        return self._value_from_raw(self._slider.value())

    def value(self) -> float:
        return self.current_value()

    def _set_slider_from_value(self, v: float) -> None:
        self._syncing = True
        try:
            self._slider.setValue(self._raw_from_value(float(v)))
        finally:
            self._syncing = False
        self._exact_value = float(v)

    # -- behavior ------------------------------------------------------
    def _expand(self) -> None:
        if self._expanded:
            return
        self._expanded = True
        self._animate_to(self._expanded_w)
        self._open_gesture()
        self._show_pip()
        self._show_flyout()

    def _apply_and_collapse(self) -> None:
        if not self._expanded:
            return
        try:
            v = self.current_value()
            # live mode already applied this value on the last drag tick;
            # don't fire the duplicate on release.
            if not (self._live and self._last_live_fired is not None
                    and v == self._last_live_fired):
                self.valueApplied.emit(v)
                if self._command is not None:
                    self._command(v)
        finally:
            self._collapse()

    def _collapse(self) -> None:
        if not self._expanded:
            return
        self._expanded = False
        self._hide_pip()
        self._arm_flyout_hide()
        self._animate_to(self._collapsed_w)
        self._last_live_fired = None
        if self._reset_on_apply:
            # spring home: the handle returns to default (center, for a jog)
            # WITHOUT re-firing - the guarded valueChanged is inert while idle
            # (decision B16), so insert_joints_live(0) never runs on release.
            self._set_slider_from_value(self._default)
        self._close_gesture()

    def _animate_to(self, target: int) -> None:
        # Drive both min and max width so the slider actually resizes (a bare
        # maximumWidth animation would let it keep its idle min). Mirror the
        # animated value onto minimumWidth each tick.
        if self._slider.maximumWidth() == target and self._slider.minimumWidth() == target:
            return
        if self._anim is not None:
            self._anim.stop()
        self._anim = QPropertyAnimation(self._slider, b"maximumWidth", self)
        self._anim.setDuration(_EXPAND_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.setStartValue(self._slider.maximumWidth())
        self._anim.setEndValue(target)
        self._anim.valueChanged.connect(
            lambda v: self._slider.setMinimumWidth(int(v)))
        self._anim.start()

    def _on_value_changed(self, _raw: int) -> None:
        if self._syncing:
            return
        self._exact_value = None
        if not self._expanded:
            return
        self._update_pip()
        if not self._live:
            return
        v = self.current_value()
        if self._last_live_fired is not None and v == self._last_live_fired:
            return
        self._last_live_fired = v
        self.valueApplied.emit(v)
        if self._command is not None:
            self._command(v)

    # -- live value pip ------------------------------------------------
    def _format_value(self, v: float) -> str:
        return str(int(round(v))) if self._notches else f"{v:.2f}"

    def _handle_center_x(self) -> int:
        margin = dpi.scaled(6)
        usable = max(1, self._slider.width() - 2 * margin)
        return int(margin + usable * self._slider.value() / _STEPS)

    def _show_pip(self) -> None:
        if self._pip is None:
            self._pip = _ValuePip()
        self._update_pip()
        try:
            self._pip.show()
        except RuntimeError:
            pass

    def _update_pip(self) -> None:
        if self._pip is None:
            return
        self._pip.setText(self._format_value(self.current_value()))
        self._pip.adjustSize()
        try:
            gp = self._slider.mapToGlobal(QPoint(self._handle_center_x(), 0))
        except RuntimeError:
            return
        self._pip.move(gp.x() - self._pip.width() // 2,
                       gp.y() - self._pip.height() - dpi.scaled(6))

    def _hide_pip(self) -> None:
        if self._pip is not None:
            self._pip.hide()

    # -- options flyout (idea B: axis + mode) --------------------------
    def _show_flyout(self) -> None:
        if self._options_factory is None:
            return
        if self._flyout is None:
            content = self._options_factory()
            self._flyout = _OptionsFlyout(content, owner=self)
            # Watch the panel AND its controls so moving onto a checkbox
            # (a Leave on the frame, an Enter on the child) keeps it up.
            for w in [self._flyout] + self._flyout.findChildren(QWidget):
                w.installEventFilter(self)
        self._flyout_timer.stop()
        self._position_flyout()
        try:
            self._flyout.show()
        except RuntimeError:
            pass

    def _position_flyout(self) -> None:
        if self._flyout is None:
            return
        self._flyout.adjustSize()
        try:
            gp = self._slider.mapToGlobal(QPoint(0, self._slider.height() + dpi.scaled(3)))
        except RuntimeError:
            return
        self._flyout.move(gp.x(), gp.y())

    def _arm_flyout_hide(self) -> None:
        # Keep the panel up while a drag is in progress (expanded); otherwise
        # start the grace timer so a hop between slider and panel doesn't
        # flicker it shut.
        if self._flyout is None or self._expanded:
            return
        self._flyout_timer.start()

    def _flyout_timeout(self) -> None:
        if self._expanded or self._flyout is None:
            return
        from PySide6.QtGui import QCursor
        gp = QCursor.pos()
        try:
            if self._slider.rect().contains(self._slider.mapFromGlobal(gp)):
                return                        # pointer back on the slider
        except RuntimeError:
            pass
        try:
            if self._flyout.frameGeometry().contains(gp):
                return                        # pointer over the panel
        except RuntimeError:
            pass
        self._hide_flyout()

    def _hide_flyout(self) -> None:
        self._flyout_timer.stop()
        if self._flyout is not None:
            self._flyout.hide()

    def _over_flyout(self, obj) -> bool:
        return (self._flyout is not None
                and (obj is self._flyout or self._flyout.isAncestorOf(obj)))

    # -- gesture undo chunk (decision B15) -------------------------------
    def _open_gesture(self) -> None:
        if self._gesture_open:
            return
        self._gesture_open = True
        if self._gesture_begin is not None:
            self._gesture_begin()

    def _close_gesture(self) -> None:
        if not self._gesture_open:
            return
        self._gesture_open = False
        if self._gesture_end is not None:
            self._gesture_end()

    def eventFilter(self, obj, event):
        t = event.type()
        if obj is self._slider:
            # Click or tab INTO the slider expands it and opens the gesture
            # BEFORE any drag tick lands, so the whole drag sits in one chunk.
            if t in (QEvent.MouseButtonPress, QEvent.FocusIn):
                self._expand()
            # Hover summons the options panel; right-click summons it too (and
            # eats the would-be native context menu).
            elif t == QEvent.Enter:
                self._show_flyout()
            elif t == QEvent.ContextMenu:
                self._show_flyout()
                return True
            elif t == QEvent.Leave:
                self._arm_flyout_hide()
            # Focus moving elsewhere while expanded must FULLY collapse -
            # a bare gesture-close would leave it wide and the eventual
            # sliderReleased would apply OUTSIDE any undo chunk. Collapse
            # paths guard re-entry (_expanded flips first, _close_gesture is
            # exactly-once), so a collapse-induced FocusOut cannot double-fire.
            elif t == QEvent.FocusOut and self._expanded:
                self._collapse()
        elif self._over_flyout(obj):
            # On the panel (or a control in it): keep it up; leaving re-arms.
            if t == QEvent.Enter:
                self._flyout_timer.stop()
            elif t == QEvent.Leave:
                self._arm_flyout_hide()
        return super().eventFilter(obj, event)

    def hideEvent(self, event) -> None:
        # A dangling open chunk corrupts Maya's undo queue: if the whole
        # widget hides mid-gesture (toolbar teardown/redock), close it.
        self._close_gesture()
        self._hide_pip()
        self._hide_flyout()
        super().hideEvent(event)
