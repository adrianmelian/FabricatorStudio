"""No-project export tests. Run:
PYTHONNOUSERSITE=1 PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen mayapy maya_tools/export/_dev/test_no_project_export.py

A project config is an autofill convenience, never a gate (Adrian,
2026-07-12): a scene outside every registered project still exports —
the scene-saved Output Dir override carries the destination and the
built-in DEFAULT_FBX_PRESETS carry the FBX settings. Being in a project
only buys the auto-resolved path, prefixes, and configured presets.

FABRICATOR_PROJECT_CONFIGS is pointed at a temp dir BEFORE maya_tools
imports so the suite never reads (or seeds into) the real user configs.

PYTHONNOUSERSITE=1 is LOAD-BEARING: a user-site numpy 2.x collides with
Maya's bundled numpy inside the shiboken6 import (see test_phase_a.py)."""
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

# Isolate project configs BEFORE export_core import: _PROJECT_CONFIGS_DIR is
# captured at module import. The packaged templates seed into this temp root,
# but their project_root placeholders (C:/YourUnrealProject, ...) can never
# match a temp-dir scene path, so the scene is genuinely out-of-project.
_CONFIGS_TMP = tempfile.mkdtemp(prefix='fs_test_configs_')
os.environ['FABRICATOR_PROJECT_CONFIGS'] = _CONFIGS_TMP

import maya.standalone
maya.standalone.initialize(name='python')

import maya.cmds as cmds

from maya_tools.export import export_core, exporter_app, anim_core, anim_app

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


SCENE_DIR = Path(tempfile.mkdtemp(prefix='fs_test_scene_'))
OUT_DIR   = Path(tempfile.mkdtemp(prefix='fs_test_out_'))


def _build_scene():
    """Minimal skinned scene saved OUTSIDE any project root."""
    cmds.file(new=True, force=True)
    joint = cmds.joint(name='root_jnt', p=(0, 0, 0))
    cube  = cmds.polyCube(name='body_geo')[0]
    cmds.skinCluster(joint, cube)
    scene = SCENE_DIR / 'no_project_scene.ma'
    cmds.file(rename=str(scene))
    cmds.file(save=True, type='mayaAscii')
    return joint, cube, scene


JOINT, CUBE, SCENE = _build_scene()


# ── Core helpers ─────────────────────────────────────────────────────────────

def test_config_or_none_is_none_out_of_project():
    assert export_core.load_project_config_or_none(str(SCENE)) is None
    try:
        export_core.load_project_config(str(SCENE))
    except RuntimeError:
        pass
    else:
        raise AssertionError('load_project_config should still raise')


def test_fbx_preset_fallback_chain():
    # No config at all -> built-in defaults.
    p = export_core.fbx_preset(None, export_core.TYPE_SKELETAL)
    assert p['FBXExportSkins'] is True and p['FBXExportUpAxis'] == 'y'
    # Config with an EMPTY preset for the type -> built-in defaults.
    p = export_core.fbx_preset({'fbx_presets': {'static_mesh': {}}},
                               export_core.TYPE_STATIC)
    assert p == export_core.DEFAULT_FBX_PRESETS[export_core.TYPE_STATIC]
    # Config with a real preset -> the config's preset wins.
    mine = {'FBXExportSkins': False}
    p = export_core.fbx_preset({'fbx_presets': {'animation': mine}}, 'animation')
    assert p is mine


# ── Rig exporter (entries) ───────────────────────────────────────────────────

ENTRY = export_core.create_entry('reggie', export_core.TYPE_SKELETAL,
                                 [JOINT, CUBE])


def test_no_project_no_output_dir_raises_instruction():
    assert export_core.get_output_dir_override() == ''
    try:
        exporter_app.export_entry(ENTRY)
    except RuntimeError as exc:
        assert 'Output Dir' in str(exc), f'unhelpful message: {exc}'
    else:
        raise AssertionError('expected RuntimeError with Output Dir instructions')


def test_saved_output_dir_carries_the_export():
    export_core.set_output_dir_override(str(OUT_DIR))
    cmds.file(save=True)          # override lives on a scene node
    written = exporter_app.export_entry(ENTRY)
    assert Path(written).is_file(), f'no FBX written: {written}'
    assert Path(written).parent == OUT_DIR


def test_export_all_no_longer_skips():
    written = exporter_app.export_all()
    assert len(written) == 1, f'expected 1 export, got {written}'


def test_one_button_uses_saved_output_dir():
    cmds.select([JOINT, CUBE], replace=True)
    written = exporter_app.export_one_button()
    assert Path(written).is_file()
    assert Path(written).parent == OUT_DIR
    assert Path(written).name.startswith('SK_')   # built-in prefix fallback


def test_ui_dest_override_still_wins_over_saved():
    with tempfile.TemporaryDirectory(prefix='fs_test_ui_dir_') as ui_dir:
        written = exporter_app.export_entry(ENTRY, dest_override=ui_dir)
        assert Path(written).parent == Path(ui_dir)


# ── Anim exporter ────────────────────────────────────────────────────────────

def test_add_clip_from_range_works_without_project():
    clip = anim_app.add_clip_from_range()     # raised RuntimeError before fix
    data = anim_core.get_clip_data(clip)
    assert data['prefix'] == ''               # no config -> no anim prefix
    # validate_clip must tolerate config=None, cinematic kind included.
    data['output_kind'] = anim_core.KIND_CINEMATIC
    anim_core.validate_clip(data, None)


def test_export_clip_resolution_without_project():
    cmds.file(save=True)
    clip = anim_core.list_clips()[0]
    real_validate, real_run = anim_core.validate_clip, anim_app.anim_pipeline.run_export
    anim_core.validate_clip = lambda d, c: ([], [])
    anim_app.anim_pipeline.run_export = lambda d, out_path, c: out_path
    try:
        # No override anywhere -> instruction, not a config crash.
        anim_core.set_output_dir_override('')
        cmds.file(save=True)
        try:
            anim_app.export_clip(clip)
        except RuntimeError as exc:
            assert 'Output Dir' in str(exc), f'unhelpful message: {exc}'
        else:
            raise AssertionError('expected RuntimeError with Output Dir instructions')
        # Scene-saved anim Output Dir -> export resolves under it.
        anim_core.set_output_dir_override(str(OUT_DIR))
        cmds.file(save=True)
        out = anim_app.export_clip(clip)
        assert Path(out).parent == OUT_DIR
    finally:
        anim_core.validate_clip = real_validate
        anim_app.anim_pipeline.run_export = real_run


check('config_or_none is None out of project', test_config_or_none_is_none_out_of_project)
check('fbx_preset fallback chain', test_fbx_preset_fallback_chain)
check('no project + no Output Dir -> instruction', test_no_project_no_output_dir_raises_instruction)
check('saved Output Dir carries the export', test_saved_output_dir_carries_the_export)
check('export_all no longer skips', test_export_all_no_longer_skips)
check('one-button uses saved Output Dir', test_one_button_uses_saved_output_dir)
check('UI dest_override still wins over saved', test_ui_dest_override_still_wins_over_saved)
check('add clip works without project', test_add_clip_from_range_works_without_project)
check('export_clip resolution without project', test_export_clip_resolution_without_project)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print('ALL PASS (9)')
