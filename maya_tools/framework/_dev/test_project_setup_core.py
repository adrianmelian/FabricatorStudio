"""Config generator + validator tests (pure; no Maya session). Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/_dev/test_project_setup_core.py

config_generator / config_validation import no maya.cmds, so this also runs
under plain `py -3`; mayapy is used for consistency with the KS test suite."""
import re
import sys
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


def test_placeholders_in():
    from maya_tools.framework import config_generator as cc
    assert cc.placeholders_in("SourceArt/Characters/{name}") == ["name"]
    assert cc.placeholders_in("A/{path}/B/{name}") == ["path", "name"]
    assert cc.placeholders_in("no/tokens/here") == []


def test_ends_with_placeholder():
    from maya_tools.framework import config_generator as cc
    assert cc._ends_with_placeholder("SourceArt/Characters/{name}") is True
    assert cc._ends_with_placeholder("SourceArt/Environments/{path}/Source") is False
    assert cc._ends_with_placeholder("flat/{name}/") is True  # trailing slash tolerated


def test_source_to_match_placeholder_terminal():
    from maya_tools.framework import config_generator as cc
    # ends in a placeholder -> ".*" tail (files may nest under the asset folder)
    assert cc.source_to_match("SourceArt/Characters/{name}") == \
        r"SourceArt/Characters/(?P<name>[^/]+)/.*\.ma$"


def test_source_to_match_literal_terminal():
    from maya_tools.framework import config_generator as cc
    # ends in a literal segment -> "[^/]+" tail (files sit directly in it)
    assert cc.source_to_match("SourceArt/Environments/{path}/Source") == \
        r"SourceArt/Environments/(?P<path>.+)/Source/[^/]+\.ma$"


def test_source_to_match_unknown_placeholder():
    from maya_tools.framework import config_generator as cc
    raised = False
    try:
        cc.source_to_match("SourceArt/{bogus}")
    except ValueError:
        raised = True
    assert raised, "unknown placeholder must raise ValueError"


def test_generate_patterns():
    from maya_tools.framework import config_generator as cc
    classes = [
        {"id": "character", "export_types": ["skeletal_mesh", "static_mesh"],
         "source_dir": "SourceArt/Characters/{name}",
         "dest_dir": "Game/Content/Characters/{name}",
         "anim": {"gameplay": "Game/Content/Characters/{name}/Animations",
                  "cinematic": ""}},
        {"id": "environment", "export_types": ["static_mesh"],
         "source_dir": "SourceArt/Environments/{path}/Source",
         "dest_dir": "Game/Content/Environment/{path}/Models",
         "anim": {"gameplay": "", "cinematic": ""}},
    ]
    out = cc.generate_patterns(classes)
    assert out["path_patterns"] == [
        {"match": r"SourceArt/Characters/(?P<name>[^/]+)/.*\.ma$",
         "dest": "Game/Content/Characters/{name}"},
        {"match": r"SourceArt/Environments/(?P<path>.+)/Source/[^/]+\.ma$",
         "dest": "Game/Content/Environment/{path}/Models"},
    ]
    assert out["anim_path_patterns"] == {
        "gameplay": [
            {"match": r"SourceArt/Characters/(?P<name>[^/]+)/.*\.ma$",
             "dest": "Game/Content/Characters/{name}/Animations"},
        ],
        "cinematic": [],
    }


def test_generate_patterns_anim_only_no_mesh_entry():
    from maya_tools.framework import config_generator as cc
    # a class with no mesh export_type contributes NO path_patterns entry,
    # but still contributes its anim entries
    classes = [
        {"id": "fx", "export_types": [],
         "source_dir": "SourceArt/FX/{name}", "dest_dir": "Game/Content/FX/{name}",
         "anim": {"gameplay": "Game/Content/FX/{name}/Anim", "cinematic": ""}},
    ]
    out = cc.generate_patterns(classes)
    assert out["path_patterns"] == []
    assert out["anim_path_patterns"]["gameplay"] == [
        {"match": r"SourceArt/FX/(?P<name>[^/]+)/.*\.ma$",
         "dest": "Game/Content/FX/{name}/Anim"},
    ]


