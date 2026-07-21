"""settings_ui offscreen tests. Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/_dev/test_settings_ui.py

Harness follows test_project_setup_ui.py: offscreen QApplication,
MAYA_APP_DIR monkeypatched per test, dialogs patched (no popups)."""
import contextlib
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
def patched(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def _window():
    from maya_tools.framework import settings_ui
    return settings_ui.SettingsUI()


def test_integration_toggles():
    from maya_tools.framework import settings_app
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        settings_app.set_integration_pref("load_hotkeys", False)
        w = _window()
        # Checkboxes reflect the persisted prefs.
        assert not w.integration_checks["load_hotkeys"].isChecked()
        # load_menu defaults OFF on a fresh install (toolbar_prefs.py,
        # 2026-07-20): stale "True" expectation fixed here, unrelated to
        # the Phase 1 bindings removal.
        assert not w.integration_checks["load_menu"].isChecked()

        applied = []
        w._apply_integration_live = lambda key: (applied.append(key), True)[1]
        notes = []
        w.logger.info = lambda msg: notes.append(msg)

        # OFF: persists + restart note, no live attempt. anim_marking_menu
        # defaults ON, so unchecking it is an actual state change (unlike
        # load_shelf/load_menu, which now default OFF on a fresh install
        # per toolbar_prefs.py, 2026-07-20 - setChecked(False) on those
        # would be a no-op and never fire the toggled signal).
        assert w.integration_checks["anim_marking_menu"].isChecked()
        w.integration_checks["anim_marking_menu"].setChecked(False)
        assert toolbar_prefs.load_prefs()["anim_marking_menu"] is False
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
    check("integration_toggles", test_integration_toggles)
    check("show_window_lifecycle", test_show_window_lifecycle)
    print(f"\n{len(FAILURES)} FAILs")
    sys.exit(1 if FAILURES else 0)
