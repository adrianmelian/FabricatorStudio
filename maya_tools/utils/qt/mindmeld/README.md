# utils.qt.mindmeld

**Mindmeld** — design system for the Fabricator Maya toolset.
Production PySide6 stylesheet + Python helpers + iconography.

---

## Files

```
mindmeld/
├── __init__.py        — package init, re-exports mindmeld_style
├── mindmeld_style.py     — apply(), widget factories, TOKENS dict
├── mindmeld.qss          — the actual Qt stylesheet
├── fonts/             — drop-in font folder (see "Fonts" below)
├── icons/             — drop-in icon folder (see "Icons" below)
└── README.md          — this file
```

---

## Apply to a tool

One line:

```python
from maya_tools.utils.qt.mindmeld import mindmeld_style

class MyToolWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        mindmeld_style.apply(self)
        ...
```

`apply()` loads the stylesheet, registers fonts (if present in `fonts/`),
and applies the QSS to the widget tree. Idempotent — safe to call from
every tool's `__init__`.

---

## Widget factories

Use these instead of raw `QPushButton(...)` / `QLabel(...)` so the system
stays consistent.

| Factory | What it makes |
|---|---|
| `button(text, kind)` | `QPushButton`. `kind`: `default`, `primary`, `danger`, `ghost`. Caps the text. |
| `field_label(text)` | Ember field label above an input. Caps the text. (2.0: no `//` prefix.) |
| `caps_label(text)` | Small caps label, neutral. |
| `display_label(text, large=False, accent=False)` | JetBrains Mono Bold display heading. |
| `helper_label(text)` | Small dim text below an input. |
| `pill(text, kind)` | Rounded status chip. `kind`: `ok`, `warn`, `idle`. (2.0: no `[ ]` brackets.) |
| `horizontal_rule()` | 1px divider. |
| `vertical_rule()` | 1px vertical divider. |
| `ascii_rule(width)` | `═══` decorative rule. |

Manual variant tagging (when you can't use a factory):

```python
my_panel.setProperty("mindmeld", "panel")
mindmeld_style.restyle(my_panel)   # re-evaluate stylesheet after property change
```

Or shorthand:

```python
mindmeld_style.tag(my_panel, "panel")
```

---

## Tokens

Raw colors are exposed in `TOKENS` for code that draws (paintEvent,
custom painting, log row coloring):

```python
from maya_tools.utils.qt.mindmeld import mindmeld_style

mindmeld_style.PLASMA   # "#7CFFB2"
mindmeld_style.EMBER    # "#FF7A3D"
mindmeld_style.TOKENS["plasma_dim"]
```

| Token | Hex | Role |
|---|---|---|
| `carbon` | `#0B0E10` | App background |
| `iron` | `#1C2126` | Panel surface |
| `bone` | `#E8E0D0` | Body text |
| `plasma` | `#7CFFB2` | Primary action / success |
| `ember` | `#FF7A3D` | Warning / danger / accent labels |
| `iron_2` | `#262C33` | Panel raised |
| `iron_3` | `#353D45` | Border / divider |
| `bone_dim` | `#8A8378` | Secondary text |
| `bone_faint` | `#5A554D` | Placeholder / disabled |
| `plasma_dim` | `#4FB888` | Plasma sub-element |
| `ember_dim` | `#B5532A` | Ember sub-element |

---

## Fonts

Mindmeld 2.0 is **one family: JetBrains Mono** (a free Google Font). Display
is the **Bold** weight set large; body is Regular / Medium. The QSS falls back
to Consolas / Courier New if it isn't installed, so tools work either way.
(VT323, the 1.0 pixel display face, is retired; its `.ttf` may still sit in the
folder but nothing references it.)

To install:

1. Download JetBrains Mono from Google Fonts:
   https://fonts.google.com/specimen/JetBrains+Mono
2. Drop the `.ttf` files into [`fonts/`](fonts/):
   ```
   fonts/
   ├── JetBrainsMono-Regular.ttf
   ├── JetBrainsMono-Medium.ttf
   └── JetBrainsMono-Bold.ttf
   ```
3. Restart Maya. `mindmeld_style.apply()` registers them automatically.

---

## Icons

Tool icons live in [`icons/`](icons/). Naming convention: lowercase,
`<tool>.png`. Use 32×32 PNGs (rendered from the SVGs in the showcase
HTML — `mark-af`, `icon-ks`, etc).

```python
btn.setIcon(mindmeld_style.icon("ks"))
window.setWindowIcon(mindmeld_style.icon("mindmeld"))
```

Returns an empty `QIcon` if the file is missing — safe before icons are
authored.

---

## Authoring the stylesheet

`mindmeld.qss` is plain Qt QSS. Edit it freely. To live-reload while
authoring:

```python
mindmeld_style.reload_qss()
mindmeld_style.apply(my_window)
```

Two QSS limitations to remember:

- **No `letter-spacing`** — Qt ignores it. The factories handle this in
  Python (`field_label` etc. already cap their text).
- **No `text-transform`** — same fix: pre-uppercase in the factory.

---

## Variant cheat sheet

Variants are `mindmeld` dynamic properties. Selectors look like
`QPushButton[mindmeld="primary"]`.

| Widget | Variant property | Result |
|---|---|---|
| `QFrame` | `panel` | Iron bg + border (use for grouped sections) |
| `QFrame` | `surface` | Iron bg only |
| `QFrame` | `rule-h` / `rule-v` | 1px divider |
| `QPushButton` | `primary` | Plasma — primary action |
| `QPushButton` | `danger` | Ember — destructive action |
| `QPushButton` | `ghost` | Borderless until hover |
| `QLabel` | `field-label` | Ember field label |
| `QLabel` | `caps` | Bone-dim small caps |
| `QLabel` | `display` / `display-lg` / `display-accent` | JetBrains Mono Bold sizes |
| `QLabel` | `helper` | Small dim text |
| `QLabel` | `dim` / `faint` / `ok` / `warn` | Color-only variants |
| `QLabel` | `pill-ok` / `pill-warn` / `pill-idle` | Bordered chip |
| `QLabel` | `ascii-rule` | `═══` rule |
| `QWidget` | `titlebar` / `statusbar` | Custom chrome backgrounds |
