# maya_tools/rigging/fabricator/ui/build_options_bar.py
"""BuildOptionsBar — single-row checkbox strip sitting above the Build
Rig button, exposing per-build flags the rigger toggles before clicking
Build. Empty / all-unchecked state matches the prior default behavior;
checked flags push into BuildContext.options via build_modules(options=…).

Mode-driven visibility: hidden on MODE_MODULES_BUILT (no point picking
build flags when there's nothing to build). Re-appears on Unbuild.

Each checkbox auto-unchecks after a successful build (handled by
FSWindow's _on_build_rig_clicked finalizer) so destructive one-shots
like 'Reset Control Shapes' don't accidentally re-fire on the next
build.
"""
__author__ = "Adrian Melian"

from PySide6 import QtCore, QtWidgets

from maya_tools.rigging.fabricator.ui import state


class BuildOptionsBar(QtWidgets.QWidget):
    """Per-build flag bar — checkboxes consumed by build_modules(options)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.create_layout()

    def create_layout(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # Reset Control Shapes — when checked, build skips the persisted
        # CV restore on every ctrl-bearing component (World, SimpleFK,
        # FKChain, SimpleIK / IKLeg, AdvancedFK). Ctrls come out with
        # their default library shape; any animator-authored CV tweaks
        # in the blueprint are ignored for THIS build only (the persisted
        # data stays on the network nodes and reappears on next build
        # if the checkbox is off again).
        self.reset_ctrl_shapes_cb = QtWidgets.QCheckBox('Reset Control Shapes')
        self.reset_ctrl_shapes_cb.setToolTip(
            'Reset Control Shapes — on the next Build, skip restoring '
            'persisted CV edits and use each ctrl\'s default library shape. '
            'Persisted shape data is NOT erased; uncheck and rebuild to '
            'get the saved CVs back.'
        )
        root.addWidget(self.reset_ctrl_shapes_cb)

        # Trailing stretch keeps checkboxes left-aligned; future flags
        # land here as additional checkboxes.
        root.addStretch(1)

    # ─── Public accessors ──────────────────────────────────────────────

    def collect_options(self) -> dict:
        """Return the current flag state as a dict consumable by
        build_modules(options=…). Only includes True flags so the
        default build path (no flags set) is identical to None."""
        opts = {}
        if self.reset_ctrl_shapes_cb.isChecked():
            opts['reset_ctrl_shapes'] = True
        return opts

    def reset_after_build(self) -> None:
        """Uncheck all destructive one-shot flags. Called by FSWindow
        after a successful build so the user doesn't accidentally
        re-trigger them on the next click."""
        self.reset_ctrl_shapes_cb.setChecked(False)

    # ─── Mode-driven visibility ────────────────────────────────────────

    def refresh_state(self, mode: str) -> None:
        """Hide on MODE_MODULES_BUILT — build flags aren't relevant in
        animation mode. Mirrors SkeletonHelpersBar.refresh_state(mode)."""
        self.setVisible(mode != state.MODE_MODULES_BUILT)
