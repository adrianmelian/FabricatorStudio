"""Phase A offscreen tests. Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/toolbar/_dev/test_phase_a.py
No Maya session required (pure Qt + pure Python).

PYTHONNOUSERSITE=1 is LOAD-BEARING: a user-site numpy 2.x (ML-dep install)
collides with Maya's bundled numpy inside the shiboken6 import - usually a
warning, occasionally a hard exit-127 crash (observed 2026-07-02).

Maya-only modules (import cmds at module scope; NOT offscreen-importable,
so they get no tests here — CP-B verifies live): dock_targets, toolbar_app,
toolbar_commands. popovers imports Qt-only; most of its classes pull Maya
modules in __init__. RenamerInline constructs offscreen (rename engines
import lazily on apply) and its four-mode strip is tested below."""
import os
import sys
import json
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "maya_tools" / "_vendor"))

FAILURES = []

def _flush_qt():
    """Collect wrappers + drain deferred deletes BETWEEN tests. The suite
    never spins an event loop, so every deleteLater (popover frames) stays
    queued and every out-of-scope top-level waits for a random gc pass;
    letting that pile up hard-crashed mayapy on a later QWidget.show()
    (silent exit 127, flaky position, no faulthandler traceback - observed
    2026-07-02, on unmodified HEAD too). ORDER IS LOAD-BEARING: gc.collect()
    FIRST, so Python-owned parents destroy their children and QObject dtors
    remove the children's queued DeferredDelete events; draining
    DeferredDelete first segfaulted (exit 139) deleting frames whose
    parents were still gc-pending."""
    if _APP is None:
        return
    import gc
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    gc.collect()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.processEvents()

def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()
    finally:
        _flush_qt()

_APP = None
def qt_app():
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication
        _APP = QApplication.instance() or QApplication([])
    return _APP

def test_prefs_roundtrip():
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fabricator_toolbar_prefs.json"
        prefs = toolbar_prefs.load_prefs(path=p)          # missing file -> defaults copy
        assert prefs["dock_mode"] == "bottom"
        assert prefs["visible"] is True
        prefs["dock_mode"] = "right"
        toolbar_prefs.save_prefs(prefs, path=p)
        again = toolbar_prefs.load_prefs(path=p)
        assert again["dock_mode"] == "right"

def test_prefs_corrupt_file_returns_defaults():
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fabricator_toolbar_prefs.json"
        p.write_text("{not json", encoding="utf-8")
        prefs = toolbar_prefs.load_prefs(path=p)
        assert prefs == toolbar_prefs.default_prefs()

def test_prefs_invalid_utf8_returns_defaults():
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fabricator_toolbar_prefs.json"
        p.write_bytes(b'{"dock_mode": "side_right\xc3')   # truncated multibyte
        prefs = toolbar_prefs.load_prefs(path=p)
        assert prefs == toolbar_prefs.default_prefs()

def test_dpi_scaled():
    from maya_tools.framework.toolbar import dpi
    dpi._set_scale_for_tests(1.5)
    assert dpi.scaled(20) == 30
    dpi._set_scale_for_tests(1.0)
    assert dpi.scaled(38) == 38

def test_icon_button_fires_command():
    qt_app()
    from maya_tools.framework.toolbar.widgets.icon_button import IconButton
    fired = []
    btn = IconButton("JS", tooltip="Joint size", command=lambda: fired.append(1))
    btn.click()
    assert fired == [1]
    assert btn.toolTip() == "Joint size"

def test_icon_button_none_command_safe():
    qt_app()
    from maya_tools.framework.toolbar.widgets.icon_button import IconButton
    IconButton("X").click()   # must not raise

def test_button_size_manifest_keys():
    qt_app()
    from PySide6.QtGui import QPixmap
    from maya_tools.framework.toolbar import dpi
    from maya_tools.framework.toolbar.toolbar_ui import _build_widget
    from maya_tools.framework.toolbar.widgets import icon_button
    # default stays the 26px square
    d = _build_widget({"type": "IconButton", "id": "d", "glyph": "D",
                       "command": lambda: None})
    assert d.width() == dpi.scaled(26) and d.height() == dpi.scaled(26)
    # manifest width/height ride through every adapter (pre-DPI px);
    # the icon area follows the button (minus the 2px rim), so wide
    # buttons get wide icon space (CP-D wave-1: the 48x24 paint toggle)
    with tempfile.TemporaryDirectory() as td:
        pm = QPixmap(8, 8); pm.fill(); pm.save(str(Path(td) / "i.png"))
        old_dir = icon_button._ICON_DIR
        icon_button._ICON_DIR = Path(td)
        try:
            w = _build_widget({"type": "Toggle", "id": "t", "glyph": "T",
                               "command": lambda: True,
                               "state_query": lambda: False,
                               "icon": "i.png", "width": 48, "height": 24})
            assert w.width() == dpi.scaled(48) and w.height() == dpi.scaled(24)
            assert w.iconSize().width() == dpi.scaled(48) - dpi.scaled(2)
            assert w.iconSize().height() == dpi.scaled(24) - dpi.scaled(2)
        finally:
            icon_button._ICON_DIR = old_dir

def test_manifest_width_height_universal():
    from maya_tools.framework.toolbar import toolbar_manifest as tm
    data = {"version": 1, "groups": [{"id": "g", "items": [
        {"type": "IconButton", "id": "a", "glyph": "A", "command": "json:dumps",
         "width": 48, "height": 24},
        {"type": "PopoverButton", "id": "b", "glyph": "B", "popover": "json:dumps",
         "width": 30}]}]}
    tm.validate_manifest(data)   # width/height legal on every type

def test_expanding_slider_expand_apply_collapse():
    qt_app()
    from maya_tools.framework.toolbar.widgets.expanding_slider import ExpandingSlider
    got = []
    s = ExpandingSlider("SM", minimum=0.0, maximum=10.0, default=5.0,
                        command=lambda v: got.append(v))
    s.show()
    assert not s.is_expanded()
    s._expand()
    assert s.is_expanded()
    s._slider.setValue(750)         # internal 0-1000 -> 7.5 in range
    s._apply_and_collapse()
    assert not s.is_expanded()
    assert got and abs(got[0] - 7.5) < 1e-6

def test_renamer_inline_four_modes():
    # RenamerPopover retired 2026-07-05 — the inline strip IS the
    # renamer. Four stacked pages, mode switch drives stack + glyph.
    qt_app()
    from maya_tools.framework.toolbar.widgets import renamer_inline as ri
    w = ri.RenamerInline()
    assert w._stack.count() == 4
    assert w.mode() == ri.HASH
    w._set_mode(ri.PREFIX_SUFFIX)
    assert w._stack.currentIndex() == ri.PREFIX_SUFFIX
    # Prefix/Suffix page: combo defaults to Prefix, always-ADD design.
    assert w._fix_side.currentIndex() == 0
    w._set_mode(ri.SELECT)
    assert w._stack.currentIndex() == ri.SELECT
    w._set_mode(ri.HASH)
    assert w._stack.currentIndex() == ri.HASH

def test_strip_constructs_both_orientations():
    qt_app()
    from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout
    from maya_tools.framework.toolbar import dpi
    from maya_tools.framework.toolbar.toolbar_ui import (
        CROSS_AXIS_H, CROSS_AXIS_V, FSToolbarStrip)

    def groups():
        return [
            {"id": "g1", "items": [
                {"type": "IconButton", "id": "btn", "glyph": "FS",
                 "command": lambda: None},
                {"type": "ExpandingSlider", "id": "sld", "glyph": "SL",
                 "min": 0.0, "max": 10.0, "default": 5.0,
                 "command": lambda v: None},
            ]},
            {"id": "g2", "items": [
                {"type": "Toggle", "id": "tgl", "glyph": "T",
                 "command": lambda: True, "state_query": lambda: False},
            ]},
        ]

    dpi._set_scale_for_tests(1.5)
    h = FSToolbarStrip(orientation="horizontal", groups=groups())
    v = FSToolbarStrip(orientation="vertical", groups=groups())
    assert h.objectName() == "fabricator_toolbar"
    assert isinstance(h.layout(), QHBoxLayout)
    assert isinstance(v.layout(), QVBoxLayout)
    # cross-axis lock: horizontal fixes height, vertical fixes WIDTH (the
    # side strip must be wide enough for field widgets - CP-A verdict)
    assert h.height() == dpi.scaled(CROSS_AXIS_H)
    assert v.width() == dpi.scaled(CROSS_AXIS_V)
    dpi._set_scale_for_tests(1.0)
    # inline manifest = 3 widgets across 2 groups + 1 divider between groups
    assert len(h.widget_instances) == 3
    assert len(v.widget_instances) == 3
    from PySide6.QtCore import Qt
    assert h.testAttribute(Qt.WA_StyledBackground)

def test_manifest_validate_and_errors():
    from maya_tools.framework.toolbar import toolbar_manifest as tm
    good = {"version": 1, "groups": [{"id": "g", "items": [
        {"type": "IconButton", "id": "a", "glyph": "A", "command": "json:dumps"}]}]}
    tm.validate_manifest(good)   # must not raise
    import copy
    bad = copy.deepcopy(good); bad["groups"][0]["items"][0].pop("command")
    try:
        tm.validate_manifest(bad); assert False, "missing command not caught"
    except ValueError as e:
        assert "a" in str(e) and "command" in str(e)   # error names the item + key
    bad2 = copy.deepcopy(good); bad2["groups"][0]["items"][0]["type"] = "Nope"
    try:
        tm.validate_manifest(bad2); assert False
    except ValueError as e:
        assert "Nope" in str(e)
    # Phase C: Toggle carries a right-click menu (paint-on-select item)
    tgl = {"version": 1, "groups": [{"id": "g", "items": [
        {"type": "Toggle", "id": "t", "glyph": "T",
         "command": "json:dumps", "state_query": "json:loads",
         "menu": [{"label": "Open Tool Settings", "command": "json:dumps"},
                  {"label": "Enter Paint Once", "command": "json:dumps"}]}]}]}
    tm.validate_manifest(tgl)   # must not raise
    assert "menu" in tm.WIDGET_TYPES["Toggle"]["optional"]
    badmenu = copy.deepcopy(tgl)
    badmenu["groups"][0]["items"][0]["menu"] = [{"label": "no command"}]
    try:
        tm.validate_manifest(badmenu); assert False, "menu entry missing command"
    except ValueError as e:
        assert "menu" in str(e)
    # 2026-07-18: PopoverButton carries a static right-click menu too (the
    # library button's Mirror Pose row).
    pop = {"version": 1, "groups": [{"id": "g", "items": [
        {"type": "PopoverButton", "id": "lib", "glyph": "L",
         "popover": "json:dumps", "trigger": "click",
         "menu": [{"label": "Mirror Pose", "command": "json:dumps"}]}]}]}
    tm.validate_manifest(pop)   # must not raise
    assert "menu" in tm.WIDGET_TYPES["PopoverButton"]["optional"]

