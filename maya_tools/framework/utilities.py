# utilities.py
# A collection of helper functions for Maya.
__author__ = "Adrian Melian"

import os
import time
import ctypes
from ctypes import wintypes

import maya.cmds as cmds

from maya_tools.framework.decorators import undo_chunk


# ---------------------------
# Small utilities / wrappers
# ---------------------------


def _active_model_panel():
    """Return a modelPanel to operate on (focused or the first available)."""
    try:
        panel = cmds.getPanel(withFocus=True)
        if panel and cmds.getPanel(typeOf=panel) == 'modelPanel':
            return panel
        # Fallback: first model panel
        panels = cmds.getPanel(type='modelPanel') or []
        if panels:
            return panels[0]
    except Exception:
        pass
    raise RuntimeError("No active modelPanel found. Open a viewport and try again.")


def _toggle_modelEditor_flag(flag_name):
    """Toggle a boolean show/hide flag on the active modelPanel."""
    panel = _active_model_panel()
    try:
        current = cmds.modelEditor(panel, q=True, **{flag_name: True})
        cmds.modelEditor(panel, e=True, **{flag_name: not current})
        print(f"✓ {flag_name} visibility is now {not current}")
        return not current
    except Exception as e:
        raise RuntimeError(f"Failed to toggle {flag_name!r} on {panel}: {e}")

def _set_modelEditor_flag(flag_name, value):
    """Set a boolean show/hide flag on the active modelPanel."""
    panel = _active_model_panel()
    try:
        cmds.modelEditor(panel, e=True, **{flag_name: value})
        print(f"✓ {flag_name} visibility is now {value}")
        return value
    except Exception as e:
        raise RuntimeError(f"Failed to toggle {flag_name!r} on {panel}: {e}")


# ---------------------------
# Viewport visibility toggles
# ---------------------------

def toggle_geometry():
    """Toggle visibility of polygon meshes in the active viewport."""
    return _toggle_modelEditor_flag('polymeshes')


def toggle_joints():
    """Toggle visibility of joints in the active viewport."""
    # Set joints to xray mode
    state = _toggle_modelEditor_flag('joints')
    return _set_modelEditor_flag('jointXray', state)


def toggle_nurbs_curves():
    """Toggle visibility of NURBS curves in the active viewport."""
    return _toggle_modelEditor_flag('nurbsCurves')


def toggle_grid():
    """Toggle grid visibility in the active viewport."""
    return _toggle_modelEditor_flag('grid')


def toggle_lights():
    """Toggle light object visibility in the active viewport."""
    return _toggle_modelEditor_flag('lights')


def toggle_cameras():
    """Toggle camera object visibility in the active viewport."""
    return _toggle_modelEditor_flag('cameras')

def restore_maya_windows():
    """
    Resets the position of all Maya UI windows to the top-left corner (0,0).
    This can help in restoring windows that are hidden off-screen.
    """
    windows = cmds.lsUI(windows=True) # Get a list of all UI windows

    for window in windows:
        try:
            # Attempt to set the top-left corner of the window to (0,0)
            # This will bring the window to the visible area of the main monitor.
            cmds.window(window, edit=True, topLeftCorner=(0, 0))
        except RuntimeError as e:
            # Catch potential errors if a window cannot be edited (e.g., non-editable internal windows)
            print(f"Could not edit window '{window}': {e}")


# ---------------------------
# Scene cleanup helpers
# ---------------------------

_DEFAULT_CAMERAS = {"persp", "top", "front", "side"}


def delete_extra_cameras():
    """Delete non-default, non-referenced cameras that aren't render/shot cameras.

    Keeps: persp, top, front, side and any referenced or camera used by the current
    render globals (as defaultRenderGlobals.imageFormat etc. do not track cameras,
    we check the modelPanel and render settings as best effort).
    """
    deleted = []
    try:
        all_cams = cmds.ls(type='camera') or []
        # Cameras can be shape nodes; get transforms to decide names
        for cam_shape in all_cams:
            transform = cmds.listRelatives(cam_shape, p=True, f=False) or []
            xform = transform[0] if transform else cam_shape
            short = xform.split('|')[-1]
            if short in _DEFAULT_CAMERAS:
                continue
            # skip referenced
            if cmds.referenceQuery(xform, isNodeReferenced=True) if cmds.objExists(xform) else False:
                continue
            # Skip Shot/sequence cameras (keep if referenced in sequencer)
            if cmds.listConnections(cam_shape, type='shot'):
                continue
            # Skip if it's the renderable camera in render settings
            try:
                if cmds.getAttr(cam_shape + ".renderable"):
                    # Don't auto-delete renderable cameras
                    continue
            except Exception:
                pass
            # Delete the transform (deletes shape too)
            try:
                cmds.delete(xform)
                deleted.append(short)
            except Exception:
                pass
    except Exception as e:
        raise RuntimeError(f"delete_extra_cameras failed: {e}")
    return deleted


