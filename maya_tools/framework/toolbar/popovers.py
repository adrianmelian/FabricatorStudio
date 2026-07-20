"""Popover content widgets for the Phase B strip (spec section 5 inventory).
Each class is a small panel of FULL-TEXT buttons (CP-B, Adrian 2026-07-02:
Animo-style full words at readable sizes, not 26px chips - reference
Animo_v7.0.0 launchers: ~28-33px button heights, panel widths ~170-240px);
every action routes through toolbar_commands.run_command (one undo step +
error surface). Backends import lazily inside handlers, keeping module
import Qt-only. Button skin: mindmeld.qss QFrame#fabricator_toolbar_popover
QPushButton (outranks the strip's compact chip rule)."""
from __future__ import annotations

from functools import partial

from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QRadioButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from maya_tools.framework.toolbar import dpi
from maya_tools.utils.qt.mindmeld import mindmeld_style


# Boxed popover tabs (Adrian, 2026-07-10): outline every tab so it reads as a
# tab, the SELECTED one plasma-green and the rest ember-orange (palette carbon/
# iron/plasma/ember). Applied inline to the Export + Library popover QTabWidgets
# only, so it overrides the global flat-underline QTabBar rule for those widget
# subtrees without touching any other tabs in the app.
_BOXED_TAB_QSS = """
QTabWidget::pane {
    border: 1px solid #7CFFB2;
    background-color: #0B0E10;
    top: -1px;
}
QTabBar::tab {
    border: 1px solid #FF7A3D;
    border-bottom: none;
    border-top-left-radius: 3px;
    border-top-right-radius: 3px;
    padding: 6px 16px;
    margin-right: 3px;
    font-size: 10px;
    font-weight: 600;
    color: #FF7A3D;
    background-color: rgba(255, 122, 61, 0.12);
}
QTabBar::tab:hover {
    background-color: rgba(255, 122, 61, 0.22);
    color: #FF7A3D;
}
QTabBar::tab:selected {
    border: 1px solid #7CFFB2;
    border-bottom: none;
    color: #7CFFB2;
    background-color: rgba(124, 255, 178, 0.18);
}
"""


def _run(label, spec_fn, *args):
    from maya_tools.framework.toolbar import toolbar_commands
    return toolbar_commands.run_command(label, spec_fn, *args)


def _text_button(label, tooltip, handler, min_width=170):
    """Full-word popover button (the panel skin lives in mindmeld.qss)."""
    btn = QPushButton(label)
    btn.setToolTip(tooltip)
    btn.setFixedHeight(dpi.scaled(28))
    btn.setMinimumWidth(dpi.scaled(min_width))
    if handler is not None:
        btn.clicked.connect(lambda *_: handler())
    return btn


def _button_column(parent, rows):
    """rows: [(label, tooltip, handler)] -> vertical column of text buttons."""
    lay = QVBoxLayout(parent); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)
    for label, tip, handler in rows:
        lay.addWidget(_text_button(label, tip, handler))
    return lay


def _slider_row(lay, label, minimum, maximum, default, steps=1000):
    """Labeled horizontal QSlider mapped to [minimum, maximum]; returns a
    zero-arg value() callable. Popover sliders are plain rows - the in-strip
    ExpandingSlider is a different animal."""
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider
    row = QHBoxLayout(); row.setSpacing(4)
    row.addWidget(QLabel(label))
    slider = QSlider(_Qt.Horizontal)
    slider.setRange(0, steps)
    span = maximum - minimum
    slider.setValue(int(round((default - minimum) / span * steps)))
    value_label = QLabel(f"{default:g}")
    def value():
        return minimum + slider.value() / steps * span
    slider.valueChanged.connect(lambda _: value_label.setText(f"{value():.2f}"))
    row.addWidget(slider, 1); row.addWidget(value_label)
    lay.addLayout(row)
    return value


def _settings_customize_layout():
    # Entering edit mode closes every open menu/popover - the menu that
    # launched this row is already dismissed by the time it fires.
    from maya_tools.framework.toolbar import reorder
    reorder.enter_edit_mode()


def _settings_show_hide_tools():
    from maya_tools.framework.toolbar import tool_visibility
    tool_visibility.show_tool_visibility()


def _settings_open_window():
    """Left-click on the gear opens the same window; this row is the
    discoverable path from the right-click menu."""
    from maya_tools.framework import settings_ui
    settings_ui.SettingsUI.show_window()


def settings_menu():
    """Right-hand gear menu (Adrian, 2026-07-07: the settings popover becomes
    a click-menu). Settings window on top (2026-07-18, same target as the
    gear's left-click), then dock top/bottom/floating (side docks retired
    2026-07-03), customize layout, show/hide tools, connect an external AI
    client, hide the toolbar."""
    from maya_tools.framework.toolbar import toolbar_app
    rows = [("Settings...",
             "Bind this machine's project roots and set the shared configs "
             "location",
             _settings_open_window),
            None]
    rows += [(f"Dock: {m.capitalize()}", f"Move the toolbar to {m}",
              partial(toolbar_app.set_dock_mode, m))
             for m in ("top", "bottom", "floating")]
    rows.append(None)
    rows.append(("Customize Layout",
                 "Drag tools to reorder them; Esc finishes",
                 _settings_customize_layout))
    rows.append(("Show / Hide Tools",
                 "Pick which tools appear on the strip",
                 _settings_show_hide_tools))
    rows.append(None)
    rows.append(("Connect AI (external clients)...",
                 "Start/stop the read-only AI bridge for external MCP clients",
                 open_connect_ai_window))
    rows.append(None)
    rows.append(("Hide Toolbar", "Hide (FabricatorStudio menu re-shows)", toolbar_app.toggle))
    return rows


