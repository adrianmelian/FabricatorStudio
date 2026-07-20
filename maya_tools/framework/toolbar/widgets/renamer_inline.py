"""RenamerInline - THE renamer, inline on the strip (Wave B item 1,
2026-07-03; consolidated 2026-07-05).

A mode button plus text inputs, no Rename button: press Enter in a field
to apply. Four modes (Adrian, 2026-07-05: "have the whole renamer window
as a simple bar input"): Hash (pattern with ##), Search/Replace,
Prefix/Suffix (always ADD - use Search/Replace to remove), and Select by
Name (native-style wildcard select: type *_r, Enter selects the ls()).

Mode button (Adrian, 2026-07-10, supersedes the 2026-07-05 menu-on-click
ruling): LEFT-click cycles to the next mode, RIGHT-click opens the 4-radio
mode menu for a direct pick - matching the strip-wide right-click-for-
options grammar. The full Renamer window and its popover are RETIRED:
this strip is the whole tool.

The field area EXPANDS on hover or focus and CONTRACTS when idle (Adrian,
2026-07-03): compact (~1/3) at rest so it steals little strip space, full
width while you're using it. Registers as a single reorder unit; its
inner widgets are real but not reorder items.

App/UI split holds: the renamer engines import lazily inside the apply
handlers, so constructing this widget stays Qt-only (offscreen-testable)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint,
                            QPropertyAnimation, Qt, QTimer)
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLineEdit, QMenu,
                               QStackedWidget, QWidget)

from maya_tools.framework.toolbar import dpi
from maya_tools.framework.toolbar.widgets.icon_button import IconButton

HASH, FIND_REPLACE, PREFIX_SUFFIX, SELECT = 0, 1, 2, 3

# (menu label, toggle glyph, toggle icon) per mode, stack-index order.
# Missing icon files fall back to the glyph (decision B25), so the two
# new modes read as text until their icons are authored.
_MODES = [
    ("Hash Rename",      "##",  "fs_hash_renamer.png"),
    ("Search / Replace", "a>b", "fs_searchreplace.png"),
    ("Prefix / Suffix",  "a+",  "fs_prefixsuffix.png"),
    ("Select by Name",   "*?",  "fs_selectbyname.png"),
]

COLLAPSED_W = 70        # idle field-area width, px pre-DPI (2-field modes)
COLLAPSED_W_HASH = 92   # HASH mode idle is ~2 chars wider so "renamer_##" reads
                        # (Adrian, 2026-07-03); the other modes stay compact
EXPANDED_W = 200        # hovered / focused field-area width
_EXPAND_MS = 130        # animation duration
TOGGLE_W = 34           # mode button width
_ROW_SPACING = 2
SLEEPING_W = TOGGLE_W + _ROW_SPACING + COLLAPSED_W_HASH   # sleeping footprint;
                        # the project chooser matches this so the two left-zone
                        # inputs line up (Adrian, 2026-07-03)


class RenamerInline(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(_ROW_SPACING)

        # Mode button (Adrian, 2026-07-10): LEFT-click cycles to the next
        # mode, RIGHT-click opens the 4-radio picker menu.
        self._toggle = IconButton(
            "##", tooltip="Renamer mode — click to cycle, right-click "
                          "to pick: Hash, Search/Replace, Prefix/Suffix, "
                          "Select by Name.",
            command=self._next_mode, width=TOGGLE_W)
        self._toggle.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._toggle.customContextMenuRequested.connect(
            lambda _pos: self._show_mode_menu())
        row.addWidget(self._toggle)

        self._stack = QStackedWidget()
        # The stack's width is driven by the expand/contract animation; fields
        # shrink with it (minWidth 0), so start collapsed (HASH is the default).
        self._stack.setMaximumWidth(dpi.scaled(COLLAPSED_W_HASH))

        self._hash = QLineEdit()
        self._hash.setPlaceholderText("renamer_##")
        self._hash.setMinimumWidth(0)
        self._hash.returnPressed.connect(self._apply_hash)
        self._stack.addWidget(self._wrap(self._hash))

        self._find = QLineEdit()
        self._find.setPlaceholderText("Search")
        self._find.setMinimumWidth(0)
        self._repl = QLineEdit()
        self._repl.setPlaceholderText("Replace")
        self._repl.setMinimumWidth(0)
        self._find.returnPressed.connect(self._apply_find_replace)
        self._repl.returnPressed.connect(self._apply_find_replace)
        self._stack.addWidget(self._wrap(self._find, self._repl))

        # Prefix/Suffix: text + which-end combo. Always ADD — removal
        # is Search/Replace's job (Adrian, 2026-07-05).
        self._fix_text = QLineEdit()
        self._fix_text.setPlaceholderText("text")
        self._fix_text.setMinimumWidth(0)
        self._fix_text.returnPressed.connect(self._apply_prefix_suffix)
        self._fix_side = QComboBox()
        self._fix_side.addItems(["Prefix", "Suffix"])
        self._fix_side.setMinimumWidth(0)
        self._stack.addWidget(self._wrap(self._fix_text, self._fix_side))

        # Select by Name: the native top-bar quick-select feel — type a
        # wildcard, Enter selects the ls() (space-separated = union).
        self._select = QLineEdit()
        self._select.setPlaceholderText("*_r")
        self._select.setMinimumWidth(0)
        self._select.returnPressed.connect(self._apply_select)
        self._stack.addWidget(self._wrap(self._select))

        row.addWidget(self._stack)

        self._mode = HASH
        self._sync()

        # expand/contract state
        self._hovered = False
        self._focused = False
        self._anim: Optional[QPropertyAnimation] = None
        # Enter/Leave/Focus can land on any descendant (children fill this
        # widget, so `self` alone won't see them) - watch the whole subtree.
        for w in [self] + self.findChildren(QWidget):
            w.installEventFilter(self)

    @staticmethod
    def _wrap(*fields) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        for f in fields:
            lay.addWidget(f)
        return page

    # -- mode -----------------------------------------------------------------
    def mode(self) -> int:
        return self._mode

    def _set_mode(self, mode: int) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self._sync()
        # collapsed width is mode-dependent (HASH is wider); re-settle it when
        # the field is idle so a switch resizes the sleeping renamer.
        if not self.is_expanded():
            self._apply_expanded()

    def _next_mode(self) -> None:
        """LEFT-click on the mode button: cycle to the next mode
        (Adrian, 2026-07-10: the mode toggle, back by popular demand)."""
        self._set_mode((self._mode + 1) % len(_MODES))

    def _show_mode_menu(self) -> None:
        """The mode picker on RIGHT-click: one menu, four radios, for a
        direct jump without cycling through."""
        menu = QMenu(self)
        for i, (label, _glyph, _icon) in enumerate(_MODES):
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(i == self._mode)
            act.triggered.connect(
                lambda _=False, m=i: self._set_mode(m))
        menu.exec(self._toggle.mapToGlobal(
            QPoint(0, self._toggle.height() + 2)))

    def _collapsed_w(self) -> int:
        return COLLAPSED_W_HASH if self._mode == HASH else COLLAPSED_W

    def _sync(self) -> None:
        self._stack.setCurrentIndex(self._mode)
        _label, glyph, icon = _MODES[self._mode]
        self._toggle.set_glyph(glyph)     # fallback when the icon is missing
        self._toggle.set_icon(icon)

    # -- expand / contract ----------------------------------------------------
    def eventFilter(self, obj, event):
        t = event.type()
        if t in (QEvent.Type.Enter, QEvent.Type.Leave):
            QTimer.singleShot(0, self._refresh_hover)     # defer: read real state
        elif t in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            QTimer.singleShot(0, self._refresh_focus)
        return False

    def _under_mouse(self) -> bool:
        try:
            return self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        except RuntimeError:
            return False

    def _refresh_hover(self) -> None:
        self._hovered = self._under_mouse()
        self._apply_expanded()

    def _refresh_focus(self) -> None:
        self._focused = any(f.hasFocus()
                            for f in (self._hash, self._find, self._repl,
                                      self._fix_text, self._select))
        self._apply_expanded()

    def _apply_expanded(self) -> None:
        target = dpi.scaled(EXPANDED_W if (self._hovered or self._focused)
                            else self._collapsed_w())
        if self._stack.maximumWidth() == target:
            return
        if self._anim is not None:
            self._anim.stop()
        self._anim = QPropertyAnimation(self._stack, b"maximumWidth", self)
        self._anim.setDuration(_EXPAND_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.setStartValue(self._stack.maximumWidth())
        self._anim.setEndValue(target)
        self._anim.start()

    def is_expanded(self) -> bool:
        return self._hovered or self._focused

    # -- apply (Enter, no button) --------------------------------------------
    def _apply_hash(self) -> None:
        from maya_tools.framework.renamer_tools import hash_renamer
        from maya_tools.framework.toolbar import toolbar_commands as tc
        # Pattern IS stripped - the window's one stripped field.
        tc.run_command("Rename", hash_renamer.rename_selection,
                       self._hash.text().strip(), 1)

    def _apply_find_replace(self) -> None:
        from maya_tools.framework.renamer_tools import renamer
        from maya_tools.framework.toolbar import toolbar_commands as tc
        # Raw text, NOT stripped: spaces are legit find/replace content.
        tc.run_command("Rename", renamer.find_replace_selection,
                       self._find.text(), self._repl.text(), True)

    def _apply_prefix_suffix(self) -> None:
        from maya_tools.framework.renamer_tools import renamer
        from maya_tools.framework.toolbar import toolbar_commands as tc
        side = 'prefix' if self._fix_side.currentIndex() == 0 else 'suffix'
        tc.run_command("Rename", renamer.prefix_suffix_selection,
                       'add', side, self._fix_text.text().strip())

    def _apply_select(self) -> None:
        from maya_tools.framework.toolbar import toolbar_commands as tc
        tc.run_command("Select", tc.select_by_wildcard,
                       self._select.text().strip())