def test_generate_export_mapping_superset():
    from maya_tools.framework import config_generator as cc
    authored = {
        "name": "Demo",
        "asset_classes": [
            {"id": "character", "export_types": ["skeletal_mesh"],
             "source_dir": "SourceArt/Characters/{name}",
             "dest_dir": "Game/Content/Characters/{name}",
             "anim": {"gameplay": "Game/Content/Characters/{name}/Animations",
                      "cinematic": ""}},
        ],
        "prefixes": {"static_mesh": "SM_", "skeletal_mesh": "SK_"},
        "anim_prefix": "A_",
        "pose_library_subpath": "SourceArt/_pose",
        "anim_library_subpath": "SourceArt/_anim",
        "fbx_presets": {"static_mesh": {}, "skeletal_mesh": {}, "animation": {}},
    }
    out = cc.generate_export_mapping(authored)
    # kept keys pass through unchanged; machine-absolute roots NEVER appear (v2)
    assert out["name"] == "Demo"
    assert "project_root" not in out
    assert out["prefixes"] == {"static_mesh": "SM_", "skeletal_mesh": "SK_"}
    assert out["anim_prefix"] == "A_"
    assert out["pose_library_subpath"] == "SourceArt/_pose"
    assert "pose_library_root" not in out
    assert out["fbx_presets"] == {"static_mesh": {}, "skeletal_mesh": {}, "animation": {}}
    # authored truth is persisted
    assert out["asset_classes"] == authored["asset_classes"]
    # derived keys are generated
    assert out["path_patterns"] == [
        {"match": r"SourceArt/Characters/(?P<name>[^/]+)/.*\.ma$",
         "dest": "Game/Content/Characters/{name}"}]
    assert out["anim_path_patterns"]["gameplay"][0]["dest"] == \
        "Game/Content/Characters/{name}/Animations"
    # provenance keys present
    assert out["schema_version"] == cc.SCHEMA_VERSION
    assert "generated" in out["_note"].lower()


# The current Archangel path_patterns / anim gameplay patterns, copied verbatim
# from maya_tools/framework/project_configs/archangel/export_mapping.json
# (the reference behavior we must preserve).
ARCHANGEL_CURRENT_PATH_PATTERNS = [
    {"match": r"SourceArt/Characters/(?P<asset>[^/]+)/.*\.ma$",
     "dest": "Game/Content/Characters/{asset}"},
    {"match": r"SourceArt/Environments/(?P<subpath>.+)/Source/[^/]+\.ma$",
     "dest": "Game/Content/Environment/{subpath}/Models"},
    {"match": r"SourceArt/UI/(?P<subpath>.+)/Source/[^/]+\.ma$",
     "dest": "Game/Content/UI/{subpath}/Models"},
]
ARCHANGEL_CURRENT_ANIM_GAMEPLAY = [
    {"match": r"SourceArt/Characters/(?P<asset>[^/]+)/.*\.ma$",
     "dest": "Game/Content/Characters/{asset}/Animations"},
]

# The migrated Archangel asset_classes (standardized {name}/{path} vocabulary).
ARCHANGEL_ASSET_CLASSES = [
    {"id": "character", "label": "Character",
     "export_types": ["skeletal_mesh", "static_mesh"],
     "source_dir": "SourceArt/Characters/{name}",
     "dest_dir": "Game/Content/Characters/{name}",
     "anim": {"gameplay": "Game/Content/Characters/{name}/Animations",
              "cinematic": ""}},
    {"id": "environment", "label": "Environment", "export_types": ["static_mesh"],
     "source_dir": "SourceArt/Environments/{path}/Source",
     "dest_dir": "Game/Content/Environment/{path}/Models",
     "anim": {"gameplay": "", "cinematic": ""}},
    {"id": "ui", "label": "UI", "export_types": ["static_mesh"],
     "source_dir": "SourceArt/UI/{path}/Source",
     "dest_dir": "Game/Content/UI/{path}/Models",
     "anim": {"gameplay": "", "cinematic": ""}},
]

# Representative scene paths relative to project_root.
SAMPLE_SCENES = [
    "SourceArt/Characters/Hero/Hero.ma",
    "SourceArt/Characters/Hero/rig/Hero_rig.ma",   # nested under the char folder
    "SourceArt/Environments/Level1/Source/Level1.ma",
    "SourceArt/UI/HUD/Source/HUD.ma",
]


