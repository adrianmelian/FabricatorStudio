"""project_config_resolve tests (pure filesystem Python; no Maya session
required). Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/_dev/test_project_setup_resolve.py

MAYA_APP_DIR is monkeypatched per-test to a temp dir (helper copied from
test_project_setup_io.py) so nothing here ever touches the real user maya
folder. The _demo_authored / _v1_files fixtures are deliberate local
copies of the io suite's (same convention as the templates suite's local
mirror): each suite stays runnable standalone."""
import contextlib
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


def _demo_authored():
    return {
        "name": "Demo Project", "engine": "unreal5",
        "linear_unit": "cm", "angular_unit": "deg", "frame_rate": "ntsc",
        "playback_start": 1, "playback_end": 60,
        "undo_depth": 200,
        "camera_near_clip": 1.0, "camera_far_clip": 1000000.0,
        "grid": {"size": 1200, "spacing": 100, "divisions": 10},
        "blueprints_subpath": "",
        "asset_classes": [
            {"id": "character", "label": "Character",
             "export_types": ["skeletal_mesh"],
             "source_dir": "SourceArt/Characters/{name}",
             "dest_dir": "Game/Content/Characters/{name}",
             "anim": {"gameplay": "Game/Content/Characters/{name}/Animations",
                      "cinematic": ""}},
        ],
        "prefixes": {"static_mesh": "SM_", "skeletal_mesh": "SK_"},
        "anim_prefix": "A_",
        "pose_library_subpath": "SourceArt/_pose",
        "anim_library_subpath": "SourceArt/_anim",
        "mesh_group_name": "geo_grp",
        "engine_up_axis": "z",
        "fbx_presets": {"static_mesh": {}, "skeletal_mesh": {}, "animation": {}},
    }


def _v1_files(root, slug="old_project"):
    """Hand-write a v1 pair on disk (bypasses the new save path on purpose)."""
    d = root / slug
    d.mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({
        "name": "Old Project", "engine": "unreal5", "linear_unit": "cm",
        "angular_unit": "deg", "frame_rate": "ntsc", "playback_start": 1,
        "playback_end": 60, "undo_depth": 200, "camera_near_clip": 1.0,
        "camera_far_clip": 10000.0, "grid": {},
        "blueprints_dir": "C:/Old/Blueprints"}), encoding="utf-8")
    (d / "export_mapping.json").write_text(json.dumps({
        "schema_version": 1, "name": "Old Project", "project_root": "C:/Old",
        "asset_classes": [{"id": "character", "label": "Character",
                           "export_types": ["skeletal_mesh"],
                           "source_dir": "SourceArt/Characters/{name}",
                           "dest_dir": "Game/Content/Characters/{name}",
                           "anim": {"gameplay": "", "cinematic": ""}}],
        "path_patterns": [], "anim_path_patterns": {"gameplay": [], "cinematic": []},
        "prefixes": {"static_mesh": "SM_", "skeletal_mesh": "SK_"},
        "anim_prefix": "A_",
        "pose_library_root": "C:/Old/SourceArt/_pose",
        "anim_library_root": "D:/Elsewhere/anims",
        "mesh_group_name": "geo_grp",
        "engine_up_axis": "z",
        "fbx_presets": {"static_mesh": {"FBXExportUpAxis": "z"},
                        "skeletal_mesh": {"FBXExportUpAxis": "z"},
                        "animation": {"FBXExportUpAxis": "z"}}}), encoding="utf-8")
    return d


def test_bindings_round_trip_and_location():
    from maya_tools.framework import project_config_resolve as pcr
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            b = {"source_art_root": "D:/Art", "content_root": "D:/Game",
                 "pose_library_root_override": "", "anim_library_root_override": "",
                 "blueprints_dir_override": ""}
            pcr.save_bindings("demo_project", b)
            path = pcp.local_configs_dir() / "_user" / "demo_project.json"
            assert path.is_file()                      # per-project file, local, _user/
            assert pcr.load_bindings("demo_project") == b
            assert pcr.load_bindings("never_saved") == {}