def test_manifest_resolve_command():
    from maya_tools.framework.toolbar import toolbar_manifest as tm
    fn = tm.resolve_command("json:dumps")            # stdlib target: offscreen-safe
    assert fn({"a": 1}) == '{"a": 1}'
    method = tm.resolve_command("collections:OrderedDict.fromkeys")  # dotted chain
    assert list(method(["x", "y"]).keys()) == ["x", "y"]
    for bad in ("json.dumps", "nosuchmod:fn", "json:nosuchattr"):
        try:
            tm.resolve_command(bad); assert False, bad
        except (ValueError, ImportError, AttributeError):
            pass

def test_toggle_lit_state_follows_command():
    qt_app()
    from maya_tools.framework.toolbar.widgets.toggle import Toggle
    state = {"on": False}
    def flip(): state["on"] = not state["on"]; return state["on"]
    t = Toggle("LR", command=flip, state_query=lambda: state["on"])
    assert t.is_lit() is False
    t.click()
    assert state["on"] is True and t.is_lit() is True
    t.click()
    assert t.is_lit() is False
    # Phase C: set_lit_external is the listener-driven resync verb
    t.set_lit_external(True)
    assert t.is_lit() is True
    t.set_lit_external(False)
    assert t.is_lit() is False
    # Phase C: Toggle forwards `menu` to IconButton's right-click menu
    tm_ = Toggle("PT", command=flip, state_query=lambda: state["on"],
                 menu=[("Open Tool Settings", lambda: None)])
    assert tm_._menu is not None and len(tm_._menu.actions()) == 1

def test_toggle_swaps_icon_with_state():
    qt_app()
    from PySide6.QtGui import QPixmap
    from maya_tools.framework.toolbar.widgets import icon_button
    from maya_tools.framework.toolbar.widgets.toggle import Toggle
    with tempfile.TemporaryDirectory() as td:
        for name in ("off.png", "on.png"):
            pm = QPixmap(8, 8); pm.fill(); pm.save(str(Path(td) / name))
        old_dir = icon_button._ICON_DIR
        icon_button._ICON_DIR = Path(td)
        try:
            t = Toggle("PNT", state_query=lambda: False,
                       icon="off.png", icon_on="on.png")
            assert t._icon_on is not None and t._icon_off is not None
            # QIcon is implicitly shared: setIcon copies keep the cacheKey,
            # so the key identifies WHICH variant the button is wearing.
            t.set_lit_external(True)
            assert t.icon().cacheKey() == t._icon_on.cacheKey()
            t.set_lit_external(False)
            assert t.icon().cacheKey() == t._icon_off.cacheKey()
            # one variant missing -> swap disabled, base icon look stands
            t2 = Toggle("PNT", state_query=lambda: False,
                        icon="off.png", icon_on="missing.png")
            key_before = t2.icon().cacheKey()
            t2.set_lit_external(True)
            assert t2._icon_on is None
            assert t2.icon().cacheKey() == key_before
        finally:
            icon_button._ICON_DIR = old_dir

def test_window_launcher_fires():
    qt_app()
    from maya_tools.framework.toolbar.widgets.window_launcher import WindowLauncher
    opened = []
    w = WindowLauncher("AS", command=lambda: opened.append(1))
    w.click()
    assert opened == [1]

def test_popover_button_opens_and_closes():
    qt_app()
    from PySide6.QtWidgets import QLabel
    from maya_tools.framework.toolbar.widgets.popover_button import PopoverButton
    made = []
    def factory():
        w = QLabel("content"); made.append(w); return w
    p = PopoverButton("JO", popover_factory=factory)
    p.show()
    p.click()
    assert made and p.is_open()
    p.close_popover()
    assert not p.is_open()
    p.click()                       # reopens with a FRESH factory call
    assert len(made) == 2
    p.close_popover()

def test_only_one_popover_open_at_a_time():
    qt_app()
    from PySide6.QtWidgets import QLabel
    from maya_tools.framework.toolbar.widgets.popover_button import PopoverButton
    # One-at-a-time governs PROVISIONAL (hover-opened) panels; committed
    # panels coexist (next test). _open_popover() is the hover-timer path.
    a = PopoverButton("A", popover_factory=lambda: QLabel("a"))
    b = PopoverButton("B", popover_factory=lambda: QLabel("b"))
    a.show(); b.show()
    a._open_popover()
    assert a.is_open() and not b.is_open()
    b._open_popover()                       # opening provisional B closes A
    assert b.is_open() and not a.is_open()
    b.close_popover()

def test_committed_popover_survives_one_at_a_time():
    qt_app()
    from PySide6.QtWidgets import QLabel
    from maya_tools.framework.toolbar.widgets.popover_button import PopoverButton
    a = PopoverButton("A", popover_factory=lambda: QLabel("a"))
    b = PopoverButton("B", popover_factory=lambda: QLabel("b"))
    a.show(); b.show()
    a._open_popover()                       # provisional hover open
    a._popover._set_pinned()                # user touches A's panel -> committed
    b._open_popover()                       # opening provisional B must NOT close committed A
    assert a.is_open() and b.is_open()
    b.close_popover()                       # ...and closing B leaves A alone
    assert a.is_open()
    a.close_popover()

def test_button_open_relaunches_but_provisional():
    qt_app()
    from PySide6.QtWidgets import QLabel
    from maya_tools.framework.toolbar.widgets.popover_button import PopoverButton
    made = []
    p = PopoverButton("JO", popover_factory=lambda: (made.append(1) or QLabel("c")))
    p.show()
    p.click()                                # button-open: PROVISIONAL (2026-07-03:
    assert p.is_open() and not p._popover._pinned   # no longer commits on press)
    first_id = id(p._popover)                # id, NOT a wrapper held across deletion
    p.click()                                # always relaunches FRESH (item 2)
    assert p.is_open() and id(p._popover) != first_id
    assert len(made) == 2                    # never a toggle-close
    p.close_popover()

def test_provisional_dismissed_on_outside_click():
    qt_app()
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QLabel
    from maya_tools.framework.toolbar.widgets.popover_button import PopoverButton
    p = PopoverButton("SEL", popover_factory=lambda: QLabel("x"))
    p.show()
    far = QPoint(-10000, -10000)             # unambiguously outside panel + owner
    p._open_popover()                        # provisional
    p._popover._dismiss_if_outside(far)      # a click outside closes it (item 1)
    assert not p.is_open()
    p._open_popover(commit=True)             # committed: X-only
    p._popover._dismiss_if_outside(far)      # ...ignores the outside click
    assert p.is_open()
    p.close_popover()

def test_popover_interaction_pins_hover_panel():
    qt_app()
    from PySide6.QtWidgets import QLineEdit
    from maya_tools.framework.toolbar.widgets.popover_button import PopoverButton
    p = PopoverButton("REN", popover_factory=lambda: QLineEdit())
    p.show()
    p._open_popover()                        # provisional hover open (uncommitted)
    frame = p._popover
    assert frame is not None and not frame._pinned
    field = frame.findChild(QLineEdit)
    frame._on_focus_changed(None, field)     # focus lands inside -> pinned
    assert frame._pinned
    from PySide6.QtCore import QEvent
    frame.leaveEvent(QEvent(QEvent.Leave))   # pointer leaves: pinned panel STAYS
    assert p.is_open()
    p.close_popover()

from PySide6.QtWidgets import QWidget as _QWidgetBase
class _FullToolWidget(_QWidgetBase):
    """Popover content that advertises a bigger tool (drives the expand button)."""
    def open_full_window(self):
        pass

def test_popover_expand_button_only_with_full_window():
    qt_app()
    from PySide6.QtWidgets import QWidget
    from maya_tools.framework.toolbar.widgets.popover_button import PopoverButton
    # item 3 (2026-07-03): the drag bar grows an EXPAND button only when the
    # panel content exposes open_full_window(). Bar count = title + close (+ expand).
    # _FullToolWidget is module-level on purpose: defining a QWidget subclass
    # inside the test would register a fresh shiboken type on every call.
    p = PopoverButton("SKN", popover_factory=_FullToolWidget)
    p.show(); p._open_popover()
    assert p._popover._bar.count() == 3      # title, expand, close
    p.close_popover()
    q = PopoverButton("SNP", popover_factory=QWidget)
    q.show(); q._open_popover()
    assert q._popover._bar.count() == 2      # title, close (no bigger tool)
    q.close_popover()

def test_popover_can_open_gate():
    qt_app()
    from PySide6.QtWidgets import QLabel
    from maya_tools.framework.toolbar.widgets.popover_button import PopoverButton
    # item 4 (2026-07-03): when can_open() is False the popover stays shut
    # (Fabricator: not a rig scene / referenced); True lets it open.
    state = {"ok": False}
    p = PopoverButton("FAB", popover_factory=lambda: QLabel("x"),
                      can_open=lambda: state["ok"])
    p.show()
    p._open_popover()
    assert not p.is_open()                    # gated shut
    state["ok"] = True
    p._open_popover()
    assert p.is_open()                        # gate lifts
    p.close_popover()

def test_factory_popover_defaults_to_hover_trigger():
    qt_app()
    from maya_tools.framework.toolbar.toolbar_ui import _build_widget
    # No trigger key -> the WIDGET default (hover) must survive the factory
    # (regression: a stale adapter default clobbered it for all but Smoosh).
    w = _build_widget({"type": "PopoverButton", "id": "x", "glyph": "X",
                       "popover": lambda: None})
    assert w._trigger == "hover"
    w2 = _build_widget({"type": "PopoverButton", "id": "y", "glyph": "Y",
                        "popover": lambda: None, "trigger": "click"})
    assert w2._trigger == "click"
    # 2026-07-18: the factory forwards `menu` to IconButton's right-click
    # menu (the library button's Mirror Pose row), popover untouched.
    w3 = _build_widget({"type": "PopoverButton", "id": "z", "glyph": "Z",
                        "popover": lambda: None, "trigger": "click",
                        "menu": [{"label": "Mirror Pose",
                                  "command": lambda: None}]})
    assert w3._menu is not None and len(w3._menu.actions()) == 1
    assert w3._menu.actions()[0].text() == "Mirror Pose"
    assert w3.text() == "Z"          # glyph intact: no labeled-mode flip

def test_popover_stays_on_screen():
    qt_app()
    from PySide6.QtWidgets import QLabel
    from maya_tools.framework.toolbar.widgets.popover_button import PopoverButton
    def factory():
        w = QLabel("tall content\n" * 30)   # force a long panel
        return w
    p = PopoverButton("SKN", popover_factory=factory)
    p.show()
    screen = p.screen().availableGeometry()
    p.move(screen.left() + 10, screen.bottom() - 20)   # strip-at-bottom case
    p.click()
    geo = p._popover.frameGeometry()
    # CP-B round 2: never off-screen - a bottom-docked strip opens UPWARD
    assert geo.bottom() <= screen.bottom() + 1
    assert geo.top() >= screen.top() - 1
    p.close_popover()

