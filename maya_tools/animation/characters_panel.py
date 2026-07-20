"""CharactersPanel — the left column shared by the Pose and Anim libraries
(Adrian, 2026-07-08: one widget so both tabs render identically).

A character DROPDOWN (namespace-qualified live bindings + offline rig_labels)
over the shared SELECTION SETS list, plus a stubbed + Add Selection Set button.
Parameterized by the library's list_rigs() so each tab lists its own saved rigs
while sharing the widget. Pure UI; set logic is headless in pose_library.sets
(via SelectionSetsWidget).
"""
from __future__ import annotations

from typing import Callable

from PySide6 import QtCore, QtWidgets

from maya_tools.animation.selection_sets_widget import SelectionSetsWidget
from maya_tools.utils.maya import rig_binding
from maya_tools.utils.qt.mindmeld import mindmeld_style


def _display_label_for_binding(binding: str) -> str:
    """Friendly combo label for a live binding: prefer the namespace
    (referenced anim/pose scenes), else the rig_label."""
    ns = binding.split(':', 1)[0] if ':' in binding else ''
    return ns or (rig_binding.get_rig_label(binding) or 'unnamed')


class CharactersPanel(QtWidgets.QWidget):
    # (binding_or_empty, rig_label). binding='' means offline browse.
    character_selected = QtCore.Signal(str, str)
    # rig_label on combo right-click -> Recapture all (opt-in per library).
    recapture_all_requested = QtCore.Signal(str)
    # RAW clicked-set text -> the hosting window selects that set's ctrls.
    set_selected = QtCore.Signal(str)
    add_set_requested = QtCore.Signal()

    def __init__(self, list_rigs: Callable[[], list],
                 enable_recapture_all: bool = True, parent=None):
        super().__init__(parent)
        self._list_rigs = list_rigs
        self._enable_recapture_all = enable_recapture_all

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(mindmeld_style.caps_label('// CHARACTER'))
        self.combo = QtWidgets.QComboBox()
        self.combo.currentIndexChanged.connect(self._emit_selection)
        if enable_recapture_all:
            # Recapture-all is the combo's right-click gesture (Pose).
            self.combo.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.combo.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.combo)

        # Selection sets — the shared widget, below the character combo. The
        # combo is fixed-height, so the sets list gets all the stretch (no
        # 50/50 fight a second stretch widget would cause).
        self.sets_widget = SelectionSetsWidget()
        self.sets_widget.set_selected.connect(self.set_selected)
        self.sets_widget.add_set_requested.connect(self.add_set_requested)
        layout.addWidget(self.sets_widget, stretch=1)

        self.refresh_btn = mindmeld_style.button('Refresh', kind='ghost')
        self.refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(self.refresh_btn)

    def refresh(self):
        prev_binding, _prev_label = self._current_entry()

        # Live bindings — display by namespace (or rig_label fallback). Each
        # entry's userData carries (binding, rig_label).
        seen_labels = set()
        try:
            live = rig_binding.find_live_bindings()
        except Exception:
            live = []
        live_entries = []
        for b in live:
            label = rig_binding.get_rig_label(b) or 'unnamed'
            display = _display_label_for_binding(b)
            live_entries.append((display, b, label))
            seen_labels.add(label)
        # Offline rig_labels (have a saved folder but no live binding).
        try:
            offline_labels = [r for r in self._list_rigs()
                              if r not in seen_labels]
        except Exception:
            offline_labels = []

        live_entries.sort(key=lambda e: e[0].lower())
        offline_entries = sorted(
            [(f'{lbl}  (offline)', '', lbl) for lbl in offline_labels],
            key=lambda e: e[0].lower(),
        )

        # Rebuild silently, restore by binding (else index 0), then emit
        # exactly once — combo rebuilds otherwise fire per-addItem.
        self.combo.blockSignals(True)
        self.combo.clear()
        for display, binding, rig_label in live_entries + offline_entries:
            self.combo.addItem(display, (binding, rig_label))
        idx = 0
        if prev_binding:
            for i in range(self.combo.count()):
                data = self.combo.itemData(i) or ('', '')
                if data[0] == prev_binding:
                    idx = i
                    break
        if self.combo.count():
            self.combo.setCurrentIndex(idx)
        self.combo.blockSignals(False)
        if self.combo.count():
            self._emit_selection()   # also refreshes the sets list
        else:
            self.refresh_sets()      # empty scene: still seed the sets list

    def refresh_sets(self):
        """Rebuild the sets list for the current character (delegated to the
        shared SelectionSetsWidget)."""
        binding, _label = self._current_entry()
        self.sets_widget.refresh_sets(binding)

    def _current_entry(self) -> tuple:
        data = self.combo.currentData() or ('', '')
        return tuple(data)

    def select_binding(self, binding: str) -> None:
        """Programmatically select the combo entry whose binding matches."""
        for i in range(self.combo.count()):
            data = self.combo.itemData(i) or ('', '')
            if data[0] == binding:
                self.combo.setCurrentIndex(i)
                return

    def select_rig_label(self, rig_label: str) -> None:
        """Select the combo entry whose rig_label matches (live or offline)."""
        for i in range(self.combo.count()):
            data = self.combo.itemData(i) or ('', '')
            if len(data) > 1 and data[1] == rig_label:
                self.combo.setCurrentIndex(i)
                return

    def _emit_selection(self, *_):
        binding, rig_label = self._current_entry()
        if rig_label:
            self.character_selected.emit(binding, rig_label)
            # Baked sets are per rig — the sets list follows the character.
            self.refresh_sets()

    def _on_context_menu(self, pos):
        binding, rig_label = self._current_entry()
        if not rig_label:
            return
        menu = QtWidgets.QMenu(self)
        act_recapture = menu.addAction('Recapture all thumbnails')
        action = menu.exec(self.combo.mapToGlobal(pos))
        if action is act_recapture:
            self.recapture_all_requested.emit(rig_label)
