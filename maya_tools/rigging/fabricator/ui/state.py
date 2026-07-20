# Python/maya_tools/rigging/fabricator/ui/state.py
"""Mode detection — what's currently in the scene + what does that mean
for the UI's button states.

Detection order is most-built-first (partial states are possible —
joints might exist with no components, or components might exist while
a joint was deleted manually). See spec Section 3 'Mode detection'."""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes


MODE_EMPTY = 'empty'
MODE_SKELETON = 'skeleton'
MODE_MODULES_BUILT = 'modules_built'
# MODE_GUIDES removed 2026-07-04 with the transient guide layer — the
# Armature (real joints + ctrls + aimers) is the editable stage now.


def detect_mode() -> str:
    """Inspect the scene + KS v2 state, return the current mode.

    Order: components-built > skeleton-in-scene > empty.

    Self-healing: if any component has is_built=True but rig_grp doesn't
    exist (the user manually deleted rig_grp, or some other tool nuked it),
    reset all is_built flags and fall through to the lower-mode detection.
    Without this, the UI gets stuck — phase buttons stay disabled because
    they think the rig is built, with no recovery path.
    """
    # 1. Any component built? → modules_built (with consistency check)
    built_nodes = [cn for cn in nodes.get_all_component_nodes()
                   if nodes.is_built(cn)]
    if built_nodes:
        if cmds.objExists('rig_grp'):
            return MODE_MODULES_BUILT
        # is_built flags are stale — rig_grp was deleted out from under us.
        # Reset so the next Build Modules can run cleanly, then fall through.
        for cn in built_nodes:
            nodes.set_built(cn, False)

    # 2. Any joint from the registry's components present? → skeleton
    reg = nodes.get_registry()
    if reg:
        for cnode in nodes.get_all_component_nodes():
            for j in nodes.get_component_joints(cnode):
                if cmds.objExists(j):
                    return MODE_SKELETON

    return MODE_EMPTY
