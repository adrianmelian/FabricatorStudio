"""The Reggie chat panel widget.

Owns: the transcript, the input row, the setup card, the worker-thread
lifecycle, and the Qt signal marshaling. Constructor kwargs
bridge_autostart (default True; False in offscreen tests, no Maya) and
spawn_threads (default True; False lets tests drive callbacks inline).

Threading contract: ChatSession.run_turn runs on a daemon worker thread;
its plain callbacks are wired to _Emitter signals (queued back onto the
Qt main thread); only main-thread slots touch widgets. maya.cmds is never
imported here.
"""
from __future__ import annotations

import re
import threading

__author__ = "Adrian Melian"

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from maya_tools.framework.toolbar import dpi
from maya_tools.framework.toolbar.reggie import keys
from maya_tools.utils.qt.mindmeld import mindmeld_style


WELCOME_TEXT = (
    "Hello, I'm Reggie, your AI TA. If something broke: give me a short "
    "description and the exact steps to reproduce it - re-opening your "
    "file and re-running the steps makes my fix much better. Just have a "
    "question? Ask away.")

# Banner facelift: the old help_btn's label, lifted to a single constant so
# swapping it is a one-line edit while Adrian decides among the candidates.
_FORM_BTN_LABEL = "?"  # Adrian's pick 2026-07-08 (was "Chat Form"/"Help" candidates)


def _is_lang_tag(line: str) -> bool:
    """A fence-line language tag: non-empty, no spaces, short, and drawn
    from the identifier-ish alphabet real tags use (python, mel, c++,
    c#, objective-c, ...)."""
    return (bool(line) and " " not in line and len(line) <= 20
            and re.fullmatch(r"[A-Za-z0-9_+#.-]+", line) is not None)


def _split_blocks(text: str):
    """Split finished reply text on ``` fences -> [('text'|'code', str)].
    A language tag on the fence line is dropped. Nested triple-backtick
    fences break this naive even/odd parity (accepted limitation)."""
    parts = []
    for i, chunk in enumerate(text.split("```")):
        if i % 2 == 0:
            if chunk.strip():
                parts.append(("text", chunk.strip()))
        else:
            code = chunk
            nl = code.find("\n")
            first = (code[:nl] if nl != -1 else code).strip()
            if nl != -1 and _is_lang_tag(first):
                code = code[nl + 1:]        # drop the tag line
            elif nl == -1 and _is_lang_tag(first):
                continue    # tag-only fragment (reply cut right after
                            # "```python"): render no code part at all
            if code.strip():
                parts.append(("code", code.strip("\n")))
    return parts


def _safe_emit(signal):
    """Wrap a bound Qt signal's .emit as a plain callback the WORKER
    thread can call even after the panel (and its _Emitter) have been
    torn down mid-turn. Emitting on an already-C++-deleted QObject raises
    RuntimeError from the CALLING thread (the worker) before any
    cross-thread delivery happens, so the guard has to live at the emit
    site, not in the main-thread _on_* slots below."""
    def _emit(*args):
        try:
            signal.emit(*args)
        except RuntimeError:
            pass
    return _emit


# Borderless buttons (change 6): scoped per-instance override so send/
# stop/new-chat/help read as flat, chrome-free actions. Kept separate
# from kind='ghost' because Send/Stop still need their primary/danger
# color identity - only the border goes, in every state (default,
# hover, pressed), which a bare property-less override does not
# reliably beat the app stylesheet's pseudo-state rules for, so hover
# and pressed are restated explicitly. border:none carries no hex, so
# this stays token-clean.
_NO_BORDER_QSS = (
    "QPushButton { border: none; } "
    "QPushButton:hover { border: none; } "
    "QPushButton:pressed { border: none; } "
    "QPushButton:disabled { border: none; }"
)


def _borderless(btn: QtWidgets.QPushButton) -> QtWidgets.QPushButton:
    btn.setStyleSheet(_NO_BORDER_QSS)
    return btn


# Banner action buttons: black (carbon) fill, legible bone text, borderless
# in every state - dark chips against the grey/blue (iron_2) banner. Every
# pseudo-state is pinned so the mindmeld kind-based background never paints
# through (same discipline as _NO_BORDER_QSS). Tokens only, no hex.
_BANNER_BTN_QSS = (
    f"QPushButton {{ background-color: {mindmeld_style.CARBON}; "
    f"color: {mindmeld_style.BONE}; border: none; }} "
    f"QPushButton:hover {{ background-color: {mindmeld_style.IRON}; "
    f"color: {mindmeld_style.BONE}; border: none; }} "
    f"QPushButton:pressed {{ background-color: {mindmeld_style.CARBON}; "
    f"color: {mindmeld_style.BONE}; border: none; }} "
    f"QPushButton:disabled {{ background-color: {mindmeld_style.CARBON}; "
    f"color: {mindmeld_style.BONE_DIM}; border: none; }}"
)


def _banner_btn(btn: QtWidgets.QPushButton) -> QtWidgets.QPushButton:
    btn.setStyleSheet(_BANNER_BTN_QSS)
    return btn


