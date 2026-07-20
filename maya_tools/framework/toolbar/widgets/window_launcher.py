"""WindowLauncher — opens a full tool window (spec 3.2). Behaviorally an
IconButton; a distinct class so the manifest reads honestly and Phase D
can style launchers differently (e.g. corner mark)."""
from __future__ import annotations

from maya_tools.framework.toolbar.widgets.icon_button import IconButton


class WindowLauncher(IconButton):
    pass
