# fs_hotkeys.py
# FabricatorStudio Maya hotkey builder — reads JSON files from hotkey_data/
# and applies them into the FabricatorStudio hotkey set on startup.
# Call build_hotkeys() from userSetup.py.
__author__ = "Adrian Melian"

import json
import os
import re

import maya.cmds as cmds
import maya.api.OpenMaya as om

_HOTKEY_DATA_DIR = os.path.join(os.path.dirname(__file__), 'hotkey_data')
_HOTKEY_SET = "FabricatorStudio"
# Pre-rebrand hotkey set name (2026-07-11, Tools Engineer S3) — migrated
# in place (renamed, preserving user edits) on the first build_hotkeys()
# call after upgrade. Remove after v1.
_OLD_HOTKEY_SET = "KSTools"
# Pre-rebrand nameCommand prefix — stale AM_ commands left over inside a
# migrated set are replaced (deleted then recreated under FAB_) by the
# build below. Remove after v1.
_OLD_NC_PREFIX = "AM_"


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _nc_name(name: str, release: bool = False) -> str:
    safe = re.sub(r'\W+', '_', name).strip('_')
    return f"FAB_{safe}_release_NC" if release else f"FAB_{safe}_NC"


def _old_nc_name(name: str, release: bool = False) -> str:
    """Pre-rebrand nameCommand name for `name` — used only to find and
    delete stale AM_-prefixed commands left behind by a migrated hotkey
    set. Remove after v1."""
    safe = re.sub(r'\W+', '_', name).strip('_')
    return f"{_OLD_NC_PREFIX}{safe}_release_NC" if release else f"{_OLD_NC_PREFIX}{safe}_NC"


def _delete_stale_nc(nc_name: str) -> None:
    """Delete a stale nameCommand left over from before the rebrand, if
    it exists. Best-effort — never blocks the build. Remove after v1."""
    try:
        if cmds.nameCommand(nc_name, q=True, exists=True):
            cmds.deleteUI(nc_name, nameCommand=True)
    except Exception:
        pass


def _migrate_old_hotkey_set() -> None:
    """One-time migration (2026-07-11, Tools Engineer S3): if the
    pre-rebrand 'KSTools' hotkey set exists and the new
    'FabricatorStudio' set doesn't yet, rename it in place so the
    user's edits carry over, then let the normal build proceed against
    the renamed set. Remove after v1."""
    existing = cmds.hotkeySet(q=True, hotkeySetArray=True) or []
    if _OLD_HOTKEY_SET in existing and _HOTKEY_SET not in existing:
        try:
            cmds.hotkeySet(_OLD_HOTKEY_SET, edit=True, name=_HOTKEY_SET)
            om.MGlobal.displayInfo(
                f'[FS] Migrated hotkey set "{_OLD_HOTKEY_SET}" -> "{_HOTKEY_SET}".')
        except Exception as exc:
            om.MGlobal.displayError(f'[FS] Hotkey set migration failed: {exc}')


def _ensure_hotkey_set() -> None:
    _migrate_old_hotkey_set()
    existing = cmds.hotkeySet(q=True, hotkeySetArray=True) or []
    if _HOTKEY_SET not in existing:
        cmds.hotkeySet(_HOTKEY_SET, source="Maya_Default")
        om.MGlobal.displayInfo(f'[FS] Created hotkey set "{_HOTKEY_SET}" (sourced from Maya_Default).')
    cmds.hotkeySet(_HOTKEY_SET, edit=True, current=True)


def _register_nc(nc_name: str, annotation: str, command: str, language: str = "python") -> None:
    # cmds.nameCommand(sourceType="python") is unreliable — Maya executes the string as MEL.
    # Wrap Python commands in MEL's python("...") instead; always use sourceType="mel".
    if language == "python":
        escaped = command.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        mel_cmd = f'python("{escaped}")'
    else:
        mel_cmd = command

    try:
        cmds.nameCommand(nc_name, annotation=annotation, command=mel_cmd, sourceType="mel")
    except RuntimeError:
        cmds.nameCommand(nc_name, edit=True, annotation=annotation, command=mel_cmd, sourceType="mel")