# Bubble content labels must explicitly disclaim the border (double-
# border fix): a QFrame's own setStyleSheet(), when written as bare
# declarations with no selector, is inherited by every descendant the
# same way an app/ancestor stylesheet is - Qt does not treat "border"
# as scoped to the widget it was set on. _add_bubble's `border: 1px
# solid <color>; border-radius: 10px;` therefore lands on every plain
# QLabel dropped into the bubble too, which has no border rule of its
# own to override it, so each label painted its own inset copy of the
# bubble's border at the label's geometry (offset by the layout's
# content margins) - a second, smaller rounded rectangle reading as a
# double outline. Confirmed empirically (offscreen pixel scan of the
# rendered bubble) before landing this fix; not present on _CodeBlock
# (mindmeld="panel") or QPushButton/QPlainTextEdit children, which
# already carry their own explicit border rules that win over the
# inherited one.
_BUBBLE_LABEL_QSS = "border: none;"


def _bubble_label(text: str = "") -> QtWidgets.QLabel:
    """A transcript-bubble content label: word-wrapped, selectable, and
    explicitly border-less (see _BUBBLE_LABEL_QSS)."""
    lbl = QtWidgets.QLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(
        QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setStyleSheet(_BUBBLE_LABEL_QSS)
    return lbl


def _delete_layout_item(item) -> None:
    """Recursively tear down one QLayoutItem: a bubble row is a QHBoxLayout
    holding a widget + a stretch, so clearing the transcript has to walk
    into nested layouts, not just top-level widgets."""
    w = item.widget()
    if w is not None:
        w.setParent(None)
        w.deleteLater()
        return
    lay = item.layout()
    if lay is not None:
        while lay.count():
            _delete_layout_item(lay.takeAt(0))


# -- banner avatar ---------------------------------------------------------

def _load_avatar_pixmap() -> QtGui.QPixmap:
    """Reggie's circular portrait for the banner (icons/fs_reggie.png,
    a finished 256x256 circular portrait - dropped in as-is, no extra ring).
    Resolved via icon_button's _ICON_DIR convention (same repo-root-relative
    path icon_button.py itself uses - not a hardcoded absolute path), so the
    banner tracks the shelf icon directory if it ever moves. Returns a null
    QPixmap when the file is missing; callers must isNull()-guard before use
    (never a hard failure - a missing avatar should not break the panel)."""
    from maya_tools.framework.toolbar.widgets import icon_button
    path = icon_button._ICON_DIR / "fs_reggie.png"
    if path.is_file():
        return QtGui.QPixmap(str(path))
    return QtGui.QPixmap()


class _Emitter(QtCore.QObject):
    token = QtCore.Signal(str)
    status = QtCore.Signal(str)
    done = QtCore.Signal(dict)
    error = QtCore.Signal(str)


class _StatusDot(QtWidgets.QLabel):
    """Small round clickable AI-key status indicator. PLASMA (green) when
    a key is detected, EMBER (amber) when not - same ok/warn semantics as
    mindmeld_style.pill, just as a dot instead of a text chip so the key
    is never rendered as text anywhere on the panel. State is readable
    back off the widget via the 'state' dynamic property ('ok'/'warn')."""

    clicked = QtCore.Signal()

    _DIAMETER = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self._DIAMETER, self._DIAMETER)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.set_state(False)

    def set_state(self, key_present: bool) -> None:
        color = mindmeld_style.PLASMA if key_present else mindmeld_style.EMBER
        self.setProperty("state", "ok" if key_present else "warn")
        radius = self._DIAMETER // 2
        self.setStyleSheet(
            f"background-color: {color}; border: none; "
            f"border-radius: {radius}px;")
        self.setToolTip(
            "Key detected - AI settings" if key_present
            else "No API key - AI settings")

    def mousePressEvent(self, event) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# Dismissal-gap grace (the classic hover-card gap problem): the SAME short
# delay opens no card immediately and, on leave, defers hiding it - moving
# the pointer from the dot onto the card (or back) must not snap it shut
# mid-crossing. Mirrors the popover_button.py precedent elsewhere in this
# package (PopoverButton/_PopoverFrame's _HOVER_DELAY_MS + arm/cancel-close
# idiom), scaled down for a small info card instead of a full panel.
_HOVERCARD_GRACE_MS = 250


