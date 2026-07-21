# maya_tools/skinning/autoskin/autoskin_ui.py
"""AutoSkin — UI only. All logic lives in autoskin_app.py.

The whole tool is one button and one checkbox (Adrian's UX contract):
select your bind joints and your mesh, press Bind Skin, and the mesh is
skinned. Everything else on this window exists to tell the truth about
what is happening.

The window wears the Mindmeld 2.0 popover face: frameless shell, the
brand row as the drag bar, an ember X close, and a folder File menu
(Install / Uninstall the backend). The install and uninstall prompts are
the same frameless popover, never a native Maya dialog.

WHY NO THREAD. The engine takes ~2 minutes, and a frozen Maya for two
minutes is not acceptable. But the work either side of it is maya.cmds,
which may only run on the main thread — so this pumps the Qt event loop
from inside the runner's stdout stream instead of moving Maya commands
off-thread. The window stays live, the stage label ticks, and Cancel is
clickable, without ever touching cmds from a worker.
"""
__author__ = "Adrian Melian"

import traceback

import maya.cmds as cmds

from PySide6 import QtCore, QtGui, QtWidgets

from maya_tools.skinning.autoskin import autoskin_app as app
from maya_tools.skinning.autoskin import backend, installer, runner
from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.qt.mindmeld import frameless, mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget


_win = None

# The stages the backend reports, in the order it reports them, with the
# words an artist would use. A progress bar that cannot say what it is
# doing is just a spinner with extra steps.
_STAGES = {
    'exporting':   'Exporting mesh',
    'prep':        'Preparing',
    'coverage':    'Preparing',
    'generating':  'Generating weights',
    'align':       'Matching to your skeleton',
    'bone-map':    'Matching to your skeleton',
    'match':       'Matching to your skeleton',
    'harvest':     'Matching to your skeleton',
    'harvesting':  'Matching to your skeleton',
    'fidelity':    'Matching to your skeleton',
    'generate':    'Building joints',
    'applying':    'Applying weights',
    'done':        'Done',
}
# The bar's stops, in the order they happen. 'Building joints' only occurs
# in Generate Joints mode; a stage the pipeline can emit but the bar does
# not list is a bar that stalls, which is why the contract test asserts
# every emitted stage lands here.
_ORDER = ['Exporting mesh', 'Preparing', 'Generating weights',
          'Matching to your skeleton', 'Building joints', 'Applying weights',
          'Done']


def label_for(stage: str, message: str = '') -> tuple:
    """(label, remaining message) for one backend progress line.

    Module-level and Qt-free ON PURPOSE: this mapping is the contract
    between what the pipeline says and what the artist reads, and it can
    be checked against a real run without building a window.

    The backend announces top-level transitions as `stage: <name>` — the
    name is the MESSAGE there, not the key. Reading the key alone pinned
    the progress bar at zero for an entire run.
    """
    if stage == 'stage' and message:
        stage, message = message, ''
    return _STAGES.get(stage, stage.replace('_', ' ').capitalize()), message


