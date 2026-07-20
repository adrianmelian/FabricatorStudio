# Python/maya_tools/export/anim_ui.py
"""Animation Exporter UI — clip table + auto-save inline editors + LoggerWidget.

All export logic lives in anim_app.py; CRUD lives in anim_core.py. This file
handles user interaction only.
"""
__author__ = "Adrian Melian"

import os
import traceback

import maya.cmds as cmds
import maya.api.OpenMaya as om
from PySide6 import QtCore, QtWidgets

from maya_tools.export import export_core, anim_app, anim_core
from maya_tools.utils.maya import rig_binding
from maya_tools.export.anim_progress import ExportProgressDialog
from maya_tools.utils.maya.gui import get_maya_window, close_windows_by_name
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget


# ─────────────────────────────────────────────────────────────────────────────
# Select-all-on-focus widgets
# ─────────────────────────────────────────────────────────────────────────────

class _SelectAllLineEdit(QtWidgets.QLineEdit):
    def focusInEvent(self, event):
        super().focusInEvent(event)
        # Defer to next event loop tick — the click that gave us focus is
        # processed AFTER focusInEvent and would otherwise clear the selection.
        QtCore.QTimer.singleShot(0, self.selectAll)


class _SelectAllSpinBox(QtWidgets.QSpinBox):
    def focusInEvent(self, event):
        super().focusInEvent(event)
        QtCore.QTimer.singleShot(0, self.selectAll)


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class AnimExporterWindow(QtWidgets.QDialog):
    WINDOW_NAME  = 'AMAnimExporterTool'
    WINDOW_TITLE = 'Anim Exporter'

    COL_ENABLED = 0
    COL_PREFIX  = 1
    COL_RIGS    = 2
    COL_NAME    = 3
    COL_START   = 4
    COL_END     = 5
    COL_DETAIL  = 6

    def __init__(self, parent=None, embedded: bool = False):
        """embedded=True: hosted inside another widget (the RigBot
        Export popover). Sweep-safe objectName — the standalone
        show_window() closes by objectName and must never reap the
        hosted instance — and the brand bar + log trim away (the
        popover drag bar carries identity; the logger mirrors to the
        Script Editor)."""
        super().__init__(parent or get_maya_window())
        mindmeld_style.apply(self)

        self._embedded = embedded
        self.setObjectName(
            self.WINDOW_NAME if not embedded
            else f'{self.WINDOW_NAME}Embedded')
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setProperty('saveWindowPref', True)
        self.setMinimumWidth(880)
        self.setMinimumHeight(480)

        self._suppress_signals = False

        self.create_layout()
        self.connect_signals()
        self.populate()
        if embedded:
            self._apply_embedded_trim()

        # Scene-open / scene-new → re-read the new scene's anim clips so the
        # window reflects the new scene instead of the stale one.
        self._scene_callback_ids = []
        for ev in (om.MSceneMessage.kAfterOpen, om.MSceneMessage.kAfterNew):
            try:
                cid = om.MSceneMessage.addCallback(ev, lambda *_: self.populate())
                self._scene_callback_ids.append(cid)
            except Exception:
                traceback.print_exc()
        # closeEvent never fires for embedded children (they die via
        # deleteLater with the popover frame) — the destroyed signal is
        # the reliable cleanup path. Ids captured in closure so the
        # slot doesn't dereference self after the Python attrs are gone.
        if self._scene_callback_ids:
            ids = list(self._scene_callback_ids)

            def _on_destroyed(_obj=None, _ids=ids):
                for cid in _ids:
                    try:
                        om.MMessage.removeCallback(cid)
                    except Exception:
                        pass
            self.destroyed.connect(_on_destroyed)

    def closeEvent(self, event):
        # Tear down the scene-refresh callbacks so they don't fire on a
        # deleted window after close.
        for cid in getattr(self, '_scene_callback_ids', []):
            try:
                om.MMessage.removeCallback(cid)
            except Exception:
                pass
        self._scene_callback_ids = []
        super().closeEvent(event)

    def _apply_embedded_trim(self):
        """Popover hosting: the drag bar carries identity and popovers
        carry no log (Adrian, 2026-07-05) — everything else stays,
        the popover IS the full tool."""
        for w in (self._brand_widget, self._brand_rule,
                  self.log_section):
            w.setVisible(False)

    # ─────────────────────────────────────────
    # Layout
    # ─────────────────────────────────────────

    def create_layout(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 12)

        # ── brand bar — display heading + tool category ──
        # (wrapped in a QWidget so embedded mode can hide it)
        self._brand_widget = QtWidgets.QWidget()
        brand_row = QtWidgets.QHBoxLayout(self._brand_widget)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(10)
        # FS export banner (Adrian, 2026-07-09), shared with the Rig Exporter;
        # text title returns if the PNG is missing.
        from PySide6.QtGui import QPixmap
        from maya_tools.framework.toolbar.widgets import icon_button
        _banner = QPixmap(str(icon_button._ICON_DIR / 'fs_export_banner.png'))
        if not _banner.isNull():
            title = QtWidgets.QLabel()
            title.setPixmap(_banner.scaledToHeight(
                40, QtCore.Qt.TransformationMode.SmoothTransformation))
        else:
            title = mindmeld_style.brand_label('Anim Exporter')
        subtitle = mindmeld_style.caps_label('// clips · gameplay + cinematic')
        brand_row.addWidget(title)
        brand_row.addWidget(subtitle, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        layout.addWidget(self._brand_widget)
        self._brand_rule = mindmeld_style.horizontal_rule()
        layout.addWidget(self._brand_rule)

        # ── Output Directory ──
        # Editable; auto-filled from the project config's anim_path_patterns
        # at populate() time. User can point export at any directory.
        header_form = QtWidgets.QFormLayout()
        header_form.setSpacing(6)
        header_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self.output_dir_edit = QtWidgets.QLineEdit()
        self.output_dir_edit.setPlaceholderText(
            '(scene not saved or project config did not match — pick a directory)'
        )
        # Auto re-derives the project-config anim destination on demand; the
        # field itself is a per-scene saved override (Adrian, 2026-07-09).
        self.output_dir_auto_btn = mindmeld_style.button('Auto', kind='default')
        self.output_dir_auto_btn.setFixedWidth(60)
        self.output_dir_auto_btn.setToolTip(
            'Fill with the auto path (this scene\'s project-config anim '
            'destination) and clear the saved override.')
        self.output_dir_browse_btn = mindmeld_style.button('Browse…', kind='default')
        self.output_dir_browse_btn.setFixedWidth(90)
        output_row = QtWidgets.QHBoxLayout()
        output_row.setSpacing(6)
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.addWidget(self.output_dir_edit, 1)
        output_row.addWidget(self.output_dir_auto_btn)
        output_row.addWidget(self.output_dir_browse_btn)
        output_row_wrapper = QtWidgets.QWidget()
        output_row_wrapper.setLayout(output_row)
        header_form.addRow(mindmeld_style.field_label('Output Dir'),
                           output_row_wrapper)
        layout.addLayout(header_form)

        # ── Fix Paths banner (ember-tinted) ──
        self.fix_paths_bar = QtWidgets.QFrame()
        self.fix_paths_bar.setStyleSheet(
            f'QFrame {{ background-color: rgba(255, 122, 61, 28); '
            f'border: 1px solid {mindmeld_style.EMBER_DIM}; }}'
            f'QFrame QLabel {{ color: {mindmeld_style.EMBER}; background: transparent; }}'
        )
        bar_layout = QtWidgets.QHBoxLayout(self.fix_paths_bar)
        bar_layout.setContentsMargins(10, 6, 10, 6)
        bar_layout.setSpacing(10)
        self.fix_paths_label = QtWidgets.QLabel('')
        self.fix_paths_btn = mindmeld_style.button('Fix Paths', kind='danger')
        self.fix_paths_btn.setFixedWidth(110)
        bar_layout.addWidget(self.fix_paths_label, 1)
        bar_layout.addWidget(self.fix_paths_btn)
        layout.addWidget(self.fix_paths_bar)
        self.fix_paths_bar.setVisible(False)

        # ── Top action row ──
        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(8)
        self.add_btn = mindmeld_style.button('+ Add Clip from Range', kind='default')
        self.dup_btn = mindmeld_style.button('Duplicate Selected', kind='default')
        self.delete_selected_btn = mindmeld_style.button('Delete Selected', kind='danger')
        top_row.addWidget(self.add_btn)
        top_row.addWidget(self.dup_btn)
        top_row.addStretch()
        top_row.addWidget(self.delete_selected_btn)
        layout.addLayout(top_row)

        # Table
        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ['', 'PREFIX', 'RIG', 'CLIP NAME', 'START', 'END', '']
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_ENABLED, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_PREFIX,  QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_NAME,    QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_RIGS,    QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_START,   QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(self.COL_END,     QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(self.COL_DETAIL,  QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(self.COL_START, 80)
        self.table.setColumnWidth(self.COL_END,   80)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        # ── Export Options (collapsed; project config autofills, the combo
        #    is the last-minute per-run override) ──
        self.options_section = CollapsibleSection('EXPORT OPTIONS')
        opts_form = QtWidgets.QFormLayout()
        opts_form.setSpacing(6)
        opts_form.setContentsMargins(0, 0, 0, 0)
        opts_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self.engine_axis_combo = QtWidgets.QComboBox()
        # itemData: '' = follow the project config; 'y'/'z' = force
        self.engine_axis_combo.addItem('Auto (project default)', '')
        self.engine_axis_combo.addItem('Y-up (Maya native: Unity, Godot)', 'y')
        self.engine_axis_combo.addItem('Z-up (Unreal)', 'z')
        self.engine_axis_combo.setToolTip(
            'Engine bone-space up axis for the exported clips. Must match '
            'how the SK_ skeletal mesh was exported or the clip plays '
            'rotated in engine. Z-up folds a -90 deg world-X rotation into '
            'the root joint frames (Unreal); Auto follows the project '
            'config (engine_up_axis in export_mapping.json).')
        opts_form.addRow(mindmeld_style.field_label('Engine Up Axis'),
                         self.engine_axis_combo)
        opts_body = QtWidgets.QVBoxLayout()
        opts_body.setContentsMargins(0, 0, 0, 0)
        opts_body.addLayout(opts_form)
        self.options_section.set_body_layout(opts_body)
        self.options_section.set_expanded(False)
        layout.addWidget(self.options_section)

        # ── Bottom action row ──
        export_row = QtWidgets.QHBoxLayout()
        export_row.setSpacing(8)
        self.export_checked_btn  = mindmeld_style.button('Export Checked',  kind='primary')
        self.export_selected_btn = mindmeld_style.button('Export Selected', kind='primary')
        self.export_all_btn      = mindmeld_style.button('Export All',      kind='primary')
        for btn in (self.export_checked_btn, self.export_selected_btn, self.export_all_btn):
            btn.setMinimumHeight(34)
        export_row.addWidget(self.export_checked_btn)
        export_row.addWidget(self.export_selected_btn)
        export_row.addWidget(self.export_all_btn)
        export_row.addStretch()

        # Maintenance gear menu
        self.maint_btn = QtWidgets.QToolButton()
        self.maint_btn.setText('⚙')
        self.maint_btn.setFixedSize(34, 34)
        self.maint_btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.maint_btn.setToolTip('Maintenance')
        maint_menu = QtWidgets.QMenu(self.maint_btn)
        self.maint_cleanup_action = maint_menu.addAction('Cleanup Stale Bindings')
        self.maint_btn.setMenu(maint_menu)
        export_row.addWidget(self.maint_btn)

        layout.addLayout(export_row)

        # ── Logger inside CollapsibleSection at the bottom ──
        self.log_section = CollapsibleSection('// LOG')
        self.logger = LoggerWidget()
        log_body_layout = QtWidgets.QVBoxLayout()
        log_body_layout.setContentsMargins(0, 0, 0, 0)
        log_body_layout.addWidget(self.logger)
        self.log_section.set_body_layout(log_body_layout)
        self.log_section.set_expanded(False)
        layout.addWidget(self.log_section)

    # ─────────────────────────────────────────
    # Signals
    # ─────────────────────────────────────────

    def connect_signals(self):
        self.add_btn.clicked.connect(self._on_add)
        self.dup_btn.clicked.connect(self._on_duplicate)
        self.delete_selected_btn.clicked.connect(self._on_delete_selected)
        self.fix_paths_btn.clicked.connect(self._on_fix_paths)
        self.output_dir_browse_btn.clicked.connect(self._on_browse_output_dir)
        self.output_dir_auto_btn.clicked.connect(self._on_output_dir_auto)
        self.output_dir_edit.textEdited.connect(self._on_output_dir_typed)
        self.output_dir_edit.editingFinished.connect(self._on_output_dir_committed)
        self.export_checked_btn.clicked.connect(self._on_export_checked)
        self.export_selected_btn.clicked.connect(self._on_export_selected)
        self.export_all_btn.clicked.connect(self._on_export_all)
        self.maint_cleanup_action.triggered.connect(self._on_cleanup_bindings)

    # ─────────────────────────────────────────
    # Populate
    # ─────────────────────────────────────────

    def populate(self):
        scene = cmds.file(q=True, sn=True) or ''

        # A per-scene saved override wins; an empty override means 'use auto',
        # so resolve the project-config anim destination as the default
        # (Adrian, 2026-07-09: opening the window no longer overwrites a dir the
        # user set by hand — the Auto button re-derives it on demand).
        try:
            override = anim_core.get_output_dir_override()
        except Exception:
            override = ''
        self.output_dir_edit.setText(override or self._auto_output_dir(scene))
        self._output_dir_user_edited = False

        # Export Options: show what Auto resolves to for this scene's project
        try:
            config = export_core.load_project_config_or_none(scene)
            auto_axis = export_core.engine_up_axis(config)
        except Exception:
            auto_axis = 'y'
        self.engine_axis_combo.setItemText(
            0, f'Auto (project: {auto_axis.upper()}-up)')

        # Fix Paths banner
        needs, src, cur = anim_app.needs_fix_paths()
        if needs:
            self.fix_paths_label.setText(f"Clips reference '{src}' but scene is '{cur}'")
            self.fix_paths_bar.setVisible(True)
        else:
            self.fix_paths_bar.setVisible(False)

        self._populate_table()
        self.logger.info('Animation Exporter ready.')

    def _on_browse_output_dir(self):
        start = self.output_dir_edit.text().strip()
        if not start or not os.path.isdir(start):
            start = os.path.dirname(cmds.file(q=True, sn=True) or '') or ''
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, 'Output Directory', start
        )
        if chosen:
            self.output_dir_edit.setText(chosen)
            self._save_output_dir_override(chosen)   # browse is a user choice

    def _auto_output_dir(self, scene: str = None) -> str:
        """The 'Auto' path: this scene's project-config gameplay anim
        destination, or '' when there is no scene / no matching project."""
        if scene is None:
            scene = cmds.file(q=True, sn=True) or ''
        if not scene:
            return ''
        try:
            config = export_core.load_project_config(scene)
            return str(export_core.resolve_anim_destination(
                scene, {'output_kind': anim_core.KIND_GAMEPLAY}, config))
        except Exception:
            return ''

    def _on_output_dir_auto(self):
        """Auto button: fill with the freshly-derived auto path and CLEAR the
        saved override (empty override = 'use auto', so it stays dynamic)."""
        self.output_dir_edit.setText(self._auto_output_dir())
        self._output_dir_user_edited = False
        try:
            anim_core.set_output_dir_override('')
        except Exception:
            pass

    def _on_output_dir_typed(self, _text=''):
        self._output_dir_user_edited = True

    def _on_output_dir_committed(self):
        """Persist the field as the per-scene override, but only when the user
        actually edited it (not on focus-loss after a programmatic set)."""
        if not getattr(self, '_output_dir_user_edited', False):
            return
        self._save_output_dir_override(self.output_dir_edit.text().strip())

    def _save_output_dir_override(self, path: str) -> None:
        self._output_dir_user_edited = False
        try:
            anim_core.set_output_dir_override(path)
        except Exception:
            pass

    def _populate_table(self):
        self._suppress_signals = True
        try:
            self.table.setRowCount(0)
            for clip_node in anim_core.list_clips():
                data = anim_core.get_clip_data(clip_node)
                self._insert_row(clip_node, data)
        finally:
            self._suppress_signals = False

    def _insert_row(self, clip_node: str, data: dict):
        row = self.table.rowCount()
        self.table.insertRow(row)

        # Enabled checkbox
        enabled_widget = QtWidgets.QWidget()
        en_layout = QtWidgets.QHBoxLayout(enabled_widget)
        en_layout.setContentsMargins(0, 0, 0, 0)
        en_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        cb = QtWidgets.QCheckBox()
        cb.setChecked(data['enabled'])
        cb.toggled.connect(lambda state, n=clip_node: self._on_enabled_toggled(n, state))
        en_layout.addWidget(cb)
        self.table.setCellWidget(row, self.COL_ENABLED, enabled_widget)

        # Prefix line edit
        prefix_edit = _SelectAllLineEdit(data['prefix'])
        prefix_edit.setMaximumWidth(80)
        prefix_edit.editingFinished.connect(
            lambda n=clip_node, w=prefix_edit: self._on_prefix_changed(n, w.text())
        )
        self.table.setCellWidget(row, self.COL_PREFIX, prefix_edit)

        # Name line edit
        name_edit = _SelectAllLineEdit(data['name'])
        name_edit.editingFinished.connect(
            lambda n=clip_node, w=name_edit: self._on_name_changed(n, w.text())
        )
        name_edit.setProperty('clip_node', clip_node)
        self.table.setCellWidget(row, self.COL_NAME, name_edit)

        # Rig summary (read-only) — single rig presentation
        rigs = data['rigs']
        if not rigs:
            summary = '(none)'
        else:
            b = rigs[0]
            if cmds.objExists(b):
                bd = rig_binding.get_binding_data(b)
                summary = bd['rig_label'] or b
            else:
                summary = '<deleted>'
        rigs_item = QtWidgets.QTableWidgetItem(summary)
        rigs_item.setData(QtCore.Qt.ItemDataRole.UserRole, clip_node)
        self.table.setItem(row, self.COL_RIGS, rigs_item)

        # Start spin
        start_spin = _SelectAllSpinBox()
        start_spin.setRange(-100000, 100000)
        start_spin.setValue(data['frame_start'])
        start_spin.valueChanged.connect(
            lambda v, n=clip_node: self._on_start_changed(n, v)
        )
        self.table.setCellWidget(row, self.COL_START, start_spin)

        # End spin
        end_spin = _SelectAllSpinBox()
        end_spin.setRange(-100000, 100000)
        end_spin.setValue(data['frame_end'])
        end_spin.valueChanged.connect(
            lambda v, n=clip_node: self._on_end_changed(n, v)
        )
        self.table.setCellWidget(row, self.COL_END, end_spin)

        # Detail button
        detail_widget = QtWidgets.QWidget()
        det_layout = QtWidgets.QHBoxLayout(detail_widget)
        det_layout.setContentsMargins(0, 0, 0, 0)
        detail_btn = mindmeld_style.button('⋯', kind='ghost')
        detail_btn.setFixedWidth(34)
        detail_btn.setMinimumWidth(34)
        detail_btn.clicked.connect(lambda _=False, n=clip_node: self._on_open_detail(n))
        det_layout.addWidget(detail_btn)
        self.table.setCellWidget(row, self.COL_DETAIL, detail_widget)

    # ─────────────────────────────────────────
    # Auto-save edit slots
    # ─────────────────────────────────────────

    def _on_enabled_toggled(self, clip_node: str, state: bool):
        if self._suppress_signals: return
        try:
            anim_core.set_clip_enabled(clip_node, state)
        except Exception as e:
            self._handle_error(e)

    def _on_prefix_changed(self, clip_node: str, value: str):
        if self._suppress_signals: return
        try:
            anim_core.set_clip_prefix(clip_node, value)
        except Exception as e:
            self._handle_error(e)

    def _on_name_changed(self, clip_node: str, value: str):
        if self._suppress_signals: return
        try:
            anim_core.set_clip_name(clip_node, value)
        except Exception as e:
            self._handle_error(e)

    def _on_start_changed(self, clip_node: str, value: int):
        if self._suppress_signals: return
        try:
            data = anim_core.get_clip_data(clip_node)
            anim_core.set_clip_range(clip_node, value, data['frame_end'])
        except Exception as e:
            self._handle_error(e)

    def _on_end_changed(self, clip_node: str, value: int):
        if self._suppress_signals: return
        try:
            data = anim_core.get_clip_data(clip_node)
            anim_core.set_clip_range(clip_node, data['frame_start'], value)
        except Exception as e:
            self._handle_error(e)

    # ─────────────────────────────────────────
    # Top-row action slots
    # ─────────────────────────────────────────

    def _on_add(self):
        try:
            node = anim_app.add_clip_from_range()
            data = anim_core.get_clip_data(node)
            self._populate_table()
            self.logger.info(f'Added clip: {data["name"] or "(unnamed)"}')
        except Exception as e:
            self._handle_error(e)

    def _on_duplicate(self):
        try:
            rows = sorted({i.row() for i in self.table.selectedIndexes()})
            if not rows:
                self.logger.warning('Select one or more clip rows first.')
                return
            for row in rows:
                rigs_item = self.table.item(row, self.COL_RIGS)
                if rigs_item:
                    src_node = rigs_item.data(QtCore.Qt.ItemDataRole.UserRole)
                    new_node = anim_app.duplicate_clip(src_node)
                    new_data = anim_core.get_clip_data(new_node)
                    self.logger.info(f'Duplicated -> {new_data["name"]}')
            self._populate_table()
        except Exception as e:
            self._handle_error(e)

    def _on_delete_selected(self):
        try:
            rows = sorted({i.row() for i in self.table.selectedIndexes()})
            if not rows:
                self.logger.warning('Select one or more clip rows first.')
                return
            nodes = []
            for row in rows:
                rigs_item = self.table.item(row, self.COL_RIGS)
                if rigs_item:
                    nodes.append(rigs_item.data(QtCore.Qt.ItemDataRole.UserRole))
            if not nodes:
                return
            confirm = QtWidgets.QMessageBox.question(
                self, 'Delete Clip(s)',
                f'Delete {len(nodes)} clip(s)? Cannot be undone '
                f'(until you reload the scene).',
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            for node in nodes:
                anim_app.delete_clip(node)
            self._populate_table()
            self.logger.info(f'Deleted {len(nodes)} clip(s).')
        except Exception as e:
            self._handle_error(e)

    def _on_fix_paths(self):
        try:
            report = anim_app.fix_paths()
            self._populate_table()
            needs, _, _ = anim_app.needs_fix_paths()
            self.fix_paths_bar.setVisible(needs)
            renamed = report.get('renamed') or []
            cleared = report.get('cleared_overrides') or []
            self.logger.success(
                f'Fix Paths: renamed {len(renamed)}, cleared {len(cleared)} dest override(s).'
            )
        except Exception as e:
            self._handle_error(e)

    # ─────────────────────────────────────────
    # Bottom-row export slots
    # ─────────────────────────────────────────

    def _engine_up_axis_override(self) -> str:
        """The Export Options pick: '' = follow the project config."""
        return self.engine_axis_combo.currentData() or ''

    def _on_export_checked(self):
        enabled = [n for n in anim_core.list_clips()
                   if anim_core.get_clip_data(n)['enabled']]
        if not enabled:
            self.logger.warning('No checked clips to export.')
            return
        progress = ExportProgressDialog(len(enabled), parent=self)
        progress.show()
        try:
            written = anim_app.export_clips(
                enabled,
                save_callback=self._save_callback, log=self.logger,
                progress_callback=progress.on_clip_started,
                cancel_check=progress.is_cancelled,
                dest_override=self.output_dir_edit.text().strip(),
                engine_up_axis_override=self._engine_up_axis_override(),
            )
            self.logger.success(f'Exported {len(written)} file(s).')
            self._toast_exported(written)
        except anim_app.ExportCancelled as e:
            self.logger.info(str(e))
        except Exception as e:
            self._handle_error(e)
        finally:
            progress.close()

    def _on_export_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            self.logger.warning('Select one or more clip rows first.')
            return
        nodes = []
        for row in rows:
            rigs_item = self.table.item(row, self.COL_RIGS)
            if rigs_item:
                nodes.append(rigs_item.data(QtCore.Qt.ItemDataRole.UserRole))
        if not nodes:
            return
        progress = ExportProgressDialog(len(nodes), parent=self)
        progress.show()
        try:
            written = anim_app.export_clips(
                nodes,
                save_callback=self._save_callback, log=self.logger,
                progress_callback=progress.on_clip_started,
                cancel_check=progress.is_cancelled,
                dest_override=self.output_dir_edit.text().strip(),
                engine_up_axis_override=self._engine_up_axis_override(),
            )
            self.logger.success(f'Exported {len(written)} of {len(nodes)} selected.')
            self._toast_exported(written)
        except anim_app.ExportCancelled as e:
            self.logger.info(str(e))
        except Exception as e:
            self._handle_error(e)
        finally:
            progress.close()

    def _on_export_all(self):
        nodes = anim_core.list_clips()
        if not nodes:
            self.logger.warning('No clips to export.')
            return
        progress = ExportProgressDialog(len(nodes), parent=self)
        progress.show()
        try:
            written = anim_app.export_clips(
                nodes,
                save_callback=self._save_callback, log=self.logger,
                progress_callback=progress.on_clip_started,
                cancel_check=progress.is_cancelled,
                dest_override=self.output_dir_edit.text().strip(),
                engine_up_axis_override=self._engine_up_axis_override(),
            )
            self.logger.success(f'Exported {len(written)} file(s) total.')
            self._toast_exported(written)
        except anim_app.ExportCancelled as e:
            self.logger.info(str(e))
        except Exception as e:
            self._handle_error(e)
        finally:
            progress.close()

    def _on_cleanup_bindings(self):
        try:
            deleted = rig_binding.cleanup_stale_bindings()
            if deleted:
                self.logger.info(f'Cleaned up {len(deleted)} stale binding(s): {deleted}')
            else:
                self.logger.info('No stale bindings found.')
            self._populate_table()
        except Exception as e:
            self._handle_error(e)

    def _on_open_detail(self, clip_node: str):
        try:
            dlg = ClipDetailDialog(clip_node, parent=self)
            dlg.exec()
            self._populate_table()
        except Exception as e:
            self._handle_error(e)

    # ─────────────────────────────────────────
    # Save-or-cancel callback for export pipeline
    # ─────────────────────────────────────────

    def _save_callback(self) -> bool:
        """Modal Save / Cancel dialog. Returns True only if the save actually
        succeeded.

        Critical for the subprocess-based pipeline: the runner opens the
        on-disk file, so the scene MUST be saved (not just clean) before
        export. Returning True without saving would let the subprocess open
        a stale on-disk file and silently export old state.

        If the save itself fails (read-only file, locked by source control,
        disk full), surface the error and return False so the export aborts.
        """
        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        msg_box.setWindowTitle('Unsaved Changes')
        msg_box.setText('The scene has unsaved changes. Save before export?')
        save_btn   = msg_box.addButton('Save',   QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg_box.addButton('Cancel', QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg_box.setDefaultButton(save_btn)
        msg_box.exec()
        if msg_box.clickedButton() != save_btn:
            return False
        try:
            cmds.file(save=True)
        except Exception as exc:
            self.logger.error(f'Save failed: {exc}')
            return False
        return True

    # ─────────────────────────────────────────
    # Error handling
    # ─────────────────────────────────────────

    def _handle_error(self, e: Exception, brief: str | None = None) -> None:
        traceback.print_exc()
        self.logger.error(brief or str(e))

    def _toast_exported(self, written) -> None:
        """Centered Mindmeld confirmation popover for a completed export.
        Guarded — a toast never blocks the export it confirms."""
        try:
            from maya_tools.utils.qt import toast
            toast.exported(written)
        except Exception:
            pass

    # ─────────────────────────────────────────
    # Show
    # ─────────────────────────────────────────

    @staticmethod
    def show_window():
        global _anim_win
        # Reload-proof singleton: the shelf reloads this module each press,
        # so close any prior window by objectName (the lost _anim_win ref
        # can't be trusted), preventing duplicate windows + duplicate scene
        # callbacks.
        close_windows_by_name(AnimExporterWindow.WINDOW_NAME)
        _anim_win = AnimExporterWindow()
        _anim_win.setWindowFlags(QtCore.Qt.WindowType.Window)
        _anim_win.show()


# ─────────────────────────────────────────────────────────────────────────────
# Per-clip detail dialog
# ─────────────────────────────────────────────────────────────────────────────

class ClipDetailDialog(QtWidgets.QDialog):
    """Edit name / output_kind / rigs (multi+ordered) / range / dest_override / delete."""

    def __init__(self, clip_node: str, parent=None):
        super().__init__(parent or get_maya_window())
        mindmeld_style.apply(self)

        self.clip_node = clip_node
        data = anim_core.get_clip_data(clip_node)
        self.setWindowTitle(f"Clip: {data['name']}")
        self.setMinimumWidth(560)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 12)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self.name_field = _SelectAllLineEdit(data['name'])
        form.addRow(mindmeld_style.field_label('Clip Name'), self.name_field)

        self.kind_combo = QtWidgets.QComboBox()
        self.kind_combo.addItems(['Gameplay', 'Cinematic'])
        self.kind_combo.setCurrentIndex(
            0 if data['output_kind'] == anim_core.KIND_GAMEPLAY else 1
        )
        form.addRow(mindmeld_style.field_label('Output Kind'), self.kind_combo)
        layout.addLayout(form)

        # Rig section
        layout.addWidget(mindmeld_style.field_label('Rig'))
        self.rigs_list = QtWidgets.QListWidget()
        self.rigs_list.setMaximumHeight(54)
        for binding in data['rigs'][:1]:  # single-rig presentation
            self._add_rig_item(binding)
        layout.addWidget(self.rigs_list)

        rigs_btn_row = QtWidgets.QHBoxLayout()
        rigs_btn_row.setSpacing(8)
        self.add_rig_combo = QtWidgets.QComboBox()
        self.add_rig_combo.setMinimumWidth(220)
        self._populate_add_rig_combo()
        self.add_rig_btn = mindmeld_style.button('Set Rig', kind='default')
        self.add_rig_btn.setFixedWidth(96)
        self.remove_rig_btn = mindmeld_style.button('Clear', kind='ghost')
        self.remove_rig_btn.setFixedWidth(96)
        self.update_rigs_sel_btn = mindmeld_style.button('Update Rig from Selection', kind='default')
        rigs_btn_row.addWidget(self.add_rig_combo)
        rigs_btn_row.addWidget(self.add_rig_btn)
        rigs_btn_row.addWidget(self.remove_rig_btn)
        rigs_btn_row.addStretch()
        rigs_btn_row.addWidget(self.update_rigs_sel_btn)
        layout.addLayout(rigs_btn_row)

        # Frame range
        range_form = QtWidgets.QFormLayout()
        range_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        range_row = QtWidgets.QHBoxLayout()
        range_row.setSpacing(6)
        self.start_spin = _SelectAllSpinBox()
        self.start_spin.setRange(-100000, 100000)
        self.start_spin.setValue(data['frame_start'])
        self.set_start_btn = mindmeld_style.button('= cur', kind='ghost')
        self.set_start_btn.setFixedWidth(58)
        self.end_spin = _SelectAllSpinBox()
        self.end_spin.setRange(-100000, 100000)
        self.end_spin.setValue(data['frame_end'])
        self.set_end_btn = mindmeld_style.button('= cur', kind='ghost')
        self.set_end_btn.setFixedWidth(58)
        self.use_pb_btn = mindmeld_style.button('Use Timeline Range', kind='default')
        range_row.addWidget(mindmeld_style.caps_label('start'))
        range_row.addWidget(self.start_spin)
        range_row.addWidget(self.set_start_btn)
        range_row.addSpacing(12)
        range_row.addWidget(mindmeld_style.caps_label('end'))
        range_row.addWidget(self.end_spin)
        range_row.addWidget(self.set_end_btn)
        range_row.addStretch()
        range_row.addWidget(self.use_pb_btn)
        range_form.addRow(mindmeld_style.field_label('Frame Range'), range_row)
        layout.addLayout(range_form)

        # Destination override
        dest_form = QtWidgets.QFormLayout()
        dest_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        dest_row = QtWidgets.QHBoxLayout()
        dest_row.setSpacing(8)
        self.dest_field = _SelectAllLineEdit(data['dest_override'])
        self.dest_field.setPlaceholderText('(use project default)')
        self.dest_browse_btn = mindmeld_style.button('Browse…', kind='ghost')
        self.dest_browse_btn.setFixedWidth(80)
        dest_row.addWidget(self.dest_field)
        dest_row.addWidget(self.dest_browse_btn)
        dest_form.addRow(mindmeld_style.field_label('Dest Override'), dest_row)
        layout.addLayout(dest_form)

        layout.addWidget(mindmeld_style.horizontal_rule())

        # Delete + close
        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.setSpacing(8)
        self.delete_btn = mindmeld_style.button('Delete Clip', kind='danger')
        self.close_btn = mindmeld_style.button('Close', kind='primary')
        bottom_row.addWidget(self.delete_btn)
        bottom_row.addStretch()
        bottom_row.addWidget(self.close_btn)
        layout.addLayout(bottom_row)

        # Connect signals — auto-save on every change
        self.name_field.editingFinished.connect(self._on_name_changed)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        self.add_rig_btn.clicked.connect(self._on_add_rig)
        self.remove_rig_btn.clicked.connect(self._on_remove_rig)
        self.update_rigs_sel_btn.clicked.connect(self._on_update_rigs_from_selection)
        self.start_spin.valueChanged.connect(self._on_range_changed)
        self.end_spin.valueChanged.connect(self._on_range_changed)
        self.set_start_btn.clicked.connect(self._on_set_start_to_current)
        self.set_end_btn.clicked.connect(self._on_set_end_to_current)
        self.use_pb_btn.clicked.connect(self._on_use_playback)
        self.dest_field.editingFinished.connect(self._on_dest_changed)
        self.dest_browse_btn.clicked.connect(self._on_browse_dest)
        self.delete_btn.clicked.connect(self._on_delete)
        self.close_btn.clicked.connect(self.accept)

    # ─────────────────────────────────────────
    # Rig list helpers
    # ─────────────────────────────────────────

    def _add_rig_item(self, binding_node: str) -> None:
        if not cmds.objExists(binding_node):
            cmds.warning(
                f'Clip {self.clip_node!r} references missing rig binding '
                f'{binding_node!r}; not shown in dialog. Re-edit the rigs list '
                f'to drop the stale connection.'
            )
            return
        bd = rig_binding.get_binding_data(binding_node)
        item = QtWidgets.QListWidgetItem(bd['rig_label'] or binding_node)
        item.setData(QtCore.Qt.ItemDataRole.UserRole, binding_node)
        self.rigs_list.addItem(item)

    def _populate_add_rig_combo(self) -> None:
        self.add_rig_combo.clear()
        for binding in rig_binding.find_live_bindings():
            bd = rig_binding.get_binding_data(binding)
            self.add_rig_combo.addItem(bd['rig_label'] or binding, userData=binding)

    def _read_rigs_from_list(self) -> list:
        out = []
        for i in range(self.rigs_list.count()):
            item = self.rigs_list.item(i)
            out.append(item.data(QtCore.Qt.ItemDataRole.UserRole))
        return out

    # ─────────────────────────────────────────
    # Slots
    # ─────────────────────────────────────────

    def _on_name_changed(self):
        anim_core.set_clip_name(self.clip_node, self.name_field.text().strip())

    def _on_kind_changed(self, idx: int):
        kind = anim_core.KIND_GAMEPLAY if idx == 0 else anim_core.KIND_CINEMATIC
        anim_core.set_clip_kind(self.clip_node, kind)

    def _on_add_rig(self):
        binding = self.add_rig_combo.currentData()
        if not binding:
            return
        # Single-rig UI — replace any existing entry
        self.rigs_list.clear()
        self._add_rig_item(binding)
        anim_core.set_clip_rigs(self.clip_node, self._read_rigs_from_list())

    def _on_remove_rig(self):
        rows = sorted({i.row() for i in self.rigs_list.selectedIndexes()},
                      reverse=True)
        for r in rows:
            self.rigs_list.takeItem(r)
        anim_core.set_clip_rigs(self.clip_node, self._read_rigs_from_list())

    def _on_update_rigs_from_selection(self):
        bindings = anim_app.bindings_from_selection()
        if not bindings:
            QtWidgets.QMessageBox.warning(
                self, 'No Rig Binding',
                'No selected joints resolve to a rig binding.',
            )
            return
        # Single-rig UI — take the first binding only
        rig = bindings[:1]
        anim_core.set_clip_rigs(self.clip_node, rig)
        self.rigs_list.clear()
        for b in rig:
            self._add_rig_item(b)

    def _on_range_changed(self, _val):
        anim_core.set_clip_range(
            self.clip_node,
            self.start_spin.value(),
            self.end_spin.value(),
        )

    def _on_set_start_to_current(self):
        f = int(cmds.currentTime(q=True))
        self.start_spin.setValue(f)

    def _on_set_end_to_current(self):
        f = int(cmds.currentTime(q=True))
        self.end_spin.setValue(f)

    def _on_use_playback(self):
        self.start_spin.setValue(int(cmds.playbackOptions(q=True, min=True)))
        self.end_spin.setValue(int(cmds.playbackOptions(q=True, max=True)))

    def _on_dest_changed(self):
        anim_core.set_clip_dest_override(self.clip_node,
                                         self.dest_field.text().strip())

    def _on_browse_dest(self):
        current = self.dest_field.text().strip() or os.path.expanduser('~')
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, 'Destination Folder', current
        )
        if path:
            self.dest_field.setText(path)
            anim_core.set_clip_dest_override(self.clip_node, path)

    def _on_delete(self):
        confirm = QtWidgets.QMessageBox.question(
            self, 'Delete Clip',
            f"Delete clip '{self.name_field.text()}'?\n"
            f"This cannot be undone (until you reload the scene).",
            QtWidgets.QMessageBox.StandardButton.Yes |
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm == QtWidgets.QMessageBox.StandardButton.Yes:
            anim_core.delete_clip(self.clip_node)
            self.accept()
