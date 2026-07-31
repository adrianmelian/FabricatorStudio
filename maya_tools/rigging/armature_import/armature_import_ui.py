# armature_import_ui.py
# Armature Import — UI only. All logic lives in armature_import_app.py.
#
# Workflow:
#   1. Pick the .usd / .usdc Armature wrote.
#   2. Pick Simple or Advanced components (Advanced needs the Ribbon pack installed).
#   3. Import. The mesh, skeleton and skin weights come in from the USD; the Fabricator
#      blueprint is written beside the file and loaded, so Build Rig is the next step.
__author__ = "Adrian Melian"

import os
import traceback

from PySide6 import QtWidgets, QtCore

from maya_tools.rigging.armature_import import armature_import_app as app
from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget

_win = None


class ArmatureImportWindow(QtWidgets.QDialog):
    WINDOW_NAME = 'ArmatureImportTool'
    WINDOW_TITLE = 'Armature Import'

    def __init__(self, parent=None):
        super().__init__(parent or get_maya_window())
        mindmeld_style.apply(self)

        self.setObjectName(self.WINDOW_NAME)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setProperty('saveWindowPref', True)
        self.setMinimumWidth(460)

        self.create_layout()
        self.connect_signals()
        self.populate()

    # ─────────────────────────────────────────
    # Layout
    # ─────────────────────────────────────────

    def create_layout(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(14, 14, 14, 12)

        # ── file row ──
        file_row = QtWidgets.QHBoxLayout()
        self.path_field = QtWidgets.QLineEdit()
        self.path_field.setPlaceholderText('Armature .usd / .usdc export')
        self.browse_btn = QtWidgets.QPushButton('Browse')
        self.browse_btn.setFixedWidth(80)
        file_row.addWidget(self.path_field)
        file_row.addWidget(self.browse_btn)
        main_layout.addLayout(file_row)

        # ── component tier ──
        tier_box = QtWidgets.QGroupBox('Components')
        tier_layout = QtWidgets.QVBoxLayout(tier_box)
        self.simple_radio = QtWidgets.QRadioButton(
            'Simple  (FK spine and neck, IK arms and legs)')
        self.advanced_radio = QtWidgets.QRadioButton(
            'Advanced  (ribbon spine, neck, arms and legs)')
        self.simple_radio.setChecked(True)
        tier_layout.addWidget(self.simple_radio)
        tier_layout.addWidget(self.advanced_radio)
        self.tier_note = QtWidgets.QLabel('')
        self.tier_note.setWordWrap(True)
        self.tier_note.setObjectName('hintLabel')
        tier_layout.addWidget(self.tier_note)
        main_layout.addWidget(tier_box)

        # ── action ──
        self.import_btn = QtWidgets.QPushButton('Import Rig')
        self.import_btn.setMinimumHeight(32)
        main_layout.addWidget(self.import_btn)

        # ── status + log ──
        self.status_label = QtWidgets.QLabel('Pick an Armature USD export.')
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        self.logger = LoggerWidget()
        log_section = CollapsibleSection('Log', self.logger)
        main_layout.addWidget(log_section)

    def connect_signals(self):
        self.browse_btn.clicked.connect(self._on_browse)
        self.import_btn.clicked.connect(self._on_import)

    def populate(self):
        """Offer Advanced only when the ribbon types are actually registered.

        The free/paid split is a build-time file split, so registry membership is the
        honest signal. No license check exists and none is added here.
        """
        available = app.advanced_available()
        self.advanced_radio.setEnabled(available)
        if available:
            self.tier_note.setText('')
        else:
            self.advanced_radio.setChecked(False)
            self.simple_radio.setChecked(True)
            self.tier_note.setText(
                'Advanced needs the Advanced Ribbon Modules pack, which is not '
                'installed in this build.')

    # ─────────────────────────────────────────
    # Slots
    # ─────────────────────────────────────────

    def _on_browse(self):
        start = os.path.dirname(self.path_field.text().strip()) or ''
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Pick an Armature USD export', start,
            'USD (*.usd *.usdc *.usda)')
        if path:
            self.path_field.setText(path)
            self.status_label.setText('Ready to import %s' % os.path.basename(path))

    def _on_import(self):
        path = self.path_field.text().strip()
        if not path:
            self.status_label.setText('Pick a USD export first.')
            self.logger.warning('No file selected.')
            return

        self.import_btn.setEnabled(False)
        self.status_label.setText('Importing...')
        QtWidgets.QApplication.processEvents()
        try:
            report = app.import_armature(
                path,
                advanced=self.advanced_radio.isChecked(),
                log=self.logger.info,
            )
        except Exception as e:
            self._handle_error(e)
            self.status_label.setText('Import failed. See the log.')
            return
        finally:
            self.import_btn.setEnabled(True)

        for line in report.as_lines():
            self.logger.info(line)
        self.logger.success('Imported. Build Rig is the next step.')
        self.status_label.setText(
            '%s imported: %d joints, %d components. Build Rig is next.'
            % (report.source.name, report.blueprint_joint_count,
               sum(report.components.values())))

    # ─────────────────────────────────────────
    # Status / error
    # ─────────────────────────────────────────

    def _handle_error(self, e, brief=None):
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
        _win = ArmatureImportWindow()
        _win.setWindowFlags(QtCore.Qt.WindowType.Window)
        _win.show()