def test_expanding_slider_live_mode_applies_on_drag():
    qt_app()
    from maya_tools.framework.toolbar.widgets.expanding_slider import ExpandingSlider
    got = []
    s = ExpandingSlider("JS", minimum=0.0, maximum=10.0, default=5.0,
                        live=True, command=got.append)
    s.show(); s._expand()
    s._slider.setValue(800)                     # 0..1000 resolution now
    assert got and abs(got[-1] - 8.0) < 1e-6    # live: fired during drag
    s._apply_and_collapse()
    assert not s.is_expanded()

def test_expanding_slider_resolution_1000():
    qt_app()
    from maya_tools.framework.toolbar.widgets.expanding_slider import ExpandingSlider
    got = []
    s = ExpandingSlider("SM", minimum=0.0, maximum=1.0, default=0.5,
                        command=got.append)
    s.show(); s._expand()
    s._slider.setValue(503); s._apply_and_collapse()
    assert abs(got[0] - 0.503) < 1e-9

def test_expanding_slider_gesture_and_reset():
    qt_app()
    from maya_tools.framework.toolbar.widgets.expanding_slider import ExpandingSlider
    events = []
    s = ExpandingSlider("CS", minimum=0.5, maximum=2.0, default=1.0,
                        reset_on_apply=True, command=lambda v: events.append(v),
                        gesture_begin=lambda: events.append("open"),
                        gesture_end=lambda: events.append("close"))
    s.show(); s._expand()
    assert events[0] == "open"                      # chunk opened at expand
    s._slider.setValue(800); s._apply_and_collapse()
    assert events[-1] == "close"                    # chunk closed at collapse
    assert events.count("open") == 1 == events.count("close")
    s._expand()
    # reset_on_apply: the handle went back to default, not the stale 1.7x
    assert abs(s.current_value() - 1.0) < 1e-9
    s._apply_and_collapse()

def test_icon_button_right_click_menu_and_set_glyph():
    qt_app()
    from maya_tools.framework.toolbar.widgets.icon_button import IconButton
    hits = []
    b = IconButton("MIR", menu=[("Disconnect", lambda: hits.append(1))])
    # 2026-07-18 regression: the menu loop shadowed the `label` param, flipping
    # menu-carrying buttons into labeled mode (text became the last row's
    # label, icon shrank to 18px). Construction must leave the glyph + the
    # icon-only path untouched.
    assert b.text() == "MIR"
    assert not b.property("fs_labeled")
    b.set_glyph("MI2")
    assert b.text() == "MI2"
    assert b._menu is not None and len(b._menu.actions()) == 1
    b._menu.actions()[0].trigger()
    assert hits == [1]

def test_strip_builds_from_manifest_structures():
    qt_app()
    from maya_tools.framework.toolbar.toolbar_ui import FSToolbarStrip
    groups = [{"id": "g", "items": [
        {"type": "IconButton", "id": "a", "glyph": "A", "command": lambda: None},
        {"type": "Toggle", "id": "b", "glyph": "B", "command": lambda: True,
         "state_query": lambda: False},
        {"type": "ExpandingSlider", "id": "c", "glyph": "C",
         "min": 0.0, "max": 1.0, "default": 0.5, "command": lambda v: None},
        {"type": "PopoverButton", "id": "d", "glyph": "D", "popover": lambda: None},
        {"type": "WindowLauncher", "id": "e", "glyph": "E", "command": lambda: None},
        # Click-trigger PopoverButton with a factory callable.
        {"type": "PopoverButton", "id": "renamer", "glyph": "REN",
         "popover": lambda: None},
    ]}]
    s = FSToolbarStrip(orientation="horizontal", groups=groups)
    assert len(s.widget_instances) == 6
    assert s.widget_by_id["a"].__class__.__name__ == "IconButton"

def test_manifest_rejects_unknown_keys():
    import copy
    from maya_tools.framework.toolbar import toolbar_manifest as tm
    good = {"version": 1, "groups": [{"id": "g", "items": [
        {"type": "IconButton", "id": "a", "glyph": "A", "command": "json:dumps",
         "tooltip": "t", "title": "T", "icon": "a.png"}]}]}
    tm.validate_manifest(good)               # tooltip + title + icon allowed on every type
    bad = copy.deepcopy(good)
    bad["groups"][0]["items"][0]["comand"] = "json:dumps"    # the typo case
    try:
        tm.validate_manifest(bad); assert False, "unknown key not caught"
    except ValueError as e:
        assert "comand" in str(e) and "a" in str(e)
    # menu is granted by the per-type optional sets ONLY: a slider (or a
    # popover) can't quietly grow one.
    sld = {"version": 1, "groups": [{"id": "g", "items": [
        {"type": "ExpandingSlider", "id": "s", "glyph": "S",
         "command": "json:dumps", "min": 0.0, "max": 1.0, "default": 0.5,
         "menu": [{"label": "x", "command": "json:dumps"}]}]}]}
    try:
        tm.validate_manifest(sld); assert False, "menu on slider not caught"
    except ValueError as e:
        assert "menu" in str(e)

def test_manifest_icon_on_is_toggle_only():
    from maya_tools.framework.toolbar import toolbar_manifest as tm
    good = {"version": 1, "groups": [{"id": "g", "items": [
        {"type": "Toggle", "id": "t", "glyph": "T", "command": "json:dumps",
         "state_query": "json:dumps",
         "icon": "fs_paint_off.png", "icon_on": "fs_paint_on.png"}]}]}
    tm.validate_manifest(good)               # lit-state variant legal on Toggle
    bad = {"version": 1, "groups": [{"id": "g", "items": [
        {"type": "IconButton", "id": "i", "glyph": "I", "command": "json:dumps",
         "icon_on": "on.png"}]}]}            # ...and ONLY on Toggle
    try:
        tm.validate_manifest(bad); assert False, "icon_on on IconButton not caught"
    except ValueError as e:
        assert "icon_on" in str(e)

def test_manifest_rejects_command_on_click_popover():
    import copy
    from maya_tools.framework.toolbar import toolbar_manifest as tm
    hover = {"version": 1, "groups": [{"id": "g", "items": [
        {"type": "PopoverButton", "id": "sm", "glyph": "SM",
         "popover": "json:dumps", "trigger": "hover", "command": "json:dumps"}]}]}
    tm.validate_manifest(hover)              # the Smoosh shape stays legal
    absent = copy.deepcopy(hover)
    del absent["groups"][0]["items"][0]["trigger"]   # absent = HOVER (CP-C default)
    tm.validate_manifest(absent)             # command + default-hover: legal
    explicit = copy.deepcopy(hover)
    explicit["groups"][0]["items"][0]["trigger"] = "click"
    try:
        tm.validate_manifest(explicit); assert False, "command on click popover"
    except ValueError as e:
        assert "command" in str(e)

def test_expanding_slider_focus_out_collapses():
    qt_app()
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent
    from PySide6.QtWidgets import QApplication
    from maya_tools.framework.toolbar.widgets.expanding_slider import ExpandingSlider
    events = []
    s = ExpandingSlider("CS", minimum=0.0, maximum=1.0, default=0.5,
                        gesture_begin=lambda: events.append("open"),
                        gesture_end=lambda: events.append("close"))
    s.show(); s._expand()
    s._slider.setFocus()
    QApplication.sendEvent(s._slider, QFocusEvent(QEvent.FocusOut))
    # FULL collapse (C9): contracts to compact - a bare gesture-close would
    # leave it wide and the eventual release applies chunk-less. The slider
    # itself stays visible throughout (it IS a slider even when idle).
    assert not s.is_expanded()
    assert not s._slider.isHidden()
    assert events == ["open", "close"]       # gesture_end exactly once


