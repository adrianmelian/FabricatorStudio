"""project_config_io tests (pure filesystem Python; no Maya session
required). Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/_dev/test_project_setup_io.py

MAYA_APP_DIR is monkeypatched per-test to a temp dir (see maya_app_dir()
below, copied from test_project_configs.py) so nothing here ever touches
the real user maya folder. project_config_io has no cached globals, so no
importlib.reload is needed here - configs_dir() is resolved fresh on
every call."""
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


def test_preflight_config_generator_available():
    """Not a project_config_io test: a precondition. Plan 3 composes
    config_generator.generate_export_mapping (Plan 1) inside split_authored,
    and this plan assumes sequential execution after Plan 1 has already
    landed on feature/project-setup-tool. Fail loudly and specifically here
    - 'Plan 1 not on the branch yet' - rather than letting every later
    task's check read as a project_config_io bug."""
    from maya_tools.framework import config_generator as cg
    assert hasattr(cg, "generate_export_mapping"), (
        "config_generator.generate_export_mapping not found - Plan 1 must "
        "land on feature/project-setup-tool before Plan 3 can proceed."
    )


def test_session_fields():
    from maya_tools.framework import project_config_io as pio
    assert pio.SESSION_FIELDS == (
        "name", "engine",
        "linear_unit", "angular_unit", "frame_rate",
        "playback_start", "playback_end",
        "undo_depth",
        "camera_near_clip", "camera_far_clip",
        "grid", "blueprints_subpath",
        "orient_convention",          # t48: mirror convention default
    )


def test_slugify():
    """slugify must match config_validation._slugify EXACTLY (same regex,
    same "never strip/dedupe a leading underscore, no empty-string
    fallback" behavior) - both compute the project IDENTITY slug, and any
    divergence would make the validator's slug-collision check
    (config_validation._validate_identity) disagree with the actual
    on-disk folder name project_config_io creates for the same name. See
    config_validation._slugify's own docstring: "Local mirror of the
    future project_config_io.slugify (Plan 3) ... Deliberately does NOT
    strip a leading '_' -- that is exactly what _validate_identity checks
    for". A name that folds to a leading-'_' or empty slug is rejected
    upstream by config_validation.validate_config before save_project is
    ever called, and save_project()'s own leading-'_' guard (Task 4) is a
    second line of defense - so slugify() itself stays "dumb": no
    stripping, no deduping, no reserved-word fallback."""
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import config_validation as cv
    assert pio.slugify("Archangel") == "archangel"
    assert pio.slugify("My Cool Project!") == "my_cool_project_"
    assert pio.slugify("  spaced  ") == "spaced"
    assert pio.slugify("__leading_underscores") == "__leading_underscores"
    assert pio.slugify("_Anything_") == "_anything_"
    assert pio.slugify("") == ""
    assert pio.slugify("___") == "___"
    for sample in ("Archangel", "My Cool Project!", "  spaced  ",
                   "__leading_underscores", "_Anything_", "___", "",
                   "Robo-Bunny 3000", "unreal5", "Totally Renamed Project"):
        assert pio.slugify(sample) == cv._slugify(sample), sample


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
        "orient_convention": "standard",
        "fbx_presets": {"static_mesh": {}, "skeletal_mesh": {}, "animation": {}},
    }


def test_generate_export_mapping_v2_shape():
    from maya_tools.framework import config_generator as cg
    out = cg.generate_export_mapping(_demo_authored())
    assert out["schema_version"] == 2
    assert "project_root" not in out
    assert out["pose_library_subpath"] == "SourceArt/_pose"
    assert out["anim_library_subpath"] == "SourceArt/_anim"
    assert "pose_library_root" not in out and "anim_library_root" not in out


def test_split_authored():
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import config_generator as cg
    authored = _demo_authored()
    session, export_mapping = pio.split_authored(authored)
    assert session == {field: authored.get(field) for field in pio.SESSION_FIELDS}
    assert session["name"] == "Demo Project"
    assert session["engine"] == "unreal5"
    assert export_mapping == cg.generate_export_mapping(authored)
    assert export_mapping["asset_classes"] == authored["asset_classes"]


