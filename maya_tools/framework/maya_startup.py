# maya_startup.py
# Applies project-config-driven session settings on Maya startup.
# Call run() from userSetup.py via evalDeferred.
__author__ = "Adrian Melian"

import json
import logging
import os

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.framework import project_config_paths

# User-side location (<maya>/fabricator_project_configs). ensure_user_configs()
# creates + seeds/migrates on first touch; cheap no-op afterwards. Seeding can
# fail with OSError (AV file lock, disk full, unreadable source) — that is
# designed to be retryable on the next import, so it must never turn into an
# ImportError here (this module is imported at Maya startup).
# Fall back to the plain path: list_configs() returns []
# for a missing dir and run() reports the missing config via displayError.
# Logging only in the fallback — no om.MGlobal/cmds calls at import time.
try:
    _CONFIGS_DIR = str(project_config_paths.ensure_user_configs())
except OSError:
    _CONFIGS_DIR = str(project_config_paths.configs_dir())
    logging.getLogger(__name__).warning(
        '[FS] Could not seed user project configs at %s; '
        'continuing without seeding (will retry next session).',
        _CONFIGS_DIR, exc_info=True,
    )
_OPTVAR_KEY   = 'FS_activeProjectConfig'
# Pre-rebrand optionVar key (2026-07-11, Tools Engineer S3) — read as a
# one-time fallback when the new key is absent, then the new key is
# written and takes over. Remove after v1.
_OLD_OPTVAR_KEY = 'KSTools_activeProjectConfig'
_DEFAULT_CONFIG = 'unreal5'
_SESSION_FILE = 'session.json'

_DEFAULT_CAMERAS = ('persp', 'top', 'front', 'side')

_new_scene_callback_id: int | None = None


# ─────────────────────────────────────────────
# Config I/O
# ─────────────────────────────────────────────

def list_configs() -> list[str]:
    """Return project config names (subdirectory names under the user project-configs dir).

    Subdirectories starting with '_' are treated as templates and skipped.
    """
    if not os.path.isdir(_CONFIGS_DIR):
        return []
    return sorted(
        name for name in os.listdir(_CONFIGS_DIR)
        if os.path.isdir(os.path.join(_CONFIGS_DIR, name)) and not name.startswith('_')
    )


def load_config(config_name: str) -> dict:
    """Load <project>/session.json from the user project-configs dir."""
    path = os.path.join(_CONFIGS_DIR, config_name, _SESSION_FILE)
    if not os.path.exists(path):
        raise RuntimeError(f'Project session config not found: {path}')
    with open(path, 'r') as fh:
        return json.load(fh)


def get_active_config() -> str:
    """Return the last-applied config name, or the default if the persisted name
    no longer exists as a project folder (handles unreal5.json → archangel/ migration).
    """
    available = set(list_configs())
    if cmds.optionVar(exists=_OPTVAR_KEY):
        name = cmds.optionVar(q=_OPTVAR_KEY)
        if name in available:
            return name
    elif cmds.optionVar(exists=_OLD_OPTVAR_KEY):   # remove after v1
        name = cmds.optionVar(q=_OLD_OPTVAR_KEY)
        if name in available:
            return name
    if _DEFAULT_CONFIG in available:
        return _DEFAULT_CONFIG
    return next(iter(sorted(available)), '')


def set_active_config(config_name: str) -> None:
    cmds.optionVar(sv=(_OPTVAR_KEY, config_name))


# ─────────────────────────────────────────────
# Apply helpers
# ─────────────────────────────────────────────

def apply_units(config: dict) -> None:
    cmds.currentUnit(linear=config.get('linear_unit', 'cm'))
    cmds.currentUnit(angle=config.get('angular_unit', 'deg'))
    cmds.currentUnit(time=config.get('frame_rate', 'ntsc'))


def apply_camera_clips(config: dict) -> None:
    near = config.get('camera_near_clip', 1.0)
    far  = config.get('camera_far_clip', 1_000_000.0)
    for cam in _DEFAULT_CAMERAS:
        shape = _camera_shape(cam)
        if shape:
            cmds.setAttr(f'{shape}.nearClipPlane', near)
            cmds.setAttr(f'{shape}.farClipPlane',  far)


def apply_grid(config: dict) -> None:
    g = config.get('grid', {})
    kwargs = {}
    if 'size'      in g: kwargs['size']      = g['size']
    if 'spacing'   in g: kwargs['spacing']   = g['spacing']
    if 'divisions' in g: kwargs['divisions'] = g['divisions']
    if kwargs:
        cmds.grid(**kwargs)


def apply_playback(config: dict) -> None:
    start = config.get('playback_start')
    end   = config.get('playback_end')
    if start is None or end is None:
        return
    cmds.playbackOptions(
        animationStartTime=start, animationEndTime=end,
        minTime=start,             maxTime=end,
    )


def apply_misc(config: dict) -> None:
    depth = config.get('undo_depth', 200)
    cmds.undoInfo(state=True, infinity=False, length=depth)

    inc_enabled = config.get('incremental_saves', True)
    inc_max     = config.get('incremental_save_max', 25)
    cmds.optionVar(iv=('isIncrementalSaveEnabled', int(inc_enabled)))
    cmds.optionVar(iv=('incrementalSaveMaxBackups', inc_max))

    cmds.scriptEditorInfo(stackTrace=True)

    _load_fbx_plugin()


