# maya_tools/utils/qt/widgets/batch_progress.py
"""Modal progress dialog for sequential batch operations.

Generalized from the anim exporter's original ExportProgressDialog —
same modality model (ApplicationModal, shown via show() not exec() so
the caller's blocking loop drives the cadence), same between-items
processEvents pattern that lets ESC fire reject() between iterations.

Usage:
    progress = BatchProgressDialog(
        total=len(items),
        title='Processing Scenes',
        header_template='PROCESSING SCENE {idx} / {total}',
        parent=...,
    )
    progress.show()
    try:
        for i, item in enumerate(items, 1):
            progress.on_item_started(i, item.name)
            if progress.is_cancelled():
                break
            # ... blocking work for this item ...
    finally:
        progress.close()

ESC, the Cancel button, and the title-bar X all funnel through reject(),
which sets _cancelled=True. The loop breaks at the next is_cancelled()
check (i.e. after the current item finishes — graceful stop semantics).
"""
__author__ = "Adrian Melian"

from PySide6 import QtCore, QtWidgets

from maya_tools.utils.qt.mindmeld import mindmeld_style


class BatchProgressDialog(QtWidgets.QDialog):
    """Modal progress dialog for a sequential per-item batch.

    `header_template` may use {idx} and {total} placeholders. The header
    updates on every on_item_started() call.
    """

    def __init__(self, total: int, title: str = 'Processing',
                 header_template: str = 'PROCESSING ITEM {idx} / {total}',
                 parent=None):
        super().__init__(parent)
        mindmeld_style.apply(self)

        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        self.setWindowFlags(
            QtCore.Qt.WindowType.Dialog
            | QtCore.Qt.WindowType.WindowTitleHint
            | QtCore.Qt.WindowType.CustomizeWindowHint
        )

        self._cancelled = False
        self._header_template = header_template

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 12)

        self._header_label = mindmeld_style.display_label(
            header_template.format(idx=0, total=total)
        )
        self._current_label = QtWidgets.QLabel('—')
        mindmeld_style.tag(self._current_label, 'dim')

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, total)
        self._bar.setValue(0)

        button_row = QtWidgets.QHBoxLayout()
        self._cancel_btn = mindmeld_style.button('Cancel', kind='danger')
        self._cancel_btn.setMinimumHeight(30)
        button_row.addStretch()
        button_row.addWidget(self._cancel_btn)

        layout.addWidget(self._header_label)
        layout.addWidget(self._current_label)
        layout.addWidget(self._bar)
        layout.addLayout(button_row)

        self._cancel_btn.clicked.connect(self.reject)

    def on_item_started(self, idx: int, item_label: str) -> None:
        """Update header + current-item label + bar, then process buffered
        Qt events so any ESC keypress queued during the previous item's
        blocking work can fire reject() before the next iteration starts.

        `total` is read from the QProgressBar's maximum (set at construction);
        passing it again per-call would be redundant and risks a header/bar
        drift if the caller's count diverges.
        """
        self._header_label.setText(
            self._header_template.format(idx=idx,
                                          total=self._bar.maximum())
        )
        self._current_label.setText(item_label)
        self._bar.setValue(idx - 1)
        QtWidgets.QApplication.processEvents()

    def is_cancelled(self) -> bool:
        return self._cancelled

    def reject(self) -> None:
        self._cancelled = True
        super().reject()
