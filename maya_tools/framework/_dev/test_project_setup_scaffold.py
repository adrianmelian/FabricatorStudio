"""project_scaffold tests (pure filesystem; no Maya session). Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/_dev/test_project_setup_scaffold.py

project_scaffold imports no maya.cmds, so this also runs under plain
py -3; mayapy is used for consistency with the KS test suite."""
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


def test_literal_head_placeholder_terminal():
    from maya_tools.framework import project_scaffold as ps
    assert ps._literal_head("SourceArt/Characters/{name}") == "SourceArt/Characters"


def test_literal_head_mid_placeholder():
    from maya_tools.framework import project_scaffold as ps
    # everything from the FIRST placeholder on is per-asset and dropped,
    # even the literal 'Source' tail after {path}
    assert ps._literal_head("SourceArt/Environments/{path}/Source") == "SourceArt/Environments"


def test_literal_head_no_placeholder():
    from maya_tools.framework import project_scaffold as ps
    assert ps._literal_head("SourceArt/FX") == "SourceArt/FX"


def test_candidate_dirs_from_asset_classes_and_roots():
    from maya_tools.framework import project_scaffold as ps
    with tempfile.TemporaryDirectory() as project_root:
        root = Path(project_root)
        authored = {
            "source_art_root": str(root),
            "asset_classes": [
                {"id": "character", "label": "Character",
                 "source_dir": "SourceArt/Characters/{name}"},
                {"id": "environment", "label": "Environment",
                 "source_dir": "SourceArt/Environments/{path}/Source"},
            ],
            "pose_library_root": str(root / "SourceArt" / "_pose"),
            "anim_library_root": str(root / "SourceArt" / "_anim"),
            "blueprints_dir": "",
        }
        assert ps.candidate_dirs(authored) == [
            ("asset_class:character", "Character source (SourceArt/Characters)",
             str(root / "SourceArt" / "Characters")),
            ("asset_class:environment", "Environment source (SourceArt/Environments)",
             str(root / "SourceArt" / "Environments")),
            ("pose_library_root", "Pose library root", str(root / "SourceArt" / "_pose")),
            ("anim_library_root", "Anim library root", str(root / "SourceArt" / "_anim")),
        ]


def test_candidate_dirs_includes_blueprints_dir_when_set():
    from maya_tools.framework import project_scaffold as ps
    with tempfile.TemporaryDirectory() as project_root:
        root = Path(project_root)
        authored = {
            "source_art_root": str(root),
            "asset_classes": [],
            "pose_library_root": "",
            "anim_library_root": "",
            "blueprints_dir": str(root / "Blueprints"),
        }
        assert ps.candidate_dirs(authored) == [
            ("blueprints_dir", "Blueprints directory", str(root / "Blueprints")),
        ]


def test_create_dirs_only_checked_keys():
    from maya_tools.framework import project_scaffold as ps
    with tempfile.TemporaryDirectory() as project_root:
        root = Path(project_root)
        authored = {
            "source_art_root": str(root),
            "asset_classes": [
                {"id": "character", "label": "Character",
                 "source_dir": "SourceArt/Characters/{name}"},
                {"id": "environment", "label": "Environment",
                 "source_dir": "SourceArt/Environments/{path}/Source"},
            ],
            "pose_library_root": "", "anim_library_root": "", "blueprints_dir": "",
        }
        created = ps.create_dirs(authored, ["asset_class:character"])
        assert created == [root / "SourceArt" / "Characters"]
        assert (root / "SourceArt" / "Characters").is_dir()
        assert not (root / "SourceArt" / "Environments").exists()


def test_create_dirs_is_idempotent_and_makes_parents():
    from maya_tools.framework import project_scaffold as ps
    with tempfile.TemporaryDirectory() as project_root:
        root = Path(project_root)
        authored = {
            "source_art_root": str(root),
            "asset_classes": [
                {"id": "environment", "label": "Environment",
                 "source_dir": "SourceArt/Environments/{path}/Source"},
            ],
            "pose_library_root": "", "anim_library_root": "", "blueprints_dir": "",
        }
        keys = ["asset_class:environment"]
        first = ps.create_dirs(authored, keys)
        second = ps.create_dirs(authored, keys)   # re-run must not raise
        assert first == second == [root / "SourceArt" / "Environments"]
        assert (root / "SourceArt" / "Environments").is_dir()


def run():
    check("literal_head/placeholder_terminal", test_literal_head_placeholder_terminal)
    check("literal_head/mid_placeholder", test_literal_head_mid_placeholder)
    check("literal_head/no_placeholder", test_literal_head_no_placeholder)
    check("candidate_dirs/asset_classes_and_roots", test_candidate_dirs_from_asset_classes_and_roots)
    check("candidate_dirs/blueprints_dir_when_set", test_candidate_dirs_includes_blueprints_dir_when_set)
    check("create_dirs/only_checked_keys", test_create_dirs_only_checked_keys)
    check("create_dirs/idempotent_and_makes_parents", test_create_dirs_is_idempotent_and_makes_parents)


if __name__ == "__main__":
    run()
    print(f"\n{len(FAILURES)} failure(s).")
    sys.exit(1 if FAILURES else 0)