_SITE = "https://fabricator.studio"


def _open_url(url: str):
    # Menu rows connect raw handlers (no run_command wrap) - a browser
    # failure must degrade to a console trace, never a crashed Qt slot.
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        import traceback
        traceback.print_exc()


def about_menu():
    """Right-click rows for the Bridge brandmark button (was AboutPopover,
    Wave B 2026-07-03). The site is LIVE (Adrian, 2026-07-19): every row
    opens its page. The About row IS the website pointer (the separate Go
    to Website row was cut as a duplicate, Adrian 2026-07-19); local About
    details live on left-click (show_about_card). File a Bug points at the
    GitHub tracker, the bug front door per the launch posture; the repo is
    private until the v1 squash-publish, so that link 404s for outsiders
    until launch day. Settings stays on the right-hand gear
    (settings_menu)."""
    return [
        ("About", "fabricator.studio - what FabricatorStudio is",
         partial(_open_url, _SITE)),
        ("Tutorials", "Video tutorials",
         partial(_open_url, f"{_SITE}/tutorials/")),
        ("Help", "Tool documentation",
         partial(_open_url, f"{_SITE}/docs/")),
        None,
        ("File a Bug", "Report an issue on the GitHub tracker",
         partial(_open_url,
                 "https://github.com/adrianmelian/FabricatorStudio/issues")),
    ]


_ABOUT_CARD = None


def _about_version_lines() -> list[str]:
    """VERSION.txt sits beside maya_tools/ (copied there by the installer;
    wire-only trees read it from Fabricator_Data/ itself). Absent file =
    a development checkout, reported honestly, never an error."""
    from maya_tools.framework.toolbar.widgets import icon_button
    try:
        raw = (icon_button._ICON_DIR.parent / 'VERSION.txt').read_text(
            encoding='utf-8')
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if lines:
            return lines[:2]     # 'Fabricator <version>' + 'Built from: <ref>'
    except OSError:
        pass
    return ['Development build (repo checkout, no VERSION.txt)']


def about_annotation():
    """The About card content - build version, license, copyright - headed
    by the fullname banner (the text title is the fallback when the PNG is
    missing, the brand-bar rule). This IS the Bridge button's hover
    annotation (manifest 'annotation' key, Adrian 2026-07-19: the doc card
    was covering the About, so the About replaced it) and the same card a
    left-click shows. License wording mirrors the README/site summary
    (claim discipline: one phrasing everywhere)."""
    from maya_tools.framework.annotations import Annotation
    from maya_tools.framework.toolbar.widgets import icon_button
    banner = icon_button._ICON_DIR / 'fs_fullname_banner.png'
    banner_path = str(banner) if banner.is_file() else ''
    text = '\n'.join(_about_version_lines() + [
        '',
        'License: Business Source License 1.1',
        'Free for organizations under 2 million USD annual revenue;',
        'one flat studio license above. Personal and portfolio use is',
        'always free.',
        '',
        '© 2026 FabricatorStudio LLC',
        'fabricator.studio',
    ])
    return Annotation(title='' if banner_path else 'FabricatorStudio',
                      text=text, banner=banner_path)


def show_about_card():
    """Left-click on the Bridge brandmark (Adrian, 2026-07-19): show the
    same About card the hover annotation shows (click = show as well).
    Same lifetime as every hover card: it stays while the cursor is on or
    near it and hides once the cursor walks away."""
    global _ABOUT_CARD
    from maya_tools.utils.qt.hover_annotation import HoverAnnotation
    if _ABOUT_CARD is None:
        _ABOUT_CARD = HoverAnnotation()
    _ABOUT_CARD.show_annotation(about_annotation(), None)


def client_config_snippet(client: str, port: int) -> str:
    """Copy-paste MCP config snippet for `client` ('claude-code' |
    'claude-desktop' | 'cursor'). --port is embedded ONLY when it differs
    from the bridge's DEFAULT_PORT - imported lazily here (see
    ConnectAIPopover's docstring: this module stays bridge/handlers/maya
    import-free at module scope). claude-desktop/cursor share a single
    PASTE-COMPLETE mcpServers config object (quality review F4, 2026-07-06:
    a bare "fabricator": {...} fragment left a user who doesn't know the
    file's shape with nowhere to drop it) - the whole object is droppable
    as-is, not a fragment to be spliced by hand."""
    from maya_tools.framework.toolbar.ai_bridge.protocol import DEFAULT_PORT
    non_default = port != DEFAULT_PORT

    if client == "claude-code":
        cmd = "claude mcp add fabricator -- uvx fabricator-mcp"
        if non_default:
            cmd += f" --port {port}"
        return cmd

    # claude-desktop and cursor share the same full mcpServers config block.
    port_args = f', "--port", "{port}"' if non_default else ""
    return ('{\n'
            '  "mcpServers": {\n'
            '    "fabricator": {\n'
            '      "command": "uvx",\n'
            f'      "args": ["fabricator-mcp"{port_args}]\n'
            '    }\n'
            '  }\n'
            '}')