def _resolve(patterns, rel):
    """Mirror of export_core: first pattern that matches wins; dest is
    str.format-substituted with the match's named groups."""
    for p in patterns:
        m = re.match(p["match"], rel)
        if m:
            return p["dest"].format(**m.groupdict())
    return None


def test_equivalence_mesh_dests():
    from maya_tools.framework import config_generator as cc
    generated = cc.generate_patterns(ARCHANGEL_ASSET_CLASSES)
    for rel in SAMPLE_SCENES:
        got = _resolve(generated["path_patterns"], rel)
        want = _resolve(ARCHANGEL_CURRENT_PATH_PATTERNS, rel)
        assert got == want, f"mesh dest mismatch for {rel}: {got!r} != {want!r}"


def test_equivalence_anim_dests():
    from maya_tools.framework import config_generator as cc
    generated = cc.generate_patterns(ARCHANGEL_ASSET_CLASSES)
    for rel in SAMPLE_SCENES:
        got = _resolve(generated["anim_path_patterns"]["gameplay"], rel)
        want = _resolve(ARCHANGEL_CURRENT_ANIM_GAMEPLAY, rel)
        assert got == want, f"anim dest mismatch for {rel}: {got!r} != {want!r}"


def test_issue_is_a_severity_field_message_namedtuple():
    from maya_tools.framework import config_validation as cv
    issue = cv.Issue(severity="error", field="name", message="name is required")
    assert issue.severity == "error"
    assert issue.field == "name"
    assert issue.message == "name is required"


def test_validate_config_is_callable_and_returns_a_list():
    # At this scaffold stage validate_config has no sub-validators wired in
    # yet, so {} is (vacuously) clean. Once Task 2+ wire in real checks, an
    # empty authored dict legitimately trips errors (see
    # test_validate_config_full_wiring_catches_every_area in Task 11) -- this
    # test's job is only to confirm the callable/list-return contract, not a
    # specific value for {}.
    from maya_tools.framework import config_validation as cv
    assert isinstance(cv.validate_config({}), list)


def test_validate_units_accepts_known_tokens():
    from maya_tools.framework import config_validation as cv
    authored = {"linear_unit": "cm", "angular_unit": "deg", "frame_rate": "ntsc"}
    assert cv._validate_units(authored) == []


def test_validate_units_accepts_numeric_fps():
    from maya_tools.framework import config_validation as cv
    authored = {"linear_unit": "m", "angular_unit": "rad", "frame_rate": "24fps"}
    assert cv._validate_units(authored) == []


def test_validate_units_accepts_drop_frame_fps():
    from maya_tools.framework import config_validation as cv
    for token in ("29.97df", "23.976df"):
        authored = {"linear_unit": "cm", "angular_unit": "deg", "frame_rate": token}
        assert cv._validate_units(authored) == []


def test_validate_units_rejects_bad_tokens():
    from maya_tools.framework import config_validation as cv
    authored = {"linear_unit": "parsecs", "angular_unit": "turns", "frame_rate": "warp9"}
    issues = cv._validate_units(authored)
    fields = {i.field for i in issues}
    assert fields == {"linear_unit", "angular_unit", "frame_rate"}
    assert all(i.severity == "error" for i in issues)


def test_validate_playback_clean_when_both_set_and_ordered():
    from maya_tools.framework import config_validation as cv
    assert cv._validate_playback({"playback_start": 1, "playback_end": 100}) == []


def test_validate_playback_clean_when_both_none():
    from maya_tools.framework import config_validation as cv
    assert cv._validate_playback({"playback_start": None, "playback_end": None}) == []


def test_validate_playback_rejects_one_sided():
    from maya_tools.framework import config_validation as cv
    issues = cv._validate_playback({"playback_start": 1, "playback_end": None})
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].field == "playback"


def test_validate_playback_rejects_start_after_end():
    from maya_tools.framework import config_validation as cv
    issues = cv._validate_playback({"playback_start": 100, "playback_end": 1})
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].field == "playback"


def test_validate_subpaths_blank_blueprints_is_warning():
    from maya_tools.framework import config_validation as cv
    issues = cv._validate_subpaths({"pose_library_subpath": "SourceArt/_pose",
                                    "anim_library_subpath": "SourceArt/_anim",
                                    "blueprints_subpath": ""})
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].field == "blueprints_subpath"


