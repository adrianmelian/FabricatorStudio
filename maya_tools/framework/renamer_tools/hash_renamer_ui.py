"""Hash Renamer UI."""
__author__ = "Adrian Melian"

import traceback

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QLabel, QLineEdit, QSpinBox,
    QHBoxLayout, QVBoxLayout,
)

import maya_tools.framework.renamer_tools.hash_renamer as hash_renamer
from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget

_INSTRUCTIONS = (
    "Use  <b>#</b>  as a digit placeholder — each # adds one digit of padding.<br>"
    "&nbsp;&nbsp;joint_##&nbsp;&nbsp;→&nbsp;&nbsp;joint_01,&nbsp;joint_02,&nbsp;joint_03 …<br>"
    "&nbsp;&nbsp;ctrl_###&nbsp;→&nbsp;&nbsp;ctrl_001,&nbsp;ctrl_002,&nbsp;ctrl_003 …<br>"
    "Objects are renamed in selection order."
)

_INSTANCE: "HashRenamerUI | None" = None


class HashRenamerUI(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        mindmeld_style.apply(self)

        self.setWindowTitle("Hash Renamer")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(400)
        self.create_layout()
        self.connect_signals()

    # ──────────────────────────────────────────────────────────────────────────

    def create_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 12)

        # ── brand bar ──
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        brand_row.addWidget(mindmeld_style.brand_label('Hash Renamer'))
        brand_row.addWidget(mindmeld_style.caps_label('// # placeholder'),
                            0, Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        root.addLayout(brand_row)
        root.addWidget(mindmeld_style.horizontal_rule())

        # ── Instructions ──
        instructions = QLabel(_INSTRUCTIONS)
        instructions.setTextFormat(Qt.RichText)
        instructions.setWordWrap(True)
        mindmeld_style.tag(instructions, 'helper')
        root.addWidget(instructions)

        root.addWidget(mindmeld_style.horizontal_rule())

        # ── Pattern ──
        pattern_row = QHBoxLayout()
        pattern_row.setSpacing(8)
        pattern_row.addWidget(mindmeld_style.field_label('Pattern'))
        self._pattern = QLineEdit()
        self._pattern.setPlaceholderText("e.g.  joint_##  or  L_finger_###_jnt")
        pattern_row.addWidget(self._pattern, 1)
        root.addLayout(pattern_row)

        # ── Start ──
        start_row = QHBoxLayout()
        start_row.setSpacing(8)
        start_row.addWidget(mindmeld_style.field_label('Start Index'))
        self._start = QSpinBox()
        self._start.setMinimum(0)
        self._start.setMaximum(9999)
        self._start.setValue(1)
        self._start.setFixedWidth(80)
        start_row.addWidget(self._start)
        start_row.addStretch()
        root.addLayout(start_row)

        root.addStretch()

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        self._btn_rename = mindmeld_style.button('Rename', kind='primary')
        self._btn_rename.setDefault(True)
        self._btn_rename.setMinimumWidth(96)
        self._btn_cancel = mindmeld_style.button('Cancel', kind='ghost')
        self._btn_cancel.setMinimumWidth(96)
        btn_row.addWidget(self._btn_rename)
        btn_row.addWidget(self._btn_cancel)
        root.addLayout(btn_row)

        # ── Logger ──
        self.log_section = CollapsibleSection('// LOG')
        self.logger = LoggerWidget()
        log_body_layout = QVBoxLayout()
        log_body_layout.setContentsMargins(0, 0, 0, 0)
        log_body_layout.addWidget(self.logger)
        self.log_section.set_body_layout(log_body_layout)
        self.log_section.set_expanded(False)
        root.addWidget(self.log_section)

    def connect_signals(self) -> None:
        self._btn_rename.clicked.connect(self._on_rename)
        self._btn_cancel.clicked.connect(self.close)
        self._pattern.returnPressed.connect(self._on_rename)

    # ──────────────────────────────────────────────────────────────────────────

    def _on_rename(self) -> None:
        pattern = self._pattern.text().strip()
        start = self._start.value()
        try:
            new_names = hash_renamer.rename_selection(pattern, start)
            self.logger.success(f"Renamed {len(new_names)} object(s).")
        except Exception as e:
            self._handle_error(e)

    def _handle_error(self, e: Exception, brief: str | None = None) -> None:
        traceback.print_exc()
        self.logger.error(brief or str(e))

    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def show_window() -> "HashRenamerUI":
        global _INSTANCE
        if _INSTANCE is not None:
            try:
                _INSTANCE.close()
            except RuntimeError:
                pass
        _INSTANCE = HashRenamerUI(parent=get_maya_window())
        _INSTANCE.show()
        _INSTANCE.raise_()
        _INSTANCE._pattern.setFocus()
        return _INSTANCE