class ConnectAIPopover(QWidget):
    """Connect-your-AI bridge control panel (Task 6): start/stop the
    read-only MCP bridge (ai_bridge), a Start-with-Maya autostart pref,
    and a copy-paste config snippet per client. ai_bridge - and,
    transitively, handlers.py's maya import - is pulled in LAZILY inside
    the methods below, never at module or class scope, so importing
    popovers.py stays Qt-only (matching every other class in this file)."""

    CLIENTS = ("claude-code", "claude-desktop", "cursor")

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        self._status = mindmeld_style.pill("Stopped", "idle")
        lay.addWidget(self._status)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(4)
        btn_row.addWidget(_text_button("Start", "Start the read-only AI bridge",
                                       self._start, min_width=80))
        btn_row.addWidget(_text_button("Stop", "Stop the AI bridge",
                                       self._stop, min_width=80))
        lay.addLayout(btn_row)

        self._autostart = QCheckBox("Start with Maya")
        self._autostart.setChecked(self._load_autostart_pref())
        self._autostart.toggled.connect(self._set_autostart)
        lay.addWidget(self._autostart)

        lay.addWidget(mindmeld_style.horizontal_rule())

        lay.addWidget(mindmeld_style.field_label("Client"))
        self._client = QComboBox()
        self._client.addItems(list(self.CLIENTS))
        lay.addWidget(self._client)

        self._snippet = QPlainTextEdit()
        self._snippet.setReadOnly(True)
        self._snippet.setFixedHeight(dpi.scaled(90))
        lay.addWidget(self._snippet)

        self._client.currentIndexChanged.connect(self._refresh_snippet)

        lay.addWidget(_text_button("Copy", "Copy the config snippet to the clipboard",
                                   self._copy, min_width=80))

        lay.addWidget(mindmeld_style.helper_label(
            "READ-ONLY: your AI can see, never touch."))

        self._refresh_status()

    # -- prefs (Start-with-Maya) -----------------------------------------

    @staticmethod
    def _load_autostart_pref() -> bool:
        try:
            from maya_tools.framework.toolbar import toolbar_prefs
            connect_ai = toolbar_prefs.load_prefs().get("connect_ai") or {}
            return bool(connect_ai.get("enabled_autostart", False))
        except Exception:
            return False

    def _set_autostart(self, checked: bool) -> None:
        # Quality review F1, 2026-07-06: a prefs-write failure (read-only
        # home, locked file) must never escape this checkbox's toggled
        # slot into Qt - surface it on the status label (no cmds here;
        # this module stays Qt-only), matching _load_autostart_pref's
        # read-side guard.
        try:
            from maya_tools.framework.toolbar import toolbar_prefs
            prefs = toolbar_prefs.load_prefs()
            connect_ai = dict(prefs.get("connect_ai") or {})
            connect_ai["enabled_autostart"] = bool(checked)
            prefs["connect_ai"] = connect_ai
            toolbar_prefs.save_prefs(prefs)
        except Exception as exc:
            self._set_status(f"Error: {exc}", "warn")

    # -- bridge lifecycle -------------------------------------------------

    def _start(self) -> None:
        try:
            from maya_tools.framework.toolbar import ai_bridge
            ai_bridge.start()
        except Exception as exc:
            self._set_status(f"Error: {exc}", "warn")
            return
        self._refresh_status()

    def _stop(self) -> None:
        try:
            from maya_tools.framework.toolbar import ai_bridge
            ai_bridge.stop()
        except Exception as exc:
            self._set_status(f"Error: {exc}", "warn")
            return
        self._refresh_status()

    def _refresh_status(self) -> None:
        try:
            from maya_tools.framework.toolbar import ai_bridge
            running = ai_bridge.is_running()
            port = ai_bridge.current_port() if running else None
        except Exception:
            running, port = False, None
        if running:
            self._set_status(f"Listening on {port}", "ok")
        else:
            self._set_status("Stopped", "idle")
        self._refresh_snippet()

    def _set_status(self, text: str, kind: str) -> None:
        # Quality review F5, 2026-07-06: text.upper() mangles error DETAIL
        # (paths, "[Errno 98]..."). Short status words (Stopped / Listening
        # on N) still get the caps pill look via mindmeld_style.pill()'s
        # own formatting; the warn/error path keeps the pill a fixed short
        # caps word and rides the raw, un-cased detail on the tooltip
        # instead of stuffing it into the label text uppercased.
        if kind == "warn":
            self._status.setText("[ ERROR ]")
            self._status.setToolTip(text)
        else:
            self._status.setText(f"[ {text.upper()} ]")
            self._status.setToolTip("")
        mindmeld_style.tag(self._status, f"pill-{kind}")

    # -- client config snippet --------------------------------------------

    def _current_or_default_port(self) -> int:
        try:
            from maya_tools.framework.toolbar import ai_bridge
            port = ai_bridge.current_port()
            if port is not None:
                return port
        except Exception:
            pass
        from maya_tools.framework.toolbar.ai_bridge.protocol import DEFAULT_PORT
        try:
            from maya_tools.framework.toolbar import toolbar_prefs
            connect_ai = toolbar_prefs.load_prefs().get("connect_ai") or {}
            return int(connect_ai.get("port", DEFAULT_PORT))
        except Exception:
            return DEFAULT_PORT

    def _refresh_snippet(self) -> None:
        # Quality review F3, 2026-07-06: reachable straight from the
        # combo's currentIndexChanged slot - no slot in this widget may
        # ever throw (its only real risk is client_config_snippet's lazy
        # DEFAULT_PORT import).
        try:
            port = self._current_or_default_port()
            client = self._client.currentText()
            self._snippet.setPlainText(client_config_snippet(client, port))
        except Exception:
            pass

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._snippet.toPlainText())


