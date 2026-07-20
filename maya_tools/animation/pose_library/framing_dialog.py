# maya_tools/animation/pose_library/framing_dialog.py
"""Thumbnail framing dialog — Maya viewport embedded in a QDialog.

The user tumbles/dollies the embedded viewport like any Maya panel,
clicks 'Capture', and the playblast is taken from THAT panel (not the
main scene viewport). The captured PNG goes to a temp file; the caller
moves it to the final pose-sibling location.

Embedding pattern: build a hidden Maya window scaffold containing a
paneLayout + modelPanel, then `wrapInstance` the modelPanel's Qt widget
pointer and reparent it into the dialog's layout. The Maya scaffold
window is never shown but exists to host the panel for its lifetime;
it gets cleaned up in `_cleanup_maya`.

Public API:
- ThumbnailFramingDialog(parent=None).run() -> Path | None
"""
__author__ = "Adrian Melian"

import tempfile
from pathlib import Path

from PySide6 import QtWidgets, QtCore

import maya.cmds as cmds
import maya.OpenMayaUI as omui
from shiboken6 import wrapInstance

from maya_tools.animation.pose_library import thumbnails as pose_thumbs
from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.qt.mindmeld import mindmeld_style


_VIEWPORT_MIN_W = 380
_VIEWPORT_MIN_H = 380


def _suspend_mtoa_viewport_callbacks() -> list:
    """Kill mtoa's modelEditorChanged scriptJob so its iconbar hook
    doesn't fire on our embedded modelPanel.

    Returns the list of killed scriptJob ids (caller passes to
    `_restore_mtoa_viewport_callbacks` on cleanup). Empty list means
    mtoa wasn't loaded or had no jobs registered — nothing to restore.

    Root cause: `mtoa.viewport.gArnoldViewportRenderContol` registers
    a scriptJob that walks every modelPanel's parent hierarchy to
    attach an Arnold iconbar. Our embedded panel lives in a hidden
    Qt window outside $gMainPane, so the lookup hits an empty `form`
    path and emits two noisy errors. Suspending mtoa for the dialog's
    lifetime sidesteps the bug at the source.
    """
    killed = []
    try:
        import mtoa.viewport as _mtoa_vp
    except ImportError:
        return killed
    ctl = getattr(_mtoa_vp, 'gArnoldViewportRenderContol', None)
    if ctl is None:
        return killed
    for job_id in list(getattr(ctl, 'script_jobs', [])):
        try:
            if cmds.scriptJob(exists=job_id):
                cmds.scriptJob(kill=job_id, force=True)
                killed.append(job_id)
        except Exception:
            pass
    # Empty the list so mtoa's setup_callbacks() on restore registers
    # fresh jobs rather than trying to skip "existing" stale ids.
    try:
        ctl.script_jobs = []
    except Exception:
        pass
    return killed


def _restore_mtoa_viewport_callbacks(killed_jobs: list) -> None:
    """Re-register mtoa's scriptJobs (counterpart to suspend).

    No-op if nothing was killed. Calls mtoa's own `setup_callbacks` so
    the registration matches whatever mtoa expects internally.
    """
    if not killed_jobs:
        return
    try:
        import mtoa.viewport as _mtoa_vp
    except ImportError:
        return
    ctl = getattr(_mtoa_vp, 'gArnoldViewportRenderContol', None)
    if ctl is None:
        return
    try:
        ctl.setup_callbacks()
    except Exception:
        pass


