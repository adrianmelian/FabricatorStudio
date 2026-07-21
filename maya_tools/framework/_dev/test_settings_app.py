"""settings_app tests (pure filesystem Python; no Maya session required). Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/_dev/test_settings_app.py

MAYA_APP_DIR is monkeypatched per-test to a temp dir (helper copied from
test_project_setup_resolve.py) so nothing here touches the real user maya
folder."""
import contextlib
import json
import os
import stat
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


def _configs_root():
    from maya_tools.framework import project_config_paths as pcp
    return pcp.local_configs_dir()


def _make_project(slug="demo", name=None):
    """Minimal v2 pair on disk under the CURRENT configs root."""
    d = _configs_root() / slug
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
        {"schema_version": 1, "name": "Old P", "project_root": "C:/Old",
         "pose_library_root": "C:/Old/SourceArt/_pose",
         "anim_library_root": "D:/Elsewhere/anims"}), encoding="utf-8")
    return d


_ROOTS = {"source_art_root": "D:/Art", "content_root": "D:/Game"}


def test_bind_writes_only_user_file():
    from maya_tools.framework import settings_app, project_config_resolve
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        d = _make_project("demo")
        before = {p.name: p.read_bytes() for p in d.iterdir()}
        out = settings_app.bind_project("demo", dict(_ROOTS))
        assert out == project_config_resolve.user_dir() / "demo.json", out
        saved = json.loads(out.read_text(encoding="utf-8"))
        assert saved["source_art_root"] == "D:/Art", saved
        after = {p.name: p.read_bytes() for p in d.iterdir()}
        assert before == after, "shared pair was touched"


def test_bind_works_when_shared_read_only():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        d = _make_project("demo")
        files = list(d.iterdir())
        try:
            for p in files:
                os.chmod(p, stat.S_IREAD)
            settings_app.bind_project("demo", dict(_ROOTS))
        finally:
            for p in files:
                os.chmod(p, stat.S_IREAD | stat.S_IWRITE)


def test_bind_validation_blocks():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("demo")
        for bad in (
            {"source_art_root": "D:/Art"},                        # missing content_root
            {"source_art_root": "Art", "content_root": "D:/G"},   # relative root
            {**_ROOTS, "pose_library_root_override": "C:poses"},  # drive-relative
        ):
            try:
                settings_app.bind_project("demo", bad)
                assert False, f"should have raised: {bad}"
            except settings_app.ValidationError as exc:
                assert any(i.severity == "error" for i in exc.issues)


def test_bind_overlap_warns_not_blocks():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("a")
        _make_project("b")
        settings_app.bind_project("a", dict(_ROOTS))
        out = settings_app.bind_project("b", dict(_ROOTS))   # same roots: warning only
        assert out.is_file()


def test_bind_unknown_and_reserved_slug():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("demo")
        for slug in ("nope", "settings"):
            try:
                settings_app.bind_project(slug, dict(_ROOTS))
                assert False, slug
            except settings_app.ValidationError:
                pass


def test_get_bindings_precedence():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("demo")
        _make_v1_project("oldp")
        assert settings_app.get_bindings("demo") == {}
        derived = settings_app.get_bindings("oldp")
        assert derived["source_art_root"] == "C:/Old", derived
        assert derived["anim_library_root_override"] == "D:/Elsewhere/anims"
        settings_app.bind_project("oldp", dict(_ROOTS))
        assert settings_app.get_bindings("oldp")["source_art_root"] == "D:/Art"


def test_pointer_roundtrip_preserves_foreign_keys():
    from maya_tools.framework import settings_app, project_config_resolve
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        shared = Path(td) / "shared_configs"
        shared.mkdir()
        settings_path = project_config_resolve.user_dir() / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"future_key": 7}), encoding="utf-8")

        settings_app.set_configs_root(str(shared))
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert data["future_key"] == 7, data
        assert data["configs_root"] == str(shared).replace("\\", "/"), data
        status = settings_app.configs_root_status()
        assert status["source"] == "pointer", status
        assert Path(status["effective_root"]) == shared, status

        settings_app.clear_configs_root()
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "configs_root" not in data and data["future_key"] == 7, data
        assert settings_app.configs_root_status()["source"] == "local"


def test_pointer_env_wins():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        shared = Path(td) / "env_configs"
        shared.mkdir()
        with env_var("FABRICATOR_PROJECT_CONFIGS", str(shared)):
            status = settings_app.configs_root_status()
            assert status["source"] == "env", status
            assert Path(status["effective_root"]) == shared, status


def test_pointer_set_nonexistent_raises_no_write():
    from maya_tools.framework import settings_app, project_config_resolve
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        try:
            settings_app.set_configs_root(str(Path(td) / "missing"))
            assert False, "should have raised"
        except settings_app.ValidationError:
            pass
        assert not (project_config_resolve.user_dir() / "settings.json").exists()


