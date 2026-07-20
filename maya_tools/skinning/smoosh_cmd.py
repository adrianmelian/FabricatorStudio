"""Python entry for the skinSmoosh MPx plugin command (skinSmoosh.py).

The plugin file defines cmds.skinSmoosh but is not importable as a
function; menu/shelf wiring inlines a load-plugin dance (fs_menu.json:155).
This module makes that dance importable so a manifest command string can
drive the toolbar's strength slider: cmds.skinSmoosh(a=<alpha>).
Undo is plugin-native (MPxCommand.isUndoable), no undo_chunk needed.
"""
from __future__ import annotations

import os

import maya.cmds as cmds


def ensure_loaded():
    if not cmds.pluginInfo("skinSmoosh", query=True, loaded=True):
        import maya_tools.skinning as skinning
        cmds.loadPlugin(os.path.join(os.path.dirname(skinning.__file__),
                                     "skinSmoosh.py"))


def smoosh(alpha=0.3, iterations=1):
    """Smooth skin weights on the selected verts/faces/edges.
    alpha: 0..1 strength (the slider's dial). iterations: passes (>=1)."""
    ensure_loaded()
    cmds.skinSmoosh(alpha=alpha, iterations=iterations)
