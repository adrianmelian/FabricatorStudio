"""Engine-template + Archangel-migration tests (pure; no Maya session). Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/_dev/test_project_setup_templates.py

config_generator / config_validation / project_config_paths import no
maya.cmds, so this also runs under plain `py -3`; mayapy is used for
consistency with the KS test suite.

Covers spec docs/superpowers/specs/2026-07-07-project-setup-tool-design.md
sec 7 (engine templates) and sec 4.2 (schema/generator), scoped to Plan 5:
_unreal5 rebuilt, archangel migrated, _unity/_godot/_generic authored, and
the template-integrity gate. The real engine export->import round-trip
(Unreal/Unity/Godot) is Adrian's MANUAL acceptance gate per spec sec 7/9 --
this suite proves compile-clean + validate-clean + complete fbx triple
only, and does not attempt to simulate an engine import.
"""
import json
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


def _load_authored_from_dir(template_dir):
    """Merge <template_dir>/session.json + export_mapping.json into one
    v2 authored dict, mirroring project_config_io.load_project's merge
    (kept a deliberate local mirror): session keys plus the export-half
    keys the setup tool authors (asset_classes, prefixes, anim_prefix,
    library subpaths, mesh_group_name, engine_up_axis, fbx presets)."""
    session = json.loads((template_dir / "session.json").read_text(encoding="utf-8"))
    export = json.loads((template_dir / "export_mapping.json").read_text(encoding="utf-8"))
    authored = dict(session)
    authored["asset_classes"] = export.get("asset_classes", [])
    authored["prefixes"] = export.get("prefixes", {})
    authored["anim_prefix"] = export.get("anim_prefix", "")
    authored["pose_library_subpath"] = export.get("pose_library_subpath", "")
    authored["anim_library_subpath"] = export.get("anim_library_subpath", "")
    authored["mesh_group_name"] = export.get("mesh_group_name", "")
    authored["engine_up_axis"] = export.get("engine_up_axis", "")
    authored["fbx_presets"] = export.get("fbx_presets", {})
    return authored


def _archangel_present():
    """Adrian's private Archangel project config is untracked as of 2026-07-20 (it named
    an unannounced game in a public repo). It still lives on his disk, so these checks
    remain real validation there, and skip cleanly in any clone that lacks it."""
    from maya_tools.framework import project_config_paths as pcp
    return (pcp.packaged_templates_dir() / "archangel" / "session.json").exists()


ARCHANGEL = _archangel_present()

# engine_up_axis "z" (ratified 2026-07-18). archangel joins only when present locally.
_UNREAL_FAMILY = ("_unreal5",) + (("archangel",) if ARCHANGEL else ())
_Y_NATIVE = ("_unity", "_godot", "_generic")   # "" = follow exporter default 'y'


def test_templates_are_schema_v2():
    """Every packaged pair is v2: schema 2, no machine-absolute roots, and
    engine_up_axis kept per the AMENDED spec sec 6 (the sweep premise was
    false - the key is live exporter truth): 'z' for the Unreal family so
    a fresh Unreal project never recreates the pre-2026-07-14 swimming-
    skeleton behavior, '' (y-native) elsewhere."""
    from maya_tools.framework import project_config_paths as pcp
    root = pcp.packaged_templates_dir()
    for name in _UNREAL_FAMILY + _Y_NATIVE:
        export = json.loads((root / name / "export_mapping.json").read_text(encoding="utf-8"))
        session = json.loads((root / name / "session.json").read_text(encoding="utf-8"))
        assert export.get("schema_version") == 2, name
        assert "project_root" not in export, name
        assert "pose_library_root" not in export, name
        assert "blueprints_dir" not in session, name
        expected_axis = "z" if name in _UNREAL_FAMILY else ""
        assert export.get("engine_up_axis") == expected_axis, (
            name, export.get("engine_up_axis"))


_FBX_TYPES = ("static_mesh", "skeletal_mesh", "animation")


def _assert_fbx_triple_complete(fbx_presets, expected_up_axis):
    assert set(fbx_presets.keys()) == set(_FBX_TYPES), fbx_presets.keys()
    for kind in _FBX_TYPES:
        preset = fbx_presets[kind]
        assert preset, f"{kind} fbx preset must not be empty"
        assert preset.get("FBXExportUpAxis") == expected_up_axis, (
            f"{kind} FBXExportUpAxis expected {expected_up_axis!r}, "
            f"got {preset.get('FBXExportUpAxis')!r}"
        )


