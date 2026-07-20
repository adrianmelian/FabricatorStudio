# skin_io_ui.py
# Skin IO — UI only. All logic lives in skin_io_app.py.
__author__ = "Adrian Melian"

import os
import re
import traceback
from pathlib import Path

import maya.cmds as cmds

from PySide6 import QtWidgets, QtCore

from maya_tools.skinning.skin_io import skin_io_app as app
from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget


_SA_OPTIONS = ['closestPoint', 'rayCast', 'closestComponent']
_IA_OPTIONS = ['name', 'label', 'closestJoint', 'oneToOne']


def _default_save_path(mesh_name: str = '') -> str:
    scene = cmds.file(query=True, sceneName=True)
    if not scene:
        return ''
    scene_dir = os.path.dirname(scene)
    stem      = mesh_name if mesh_name else os.path.splitext(os.path.basename(scene))[0]
    return str(Path(scene_dir) / 'Data' / f'{stem}_skin.json')


class SkinIOWindow(QtWidgets.QDialog):
    WINDOW_NAME  = 'SkinIOTool'
    WINDOW_TITLE = 'Skin IO'

    def __init__(self, parent=None):
        super().__init__(parent or get_maya_window())
        mindmeld_style.apply(self)

        self.setObjectName(self.WINDOW_NAME)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setProperty('saveWindowPref', True)
        self.setMinimumWidth(480)

        # Vert-subset snapshot for scoped loads. Set by _on_load_use_selected
        # when the active selection is vertices; cleared on mesh-field edits.
        self._load_vert_indices: list[int] | None = None

        self.create_layout()
        self.connect_signals()
        default = _default_save_path()
        if default:
            self.save_path_field.setText(default)
        self._update_load_scope_label()

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
        # FS Skin IO banner (Adrian, 2026-07-11); text title returns if missing.
        from PySide6.QtGui import QPixmap
        from maya_tools.framework.toolbar.widgets import icon_button
        _banner = QPixmap(str(icon_button._ICON_DIR / 'fs_skinio_banner.png'))
        if not _banner.isNull():
            _brand = QtWidgets.QLabel()
            _brand.setPixmap(_banner.scaledToHeight(
                40, QtCore.Qt.TransformationMode.SmoothTransformation))
            brand_row.addWidget(_brand)
        else:
            brand_row.addWidget(mindmeld_style.brand_label('Skin IO'))
        brand_row.addWidget(mindmeld_style.caps_label('// weights · save + load'),
                            0, QtCore.Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        main_layout.addLayout(brand_row)
        main_layout.addWidget(mindmeld_style.horizontal_rule())

        self.tabs = QtWidgets.QTabWidget()
        main_layout.addWidget(self.tabs)

        self.tabs.addTab(self._build_save_tab(), 'SAVE')
        self.tabs.addTab(self._build_load_tab(), 'LOAD')

        # ── Logger ──
        self.log_section = CollapsibleSection('// LOG')
        self.logger = LoggerWidget()
        log_body_layout = QtWidgets.QVBoxLayout()
        log_body_layout.setContentsMargins(0, 0, 0, 0)
        log_body_layout.addWidget(self.logger)
        self.log_section.set_body_layout(log_body_layout)
        self.log_section.set_expanded(False)
        main_layout.addWidget(self.log_section)

    def _build_save_tab(self) -> QtWidgets.QWidget:
        tab    = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 14, 12, 12)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        mesh_row              = QtWidgets.QHBoxLayout()
        mesh_row.setSpacing(8)
        self.save_mesh_field  = QtWidgets.QLineEdit()
        self.save_mesh_field.setPlaceholderText('e.g. pSphere1')
        self.save_use_sel_btn = mindmeld_style.button('Use Selected', kind='ghost')
        self.save_use_sel_btn.setFixedWidth(120)
        mesh_row.addWidget(self.save_mesh_field)
        mesh_row.addWidget(self.save_use_sel_btn)
        form.addRow(mindmeld_style.field_label('Mesh'), mesh_row)

        save_path_row        = QtWidgets.QHBoxLayout()
        save_path_row.setSpacing(8)
        self.save_path_field = QtWidgets.QLineEdit()
        self.save_path_field.setPlaceholderText('Output .json path')
        self.save_browse_btn = mindmeld_style.button('Browse…', kind='ghost')
        self.save_browse_btn.setFixedWidth(80)
        save_path_row.addWidget(self.save_path_field)
        save_path_row.addWidget(self.save_browse_btn)
        form.addRow(mindmeld_style.field_label('Output File'), save_path_row)

        layout.addLayout(form)
        layout.addStretch()

        self.save_btn = mindmeld_style.button('Save Skin Weights', kind='primary')
        self.save_btn.setMinimumHeight(40)
        layout.addWidget(self.save_btn)

        return tab

    def _build_load_tab(self) -> QtWidgets.QWidget:
        tab    = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 14, 12, 12)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        target_row            = QtWidgets.QHBoxLayout()
        target_row.setSpacing(8)
        self.load_mesh_field  = QtWidgets.QLineEdit()
        self.load_mesh_field.setPlaceholderText('e.g. pSphere1')
        self.load_use_sel_btn = mindmeld_style.button('Use Selected', kind='ghost')
        self.load_use_sel_btn.setFixedWidth(120)
        target_row.addWidget(self.load_mesh_field)
        target_row.addWidget(self.load_use_sel_btn)
        form.addRow(mindmeld_style.field_label('Target Mesh'), target_row)

        load_path_row        = QtWidgets.QHBoxLayout()
        load_path_row.setSpacing(8)
        self.load_path_field = QtWidgets.QLineEdit()
        self.load_path_field.setPlaceholderText('Skin weights .json path')
        self.load_browse_btn = mindmeld_style.button('Browse…', kind='ghost')
        self.load_browse_btn.setFixedWidth(80)
        load_path_row.addWidget(self.load_path_field)
        load_path_row.addWidget(self.load_browse_btn)
        form.addRow(mindmeld_style.field_label('Skin File'), load_path_row)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(['Transfer  (closest point)', 'Direct  (same topology)'])
        form.addRow(mindmeld_style.field_label('Mode'), self.mode_combo)

        layout.addLayout(form)

        self.transfer_group = QtWidgets.QGroupBox('TRANSFER OPTIONS')
        tg_layout = QtWidgets.QFormLayout(self.transfer_group)
        tg_layout.setSpacing(8)
        tg_layout.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self.surface_assoc_combo = QtWidgets.QComboBox()
        self.surface_assoc_combo.addItems(_SA_OPTIONS)
        tg_layout.addRow(mindmeld_style.field_label('Surface Association'), self.surface_assoc_combo)

        self.ia_primary_combo = QtWidgets.QComboBox()
        self.ia_primary_combo.addItems(_IA_OPTIONS)
        tg_layout.addRow(mindmeld_style.field_label('Influence Match (1°)'), self.ia_primary_combo)

        self.ia_secondary_combo = QtWidgets.QComboBox()
        self.ia_secondary_combo.addItems(['(none)'] + _IA_OPTIONS)
        self.ia_secondary_combo.setCurrentText('label')
        tg_layout.addRow(mindmeld_style.field_label('Influence Match (2°)'), self.ia_secondary_combo)

        layout.addWidget(self.transfer_group)
        self.transfer_group.setVisible(True)

        self.create_missing_cb = QtWidgets.QCheckBox('Create missing influences')
        self.create_missing_cb.setChecked(False)
        layout.addWidget(self.create_missing_cb)

        layout.addStretch()

        self.load_scope_label = mindmeld_style.caps_label('// scope: whole mesh')
        layout.addWidget(self.load_scope_label)

        self.load_btn = mindmeld_style.button('Load Skin Weights', kind='primary')
        self.load_btn.setMinimumHeight(40)
        layout.addWidget(self.load_btn)

        return tab

    # ─────────────────────────────────────────
    # Signals
    # ─────────────────────────────────────────

    def connect_signals(self):
        self.save_use_sel_btn.clicked.connect(self._on_save_use_selected)
        self.save_browse_btn.clicked.connect(self._on_save_browse)
        self.save_btn.clicked.connect(self._on_save)
        self.load_use_sel_btn.clicked.connect(self._on_load_use_selected)
        self.load_browse_btn.clicked.connect(self._on_load_browse)
        self.load_btn.clicked.connect(self._on_load)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        # Any manual edit of the target-mesh field invalidates the snapshotted
        # vert-subset — user is changing what they target, can't trust prior verts.
        self.load_mesh_field.textEdited.connect(self._on_load_mesh_edited)

    # ─────────────────────────────────────────
    # Handlers
    # ─────────────────────────────────────────

    def _on_save_use_selected(self):
        sel = cmds.ls(selection=True, type='transform') or []
        meshes = [s for s in sel if cmds.listRelatives(s, shapes=True, type='mesh', noIntermediate=True)]
        if not meshes:
            self.logger.error('Select at least one mesh first.')
            return
        self.save_mesh_field.setText(', '.join(meshes))
        path = _default_save_path(meshes[0])
        if path:
            self.save_path_field.setText(path)

    def _on_save_browse(self):
        current = self.save_path_field.text().strip()
        start   = current if current else _default_save_path()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Save Skin Weights', start, 'Skin JSON (*.json)'
        )
        if path:
            if not path.endswith('.json'):
                path += '.json'
            self.save_path_field.setText(path)

    def _on_save(self):
        text = self.save_mesh_field.text().strip()
        path = self.save_path_field.text().strip()
        if not text or not path:
            self.logger.error('Mesh and output path are required.')
            return
        meshes = [m.strip() for m in text.split(',') if m.strip()]
        if not meshes:
            self.logger.error('No valid mesh names parsed.')
            return
        try:
            dir_part = os.path.dirname(path)
            if dir_part:
                os.makedirs(dir_part, exist_ok=True)
            app.save_skin_from_meshes(meshes, path)
            base = os.path.basename(path)
            if len(meshes) > 1:
                self.logger.success(f'Saved {len(meshes)} meshes → {base}')
            else:
                self.logger.success(f'Saved → {base}')
        except Exception as e:
            self._handle_error(e)

    def _on_load_use_selected(self):
        # Verts first (more specific). filterExpand mask 31 = polygon vertices.
        # No fullPath flag — we want the namespace-qualified short form
        # (e.g. 'Michael:body_geo') in the field, not the full DAG path.
        # Entries can be single indices ('foo.vtx[42]') or ranges
        # ('foo.vtx[42:99]') depending on the selection; handle both.
        raw = cmds.ls(selection=True) or []
        verts = cmds.filterExpand(raw, selectionMask=31) or []
        if verts:
            mesh_to_indices: dict[str, list[int]] = {}
            for v in verts:
                m = re.match(r'^(.*)\.vtx\[(\d+)(?::(\d+))?\]$', v)
                if not m:
                    continue
                mesh = m.group(1)
                start = int(m.group(2))
                end = int(m.group(3)) if m.group(3) is not None else start
                mesh_to_indices.setdefault(mesh, []).extend(range(start, end + 1))
            if len(mesh_to_indices) != 1:
                self.logger.error(
                    f'Vert selection spans {len(mesh_to_indices)} meshes — '
                    f'select verts on a single mesh.'
                )
                return
            mesh = next(iter(mesh_to_indices))
            indices = sorted(set(mesh_to_indices[mesh]))
            self.load_mesh_field.setText(mesh)
            self._load_vert_indices = indices
            self._update_load_scope_label()
            return

        # Fall back to whole-mesh transform selection.
        sel = cmds.ls(selection=True, type='transform') or []
        meshes = [s for s in sel if cmds.listRelatives(s, shapes=True, type='mesh', noIntermediate=True)]
        if not meshes:
            self.logger.error('Select at least one mesh (or vertices on a mesh) first.')
            return
        self.load_mesh_field.setText(', '.join(meshes))
        self._load_vert_indices = None
        self._update_load_scope_label()

    def _on_load_mesh_edited(self, _text: str) -> None:
        # Manual edit invalidates the vert snapshot — the user is retargeting.
        if self._load_vert_indices is not None:
            self._load_vert_indices = None
            self._update_load_scope_label()

    def _update_load_scope_label(self) -> None:
        if self._load_vert_indices is None:
            self.load_scope_label.setText('// scope: whole mesh')
        else:
            mesh = self.load_mesh_field.text().strip() or 'mesh'
            self.load_scope_label.setText(
                f'// scope: {len(self._load_vert_indices)} vert(s) on {mesh}'
            )

    def _on_load_browse(self):
        current = self.load_path_field.text().strip()
        start   = current if current else _default_save_path()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Load Skin Weights', start, 'Skin JSON (*.json)'
        )
        if path:
            self.load_path_field.setText(path)

    def _on_mode_changed(self, index: int):
        self.transfer_group.setVisible(index == 0)

    def _on_load(self):
        text = self.load_mesh_field.text().strip()
        path = self.load_path_field.text().strip()
        if not text or not path:
            self.logger.error('Target mesh and skin file are required.')
            return
        targets = [m.strip() for m in text.split(',') if m.strip()]
        if not targets:
            self.logger.error('No valid target mesh names parsed.')
            return
        mode = 'transfer' if self.mode_combo.currentIndex() == 0 else 'direct'
        sa   = self.surface_assoc_combo.currentText()
        ia1  = self.ia_primary_combo.currentText()
        ia2  = self.ia_secondary_combo.currentText()
        ia   = (ia1, ia2) if ia2 != '(none)' else (ia1,)
        vert_indices = self._load_vert_indices
        try:
            app.load_skin_to_meshes(
                targets, path,
                mode=mode,
                surface_association=sa,
                influence_association=ia,
                create_missing_influences=self.create_missing_cb.isChecked(),
                vert_indices=vert_indices,
            )
            base = os.path.basename(path)
            if vert_indices:
                self.logger.success(
                    f'Loaded → {base} ({len(vert_indices)} verts on {targets[0]})'
                )
            elif len(targets) > 1:
                self.logger.success(f'Loaded → {base} ({len(targets)} meshes)')
            else:
                self.logger.success(f'Loaded → {base}')
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
        _win = SkinIOWindow()
        _win.setWindowFlags(QtCore.Qt.WindowType.Window)
        _win.show()
