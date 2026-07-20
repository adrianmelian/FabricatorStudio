"""Project-setup shareability + Perforce-guard tests (Plan 4). Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/_dev/test_project_setup_shareability.py

Covers: project_config_paths.configs_dir() honoring FABRICATOR_PROJECT_CONFIGS,
the per-user split (toolbar_prefs stays on maya_root()), the import-order
smoke check (cached module globals vs live configs_dir()), the
seeding-gated-to-writable-roots + read-only-bit-clearing + rmtree-logging
behavior of ensure_user_configs(), and the blueprint_library Perforce
read-only guards (duplicate/rename/delete/require_project_dir).

No Maya session required: project_config_paths and blueprint_library's file
ops are pure filesystem Python. MAYA_APP_DIR is monkeypatched per-test to a
temp dir so nothing here ever touches the real user maya folder (see
maya_app_dir() below, copied from test_project_configs.py).

PYTHONNOUSERSITE=1 is LOAD-BEARING: a user-site numpy 2.x collides with
Maya's bundled numpy inside the shiboken6 import (see test_phase_a.py).
"""
import contextlib
import importlib
import json
import logging
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
    """Point MAYA_APP_DIR at `path` for the duration; restore on exit.
    Copied from test_project_configs.py."""
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
def env_override(key, value):
    """Set (or clear, when value is None) an env var for the duration;
    restore on exit."""
    old = os.environ.get(key)
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = str(value)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def _write_config_dir(parent: Path, name: str, marker: str) -> Path:
    """Create parent/name/{session.json, export_mapping.json} with marker
    payloads. Copied from test_project_configs.py."""
    d = parent / name
    d.mkdir(parents=True)
    (d / "session.json").write_text('{"name": "%s"}' % marker, encoding="utf-8")
    (d / "export_mapping.json").write_text('{"project_root": "%s"}' % marker, encoding="utf-8")
    return d


def _make_project(configs_root: Path, name: str, blueprints_dir) -> Path:
    """Create a minimal-but-RESOLVABLE v1 pair: project_dir() resolves
    through project_config_resolve (v2), so the fixture needs a derivable
    absolute project_root; the off-root absolute blueprints_dir comes back
    verbatim as the derived per-machine override."""
    proj = configs_root / name
    proj.mkdir(parents=True)
    (proj / "session.json").write_text(
        json.dumps({"name": name, "blueprints_dir": str(blueprints_dir)}),
        encoding="utf-8")
    (proj / "export_mapping.json").write_text(
        json.dumps({"schema_version": 1, "name": name,
                    "project_root": str(configs_root.parent / "fixture_root")}),
        encoding="utf-8")
    return proj


def test_require_project_dir_unbound_names_the_bind_remedy():
    """UNBOUND project vs bound-but-no-blueprints must produce DIFFERENT
    actionable messages - an unbound machine's remedy is binding roots,
    not configuring a blueprints folder."""
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_resolve as pcr
    from maya_tools.rigging.fabricator import blueprint_library as bl
    with tempfile.TemporaryDirectory() as maya_dir:
        with maya_app_dir(maya_dir):
            pio.save_project({"name": "demo", "blueprints_subpath": ""})
            old_active = bl.active_project_name
            bl.active_project_name = lambda: "demo"
            try:
                raised = ""
                try:
                    bl.require_project_dir()      # unbound: no bindings yet
                except bl.LibraryError as exc:
                    raised = str(exc)
                assert "bind" in raised.lower() or "root" in raised.lower(), raised
                pcr.save_bindings("demo", {"source_art_root": str(Path(maya_dir) / "art"),
                                           "content_root": str(Path(maya_dir) / "game")})
                raised = ""
                try:
                    bl.require_project_dir()      # bound, blank blueprints
                except bl.LibraryError as exc:
                    raised = str(exc)
                assert "blueprints" in raised.lower(), raised
            finally:
                bl.active_project_name = old_active


def test_project_dir_resolves_v2_subpath_override_and_unbound():
    """project_dir() consumes the RESOLVED config (v2): unbound -> None
    (factory lane, never a raise), bound -> source_art_root/subpath, and a
    per-machine override wins."""
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_resolve as pcr
    from maya_tools.rigging.fabricator import blueprint_library as bl
    with tempfile.TemporaryDirectory() as maya_dir:
        with maya_app_dir(maya_dir):
            pio.save_project({"name": "demo", "blueprints_subpath": "Blueprints"})
            old_active = bl.active_project_name
            bl.active_project_name = lambda: "demo"
            try:
                assert bl.project_dir() is None            # unbound, no raise
                art = Path(maya_dir) / "art"
                pcr.save_bindings("demo", {"source_art_root": str(art),
                                           "content_root": str(Path(maya_dir) / "game")})
                assert bl.project_dir() == art / "Blueprints"
                elsewhere = Path(maya_dir) / "elsewhere"
                pcr.save_bindings("demo", {"source_art_root": str(art),
                                           "content_root": str(Path(maya_dir) / "game"),
                                           "blueprints_dir_override": str(elsewhere)})
                assert bl.project_dir() == elsewhere       # override wins
            finally:
                bl.active_project_name = old_active