def delete_unknown_nodes():
    """Delete unknown/unsupported nodes (often left from missing plugins)."""
    deleted = []
    try:
        for t in ('unknown', 'unknownDag', 'unknownTransform'):
            nodes = cmds.ls(type=t) or []
            if nodes:
                try:
                    cmds.delete(nodes)
                    deleted.extend(nodes)
                except Exception:
                    pass
    except Exception as e:
        raise RuntimeError(f"delete_unknown_nodes failed: {e}")
    return deleted


def delete_unknown_plugins():
    """Remove unknown plugins entries from the scene (not loadable in this Maya)."""
    removed = []
    try:
        # Some versions expose unknownPlugin command; guard accordingly
        if hasattr(cmds, 'unknownPlugin'):
            plugs = cmds.unknownPlugin(q=True, list=True) or []
            for p in plugs:
                try:
                    cmds.unknownPlugin(p, remove=True)
                    removed.append(p)
                except Exception:
                    pass
    except Exception as e:
        raise RuntimeError(f"delete_unknown_plugins failed: {e}")
    return removed


def delete_unused_shading_nodes():
    """Delete unused materials/shading networks (safe Maya built-in)."""
    try:
        cmds.hyperShade(removeUnusedNodes=True)
        return True
    except Exception as e:
        raise RuntimeError(f"delete_unused_shading_nodes failed: {e}")


def delete_empty_display_layers():
    """Delete empty display layers (ignores the default layer)."""
    deleted = []
    try:
        layers = (cmds.ls(type='displayLayer') or [])
        for lyr in layers:
            if lyr in {'defaultLayer'}:
                continue
            members = cmds.editDisplayLayerMembers(lyr, q=True) or []
            if not members:
                try:
                    cmds.delete(lyr)
                    deleted.append(lyr)
                except Exception:
                    pass
    except Exception as e:
        raise RuntimeError(f"delete_empty_display_layers failed: {e}")
    return deleted


def freeze_transforms_delete_history(selection_only=True):
    """Freeze transforms and delete history on current selection (or entire scene)."""
    try:
        targets = cmds.ls(sl=True, long=True) if selection_only else cmds.ls(geometry=True, long=True)
        if not targets:
            return []
        for t in targets:
            try:
                cmds.makeIdentity(t, a=True, t=True, r=True, s=True, n=False, pn=True)
            except Exception:
                pass
        try:
            cmds.delete(targets, ch=True)
        except Exception:
            pass
        return targets
    except Exception as e:
        raise RuntimeError(f"freeze_transforms_delete_history failed: {e}")


def clean_scene():
    """Run a suite of safe cleanups and return a summary dict."""
    with undo_chunk("Clean Scene"):
        cams = delete_extra_cameras()
        unknown_nodes = delete_unknown_nodes()
        unknown_plugs = delete_unknown_plugins()
        unused = delete_unused_shading_nodes()
        empty_layers = delete_empty_display_layers()
    return {
        'deleted_cameras': cams,
        'deleted_unknown_nodes': unknown_nodes,
        'removed_unknown_plugins': unknown_plugs,
        'deleted_unused_shading_nodes': bool(unused),
        'deleted_empty_display_layers': empty_layers,
    }


# ---------------------------
# World-space snapping
# ---------------------------


