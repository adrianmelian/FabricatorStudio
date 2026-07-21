"""Project setup UI tests (offscreen; no live Maya session). Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/_dev/test_project_setup_ui.py

PYTHONNOUSERSITE=1 is LOAD-BEARING: a user-site numpy 2.x collides with
Maya's bundled numpy inside the shiboken6 import (see test_phase_a.py).

MAYA_APP_DIR is monkeypatched per-test to a temp dir (see maya_app_dir()
below) so nothing here ever touches the real user maya folder — importing
project_setup_ui pulls in project_setup_app -> maya_startup, which seeds
project configs at import time (see test_project_configs.py).

qt_app()/_flush_qt() mirror toolbar/_dev/test_phase_a.py: the suite never
spins an event loop, so every deleteLater stays queued; draining it BETWEEN
checks (gc.collect() first, then DeferredDelete) avoids the exit-127/139
crashes documented there."""
import contextlib
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

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
    finally:
        _flush_qt()


@contextlib.contextmanager
def maya_app_dir(path):
    """Point MAYA_APP_DIR at `path` for the duration; restore on exit."""
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
def patched(obj, name, replacement):
    """Monkeypatch obj.name = replacement for the duration; restore the ORIGINAL
    afterward. Every test in this suite that stubs a Qt static dialog method
    (QInputDialog.getText, QMessageBox.question, QFileDialog.getExistingDirectory)
    or a module-level class method must go through this, never a bare assignment -
    otherwise the stub leaks into whichever test runs next and the suite becomes
    order-dependent."""
    original = getattr(obj, name)
    setattr(obj, name, replacement)
    try:
        yield
    finally:
        setattr(obj, name, original)


_APP = None
_OM_NEUTRALIZED = False


def _neutralize_unsafe_mglobal_display():
    """maya.api.OpenMaya.MGlobal.displayWarning/.displayError SEGFAULT under
    bare `mayapy` with QT_QPA_PLATFORM=offscreen and NO
    maya.standalone.initialize() - verified 2026-07-07: a bare
    `om.MGlobal.displayWarning("x")`, with no Qt involved at all, segfaults
    as the very first OpenMaya call made in the process; a bare
    `om.MGlobal.displayInfo("x")` (called any number of times) does not.
    LoggerWidget.warning()/.error() call displayWarning/displayError;
    .info()/.success() both call displayInfo only (see logger.py) - so only
    the first two are actually unsafe here.

    Branch 1 of test_logger_widget_display_calls_are_offscreen_safe's
    docstring (call `maya.standalone.initialize()` once, before the
    QApplication) was tried and rejected: initializing standalone makes
    something more fundamental crash instead - constructing a bare
    `QWidget()` under QT_QPA_PLATFORM=offscreen AFTER
    maya.standalone.initialize() exits 127, which would break every single
    test in this suite, not just the logger ones. So this codebase takes
    branch 2, applied centrally rather than per-test: `MGlobal` itself is
    an immutable C++-bound type (`om.MGlobal.displayWarning = ...` raises
    `TypeError: cannot set 'displayWarning' attribute of immutable type`),
    so the module-level `om` NAME inside `maya_tools.utils.qt.widgets.logger`
    is replaced, once, with a shim whose displayInfo is the real (proven
    safe) function and whose displayWarning/displayError are no-ops. Every
    LoggerWidget constructed anywhere in this process after this call - in
    every task's tests, not just this smoke check - is protected, since they
    all share the same imported `logger` module."""
    global _OM_NEUTRALIZED
    if _OM_NEUTRALIZED:
        return
    import maya_tools.utils.qt.widgets.logger as logger_mod

    class _SafeMGlobal:
        displayInfo = staticmethod(logger_mod.om.MGlobal.displayInfo)
        displayWarning = staticmethod(lambda *a, **k: None)
        displayError = staticmethod(lambda *a, **k: None)

    class _SafeOM:
        MGlobal = _SafeMGlobal

    logger_mod.om = _SafeOM
    _OM_NEUTRALIZED = True


def qt_app():
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication
        _APP = QApplication.instance() or QApplication([])
    _neutralize_unsafe_mglobal_display()
    return _APP


def _flush_qt():
    if _APP is None:
        return
    import gc
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    gc.collect()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.processEvents()


def test_project_setup_app_imports_alone_offscreen():
    """Smoke check, not a feature test: project_setup_app must be importable on
    its OWN under offscreen mayapy, with nothing else from this UI plan pulled in
    yet. This is the direct test of Plan 6's binding import rule (see the shared
    build contract) - maya_startup and toolbar_app import maya.cmds /
    maya.OpenMayaUI at module top and are NOT offscreen-importable, so
    project_setup_app must import them LAZILY inside apply_and_activate() only,
    never at its own module top. If Plan 6 regresses that rule, this fails here -
    before Task 2 builds a UI module that imports project_setup_app transitively,
    which would otherwise bury the same failure behind a Qt traceback."""
    import maya_tools.framework.project_setup_app  # noqa: F401 - a clean import is the assertion


def test_logger_widget_display_calls_are_offscreen_safe():
    """Smoke check, not a feature test: LoggerWidget.info()/.warning()/.error()/
    .success() call om.MGlobal.display*() at call time (see
    maya_tools/utils/qt/widgets/logger.py) - a real Maya API call, not a Qt-only
    one. Whether that is safe under `mayapy` with QT_QPA_PLATFORM=offscreen and NO
    maya.standalone.initialize() is verified here, once, before any dialog in this
    plan builds a real LoggerWidget - never assumed.

    RESOLVED 2026-07-07 - this codebase needed branch 2, not branch 1: a bare
    `om.MGlobal.displayWarning(...)`/`displayError(...)` (no Qt at all)
    segfaults under bare mayapy with QT_QPA_PLATFORM=offscreen and no
    maya.standalone.initialize() (displayInfo does not). Branch 1
    (initialize standalone once, before the QApplication) was tried and
    rejected here: it makes plain `QWidget()` construction under
    QT_QPA_PLATFORM=offscreen crash instead (exit 127) - strictly worse,
    since it would break every test in this suite, not just the logger
    ones. So `qt_app()` (called at the top of every test) neutralizes
    `logger.om`'s displayWarning/displayError to no-ops centrally, once,
    via `_neutralize_unsafe_mglobal_display()` above - see its docstring for
    the full detail. This check now exercises the REAL LoggerWidget
    (construction, buffer, HTML formatting, filter plumbing) with that
    neutralization already in place via qt_app(), which is exactly what
    every other test in this suite gets too."""
    qt_app()
    from maya_tools.utils.qt.widgets import LoggerWidget
    widget = LoggerWidget()
    widget.info("smoke check: offscreen display call")
    widget.warning("smoke check: offscreen display call")
    widget.error("smoke check: offscreen display call")
    widget.success("smoke check: offscreen display call")


def test_constructs_offscreen_without_maya_session():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [
                {"slug": "unreal5", "name": "Unreal Engine 5", "engine": "unreal5"},
            ]
            dlg = ui.ProjectSetupUI()   # parent=None: must never touch a Maya session
            assert dlg.objectName() == ui.ProjectSetupUI.WINDOW_NAME
            assert dlg.windowTitle() == "Mindmeld"
            assert dlg.project_list.count() == 1
            dlg.close()


