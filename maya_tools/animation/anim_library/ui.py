# maya_tools/animation/anim_library/ui.py
"""AnimLibraryWindow — gallery + mode picker + Load button + hover GIF.

Mirrors the structure of pose_library/ui.py. Public entry:
    AnimLibraryWindow.show_window()

Architecture references:
- gallery cards: a custom QFrame with a QStackedWidget toggling between
  a static PNG QLabel and a QMovie-driven GIF QLabel on hover.
- save dialog: simple QDialog with line edit + category combo +
  time-range label.
- load panel: radio mode picker + 3 checkboxes + Load Animation button.
  Double-click on a gallery card also loads with the current settings.
"""
__author__ = "Adrian Melian"

import traceback
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

import maya.cmds as cmds

from maya_tools.animation.anim_library import app as anim_app
from maya_tools.animation.anim_library import gifs as anim_gifs
from maya_tools.animation.pose_library import sets as pose_sets
from maya_tools.animation.characters_panel import CharactersPanel
from maya_tools.animation.anim_library.framing_dialog import (
    ThumbnailFramingDialog,
)
from maya_tools.utils.maya import rig_binding
from maya_tools.utils.maya.gui import get_maya_window, close_windows_by_name
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget


_win = None  # show_window's singleton handle


_OPTION_VAR_W = 'KSAnimLib_window_size_w'
_OPTION_VAR_H = 'KSAnimLib_window_size_h'
_OPTION_VAR_SPLITTER = 'KSAnimLib_splitter_state'
_OPTION_VAR_LAST_RIG = 'KSAnimLib_last_rig'

_DEFAULT_W, _DEFAULT_H = 1100, 720
_MIN_W, _MIN_H = 880, 540

_CARD_SIZE = 128


def _load_window_size() -> tuple:
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