def snap_a_to_b(position=True, orientation=True):
    """Snap all selected objects (except the last) to the last selected object.

    Two selection modes:
      Pair mode:     [src1, src2, ..., target]
                     Sources snap to target's world transform (existing).
      Centroid mode: [src1, src2, ..., comp1, comp2, ...]
                     Sources snap to the world-space centroid of the
                     trailing component selection (edges, faces, verts, CVs).
                     Position-only; the orientation flag is ignored — there
                     is no canonical orientation for an arbitrary component
                     set. Useful for placing a joint at the centre of an
                     edge loop around a wrist, knee, etc.

    Components are detected by the presence of '.' in the selection string
    (e.g. 'pSphere1.e[10]'); transforms never have '.'.

    Args:
        position (bool): snap world position
        orientation (bool): snap world orientation (pair mode only)

    Returns:
        List of objects that were moved.
    """
    with undo_chunk("Snap objects"):
        selected = cmds.ls(sl=True) or []
        if len(selected) < 2:
            return []

        components = [s for s in selected if '.' in s]
        if components:
            sources = [s for s in selected if '.' not in s]
            if not sources:
                cmds.warning('Snap: select transform(s) before the components.')
                return []
            center = _components_centroid(components)
            if center is None:
                cmds.warning('Snap: could not resolve component positions.')
                return []
            for obj in sources:
                set_world_position(obj, center)
            cmds.select(sources)
            return sources

        target = selected[-1]
        source = selected[:-1]
        if position:
            dst_pos = get_world_position(target)
            for obj in source:
                set_world_position(obj, dst_pos)
        if orientation:
            dst_rot = get_world_orientation(target)
            for obj in source:
                set_world_orientation(obj, dst_rot)
        cmds.select(source)
        return source


def match_transform(position=False, rotation=False, scale=False, pivot=False):
    """Match all selected objects (except the last) to the last selected
    object via cmds.matchTransform (Maya's Modify > Match Transformations).

    Pair mode only: [src1, src2, ..., target], the same selection
    semantics as snap_a_to_b. Flags mirror Maya's own menu:
    position/rotation/scale map to matchTransform's pos/rot/scl; pivot
    matches the rotate + scale pivots (piv). All-False is Match All
    Transforms (position + rotation + scale, pivots untouched), the same
    as a flagless matchTransform call.

    Returns:
        List of objects that were matched.
    """
    with undo_chunk("Match transform"):
        selected = cmds.ls(sl=True, type='transform') or []
        if len(selected) < 2:
            cmds.warning('Match: select source transform(s), then the '
                         'target last.')
            return []
        flags = {}
        if position:
            flags['pos'] = True
        if rotation:
            flags['rot'] = True
        if scale:
            flags['scl'] = True
        if pivot:
            flags['piv'] = True
        target = selected[-1]
        source = selected[:-1]
        cmds.matchTransform(source + [target], **flags)
        cmds.select(source)
        return source


def create_locator_at_selection(orientation: bool = False) -> str:
    """Create a locator snapped to the current selection.

    Same selection semantics as snap_a_to_b:
      Components selected (verts / edges / faces / CVs):
          locator at the world-space centroid; orientation ignored.
      One or more transforms selected:
          locator at the LAST selected transform's world position
          (matches snap_a_to_b's "snap to last" convention).
      orientation=True with transforms: also matches the last transform's
          world orientation. Default False matches the shelf snap button.

    Returns the created locator name, or '' if nothing was created.
    """
    selected = cmds.ls(sl=True) or []
    if not selected:
        cmds.warning('Create Locator: nothing selected.')
        return ''

    with undo_chunk('Create Locator at Selection'):
        components = [s for s in selected if '.' in s]
        objects    = [s for s in selected if '.' not in s]
        loc = cmds.spaceLocator(name='pivot_loc#')[0]

        if components:
            # Components → centroid position, no orientation.
            center = _components_centroid(components)
            if center is None:
                cmds.warning('Create Locator: could not resolve component positions.')
                cmds.delete(loc)
                return ''
            set_world_position(loc, center)
        elif objects:
            # Match the LAST selected transform, like snap_a_to_b does.
            target = objects[-1]
            set_world_position(loc, get_world_position(target))
            if orientation:
                set_world_orientation(loc, get_world_orientation(target))
        else:
            cmds.warning('Create Locator: selection unresolved.')
            cmds.delete(loc)
            return ''

        cmds.select(loc, r=True)
        return loc


def _components_centroid(components):
    """World-space centroid of a component selection.

    Polygon components (edges/faces/verts) convert to vertices via
    `polyListComponentConversion`. NURBS CVs and any other addressable
    component fall through to direct `xform` query. Returns None if no
    positions can be resolved.
    """
    flat = cmds.ls(components, flatten=True) or []
    if not flat:
        return None

    verts = cmds.polyListComponentConversion(flat, toVertex=True) or []
    verts = cmds.ls(verts, flatten=True) or []
    points = verts or flat  # NURBS CVs xform directly

    positions = []
    for p in points:
        try:
            positions.append(cmds.xform(p, q=True, ws=True, t=True))
        except Exception:
            pass
    if not positions:
        return None

    n = len(positions)
    return [sum(c) / n for c in zip(*positions)]