def test_show_window_singleton_and_module_global():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            ui.get_maya_window = lambda: None   # no live Maya session under test
            assert ui._win is None
            ui.ProjectSetupUI.show_window()
            first = ui._win
            assert first is not None and first.objectName() == ui.ProjectSetupUI.WINDOW_NAME
            ui.ProjectSetupUI.show_window()      # second call: raises the existing one, no duplicate
            assert ui._win is first
            first.close()                        # X / Esc only hides the dialog
            ui.ProjectSetupUI.show_window()      # reopen after close: sweep hidden leftover, rebuild
            assert ui._win is not first and ui._win.isVisible()
            ui._win.close()


def _fake_project(slug, name, engine="unreal5"):
    return {"slug": slug, "name": name, "engine": engine}


def test_new_loads_engine_template_defaults_into_editor():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            # Engine templates come from project_setup_app's contracted template
            # API, never project_config_io.load_project (that stays reserved for
            # loading an EXISTING saved project on Edit/Duplicate).
            ui.project_setup_app.list_engine_templates = lambda: [
                {"id": "unreal5", "label": "Unreal Engine 5"},
                {"id": "unity", "label": "Unity"},
            ]
            ui.project_setup_app.template_defaults = lambda template_id: {
                "name": f"{template_id} template", "engine": template_id}
            dlg = ui.ProjectSetupUI()
            dlg._on_new()
            assert dlg._editing_slug is None
            assert dlg.pages.currentIndex() == 1
            assert dlg.name_edit.text() == ""          # New starts blank, not the template's name
            assert dlg.engine_combo.isEnabled()
            assert dlg.engine_combo.count() == 2
            dlg.close()


def test_edit_loads_selected_project_and_disables_engine():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [_fake_project("hero", "Hero")]
            ui.project_config_io.load_project = lambda slug: {"name": "Hero", "engine": "unreal5"}
            dlg = ui.ProjectSetupUI()
            dlg.project_list.setCurrentRow(0)
            dlg._on_edit()
            assert dlg._editing_slug == "hero"
            assert dlg.pages.currentIndex() == 1
            assert dlg.name_edit.text() == "Hero"
            assert not dlg.engine_combo.isEnabled()
            dlg.close()


def test_duplicate_calls_app_and_opens_editor_on_the_copy():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            calls = []
            ui.project_setup_app.list_projects = lambda: [_fake_project("hero", "Hero")]
            ui.project_setup_app.duplicate_project = lambda slug, name: calls.append((slug, name)) or Path("/x/hero-2")
            ui.project_config_io.slugify = lambda name: "hero-2"
            ui.project_config_io.load_project = lambda slug: {"name": "Hero (2)", "engine": "unreal5"}
            with patched(ui.QtWidgets.QInputDialog, "getText",
                         staticmethod(lambda *a, **k: ("Hero (2)", True))):
                dlg = ui.ProjectSetupUI()
                dlg.project_list.setCurrentRow(0)
                dlg._on_duplicate()
            assert calls == [("hero", "Hero (2)")]
            assert dlg._editing_slug == "hero-2"
            assert dlg.pages.currentIndex() == 1
            dlg.close()


def test_delete_confirms_then_calls_app_and_refreshes():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            projects = [_fake_project("hero", "Hero")]
            ui.project_setup_app.list_projects = lambda: projects
            deleted = []
            ui.project_setup_app.delete_project = lambda slug: deleted.append(slug) or Path("/trash/hero")
            dlg = ui.ProjectSetupUI()
            dlg.project_list.setCurrentRow(0)
            with patched(ui.QtWidgets.QMessageBox, "question",
                         staticmethod(lambda *a, **k: ui.QtWidgets.QMessageBox.StandardButton.Yes)):
                dlg._on_delete()
            assert deleted == ["hero"]
            dlg.close()


def test_apply_and_activate_calls_app():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [_fake_project("hero", "Hero")]
            applied = []
            ui.project_setup_app.apply_and_activate = lambda slug: applied.append(slug)
            dlg = ui.ProjectSetupUI()
            dlg.project_list.setCurrentRow(0)
            dlg._on_apply_activate()
            assert applied == ["hero"]
            dlg.close()


def test_name_change_updates_slug_preview():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            ui.project_config_io.slugify = lambda name: name.strip().lower().replace(" ", "-")
            dlg = ui.ProjectSetupUI()
            dlg.name_edit.setText("Hero Project")
            assert "hero-project" in dlg.slug_preview_label.text()
            dlg.close()


def test_browse_project_root_sets_readonly_field():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            assert dlg.source_art_root_edit.isReadOnly()
            assert dlg.content_root_edit.isReadOnly()
            with patched(ui.QtWidgets.QFileDialog, "getExistingDirectory",
                         staticmethod(lambda *a, **k: "C:/Demo/Art")):
                dlg._on_browse_source_art_root()
            with patched(ui.QtWidgets.QFileDialog, "getExistingDirectory",
                         staticmethod(lambda *a, **k: "C:/Demo/Game")):
                dlg._on_browse_content_root()
            assert dlg.source_art_root_edit.text() == "C:/Demo/Art"
            assert dlg.content_root_edit.text() == "C:/Demo/Game"
            dlg.close()


def test_collect_authored_identity_and_root_round_trip():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            dlg._populate_editor({"name": "Hero", "engine": "unity"},
                                 {"source_art_root": "C:/Demo/Art",
                                  "content_root": "C:/Demo/Game"})
            got = dlg._collect_authored()
            assert got["name"] == "Hero"
            assert got["engine"] == "unity"
            assert "project_root" not in got               # roots are user-tier
            got_b = dlg._collect_bindings()
            assert got_b["source_art_root"] == "C:/Demo/Art"
            assert got_b["content_root"] == "C:/Demo/Game"
            dlg.close()


def test_session_fields_round_trip():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            authored = {
                "name": "Hero", "engine": "unreal5", "project_root": "C:/Demo",
                "linear_unit": "m", "angular_unit": "rad", "frame_rate": "film",
                "playback_start": 1, "playback_end": 240, "undo_depth": 500,
                "camera_near_clip": 2.5, "camera_far_clip": 50000.0,
                "grid": {"size": 20, "spacing": 4, "divisions": 8},
            }
            dlg._populate_editor(authored)
            got = dlg._collect_authored()
            for key in ("linear_unit", "angular_unit", "frame_rate", "playback_start",
                        "playback_end", "undo_depth", "camera_near_clip",
                        "camera_far_clip", "grid"):
                assert got[key] == authored[key], (key, got[key], authored[key])
            dlg.close()


def test_playback_unchecked_yields_none_pair():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            dlg._populate_editor({"name": "Hero", "engine": "unreal5"})   # no playback keys
            got = dlg._collect_authored()
            assert got["playback_start"] is None and got["playback_end"] is None
            assert got["grid"] == {}
            dlg.close()


