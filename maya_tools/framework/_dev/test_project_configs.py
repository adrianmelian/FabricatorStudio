"""Project-config relocation tests. Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/_dev/test_project_configs.py

No Maya session required: project_config_paths is pure filesystem Python,
and the enumeration tests import maya_startup / export_core WITHOUT
maya.standalone.initialize() — bare-mayapy module import of maya.cmds /
maya.api.OpenMaya / maya.mel is safe (verified 2026-07-02); no cmds
function is ever CALLED in this process.

PYTHONNOUSERSITE=1 is LOAD-BEARING: a user-site numpy 2.x collides with
Maya's bundled numpy inside the shiboken6 import (see test_phase_a.py).

MAYA_APP_DIR is monkeypatched per-test to a temp dir so nothing here ever
touches the real user maya folder."""
import contextlib
import importlib
import json
import logging
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


def _write_config_dir(parent: Path, name: str, marker: str) -> Path:
    """Create parent/name/{session.json, export_mapping.json} with marker payloads."""
    d = parent / name
    d.mkdir(parents=True)
    (d / "session.json").write_text('{"name": "%s"}' % marker, encoding="utf-8")
    (d / "export_mapping.json").write_text('{"project_root": "%s"}' % marker, encoding="utf-8")
    return d


def test_configs_dir_honors_maya_app_dir():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            assert pcp.configs_dir() == Path(td) / "fabricator_project_configs"


def test_configs_dir_falls_back_to_real_documents_maya():
    """No MAYA_APP_DIR → <real Documents>/maya, where Documents is the
    redirect-aware shell folder (e.g. D:\\Documents), matching what Maya
    itself resolves when it sets MAYA_APP_DIR. Plain ~/Documents is only
    the last-resort fallback when the shell query is unavailable."""
    from maya_tools.framework import project_config_paths as pcp
    old = os.environ.pop("MAYA_APP_DIR", None)
    try:
        docs = pcp._documents_dir()
        assert docs.name == "Documents" or docs.is_dir()
        assert pcp.maya_root() == docs / "maya"
        assert pcp.configs_dir() == docs / "maya" / "fabricator_project_configs"
        # toolbar prefs must resolve from the SAME root (single source).
        from maya_tools.framework.toolbar import toolbar_prefs
        assert toolbar_prefs.prefs_path() == docs / "maya" / toolbar_prefs.PREFS_FILENAME
    finally:
        if old is not None:
            os.environ["MAYA_APP_DIR"] = old


def test_packaged_templates_dir_is_in_tree():
    from maya_tools.framework import project_config_paths as pcp
    expected = REPO_ROOT / "maya_tools" / "framework" / "project_configs"
    assert pcp.packaged_templates_dir() == expected


def test_module_source_is_pure_python():
    src = (REPO_ROOT / "maya_tools" / "framework" / "project_config_paths.py").read_text(encoding="utf-8")
    for forbidden in ("maya.cmds", "maya.api", "maya.mel", "PySide6", "shiboken"):
        assert forbidden not in src, f"project_config_paths.py must not reference {forbidden}"


def test_ensure_creates_configs_dir():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as packaged_td:
        with maya_app_dir(td):
            result = pcp.ensure_user_configs(packaged_dir=Path(packaged_td))
            assert result == Path(td) / "fabricator_project_configs"
            assert result.is_dir()


def test_seed_copies_underscore_template_unprefixed():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as packaged_td:
        packaged = Path(packaged_td)
        _write_config_dir(packaged, "_unreal5", "template-unreal5")
        with maya_app_dir(td):
            user_dir = pcp.ensure_user_configs(packaged_dir=packaged)
            seeded = user_dir / "unreal5"
            assert (seeded / "session.json").read_text(encoding="utf-8") == '{"name": "template-unreal5"}'
            assert (seeded / "export_mapping.json").is_file()
            assert not (user_dir / "_unreal5").exists()   # seeded under the un-prefixed name only


