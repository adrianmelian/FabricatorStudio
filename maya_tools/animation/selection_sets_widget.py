"""SelectionSetsWidget — the `// SELECTION SETS` list, shared by the Pose and
Anim libraries (Adrian, 2026-07-08: selection sets belong in BOTH tabs, not
just Poses).

Sets are SELECTION HELPERS (Adrian, 2026-07-05): clicking one emits
set_selected(raw) and the hosting window selects that set's ctrls in the
viewport via pose_sets.select_set_controls. This is pure UI - all set logic
lives headless in pose_library.sets (imported as pose_sets), so both libraries
share one source of truth.

Public API:
- signals: set_selected(str raw-list-text), add_set_requested()
- refresh_sets(binding): rebuild the list for a live binding
"""
from __future__ import annotations

import maya.cmds as cmds
from PySide6 import QtCore, QtWidgets

from maya_tools.animation.pose_library import sets as pose_sets
from maya_tools.utils.qt.mindmeld import mindmeld_style


class SelectionSetsWidget(QtWidgets.QWidget):
    # RAW list text ('user:' prefix intact) — the hosting window resolves it.
    set_selected = QtCore.Signal(str)
    add_set_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._binding = ''
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(mindmeld_style.caps_label('// SELECTION SETS'))
        self.sets_list = QtWidgets.QListWidget()
        # itemClicked (not selectionChanged): sets are selection HELPERS —
        # re-clicking the same row must re-select in the viewport after the
        # user hand-edited their selection.
        self.sets_list.itemClicked.connect(self._emit_set)
        # Right-click a USER set to delete it (built-in / baked sets can't be).
        self.sets_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.sets_list.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.sets_list, stretch=1)

        self.add_set_btn = mindmeld_style.button('+ Add Selection Set',
                                                 kind='default')
        self.add_set_btn.setToolTip(
            'Save the current viewport selection of Fabricator ctrls as a '
            'reusable selection set.')
        self.add_set_btn.clicked.connect(self._on_add)
        layout.addWidget(self.add_set_btn)

    def refresh_sets(self, binding: str) -> None:
        """Rebuild the selection-sets list for the given live binding,
        preserving the highlighted row. The binding's BAKED sets (real
        objectSets, baked at Build Modules) are the truth when present; rigs
        built before the bake fall back to runtime derivation. Restores the
        highlight only — selection helpers act on CLICK, never as a refresh
        side effect (a rebuild must not grab the user's viewport selection)."""
        self._binding = binding
        prev = (self.sets_list.currentItem().text()
                if self.sets_list.currentItem() else '')

        entries = [pose_sets.ALL_CONTROLS]
        baked = pose_sets.rig_baked_sets(binding) if binding else []
        if baked:
            entries += baked
        else:
            entries += [n for n in pose_sets.list_builtin_sets()
                        if n != pose_sets.ALL_CONTROLS]
        entries += [f'user:{n}' for n in pose_sets.list_user_sets()]

        self.sets_list.blockSignals(True)
        self.sets_list.clear()
        for name in entries:
            self.sets_list.addItem(name)
        row = 0
        if prev:
            found = self.sets_list.findItems(prev, QtCore.Qt.MatchExactly)
            if found:
                row = self.sets_list.row(found[0])
        self.sets_list.setCurrentRow(row if self.sets_list.count() else -1)
        self.sets_list.blockSignals(False)

    def _emit_set(self, item=None):
        item = item or self.sets_list.currentItem()
        if item is not None:
            # RAW text ('user:' prefix intact) — the window resolves.
            self.set_selected.emit(item.text())

    def _on_add(self):
        """Save the viewport selection of Fabricator ctrls as a user set —
        the shared 'create from selection' both libraries call."""
        from maya_tools.animation.pose_library.address import ctrl_to_address
        addresses = []
        for ctrl in cmds.ls(sl=True, type='transform') or []:
            addr = ctrl_to_address(ctrl)
            if addr is not None:
                addresses.append(addr)
        if not addresses:
            QtWidgets.QMessageBox.warning(
                self, 'Add Selection Set',
                'No Fabricator-tagged ctrls in the selection.')
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, 'Add Selection Set', f'Set name ({len(addresses)} ctrls):')
        if not ok or not name.strip():
            return
        try:
            pose_sets.save_user_set(name.strip(), addresses)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, 'Add Selection Set', f'Save failed: {exc}')
            return
        self.refresh_sets(self._binding)

    def _on_context_menu(self, pos):
        """Right-click a USER set (the 'user:' rows) to delete it. Built-in and
        baked sets aren't user-owned, so they offer no delete."""
        item = self.sets_list.itemAt(pos)
        if item is None or not item.text().startswith('user:'):
            return
        name = item.text()[len('user:'):]
        menu = QtWidgets.QMenu(self)
        del_act = menu.addAction(f'Delete "{name}"')
        if menu.exec(self.sets_list.mapToGlobal(pos)) is not del_act:
            return
        confirm = QtWidgets.QMessageBox.question(
            self, 'Delete Selection Set',
            f'Delete the "{name}" selection set?',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        try:
            pose_sets.delete_user_set(name)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, 'Delete Selection Set', f'Delete failed: {exc}')
            return
        self.refresh_sets(self._binding)