def test_save_project_create_writes_atomic_pair():
    from maya_tools.framework import project_config_io as pio
    authored = _demo_authored()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            target = pio.save_project(authored)
            assert target.name == "demo_project"
            session_path = target / "session.json"
            export_path = target / "export_mapping.json"
            assert session_path.is_file()
            assert export_path.is_file()
            session = json.loads(session_path.read_text(encoding="utf-8"))
            assert session["name"] == "Demo Project"
            assert session["engine"] == "unreal5"
            export_mapping = json.loads(export_path.read_text(encoding="utf-8"))
            assert export_mapping["asset_classes"] == authored["asset_classes"]
            assert export_mapping["path_patterns"], "derived path_patterns must be generated"
            # staging left no debris behind in the configs root
            root = Path(td) / "fabricator_project_configs"
            leftovers = [p.name for p in root.iterdir() if p.name.startswith("_stage-")]
            assert leftovers == []


def test_save_project_never_overwrites_on_create():
    from maya_tools.framework import project_config_io as pio
    authored = _demo_authored()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            pio.save_project(authored)
            raised = False
            try:
                pio.save_project(authored)
            except FileExistsError:
                raised = True
            assert raised, "second create with the same name must raise FileExistsError"


def test_save_project_overwrite_edits_in_place():
    from maya_tools.framework import project_config_io as pio
    authored = _demo_authored()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            target = pio.save_project(authored)
            edited = dict(authored)
            edited["undo_depth"] = 500
            edited["playback_end"] = 120
            result = pio.save_project(edited, overwrite=True)
            assert result == target
            session = json.loads((target / "session.json").read_text(encoding="utf-8"))
            assert session["undo_depth"] == 500
            assert session["playback_end"] == 120


def test_save_project_read_only_target_raises_actionable_error():
    from maya_tools.framework import project_config_io as pio
    authored = _demo_authored()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            target = pio.save_project(authored)
            session_path = target / "session.json"
            os.chmod(session_path, stat.S_IREAD)
            try:
                raised = False
                try:
                    pio.save_project(authored, overwrite=True)
                except pio.ReadOnlyConfigError as exc:
                    raised = True
                    msg = str(exc).lower()
                    assert "perforce" in msg or "read-only" in msg
                assert raised, "a read-only session.json must raise ReadOnlyConfigError"
                assert isinstance(pio.ReadOnlyConfigError(""), OSError)
            finally:
                os.chmod(session_path, stat.S_IWRITE | stat.S_IREAD)


def test_save_project_edit_with_slug_pins_original_folder():
    """slug= PINS the target folder: an edit that changes the cosmetic
    name must still land on the ORIGINAL slug's directory, never create a
    second, orphaned folder alongside it. Identity is the slug, not the
    'name' field."""
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_paths
    authored = _demo_authored()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            target = pio.save_project(authored)
            original_slug = target.name
            assert original_slug == "demo_project"

            renamed = dict(authored)
            renamed["name"] = "Totally Renamed Project"
            result = pio.save_project(renamed, overwrite=True, slug=original_slug)

            assert result == target, "passing the original slug must overwrite the original folder"
            root = project_config_paths.configs_dir()
            surviving = sorted(p.name for p in root.iterdir() if not p.name.startswith("_"))
            assert surviving == [original_slug], (
                "the rename must not orphan a new folder alongside the original"
            )
            session = json.loads((target / "session.json").read_text(encoding="utf-8"))
            assert session["name"] == "Totally Renamed Project"