def test_list_projects_with_status_triage():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("bound_p")
        _make_project("unbound_p")
        _make_v1_project("derived_p")
        settings_app.bind_project("bound_p", dict(_ROOTS))
        rows = {r["slug"]: r for r in settings_app.list_projects_with_status()}
        assert rows["bound_p"]["bound"] and not rows["bound_p"]["derived"], rows
        assert not rows["unbound_p"]["bound"], rows
        assert rows["derived_p"]["bound"] and rows["derived_p"]["derived"], rows


def test_binding_status_green_when_both_roots_resolve():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("demo")
        with tempfile.TemporaryDirectory() as art, tempfile.TemporaryDirectory() as game:
            settings_app.bind_project("demo", {"source_art_root": art,
                                               "content_root": game})
            status = settings_app.binding_status("demo")
            assert status == {"state": "green", "reason": ""}, status


def test_binding_status_orange_missing_when_root_moved():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("demo")
        with tempfile.TemporaryDirectory() as art:
            missing = str(Path(art) / "moved_away")
            settings_app.bind_project("demo", {"source_art_root": art,
                                               "content_root": missing})
            status = settings_app.binding_status("demo")
            assert status["state"] == "orange", status
            assert status["reason"] == f"missing:{missing}", status


def test_binding_status_orange_unbound_when_roots_empty():
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        _make_project("demo")
        status = settings_app.binding_status("demo")
        assert status == {"state": "orange", "reason": "unbound"}, status


def test_integration_prefs_roundtrip():
    from maya_tools.framework import settings_app
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        prefs = settings_app.integration_prefs()
        # menu/shelf default OFF (toolbar_prefs._DEFAULTS, 2026-07-20
        # installer-polish change): a fresh install shouldn't scatter a menu
        # and two shelf tabs across Maya uninvited; hotkeys/marking menu
        # stay on (no visual clutter).
        assert prefs == {"load_menu": False, "load_shelf": False,
                         "load_hotkeys": True, "anim_marking_menu": True}, prefs
        settings_app.set_integration_pref("load_shelf", False)
        assert settings_app.integration_prefs()["load_shelf"] is False
        # Persisted in the unified prefs file, other keys intact.
        on_disk = toolbar_prefs.load_prefs()
        assert on_disk["load_shelf"] is False and on_disk["load_menu"] is False
        assert on_disk["dock_mode"] == "bottom", on_disk
        try:
            settings_app.set_integration_pref("nope", True)
            assert False, "should have raised"
        except ValueError:
            pass


def test_integration_gates_skip_headless():
    """Pref off -> each build entry point returns BEFORE any Maya call, so
    calling it under bare mayapy must not raise. (Pref ON would crash
    headless, which is exactly why the gate must come first.)"""
    from maya_tools.framework import settings_app
    with tempfile.TemporaryDirectory() as td, maya_app_dir(td):
        for key in ("load_menu", "load_shelf", "load_hotkeys"):
            settings_app.set_integration_pref(key, False)
        from maya_tools.framework.menu.fs_menu import build_menu
        from maya_tools.framework.shelves.fs_shelf import build_shelves
        from maya_tools.framework.hotkeys.fs_hotkeys import build_hotkeys
        build_menu()
        build_shelves()
        build_hotkeys()


if __name__ == "__main__":
    check("bind_writes_only_user_file", test_bind_writes_only_user_file)
    check("bind_works_when_shared_read_only", test_bind_works_when_shared_read_only)
    check("bind_validation_blocks", test_bind_validation_blocks)
    check("bind_overlap_warns_not_blocks", test_bind_overlap_warns_not_blocks)
    check("bind_unknown_and_reserved_slug", test_bind_unknown_and_reserved_slug)
    check("get_bindings_precedence", test_get_bindings_precedence)
    check("pointer_roundtrip_preserves_foreign_keys", test_pointer_roundtrip_preserves_foreign_keys)
    check("pointer_env_wins", test_pointer_env_wins)
    check("pointer_set_nonexistent_raises_no_write", test_pointer_set_nonexistent_raises_no_write)
    check("list_projects_with_status_triage", test_list_projects_with_status_triage)
    check("binding_status_green_when_both_roots_resolve", test_binding_status_green_when_both_roots_resolve)
    check("binding_status_orange_missing_when_root_moved", test_binding_status_orange_missing_when_root_moved)
    check("binding_status_orange_unbound_when_roots_empty", test_binding_status_orange_unbound_when_roots_empty)
    check("integration_prefs_roundtrip", test_integration_prefs_roundtrip)
    check("integration_gates_skip_headless", test_integration_gates_skip_headless)
    print(f"\n{len(FAILURES)} FAILs")
    sys.exit(1 if FAILURES else 0)