def get_world_position(object_name) -> list:
    """
    Returns the world-space translation (position) of a given object.

    Args:
        object_name (str): The name of the Maya object.

    Returns:
        list: A list of three floats [x, y, z] representing the world-space position.
              Returns None if the object does not exist.
    """
    if cmds.objExists(object_name):
        world_position = cmds.xform(object_name, query=True, worldSpace=True, rotatePivot=True)
        return world_position
    else:
        raise Exception(f"Object '{object_name}' does not exist.")

def get_world_orientation(object_name) -> list:
    """
    Returns the world-space orientation (rotation) of a given object.

    Args:
        object_name (str): The name of the Maya object.

    Returns:
        list: A list of three floats [x, y, z] representing the world-space orientation.
              Returns [0,0,0] if the object does not exist.
    """
    if cmds.objExists(object_name):
        world_orientation = cmds.xform(object_name, query=True, worldSpace=True, rotation=True)
        return world_orientation
    else:
        raise Exception(f"Object '{object_name}' does not exist.")

def set_world_position(object_name, position) -> bool:
    """
    Sets the world-space position of a given object.

    Args:
        object_name (str): The name of the Maya object.
        position (list): A list of three floats [x, y, z] representing the new world-space position.

    Returns:
        bool: True if the position was successfully set, False otherwise.
    """
    if cmds.objExists(object_name):
        cmds.move(position[0], position[1], position[2], object_name, rotatePivotRelative=True)
        print(f"✓ {object_name} world position set to {position}")
        return True
    return False

def set_world_orientation(object_name, orientation) -> bool:
    """
    Sets the world-space orientation of a given object.

    Args:
        object_name (str): The name of the Maya object.
        orientation (list): A list of three floats [x, y, z] representing the new world-space orientation.

    Returns:
        bool: True if the orientation was successfully set, False otherwise.
    """
    if cmds.objExists(object_name):
        cmds.rotate(orientation[0], orientation[1], orientation[2], object_name)
        print(f"✓ {object_name} world orientation set to {orientation}")
        return True
    return False


# ---------------------------
# Selection helpers
# ---------------------------


def select_child_joints():
    """Select all joint descendants of the current selection.

    The originally selected node is included if it is itself a joint.
    Returns the new selection (sorted, deduplicated). Warns and returns []
    if nothing is selected or no joints are found.
    """
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning("Nothing selected.")
        return []

    joints = set()
    for node in sel:
        if cmds.nodeType(node) == 'joint':
            joints.add(node)
        descendants = cmds.listRelatives(node, allDescendents=True, type='joint', fullPath=True) or []
        joints.update(descendants)

    if not joints:
        cmds.warning("No joints found in selection or its descendants.")
        return []

    result = sorted(joints)
    cmds.select(result, replace=True)
    print(f"✓ Selected {len(result)} joint(s).")
    return result


# ─────────────────────────────────────────────
# Rig widgets that are meshes (Adrian, 2026-07-13)
# ─────────────────────────────────────────────
#
# The Armature's ctrl balls are polygon MESHES. They have to be: x-ray is
# `mesh.alwaysDrawOnTop`, and Maya does not expose that attribute on a
# nurbsSurface, so a ctrl that draws through the character CANNOT be a NURBS
# shape. The cost is that every scene-wide geometry scan can now see them.
#
# They are rig UI, not geometry. Anything asking "what meshes are in this
# scene" must exclude them, or a rigger in edit mode gets fifty ctrl balls back
# from "Select unskinned meshes" (they are, technically, unskinned meshes).
# Selection-driven tools are unaffected — nobody selects a ctrl ball and asks
# to skin it.

_ARMATURE_GRP = 'fab_armature_grp'


def is_rig_widget(node: str) -> bool:
    """True for a node that is rig UI rather than geometry — today, anything
    under the Armature group (its ctrl balls and guide lines)."""
    for path in cmds.ls(node, long=True) or []:
        if f'|{_ARMATURE_GRP}|' in path:
            return True
    return False


def scene_mesh_shapes() -> list:
    """Every real geometry mesh shape in the scene, full paths, intermediates
    and rig widgets excluded. The scene-wide counterpart to the selection-driven
    helpers below — use this instead of a bare cmds.ls(type='mesh')."""
    return [s for s in (cmds.ls(type='mesh', long=True,
                                noIntermediate=True) or [])
            if not is_rig_widget(s)]