def test_save_project_rejects_underscore_prefixed_slug():
    """slug= is caller-supplied and must not be able to target a reserved
    '_'-prefixed dir (template or _trash) - that would silently corrupt
    packaged template data or the trash archive."""
    from maya_tools.framework import project_config_io as pio
    authored = _demo_authored()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            raised = False
            try:
                pio.save_project(authored, slug="_unreal5")
            except ValueError:
                raised = True
            assert raised, "a leading-underscore slug is reserved and must be rejected"


def test_save_project_read_only_configs_root_raises_on_create():
    """Read-only guard on the CREATE path (never-overwrite branch), to
    match the read-only guard already proven on the overwrite/edit path
    above - both paths promise ReadOnlyConfigError, never a raw
    PermissionError.

    Windows note: unlike a FILE's read-only attribute (which Windows DOES
    enforce - see test_save_project_read_only_target_raises_actionable_error
    above), a DIRECTORY's read-only FILE_ATTRIBUTE (what os.chmod toggles
    via S_IWRITE) is pure metadata on NTFS and does NOT block creating
    files/subdirs inside it (verified empirically:
    os.chmod(a_dir, stat.S_IREAD) does not stop
    tempfile.mkdtemp(dir=a_dir) from succeeding) - so a read-only CONFIGS
    ROOT can't be simulated with chmod alone on this platform. This test
    instead monkeypatches tempfile.mkdtemp - the exact primitive
    _create_atomic calls to stage inside the configs root - to raise
    PermissionError directly, proving save_project's create path converts
    that into the actionable ReadOnlyConfigError exactly as it would for a
    genuinely read-only root on a filesystem/OS that does enforce
    directory write permissions."""
    from maya_tools.framework import project_config_io as pio
    authored = _demo_authored()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            original_mkdtemp = tempfile.mkdtemp

            def _denied(*args, **kwargs):
                raise PermissionError(13, "Permission denied (simulated)")

            tempfile.mkdtemp = _denied
            try:
                raised = False
                try:
                    pio.save_project(authored)
                except pio.ReadOnlyConfigError as exc:
                    raised = True
                    msg = str(exc).lower()
                    assert "perforce" in msg or "read-only" in msg
                assert raised, "a read-only configs root must raise ReadOnlyConfigError on create"
            finally:
                tempfile.mkdtemp = original_mkdtemp


def test_load_project_round_trips_authored_shape():
    from maya_tools.framework import project_config_io as pio
    authored = _demo_authored()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            pio.save_project(authored)
            loaded = pio.load_project("demo_project")
            # full-dict equality IS the key-set net (the mesh_group_name
            # wipe lesson, 2026-07-18): any union field the loader forgets,
            # or any extra it leaks, fails loudly here.
            assert loaded == authored, (loaded, authored)
            # generated-only keys are NOT part of the authored union
            assert "path_patterns" not in loaded
            assert "anim_path_patterns" not in loaded
            assert "schema_version" not in loaded


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


def test_load_project_upgrades_v1_shape():
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            _v1_files(pcp.configs_dir())
            loaded = pio.load_project("old_project")
            assert "project_root" not in loaded
            assert loaded["pose_library_subpath"] == "SourceArt/_pose"   # was under root -> relative
            assert loaded["anim_library_subpath"] == ""                  # off-root -> override, not subpath
            assert loaded["blueprints_subpath"] == "Blueprints"          # was under root -> relative
            assert loaded["mesh_group_name"] == "geo_grp"
            assert set(loaded) == set(_demo_authored())                  # exact v2 key set


def test_derive_v1_bindings():
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            _v1_files(pcp.configs_dir())
            b = pio.derive_v1_bindings("old_project")
            assert b["source_art_root"] == "C:/Old" and b["content_root"] == "C:/Old"
            assert b["anim_library_root_override"] == "D:/Elsewhere/anims"
            assert b["pose_library_root_override"] == ""
            assert b["blueprints_dir_override"] == ""