ASSET_CLASS_FIXTURE = [
    {"id": "character", "label": "Character",
     "export_types": ["skeletal_mesh", "static_mesh"],
     "source_dir": "SourceArt/Characters/{name}",
     "dest_dir": "Game/Content/Characters/{name}",
     "anim": {"gameplay": "Game/Content/Characters/{name}/Animations", "cinematic": ""}},
    {"id": "environment", "label": "Environment",
     "export_types": ["static_mesh"],
     "source_dir": "SourceArt/Environments/{path}/Source",
     "dest_dir": "Game/Content/Environment/{path}/Models",
     "anim": {"gameplay": "", "cinematic": ""}},
]


def test_asset_classes_round_trip():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            dlg._populate_editor({"name": "Hero", "engine": "unreal5",
                                  "asset_classes": ASSET_CLASS_FIXTURE})
            assert dlg.asset_table.rowCount() == 2
            got = dlg._collect_authored()["asset_classes"]
            assert got == ASSET_CLASS_FIXTURE
            dlg.close()


def test_add_and_remove_asset_class_row():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            dlg._on_add_asset_class()
            assert dlg.asset_table.rowCount() == 1
            dlg.asset_table.setCurrentCell(0, 0)
            dlg._on_remove_asset_class()
            assert dlg.asset_table.rowCount() == 0
            dlg.close()


def test_asset_class_blank_id_row_is_dropped_on_collect():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            dlg._on_add_asset_class()   # a fresh, still-blank-id row
            got = dlg._collect_authored()["asset_classes"]
            assert got == []
            dlg.close()


def test_pose_derive_toggle_computes_path_from_project_root():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            dlg.source_art_root_edit.setText("C:/Demo")
            dlg.pose_derive_cb.setChecked(True)
            assert dlg.pose_root_edit.text() == "C:/Demo/SourceArt/_pose"
            assert dlg.pose_root_edit.isReadOnly()
            dlg.pose_derive_cb.setChecked(False)
            assert not dlg.pose_root_edit.isReadOnly()
            dlg.close()


def test_blueprints_leave_blank_clears_and_disables_browse():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            dlg.blueprints_dir_edit.setText("C:/Demo/Blueprints")
            dlg.blueprints_blank_cb.setChecked(True)
            assert dlg.blueprints_dir_edit.text() == ""
            assert not dlg.blueprints_browse_btn.isEnabled()
            dlg.close()


def test_library_roots_and_blueprints_round_trip():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            authored = {"name": "Hero", "engine": "unreal5",
                        "pose_library_subpath": "SourceArt/_pose",
                        "anim_library_subpath": "SourceArt/_anim",
                        "blueprints_subpath": ""}
            bindings = {"source_art_root": "C:/Demo", "content_root": "C:/DemoGame",
                        "pose_library_root_override": "",
                        "anim_library_root_override": "C:/Elsewhere/Anims",
                        "blueprints_dir_override": "C:/BP"}
            dlg._populate_editor(authored, bindings)
            assert dlg.pose_derive_cb.isChecked()          # no override -> derived
            assert not dlg.anim_derive_cb.isChecked()      # override -> custom path shown
            assert dlg.anim_root_edit.text() == "C:/Elsewhere/Anims"
            got_a = dlg._collect_authored()
            got_b = dlg._collect_bindings()
            assert got_a["pose_library_subpath"] == "SourceArt/_pose"
            assert got_b["pose_library_root_override"] == ""
            assert got_b["anim_library_root_override"] == "C:/Elsewhere/Anims"
            assert got_b["blueprints_dir_override"] == "C:/BP"
            dlg.close()


FIXTURE_AUTHORED = {
    "name": "Hero Project",
    "engine": "unreal5",
    "linear_unit": "cm",
    "angular_unit": "deg",
    "frame_rate": "ntsc",
    "playback_start": 1,
    "playback_end": 120,
    "undo_depth": 200,
    "camera_near_clip": 1.0,
    "camera_far_clip": 100000.0,
    "grid": {"size": 12, "spacing": 5, "divisions": 5},
    "blueprints_subpath": "",
    "asset_classes": [
        {"id": "character", "label": "Character",
         "export_types": ["skeletal_mesh", "static_mesh"],
         "source_dir": "SourceArt/Characters/{name}",
         "dest_dir": "Game/Content/Characters/{name}",
         "anim": {"gameplay": "Game/Content/Characters/{name}/Animations", "cinematic": ""}},
    ],
    "prefixes": {"static_mesh": "SM_", "skeletal_mesh": "SK_"},
    "anim_prefix": "A_",
    "pose_library_subpath": "SourceArt/_pose",
    "anim_library_subpath": "SourceArt/_anim",
    "mesh_group_name": "geo_grp",
    "engine_up_axis": "z",
    "orient_convention": "standard",
    "fbx_presets": {"static_mesh": {"FBXExportUpAxis": "z"},
                    "skeletal_mesh": {"FBXExportUpAxis": "z"},
                    "animation": {"FBXExportUpAxis": "z"}},
}

FIXTURE_BINDINGS = {
    "source_art_root": "C:/Demo",
    "content_root": "C:/DemoGame",
    "pose_library_root_override": "",
    "anim_library_root_override": "C:/Elsewhere/Anims",
    "blueprints_dir_override": "",
}


