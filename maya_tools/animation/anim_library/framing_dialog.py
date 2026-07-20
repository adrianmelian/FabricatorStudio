# maya_tools/animation/anim_library/framing_dialog.py
"""Re-export pose_library.framing_dialog — both libraries share the
same modal dialog. The anim_library injects a sequence-capture
callback (playblast + Pillow GIF, lands in Task 7), pose_library uses
the default still-capture fallback.
"""
from maya_tools.animation.pose_library.framing_dialog import (  # noqa: F401
    ThumbnailFramingDialog,
)

__all__ = ('ThumbnailFramingDialog',)