def test_seed_never_overwrites_existing_user_config():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as packaged_td:
        packaged = Path(packaged_td)
        _write_config_dir(packaged, "_unreal5", "template-unreal5")
        with maya_app_dir(td):
            user_dir = pcp.ensure_user_configs(packaged_dir=packaged)
            # user edits their copy...
            sentinel = user_dir / "unreal5" / "session.json"
            sentinel.write_text('{"name": "user-edited"}', encoding="utf-8")
            # ...and a second run (idempotent) must not clobber it
            pcp.ensure_user_configs(packaged_dir=packaged)
            assert sentinel.read_text(encoding="utf-8") == '{"name": "user-edited"}'


def test_seed_copy_failure_leaves_no_partial_config():
    # A partway copytree failure (AV lock, disk full, unreadable template)
    # must never publish a half-seeded config: the next run must retry.
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as packaged_td:
        packaged = Path(packaged_td)
        _write_config_dir(packaged, "_unreal5", "template-unreal5")
        with maya_app_dir(td):
            real_copytree = pcp.shutil.copytree

            def failing_copytree(src, dst, **kw):
                Path(dst).mkdir(parents=True)
                (Path(dst) / "session.json").write_text("{", encoding="utf-8")
                raise OSError("simulated partway copy failure")

            pcp.shutil.copytree = failing_copytree
            try:
                try:
                    pcp.ensure_user_configs(packaged_dir=packaged)
                except OSError:
                    pass
                else:
                    raise AssertionError("expected OSError from the failed seeding")
                user_dir = Path(td) / "fabricator_project_configs"
                assert not (user_dir / "unreal5").exists()   # nothing half-seeded published
                visible = [p.name for p in user_dir.iterdir() if not p.name.startswith("_")]
                assert visible == [], f"failed seeding left visible debris: {visible}"
            finally:
                pcp.shutil.copytree = real_copytree
            # the failure was not made permanent: the next run seeds fully
            user_dir = pcp.ensure_user_configs(packaged_dir=packaged)
            got = (user_dir / "unreal5" / "session.json").read_text(encoding="utf-8")
            assert got == '{"name": "template-unreal5"}'
            assert (user_dir / "unreal5" / "export_mapping.json").is_file()


def test_seed_yields_to_concurrent_winner():
    # TOCTOU: dest appears between the exists() check and the publish
    # rename (two Maya sessions launched near-simultaneously). The loser
    # must not raise; the winner's copy is authoritative.
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as packaged_td:
        packaged = Path(packaged_td)
        _write_config_dir(packaged, "_unreal5", "template-unreal5")
        with maya_app_dir(td):
            user_dir = Path(td) / "fabricator_project_configs"
            real_copytree = pcp.shutil.copytree

            def racing_copytree(src, dst, **kw):
                if not (user_dir / "unreal5").exists():
                    _write_config_dir(user_dir, "unreal5", "concurrent-winner")
                return real_copytree(src, dst, **kw)

            pcp.shutil.copytree = racing_copytree
            try:
                pcp.ensure_user_configs(packaged_dir=packaged)   # must not raise
            finally:
                pcp.shutil.copytree = real_copytree
            got = (user_dir / "unreal5" / "session.json").read_text(encoding="utf-8")
            assert got == '{"name": "concurrent-winner"}'
            visible = sorted(p.name for p in user_dir.iterdir() if not p.name.startswith("_"))
            assert visible == ["unreal5"], f"race left visible debris: {visible}"


def test_migrates_legacy_dir_and_keeps_source():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as packaged_td:
        packaged = Path(packaged_td)
        src = _write_config_dir(packaged, "archangel", "live-archangel")
        with maya_app_dir(td):
            user_dir = pcp.ensure_user_configs(packaged_dir=packaged)
            migrated = user_dir / "archangel"
            assert (migrated / "session.json").read_text(encoding="utf-8") == '{"name": "live-archangel"}'
            assert (migrated / "export_mapping.json").is_file()
            # COPY ONLY: the source stays intact
            assert (src / "session.json").read_text(encoding="utf-8") == '{"name": "live-archangel"}'
            assert (src / "export_mapping.json").is_file()