class AnimLibraryWindow(QtWidgets.QWidget):
    """Top-level window for Animation Studio."""

    WINDOW_NAME = 'FSAnimLibrary'

    def __init__(self, parent=None, embedded: bool = False):
        """embedded=True: hosted inside another widget (the RigBot
        Library popover). Sweep-safe objectName — the standalone
        show_window() closes by objectName and must never reap the
        hosted instance — and the brand bar + log trim away (the
        popover drag bar carries identity). The host resets the
        window flags to Widget after construction."""
        super().__init__(parent or get_maya_window())
        self._embedded = embedded
        self.setObjectName(
            self.WINDOW_NAME if not embedded
            else f'{self.WINDOW_NAME}Embedded')
        self.setWindowFlags(QtCore.Qt.WindowType.Window)
        self.setWindowTitle('Animation Studio')
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(*_load_window_size())
        mindmeld_style.apply(self)

        # Active binding tracked at the window level so save/load both
        # use the same target. Set by CharactersPanel selection.
        self._active_binding = ''
        self._active_rig_label = ''

        self.create_layout()
        self.connect_signals()
        self.populate_characters()
        if embedded:
            self._apply_embedded_trim()

    # -- Layout ----------------------------------------------------

    def create_layout(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Brand row (wrapped in a QWidget so embedded mode can hide it)
        self._brand_widget = QtWidgets.QWidget()
        brand_row = QtWidgets.QHBoxLayout(self._brand_widget)
        brand_row.setContentsMargins(0, 0, 0, 0)
        # FS library banner (Adrian, 2026-07-09) as the header; shared with
        # Pose Studio. Text title returns if the file is missing.
        from maya_tools.framework.toolbar.widgets import icon_button
        _banner = QtGui.QPixmap(str(icon_button._ICON_DIR / 'fs_library.png'))
        if not _banner.isNull():
            title = QtWidgets.QLabel()
            title.setPixmap(_banner.scaledToHeight(
                44, QtCore.Qt.TransformationMode.SmoothTransformation))
        else:
            title = mindmeld_style.brand_label('Animation Studio')
        subtitle = mindmeld_style.caps_label('// animations · clips')
        brand_row.addWidget(title)
        brand_row.addWidget(subtitle, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        root.addWidget(self._brand_widget)
        self._brand_rule = mindmeld_style.horizontal_rule()
        root.addWidget(self._brand_rule)

        # 3-panel splitter (Characters / Gallery / Properties)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.characters_panel = CharactersPanel(
            list_rigs=anim_app.list_rigs, enable_recapture_all=False)
        self.grid_panel = AnimGridPanel()
        self.properties_panel = PropertiesPanel()
        self.splitter.addWidget(self.characters_panel)
        self.splitter.addWidget(self.grid_panel)
        self.splitter.addWidget(self.properties_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([220, 600, 280])
        self._restore_splitter_state()
        root.addWidget(self.splitter, 1)

        # Logger (collapsed by default)
        self.log_section = CollapsibleSection('// LOG')
        self.logger = LoggerWidget()
        log_body = QtWidgets.QVBoxLayout()
        log_body.setContentsMargins(0, 0, 0, 0)
        log_body.addWidget(self.logger)
        self.log_section.set_body_layout(log_body)
        self.log_section.set_expanded(False)
        root.addWidget(self.log_section)

    def _apply_embedded_trim(self):
        """Popover hosting: the drag bar carries identity and popovers
        carry no log (Adrian, 2026-07-05) — everything else stays,
        the popover IS the full tool."""
        for w in (self._brand_widget, self._brand_rule,
                  self.log_section):
            w.setVisible(False)

    def connect_signals(self):
        self.characters_panel.character_selected.connect(self._on_character_selected)
        self.characters_panel.set_selected.connect(self._on_set_selected)
        # + Add Selection Set and right-click delete are handled inside the
        # shared SelectionSetsWidget now — no host wiring needed.
        self.grid_panel.anim_selected.connect(self._on_anim_selected)
        self.grid_panel.anim_activated.connect(self._on_anim_activated)
        self.grid_panel.anim_save_requested.connect(self._on_save_requested)
        self.grid_panel.anim_delete_requested.connect(self._on_delete_requested)
        self.properties_panel.load_requested.connect(self._on_load_requested)
        self.properties_panel.delete_requested.connect(self._on_delete_requested)
        self.properties_panel.recapture_requested.connect(self._on_recapture_requested)

    # -- Public window management ----------------------------------

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

    def showEvent(self, event):
        super().showEvent(event)
        # populate_characters() runs during __init__, BEFORE the window is
        # shown — that fires AnimGridPanel.refresh() while gallery_widget
        # width is still 0, falling through to the cols=4 fallback. Re-run
        # the grid refresh once on the first show so the column count
        # matches the actual rendered window width.
        if not getattr(self, '_first_show_done', False):
            self._first_show_done = True
            QtCore.QTimer.singleShot(0, self.grid_panel.refresh)

    # -- Population ------------------------------------------------

    def populate_characters(self):
        # Read the persisted last-rig BEFORE refresh(): refresh() auto-selects
        # index 0 and emits character_selected, whose handler overwrites this
        # optionVar — reading it after would get the just-clobbered value.
        last = ''
        try:
            if cmds.optionVar(exists=_OPTION_VAR_LAST_RIG):
                last = cmds.optionVar(q=_OPTION_VAR_LAST_RIG) or ''
        except Exception:
            pass
        self.characters_panel.refresh()
        if last:
            try:
                self.characters_panel.select_rig_label(last)
            except Exception:
                pass
        # If no LIVE binding ended up selected (persisted rig missing/offline),
        # default to the first live binding — a live rig is the useful default.
        if not self.characters_panel._current_entry()[0]:
            try:
                bindings = rig_binding.find_live_bindings() or []
                if bindings:
                    self.characters_panel.select_binding(bindings[0])
            except Exception:
                pass
        self.logger.info('Animation Studio ready.')

    # -- Signal handlers -------------------------------------------

    def _on_character_selected(self, binding: str, rig_label: str):
        self._active_binding = binding
        self._active_rig_label = rig_label
        self.grid_panel.set_rig(rig_label)
        self.properties_panel.set_anim(None)
        try:
            cmds.optionVar(stringValue=(_OPTION_VAR_LAST_RIG, rig_label))
        except Exception:
            pass

    def _on_anim_selected(self, path):
        self.properties_panel.set_anim(path)

    def _on_anim_activated(self, path):
        """Double-click on a card — load with current Properties settings."""
        self.properties_panel.set_anim(path)
        self.properties_panel._emit_load()

    def _on_save_requested(self, rig_label: str):
        """Save flow: resolve binding → save dialog → write JSON →
        framing dialog → capture GIF + PNG."""
        binding = self._active_binding   # the Characters-panel selection is the
        if not binding:                  # target (not a viewport-selection guess)
            self.logger.warning(
                'No live rig selected — pick a live character first.')
            return

        try:
            time_range = anim_app.resolve_time_range()
        except Exception as exc:
            self._handle_error(exc, 'Could not resolve time range.')
            return

        fps = anim_app._current_fps()
        dialog = _SaveAnimDialog(time_range=time_range, fps=fps, parent=self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        name = dialog.name_value()
        category = dialog.category_value()
        if not name:
            self._handle_error(RuntimeError('Name is empty.'))
            return

        try:
            json_path = anim_app.save_anim(
                name, category=category, binding=binding,
                time_range=time_range,
            )
            self.logger.success(f'Wrote {json_path.name}.')
        except Exception as exc:
            self._handle_error(exc)
            return

        root_joint = rig_binding.get_root_joint(binding) if binding else ''
        gif_path = json_path.parent / f'{name}.gif'
        png_path = json_path.parent / f'{name}.png'

        def _capture_callback(panel_name):
            gif, png = anim_gifs.playblast_and_gif(
                panel=panel_name,
                time_range=time_range,
                root_joint=root_joint,
                out_gif=gif_path, out_png=png_path,
            )
            if gif:
                self.logger.success(f'Wrote {gif.name}.')
            if png:
                self.logger.success(f'Wrote {png.name}.')
            return gif

        result = ThumbnailFramingDialog.run(parent=self,
                                            capture_callback=_capture_callback)
        if result is None:
            try:
                json_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.logger.warning('Save cancelled — JSON removed.')
        self.grid_panel.refresh()

    def _on_set_selected(self, raw):
        """Selection helper: select the clicked set's ctrls on the active
        character in the viewport (mirrors Pose Studio)."""
        try:
            binding = self._active_binding
            if not binding:
                self.logger.warning(
                    'Selection helpers need a live rig — pick a live '
                    'character first.')
                return
            n = pose_sets.select_set_controls(raw, binding)
            if n:
                self.logger.info(f'Selected {n} ctrl(s): {raw}.')
            else:
                self.logger.warning(
                    f'{raw}: no matching ctrls on the active rig — '
                    f'selection cleared.')
        except Exception as exc:
            traceback.print_exc()
            self.logger.error(f'Set selection failed: {exc}')

    def _on_load_requested(self, path, mode, start_anchor, end_anchor,
                            root_motion, set_name):
        try:
            # Apply to Selected Controls feeds the viewport-selection predicate;
            # a whole-rig load ('') passes no filter (all channels apply).
            set_filter = (pose_sets.builtin_set(pose_sets.SELECTED_CONTROLS)
                          if set_name == pose_sets.SELECTED_CONTROLS else None)
            summary = anim_app.load_anim(
                path, mode=mode,
                apply_start_anchor=start_anchor,
                apply_end_anchor=end_anchor,
                apply_root_motion=root_motion,
                set_filter=set_filter,
                binding=self._active_binding,   # the panel is the target truth
            )
            # Path stem ends in '.anim' because the filename is
            # '<name>.anim.json' — strip it for the log line.
            display = Path(path).stem
            if display.endswith('.anim'):
                display = display[:-5]
            self.logger.success(
                f'Loaded {display} ({mode}): applied '
                f'{summary["applied_channels"]} channel(s); '
                f'{summary.get("skipped_filter", 0)} filtered by selection; '
                f'{len(summary["skipped_missing"])} missing.'
            )
        except Exception as exc:
            self._handle_error(exc)

    def _on_delete_requested(self, path):
        confirm = QtWidgets.QMessageBox.question(
            self, 'Delete',
            f'Delete {Path(path).stem}?',
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        # Release the QMovie file handle on the matching card before
        # delete_anim tries to unlink the sibling .gif. Windows holds
        # an open handle on a QMovie's source and the unlink raises
        # PermissionError otherwise.
        for c in self.grid_panel._cards:
            if c.path == path:
                c.release_movie()
                break
        try:
            anim_app.delete_anim(path)
            self.logger.info(f'Deleted {Path(path).name}.')
            self.grid_panel.refresh()
            self.properties_panel.set_anim(None)
        except Exception as exc:
            self._handle_error(exc)

    def _on_recapture_requested(self, path):
        """Re-frame + re-render the static PNG and hover GIF for an
        existing clip. Same callback shape as the Save flow's framing
        dialog — only the destination paths differ (alongside the
        existing .anim.json)."""
        binding = self._active_binding   # the Characters-panel selection is the
        if not binding:                  # target (not a viewport-selection guess)
            self.logger.warning(
                'No live rig selected — pick a live character first.')
            return
        try:
            data = anim_app.read_anim(path)
        except Exception as exc:
            self._handle_error(exc, 'Could not read clip JSON.')
            return
        tr = data.get('time_range') or {}
        try:
            time_range = (float(tr['start']), float(tr['end']))
        except Exception as exc:
            self._handle_error(exc, 'Clip JSON missing time_range.')
            return

        root_joint = rig_binding.get_root_joint(binding) if binding else ''
        stem = Path(path).stem
        if stem.endswith('.anim'):
            stem = stem[:-5]
        gif_path = Path(path).parent / f'{stem}.gif'
        png_path = Path(path).parent / f'{stem}.png'

        def _capture_callback(panel_name):
            gif, png = anim_gifs.playblast_and_gif(
                panel=panel_name,
                time_range=time_range,
                root_joint=root_joint,
                out_gif=gif_path, out_png=png_path,
            )
            if gif:
                self.logger.success(f'Re-captured {gif.name}.')
            if png:
                self.logger.success(f'Re-captured {png.name}.')
            return gif

        ThumbnailFramingDialog.run(parent=self,
                                    capture_callback=_capture_callback)
        self.grid_panel.refresh()

    # -- Splitter / window state ----------------------------------

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
        # Persist splitter state + window size before teardown.
        try:
            state = bytes(self.splitter.saveState().toBase64()).decode('ascii')
            cmds.optionVar(stringValue=(_OPTION_VAR_SPLITTER, state))
        except Exception:
            pass
        _save_window_size(self.width(), self.height())
        super().closeEvent(event)

    # -- Error helper ----------------------------------------------

    def _handle_error(self, exc, brief: str = ''):
        traceback.print_exc()
        self.logger.error(brief or str(exc))


# --- _AnimCard ---------------------------------------------------------------

class _AnimCard(QtWidgets.QFrame):
    """Gallery card with name + static PNG + hover-driven GIF."""

    selected = QtCore.Signal(object)       # emits Path of this card
    activated = QtCore.Signal(object)      # double-click -> emits Path
    delete_requested = QtCore.Signal(object)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setFixedSize(_CARD_SIZE + 8, _CARD_SIZE + 32)
        self.setProperty('mindmeld', 'card')
        mindmeld_style.restyle(self)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Stacked: static PNG (default), QMovie GIF (on hover)
        self._stack = QtWidgets.QStackedWidget()
        self._stack.setFixedSize(_CARD_SIZE, _CARD_SIZE)

        self._static_label = QtWidgets.QLabel()
        self._static_label.setFixedSize(_CARD_SIZE, _CARD_SIZE)
        self._static_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._static_label.setScaledContents(True)
        self._stack.addWidget(self._static_label)

        self._gif_label = QtWidgets.QLabel()
        self._gif_label.setFixedSize(_CARD_SIZE, _CARD_SIZE)
        self._gif_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._gif_label.setScaledContents(True)
        self._stack.addWidget(self._gif_label)

        self._movie = None
        self._load_thumbs()

        layout.addWidget(self._stack, 0, QtCore.Qt.AlignmentFlag.AlignCenter)

        # Strip the .anim suffix from the displayed name. The path's
        # stem is "<name>.anim" because the full file ends in
        # ".anim.json"; .stem only chops the final .json.
        display_name = self.path.stem
        if display_name.endswith('.anim'):
            display_name = display_name[:-5]
        name = QtWidgets.QLabel(display_name)
        name.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        name.setWordWrap(True)
        layout.addWidget(name)

    def _load_thumbs(self):
        # Strip ".anim" to match the save-side sibling pattern:
        # <stem>.anim.json + <stem>.png + <stem>.gif
        stem = self.path.stem
        if stem.endswith('.anim'):
            stem = stem[:-5]

        png = self.path.parent / f'{stem}.png'
        if png.is_file():
            pix = QtGui.QPixmap(str(png))
            self._static_label.setPixmap(pix)

        gif = self.path.parent / f'{stem}.gif'
        if gif.is_file():
            self._movie = QtGui.QMovie(str(gif))
            self._movie.setSpeed(100)
            self._gif_label.setMovie(self._movie)

    def set_selected(self, on: bool):
        self.setProperty('mindmeld', 'card_selected' if on else 'card')
        mindmeld_style.restyle(self)

    def release_movie(self):
        """Stop + force-destroy the QMovie so Windows releases the GIF
        file handle.

        QMovie keeps the source file open via QImageReader for its
        lifetime. On Windows the OS refuses to unlink an open file, so
        callers about to delete the sibling .gif must release the movie
        first.

        `deleteLater()` defers destruction to the next event-loop tick,
        which doesn't help when the caller is about to unlink the file
        synchronously. shiboken6.delete tears down the C++ object NOW,
        immediately releasing the file handle.
        """
        if self._movie is not None:
            self._movie.stop()
            self._gif_label.setMovie(None)
            try:
                from shiboken6 import delete as _shiboken_delete
                _shiboken_delete(self._movie)
            except Exception:
                # Fallback: best-effort deferred deletion if shiboken
                # isn't available for some reason.
                self._movie.deleteLater()
            self._movie = None

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._movie is not None:
            self._stack.setCurrentIndex(1)
            self._movie.start()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._movie is not None:
            self._movie.stop()
            self._movie.jumpToFrame(0)
        self._stack.setCurrentIndex(0)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.selected.emit(self.path)
        elif event.button() == QtCore.Qt.MouseButton.RightButton:
            menu = QtWidgets.QMenu(self)
            act_load = menu.addAction('Load')
            act_del = menu.addAction('Delete')
            chosen = menu.exec(event.globalPosition().toPoint())
            if chosen == act_load:
                self.activated.emit(self.path)
            elif chosen == act_del:
                self.delete_requested.emit(self.path)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.activated.emit(self.path)
        super().mouseDoubleClickEvent(event)


# --- _SaveAnimDialog ---------------------------------------------------------

class _SaveAnimDialog(QtWidgets.QDialog):
    """Name + category + time-range display. OK -> calls back into the window."""

    def __init__(self, time_range, fps, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Save Animation')
        self.setMinimumWidth(360)
        mindmeld_style.apply(self)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(6)

        form = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText('e.g. run_pass')
        form.addRow(mindmeld_style.field_label('Name'), self.name_edit)
        self.cat_combo = QtWidgets.QComboBox()
        self.cat_combo.addItems(anim_app.CATEGORIES)
        form.addRow(mindmeld_style.field_label('Category'), self.cat_combo)
        layout.addLayout(form)

        start, end = time_range
        length = end - start
        info = QtWidgets.QLabel(
            f'Saving frames {start:g}–{end:g} '
            f'({length:g} frames, {fps:g} fps)'
        )
        layout.addWidget(info)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
            | QtWidgets.QDialogButtonBox.StandardButton.Save
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def name_value(self):
        return self.name_edit.text().strip()

    def category_value(self):
        return self.cat_combo.currentText()


# --- CharactersPanel ---------------------------------------------------------

class AnimGridPanel(QtWidgets.QWidget):
    """Center panel — search bar + Save button + gallery of anim cards.

    Emits:
      anim_selected(path)      — Path or None on selection change.
      anim_activated(path)     — Path on double-click.
      anim_save_requested(rig) — current rig_label when Save is clicked.
      anim_delete_requested(path) — Path when a card's right-click → Delete fires.
    """
    anim_selected = QtCore.Signal(object)
    anim_activated = QtCore.Signal(object)
    anim_save_requested = QtCore.Signal(str)
    anim_delete_requested = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rig_label = ''
        self._search_text = ''
        self._cards = []
        self._selected_path = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(mindmeld_style.caps_label('// ANIMS'))

        # Top row: search + Save
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(4)
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText('search anims...')
        self.search_edit.textChanged.connect(self._on_search_changed)
        top.addWidget(self.search_edit, stretch=1)
        self.save_btn = mindmeld_style.button('+ Save Anim', kind='primary')
        self.save_btn.clicked.connect(self._on_save_clicked)
        top.addWidget(self.save_btn)
        layout.addLayout(top)

        # Gallery (scroll area + grid)
        self.gallery_scroll = QtWidgets.QScrollArea()
        self.gallery_scroll.setWidgetResizable(True)
        self.gallery_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.gallery_widget = QtWidgets.QWidget()
        self.gallery_layout = QtWidgets.QGridLayout(self.gallery_widget)
        self.gallery_layout.setContentsMargins(4, 4, 4, 4)
        self.gallery_layout.setSpacing(8)
        self.gallery_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.gallery_scroll.setWidget(self.gallery_widget)
        layout.addWidget(self.gallery_scroll, stretch=1)

    def set_rig(self, rig_label: str) -> None:
        self._rig_label = rig_label or ''
        self.refresh()

    def current_rig_label(self) -> str:
        return self._rig_label

    def current_path(self) -> Path | None:
        return self._selected_path

    def refresh(self) -> None:
        # Clear existing cards — call release_movie() so each card's
        # QMovie is force-destroyed before deleteLater. deleteLater
        # alone is async and leaves the QMovie alive long enough to
        # keep its GIF file locked on Windows; over multiple refreshes
        # stale movies accumulate and eventually a delete_anim hits a
        # file one of them is still holding open.
        while self.gallery_layout.count():
            item = self.gallery_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                release = getattr(w, 'release_movie', None)
                if callable(release):
                    try:
                        release()
                    except Exception:
                        pass
                w.deleteLater()
        self._cards = []
        self._selected_path = None
        self.anim_selected.emit(None)

        if not self._rig_label:
            return
        anims = anim_app.list_anims(self._rig_label)
        needle = self._search_text.strip().lower()
        if needle:
            anims = [p for p in anims if needle in p.stem.lower()]

        cols = max(1, (self.gallery_widget.width() // (_CARD_SIZE + 16)) or 4)
        for i, path in enumerate(anims):
            card = _AnimCard(path, parent=self.gallery_widget)
            card.selected.connect(self._on_card_selected)
            card.activated.connect(self._on_card_activated)
            card.delete_requested.connect(self.anim_delete_requested)
            r, c = divmod(i, cols)
            self.gallery_layout.addWidget(card, r, c)
            self._cards.append(card)

    def select_path(self, path: Path) -> None:
        """Highlight the card whose path matches. Silent no-op if no match."""
        for c in self._cards:
            on = (c.path == path)
            c.set_selected(on)
            if on:
                self._selected_path = c.path

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text or ''
        self.refresh()

    def _on_save_clicked(self) -> None:
        if self._rig_label:
            self.anim_save_requested.emit(self._rig_label)

    def _on_card_selected(self, path: Path) -> None:
        self._selected_path = path
        for c in self._cards:
            c.set_selected(c.path == path)
        self.anim_selected.emit(path)

    def _on_card_activated(self, path: Path) -> None:
        self._selected_path = path
        self.anim_activated.emit(path)


# --- PropertiesPanel ---------------------------------------------------------

class PropertiesPanel(QtWidgets.QWidget):
    """Right panel — selected clip details + load/delete/recapture controls.

    Emits:
      load_requested(path, mode_str, start_anchor, end_anchor, root_motion, set_name)
      delete_requested(path)
      recapture_requested(path)
    """
    # path, mode, start_anchor, end_anchor, root_motion, set_name
    # (set_name '' = whole rig; pose_sets.SELECTED_CONTROLS = viewport selection)
    load_requested = QtCore.Signal(object, str, bool, bool, bool, str)
    delete_requested = QtCore.Signal(object)
    recapture_requested = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._anim_path = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(mindmeld_style.caps_label('// PROPERTIES'))

        # Read-only metadata form.
        self.form = QtWidgets.QFormLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setSpacing(4)
        self.name_label = QtWidgets.QLabel('—')
        self.source_label = QtWidgets.QLabel('—')
        self.range_label = QtWidgets.QLabel('—')
        self.frames_label = QtWidgets.QLabel('—')
        self.fps_label = QtWidgets.QLabel('—')
        self.form.addRow(mindmeld_style.field_label('Name:'),       self.name_label)
        self.form.addRow(mindmeld_style.field_label('Source rig:'), self.source_label)
        self.form.addRow(mindmeld_style.field_label('Time range:'), self.range_label)
        self.form.addRow(mindmeld_style.field_label('Frames:'),     self.frames_label)
        self.form.addRow(mindmeld_style.field_label('FPS:'),        self.fps_label)
        layout.addLayout(self.form)

        layout.addWidget(mindmeld_style.horizontal_rule())

        # Mode picker
        layout.addWidget(mindmeld_style.field_label('Mode:'))
        self.mode_group = QtWidgets.QButtonGroup(self)
        self.mode_place   = QtWidgets.QRadioButton('Place')
        self.mode_insert  = QtWidgets.QRadioButton('Insert')
        self.mode_replace = QtWidgets.QRadioButton('Replace')
        self.mode_place.setChecked(True)
        for rb in (self.mode_place, self.mode_insert, self.mode_replace):
            self.mode_group.addButton(rb)
            layout.addWidget(rb)

        # Apply-* checkboxes — all default on.
        self.cb_start_anchor = QtWidgets.QCheckBox('Apply start anchor')
        self.cb_end_anchor   = QtWidgets.QCheckBox('Apply end anchor')
        self.cb_root_motion  = QtWidgets.QCheckBox('Apply root motion')
        for cb in (self.cb_start_anchor, self.cb_end_anchor, self.cb_root_motion):
            cb.setChecked(True)
            layout.addWidget(cb)

        layout.addStretch(1)

        # Action buttons
        self.load_btn = mindmeld_style.button('Load Animation', kind='primary')
        self.load_btn.setMinimumHeight(30)
        self.load_btn.clicked.connect(self._emit_load)
        layout.addWidget(self.load_btn)

        self.load_selected_btn = mindmeld_style.button(
            'Apply to Selected Controls', kind='default')
        self.load_selected_btn.setToolTip(
            'Load the animation onto only the ctrls selected in the '
            'viewport. Click a Selection Set on the left to select a '
            'region/side/kind first.')
        self.load_selected_btn.clicked.connect(self._emit_load_selected)
        layout.addWidget(self.load_selected_btn)

        self.recapture_btn = mindmeld_style.button('Recapture Thumb + GIF',
                                                    kind='default')
        self.recapture_btn.clicked.connect(self._emit_recapture)
        layout.addWidget(self.recapture_btn)

        self.delete_btn = mindmeld_style.button('Delete', kind='danger')
        self.delete_btn.clicked.connect(self._emit_delete)
        layout.addWidget(self.delete_btn)

        self.set_anim(None)

    def set_anim(self, path: Path | None) -> None:
        """Populate the form + enable buttons for the given clip path.
        Pass None to clear back to the empty / disabled state."""
        self._anim_path = path
        if path is None:
            self.name_label.setText('—')
            self.source_label.setText('—')
            self.range_label.setText('—')
            self.frames_label.setText('—')
            self.fps_label.setText('—')
            for btn in (self.load_btn, self.load_selected_btn,
                        self.recapture_btn, self.delete_btn):
                btn.setEnabled(False)
            return
        try:
            data = anim_app.read_anim(path)
        except Exception:
            # Read failure — show what we can, disable actions.
            self.name_label.setText(Path(path).stem.replace('.anim', '') +
                                     ' (read error)')
            self.source_label.setText('—')
            self.range_label.setText('—')
            self.frames_label.setText('—')
            self.fps_label.setText('—')
            for btn in (self.load_btn, self.load_selected_btn,
                        self.recapture_btn, self.delete_btn):
                btn.setEnabled(False)
            return
        name = Path(path).stem
        if name.endswith('.anim'):
            name = name[:-5]
        tr = data.get('time_range') or {}
        start = tr.get('start', '—')
        end   = tr.get('end',   '—')
        length = tr.get('length', '—')
        fps = data.get('source_fps', '—')
        self.name_label.setText(name)
        self.source_label.setText(str(data.get('source_rig', '—')))
        self.range_label.setText(f'{start}–{end}')
        self.frames_label.setText(str(length))
        self.fps_label.setText(str(fps))
        for btn in (self.load_btn, self.load_selected_btn,
                    self.recapture_btn, self.delete_btn):
            btn.setEnabled(True)

    def selected_mode(self) -> str:
        if self.mode_place.isChecked():
            return anim_app.MODE_PLACE
        if self.mode_insert.isChecked():
            return anim_app.MODE_INSERT
        return anim_app.MODE_REPLACE

    def _emit_load(self) -> None:
        self._emit(set_name='')                              # whole rig

    def _emit_load_selected(self) -> None:
        self._emit(set_name=pose_sets.SELECTED_CONTROLS)     # viewport selection

    def _emit(self, set_name: str) -> None:
        if self._anim_path is None:
            return
        self.load_requested.emit(
            self._anim_path,
            self.selected_mode(),
            bool(self.cb_start_anchor.isChecked()),
            bool(self.cb_end_anchor.isChecked()),
            bool(self.cb_root_motion.isChecked()),
            set_name,
        )

    def _emit_delete(self) -> None:
        if self._anim_path is not None:
            self.delete_requested.emit(self._anim_path)

    def _emit_recapture(self) -> None:
        if self._anim_path is not None:
            self.recapture_requested.emit(self._anim_path)