def test_rel_under_boundaries():
    from maya_tools.framework import project_config_io as pio
    assert pio._rel_under("C:/Old/Sub", "C:/Old") == "Sub"
    assert pio._rel_under("C:/OldStuff/poses", "C:/Old") is None   # sibling string-prefix
    assert pio._rel_under("C:/Old", "C:/Old") is None              # root itself
    assert pio._rel_under("", "C:/Old") is None
    assert pio._rel_under("C:/Old/Sub", "") is None


def test_derive_v1_bindings_relative_root_returns_none():
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            d = _v1_files(pcp.configs_dir())
            export_path = d / "export_mapping.json"
            export = json.loads(export_path.read_text(encoding="utf-8"))
            export["project_root"] = "UnrealProjects/Game"   # hand-edited relative root
            export_path.write_text(json.dumps(export), encoding="utf-8")
            assert pio.derive_v1_bindings("old_project") is None   # unbound, not garbage


def test_derive_v1_bindings_pose_and_blueprints_override_branches():
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            d = _v1_files(pcp.configs_dir())
            export_path = d / "export_mapping.json"
            export = json.loads(export_path.read_text(encoding="utf-8"))
            export["pose_library_root"] = "E:/PoseShare"     # off-root -> override
            export_path.write_text(json.dumps(export), encoding="utf-8")
            session_path = d / "session.json"
            session = json.loads(session_path.read_text(encoding="utf-8"))
            session["blueprints_dir"] = "F:/TeamBlueprints"  # off-root -> override
            session_path.write_text(json.dumps(session), encoding="utf-8")
            b = pio.derive_v1_bindings("old_project")
            assert b["pose_library_root_override"] == "E:/PoseShare"
            assert b["blueprints_dir_override"] == "F:/TeamBlueprints"


def test_upgrade_v1_mixed_pair_prefers_present_subpaths():
    """Crash window heal: v2 session.json beside a still-v1 export_mapping
    (the overwrite save writes them sequentially) must not wipe
    blueprints_subpath on the next load."""
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            d = _v1_files(pcp.configs_dir())
            session_path = d / "session.json"
            session = json.loads(session_path.read_text(encoding="utf-8"))
            del session["blueprints_dir"]                    # v2 session shape
            session["blueprints_subpath"] = "Blueprints"
            session_path.write_text(json.dumps(session), encoding="utf-8")
            loaded = pio.load_project("old_project")         # export still schema 1
            assert loaded["blueprints_subpath"] == "Blueprints"


def test_derive_v1_bindings_none_for_v2():
    from maya_tools.framework import project_config_io as pio
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            pio.save_project(_demo_authored())
            assert pio.derive_v1_bindings("demo_project") is None


def test_load_project_asset_classes_is_truth_not_persisted_patterns():
    from maya_tools.framework import project_config_io as pio
    authored = _demo_authored()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            target = pio.save_project(authored)
            # hand-corrupt the persisted derived path_patterns to prove the
            # loader ignores them and only trusts asset_classes
            export_path = target / "export_mapping.json"
            export_mapping = json.loads(export_path.read_text(encoding="utf-8"))
            export_mapping["path_patterns"] = [{"match": "BOGUS", "dest": "BOGUS"}]
            export_path.write_text(json.dumps(export_mapping), encoding="utf-8")
            loaded = pio.load_project("demo_project")
            assert loaded["asset_classes"] == authored["asset_classes"]


def test_load_project_missing_slug_raises():
    from maya_tools.framework import project_config_io as pio
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            raised = False
            try:
                pio.load_project("nope")
            except FileNotFoundError:
                raised = True
            assert raised


def test_list_projects_excludes_underscore_and_reports_shape():
    from maya_tools.framework import project_config_io as pio
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            a = dict(_demo_authored()); a["name"] = "Alpha"; a["engine"] = "unreal5"
            b = dict(_demo_authored()); b["name"] = "Beta"; b["engine"] = "unity"
            pio.save_project(a)
            pio.save_project(b)
            root = Path(td) / "fabricator_project_configs"
            (root / "_scratch").mkdir()   # a template/scratch dir, must be skipped
            listed = pio.list_projects()
            slugs = sorted(p["slug"] for p in listed)
            assert slugs == ["alpha", "beta"]
            by_slug = {p["slug"]: p for p in listed}
            assert by_slug["alpha"]["name"] == "Alpha"
            assert by_slug["alpha"]["engine"] == "unreal5"
            assert by_slug["beta"]["engine"] == "unity"