def test_validate_subpaths_absolute_is_error():
    from maya_tools.framework import config_validation as cv
    issues = cv._validate_subpaths({"pose_library_subpath": "C:/Abs/Poses",
                                    "anim_library_subpath": "SourceArt/_anim",
                                    "blueprints_subpath": "Blueprints"})
    errors = [i for i in issues if i.severity == "error"]
    assert len(errors) == 1
    assert errors[0].field == "pose_library_subpath"


def test_validate_subpaths_relative_set_is_clean():
    from maya_tools.framework import config_validation as cv
    issues = cv._validate_subpaths({"pose_library_subpath": "SourceArt/_pose",
                                    "anim_library_subpath": "SourceArt/_anim",
                                    "blueprints_subpath": "Content/Blueprints"})
    assert issues == []


def test_validate_asset_classes_requires_at_least_one():
    from maya_tools.framework import config_validation as cv
    issues = cv._validate_asset_classes({"asset_classes": []})
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].field == "asset_classes"


def test_validate_asset_classes_flags_duplicate_and_bad_id():
    from maya_tools.framework import config_validation as cv
    authored = {"asset_classes": [
        {"id": "character", "source_dir": "SourceArt/Characters/{name}",
         "dest_dir": "Game/Content/Characters/{name}",
         "anim": {"gameplay": "", "cinematic": ""}},
        {"id": "character", "source_dir": "SourceArt/Characters2/{name}",
         "dest_dir": "Game/Content/Characters2/{name}",
         "anim": {"gameplay": "", "cinematic": ""}},
        {"id": "Bad Id!", "source_dir": "SourceArt/X/{name}",
         "dest_dir": "Game/Content/X/{name}",
         "anim": {"gameplay": "", "cinematic": ""}},
    ]}
    issues = cv._validate_asset_classes(authored)
    fields = [i.field for i in issues]
    assert "asset_classes[1].id" in fields   # duplicate of [0]
    assert "asset_classes[2].id" in fields    # bad chars


def test_validate_asset_classes_requires_nonempty_dirs():
    from maya_tools.framework import config_validation as cv
    authored = {"asset_classes": [
        {"id": "character", "source_dir": "", "dest_dir": "",
         "anim": {"gameplay": "", "cinematic": ""}},
    ]}
    issues = cv._validate_asset_classes(authored)
    fields = {i.field for i in issues}
    assert "asset_classes[0].source_dir" in fields
    assert "asset_classes[0].dest_dir" in fields


def test_validate_asset_classes_rejects_unknown_placeholder():
    from maya_tools.framework import config_validation as cv
    authored = {"asset_classes": [
        {"id": "fx", "source_dir": "SourceArt/FX/{bogus}",
         "dest_dir": "Game/Content/FX/{bogus}",
         "anim": {"gameplay": "", "cinematic": ""}},
    ]}
    issues = cv._validate_asset_classes(authored)
    messages = " ".join(i.message for i in issues)
    assert "{bogus}" in messages


def test_validate_asset_classes_requires_dest_tokens_from_source():
    from maya_tools.framework import config_validation as cv
    authored = {"asset_classes": [
        {"id": "environment", "source_dir": "SourceArt/Environments/{path}/Source",
         "dest_dir": "Game/Content/Environment/{name}/Models",   # {name} not in source
         "anim": {"gameplay": "", "cinematic": ""}},
    ]}
    issues = cv._validate_asset_classes(authored)
    dest_issues = [i for i in issues if i.field == "asset_classes[0].dest_dir"]
    assert len(dest_issues) == 1
    assert "{name}" in dest_issues[0].message


def test_validate_asset_classes_checks_anim_dest_tokens_too():
    from maya_tools.framework import config_validation as cv
    authored = {"asset_classes": [
        {"id": "character", "source_dir": "SourceArt/Characters/{name}",
         "dest_dir": "Game/Content/Characters/{name}",
         "anim": {"gameplay": "Game/Content/Characters/{path}/Animations",
                  "cinematic": ""}},
    ]}
    issues = cv._validate_asset_classes(authored)
    anim_issues = [i for i in issues if i.field == "asset_classes[0].anim.gameplay"]
    assert len(anim_issues) == 1
    assert "{path}" in anim_issues[0].message