def test_expanding_slider_always_visible_expands_on_click():
    # The rework (2026-07-03): a compact slider at rest that WIDENS on a
    # click/focus into it, then contracts. It is never a bare glyph button.
    qt_app()
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from maya_tools.framework.toolbar import dpi
    from maya_tools.framework.toolbar.widgets import expanding_slider as es
    from maya_tools.framework.toolbar.widgets.expanding_slider import ExpandingSlider
    s = ExpandingSlider("SL", minimum=0.0, maximum=10.0, default=0.0)
    s.show()
    # idle: slider shown, compact width, not expanded
    assert not s._slider.isHidden() and not s.is_expanded()
    assert s._slider.maximumWidth() == dpi.scaled(es.COLLAPSED_W)
    # a mouse press INTO the slider expands it (anim targets expanded width)
    press = QMouseEvent(QEvent.MouseButtonPress, QPointF(s._slider.rect().center()),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(s._slider, press)
    assert s.is_expanded()
    assert s._anim is not None and s._anim.endValue() == dpi.scaled(es.EXPANDED_W)
    # no event loop here: land the expand width by hand so the collapse sees a
    # delta and builds its own anim (mirrors the renamer expand/contract test).
    s._slider.setMinimumWidth(dpi.scaled(es.EXPANDED_W))
    s._slider.setMaximumWidth(dpi.scaled(es.EXPANDED_W))
    s._apply_and_collapse()
    assert not s.is_expanded()
    assert s._anim.endValue() == dpi.scaled(es.COLLAPSED_W)

def test_expanding_slider_center_return_springs_home():
    # The AnimBot jog (Adrian, 2026-07-03): handle rests at track center,
    # right of center reads up to max, the min==default left half is a dead
    # zone (insert only adds), and release springs the handle back to center
    # WITHOUT re-firing a 0 that would delete what was just inserted.
    qt_app()
    from maya_tools.framework.toolbar.widgets.expanding_slider import ExpandingSlider
    got = []
    s = ExpandingSlider("INS", minimum=0.0, maximum=10.0, default=0.0,
                        live=True, center_return=True, command=got.append)
    s.show(); s._expand()
    assert s._slider.value() == 500                 # rests at center (raw home)
    assert abs(s.current_value() - 0.0) < 1e-9      # center == default (0)
    s._slider.setValue(1000); assert abs(s.current_value() - 10.0) < 1e-6   # far right = max
    s._slider.setValue(0);    assert abs(s.current_value() - 0.0) < 1e-9    # left half dead
    got.clear()
    s._slider.setValue(800)                         # right: value 6, live-fires
    assert got and abs(got[-1] - 6.0) < 1e-6
    s._apply_and_collapse()
    assert s._slider.value() == 500                 # sprang back to center
    assert 0.0 not in got                           # spring reset never fired a 0

def test_insert_gesture_user_error_and_leak_guard():
    # Fail elegantly (Adrian, 2026-07-03): a wrong selection surfaces a clean
    # message, never a traceback, and the gesture leaves no dangling chunk.
    from maya_tools.framework.toolbar import toolbar_commands as tc

    def bad_selection():
        raise tc.ToolbarUserError("Select two adjacent joints")
    # swallowed to a clean None - no exception escapes the runner
    assert tc.run_in_gesture("Insert Joints", bad_selection) is None
    # leak guard: a rejected begin captured no state, so end() must NOT try to
    # close a chunk it never opened (that would unbalance Maya's undo queue).
    tc._INSERT_STATE = None
    tc.insert_joints_end()          # no-op, no Maya call, no raise
    assert tc._INSERT_STATE is None

def test_offset_compute_pure():
    # idea B math (offscreen, no Maya): OFFSET adds v on checked axes; SPREAD
    # translates each joint out from the centroid by v/10 of its distance.
    from maya_tools.framework.toolbar import toolbar_commands as tc
    items = [("a", (0.0, 0.0, 0.0)), ("b", (2.0, 0.0, 0.0))]
    centroid = (1.0, 0.0, 0.0)
    out = dict(tc._compute_offset(items, centroid, (True, False, False), "offset", 5.0))
    assert out["a"] == (5.0, 0.0, 0.0) and out["b"] == (7.0, 0.0, 0.0)
    out = dict(tc._compute_offset(items, centroid, (False, True, False), "offset", 3.0))
    assert out["a"] == (0.0, 3.0, 0.0)            # unchecked axes untouched
    # SPREAD normalizes v against vmax: v=+vmax doubles, v=-vmax collapses.
    out = dict(tc._compute_offset(items, centroid, (True, False, False), "spread", 10.0, vmax=10.0))
    assert abs(out["a"][0] + 1.0) < 1e-9 and abs(out["b"][0] - 3.0) < 1e-9   # full -> double
    out = dict(tc._compute_offset(items, centroid, (True, False, False), "spread", -10.0, vmax=10.0))
    assert abs(out["a"][0] - 1.0) < 1e-9 and abs(out["b"][0] - 1.0) < 1e-9   # -full -> collapse
    # default vmax is the module's _OFFSET_MAX (mirrors JSON): half-range = half-spread
    out = dict(tc._compute_offset(items, centroid, (True, False, False), "spread", tc._OFFSET_MAX / 2))
    assert abs(out["b"][0] - 2.5) < 1e-9        # dist +1, f=0.5 -> 2 + 0.5

def test_offset_options_setters_and_panel():
    qt_app()
    from PySide6.QtWidgets import QCheckBox, QRadioButton
    from maya_tools.framework.toolbar import toolbar_commands as tc
    from maya_tools.framework.toolbar.popovers import OffsetOptionsPanel
    saved = dict(tc._OFFSET_OPTS)                 # module state - snapshot/restore
    try:
        tc.offset_set_axis("x", True); tc.offset_set_axis("y", False)
        tc.offset_set_axis("z", False); tc.offset_set_mode("offset")
        p = OffsetOptionsPanel()
        boxes = {b.text(): b for b in p.findChildren(QCheckBox)}
        assert set(boxes) == {"X", "Y", "Z"} and boxes["X"].isChecked()
        boxes["Y"].setChecked(True)               # writes straight through
        assert tc.offset_axis_enabled("y")
        spread = [r for r in p.findChildren(QRadioButton) if r.text() == "Spread"][0]
        spread.setChecked(True)
        assert tc.offset_mode_is("spread")
    finally:
        tc._OFFSET_OPTS.clear(); tc._OFFSET_OPTS.update(saved)

def test_expanding_slider_options_flyout():
    qt_app()
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QLabel
    from maya_tools.framework.toolbar.widgets.expanding_slider import ExpandingSlider
    made = []
    s = ExpandingSlider("OFF", minimum=-10.0, maximum=10.0, default=0.0,
                        center_return=True,
                        options_factory=lambda: (made.append(1), QLabel("opts"))[1])
    s.show()
    assert s._flyout is None
    # right-click is consumed by the filter and summons the panel (built lazily)
    consumed = s.eventFilter(s._slider, QEvent(QEvent.Type.ContextMenu))
    assert consumed is True and s._flyout is not None and made
    s._expand(); assert s._flyout.isVisible()     # a drag keeps the panel up
    s._hide_flyout(); assert not s._flyout.isVisible()

def test_active_project_pref_roundtrip():
    import json
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "prefs.json"
        prefs = toolbar_prefs.default_prefs()
        assert prefs["active_project"] == ""             # ships empty
        prefs["active_project"] = "HeroRig"
        toolbar_prefs.save_prefs(prefs, path=p)
        assert toolbar_prefs.load_prefs(path=p)["active_project"] == "HeroRig"
        # a wrong-typed hand edit coerces back to the "" default, not poison
        p.write_text(json.dumps({**prefs, "active_project": 123}), encoding="utf-8")
        assert toolbar_prefs.load_prefs(path=p)["active_project"] == ""

def test_project_chooser_confirm_and_revert():
    qt_app()
    from maya_tools.framework.toolbar.widgets.project_chooser import ProjectChooser
    c = ProjectChooser()
    # populate without touching disk/prefs
    c._names = ["A", "B"]; c._current = "A"
    c.blockSignals(True); c.clear(); c.addItems(["A", "B"])
    c.setCurrentIndex(0); c.blockSignals(False)
    committed = []
    c._commit = lambda name: committed.append(name)     # don't hit real prefs
    # decline -> snaps back to A, nothing committed
    c._confirm = lambda name: False
    c.setCurrentIndex(1); c._on_activated(1)
    assert not committed and c.currentText() == "A"
    # accept -> commits B
    c._confirm = lambda name: True
    c._on_activated(1)
    assert committed == ["B"]

def test_tool_visibility_set_hidden_roundtrip():
    from maya_tools.framework.toolbar import tool_visibility as tv
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "prefs.json"
        tv.set_hidden("center", "smoosh", True, path=p)
        hid = lambda: toolbar_prefs.load_prefs(path=p)["layout"]["groups"]["center"]["hidden"]
        assert hid() == ["smoosh"]
        tv.set_hidden("center", "smoosh", True, path=p)      # idempotent add
        assert hid() == ["smoosh"]
        tv.set_hidden("center", "smoosh", False, path=p)     # unhide removes
        assert hid() == []

def test_tool_visibility_panel_reflects_and_toggles():
    qt_app()
    from maya_tools.framework.toolbar.tool_visibility import ToolVisibilityPanel
    groups = [{"id": "g", "label": "G", "items": [
        {"id": "a", "type": "IconButton", "title": "Alpha"},
        {"id": "b", "type": "IconButton", "tooltip": "Bravo"}]}]
    calls = []
    panel = ToolVisibilityPanel(
        groups=groups, hidden_by_group={"g": {"b"}},
        on_change=lambda gid, iid, hidden: calls.append((gid, iid, hidden)))
    assert panel.boxes[("g", "a")].isChecked()               # visible = checked
    assert not panel.boxes[("g", "b")].isChecked()           # hidden = unchecked
    panel.boxes[("g", "a")].setChecked(False)                # hide a
    assert calls[-1] == ("g", "a", True)
    panel.boxes[("g", "b")].setChecked(True)                 # show b
    assert calls[-1] == ("g", "b", False)

def test_expanding_slider_handle_labels_and_range_inputs():
    qt_app()
    from maya_tools.framework.toolbar.widgets.expanding_slider import ExpandingSlider
    from maya_tools.utils.qt.mindmeld import mindmeld_style
    s = ExpandingSlider("OFF", minimum=-100.0, maximum=100.0, default=0.0,
                        center_return=True, handle_label="OS", handle_color="ember",
                        notches=10, range_inputs=True)
    s.show()
    assert s._notches == 10                                   # explicit override honored
    assert s._slider._handle_label == "OS"
    assert s._slider._handle_color == mindmeld_style.TOKENS["ember"]   # token -> hex
    assert s._min_spin.value() == -100 and s._max_spin.value() == 100
    # editing the max field re-ranges the slider live
    s._max_spin.setValue(200)
    assert s._max == 200.0
    s._slider.setValue(1000)
    assert abs(s.current_value() - 200.0) < 1e-6
    # insert keeps the auto integer notch count + its own green label
    ins = ExpandingSlider("IN", minimum=0.0, maximum=10.0, default=0.0,
                          center_return=True, handle_label="IN", handle_color="plasma")
    assert ins._notches == 10 and ins._min_spin is None
    assert ins._slider._handle_label == "IN"

def test_dock_mode_sides_retired():
    # Side docks removed (Adrian, 2026-07-03): left/right - and any stale pref -
    # collapse to bottom so a machine last docked to a side heals on launch.
    from maya_tools.framework.toolbar import dock_targets as dt
    assert dt.normalize_mode("left") == "bottom"
    assert dt.normalize_mode("right") == "bottom"
    assert dt.normalize_mode("side_left") == "bottom"
    assert dt.normalize_mode("side_right") == "bottom"
    assert dt.normalize_mode("garbage") == "bottom"
    assert dt.normalize_mode("top") == "top"
    assert dt.normalize_mode("bottom") == "bottom"
    assert dt.normalize_mode("floating") == "floating"

def test_icon_button_hover_brightens_graphic():
    # Shelf-style hover (Adrian, 2026-07-03): the icon graphic brightens (a
    # distinct QIcon), restored on leave; no square is involved.
    qt_app()
    from maya_tools.framework.toolbar.widgets.icon_button import IconButton
    b = IconButton("X", icon="fs_snap.png")
    assert b._base_icon is not None and b._hover_icon is None
    base_key = b.icon().cacheKey()
    b._hovered = True; b._refresh_icon()
    assert b._hover_icon is not None                 # brightened copy built
    assert b.icon().cacheKey() != base_key           # ...and shown on hover
    b._hovered = False; b._refresh_icon()
    assert b.icon().cacheKey() == base_key           # base restored on leave

def test_prefs_atomic_write_leaves_no_tmp():
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fabricator_toolbar_prefs.json"
        prefs = toolbar_prefs.default_prefs()
        prefs["dock_mode"] = "left"
        toolbar_prefs.save_prefs(prefs, path=p)
        assert json.loads(p.read_text(encoding="utf-8"))["dock_mode"] == "left"
        assert not [f for f in os.listdir(td) if f.endswith(".tmp")]
        toolbar_prefs.save_prefs(prefs, path=p)   # overwrite path too
        assert not [f for f in os.listdir(td) if f.endswith(".tmp")]

def test_prefs_type_coercion():
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fabricator_toolbar_prefs.json"
        p.write_text(json.dumps({"visible": "yes", "scale": "big",
                                 "dock_mode": 7, "per_widget": [],
                                 "floating_pos": "nope"}), encoding="utf-8")
        prefs = toolbar_prefs.load_prefs(path=p)
        assert prefs["visible"] is True           # wrong type -> that key's default
        assert prefs["scale"] == 1.0
        assert prefs["dock_mode"] == "bottom"
        assert prefs["per_widget"] == {}
        assert prefs["floating_pos"] is None
        p.write_text(json.dumps({"scale": 2}), encoding="utf-8")
        assert toolbar_prefs.load_prefs(path=p)["scale"] == 2.0   # int widens

def test_prefs_floating_pos_roundtrip():
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fabricator_toolbar_prefs.json"
        prefs = toolbar_prefs.default_prefs()
        assert prefs["floating_pos"] is None
        prefs["floating_pos"] = [120, 340]
        toolbar_prefs.save_prefs(prefs, path=p)
        assert toolbar_prefs.load_prefs(path=p)["floating_pos"] == [120, 340]

def test_prefs_layout_roundtrip_and_coercion():
    from maya_tools.framework.toolbar import toolbar_prefs
    # Schema: layout.groups maps group_id -> {"order": [item ids], "hidden": []}.
    # "hidden" ships EMPTY and unused by any UI in v1 - the schema already
    # expresses the future show/hide feature (foundation requirement).
    assert toolbar_prefs.default_prefs()["layout"] == {"version": 1, "groups": {}}
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fabricator_toolbar_prefs.json"
        prefs = toolbar_prefs.default_prefs()
        prefs["layout"]["groups"]["skinning"] = {
            "order": ["smoosh", "paint_on_select"], "hidden": ["avg_weights"]}
        toolbar_prefs.save_prefs(prefs, path=p)
        again = toolbar_prefs.load_prefs(path=p)
        assert again["layout"]["groups"]["skinning"]["order"] == [
            "smoosh", "paint_on_select"]
        assert again["layout"]["groups"]["skinning"]["hidden"] == ["avg_weights"]
        # wrong-typed value falls back to the dict default (existing _coerce)
        p.write_text(json.dumps({"layout": "nope"}), encoding="utf-8")
        assert toolbar_prefs.load_prefs(path=p)["layout"] == {
            "version": 1, "groups": {}}
        # a loaded default must be a COPY - mutating it can't poison the module
        fresh = toolbar_prefs.load_prefs(path=Path(td) / "missing.json")
        fresh["layout"]["groups"]["x"] = {"order": [], "hidden": []}
        assert toolbar_prefs.default_prefs()["layout"]["groups"] == {}

def test_apply_layout_prefs_order_and_append():
    from maya_tools.framework.toolbar import toolbar_manifest as tm
    groups = [{"id": "g", "label": "G", "items": [
        {"type": "IconButton", "id": "a", "glyph": "A", "command": "json:dumps"},
        {"type": "IconButton", "id": "b", "glyph": "B", "command": "json:dumps"},
        {"type": "IconButton", "id": "c", "glyph": "C", "command": "json:dumps"},
    ]}]
    layout = {"version": 1,
              "groups": {"g": {"order": ["c", "zombie", "a"], "hidden": []}}}
    out = tm.apply_layout_prefs(groups, layout)
    # prefs order applied; unknown id dropped silently; manifest ids missing
    # from the order APPENDED in manifest order (new tools always appear)
    assert [i["id"] for i in out[0]["items"]] == ["c", "a", "b"]
    assert out[0]["label"] == "G"                       # group keys survive
    assert [i["id"] for i in groups[0]["items"]] == ["a", "b", "c"]  # input intact
    # duplicated id in the order (hand-edited prefs): first occurrence
    # wins, the item is placed exactly once
    out_dup = tm.apply_layout_prefs(
        groups, {"version": 1,
                 "groups": {"g": {"order": ["b", "a", "b"], "hidden": []}}})
    assert [i["id"] for i in out_dup[0]["items"]] == ["b", "a", "c"]
    # non-string (unhashable) entry in the order: dropped, NOT a TypeError
    # escaping _resolved_groups and killing launch()
    out_bad = tm.apply_layout_prefs(
        groups, {"version": 1,
                 "groups": {"g": {"order": [["a"], "c"], "hidden": []}}})
    assert [i["id"] for i in out_bad[0]["items"]] == ["c", "a", "b"]

def test_apply_layout_prefs_hidden_and_noop():
    from maya_tools.framework.toolbar import toolbar_manifest as tm
    groups = [{"id": "g", "items": [
        {"type": "IconButton", "id": "a", "glyph": "A", "command": "json:dumps"},
        {"type": "IconButton", "id": "b", "glyph": "B", "command": "json:dumps"},
    ]}]
    # hidden filters an ordered id...
    out = tm.apply_layout_prefs(
        groups, {"version": 1, "groups": {"g": {"order": ["b"], "hidden": ["a"]}}})
    assert [i["id"] for i in out[0]["items"]] == ["b"]
    # ...and an APPENDED id too (future show/hide is prefs-only by design)
    out2 = tm.apply_layout_prefs(
        groups, {"version": 1, "groups": {"g": {"order": [], "hidden": ["b"]}}})
    assert [i["id"] for i in out2[0]["items"]] == ["a"]
    # empty / missing / malformed layout and untouched groups: all no-ops
    for noop in (None, {}, {"groups": {}}, {"groups": "bad"},
                 {"version": 1, "groups": {"other": {"order": ["x"], "hidden": []}}},
                 {"version": 1, "groups": {"g": "bad"}}):
        res = tm.apply_layout_prefs(groups, noop)
        assert [i["id"] for i in res[0]["items"]] == ["a", "b"], noop

def test_strip_reorder_bookkeeping():
    qt_app()
    from maya_tools.framework.toolbar.toolbar_ui import FSToolbarStrip
    groups = [
        {"id": "g1", "items": [
            {"type": "IconButton", "id": "a", "glyph": "A", "command": lambda: None}]},
        {"id": "g2", "items": [
            {"type": "IconButton", "id": "b", "glyph": "B", "command": lambda: None}]},
    ]
    s = FSToolbarStrip(orientation="horizontal", groups=groups)
    wa = s.widget_by_id["a"]; wb = s.widget_by_id["b"]
    # reverse map: reorder.py resolves a dragged widget back to its item id
    assert s.id_by_widget[wa] == "a" and s.id_by_widget[wb] == "b"
    # each group box knows its manifest group id (persistence key)
    assert wa.parentWidget().property("fs_group_id") == "g1"
    assert wb.parentWidget().property("fs_group_id") == "g2"

def test_hover_suppression_switch():
    qt_app()
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtWidgets import QLabel
    from maya_tools.framework.toolbar.widgets import popover_button
    from maya_tools.framework.toolbar.widgets.popover_button import PopoverButton
    p = PopoverButton("SM", popover_factory=lambda: QLabel("x"))
    p.show()
    c = QPointF(p.rect().center())
    g = QPointF(p.mapToGlobal(p.rect().center()))
    try:
        popover_button.set_hover_suppressed(True)
        assert popover_button.hover_suppressed() is True
        p.enterEvent(QEnterEvent(c, g, g))
        # suppressed: the 350ms open timer must NEVER arm (edit-mode rule)
        assert not p._hover_timer.isActive()
    finally:
        popover_button.set_hover_suppressed(False)   # never leak into other tests
    assert popover_button.hover_suppressed() is False
    p.enterEvent(QEnterEvent(c, g, g))
    assert p._hover_timer.isActive()                 # normal behavior restored
    # timer armed BEFORE suppression flips on: the armed timer still fires,
    # so _open_popover's final gate must swallow the open (a popover opening
    # mid-edit would drop live tool widgets outside the reorder filter)
    try:
        popover_button.set_hover_suppressed(True)
        p._hover_timer.stop()                        # fire the timeout path
        p._hover_timer.timeout.emit()                # deterministically
        assert not p.is_open()
    finally:
        popover_button.set_hover_suppressed(False)   # never leak into other tests
    p._open_popover()                                # gate lifts with the flag
    assert p.is_open()
    p.close_popover()

def test_edit_mode_enter_exit_lifecycle():
    qt_app()
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QEnterEvent, QMouseEvent
    from PySide6.QtWidgets import QApplication, QLabel
    from maya_tools.framework.toolbar import reorder
    from maya_tools.framework.toolbar.toolbar_ui import FSToolbarStrip
    from maya_tools.framework.toolbar.widgets import popover_button
    fired = []
    groups = [{"id": "g1", "items": [
        {"type": "PopoverButton", "id": "pop", "glyph": "P",
         "popover": lambda: QLabel("x")},
        {"type": "IconButton", "id": "tool", "glyph": "T",
         "command": lambda: fired.append(1)},
    ]}]
    strip = FSToolbarStrip(orientation="horizontal", groups=groups)
    strip.show()
    qt_app().processEvents()
    pop = strip.widget_by_id["pop"]
    tool = strip.widget_by_id["tool"]
    pop.click()                              # open the popover...
    pop._popover._set_pinned()               # ...and PIN it (the hard case)
    # prefs_path ALWAYS points at a tempdir in tests: once Task 7 lands,
    # a drop that moved something persists on release - a None path
    # anywhere in the suite risks writing the user's REAL prefs file.
    with tempfile.TemporaryDirectory() as td:
        ctl = reorder.ReorderController(
            strip, prefs_path=Path(td) / "fabricator_toolbar_prefs.json")
        ctl.enter()
        assert ctl.is_active()
        assert not pop.is_open()             # entering closed the pinned panel
        assert strip.property("edit_mode") is True     # QSS hook is live
        assert popover_button.hover_suppressed() is True
        # hover during edit mode never arms the 350ms timer
        c = QPointF(pop.rect().center())
        g = QPointF(pop.mapToGlobal(pop.rect().center()))
        pop.enterEvent(QEnterEvent(c, g, g))
        assert not pop._hover_timer.isActive()
        # a REAL click cycle through Qt event delivery: the filter consumes
        # press+release, so clicked never emits - the command NEVER fires
        # mid-edit (the Smoosh click-command is a real skin op).
        lc = QPointF(tool.rect().center())
        lg = QPointF(tool.mapToGlobal(tool.rect().center()))
        QApplication.sendEvent(tool, QMouseEvent(
            QEvent.MouseButtonPress, lc, lg, lg,
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        QApplication.sendEvent(tool, QMouseEvent(
            QEvent.MouseButtonRelease, lc, lg, lg,
            Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
        assert fired == []
        # enter() closed the pinned panel via deleteLater: flush the
        # deferred delete NOW so exit() runs after the frame's wrapper is
        # dead (the frame is a Qt.Tool top-level window, so the
        # same-window filter never watched it, and filter removal is
        # try/except'd regardless). exit() must NOT raise, and
        # suppression must clear below.
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        ctl.exit()
        assert not ctl.is_active()
        assert strip.property("edit_mode") is False
        assert popover_button.hover_suppressed() is False
        tool.click()                         # normal behavior restored
        assert fired == [1]

def test_edit_mode_esc_exits():
    qt_app()
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from maya_tools.framework.toolbar import reorder
    from maya_tools.framework.toolbar.toolbar_ui import FSToolbarStrip
    from maya_tools.framework.toolbar.widgets import popover_button
    groups = [{"id": "g1", "items": [
        {"type": "IconButton", "id": "a", "glyph": "A", "command": lambda: None}]}]
    strip = FSToolbarStrip(orientation="horizontal", groups=groups)
    strip.show()
    ctl = reorder.ReorderController(strip)
    prev_policy = strip.focusPolicy()
    ctl.enter()
    assert strip.focusPolicy() == Qt.StrongFocus     # edit mode takes keys
    esc = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    assert ctl.eventFilter(strip, esc) is True
    assert not ctl.is_active()
    assert strip.property("edit_mode") is False
    assert popover_button.hover_suppressed() is False
    assert strip.focusPolicy() == prev_policy        # policy round-trips

def test_edit_mode_click_outside_exits():
    qt_app()
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QLabel
    from maya_tools.framework.toolbar import reorder
    from maya_tools.framework.toolbar.toolbar_ui import FSToolbarStrip
    groups = [{"id": "g1", "items": [
        {"type": "IconButton", "id": "a", "glyph": "A", "command": lambda: None}]}]
    strip = FSToolbarStrip(orientation="horizontal", groups=groups)
    strip.show()

    class _Witness(QLabel):
        """Records presses that actually LAND (a consumed press never
        reaches mousePressEvent)."""
        def __init__(self):
            super().__init__("elsewhere")
            self.presses = 0
        def mousePressEvent(self, e):
            self.presses += 1
            super().mousePressEvent(e)

    outside = _Witness()
    outside.show()
    ctl = reorder.ReorderController(strip)
    ctl.enter()

    def press_on(w):
        return QMouseEvent(QEvent.MouseButtonPress, QPointF(2, 2),
                           w.mapToGlobal(QPointF(2, 2)),
                           Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)

    # a press INSIDE the strip must not end edit mode (it starts a drag) -
    # sendEvent routes through the app-level filter (CP-D wave-1)
    QApplication.sendEvent(strip.widget_by_id["a"], press_on(strip.widget_by_id["a"]))
    assert ctl.is_active()
    # ...but a press anywhere OUTSIDE ends it, and the click is NOT eaten
    QApplication.sendEvent(outside, press_on(outside))
    assert not ctl.is_active()
    assert strip.property("edit_mode") is False
    assert outside.presses == 1   # the click still LANDED where aimed
    outside.deleteLater()

def test_edit_mode_keys_consumed():
    qt_app()
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from maya_tools.framework.toolbar import reorder
    from maya_tools.framework.toolbar.toolbar_ui import FSToolbarStrip
    fired = []
    groups = [{"id": "g1", "items": [
        {"type": "IconButton", "id": "a", "glyph": "A",
         "command": lambda: fired.append(1)}]}]
    strip = FSToolbarStrip(orientation="horizontal", groups=groups)
    strip.show()
    ctl = reorder.ReorderController(strip)
    ctl.enter()
    tool = strip.widget_by_id["a"]
    # Tab moves focus onto a button and Space/Return CLICKS it - a watched
    # item must swallow every key except Esc (no tool fires mid-edit)
    space = QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier)
    assert ctl.eventFilter(tool, space) is True      # consumed
    assert fired == []                               # command never fired
    ctl.exit()

def test_edit_mode_module_entry_points():
    qt_app()
    from maya_tools.framework.toolbar import reorder
    from maya_tools.framework.toolbar.toolbar_ui import FSToolbarStrip
    from maya_tools.framework.toolbar.widgets import popover_button
    groups = [{"id": "g1", "items": [
        {"type": "IconButton", "id": "a", "glyph": "A", "command": lambda: None}]}]
    strip = FSToolbarStrip(orientation="horizontal", groups=groups)
    strip.show()
    ctl = reorder.enter_edit_mode(strip)
    assert ctl is not None and ctl.is_active()
    assert reorder.enter_edit_mode(strip) is ctl     # idempotent: same
    assert ctl.is_active()                           # controller, still on
    ctl.exit()
    strip2 = FSToolbarStrip(orientation="horizontal", groups=groups)
    strip2.show()
    ctl2 = reorder.enter_edit_mode(strip2)           # rebuilt strip (new
    assert ctl2 is not ctl and ctl2.is_active()      # identity): fresh ctl
    reorder.exit_edit_mode()
    assert not ctl2.is_active()
    assert popover_button.hover_suppressed() is False

def test_edit_mode_drag_reorders_and_persists():
    qt_app()
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from maya_tools.framework.toolbar import reorder, toolbar_prefs
    from maya_tools.framework.toolbar.toolbar_ui import FSToolbarStrip

    def make_groups(fired):
        return [{"id": "g1", "items": [
            {"type": "IconButton", "id": "a", "glyph": "A",
             "command": lambda: fired.append("a")},
            {"type": "IconButton", "id": "b", "glyph": "B",
             "command": lambda: fired.append("b")},
            {"type": "IconButton", "id": "c", "glyph": "C",
             "command": lambda: fired.append("c")},
        ]}]

    def mouse(t, gp, button, buttons):
        return QMouseEvent(t, QPointF(0, 0), gp, gp, button, buttons,
                           Qt.NoModifier)

    def center_g(widget, axis, delta):
        gc = widget.parentWidget().mapToGlobal(widget.geometry().center())
        return (QPointF(gc.x() + delta, gc.y()) if axis == "x"
                else QPointF(gc.x(), gc.y() + delta))

    def order_of(strip, box):
        lay = box.layout()
        return [strip.id_by_widget[lay.itemAt(i).widget()]
                for i in range(lay.count())]

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fabricator_toolbar_prefs.json"
        fired = []

        # -- horizontal: drag A rightward past C's midpoint (axis x) --------
        strip = FSToolbarStrip(orientation="horizontal",
                               groups=make_groups(fired))
        strip.show()
        qt_app().processEvents()             # flush layout: real geometries
        ctl = reorder.ReorderController(strip, prefs_path=p)
        ctl.enter()
        wa = strip.widget_by_id["a"]; wc = strip.widget_by_id["c"]
        box = wa.parentWidget()
        assert order_of(strip, box) == ["a", "b", "c"]
        # a plain click (press+release, NO move) must persist NOTHING: a
        # stray order entry would pin this group against future manifest
        # reorderings
        ctl.eventFilter(wa, mouse(QEvent.MouseButtonPress,
                                  center_g(wa, "x", 0),
                                  Qt.LeftButton, Qt.LeftButton))
        ctl.eventFilter(wa, mouse(QEvent.MouseButtonRelease,
                                  center_g(wa, "x", 0),
                                  Qt.LeftButton, Qt.NoButton))
        assert not p.exists()         # click-without-drag wrote nothing
        ctl.eventFilter(wa, mouse(QEvent.MouseButtonPress,
                                  center_g(wa, "x", 0),
                                  Qt.LeftButton, Qt.LeftButton))
        ctl.eventFilter(wa, mouse(QEvent.MouseMove,
                                  center_g(wc, "x", 4),
                                  Qt.NoButton, Qt.LeftButton))
        # live swap DURING the drag: the layout itself is the drop indicator
        assert order_of(strip, box) == ["b", "c", "a"]
        ctl.eventFilter(wa, mouse(QEvent.MouseButtonRelease,
                                  center_g(wa, "x", 0),
                                  Qt.LeftButton, Qt.NoButton))
        saved = toolbar_prefs.load_prefs(path=p)
        assert saved["layout"]["groups"]["g1"]["order"] == ["b", "c", "a"]
        assert saved["layout"]["groups"]["g1"]["hidden"] == []   # ships empty
        assert fired == []            # consumed: no command fired mid-drag
        ctl.exit()

        # -- vertical: axis flips to y; drag C upward past A's midpoint -----
        strip_v = FSToolbarStrip(orientation="vertical",
                                 groups=make_groups(fired))
        strip_v.show()
        qt_app().processEvents()
        ctl_v = reorder.ReorderController(strip_v, prefs_path=p)
        ctl_v.enter()
        va = strip_v.widget_by_id["a"]; vc = strip_v.widget_by_id["c"]
        vbox = vc.parentWidget()
        ctl_v.eventFilter(vc, mouse(QEvent.MouseButtonPress,
                                    center_g(vc, "y", 0),
                                    Qt.LeftButton, Qt.LeftButton))
        ctl_v.eventFilter(vc, mouse(QEvent.MouseMove,
                                    center_g(va, "y", -4),
                                    Qt.NoButton, Qt.LeftButton))
        ctl_v.eventFilter(vc, mouse(QEvent.MouseButtonRelease,
                                    center_g(vc, "y", 0),
                                    Qt.LeftButton, Qt.NoButton))
        assert order_of(strip_v, vbox) == ["c", "a", "b"]
        assert toolbar_prefs.load_prefs(
            path=p)["layout"]["groups"]["g1"]["order"] == ["c", "a", "b"]
        ctl_v.exit()

def test_qss_has_edit_mode_rules():
    # Contract guard: reorder.py sets the strip's edit_mode property; the
    # visual only exists if mindmeld.qss actually styles that selector.
    qss = (REPO_ROOT / "maya_tools" / "utils" / "qt" / "mindmeld"
           / "mindmeld.qss").read_text(encoding="utf-8")
    assert 'QWidget#fabricator_toolbar[edit_mode="true"]' in qss
    assert 'QWidget#fabricator_toolbar[edit_mode="true"] QPushButton' in qss

def test_manifest_real_file_validates():
    from maya_tools.framework.toolbar import toolbar_manifest as tm
    data = tm.load_manifest()                        # ships toolbar.json (Task 10)
    tm.validate_manifest(data)
    assert data["groups"], "shipped manifest has groups"

def test_strip_zones_place_left_center_right():
    qt_app()
    from maya_tools.framework.toolbar.toolbar_ui import FSToolbarStrip
    # Wave B: groups carry a zone; the strip lays them out left / center /
    # right with stretches between (stretches are non-widget layout items).
    groups = [
        {"id": "L", "zone": "logo",     "items": [{"type": "IconButton", "id": "a", "glyph": "A", "command": lambda: None}]},
        {"id": "C", "zone": "center",   "items": [{"type": "IconButton", "id": "b", "glyph": "B", "command": lambda: None}]},
        {"id": "R", "zone": "settings", "items": [{"type": "IconButton", "id": "c", "glyph": "C", "command": lambda: None}]},
    ]
    s = FSToolbarStrip(orientation="horizontal", groups=groups)
    assert set(s.widget_by_id) == {"a", "b", "c"}
    lay = s.layout()
    order = [lay.itemAt(i).widget().property("fs_group_id")
             for i in range(lay.count()) if lay.itemAt(i).widget() is not None]
    assert order == ["L", "C", "R"]

def test_strip_edges_are_squishy():
    qt_app()
    from PySide6.QtCore import Qt
    from maya_tools.framework.toolbar import dpi
    from maya_tools.framework.toolbar.toolbar_ui import WALL_GAP, FSToolbarStrip
    groups = [
        {"id": "L", "zone": "logo",     "items": [{"type": "IconButton", "id": "a", "glyph": "A", "command": lambda: None}]},
        {"id": "R", "zone": "settings", "items": [{"type": "IconButton", "id": "b", "glyph": "B", "command": lambda: None}]},
    ]
    s = FSToolbarStrip(orientation="horizontal", groups=groups)
    lay = s.layout()
    spacers = [lay.itemAt(i).spacerItem() for i in range(lay.count())
               if lay.itemAt(i).spacerItem() is not None]
    # wall gaps do NOT expand horizontally (Maximum policy -> shrink-only);
    # the between-zone gaps DO (addStretch -> Expanding).
    edges = [sp for sp in spacers if not (sp.expandingDirections() & Qt.Horizontal)]
    stretches = [sp for sp in spacers if sp.expandingDirections() & Qt.Horizontal]
    assert len(edges) == 2 and len(stretches) >= 1
    # each wall gap prefers WALL_GAP as its width but can give it back
    assert all(sp.sizeHint().width() == dpi.scaled(WALL_GAP) for sp in edges)

def test_cluster_is_single_reorder_unit():
    qt_app()
    from maya_tools.framework.toolbar.toolbar_ui import FSToolbarStrip
    from maya_tools.framework.toolbar import reorder
    from maya_tools.framework.toolbar.widgets.icon_button import IconButton
    groups = [{"id": "center", "zone": "center", "items": [
        {"type": "IconButton", "id": "solo", "glyph": "S", "command": lambda: None},
        {"type": "Cluster", "id": "pair", "items": [
            {"type": "IconButton", "id": "c_a", "glyph": "A", "command": lambda: None},
            {"type": "IconButton", "id": "c_b", "glyph": "B", "command": lambda: None},
        ]},
    ]}]
    s = FSToolbarStrip(orientation="horizontal", groups=groups)
    # the cluster registers as ONE reorder unit; its sub-buttons do not
    assert "pair" in s.widget_by_id
    assert "c_a" not in s.widget_by_id and "c_b" not in s.widget_by_id
    cluster = s.widget_by_id["pair"]
    subs = cluster.findChildren(IconButton)
    assert len(subs) == 2                      # sub-buttons are real, inside the composite
    # reorder maps a sub-button back to the cluster: the pair drags as one
    ctl = reorder.ReorderController(s)
    assert ctl._item_of(subs[0]) is cluster

def test_manifest_validates_cluster():
    import copy
    from maya_tools.framework.toolbar import toolbar_manifest as tm
    good = {"version": 1, "groups": [{"id": "g", "zone": "center", "items": [
        {"type": "Cluster", "id": "pair", "items": [
            {"type": "IconButton", "id": "x", "glyph": "X", "command": "json:dumps"},
            {"type": "IconButton", "id": "y", "glyph": "Y", "command": "json:dumps"},
        ]}]}]}
    tm.validate_manifest(good)                 # a cluster with sub-items is legal
    empty = copy.deepcopy(good)
    empty["groups"][0]["items"][0]["items"] = []
    try:
        tm.validate_manifest(empty); assert False, "empty cluster not caught"
    except ValueError:
        pass
    nested = copy.deepcopy(good)
    nested["groups"][0]["items"][0]["items"][0] = {
        "type": "Cluster", "id": "z", "items": [
            {"type": "IconButton", "id": "w", "glyph": "W", "command": "json:dumps"}]}
    try:
        tm.validate_manifest(nested); assert False, "nested cluster not caught"
    except ValueError:
        pass

def test_provisional_leave_close_arms_and_cancels():
    qt_app()
    from PySide6.QtWidgets import QLabel
    from maya_tools.framework.toolbar.widgets.popover_button import PopoverButton
    # 2026-07-03: a provisional panel arms a DELAYED close on leave; enter (or
    # returning to the owner) cancels; committed panels never arm it.
    p = PopoverButton("SEL", popover_factory=lambda: QLabel("x"))
    p.show()
    p._open_popover()
    frame = p._popover
    frame.arm_close()
    assert frame._close_timer.isActive()
    frame.cancel_close()
    assert not frame._close_timer.isActive()
    frame._set_pinned()                       # committed
    frame.arm_close()
    assert not frame._close_timer.isActive()  # X only, no leave-close
    p.close_popover()

def test_renamer_inline_expands_on_hover_focus():
    # RenamerPopover retired 2026-07-05 (commit 4410de8): RenamerInline no
    # longer takes popover_factory (the strip IS the whole tool now), and
    # the old click-to-cycle _flip_mode() became _show_mode_menu()'s
    # 4-radio picker over the same _set_mode(mode) call the menu itself
    # uses - so _set_mode() here exercises the identical collapse-on-
    # mode-switch behavior the retired _flip_mode() call did.
    qt_app()
    from maya_tools.framework.toolbar import dpi
    from maya_tools.framework.toolbar.widgets import renamer_inline as ri
    r = ri.RenamerInline()
    # default mode is HASH -> idle uses the wider hash collapse width
    assert r._stack.maximumWidth() == dpi.scaled(ri.COLLAPSED_W_HASH)
    assert not r.is_expanded()
    r._focused = True
    r._apply_expanded()
    assert r.is_expanded() and r._anim.endValue() == dpi.scaled(ri.EXPANDED_W)
    r._stack.setMaximumWidth(dpi.scaled(ri.EXPANDED_W))   # pretend the anim finished
    r._focused = False
    r._apply_expanded()
    assert not r.is_expanded() and r._anim.endValue() == dpi.scaled(ri.COLLAPSED_W_HASH)
    # switching to find/replace settles to the narrower compact width
    r._set_mode(ri.FIND_REPLACE)
    assert r._anim.endValue() == dpi.scaled(ri.COLLAPSED_W)

def test_renamer_inline_toggle_and_single_unit():
    # RenamerPopover retired 2026-07-05 (commit 4410de8): RenamerInline no
    # longer takes popover_factory, and mode switching is _set_mode()/
    # _show_mode_menu() (a menu), not a click-flip - so this exercises
    # _set_mode() directly in place of the retired _flip_mode(). The
    # manifest item's "popover" key below is now dead data for this widget
    # type (_make_renamer_inline ignores it entirely); left in place only
    # because this raw groups dict bypasses validate_manifest and
    # FSToolbarStrip tolerates the unknown key.
    qt_app()
    from PySide6.QtWidgets import QLabel, QLineEdit
    from maya_tools.framework.toolbar.widgets.renamer_inline import (
        RenamerInline, HASH, FIND_REPLACE)
    from maya_tools.framework.toolbar.toolbar_ui import FSToolbarStrip
    from maya_tools.framework.toolbar import reorder
    # item 1: mode switch flips Hash (one field) <-> Find/Replace (two fields)
    r = RenamerInline()
    # the toggle now carries a per-mode ICON (glyph is the missing-file fallback)
    assert r.mode() == HASH and r._stack.currentIndex() == 0
    assert not r._toggle.icon().isNull()        # hash icon set
    r._set_mode(FIND_REPLACE)
    assert r.mode() == FIND_REPLACE and r._stack.currentIndex() == 1
    assert not r._toggle.icon().isNull()        # find/replace icon set
    # in a strip it is ONE reorder unit; the inner fields are not reorder items
    groups = [{"id": "center", "zone": "center", "items": [
        {"type": "RenamerInline", "id": "renamer", "glyph": "REN",
         "popover": lambda: QLabel("full")}]}]
    s = FSToolbarStrip(orientation="horizontal", groups=groups)
    assert "renamer" in s.widget_by_id
    inline = s.widget_by_id["renamer"]
    fields = inline.findChildren(QLineEdit)
    # four-mode consolidation (2026-07-05): hash, find, replace, prefix/
    # suffix text, select-by-name = 5 QLineEdits (this count was 3 under
    # the old 2-mode design it originally documented).
    assert len(fields) == 5
    ctl = reorder.ReorderController(s)
    assert ctl._item_of(fields[0]) is inline    # a field drags the whole inline

def test_expanding_slider_custom_gesture_wiring():
    # idea A: an ExpandingSlider may supply its OWN gesture_begin/gesture_end
    # commands (insert-joints captures the pair + owns the chunk); absent them
    # the generic begin/end still wire.
    from maya_tools.framework.toolbar import (toolbar_app, toolbar_commands as tc,
                                              toolbar_manifest as tm)
    custom = {"type": "ExpandingSlider", "id": "x", "glyph": "X",
              "min": 0, "max": 5, "default": 0, "live": True,
              "command": "json:dumps", "gesture_begin": "json:loads",
              "gesture_end": "json:dumps"}
    r = toolbar_app._resolve_item(dict(custom), tm, tc)
    assert callable(r["command"]) and callable(r["gesture_begin"]) and callable(r["gesture_end"])
    generic = {"type": "ExpandingSlider", "id": "y", "glyph": "Y",
               "min": 0, "max": 5, "default": 0, "live": True, "command": "json:dumps"}
    r2 = toolbar_app._resolve_item(dict(generic), tm, tc)
    assert callable(r2["gesture_begin"]) and callable(r2["gesture_end"])

def test_mirror_menu_and_constraints_removed():
    # 2026-07-07 (Adrian): the option-popovers became click-menus and the
    # Constraints button was removed from the strip. mirror_menu() returns
    # exactly the three mirror rows (Disconnect Mirror still gone), and the
    # ship manifest no longer carries a constraints item.
    from maya_tools.framework.toolbar.popovers import mirror_menu
    labels = [row[0] for row in mirror_menu()]
    assert labels == ["Mirror Joints (YZ)", "Mirror Skin Weights",
                      "Mirror Ctrl Curves"], labels
    assert "Disconnect Mirror" not in labels
    from maya_tools.framework.toolbar import toolbar_manifest as tm
    ids = [it["id"] for g in tm.load_manifest()["groups"] for it in g["items"]]
    assert "constraints" not in ids, ids


def test_menu_button_rows_and_firing():
    # MenuButton row builder (Qt-only, no menu event loop): 3-tuple, 2-tuple,
    # a None separator, and a handler=None disabled row all render right, and
    # the enabled rows fire their handlers.
    qt_app()
    from PySide6.QtWidgets import QMenu
    from maya_tools.framework.toolbar.widgets.menu_button import MenuButton
    fired = []
    rows = [("Do A", "tip A", lambda: fired.append("a")),
            None,                                    # separator
            ("Inert", "", None),                     # disabled
            ("Do B", lambda: fired.append("b"))]     # 2-tuple form
    btn = MenuButton("M", menu_factory=lambda: rows)
    menu = QMenu(btn)
    for r in rows:
        MenuButton._add_row(menu, r)
    acts = menu.actions()
    assert [a.text() for a in acts if not a.isSeparator()] == ["Do A", "Inert", "Do B"]
    assert any(a.isSeparator() for a in acts)
    assert not [a for a in acts if a.text() == "Inert"][0].isEnabled()
    [a for a in acts if a.text() == "Do A"][0].trigger()
    [a for a in acts if a.text() == "Do B"][0].trigger()
    assert fired == ["a", "b"], fired


def test_menu_button_builds_via_registry_and_annotates():
    qt_app()
    from maya_tools.framework.toolbar.toolbar_ui import _build_widget, FSToolbarStrip
    from maya_tools.framework.toolbar.widgets.menu_button import MenuButton
    w = _build_widget({"type": "MenuButton", "id": "m", "glyph": "M",
                       "menu_factory": lambda: [("X", lambda: None)]})
    assert isinstance(w, MenuButton)
    # MenuButtons annotate too (Adrian, 2026-07-10): their menus moved off
    # left-click to right-click, freeing hover for the annotation card -
    # same style as every other annotated button.
    assert FSToolbarStrip._wants_annotation({"type": "MenuButton", "id": "m"}) is True
    assert FSToolbarStrip._wants_annotation({"type": "IconButton", "id": "b"}) is True

def test_connect_ai_popover_constructs():
    # Task 6: the popover must construct offscreen with no throw - ai_bridge
    # (and its maya-bound handlers chain) is imported lazily inside its
    # methods, never at class/module scope.
    qt_app()
    from maya_tools.framework.toolbar.popovers import ConnectAIPopover
    from PySide6.QtWidgets import QWidget
    w = ConnectAIPopover()
    assert isinstance(w, QWidget)

def test_client_config_snippet_default_port():
    from maya_tools.framework.toolbar.ai_bridge.protocol import DEFAULT_PORT
    from maya_tools.framework.toolbar.popovers import client_config_snippet
    cli = client_config_snippet("claude-code", DEFAULT_PORT)
    assert "uvx" in cli and "fabricator-mcp" in cli and "--port" not in cli
    for client in ("claude-desktop", "cursor"):
        block = client_config_snippet(client, DEFAULT_PORT)
        # F4 (2026-07-06): paste-complete - wrapped in the mcpServers
        # parent object, not a bare "fabricator": {...} fragment.
        assert ('"mcpServers"' in block and '"fabricator"' in block
                and "uvx" in block and "--port" not in block)

def test_client_config_snippet_custom_port():
    from maya_tools.framework.toolbar.popovers import client_config_snippet
    cli = client_config_snippet("claude-code", 7001)
    assert "--port 7001" in cli
    for client in ("claude-desktop", "cursor"):
        block = client_config_snippet(client, 7001)
        assert '"mcpServers"' in block and '"--port", "7001"' in block

def test_connect_ai_prefs_passthrough():
    # connect_ai is a forward-compat pass-through sub-dict (no schema change
    # in toolbar_prefs) - proves it round-trips through load/save unchanged.
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fabricator_toolbar_prefs.json"
        prefs = toolbar_prefs.load_prefs(path=p)
        prefs["connect_ai"] = {"enabled_autostart": True, "port": 7001}
        toolbar_prefs.save_prefs(prefs, path=p)
        again = toolbar_prefs.load_prefs(path=p)
        assert again["connect_ai"] == {"enabled_autostart": True, "port": 7001}

def main():
    check("prefs_roundtrip", test_prefs_roundtrip)
    check("prefs_corrupt_file_returns_defaults", test_prefs_corrupt_file_returns_defaults)
    check("prefs_invalid_utf8_returns_defaults", test_prefs_invalid_utf8_returns_defaults)
    check("dpi_scaled", test_dpi_scaled)
    check("icon_button_fires_command", test_icon_button_fires_command)
    check("icon_button_none_command_safe", test_icon_button_none_command_safe)
    check("expanding_slider_expand_apply_collapse", test_expanding_slider_expand_apply_collapse)
    check("strip_constructs_both_orientations", test_strip_constructs_both_orientations)
    check("manifest_validate_and_errors", test_manifest_validate_and_errors)
    check("manifest_resolve_command", test_manifest_resolve_command)
    check("toggle_lit_state_follows_command", test_toggle_lit_state_follows_command)
    check("toggle_swaps_icon_with_state", test_toggle_swaps_icon_with_state)
    check("button_size_manifest_keys", test_button_size_manifest_keys)
    check("manifest_width_height_universal", test_manifest_width_height_universal)
    check("window_launcher_fires", test_window_launcher_fires)
    check("popover_button_opens_and_closes", test_popover_button_opens_and_closes)
    check("only_one_popover_open_at_a_time", test_only_one_popover_open_at_a_time)
    check("committed_popover_survives_one_at_a_time", test_committed_popover_survives_one_at_a_time)
    check("button_open_relaunches_but_provisional", test_button_open_relaunches_but_provisional)
    check("provisional_dismissed_on_outside_click", test_provisional_dismissed_on_outside_click)
    check("popover_expand_button_only_with_full_window", test_popover_expand_button_only_with_full_window)
    check("popover_interaction_pins_hover_panel", test_popover_interaction_pins_hover_panel)
    check("popover_can_open_gate", test_popover_can_open_gate)
    check("factory_popover_defaults_to_hover_trigger", test_factory_popover_defaults_to_hover_trigger)
    check("popover_stays_on_screen", test_popover_stays_on_screen)
    check("expanding_slider_live_mode_applies_on_drag", test_expanding_slider_live_mode_applies_on_drag)
    check("expanding_slider_resolution_1000", test_expanding_slider_resolution_1000)
    check("expanding_slider_gesture_and_reset", test_expanding_slider_gesture_and_reset)
    check("icon_button_right_click_menu_and_set_glyph", test_icon_button_right_click_menu_and_set_glyph)
    check("strip_builds_from_manifest_structures", test_strip_builds_from_manifest_structures)
    check("manifest_rejects_unknown_keys", test_manifest_rejects_unknown_keys)
    check("manifest_icon_on_is_toggle_only", test_manifest_icon_on_is_toggle_only)
    check("manifest_rejects_command_on_click_popover", test_manifest_rejects_command_on_click_popover)
    check("expanding_slider_focus_out_collapses", test_expanding_slider_focus_out_collapses)
    check("expanding_slider_always_visible_expands_on_click", test_expanding_slider_always_visible_expands_on_click)
    check("expanding_slider_center_return_springs_home", test_expanding_slider_center_return_springs_home)
    check("insert_gesture_user_error_and_leak_guard", test_insert_gesture_user_error_and_leak_guard)
    check("offset_compute_pure", test_offset_compute_pure)
    check("offset_options_setters_and_panel", test_offset_options_setters_and_panel)
    check("expanding_slider_options_flyout", test_expanding_slider_options_flyout)
    check("active_project_pref_roundtrip", test_active_project_pref_roundtrip)
    check("project_chooser_confirm_and_revert", test_project_chooser_confirm_and_revert)
    check("dock_mode_sides_retired", test_dock_mode_sides_retired)
    check("icon_button_hover_brightens_graphic", test_icon_button_hover_brightens_graphic)
    check("tool_visibility_set_hidden_roundtrip", test_tool_visibility_set_hidden_roundtrip)
    check("tool_visibility_panel_reflects_and_toggles", test_tool_visibility_panel_reflects_and_toggles)
    check("expanding_slider_handle_labels_and_range_inputs", test_expanding_slider_handle_labels_and_range_inputs)
    check("prefs_atomic_write_leaves_no_tmp", test_prefs_atomic_write_leaves_no_tmp)
    check("prefs_type_coercion", test_prefs_type_coercion)
    check("prefs_floating_pos_roundtrip", test_prefs_floating_pos_roundtrip)
    check("manifest_real_file_validates", test_manifest_real_file_validates)
    check("strip_zones_place_left_center_right", test_strip_zones_place_left_center_right)
    check("strip_edges_are_squishy", test_strip_edges_are_squishy)
    check("cluster_is_single_reorder_unit", test_cluster_is_single_reorder_unit)
    check("manifest_validates_cluster", test_manifest_validates_cluster)
    check("provisional_leave_close_arms_and_cancels", test_provisional_leave_close_arms_and_cancels)
    check("renamer_inline_expands_on_hover_focus", test_renamer_inline_expands_on_hover_focus)
    check("renamer_inline_toggle_and_single_unit", test_renamer_inline_toggle_and_single_unit)
    check("expanding_slider_custom_gesture_wiring", test_expanding_slider_custom_gesture_wiring)
    check("mirror_menu_and_constraints_removed", test_mirror_menu_and_constraints_removed)
    check("menu_button_rows_and_firing", test_menu_button_rows_and_firing)
    check("menu_button_builds_via_registry_and_annotates", test_menu_button_builds_via_registry_and_annotates)
    check("connect_ai_popover_constructs", test_connect_ai_popover_constructs)
    check("client_config_snippet_default_port", test_client_config_snippet_default_port)
    check("client_config_snippet_custom_port", test_client_config_snippet_custom_port)
    check("connect_ai_prefs_passthrough", test_connect_ai_prefs_passthrough)
    check("prefs_layout_roundtrip_and_coercion", test_prefs_layout_roundtrip_and_coercion)
    check("apply_layout_prefs_order_and_append", test_apply_layout_prefs_order_and_append)
    check("apply_layout_prefs_hidden_and_noop", test_apply_layout_prefs_hidden_and_noop)
    check("strip_reorder_bookkeeping", test_strip_reorder_bookkeeping)
    check("hover_suppression_switch", test_hover_suppression_switch)
    check("edit_mode_enter_exit_lifecycle", test_edit_mode_enter_exit_lifecycle)
    check("edit_mode_esc_exits", test_edit_mode_esc_exits)
    check("edit_mode_click_outside_exits", test_edit_mode_click_outside_exits)
    check("edit_mode_keys_consumed", test_edit_mode_keys_consumed)
    check("edit_mode_module_entry_points", test_edit_mode_module_entry_points)
    check("edit_mode_drag_reorders_and_persists", test_edit_mode_drag_reorders_and_persists)
    check("qss_has_edit_mode_rules", test_qss_has_edit_mode_rules)
    if FAILURES:
        print(f"PHASE A TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("PHASE A TESTS: OK")

if __name__ == "__main__":
    main()