def test_full_authored_round_trip():
    """Two-tier round trip: populate from (authored, bindings), collect
    BOTH back full-dict equal - the UI-layer key-set net (the
    mesh_group_name / engine_up_axis wipe lesson)."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            dlg._populate_editor(FIXTURE_AUTHORED, FIXTURE_BINDINGS)
            got = dlg._collect_authored()
            assert got == FIXTURE_AUTHORED, (got, FIXTURE_AUTHORED)
            got_b = dlg._collect_bindings()
            assert got_b == FIXTURE_BINDINGS, (got_b, FIXTURE_BINDINGS)
            dlg.close()


def test_mirror_convention_round_trip_and_edit_gate():
    """t48 Phase D: the picker round-trips through authored, defaults
    to unreal when absent, changes silently in NEW-project mode, and in
    EDIT mode pops the mixed-convention warning — No reverts, Yes
    keeps. (The warning fires on change, not save: the commitment point
    Adrian chose 2026-07-19.)"""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            # absent key -> unreal
            dlg._populate_editor({"name": "Hero", "engine": "unreal5"})
            assert dlg._collect_authored()["orient_convention"] == "unreal"
            # authored standard round-trips
            dlg._populate_editor({"name": "Hero", "engine": "unreal5",
                                  "orient_convention": "standard"})
            assert dlg.mirror_convention_combo.currentData() == "standard"
            assert dlg._collect_authored()["orient_convention"] == "standard"
            # NEW-project mode: switching never pops the dialog
            assert dlg._editing_slug is None
            def _boom(*_a, **_k):
                raise AssertionError("warning dialog fired in NEW mode")
            with patched(ui.QtWidgets.QMessageBox, "warning",
                         staticmethod(_boom)):
                dlg.mirror_convention_combo.setCurrentIndex(
                    dlg.mirror_convention_combo.findData("unreal"))
            assert dlg._collect_authored()["orient_convention"] == "unreal"
            # EDIT mode, No -> reverts to the loaded value
            dlg._populate_editor({"name": "Hero", "engine": "unreal5",
                                  "orient_convention": "unreal"})
            dlg._editing_slug = "hero"
            with patched(ui.QtWidgets.QMessageBox, "warning",
                         staticmethod(lambda *a, **k:
                                      ui.QtWidgets.QMessageBox.StandardButton.No)):
                dlg.mirror_convention_combo.setCurrentIndex(
                    dlg.mirror_convention_combo.findData("standard"))
            assert dlg.mirror_convention_combo.currentData() == "unreal"
            # EDIT mode, Yes -> the change sticks
            with patched(ui.QtWidgets.QMessageBox, "warning",
                         staticmethod(lambda *a, **k:
                                      ui.QtWidgets.QMessageBox.StandardButton.Yes)):
                dlg.mirror_convention_combo.setCurrentIndex(
                    dlg.mirror_convention_combo.findData("standard"))
            assert dlg._collect_authored()["orient_convention"] == "standard"
            dlg.close()


def test_rebinding_roots_does_not_fossilize_blueprints_preview():
    """Re-binding the Source Art Root must re-derive the blueprints
    preview - not fossilize the OLD root's preview text into a machine
    override the user never authored (review HIGH, 2026-07-18)."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            authored = dict(FIXTURE_AUTHORED)
            authored["blueprints_subpath"] = "SourceArt/Blueprints"
            bindings = dict(FIXTURE_BINDINGS)
            dlg._populate_editor(authored, bindings)
            assert dlg.blueprints_dir_edit.text() == "C:/Demo/SourceArt/Blueprints"
            dlg.source_art_root_edit.setText("D:/NewArt")     # moved-tree re-bind
            assert dlg.blueprints_dir_edit.text() == "D:/NewArt/SourceArt/Blueprints"
            got_a = dlg._collect_authored()
            got_b = dlg._collect_bindings()
            assert got_a["blueprints_subpath"] == "SourceArt/Blueprints"
            assert got_b["blueprints_dir_override"] == ""     # NO fossil override
            dlg.close()


def test_template_switch_preserves_custom_library_rows():
    """Switching engine templates mid-New keeps the user's library picks
    (under-root and off-root alike) - a template switch changes session
    defaults and classes, never where this user keeps libraries."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            templates = [{"id": "unreal5", "label": "Unreal 5"},
                         {"id": "unity", "label": "Unity"}]
            ui.project_setup_app.list_engine_templates = lambda: templates
            ui.project_setup_app.template_defaults = lambda tid: {
                "engine": tid,
                "pose_library_subpath": f"{tid}_pose",
                "anim_library_subpath": f"{tid}_anim",
                "engine_up_axis": "z" if tid == "unreal5" else ""}
            dlg = ui.ProjectSetupUI()
            dlg._on_new()
            dlg.source_art_root_edit.setText("C:/Art")
            dlg.content_root_edit.setText("C:/Game")
            dlg.pose_derive_cb.setChecked(False)
            dlg.pose_root_edit.setText("C:/Art/CustomPoses")  # under-root custom
            dlg.engine_combo.setCurrentIndex(1)               # switch template
            got_a = dlg._collect_authored()
            got_b = dlg._collect_bindings()
            assert got_a["pose_library_subpath"] == "CustomPoses"   # pick survived
            assert got_b["pose_library_root_override"] == ""
            assert got_a["engine_up_axis"] == ""              # axis DID follow template
            dlg.close()


def test_populate_keeps_legitimately_blank_subpaths():
    """A blank authored subpath (exporter-fallback semantics) must survive
    Edit -> Save untouched - never coerced to the default, which would
    change TEAMMATES' resolution (recovered unverified finding)."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            authored = dict(FIXTURE_AUTHORED)
            authored["anim_library_subpath"] = ""             # legit blank
            bindings = dict(FIXTURE_BINDINGS)                 # anim has an override
            dlg._populate_editor(authored, bindings)
            got = dlg._collect_authored()
            assert got["anim_library_subpath"] == ""          # not coerced
            dlg.close()


def test_populate_ends_with_a_clean_final_revalidate():
    """The panel after populate must reflect the FULLY-populated form -
    not whichever mid-populate widget signal fired last (Adrian's live
    legacy-config repro, 2026-07-18: four phantom errors + Save wrongly
    disabled on a config whose only real gap was asset_classes)."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            # Fully valid two-tier populate: Save must be enabled at once.
            dlg._editing_slug = "hero_project"
            dlg._populate_editor(FIXTURE_AUTHORED, FIXTURE_BINDINGS)
            assert dlg.save_btn.isEnabled()
            # Legacy shape (no classes, all else present): exactly ONE error.
            legacy = dict(FIXTURE_AUTHORED)
            legacy["asset_classes"] = []
            dlg._populate_editor(legacy, FIXTURE_BINDINGS)
            assert not dlg.save_btn.isEnabled()
            dlg.close()


def test_editor_prompts_unbound():
    """Editing a project with no machine bindings: root fields blank,
    save disabled, and the validation panel names source_art_root."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            dlg._populate_editor(dict(FIXTURE_AUTHORED), {})
            assert dlg.source_art_root_edit.text() == ""
            dlg._revalidate()
            assert not dlg.save_btn.isEnabled()
            dlg.close()


def test_fbx_presets_shown_read_only():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            dlg._populate_editor(FIXTURE_AUTHORED)
            assert dlg.fbx_preview.isReadOnly()
            assert "FBXExportUpAxis" in dlg.fbx_preview.toPlainText()
            dlg.close()


def test_revalidate_disables_save_on_error_issue():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            Issue = ui.config_validation.Issue
            ui.config_validation.validate_config = lambda authored, existing_projects=None: [
                Issue("error", "project_root", "project_root is required"),
            ]
            dlg = ui.ProjectSetupUI()
            dlg._populate_editor({"name": "Hero", "engine": "unreal5"})
            dlg._revalidate()
            assert not dlg.save_btn.isEnabled()
            dlg.close()


def test_revalidate_enables_save_when_only_warnings():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            Issue = ui.config_validation.Issue
            ui.config_validation.validate_config = lambda authored, existing_projects=None: [
                Issue("warning", "blueprints_dir", "left blank: factory blueprints only"),
            ]
            dlg = ui.ProjectSetupUI()
            dlg._populate_editor({"name": "Hero", "engine": "unreal5"},
                                 {"source_art_root": "C:/Demo",
                                  "content_root": "C:/DemoGame"})
            dlg._revalidate()
            assert dlg.save_btn.isEnabled()
            dlg.close()


def test_existing_projects_excludes_the_slug_being_edited():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [
                {"slug": "hero", "name": "Hero", "engine": "unreal5"},
                {"slug": "sidekick", "name": "Sidekick", "engine": "unreal5"},
            ]
            ui.project_config_io.load_project = lambda slug: {"name": slug, "engine": "unreal5"}
            dlg = ui.ProjectSetupUI()
            dlg._editing_slug = "hero"
            existing = dlg._existing_authored_excluding_current()
            assert [e["name"] for e in existing] == ["sidekick"]
            dlg.close()


