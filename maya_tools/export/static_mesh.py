"""Static mesh export — non-destructive, single FBX writeout."""
__author__ = "Adrian Melian"

import maya.cmds as cmds
import maya.mel as mel

from maya_tools.export import export_core


def export_static(meshes: list[str], out_path: str, fbx_preset: dict) -> str:
    """Export the given mesh transforms (and their groups) to a single FBX.

    Non-destructive: scene state is identical before and after.
    """
    if not meshes:
        raise RuntimeError("export_static called with no meshes.")

    missing = [m for m in meshes if not cmds.objExists(m)]
    if missing:
        raise RuntimeError(f"Static export: missing nodes: {', '.join(missing)}")

    selection_backup = cmds.ls(sl=True) or []
    try:
        export_core.apply_fbx_preset(fbx_preset)
        cmds.select(meshes, replace=True)
        mel.eval('FBXExport -f "{}" -s;'.format(out_path.replace('\\', '/')))
    finally:
        if selection_backup:
            cmds.select(selection_backup, replace=True)
        else:
            cmds.select(clear=True)

    return out_path
