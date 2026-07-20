# joint_orient_ui.py
# Joint Orient Tool — UI only. All logic lives in joint_orient_app.py.
#
# Workflow:
#   1. Create Aimers — builds XYZ arrow ctrl + offset node per joint.
#   2. Rotate aimer ctrl to desired joint orientation; use the aimTarget enum
#      in the Channel Box to auto-aim at a child joint or Local.
#   3. Orient All Joints — unparents, bakes aimer rotation into joint rotate,
#      reparents, and deletes all aimers.
__author__ = "Adrian Melian"

import traceback

import maya.cmds as cmds

from PySide6 import QtWidgets, QtCore

from maya_tools.rigging.joint_orient import joint_orient_app as app
from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget



# ─────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────

def _find_root(jnt: str) -> str:
    """Walk up the joint hierarchy and return the topmost joint."""
    current = jnt
    while True:
        parents = cmds.listRelatives(current, parent=True, type='joint') or []
        if not parents:
            return current
        current = parents[0]



# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────

class JointOrientWindow(QtWidgets.QDialog):
    WINDOW_NAME  = 'JointOrientTool'
    WINDOW_TITLE = 'Joint Aimer'

    def __init__(self, parent=None):
        super().__init__(parent or get_maya_window())
        mindmeld_style.apply(self)

        self.setObjectName(self.WINDOW_NAME)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setProperty('saveWindowPref', True)
        self.setMinimumWidth(380)

        self.create_layout()
        self.connect_signals()

    # ─────────────────────────────────────────
    # Layout
    # ─────────────────────────────────────────

    def create_layout(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(14, 14, 14, 12)

        # ── brand bar ──
        brand_row = QtWidgets.QHBoxLayout()
        brand_row.setSpacing(10)
        brand_row.addWidget(mindmeld_style.brand_label('Joint Aimer'))
        brand_row.addWidget(mindmeld_style.caps_label('// aimer-driven'),
                            0, QtCore.Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        main_layout.addLayout(brand_row)
        main_layout.addWidget(mindmeld_style.horizontal_rule())

        # ── Aimer Settings ──
        settings_grp    = QtWidgets.QGroupBox('AIMER SETTINGS')
        settings_layout = QtWidgets.QFormLayout(settings_grp)
        settings_layout.setSpacing(8)
        settings_layout.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self.scale_spin = QtWidgets.QDoubleSpinBox()
        self.scale_spin.setRange(0.1, 100.0)
        self.scale_spin.setValue(10.0)
        self.scale_spin.setSingleStep(1.0)
        self.scale_spin.setDecimals(1)
        settings_layout.addRow(mindmeld_style.field_label('Aimer Scale'), self.scale_spin)

        main_layout.addWidget(settings_grp)

        # ── Create Aimers ──
        create_grp    = QtWidgets.QGroupBox('CREATE AIMERS')
        create_layout = QtWidgets.QVBoxLayout(create_grp)
        create_layout.setSpacing(6)

        self.create_root_btn = mindmeld_style.button('Create Aimers — From Root', kind='default')
        create_layout.addWidget(self.create_root_btn)

        main_layout.addWidget(create_grp)

        # ── Mirror Selected ──
        self.mirror_btn = mindmeld_style.button('Mirror Selected Aimers', kind='default')
        main_layout.addWidget(self.mirror_btn)

        # ── Delete ──
        self.delete_all_btn = mindmeld_style.button('Delete All Aimers', kind='danger')
        main_layout.addWidget(self.delete_all_btn)

        # ── Orient Joints ──
        self.orient_all_btn = mindmeld_style.button('Orient All Joints', kind='primary')
        self.orient_all_btn.setMinimumHeight(40)
        main_layout.addWidget(self.orient_all_btn)

        # ── Logger ──
        self.log_section = CollapsibleSection('// LOG')
        self.logger = LoggerWidget()
        log_body_layout = QtWidgets.QVBoxLayout()
        log_body_layout.setContentsMargins(0, 0, 0, 0)
        log_body_layout.addWidget(self.logger)
        self.log_section.set_body_layout(log_body_layout)
        self.log_section.set_expanded(False)
        main_layout.addWidget(self.log_section)

    # ─────────────────────────────────────────
    # Signals
    # ─────────────────────────────────────────

    def connect_signals(self):
        self.create_root_btn.clicked.connect(self._on_create_from_root)
        self.mirror_btn.clicked.connect(self._on_mirror_selected)
        self.delete_all_btn.clicked.connect(self._on_delete_all)
        self.orient_all_btn.clicked.connect(self._on_orient_all)

    # ─────────────────────────────────────────
    # Handlers
    # ─────────────────────────────────────────

    def _on_create_from_root(self):
        sel = cmds.ls(selection=True, type='joint')
        if not sel:
            self.logger.error('Select a joint first.')
            return
        try:
            scale   = self.scale_spin.value()
            root    = _find_root(sel[0])
            aimers  = app.create_aimers_from_root(root, scale)
            self.logger.success(f'Created {len(aimers)} aimer(s) from root "{root}".')
        except Exception as e:
            self._handle_error(e)

    def _on_mirror_selected(self):
        try:
            result = app.mirror_aimers()
        except Exception as e:
            self._handle_error(e)
            return

        if result['mirrored']:
            self.logger.success(
                f"Mirrored {result['mirrored']} aimer(s). "
                f"{result['skipped']} skipped."
            )
        else:
            self.logger.warning(f"No aimers mirrored. {result['skipped']} skipped.")
        for reason in result['reasons']:
            self.logger.warning(reason)

    def _on_delete_all(self):
        try:
            app.delete_all_aimers()
            self.logger.success('All aimers deleted.')
        except Exception as e:
            self._handle_error(e)

    def _on_orient_all(self):
        try:
            oriented = app.orient_all_aimers()
            self.logger.success(f'Oriented {len(oriented)} joint(s).')
        except Exception as e:
            self._handle_error(e)

    # ─────────────────────────────────────────
    # Status / error
    # ─────────────────────────────────────────

    def _handle_error(self, e: Exception, brief: str | None = None) -> None:
        traceback.print_exc()
        self.logger.error(brief or str(e))

    # ─────────────────────────────────────────
    # Show
    # ─────────────────────────────────────────

    @staticmethod
    def show_window():
        global _win
        try:
            _win.close()
            _win.deleteLater()
        except Exception:
            pass
        _win = JointOrientWindow()
        _win.setWindowFlags(QtCore.Qt.WindowType.Window)
        _win.show()
