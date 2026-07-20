# maya_tools/animation/pose_library/ui.py
"""Pose Studio v1 — main window.

3-panel layout (Adrian's 2026-07-05 restructure):
- Left: character COMBO (namespace-qualified live bindings + offline
        rig_labels — the old list "freed up a ton of space") over the
        SELECTION SETS list (moved out of the Properties dropdown),
        plus a stubbed + Add Selection Set button (wiring lands in a
        later session).
- Center: pose grid (QListWidget IconMode) — pose cards w/ thumbnails.
        Only the ACTIVE character's thumbnails ever load (guardrail).
- Right: Properties panel — current pose details + apply controls
         (blend slider, Move World checkbox; Apply = whole pose,
         Apply to Selected Controls = viewport selection — the sets
         list SELECTS, it never filters silently).

Mindmeld-styled. Headless work delegated to pose_library.app.
"""
__author__ = "Adrian Melian"

import shutil
import traceback
from pathlib import Path

from PySide6 import QtWidgets, QtCore, QtGui

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.animation.pose_library import app as pose_app
from maya_tools.animation.pose_library import sets as pose_sets
from maya_tools.animation.pose_library import thumbnails as pose_thumbs
from maya_tools.animation.characters_panel import CharactersPanel
from maya_tools.animation.pose_library.address import Address
from maya_tools.utils.maya import rig_binding
from maya_tools.utils.maya.gui import get_maya_window, close_windows_by_name
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget
from maya_tools.utils.qt.mindmeld import mindmeld_style


_OPTION_VAR_W = 'KSPoseLib_window_size_w'
_OPTION_VAR_H = 'KSPoseLib_window_size_h'
_OPTION_VAR_SPLITTER = 'KSPoseLib_splitter_state'

_DEFAULT_W, _DEFAULT_H = 1100, 720
_MIN_W, _MIN_H = 880, 540

_win = None


def _load_window_size():
    try:
        if cmds.optionVar(exists=_OPTION_VAR_W) and cmds.optionVar(exists=_OPTION_VAR_H):
            return (int(cmds.optionVar(q=_OPTION_VAR_W)),
                    int(cmds.optionVar(q=_OPTION_VAR_H)))
    except Exception:
        pass
    return _DEFAULT_W, _DEFAULT_H


def _save_window_size(w: int, h: int) -> None:
    try:
        cmds.optionVar(intValue=(_OPTION_VAR_W, w))
        cmds.optionVar(intValue=(_OPTION_VAR_H, h))
    except Exception:
        pass


def _namespace_of(binding: str) -> str:
    """Extract the namespace prefix from a binding node name.
    e.g. 'Michael_A:FAB_RigBinding_Michael' -> 'Michael_A'. Returns '' if
    the binding is in the root namespace (no colon)."""
    return binding.split(':', 1)[0] if ':' in binding else ''


def _display_label_for_binding(binding: str) -> str:
    """Friendly Characters-panel label for a live binding.
    Prefers the namespace (typical for referenced anim scenes); falls
    back to the rig_label."""
    ns = _namespace_of(binding)
    rig_label = rig_binding.get_rig_label(binding) or 'unnamed'
    return ns or rig_label


