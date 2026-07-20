"""settings_ui offscreen tests. Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/_dev/test_settings_ui.py

Harness follows test_project_setup_ui.py: offscreen QApplication,
MAYA_APP_DIR monkeypatched per test, dialogs patched (no popups)."""
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from PySide6 import QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _neutralize_unsafe_mglobal_display():
    """MGlobal.displayWarning/.displayError SEGFAULT under bare mayapy
    with no maya.standalone.initialize() (verified 2026-07-07; full
    rationale in test_project_setup_ui.py's copy of this helper).
    displayInfo is safe. MGlobal is an immutable C++-bound type, so the
    module-level `om` NAME inside the shared logger module is replaced
    with a shim: real displayInfo, no-op displayWarning/displayError.
    Applied once, before any LoggerWidget exists in this process."""
    import maya_tools.utils.qt.widgets.logger as logger_mod

    class _SafeMGlobal:
        displayInfo = staticmethod(logger_mod.om.MGlobal.displayInfo)
        displayWarning = staticmethod(lambda *a, **k: None)
        displayError = staticmethod(lambda *a, **k: None)

    class _SafeOM:
        MGlobal = _SafeMGlobal

    logger_mod.om = _SafeOM


_neutralize_unsafe_mglobal_display()

FAILURES = []


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


@contextlib.contextmanager
def maya_app_dir(path):
    old = os.environ.get("MAYA_APP_DIR")
    os.environ["MAYA_APP_DIR"] = str(path)
    try:
        yield Path(path)
    finally:
        if old is None:
            os.environ.pop("MAYA_APP_DIR", None)
        else:
            os.environ["MAYA_APP_DIR"] = old


@contextlib.contextmanager
def env_var(name, value):
    old = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