def test_configs_dir_honors_env_override():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as maya_dir, tempfile.TemporaryDirectory() as shared_dir:
        with maya_app_dir(maya_dir), env_override("FABRICATOR_PROJECT_CONFIGS", shared_dir):
            assert pcp.configs_dir() == Path(shared_dir)


def test_configs_dir_falls_back_without_override():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as maya_dir:
        with maya_app_dir(maya_dir), env_override("FABRICATOR_PROJECT_CONFIGS", None):
            assert pcp.configs_dir() == Path(maya_dir) / pcp.CONFIGS_DIRNAME


def test_toolbar_prefs_stays_on_maya_root_when_shared_override_set():
    # Per-user split (spec §4.4): the shared configs root moves, but
    # active_project / window prefs must stay LOCAL.
    from maya_tools.framework import project_config_paths as pcp
    from maya_tools.framework.toolbar import toolbar_prefs
    with tempfile.TemporaryDirectory() as maya_dir, tempfile.TemporaryDirectory() as shared_dir:
        with maya_app_dir(maya_dir), env_override("FABRICATOR_PROJECT_CONFIGS", shared_dir):
            assert pcp.configs_dir() == Path(shared_dir)
            assert toolbar_prefs.prefs_path() == Path(maya_dir) / toolbar_prefs.PREFS_FILENAME


def test_import_order_smoke_cached_globals_match_live_configs_dir():
    # Spec §4.4 / finding 7: maya_startup._CONFIGS_DIR and
    # export_core._PROJECT_CONFIGS_DIR are frozen at import time, so a
    # studio must set FABRICATOR_PROJECT_CONFIGS before Maya launches. This
    # is the setup-time smoke check that catches an import-order mistake.
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as maya_dir:
        with maya_app_dir(maya_dir):
            import maya_tools.framework.maya_startup as ms
            import maya_tools.export.export_core as ec
            importlib.reload(ms)
            importlib.reload(ec)
            assert ms._CONFIGS_DIR == str(pcp.configs_dir())
            assert ec._PROJECT_CONFIGS_DIR == pcp.configs_dir()


def test_ensure_user_configs_skips_seeding_on_unwritable_root():
    # Shared/read-only root (spec §4.4): the TA publishes templates once;
    # an artist's first launch must never hit a permission error or churn
    # the shared root trying to auto-seed/migrate.
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as maya_dir, tempfile.TemporaryDirectory() as packaged_td:
        packaged = Path(packaged_td)
        _write_config_dir(packaged, "_unreal5", "template-unreal5")
        with maya_app_dir(maya_dir):
            real_mkstemp = pcp.tempfile.mkstemp

            def failing_mkstemp(*a, **kw):
                raise OSError("simulated read-only shared root")

            pcp.tempfile.mkstemp = failing_mkstemp
            try:
                user_dir = pcp.ensure_user_configs(packaged_dir=packaged)
            finally:
                pcp.tempfile.mkstemp = real_mkstemp
            assert user_dir == Path(maya_dir) / pcp.CONFIGS_DIRNAME
            assert not (user_dir / "unreal5").exists()   # seeding skipped: root not writable


def test_seed_clears_readonly_mode_bits_on_copied_files():
    # shutil.copytree's copy2 preserves source mode bits; a legacy config
    # migrated from a Perforce workspace can ship read-only. The LOCAL
    # seeded copy must always be freely editable.
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as maya_dir, tempfile.TemporaryDirectory() as packaged_td:
        packaged = Path(packaged_td)
        src_dir = _write_config_dir(packaged, "_unreal5", "template-unreal5")
        (src_dir / "session.json").chmod(stat.S_IREAD)
        (src_dir / "export_mapping.json").chmod(stat.S_IREAD)
        try:
            with maya_app_dir(maya_dir):
                user_dir = pcp.ensure_user_configs(packaged_dir=packaged)
                seeded_session = user_dir / "unreal5" / "session.json"
                seeded_session.write_text('{"name": "edited-locally"}', encoding="utf-8")
                assert seeded_session.read_text(encoding="utf-8") == '{"name": "edited-locally"}'
        finally:
            (src_dir / "session.json").chmod(stat.S_IWRITE)
            (src_dir / "export_mapping.json").chmod(stat.S_IWRITE)


