# maya_tools/rigging/fabricator/ui/tools_panel.py
"""ToolsPanel — right-of-canvas panel shown in Animation Mode.

Replaces the Properties panel post-build with animator-oriented tools.
First inhabitant: an embedded Curve-O-Matic. Future tools dock here as
they're added.

Curve-O-Matic is its own QDialog; we instantiate it and strip the
window/tool flags so it embeds as a regular child widget instead of
popping out into its own floating window.
"""
__author__ = "Adrian Melian"

from PySide6 import QtWidgets, QtCore

from maya_tools.utils.qt.mindmeld import mindmeld_style


class ToolsPanel(QtWidgets.QWidget):
    """Animation-Mode right panel. Hosts embedded animator tools."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.create_layout()

    def create_layout(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.addWidget(mindmeld_style.caps_label('// TOOLS'))

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        outer.addWidget(scroll, stretch=1)

        body = QtWidgets.QWidget()
        scroll.setWidget(body)
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(2, 2, 2, 2)
        body_layout.setSpacing(8)

        # Curve-O-Matic embedded. CurveOMaticWindow is a QDialog with
        # Tool flag — strip the flags so Qt treats it as a regular child
        # widget owned by this layout (no separate window).
        from maya_tools.rigging.curve_o_matic.curve_o_matic_ui import (
            CurveOMaticWindow,
        )
        self.curve_o_matic = CurveOMaticWindow(embedded=True)
        self.curve_o_matic.setWindowFlags(QtCore.Qt.WindowType.Widget)
        body_layout.addWidget(self.curve_o_matic)

        body_layout.addStretch(1)
