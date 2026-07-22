"""Exporter UI — multi-entry list with one-button + per-entry exports + Fix Paths.

All export logic lives in exporter_app.py; this file handles user interaction only.
"""
__author__ = "Adrian Melian"

import os
import traceback
from pathlib import Path

import maya.cmds as cmds
import maya.api.OpenMaya as om

from PySide6 import QtCore, QtGui, QtWidgets

from maya_tools.export import export_core, exporter_app
from maya_tools.utils.maya.gui import get_maya_window, close_windows_by_name
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget
from maya_tools.utils.qt import progress_card_guard as card_guard


_TYPE_LABEL = {
    export_core.TYPE_STATIC:   'static',
    export_core.TYPE_SKELETAL: 'skeletal',
}


class _SelectAllLineEdit(QtWidgets.QLineEdit):
    """QLineEdit that selects all text on focus. Same behavior as the anim
    exporter's _SelectAllLineEdit (anim_ui.py): defer the selectAll to the
    next event-loop tick so the focus-grabbing click doesn't immediately
    clear the selection."""

    def focusInEvent(self, event):
        super().focusInEvent(event)
        QtCore.QTimer.singleShot(0, self.selectAll)


# ─────────────────────────────────────────────────────────────────────────────
# Per-entry detail dialog
# ─────────────────────────────────────────────────────────────────────────────

