# maya_tools/skinning/dem_bones_bake/dem_bones_bake_ui.py
"""DemBones Bake — UI only. All logic lives in dem_bones_bake_app.py.

Two stages, one workflow: DEFORM adds the teacher deformer (Delta Mush is
the first block; future deformers slot in as further labeled blocks under
the same tab), BAKE converts that deform stack into skinCluster weights.
They ship together because they are used together — the bake has nothing
to consume without the smooth.

Replaces the BAKE + DEFORM half of the retired AutoSkinDialog; the BIND
half is superseded by AutoSkin (maya_tools/skinning/autoskin/).

Selection-driven per click (each stage reads the live Maya selection when
its button is pressed).

Spec: docs/superpowers/specs/2026-05-26-dem-bones-bake-design.md
"""
__author__ = "Adrian Melian"

import traceback

from PySide6 import QtCore, QtWidgets

from maya_tools.framework.installer import ml_deps_ui
from maya_tools.skinning.dem_bones_bake import dem_bones_bake_app as app
from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget

_win = None


class DemBonesBakeWindow(QtWidgets.QDialog):
    """Mindmeld 2.0 floating dialog for the Delta Mush -> DemBones flow."""

    WINDOW_NAME = 'DemBonesBakeTool'
    WINDOW_TITLE = 'DemBonesBake'

    def __init__(self, parent=None):
        super().__init__(parent or get_maya_window())
        mindmeld_style.apply(self)
        self.setObjectName(self.WINDOW_NAME)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setProperty('saveWindowPref', True)
        self.setMinimumWidth(400)
        self.create_layout()
        self.connect_signals()

    # === Layout ===
    def create_layout(self) -> None:
        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(14, 14, 14, 12)
        main.setSpacing(10)

        main.addLayout(self._build_brand_row())
        main.addWidget(mindmeld_style.horizontal_rule())

        self._tabs = QtWidgets.QTabWidget()
        self._tabs.addTab(self._build_deform_tab(), 'DEFORM')
        self._tabs.addTab(self._build_bake_tab(), 'BAKE')
        main.addWidget(self._tabs)

        # Persistent status line — stays visible across both tabs.
        self._status_label = QtWidgets.QLabel('Status: Ready.')
        self._status_label.setWordWrap(True)
        main.addWidget(self._status_label)

        self._log_widget = LoggerWidget()
        self._log_section = CollapsibleSection('LOG')
        log_body = QtWidgets.QVBoxLayout()
        log_body.setContentsMargins(0, 4, 0, 0)
        log_body.addWidget(self._log_widget)
        self._log_section.set_body_layout(log_body)
        self._log_section.set_expanded(True)
        main.addWidget(self._log_section)

    def _build_brand_row(self) -> QtWidgets.QHBoxLayout:
        """Banner is the identity; the text brand is the fallback only."""
        from maya_tools.framework.toolbar.widgets.icon_button import load_icon

        brand_row = QtWidgets.QHBoxLayout()
        brand_row.setSpacing(10)

        logo = load_icon('fs_dembonesbake_banner.png')
        if logo is not None and not logo.isNull():
            banner = QtWidgets.QLabel()
            banner.setPixmap(logo.pixmap(QtCore.QSize(152, 38)))
            brand_row.addWidget(banner)
        else:
            brand_row.addWidget(mindmeld_style.brand_label('DemBonesBake'))

        brand_row.addWidget(
            mindmeld_style.caps_label('weights · smooth + bake'),
            0, QtCore.Qt.AlignmentFlag.AlignBottom,
        )
        brand_row.addStretch()
        return brand_row

    # === Tab builders ===
    def _build_deform_tab(self) -> QtWidgets.QWidget:
        """DEFORM — the teacher deformer stack the bake consumes. Delta Mush
        is the first block; add future deformers as further display_label +
        fields + button groups in this same tab."""
        tab = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(tab)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        lay.addWidget(mindmeld_style.display_label('Delta Mush'))

        iters_row = QtWidgets.QHBoxLayout()
        iters_row.addWidget(mindmeld_style.field_label('Smoothing Iterations'))
        iters_row.addStretch(1)
        self._dmush_iters_spin = QtWidgets.QSpinBox()
        self._dmush_iters_spin.setRange(0, 200)
        self._dmush_iters_spin.setValue(10)
        mindmeld_style.tag(self._dmush_iters_spin, 'panel')
        iters_row.addWidget(self._dmush_iters_spin)
        lay.addLayout(iters_row)

        step_row = QtWidgets.QHBoxLayout()
        step_row.addWidget(mindmeld_style.field_label('Smoothing Step'))
        step_row.addStretch(1)
        self._dmush_step_spin = QtWidgets.QDoubleSpinBox()
        self._dmush_step_spin.setRange(0.0, 1.0)
        self._dmush_step_spin.setSingleStep(0.05)
        self._dmush_step_spin.setDecimals(2)
        self._dmush_step_spin.setValue(0.5)
        mindmeld_style.tag(self._dmush_step_spin, 'panel')
        step_row.addWidget(self._dmush_step_spin)
        lay.addLayout(step_row)

        lay.addWidget(mindmeld_style.helper_label(
            'Select the bound mesh, then smooth it before baking.'))

        lay.addStretch(1)
        self._dmush_button = mindmeld_style.button('Apply Delta Mush',
                                                     kind='primary')
        lay.addWidget(self._dmush_button)
        return tab

    def _build_bake_tab(self) -> QtWidgets.QWidget:
        """BAKE — convert the deform stack into skinCluster weights."""
        tab = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(tab)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        lay.addWidget(mindmeld_style.display_label('DemBones'))

        max_inf_row = QtWidgets.QHBoxLayout()
        max_inf_row.addWidget(mindmeld_style.field_label('Max Influences'))
        max_inf_row.addStretch(1)
        self._dembones_max_inf_spin = QtWidgets.QSpinBox()
        self._dembones_max_inf_spin.setRange(1, 8)
        self._dembones_max_inf_spin.setValue(4)
        mindmeld_style.tag(self._dembones_max_inf_spin, 'panel')
        max_inf_row.addWidget(self._dembones_max_inf_spin)
        lay.addLayout(max_inf_row)

        self._dembones_debug_check = QtWidgets.QCheckBox('Debug Compare')
        self._dembones_debug_check.setToolTip(
            'Bake DemBones weights to a duplicate of the input mesh; leave '
            "the original mesh + deltaMush stack untouched. Use to A/B "
            'the bake against the live deltaMush stack.'
        )
        lay.addWidget(self._dembones_debug_check)

        lay.addWidget(mindmeld_style.helper_label(
            'Bakes over the active timeline range.'))

        lay.addStretch(1)
        self._dembones_button = mindmeld_style.button('Bake DemBones',
                                                       kind='primary')
        lay.addWidget(self._dembones_button)
        return tab

    def connect_signals(self) -> None:
        self._dmush_button.clicked.connect(self._on_dmush_clicked)
        self._dembones_button.clicked.connect(self._on_dembones_clicked)

    # === Button handlers ===
    def _on_dmush_clicked(self) -> None:
        """Add a deltaMush deformer to each selected mesh."""
        self._dmush_button.setEnabled(False)
        iters = self._dmush_iters_spin.value()
        step = self._dmush_step_spin.value()
        try:
            self._set_status(
                f'Applying deltaMush (iters={iters}, step={step:.2f})...'
            )
            self._log_widget.info(
                f'[DemBones Bake] DeltaMush: iters={iters}, step={step:.2f}'
            )
            QtWidgets.QApplication.processEvents()
            created = app.delta_mush(iters, step)
            msg = (
                f'Applied deltaMush to {len(created)} mesh(es): '
                f"{', '.join(created)}."
            )
            self._set_status(msg)
            self._log_widget.success(msg)
        except Exception as e:
            self._handle_error(
                e, brief=f'DeltaMush failed: {str(e).splitlines()[0]}')
        finally:
            self._dmush_button.setEnabled(True)

    def _on_dembones_clicked(self) -> None:
        """Bake DemBones into the current selection's skinCluster weights."""
        self._dembones_button.setEnabled(False)
        max_inf = self._dembones_max_inf_spin.value()
        debug_compare = self._dembones_debug_check.isChecked()
        try:
            self._set_status(
                f'Baking DemBones (max_influences={max_inf}, '
                f'debug_compare={debug_compare})...'
            )
            self._log_widget.info(
                f'[DemBones Bake] DemBones: max_influences={max_inf}, '
                f'debug_compare={debug_compare}'
            )
            QtWidgets.QApplication.processEvents()
            stats = self._run_dembones_with_deps_prompt(max_inf, debug_compare)
            sc = stats['skin_cluster']                  # contract-guaranteed
            compare_mesh = stats.get('compare_mesh')    # None unless debug_compare
            if debug_compare and compare_mesh:
                msg = (
                    f'Baked DemBones weights to debug duplicate '
                    f'{compare_mesh!r} (skinCluster: {sc}).'
                )
            else:
                msg = f'Baked DemBones weights to {sc}.'
            self._set_status(msg)
            self._log_widget.success(msg)
        except Exception as e:
            self._handle_error(
                e, brief=f'DemBones bake failed: {str(e).splitlines()[0]}')
        finally:
            self._dembones_button.setEnabled(True)

    def _run_dembones_with_deps_prompt(self, max_inf: int,
                                        debug_compare: bool) -> dict:
        """Run dem_bones_bake(); on a missing-deps RuntimeError, offer the
        guided ML-deps installer once, then retry. Mirrors
        bbw_skin_ui._run_solve_with_deps_prompt — see that docstring for the
        substring-detection rationale."""
        try:
            return app.dem_bones_bake(
                max_influences=max_inf, debug_compare=debug_compare,
            )
        except RuntimeError as exc:
            if 'not importable in mayapy' not in str(exc):
                raise
            self._log_widget.warning(f'{exc}')
            if not ml_deps_ui.maybe_prompt_first_run('DemBones Bake',
                                                      parent=self):
                raise
            return app.dem_bones_bake(
                max_influences=max_inf, debug_compare=debug_compare,
            )

    # === Error reporting ===
    def _handle_error(self, e: Exception, brief: str = None) -> None:
        """Print full traceback to script editor + surface brief on status."""
        traceback.print_exc()
        self._set_status(brief or str(e), ok=False)
        self._log_widget.error(str(e))

    # === Status ===
    def _set_status(self, text: str, ok: bool = True) -> None:
        """Update the status label. ok=False renders in ember for errors."""
        self._status_label.setText(f'Status: {text}')
        self._status_label.setStyleSheet(
            '' if ok else f'color: {mindmeld_style.EMBER};')

    @staticmethod
    def show_window():
        global _win
        try:
            _win.close()
            _win.deleteLater()
        except Exception:
            pass
        _win = DemBonesBakeWindow()
        _win.setWindowFlags(QtCore.Qt.WindowType.Window)
        _win.show()
        _win.raise_()
        _win.activateWindow()
        return _win