def test_seed_logs_rmtree_cleanup_failure_without_swallowing():
    # A locked staging dir (AV scan, network hiccup) must not go silent:
    # the seed itself still succeeds, but the cleanup failure is LOGGED so
    # orphaned '_stage-*' dirs don't accumulate unnoticed on a shared root.
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as maya_dir, tempfile.TemporaryDirectory() as packaged_td:
        packaged = Path(packaged_td)
        _write_config_dir(packaged, "_unreal5", "template-unreal5")
        with maya_app_dir(maya_dir):
            real_rmtree = pcp.shutil.rmtree

            def failing_rmtree(path, *a, **kw):
                raise OSError("simulated locked staging dir (AV scan)")

            pcp.shutil.rmtree = failing_rmtree
            log = logging.getLogger("maya_tools.framework.project_config_paths")
            records = []
            handler = logging.Handler()
            handler.emit = lambda record: records.append(record)
            log.addHandler(handler)
            try:
                user_dir = pcp.ensure_user_configs(packaged_dir=packaged)
                assert (user_dir / "unreal5" / "session.json").is_file()   # seed itself still succeeds
                assert any("rmtree" in r.getMessage().lower() or "stage" in r.getMessage().lower()
                           or "cleanup" in r.getMessage().lower() for r in records), \
                    "expected the rmtree cleanup failure to be logged, not swallowed"
            finally:
                log.removeHandler(handler)
                pcp.shutil.rmtree = real_rmtree


def test_require_project_dir_mkdir_blocked_raises_library_error():
    # A portable, chmod-free way to force the exact OSError shape the p4
    # guard must also catch: blueprints_dir's PARENT segment already exists
    # as a plain FILE, so mkdir(parents=True) cannot create a child under
    # it (NotADirectoryError/FileExistsError, both OSError subclasses).
    from maya_tools.rigging.fabricator import blueprint_library as bl
    with tempfile.TemporaryDirectory() as maya_dir, tempfile.TemporaryDirectory() as parent_dir:
        with maya_app_dir(maya_dir):
            from maya_tools.framework import project_config_paths as pcp
            blocked_parent = Path(parent_dir) / "not_a_directory"
            blocked_parent.write_text("i am a file", encoding="utf-8")
            blueprints_dir = blocked_parent / "Blueprints"
            _make_project(pcp.configs_dir(), "demo", blueprints_dir)
            old_active = bl.active_project_name
            bl.active_project_name = lambda: "demo"
            try:
                raised = False
                try:
                    bl.require_project_dir()
                except bl.LibraryError:
                    raised = True
                except OSError:
                    raised = False
                assert raised, "expected LibraryError, not a raw OSError"
            finally:
                bl.active_project_name = old_active


def test_duplicate_copy_failure_raises_library_error():
    # shutil.copy2 failing (e.g. a Perforce-unopened dest, an ACL denial)
    # must surface as LibraryError, not a raw PermissionError. Monkeypatch
    # copy2 to force the failure deterministically (chmod is unreliable for
    # forcing a NEW file's write to fail — the dest doesn't exist yet).
    from maya_tools.rigging.fabricator import blueprint_library as bl
    with tempfile.TemporaryDirectory() as maya_dir, tempfile.TemporaryDirectory() as bp_dir:
        with maya_app_dir(maya_dir):
            from maya_tools.framework import project_config_paths as pcp
            _make_project(pcp.configs_dir(), "demo", Path(bp_dir))
            old_active = bl.active_project_name
            bl.active_project_name = lambda: "demo"
            src = Path(bp_dir) / "Frog.limb.yaml"
            src.write_text("data", encoding="utf-8")
            real_copy2 = bl.shutil.copy2

            def failing_copy2(*a, **kw):
                raise PermissionError("simulated p4-unopened destination")

            bl.shutil.copy2 = failing_copy2
            try:
                raised = False
                try:
                    bl.duplicate(src, "FrogCopy")
                except bl.LibraryError:
                    raised = True
                except PermissionError:
                    raised = False
                assert raised, "expected LibraryError, not a raw PermissionError"
            finally:
                bl.shutil.copy2 = real_copy2
                bl.active_project_name = old_active


