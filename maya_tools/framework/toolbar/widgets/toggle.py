"""Toggle — lit/unlit state button (spec 3.2).

Phase B state model: state_query at construction + resync after every fire
(the command's return value wins when it returns a bool). Scene-event
resync arrives with the Phase C context-slot callbacks."""
from __future__ import annotations

import inspect
from typing import Callable, Optional

from maya_tools.framework.toolbar.widgets.icon_button import IconButton, load_icon


class Toggle(IconButton):
    def __init__(self, glyph: str, tooltip: str = "",
                 command: Optional[Callable[[], object]] = None,
                 state_query: Optional[Callable[[], bool]] = None,
                 menu: Optional[list] = None,
                 icon: Optional[str] = None,
                 icon_on: Optional[str] = None,
                 label: Optional[str] = None,
                 width: Optional[int] = None,
                 height: Optional[int] = None, parent=None):
        self._state_query = state_query
        self._lit = False
        # State-swapped icon (Phase D, the paint_mode on/off pair): `icon`
        # is the OFF look, `icon_on` the lit look. BOTH must resolve or the
        # swap is disabled (base icon + QSS lit look degrade as before).
        self._icon_off = load_icon(icon) if icon else None
        self._icon_on = load_icon(icon_on) if icon_on else None
        if self._icon_off is None or self._icon_on is None:
            self._icon_on = None
        # `icon` forwards once the Task 9 IconButton upgrade (decision B25)
        # adds the parameter; until then Phase A's glyph-only base applies.
        # `menu` forwards to IconButton's right-click menu (Phase C: the
        # paint-on-select Toggle carries Options/Paint-Once actions).
        base_kwargs = {"tooltip": tooltip, "command": None, "menu": menu,
                       "label": label,
                       "width": width, "height": height, "parent": parent}
        if "icon" in inspect.signature(IconButton.__init__).parameters:
            base_kwargs["icon"] = icon
        super().__init__(glyph, **base_kwargs)
        # No lit-square darkening for toggles (Adrian, 2026-07-03): the on/off
        # icon pair already shows the state. QSS keys off this property; the
        # Fabricator context-lit button (no property) keeps its glow square.
        self.setProperty("fs_toggle", True)
        self._toggle_command = command
        self.clicked.connect(self._on_toggle_clicked)
        self.refresh_state()

    def is_lit(self) -> bool:
        return self._lit

    def refresh_state(self) -> None:
        if self._state_query is not None:
            try:
                self._set_lit(bool(self._state_query()))
            except Exception:
                self._set_lit(False)

    def _on_toggle_clicked(self) -> None:
        if self._toggle_command is None:
            return
        result = self._toggle_command()
        if isinstance(result, bool):
            self._set_lit(result)
        else:
            self.refresh_state()

    def set_lit_external(self, lit: bool) -> None:
        """Public resync verb for listener-driven state pushes (Phase C):
        scene callbacks compute the state and set the lit look directly."""
        self._set_lit(lit)

    def _set_lit(self, lit: bool) -> None:
        self._lit = lit
        self.set_lit(lit)          # IconButton owns the QSS repolish (C5)
        if self._icon_on is not None:
            self._apply_icon(self._icon_on if lit else self._icon_off)