def select_child_meshes():
    """Select all mesh-transform descendants of the current selection.

    The originally selected node is included if its shape is a non-intermediate
    mesh. Returns the new selection (sorted, deduplicated). Warns and returns []
    if nothing is selected or no meshes are found.
    """
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning("Nothing selected.")
        return []

    transforms = set()
    for node in sel:
        own_shapes = cmds.listRelatives(node, shapes=True, type='mesh',
                                        noIntermediate=True, fullPath=True) or []
        if own_shapes:
            transforms.add(node)
        descendant_shapes = cmds.listRelatives(node, allDescendents=True, type='mesh',
                                               noIntermediate=True, fullPath=True) or []
        for shape in descendant_shapes:
            parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            if parent:
                transforms.add(parent[0])

    if not transforms:
        cmds.warning("No meshes found in selection or its descendants.")
        return []

    result = sorted(transforms)
    cmds.select(result, replace=True)
    print(f"✓ Selected {len(result)} mesh(es).")
    return result


# ---------------------------
# Skin Weight Copy/Paste
# ---------------------------

_SKIN_WEIGHTS_CLIPBOARD = {}


def copy_skin_weights():
    """
    Copy skin weights from selected vertex/vertices.
    If multiple vertices are selected, averages the weights across them.

    Returns:
        dict: Dictionary containing influence weights, or empty dict on failure.
    """
    selection = cmds.ls(sl=True, fl=True)
    if not selection:
        cmds.warning("No vertices selected. Select at least one vertex to copy skin weights.")
        return {}

    # Filter to get only vertices
    vertices = [v for v in selection if '.vtx[' in v]
    if not vertices:
        cmds.warning("No vertices in selection. Select at least one vertex to copy skin weights.")
        return {}

    # Get the mesh from the first vertex
    mesh = vertices[0].split('.')[0]

    # Find the skinCluster attached to this mesh
    skin_cluster = None
    history = cmds.listHistory(mesh, pdo=True) or []
    for node in history:
        if cmds.nodeType(node) == 'skinCluster':
            skin_cluster = node
            break

    if not skin_cluster:
        cmds.warning(f"No skinCluster found on mesh '{mesh}'.")
        return {}

    # Get all influences in the skinCluster
    influences = cmds.skinCluster(skin_cluster, q=True, inf=True) or []

    # Initialize weight accumulator
    weight_sum = {inf: 0.0 for inf in influences}

    # Collect weights from all selected vertices
    for vtx in vertices:
        weights = cmds.skinPercent(
            skin_cluster,
            vtx,
            q=True,
            value=True
        )
        for i, inf in enumerate(influences):
            weight_sum[inf] += weights[i]

    # Average the weights
    num_vertices = len(vertices)
    averaged_weights = {inf: weight_sum[inf] / num_vertices for inf in influences}

    # Filter out zero weights
    filtered_weights = {inf: wgt for inf, wgt in averaged_weights.items() if wgt > 0.0001}

    # Store in clipboard
    global _SKIN_WEIGHTS_CLIPBOARD
    _SKIN_WEIGHTS_CLIPBOARD = {
        'influences': filtered_weights,
        'skin_cluster': skin_cluster,
        'mesh': mesh
    }

    print(f"✓ Copied skin weights from {num_vertices} vertex/vertices:")
    for inf, wgt in filtered_weights.items():
        print(f"  {inf}: {wgt:.4f}")

    return filtered_weights


def paste_skin_weights():
    """
    Paste previously copied skin weights to selected vertex/vertices.

    Returns:
        list: List of vertices that were modified.
    """
    global _SKIN_WEIGHTS_CLIPBOARD

    if not _SKIN_WEIGHTS_CLIPBOARD:
        cmds.warning("No skin weights in clipboard. Use copy_skin_weights() first.")
        return []

    selection = cmds.ls(sl=True, fl=True)
    if not selection:
        cmds.warning("No vertices selected. Select at least one vertex to paste skin weights.")
        return []

    # Filter to get only vertices
    vertices = [v for v in selection if '.vtx[' in v]
    if not vertices:
        cmds.warning("No vertices in selection. Select at least one vertex to paste skin weights.")
        return []

    # Get the mesh from the first vertex
    mesh = vertices[0].split('.')[0]

    # Find the skinCluster attached to this mesh
    skin_cluster = None
    history = cmds.listHistory(mesh, pdo=True) or []
    for node in history:
        if cmds.nodeType(node) == 'skinCluster':
            skin_cluster = node
            break

    if not skin_cluster:
        cmds.warning(f"No skinCluster found on mesh '{mesh}'.")
        return []

    # Get influences from the current skinCluster
    current_influences = cmds.skinCluster(skin_cluster, q=True, inf=True) or []

    # Prepare transform values for skinPercent
    copied_influences = _SKIN_WEIGHTS_CLIPBOARD['influences']
    transform_values = []

    for inf, wgt in copied_influences.items():
        if inf in current_influences:
            transform_values.append((inf, wgt))
        else:
            cmds.warning(f"Influence '{inf}' from clipboard not found in target skinCluster. Skipping.")

    if not transform_values:
        cmds.warning("No matching influences found between copied and target skin weights.")
        return []

    # Apply weights to all selected vertices
    with undo_chunk("Paste Skin Weights"):
        for vtx in vertices:
            cmds.skinPercent(
                skin_cluster,
                vtx,
                transformValue=transform_values,
                normalize=True
            )

    print(f"✓ Pasted skin weights to {len(vertices)} vertex/vertices.")

    return vertices


