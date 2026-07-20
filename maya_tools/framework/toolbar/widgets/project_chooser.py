"""ProjectChooser - a minimal active-project dropdown (Wave B2, 2026-07-03).

Lists the project folders under the user project-configs dir. A confirm gates
every switch. A committed pick runs the SAME full activation as Project
Setup's Apply & Activate (project_setup_app.apply_and_activate: session.json
applied via maya_startup.run + optionVar + toolbar prefs' active_project), so
the two entry points can never diverge. Window-side activations flow back in
through refresh_state() (toolbar_app.set_active_project pings live choosers).
The exporter is the one deliberate exception: it resolves its config per-scene
and reads neither store (spec sec 4.3/11).

App/UI split holds: prefs + project listing import lazily inside handlers, so
constructing the widget stays Qt-only (offscreen-testable)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEasingCurve, Qt, QVariantAnimation
from PySide6.QtWidgets import QComboBox

from maya_tools.framework.toolbar import dpi

_PLACEHOLDER = "(no project)"


class ProjectChooser(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        from maya_tools.framework.toolbar.widgets import renamer_inline as ri
        self.setObjectName("fs_project_chooser")
        # Match the sleeping renamer's FIELD width (not toggle+field): the combo
        # is one solid box, so lining it up with the renamer's input box makes
        # the two left-zone inputs read the same size (Adrian, 2026-07-03).
        # Expand/contract (Adrian, 2026-07-19): clicked, the combo widens to
        # the renamer's expanded width (long project names read whole) and the
        # popup drops at that width; clicking away contracts it again. Same
        # animation feel as RenamerInline (duration + easing shared).
        self._collapsed_w = dpi.scaled(ri.COLLAPSED_W_HASH)
        self._expanded_w = dpi.scaled(ri.EXPANDED_W)
        self._anim_ms = ri._EXPAND_MS
        self.setFixedWidth(self._collapsed_w)
        self._anim: Optional[QVariantAnimation] = None
        self._opening = False     # expand-then-popup handshake in flight
        self.setToolTip("Active project (stored in toolbar prefs)")
        self._names: list[str] = []
        self._current = ""
        self._reload()
        # activated fires ONLY on a user pick, not on programmatic index sets,
        # so the confirm/revert below can never recurse into itself.
        self.activated.connect(self._on_activated)

    # -- expand / contract ----------------------------------------------------
    def showPopup(self) -> None:
        """Expand FIRST, then drop the popup once the animation lands, so
        the popup opens at the expanded width instead of the sleeping one."""
        if self.width() < self._expanded_w and not self._opening:
            self._opening = True
            self._animate_width(self._expanded_w, on_done=self._open_popup_now)
            return
        super().showPopup()

    def _open_popup_now(self) -> None:
        # QVariantAnimation.stop() also emits finished — the _opening flag
        # (cleared by a contract) keeps a cancelled expand from popping.
        if not self._opening:
            return
        self._opening = False
        super().showPopup()

    def focusOutEvent(self, event) -> None:
        """Click-away contracts. PopupFocusReason is the popup itself taking
        focus — that one is not a click-away, the combo is in use."""
        super().focusOutEvent(event)
        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self._opening = False
            self._animate_width(self._collapsed_w)

    def _animate_width(self, target: int, on_done=None) -> None:
        if self._anim is not None:
            self._anim.stop()
        anim = QVariantAnimation(self)
        anim.setDuration(self._anim_ms)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.setStartValue(self.width())
        anim.setEndValue(target)
        anim.valueChanged.connect(lambda v: self.setFixedWidth(int(v)))
        if on_done is not None:
            anim.finished.connect(on_done)
        anim.start()
        self._anim = anim

    def _reload(self) -> None:
        names, active = self._load_state()
        self._names = names
        self.blockSignals(True)
        self.clear()
        if names:
            self.addItems(names)
            idx = names.index(active) if active in names else 0
            self.setCurrentIndex(idx)
            self._current = names[idx]
        else:
            self.addItem(_PLACEHOLDER)
            self.setCurrentIndex(0)
            self._current = ""
        self.blockSignals(False)

    @staticmethod
    def _load_state():
        """(project names, active name) - defensive so offscreen construction
        (no project-configs dir, no prefs) yields the empty placeholder."""
        try:
            from maya_tools.export import export_core
            from maya_tools.framework.toolbar import toolbar_app
            names = [p.name for p in export_core.iter_project_dirs()]
            return names, toolbar_app.active_project()
        except Exception:
            return [], ""

    def _on_activated(self, index: int) -> None:
        if not self._names:
            return
        name = self.itemText(index)
        if name == self._current:
            return
        if not self._confirm(name):
            revert = self._names.index(self._current) if self._current in self._names else 0
            self.setCurrentIndex(revert)
            return
        self._commit(name)

    def _confirm(self, name: str) -> bool:
        try:
            from maya import cmds
            ans = cmds.confirmDialog(
                title="Switch Project",
                message=f"Set the active project to '{name}'?",
                button=["Switch", "Cancel"], defaultButton="Switch",
                cancelButton="Cancel", dismissString="Cancel")
            return ans == "Switch"
        except Exception:
            return True          # no Maya UI (offscreen/tests): treat as confirmed

    def refresh_state(self) -> None:
        """Resync from prefs - called by the toolbar re-show loop and by
        toolbar_app.set_active_project when a window-side activation lands."""
        self._reload()

    def _commit(self, name: str) -> None:
        self._current = name
        try:
            from maya_tools.framework import project_setup_app
            project_setup_app.apply_and_activate(name)
        except Exception:
            # No live Maya (offscreen/tests): still remember the pick.
            try:
                from maya_tools.framework.toolbar import toolbar_app
                toolbar_app.set_active_project(name)
            except Exception:
                pass
        try:
            from maya_tools.utils.qt import toast
            toast.show_toast(f"Project changed to '{name}'")
        except Exception:
            pass
