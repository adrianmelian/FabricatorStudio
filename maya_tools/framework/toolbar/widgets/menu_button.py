"""MenuButton — a toolbar button that drops a native QMenu on RIGHT-CLICK
(Adrian, 2026-07-07; moved off left-click to right-click 2026-07-10). The
option-popovers (Scene / Create / Selection / Mirror / Skeleton / Skin /
Settings) trade their frameless movable panel for a plain right-click menu,
the same shape as the renamer's mode menu (renamer_inline._show_mode_menu).
No hover, no drag bar: one right-click, a menu of full-word actions drops
below the button, exactly one fires.

The rows come from a menu_factory() callable resolved by the app layer
(the same seam as popover_factory): it returns
[(label, tooltip, handler), ...]. A 2-tuple (label, handler) is allowed;
handler=None marks a disabled row; a bare None row is a separator. The
factory is called FRESH on every open so selection-dependent labels stay
live and the factory can import its Maya backends lazily. The QMenu is
parented to the button, so mindmeld.qss (its QMenu block) skins it for
free."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QMenu

from maya_tools.framework.toolbar.widgets.icon_button import IconButton


class MenuButton(IconButton):
    def __init__(self, glyph: str, tooltip: str = "",
                 menu_factory: Optional[Callable[[], list]] = None,
                 icon: Optional[str] = None,
                 title: Optional[str] = None,
                 command: Optional[Callable[[], None]] = None,
                 width: Optional[int] = None,
                 height: Optional[int] = None, parent=None):
        kwargs = {"tooltip": tooltip, "command": command,
                  "width": width, "height": height, "parent": parent}
        if icon is not None:
            kwargs["icon"] = icon
        super().__init__(glyph, **kwargs)
        self._menu_factory = menu_factory
        self._title = title or tooltip or glyph
        # RIGHT-click opens the menu (Adrian, 2026-07-10): the option menus
        # moved off left-click onto right-click, matching the other
        # right-click affordances on the strip. Left-click fires the OPTIONAL
        # `command` (Adrian, 2026-07-10: Skeleton/Skin Tools open their IO
        # windows); with no command, left-click does nothing.
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda _pos: self._show_menu())

    def _show_menu(self) -> None:
        if self._menu_factory is None:
            return
        try:
            rows = self._menu_factory() or []
        except Exception:
            # A broken factory degrades to no menu, never a crashed click.
            import traceback
            traceback.print_exc()
            return
        menu = QMenu(self)
        menu.setToolTipsVisible(True)      # the per-row tooltip carries the detail
        for row in rows:
            self._add_row(menu, row)
        menu.exec(self._drop_point(menu))

    def _drop_point(self, menu: QMenu) -> QPoint:
        """Drop below the button by default; flip ABOVE when the toolbar is
        docked near the screen bottom and a below-menu would run off-screen
        (Adrian, 2026-07-08). Full-word menus (Scene/Create/Selection/...) are
        tall enough to clip off a bottom-docked strip."""
        below = self.mapToGlobal(QPoint(0, self.height() + 2))
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            menu_h = menu.sizeHint().height()
            if below.y() + menu_h > avail.bottom():
                above_y = self.mapToGlobal(QPoint(0, -2)).y() - menu_h
                # Only flip if above actually fits better than below.
                if above_y >= avail.top():
                    return QPoint(below.x(), above_y)
        return below

    @staticmethod
    def _add_row(menu: QMenu, row) -> None:
        if row is None:                    # a bare None row is a separator
            menu.addSeparator()
            return
        label, tip, handler = MenuButton._unpack(row)
        action = menu.addAction(label)
        if tip:
            action.setToolTip(tip)
        if handler is None:                # inert row (e.g. a coming-soon entry)
            action.setEnabled(False)
        else:
            action.triggered.connect(lambda _=False, f=handler: f())

    @staticmethod
    def _unpack(row):
        """(label, tooltip, handler) | (label, handler) -> a 3-tuple."""
        if len(row) == 3:
            return row[0], row[1], row[2]
        if len(row) == 2:
            return row[0], "", row[1]
        raise ValueError(f"MenuButton row {row!r} must be a 2- or 3-tuple")
