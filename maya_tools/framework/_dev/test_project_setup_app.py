"""project_setup_app tests. Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/_dev/test_project_setup_app.py

project_setup_app itself imports ONLY config_validation, project_config_io,
and project_scaffold at module top -- all pure, zero maya/Qt. maya_startup
and toolbar_app import maya.cmds / maya.OpenMayaUI at THEIR module top and
are not offscreen-importable, so the contract's binding lazy-import rule
applies: project_setup_app imports them LAZILY, inside apply_and_activate()
only, never at its own module top. That keeps `import
maya_tools.framework.project_setup_app` itself -- and the Plan 7 UI that
imports it -- safe under bare mayapy with no maya.standalone.initialize(),
BY CONSTRUCTION (Task 6 adds a smoke check that pins this). The one test
that reaches apply_and_activate (Task 11) imports maya_startup directly in
the TEST (a plain KS-suite bare-mayapy import, same convention used
throughout the KS test suite) purely so it can monkeypatch
maya_startup.run to a spy before calling apply_and_activate -- the lazy
import inside apply_and_activate resolves the same cached module object,
so the patched attribute is what actually runs; no live Maya session is
ever touched.

MAYA_APP_DIR is monkeypatched per-test to a temp dir. Every test imports
project_setup_app INSIDE its own `with maya_app_dir(...)` block. Because
Python caches modules after the first import, later tests reuse the same
module object; that is safe here because every project_config_io /
project_scaffold / config_validation / toolbar_app call resolves its root
LIVE (via project_config_paths) on every call."""
import contextlib
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


def test_project_setup_app_imports_without_maya_session():
    """project_setup_app must import cleanly under bare mayapy with no
    maya.standalone.initialize() and no live Maya session -- the
    contract's binding lazy-import rule (maya_startup / toolbar_app
    imported ONLY inside apply_and_activate, never at project_setup_app
    module top) is what makes this possible, and is what keeps this
    whole suite -- and Plan 7's UI, which imports project_setup_app at
    module scope -- offscreen-safe. This check must run before any test
    that calls apply_and_activate (Task 11), so it can assert
    maya_startup has not yet been imported at all."""
    for name in (
        "maya_tools.framework.maya_startup",
        "maya_tools.framework.toolbar.toolbar_app",
    ):
        assert name not in sys.modules, (
            f"{name} must not be imported before apply_and_activate() runs")
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            assert not hasattr(psa, "maya_startup"), (
                "maya_startup must be imported LAZILY inside "
                "apply_and_activate(), never at project_setup_app module top")
            assert not hasattr(psa, "toolbar_app"), (
                "toolbar_app must be imported LAZILY inside "
                "apply_and_activate(), never at project_setup_app module top")