def test_list_projects_empty_when_no_configs_dir():
    from maya_tools.framework import project_config_io as pio
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            assert pio.list_projects() == []


def test_delete_project_soft_moves_to_trash():
    from maya_tools.framework import project_config_io as pio
    authored = _demo_authored()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            target = pio.save_project(authored)
            trashed = pio.delete_project("demo_project")
            assert not target.exists()
            assert trashed.exists()
            assert trashed.parent.name == "_trash"
            assert trashed.name.startswith("demo_project_")
            assert (trashed / "session.json").is_file()
            # soft-deleted projects are invisible to list_projects/load_project
            assert pio.list_projects() == []
            raised = False
            try:
                pio.load_project("demo_project")
            except FileNotFoundError:
                raised = True
            assert raised


def test_delete_project_moves_bindings_into_trash_bundle():
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_resolve as pcr
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            pio.save_project(_demo_authored())
            pcr.save_bindings("demo_project", {"source_art_root": "D:/A",
                                               "content_root": "D:/B"})
            dest = pio.delete_project("demo_project")
            assert (dest / "user_bindings.json").is_file()   # rode into the bundle
            assert pcr.load_bindings("demo_project") == {}   # gone from _user/


def test_delete_project_shared_root_keeps_bindings_out_of_shared_tree():
    """Team mode: the trash bundle lives in the SHARED tree, and machine
    bindings must never land there (P4/git would pick them up). They
    soft-delete into the local _user/_trash/ instead."""
    from maya_tools.framework import project_config_io as pio
    from maya_tools.framework import project_config_resolve as pcr
    from maya_tools.framework import project_config_paths as pcp
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            os.environ["FABRICATOR_PROJECT_CONFIGS"] = str(Path(td) / "shared")
            try:
                pio.save_project(_demo_authored())           # lands in the shared root
                pcr.save_bindings("demo_project", {"source_art_root": "D:/A",
                                                   "content_root": "D:/B"})
                dest = pio.delete_project("demo_project")
                assert str(dest).startswith(str(Path(td) / "shared"))
                assert not (dest / "user_bindings.json").exists()   # never in shared tree
                assert pcr.load_bindings("demo_project") == {}      # cleared locally
                local_trash = pcr.user_dir() / "_trash"
                assert local_trash.is_dir() and any(
                    p.name.startswith("demo_project_") for p in local_trash.iterdir())
            finally:
                del os.environ["FABRICATOR_PROJECT_CONFIGS"]


def test_delete_project_missing_slug_raises():
    from maya_tools.framework import project_config_io as pio
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            raised = False
            try:
                pio.delete_project("nope")
            except FileNotFoundError:
                raised = True
            assert raised


def test_delete_project_read_only_configs_root_raises_actionable_error():
    """Read-only guard on the DELETE path, to match the docstring's
    ReadOnlyConfigError promise on both the save AND delete paths. The
    os.rename(target, dest) inside delete_project is what needs write
    permission on the configs root, and is what must surface
    ReadOnlyConfigError instead of a raw PermissionError.

    Windows note: same platform limitation as
    test_save_project_read_only_configs_root_raises_on_create above - a
    DIRECTORY's read-only FILE_ATTRIBUTE (os.chmod's S_IWRITE bit) is pure
    metadata on NTFS and does NOT block renaming an entry out of that
    directory (verified empirically alongside the mkdtemp case). So this
    test monkeypatches os.rename - the exact primitive delete_project
    calls - to raise PermissionError directly, proving delete_project
    converts that into the actionable ReadOnlyConfigError exactly as it
    would for a genuinely read-only root."""
    from maya_tools.framework import project_config_io as pio
    authored = _demo_authored()
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            pio.save_project(authored)
            original_rename = os.rename

            def _denied(*args, **kwargs):
                raise PermissionError(13, "Permission denied (simulated)")

            os.rename = _denied
            try:
                raised = False
                try:
                    pio.delete_project("demo_project")
                except pio.ReadOnlyConfigError as exc:
                    raised = True
                    msg = str(exc).lower()
                    assert "perforce" in msg or "read-only" in msg
                assert raised, "a read-only configs root must raise ReadOnlyConfigError on delete"
            finally:
                os.rename = original_rename