def _confirm_popover(parent, title: str, subtitle: str, lines: list,
                     ok_label: str, ok_kind: str = 'primary',
                     cancel_label: str = 'Not now') -> bool:
    """A frameless Mindmeld confirm popover — the Fabricator popover face
    (drag bar + ember X + panel body + mindmeld buttons), used for the
    install and uninstall prompts instead of a native Maya dialog.

    Returns True on the primary action, False on cancel or the X. The X
    and drag bar come from the frameless shell, so the popover moves and
    closes exactly like a full tool window.
    """
    dlg = QtWidgets.QDialog(parent)
    # adopt() before apply(): the mindmeld='frameless' border rides a
    # dynamic property that must exist when the stylesheet lands.
    frameless.adopt(dlg)
    mindmeld_style.apply(dlg)
    dlg.setModal(True)
    dlg.setMinimumWidth(440)

    root = QtWidgets.QVBoxLayout(dlg)
    root.setContentsMargins(14, 14, 14, 12)
    root.setSpacing(10)

    # Drag bar: title + subtitle + ember X (the popover's grab handle).
    bar = frameless.DragBar()
    brand_row = QtWidgets.QHBoxLayout(bar)
    brand_row.setContentsMargins(0, 0, 0, 0)
    brand_row.setSpacing(10)
    brand_row.addWidget(mindmeld_style.display_label(title))
    if subtitle:
        brand_row.addWidget(mindmeld_style.caps_label(subtitle), 0,
                            QtCore.Qt.AlignmentFlag.AlignBottom)
    brand_row.addStretch()
    brand_row.addWidget(frameless.close_button(dlg), 0,
                        QtCore.Qt.AlignmentFlag.AlignTop)
    root.addWidget(bar)
    root.addWidget(mindmeld_style.horizontal_rule())

    # Body: the message on an iron panel — the 2.0 two-tone signature.
    panel = QtWidgets.QFrame()
    mindmeld_style.tag(panel, 'panel')
    panel_lay = QtWidgets.QVBoxLayout(panel)
    panel_lay.setContentsMargins(12, 12, 12, 12)
    panel_lay.setSpacing(6)
    for line in lines:
        lbl = mindmeld_style.helper_label(line)
        lbl.setWordWrap(True)
        panel_lay.addWidget(lbl)
    root.addWidget(panel)

    # Buttons: cancel (ghost) + the primary action.
    btn_row = QtWidgets.QHBoxLayout()
    btn_row.addStretch(1)
    cancel_btn = mindmeld_style.button(cancel_label, kind='ghost')
    ok_btn = mindmeld_style.button(ok_label, kind=ok_kind)
    cancel_btn.clicked.connect(dlg.reject)
    ok_btn.clicked.connect(dlg.accept)
    btn_row.addWidget(cancel_btn)
    btn_row.addWidget(ok_btn)
    root.addLayout(btn_row)

    ok_btn.setFocus()
    return dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted


