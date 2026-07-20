# fs_shelf.py
# FabricatorStudio Maya shelf builder — reads JSON files from shelf_data/
# and builds shelves on startup. Call build_shelves() from userSetup.py
# via evalDeferred.
__author__ = "Adrian Melian"

import json
import os

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om

_SHELF_DATA_DIR = os.path.join(os.path.dirname(__file__), 'shelf_data')
# icons live at the REPO ROOT's icons/ folder, not under maya_tools/. From this
# file (maya_tools/framework/shelves/fs_shelf.py) walk up 4 levels to get to repo root.
_ICONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    'icons',
)


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _resolve_image(image: str) -> str:
    """Resolve an image name to a full path if it exists in icons/, else return as-is for Maya to resolve."""
    if not image:
        return 'commandButton.png'
    if os.path.isabs(image):
        return image
    candidate = os.path.join(_ICONS_DIR, image)
    if os.path.exists(candidate):
        return candidate
    return image  # fall back to Maya's icon search path (built-in icons)


def _normalise_image(resolved_path: str) -> str:
    """Inverse of _resolve_image — convert a resolved path back to a storable JSON value."""
    if not resolved_path or resolved_path == 'commandButton.png':
        return ''
    norm_icons = os.path.normcase(os.path.normpath(_ICONS_DIR))
    norm_path  = os.path.normcase(os.path.normpath(resolved_path))
    if norm_path.startswith(norm_icons):
        return os.path.basename(resolved_path)
    return resolved_path


def _read_button(child: str) -> dict:
    """Read a live shelfButton node and return a JSON-ready dict."""
    btn: dict = {
        'type':    'button',
        'label':   cmds.shelfButton(child, q=True, label=True)      or '',
        'tooltip': cmds.shelfButton(child, q=True, annotation=True)  or '',
        'image':   _normalise_image(cmds.shelfButton(child, q=True, image=True) or ''),
        'command': cmds.shelfButton(child, q=True, command=True)     or '',
    }

    dbl = cmds.shelfButton(child, q=True, doubleClickCommand=True) or ''
    if dbl:
        btn['doubleClickCommand'] = dbl

    popup_items: list[dict] = []
    for popup in (cmds.shelfButton(child, q=True, popupMenuArray=True) or []):
        for item in (cmds.popupMenu(popup, q=True, itemArray=True) or []):
            full_item = f'{popup}|{item}'
            if not cmds.menuItem(full_item, exists=True):
                full_item = item  # fallback: some Maya versions return full paths already
            if cmds.menuItem(full_item, q=True, divider=True):
                popup_items.append({'type': 'divider'})
            else:
                popup_items.append({
                    'type':    'item',
                    'label':   cmds.menuItem(full_item, q=True, label=True)   or '',
                    'command': cmds.menuItem(full_item, q=True, command=True) or '',
                })

    if popup_items:
        btn['popup'] = popup_items

    return btn


def _build_save_button(shelf: str, shelf_name: str) -> None:
    """Add the save-shelf button to the given shelfLayout."""
    cmd = (
        'import maya_tools.framework.shelves.fs_shelf as _s; '
        f'_s.save_shelf_to_json("{shelf_name}")'
    )
    cmds.shelfButton(
        label='Save Shelf',
        command=cmd,
        annotation='Save current shelf state to JSON',
        image1=_resolve_image('save.png'),
        parent=shelf,
        style='iconOnly',
        noDefaultPopup=True,
    )


def _build_popup(btn: str, items: list[dict]) -> None:
    """Add a right-click popup menu to a shelf button."""
    popup = cmds.popupMenu(button=3, parent=btn)
    for item in items:
        if item.get('type') == 'divider':
            cmds.menuItem(divider=True, parent=popup)
        else:
            cmds.menuItem(
                label=item.get('label', ''),
                command=item.get('command', ''),
                parent=popup,
            )


def _build_shelf(data: dict) -> None:
    """Build a single shelf from a data dict. Deletes any existing shelf with the same name first."""
    shelf_name = data['name']
    shelf_top_level = mel.eval('$_tmp = $gShelfTopLevel')

    if cmds.shelfLayout(shelf_name, exists=True):
        cmds.deleteUI(shelf_name, layout=True)

    shelf = cmds.shelfLayout(shelf_name, parent=shelf_top_level)

    for btn_data in data.get('buttons', []):
        btn_type = btn_data.get('type', 'button')

        if btn_type == 'separator':
            cmds.separator(style='shelf', horizontal=False, parent=shelf)
            continue

        if btn_type == 'save_button':
            _build_save_button(shelf, shelf_name)
            continue

        btn_kwargs = dict(
            label=btn_data.get('label', ''),
            command=btn_data.get('command', ''),
            annotation=btn_data.get('tooltip', btn_data.get('label', '')),
            image1=_resolve_image(btn_data.get('image', '')),
            parent=shelf,
            style='iconOnly',
            noDefaultPopup=True,
            # 64x64 reads cleaner than Maya's default 32x32 — RGBA Mindmeld
            # glyphs have the resolution and Maya scales them properly.
            width=64,
            height=64,
        )
        dbl = btn_data.get('doubleClickCommand')
        if dbl:
            btn_kwargs['doubleClickCommand'] = dbl

        btn = cmds.shelfButton(**btn_kwargs)

        popup_items = btn_data.get('popup', [])
        if popup_items:
            _build_popup(btn, popup_items)


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def save_shelf_to_json(shelf_name: str) -> None:
    """Serialise the live Maya shelf back to its shelf_data JSON file.

    Reads the current shelf state (including any edits made in Maya's Shelf Editor),
    strips the save button, and writes to shelf_data/<shelf_name>.json.
    """
    try:
        if not cmds.shelfLayout(shelf_name, exists=True):
            om.MGlobal.displayError(f'[FS] Shelf "{shelf_name}" does not exist.')
            return

        children = cmds.shelfLayout(shelf_name, q=True, childArray=True) or []
        buttons: list[dict] = []

        for child in children:
            ui_type = cmds.objectTypeUI(child)
            if ui_type == 'separator':
                buttons.append({'type': 'separator'})
            elif ui_type == 'shelfButton':
                btn_data = _read_button(child)
                if 'save_shelf_to_json' in btn_data.get('command', ''):
                    continue  # skip the save button itself
                buttons.append(btn_data)

        # Strip trailing separators
        while buttons and buttons[-1].get('type') == 'separator':
            buttons.pop()

        buttons.append({'type': 'save_button'})

        data = {'name': shelf_name, 'buttons': buttons}
        out_path = os.path.join(_SHELF_DATA_DIR, f'{shelf_name}.json')

        with open(out_path, 'w') as fh:
            json.dump(data, fh, indent=2)

        om.MGlobal.displayInfo(f'[FS] Shelf "{shelf_name}" saved to {out_path}')

    except Exception as exc:
        om.MGlobal.displayError(f'[FS] save_shelf_to_json failed: {exc}')