class PoseLibraryWindow(QtWidgets.QDialog):
    WINDOW_NAME = 'FSPoseLibrary'
    WINDOW_TITLE = 'Pose Studio'

    def __init__(self, parent=None, embedded: bool = False):
        """embedded=True: hosted inside another widget (the RigBot
        Library popover). Sweep-safe objectName — the standalone
        show_window() closes by objectName and must never reap the
        hosted instance — and the brand block + log trim away (the
        popover drag bar carries identity; Sets… survives the trim,
        it is the set-management door)."""
        super().__init__(parent or get_maya_window())
        self._embedded = embedded
        self.setObjectName(
            self.WINDOW_NAME if not embedded
            else f'{self.WINDOW_NAME}Embedded')
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setProperty('saveWindowPref', True)
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(*_load_window_size())

        # Active binding tracked at the window level so save/apply both
        # use the same target. Set by CharactersPanel selection.
        self._active_binding = ''
        self._active_rig_label = ''
        # Maya selection-changed callback id; removed in closeEvent.
        self._sel_callback_id = None

        mindmeld_style.apply(self)

        self.create_layout()
        self.connect_signals()
        self.populate_characters()
        self._install_selection_callback()
        if embedded:
            self._apply_embedded_trim()

    # -- Layout ----------------------------------------------------

    def create_layout(self):
        root = QtWidgets.QVBoxLayout(self)
        # Match the Animation Studio's outer frame exactly (Adrian, 2026-07-10):
        # no extra inset that reads as a border when flipping tabs.
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Brand bar — title block wrapped so embedded mode can hide it.
        title_row = QtWidgets.QHBoxLayout()
        self._brand_widget = QtWidgets.QWidget()
        title_block = QtWidgets.QVBoxLayout(self._brand_widget)
        title_block.setContentsMargins(0, 0, 0, 0)
        # FS library banner (Adrian, 2026-07-09) as the header; shared with
        # Animation Studio. Text title returns if the file is missing.
        from maya_tools.framework.toolbar.widgets import icon_button
        _banner = QtGui.QPixmap(str(icon_button._ICON_DIR / 'fs_library.png'))
        if not _banner.isNull():
            title = QtWidgets.QLabel()
            title.setPixmap(_banner.scaledToHeight(
                44, QtCore.Qt.SmoothTransformation))
        else:
            title = mindmeld_style.display_label('POSE STUDIO')
        subtitle = mindmeld_style.caps_label('FABRICATOR · V1')
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        title_row.addWidget(self._brand_widget, stretch=1)
        root.addLayout(title_row)
        self._brand_rule = mindmeld_style.horizontal_rule()
        root.addWidget(self._brand_rule)

        # 3-panel splitter
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.characters_panel = CharactersPanel(
            list_rigs=pose_app.list_rigs, enable_recapture_all=True)
        self.grid_panel = PoseGridPanel()
        self.properties_panel = PropertiesPanel()
        self.splitter.addWidget(self.characters_panel)
        self.splitter.addWidget(self.grid_panel)
        self.splitter.addWidget(self.properties_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([220, 600, 280])
        self._restore_splitter_state()
        root.addWidget(self.splitter, stretch=1)

        # Logger inside CollapsibleSection (collapsed by default).
        self.log_section = CollapsibleSection('// LOG')
        self.logger = LoggerWidget()
        log_body_layout = QtWidgets.QVBoxLayout()
        log_body_layout.setContentsMargins(0, 0, 0, 0)
        log_body_layout.addWidget(self.logger)
        self.log_section.set_body_layout(log_body_layout)
        self.log_section.set_expanded(False)
        root.addWidget(self.log_section)

    def connect_signals(self):
        self.characters_panel.character_selected.connect(self._on_character_selected)
        self.grid_panel.pose_selected.connect(self._on_pose_selected)
        self.grid_panel.pose_activated.connect(self._on_pose_activated)
        self.grid_panel.pose_save_requested.connect(self._on_save_requested)
        self.properties_panel.apply_requested.connect(self._on_apply_requested)
        self.properties_panel.delete_requested.connect(self._on_delete_requested)
        self.properties_panel.recapture_requested.connect(
            self._on_recapture_requested)
        self.characters_panel.recapture_all_requested.connect(
            self._on_recapture_all_requested)
        # Left-panel sets are SELECTION HELPERS (Adrian, 2026-07-05):
        # click → select the set's ctrls in the viewport; Apply to
        # + Add Selection Set and right-click delete are handled inside the
        # shared SelectionSetsWidget now — no host wiring needed.
        self.characters_panel.set_selected.connect(self._on_set_selected)

    # -- Population ------------------------------------------------

    def populate_characters(self):
        self.characters_panel.refresh()

    # -- Signal handlers -------------------------------------------

    def _on_character_selected(self, binding: str, rig_label: str):
        self._active_binding = binding
        self._active_rig_label = rig_label
        self.grid_panel.set_rig(rig_label)

    def _on_pose_selected(self, pose_path):
        self.properties_panel.set_pose(pose_path)

    def _on_pose_activated(self, pose_path):
        """Double-click on a pose card → whole-pose apply with the
        Properties panel's settings (Blend / Move World). itemSelectionChanged
        fires before itemDoubleClicked, so the panel's _pose_path is already
        synced when this handler runs — we just re-use the Apply button's
        existing emitter."""
        # Guard: if for any reason the panel doesn't have a pose yet, set it.
        self.properties_panel.set_pose(pose_path)
        self.properties_panel._emit_apply()

    def _on_save_requested(self, rig_label: str):
        try:
            if not self._active_binding:
                self.logger.warning(
                    'No live rig selected. Select a character row with a live '
                    'binding (or pick a ctrl in the viewport on the rig you '
                    'want to save).')
                return
            name, ok = QtWidgets.QInputDialog.getText(
                self, 'Save Pose', 'Pose name:'
            )
            if not ok or not name:
                return
            # Framing dialog FIRST so the user can frame the thumbnail
            # before any disk writes happen.
            from maya_tools.animation.pose_library.framing_dialog import (
                ThumbnailFramingDialog,
            )
            tmp_thumb = ThumbnailFramingDialog.run(parent=self)
            if tmp_thumb is None:
                # User cancelled — no save, no toast.
                return
            # Write the pose JSON; skip the automatic thumbnail capture
            # so the framed temp PNG can take its place. Category is
            # always DEFAULT_CATEGORY in v1 (user-defined categories
            # are v2 work; the field stays in the schema for forward
            # compat).
            path = pose_app.save_pose(name,
                                      category=pose_app.DEFAULT_CATEGORY,
                                      binding=self._active_binding,
                                      _capture_thumbnail=False)
            final_thumb = pose_thumbs.thumb_path_for(path)
            shutil.move(str(tmp_thumb), str(final_thumb))
            self.logger.info(f'Saved pose: {path.name}')
            self.grid_panel.refresh()
        except Exception as exc:
            traceback.print_exc()
            self.logger.error(f'Save failed: {exc}')

    def _on_apply_requested(self, pose_path, blend: float, set_name: str,
                             move_world: bool):
        try:
            if not self._active_binding:
                self.logger.warning(
                    'No live rig selected. Pick a character row with a live '
                    'binding before applying.')
                return
            # Two apply intents since the 2026-07-05 redesign: '' =
            # whole pose (Apply), SELECTED_CONTROLS = viewport
            # selection (Apply to Selected Controls). Set targeting
            # happens BEFORE apply via the selection helpers.
            if set_name == pose_sets.SELECTED_CONTROLS:
                set_filter = pose_sets.builtin_set(set_name)
            else:
                set_filter = None
            # Selected-Controls is an explicit "apply only these" intent —
            # let the filter authorize the world ctrl too, so a user who
            # selected the world ctrl gets it applied regardless of
            # move_world.
            world_follows_filter = (set_name == pose_sets.SELECTED_CONTROLS)
            summary = pose_app.load_pose(
                pose_path, blend=blend, set_filter=set_filter,
                binding=self._active_binding, move_world=move_world,
                world_follows_filter=world_follows_filter,
            )
            effective_move_world = move_world or (
                world_follows_filter and summary['skipped_world'] == 0
            )
            world_note = (
                '' if effective_move_world
                else f', world preserved ({summary["skipped_world"]} skipped)'
            )
            self.logger.info(
                f'Applied {Path(pose_path).stem}: {summary["applied"]} ctrl(s) '
                f'(skipped: {len(summary["skipped_missing"])} missing, '
                f'{summary["skipped_filter"]} filtered{world_note}).'
            )
        except Exception as exc:
            traceback.print_exc()
            self.logger.error(f'Apply failed: {exc}')

    def _on_delete_requested(self, pose_path):
        try:
            confirm = QtWidgets.QMessageBox.question(
                self, 'Delete Pose',
                f'Delete {Path(pose_path).stem}? (file + thumbnail removed)',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if confirm != QtWidgets.QMessageBox.Yes:
                return
            pose_app.delete_pose(pose_path)
            self.logger.info(f'Deleted: {Path(pose_path).name}')
            self.grid_panel.refresh()
            self.properties_panel.set_pose(None)
        except Exception as exc:
            traceback.print_exc()
            self.logger.error(f'Delete failed: {exc}')

    def _on_recapture_requested(self, pose_path):
        try:
            from maya_tools.animation.pose_library.framing_dialog import (
                ThumbnailFramingDialog,
            )
            tmp_thumb = ThumbnailFramingDialog.run(parent=self)
            if tmp_thumb is None:
                return  # User cancelled
            final_thumb = pose_thumbs.thumb_path_for(pose_path)
            shutil.move(str(tmp_thumb), str(final_thumb))
            self.logger.info(f'Recaptured thumbnail: {Path(pose_path).stem}')
            self.grid_panel.refresh()
        except Exception as exc:
            traceback.print_exc()
            self.logger.error(f'Recapture failed: {exc}')

    def _on_recapture_all_requested(self, rig_label: str):
        try:
            confirm = QtWidgets.QMessageBox.question(
                self, 'Recapture All Thumbnails',
                f'Recapture every thumbnail for rig {rig_label!r}? '
                f'This will apply each pose, capture, and restore the '
                f'current pose afterwards.',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if confirm != QtWidgets.QMessageBox.Yes:
                return
            n = pose_thumbs.recapture_for_rig(rig_label)
            self.logger.info(
                f'Recaptured {n} thumbnail(s) for rig {rig_label!r}.'
            )
            self.grid_panel.refresh()
        except Exception as exc:
            traceback.print_exc()
            self.logger.error(f'Batch recapture failed: {exc}')

    def _on_set_selected(self, raw: str):
        """Selection helper: select the clicked set's ctrls on the
        active character in the viewport."""
        try:
            if not self._active_binding:
                self.logger.warning(
                    'Selection helpers need a live rig — pick a live '
                    'character first.')
                return
            n = pose_sets.select_set_controls(raw, self._active_binding)
            if n:
                self.logger.info(f'Selected {n} ctrl(s): {raw}.')
            else:
                self.logger.warning(
                    f'{raw}: no matching ctrls on the active rig — '
                    f'selection cleared.')
        except Exception as exc:
            traceback.print_exc()
            self.logger.error(f'Set selection failed: {exc}')

    # -- Maya selection auto-switch --------------------------------

    def _apply_embedded_trim(self):
        """Popover hosting: the drag bar carries identity and popovers
        carry no log (Adrian, 2026-07-05) — everything else stays,
        the popover IS the full tool."""
        for w in (self._brand_widget, self._brand_rule,
                  self.log_section):
            w.setVisible(False)

    def _install_selection_callback(self):
        """Register a Maya selection-changed callback that auto-switches
        the Characters panel to whichever rig owns the new selection.
        Uses MEventMessage (not scriptJob). Cleanup runs from BOTH
        closeEvent (standalone) AND the destroyed signal — closeEvent
        never fires for popover-hosted children (they die via
        deleteLater with the frame)."""
        try:
            self._sel_callback_id = om.MEventMessage.addEventCallback(
                'SelectionChanged', self._on_maya_selection_changed,
            )
        except Exception as exc:
            self.logger.warning(
                f'Pose Studio: selection auto-switch unavailable — {exc}'
            )
        if self._sel_callback_id is not None:
            cid = self._sel_callback_id

            def _on_destroyed(_obj=None, _cid=cid):
                try:
                    om.MMessage.removeCallback(_cid)
                except Exception:
                    pass
            self.destroyed.connect(_on_destroyed)

    def _on_maya_selection_changed(self, *_):
        # Resolve current selection to a binding; auto-select its row.
        try:
            sel_long = cmds.ls(cmds.ls(sl=True) or [], long=True) or []
            if not sel_long:
                return
            for b in rig_binding.find_live_bindings():
                controls_grp = rig_binding.get_controls_grp(b)
                if not controls_grp:
                    continue
                descendants = set(cmds.listRelatives(
                    controls_grp, allDescendents=True, fullPath=True,
                ) or [])
                if any(s in descendants for s in sel_long):
                    if b != self._active_binding:
                        self.characters_panel.select_binding(b)
                    return
        except Exception:
            # Selection-changed callbacks must not raise (Maya crash risk).
            traceback.print_exc()

    # -- Window state ----------------------------------------------

    def _restore_splitter_state(self):
        try:
            if cmds.optionVar(exists=_OPTION_VAR_SPLITTER):
                state = cmds.optionVar(q=_OPTION_VAR_SPLITTER)
                if state:
                    self.splitter.restoreState(
                        QtCore.QByteArray.fromBase64(state.encode('ascii'))
                    )
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            state = bytes(self.splitter.saveState().toBase64()).decode('ascii')
            cmds.optionVar(stringValue=(_OPTION_VAR_SPLITTER, state))
        except Exception:
            pass
        _save_window_size(self.width(), self.height())
        # Remove the Maya selection callback so it doesn't outlive the window.
        if self._sel_callback_id is not None:
            try:
                om.MMessage.removeCallback(self._sel_callback_id)
            except Exception:
                pass
            self._sel_callback_id = None
        super().closeEvent(event)

    # -- show_window ----------------------------------------------

    @classmethod
    def show_window(cls):
        global _win
        # Reload-proof singleton: the shelf reloads this module each press,
        # so close any prior window by objectName, not the lost _win ref.
        close_windows_by_name(cls.WINDOW_NAME)
        _win = cls()
        _win.show()
        _win.raise_()
        _win.activateWindow()
        return _win


class PoseGridPanel(QtWidgets.QWidget):
    """Center panel — pose grid (icons) for the selected rig."""
    pose_selected = QtCore.Signal(object)         # Path or None
    pose_activated = QtCore.Signal(object)        # Path (double-click → apply)
    pose_save_requested = QtCore.Signal(str)      # rig_label

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rig_label = ''
        self._search_text = ''

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(mindmeld_style.caps_label('// POSES'))

        # Top row: search + Save — matches the Animation Studio layout exactly
        # (search stretches, Save button to its right).
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(4)
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText('search poses...')
        self.search_edit.textChanged.connect(self._on_search_changed)
        top.addWidget(self.search_edit, stretch=1)
        self.save_btn = mindmeld_style.button('+ Save Pose', kind='primary')
        self.save_btn.clicked.connect(self._on_save_clicked)
        top.addWidget(self.save_btn)
        layout.addLayout(top)

        self.list = QtWidgets.QListWidget()
        self.list.setViewMode(QtWidgets.QListWidget.IconMode)
        self.list.setResizeMode(QtWidgets.QListWidget.Adjust)
        self.list.setMovement(QtWidgets.QListWidget.Static)
        self.list.setIconSize(QtCore.QSize(128, 128))
        self.list.setGridSize(QtCore.QSize(160, 180))
        self.list.setSpacing(8)
        self.list.itemSelectionChanged.connect(self._emit_selection)
        self.list.itemDoubleClicked.connect(self._emit_activation)
        layout.addWidget(self.list, stretch=1)

    def set_rig(self, rig_label: str):
        self._rig_label = rig_label
        self.refresh()

    def refresh(self):
        self.list.clear()
        if not self._rig_label:
            return
        needle = self._search_text.strip().lower()
        for pose_path in pose_app.list_poses(self._rig_label):
            name = pose_path.stem.replace('.pose', '')
            if needle and needle not in name.lower():
                continue
            item = QtWidgets.QListWidgetItem(name)
            item.setData(QtCore.Qt.UserRole, pose_path)
            thumb = pose_thumbs.thumb_path_for(pose_path)
            if thumb.is_file():
                item.setIcon(QtGui.QIcon(str(thumb)))
            self.list.addItem(item)

    def _on_search_changed(self, text: str):
        self._search_text = text or ''
        self.refresh()

    def _emit_selection(self):
        item = self.list.currentItem()
        if not item:
            self.pose_selected.emit(None)
            return
        self.pose_selected.emit(item.data(QtCore.Qt.UserRole))

    def _emit_activation(self, item):
        """Double-click → emit pose_activated with the item's path."""
        if item is not None:
            self.pose_activated.emit(item.data(QtCore.Qt.UserRole))

    def _on_save_clicked(self):
        if self._rig_label:
            self.pose_save_requested.emit(self._rig_label)


class PropertiesPanel(QtWidgets.QWidget):
    """Right panel — selected pose details + apply controls."""
    # path, blend, set_name, move_world
    apply_requested = QtCore.Signal(object, float, str, bool)
    delete_requested = QtCore.Signal(object)
    recapture_requested = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pose_path = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(mindmeld_style.caps_label('// PROPERTIES'))

        # Form
        self.form = QtWidgets.QFormLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setSpacing(4)
        self.name_label = QtWidgets.QLabel('—')
        self.source_label = QtWidgets.QLabel('—')
        self.form.addRow(mindmeld_style.field_label('Name:'), self.name_label)
        self.form.addRow(mindmeld_style.field_label('Source rig:'), self.source_label)
        layout.addLayout(self.form)

        layout.addWidget(mindmeld_style.horizontal_rule())

        # Blend slider (DoubleSpinBox 0-1)
        layout.addWidget(mindmeld_style.field_label('Blend:'))
        self.blend = QtWidgets.QDoubleSpinBox()
        self.blend.setRange(0.0, 1.0)
        self.blend.setSingleStep(0.05)
        self.blend.setDecimals(2)
        self.blend.setValue(1.0)
        layout.addWidget(self.blend)

        # No set-filter state here (Adrian, 2026-07-05 redesign): the
        # left panel's sets are selection HELPERS, and targeting is
        # explicit — Apply = whole pose, Apply to Selected Controls =
        # the viewport selection.

        # Move World checkbox — default OFF (character stays planted).
        self.move_world_chk = QtWidgets.QCheckBox('Move World')
        self.move_world_chk.setChecked(False)
        self.move_world_chk.setToolTip(
            'When checked, the saved World ctrl position is applied — the '
            'rig may jump to its captured world position. When unchecked '
            '(default), the World ctrl stays where it is and only the '
            'pose-shape transfers.'
        )
        layout.addWidget(self.move_world_chk)

        layout.addStretch(1)

        # Buttons
        self.apply_btn = mindmeld_style.button('Apply', kind='primary')
        self.apply_btn.setToolTip('Apply the pose to every control.')
        self.apply_btn.clicked.connect(self._emit_apply)
        layout.addWidget(self.apply_btn)

        self.apply_selected_btn = mindmeld_style.button(
            'Apply to Selected Controls', kind='default')
        self.apply_selected_btn.setToolTip(
            'Apply the pose only to the ctrls selected in the '
            'viewport. Click a Selection Set on the left to select a '
            'region/side/kind first.')
        self.apply_selected_btn.clicked.connect(self._emit_apply_selected)
        layout.addWidget(self.apply_selected_btn)

        self.recapture_btn = mindmeld_style.button('Recapture Thumbnail',
                                                    kind='default')
        self.recapture_btn.clicked.connect(self._emit_recapture)
        layout.addWidget(self.recapture_btn)

        self.delete_btn = mindmeld_style.button('Delete', kind='danger')
        self.delete_btn.clicked.connect(self._emit_delete)
        layout.addWidget(self.delete_btn)

        self.set_pose(None)

    def set_pose(self, pose_path):
        self._pose_path = pose_path
        buttons = (self.apply_btn, self.apply_selected_btn,
                   self.recapture_btn, self.delete_btn)
        if pose_path is None:
            self.name_label.setText('—')
            self.source_label.setText('—')
            for btn in buttons:
                btn.setEnabled(False)
            return
        try:
            data = pose_app.read_pose(pose_path)
        except Exception:
            self.name_label.setText('— (read error)')
            return
        self.name_label.setText(data.get('name', '—'))
        self.source_label.setText(data.get('source_rig', '—'))
        for btn in buttons:
            btn.setEnabled(True)

    def _emit_apply(self):
        """Whole-pose apply (set_name '' → no filter)."""
        if self._pose_path is not None:
            self.apply_requested.emit(
                self._pose_path, float(self.blend.value()), '',
                bool(self.move_world_chk.isChecked()),
            )

    def _emit_apply_selected(self):
        """Viewport-selection apply — the old selected_controls set,
        promoted to an explicit button (Adrian, 2026-07-05)."""
        if self._pose_path is not None:
            self.apply_requested.emit(
                self._pose_path, float(self.blend.value()),
                pose_sets.SELECTED_CONTROLS,
                bool(self.move_world_chk.isChecked()),
            )

    def _emit_delete(self):
        if self._pose_path is not None:
            self.delete_requested.emit(self._pose_path)

    def _emit_recapture(self):
        if self._pose_path is not None:
            self.recapture_requested.emit(self._pose_path)


class SetsDialog(QtWidgets.QDialog):
    """Manage user-authored pose sets.

    User selects ctrls in the viewport, names a set, and saves it
    as a component-address list. Saved sets show up in the
    Properties panel's set-filter combo.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Pose Studio — Sets')
        self.setMinimumSize(520, 380)
        mindmeld_style.apply(self)
        self.create_layout()
        self.refresh_list()

    def create_layout(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        root.addWidget(mindmeld_style.caps_label('// USER SETS'))

        self.list = QtWidgets.QListWidget()
        root.addWidget(self.list, stretch=1)

        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(4)
        self.create_btn = mindmeld_style.button(
            '+ Create from Selection', kind='primary'
        )
        self.create_btn.clicked.connect(self._on_create)
        btns.addWidget(self.create_btn)
        self.delete_btn = mindmeld_style.button('Delete', kind='danger')
        self.delete_btn.clicked.connect(self._on_delete)
        btns.addWidget(self.delete_btn)
        btns.addStretch(1)
        root.addLayout(btns)

        hint = mindmeld_style.field_label(
            'Select Fabricator ctrls in the viewport, then click '
            '"Create from Selection".'
        )
        root.addWidget(hint)

    def refresh_list(self):
        self.list.clear()
        for name in pose_sets.list_user_sets():
            self.list.addItem(name)

    def _on_create(self):
        from maya_tools.animation.pose_library.address import ctrl_to_address
        sel = cmds.ls(sl=True, type='transform') or []
        addresses = []
        for ctrl in sel:
            addr = ctrl_to_address(ctrl)
            if addr is not None:
                addresses.append(addr)
        if not addresses:
            QtWidgets.QMessageBox.warning(
                self, 'Create Set',
                'No Fabricator-tagged ctrls in selection.',
            )
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, 'Create Set', f'Set name ({len(addresses)} ctrls):'
        )
        if not ok or not name.strip():
            return
        try:
            pose_sets.save_user_set(name.strip(), addresses)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, 'Create Set', f'Save failed: {exc}'
            )
            return
        self.refresh_list()

    def _on_delete(self):
        item = self.list.currentItem()
        if not item:
            return
        name = item.text()
        confirm = QtWidgets.QMessageBox.question(
            self, 'Delete Set', f'Delete user set {name!r}?',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        pose_sets.delete_user_set(name)
        self.refresh_list()