def test_configs_panel_present():
    """Phase 1b: the Shared Project Configs pointer moved from Settings into
    Mindmeld - present as an editable panel (read-only line edit + Browse +
    Use Local Default + status/helper), driven by settings_app. (Positioned
    below the project list, above VALIDATION, since 2026-07-20.)"""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            assert dlg.configs_root_edit.isReadOnly()
            assert "LOCAL DEFAULT" in dlg.configs_status_label.text()
            assert not dlg.configs_local_btn.isEnabled()
            helper_texts = [lbl.text() for lbl in dlg.findChildren(ui.QtWidgets.QLabel)
                            if lbl.property("mindmeld") == "helper"]
            assert ("Where this machine reads project definitions from. Set "
                    "once; teams point this at a shared location.") in helper_texts, \
                helper_texts
            dlg.close()


def test_configs_browse_sets_pointer_and_repopulates_project_list():
    """Browse persists the pointer via settings_app.set_configs_root and
    re-populate()s so the project list re-reads the new configs root."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            shared = Path(td) / "shared"
            shared.mkdir()
            calls = {"n": 0}
            real_list_projects = ui.project_setup_app.list_projects

            def _list_projects():
                calls["n"] += 1
                if calls["n"] == 1:
                    return []
                return [_fake_project("teamproj", "Team Project")]
            ui.project_setup_app.list_projects = _list_projects
            dlg = ui.ProjectSetupUI()
            assert dlg.project_list.count() == 0
            with patched(ui.QtWidgets.QFileDialog, "getExistingDirectory",
                         staticmethod(lambda *a, **k: str(shared))):
                dlg.configs_browse_btn.click()
            data = json.loads((ui.project_config_resolve.user_dir() /
                               "settings.json").read_text(encoding="utf-8"))
            assert data["configs_root"] == str(shared).replace("\\", "/"), data
            assert dlg.configs_root_edit.text() == str(shared).replace("\\", "/")
            assert dlg.project_list.count() == 1   # repopulated from the new root
            ui.project_setup_app.list_projects = real_list_projects
            dlg.close()


def test_configs_use_local_default_clears_pointer():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            shared = Path(td) / "shared"
            shared.mkdir()
            ui.settings_app.set_configs_root(str(shared))
            dlg = ui.ProjectSetupUI()
            assert "POINTER" in dlg.configs_status_label.text()
            dlg.configs_local_btn.click()
            assert "LOCAL DEFAULT" in dlg.configs_status_label.text()
            dlg.close()


def test_configs_env_override_disables_browse():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            shared_dir = str(Path(td) / "shared")
            os.environ["FABRICATOR_PROJECT_CONFIGS"] = shared_dir
            try:
                dlg = ui.ProjectSetupUI()
                assert "ENV VAR" in dlg.configs_status_label.text()
                assert not dlg.configs_browse_btn.isEnabled()
                assert not dlg.configs_local_btn.isEnabled()
            finally:
                os.environ.pop("FABRICATOR_PROJECT_CONFIGS", None)
            dlg.close()


def test_new_leaves_save_disabled_until_fields_are_filled():
    # End-to-end lock-in: _on_new() clears name_edit, which fires
    # _on_name_changed -> _on_field_changed -> _revalidate() (wired above);
    # on a still-blank New page, _revalidate()'s early-exit branch disables
    # Save without running config_validation at all (no noisy issue spam on
    # a page the user hasn't touched yet).
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            dlg._on_new()
            assert not dlg.save_btn.isEnabled()
            dlg.close()


def test_save_new_calls_create_project_and_returns_to_list():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            ui.config_validation.validate_config = lambda authored, existing_projects=None: []
            created = []
            ui.project_setup_app.create_project = lambda authored, bindings: created.append((authored, bindings)) or Path("/configs/hero")
            ui.project_scaffold.candidate_dirs = lambda resolved: []   # no scaffold offer this test
            dlg = ui.ProjectSetupUI()
            dlg._on_new()
            dlg.name_edit.setText("Hero")
            dlg.source_art_root_edit.setText("C:/Demo/Art")
            dlg.content_root_edit.setText("C:/Demo/Game")
            dlg._on_save()
            assert created and created[0][0]["name"] == "Hero"
            assert created[0][1]["source_art_root"] == "C:/Demo/Art"   # both tiers passed
            assert dlg.pages.currentIndex() == 0
            assert dlg._editing_slug is None
            dlg.close()


def test_save_edit_calls_edit_project_with_slug():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [{"slug": "hero", "name": "Hero", "engine": "unreal5"}]
            ui.project_config_io.load_project = lambda slug: {"name": "Hero", "engine": "unreal5"}
            ui.config_validation.validate_config = lambda authored, existing_projects=None: []
            edited = []
            ui.project_setup_app.edit_project = lambda slug, authored, bindings: edited.append((slug, authored, bindings)) or Path("/configs/hero")
            ui.project_scaffold.candidate_dirs = lambda resolved: []
            # project_config_resolve is never reloaded by ui's own reload block
            # (only project_setup_app/settings_app are), so a bare assignment here
            # would leak this stub into EVERY later test's real get_bindings/
            # load_bindings calls for the rest of the process - go through
            # patched() (see its docstring) so it is restored on exit.
            with patched(ui.project_config_resolve, "load_bindings", lambda slug: {
                    "source_art_root": "C:/Demo/Art", "content_root": "C:/Demo/Game",
                    "pose_library_root_override": "", "anim_library_root_override": "",
                    "blueprints_dir_override": ""}):
                dlg = ui.ProjectSetupUI()
                dlg.project_list.setCurrentRow(0)
                dlg._on_edit()
                dlg._on_save()
                assert edited and edited[0][0] == "hero"
                dlg.close()


def test_save_blocked_and_issues_shown_on_error():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            Issue = ui.config_validation.Issue
            ui.config_validation.validate_config = lambda authored, existing_projects=None: [
                Issue("error", "project_root", "project_root is required"),
            ]
            called = []
            ui.project_setup_app.create_project = lambda authored, bindings: called.append(authored)
            dlg = ui.ProjectSetupUI()
            dlg._on_new()
            dlg._on_save()
            assert called == []                    # create_project never invoked
            assert dlg.pages.currentIndex() == 1    # stays on the editor page
            dlg.close()


def test_save_offers_scaffold_checklist_and_creates_checked_dirs():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            ui.config_validation.validate_config = lambda authored, existing_projects=None: []
            ui.project_setup_app.create_project = lambda authored, bindings: Path("/configs/hero")
            ui.project_scaffold.candidate_dirs = lambda resolved: [
                ("character_source", "Character Source Art", "C:/Demo/SourceArt/Characters")]
            scaffolded = []
            ui.project_setup_app.scaffold = lambda slug, keys: scaffolded.append((slug, keys)) or [Path("C:/Demo/SourceArt/Characters")]
            dlg = ui.ProjectSetupUI()
            dlg._on_new()
            dlg.name_edit.setText("Hero")
            dlg.source_art_root_edit.setText("C:/Demo/Art")
            dlg.content_root_edit.setText("C:/Demo/Game")
            with patched(ui._ScaffoldChecklistDialog, "exec",
                         lambda self: ui.QtWidgets.QDialog.DialogCode.Accepted):
                dlg._on_save()
            assert scaffolded == [("hero", ["character_source"])]
            dlg.close()


def test_selecting_project_populates_editable_bindings_view():
    """Phase 3: selecting a row loads that project's bindings, EDITABLE,
    on the right via settings_app.get_bindings."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [
                _fake_project("hero", "Hero"), _fake_project("sidekick", "Sidekick")]
            fixture = {
                "hero": {"source_art_root": "C:/Art/Hero", "content_root": "C:/Game/Hero",
                        "pose_library_root_override": "C:/Art/Hero/Poses",
                        "anim_library_root_override": "", "blueprints_dir_override": ""},
                "sidekick": {},
            }
            ui.settings_app.get_bindings = lambda slug: fixture[slug]
            dlg = ui.ProjectSetupUI()
            for edit in (dlg.bindings_source_edit, dlg.bindings_content_edit,
                        dlg.bindings_pose_override_edit, dlg.bindings_anim_override_edit,
                        dlg.bindings_blueprints_override_edit):
                assert not edit.isReadOnly()
            dlg.project_list.setCurrentRow(0)
            assert dlg.bindings_source_edit.text() == "C:/Art/Hero"
            assert dlg.bindings_content_edit.text() == "C:/Game/Hero"
            assert dlg.bindings_pose_override_edit.text() == "C:/Art/Hero/Poses"
            assert not dlg._bindings_dirty            # a fresh load is never dirty
            dlg.project_list.setCurrentRow(1)
            assert dlg.bindings_source_edit.text() == ""
            assert dlg.bindings_content_edit.text() == ""
            dlg.close()