class _KeyStatusCard(QtWidgets.QFrame):
    """Frameless hover-card anchored under key_dot: a one-sentence
    explanation of the current ok/warn key state plus a Configure...
    button (popovers.open_connect_ai_window). Enter/leave timing: leaving
    EITHER the dot or the card arms a short grace timer; entering EITHER
    cancels it; the timeout re-checks the live cursor position against
    both rects before actually hiding (belt-and-suspenders over a missed
    enter/leave pairing - same idiom as _PopoverFrame._on_close_timeout).
    Parented to the panel (a Qt.Tool top-level, like _PopoverFrame, but
    still Qt-owned by its `parent` arg) so panel teardown takes it down
    too - no dangling top-level widget to guard against separately."""

    _OK_TEXT = "Reggie is connected - your Anthropic API key was found."
    _WARN_TEXT = ("No Anthropic API key found - Reggie can't chat until "
                  "one is set.")

    def __init__(self, dot: "_StatusDot", parent: QtWidgets.QWidget):
        super().__init__(parent, QtCore.Qt.WindowType.Tool
                          | QtCore.Qt.WindowType.FramelessWindowHint)
        self._dot = dot
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setProperty("mindmeld", "panel")
        mindmeld_style.apply(self)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(dpi.scaled(10), dpi.scaled(8),
                               dpi.scaled(10), dpi.scaled(8))
        lay.setSpacing(dpi.scaled(6))

        self.message_label = mindmeld_style.helper_label("")
        self.message_label.setWordWrap(True)
        self.message_label.setFixedWidth(dpi.scaled(220))
        lay.addWidget(self.message_label)

        self.configure_btn = mindmeld_style.button("Configure...", "ghost")
        self.configure_btn.clicked.connect(self._on_configure)
        lay.addWidget(self.configure_btn)

        self._hide_timer = QtCore.QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(_HOVERCARD_GRACE_MS)
        self._hide_timer.timeout.connect(self._on_hide_timeout)

    def refresh_text(self) -> None:
        ok = self._dot.property("state") == "ok"
        self.message_label.setText(self._OK_TEXT if ok else self._WARN_TEXT)

    def show_near(self, anchor: QtWidgets.QWidget) -> None:
        """Position just below/left of `anchor` (the dot), clamped to the
        anchor's screen so the card can never drift off-screen."""
        self.refresh_text()
        self.cancel_hide()
        self.adjustSize()
        below = anchor.mapToGlobal(
            QtCore.QPoint(0, anchor.height() + dpi.scaled(4)))
        x = below.x() - self.width() + anchor.width()
        y = below.y()
        screen = anchor.screen() or self.screen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = min(max(x, geo.left()), geo.right() - self.width())
            y = max(min(y, geo.bottom() - self.height()), geo.top())
        self.move(x, y)
        self.show()

    def _on_configure(self) -> None:
        # Lazy import: same cycle-avoidance reason as _on_key_dot_clicked.
        from maya_tools.framework.toolbar import popovers
        popovers.open_connect_ai_window()

    def arm_hide(self) -> None:
        self._hide_timer.start()

    def cancel_hide(self) -> None:
        self._hide_timer.stop()

    def _on_hide_timeout(self) -> None:
        if not isValid(self):
            return
        pos = QtGui.QCursor.pos()
        if self.frameGeometry().contains(pos):
            return    # pointer is back over the card: a missed enterEvent
        try:
            if isValid(self._dot) and self._dot.rect().contains(
                    self._dot.mapFromGlobal(pos)):
                return    # pointer is back over the dot
        except RuntimeError:
            pass
        self.hide()

    def enterEvent(self, event) -> None:
        self.cancel_hide()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.arm_hide()
        super().leaveEvent(event)


class _CodeBlock(QtWidgets.QFrame):
    """Monospace code + a Copy button (QApplication.clipboard().setText).
    Copy is the fix channel's last mile — always visible, never truncates."""

    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self.setProperty("mindmeld", "panel")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addStretch(1)
        copy_btn = mindmeld_style.button("Copy", "ghost")
        copy_btn.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(code))
        top.addWidget(copy_btn)
        lay.addLayout(top)

        body = QtWidgets.QPlainTextEdit(code)
        body.setReadOnly(True)
        body.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        body.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        font = QtGui.QFont(mindmeld_style.TOKENS["font_body"])
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        body.setFont(font)
        # Never truncate: size to the exact line count so nothing is
        # clipped vertically; long lines get a horizontal scrollbar
        # instead of wrapping (keeps code formatting intact).
        line_count = max(1, code.count("\n") + 1)
        body.setFixedHeight(body.fontMetrics().lineSpacing() * line_count + 16)
        body.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Fixed)
        lay.addWidget(body)


class _HelpDialog(QtWidgets.QDialog):
    """"Ask Reggie for help" — a one-line description + repro steps,
    composed into a single message and submitted through the owning
    panel's normal _send() path (single-flight, bridge-ensure, and
    history all apply unchanged). An unsent draft in the input row is
    never clobbered: the composed report is appended below it. If a
    turn is already running (session busy), the report is staged in
    the input row instead of sent, with an explanation on the panel's
    status line, so nothing is silently dropped.

    Parented to the panel (a top-level window, so mindmeld_style.apply
    runs on it directly per the tool_visibility.py precedent). Qt's
    parent/child ownership means the panel tearing down mid-dialog
    takes this window down with it — no dangling top-level widget.
    WA_DeleteOnClose keeps closed dialogs from piling up as hidden
    children of a long-lived panel; the panel clears its reference on
    finished so nothing dereferences the deleted wrapper."""

    def __init__(self, panel: "ReggiePanel"):
        super().__init__(panel)
        self._panel = panel
        mindmeld_style.apply(self)
        self.setWindowTitle("Ask Reggie for help")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        lay.addWidget(QtWidgets.QLabel("What happened (one line):"))
        self.what_edit = QtWidgets.QLineEdit()
        lay.addWidget(self.what_edit)

        lay.addWidget(QtWidgets.QLabel(
            "Exact steps to reproduce (re-run them in a fresh file if "
            "you can):"))
        self.steps_edit = QtWidgets.QPlainTextEdit()
        fm = self.steps_edit.fontMetrics()
        self.steps_edit.setFixedHeight(fm.lineSpacing() * 4 + 16)
        lay.addWidget(self.steps_edit)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addStretch(1)
        self.send_btn = mindmeld_style.button("Send", "primary")
        self.send_btn.clicked.connect(self._on_send)
        btn_row.addWidget(self.send_btn)
        self.cancel_btn = mindmeld_style.button("Cancel", "ghost")
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)
        lay.addLayout(btn_row)

    def _on_send(self) -> None:
        what = self.what_edit.text().strip()
        steps = self.steps_edit.toPlainText().strip()
        if not what and not steps:
            return    # both empty: Send does nothing
        message = f"What happened: {what}\n\nSteps to reproduce:\n{steps}"
        panel = self._panel
        # Never clobber an unsent draft: append the report below it.
        existing = panel.input_edit.toPlainText()
        if existing.strip():
            message = existing + "\n\n" + message
        panel.input_edit.setPlainText(message)
        if panel._session is not None and panel._session.busy:
            # A turn is running: _send() would silently no-op. Stage the
            # report in the input row instead and say so honestly.
            panel.status_line.setText(
                "Reggie is busy - your report is in the message box, "
                "press Send when he's done")
            self.accept()
            return
        panel._send()
        self.accept()


