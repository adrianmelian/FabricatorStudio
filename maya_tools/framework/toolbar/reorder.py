"""Edit-mode drag-to-reorder for the FabricatorStudio strip (Phase D).

Entered from the Settings menu's "Customize Layout" row; left via Esc OR by
clicking anywhere OUTSIDE the strip (CP-D wave-1 verdict: Esc alone made
it too easy to walk away with edit mode still armed). One
ReorderController per strip, parented to it (dies with it). While
active it:
  * suppresses popover hover-open (popover_button.set_hover_suppressed) -
    the 350ms timer must never fire mid-edit,
  * closes every open popover, PINNED ones included, via each owner
    button's close_popover() (the existing close path),
  * flags the strip with the `edit_mode` QSS dynamic property (styled in
    mindmeld.qss, TOOLBAR strip section: accent frame + dimmed buttons),
  * installs itself as an event filter on every item widget and its
    SAME-WINDOW children (composite widgets drag as one unit; popover
    frames are Qt.Tool TOP-LEVEL windows, so a just-closed frame with a
    pending deleteLater never lands in the watch list) and CONSUMES all
    mouse events plus every item key except Esc - a tool can never fire
    mid-edit (the Smoosh click-command is a real skin op, and Space or
    Return on a focused button clicks it),
  * live-swaps the dragged widget inside its group box (the layout itself
    is the drop indicator; axis x when horizontal, y when vertical),
  * persists the group's order to toolbar_prefs on every drop that
    actually moved something (atomic save; schema {"version": 1,
    "groups": {gid: {"order": [...], "hidden": [...]}}}); a plain
    consumed click writes nothing.

v1 scope (locked 2026-07-02): reorder is WITHIN-GROUP only. Module import
stays Qt-only - toolbar_app (Maya-only) is imported lazily inside
enter_edit_mode() - so this file is offscreen-testable.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget

from maya_tools.framework.toolbar import toolbar_prefs
from maya_tools.framework.toolbar.widgets import popover_button

# Everything the filter swallows while edit mode is on. ContextMenu and
# DblClick ride along with the locked press/move/release set: right-click
# menus (Fabricator quick Build/Unbuild) are tools too and must not fire.
_MOUSE_EVENTS = (QEvent.MouseButtonPress, QEvent.MouseMove,
                 QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick,
                 QEvent.ContextMenu)


class ReorderController(QObject):
    """Owns one strip's edit mode. prefs_path is a test seam - None means
    the real prefs file (toolbar_prefs default path)."""

    def __init__(self, strip, prefs_path: Path | None = None):
        super().__init__(strip)
        self._strip = strip
        self._prefs_path = prefs_path
        self._active = False
        self._watched: list[QWidget] = []
        self._drag_item: QWidget | None = None
        self._prev_focus_policy = strip.focusPolicy()   # restored by exit()
        # Teardown safety: a dock-mode switch mid-edit rebuilds the strip;
        # the module-global hover suppression must not outlive it.
        strip.destroyed.connect(
            lambda *_: popover_button.set_hover_suppressed(False))

    # -- lifecycle ----------------------------------------------------------

    def is_active(self) -> bool:
        return self._active

    def enter(self) -> None:
        if self._active:
            return
        self._active = True
        popover_button.set_hover_suppressed(True)
        for w in self._strip.widget_instances:
            if hasattr(w, "close_popover"):
                w.close_popover()        # pinned panels close here too
        self._watched = []
        for w in self._strip.widget_instances:
            # SAME-WINDOW children only: the popover frames closed just
            # above are Qt.Tool TOP-LEVEL windows still parented to their
            # button while deleteLater is pending - watching them (or
            # their children) would hand exit() dead shiboken wrappers.
            for target in [w] + [c for c in w.findChildren(QWidget)
                                 if not c.isWindow()
                                 and c.window() is w.window()]:
                target.installEventFilter(self)
                self._watched.append(target)
        self._strip.installEventFilter(self)   # Esc reaches the filter
        app = QApplication.instance()
        if app is not None:
            # App-wide watch: ONLY for the click-outside exit and global
            # Esc - inside-the-strip consume logic stays on the per-widget
            # filters (belt and suspenders; the app phase runs first and
            # consuming there stops the object phase, so nothing double-
            # fires). Destroying the controller auto-detaches this.
            app.installEventFilter(self)
        self._set_edit_property(True)
        self._prev_focus_policy = self._strip.focusPolicy()
        self._strip.setFocusPolicy(Qt.StrongFocus)
        self._strip.setFocus(Qt.OtherFocusReason)

    def exit(self) -> None:
        if not self._active:
            return
        self._active = False
        self._drag_item = None
        # User-visible state resets FIRST (teardown safety): if a watched
        # widget died since enter(), suppression, the QSS dress and the
        # focus policy must not stay stuck behind a raise.
        popover_button.set_hover_suppressed(False)
        self._set_edit_property(False)
        self._strip.setFocusPolicy(self._prev_focus_policy)
        if self._strip.hasFocus():
            self._strip.clearFocus()
        for target in self._watched:
            try:
                target.removeEventFilter(self)
            except RuntimeError:
                pass     # dead shiboken wrapper (deleteLater'd mid-edit)
        self._watched = []
        self._strip.removeEventFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def _set_edit_property(self, on: bool) -> None:
        """Flip the strip's edit_mode QSS property and repolish the tree -
        descendant rules ([edit_mode="true"] QPushButton) only re-evaluate
        on the widgets they style, so every child repolishes."""
        strip = self._strip
        strip.setProperty("edit_mode", on)
        for w in [strip] + strip.findChildren(QWidget):
            w.style().unpolish(w)
            w.style().polish(w)
        strip.update()

    # -- event filter ---------------------------------------------------------

    def eventFilter(self, obj, event):
        if not self._active:
            return False
        t = event.type()
        if t == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.exit()            # global: the app-level watch means Esc
            return True            # works from ANY focus, viewport included
        try:
            inside = (isinstance(obj, QWidget)
                      and (obj is self._strip
                           or self._strip.isAncestorOf(obj)))
        except RuntimeError:
            self._active = False   # strip died mid-edit under the filter
            return False
        if not inside:
            # Click-outside exit (CP-D wave-1 verdict): a press anywhere
            # that is not the strip ends edit mode. NOT consumed - the
            # click still lands where the user aimed. QWidget only: Qt
            # delivers every press to the top-level QWidgetWindow (a
            # QWindow) FIRST, and the strip's own window must not read
            # as an outside click before the real target dispatches.
            if t == QEvent.MouseButtonPress and isinstance(obj, QWidget):
                self.exit()
            return False
        if (t in (QEvent.KeyPress, QEvent.KeyRelease)
                and obj is not self._strip
                and event.key() != Qt.Key_Escape):
            # Keys fire tools too: Tab moves focus onto a button and
            # Space/Return CLICKS it. Watched items swallow every key
            # except Esc (handled above - Esc keeps exiting).
            return True
        if t not in _MOUSE_EVENTS:
            return False
        # From here down EVERYTHING is consumed (return True): edit mode
        # means no tool can fire (the Smoosh click-command is a real skin
        # op) and no context menu can open.
        item = self._item_of(obj)
        if t == QEvent.MouseButtonPress:
            # a consumed click can still move Qt focus around: re-assert
            # the strip so Esc keeps exiting after every click
            self._strip.setFocus(Qt.OtherFocusReason)
        if (t == QEvent.MouseButtonPress and item is not None
                and event.button() == Qt.LeftButton):
            self._drag_item = item
            self._drag_moved = False
            return True
        if (t == QEvent.MouseMove and self._drag_item is not None
                and item is self._drag_item):
            self._drag_to(event.globalPosition().toPoint())
            return True
        if (t == QEvent.MouseButtonRelease and self._drag_item is not None
                and item is self._drag_item):
            self._drag_item = None
            if self._drag_moved:
                # Only a real move persists: a plain consumed click must
                # not write an order entry that would pin this group
                # against future manifest reorderings.
                self._persist_group(item)
            self._drag_moved = False
            return True
        return True

    def _item_of(self, obj) -> QWidget | None:
        """Map a watched object (an item widget OR any of its children -
        composite widgets drag as one unit) back to its item widget."""
        w = obj if isinstance(obj, QWidget) else None
        while w is not None:
            if w in self._strip.id_by_widget:
                return w
            w = w.parentWidget()
        return None

    def _drag_to(self, global_pos) -> None:
        """Hit-test the cursor against sibling midpoints WITHIN THE SAME
        GROUP BOX and live-swap: the layout itself is the drop indicator.
        Axis follows strip orientation (x horizontal, y vertical). The
        box's edges bound the drag - within-group only, by construction."""
        w = self._drag_item
        box = w.parentWidget()
        lay = box.layout()
        if lay is None:
            return
        idx = lay.indexOf(w)
        if idx < 0:
            return
        horizontal = self._strip.orientation == "horizontal"
        local = box.mapFromGlobal(global_pos)
        pos = local.x() if horizontal else local.y()
        target = idx
        for i in range(lay.count()):
            sib = lay.itemAt(i).widget()
            if sib is None or sib is w:
                continue
            center = sib.geometry().center()
            mid = center.x() if horizontal else center.y()
            if i < idx and pos < mid:
                target = min(target, i)
            elif i > idx and pos > mid:
                target = max(target, i)
        if target != idx:
            lay.removeWidget(w)
            lay.insertWidget(target, w)
            lay.activate()   # fresh geometry for the NEXT move's hit-test
            self._drag_moved = True   # real move: this drop will persist

    def _persist_group(self, w) -> None:
        """Drop (release after a real move - the caller checks the dirty
        flag): write this group's current visual order into the layout
        prefs (atomic save via toolbar_prefs). Existing hidden lists are
        preserved verbatim - this v1 UI never writes hidden."""
        box = w.parentWidget()
        lay = box.layout()
        gid = box.property("fs_group_id")
        if not gid or lay is None:
            return
        order = []
        for i in range(lay.count()):
            sib = lay.itemAt(i).widget()
            if sib is not None and sib in self._strip.id_by_widget:
                order.append(self._strip.id_by_widget[sib])
        prefs = toolbar_prefs.load_prefs(path=self._prefs_path)
        layout_prefs = prefs.get("layout")
        if not isinstance(layout_prefs, dict):
            layout_prefs = {"version": 1, "groups": {}}
        if not isinstance(layout_prefs.get("groups"), dict):
            # a hand-edited non-dict "groups" must not raise inside the
            # mouse-release handler
            layout_prefs["groups"] = {}
        groups = layout_prefs["groups"]
        entry = groups.get(gid)
        if not isinstance(entry, dict):
            entry = {"order": [], "hidden": []}
        entry["order"] = order
        entry.setdefault("hidden", [])
        groups[gid] = entry
        prefs["layout"] = layout_prefs
        toolbar_prefs.save_prefs(prefs, path=self._prefs_path)


# -- module-level entry (SettingsPopover / console) ---------------------------

_CONTROLLER: ReorderController | None = None


def enter_edit_mode(strip=None) -> ReorderController | None:
    """Enter edit mode on the given strip (default: the live toolbar_app
    strip). Reuses one controller per strip INSTANCE - a rebuilt strip
    (new identity) gets a fresh controller; enter() is idempotent."""
    global _CONTROLLER
    if strip is None:
        from maya_tools.framework.toolbar import toolbar_app   # Maya-only, lazy
        strip = toolbar_app._STRIP
    if strip is None:
        return None
    if _CONTROLLER is None or _CONTROLLER._strip is not strip:
        _CONTROLLER = ReorderController(strip)
    _CONTROLLER.enter()
    return _CONTROLLER


def exit_edit_mode() -> None:
    """Symmetry hook for console use; Esc / click-outside are the in-UI
    exits."""
    if _CONTROLLER is None:
        return
    try:
        _CONTROLLER.exit()
    except RuntimeError:
        pass          # strip already deleted with its dock host