def _valid_authored(name="Demo"):
    """A fully valid v2 authored config (spec sec 3/5) - project tier
    only; machine roots live in _valid_bindings."""
    return {
        "name": name,
        "engine": "unreal5",
        "linear_unit": "cm",
        "angular_unit": "deg",
        "frame_rate": "ntsc",
        "playback_start": 1,
        "playback_end": 100,
        "undo_depth": 200,
        "camera_near_clip": 1.0,
        "camera_far_clip": 1000000.0,
        "grid": {"size": 12, "spacing": 5, "divisions": 5},
        "blueprints_subpath": "",
        "asset_classes": [
            {"id": "character", "label": "Character",
             "export_types": ["skeletal_mesh", "static_mesh"],
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
        "fbx_presets": {
            "static_mesh": {"FBXExportUpAxis": "y"},
            "skeletal_mesh": {"FBXExportUpAxis": "y"},
            "animation": {"FBXExportUpAxis": "y"},
        },
    }


def _valid_bindings(project_root):
    """Machine bindings rooted at an existing temp dir (browse-confirmed)."""
    return {"source_art_root": str(Path(project_root)),
            "content_root": str(Path(project_root)),
            "pose_library_root_override": "",
            "anim_library_root_override": "",
            "blueprints_dir_override": ""}


def test_validation_error_carries_issues():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            from maya_tools.framework.config_validation import Issue
            issues = [Issue("error", "project_root", "project_root is required")]
            exc = psa.ValidationError(issues)
            assert exc.issues == issues
            assert "project_root is required" in str(exc)


def test_create_project_raises_on_invalid():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            from maya_tools.framework.config_validation import Issue

            real_validate = psa.config_validation.validate_config
            calls = []

            def fake_validate(authored, existing_projects=None):
                calls.append((authored, existing_projects))
                return [Issue("error", "project_root", "project_root is required")]

            psa.config_validation.validate_config = fake_validate
            try:
                raised = False
                try:
                    psa.create_project({"name": "Nope"}, {})
                except psa.ValidationError as exc:
                    raised = True
                    assert Issue("error", "project_root", "project_root is required") in exc.issues
                assert raised, "expected ValidationError"
                assert len(calls) == 1
            finally:
                psa.config_validation.validate_config = real_validate


def test_create_project_saves_and_round_trips():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            from maya_tools.framework import project_config_io as pcio
            with tempfile.TemporaryDirectory() as project_root:
                authored = _valid_authored(name="Demo")
                saved_path = psa.create_project(authored, _valid_bindings(project_root))
                assert Path(saved_path).is_dir()
                slug = pcio.slugify("Demo")
                loaded = pcio.load_project(slug)
                assert loaded == authored          # full v2 round trip
                from maya_tools.framework import project_config_resolve as pcr
                assert pcr.load_bindings(slug)["source_art_root"] == str(Path(project_root))


def test_create_project_rejects_duplicate_name_via_validation():
    # config_validation's own existing_projects slug-collision check (spec
    # sec 4.3: "collision blocks") fires BEFORE project_config_io.save_project
    # would ever raise FileExistsError -- this is the path a real caller hits.
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            with tempfile.TemporaryDirectory() as project_root:
                psa.create_project(_valid_authored(name="Demo"), _valid_bindings(project_root))
                raised = False
                try:
                    psa.create_project(_valid_authored(name="Demo"), _valid_bindings(project_root))
                except psa.ValidationError:
                    raised = True
                assert raised, "expected ValidationError for a duplicate project name/slug"


def test_create_project_succeeds_with_warnings_only():
    # spec sec 5 / contract: "errors block save, warnings confirm-through"
    # -- a validate_config result containing ONLY warning-severity Issues
    # must not raise; create_project must still save and return a Path.
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            from maya_tools.framework.config_validation import Issue

            real_validate = psa.config_validation.validate_config

            def fake_validate(authored, existing_projects=None):
                return [Issue("warning", "grid", "grid.size is unusually small")]

            psa.config_validation.validate_config = fake_validate
            try:
                with tempfile.TemporaryDirectory() as project_root:
                    result = psa.create_project(_valid_authored(name="Warned"), _valid_bindings(project_root))
                    assert isinstance(result, Path)
                    assert Path(result).is_dir()
            finally:
                psa.config_validation.validate_config = real_validate


def test_edit_project_saves_in_place():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            from maya_tools.framework import project_config_io as pcio
            with tempfile.TemporaryDirectory() as project_root:
                authored = _valid_authored(name="Demo")
                psa.create_project(authored, _valid_bindings(project_root))
                slug = pcio.slugify("Demo")
                updated = dict(authored)
                updated["undo_depth"] = 500
                psa.edit_project(slug, updated, _valid_bindings(project_root))
                loaded = pcio.load_project(slug)
                assert loaded["undo_depth"] == 500


def test_edit_project_rejects_identity_changing_rename():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            from maya_tools.framework import project_config_io as pcio
            with tempfile.TemporaryDirectory() as project_root:
                authored = _valid_authored(name="Demo")
                psa.create_project(authored, _valid_bindings(project_root))
                slug = pcio.slugify("Demo")
                renamed = dict(authored)
                renamed["name"] = "Totally Different"
                raised = False
                try:
                    psa.edit_project(slug, renamed, _valid_bindings(project_root))
                except psa.ValidationError as exc:
                    raised = True
                    assert exc.issues[0].field == "name"
                assert raised, "expected ValidationError for an identity-changing rename"
                # the ORIGINAL folder is untouched
                assert pcio.load_project(slug)["name"] == "Demo"


def test_duplicate_project_creates_new_slug():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            from maya_tools.framework import project_config_io as pcio
            with tempfile.TemporaryDirectory() as project_root:
                authored = _valid_authored(name="Demo")
                original_slug = pcio.slugify("Demo")
                psa.create_project(authored, _valid_bindings(project_root))
                new_slug = pcio.slugify("Demo Copy")
                psa.duplicate_project(original_slug, "Demo Copy")
                slugs = {meta["slug"] for meta in pcio.list_projects()}
                assert original_slug in slugs
                assert new_slug in slugs
                assert pcio.load_project(new_slug)["name"] == "Demo Copy"
                from maya_tools.framework import project_config_resolve as pcr
                # same-machine duplicate copies the source project bindings
                assert pcr.load_bindings(new_slug)["source_art_root"] == str(Path(project_root))


def test_delete_project_delegates_to_project_config_io():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            recorded = []
            real_delete = psa.project_config_io.delete_project

            def fake_delete(slug):
                recorded.append(slug)
                return Path("SENTINEL") / slug

            psa.project_config_io.delete_project = fake_delete
            try:
                result = psa.delete_project("demo")
                assert recorded == ["demo"]
                assert result == Path("SENTINEL") / "demo"
            finally:
                psa.project_config_io.delete_project = real_delete


def test_list_projects_delegates_to_project_config_io():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            canned = [{"slug": "demo", "name": "Demo", "engine": "unreal5"}]
            real_list = psa.project_config_io.list_projects
            psa.project_config_io.list_projects = lambda: canned
            try:
                assert psa.list_projects() == canned
            finally:
                psa.project_config_io.list_projects = real_list


def test_list_engine_templates_delegates_to_project_config_io():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            canned = [{"id": "unreal5", "label": "Unreal 5"}, {"id": "unity", "label": "Unity"}]
            real_list_templates = psa.project_config_io.list_templates
            psa.project_config_io.list_templates = lambda: canned
            try:
                assert psa.list_engine_templates() == canned
            finally:
                psa.project_config_io.list_templates = real_list_templates


def test_template_defaults_delegates_to_project_config_io():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            canned = {"name": "", "engine": "unreal5", "project_root": "", "linear_unit": "cm"}
            recorded = []
            real_load_template = psa.project_config_io.load_template

            def fake_load_template(template_id):
                recorded.append(template_id)
                return canned

            psa.project_config_io.load_template = fake_load_template
            try:
                assert psa.template_defaults("unreal5") == canned
                assert recorded == ["unreal5"]
            finally:
                psa.project_config_io.load_template = real_load_template


def test_apply_and_activate_syncs_both_stores():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            import maya_tools.framework.maya_startup as maya_startup
            from maya_tools.framework.toolbar import toolbar_app

            # apply_and_activate imports maya_startup LAZILY, inside its
            # own body (contract's binding lazy-import rule -- never at
            # project_setup_app module top). That lazy `from
            # maya_tools.framework import maya_startup` resolves the SAME
            # cached module object this test just imported, so patching
            # maya_startup.run here is what apply_and_activate will
            # actually call.
            recorded = []
            real_run = maya_startup.run
            maya_startup.run = lambda slug: recorded.append(slug)
            try:
                psa.apply_and_activate("demo")
            finally:
                maya_startup.run = real_run

            assert recorded == ["demo"]
            assert toolbar_app.active_project() == "demo"


def test_scaffold_delegates_to_project_scaffold():
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            from maya_tools.framework import project_config_io as pcio
            with tempfile.TemporaryDirectory() as project_root:
                authored = _valid_authored(name="Demo")
                psa.create_project(authored, _valid_bindings(project_root))
                slug = pcio.slugify("Demo")
                from maya_tools.framework import project_config_resolve as pcr
                keys = [key for key, _label, _abspath in
                        psa.project_scaffold.candidate_dirs(pcr.resolve_project(slug))]
                created = psa.scaffold(slug, keys)
                assert created, "expected at least one directory to be created"
                for path in created:
                    assert path.is_dir()


def test_duplicate_project_unbound_source_raises_actionable():
    # Duplicating an unbound v2 project must say WHY (bind the source
    # first), not misattribute 'source_art_root is required' to the copy.
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            from maya_tools.framework import project_config_io as pcio
            with tempfile.TemporaryDirectory() as project_root:
                psa.create_project(_valid_authored(name="Demo"), _valid_bindings(project_root))
                slug = pcio.slugify("Demo")
                from maya_tools.framework import project_config_resolve as pcr
                (pcr.user_dir() / f"{slug}.json").unlink()   # simulate unbound
                raised = ""
                try:
                    psa.duplicate_project(slug, "Demo Copy")
                except psa.ValidationError as exc:
                    raised = str(exc)
                assert "bind" in raised.lower() or "bindings" in raised.lower(), raised
                assert pcio.slugify("Demo Copy") not in {
                    m["slug"] for m in pcio.list_projects()}


def test_create_project_blocks_on_bindings_error():
    # A bindings error blocks save exactly like a config error: no project
    # folder is created and the issue rides ValidationError.issues.
    with tempfile.TemporaryDirectory() as td:
        with maya_app_dir(td):
            import maya_tools.framework.project_setup_app as psa
            from maya_tools.framework import project_config_io as pcio
            raised = False
            try:
                psa.create_project(_valid_authored(name="Demo"),
                                   {"source_art_root": "", "content_root": "rel/path"})
            except psa.ValidationError as exc:
                raised = True
                fields = [i.field for i in exc.issues if i.severity == "error"]
                assert "source_art_root" in fields and "content_root" in fields
            assert raised, "expected ValidationError from bindings"
            assert pcio.list_projects() == []      # nothing was saved


def run():
    check("project_setup_app/imports_without_maya_session", test_project_setup_app_imports_without_maya_session)
    check("ValidationError/carries_issues", test_validation_error_carries_issues)
    check("create_project/raises_on_invalid", test_create_project_raises_on_invalid)
    check("create_project/saves_and_round_trips", test_create_project_saves_and_round_trips)
    check("create_project/rejects_duplicate_name", test_create_project_rejects_duplicate_name_via_validation)
    check("create_project/succeeds_with_warnings_only", test_create_project_succeeds_with_warnings_only)
    check("create_project/blocks_on_bindings_error", test_create_project_blocks_on_bindings_error)
    check("duplicate_project/unbound_source_raises_actionable", test_duplicate_project_unbound_source_raises_actionable)
    check("edit_project/saves_in_place", test_edit_project_saves_in_place)
    check("edit_project/rejects_identity_changing_rename", test_edit_project_rejects_identity_changing_rename)
    check("duplicate_project/creates_new_slug", test_duplicate_project_creates_new_slug)
    check("delete_project/delegates", test_delete_project_delegates_to_project_config_io)
    check("list_projects/delegates", test_list_projects_delegates_to_project_config_io)
    check("list_engine_templates/delegates", test_list_engine_templates_delegates_to_project_config_io)
    check("template_defaults/delegates", test_template_defaults_delegates_to_project_config_io)
    check("apply_and_activate/syncs_both_stores", test_apply_and_activate_syncs_both_stores)
    check("scaffold/delegates_to_project_scaffold", test_scaffold_delegates_to_project_scaffold)


if __name__ == "__main__":
    run()
    print(f"\n{len(FAILURES)} failure(s).")
    sys.exit(1 if FAILURES else 0)