_CONNECT_AI_WIN = None


def open_connect_ai_window():
    """Settings > Connect AI: the external/BYO-client path (bridge
    start/stop + client config snippets), now that the toolbar button is
    Reggie's. Parented to Maya's main window with the mindmeld skin
    applied directly (the strip-root cascade never reaches a standalone
    window) - the tool_visibility.show_tool_visibility pattern."""
    global _CONNECT_AI_WIN
    if _CONNECT_AI_WIN is not None:
        try:
            _CONNECT_AI_WIN.close()
            _CONNECT_AI_WIN.deleteLater()
        except Exception:
            pass
        _CONNECT_AI_WIN = None
    parent = None
    try:
        from maya_tools.utils.maya.gui import get_maya_window
        parent = get_maya_window()
    except Exception:
        pass
    from PySide6.QtCore import Qt
    w = ConnectAIPopover(parent=parent)
    w.setWindowFlags(Qt.WindowType.Tool)
    w.setWindowTitle("Connect your AI (external clients)")
    mindmeld_style.apply(w)
    w.show()
    _CONNECT_AI_WIN = w


def create_menu():
    """Make an object at the selection's center (origin if nothing selected).
    Joint is FIRST and uses the SMART joint create (Adrian, 2026-07-03: the
    Alt+J script - child of the last-selected joint, so presses chain); the
    rest are poly primitives."""
    from maya_tools.framework.toolbar import toolbar_commands as tc

    def smart_joint():
        from maya_tools.skeleton import skeleton_utils
        _run("Create Joint", skeleton_utils.create_joint)

    helpers = [("Null", "null"), ("Locator", "locator")]
    polys = [("Cube", "cube"), ("Sphere", "sphere"),
             ("Cylinder", "cylinder"), ("Plane", "plane")]

    def prim_row(label, kid):
        return (label, f"Create a {label.lower()} at selection center / origin",
                partial(_run, f"Create {label}", tc.create_primitive, kid))

    rows = [("Joint", "Create a joint (child of the last-selected joint, "
             "else at origin); chains on repeat", smart_joint)]
    rows.append(None)
    rows += [prim_row(label, kid) for (label, kid) in helpers]
    rows.append(None)
    rows += [prim_row(label, kid) for (label, kid) in polys]
    return rows


class LibraryPopover(QWidget):
    """Both libraries, FULL tools, tabbed (Adrian, 2026-07-05: the big
    boys get the popover treatment; his call — NO caching, fresh per
    open, "if I ever popped in my own gif all I need to do is restart
    the tool"). POSE hosts PoseLibraryWindow, ANIM hosts
    AnimLibraryWindow — each embedded=True (sweep-safe objectName,
    brand + log trimmed). Tabs build LAZILY: each tool scans its
    library folder + loads the ACTIVE character's thumbnails on
    construction (the only-active-character guardrail lives in the
    tools themselves), and a click-open should pay for one tool, not
    two."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._tabs = QTabWidget()
        # Boxed, coloured tabs matching the Export popover (Adrian, 2026-07-10):
        # live tab ember-orange, the one behind it plasma-green. Scoped inline
        # to this QTabWidget only.
        self._tabs.setStyleSheet(_BOXED_TAB_QSS)
        self._tabs.addTab(QWidget(), "POSE")
        self._tabs.addTab(QWidget(), "ANIM")
        lay.addWidget(self._tabs)
        self._built = set()
        self._tabs.currentChanged.connect(self._ensure_tab)
        self._ensure_tab(0)

    @staticmethod
    def _make_banner(icon_name: str) -> QLabel | None:
        """Per-tab brand banner (Adrian, 2026-07-11: the POSE and ANIM tabs
        each get their own studio banner instead of one shared library
        banner on the popover frame). Silently skipped if the PNG is
        missing."""
        from PySide6.QtGui import QPixmap
        from PySide6.QtCore import Qt
        from maya_tools.framework.toolbar.widgets import icon_button
        pix = QPixmap(str(icon_button._ICON_DIR / icon_name))
        if pix.isNull():
            return None
        banner = QLabel()
        banner.setPixmap(pix.scaledToHeight(
            40, Qt.TransformationMode.SmoothTransformation))
        banner.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        banner.setContentsMargins(8, 6, 0, 4)
        return banner

    def _ensure_tab(self, index: int):
        if index in self._built:
            return
        self._built.add(index)
        from PySide6.QtCore import Qt
        if index == 0:
            from maya_tools.animation.pose_library.ui import PoseLibraryWindow
            tool = PoseLibraryWindow(embedded=True)
            # QDialog turns Escape into reject() — route it to the
            # frame so Escape dismisses the popover instead of
            # hollowing a tab. (AnimLibraryWindow is a plain QWidget —
            # no reject path to route.)
            tool.rejected.connect(lambda: self.window().close())
            banner = self._make_banner('fs_pose_studio.png')
        else:
            from maya_tools.animation.anim_library.ui import AnimLibraryWindow
            tool = AnimLibraryWindow(embedded=True)
            banner = self._make_banner('fs_anim_studio.png')
        tool.setWindowFlags(Qt.WindowType.Widget)
        page = self._tabs.widget(index)
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.setSpacing(0)
        if banner is not None:
            page_lay.addWidget(banner)
        page_lay.addWidget(tool)

    def open_full_window(self):    # drag-bar expand hook (item 3, 2026-07-03)
        if self._tabs.currentIndex() == 0:
            from maya_tools.animation.pose_library.ui import PoseLibraryWindow
            PoseLibraryWindow.show_window()
        else:
            from maya_tools.animation.anim_library.ui import AnimLibraryWindow
            AnimLibraryWindow.show_window()


class FabricatorPopover(QWidget):
    """Single contextual button (item 4, 2026-07-03): GREEN Build when the rig
    is unbuilt, ORANGE Unbuild when built. The owner button's can_open guard
    (fabricator_rig_present) suppresses this panel entirely when it isn't a
    fabricator scene or the rig is referenced (anim scene), so it only ever
    renders for a live local rig - hence exactly one button, no empty state.
    After a Build/Unbuild the panel REBUILDS in place so it flips Build<->Unbuild
    (Adrian, 2026-07-03: it was staying stale)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(2)
        self._rebuild()

    def _rebuild(self):
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()          # deferred: safe to drop the button
                                         # that is mid-click (delete-sender case)
        from maya_tools.framework.toolbar import toolbar_commands as tc
        if tc.fabricator_is_built():
            btn = _text_button("Unbuild", "Unbuild the rig back to guides",
                               self._unbuild, min_width=120)
            btn.setObjectName("fs_unbuild")
        else:
            btn = _text_button("Build", "Build the rig from the blueprint",
                               self._build, min_width=120)
            btn.setObjectName("fs_build")
        self._lay.addWidget(btn)

    def _build(self):
        from maya_tools.framework.toolbar import toolbar_commands as tc
        _run("Build", tc.fabricator_build_quick)
        self._rebuild()                  # flip to Unbuild

    def _unbuild(self):
        from maya_tools.framework.toolbar import toolbar_commands as tc
        _run("Unbuild", tc.fabricator_unbuild_quick)
        self._rebuild()                  # flip to Build

    def open_full_window(self):          # drag-bar expand hook (item 3 mechanism)
        from maya_tools.rigging.fabricator.ui.fs_window import FSWindow
        FSWindow.show_window()