def test_bindings_browse_sets_field_and_marks_dirty():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [_fake_project("hero", "Hero")]
            ui.settings_app.get_bindings = lambda slug: {}
            dlg = ui.ProjectSetupUI()
            dlg.project_list.setCurrentRow(0)
            with patched(ui.QtWidgets.QFileDialog, "getExistingDirectory",
                         staticmethod(lambda *a, **k: "C:/Demo/Art")):
                dlg.bindings_source_browse_btn.click()
            assert dlg.bindings_source_edit.text() == "C:/Demo/Art"
            assert dlg._bindings_dirty
            dlg.close()


def test_bindings_validation_disables_and_enables_bind():
    """Inline validation: untouched+empty = no noisy errors (Bind stays
    disabled quietly); an invalid entry disables Bind with a visible
    error; a valid pair of roots enables it."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [_fake_project("hero", "Hero")]
            ui.settings_app.get_bindings = lambda slug: {}
            dlg = ui.ProjectSetupUI()
            dlg.project_list.setCurrentRow(0)
            assert not dlg.bindings_bind_btn.isEnabled()
            assert not dlg.validation_logger._buffer          # no noise on a fresh unbound select

            dlg.bindings_content_edit.setText("relative/not/absolute")
            assert not dlg.bindings_bind_btn.isEnabled()
            assert any(lvl == "ERROR" for _, lvl, _ in dlg.validation_logger._buffer)

            dlg.bindings_source_edit.setText("C:/Art/Hero")
            dlg.bindings_content_edit.setText("C:/Game/Hero")
            assert dlg.bindings_bind_btn.isEnabled()
            dlg.close()


def test_bindings_revert_reloads_persisted_and_clears_dirty():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [_fake_project("hero", "Hero")]
            ui.settings_app.get_bindings = lambda slug: {
                "source_art_root": "C:/Art/Hero", "content_root": "C:/Game/Hero",
                "pose_library_root_override": "", "anim_library_root_override": "",
                "blueprints_dir_override": ""}
            dlg = ui.ProjectSetupUI()
            dlg.project_list.setCurrentRow(0)
            dlg.bindings_source_edit.setText("C:/Somewhere/Else")
            assert dlg._bindings_dirty
            dlg._on_bindings_revert()
            assert dlg.bindings_source_edit.text() == "C:/Art/Hero"
            assert not dlg._bindings_dirty
            dlg.close()


def test_switching_selection_with_dirty_edits_logs_discard_note():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [
                _fake_project("hero", "Hero"), _fake_project("sidekick", "Sidekick")]
            ui.settings_app.get_bindings = lambda slug: {}
            dlg = ui.ProjectSetupUI()
            dlg.project_list.setCurrentRow(0)
            dlg.bindings_source_edit.setText("C:/Unsaved")
            assert dlg._bindings_dirty
            dlg.project_list.setCurrentRow(1)
            messages = [msg for _, _, msg in dlg.logger._buffer]
            assert any("hero" in m and "discard" in m.lower() for m in messages), messages
            assert not dlg._bindings_dirty         # the new (sidekick) load is clean
            dlg.close()


def test_bind_recomputes_row_and_pill_status_and_keeps_selection():
    """The app-facade-mocked wiring test: Bind calls settings_app.bind_project,
    then the row pill and the right-pane status pill both flip live, and the
    selected project stays selected."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [_fake_project("hero", "Hero")]
            state = {"bound": False}

            def _get_bindings(slug):
                if state["bound"]:
                    return {"source_art_root": "C:/Art", "content_root": "C:/Game",
                            "pose_library_root_override": "",
                            "anim_library_root_override": "",
                            "blueprints_dir_override": ""}
                return {}

            def _binding_status(slug):
                return ({"state": "green", "reason": ""} if state["bound"]
                        else {"state": "orange", "reason": "unbound"})

            bound_calls = []

            def _bind_project(slug, bindings):
                bound_calls.append((slug, bindings))
                state["bound"] = True
                return Path("/x")

            ui.settings_app.get_bindings = _get_bindings
            ui.settings_app.binding_status = _binding_status
            ui.settings_app.bind_project = _bind_project
            dlg = ui.ProjectSetupUI()
            dlg.project_list.setCurrentRow(0)
            assert dlg.bindings_status_pill.text() == "UNBOUND"
            dlg.bindings_source_edit.setText("C:/Art")
            dlg.bindings_content_edit.setText("C:/Game")
            dlg._on_bindings_bind()
            assert bound_calls and bound_calls[0][0] == "hero"
            assert bound_calls[0][1]["source_art_root"] == "C:/Art"
            assert dlg.bindings_status_pill.text() == "BOUND"
            assert dlg._selected_slug() == "hero"          # selection kept
            item = dlg.project_list.item(0)
            assert item.foreground().color().name() == ui.QtGui.QColor(
                ui.mindmeld_style.PLASMA).name()
            assert "BOUND" in item.text() and "UNBOUND" not in item.text()
            dlg.close()