def _assert_matches_generator(template_dir):
    """The shipped export_mapping.json's derived block (path_patterns,
    anim_path_patterns, schema_version, _note) must not have drifted from
    what config_generator.generate_export_mapping produces from that same
    file's own asset_classes -- proves the static copy is self-consistent."""
    from maya_tools.framework import config_generator as cg
    authored = _load_authored_from_dir(template_dir)
    persisted = json.loads((template_dir / "export_mapping.json").read_text(encoding="utf-8"))
    persisted = {k: v for k, v in persisted.items() if k != "_status"}
    regenerated = cg.generate_export_mapping(authored)
    assert regenerated == persisted, (
        f"{template_dir.name}/export_mapping.json has drifted from "
        f"config_generator.generate_export_mapping's output for its own "
        f"asset_classes -- regenerate and re-save"
    )


def test_unreal5_session_no_up_axis_has_engine():
    from maya_tools.framework import project_config_paths as pcp
    d = pcp.packaged_templates_dir() / "_unreal5"
    session = json.loads((d / "session.json").read_text(encoding="utf-8"))
    assert "up_axis" not in session
    assert session["engine"] == "unreal5"


def test_unreal5_export_mapping_has_asset_classes():
    from maya_tools.framework import project_config_paths as pcp
    d = pcp.packaged_templates_dir() / "_unreal5"
    export = json.loads((d / "export_mapping.json").read_text(encoding="utf-8"))
    ids = [ac["id"] for ac in export["asset_classes"]]
    assert ids == ["character", "environment", "ui"]
    assert export["schema_version"] == 2


def test_unreal5_export_mapping_matches_generator():
    from maya_tools.framework import project_config_paths as pcp
    _assert_matches_generator(pcp.packaged_templates_dir() / "_unreal5")


def test_unreal5_fbx_triple_complete():
    from maya_tools.framework import project_config_paths as pcp
    d = pcp.packaged_templates_dir() / "_unreal5"
    export = json.loads((d / "export_mapping.json").read_text(encoding="utf-8"))
    _assert_fbx_triple_complete(export["fbx_presets"], expected_up_axis="z")


def test_archangel_session_no_up_axis_has_engine():
    from maya_tools.framework import project_config_paths as pcp
    d = pcp.packaged_templates_dir() / "archangel"
    session = json.loads((d / "session.json").read_text(encoding="utf-8"))
    assert "up_axis" not in session
    assert session["engine"] == "unreal5"
    assert session["name"] == "Archangel"


def test_archangel_export_mapping_has_asset_classes():
    from maya_tools.framework import project_config_paths as pcp
    d = pcp.packaged_templates_dir() / "archangel"
    export = json.loads((d / "export_mapping.json").read_text(encoding="utf-8"))
    ids = [ac["id"] for ac in export["asset_classes"]]
    assert ids == ["character", "environment", "ui"]
    assert export["schema_version"] == 2
    assert "project_root" not in export      # v2: roots are user-tier


def test_archangel_export_mapping_matches_generator():
    from maya_tools.framework import project_config_paths as pcp
    _assert_matches_generator(pcp.packaged_templates_dir() / "archangel")


# The pre-migration Archangel path_patterns / anim gameplay patterns
# (verbatim from archangel/export_mapping.json before this plan's Task 3) --
# the reference behavior the migration must preserve.
_ARCHANGEL_PRE_MIGRATION_PATH_PATTERNS = [
    {"match": r"SourceArt/Characters/(?P<asset>[^/]+)/.*\.ma$",
     "dest": "Game/Content/Characters/{asset}"},
    {"match": r"SourceArt/Environments/(?P<subpath>.+)/Source/[^/]+\.ma$",
     "dest": "Game/Content/Environment/{subpath}/Models"},
    {"match": r"SourceArt/UI/(?P<subpath>.+)/Source/[^/]+\.ma$",
     "dest": "Game/Content/UI/{subpath}/Models"},
]
_ARCHANGEL_PRE_MIGRATION_ANIM_GAMEPLAY = [
    {"match": r"SourceArt/Characters/(?P<asset>[^/]+)/.*\.ma$",
     "dest": "Game/Content/Characters/{asset}/Animations"},
]

_SAMPLE_SCENES = [
    "SourceArt/Characters/Hero/Hero.ma",
    "SourceArt/Characters/Hero/rig/Hero_rig.ma",   # nested under the char folder
    "SourceArt/Environments/Level1/Source/Level1.ma",
    "SourceArt/UI/HUD/Source/HUD.ma",
]


def _resolve(patterns, rel):
    """Mirror of export_core: first pattern that matches wins; dest is
    str.format-substituted with the match's named groups."""
    import re
    for p in patterns:
        m = re.match(p["match"], rel)
        if m:
            return p["dest"].format(**m.groupdict())
    return None