def fabricator_build_menu():
    """Right-click rows for the toolbar's Fabricator button: a single Build
    (rig unbuilt) or Unbuild (rig built) action, chosen FRESH each open so the
    row flips with rig state. The FabricatorPopover's contextual-button logic,
    served as menu_factory (label, tooltip, handler) rows."""
    from maya_tools.framework.toolbar import toolbar_commands as tc
    if tc.fabricator_is_built():
        return [("Unbuild", "Unbuild the rig back to guides",
                 partial(_run, "Unbuild", tc.fabricator_unbuild_quick))]
    return [("Build", "Build the rig from the blueprint",
             partial(_run, "Build", tc.fabricator_build_quick))]


def scene_menu():
    """Save / Save As / Open / Load-in-dir / Reload(safe) / Rig<->Source / Scene Batch."""
    from maya_tools.framework.toolbar import toolbar_commands as tc
    from maya_tools.framework import scene_loader
    from maya_tools.scene_batch.scene_batch_ui import SceneBatchWindow
    return [
        ("Save Scene", "Save the current scene", partial(_run, "Save Scene", tc.save_scene)),
        ("Save Scene As...", "Save the scene to a new file", partial(_run, "Save Scene As", tc.save_scene_as)),
        None,
        ("Open Scene...", "Open browser rooted at this scene's folder", partial(_run, "Open Scene", scene_loader.open_scene_in_current_dir)),
        ("Reload Scene", "Reopen this scene (prompts if unsaved)", partial(_run, "Reload Scene", tc.reload_scene)),
        None,
        ("Open Rig <-> Source", "Swap between the Rig and Source sibling file", partial(_run, "Rig<->Source", scene_loader.open_source_rig_sibling)),
        ("Batch Runner...", "Batch-run a Python script across many scenes", partial(_run, "Batch Runner", SceneBatchWindow.show_window)),
    ]