def test_validate_asset_classes_none_source_and_dest_dir_do_not_crash():
    # An explicit JSON null for source_dir/dest_dir (as opposed to a missing
    # key) must be coerced to "" before any placeholder/coverage check runs,
    # or config_generator.placeholders_in(None) raises TypeError.
    from maya_tools.framework import config_validation as cv
    authored = {"asset_classes": [
        {"id": "props", "source_dir": None, "dest_dir": None,
         "anim": {"gameplay": "", "cinematic": ""}},
    ]}
    issues = cv._validate_asset_classes(authored)
    fields = {i.field for i in issues}
    assert "asset_classes[0].source_dir" in fields
    assert "asset_classes[0].dest_dir" in fields


def test_validate_asset_classes_rejects_non_string_id():
    from maya_tools.framework import config_validation as cv
    authored = {"asset_classes": [
        {"id": 7, "source_dir": "SourceArt/X/{name}",
         "dest_dir": "Game/Content/X/{name}",
         "anim": {"gameplay": "", "cinematic": ""}},
    ]}
    issues = cv._validate_asset_classes(authored)
    id_issues = [i for i in issues if i.field == "asset_classes[0].id"]
    assert len(id_issues) == 1
    assert id_issues[0].severity == "error"


def test_assert_patterns_resolve_passes_for_valid_classes():
    from maya_tools.framework import config_validation as cv
    classes = [
        {"id": "character", "export_types": ["skeletal_mesh"],
         "source_dir": "SourceArt/Characters/{name}",
         "dest_dir": "Game/Content/Characters/{name}",
         "anim": {"gameplay": "Game/Content/Characters/{name}/Animations",
                  "cinematic": ""}},
    ]
    assert cv._assert_patterns_resolve(classes) == []


def test_validate_asset_classes_runs_post_generate_assert_when_clean():
    # A config that passes every declarative check but where generate_patterns
    # itself produced a bad dest must still be caught -- the regression net
    # against generator/validator drift. Simulated by monkeypatching
    # config_generator.generate_patterns.
    from maya_tools.framework import config_validation as cv
    from maya_tools.framework import config_generator as cg
    classes = [
        {"id": "character", "export_types": ["skeletal_mesh"],
         "source_dir": "SourceArt/Characters/{name}",
         "dest_dir": "Game/Content/Characters/{name}",
         "anim": {"gameplay": "", "cinematic": ""}},
    ]
    real_generate_patterns = cg.generate_patterns

    def broken_generate_patterns(_classes):
        return {
            "path_patterns": [{
                "match": r"SourceArt/Characters/(?P<name>[^/]+)/.*\.ma$",
                "dest": "Game/Content/Characters/{missing_token}",
            }],
            "anim_path_patterns": {"gameplay": [], "cinematic": []},
        }

    cg.generate_patterns = broken_generate_patterns
    try:
        issues = cv._validate_asset_classes({"asset_classes": classes})
    finally:
        cg.generate_patterns = real_generate_patterns
    assert any(i.field == "asset_classes" and "does not resolve" in i.message
               for i in issues)


def test_validate_fbx_requires_all_three_types():
    from maya_tools.framework import config_validation as cv
    issues = cv._validate_fbx({"fbx_presets": {"static_mesh": {"FBXExportSkins": False}}})
    fields = {i.field for i in issues}
    assert "fbx_presets.skeletal_mesh" in fields
    assert "fbx_presets.animation" in fields
    assert "fbx_presets.static_mesh" not in fields


def test_validate_fbx_warns_on_unknown_key():
    # An unrecognized key is a WARNING, not an error -- a vetted Unity/Godot
    # preset with extra options must not be blocked from saving, while a
    # typo still surfaces to the author.
    from maya_tools.framework import config_validation as cv
    authored = {"fbx_presets": {
        "static_mesh": {"FBXExportSkinz": False},
        "skeletal_mesh": {"FBXExportSkins": True},
        "animation": {"FBXExportSkins": False},
    }}
    issues = cv._validate_fbx(authored)
    assert len(issues) == 1
    assert issues[0].field == "fbx_presets.static_mesh.FBXExportSkinz"
    assert issues[0].severity == "warning"


