"""Show / Hide tools manager (2026-07-03): a checklist of every strip tool.

Unchecking a tool writes its id into that group's layout.hidden list - which
apply_layout_prefs already filters out - and relaunches the strip, so a tool
vanishes or returns live. Built on the drag-reorder foundation's prefs schema
(toolbar_prefs.layout.groups[gid].hidden), which shipped resolver-ready and
UI-less; this is that UI.

The panel is a standalone tool window parented to Maya's main window, so the
relaunch it triggers never tears it down. Module import stays Qt-only (Maya +
toolbar_app import lazily inside handlers), so it is offscreen-testable."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QScrollArea, QVBoxLayout, QWidget)

from maya_tools.framework.toolbar import toolbar_prefs
from maya_tools.utils.qt.mindmeld import mindmeld_style


def _item_label(item: dict) -> str:
    return item.get("title") or item.get("tooltip") or item.get("id", "?")


def set_hidden(gid: str, item_id: str, hidden: bool,
               path: Path | None = None) -> None:
    """Add/remove item_id in group gid's layout.hidden list (atomic save).
    Order is preserved verbatim; a hand-edited malformed layout is healed,
    never raised on (this runs from a checkbox slot)."""
    prefs = toolbar_prefs.load_prefs(path=path)
    layout = prefs.get("layout")
    if not isinstance(layout, dict):
        layout = {"version": 1, "groups": {}}
    if not isinstance(layout.get("groups"), dict):
        layout["groups"] = {}
    groups = layout["groups"]
    entry = groups.get(gid)
    if not isinstance(entry, dict):
        entry = {"order": [], "hidden": []}
    hid = entry.get("hidden")
    hid = [h for h in hid if h != item_id] if isinstance(hid, list) else []
    if hidden:
        hid.append(item_id)
    entry["hidden"] = hid
    entry.setdefault("order", [])
    groups[gid] = entry
    prefs["layout"] = layout
    toolbar_prefs.save_prefs(prefs, path=path)


def _hidden_by_group() -> dict:
    layout = toolbar_prefs.load_prefs().get("layout") or {}
    groups = layout.get("groups") if isinstance(layout, dict) else {}
    out = {}
    if isinstance(groups, dict):
        for gid, entry in groups.items():
            hid = entry.get("hidden") if isinstance(entry, dict) else None
            out[gid] = set(hid) if isinstance(hid, list) else set()
    return out


class ToolVisibilityPanel(QWidget):
    """A checkbox per tool (checked = visible), grouped by strip group. Seams:
    `groups` (manifest groups), `hidden_by_group` (gid -> set of hidden ids)
    and `on_change(gid, item_id, hidden)` all default to the live sources, so
    tests drive it without Maya or the real prefs file."""

    def __init__(self, groups: Optional[list] = None,
                 hidden_by_group: Optional[dict] = None,
                 on_change: Optional[Callable[[str, str, bool], None]] = None,
                 parent=None):
        super().__init__(parent)
        mindmeld_style.apply(self)
        self.setWindowFlags(Qt.Window)
        self.setWindowTitle("Show / Hide Tools")
        if groups is None:
            from maya_tools.framework.toolbar import toolbar_manifest
            groups = toolbar_manifest.load_manifest()["groups"]
        self._hidden = hidden_by_group if hidden_by_group is not None else _hidden_by_group()
        self._on_change = on_change or self._default_apply
        self.boxes = {}                       # (gid, item_id) -> QCheckBox

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(6)
        outer.addWidget(mindmeld_style.brand_label("Bridge"))
        outer.addWidget(mindmeld_style.caps_label("// tools · show + hide"))
        outer.addWidget(mindmeld_style.horizontal_rule())

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(6)
        for group in groups:
            gid = group.get("id", "?")
            body_lay.addWidget(mindmeld_style.field_label(group.get("label", gid)))
            hidden = self._hidden.get(gid, set())
            for item in group.get("items", []):
                iid = item.get("id", "?")
                box = QCheckBox(_item_label(item))
                box.setChecked(iid not in hidden)
                box.toggled.connect(
                    lambda on, g=gid, i=iid: self._on_change(g, i, not on))
                body_lay.addWidget(box)
                self.boxes[(gid, iid)] = box
        body_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

    @staticmethod
    def _default_apply(gid: str, item_id: str, hidden: bool) -> None:
        set_hidden(gid, item_id, hidden)
        try:
            from maya_tools.framework.toolbar import toolbar_app
            toolbar_app.launch()              # rebuild so the change shows live
        except Exception:
            pass


_win = None


def show_tool_visibility():
    """Open (or re-open) the manager, parented to Maya's main window."""
    global _win
    try:
        _win.close()
        _win.deleteLater()
    except Exception:
        pass
    parent = None
    try:
        from maya_tools.utils.maya.gui import get_maya_window
        parent = get_maya_window()
    except Exception:
        pass
    _win = ToolVisibilityPanel(parent=parent)
    _win.setWindowFlags(Qt.Window)
    from maya_tools.framework.toolbar import dpi
    _win.resize(dpi.scaled(300), dpi.scaled(420))
    _win.show()
    return _win
