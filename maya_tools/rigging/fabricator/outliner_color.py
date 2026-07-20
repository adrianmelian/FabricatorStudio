# maya_tools/rigging/fabricator/outliner_color.py
"""Outliner row coloring for built Fabricator rigs.

Paints three layers at the end of build_modules:
  - Top rig group (the <rig_label> transform): KS plasma_dim accent
  - Each component network node (fab_<id>): the component's
    CONTRACT.color (same color the palette swatch uses)
  - Each joint owned by a component (anywhere in its joints[]):
    the owning component's CONTRACT.color — matches the canvas
    row tint, single source of truth

Cleared at the start of unbuild_modules so Edit Mode shows a neutral
outliner — the paint/clear cycle is tied to the build/unbuild
lifecycle. Manual rebuild is the recovery path if colors drift
mid-Edit (e.g., a component was removed and its joints' tints went
stale).
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes
from maya_tools.rigging.fabricator.modules import get_component_class
from maya_tools.utils.qt.mindmeld import mindmeld_style


_TOP_GROUP_COLOR_HEX = mindmeld_style.PLASMA_DIM   # rig identity
_GEO_GROUP_COLOR_HEX = mindmeld_style.BONE_DIM      # static content
_RIG_GROUP_COLOR_HEX = mindmeld_style.EMBER_DIM     # rig machinery

# Structural plumbing groups + their identity colors. Three bands across
# the rig hierarchy: top group (plasma_dim, identity), geo (bone_dim,
# content), rig machinery (ember_dim — rig_grp + its ctrl/null children
# share one color so the rig-machinery subtree reads as one block).
_STRUCTURAL_GROUP_COLORS = {
    'geo_grp':         _GEO_GROUP_COLOR_HEX,
    'rig_grp':         _RIG_GROUP_COLOR_HEX,
    'fab_controls_grp': _RIG_GROUP_COLOR_HEX,
    'fab_nulls_grp':    _RIG_GROUP_COLOR_HEX,
}


def _hex_to_rgb(hex_str: str) -> tuple:
    """Convert '#RRGGBB' to (r, g, b) floats in [0, 1]. Returns neutral
    grey on malformed input rather than raising — coloring is cosmetic."""
    s = (hex_str or '').lstrip('#')
    if len(s) != 6:
        return (0.5, 0.5, 0.5)
    try:
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
    except ValueError:
        return (0.5, 0.5, 0.5)
    return (r, g, b)


def _set_outliner_color(node: str, rgb: tuple) -> None:
    if not cmds.objExists(node):
        return
    if not cmds.attributeQuery('useOutlinerColor', node=node, exists=True):
        return
    cmds.setAttr(f'{node}.useOutlinerColor', True)
    cmds.setAttr(f'{node}.outlinerColor', *rgb)


def _clear_outliner_color(node: str) -> None:
    if not cmds.objExists(node):
        return
    if not cmds.attributeQuery('useOutlinerColor', node=node, exists=True):
        return
    cmds.setAttr(f'{node}.useOutlinerColor', False)


def _rig_top_group() -> str:
    """Return the name of the rig's top group (the <rig_label> transform
    that wraps rig_grp + geo_grp + root joint), or '' if no built rig is
    in scene."""
    if not cmds.objExists('rig_grp'):
        return ''
    parents = cmds.listRelatives('rig_grp', parent=True, fullPath=False) or []
    return parents[0] if parents else ''


def paint_rig_outliner_colors() -> None:
    """Paint the rig's outliner colors. Safe to call multiple times —
    idempotent. No-op when no Fabricator registry is in scene."""
    if not nodes.get_registry():
        return

    top = _rig_top_group()
    if top:
        _set_outliner_color(top, _hex_to_rgb(_TOP_GROUP_COLOR_HEX))
    for grp, hex_color in _STRUCTURAL_GROUP_COLORS.items():
        _set_outliner_color(grp, _hex_to_rgb(hex_color))

    for cnode in nodes.get_all_component_nodes():
        ctype = nodes.get_component_type(cnode)
        try:
            cls = get_component_class(ctype)
        except KeyError:
            continue
        contract = cls.CONTRACT
        if not contract.color:
            continue
        rgb = _hex_to_rgb(contract.color)
        _set_outliner_color(cnode, rgb)
        for j in (nodes.get_component_joints(cnode) or []):
            _set_outliner_color(j, rgb)


def clear_rig_outliner_colors() -> None:
    """Inverse of paint — unset useOutlinerColor on the top group, every
    component network node, and every owned joint. Safe to call when no
    rig is in scene (no-op)."""
    if not nodes.get_registry():
        return

    top = _rig_top_group()
    if top:
        _clear_outliner_color(top)
    for grp in _STRUCTURAL_GROUP_COLORS:
        _clear_outliner_color(grp)

    for cnode in nodes.get_all_component_nodes():
        _clear_outliner_color(cnode)
        for j in (nodes.get_component_joints(cnode) or []):
            _clear_outliner_color(j)