def test_rename_failure_raises_library_error():
    # `Path.rename` failing (e.g. a Perforce-unopened dest, an ACL denial)
    # must surface as LibraryError, not a raw PermissionError.
    #
    # NOTE: chmod(stat.S_IREAD) on the SOURCE file does NOT block
    # Path.rename on Windows (verified on the real machine) — Windows
    # rename (MoveFileEx) only cares about the directory entry's
    # permissions, not the read-only attribute on the file being moved, so
    # a read-only-attributed source renames just fine. That means a
    # chmod-based repro can never go red here; force the failure
    # deterministically instead by monkeypatching `Path.rename`, mirroring
    # the `shutil.copy2` monkeypatch technique Task 8 uses for
    # `duplicate()`.
    from maya_tools.rigging.fabricator import blueprint_library as bl
    with tempfile.TemporaryDirectory() as maya_dir, tempfile.TemporaryDirectory() as bp_dir:
        with maya_app_dir(maya_dir):
            from maya_tools.framework import project_config_paths as pcp
            _make_project(pcp.configs_dir(), "demo", Path(bp_dir))
            old_active = bl.active_project_name
            bl.active_project_name = lambda: "demo"
            target = Path(bp_dir) / "Frog.blueprint.yaml"
            target.write_text("data", encoding="utf-8")
            real_rename = Path.rename

            def failing_rename(self, *a, **kw):
                raise PermissionError("simulated p4-unopened destination")

            Path.rename = failing_rename
            try:
                raised = False
                try:
                    bl.rename(target, "Toad")
                except bl.LibraryError:
                    raised = True
                except PermissionError:
                    raised = False
                assert raised, "expected LibraryError, not a raw PermissionError"
            finally:
                Path.rename = real_rename
                bl.active_project_name = old_active


def test_delete_readonly_file_raises_library_error():
    # Windows also blocks deleting a read-only-attributed file. Must
    # surface as LibraryError, not a raw PermissionError.
    from maya_tools.rigging.fabricator import blueprint_library as bl
    with tempfile.TemporaryDirectory() as maya_dir, tempfile.TemporaryDirectory() as bp_dir:
        with maya_app_dir(maya_dir):
            from maya_tools.framework import project_config_paths as pcp
            _make_project(pcp.configs_dir(), "demo", Path(bp_dir))
            old_active = bl.active_project_name
            bl.active_project_name = lambda: "demo"
            target = Path(bp_dir) / "Frog.blueprint.yaml"
            target.write_text("data", encoding="utf-8")
            target.chmod(stat.S_IREAD)
            try:
                raised = False
                try:
                    bl.delete(target)
                except bl.LibraryError:
                    raised = True
                except PermissionError:
                    raised = False
                assert raised, "expected LibraryError, not a raw PermissionError"
            finally:
                target.chmod(stat.S_IWRITE)
                bl.active_project_name = old_active


def run():
    check("blueprint_library/project_dir_resolves_v2_subpath_override_and_unbound", test_project_dir_resolves_v2_subpath_override_and_unbound)
    check("blueprint_library/require_project_dir_unbound_names_bind_remedy", test_require_project_dir_unbound_names_the_bind_remedy)
    check("configs_dir/honors_env_override", test_configs_dir_honors_env_override)
    check("configs_dir/falls_back_without_override", test_configs_dir_falls_back_without_override)
    check("toolbar_prefs/stays_on_maya_root_when_shared", test_toolbar_prefs_stays_on_maya_root_when_shared_override_set)
    check("import_order/smoke_cached_globals_match_live", test_import_order_smoke_cached_globals_match_live_configs_dir)
    check("ensure_user_configs/skips_seeding_on_unwritable_root", test_ensure_user_configs_skips_seeding_on_unwritable_root)
    check("seed/clears_readonly_mode_bits_on_copied_files", test_seed_clears_readonly_mode_bits_on_copied_files)
    check("seed/logs_rmtree_cleanup_failure", test_seed_logs_rmtree_cleanup_failure_without_swallowing)
    check("blueprint_library/mkdir_guard_raises_library_error", test_require_project_dir_mkdir_blocked_raises_library_error)
    check("blueprint_library/duplicate_guard_raises_library_error", test_duplicate_copy_failure_raises_library_error)
    check("blueprint_library/rename_guard_raises_library_error", test_rename_failure_raises_library_error)
    check("blueprint_library/delete_guard_raises_library_error", test_delete_readonly_file_raises_library_error)


if __name__ == "__main__":
    run()
    print(f"\n{len(FAILURES)} failure(s).")
    sys.exit(1 if FAILURES else 0)