def test_migration_never_overwrites_existing_user_config():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as packaged_td:
        packaged = Path(packaged_td)
        _write_config_dir(packaged, "archangel", "stale-in-tree")
        with maya_app_dir(td):
            user_dir = Path(td) / "fabricator_project_configs"
            _write_config_dir(user_dir, "archangel", "user-authored")
            pcp.ensure_user_configs(packaged_dir=packaged)
            got = (user_dir / "archangel" / "session.json").read_text(encoding="utf-8")
            assert got == '{"name": "user-authored"}'


def test_legacy_config_outranks_its_template():
    # Installer-upgrade case: _preserve_paths/_restore_paths puts the old
    # in-tree project_configs/ (with an edited legacy unreal5/) back on top
    # of a new payload that ships _unreal5/, so the packaged dir holds
    # BOTH. Plain ASCII sort would seed the pristine template first
    # ('_' < letters) and the dest.exists() guard would then skip the
    # user's real data. The legacy live config must win.
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as packaged_td:
        packaged = Path(packaged_td)
        _write_config_dir(packaged, "_unreal5", "template-unreal5")
        _write_config_dir(packaged, "unreal5", "legacy-user-edited")
        with maya_app_dir(td):
            user_dir = pcp.ensure_user_configs(packaged_dir=packaged)
            got = (user_dir / "unreal5" / "session.json").read_text(encoding="utf-8")
            assert got == '{"name": "legacy-user-edited"}'
            entries = sorted(p.name for p in user_dir.iterdir())
            assert entries == ["unreal5"], f"unexpected entries: {entries}"


def test_seed_and_migrate_are_idempotent_together():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as packaged_td:
        packaged = Path(packaged_td)
        _write_config_dir(packaged, "_unreal5", "template-unreal5")
        _write_config_dir(packaged, "archangel", "live-archangel")
        with maya_app_dir(td):
            user_dir = pcp.ensure_user_configs(packaged_dir=packaged)
            for name in ("unreal5", "archangel"):
                (user_dir / name / "session.json").write_text('{"name": "touched-%s"}' % name, encoding="utf-8")
            pcp.ensure_user_configs(packaged_dir=packaged)   # second run: no-op
            for name in ("unreal5", "archangel"):
                got = (user_dir / name / "session.json").read_text(encoding="utf-8")
                assert got == '{"name": "touched-%s"}' % name
            entries = sorted(p.name for p in user_dir.iterdir())
            assert entries == ["archangel", "unreal5"]


def test_maya_startup_reads_user_configs_dir():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.maya_startup as ms
            importlib.reload(ms)   # re-evaluates _CONFIGS_DIR under the temp MAYA_APP_DIR
            assert ms._CONFIGS_DIR == str(pcp.configs_dir())
            # reload ran ensure_user_configs() against the REAL packaged dir:
            # 'unreal5' arrives via migration (pre-rename) or seeding (post-rename)
            assert "unreal5" in ms.list_configs()
            # behavior guard: default-config fallback chain unchanged
            assert ms._DEFAULT_CONFIG == "unreal5"


def test_maya_startup_import_survives_seeding_failure():
    # Import-time OSError guard (e9776c3): a seeding failure during module
    # import (AV file lock, disk full at Maya launch) must never become an
    # ImportError — maya_startup must stay
    # importable. The module falls back to the plain (uncreated) configs
    # path and enumeration degrades to an empty list; seeding is retryable
    # on the next import.
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            real_ensure = pcp.ensure_user_configs

            def failing_ensure(packaged_dir=None):
                raise OSError("simulated seeding failure (AV lock / disk full)")

            log = logging.getLogger("maya_tools.framework.maya_startup")
            old_disabled = log.disabled
            pcp.ensure_user_configs = failing_ensure
            log.disabled = True   # the fallback warning is expected; keep suite output clean
            try:
                import maya_tools.framework.maya_startup as ms
                importlib.reload(ms)   # must not raise (no ImportError at Maya startup)
                assert ms._CONFIGS_DIR == str(pcp.configs_dir())
                assert ms.list_configs() == []   # dir never created; degrades to empty, not a crash
            finally:
                pcp.ensure_user_configs = real_ensure
                log.disabled = old_disabled