def test_validate_fbx_accepts_the_shipped_unreal5_preset_keys():
    from maya_tools.framework import config_validation as cv
    import json
    mapping = json.loads((REPO_ROOT / "maya_tools" / "framework" / "project_configs"
                          / "_unreal5" / "export_mapping.json").read_text(encoding="utf-8"))
    issues = cv._validate_fbx({"fbx_presets": mapping["fbx_presets"]})
    assert issues == []


def test_validate_prefixes_requires_static_and_skeletal_and_anim():
    from maya_tools.framework import config_validation as cv
    issues = cv._validate_prefixes({})
    fields = {i.field for i in issues}
    assert fields == {"prefixes.static_mesh", "prefixes.skeletal_mesh", "anim_prefix"}


def test_validate_prefixes_passes_when_all_present():
    from maya_tools.framework import config_validation as cv
    authored = {"prefixes": {"static_mesh": "SM_", "skeletal_mesh": "SK_"}, "anim_prefix": "A_"}
    assert cv._validate_prefixes(authored) == []


def test_validate_identity_requires_name():
    from maya_tools.framework import config_validation as cv
    issues = cv._validate_identity({}, [])
    assert len(issues) == 1
    assert issues[0].field == "name"


def test_validate_identity_rejects_underscore_leading_slug():
    from maya_tools.framework import config_validation as cv
    issues = cv._validate_identity({"name": "_Hidden"}, [])
    assert len(issues) == 1
    assert "must not start with '_'" in issues[0].message


def test_validate_identity_flags_slug_collision():
    from maya_tools.framework import config_validation as cv
    existing = [{"name": "Archangel"}]
    issues = cv._validate_identity({"name": "archangel"}, existing)
    assert len(issues) == 1
    assert "collides" in issues[0].message


def test_validate_identity_clean_when_unique():
    from maya_tools.framework import config_validation as cv
    existing = [{"name": "Archangel"}]
    assert cv._validate_identity({"name": "Demo"}, existing) == []


GOLDEN_AUTHORED = {
    "name": "Demo",
    "engine": "unreal5",
    "linear_unit": "cm", "angular_unit": "deg", "frame_rate": "ntsc",
    "playback_start": 1, "playback_end": 100,
    "undo_depth": 50,
    "camera_near_clip": 1.0, "camera_far_clip": 1000000.0,
    "grid": {"size": 12, "spacing": 5, "divisions": 5},
    "blueprints_subpath": "Content/Blueprints",
    "asset_classes": [
        {"id": "character", "label": "Character",
         "export_types": ["skeletal_mesh", "static_mesh"],
         "source_dir": "SourceArt/Characters/{name}",
         "dest_dir": "Game/Content/Characters/{name}",
         "anim": {"gameplay": "Game/Content/Characters/{name}/Animations",
                  "cinematic": ""}},
        {"id": "environment", "label": "Environment", "export_types": ["static_mesh"],
         "source_dir": "SourceArt/Environments/{path}/Source",
         "dest_dir": "Game/Content/Environment/{path}/Models",
         "anim": {"gameplay": "", "cinematic": ""}},
    ],
    "prefixes": {"static_mesh": "SM_", "skeletal_mesh": "SK_"},
    "anim_prefix": "A_",
    "pose_library_subpath": "SourceArt/_pose_library",
    "anim_library_subpath": "SourceArt/_anim_library",
    "mesh_group_name": "geo_grp",
    "engine_up_axis": "z",
    "orient_convention": "standard",
    "fbx_presets": {
        "static_mesh": {"FBXExportSkins": False, "FBXExportUpAxis": "z"},
        "skeletal_mesh": {"FBXExportSkins": True, "FBXExportUpAxis": "z"},
        "animation": {"FBXExportBakeComplexAnimation": True, "FBXExportUpAxis": "z"},
    },
}


def test_validate_config_golden_authored_is_clean():
    from maya_tools.framework import config_validation as cv
    assert cv.validate_config(GOLDEN_AUTHORED, existing_projects=[]) == []


def test_validate_config_errors_before_warnings():
    from maya_tools.framework import config_validation as cv
    import copy
    broken = copy.deepcopy(GOLDEN_AUTHORED)
    broken["blueprints_subpath"] = ""   # -> warning
    broken["angular_unit"] = "turns"    # -> error
    issues = cv.validate_config(broken, existing_projects=[])
    assert len(issues) == 2
    assert issues[0].severity == "error"
    assert issues[0].field == "angular_unit"
    assert issues[1].severity == "warning"
    assert issues[1].field == "blueprints_subpath"


