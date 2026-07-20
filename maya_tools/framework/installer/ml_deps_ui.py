# maya_tools/framework/installer/ml_deps_ui.py
"""ML skinning dependency installer — Mindmeld-styled progress dialog.

Two entry points:
    show_ml_deps_installer(parent=None)   — modal, runs the install, blocks
                                             until done, shows the report.
    maybe_prompt_first_run(tool_name, parent=None)
                                           — call from an ML tool's app-layer
                                             failure path (dem_bones_bake_app)
                                             when the
                                             solver-deps probe fails. Offers
                                             a one-click "Install ML
                                             dependencies now" dialog instead
                                             of just a pip-hint string. If the
                                             user declines or it fails, the
                                             tool stays visible and the
                                             existing RuntimeError pip-hint
                                             message is still what surfaces.
"""
__author__ = "Adrian Melian"

from PySide6 import QtCore, QtWidgets

from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.framework.installer import ml_deps_app


class MLDepsInstallDialog(QtWidgets.QDialog):
    """Modal dialog: confirm -> progress -> report, all in one window.

    Mirrors BatchProgressDialog's modality/style conventions but adds a
    pre-install confirm step and a post-install report view, since this is
    a one-shot setup flow rather than a per-frame batch loop.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        mindmeld_style.apply(self)

        self.setWindowTitle('Install ML Dependencies')
        self.setMinimumWidth(520)
        self.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        self.setWindowFlags(
            QtCore.Qt.WindowType.Dialog
            | QtCore.Qt.WindowType.WindowTitleHint
            | QtCore.Qt.WindowType.CustomizeWindowHint
        )

        self._report = None
        self._create_layout()
        self._connect_signals()
        self._populate()

    # ---------- UI construction ----------

    def _create_layout(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 14, 16, 14)

        layout.addWidget(mindmeld_style.brand_label('ML Skinning Dependencies'))

        self._intro_label = QtWidgets.QLabel()
        self._intro_label.setWordWrap(True)
        layout.addWidget(self._intro_label)

        self._plan_list = QtWidgets.QPlainTextEdit()
        self._plan_list.setReadOnly(True)
        self._plan_list.setMaximumHeight(120)
        layout.addWidget(self._plan_list)

        self._status_label = mindmeld_style.helper_label('—')
        layout.addWidget(self._status_label)

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, len(ml_deps_app.build_install_plan()))
        self._bar.setValue(0)
        layout.addWidget(self._bar)

        self._report_view = QtWidgets.QPlainTextEdit()
        self._report_view.setReadOnly(True)
        self._report_view.setVisible(False)
        self._report_view.setMinimumHeight(140)
        layout.addWidget(self._report_view)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch()
        self._cancel_btn = mindmeld_style.button('Cancel', kind='ghost')
        self._install_btn = mindmeld_style.button('Install', kind='primary')
        self._close_btn = mindmeld_style.button('Close', kind='default')
        self._close_btn.setVisible(False)
        button_row.addWidget(self._cancel_btn)
        button_row.addWidget(self._install_btn)
        button_row.addWidget(self._close_btn)
        layout.addLayout(button_row)

    def _connect_signals(self) -> None:
        self._cancel_btn.clicked.connect(self.reject)
        self._close_btn.clicked.connect(self.accept)
        self._install_btn.clicked.connect(self._on_install_clicked)

    def _populate(self) -> None:
        self._intro_label.setText(
            'Installs numpy, scipy, and py_dem_bones '
            'into mayapy\'s user site-packages via pip. This is a one-time setup '
            'for DemBones Bake. No admin '
            'rights required. Requires an internet connection for this step only — '
            'nothing else in the toolset makes network calls.'
        )
        self._plan_list.setPlainText('\n'.join(f'  - {s}' for s in ml_deps_app.build_install_plan()))

    # ---------- install flow ----------

    def _on_install_clicked(self) -> None:
        self._install_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._status_label.setText('Resolving mayapy...')
        QtWidgets.QApplication.processEvents()

        def _progress(idx, total, spec):
            self._bar.setValue(idx - 1)
            self._status_label.setText(f'Installing {idx}/{total}: {spec}')
            QtWidgets.QApplication.processEvents()

        report = ml_deps_app.install_ml_deps(progress_cb=_progress)
        self._report = report
        self._bar.setValue(self._bar.maximum())
        self._show_report(report)

    def _show_report(self, report) -> None:
        text = ml_deps_app.format_report_text(report)
        self._report_view.setPlainText(text)
        self._report_view.setVisible(True)

        if report.fatal_error:
            self._status_label.setText('Could not start — see report below.')
        elif report.all_ok:
            self._status_label.setText('All ML dependencies installed.')
        elif report.offline:
            self._status_label.setText('Offline — could not reach PyPI. Tools stay visible; retry later.')
        else:
            self._status_label.setText(f'{len(report.failed)} package(s) failed — see report below.')

        self._install_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._close_btn.setVisible(True)

    # ---------- public result access ----------

    def report(self):
        return self._report


def show_ml_deps_installer(parent=None) -> "ml_deps_app.InstallReport | None":
    """Show the modal ML-deps installer dialog. Returns the InstallReport,
    or None if the user cancelled before installing."""
    dlg = MLDepsInstallDialog(parent)
    dlg.exec()
    return dlg.report()


def maybe_prompt_first_run(tool_name: str, parent=None) -> bool:
    """Offer a one-click install when an ML tool's solver-deps probe fails.

    Call this from the app-layer except-path right before re-raising the
    existing RuntimeError pip-hint — e.g.:

        try:
            _verify_solver_deps(mode)
        except RuntimeError as exc:
            if ml_deps_ui.maybe_prompt_first_run('DemBones Bake'):
                _verify_solver_deps(mode)   # re-probe; raises again if still missing
            else:
                raise

    Returns True if the install run reported all_ok (caller should re-probe
    and continue); False if the user declined, cancelled, or the install
    did not fully succeed (caller should surface the original error).
    """
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(f'{tool_name} — Missing Dependencies')
    box.setIcon(QtWidgets.QMessageBox.Icon.Question)
    box.setText(
        f'{tool_name} needs a few Python packages that are not installed yet '
        f'(numpy, scipy, py_dem_bones).\n\n'
        f'Install them now via mayapy pip? This is a one-time step and needs '
        f'an internet connection.'
    )
    box.setStandardButtons(
        QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
    )
    box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Yes)
    if box.exec() != QtWidgets.QMessageBox.StandardButton.Yes:
        return False

    report = show_ml_deps_installer(parent)
    return bool(report and report.all_ok)
