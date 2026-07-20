"""FabricatorButton — the toolbar's Fabricator launcher (Adrian, 2026-07-08).

Four interactions on one logo button:
  - hover        -> rich annotation card (toolbar_ui._maybe_annotate attaches
                    it automatically: a FabricatorButton is not a hover-popover,
                    so it gets the standard hover description from doc/title/tip)
  - left click   -> open the Fabricator window DOCKED (workspaceControl)
  - double click -> open the Fabricator window UNDOCKED (floating)
  - right click  -> Build / Unbuild the rig (menu_factory returns the row fresh
                    each open, so it flips Build<->Unbuild with rig state)

Single vs double click needs a delay: a valid left click starts a timer sized to
the OS double-click interval; a double-click cancels that pending single and
fires the undocked open instead. So a single click's docked-open lands one
double-click-interval after release (Adrian accepted this tradeoff, 2026-07-08).

Robustness note: a double-click makes Qt emit `clicked` twice (the ignored
dbl-click event is re-sent as a press). We swallow EXACTLY the one trailing
click with a one-shot flag consumed by that click itself - no timer, so nothing
can fire on a torn-down button, a genuine next click is never dropped, and a
long-held second press can't race a time window. Commands arrive as already-
resolved callables from the app layer, exactly like every other widget.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtWidgets import QApplication, QMenu

from maya_tools.framework.toolbar.widgets.icon_button import IconButton


class FabricatorButton(IconButton):
    def __init__(self, glyph: str, tooltip: str = "",
                 command: Optional[Callable[[], object]] = None,
                 double_command: Optional[Callable[[], object]] = None,
                 menu_factory: Optional[Callable[[], list]] = None,
                 icon: Optional[str] = None,
                 title: Optional[str] = None,
                 width: Optional[int] = None,
                 height: Optional[int] = None, parent=None):
        # command=None to the base: the single/double split is gated in the
        # overridden _on_clicked + mouseDoubleClickEvent below, NOT via the base
        # QPushButton command. (IconButton.__init__ still wires clicked ->
        # _on_clicked, which resolves to our override.)
        super().__init__(glyph, tooltip=tooltip, command=None, icon=icon,
                         width=width, height=height, parent=parent)
        self._single = command
        self._double = double_command
        self._menu_factory = menu_factory
        self._title = title or tooltip or glyph

        self._click_timer = QTimer(self)      # parented -> Qt stops+frees it on
        self._click_timer.setSingleShot(True)  # widget teardown (no stray fire)
        self._click_timer.timeout.connect(self._fire_single)
        self._ignore_next_click = False

        # Right-click Build / Unbuild - the factory is called FRESH each open so
        # the single row flips Build<->Unbuild with rig state.
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_build_menu)

    # --- single vs double click ----------------------------------------------
    def _on_clicked(self) -> None:            # overrides IconButton._on_clicked
        """A valid left click landed. On a double-click Qt emits `clicked`
        twice (the ignored dbl-click event is re-sent as a press); the second
        one - the double's own trailing click - is swallowed here via the
        one-shot _ignore_next_click flag so it never fires a spurious dock. A
        real single click starts the delayed docked-open."""
        if self._ignore_next_click:           # consume EXACTLY the trailing
            self._ignore_next_click = False    # click, then re-arm immediately
            return
        self._click_timer.start(QApplication.doubleClickInterval())

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()          # cancel the pending single-open
            self._ignore_next_click = True     # swallow the trailing clicked
            if self._double is not None:
                self._double()
        # Keep super(): the ignored dbl-click is re-sent as a press, which emits
        # the trailing `clicked` we just armed to swallow. No timer here, so
        # nothing can fire on a deleted button and the swallow is exact (not a
        # time window that could drop a real next click or race a long hold).
        super().mouseDoubleClickEvent(event)

    def _fire_single(self) -> None:
        if self._single is not None:
            self._single()

    # --- right-click Build / Unbuild -----------------------------------------
    def _show_build_menu(self, _pos) -> None:
        if self._menu_factory is None:
            return
        try:
            rows = self._menu_factory() or []
        except Exception:
            # A broken factory degrades to no menu, never a crashed right-click.
            import traceback
            traceback.print_exc()
            return
        if not rows:
            return
        menu = QMenu(self)
        menu.setToolTipsVisible(True)         # per-row tooltip carries detail
        for row in rows:
            if row is None:                   # a bare None row is a separator
                menu.addSeparator()
                continue
            if len(row) == 3:
                label, tip, handler = row
            elif len(row) == 2:
                label, handler = row
                tip = ""
            else:
                continue
            action = menu.addAction(label)
            if tip:
                action.setToolTip(tip)
            if handler is None:               # inert row
                action.setEnabled(False)
            else:
                action.triggered.connect(lambda _=False, f=handler: f())
        menu.exec(self._drop_point(menu))

    def _drop_point(self, menu: QMenu) -> QPoint:
        """Edge-aware placement (mirrors MenuButton): drop below by default,
        flip ABOVE when the strip is docked near the screen bottom and a
        below-menu would run off-screen."""
        below = self.mapToGlobal(QPoint(0, self.height() + 2))
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            menu_h = menu.sizeHint().height()
            if below.y() + menu_h > avail.bottom():
                above_y = self.mapToGlobal(QPoint(0, -2)).y() - menu_h
                if above_y >= avail.top():
                    return QPoint(below.x(), above_y)
        return below
