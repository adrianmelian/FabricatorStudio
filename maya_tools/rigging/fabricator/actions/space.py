# Python/maya_tools/rigging/fabricator/actions/space.py
"""Generic space-switching handlers — used by every component that
declares space_consumers."""
__author__ = "Adrian Melian"

import maya.cmds as cmds
import maya.api.OpenMaya as om


def switch_with_match(component_id: str, ctrl: str, attr_name: str = 'space',
                       new_space: str = '') -> None:
    """Capture ctrl world transform; flip enum to new_space; set local
    transform to compensate so visual position doesn't jump.
    """
    if not cmds.objExists(ctrl):
        return
    if not cmds.attributeQuery(attr_name, node=ctrl, exists=True):
        return
    # Find new_space's enum index
    enum_str = cmds.attributeQuery(attr_name, node=ctrl, listEnum=True)
    if not enum_str:
        return
    names = enum_str[0].split(':')
    if new_space not in names:
        return
    new_idx = names.index(new_space)

    # Capture current world matrix
    cur_wm = cmds.xform(ctrl, q=True, ws=True, m=True)

    # Flip enum
    cmds.setAttr(f'{ctrl}.{attr_name}', new_idx)

    # Re-evaluate offsetParentMatrix
    cmds.dgdirty(f'{ctrl}.offsetParentMatrix')

    # Compute new local matrix to preserve world
    cmds.xform(ctrl, ws=True, m=cur_wm)


def switch_no_match(component_id: str, ctrl: str, attr_name: str = 'space',
                    new_space: str = '') -> None:
    """Just flip the enum; visual position will jump to wherever the new
    space + ctrl's local transform put it."""
    if not cmds.objExists(ctrl):
        return
    if not cmds.attributeQuery(attr_name, node=ctrl, exists=True):
        return
    enum_str = cmds.attributeQuery(attr_name, node=ctrl, listEnum=True)
    if not enum_str:
        return
    names = enum_str[0].split(':')
    if new_space in names:
        cmds.setAttr(f'{ctrl}.{attr_name}', names.index(new_space))