class EntryDetailDialog(QtWidgets.QDialog):
    """Edit name / enabled / dest_override / selection / delete for one entry."""

    def __init__(self, entry_node: str, parent=None):
        super().__init__(parent or get_maya_window())
        mindmeld_style.apply(self)

        self.entry_node = entry_node
        self.deleted    = False

        data = export_core.get_entry_data(entry_node)
        self.setWindowTitle(f"Entry: {data['name']}")
        self.setMinimumWidth(440)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 12)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self.prefix_field = QtWidgets.QLineEdit(data['prefix'])
        self.prefix_field.setMaximumWidth(120)
        form.addRow(mindmeld_style.field_label('Prefix'), self.prefix_field)

        self.name_field = QtWidgets.QLineEdit(data['name'])
        form.addRow(mindmeld_style.field_label('Name'), self.name_field)

        self.type_label = QtWidgets.QLabel(_TYPE_LABEL.get(data['type'], data['type']).upper())
        self.type_label.setProperty('mindmeld', 'dim')
        form.addRow(mindmeld_style.field_label('Type'), self.type_label)

        self.enabled_cb = QtWidgets.QCheckBox('enabled')
        self.enabled_cb.setChecked(data['enabled'])
        form.addRow(mindmeld_style.field_label('State'), self.enabled_cb)

        dest_row             = QtWidgets.QHBoxLayout()
        dest_row.setSpacing(8)
        self.dest_field      = QtWidgets.QLineEdit(data['dest_override'])
        self.dest_field.setPlaceholderText('(use project default)')
        self.dest_browse_btn = mindmeld_style.button('Browse…', kind='ghost')
        self.dest_browse_btn.setFixedWidth(80)
        dest_row.addWidget(self.dest_field)
        dest_row.addWidget(self.dest_browse_btn)
        form.addRow(mindmeld_style.field_label('Dest Override'), dest_row)

        self.nodes_list = QtWidgets.QListWidget()
        self.nodes_list.setMaximumHeight(140)
        for n in data['nodes']:
            item = QtWidgets.QListWidgetItem(n)
            if not cmds.objExists(n):
                item.setForeground(QtGui.QBrush(QtGui.QColor(mindmeld_style.EMBER)))
                item.setText(f'{n}   (missing)')
            self.nodes_list.addItem(item)
        form.addRow(mindmeld_style.field_label('Connected Nodes'), self.nodes_list)

        layout.addLayout(form)

        update_row = QtWidgets.QHBoxLayout()
        update_row.setSpacing(8)
        self.update_sel_btn = mindmeld_style.button('Update Selection', kind='default')
        self.update_sel_btn.setToolTip("Replace this entry's connected nodes with the current viewport selection.")
        self.delete_btn = mindmeld_style.button('Delete Entry', kind='danger')
        update_row.addWidget(self.update_sel_btn)
        update_row.addStretch()
        update_row.addWidget(self.delete_btn)
        layout.addLayout(update_row)

        layout.addWidget(mindmeld_style.horizontal_rule())

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = button_box.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            mindmeld_style.tag(ok_btn, 'primary')
            ok_btn.setText('OK')
        cancel_btn = button_box.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            mindmeld_style.tag(cancel_btn, 'ghost')
            cancel_btn.setText('CANCEL')
        layout.addWidget(button_box)

        self.dest_browse_btn.clicked.connect(self._on_browse_dest)
        self.update_sel_btn.clicked.connect(self._on_update_selection)
        self.delete_btn.clicked.connect(self._on_delete)
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)

    def _on_browse_dest(self):
        current = self.dest_field.text().strip()
        start   = current if current else os.path.expanduser('~')
        path    = QtWidgets.QFileDialog.getExistingDirectory(self, 'Destination Folder', start)
        if path:
            self.dest_field.setText(path)

    def _on_update_selection(self):
        sel = cmds.ls(sl=True, long=False) or []
        if not sel:
            QtWidgets.QMessageBox.warning(self, 'No Selection', 'Select nodes in the viewport first.')
            return
        export_core.set_entry_nodes(self.entry_node, sel)
        self.nodes_list.clear()
        for n in sel:
            self.nodes_list.addItem(QtWidgets.QListWidgetItem(n))

    def _on_delete(self):
        confirm = QtWidgets.QMessageBox.question(
            self, 'Delete Entry',
            f'Delete entry "{self.name_field.text()}"?\nThis cannot be undone (until you reload the scene).',
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm == QtWidgets.QMessageBox.StandardButton.Yes:
            export_core.delete_entry(self.entry_node)
            self.deleted = True
            self.accept()

    def _on_accept(self):
        if not self.deleted:
            export_core.set_entry_prefix(self.entry_node, self.prefix_field.text())
            export_core.set_entry_name(self.entry_node, self.name_field.text().strip() or 'unnamed')
            export_core.set_entry_enabled(self.entry_node, self.enabled_cb.isChecked())
            export_core.set_entry_dest_override(self.entry_node, self.dest_field.text().strip())
        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class ExporterWindow(QtWidgets.QDialog):
    WINDOW_NAME  = 'AMExporterTool'
    WINDOW_TITLE = 'Rig Exporter'

    COL_ENABLED    = 0
    COL_PREFIX     = 1
    COL_NAME       = 2
    COL_TYPE       = 3
    COL_SELECTION  = 4
    COL_DETAIL     = 5

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
        self.setMinimumWidth(680)
        self.setMinimumHeight(420)

        self.create_layout()
        self.connect_signals()
        self.populate()
        if embedded:
            self._apply_embedded_trim()

        # Scene-open / scene-new → re-read the new scene's exporter registry
        # so the window reflects the new scene instead of the stale one.
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
        # FS export banner (Adrian, 2026-07-09), shared with the Anim Exporter;
        # text title returns if the PNG is missing.
        from maya_tools.framework.toolbar.widgets import icon_button
        _banner = QtGui.QPixmap(str(icon_button._ICON_DIR / 'fs_export_banner.png'))
        if not _banner.isNull():
            title = QtWidgets.QLabel()
            title.setPixmap(_banner.scaledToHeight(
                40, QtCore.Qt.TransformationMode.SmoothTransformation))
        else:
            title = mindmeld_style.brand_label('Rig Exporter')
        subtitle = mindmeld_style.caps_label('// model · static + skeletal')
        brand_row.addWidget(title)
        brand_row.addWidget(subtitle, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        layout.addWidget(self._brand_widget)
        self._brand_rule = mindmeld_style.horizontal_rule()
        layout.addWidget(self._brand_rule)

        # ── Output Directory ──
        # Editable; auto-filled from the project config's resolved destination
        # at populate() time. User can point export at any directory.
        header_form = QtWidgets.QFormLayout()
        header_form.setSpacing(6)
        header_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self.output_dir_edit = QtWidgets.QLineEdit()
        self.output_dir_edit.setPlaceholderText(
            '(scene not saved or project config did not match — pick a directory)'
        )
        # Auto re-derives the project-config destination on demand; the field
        # itself is a per-scene saved override (Adrian, 2026-07-09: opening the
        # window no longer overwrites a directory the user set by hand).
        self.output_dir_auto_btn = mindmeld_style.button('Auto', kind='default')
        self.output_dir_auto_btn.setFixedWidth(60)
        self.output_dir_auto_btn.setToolTip(
            'Fill with the auto path (this scene\'s project-config destination) '
            'and clear the saved override.')
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

        # ── Fix Paths banner (hidden by default, ember-tinted) ──
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
        self.create_btn = mindmeld_style.button('+ Create Entry', kind='default')
        self.create_btn.setToolTip(
            'Seed an entry straight from the rig: the World/root joint plus the '
            'mesh group (project-setup Mesh Group Name, else a geo_grp/mesh_grp '
            'group). No selection needed.')
        self.add_btn = mindmeld_style.button('+ Add Entry from Selection', kind='default')
        self.delete_selected_btn = mindmeld_style.button('Delete Selected', kind='danger')
        top_row.addWidget(self.create_btn)
        top_row.addWidget(self.add_btn)
        top_row.addStretch()
        top_row.addWidget(self.delete_selected_btn)
        layout.addLayout(top_row)

        # ── Entries table ──
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['', 'PREFIX', 'NAME', 'TYPE', 'SELECTION', ''])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_ENABLED,   QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_PREFIX,    QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_NAME,      QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(self.COL_TYPE,      QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_SELECTION, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_DETAIL,    QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.resizeSection(self.COL_NAME, 240)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
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
            'Engine bone-space up axis for skeletal exports. Z-up folds a '
            '-90 deg world-X rotation into the root joint frame so a Z-up '
            'engine (Unreal) imports the root bone at identity — required '
            'for engine-authored animations (UE mannequin) to play on the '
            'exported skeleton without a 90 deg pitch. Auto follows the '
            'project config (engine_up_axis in export_mapping.json).')
        opts_form.addRow(mindmeld_style.field_label('Engine Up Axis'),
                         self.engine_axis_combo)
        opts_body = QtWidgets.QVBoxLayout()
        opts_body.setContentsMargins(0, 0, 0, 0)
        opts_body.addLayout(opts_form)
        self.options_section.set_body_layout(opts_body)
        self.options_section.set_expanded(False)
        layout.addWidget(self.options_section)

        # ── Export action row ──
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
        layout.addLayout(export_row)

        # ── Logger inside CollapsibleSection at the bottom ──
        self.log_section = CollapsibleSection('// LOG')
        self.logger = LoggerWidget()
        # (log_section hidden by _apply_embedded_trim in popover hosting)
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
        self.create_btn.clicked.connect(self._on_create_entry)
        self.delete_selected_btn.clicked.connect(self._on_delete_selected)
        self.output_dir_browse_btn.clicked.connect(self._on_browse_output_dir)
        self.output_dir_auto_btn.clicked.connect(self._on_output_dir_auto)
        # Persist ONLY genuine user edits: textEdited fires on keystrokes (never
        # on programmatic setText), so populate()/Auto filling the field can't
        # falsely save an override. Committed on editingFinished.
        self.output_dir_edit.textEdited.connect(self._on_output_dir_typed)
        self.output_dir_edit.editingFinished.connect(self._on_output_dir_committed)
        self.fix_paths_btn.clicked.connect(self._on_fix_paths)
        self.export_checked_btn.clicked.connect(self._on_export_checked)
        self.export_selected_btn.clicked.connect(self._on_export_selected)
        self.export_all_btn.clicked.connect(self._on_export_all)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)

    # ─────────────────────────────────────────
    # Populate
    # ─────────────────────────────────────────

    def populate(self):
        scene = cmds.file(q=True, sn=True) or ''

        # Output Dir: a per-scene saved override wins; an empty override means
        # 'use auto', so resolve the project-config destination as the default
        # (Adrian, 2026-07-09: opening the window no longer overwrites a dir the
        # user set by hand — the Auto button re-derives it on demand).
        try:
            override = export_core.get_output_dir_override()
        except Exception:
            override = ''
        self.output_dir_edit.setText(override or self._auto_output_dir(scene))
        self._output_dir_user_edited = False

        # Fix Paths banner
        needs, src, cur = exporter_app.needs_fix_paths()
        if needs:
            self.fix_paths_label.setText(f"Entries reference '{src}' but scene is '{cur}'")
            self.fix_paths_bar.setVisible(True)
        else:
            self.fix_paths_bar.setVisible(False)

        # Export Options: show what Auto resolves to for this scene's project
        try:
            config = export_core.load_project_config_or_none(scene)
            auto_axis = export_core.engine_up_axis(config)
        except Exception:
            auto_axis = 'y'
        self.engine_axis_combo.setItemText(
            0, f'Auto (project: {auto_axis.upper()}-up)')

        # Entries table
        self._populate_table()
        self.logger.info('Exporter ready.')

    def _populate_table(self):
        self.table.setRowCount(0)
        for entry_node in export_core.list_entries():
            data = export_core.get_entry_data(entry_node)
            row  = self.table.rowCount()
            self.table.insertRow(row)

            # Enabled checkbox
            enabled_widget = QtWidgets.QWidget()
            enabled_layout = QtWidgets.QHBoxLayout(enabled_widget)
            enabled_layout.setContentsMargins(0, 0, 0, 0)
            enabled_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            cb = QtWidgets.QCheckBox()
            cb.setChecked(data['enabled'])
            cb.toggled.connect(lambda state, n=entry_node: export_core.set_entry_enabled(n, state))
            enabled_layout.addWidget(cb)
            self.table.setCellWidget(row, self.COL_ENABLED, enabled_widget)

            # Missing-nodes check (used to color both PREFIX and NAME red)
            missing = [n for n in data['nodes'] if not cmds.objExists(n)]
            ember_palette = QtGui.QPalette()
            ember_palette.setColor(QtGui.QPalette.ColorRole.Text,
                                    QtGui.QColor(mindmeld_style.EMBER))

            # Prefix line edit (max-width 80 mirrors the anim exporter)
            prefix_edit = _SelectAllLineEdit(data['prefix'])
            prefix_edit.setMaximumWidth(80)
            if missing:
                prefix_edit.setPalette(ember_palette)
            prefix_edit.editingFinished.connect(
                lambda n=entry_node, w=prefix_edit: self._on_prefix_changed(n, w.text())
            )
            self.table.setCellWidget(row, self.COL_PREFIX, prefix_edit)

            # Name line edit
            name_edit = _SelectAllLineEdit(data['name'])
            if missing:
                name_edit.setPalette(ember_palette)
            name_edit.editingFinished.connect(
                lambda n=entry_node, w=name_edit: self._on_name_changed(n, w.text())
            )
            name_edit.setProperty('entry_node', entry_node)
            self.table.setCellWidget(row, self.COL_NAME, name_edit)

            # Type
            type_item = QtWidgets.QTableWidgetItem(_TYPE_LABEL.get(data['type'], data['type']))
            self.table.setItem(row, self.COL_TYPE, type_item)

            # Selection summary
            summary = self._format_selection(data['nodes'])
            sel_item = QtWidgets.QTableWidgetItem(summary)
            if missing:
                sel_item.setToolTip(f"Missing: {', '.join(missing)}")
            self.table.setItem(row, self.COL_SELECTION, sel_item)

            # Detail button
            detail_widget = QtWidgets.QWidget()
            detail_layout = QtWidgets.QHBoxLayout(detail_widget)
            detail_layout.setContentsMargins(0, 0, 0, 0)
            detail_btn = mindmeld_style.button('⋯', kind='ghost')
            detail_btn.setFixedWidth(34)
            detail_btn.setMinimumWidth(34)
            detail_btn.clicked.connect(lambda _=False, n=entry_node: self._on_open_detail(n))
            detail_layout.addWidget(detail_btn)
            self.table.setCellWidget(row, self.COL_DETAIL, detail_widget)

    @staticmethod
    def _format_selection(nodes: list[str]) -> str:
        if not nodes:
            return '(empty)'
        if len(nodes) <= 3:
            return ' + '.join(nodes)
        return f'{nodes[0]} + {nodes[1]} + … ({len(nodes)} nodes)'

    # ─────────────────────────────────────────
    # Slots
    # ─────────────────────────────────────────

    def _on_add(self):
        try:
            node = exporter_app.add_entry_from_selection()
            self.populate()
            self.logger.success(f'Added entry: {export_core.get_entry_data(node)["name"]}')
        except Exception as e:
            self._handle_error(e)

    def _on_create_entry(self):
        try:
            node = exporter_app.create_seeded_entry()
            self.populate()
            self.logger.success(
                f'Created entry: {export_core.get_entry_data(node)["name"]}')
        except Exception as e:
            self._handle_error(e)

    def _on_prefix_changed(self, entry_node: str, value: str):
        try:
            export_core.set_entry_prefix(entry_node, value)
        except Exception as e:
            self._handle_error(e)

    def _on_name_changed(self, entry_node: str, value: str):
        try:
            export_core.set_entry_name(entry_node, value.strip() or 'unnamed')
        except Exception as e:
            self._handle_error(e)

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
        """The 'Auto' path: this scene's project-config resolved destination,
        or '' when there is no scene / no matching project."""
        if scene is None:
            scene = cmds.file(q=True, sn=True) or ''
        if not scene:
            return ''
        try:
            config = export_core.load_project_config(scene)
            return str(export_core.resolve_destination(scene, config))
        except Exception:
            return ''

    def _on_output_dir_auto(self):
        """Auto button: fill with the freshly-derived auto path and CLEAR the
        saved override (empty override = 'use auto', so it stays dynamic)."""
        self.output_dir_edit.setText(self._auto_output_dir())
        self._output_dir_user_edited = False
        try:
            export_core.set_output_dir_override('')
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
            export_core.set_output_dir_override(path)
        except Exception:
            pass

    def _on_delete_selected(self):
        try:
            rows = sorted({i.row() for i in self.table.selectedIndexes()})
            if not rows:
                self.logger.warning('Select one or more entry rows first.')
                return
            nodes = []
            for row in rows:
                widget = self.table.cellWidget(row, self.COL_NAME)
                if widget:
                    nodes.append(widget.property('entry_node'))
            if not nodes:
                return
            confirm = QtWidgets.QMessageBox.question(
                self, 'Delete Entry(ies)',
                f'Delete {len(nodes)} entry(ies)? Cannot be undone '
                f'(until you reload the scene).',
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            for node in nodes:
                export_core.delete_entry(node)
            self._populate_table()
            self.logger.info(f'Deleted {len(nodes)} entry(ies).')
        except Exception as e:
            self._handle_error(e)

    def _on_fix_paths(self):
        try:
            report = exporter_app.fix_paths()
            self._populate_table()
            needs, _, _ = exporter_app.needs_fix_paths()
            self.fix_paths_bar.setVisible(needs)

            renamed = report.get('renamed') or []
            cleared = report.get('cleared_overrides') or []
            missing = report.get('missing_nodes') or {}

            parts = []
            if renamed:
                parts.append(f'renamed {len(renamed)}')
            if cleared:
                parts.append(f'cleared {len(cleared)} dest override(s)')
            if missing:
                parts.append(f'{sum(len(v) for v in missing.values())} missing node(s)')
            summary = ', '.join(parts) if parts else 'no changes needed'
            msg = f'Fix Paths: {summary}. Save the scene to persist.'
            if missing:
                self.logger.error(msg)
            else:
                self.logger.success(msg)

            if missing:
                detail = '\n'.join(f'• {entry}: {", ".join(nodes)}' for entry, nodes in missing.items())
                QtWidgets.QMessageBox.warning(
                    self, 'Missing Nodes',
                    f'Some entries reference nodes that don\'t exist in this scene:\n\n{detail}\n\n'
                    f'Open the entry detail dialog to fix or update selections.'
                )
        except Exception as e:
            self._handle_error(e)

    def _engine_up_axis_override(self) -> str:
        """The Export Options pick: '' = follow the project config."""
        return self.engine_axis_combo.currentData() or ''

    def _on_export_progress(self, index: int, total: int, name: str) -> None:
        """exporter_app progress hook — ticks the coach card per entry.
        Called before each entry exports, so the bar shows work done."""
        card_guard.update(int((index - 1) / max(1, total) * 100),
                          f'Exporting {name} ({index} of {total})')

    def _run_export(self, entry_nodes=None, label: str = 'entry(ies)'):
        """Shared export body for the three buttons: coach card around
        the blocking export, per-entry ticks, success log + toast.
        entry_nodes=None means export_all (every enabled entry)."""
        owns_card = card_guard.start('Exporting files', 'Preparing export...')
        written = []
        ok = False
        try:
            if entry_nodes is None:
                written = exporter_app.export_all(
                    dest_override=self.output_dir_edit.text().strip(),
                    engine_up_axis_override=self._engine_up_axis_override(),
                    progress_cb=self._on_export_progress,
                )
            else:
                written = exporter_app.export_selected_entries(
                    entry_nodes,
                    dest_override=self.output_dir_edit.text().strip(),
                    engine_up_axis_override=self._engine_up_axis_override(),
                    progress_cb=self._on_export_progress,
                )
            ok = True
        except Exception as e:
            self._handle_error(e)
        finally:
            if owns_card:
                if ok:
                    card_guard.finish(f'Exported {len(written)} file(s)')
                else:
                    card_guard.fail('Export failed. See the log.')
        if not ok:
            return
        if entry_nodes is None:
            self.logger.success(f'Exported {len(written)} file(s).')
        else:
            self.logger.success(
                f'Exported {len(written)} of {len(entry_nodes)} {label}.')
        self._toast_exported(written)

    def _on_export_checked(self):
        self._run_export()

    def _on_export_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            self.logger.error('Select one or more entry rows in the table first.')
            return
        entry_nodes = []
        for row in rows:
            widget = self.table.cellWidget(row, self.COL_NAME)
            if widget:
                entry_nodes.append(widget.property('entry_node'))
        self._run_export(entry_nodes, label='selected entry(ies)')

    def _on_export_all(self):
        self._run_export(export_core.list_entries())

    def _on_table_context_menu(self, pos: QtCore.QPoint):
        """Right-click a single row: export just that entry, no checking
        boxes or multi-select required. Empty space under the rows is a
        no-op (Adrian, 2026-07-21)."""
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        widget = self.table.cellWidget(row, self.COL_NAME)
        entry_node = widget.property('entry_node') if widget else None
        if not entry_node:
            return

        name = widget.text().strip() if widget else ''
        label = f'Export This Entry ({name})' if name else 'Export This Entry'

        menu = QtWidgets.QMenu(self.table)
        export_act = menu.addAction(label)
        export_act.triggered.connect(
            lambda _checked=False, n=entry_node: self._run_export([n], label='entry'))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_open_detail(self, entry_node: str):
        dlg = EntryDetailDialog(entry_node, parent=self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._populate_table()

    # ─────────────────────────────────────────
    # Status / error
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
        global _win
        # Reload-proof singleton: the shelf reloads this module each press,
        # so close any prior window by objectName (the lost _win ref can't
        # be trusted), preventing duplicate windows + duplicate scene
        # callbacks.
        close_windows_by_name(ExporterWindow.WINDOW_NAME)
        _win = ExporterWindow()
        _win.setWindowFlags(QtCore.Qt.WindowType.Window)
        _win.show()