def test_validate_config_full_wiring_catches_every_area():
    from maya_tools.framework import config_validation as cv
    issues = cv.validate_config({}, existing_projects=[])
    fields = {i.field for i in issues}
    assert "name" in fields
    assert "linear_unit" in fields
    assert "angular_unit" in fields
    assert "frame_rate" in fields
    assert "project_root" not in fields      # v2: roots are user-tier bindings
    assert "asset_classes" in fields
    assert "fbx_presets.static_mesh" in fields
    assert "prefixes.static_mesh" in fields
    assert "blueprints_subpath" in fields


def test_validate_config_v2_rejects_absolute_subpaths_and_dirs():
    from maya_tools.framework import config_validation as cv
    import copy
    authored = copy.deepcopy(GOLDEN_AUTHORED)
    authored["pose_library_subpath"] = "C:/Absolute/Poses"
    authored["asset_classes"][0]["source_dir"] = "C:/Abs/Chars/{name}"
    issues = cv.validate_config(authored, existing_projects=[])
    fields = [i.field for i in issues if i.severity == "error"]
    assert "pose_library_subpath" in fields
    assert any(f.endswith("source_dir") for f in fields)


def test_validate_config_v2_rejects_absolute_dest_dir_and_traversal():
    from maya_tools.framework import config_validation as cv
    import copy
    authored = copy.deepcopy(GOLDEN_AUTHORED)
    authored["asset_classes"][0]["dest_dir"] = "C:/Game/Content/Chars/{name}"
    authored["anim_library_subpath"] = "../SharedAnims"      # escapes the root
    authored["blueprints_subpath"] = "C:Blueprints"          # drive-relative, isabs()==False
    issues = cv.validate_config(authored, existing_projects=[])
    fields = [i.field for i in issues if i.severity == "error"]
    assert any(f.endswith("dest_dir") for f in fields)
    assert "anim_library_subpath" in fields
    assert "blueprints_subpath" in fields


def test_validate_identity_reserves_settings_name():
    from maya_tools.framework import config_validation as cv
    issues = cv._validate_identity({"name": "Settings"}, [])
    assert len(issues) == 1 and issues[0].severity == "error"
    assert "reserved" in issues[0].message.lower()


def test_validate_bindings():
    from maya_tools.framework import config_validation as cv
    issues = cv.validate_bindings({"source_art_root": "",
                                   "content_root": "relative/path",
                                   "anim_library_root_override": "also/relative"})
    fields = [i.field for i in issues if i.severity == "error"]
    assert "source_art_root" in fields              # required
    assert "content_root" in fields                 # must be absolute
    assert "anim_library_root_override" in fields   # override must be absolute when set


def test_validate_bindings_clean_and_overlap_warns():
    from maya_tools.framework import config_validation as cv
    clean = {"source_art_root": "D:/Art", "content_root": "D:/Game"}
    assert cv.validate_bindings(clean) == []
    issues = cv.validate_bindings(
        clean,
        existing_bindings=[{"slug": "other", "source_art_root": "D:/Art/Sub",
                            "content_root": "E:/X"}])
    assert any(i.severity == "warning" and i.field == "source_art_root" for i in issues)
    assert not [i for i in issues if i.severity == "error"]