def test_bind_writes_local_bindings_and_leaves_shared_pair_untouched():
    """Real end-to-end persistence (no settings_app mocking): editing the
    right-pane roots and clicking Bind writes _user/<slug>.json, and the
    project's session.json (the shared pair) is byte-for-byte and
    mtime-for-mtime untouched across the Bind - bind_project only ever
    opens project_config_resolve.save_bindings, never save_project."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_config_io.save_project(
                {"name": "Hero", "engine": "unreal5"}, overwrite=False)
            session_path = (ui.project_config_io.project_config_paths.configs_dir()
                            / "hero" / "session.json")
            before_bytes = session_path.read_bytes()
            before_mtime = session_path.stat().st_mtime_ns

            dlg = ui.ProjectSetupUI()
            dlg.project_list.setCurrentRow(0)
            with tempfile.TemporaryDirectory() as art, tempfile.TemporaryDirectory() as game:
                dlg.bindings_source_edit.setText(art.replace("\\", "/"))
                dlg.bindings_content_edit.setText(game.replace("\\", "/"))
                assert dlg.bindings_bind_btn.isEnabled()
                dlg._on_bindings_bind()
                got = ui.settings_app.get_bindings("hero")
                assert got["source_art_root"] == art.replace("\\", "/")
                assert got["content_root"] == game.replace("\\", "/")
            dlg.close()

            after_bytes = session_path.read_bytes()
            after_mtime = session_path.stat().st_mtime_ns
            assert after_bytes == before_bytes
            assert after_mtime == before_mtime


def test_bind_succeeds_when_shared_configs_root_is_read_only():
    """Read-only-configs regression: simulate a read-only shared configs
    root (any attempt to write the shared pair raises) and confirm Bind
    still succeeds - proving the right pane's Bind path never opens
    project_config_io.save_project / project_setup_app.create_project /
    edit_project, only the local-only settings_app.bind_project."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_config_io.save_project(
                {"name": "Hero", "engine": "unreal5"}, overwrite=False)

            def _boom(*_a, **_k):
                raise ui.project_config_io.ReadOnlyConfigError(
                    "simulated read-only shared configs root")

            with patched(ui.project_config_io, "save_project", _boom), \
                 patched(ui.project_setup_app, "create_project", _boom), \
                 patched(ui.project_setup_app, "edit_project", _boom):
                dlg = ui.ProjectSetupUI()
                dlg.project_list.setCurrentRow(0)
                with tempfile.TemporaryDirectory() as art, tempfile.TemporaryDirectory() as game:
                    art_slash = art.replace("\\", "/")
                    game_slash = game.replace("\\", "/")
                    dlg.bindings_source_edit.setText(art_slash)
                    dlg.bindings_content_edit.setText(game_slash)
                    dlg._on_bindings_bind()
                got = ui.settings_app.get_bindings("hero")
                assert got["source_art_root"] == art_slash
                assert got["content_root"] == game_slash
                dlg.close()


def test_row_status_colors_green_for_resolving_roots_orange_otherwise():
    """Phase 2 (re-verified after the plain-text-row port): project_list
    rows carry status via foreground color - green (mindmeld_style.PLASMA,
    the same hex the pill-ok text uses) when both roots resolve on disk,
    orange (mindmeld_style.EMBER, same hex as pill-warn) otherwise (unbound
    or moved) - plus a plain-text BOUND/UNBOUND token so the row reads
    without relying on color alone."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            with tempfile.TemporaryDirectory() as art, tempfile.TemporaryDirectory() as game:
                statuses = {
                    "bound_p": {"state": "green", "reason": ""},
                    "unbound_p": {"state": "orange", "reason": "unbound"},
                }
                ui.project_setup_app.list_projects = lambda: [
                    _fake_project("bound_p", "Bound"), _fake_project("unbound_p", "Unbound")]
                ui.settings_app.binding_status = lambda slug: statuses[slug]
                dlg = ui.ProjectSetupUI()
                rows = {}
                for row in range(dlg.project_list.count()):
                    item = dlg.project_list.item(row)
                    rows[item.data(ui.QtCore.Qt.ItemDataRole.UserRole)] = item
                green = ui.QtGui.QColor(ui.mindmeld_style.PLASMA).name()
                orange = ui.QtGui.QColor(ui.mindmeld_style.EMBER).name()
                assert rows["bound_p"].foreground().color().name() == green
                assert "BOUND" in rows["bound_p"].text()
                assert "UNBOUND" not in rows["bound_p"].text()
                assert rows["unbound_p"].foreground().color().name() == orange
                assert "UNBOUND" in rows["unbound_p"].text()
                dlg.close()


def test_empty_selection_shows_placeholder_right_pane():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            assert dlg.project_list.currentItem() is None
            assert dlg.bindings_source_edit.text() == ""
            assert dlg.bindings_status_pill.property("mindmeld") == "pill-idle"
            dlg.close()


def test_derived_bindings_show_helper_label_and_prefill_without_persisting():
    """Phase 4: a v1-derived project (no persisted _user/<slug>.json, but
    derive_v1_bindings yields roots) pre-fills the right pane AND shows the
    derived helper label. The pre-fill is a suggestion, never auto-committed -
    selecting the row must not write _user/<slug>.json."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [_fake_project("legacy", "Legacy")]
            derived = {"source_art_root": "C:/V1/Art", "content_root": "C:/V1/Art",
                       "pose_library_root_override": "", "anim_library_root_override": "",
                       "blueprints_dir_override": ""}
            with patched(ui.project_config_resolve, "load_bindings", lambda slug: None), \
                 patched(ui.project_config_io, "derive_v1_bindings", lambda slug: dict(derived)):
                dlg = ui.ProjectSetupUI()
                dlg.project_list.setCurrentRow(0)
                assert dlg.bindings_source_edit.text() == "C:/V1/Art"
                assert dlg.bindings_content_edit.text() == "C:/V1/Art"
                # dlg is never .show()'d in this suite, so QWidget.isVisible()
                # (which also reflects the top-level's own on-screen state)
                # would read False regardless of setVisible(); isVisibleTo()
                # checks only the widget's own flag and intermediate
                # ancestors, ignoring dlg's un-shown top-level state.
                assert dlg.bindings_derived_label.isVisibleTo(dlg)
                assert "Derived from the v1 config" in dlg.bindings_derived_label.text()
                user_dir = ui.project_config_resolve.user_dir()
                assert not (user_dir / "legacy.json").exists(), \
                    "selecting a derived project must never write persisted bindings"
                dlg.close()


