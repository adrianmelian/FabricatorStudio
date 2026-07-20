"""Maya decorators and context managers for use across all AM tools."""
__author__ = "Adrian Melian"

import maya.cmds as cmds


class undo_chunk:
    """Context manager — groups all wrapped operations into one undo step.

    Example:
        with undo_chunk("Rename objects"):
            hash_rename(sel, pattern)
    """
    def __init__(self, name: str = "Operation"):
        self.name = name

    def __enter__(self):
        try:
            cmds.undoInfo(openChunk=True, chunkName=self.name)
        except Exception:
            pass

    def __exit__(self, *_):
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