def test_archangel_equivalence_mesh_dests():
    from maya_tools.framework import project_config_paths as pcp
    d = pcp.packaged_templates_dir() / "archangel"
    export = json.loads((d / "export_mapping.json").read_text(encoding="utf-8"))
    for rel in _SAMPLE_SCENES:
        got = _resolve(export["path_patterns"], rel)
        want = _resolve(_ARCHANGEL_PRE_MIGRATION_PATH_PATTERNS, rel)
        assert got == want, f"mesh dest mismatch for {rel}: {got!r} != {want!r}"


def test_archangel_equivalence_anim_dests():
    from maya_tools.framework import project_config_paths as pcp
    d = pcp.packaged_templates_dir() / "archangel"
    export = json.loads((d / "export_mapping.json").read_text(encoding="utf-8"))
    for rel in _SAMPLE_SCENES:
        got = _resolve(export["anim_path_patterns"]["gameplay"], rel)
        want = _resolve(_ARCHANGEL_PRE_MIGRATION_ANIM_GAMEPLAY, rel)
        assert got == want, f"anim dest mismatch for {rel}: {got!r} != {want!r}"


def test_unity_template_ships_engine_and_starter_classes():
    from maya_tools.framework import project_config_paths as pcp
    d = pcp.packaged_templates_dir() / "_unity"
    session = json.loads((d / "session.json").read_text(encoding="utf-8"))
    assert "up_axis" not in session
    assert session["engine"] == "unity"
    export = json.loads((d / "export_mapping.json").read_text(encoding="utf-8"))
    ids = [ac["id"] for ac in export["asset_classes"]]
    assert ids == ["character", "environment"]
    assert export["prefixes"] == {"static_mesh": "SM_", "skeletal_mesh": "SK_"}
    assert export["anim_prefix"] == "A_"
    # Demoted pending Adrian's manual Unity export->import round-trip
    # (spec sec 7/9) -- same "_status" marker _generic ships with.
    assert export["_status"] == "experimental"


def test_unity_export_mapping_matches_generator():
    from maya_tools.framework import project_config_paths as pcp
    _assert_matches_generator(pcp.packaged_templates_dir() / "_unity")


def test_unity_fbx_triple_complete_y_axis():
    from maya_tools.framework import project_config_paths as pcp
    d = pcp.packaged_templates_dir() / "_unity"
    export = json.loads((d / "export_mapping.json").read_text(encoding="utf-8"))
    _assert_fbx_triple_complete(export["fbx_presets"], expected_up_axis="y")


def test_godot_template_ships_engine_and_starter_classes():
    from maya_tools.framework import project_config_paths as pcp
    d = pcp.packaged_templates_dir() / "_godot"
    session = json.loads((d / "session.json").read_text(encoding="utf-8"))
    assert "up_axis" not in session
    assert session["engine"] == "godot"
    export = json.loads((d / "export_mapping.json").read_text(encoding="utf-8"))
    ids = [ac["id"] for ac in export["asset_classes"]]
    assert ids == ["character", "environment"]
    assert export["prefixes"] == {"static_mesh": "SM_", "skeletal_mesh": "SK_"}
    assert export["anim_prefix"] == "A_"
    # Demoted pending Adrian's manual Godot export->import round-trip
    # (spec sec 7/9) -- same "_status" marker _generic ships with.
    assert export["_status"] == "experimental"


def test_godot_export_mapping_matches_generator():
    from maya_tools.framework import project_config_paths as pcp
    _assert_matches_generator(pcp.packaged_templates_dir() / "_godot")


def test_godot_fbx_triple_complete_y_axis():
    from maya_tools.framework import project_config_paths as pcp
    d = pcp.packaged_templates_dir() / "_godot"
    export = json.loads((d / "export_mapping.json").read_text(encoding="utf-8"))
    _assert_fbx_triple_complete(export["fbx_presets"], expected_up_axis="y")


def test_generic_template_marked_experimental():
    from maya_tools.framework import project_config_paths as pcp
    d = pcp.packaged_templates_dir() / "_generic"
    session = json.loads((d / "session.json").read_text(encoding="utf-8"))
    assert "up_axis" not in session
    assert session["engine"] == "generic"
    export = json.loads((d / "export_mapping.json").read_text(encoding="utf-8"))
    assert export["_status"] == "experimental"
    assert export["fbx_presets"] == {"static_mesh": {}, "skeletal_mesh": {}, "animation": {}}
    assert export["asset_classes"] == []


def test_generic_generate_export_mapping_runs_cleanly_on_empty_asset_classes():
    from maya_tools.framework import project_config_paths as pcp
    from maya_tools.framework import config_generator as cg
    d = pcp.packaged_templates_dir() / "_generic"
    authored = _load_authored_from_dir(d)
    out = cg.generate_export_mapping(authored)   # must not raise on empty asset_classes
    assert out["path_patterns"] == []
    assert out["anim_path_patterns"] == {"gameplay": [], "cinematic": []}


