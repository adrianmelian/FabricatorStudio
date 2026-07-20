# Python/maya_tools/utils/qt/widgets/mindmeld_tree.py
"""MindmeldTreeWidget — QTreeWidget with painted disclosure triangles.

The Mindmeld stylesheet deliberately blanks Qt's native branch
indicators (the platform-drawn triangles clash with the dark theme), so
a plain QTreeWidget renders NO expand/collapse arrow at all — rows with
children collapse only if the user happens to click the empty branch
gutter. Any tree that wants a visible disclosure affordance should use
(or subclass) this widget: it paints replacement arrows in Mindmeld
plasma_dim — right-pointing for collapsed rows, down-pointing for
expanded — for every row that has children.
"""
__author__ = "Adrian Melian"

from PySide6 import QtWidgets, QtCore, QtGui

from maya_tools.utils.qt.mindmeld import mindmeld_style


class MindmeldTreeWidget(QtWidgets.QTreeWidget):
    """QTreeWidget whose branch column draws Mindmeld disclosure arrows."""

    _BRANCH_COLOR = QtGui.QColor(mindmeld_style.PLASMA_DIM)

    def drawBranches(self, painter: QtGui.QPainter,
                     rect: QtCore.QRect, index: QtCore.QModelIndex) -> None:
        # Only items with children get an indicator.
        if not self.model().hasChildren(index):
            return
        # Indent column is one level to the left of the item's row rect.
        indent = self.indentation()
        cx = rect.right() - indent // 2
        cy = rect.top() + rect.height() // 2
        size = 4  # half-width of the triangle in pixels

        path = QtGui.QPainterPath()
        if self.isExpanded(index):
            # Down-pointing triangle for expanded rows.
            path.moveTo(cx - size, cy - size // 2)
            path.lineTo(cx + size, cy - size // 2)
            path.lineTo(cx, cy + size)
        else:
            # Right-pointing triangle for collapsed rows.
            path.moveTo(cx - size // 2, cy - size)
            path.lineTo(cx + size, cy)
            path.lineTo(cx - size // 2, cy + size)
        path.closeSubpath()

        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(self._BRANCH_COLOR)
        painter.drawPath(path)
        painter.restore()
