# skeleton_io_ui.py
# Skeleton IO — UI only. All logic lives in skeleton_io_app.py.
__author__ = "Adrian Melian"

import os
import traceback
from pathlib import Path

import maya.cmds as cmds

from PySide6 import QtWidgets, QtCore

from maya_tools.skeleton.skeleton_io import skeleton_io_app as app
from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget


def _default_export_path() -> str:
    scene = cmds.file(query=True, sceneName=True)
    if not scene:
        return ''
    scene_dir  = os.path.dirname(scene)
    scene_name = os.path.splitext(os.path.basename(scene))[0]
    return str(Path(scene_dir) / 'Data' / f'{scene_name}_skeleton.json')


class SkeletonIOWindow(QtWidgets.QDialog):
    WINDOW_NAME  = 'SkeletonIOTool'
    WINDOW_TITLE = 'Skeleton IO'

    def __init__(self, parent=None):
        super().__init__(parent or get_maya_window())
        mindmeld_style.apply(self)

        self.setObjectName(self.WINDOW_NAME)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setProperty('saveWindowPref', True)
        self.setMinimumWidth(460)

        self.create_layout()
        self.connect_signals()
        default = _default_export_path()
        if default:
            self.export_path_field.setText(default)

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
        # FS Skeleton IO banner (Adrian, 2026-07-11); text title returns if missing.
        from PySide6.QtGui import QPixmap
        from maya_tools.framework.toolbar.widgets import icon_button
        _banner = QPixmap(str(icon_button._ICON_DIR / 'fs_skeletonio_banner.png'))
        if not _banner.isNull():
            _brand = QtWidgets.QLabel()
            _brand.setPixmap(_banner.scaledToHeight(
                40, QtCore.Qt.TransformationMode.SmoothTransformation))
            brand_row.addWidget(_brand)
        else:
            brand_row.addWidget(mindmeld_style.brand_label('Skeleton IO'))
        brand_row.addWidget(mindmeld_style.caps_label('// hierarchy · save + load'),
                            0, QtCore.Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        main_layout.addLayout(brand_row)
        main_layout.addWidget(mindmeld_style.horizontal_rule())

        # ── Tabs ──
        self.tabs = QtWidgets.QTabWidget()
        main_layout.addWidget(self.tabs)

        self.tabs.addTab(self._build_export_tab(), 'EXPORT')
        self.tabs.addTab(self._build_import_tab(), 'IMPORT')

        # ── Logger ──
        self.log_section = CollapsibleSection('// LOG')
        self.logger = LoggerWidget()
        log_body_layout = QtWidgets.QVBoxLayout()
        log_body_layout.setContentsMargins(0, 0, 0, 0)
        log_body_layout.addWidget(self.logger)
        self.log_section.set_body_layout(log_body_layout)
        self.log_section.set_expanded(False)
        main_layout.addWidget(self.log_section)

    def _build_export_tab(self) -> QtWidgets.QWidget:
        tab    = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 14, 12, 12)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        # Root joint row
        root_row       = QtWidgets.QHBoxLayout()
        root_row.setSpacing(8)
        self.root_field = QtWidgets.QLineEdit()
        self.root_field.setPlaceholderText('e.g. root')
        self.use_sel_btn = mindmeld_style.button('Use Selected', kind='ghost')
        self.use_sel_btn.setFixedWidth(120)
        root_row.addWidget(self.root_field)
        root_row.addWidget(self.use_sel_btn)
        form.addRow(mindmeld_style.field_label('Root Joint'), root_row)

        # Output path row
        export_path_row        = QtWidgets.QHBoxLayout()
        export_path_row.setSpacing(8)
        self.export_path_field = QtWidgets.QLineEdit()
        self.export_path_field.setPlaceholderText('Output .json path')
        self.export_browse_btn = mindmeld_style.button('Browse…', kind='ghost')
        self.export_browse_btn.setFixedWidth(80)
        export_path_row.addWidget(self.export_path_field)
        export_path_row.addWidget(self.export_browse_btn)
        form.addRow(mindmeld_style.field_label('Output File'), export_path_row)

        layout.addLayout(form)

        self.user_attrs_cb = QtWidgets.QCheckBox('Include user attributes')
        self.user_attrs_cb.setChecked(True)
        layout.addWidget(self.user_attrs_cb)

        layout.addStretch()

        self.export_btn = mindmeld_style.button('Export Skeleton', kind='primary')
        self.export_btn.setMinimumHeight(40)
        layout.addWidget(self.export_btn)

        return tab

    def _build_import_tab(self) -> QtWidgets.QWidget:
        tab    = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 14, 12, 12)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        # Input path row
        import_path_row        = QtWidgets.QHBoxLayout()
        import_path_row.setSpacing(8)
        self.import_path_field = QtWidgets.QLineEdit()
        self.import_path_field.setPlaceholderText('Skeleton .json path')
        self.import_browse_btn = mindmeld_style.button('Browse…', kind='ghost')
        self.import_browse_btn.setFixedWidth(80)
        import_path_row.addWidget(self.import_path_field)
        import_path_row.addWidget(self.import_browse_btn)
        form.addRow(mindmeld_style.field_label('Skeleton File'), import_path_row)

        # Namespace row
        self.namespace_field = QtWidgets.QLineEdit()
        self.namespace_field.setPlaceholderText('optional')
        form.addRow(mindmeld_style.field_label('Namespace'), self.namespace_field)

        layout.addLayout(form)

        self.remove_scale_cb = QtWidgets.QCheckBox('Remove scale offset')
        self.remove_scale_cb.setChecked(True)
        self.zero_scales_cb = QtWidgets.QCheckBox('Zero joint scales')
        self.zero_scales_cb.setChecked(True)
        self.bake_jo_cb = QtWidgets.QCheckBox('Bake to joint orient  (rotate → 0)')
        self.bake_jo_cb.setChecked(False)
        layout.addWidget(self.remove_scale_cb)
        layout.addWidget(self.zero_scales_cb)
        layout.addWidget(self.bake_jo_cb)

        layout.addStretch()

        self.import_btn = mindmeld_style.button('Import Skeleton', kind='primary')
        self.import_btn.setMinimumHeight(40)
        layout.addWidget(self.import_btn)

        return tab

    # ─────────────────────────────────────────
    # Signals
    # ─────────────────────────────────────────

    def connect_signals(self):
        self.use_sel_btn.clicked.connect(self._on_use_selected)
        self.export_browse_btn.clicked.connect(self._on_export_browse)
        self.export_btn.clicked.connect(self._on_export)
        self.import_browse_btn.clicked.connect(self._on_import_browse)
        self.import_btn.clicked.connect(self._on_import)

    # ─────────────────────────────────────────
    # Handlers
    # ─────────────────────────────────────────

    def _on_use_selected(self):
        sel = cmds.ls(selection=True, type='joint')
        if sel:
            self.root_field.setText(sel[0])
        else:
            self.logger.error('Select a joint first.')

    def _on_export_browse(self):
        current = self.export_path_field.text().strip()
        start   = current if current else _default_export_path()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Export Skeleton', start, 'Skeleton JSON (*.json)'
        )
        if path:
            if not path.endswith('.json'):
                path += '.json'
            self.export_path_field.setText(path)

    def _on_export(self):
        root = self.root_field.text().strip()
        path = self.export_path_field.text().strip()
        if not root or not path:
            self.logger.error('Root joint and output path are required.')
            return
        try:
            app.export_skeleton(root, path, include_user_attrs=self.user_attrs_cb.isChecked())
            self.logger.success(f'Exported → {os.path.basename(path)}')
        except Exception as e:
            self._handle_error(e)

    def _on_import_browse(self):
        current = self.import_path_field.text().strip()
        start   = current if current else _default_export_path()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Import Skeleton', start, 'Skeleton JSON (*.json)'
        )
        if path:
            self.import_path_field.setText(path)

    def _on_import(self):
        path = self.import_path_field.text().strip()
        if not path:
            self.logger.error('Select a skeleton file first.')
            return
        try:
            joints = app.import_skeleton(
                path,
                namespace=self.namespace_field.text().strip(),
                remove_scale_offset=self.remove_scale_cb.isChecked(),
                zero_joint_scales=self.zero_scales_cb.isChecked(),
                bake_to_joint_orient=self.bake_jo_cb.isChecked(),
            )
            self.logger.success(f'Imported {len(joints)} joints.')
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
        _win = SkeletonIOWindow()
        _win.setWindowFlags(QtCore.Qt.WindowType.Window)
        _win.show()
