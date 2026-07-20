# maya_tools/rigging/fabricator/ui/build_issues_dialog.py
"""Build Issues dialog — the standardized pre-build gate. UI only;
all detection + fixing lives in fabricator/build_checks.py.

One window for every detected build break: select an issue to read a
plain-language description, Fix Selected / Fix All to auto-repair,
Build (Anyway) to proceed, Cancel to bail. Replaces the old sequential
guard popups (duplicated joints, orphaned modules, missing aimers).
"""
__author__ = "Adrian Melian"

import traceback

from PySide6 import QtWidgets, QtCore, QtGui

from maya_tools.rigging.fabricator import build_checks
from maya_tools.utils.qt.mindmeld import mindmeld_style


class BuildIssuesDialog(QtWidgets.QDialog):
    """Modal pre-build gate. Use run_gate() — returns None when the
    user cancels, else {'force_missing_aimers': bool} describing what
    the build should push through."""

    WINDOW_TITLE = 'Fabricator — build checks'

    def __init__(self, parent, issues, logger=None):
        super().__init__(parent)
        mindmeld_style.apply(self)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setMinimumSize(620, 460)

        self._issues = list(issues)
        self._logger = logger

        self.create_layout()
        self.connect_signals()
        self.populate()

    # ── layout ────────────────────────────────────────────────────

    def create_layout(self):
        main = QtWidgets.QVBoxLayout(self)
        main.setSpacing(10)
        main.setContentsMargins(14, 14, 14, 12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(mindmeld_style.display_label('BUILD CHECKS',
                                                      large=True))
        header.addWidget(
            mindmeld_style.caps_label('// issues · fix or skip'),
            0, QtCore.Qt.AlignmentFlag.AlignBottom)
        header.addStretch()
        main.addLayout(header)
        main.addWidget(mindmeld_style.horizontal_rule())

        # Issue list ↔ empty state
        self.stack = QtWidgets.QStackedWidget()

        self.issue_list = QtWidgets.QListWidget()
        self.issue_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.stack.addWidget(self.issue_list)

        empty_page = QtWidgets.QWidget()
        empty_lay = QtWidgets.QVBoxLayout(empty_page)
        empty_lay.addStretch()
        empty_lay.addWidget(
            mindmeld_style.caps_label('// all checks pass'),
            0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        empty_lay.addStretch()
        self.stack.addWidget(empty_page)

        main.addWidget(self.stack, 1)

        # Description pane
        main.addWidget(mindmeld_style.caps_label('// details'))
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(130)
        main.addWidget(self.details, 1)

        # Actions
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)
        self.fix_selected_btn = mindmeld_style.button('Fix Selected')
        # Fix All is the recommended action → plasma green (primary).
        self.fix_all_btn = mindmeld_style.button('Fix All', kind='primary')
        btn_row.addWidget(self.fix_selected_btn)
        btn_row.addWidget(self.fix_all_btn)
        btn_row.addStretch()
        # Building past unresolved issues is the caution path → ember orange.
        self.build_btn = mindmeld_style.button('Build Anyway',
                                               kind='danger')
        self.cancel_btn = mindmeld_style.button('Cancel', kind='ghost')
        btn_row.addWidget(self.build_btn)
        btn_row.addWidget(self.cancel_btn)
        main.addLayout(btn_row)

    def connect_signals(self):
        self.issue_list.itemSelectionChanged.connect(self._on_selection)
        self.fix_selected_btn.clicked.connect(self._on_fix_selected)
        self.fix_all_btn.clicked.connect(self._on_fix_all)
        self.build_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

    # ── state ─────────────────────────────────────────────────────

    def populate(self):
        """Render the current issue set; refresh button states.
        Severity speaks in color (Adrian, 2026-07-05): errors FLARE
        red, warnings AMBER yellow — colors are data here, sourced
        from the Mindmeld tokens."""
        self.issue_list.clear()
        for i, issue in enumerate(self._issues):
            severity = getattr(issue, 'severity', 'error')
            prefix = 'Warning — ' if severity == 'warning' else ''
            suffix = ('' if issue.fixable or severity == 'warning'
                      else '  — manual fix required')
            item = QtWidgets.QListWidgetItem(
                f'{prefix}{issue.title}{suffix}')
            item.setData(QtCore.Qt.ItemDataRole.UserRole, i)
            item.setForeground(QtGui.QBrush(QtGui.QColor(
                mindmeld_style.AMBER if severity == 'warning'
                else mindmeld_style.FLARE)))
            self.issue_list.addItem(item)

        if self._issues:
            self.stack.setCurrentIndex(0)
            self.issue_list.setCurrentRow(0)
        else:
            self.stack.setCurrentIndex(1)
            self.details.setPlainText(
                'All checks pass. Build when ready.')

        fixable_any = any(i.fixable for i in self._issues)
        self.fix_all_btn.setEnabled(fixable_any)
        self.fix_selected_btn.setEnabled(fixable_any)

        blocking = [i for i in self._issues
                    if not i.fixable and not i.skippable]
        skippable_left = [i for i in self._issues if i.skippable]
        warnings_only = bool(self._issues) and all(
            getattr(i, 'severity', 'error') == 'warning'
            for i in self._issues)
        if not self._issues:
            self.build_btn.setText('Build')
            self.build_btn.setEnabled(True)
        elif warnings_only:
            # Warnings inform, they don't scold — the button stays a
            # confident Build.
            self.build_btn.setText('Build')
            self.build_btn.setEnabled(True)
        elif blocking or [i for i in self._issues
                          if i.fixable and not i.skippable]:
            # Non-skippable issues remain (fixable or manual) — the
            # build would abort on them, so don't offer to try.
            self.build_btn.setText('Build Anyway')
            self.build_btn.setEnabled(False)
        else:
            self.build_btn.setText('Build Anyway')
            self.build_btn.setEnabled(bool(skippable_left))

    def _selected_issues(self):
        out = []
        for item in self.issue_list.selectedItems():
            idx = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if 0 <= idx < len(self._issues):
                out.append(self._issues[idx])
        return out

    def _on_selection(self):
        selected = self._selected_issues()
        if not selected:
            self.details.setPlainText('')
            return
        blocks = []
        for issue in selected:
            fix_line = (f'\n\n[Fix: {issue.fix_label}]'
                        if issue.fixable else '')
            blocks.append(f'{issue.title}\n\n{issue.description}'
                          f'{fix_line}')
        self.details.setPlainText(
            ('\n\n' + '─' * 48 + '\n\n').join(blocks))

    # ── fixing ────────────────────────────────────────────────────

    def _run_fixes(self, issues):
        for issue in issues:
            if not issue.fixable or issue.fix is None:
                continue
            try:
                summary = issue.fix()
                self._log_info(f'Fixed — {issue.title}: {summary}')
            except Exception as e:
                traceback.print_exc()
                self._log_error(f'Fix failed — {issue.title}: {e}')
        # Re-run detection: fixes may clear (or reveal) issues.
        self._issues = build_checks.run_prebuild_checks()
        self.populate()

    def _on_fix_selected(self):
        selected = [i for i in self._selected_issues() if i.fixable]
        if selected:
            self._run_fixes(selected)

    def _on_fix_all(self):
        self._run_fixes([i for i in self._issues if i.fixable])

    def _log_info(self, msg):
        if self._logger:
            self._logger.info(msg)

    def _log_error(self, msg):
        if self._logger:
            self._logger.error(msg)

    # ── entry point ───────────────────────────────────────────────

    @classmethod
    def run_gate(cls, parent, issues, logger=None):
        """Show the gate. Returns None on cancel, else a decision dict:
        {'force_missing_aimers': bool} — True when the user chose to
        build past a still-unfixed missing-aimers issue."""
        dlg = cls(parent, issues, logger=logger)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        return {
            'force_missing_aimers': any(
                i.key == 'missing_aimers' for i in dlg._issues),
        }