@contextlib.contextmanager
def packaged_templates_dir(path):
    """Point project_config_paths.packaged_templates_dir() at `path` for
    the duration; restore on exit. That function takes no override param,
    so this monkeypatches the module attribute itself - project_config_io
    calls it as `project_config_paths.packaged_templates_dir()` (through
    the module, not a bound import), so the patch reaches it with no
    importlib.reload needed."""
    from maya_tools.framework import project_config_paths as pcp
    original = pcp.packaged_templates_dir
    pcp.packaged_templates_dir = lambda: Path(path)
    try:
        yield Path(path)
    finally:
        pcp.packaged_templates_dir = original


def _write_template(root, template_id, *, engine_label=None,
                     name="Should Never Load",
                     project_root="C:/ShouldNeverLoad"):
    """Build one packaged template dir (root/_<template_id>/{session,
    export_mapping}.json) from _demo_authored(), for list_templates/
    load_template tests only - never used by the real in-tree templates."""
    from maya_tools.framework import project_config_io as pio
    authored = _demo_authored()
    authored["name"] = name
    authored["project_root"] = project_root
    if engine_label is not None:
        authored["engine"] = engine_label
    session, export_mapping = pio.split_authored(authored)
    target = Path(root) / f"_{template_id}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "session.json").write_text(json.dumps(session), encoding="utf-8")
    (target / "export_mapping.json").write_text(json.dumps(export_mapping), encoding="utf-8")
    return target


def test_list_templates_reads_underscore_dirs_and_reports_shape():
    with tempfile.TemporaryDirectory() as td:
        _write_template(td, "unreal5", engine_label="Unreal 5")
        _write_template(td, "unity", engine_label="Unity")
        (Path(td) / "archangel").mkdir()     # legacy non-underscore config: skip
        (Path(td) / "__pycache__").mkdir()   # never a template: skip
        with packaged_templates_dir(td):
            from maya_tools.framework import project_config_io as pio
            listed = pio.list_templates()
            ids = sorted(t["id"] for t in listed)
            assert ids == ["unity", "unreal5"]
            by_id = {t["id"]: t for t in listed}
            assert by_id["unreal5"]["label"] == "Unreal 5"
            assert by_id["unity"]["label"] == "Unity"


def test_list_templates_empty_when_no_packaged_dir():
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "does_not_exist"
        with packaged_templates_dir(missing):
            from maya_tools.framework import project_config_io as pio
            assert pio.list_templates() == []


def test_load_template_returns_skeleton_with_blank_name_and_root():
    with tempfile.TemporaryDirectory() as td:
        _write_template(td, "unreal5", engine_label="Unreal 5",
                        name="Should Never Load",
                        project_root="C:/ShouldNeverLoad")
        with packaged_templates_dir(td):
            from maya_tools.framework import project_config_io as pio
            reference = _demo_authored()
            skeleton = pio.load_template("unreal5")
            assert skeleton["name"] == ""
            assert "project_root" not in skeleton      # v2: roots are user-tier
            assert skeleton["engine"] == "Unreal 5"
            assert skeleton["linear_unit"] == reference["linear_unit"]
            assert skeleton["asset_classes"] == reference["asset_classes"]
            assert skeleton["prefixes"] == reference["prefixes"]
            assert skeleton["fbx_presets"] == reference["fbx_presets"]