# Vetted = shipped "live" per spec sec 7's template gate: compiles clean,
# validates clean, AND has passed Adrian's real engine export->import
# round-trip. That round-trip only ever ran for _unreal5 (the Archangel
# lineage) -- there is no evidence it ran for _unity or _godot, so both
# stay demoted to experimental (own "_status" marker, same as _generic)
# until that manual gate runs. Promotion rule: run the round-trip, flip
# the template's export_mapping.json "_status" back off (delete the key),
# then add the template back to this tuple.
VETTED_TEMPLATES = ("_unreal5",)


def test_vetted_templates_validate_with_zero_errors():
    from maya_tools.framework import project_config_paths as pcp
    from maya_tools.framework import config_validation as cv
    for name in VETTED_TEMPLATES:
        d = pcp.packaged_templates_dir() / name
        authored = _load_authored_from_dir(d)
        with tempfile.TemporaryDirectory() as td:
            # The shipped template's project_root is a human-readable
            # placeholder (e.g. "C:/YourUnrealProject") that does not exist
            # on this machine; substitute a real, existing directory so
            # this test exercises schema/asset_class/fbx correctness only,
            # not "did Adrian happen to create that literal folder."
            authored = dict(authored)
            authored["project_root"] = td
            issues = cv.validate_config(authored)
        errors = [i for i in issues if i.severity == "error"]
        assert not errors, f"{name} has validation errors: {errors}"


def test_vetted_templates_fbx_triple_complete():
    from maya_tools.framework import project_config_paths as pcp
    expected_axis = {"_unreal5": "z", "_unity": "y", "_godot": "y"}
    for name in VETTED_TEMPLATES:
        d = pcp.packaged_templates_dir() / name
        export = json.loads((d / "export_mapping.json").read_text(encoding="utf-8"))
        _assert_fbx_triple_complete(export["fbx_presets"], expected_up_axis=expected_axis[name])


def test_vetted_templates_generate_export_mapping_cleanly():
    from maya_tools.framework import project_config_paths as pcp
    from maya_tools.framework import config_generator as cg
    for name in VETTED_TEMPLATES:
        d = pcp.packaged_templates_dir() / name
        authored = _load_authored_from_dir(d)
        out = cg.generate_export_mapping(authored)   # must not raise
        assert out["asset_classes"] == authored["asset_classes"]


def run():
    # checks are appended by each task below
    check("unreal5/session_no_up_axis_has_engine", test_unreal5_session_no_up_axis_has_engine)
    check("unreal5/export_mapping_has_asset_classes", test_unreal5_export_mapping_has_asset_classes)
    check("unreal5/export_mapping_matches_generator", test_unreal5_export_mapping_matches_generator)
    check("unreal5/fbx_triple_complete", test_unreal5_fbx_triple_complete)
    if ARCHANGEL:
        check("archangel/session_no_up_axis_has_engine", test_archangel_session_no_up_axis_has_engine)
        check("archangel/export_mapping_has_asset_classes", test_archangel_export_mapping_has_asset_classes)
        check("archangel/export_mapping_matches_generator", test_archangel_export_mapping_matches_generator)
        check("archangel/equivalence_mesh_dests", test_archangel_equivalence_mesh_dests)
        check("archangel/equivalence_anim_dests", test_archangel_equivalence_anim_dests)
    else:
        print("  skip: archangel/* (private config not present, expected in a public clone)")
    check("unity/ships_engine_and_starter_classes", test_unity_template_ships_engine_and_starter_classes)
    check("unity/export_mapping_matches_generator", test_unity_export_mapping_matches_generator)
    check("unity/fbx_triple_complete_y_axis", test_unity_fbx_triple_complete_y_axis)
    check("godot/ships_engine_and_starter_classes", test_godot_template_ships_engine_and_starter_classes)
    check("godot/export_mapping_matches_generator", test_godot_export_mapping_matches_generator)
    check("godot/fbx_triple_complete_y_axis", test_godot_fbx_triple_complete_y_axis)
    check("generic/marked_experimental", test_generic_template_marked_experimental)
    check("generic/generate_export_mapping_runs_cleanly", test_generic_generate_export_mapping_runs_cleanly_on_empty_asset_classes)
    check("template_integrity/schema_v2_and_engine_up_axis", test_templates_are_schema_v2)
    check("template_integrity/vetted_zero_validation_errors", test_vetted_templates_validate_with_zero_errors)
    check("template_integrity/vetted_fbx_triple_complete", test_vetted_templates_fbx_triple_complete)
    check("template_integrity/vetted_generate_export_mapping_cleanly", test_vetted_templates_generate_export_mapping_cleanly)


if __name__ == "__main__":
    run()
    print(f"\n{len(FAILURES)} failure(s).")
    sys.exit(1 if FAILURES else 0)
