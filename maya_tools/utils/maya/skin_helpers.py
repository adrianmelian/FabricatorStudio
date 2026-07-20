# maya_tools/utils/maya/skin_helpers.py
"""Cross-tool skinCluster helpers.

Used by maya_tools.skinning.* tools that need to introspect existing
skinClusters. Kept here (not in any owning tool) per the CLAUDE.md
cross-tool conventions section.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds


def get_skin_influences(mesh: str) -> set:
    """Return the set of long-path joint names that drive `mesh`'s
    skinCluster, or None if the mesh has no skinCluster. Used to filter
    exported skeletons / re-skin existing binds to just the joints that
    actually skin geometry."""
    history = cmds.listHistory(mesh, pruneDagObjects=True) or []
    skin_clusters = cmds.ls(history, type='skinCluster') or []
    if not skin_clusters:
        return None
    influences = cmds.skinCluster(skin_clusters[0], q=True, influence=True) or []
    return set(cmds.ls(influences, long=True) or [])