def test_load_template_missing_id_raises():
    with tempfile.TemporaryDirectory() as td:
        with packaged_templates_dir(td):
            from maya_tools.framework import project_config_io as pio
            raised = False
            try:
                pio.load_template("nope")
            except FileNotFoundError:
                raised = True
            assert raised


def run():
    check("preflight/config_generator_available", test_preflight_config_generator_available)
    check("SESSION_FIELDS", test_session_fields)
    check("slugify", test_slugify)
    check("split_authored", test_split_authored)
    check("generate_export_mapping_v2_shape", test_generate_export_mapping_v2_shape)
    check("save_project/create_writes_atomic_pair", test_save_project_create_writes_atomic_pair)
    check("save_project/never_overwrites_on_create", test_save_project_never_overwrites_on_create)
    check("save_project/overwrite_edits_in_place", test_save_project_overwrite_edits_in_place)
    check("save_project/read_only_target_raises_actionable_error", test_save_project_read_only_target_raises_actionable_error)
    check("save_project/edit_with_slug_pins_original_folder", test_save_project_edit_with_slug_pins_original_folder)
    check("save_project/rejects_underscore_prefixed_slug", test_save_project_rejects_underscore_prefixed_slug)
    check("save_project/read_only_configs_root_raises_on_create", test_save_project_read_only_configs_root_raises_on_create)
    check("load_project/round_trips_authored_shape", test_load_project_round_trips_authored_shape)
    check("load_project/upgrades_v1_shape", test_load_project_upgrades_v1_shape)
    check("load_project/mixed_pair_prefers_present_subpaths", test_upgrade_v1_mixed_pair_prefers_present_subpaths)
    check("rel_under/boundaries", test_rel_under_boundaries)
    check("derive_v1_bindings/basic", test_derive_v1_bindings)
    check("derive_v1_bindings/none_for_v2", test_derive_v1_bindings_none_for_v2)
    check("derive_v1_bindings/relative_root_returns_none", test_derive_v1_bindings_relative_root_returns_none)
    check("derive_v1_bindings/pose_and_blueprints_override_branches", test_derive_v1_bindings_pose_and_blueprints_override_branches)
    check("load_project/asset_classes_is_truth_not_persisted_patterns", test_load_project_asset_classes_is_truth_not_persisted_patterns)
    check("load_project/missing_slug_raises", test_load_project_missing_slug_raises)
    check("list_projects/excludes_underscore_and_reports_shape", test_list_projects_excludes_underscore_and_reports_shape)
    check("list_projects/empty_when_no_configs_dir", test_list_projects_empty_when_no_configs_dir)
    check("delete_project/soft_moves_to_trash", test_delete_project_soft_moves_to_trash)
    check("delete_project/moves_bindings_into_trash_bundle", test_delete_project_moves_bindings_into_trash_bundle)
    check("delete_project/shared_root_keeps_bindings_out_of_shared_tree", test_delete_project_shared_root_keeps_bindings_out_of_shared_tree)
    check("delete_project/missing_slug_raises", test_delete_project_missing_slug_raises)
    check("delete_project/read_only_configs_root_raises_actionable_error", test_delete_project_read_only_configs_root_raises_actionable_error)
    check("list_templates/reads_underscore_dirs_and_reports_shape", test_list_templates_reads_underscore_dirs_and_reports_shape)
    check("list_templates/empty_when_no_packaged_dir", test_list_templates_empty_when_no_packaged_dir)
    check("load_template/returns_skeleton_with_blank_name_and_root", test_load_template_returns_skeleton_with_blank_name_and_root)
    check("load_template/missing_id_raises", test_load_template_missing_id_raises)
    # further checks are appended by each task below


if __name__ == "__main__":
    run()
    print(f"\n{len(FAILURES)} failure(s).")
    sys.exit(1 if FAILURES else 0)