def run():
    check("placeholders_in", test_placeholders_in)
    check("ends_with_placeholder", test_ends_with_placeholder)
    check("source_to_match/placeholder_terminal", test_source_to_match_placeholder_terminal)
    check("source_to_match/literal_terminal", test_source_to_match_literal_terminal)
    check("source_to_match/unknown_placeholder", test_source_to_match_unknown_placeholder)
    check("generate_patterns", test_generate_patterns)
    check("generate_patterns/anim_only", test_generate_patterns_anim_only_no_mesh_entry)
    check("generate_export_mapping/superset", test_generate_export_mapping_superset)
    check("equivalence/mesh_dests", test_equivalence_mesh_dests)
    check("equivalence/anim_dests", test_equivalence_anim_dests)
    check("config_validation/issue_shape", test_issue_is_a_severity_field_message_namedtuple)
    check("config_validation/validate_config_stub", test_validate_config_is_callable_and_returns_a_list)
    check("validate_units/known_tokens", test_validate_units_accepts_known_tokens)
    check("validate_units/numeric_fps", test_validate_units_accepts_numeric_fps)
    check("validate_units/drop_frame_fps", test_validate_units_accepts_drop_frame_fps)
    check("validate_units/bad_tokens", test_validate_units_rejects_bad_tokens)
    check("validate_playback/clean_ordered", test_validate_playback_clean_when_both_set_and_ordered)
    check("validate_playback/clean_both_none", test_validate_playback_clean_when_both_none)
    check("validate_playback/one_sided", test_validate_playback_rejects_one_sided)
    check("validate_playback/start_after_end", test_validate_playback_rejects_start_after_end)
    check("validate_subpaths/blank_blueprints_is_warning", test_validate_subpaths_blank_blueprints_is_warning)
    check("validate_subpaths/absolute_is_error", test_validate_subpaths_absolute_is_error)
    check("validate_subpaths/relative_set_is_clean", test_validate_subpaths_relative_set_is_clean)
    check("validate_asset_classes/requires_at_least_one", test_validate_asset_classes_requires_at_least_one)
    check("validate_asset_classes/duplicate_and_bad_id", test_validate_asset_classes_flags_duplicate_and_bad_id)
    check("validate_asset_classes/requires_nonempty_dirs", test_validate_asset_classes_requires_nonempty_dirs)
    check("validate_asset_classes/rejects_unknown_placeholder", test_validate_asset_classes_rejects_unknown_placeholder)
    check("validate_asset_classes/dest_tokens_from_source", test_validate_asset_classes_requires_dest_tokens_from_source)
    check("validate_asset_classes/anim_dest_tokens", test_validate_asset_classes_checks_anim_dest_tokens_too)
    check("validate_asset_classes/none_dirs_do_not_crash", test_validate_asset_classes_none_source_and_dest_dir_do_not_crash)
    check("validate_asset_classes/rejects_non_string_id", test_validate_asset_classes_rejects_non_string_id)
    check("assert_patterns_resolve/passes_for_valid_classes", test_assert_patterns_resolve_passes_for_valid_classes)
    check("validate_asset_classes/runs_post_generate_assert", test_validate_asset_classes_runs_post_generate_assert_when_clean)
    check("validate_fbx/requires_all_three_types", test_validate_fbx_requires_all_three_types)
    check("validate_fbx/warns_on_unknown_key", test_validate_fbx_warns_on_unknown_key)
    check("validate_fbx/accepts_shipped_unreal5_keys", test_validate_fbx_accepts_the_shipped_unreal5_preset_keys)
    check("validate_prefixes/requires_all", test_validate_prefixes_requires_static_and_skeletal_and_anim)
    check("validate_prefixes/passes_when_all_present", test_validate_prefixes_passes_when_all_present)
    check("validate_identity/requires_name", test_validate_identity_requires_name)
    check("validate_identity/rejects_underscore_leading_slug", test_validate_identity_rejects_underscore_leading_slug)
    check("validate_identity/flags_slug_collision", test_validate_identity_flags_slug_collision)
    check("validate_identity/clean_when_unique", test_validate_identity_clean_when_unique)
    check("validate_config/golden_authored_is_clean", test_validate_config_golden_authored_is_clean)
    check("validate_config/errors_before_warnings", test_validate_config_errors_before_warnings)
    check("validate_config/full_wiring_catches_every_area", test_validate_config_full_wiring_catches_every_area)
    check("validate_config/v2_rejects_absolute_subpaths_and_dirs", test_validate_config_v2_rejects_absolute_subpaths_and_dirs)
    check("validate_config/v2_rejects_absolute_dest_dir_and_traversal", test_validate_config_v2_rejects_absolute_dest_dir_and_traversal)
    check("validate_identity/reserves_settings_name", test_validate_identity_reserves_settings_name)
    check("validate_bindings/required_and_absolute", test_validate_bindings)
    check("validate_bindings/clean_and_overlap_warns", test_validate_bindings_clean_and_overlap_warns)


if __name__ == "__main__":
    run()
    print(f"\n{len(FAILURES)} failure(s).")
    sys.exit(1 if FAILURES else 0)
