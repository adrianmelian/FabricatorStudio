"""AM Renamer UI — tabbed rename tool (Hash, Find/Replace, Prefix/Suffix, Renumber)."""
__author__ = "Adrian Melian"

import traceback

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QHBoxLayout,
    QLabel, QLineEdit, QRadioButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

import maya_tools.framework.renamer_tools.renamer as renamer_app
import maya_tools.framework.renamer_tools.hash_renamer as hash_renamer
from maya_tools.utils.maya.gui import get_maya_window, close_windows_by_name
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget

_win: "FSRenamerUI | None" = None

TAB_HASH         = 0
TAB_FIND_REPLACE = 1
TAB_PREFIX_SUFFIX = 2
TAB_RENUMBER     = 3


class FSRenamerUI(QDialog):
    WINDOW_NAME = 'FSRenamerUI'

    def __init__(self, parent=None):
        super().__init__(parent)
        mindmeld_style.apply(self)

        self.setObjectName(self.WINDOW_NAME)
        self.setWindowTitle("Renamer")
        self.setProperty('saveWindowPref', True)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(440)
        self.create_layout()
        self.connect_signals()

    # ── Layout ────────────────────────────────────────────────────────────────

    def create_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 12)

        # ── brand bar ──
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        brand_row.addWidget(mindmeld_style.brand_label('Renamer'))
        brand_row.addWidget(mindmeld_style.caps_label('// 4-mode rename'),
                            0, Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        root.addLayout(brand_row)
        root.addWidget(mindmeld_style.horizontal_rule())

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_hash_tab(),          "HASH")
        self._tabs.addTab(self._build_find_replace_tab(),  "FIND / REPLACE")
        self._tabs.addTab(self._build_prefix_suffix_tab(), "PREFIX / SUFFIX")
        self._tabs.addTab(self._build_renumber_tab(),      "RENUMBER")
        root.addWidget(self._tabs)

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

        self.log_section = CollapsibleSection('// LOG')
        self.logger = LoggerWidget()
        log_body_layout = QVBoxLayout()
        log_body_layout.setContentsMargins(0, 0, 0, 0)
        log_body_layout.addWidget(self.logger)
        self.log_section.set_body_layout(log_body_layout)
        self.log_section.set_expanded(False)
        root.addWidget(self.log_section)

    # ── Tab builders ──────────────────────────────────────────────────────────

    def _build_hash_tab(self) -> QWidget:
        w, lay = self._tab_widget()

        lay.addWidget(self._instruction(
            "Use <b>#</b> as a digit placeholder — each # adds one digit of padding.<br>"
            "&nbsp;&nbsp;joint_##&nbsp;→&nbsp;joint_01,&nbsp;joint_02,&nbsp;joint_03 …<br>"
            "&nbsp;&nbsp;ctrl_###&nbsp;→&nbsp;ctrl_001,&nbsp;ctrl_002,&nbsp;ctrl_003 …<br>"
            "Objects are renamed in selection order."
        ))
        lay.addWidget(mindmeld_style.horizontal_rule())

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(mindmeld_style.field_label('Pattern'))
        self._hash_pattern = QLineEdit()
        self._hash_pattern.setPlaceholderText("e.g.  joint_##  or  L_finger_###_jnt")
        row.addWidget(self._hash_pattern, 1)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(mindmeld_style.field_label('Start'))
        self._hash_start = QSpinBox()
        self._hash_start.setRange(0, 9999)
        self._hash_start.setValue(1)
        self._hash_start.setFixedWidth(80)
        row2.addWidget(self._hash_start)
        row2.addStretch()
        lay.addLayout(row2)

        lay.addStretch()
        return w

    def _build_find_replace_tab(self) -> QWidget:
        w, lay = self._tab_widget()

        lay.addWidget(self._instruction(
            "Replace text in selected object names. Leave <b>Replace</b> empty to delete."
        ))
        lay.addWidget(mindmeld_style.horizontal_rule())

        find_row = QHBoxLayout()
        find_row.setSpacing(8)
        find_row.addWidget(mindmeld_style.field_label('Find'))
        self._fr_find = QLineEdit()
        self._fr_find.setPlaceholderText("text to find")
        find_row.addWidget(self._fr_find, 1)
        lay.addLayout(find_row)

        rep_row = QHBoxLayout()
        rep_row.setSpacing(8)
        rep_row.addWidget(mindmeld_style.field_label('Replace'))
        self._fr_replace = QLineEdit()
        self._fr_replace.setPlaceholderText("replacement (empty = delete)")
        rep_row.addWidget(self._fr_replace, 1)
        lay.addLayout(rep_row)

        self._fr_case = QCheckBox("Case sensitive")
        self._fr_case.setChecked(True)
        lay.addWidget(self._fr_case)

        lay.addStretch()
        return w

    def _build_prefix_suffix_tab(self) -> QWidget:
        w, lay = self._tab_widget()

        lay.addWidget(self._instruction(
            "Add or remove a prefix or suffix from selected object names."
        ))
        lay.addWidget(mindmeld_style.horizontal_rule())

        # Mode — needs explicit group so it doesn't conflict with Side radios
        self._ps_mode_group = QButtonGroup(self)
        self._ps_add    = QRadioButton("Add")
        self._ps_remove = QRadioButton("Remove")
        self._ps_mode_group.addButton(self._ps_add)
        self._ps_mode_group.addButton(self._ps_remove)
        self._ps_add.setChecked(True)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        mode_row.addWidget(mindmeld_style.field_label('Mode'))
        mode_row.addWidget(self._ps_add)
        mode_row.addWidget(self._ps_remove)
        mode_row.addStretch()
        lay.addLayout(mode_row)

        # Side
        self._ps_side_group = QButtonGroup(self)
        self._ps_prefix = QRadioButton("Prefix")
        self._ps_suffix = QRadioButton("Suffix")
        self._ps_side_group.addButton(self._ps_prefix)
        self._ps_side_group.addButton(self._ps_suffix)
        self._ps_prefix.setChecked(True)

        side_row = QHBoxLayout()
        side_row.setSpacing(10)
        side_row.addWidget(mindmeld_style.field_label('Side'))
        side_row.addWidget(self._ps_prefix)
        side_row.addWidget(self._ps_suffix)
        side_row.addStretch()
        lay.addLayout(side_row)

        text_row = QHBoxLayout()
        text_row.setSpacing(8)
        text_row.addWidget(mindmeld_style.field_label('Text'))
        self._ps_text = QLineEdit()
        self._ps_text.setPlaceholderText("e.g.  L_  or  _jnt")
        text_row.addWidget(self._ps_text, 1)
        lay.addLayout(text_row)

        lay.addStretch()
        return w

    def _build_renumber_tab(self) -> QWidget:
        w, lay = self._tab_widget()

        lay.addWidget(self._instruction(
            "Strips trailing numbers and resequences selection in order.<br>"
            "&nbsp;&nbsp;joint_01,&nbsp;joint_03,&nbsp;joint_07&nbsp;→&nbsp;joint_01,&nbsp;joint_02,&nbsp;joint_03"
        ))
        lay.addWidget(mindmeld_style.horizontal_rule())

        def _spin_row(label_text, attr, lo, hi, val, w=80):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(mindmeld_style.field_label(label_text))
            sb = QSpinBox()
            sb.setRange(lo, hi)
            sb.setValue(val)
            sb.setFixedWidth(w)
            setattr(self, attr, sb)
            row.addWidget(sb)
            row.addStretch()
            return row

        sep_row = QHBoxLayout()
        sep_row.setSpacing(8)
        sep_row.addWidget(mindmeld_style.field_label('Separator'))
        self._rn_sep = QLineEdit("_")
        self._rn_sep.setFixedWidth(60)
        sep_row.addWidget(self._rn_sep)
        sep_row.addStretch()
        lay.addLayout(sep_row)

        lay.addLayout(_spin_row("Padding", "_rn_pad",   1, 6,    2))
        lay.addLayout(_spin_row("Start",   "_rn_start", 0, 9999, 1))

        lay.addStretch()
        return w

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _tab_widget() -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 14, 12, 12)
        lay.setSpacing(10)
        return w, lay

    @staticmethod
    def _instruction(html: str) -> QLabel:
        lbl = QLabel(html)
        lbl.setTextFormat(Qt.RichText)
        lbl.setWordWrap(True)
        mindmeld_style.tag(lbl, 'helper')
        return lbl

    # ── Signals ───────────────────────────────────────────────────────────────

    def connect_signals(self) -> None:
        self._btn_rename.clicked.connect(self._on_rename)
        self._btn_cancel.clicked.connect(self.close)
        self._hash_pattern.returnPressed.connect(self._on_rename)
        self._fr_find.returnPressed.connect(self._on_rename)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_rename(self) -> None:
        idx = self._tabs.currentIndex()
        try:
            if idx == TAB_HASH:
                self._do_hash()
            elif idx == TAB_FIND_REPLACE:
                self._do_find_replace()
            elif idx == TAB_PREFIX_SUFFIX:
                self._do_prefix_suffix()
            elif idx == TAB_RENUMBER:
                self._do_renumber()
        except Exception as e:
            self._handle_error(e)

    def _do_hash(self) -> None:
        pattern = self._hash_pattern.text().strip()
        start   = self._hash_start.value()
        names   = hash_renamer.rename_selection(pattern, start)
        self.logger.success(f"Renamed {len(names)} object(s).")

    def _do_find_replace(self) -> None:
        names = renamer_app.find_replace_selection(
            self._fr_find.text(),
            self._fr_replace.text(),
            self._fr_case.isChecked(),
        )
        self.logger.success(f"Find/replace applied to {len(names)} object(s).")

    def _do_prefix_suffix(self) -> None:
        mode = 'add'    if self._ps_add.isChecked()    else 'remove'
        side = 'prefix' if self._ps_prefix.isChecked() else 'suffix'
        text = self._ps_text.text()
        names = renamer_app.prefix_suffix_selection(mode, side, text)
        self.logger.success(f"Done — {len(names)} object(s) processed.")

    def _do_renumber(self) -> None:
        names = renamer_app.renumber_selection(
            self._rn_sep.text(),
            self._rn_pad.value(),
            self._rn_start.value(),
        )
        self.logger.success(f"Renumbered {len(names)} object(s).")

    # ── Status / error ────────────────────────────────────────────────────────

    def _handle_error(self, e: Exception, brief: str | None = None) -> None:
        traceback.print_exc()
        self.logger.error(brief or str(e))

    # ── Entry point ───────────────────────────────────────────────────────────

    @staticmethod
    def show_window(tab: int = TAB_HASH) -> "FSRenamerUI":
        global _win
        # Reload-proof singleton: the shelf reloads this module each press,
        # so close any prior window by objectName, not the lost _win ref.
        close_windows_by_name(FSRenamerUI.WINDOW_NAME)
        _win = FSRenamerUI(parent=get_maya_window())
        _win._tabs.setCurrentIndex(tab)
        _win.show()
        _win.raise_()
        return _win