def test_list_configs_skips_underscore_dirs_in_user_dir():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.maya_startup as ms
            importlib.reload(ms)
            (pcp.configs_dir() / "_scratch").mkdir()
            names = ms.list_configs()
            assert "_scratch" not in names
            assert "unreal5" in names


def test_export_core_reads_user_configs_dir():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.export.export_core as ec
            importlib.reload(ec)   # re-evaluates _PROJECT_CONFIGS_DIR under the temp MAYA_APP_DIR
            assert ec._PROJECT_CONFIGS_DIR == pcp.configs_dir()
            assert "unreal5" in [p.name for p in ec.iter_project_dirs()]


def test_export_core_import_survives_seeding_failure():
    # Import-time OSError guard (mirrors maya_startup's, e9776c3): a seeding
    # failure during module import must never become an ImportError — every
    # export and library tool (exporter_ui, static_mesh, skeletal_mesh,
    # anim_core, pose_library, anim_library, ...) imports export_core. The
    # module falls back to the plain (uncreated) configs path and enumeration
    # degrades to an empty list; seeding is retryable on the next import.
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            real_ensure = pcp.ensure_user_configs

            def failing_ensure(packaged_dir=None):
                raise OSError("simulated seeding failure (AV lock / disk full)")

            log = logging.getLogger("maya_tools.export.export_core")
            old_disabled = log.disabled
            pcp.ensure_user_configs = failing_ensure
            log.disabled = True   # the fallback warning is expected; keep suite output clean
            try:
                import maya_tools.export.export_core as ec
                importlib.reload(ec)   # must not raise (no ImportError for export/library tools)
                assert ec._PROJECT_CONFIGS_DIR == pcp.configs_dir()
                assert ec.iter_project_dirs() == []   # dir never created; degrades to empty, not a crash
            finally:
                pcp.ensure_user_configs = real_ensure
                log.disabled = old_disabled


def test_iter_project_dirs_skips_underscore_in_user_dir():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.export.export_core as ec
            importlib.reload(ec)
            (pcp.configs_dir() / "_scratch").mkdir()
            names = [p.name for p in ec.iter_project_dirs()]
            assert "_scratch" not in names


def test_no_match_error_names_user_location():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.export.export_core as ec
            importlib.reload(ec)
            try:
                ec.load_project_config("X:/definitely/nowhere/scene.ma")
            except RuntimeError as exc:
                assert "fabricator_project_configs" in str(exc)
            else:
                raise AssertionError("expected RuntimeError for an unmatched scene path")


def test_choke_points_all_use_project_config_paths():
    sources = {
        "maya_startup.py": (REPO_ROOT / "maya_tools" / "framework" / "maya_startup.py").read_text(encoding="utf-8"),
        "export_core.py": (REPO_ROOT / "maya_tools" / "export" / "export_core.py").read_text(encoding="utf-8"),
    }
    for fname, src in sources.items():
        assert "project_config_paths" in src, f"{fname} must resolve configs via project_config_paths"
    # the old in-tree constructions must be gone
    assert "os.path.dirname(__file__), 'project_configs'" not in sources["maya_startup.py"]
    assert "/ 'framework' / 'project_configs'" not in sources["export_core.py"]

    # fs_window no longer imports project_config_paths directly (finding 9,
    # 2026-07-07): it routes exclusively through
    # blueprint_library.require_project_dir(), which itself funnels through
    # project_config_paths.configs_dir(). fs_window cannot be imported here
    # (PySide6 + live Maya modules), so pin the routing by source scan.
    ks_src = (REPO_ROOT / "maya_tools" / "rigging" / "fabricator" / "ui" / "fs_window.py").read_text(encoding="utf-8")
    assert "'framework' / 'project_configs'" not in ks_src
    assert "project_config_paths" not in ks_src, \
        "fs_window.py must route configs via blueprint_library, not project_config_paths directly"
    assert "blueprint_library" in ks_src, \
        "fs_window.py must resolve the project blueprints folder via blueprint_library"
    assert "require_project_dir" in ks_src, \
        "fs_window.py's Save Rig choke point must call blueprint_library.require_project_dir()"
    assert "LibraryError" in ks_src, \
        "fs_window.py must catch blueprint_library.LibraryError at its require_project_dir() call site"

    bl_src = (REPO_ROOT / "maya_tools" / "rigging" / "fabricator" / "blueprint_library.py").read_text(encoding="utf-8")
    assert "project_config_paths" in bl_src, \
        "blueprint_library.py must itself resolve configs via project_config_paths"