class ExportPopover(QWidget):
    """Both exporters, FULL tools, tabbed (Adrian, 2026-07-05: the
    exporters get the popover treatment). MODEL hosts ExporterWindow,
    ANIM hosts AnimExporterWindow — each embedded=True (sweep-safe
    objectName, brand bar + log trimmed; the drag bar carries
    identity). Tabs build LAZILY: each window's populate() scans the
    scene, and a hover-open should pay for one scan, not two. Escape
    inside either tool dismisses the whole popover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # FS export banner (Adrian, 2026-07-09): the embedded exporters hide
        # their own brand bars in the popover, so the popover carries the shared
        # export banner up top, left-justified. Silently skipped if PNG missing.
        from PySide6.QtGui import QPixmap
        from PySide6.QtCore import Qt
        from maya_tools.framework.toolbar.widgets import icon_button
        _banner_pix = QPixmap(str(icon_button._ICON_DIR / 'fs_export_banner.png'))
        if not _banner_pix.isNull():
            _banner = QLabel()
            _banner.setPixmap(_banner_pix.scaledToHeight(
                40, Qt.TransformationMode.SmoothTransformation))
            _banner.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            _banner.setContentsMargins(8, 6, 0, 4)
            lay.addWidget(_banner)
        self._tabs = QTabWidget()
        # Boxed, coloured tabs (Adrian, 2026-07-10): the flat underline tabs
        # didn't read as tabs here — outline them, the LIVE tab ember-orange
        # and the ones behind it plasma-green, so the pair reads as a switch.
        # Scoped to THIS QTabWidget (inline sheet) — the Library popover and
        # every other QTabBar keep the global underline look.
        self._tabs.setStyleSheet(_BOXED_TAB_QSS)
        self._tabs.addTab(QWidget(), "MODEL/RIG")
        self._tabs.addTab(QWidget(), "ANIM")
        lay.addWidget(self._tabs)
        self._built = set()
        self._tabs.currentChanged.connect(self._ensure_tab)
        self._ensure_tab(0)

    def _ensure_tab(self, index: int):
        if index in self._built:
            return
        self._built.add(index)
        from PySide6.QtCore import Qt
        if index == 0:
            from maya_tools.export.exporter_ui import ExporterWindow
            tool = ExporterWindow(embedded=True)
        else:
            from maya_tools.export.anim_ui import AnimExporterWindow
            tool = AnimExporterWindow(embedded=True)
        tool.setWindowFlags(Qt.WindowType.Widget)
        # QDialog turns Escape into reject() — route it to the frame
        # so Escape dismisses the popover instead of hollowing a tab.
        tool.rejected.connect(lambda: self.window().close())
        page = self._tabs.widget(index)
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.addWidget(tool)

    def open_full_window(self):    # drag-bar expand hook (item 3, 2026-07-03)
        if self._tabs.currentIndex() == 0:
            from maya_tools.export.exporter_ui import ExporterWindow
            ExporterWindow.show_window()
        else:
            from maya_tools.export.anim_ui import AnimExporterWindow
            AnimExporterWindow.show_window()


def clipboard_menu():
    """The shelf's Clipboard button, promoted to the toolbar (Adrian,
    2026-07-10). Copies scene/selection strings to the OS clipboard. Mirrors
    the shelf commands EXACTLY (shelf_data/fsModel.json + fsAnim.json): the Qt
    clipboard, and Copy Scene Name is the base name MINUS its extension. Maya
    imports are lazy inside the handlers, keeping this module Qt-only at import
    time (every other factory here does the same)."""
    import os
    import maya.cmds as cmds
    from PySide6.QtWidgets import QApplication

    def copy_selected():
        QApplication.clipboard().setText('\n'.join(cmds.ls(sl=True) or []))

    def copy_scene_name():
        scene = cmds.file(q=True, sn=True)
        QApplication.clipboard().setText(
            os.path.splitext(os.path.basename(scene))[0] if scene else '')

    def copy_scene_dir():
        scene = cmds.file(q=True, sn=True)
        QApplication.clipboard().setText(os.path.dirname(scene) if scene else '')

    def copy_scene_fullpath():
        QApplication.clipboard().setText(cmds.file(q=True, sn=True) or '')

    return [
        ("Copy Selected", "Copy selected node names to the clipboard", copy_selected),
        None,
        ("Copy Scene Name", "Copy the scene file name (no extension)", copy_scene_name),
        ("Copy Scene Directory", "Copy the scene's folder path", copy_scene_dir),
        ("Copy Scene Full Path", "Copy the scene's full file path", copy_scene_fullpath),
    ]


def select_menu():
    """The shelf's Selection Helpers, promoted."""
    from maya_tools.framework.toolbar import toolbar_commands as tc
    from maya_tools.framework import utilities
    return [
        ("Select Child Joints", "Selected node + all joint descendants", partial(_run, "Select Child Joints", utilities.select_child_joints)),
        ("Select Child Meshes", "All mesh descendants of selection", partial(_run, "Select Child Meshes", utilities.select_child_meshes)),
        None,
        ("Select Influencing Joints", "Joints driving the selected meshes", partial(_run, "Select Influencing", tc.select_influencing_joints)),
        ("Select Influenced Meshes", "Meshes driven by the selected joints", partial(_run, "Select Influenced", tc.select_influenced_meshes)),
    ]


def snap_menu():
    """Right-click rows for the Snap button: cmds.matchTransform options
    (Maya's Modify > Match Transformations). Sources match the LAST
    selected, the same semantics as the button's own Snap A to B."""
    from maya_tools.framework.toolbar import toolbar_commands as tc
    return [
        ("Match All", "Match position + rotation + scale to the last selected",
         partial(_run, "Match All", tc.match_all)),
        None,
        ("Match Rotation", "Match world rotation to the last selected",
         partial(_run, "Match Rotation", tc.match_rotation)),
        ("Match Translation", "Match world translation to the last selected",
         partial(_run, "Match Translation", tc.match_translation)),
        ("Match Scale", "Match scale to the last selected",
         partial(_run, "Match Scale", tc.match_scale)),
        None,
        ("Match Pivot", "Match rotate + scale pivots to the last selected",
         partial(_run, "Match Pivot", tc.match_pivot)),
    ]


def mirror_menu():
    """Mirror joints / weights / curves."""
    from maya_tools.framework.toolbar import toolbar_commands as tc
    return [
        ("Mirror Joints (YZ)", "Mirror selected joints + descendants", partial(_run, "Mirror Joints", tc.mirror_joints_selected)),
        ("Mirror Skin Weights", "Mirror weights on first selected mesh", partial(_run, "Mirror Weights", tc.mirror_selected_weights)),
        ("Mirror Ctrl Curves", "Mirror ctrl shapes to the opposite side", partial(_run, "Mirror Curves", tc.mirror_selected_curves)),
    ]


# ConstraintsPopover retired 2026-07-07 (Adrian: remove Constraints from the
# toolbar). The constrain / delete_constraints commands remain in
# toolbar_commands for the shelf; only the strip button is gone.