# ---------------------------
# Clipboard helpers
# ---------------------------

def copy_fullpath_to_clipboard():
    """Copy the current scene name to the clipboard."""
    filename = cmds.file(q=True, sn=True)
    copy_to_clipboard(filename)
    print(f"✓ Copied {filename!r} to clipboard.")

def copy_folder_to_clipboard():
    """Copy the current scene folder to the clipboard."""
    filename = os.path.dirname(cmds.file(q=True, sn=True))
    copy_to_clipboard(filename)
    print(f"✓ Copied {filename!r} to clipboard.")

def copy_scenename_to_clipboard():
    """Copy the current scene filename to the clipboard."""
    filename = os.path.basename(cmds.file(q=True, sn=True))
    copy_to_clipboard(filename)
    print(f"✓ Copied {filename!r} to clipboard.")

def copy_to_clipboard(text: str, retries: int = 6, base_delay: float = 0.02) -> None:
    """
    Copy `text` to the Windows clipboard using pure WinAPI (no windows/processes).
    - Retries briefly if the clipboard is busy.
    - Converts LF to CRLF (per CF_UNICODETEXT expectations).

    Raises OSError/MemoryError on failure.
    """
    # --- WinAPI setup ---
    user32   = ctypes.WinDLL("user32",  use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Constants
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE  = 0x0002

    # Prototypes (critical on 64-bit!)
    kernel32.GlobalAlloc.restype  = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)

    kernel32.GlobalLock.restype   = wintypes.LPVOID
    kernel32.GlobalLock.argtypes  = (wintypes.HGLOBAL,)

    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalUnlock.argtypes= (wintypes.HGLOBAL,)

    kernel32.GlobalFree.restype   = wintypes.HGLOBAL
    kernel32.GlobalFree.argtypes  = (wintypes.HGLOBAL,)

    user32.OpenClipboard.restype  = wintypes.BOOL
    user32.OpenClipboard.argtypes = (wintypes.HWND,)

    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes= ()

    user32.SetClipboardData.restype  = wintypes.HANDLE
    user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)

    user32.CloseClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes= ()

    if not isinstance(text, str):
        text = str(text)

    # CF_UNICODETEXT convention: CRLF line endings and double-NUL terminator
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    data = text.encode("utf-16-le") + b"\x00\x00"

    # Allocate global moveable memory and copy the bytes into it
    h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h_global:
        raise MemoryError("GlobalAlloc failed", ctypes.get_last_error())

    lpvoid = kernel32.GlobalLock(h_global)
    if not lpvoid:
        kernel32.GlobalFree(h_global)
        raise MemoryError("GlobalLock failed", ctypes.get_last_error())

    try:
        ctypes.memmove(lpvoid, data, len(data))
    finally:
        kernel32.GlobalUnlock(h_global)

    # Open the clipboard (retry if busy)
    for attempt in range(retries):
        if user32.OpenClipboard(None):
            break
        time.sleep(base_delay * (attempt + 1))
    else:
        kernel32.GlobalFree(h_global)
        raise OSError("OpenClipboard failed (clipboard busy)", ctypes.get_last_error())

    try:
        if not user32.EmptyClipboard():
            raise OSError("EmptyClipboard failed", ctypes.get_last_error())

        # On success, ownership of h_global transfers to the system
        if not user32.SetClipboardData(CF_UNICODETEXT, h_global):
            raise OSError("SetClipboardData failed", ctypes.get_last_error())
        h_global = None
    finally:
        user32.CloseClipboard()
        if h_global:
            # If SetClipboardData failed, we still own the memory—free it.
            kernel32.GlobalFree(h_global)