def test_bindings_stay_local_when_configs_root_shared():
    from maya_tools.framework import project_config_resolve as pcr
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            os.environ["FABRICATOR_PROJECT_CONFIGS"] = str(Path(td) / "shared")
            try:
                pcr.save_bindings("team_proj", {"source_art_root": "D:/A",
                                                "content_root": "D:/B"})
                assert (pcp.local_configs_dir() / "_user" / "team_proj.json").is_file()
                assert not (Path(td) / "shared" / "_user").exists()   # NEVER in the shared tree
            finally:
                del os.environ["FABRICATOR_PROJECT_CONFIGS"]


def test_resolve_project_merges_tiers():
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_resolve as pcr
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            pio.save_project(_demo_authored())
            pcr.save_bindings("demo_project", {
                "source_art_root": "D:/Art", "content_root": "E:/Game",
                "pose_library_root_override": "",
                "anim_library_root_override": "F:/SharedAnims",
                "blueprints_dir_override": ""})
            r = pcr.resolve_project("demo_project")
            assert r["source_art_root"] == "D:/Art" and r["content_root"] == "E:/Game"
            assert r["pose_library_root"] == "D:/Art/SourceArt/_pose"   # subpath under source art
            assert r["anim_library_root"] == "F:/SharedAnims"           # override wins
            assert r["blueprints_dir"] == ""                            # blank subpath, no override
            assert r["mesh_group_name"] == "geo_grp"                    # project fields ride along
            assert r["engine_up_axis"] == "z"
            assert r["slug"] == "demo_project"                          # identity for consumers
            # generated patterns ride the resolved view (asset_classes is
            # truth; export_core matches/routes off these)
            assert r["path_patterns"] and "match" in r["path_patterns"][0]
            assert r["anim_path_patterns"]["gameplay"][0]["dest"] == \
                "Game/Content/Characters/{name}/Animations"


def test_resolve_project_v1_derives_bindings():
    from maya_tools.framework import project_config_resolve as pcr
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            _v1_files(pcp.configs_dir())
            r = pcr.resolve_project("old_project")
            assert r["source_art_root"] == "C:/Old" and r["content_root"] == "C:/Old"
            assert r["pose_library_root"].replace("\\", "/") == "C:/Old/SourceArt/_pose"
            assert r["anim_library_root"] == "D:/Elsewhere/anims"


def test_resolve_project_unbound_raises_actionable():
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_resolve as pcr
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            pio.save_project(_demo_authored())    # v2 config, no bindings, no v1 to derive
            raised = ""
            try:
                pcr.resolve_project("demo_project")
            except pcr.UnboundProjectError as exc:
                raised = str(exc)
            assert "demo_project.json" in raised
            assert "_user" in raised.replace("\\", "/")


def test_resolve_all_skips_unbound():
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_resolve as pcr
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            pio.save_project(_demo_authored())
            b = dict(_demo_authored())
            b["name"] = "Bound Project"
            pio.save_project(b)
            pcr.save_bindings("bound_project", {"source_art_root": "D:/A",
                                                "content_root": "D:/B"})
            resolved = pcr.resolve_all()
            assert [r["name"] for r in resolved] == ["Bound Project"]   # unbound demo skipped


def test_load_bindings_non_dict_json_reads_as_unbound():
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_resolve as pcr
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            pio.save_project(_demo_authored())
            path = pcr.user_dir() / "demo_project.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            for payload in ('"D:/Art"', '[1]', 'true'):
                path.write_text(payload, encoding="utf-8")
                assert pcr.load_bindings("demo_project") == {}, payload
                raised = False
                try:
                    pcr.resolve_project("demo_project")
                except pcr.UnboundProjectError:
                    raised = True
                assert raised, payload      # actionable error, never AttributeError


def test_saved_bindings_win_over_v1_derive():
    from maya_tools.framework import project_config_resolve as pcr
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            _v1_files(pcp.configs_dir())                     # derivable v1 pair on disk
            pcr.save_bindings("old_project", {"source_art_root": "D:/NewArt",
                                              "content_root": "D:/NewGame"})
            r = pcr.resolve_project("old_project")
            assert r["source_art_root"] == "D:/NewArt"       # saved bindings, not C:/Old
            assert r["content_root"] == "D:/NewGame"