def test_real_packaged_template_is_underscore_prefixed_and_seeds():
    from maya_tools.framework import project_config_paths as pcp
    packaged = pcp.packaged_templates_dir()
    assert (packaged / "_unreal5" / "session.json").is_file()
    assert (packaged / "_unreal5" / "export_mapping.json").is_file()
    assert not (packaged / "unreal5").exists()   # renamed, not copied
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            user_dir = pcp.ensure_user_configs()
            seeded = user_dir / "unreal5" / "session.json"
            assert seeded.is_file()
            template = (packaged / "_unreal5" / "session.json").read_text(encoding="utf-8")
            assert seeded.read_text(encoding="utf-8") == template


def test_local_configs_dir_ignores_env_and_pointer():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            local = pcp.local_configs_dir()
            assert str(local).replace("\\", "/").endswith("fabricator_project_configs")
            os.environ["FABRICATOR_PROJECT_CONFIGS"] = "X:/somewhere/else"
            try:
                assert pcp.local_configs_dir() == local     # env var must NOT move it
            finally:
                del os.environ["FABRICATOR_PROJECT_CONFIGS"]


def test_configs_dir_resolution_order():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            local = pcp.local_configs_dir()
            assert pcp.configs_dir() == local              # nothing set -> local
            settings = local / "_user" / "settings.json"
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps({"configs_root": str(Path(td) / "shared")}),
                                encoding="utf-8")
            assert pcp.configs_dir() == Path(td) / "shared"  # pointer wins over local
            os.environ["FABRICATOR_PROJECT_CONFIGS"] = str(Path(td) / "env_root")
            try:
                assert pcp.configs_dir() == Path(td) / "env_root"  # env var wins over pointer
            finally:
                del os.environ["FABRICATOR_PROJECT_CONFIGS"]


def test_configs_dir_pointer_blank_or_corrupt_falls_back_local():
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            local = pcp.local_configs_dir()
            settings = local / "_user" / "settings.json"
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps({"configs_root": ""}), encoding="utf-8")
            assert pcp.configs_dir() == local
            settings.write_text("{not json", encoding="utf-8")
            assert pcp.configs_dir() == local
            # valid JSON that is NOT an object (the natural hand-edit for a
            # one-value pointer file) must also fall back, never raise
            settings.write_text(json.dumps("X:/somewhere"), encoding="utf-8")
            assert pcp.configs_dir() == local
            settings.write_text("null", encoding="utf-8")
            assert pcp.configs_dir() == local
            settings.write_text(json.dumps({"configs_root": 123}), encoding="utf-8")
            assert pcp.configs_dir() == local