def test_persisted_bindings_hide_the_derived_label():
    """A project with real persisted bindings never shows the derived
    affordance, even though derive_v1_bindings would also happily produce
    something for it (persisted always wins)."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [_fake_project("hero", "Hero")]
            persisted = {"source_art_root": "C:/Real/Art", "content_root": "C:/Real/Game",
                        "pose_library_root_override": "", "anim_library_root_override": "",
                        "blueprints_dir_override": ""}
            with patched(ui.project_config_resolve, "load_bindings", lambda slug: dict(persisted)), \
                 patched(ui.project_config_io, "derive_v1_bindings",
                         lambda slug: {"source_art_root": "C:/Ignored"}):
                dlg = ui.ProjectSetupUI()
                dlg.project_list.setCurrentRow(0)
                assert dlg.bindings_source_edit.text() == "C:/Real/Art"
                assert not dlg.bindings_derived_label.isVisibleTo(dlg)
                dlg.close()


def test_empty_project_list_shows_configs_pointer_affordance():
    """Phase 4: an empty project list points the user at the SHARED PROJECT
    CONFIGS pointer already at the top of this same window (Phase 1b moved
    it here - no 'Change in Settings...' hop)."""
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: []
            dlg = ui.ProjectSetupUI()
            assert dlg.empty_list_label.isVisibleTo(dlg)
            assert "Shared Project Configs" in dlg.empty_list_label.text()
            dlg.close()


def test_nonempty_project_list_hides_the_empty_list_affordance():
    qt_app()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_ui as ui
            importlib.reload(ui)
            ui.project_setup_app.list_projects = lambda: [_fake_project("hero", "Hero")]
            dlg = ui.ProjectSetupUI()
            assert not dlg.empty_list_label.isVisibleTo(dlg)
            dlg.close()


def run():
    check("project_setup_app_imports_alone_offscreen", test_project_setup_app_imports_alone_offscreen)
    check("logger_widget_display_calls_are_offscreen_safe", test_logger_widget_display_calls_are_offscreen_safe)
    check("constructs_offscreen_without_maya_session", test_constructs_offscreen_without_maya_session)
    check("show_window_singleton_and_module_global", test_show_window_singleton_and_module_global)
    check("new_loads_engine_template_defaults_into_editor", test_new_loads_engine_template_defaults_into_editor)
    check("edit_loads_selected_project_and_disables_engine", test_edit_loads_selected_project_and_disables_engine)
    check("duplicate_calls_app_and_opens_editor_on_the_copy", test_duplicate_calls_app_and_opens_editor_on_the_copy)
    check("delete_confirms_then_calls_app_and_refreshes", test_delete_confirms_then_calls_app_and_refreshes)
    check("apply_and_activate_calls_app", test_apply_and_activate_calls_app)
    check("name_change_updates_slug_preview", test_name_change_updates_slug_preview)
    check("browse_project_root_sets_readonly_field", test_browse_project_root_sets_readonly_field)
    check("collect_authored_identity_and_root_round_trip", test_collect_authored_identity_and_root_round_trip)
    check("session_fields_round_trip", test_session_fields_round_trip)
    check("playback_unchecked_yields_none_pair", test_playback_unchecked_yields_none_pair)
    check("asset_classes_round_trip", test_asset_classes_round_trip)
    check("add_and_remove_asset_class_row", test_add_and_remove_asset_class_row)
    check("asset_class_blank_id_row_is_dropped_on_collect", test_asset_class_blank_id_row_is_dropped_on_collect)
    check("pose_derive_toggle_computes_path_from_project_root", test_pose_derive_toggle_computes_path_from_project_root)
    check("blueprints_leave_blank_clears_and_disables_browse", test_blueprints_leave_blank_clears_and_disables_browse)
    check("library_roots_and_blueprints_round_trip", test_library_roots_and_blueprints_round_trip)
    check("full_authored_round_trip", test_full_authored_round_trip)
    check("mirror_convention_round_trip_and_edit_gate",
          test_mirror_convention_round_trip_and_edit_gate)
    check("editor_prompts_unbound", test_editor_prompts_unbound)
    check("populate_ends_with_a_clean_final_revalidate", test_populate_ends_with_a_clean_final_revalidate)
    check("rebinding_roots_does_not_fossilize_blueprints_preview", test_rebinding_roots_does_not_fossilize_blueprints_preview)
    check("template_switch_preserves_custom_library_rows", test_template_switch_preserves_custom_library_rows)
    check("populate_keeps_legitimately_blank_subpaths", test_populate_keeps_legitimately_blank_subpaths)
    check("fbx_presets_shown_read_only", test_fbx_presets_shown_read_only)
    check("revalidate_disables_save_on_error_issue", test_revalidate_disables_save_on_error_issue)
    check("revalidate_enables_save_when_only_warnings", test_revalidate_enables_save_when_only_warnings)
    check("existing_projects_excludes_the_slug_being_edited", test_existing_projects_excludes_the_slug_being_edited)
    check("configs_panel_present", test_configs_panel_present)
    check("configs_browse_sets_pointer_and_repopulates_project_list",
          test_configs_browse_sets_pointer_and_repopulates_project_list)
    check("configs_use_local_default_clears_pointer", test_configs_use_local_default_clears_pointer)
    check("configs_env_override_disables_browse", test_configs_env_override_disables_browse)
    check("new_leaves_save_disabled_until_fields_are_filled", test_new_leaves_save_disabled_until_fields_are_filled)
    check("save_new_calls_create_project_and_returns_to_list", test_save_new_calls_create_project_and_returns_to_list)
    check("save_edit_calls_edit_project_with_slug", test_save_edit_calls_edit_project_with_slug)
    check("save_blocked_and_issues_shown_on_error", test_save_blocked_and_issues_shown_on_error)
    check("save_offers_scaffold_checklist_and_creates_checked_dirs", test_save_offers_scaffold_checklist_and_creates_checked_dirs)
    check("selecting_project_populates_editable_bindings_view",
          test_selecting_project_populates_editable_bindings_view)
    check("row_status_colors_green_for_resolving_roots_orange_otherwise",
          test_row_status_colors_green_for_resolving_roots_orange_otherwise)
    check("empty_selection_shows_placeholder_right_pane",
          test_empty_selection_shows_placeholder_right_pane)
    # Phase 3: editable right pane, local-only Bind
    check("bindings_browse_sets_field_and_marks_dirty",
          test_bindings_browse_sets_field_and_marks_dirty)
    check("bindings_validation_disables_and_enables_bind",
          test_bindings_validation_disables_and_enables_bind)
    check("bindings_revert_reloads_persisted_and_clears_dirty",
          test_bindings_revert_reloads_persisted_and_clears_dirty)
    check("switching_selection_with_dirty_edits_logs_discard_note",
          test_switching_selection_with_dirty_edits_logs_discard_note)
    check("bind_recomputes_row_and_pill_status_and_keeps_selection",
          test_bind_recomputes_row_and_pill_status_and_keeps_selection)
    check("bind_writes_local_bindings_and_leaves_shared_pair_untouched",
          test_bind_writes_local_bindings_and_leaves_shared_pair_untouched)
    check("bind_succeeds_when_shared_configs_root_is_read_only",
          test_bind_succeeds_when_shared_configs_root_is_read_only)
    # Phase 4: unbound pre-fill, empty-list affordance
    check("derived_bindings_show_helper_label_and_prefill_without_persisting",
          test_derived_bindings_show_helper_label_and_prefill_without_persisting)
    check("persisted_bindings_hide_the_derived_label",
          test_persisted_bindings_hide_the_derived_label)
    check("empty_project_list_shows_configs_pointer_affordance",
          test_empty_project_list_shows_configs_pointer_affordance)
    check("nonempty_project_list_hides_the_empty_list_affordance",
          test_nonempty_project_list_hides_the_empty_list_affordance)
    # further checks are appended by each task below


if __name__ == "__main__":
    run()
    print(f"\n{len(FAILURES)} failure(s).")
    sys.exit(1 if FAILURES else 0)