# Reggie model picker (Adrian, 2026-07-11): right-click Send to choose the
# Claude model. (label, model_id); Fable is flagged premium so a typo-level
# bug never burns premium credits. Default mirrors api_client.DEFAULT_MODEL.
_DEFAULT_MODEL_ID = "claude-sonnet-5"
_REGGIE_MODEL_PREF = "reggie_model"
REGGIE_MODELS = (
    ("Haiku 4.5  (fast, cheap)", "claude-haiku-4-5-20251001"),
    ("Sonnet 5  (balanced, default)", "claude-sonnet-5"),
    ("Opus 4.8  (deep reasoning)", "claude-opus-4-8"),
    ("Fable 5  (premium - costly)", "claude-fable-5"),
)


class ReggiePanel(QtWidgets.QWidget):

    def __init__(self, parent=None, *, bridge_autostart=True,
                 spawn_threads=True):
        super().__init__(parent)
        self._bridge_autostart = bridge_autostart
        self._spawn_threads = spawn_threads
        self._model = self._load_model_pref()   # right-click Send to change
        self._session = None            # ChatSession, built on first send
        self._stop_event = None         # threading.Event of the live turn
        self._live_bubble = None        # the in-progress Reggie bubble
        self._live_label = None         # its live streaming QLabel
        self._stream_text = ""          # raw text accumulated this turn
        self._help_dialog = None        # live _HelpDialog; cleared on finished
        self._key_hovercard = None      # lazy _KeyStatusCard; built on first hover
        self._emitter = _Emitter(self)
        self._emitter.token.connect(self._on_token)
        self._emitter.status.connect(self._on_status)
        self._emitter.done.connect(self._on_done)
        self._emitter.error.connect(self._on_error)

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(0)   # banner sits flush against the chat box below;
        #                      its own ember border is the separator now, so
        #                      the old horizontal_rule (and its gap) is gone.

        # Identity banner (always visible, above the splitter, in both the
        # key-present and no-key states) - see _build_banner.
        self.banner = self._build_banner()
        root.addWidget(self.banner)

        # transcript: QScrollArea > QWidget > QVBoxLayout of bubbles + stretch
        self.transcript = QtWidgets.QScrollArea(widgetResizable=True)
        self.transcript.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._transcript_body = QtWidgets.QWidget()
        self._transcript_layout = QtWidgets.QVBoxLayout(self._transcript_body)
        self._transcript_layout.setContentsMargins(6, 6, 6, 6)
        self._transcript_layout.setSpacing(8)
        self._transcript_layout.addStretch(1)
        self.transcript.setWidget(self._transcript_body)
        self.transcript.setMinimumHeight(dpi.scaled(80))

        # QWidget holding input_edit/send/stop/new
        self.input_row = self._build_input_row()
        self.input_row.setMinimumHeight(dpi.scaled(60))

        # Resizable composer (change 5): a vertical QSplitter between the
        # transcript (top) and the input area (bottom) so a long message
        # can get more room - drag the handle instead of staying locked
        # at 3 lines. House pattern per fs_window.py's main splitter:
        # QSplitter + objectName + stretch factors favoring the larger
        # pane; setChildrenCollapsible(False) plus each pane's minimum
        # height above keep either side from vanishing to zero.
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.splitter.setObjectName("reggie_splitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(dpi.scaled(6))
        self.splitter.addWidget(self.transcript)
        self.splitter.addWidget(self.input_row)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([dpi.scaled(420), dpi.scaled(100)])
        root.addWidget(self.splitter, 1)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)
        self.status_line = mindmeld_style.helper_label("")      # muted tool activity
        status_row.addWidget(self.status_line, 1)
        root.addLayout(status_row)

        self.setup_card = self._build_setup_card()   # QWidget
        root.addWidget(self.setup_card)

        self._refresh_key_state()                    # shows card OR input row
        self._show_welcome_bubble()                  # empty transcript furniture
        if bridge_autostart:
            self._ensure_bridge()                    # try/except ai_bridge.start()
        # This panel lives in its own workspaceControl, disconnected from
        # the toolbar strip that would otherwise apply the mindmeld
        # stylesheet - so it must apply it to itself, once, on the root,
        # after all children exist (tool_visibility.py's
        # ToolVisibilityPanel is the precedent for a standalone tool
        # window applying mindmeld_style directly to self).
        mindmeld_style.apply(self)

    # -- banner construction -------------------------------------------------

    def _build_banner(self) -> QtWidgets.QWidget:
        """Reggie's identity banner: a contact-header row - New Chat (left),
        a centered circular avatar, Chat Form (right) - with the name and
        key_dot centered directly beneath the avatar. new_chat_btn and
        help_btn keep their attribute names and click handlers from the old
        input-row placement; only their container and help_btn's label
        changed. key_dot keeps its _StatusDot identity and click handler;
        this is also where its hover-card wiring (installEventFilter) is
        attached."""
        banner = QtWidgets.QWidget()
        banner.setObjectName("reggie_banner")
        # Grey/blue filled identity header (iron_2), no outline. Scoped to the
        # widget's own objectName so the fill styles only the banner; child
        # labels are transparent and show it through, the avatar is a pixmap,
        # and the banner buttons carry their own (black) fill. A bare
        # background: on a parent would not bleed the way border did, but the
        # #objectName scope is kept for the same defensive reason. Token only.
        banner.setStyleSheet(
            f"QWidget#reggie_banner {{ background-color: "
            f"{mindmeld_style.TOKENS['iron_2']}; }}")
        outer = QtWidgets.QVBoxLayout(banner)
        # inner padding so content doesn't jam against the ember line
        outer.setContentsMargins(dpi.scaled(8), dpi.scaled(6),
                                 dpi.scaled(8), dpi.scaled(6))
        outer.setSpacing(dpi.scaled(2))

        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        # kind='default' (see the old input-row comment this replaces): the
        # base QPushButton rule is the neutral secondary treatment; borders
        # stripped so it reads as flat chrome-free banner furniture.
        self.new_chat_btn = _banner_btn(mindmeld_style.button("New Chat"))
        self.new_chat_btn.clicked.connect(self._on_new_chat_clicked)
        top_row.addWidget(self.new_chat_btn, 0,
                          QtCore.Qt.AlignmentFlag.AlignVCenter)

        top_row.addStretch(1)

        self.avatar_label = QtWidgets.QLabel()
        avatar_size = dpi.scaled(64)
        self.avatar_label.setFixedSize(avatar_size, avatar_size)
        self.avatar_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        raw_avatar = _load_avatar_pixmap()
        if not raw_avatar.isNull():
            self.avatar_label.setPixmap(raw_avatar.scaled(
                avatar_size, avatar_size,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation))
        top_row.addWidget(self.avatar_label, 0,
                          QtCore.Qt.AlignmentFlag.AlignVCenter)

        top_row.addStretch(1)

        # _FORM_BTN_LABEL: a one-line swap while Adrian decides among the
        # candidates ("Chat Form" / "Help" / "?") - the handler is unchanged,
        # this button still opens _HelpDialog.
        self.help_btn = _banner_btn(mindmeld_style.button(_FORM_BTN_LABEL))
        self.help_btn.clicked.connect(self._on_help_clicked)
        top_row.addWidget(self.help_btn, 0,
                          QtCore.Qt.AlignmentFlag.AlignVCenter)

        outer.addLayout(top_row)

        name_row = QtWidgets.QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(4)
        name_row.addStretch(1)

        self.name_label = QtWidgets.QLabel("Reggie")
        # Color + size only via tokens (no font-family override - JetBrains
        # Mono, the QSS default, is what Adrian wants here, not VT323).
        self.name_label.setStyleSheet(
            f"color: {mindmeld_style.EMBER}; font-size: {dpi.scaled(20)}px;")
        name_row.addWidget(self.name_label, 0,
                           QtCore.Qt.AlignmentFlag.AlignVCenter)

        self.key_dot = _StatusDot()                    # AI key presence
        self.key_dot.clicked.connect(self._on_key_dot_clicked)
        self.key_dot.installEventFilter(self)           # hover-card wiring
        name_row.addWidget(self.key_dot, 0,
                           QtCore.Qt.AlignmentFlag.AlignVCenter)

        name_row.addStretch(1)
        outer.addLayout(name_row)

        return banner

    # -- setup card / input row construction -------------------------------

    def _build_setup_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QWidget()
        card.setProperty("mindmeld", "surface")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        intro = QtWidgets.QLabel(
            "Reggie needs an Anthropic API key, set once as an environment "
            "variable. We never store or see it — it stays in your "
            "environment.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        link = QtWidgets.QLabel(
            '<a href="https://console.anthropic.com">console.anthropic.com</a>')
        link.setTextFormat(QtCore.Qt.TextFormat.RichText)
        link.setOpenExternalLinks(True)
        lay.addWidget(link)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._setx_line = QtWidgets.QLineEdit(
            'setx ANTHROPIC_API_KEY "sk-ant-..."')
        self._setx_line.setReadOnly(True)
        row.addWidget(self._setx_line, 1)
        copy_btn = mindmeld_style.button("Copy", "ghost")
        copy_btn.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(
                self._setx_line.text()))
        row.addWidget(copy_btn)
        lay.addLayout(row)

        hint = mindmeld_style.helper_label(
            "macOS/Linux: add it to your shell profile, then restart Maya.")
        lay.addWidget(hint)

        refresh_btn = mindmeld_style.button("Refresh")
        refresh_btn.clicked.connect(self._on_refresh_clicked)
        lay.addWidget(refresh_btn)

        return card

    def _build_input_row(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.input_edit = QtWidgets.QPlainTextEdit()
        self.input_edit.setPlaceholderText(
            "Ask Reggie... (Enter to send, Shift+Enter for a new line)")
        fm = self.input_edit.fontMetrics()
        # Minimum only (not fixed): the splitter (change 5) lets this
        # grow when the handle is dragged, so it needs an Expanding
        # vertical policy to actually claim that extra pane height.
        self.input_edit.setMinimumHeight(fm.lineSpacing() * 3 + 16)
        self.input_edit.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                      QtWidgets.QSizePolicy.Policy.Expanding)
        # Green (plasma) outline around the composer - scoped to the type so
        # it styles the editor frame, not descendants (token, no hex).
        self.input_edit.setStyleSheet(
            f"QPlainTextEdit {{ border: 1px solid {mindmeld_style.PLASMA}; "
            f"border-radius: {dpi.scaled(4)}px; }}")
        self.input_edit.installEventFilter(self)
        row.addWidget(self.input_edit, 1)

        btn_col = QtWidgets.QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(4)
        self.send_btn = mindmeld_style.button("Send", "primary")
        self.send_btn.clicked.connect(self._send)
        # Thin plasma outline (Adrian): matches the composer's own green
        # border, token not hex, same 4px radius.
        self.send_btn.setStyleSheet(
            f"QPushButton {{ border: 1px solid {mindmeld_style.PLASMA}; "
            f"border-radius: {dpi.scaled(4)}px; }}")
        # Fill the composer height (Adrian): Send grows to match the input box.
        self.send_btn.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                                    QtWidgets.QSizePolicy.Policy.Expanding)
        # Right-click Send -> checkable model picker (Adrian, 2026-07-11).
        self.send_btn.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.send_btn.customContextMenuRequested.connect(self._show_model_menu)
        self.send_btn.setToolTip(self._model_tooltip())
        # Corner flag hinting the right-click model menu (Adrian): a small
        # plasma triangle pinned to the button's bottom-right, mouse-through
        # so it never eats a click. Repositioned on resize via eventFilter.
        self._rc_hint = QtWidgets.QLabel("◢", self.send_btn)  # lower-right triangle flag
        self._rc_hint.setStyleSheet(
            f"QLabel {{ color: {mindmeld_style.PLASMA}; "
            f"background: transparent; font-size: {dpi.scaled(8)}px; }}")
        self._rc_hint.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._rc_hint.setToolTip("Right-click to change model")
        self.send_btn.installEventFilter(self)
        btn_col.addWidget(self.send_btn)
        self.stop_btn = _borderless(mindmeld_style.button("Stop", "danger"))
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.stop_btn.setVisible(False)
        btn_col.addWidget(self.stop_btn)
        row.addLayout(btn_col)

        # new_chat_btn/help_btn moved to the identity banner (_build_banner) -
        # they no longer live in the composer.

        return container

    # -- Model picker (right-click Send) -----------------------------------

    def _model_label(self, model_id: str) -> str:
        return next((lbl for lbl, mid in REGGIE_MODELS if mid == model_id),
                    model_id)

    def _model_tooltip(self) -> str:
        return ("Send   (right-click to change model)\n"
                f"Model: {self._model_label(self._model)}")

    def _show_model_menu(self, pos) -> None:
        menu = QtWidgets.QMenu(self.send_btn)
        group = QtGui.QActionGroup(menu)
        group.setExclusive(True)
        for label, model_id in REGGIE_MODELS:
            act = QtGui.QAction(label, menu)
            act.setCheckable(True)
            act.setChecked(model_id == self._model)
            act.triggered.connect(
                lambda _checked=False, m=model_id: self._set_model(m))
            group.addAction(act)
            menu.addAction(act)
        menu.exec(self.send_btn.mapToGlobal(pos))

    def _set_model(self, model_id: str) -> None:
        self._model = model_id
        self._save_model_pref(model_id)
        # Live swap: an existing session's client reads .model per request,
        # so the change takes effect on the next message without a new chat.
        if self._session is not None:
            try:
                self._session._client.model = model_id
            except Exception:
                pass
        self.send_btn.setToolTip(self._model_tooltip())

    def _load_model_pref(self) -> str:
        # toolbar_prefs is Maya-free (JSON), so this honors the panel's
        # "maya.cmds is never imported here" contract.
        try:
            from maya_tools.framework.toolbar import toolbar_prefs
            return (toolbar_prefs.load_prefs().get(_REGGIE_MODEL_PREF)
                    or _DEFAULT_MODEL_ID)
        except Exception:
            return _DEFAULT_MODEL_ID

    def _save_model_pref(self, model_id: str) -> None:
        try:
            from maya_tools.framework.toolbar import toolbar_prefs
            prefs = toolbar_prefs.load_prefs()
            prefs[_REGGIE_MODEL_PREF] = model_id
            toolbar_prefs.save_prefs(prefs)
        except Exception:
            pass

    def _position_rc_hint(self) -> None:
        # Pin the right-click flag to the Send button's bottom-right corner;
        # follows the button as it grows/shrinks with the composer splitter.
        hint = getattr(self, "_rc_hint", None)
        if hint is None:
            return
        hint.adjustSize()
        hint.move(self.send_btn.width() - hint.width() - dpi.scaled(2),
                  self.send_btn.height() - hint.height())
        hint.raise_()

    # -- Enter-to-send / Shift+Enter-newline -------------------------------

    def eventFilter(self, obj, event) -> bool:
        # getattr(..., None) guards: key_dot is built (and installed) in
        # _build_banner, which runs BEFORE input_edit exists - construction
        # sends key_dot ordinary Qt events (parent/child, polish, ...) well
        # before self.input_edit is assigned, so a bare `self.input_edit`
        # here would AttributeError on the very first one.
        if (obj is getattr(self, "send_btn", None)
                and event.type() == QtCore.QEvent.Type.Resize):
            self._position_rc_hint()
            return False
        if (obj is getattr(self, "input_edit", None)
                and event.type() == QtCore.QEvent.Type.KeyPress):
            key = event.key()
            if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
                    return False    # let QPlainTextEdit insert the newline
                # Only the physical button's enabled state gates a repeat
                # Enter (clicked() is already unreachable on a disabled
                # button, but this shortcut calls _send() directly).
                if self.send_btn.isEnabled():
                    self._send()
                return True
        if obj is getattr(self, "key_dot", None):
            # Hover-card wiring (the delicate part): Enter shows/refreshes
            # the card; Leave arms its grace-period hide (see _KeyStatusCard
            # - entering the card itself cancels that timer, so crossing the
            # dot-to-card gap never dismisses it). Never consumed - the
            # dot's own mousePressEvent (click-to-configure) still runs.
            if event.type() == QtCore.QEvent.Type.Enter:
                self._show_key_hovercard()
            elif event.type() == QtCore.QEvent.Type.Leave:
                if self._key_hovercard is not None:
                    self._key_hovercard.arm_hide()
        return super().eventFilter(obj, event)

    # -- key state -----------------------------------------------------------

    def _refresh_key_state(self) -> None:
        key = keys.get_api_key()
        if key:
            self.setup_card.setVisible(False)
            self.input_row.setVisible(True)
        else:
            self.setup_card.setVisible(True)
            self.input_row.setVisible(False)
        self.key_dot.set_state(bool(key))

    def _on_refresh_clicked(self) -> None:
        keys.refresh_from_registry()
        self._refresh_key_state()

    def _on_key_dot_clicked(self) -> None:
        # Lazy import (avoids a cycle: popovers pulls in the toolbar
        # package, which does not need to be live at panel-import time).
        from maya_tools.framework.toolbar import popovers
        popovers.open_connect_ai_window()

    def _show_key_hovercard(self) -> "_KeyStatusCard":
        """Build (once) and show the key_dot hover-card, positioned just
        below/left of the dot and clamped to its screen. A plain method
        (not gated behind a real OS hover event) so it can be driven
        deterministically in offscreen tests; the real hover path is the
        key_dot Enter case in eventFilter above."""
        if self._key_hovercard is None:
            self._key_hovercard = _KeyStatusCard(self.key_dot, self)
        self._key_hovercard.show_near(self.key_dot)
        return self._key_hovercard

    # -- bridge (idempotent, main thread, try/except) -----------------------

    def _ensure_bridge(self) -> None:
        try:
            from maya_tools.framework.toolbar import ai_bridge
            ai_bridge.start()
        except Exception:
            pass

    # -- sending / stopping --------------------------------------------------

    def _send(self) -> None:
        if self._session is not None and self._session.busy:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        self.input_edit.clear()
        self._add_text_bubble("user", text)

        if self._bridge_autostart:
            self._ensure_bridge()

        if self._session is None:
            from maya_tools.framework.toolbar.reggie import agent, api_client
            self._session = agent.ChatSession(
                api_client.AnthropicClient(keys.get_api_key(),
                                           model=self._model))

        self._stop_event = threading.Event()
        self.send_btn.setEnabled(False)
        # New Chat is dead during a live turn: session.new_chat() mutating
        # history under a running run_turn is a data race (the agent also
        # busy-guards new_chat as defense-in-depth for non-UI callers).
        self.new_chat_btn.setEnabled(False)
        self.stop_btn.setVisible(True)
        self.status_line.setText("")

        session = self._session
        stop_event = self._stop_event
        if self._spawn_threads:
            threading.Thread(
                target=session.run_turn, args=(text,),
                kwargs=dict(on_token=_safe_emit(self._emitter.token),
                           on_status=_safe_emit(self._emitter.status),
                           on_done=_safe_emit(self._emitter.done),
                           on_error=_safe_emit(self._emitter.error),
                           stop_event=stop_event),
                daemon=True).start()

    def _on_stop_clicked(self) -> None:
        self.stop_current_turn()
        self.status_line.setText(
            "stopping... (may take a few seconds on a stalled connection)")

    def stop_current_turn(self) -> None:
        """Idempotent; safe from destroyed-signal context (Task 10)."""
        if self._stop_event is not None:
            self._stop_event.set()

    def _on_new_chat_clicked(self) -> None:
        if self._session is not None:
            self._session.new_chat()
        self._clear_transcript()
        # Any in-flight streaming bubble just got deleteLater()'d with the
        # rest of the transcript; drop the dangling references so a
        # trailing _on_token/_on_done from a still-running turn can't
        # touch freed widgets.
        self._live_bubble = None
        self._live_label = None
        self._stream_text = ""
        self._show_welcome_bubble()

    def _on_help_clicked(self) -> None:
        # Stored on self (not a local var) so the dialog isn't eligible
        # for Python-side GC while open, and so tests can reach it.
        # open() shows it as a non-blocking, application-modal window
        # (no nested exec() event loop) — same effect as a modal dialog
        # without the re-entrancy that would make this untestable.
        # WA_DeleteOnClose deletes the C++ object once the dialog is
        # done, so the reference is cleared on finished — nothing may
        # dereference self._help_dialog after that.
        self._help_dialog = _HelpDialog(self)
        self._help_dialog.setModal(True)
        self._help_dialog.finished.connect(self._on_help_dialog_finished)
        self._help_dialog.open()

    def _on_help_dialog_finished(self, *_args) -> None:
        self._help_dialog = None

    # -- main-thread slots (queued from the worker via _Emitter) ------------

    def _on_token(self, text: str) -> None:
        if not isValid(self):
            return
        if self._live_label is None:
            was_bottom = self._scroll_at_bottom()
            self._live_bubble = self._add_bubble("reggie")
            self._live_label = _bubble_label()
            self._live_bubble.layout().addWidget(self._live_label)
            self._stream_text = ""
            if was_bottom:
                self._autoscroll_to_bottom()
        was_bottom = self._scroll_at_bottom()
        self._stream_text += text
        self._live_label.setText(self._stream_text)
        if was_bottom:
            self._autoscroll_to_bottom()

    def _on_status(self, text: str) -> None:
        if not isValid(self):
            return
        self.status_line.setText(text)

    def _on_done(self, payload: dict) -> None:
        if not isValid(self):
            return
        if self._live_bubble is not None and isValid(self._live_bubble):
            self._render_finished_bubble(self._live_bubble, self._stream_text)
        self._live_bubble = None
        self._live_label = None
        self._stream_text = ""
        self._stop_event = None
        self.send_btn.setEnabled(True)
        self.new_chat_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        checked = payload.get("checked") or []
        if checked:
            self.status_line.setText("checked: " + ", ".join(checked))
        else:
            self.status_line.setText("")

    def _on_error(self, message: str) -> None:
        if not isValid(self):
            return
        self._live_bubble = None
        self._live_label = None
        self._stream_text = ""
        self._add_text_bubble("error", message)
        self.send_btn.setEnabled(True)
        self.new_chat_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self._stop_event = None
        if "API key" in message:
            self._refresh_key_state()

    # -- bubbles / transcript --------------------------------------------

    def _scroll_at_bottom(self) -> bool:
        bar = self.transcript.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _autoscroll_to_bottom(self) -> None:
        # Deferred a tick so the layout has settled — but the panel can be
        # torn down (dock closed mid-stream) before the timer fires, and a
        # bare lambda would then poke a dead scrollbar (RuntimeError in the
        # Script Editor). Same isValid idiom as the _on_* slots.
        def _apply():
            if isValid(self):
                bar = self.transcript.verticalScrollBar()
                bar.setValue(bar.maximum())
        QtCore.QTimer.singleShot(0, _apply)

    # kind -> border token (changes 1-3): rounded, transparent-fill
    # bubbles - a bright fill behind text read too harsh (Adrian). User
    # is PLASMA (green), Reggie replies are EMBER (orange), errors get
    # FLARE (red) - the same severity-language token the build-checks
    # dialog uses for errors, so a broken-turn bubble reads distinctly
    # from a normal Reggie reply without losing the family resemblance.
    _BUBBLE_BORDER = {
        "user": mindmeld_style.PLASMA,
        "reggie": mindmeld_style.EMBER,
        "error": mindmeld_style.FLARE,
    }
    _BUBBLE_RADIUS = 10

    def _add_bubble(self, kind: str) -> QtWidgets.QFrame:
        """kind: 'user' | 'reggie' | 'error'. User bubbles are right-
        aligned, Reggie/error bubbles left-aligned; border color (see
        _BUBBLE_BORDER) distinguishes sender without avatars."""
        bubble = QtWidgets.QFrame()
        border_color = self._BUBBLE_BORDER[kind]
        bubble.setStyleSheet(
            f"border: 1px solid {border_color}; background: transparent; "
            f"border-radius: {self._BUBBLE_RADIUS}px;")
        bubble.setMaximumWidth(dpi.scaled(420))
        inner = QtWidgets.QVBoxLayout(bubble)
        inner.setContentsMargins(8, 6, 8, 6)
        inner.setSpacing(4)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if kind == "user":
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch(1)

        was_bottom = self._scroll_at_bottom()
        insert_at = max(self._transcript_layout.count() - 1, 0)
        self._transcript_layout.insertLayout(insert_at, row)
        if was_bottom:
            self._autoscroll_to_bottom()
        return bubble

    def _add_text_bubble(self, kind: str, text: str) -> QtWidgets.QFrame:
        bubble = self._add_bubble(kind)
        lbl = _bubble_label(text)
        if kind == "error":
            lbl.setProperty("mindmeld", "helper")
        bubble.layout().addWidget(lbl)
        return bubble

    def _render_finished_bubble(self, bubble: QtWidgets.QFrame, text: str) -> None:
        layout = bubble.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for kind, content in _split_blocks(text):
            if kind == "code":
                layout.addWidget(_CodeBlock(content))
            else:
                layout.addWidget(_bubble_label(content))

    def _clear_transcript(self) -> None:
        while self._transcript_layout.count() > 1:    # keep the trailing stretch
            _delete_layout_item(self._transcript_layout.takeAt(0))

    def _show_welcome_bubble(self) -> None:
        """UI furniture only: a Reggie-styled bubble shown on an empty
        transcript (construction and after New Chat). Added straight to
        the transcript via _add_text_bubble like any Reggie reply, but it
        never touches ChatSession/self._session.history — the model never
        sees it and it is never sent as a message."""
        self._add_text_bubble("reggie", WELCOME_TEXT)