@contextlib.contextmanager
def patched(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def _configs_root():
    from maya_tools.framework import project_config_paths as pcp
    return pcp.local_configs_dir()


def _make_project(slug="demo", name=None, root=None):
    d = (root if root is not None else _configs_root()) / slug
    d.mkdir(parents=True)
    (d / "session.json").write_text(json.dumps(
        {"name": name or slug.title(), "engine": "unreal5"}), encoding="utf-8")
    (d / "export_mapping.json").write_text(json.dumps(
        {"schema_version": 2, "name": name or slug.title()}), encoding="utf-8")
    return d


def _make_v1_project(slug="oldp"):
    d = _configs_root() / slug
    d.mkdir(parents=True)
    (d / "session.json").write_text(json.dumps(
        {"name": "Old P", "engine": "unreal5",
         "blueprints_dir": "C:/Old/Blueprints"}), encoding="utf-8")
    (d / "export_mapping.json").write_text(json.dumps(
        {"schema_version": 1, "name": "Old P", "project_root": "C:/Old"}),
        encoding="utf-8")
    return d


def _window():
    from maya_tools.framework import settings_ui
    return settings_ui.SettingsUI()


def _row_pills(window):
    """slug -> pill text, read from the list's item widgets."""
    out = {}
    for i in range(window.project_list.count()):
        item = window.project_list.item(i)
        slug = item.data(window.SLUG_ROLE)
        widget = window.project_list.itemWidget(item)
        texts = [lbl.text() for lbl in widget.findChildren(QtWidgets.QLabel)]
        out[slug] = next(t for t in texts if t in ("BOUND", "UNBOUND"))
    return out


def _select(window, slug):
    for i in range(window.project_list.count()):
        if window.project_list.item(i).data(window.SLUG_ROLE) == slug:
            window.project_list.setCurrentRow(i)
            return
    raise AssertionError(f"{slug} not in list")


_ROOTS = {"source_art_root": "D:/Art", "content_root": "D:/Game"}


def test_empty_state():
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        w = _window()
        assert w.editor_stack.currentWidget() is w.empty_label
        assert "No projects found" in w.empty_label.text()
        assert "0 projects found" in w.configs_status_label.text()


def test_pointer_three_states():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        w = _window()
        assert "LOCAL DEFAULT" in w.configs_status_label.text()
        assert not w.configs_local_btn.isEnabled()

        shared = Path(td) / "shared"
        shared.mkdir()
        _make_project("teamproj", root=shared)
        settings_app.set_configs_root(str(shared))
        w = _window()
        assert "POINTER" in w.configs_status_label.text()
        assert w.configs_local_btn.isEnabled()
        assert "teamproj" in _row_pills(w)

        with env_var("FABRICATOR_PROJECT_CONFIGS", str(shared)):
            w = _window()
            assert "ENV VAR" in w.configs_status_label.text()
            assert not w.configs_browse_btn.isEnabled()
            assert not w.configs_local_btn.isEnabled()


def test_browse_sets_pointer_and_reloads():
    from maya_tools.framework import settings_ui, project_config_resolve
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        shared = Path(td) / "shared"
        shared.mkdir()
        _make_project("teamproj", root=shared)
        w = _window()
        with patched(settings_ui.QtWidgets.QFileDialog, "getExistingDirectory",
                     staticmethod(lambda *a, **k: str(shared))):
            w.configs_browse_btn.click()
        data = json.loads((project_config_resolve.user_dir() /
                           "settings.json").read_text(encoding="utf-8"))
        assert data["configs_root"] == str(shared).replace("\\", "/"), data
        assert "teamproj" in _row_pills(w)


def test_pills_and_derived_prefill():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("boundp")
        _make_project("unboundp")
        _make_v1_project("oldp")
        settings_app.bind_project("boundp", dict(_ROOTS))
        w = _window()
        pills = _row_pills(w)
        assert pills["boundp"] == "BOUND", pills
        assert pills["unboundp"] == "UNBOUND", pills
        assert pills["oldp"] == "BOUND", pills
        # Regression: rows must keep a readable height (pre-insert
        # sizeHint smooshed them to style-less micro-rows, 2026-07-18).
        for i in range(w.project_list.count()):
            h = w.project_list.item(i).sizeHint().height()
            assert h >= 32, f"row {i} height {h}"
        _select(w, "oldp")
        # Window itself is never shown offscreen: assert relative visibility.
        assert w.derived_label.isVisibleTo(w)
        assert w.binding_edits["source_art_root"].text() == "C:/Old"
        _select(w, "boundp")
        assert not w.derived_label.isVisibleTo(w)


def test_bind_flow():
    from maya_tools.framework import project_config_resolve
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("demo")
        w = _window()
        _select(w, "demo")
        assert not w.bind_btn.isEnabled()          # untouched: no noisy errors
        w.binding_edits["source_art_root"].setText("D:/Art")
        w.binding_edits["content_root"].setText("D:/Game")
        assert w.bind_btn.isEnabled()
        w.bind_btn.click()
        saved = json.loads((project_config_resolve.user_dir() /
                            "demo.json").read_text(encoding="utf-8"))
        assert saved["content_root"] == "D:/Game", saved
        assert _row_pills(w)["demo"] == "BOUND"


def test_validation_blocks_bind():
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("demo")
        w = _window()
        _select(w, "demo")
        w.binding_edits["source_art_root"].setText("relative/path")
        w.binding_edits["content_root"].setText("D:/Game")
        assert not w.bind_btn.isEnabled()


def test_dirty_switch_notes_discard():
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("aaa")
        _make_project("bbb")
        w = _window()
        _select(w, "aaa")
        notes = []
        w.logger.info = lambda msg: notes.append(msg)
        w.binding_edits["source_art_root"].setText("D:/Art")
        _select(w, "bbb")
        assert any("discarded" in n for n in notes), notes
        assert w.binding_edits["source_art_root"].text() == ""   # bbb is unbound


def test_revert():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("demo")
        settings_app.bind_project("demo", dict(_ROOTS))
        w = _window()
        _select(w, "demo")
        w.binding_edits["source_art_root"].setText("D:/Wrong")
        w.revert_btn.click()
        assert w.binding_edits["source_art_root"].text() == "D:/Art"


def test_integration_toggles():
    from maya_tools.framework import settings_app
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        settings_app.set_integration_pref("load_hotkeys", False)
        w = _window()
        # Checkboxes reflect the persisted prefs.
        assert not w.integration_checks["load_hotkeys"].isChecked()
        assert w.integration_checks["load_menu"].isChecked()

        applied = []
        w._apply_integration_live = lambda key: (applied.append(key), True)[1]
        notes = []
        w.logger.info = lambda msg: notes.append(msg)

        # OFF: persists + restart note, no live attempt.
        w.integration_checks["load_shelf"].setChecked(False)
        assert toolbar_prefs.load_prefs()["load_shelf"] is False
        assert any("next Maya start" in n for n in notes), notes
        assert applied == [], applied

        # ON: persists + live build attempted.
        w.integration_checks["load_hotkeys"].setChecked(True)
        assert toolbar_prefs.load_prefs()["load_hotkeys"] is True
        assert applied == ["load_hotkeys"], applied


def test_show_window_lifecycle():
    from maya_tools.framework import settings_ui
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        with patched(settings_ui, "get_maya_window", lambda: None):
            settings_ui.SettingsUI.show_window()
            first = settings_ui._win
            assert first is not None and first.isVisible()
            settings_ui.SettingsUI.show_window()   # visible match: no rebuild
            assert settings_ui._win is first
            first.close()
            settings_ui.SettingsUI.show_window()   # hidden corpse: swept, rebuilt
            assert settings_ui._win is not first
            settings_ui._win.close()


if __name__ == "__main__":
    check("empty_state", test_empty_state)
    check("pointer_three_states", test_pointer_three_states)
    check("browse_sets_pointer_and_reloads", test_browse_sets_pointer_and_reloads)
    check("pills_and_derived_prefill", test_pills_and_derived_prefill)
    check("bind_flow", test_bind_flow)
    check("validation_blocks_bind", test_validation_blocks_bind)
    check("dirty_switch_notes_discard", test_dirty_switch_notes_discard)
    check("revert", test_revert)
    check("integration_toggles", test_integration_toggles)
    check("show_window_lifecycle", test_show_window_lifecycle)
    print(f"\n{len(FAILURES)} FAILs")
    sys.exit(1 if FAILURES else 0)
