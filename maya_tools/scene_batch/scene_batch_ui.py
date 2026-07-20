# maya_tools/scene_batch/scene_batch_ui.py
"""SceneBatchWindow — scene list + script editor + export/save checkboxes
+ Run button + log.

Run handler is implemented in Task 5 (uses BatchProgressDialog to drive
the per-scene subprocess loop). Auto-restore + debounced save are added
in Task 6.
"""
__author__ = "Adrian Melian"

import traceback
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from maya_tools.scene_batch import scene_batch_app as batch_app
from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import (
    BatchProgressDialog, CollapsibleSection, LoggerWidget,
)


_win = None  # show_window's singleton handle


class SceneBatchWindow(QtWidgets.QWidget):
    """Top-level window for the Maya Scene Batch tool."""

    def __init__(self, parent=None):
        super().__init__(parent or get_maya_window())
        self.setWindowFlags(QtCore.Qt.WindowType.Window)
        self.setWindowTitle('Batch Runner')
        self.setMinimumSize(640, 600)
        mindmeld_style.apply(self)

        # 500ms debounce on state writes — avoids hammering the disk while
        # the user is mid-type in the script editor.
        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_state)

        # Suppress save signals during populate (the restore flow below),
        # so restoring state doesn't ping-pong into another save.
        self._restoring = True

        self.create_layout()
        self.connect_signals()
        self._restore_state()
        self._restoring = False

    # ── Layout ─────────────────────────────────────────────────

    def create_layout(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Brand row: Batch Runner banner image (Adrian, 2026-07-11), text
        # fallback if the PNG is missing. Public name is "Batch Runner";
        # the code identity stays scene_batch / SceneBatchWindow.
        brand_row = QtWidgets.QHBoxLayout()
        from PySide6.QtGui import QPixmap
        from maya_tools.framework.toolbar.widgets import icon_button
        _pix = QPixmap(str(icon_button._ICON_DIR / 'fs_batchrunner_banner.png'))
        if not _pix.isNull():
            title = QtWidgets.QLabel()
            title.setPixmap(_pix.scaledToHeight(
                40, QtCore.Qt.TransformationMode.SmoothTransformation))
        else:
            title = mindmeld_style.brand_label('Batch Runner')
        subtitle = mindmeld_style.caps_label('// pipeline · batches')
        brand_row.addWidget(title)
        brand_row.addWidget(subtitle, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        root.addLayout(brand_row)
        root.addWidget(mindmeld_style.horizontal_rule())

        # Scene list
        root.addWidget(mindmeld_style.field_label('Scenes'))
        self.scene_list = QtWidgets.QListWidget()
        self.scene_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.scene_list.setMinimumHeight(120)
        root.addWidget(self.scene_list, 1)

        scene_btns = QtWidgets.QHBoxLayout()
        self.add_btn = mindmeld_style.button('+ Add Scenes', kind='default')
        self.remove_btn = mindmeld_style.button('- Remove Selected',
                                                 kind='default')
        self.clear_btn = mindmeld_style.button('Clear All', kind='ghost')
        scene_btns.addWidget(self.add_btn)
        scene_btns.addWidget(self.remove_btn)
        scene_btns.addWidget(self.clear_btn)
        scene_btns.addStretch()
        root.addLayout(scene_btns)

        # Script editor
        root.addWidget(mindmeld_style.field_label('Script (Python)'))
        self.script_edit = QtWidgets.QPlainTextEdit()
        self.script_edit.setPlaceholderText(
            '# Runs after each scene opens, before export/save.\n'
            '# Example: cmds.shelfButton(...)'
        )
        mono = QtGui.QFont('JetBrains Mono')
        mono.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        if not mono.exactMatch():
            mono = QtGui.QFont('Consolas')
            mono.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        self.script_edit.setFont(mono)
        self.script_edit.setMinimumHeight(140)
        root.addWidget(self.script_edit, 1)

        # Checkbox row
        opts_row = QtWidgets.QHBoxLayout()
        opts_row.addWidget(mindmeld_style.field_label('Per scene:'))
        self.cb_export = QtWidgets.QCheckBox('Export')
        self.cb_export.setChecked(True)
        self.cb_save = QtWidgets.QCheckBox('Save')
        self.cb_save.setChecked(False)
        opts_row.addWidget(self.cb_export)
        opts_row.addWidget(self.cb_save)
        opts_row.addStretch()
        root.addLayout(opts_row)

        # Run button (right-aligned)
        run_row = QtWidgets.QHBoxLayout()
        run_row.addStretch()
        self.run_btn = mindmeld_style.button('▶ Run', kind='primary')
        self.run_btn.setMinimumHeight(34)
        self.run_btn.setMinimumWidth(120)
        run_row.addWidget(self.run_btn)
        root.addLayout(run_row)

        # Logger
        self.log_section = CollapsibleSection('// LOG')
        self.logger = LoggerWidget()
        log_body = QtWidgets.QVBoxLayout()
        log_body.setContentsMargins(0, 0, 0, 0)
        log_body.addWidget(self.logger)
        self.log_section.set_body_layout(log_body)
        self.log_section.set_expanded(False)
        root.addWidget(self.log_section)

    def connect_signals(self):
        self.add_btn.clicked.connect(self._on_add_scenes)
        self.remove_btn.clicked.connect(self._on_remove_selected)
        self.clear_btn.clicked.connect(self._on_clear_all)
        self.run_btn.clicked.connect(self._on_run)

        # Debounced state persistence — every meaningful change schedules
        # a save 500ms later. Spamming changes resets the timer.
        self.scene_list.model().rowsInserted.connect(self._schedule_save)
        self.scene_list.model().rowsRemoved.connect(self._schedule_save)
        self.scene_list.model().modelReset.connect(self._schedule_save)
        self.script_edit.textChanged.connect(self._schedule_save)
        self.cb_export.toggled.connect(self._schedule_save)
        self.cb_save.toggled.connect(self._schedule_save)

    # ── Public window management ───────────────────────────────

    @classmethod
    def show_window(cls):
        global _win
        if _win is not None:
            try:
                _win.close()
                _win.deleteLater()
            except Exception:
                pass
        _win = cls()
        _win.show()
        _win.raise_()
        _win.activateWindow()
        return _win

    def closeEvent(self, event):
        # Flush any pending debounced save before the window dies.
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_state()
        super().closeEvent(event)

    # ── Scene-list handlers ────────────────────────────────────

    def _on_add_scenes(self):
        paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            self, 'Add Maya Scenes', '',
            'Maya Scenes (*.ma *.mb);;All Files (*)',
        )
        existing = set(self._collect_scene_strings())
        for p in paths:
            if p in existing:
                continue
            self.scene_list.addItem(p)
            existing.add(p)

    def _on_remove_selected(self):
        for item in self.scene_list.selectedItems():
            self.scene_list.takeItem(self.scene_list.row(item))

    def _on_clear_all(self):
        self.scene_list.clear()

    def _collect_scene_strings(self) -> list:
        return [self.scene_list.item(i).text()
                for i in range(self.scene_list.count())]

    def _collect_scene_paths(self) -> list:
        return [Path(s) for s in self._collect_scene_strings()]

    # ── Run handler ────────────────────────────────────────────

    def _on_run(self):
        scenes = self._collect_scene_paths()
        if not scenes:
            self.logger.warning('No scenes in the list — add some first.')
            return

        # Validate all paths upfront so the user knows about typos before
        # spawning any subprocesses.
        missing = [p for p in scenes if not p.is_file()]
        if missing:
            self.logger.error(
                f'{len(missing)} scene(s) not found on disk: '
                f'{", ".join(str(p) for p in missing)}'
            )
            return

        user_script = self.script_edit.toPlainText() or ''
        do_export = self.cb_export.isChecked()
        do_save = self.cb_save.isChecked()

        self.run_btn.setEnabled(False)
        self._set_inputs_enabled(False)
        try:
            self._execute_batch(scenes, user_script, do_export, do_save)
        finally:
            self.run_btn.setEnabled(True)
            self._set_inputs_enabled(True)

    def _execute_batch(self, scenes, user_script, do_export, do_save):
        """Run the per-scene loop inside a BatchProgressDialog."""
        total = len(scenes)
        results = []
        progress = BatchProgressDialog(
            total=total,
            title='Batch Runner',
            header_template='PROCESSING SCENE {idx} / {total}',
            parent=self,
        )
        progress.show()

        self.logger.info(f'Starting batch: {total} scene(s).')
        cancelled = False
        try:
            for i, scene_path in enumerate(scenes, 1):
                # Check cancel BEFORE updating the header — otherwise the
                # dialog flashes "PROCESSING SCENE N" for a frame before
                # the loop breaks.
                if progress.is_cancelled():
                    break
                progress.on_item_started(i, scene_path.name)
                self.logger.info(f'── Scene {i}/{total}: {scene_path.name} ──')
                try:
                    # cancel_check threads the dialog's flag through the
                    # subprocess polling loop so Cancel/ESC pressed
                    # mid-subprocess can register without waiting for
                    # the next between-scenes check.
                    result = batch_app.run_one_scene(
                        scene_path,
                        user_script=user_script,
                        export=do_export,
                        save=do_save,
                        log_callback=self.logger.info,
                        cancel_check=progress.is_cancelled,
                    )
                except Exception as exc:
                    self._handle_error(
                        exc, f'subprocess failed for {scene_path.name}'
                    )
                    result = {
                        'scene': scene_path, 'ok': False,
                        'opened': False, 'scripted': None,
                        'exported': None, 'saved': None,
                        'errors': [str(exc)], 'cancelled': False,
                    }
                results.append(result)
                self._log_scene_result(result)
        finally:
            # Capture cancel state BEFORE close — reading from a closed
            # QDialog works in practice but is brittle.
            cancelled = progress.is_cancelled()
            progress.close()

        self._log_summary(results, cancelled=cancelled)

    def _log_scene_result(self, result: dict):
        if result['ok'] and not result['errors']:
            self.logger.success(f"  ✓ {result['scene'].name}")
        else:
            stages = []
            if result['opened']:
                stages.append('opened')
            if result['scripted'] is True:
                stages.append('script')
            if result['exported'] is True:
                stages.append('export')
            if result['saved'] is True:
                stages.append('save')
            tail = f"  partial ({', '.join(stages) or 'none'})" if stages else '  failed'
            self.logger.warning(f"  ⚠ {result['scene'].name}{tail}")
            for err in result['errors']:
                self.logger.error(f'    · {err}')

    def _log_summary(self, results, cancelled: bool):
        if not results:
            self.logger.info('Batch ended with no scenes processed.')
            return
        ok = sum(1 for r in results if r['ok'] and not r['errors'])
        failed = [r['scene'].name for r in results if not r['ok'] or r['errors']]
        prefix = 'Cancelled' if cancelled else 'Batch complete'
        msg = f'{prefix}: {ok} ok, {len(failed)} failed'
        if failed:
            msg += f' ({", ".join(failed)})'
            self.logger.warning(msg)
        else:
            self.logger.success(msg)

    def _set_inputs_enabled(self, enabled: bool):
        """Lock/unlock the editor surface while a batch is running."""
        for w in (self.scene_list, self.script_edit, self.cb_export,
                  self.cb_save, self.add_btn, self.remove_btn,
                  self.clear_btn):
            w.setEnabled(enabled)

    # ── State save / restore ───────────────────────────────────

    def _schedule_save(self, *_args):
        """Bump the debounce timer. During _restore_state, no-op."""
        if self._restoring:
            return
        self._save_timer.start()

    def _save_state(self):
        try:
            batch_app.save_state(
                scenes=self._collect_scene_strings(),
                user_script=self.script_edit.toPlainText() or '',
                export=bool(self.cb_export.isChecked()),
                save=bool(self.cb_save.isChecked()),
            )
        except Exception as exc:
            # Auto-save errors are non-fatal — log and move on.
            self.logger.warning(f'State save failed: {exc}')

    def _restore_state(self):
        try:
            state = batch_app.load_state()
        except Exception as exc:
            self.logger.warning(f'State restore failed: {exc}')
            return
        for s in state['scenes']:
            self.scene_list.addItem(s)
        self.script_edit.setPlainText(state['user_script'])
        self.cb_export.setChecked(state['export'])
        self.cb_save.setChecked(state['save'])

    # ── Error helper ───────────────────────────────────────────

    def _handle_error(self, exc, brief: str = ''):
        traceback.print_exc()
        self.logger.error(brief or str(exc))
