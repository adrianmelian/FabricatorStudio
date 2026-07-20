# Python/maya_tools/export/anim_progress.py
"""Back-compat shim — preserves the ExportProgressDialog name + on_clip_started
method that anim_ui still calls. Real implementation lives in
maya_tools.utils.qt.widgets.batch_progress.BatchProgressDialog.

Kept so anim_ui doesn't need to change its import or call sites; new
callers should use BatchProgressDialog directly via
`from maya_tools.utils.qt.widgets import BatchProgressDialog`.
"""
__author__ = "Adrian Melian"

from maya_tools.utils.qt.widgets.batch_progress import BatchProgressDialog


class ExportProgressDialog(BatchProgressDialog):
    """Anim-exporter labels on top of the generic BatchProgressDialog."""

    def __init__(self, total: int, parent=None):
        super().__init__(
            total=total,
            title='Exporting Animations',
            header_template='EXPORTING CLIP {idx} / {total}',
            parent=parent,
        )

    def on_clip_started(self, idx: int, total: int, clip_label: str) -> None:
        """Back-compat alias for on_item_started — anim_ui calls this name.

        `total` is accepted for the legacy 3-arg signature but ignored; the
        base class reads the count from the progress bar's maximum.
        """
        return self.on_item_started(idx, clip_label)