def test_resolve_all_survives_corrupt_neighbor():
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_resolve as pcr
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            good = _demo_authored()
            good["name"] = "Bound Project"
            pio.save_project(good)
            pcr.save_bindings("bound_project", {"source_art_root": "D:/A",
                                                "content_root": "D:/B"})
            broken = pcp.configs_dir() / "broken_project"
            broken.mkdir(parents=True)
            (broken / "session.json").write_text('{"name": "Broken"}', encoding="utf-8")
            (broken / "export_mapping.json").write_text("{not json", encoding="utf-8")
            resolved = pcr.resolve_all()                     # must not raise
            assert [r["name"] for r in resolved] == ["Bound Project"]


def test_resolve_legacy_config_falls_back_to_persisted_patterns():
    """A pre-asset-classes v1 config (the real 2026 archangel shape: no
    asset_classes key, hand-authored path_patterns with legacy group
    names) must keep matching/routing via its persisted patterns."""
    from maya_tools.framework import project_config_resolve as pcr
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            d = pcp.configs_dir() / "legacy_proj"
            d.mkdir(parents=True)
            (d / "session.json").write_text(json.dumps({
                "name": "Legacy", "engine": "unreal5"}), encoding="utf-8")
            (d / "export_mapping.json").write_text(json.dumps({
                "name": "Legacy", "project_root": "C:/Legacy",
                "path_patterns": [
                    {"match": r"SourceArt/Characters/(?P<asset>[^/]+)/.*\.ma$",
                     "dest": "Game/Content/Characters/{asset}"}],
                "anim_path_patterns": {"gameplay": [
                    {"match": r"SourceArt/Characters/(?P<asset>[^/]+)/.*\.ma$",
                     "dest": "Game/Content/Characters/{asset}/Animations"}],
                    "cinematic": []},
                "prefixes": {"static_mesh": "SM_", "skeletal_mesh": "SK_"},
                "anim_prefix": "A_",
                "fbx_presets": {"static_mesh": {}, "skeletal_mesh": {},
                                "animation": {}}}), encoding="utf-8")
            r = pcr.resolve_project("legacy_proj")
            assert r["source_art_root"] == "C:/Legacy"      # derive still works
            assert r["path_patterns"][0]["match"] == \
                r"SourceArt/Characters/(?P<asset>[^/]+)/.*\.ma$"
            assert r["anim_path_patterns"]["gameplay"][0]["dest"] == \
                "Game/Content/Characters/{asset}/Animations"


def test_settings_slug_is_reserved():
    from maya_tools.framework import project_config_resolve as pcr
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            settings = pcp.local_configs_dir() / "_user" / "settings.json"
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps({"configs_root": str(Path(td) / "shared")}),
                                encoding="utf-8")
            # the pointer file must never read back as project bindings
            assert pcr.load_bindings("settings") == {}
            raised = False
            try:
                pcr.save_bindings("settings", {"source_art_root": "D:/A",
                                               "content_root": "D:/B"})
            except ValueError:
                raised = True
            assert raised                                     # and never be overwritten
            assert json.loads(settings.read_text(encoding="utf-8"))["configs_root"]


def main():
    check("bindings/round_trip_and_location", test_bindings_round_trip_and_location)
    check("bindings/non_dict_json_reads_as_unbound", test_load_bindings_non_dict_json_reads_as_unbound)
    check("bindings/saved_win_over_v1_derive", test_saved_bindings_win_over_v1_derive)
    check("bindings/settings_slug_is_reserved", test_settings_slug_is_reserved)
    check("resolve_project/legacy_persisted_patterns_fallback", test_resolve_legacy_config_falls_back_to_persisted_patterns)
    check("resolve_all/survives_corrupt_neighbor", test_resolve_all_survives_corrupt_neighbor)
    check("bindings/stay_local_when_configs_root_shared", test_bindings_stay_local_when_configs_root_shared)
    check("resolve_project/merges_tiers", test_resolve_project_merges_tiers)
    check("resolve_project/v1_derives_bindings", test_resolve_project_v1_derives_bindings)
    check("resolve_project/unbound_raises_actionable", test_resolve_project_unbound_raises_actionable)
    check("resolve_all/skips_unbound", test_resolve_all_skips_unbound)
    print(f"\n{len(FAILURES)} failure(s).")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