class JointOrientPopover(QWidget):
    """Create Aimers (from selected root) / Mirror Aimers / Orient All /
    Delete Aimers — the four JointOrientWindow actions, compact."""
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)
        for label, tip, handler in (
                ("Create Aimers", "Create aimers from selected joint's root", self._create),
                ("Mirror Aimers", "Mirror selected aimers L to R", self._mirror),
                ("Orient All + Cleanup", "Orient every aimer, then remove them", self._orient),
                ("Delete All Aimers", "Remove all aimers unchanged", self._delete)):
            lay.addWidget(_text_button(label, tip, handler))

    def _create(self):
        from maya_tools.framework.toolbar import toolbar_commands
        # Composed glue: the root walk runs INSIDE the runner so the
        # no-selection raise hits the warning surface, not the Qt slot (B4).
        _run("Create Aimers", toolbar_commands.create_aimers_from_selected_root)

    def _mirror(self):
        from maya_tools.rigging.joint_orient import joint_orient_app
        _run("Mirror Aimers", joint_orient_app.mirror_aimers)

    def _orient(self):
        from maya_tools.rigging.joint_orient import joint_orient_app
        _run("Orient All Aimers", joint_orient_app.orient_all_aimers)

    def _delete(self):
        from maya_tools.rigging.joint_orient import joint_orient_app
        _run("Delete All Aimers", joint_orient_app.delete_all_aimers)

    def open_full_window(self):     # drag-bar expand hook (item 3, 2026-07-03)
        from maya_tools.rigging.joint_orient.joint_orient_ui import JointOrientWindow
        JointOrientWindow.show_window()


