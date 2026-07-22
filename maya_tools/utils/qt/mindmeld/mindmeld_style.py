"""Mindmeld — design system loader for PySide6 tools.

Public API:

    from maya_tools.utils.qt.mindmeld import mindmeld_style

    mindmeld_style.apply(self)                       # apply stylesheet + load fonts
    btn = mindmeld_style.button("Build Rig", "primary")
    lbl = mindmeld_style.field_label("Joint Name")
    mindmeld_style.tag(widget, "panel")              # set mindmeld dynamic property + restyle

Tokens are exposed as `mindmeld_style.TOKENS` for code that needs raw colors
(e.g. paintEvent overrides, custom drawing, log row colors).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QPixmap
from PySide6.QtWidgets import (QFrame, QGroupBox, QLabel, QPushButton,
                               QSizePolicy, QWidget)


# ============================================================
# Design tokens — kept in sync with mindmeld.qss
# ============================================================

TOKENS = {
    # core palette (5)
    "carbon": "#0B0E10",
    "iron":   "#1C2126",
    "bone":   "#E8E0D0",
    "plasma": "#7CFFB2",
    "ember":  "#FF7A3D",
    # derivatives (tints, not new colors)
    "iron_2":     "#262C33",
    "iron_3":     "#353D45",
    "bone_dim":   "#8A8378",
    "bone_faint": "#5A554D",
    "plasma_dim": "#4FB888",
    "ember_dim":  "#B5532A",
    # glow washes (web 2.0 tokens: alpha 0.18 / 0.22 → Qt 0-255)
    "plasma_glow": "rgba(124, 255, 178, 46)",
    "ember_glow":  "rgba(255, 122, 61, 56)",
    # signal colors (Adrian, 2026-07-05: errors are red, warnings are
    # yellow — the build-checks dialog's severity language). Warm
    # tints tuned to sit beside ember without clashing.
    "flare": "#FF5C5C",
    "amber": "#FFC857",
    # magma (brand tertiary #FF3B3B, ratified 2026-07-17): promoted to the
    # danger/destructive/error red on 2026-07-20 (Adrian vetoed the DNA's
    # code-only reservation - red = delete/error). magma_dim = hover/border.
    "magma":      "#FF3B3B",
    "magma_dim":  "#B52A2A",
    "magma_glow": "rgba(255, 59, 59, 56)",
    # type — Mindmeld 2.0 (2026-07-13): one family, JetBrains Mono. Display is
    # the BOLD weight set large (the outlined tri-color treatment lives in the
    # banner PNGs, not in QSS text). VT323 is RETIRED per the brand DNA.
    "font_display": "JetBrains Mono",
    "font_body":    "JetBrains Mono",
    # rounded forms (2.0): generous radius replaces the 1.0 hard-2px corner.
    # QSS has no variables, so these are applied inline in mindmeld.qss and
    # kept here for code that draws (paintEvent, custom widgets) to match.
    "radius_sm":   "6px",   # buttons, inputs, combos, spinboxes
    "radius_md":   "8px",   # panels, cards, lists, sections, menus
    "radius_pill": "10px",  # status pills, soft chips
}

# Convenience aliases for the most common tokens
CARBON = TOKENS["carbon"]
IRON = TOKENS["iron"]
BONE = TOKENS["bone"]
PLASMA = TOKENS["plasma"]
EMBER = TOKENS["ember"]
BONE_DIM = TOKENS["bone_dim"]
BONE_FAINT = TOKENS["bone_faint"]
PLASMA_DIM = TOKENS["plasma_dim"]
EMBER_DIM = TOKENS["ember_dim"]
FLARE = TOKENS["flare"]
AMBER = TOKENS["amber"]
MAGMA = TOKENS["magma"]
MAGMA_DIM = TOKENS["magma_dim"]


# ============================================================
# Coach card — the standard popover card (Adrian's coachmark
# mockup, 2026-07-19). ONE frame + typography source consumed by
# the first-run tour cards, hover annotations, toasts, and the
# progress card, so every floating card in the toolset reads as
# the same object: iron ground, 1px accent border (plasma unless
# the card carries a warn/error accent), radius_md corners; bone
# bold title over bone_dim body; ember caps eyebrow.
# ============================================================

def coach_frame_qss(selector: str, accent: Optional[str] = None) -> str:
    """QSS for the standard card frame, scoped to `selector`
    (e.g. 'QFrame#mmToast'). Accent defaults to plasma; pass AMBER /
    FLARE for warn / error cards (color law: plasma = go, and a red
    message in a green frame would lie)."""
    return (f"{selector} {{"
            f" background-color: {IRON};"
            f" border: 1px solid {accent or PLASMA};"
            f" border-radius: {TOKENS['radius_md']};"
            f" }}")


COACH_TITLE_QSS = (f"color: {BONE}; background: transparent; "
                   f"font-family: '{TOKENS['font_display']}'; "
                   f"font-size: 14px; font-weight: 700;")

COACH_BODY_QSS = (f"color: {BONE_DIM}; background: transparent; "
                  f"font-size: 12px;")

COACH_EYEBROW_QSS = (f"color: {EMBER}; background: transparent; "
                     f"font-weight: 700; letter-spacing: 2px;")


# ============================================================
# Paths
# ============================================================

_HERE = Path(__file__).parent
_QSS_PATH = _HERE / "mindmeld.qss"
_FONTS_DIR = _HERE / "fonts"
_ICONS_DIR = _HERE / "icons"

_FONTS_LOADED = False
_QSS_CACHE: Optional[str] = None


# ============================================================
# Stylesheet + font loading
# ============================================================

def get_qss() -> str:
    """Read mindmeld.qss from disk (cached on first call)."""
    global _QSS_CACHE
    if _QSS_CACHE is None:
        _QSS_CACHE = _QSS_PATH.read_text(encoding="utf-8")
    return _QSS_CACHE


def reload_qss() -> str:
    """Force-reread mindmeld.qss. Use during stylesheet authoring."""
    global _QSS_CACHE
    _QSS_CACHE = None
    return get_qss()


def load_mindmeld_fonts() -> list[str]:
    """Register Mindmeld fonts with the application font DB.

    Loads the JetBrains Mono family (Regular / Medium / Bold) from
    `mindmeld/fonts/`. Missing fonts fall back to the platform's monospace
    stack (Consolas / Courier New) — the tools still work, they just lose the
    exact display face. (VT323 is retired under Mindmeld 2.0 but its file may
    still sit in the folder; loading it is harmless.) Returns the family names
    that were successfully registered.
    """
    global _FONTS_LOADED
    loaded: list[str] = []

    if not _FONTS_DIR.exists():
        _FONTS_LOADED = True
        return loaded

    for font_file in sorted(_FONTS_DIR.glob("*.[to]tf")):
        font_id = QFontDatabase.addApplicationFont(str(font_file))
        if font_id == -1:
            continue
        for family in QFontDatabase.applicationFontFamilies(font_id):
            if family not in loaded:
                loaded.append(family)

    _FONTS_LOADED = True
    return loaded


def apply(target: QWidget, *, load_fonts: bool = True) -> None:
    """Apply the Mindmeld stylesheet to a widget tree.

    Usage:
        class MyToolWindow(QDialog):
            def __init__(self, parent=None):
                super().__init__(parent)
                mindmeld_style.apply(self)
                ...
    """
    if load_fonts and not _FONTS_LOADED:
        load_mindmeld_fonts()
    target.setStyleSheet(get_qss())


def restyle(widget: QWidget) -> None:
    """Force a widget to re-evaluate its stylesheet.

    Call this after changing a `mindmeld` dynamic property at runtime —
    QSS doesn't auto-reapply on property changes.
    """
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def tag(widget: QWidget, variant: str) -> QWidget:
    """Set the `mindmeld` dynamic property and restyle. Returns the widget."""
    widget.setProperty("mindmeld", variant)
    restyle(widget)
    return widget


# ============================================================
# Widget factories
# ============================================================

def button(text: str, kind: str = "default", *, parent: Optional[QWidget] = None) -> QPushButton:
    """Create a Mindmeld-styled button.

    kind: 'default' | 'primary' | 'danger' | 'ghost'
    """
    btn = QPushButton(text.upper(), parent)
    if kind != "default":
        btn.setProperty("mindmeld", kind)
    return btn


def field_label(text: str, *, parent: Optional[QWidget] = None) -> QLabel:
    """Field label — ember accent, caps.

    Caller passes plain text; this helper handles the casing. (The 1.0 `//`
    prefix was retired in the Mindmeld 2.0 pass, 2026-07-13 — it was a
    console-era motif the brand DNA drops.)
    """
    lbl = QLabel(text.upper(), parent)
    lbl.setProperty("mindmeld", "field-label")
    return lbl


def caps_label(text: str, *, accent: bool = False,
               parent: Optional[QWidget] = None) -> QLabel:
    """Small caps label — neutral, used for column headers, info chips.

    `accent=True` takes ember instead of bone_dim, for a caps label that
    NAMES a region rather than annotating one (a panel header). Keeps the
    caps sizing, so it stays a header rather than becoming a field label.
    """
    lbl = QLabel(text.upper(), parent)
    lbl.setProperty("mindmeld", "caps-accent" if accent else "caps")
    return lbl


def display_label(text: str, *, large: bool = False, accent: bool = False,
                  parent: Optional[QWidget] = None) -> QLabel:
    """Display-typography label — JetBrains Mono Bold, large.

    `large=True` uses the 40px size (window titles).
    `accent=True` colors it plasma.
    """
    lbl = QLabel(text.upper(), parent)
    if accent:
        variant = "display-accent"
    elif large:
        variant = "display-lg"
    else:
        variant = "display"
    lbl.setProperty("mindmeld", variant)
    return lbl


def brand_label(name: str, *, parent: Optional[QWidget] = None) -> QLabel:
    """Tool brand label — JetBrains Mono Bold large, case preserved verbatim.

    The FALLBACK for a tool's identity when its banner PNG is missing (2.0
    leads with the banner). Unlike `display_label`, this does NOT upper-case —
    tool names render as authored.
    """
    lbl = QLabel(name, parent)
    lbl.setProperty("mindmeld", "display-lg")
    return lbl


def helper_label(text: str, *, parent: Optional[QWidget] = None) -> QLabel:
    """Helper text — small, dim, sits below an input."""
    lbl = QLabel(text, parent)
    lbl.setProperty("mindmeld", "helper")
    return lbl


def pill(text: str, kind: str = "idle", *, parent: Optional[QWidget] = None) -> QLabel:
    """Status pill — rounded chip indicating state.

    kind: 'ok' | 'warn' | 'idle'
    (The 1.0 `[ BRACKETED ]` text was retired in the Mindmeld 2.0 pass,
    2026-07-13; the rounded chip IS the pill now.)
    """
    lbl = QLabel(text.upper(), parent)
    lbl.setProperty("mindmeld", f"pill-{kind}")
    lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    return lbl


def group_box(title: str, *, accent: bool = False,
              parent: Optional[QWidget] = None) -> QGroupBox:
    """Titled group frame around a set of related controls.

    Default is the quiet form: iron_3 outline, ember title. `accent=True`
    takes the ember outline too, for a group that should read as one
    named instrument rather than background chrome.

    Ember is the label accent, so naming a region is exactly its job.
    It never becomes a CTA or a state colour, and one accented group per
    region is the limit before the emphasis stops meaning anything.
    """
    box = QGroupBox(title, parent)
    if accent:
        box.setProperty("mindmeld", "accent")
    return box


def horizontal_rule(*, parent: Optional[QWidget] = None) -> QFrame:
    """1px horizontal divider line."""
    f = QFrame(parent)
    f.setProperty("mindmeld", "rule-h")
    f.setFrameShape(QFrame.NoFrame)
    return f


def vertical_rule(*, parent: Optional[QWidget] = None) -> QFrame:
    """1px vertical divider line."""
    f = QFrame(parent)
    f.setProperty("mindmeld", "rule-v")
    f.setFrameShape(QFrame.NoFrame)
    return f


def ascii_rule(width: int = 80, *, parent: Optional[QWidget] = None) -> QLabel:
    """ASCII-character horizontal rule (═══...). Pure decoration."""
    lbl = QLabel("═" * width, parent)
    lbl.setProperty("mindmeld", "ascii-rule")
    return lbl


# ============================================================
# Iconography
# ============================================================

def icon(name: str) -> QIcon:
    """Load a Mindmeld icon by name from mindmeld/icons/<name>.png.

    Returns an empty QIcon if the file is missing — call sites stay
    safe even before icons are authored.
    """
    path = _ICONS_DIR / f"{name}.png"
    if path.exists():
        return QIcon(str(path))
    return QIcon()


def pixmap(name: str) -> QPixmap:
    """Load a Mindmeld pixmap by name from mindmeld/icons/<name>.png."""
    path = _ICONS_DIR / f"{name}.png"
    if path.exists():
        return QPixmap(str(path))
    return QPixmap()


# ============================================================
# Log tagging — used by LoggerWidget
# ============================================================

LOG_TAG_COLORS = {
    "OK":   PLASMA,
    "INFO": BONE_DIM,
    "WARN": EMBER,
    "ERR":  EMBER,
    "RUN":  BONE,
    ">>":   PLASMA,
}
