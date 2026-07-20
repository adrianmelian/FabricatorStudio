# maya_tools/animation/anim_utils.py
"""Animation snap utility.

Single consumed entry point: snap(). Bound to the FabricatorStudio hotkey set
(Alt+Shift+S, "snapper") — see framework/hotkeys/hotkey_data/fs_hotkeys.json.

Everything else that used to live in this module (v1 rig-menu popup,
FK/IK match, pose mirroring, graph-editor range fitting, etc.) has been
dropped: zero live callers anywhere in the current tree once the legacy
rigger (rigging/rigger/, excluded from packaging) is discounted. The
Fabricator marking menu (rigging/fabricator/ui/animation_menu.py) and
Anim Helpers (animation/anim_helpers/anim_helpers_app.py) are the current
homes for FK/IK matching and pose mirroring respectively.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.framework.decorators import undo_chunk


def snap(source: list | None = None, target: str = '', move: bool = True, rotate: bool = True) -> None:
    """Match one or more source transforms to a target transform's world
    position and/or rotation.

    Args:
        source: Nodes to move. If omitted, uses all but the last of the
            current selection.
        target: Node to match to. If omitted, uses the last selected node.
        move: Match world translation.
        rotate: Match world rotation.

    Requires at least two selected nodes when source/target aren't passed
    explicitly (source = selection[:-1], target = selection[-1]). Warns
    and no-ops if fewer than two are available. Re-selects `source` on
    completion, matching the legacy tool's behavior so a follow-up hotkey
    or menu action still has the right nodes selected.
    """
    if not source:
        source = []
    if not source or not target:
        selected = cmds.ls(sl=True) or []
        if len(selected) < 2:
            cmds.warning('snap: select two or more objects (sources..., target). Aborting.')
            return
        source = selected[:-1]
        target = selected[-1]

    if not cmds.objExists(target):
        cmds.warning(f'snap: target {target!r} does not exist. Aborting.')
        return

    t_pos = cmds.xform(target, query=True, worldSpace=True, rotatePivot=True)
    t_rot = cmds.xform(target, query=True, worldSpace=True, rotation=True)

    with undo_chunk('Snap'):
        for node in source:
            if not cmds.objExists(node):
                cmds.warning(f'snap: {node!r} does not exist, skipping.')
                continue
            if move:
                cmds.move(t_pos[0], t_pos[1], t_pos[2], node,
                          absolute=True, rotatePivotRelative=True)
            if rotate:
                cmds.xform(node, worldSpace=True, rotation=t_rot)

    cmds.select(source)