class InsertJointsPopover(QWidget):
    """Count slider + offset(bias) slider + Apply (Adrian's spec: slider
    with a popup carrying the extra offset slider)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        count = _slider_row(lay, "Count", 1, 20, 3, steps=19)
        bias = _slider_row(lay, "Offset", -1.0, 1.0, 0.0)
        def apply():
            from maya_tools.framework.toolbar import toolbar_commands as tc
            _run("Insert Joints", tc.insert_joints_apply, count(), bias())
        lay.addWidget(_text_button("Insert Joints", "Insert between the two selected joints", apply))


class SmooshPopover(QWidget):
    """Hover panel: doc blurb + strength + iterations sliders + apply
    (clicking the strip button itself smooshes at the last-applied
    strength). The blurb rides IN the panel (Adrian, 2026-07-10): a
    hover-triggered popover never gets a separate annotation card, so
    this is where its doc line lives."""
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        blurb = mindmeld_style.helper_label(
            'Press to progressively hammer-smooth out the skin weights '
            'on the selected mesh or vertices.')
        blurb.setWordWrap(True)
        blurb.setMaximumWidth(dpi.scaled(220))
        lay.addWidget(blurb)
        from maya_tools.framework.toolbar import toolbar_commands as tc
        alpha = _slider_row(lay, "Strength", 0.0, 1.0, tc.get_smoosh_alpha())
        iters = _slider_row(lay, "Iterations", 1, 10, 1, steps=9)
        def apply():
            tc.set_smoosh_alpha(alpha())
            _run("Smoosh", tc.smoosh_at_stored_strength, int(round(iters())))
        lay.addWidget(_text_button("Apply Smoosh", "Smoosh the selected verts", apply))


def skin_menu():
    from maya_tools.framework.toolbar import toolbar_commands as tc

    def combine():
        from maya_tools.skinning import combine_skinned
        _run("Combine Skinned", combine_skinned.combine_skinned_meshes)

    def separate():
        from maya_tools.skinning import combine_skinned
        _run("Separate Skinned", combine_skinned.separate_skinned_mesh)

    def window():
        from maya_tools.skinning.skin_io.skin_io_ui import SkinIOWindow
        SkinIOWindow.show_window()

    def autoskin():
        from maya_tools.skinning.autoskin.autoskin_ui import AutoSkinWindow
        AutoSkinWindow.show_window()

    def dembones_bake():
        from maya_tools.skinning.dem_bones_bake.dem_bones_bake_ui import DemBonesBakeWindow
        DemBonesBakeWindow.show_window()

    # AutoSkin REPLACES the old bind pipeline here (Adrian, 2026-07-13).
    # DemBones Bake is what survived the old AutoSkinDialog: its DEFORM +
    # BAKE tabs shipped as their own tool (Adrian, 2026-07-18), sitting
    # under AutoSkin because it polishes what AutoSkin binds. It ships as
    # a SEPARATE add-on package (Adrian, 2026-07-19) and is excluded from
    # the free zip, so its entry only appears when the package is present
    # — the presence probe makes the menu correct in dev, in the shipped
    # free build, and after the add-on installs.
    import importlib.util
    entries = [
        ("AutoSkin...", "AutoSkin: select your joints + mesh, press Bind Skin", autoskin),
    ]
    if importlib.util.find_spec(
            'maya_tools.skinning.dem_bones_bake') is not None:
        entries.append(("DemBones Bake...",
                        "Delta Mush the bound mesh, then bake it into skin weights",
                        dembones_bake))
    return entries + [
        None,
        ("Save Temp Skin", "Save skin from selected meshes to the temp slot", partial(_run, "Save Temp Skin", tc.save_temp_skin)),
        ("Import Temp Skin", "Load temp skin by closest point (0.99:1 - catches all cases)", partial(_run, "Import Temp Skin", tc.transfer_temp_skin)),
        None,
        ("Disconnect All Skins", "Unbind every live skinned mesh in the scene (keep history)", partial(_run, "Disconnect Skins", tc.disconnect_all_skins)),
        ("Reconnect All Skins", "Rebind every detached skinned mesh at current joint positions", partial(_run, "Reconnect Skins", tc.reconnect_all_skins)),
        None,
        ("Combine Selected", "Combine selected skinned meshes", combine),
        ("Separate Selected", "Separate a multi-shell skinned mesh", separate),
        None,
        ("Skin IO Window...", "Full save/load/transfer options", window),
    ]


def skeleton_menu():
    from maya_tools.framework.toolbar import toolbar_commands as tc

    def window():
        from maya_tools.skeleton.skeleton_io.skeleton_io_ui import SkeletonIOWindow
        SkeletonIOWindow.show_window()

    return [
        ("Save Temp Skeleton", "Export from the selected joint's root to the temp slot", partial(_run, "Save Temp Skeleton", tc.save_temp_skeleton)),
        ("Load Temp Skeleton", "Rebuild the temp skeleton", partial(_run, "Load Temp Skeleton", tc.load_temp_skeleton)),
        None,
        ("Skeleton IO Window...", "Full export/import options", window),
    ]


class CurveOMaticPopover(QWidget):
    """The FULL Curve-O-Matic tool in a popover (Adrian, 2026-07-05:
    the renamer treatment — the popover IS the tool, not a sampler).

    Hosts CurveOMaticWindow itself as a child widget — the pattern
    Fabricator's Tools panel proved. embedded=True buys the sweep-safe
    objectName (the standalone show_window() closes by objectName and
    must never reap this instance); the embed trim is then UNDONE
    because a popover wants the whole tool: shape list, Swap, Build at
    Origin, Edit/Mirror/Combine, Color, Save, log. The in-window brand
    bar stays — the CtrlEditor banner shows in the popover too
    (Adrian, 2026-07-15).
    The window's SelectionChanged auto-highlight rides along and
    cleans itself up via its destroyed-signal hook when the frame
    deleteLater()s. Single source of truth: zero duplicated fields,
    the window and the popover can never drift."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import Qt
        from maya_tools.rigging.curve_o_matic.curve_o_matic_ui import (
            CurveOMaticWindow,
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._tool = CurveOMaticWindow(embedded=True)
        self._tool.setWindowFlags(Qt.WindowType.Widget)
        # Un-trim Build at Origin + Save — the popover is the full
        # tool MINUS the log (Adrian, 2026-07-05: no log here;
        # LoggerWidget mirrors to the Script Editor anyway, so
        # feedback still lands).
        self._tool.build_origin_btn.setVisible(True)
        self._tool.save_btn.setVisible(True)
        # In-window brand stays visible (Adrian, 2026-07-15: the CtrlEditor
        # banner shows in the popover too — supersedes the 2026-07-05
        # drag-bar-carries-the-identity hide).
        # QDialog turns Escape into reject(); route that to the frame
        # so Escape dismisses the whole popover instead of hollowing
        # out its content.
        self._tool.rejected.connect(lambda: self.window().close())
        lay.addWidget(self._tool)

    def open_full_window(self):     # drag-bar expand hook (item 3, 2026-07-03)
        from maya_tools.rigging.curve_o_matic.curve_o_matic_ui import CurveOMaticWindow
        CurveOMaticWindow.show_window()


# RenamerPopover retired 2026-07-05 (Adrian: deprecate the renamer
# window entirely) - RenamerInline four modes ARE the renamer now;
# see widgets/renamer_inline.py. Renumber lives on app-side only
# (renamer.renumber_selection); the Hash mode covers its main use.


class OffsetOptionsPanel(QWidget):
    """Companion panel for the offset-joints jog (idea B, 2026-07-03): the
    axis toggles (X/Y/Z) and the Offset<->Spread mode. Summoned by hovering
    or clicking into the slider, or by right-clicking it; it writes straight
    to toolbar_commands._OFFSET_OPTS, which the live gesture snapshots at
    begin. Built fresh on each open, so the checks mirror the live state."""
    def __init__(self, parent=None):
        super().__init__(parent)
        from maya_tools.framework.toolbar import toolbar_commands as tc
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        root.addWidget(mindmeld_style.field_label("Axis"))
        axis_row = QHBoxLayout()
        axis_row.setContentsMargins(0, 0, 0, 0)
        axis_row.setSpacing(8)
        for ax in ("x", "y", "z"):
            box = QCheckBox(ax.upper())
            box.setChecked(tc.offset_axis_enabled(ax))
            box.toggled.connect(partial(tc.offset_set_axis, ax))
            axis_row.addWidget(box)
        axis_row.addStretch()
        root.addLayout(axis_row)

        root.addWidget(mindmeld_style.field_label("Mode"))
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(8)
        self._mode_group = QButtonGroup(self)
        for mode, text in (("offset", "Offset"), ("spread", "Spread")):
            rb = QRadioButton(text)
            rb.setChecked(tc.offset_mode_is(mode))
            # toggled fires for BOTH the newly-on and newly-off button; only
            # the one turning ON writes the mode.
            rb.toggled.connect(lambda on, m=mode: on and tc.offset_set_mode(m))
            self._mode_group.addButton(rb)
            mode_row.addWidget(rb)
        mode_row.addStretch()
        root.addLayout(mode_row)
