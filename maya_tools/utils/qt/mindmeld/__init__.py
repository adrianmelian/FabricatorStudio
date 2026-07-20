"""utils.qt.mindmeld — Mindmeld design system for PySide6 Maya tools.

Quick start:

    from maya_tools.utils.qt.mindmeld import mindmeld_style

    class MyToolWindow(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            mindmeld_style.apply(self)

            build_btn = mindmeld_style.button("Build Rig", "primary")
            label = mindmeld_style.field_label("Joint Name")

See `mindmeld_style.py` for the full API and `mindmeld.qss` for the stylesheet.
"""

from . import mindmeld_style

__all__ = ["mindmeld_style"]