def _apply_entry(entry: dict) -> None:
    key = entry.get("key", "").strip()
    name = entry.get("name", "").strip()

    if not key:
        om.MGlobal.displayWarning('[FS] Hotkey entry missing "key" field — skipping.')
        return
    if not name:
        om.MGlobal.displayWarning('[FS] Hotkey entry missing "name" field — skipping.')
        return

    annotation  = entry.get("annotation", name)
    command     = entry.get("command", "").strip()
    language    = entry.get("language", "python")
    release_cmd = (entry.get("release_command") or "").strip()

    hotkey_kwargs = {
        "keyShortcut":   key,
        "ctrlModifier":  bool(entry.get("ctrl",  False)),
        "shiftModifier": bool(entry.get("shift", False)),
        "altModifier":   bool(entry.get("alt",   False)),
    }

    if command:
        nc = _nc_name(name)
        _delete_stale_nc(_old_nc_name(name))       # remove after v1
        _register_nc(nc, annotation, command, language)
        cmds.hotkey(name=nc, **hotkey_kwargs)

    if release_cmd:
        nc_rel = _nc_name(name, release=True)
        _delete_stale_nc(_old_nc_name(name, release=True))   # remove after v1
        _register_nc(nc_rel, f"{annotation} (release)", release_cmd, language)
        cmds.hotkey(releaseName=nc_rel, **hotkey_kwargs)


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def build_hotkeys() -> None:
    """Read all JSON files in hotkey_data/ and apply hotkeys into the FabricatorStudio hotkey set.

    Creates the FabricatorStudio set (sourced from Maya_Default) if it doesn't exist. If the
    pre-rebrand 'KSTools' set exists from an earlier install, it is renamed in
    place first so the user's edits carry over (remove after v1).
    Safe to call multiple times — re-registers nameCommands and reassigns bindings.

    No-op in batch mode — cmds.hotkeySet isn't available without a UI, so
    spawning maya.exe -batch (e.g. via the Scene Batch tool) would otherwise
    spit a traceback from the userSetup.py deferred call.

    Gated by the Settings > MAYA INTEGRATION 'load_hotkeys' pref
    (2026-07-18); the gate sits before any Maya call so it is
    headless-testable.
    """
    from maya_tools.framework.toolbar import toolbar_prefs
    if not toolbar_prefs.load_prefs().get('load_hotkeys', True):
        om.MGlobal.displayInfo('[FS] Hotkey load disabled in Settings.')
        return
    if cmds.about(batch=True):
        return
    if not os.path.isdir(_HOTKEY_DATA_DIR):
        om.MGlobal.displayWarning(f'[FS] hotkey_data directory not found: {_HOTKEY_DATA_DIR}')
        return

    _ensure_hotkey_set()

    total = 0
    for filename in sorted(os.listdir(_HOTKEY_DATA_DIR)):
        if not filename.endswith('.json'):
            continue
        path = os.path.join(_HOTKEY_DATA_DIR, filename)
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
        except Exception as exc:
            om.MGlobal.displayError(f'[FS] Failed to read {filename}: {exc}')
            continue

        entries = data.get("hotkeys", [])
        applied = 0
        for entry in entries:
            try:
                _apply_entry(entry)
                applied += 1
            except Exception as exc:
                om.MGlobal.displayError(
                    f'[FS] Failed to apply hotkey "{entry.get("name", "?")}" '
                    f'from {filename}: {exc}'
                )
        total += applied
        om.MGlobal.displayInfo(f'[FS] {filename}: {applied}/{len(entries)} hotkeys applied.')

    om.MGlobal.displayInfo(f'[FS] Hotkey build complete — {total} total bindings.')


def rebuild_hotkeys() -> None:
    """Convenience alias for iterative development / menu trigger."""
    build_hotkeys()