class ThumbnailFramingDialog(QtWidgets.QDialog):
    """Modal dialog embedding a Maya viewport for thumbnail framing."""

    def __init__(self, parent=None, capture_callback=None):
        super().__init__(parent or get_maya_window())
        self.setWindowTitle('Frame Thumbnail')
        self.setMinimumSize(_VIEWPORT_MIN_W + 24, _VIEWPORT_MIN_H + 120)
        mindmeld_style.apply(self)

        # State
        self._captured_path = None
        self._maya_window_name = ''
        self._panel_name = ''
        self._mtoa_killed_jobs = []
        # Injected at construction; called with (panel_name) at Capture click.
        # When None, falls back to pose_thumbs.capture (legacy pose-library
        # behavior — a single-frame PNG to a temp path).
        self._capture_callback = capture_callback

        # Suspend mtoa's modelEditorChanged scriptJob BEFORE creating the
        # embedded modelPanel — it hooks panel create/destroy to attach an
        # Arnold iconbar, and when the panel lives outside $gMainPane
        # (hidden Qt window) the lookup walks an empty path: produces
        # "Object '|' not found" on create + "cleanupModelPanelBar
        # ||<panel>" syntax error on destroy. Restored in _cleanup_maya.
        self._mtoa_killed_jobs = _suspend_mtoa_viewport_callbacks()

        self.create_layout()
        self._frame_default()

    # ── Layout ─────────────────────────────────────────────────

    def create_layout(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addWidget(mindmeld_style.caps_label('// FRAME THUMBNAIL'))
        root.addWidget(mindmeld_style.field_label(
            'Tumble (alt+drag), dolly (alt+RMB), pan (alt+MMB) to frame '
            'the pose, then Capture.'
        ))

        # Build a hidden Maya window scaffold and pull its modelPanel
        # widget out via wrapInstance.
        self._maya_window_name = cmds.window('ksPoseLibThumbFrameHelper',
                                              visible=False)
        cmds.paneLayout()
        self._panel_name = cmds.modelPanel(menuBarVisible=False,
                                            label='Frame')
        # Configure the embedded viewport: show meshes + ctrls, hide grid,
        # smooth shaded, default lights. (Keeps the framing view free of
        # the user's chosen display toggles in their main viewport.)
        model_editor = cmds.modelPanel(self._panel_name, q=True,
                                        modelEditor=True)
        cmds.modelEditor(
            model_editor, edit=True,
            allObjects=False,
            polymeshes=True,
            nurbsCurves=False,
            displayLights='default',
            shadows=False,
            displayAppearance='smoothShaded',
            displayTextures=True,
            grid=False,
            headsUpDisplay=False,
        )

        ptr = omui.MQtUtil.findControl(self._panel_name)
        if ptr is None:
            raise RuntimeError(
                'Pose Studio: could not embed Maya viewport for thumbnail '
                'framing — MQtUtil.findControl returned None.'
            )
        panel_widget = wrapInstance(int(ptr), QtWidgets.QWidget)
        panel_widget.setMinimumSize(_VIEWPORT_MIN_W, _VIEWPORT_MIN_H)
        root.addWidget(panel_widget, stretch=1)

        # Bottom button row
        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(4)
        self.fit_btn = mindmeld_style.button('Frame All', kind='ghost')
        self.fit_btn.clicked.connect(self._frame_default)
        btns.addWidget(self.fit_btn)
        self.fit_selected_btn = mindmeld_style.button('Frame Selected',
                                                       kind='ghost')
        self.fit_selected_btn.clicked.connect(self._frame_selected)
        btns.addWidget(self.fit_selected_btn)
        btns.addStretch(1)
        self.cancel_btn = mindmeld_style.button('Cancel', kind='default')
        self.cancel_btn.clicked.connect(self.reject)
        btns.addWidget(self.cancel_btn)
        self.capture_btn = mindmeld_style.button('Capture Thumbnail',
                                                  kind='primary')
        self.capture_btn.clicked.connect(self._on_capture)
        btns.addWidget(self.capture_btn)
        root.addLayout(btns)

    # ── Framing ────────────────────────────────────────────────

    def _frame_default(self):
        """Fit the rig in the embedded viewport."""
        try:
            cmds.viewFit(panel=self._panel_name, all=True)
        except Exception:
            pass

    def _frame_selected(self):
        """Fit the current selection in the embedded viewport."""
        try:
            cmds.viewFit(panel=self._panel_name)
        except Exception:
            pass

    # ── Capture ────────────────────────────────────────────────

    def _on_capture(self):
        """Capture button clicked — delegate to callback, or fall back to
        pose_thumbs.capture for backwards compatibility.

        callback signature: callable(panel_name: str) -> Path | None
        The callback is responsible for capturing and returning the path
        of whatever it produced (still PNG, GIF, etc.). Contract:
            - Returns Path → dialog stores it, accepts, run() returns it.
            - Returns None → dialog rejects (user-initiated abort). run()
              returns None. Same behavior as the user clicking Cancel.
            - Raises Exception → dialog shows QMessageBox.critical, stays
              open so the user can re-attempt or cancel.

        The unified QMessageBox failure surface keeps the callback branch's
        UX consistent with the legacy pose_thumbs.capture fallback.
        """
        if self._capture_callback is not None:
            try:
                result = self._capture_callback(self._panel_name)
            except Exception as exc:
                QtWidgets.QMessageBox.critical(
                    self, 'Capture Failed',
                    f'Thumbnail capture failed: {exc}',
                )
                return
            if result is None:
                # Callback signaled user-initiated abort (cancel inside the
                # callback). Treat as if the user clicked Cancel — reject
                # so run() returns None without accepting a stale path.
                self.reject()
                return
            self._captured_path = result
            self.accept()
            return
        # Fallback: legacy still capture to a temp PNG.
        try:
            tmp_path = Path(
                tempfile.mktemp(prefix='ks_pose_framed_', suffix='.png')
            )
            pose_thumbs.capture(tmp_path, panel=self._panel_name)
            self._captured_path = tmp_path
            self.accept()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, 'Capture Failed',
                f'Thumbnail capture failed: {exc}',
            )

    def captured_path(self):
        """Return the captured Path (PNG, GIF, etc.), or None if not captured."""
        return self._captured_path

    # ── Cleanup ────────────────────────────────────────────────

    def _cleanup_maya(self):
        """Delete the hidden Maya window scaffold + its modelPanel.

        The destroy-side `cleanupModelPanelBar ||modelPanel<N>;` MEL
        syntax error comes from Maya's compiled C++ panel teardown
        (the MEL proc is defined but no .mel file in the install
        calls it — the call is built and evaluated from compiled
        code at panel destruction). The `||` is a MEL parse error,
        so we can't redefine the proc to fix it.

        Two-pronged suppression:
        1. Delete the modelPanel BEFORE the window so the C++
           teardown sees the panel already gone and (sometimes) skips
           the buggy MEL.
        2. Toggle scriptEditorInfo(suppressErrors=True) around
           deleteUI, and defer the restoration via evalDeferred so
           that Maya's deferred MEL emission still happens inside
           the suppression window.
        """
        suppressed = False
        try:
            cmds.scriptEditorInfo(suppressErrors=True)
            suppressed = True
        except Exception:
            pass
        try:
            if (self._panel_name and
                    cmds.modelPanel(self._panel_name, q=True, exists=True)):
                # catchQuiet swallows errors emitted by the deleteUI's
                # MEL eval chain, including the malformed
                # `cleanupModelPanelBar ||<panel>;` the C++ teardown
                # builds when the panel lives outside $gMainPane.
                from maya import mel as _mel
                _mel.eval(
                    'catchQuiet(`deleteUI -panel "{0}"`)'.format(
                        self._panel_name
                    )
                )
        except Exception:
            pass
        try:
            if self._maya_window_name and cmds.window(
                    self._maya_window_name, exists=True):
                from maya import mel as _mel
                _mel.eval(
                    'catchQuiet(`deleteUI -window "{0}"`)'.format(
                        self._maya_window_name
                    )
                )
        except Exception:
            pass
        if suppressed:
            # Restore error display on the next idle tick — Maya emits
            # the buggy panel-teardown MEL deferred, after this function
            # has already returned. Restoring inline would let the
            # error print after suppression is back off.
            try:
                cmds.evalDeferred(
                    'import maya.cmds as cmds; '
                    'cmds.scriptEditorInfo(suppressErrors=False)',
                    lowestPriority=True,
                )
            except Exception:
                try:
                    cmds.scriptEditorInfo(suppressErrors=False)
                except Exception:
                    pass
        _restore_mtoa_viewport_callbacks(self._mtoa_killed_jobs)
        self._mtoa_killed_jobs = []
        self._maya_window_name = ''
        self._panel_name = ''

    def closeEvent(self, event):
        self._cleanup_maya()
        super().closeEvent(event)

    def reject(self):
        self._cleanup_maya()
        super().reject()

    def accept(self):
        # Defer cleanup so the caller can read captured_path() first.
        super().accept()
        QtCore.QTimer.singleShot(0, self._cleanup_maya)

    # ── Public entry ───────────────────────────────────────────

    @classmethod
    def run(cls, parent=None, capture_callback=None):
        """Show the framing dialog modally; return the captured Path or None.

        When `capture_callback` is provided, the callback can signal
        user-initiated abort by returning None (treated as cancel) or surface
        a failure by raising (QMessageBox.critical is shown, dialog stays
        open). On success the callback returns the produced Path.
        """
        dlg = cls(parent=parent, capture_callback=capture_callback)
        result = dlg.exec()
        if result == QtWidgets.QDialog.Accepted:
            return dlg.captured_path()
        return None