class AutoSkinWindow(QtWidgets.QDialog):
    WINDOW_NAME = 'AutoSkinTool'
    WINDOW_TITLE = 'AutoSkin'

    def __init__(self, parent=None):
        super().__init__(parent or get_maya_window())

        # Frameless popover shell (Adrian, 2026-07-20): no OS title bar —
        # the brand row is the drag bar and carries the ember X, the same
        # face as Fabricator. adopt() before apply(), so the
        # mindmeld='frameless' border is set when the stylesheet lands.
        frameless.adopt(self)
        mindmeld_style.apply(self)

        self.setObjectName(self.WINDOW_NAME)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setProperty('saveWindowPref', True)
        self.setMinimumWidth(440)

        self._cancelled = False
        self._running = False
        self._health = None

        self._build_file_menu()
        self.create_layout()
        self.connect_signals()
        self.refresh_state()

    # ─────────────────────────────────────────
    # File menu (folder button pops it — the Fabricator pattern)
    # ─────────────────────────────────────────

    def _build_file_menu(self):
        self._file_menu = QtWidgets.QMenu('File', self)
        # Install acts as install OR reinstall/repair — always offered on a
        # capable machine. Uninstall only when there is something to remove.
        self._install_action = QtGui.QAction('Install Backend...', self)
        self._install_action.triggered.connect(self._on_install)
        self._file_menu.addAction(self._install_action)
        self._file_menu.addSeparator()
        self._uninstall_action = QtGui.QAction('Uninstall Backend...', self)
        self._uninstall_action.triggered.connect(self._on_uninstall)
        self._file_menu.addAction(self._uninstall_action)

    # ─────────────────────────────────────────
    # Layout
    # ─────────────────────────────────────────

    def create_layout(self):
        # Lazy import (matches fs_window): keeps icon_button off the module
        # import path so the offscreen test imports without Maya chrome.
        from maya_tools.framework.toolbar.widgets import icon_button

        main = QtWidgets.QVBoxLayout(self)
        main.setSpacing(10)
        main.setContentsMargins(14, 14, 14, 12)

        # Brand bar — the frameless shell's DragBar: with the OS title bar
        # gone this row is the window's grab handle, and the ember X at its
        # right edge is the close (runs the normal close() path).
        self._brand_bar = frameless.DragBar()
        brand_row = QtWidgets.QHBoxLayout(self._brand_bar)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(10)

        # File button: RigBot's folder icon, InstantPopup on the File menu.
        # Left end — where a menu belongs. Text fallback keeps it usable if
        # the icon ever goes missing.
        self._file_btn = QtWidgets.QToolButton()
        self._file_btn.setObjectName('fab_file_btn')
        _folder = icon_button.load_icon('fs_folder.png')
        if _folder is not None:
            self._file_btn.setIcon(_folder)
            self._file_btn.setIconSize(QtCore.QSize(22, 22))
        else:
            self._file_btn.setText('FILE')
        self._file_btn.setToolTip('File — Install / Uninstall the AutoSkin backend')
        self._file_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self._file_btn.setMenu(self._file_menu)
        brand_row.addWidget(self._file_btn)

        # AutoSkin banner (Adrian, 2026-07-13), scaled to 40px high. The
        # text brand returns if the PNG is ever missing, so a lost asset
        # costs a nice header, not a window. The test asserts on this
        # branch, not on Qt internals.
        _banner = QtGui.QPixmap(str(icon_button._ICON_DIR / 'fs_autoskin.png'))
        self.banner_shown = not _banner.isNull()
        if self.banner_shown:
            title = QtWidgets.QLabel()
            title.setPixmap(_banner.scaledToHeight(
                40, QtCore.Qt.TransformationMode.SmoothTransformation))
        else:
            title = mindmeld_style.brand_label('AutoSkin')
        brand_row.addWidget(title)
        brand_row.addWidget(mindmeld_style.caps_label('powered by SkinTokens'),
                            0, QtCore.Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        brand_row.addWidget(frameless.close_button(self), 0,
                            QtCore.Qt.AlignmentFlag.AlignTop)
        main.addWidget(self._brand_bar)
        main.addWidget(mindmeld_style.horizontal_rule())

        # What it is, and whose engine it runs on. The credit line is where the
        # technical provenance belongs — the tool's NAME says what it does, and
        # SkinTokens is not ours to wear (Adrian, 2026-07-13).
        main.addWidget(mindmeld_style.helper_label(
            'Autoregressive Skeleton & Skin weights '
            '(the successor of UniRig)'))

        # ── The gesture ──────────────────────────────────────────────
        self.hint = mindmeld_style.helper_label(
            'Select your bind joints and your mesh, then press Bind Skin.')
        self.hint.setWordWrap(True)
        main.addWidget(self.hint)

        self.generate_cb = QtWidgets.QCheckBox('Generate Joints')
        self.generate_cb.setToolTip(
            'Build a skeleton too. Select only the mesh — AutoSkin predicts '
            'the joints, then skins to them.')
        main.addWidget(self.generate_cb)

        self.generate_note = mindmeld_style.helper_label(
            'Generated joints are machine-placed and generically named '
            '(bone_00, bone_01…). Great for props, creatures and blockout; '
            'for a production rig, skin to your own skeleton.')
        self.generate_note.setWordWrap(True)
        self.generate_note.setVisible(False)
        main.addWidget(self.generate_note)

        self.bind_btn = mindmeld_style.button('Bind Skin', kind='primary')
        main.addWidget(self.bind_btn)

        # ── Backend install (only when it is missing) ────────────────
        # Kept as an in-window fix for the SETUP state: the auto-prompt on
        # open is the primary path, but a window that reached this state
        # (e.g. an install declined earlier in the session) still offers the
        # fix inline rather than only through the File menu.
        self.install_btn = mindmeld_style.button('Install AutoSkin Backend',
                                                 kind='default')
        self.install_btn.setVisible(False)
        main.addWidget(self.install_btn)

        # ── Progress ─────────────────────────────────────────────────
        self.progress_box = QtWidgets.QWidget()
        pbox = QtWidgets.QVBoxLayout(self.progress_box)
        pbox.setContentsMargins(0, 0, 0, 0)
        pbox.setSpacing(6)

        self.stage_label = mindmeld_style.caps_label('idle')
        pbox.addWidget(self.stage_label)

        bar_row = QtWidgets.QHBoxLayout()
        bar_row.setSpacing(8)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, len(_ORDER))
        self.bar.setTextVisible(False)
        bar_row.addWidget(self.bar, 1)
        self.cancel_btn = mindmeld_style.button('Cancel', kind='danger')
        self.cancel_btn.setFixedWidth(90)
        bar_row.addWidget(self.cancel_btn)
        pbox.addLayout(bar_row)

        self.progress_box.setVisible(False)
        main.addWidget(self.progress_box)

        # ── Log ──────────────────────────────────────────────────────
        self.log_section = CollapsibleSection('LOG')
        self.logger = LoggerWidget()
        log_body = QtWidgets.QVBoxLayout()
        log_body.setContentsMargins(0, 0, 0, 0)
        log_body.addWidget(self.logger)
        self.log_section.set_body_layout(log_body)
        self.log_section.set_expanded(True)
        main.addWidget(self.log_section)

    def connect_signals(self):
        self.bind_btn.clicked.connect(self._on_bind)
        self.install_btn.clicked.connect(self._on_install)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.generate_cb.toggled.connect(self._on_generate_toggled)

    # ─────────────────────────────────────────
    # State
    # ─────────────────────────────────────────

    def refresh_state(self):
        """Ask the backend what it can do, and say so plainly.

        AutoSkin is a DETECTED feature: no GPU is not an error to hide
        behind a disabled button with no explanation — it is a fact the
        artist deserves in words.

        Pure read: this reflects backend state, it never changes it. The
        code-only security auto-update runs once in show_window(), before the
        window is built, so refresh_state has no side effects and stays safe to
        call on every state change.
        """
        try:
            health = backend.health()
        except Exception as exc:
            self._handle_error(exc, brief='Could not check the AutoSkin backend')
            return

        # Stashed for show_window's open check, so opening the tool never
        # probes the GPU twice.
        self._health = health

        ready = health['ready']
        gpu_ok = health['gpu']['ok']
        installed = health['install']['installed']

        self.bind_btn.setEnabled(ready and not self._running)
        self.install_btn.setVisible(gpu_ok and not installed and not self._running)
        self.install_btn.setEnabled(not self._running)
        # File menu: install/repair offered on any capable machine, uninstall
        # only when there is a backend to remove. Both idle while a job runs.
        self._install_action.setEnabled(gpu_ok and not self._running)
        self._uninstall_action.setEnabled(installed and not self._running)

        if ready:
            self.hint.setText(
                'Select your bind joints and your mesh, then press Bind Skin.')
        else:
            # No GPU or not set up: the hint carries the reason in words.
            # When the backend is merely missing, show_window's open prompt
            # drives the fix, so no passive status indicator is needed.
            self.hint.setText(health['reason'])

    def _on_generate_toggled(self, on: bool):
        self.generate_note.setVisible(on)
        self.hint.setText(
            'Select only your mesh, then press Bind Skin.' if on else
            'Select your bind joints and your mesh, then press Bind Skin.')

    # ─────────────────────────────────────────
    # Progress plumbing
    # ─────────────────────────────────────────

    def _begin(self, what: str):
        self._cancelled = False
        self._running = True
        self.progress_box.setVisible(True)
        self.bar.setValue(0)
        self.stage_label.setText(what)
        self.bind_btn.setEnabled(False)
        self.install_btn.setEnabled(False)
        self._install_action.setEnabled(False)
        self._uninstall_action.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        QtWidgets.QApplication.processEvents()

    def _end(self):
        self._running = False
        self.progress_box.setVisible(False)
        self.refresh_state()

    def _pump(self, stage: str, message: str = ''):
        """Called from inside the runner's stdout stream. Updates the
        window AND pumps the event loop, which is what keeps Maya alive
        and Cancel clickable through a two-minute GPU job.

        The stage-to-words mapping lives in label_for() so it can be
        checked against a REAL run without a window (see the stage
        contract in the test suite).
        """
        label, message = label_for(stage, message)
        self.stage_label.setText(label.lower()
                                 + (f' — {message}' if message else ''))
        if label in _ORDER:
            # NEVER let the bar run backwards. The stages are not a straight
            # line: Generate Joints aligns ('Matching…'), builds the skeleton
            # ('Building joints'), then harvests weights — which reports
            # 'Matching…' again and would drag the bar back a step. A bar that
            # retreats reads as a failure to anyone watching.
            self.bar.setValue(max(self.bar.value(), _ORDER.index(label) + 1))
        QtWidgets.QApplication.processEvents()

    def _on_cancel(self):
        self._cancelled = True
        self.cancel_btn.setEnabled(False)
        self.stage_label.setText('cancelling…')
        self.logger.warning('Cancelling…')
        QtWidgets.QApplication.processEvents()

    # ─────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────

    def _on_bind(self):
        generate = self.generate_cb.isChecked()
        self.logger.clear()
        self._begin('starting')
        try:
            result = app.bind_skin(
                generate_joints=generate,
                progress=self._pump,
                cancel_check=lambda: self._cancelled,
            )
        except runner.RunnerCancelled:
            self.logger.warning('Cancelled. Your scene is untouched.')
        except app.AutoSkinError as exc:
            # Expected, user-facing: say it plainly, no traceback theatre.
            self.logger.error(str(exc))
        except Exception as exc:
            self._handle_error(exc, brief='AutoSkin failed')
        else:
            meshes = ', '.join(m.split('|')[-1] for m in result['meshes'])
            if result['generated_joints']:
                self.logger.success(
                    f'Skinned {meshes} to {len(result["joints"])} generated '
                    f'joint(s).')
            else:
                self.logger.success(
                    f'Skinned {meshes} to {len(result["joints"])} joint(s).')
            if result.get('rebound'):
                self.logger.warning(
                    'Replaced the existing skin on: '
                    + ', '.join(m.split('|')[-1] for m in result['rebound'])
                    + '. Ctrl+Z restores it.')
        finally:
            self._end()

    def _prompt_install_or_close(self):
        """The open-time path: the backend is missing, so lead with the
        install offer. Declining closes the window — everything in it is
        dead without the backend, and a window of disabled affordances
        teaches that the tool is broken (Adrian, 2026-07-20)."""
        if not self._on_install():
            self.close()

    def _on_install(self) -> bool:
        """~8.8 GB and about ten minutes. Say so before starting, not
        after — an artist who did not know that has every right to be
        annoyed.

        Returns False when the artist declines the download, True once
        the offer is accepted (whether or not the install then succeeds:
        a failure is reported in the log, and the window stays up so the
        reason can be read and the install retried)."""
        if not _confirm_popover(
                self, 'Install Backend', 'one-time setup · ~8.8 GB',
                ['AutoSkin needs a one-time backend: a Python environment '
                 'and its models, about 8.8 GB.',
                 'It downloads once and takes roughly ten minutes. Maya '
                 'stays usable, but AutoSkin will be busy.'],
                ok_label='Install', ok_kind='primary'):
            return False

        self.logger.clear()
        self._begin('installing backend')
        self.bar.setRange(0, 0)      # indeterminate: it is a download
        try:
            result = installer.install(
                progress=lambda i, n, label: self._pump('installing', label))
        except installer.InstallError as exc:
            self.logger.error(str(exc))
        except Exception as exc:
            self._handle_error(exc, brief='Backend install failed')
        else:
            smoke = result.get('smoke') or {}
            if result['ok']:
                self.logger.success(
                    'AutoSkin backend installed and smoke-tested: '
                    + smoke.get('detail', 'ok'))
            else:
                # Installed, but the engine did not actually run. Say which.
                self.logger.error(
                    'The backend installed, but its smoke test failed: '
                    + smoke.get('detail', 'unknown'))
        finally:
            self.bar.setRange(0, len(_ORDER))
            self._end()
        return True

    def _on_uninstall(self):
        """Remove the backend (~8.8 GB) from disk. Confirmed first — it is
        a big, slow redownload to undo. Only ever offered when a backend is
        actually installed (refresh_state gates the action)."""
        if not _confirm_popover(
                self, 'Uninstall Backend', 'removes ~8.8 GB',
                ['This deletes the AutoSkin backend — the Python '
                 'environment, engine and models — from disk.',
                 'Reinstalling it later is the same ~8.8 GB download. '
                 'Your scenes and skins are untouched.'],
                ok_label='Uninstall', ok_kind='danger'):
            return

        self.logger.clear()
        self._begin('uninstalling backend')
        self.bar.setRange(0, 0)      # indeterminate: a bulk delete
        try:
            result = installer.uninstall()
        except Exception as exc:
            self._handle_error(exc, brief='Uninstall failed')
        else:
            removed = ', '.join(result['removed']) if result['removed'] else 'nothing'
            self.logger.success(f'AutoSkin backend removed ({removed}).')
        finally:
            self.bar.setRange(0, len(_ORDER))
            self._end()

    # ─────────────────────────────────────────
    # Errors
    # ─────────────────────────────────────────

    def _handle_error(self, e: Exception, brief: str = '') -> None:
        traceback.print_exc()
        self.logger.error(f'{brief}: {e}' if brief else str(e))

    @staticmethod
    def show_window():
        global _win
        try:
            _win.close()
            _win.deleteLater()
        except Exception:
            pass

        # Apply a pending CODE-ONLY backend update BEFORE building the window,
        # so it opens already-current instead of flashing an out-of-date
        # prompt. This is how the v1.3 bpy-server loopback SECURITY fix reaches
        # an existing install: a patch the user must opt into is one most users
        # never get. Only the fast git checkout runs unattended; a bump that
        # needs the venv or new models still routes to the gated installer in
        # the window. Best-effort — an offline or stalled update just opens the
        # normal out-of-date view, never blocks the tool.
        update_note = ''
        try:
            if installer.repo_update_available():
                res = installer.apply_repo_update()
                update_note = res.get('detail', '')
        except Exception:
            pass

        _win = AutoSkinWindow()
        if update_note:
            try:
                _win.logger.info(update_note)
            except Exception:
                pass
        # setWindowFlags REPLACES flags — OR FramelessWindowHint back in or
        # adopt()'s frameless shell loses its title-bar removal.
        _win.setWindowFlags(QtCore.Qt.WindowType.Window
                            | QtCore.Qt.WindowType.FramelessWindowHint)
        _win.show()

        # Every open, check the backend: GPU present but backend/models
        # missing → offer the download immediately, and close the window if
        # the artist declines (see _prompt_install_or_close). Reads the health
        # refresh_state already stashed, so the GPU is not probed twice. Via
        # QTimer so the window paints before the modal appears — a dialog over
        # an unpainted window reads as a hang. Lives here, not in
        # refresh_state, so it fires once on open and never during the test
        # suite's window construction.
        health = _win._health
        if health and health['gpu']['ok'] and not health['install']['installed']:
            QtCore.QTimer.singleShot(0, _win._prompt_install_or_close)
        return _win
