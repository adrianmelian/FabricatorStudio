"""IconButton — one click, one op (spec section 3.2).
Renders a real icon from icons/ when the manifest names one that
exists (decision B25); otherwise the text glyph stands in. The remade
simplified icon set is still a Phase D item. Optional right-click menu
(e.g. Fabricator quick Build/Unbuild, decision B24)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QPushButton

from maya_tools.framework.toolbar import dpi

_ICON_DIR = Path(__file__).resolve().parents[4] / "icons"

# Shelf-style hover (Adrian, 2026-07-03): the icon GRAPHIC brightens, no square.
_HOVER_BRIGHTEN = 0.30


def load_icon(name: str) -> Optional[QIcon]:
    """Resolve an icons/ filename to a QIcon; None when the file is
    absent (the glyph-text fallback is the caller's job, decision B25)."""
    path = _ICON_DIR / name
    if path.is_file():
        return QIcon(str(path))
    return None


def _brighten_icon(icon: QIcon, size: QSize) -> QIcon:
    """A brightened copy of `icon`: white laid over it with SourceAtop, so only
    the graphic's OPAQUE pixels lift (respecting alpha) - the shelf-hover look,
    never a lit square. Falls back to the original if the pixmap is empty."""
    pm = icon.pixmap(size)
    if pm.isNull():
        return icon
    out = QPixmap(pm.size())
    out.setDevicePixelRatio(pm.devicePixelRatio())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, pm)
    p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
    p.fillRect(out.rect(), QColor(255, 255, 255, int(255 * _HOVER_BRIGHTEN)))
    p.end()
    return QIcon(out)


class IconButton(QPushButton):
    def __init__(self, glyph: str, tooltip: str = "",
                 command: Optional[Callable[[], None]] = None,
                 menu: Optional[list[tuple[str, Callable]]] = None,
                 icon: Optional[str] = None,
                 label: Optional[str] = None,
                 width: Optional[int] = None,
                 height: Optional[int] = None, parent=None):
        super().__init__(glyph, parent)
        self._command = command
        self.setToolTip(tooltip)
        self.setFlat(True)
        # Per-item size (CP-D wave-1): manifest width/height in pre-DPI px,
        # 26x26 square default (e.g. the paint toggle rides wide at 48x24).
        btn_w = dpi.scaled(width if width else 26)
        btn_h = dpi.scaled(height if height else 26)
        self._btn_w, self._btn_h = btn_w, btn_h   # kept for runtime set_icon
        self._base_icon: Optional[QIcon] = None   # the un-hovered icon
        self._hover_icon: Optional[QIcon] = None  # brightened copy (lazy)
        self._hovered = False
        self.setFixedSize(btn_w, btn_h)
        # Styling comes from mindmeld.qss (TOOLBAR section, decision B8):
        # the strip root's stylesheet cascades to every child button.
        self.clicked.connect(self._on_clicked)

        self._menu: Optional[QMenu] = None
        if menu:
            self._menu = QMenu(self)
            # Loop var must NOT be `label`: it shadowed the label PARAM, so a
            # menu-carrying icon button fell into the labeled-mode branch below
            # (text + 18px icon) and its full-size icon shrank. Latent since
            # B24; surfaced by the library button's Mirror Pose row
            # (2026-07-18, the first menu+icon user).
            for entry_label, fn in menu:
                action = self._menu.addAction(entry_label)
                action.triggered.connect(
                    lambda checked=False, f=fn: f())
            self.setContextMenuPolicy(Qt.CustomContextMenu)
            self.customContextMenuRequested.connect(self._show_menu)

        loaded = load_icon(icon) if icon else None
        if label:
            # Labeled mode button (Symmetry-style, Adrian 2026-07-08): a
            # PERSISTENT text label, with an OPTIONAL small 18px icon to its
            # left. The fs_labeled property drives the plasma-off / ember-on
            # border in mindmeld.qss, matching the Fabricator-window Symmetry
            # toggle. Text-only is fine - the border carries the on/off state.
            self.setText(label)
            self.setProperty("fs_labeled", True)
            if loaded is not None:
                self.setIconSize(QSize(dpi.scaled(18), dpi.scaled(18)))
                self._apply_icon(loaded)
        elif loaded is not None:
            # Icon-only button: the icon fills the button minus a 2px pre-DPI
            # rim (CP-D wave-1: the old fixed 20px read too small). QIcon
            # scales to FIT preserving aspect - square art centers.
            rim = dpi.scaled(2)
            self.setIconSize(QSize(btn_w - rim, btn_h - rim))
            self._apply_icon(loaded)
            self.setText("")
        # else: no icon, no label -> keep the glyph text fallback (B25)

    def set_glyph(self, text: str) -> None:
        """Phase D icon-swap seam: callers say set_glyph, not setText."""
        self.setText(text)

    def set_icon(self, name: str) -> None:
        """Swap the button's icon by KSShelf filename at runtime (renamer mode
        toggle, Adrian 2026-07-03). Missing file -> no-op, so the glyph
        fallback set alongside stays visible."""
        loaded = load_icon(name)
        if loaded is None:
            return
        rim = dpi.scaled(2)
        self.setIconSize(QSize(self._btn_w - rim, self._btn_h - rim))
        self._apply_icon(loaded)
        self.setText("")

    def _apply_icon(self, qicon: QIcon) -> None:
        """Set the button's BASE icon; the hover-brightened copy rebuilds lazily
        (Toggle routes its lit/unlit swaps through here too)."""
        self._base_icon = qicon
        self._hover_icon = None
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        if self._base_icon is None:
            return
        if self._hovered:
            if self._hover_icon is None:
                self._hover_icon = _brighten_icon(self._base_icon, self.iconSize())
            super().setIcon(self._hover_icon)
        else:
            super().setIcon(self._base_icon)

    def enterEvent(self, event):
        self._hovered = True
        self._refresh_icon()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._refresh_icon()
        super().leaveEvent(event)

    def set_lit(self, lit: bool) -> None:
        """Context-slot seam (decision C5): flip the `lit` QSS property and
        repolish so the mindmeld [lit="true"] rule applies. Toggle's
        internal _set_lit delegates here."""
        self.setProperty("lit", lit)
        self.style().unpolish(self)
        self.style().polish(self)

    def _show_menu(self, _pos) -> None:
        if self._menu is not None:
            self._menu.exec(QCursor.pos())

    def _on_clicked(self):
        if self._command is not None:
            self._command()