def test_load_project_config_v2_split_roots():
    """The exporter seam end-to-end on a v2 project: scenes match under the
    SOURCE ART root, output lands under the CONTENT root, libraries ride
    the resolved config; the content tree never matches as a source."""
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_resolve as pcr
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.export.export_core as ec
            importlib.reload(ec)
            pio.save_project({
                "name": "Split Demo", "engine": "unreal5",
                "linear_unit": "cm", "angular_unit": "deg", "frame_rate": "ntsc",
                "playback_start": None, "playback_end": None, "undo_depth": 200,
                "camera_near_clip": 1.0, "camera_far_clip": 100000.0,
                "grid": {}, "blueprints_subpath": "",
                "asset_classes": [
                    {"id": "character", "label": "Character",
                     "export_types": ["skeletal_mesh"],
                     "source_dir": "SourceArt/Characters/{name}",
                     "dest_dir": "Game/Content/Characters/{name}",
                     "anim": {"gameplay": "Game/Content/Characters/{name}/Animations",
                              "cinematic": ""}}],
                "prefixes": {"static_mesh": "SM_", "skeletal_mesh": "SK_"},
                "anim_prefix": "A_",
                "pose_library_subpath": "SourceArt/_pose",
                "anim_library_subpath": "SourceArt/_anim",
                "mesh_group_name": "", "engine_up_axis": "z",
                "fbx_presets": {"static_mesh": {}, "skeletal_mesh": {},
                                "animation": {}}})
            art = Path(td) / "art"
            game = Path(td) / "game"
            pcr.save_bindings("split_demo", {"source_art_root": str(art),
                                             "content_root": str(game)})
            scene = str(art / "SourceArt/Characters/Hero/Hero.ma").replace("\\", "/")
            config = ec.load_project_config(scene)
            assert config["slug"] == "split_demo"
            assert config["engine_up_axis"] == "z"          # exporter axis rides along
            assert ec.resolve_destination(scene, config) == \
                game / "Game/Content/Characters/Hero"
            assert ec.resolve_anim_destination(
                scene, {"output_kind": "gameplay"}, config) == \
                game / "Game/Content/Characters/Hero/Animations"
            assert ec._library_root_from_config(config, "pose_library") == \
                art / "SourceArt/_pose"
            content_scene = str(game / "Game/Content/Characters/Hero/Hero.ma")
            try:
                ec.load_project_config(content_scene)
            except RuntimeError:
                pass
            else:
                raise AssertionError("content-tree scene must not match as source")


def main():
    check("configs_dir_honors_maya_app_dir", test_configs_dir_honors_maya_app_dir)
    check("load_project_config_v2_split_roots", test_load_project_config_v2_split_roots)
    check("local_configs_dir_ignores_env_and_pointer", test_local_configs_dir_ignores_env_and_pointer)
    check("configs_dir_resolution_order", test_configs_dir_resolution_order)
    check("configs_dir_pointer_blank_or_corrupt_falls_back_local", test_configs_dir_pointer_blank_or_corrupt_falls_back_local)
    check("configs_dir_falls_back_to_real_documents_maya", test_configs_dir_falls_back_to_real_documents_maya)
    check("packaged_templates_dir_is_in_tree", test_packaged_templates_dir_is_in_tree)
    check("module_source_is_pure_python", test_module_source_is_pure_python)
    check("ensure_creates_configs_dir", test_ensure_creates_configs_dir)
    check("seed_copies_underscore_template_unprefixed", test_seed_copies_underscore_template_unprefixed)
    check("seed_never_overwrites_existing_user_config", test_seed_never_overwrites_existing_user_config)
    check("seed_copy_failure_leaves_no_partial_config", test_seed_copy_failure_leaves_no_partial_config)
    check("seed_yields_to_concurrent_winner", test_seed_yields_to_concurrent_winner)
    check("migrates_legacy_dir_and_keeps_source", test_migrates_legacy_dir_and_keeps_source)
    check("migration_never_overwrites_existing_user_config", test_migration_never_overwrites_existing_user_config)
    check("legacy_config_outranks_its_template", test_legacy_config_outranks_its_template)
    check("seed_and_migrate_are_idempotent_together", test_seed_and_migrate_are_idempotent_together)
    check("maya_startup_reads_user_configs_dir", test_maya_startup_reads_user_configs_dir)
    check("maya_startup_import_survives_seeding_failure", test_maya_startup_import_survives_seeding_failure)
    check("list_configs_skips_underscore_dirs_in_user_dir", test_list_configs_skips_underscore_dirs_in_user_dir)
    check("export_core_reads_user_configs_dir", test_export_core_reads_user_configs_dir)
    check("export_core_import_survives_seeding_failure", test_export_core_import_survives_seeding_failure)
    check("iter_project_dirs_skips_underscore_in_user_dir", test_iter_project_dirs_skips_underscore_in_user_dir)
    check("no_match_error_names_user_location", test_no_match_error_names_user_location)
    check("choke_points_all_use_project_config_paths", test_choke_points_all_use_project_config_paths)
    check("real_packaged_template_is_underscore_prefixed_and_seeds", test_real_packaged_template_is_underscore_prefixed_and_seeds)
    if FAILURES:
        print(f"PROJECT CONFIG TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("PROJECT CONFIG TESTS: OK")


if __name__ == "__main__":
    main()