def _remove_stale_ks_shelves() -> None:
    """One-time cleanup: the ksModel/ksAnim shelves were renamed to
    fsModel/fsAnim (D6, 2026-07-11). Delete any surviving old-named
    shelfLayout tabs so users don't end up with duplicate stale tabs
    sitting next to the new ones. Remove after v1."""
    for stale_name in ('ksModel', 'ksAnim'):
        if cmds.shelfLayout(stale_name, exists=True):
            cmds.deleteUI(stale_name, layout=True)
            om.MGlobal.displayInfo(f'[FS] Removed stale shelf tab "{stale_name}".')


def build_shelves() -> None:
    """Read all JSON files in shelf_data/ and build (or rebuild) each shelf.

    Safe to call multiple times — deletes and recreates each shelf.
    Gated by the Settings > MAYA INTEGRATION 'load_shelf' pref (2026-07-18);
    the gate sits before any Maya call so it is headless-testable.
    """
    from maya_tools.framework.toolbar import toolbar_prefs
    if not toolbar_prefs.load_prefs().get('load_shelf', True):
        om.MGlobal.displayInfo('[FS] Shelf load disabled in Settings.')
        return
    if not os.path.isdir(_SHELF_DATA_DIR):
        om.MGlobal.displayWarning(f'[FS] shelf_data directory not found: {_SHELF_DATA_DIR}')
        return

    _remove_stale_ks_shelves()

    for filename in sorted(os.listdir(_SHELF_DATA_DIR)):
        if not filename.endswith('.json'):
            continue
        path = os.path.join(_SHELF_DATA_DIR, filename)
        try:
            with open(path, 'r') as fh:
                data = json.load(fh)
            _build_shelf(data)
            om.MGlobal.displayInfo(f'[FS] Shelf "{data["name"]}" built.')
        except Exception as exc:
            om.MGlobal.displayError(f'[FS] Failed to build shelf from {filename}: {exc}')


def rebuild_shelves() -> None:
    """Convenience alias — reload JSON and rebuild. Useful during development."""
    build_shelves()



"""
AMGeneral Layout:
Save Button
    Save Scene
    Save Scene As...
    Load Scene
    Reload Scene
Project Chooser Tool
Export Tool
Clipboard Button
    Copy Selected
    Copy Scene Name
    Copy Scene Directory
    Copy Scene Fullpath
Scene Cleanup Tool
Selection Helpers
    Select joints influencing selected
    Select objects influenced by selected joints
    select unskinned meshes
    Create locator at center of selected components
Rename Tools:
    Hash Renamer
    Grep Renamer
    Comet Renamer
    Prefix/Suffix Renamer
Snap A to B
Animation Tools:
    Pose Studio
    Animation Studio
    Reverse Animation Curve
Texture Tools:
    UV Editor
    SetCutSewUVTool
    UVPlanarProjectionOptions
    UVAutomaticProjection
    polyProjection -type Planar -md p 
Skeleton Tools:
    Save Skeleton
    Load Skeleton
    Fit Skeleton to Mesh Tool
Skinning Tools:
    Maya Skinning Tool
    AM Advanced Skinning Tool (to be made later)
Rigger Tool
smoothSkinWeightsCmd
AverageSkinWeights
CopySkinWeights on selected vertices
PasteSkinWeights to selected vertices

AMModeling Shelf
Save Button
    Save Scene
    Save Scene As...
    Load Scene
    Reload Scene
Project Chooser Tool
Export Tool
Clipboard Button
    Copy Selected
    Copy Scene Name
    Copy Scene Directory
    Copy Scene Fullpath
Scene Cleanup Tool
Selection Helpers
    Select joints influencing selected
    Select objects influenced by selected joints
    select unskinned meshes
    Create locator at center of selected components
Rename Tools:
    Hash Renamer
    Grep Renamer
    Comet Renamer
    Prefix/Suffix Renamer
Snap A to B
Display Face Noormals
Sculpt Geometry Tool
Extrude Selected Faces
SplitEdgeRingTool (Classic)
Bevel Edges
Delete Edge/Vertex
Merge Options
Toggle Hard/Soft Normals
Reverse Normals
Split polygon Tool
Combine selected meshes
Separate selected mesh
Mirror Geometry
Merge selected components to center
performPolyMerge 0
setToolTo polyAppendFacetContext ; polyAppendFacetCtx -e -pc `optionVar -q polyKeepFacetsPlanar` polyAppendFacetContext
Lattice Options
UV Editor
"""