# ─────────────────────────────────────────────
# Internal
# ─────────────────────────────────────────────

def _camera_shape(cam_transform: str) -> str | None:
    if not cmds.objExists(cam_transform):
        return None
    shapes = cmds.listRelatives(cam_transform, shapes=True, type='camera') or []
    return shapes[0] if shapes else None


def _register_new_scene_callback() -> None:
    global _new_scene_callback_id
    if _new_scene_callback_id is not None:
        return

    def _on_new_scene(*_):
        try:
            cfg = load_config(get_active_config())
            apply_units(cfg)
            apply_camera_clips(cfg)
            apply_grid(cfg)
            apply_playback(cfg)
        except Exception as exc:
            om.MGlobal.displayWarning(f'[FS] Could not re-apply settings on new scene: {exc}')

    _new_scene_callback_id = om.MSceneMessage.addCallback(
        om.MSceneMessage.kAfterNew, _on_new_scene
    )


def _load_fbx_plugin() -> None:
    if not cmds.pluginInfo('fbxmaya', q=True, loaded=True):
        try:
            cmds.loadPlugin('fbxmaya', quiet=True)
        except Exception:
            cmds.warning('[FS] Could not load FBX plugin.')


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def run(config_name: str | None = None) -> None:
    """Apply all session settings from the named project config.

    If config_name is None, uses the last-persisted config (or the default).
    """
    if config_name is None:
        config_name = get_active_config()

    try:
        config = load_config(config_name)
    except RuntimeError as exc:
        om.MGlobal.displayError(f'[FS] {exc}')
        return

    apply_units(config)
    apply_camera_clips(config)
    apply_grid(config)
    apply_playback(config)
    apply_misc(config)
    set_active_config(config_name)
    _register_new_scene_callback()
    _register_anim_marking_menu()
    _install_status_line_button()
    _restore_toolbar()
    _maybe_first_run_onboarding()
    _maybe_check_updates()

    om.MGlobal.displayInfo(f'[FS] Project config "{config.get("name", config_name)}" applied.')


def _install_status_line_button() -> None:
    """Put the FS mark on Maya's Status Line (Adrian, 2026-07-10): one click
    toggles the FS toolbar. The designated user entry point once shelves and
    menus retire in the final polish pass. status_button.install() is
    batch-safe and idempotent; same guard posture as the marking menu."""
    try:
        from maya_tools.framework.toolbar import status_button
        status_button.install()
    except Exception as exc:
        om.MGlobal.displayWarning(
            f'[FS] Could not install the FS Status Line button: {exc}'
        )


def _restore_toolbar() -> None:
    """Bring the FS toolbar back exactly as the user left it (Adrian,
    2026-07-10): open last session -> open now; hidden last session -> stays
    hidden. toolbar_app.restore() already holds both halves (it reads the
    'visible' pref that toggle()/launch() maintain, and is batch-guarded);
    this is just the startup call it was waiting for."""
    try:
        from maya_tools.framework.toolbar import toolbar_app
        toolbar_app.restore()
    except Exception as exc:
        om.MGlobal.displayWarning(
            f'[FS] Could not restore the FS toolbar: {exc}'
        )


def _maybe_first_run_onboarding() -> None:
    """One-time onboarding moments (t34, Adrian 2026-07-10): the
    no-project welcome prompt and the Status-Line-button discovery
    callout. first_run.maybe_run is batch-guarded, deferred until the
    UI settles, and spends each moment's seen-flag at show time — never
    nags. Same guard posture as the marking menu / status button."""
    try:
        from maya_tools.framework import first_run
        first_run.maybe_run()
    except Exception as exc:
        om.MGlobal.displayWarning(
            f'[FS] First-run onboarding skipped: {exc}'
        )


def _maybe_check_updates() -> None:
    """Quiet startup update check (Adrian, 2026-08-04). Batch-safe,
    pref-gated (Settings > UPDATES), runs its network fetch on a worker
    thread and fades one toast when a newer toolset exists. Silent on
    every failure — offline is a normal state. Self-disables on linked
    and source installs; see updater_core.detect_install."""
    try:
        from maya_tools.framework.updater import updater_ui
        updater_ui.maybe_check_on_startup()
    except Exception as exc:
        om.MGlobal.displayWarning(f'[FS] Update check skipped: {exc}')


def _register_anim_marking_menu() -> None:
    """Install the Ctrl+Alt+RMB animation marking menu on viewPanes.

    Previously only registered when FSWindow opened — which never happens
    in animation scenes (animators work with referenced rigs). Registering
    at startup makes the menu available in every scene regardless of
    whether any KS tool window is opened.

    Gated by the Settings > MAYA INTEGRATION 'anim_marking_menu' pref
    (2026-07-18).
    """
    from maya_tools.framework.toolbar import toolbar_prefs
    if not toolbar_prefs.load_prefs().get('anim_marking_menu', True):
        om.MGlobal.displayInfo(
            '[FS] Animation marking menu disabled in Settings.')
        return
    try:
        from maya_tools.rigging.fabricator.ui import animation_menu
        animation_menu.register_menu()
    except Exception as exc:
        om.MGlobal.displayWarning(
            f'[FS] Could not register animation marking menu: {exc}'
        )
