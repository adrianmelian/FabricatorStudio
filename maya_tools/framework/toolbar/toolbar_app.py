"""FabricatorStudio toolbar — lifecycle (spec 3.1).

Public entry points:
    toolbar_app.launch()   # build + dock per prefs
    toolbar_app.toggle()   # menu entry point: show if hidden/absent, else hide
    toolbar_app.teardown() # remove every trace from the Maya session

Two-file convention: this module owns logic/lifecycle; toolbar_ui owns widgets.
"""
from __future__ import annotations

import maya.cmds as cmds
from PySide6 import QtWidgets
from shiboken6 import isValid

from maya_tools.framework.toolbar import dock_targets, scene_listeners, toolbar_prefs
from maya_tools.framework.toolbar.toolbar_ui import OBJECT_NAME, FSToolbarStrip

_STRIP: FSToolbarStrip | None = None


def _existing_strips() -> list[QtWidgets.QWidget]:
    main = dock_targets.maya_main_window()
    if main is None:
        return []
    found = []
    for w in main.findChildren(QtWidgets.QWidget, OBJECT_NAME):
        try:
            if isValid(w):
                found.append(w)
        except Exception:
            continue
    return found


def _record_floating_pos() -> None:
    """Floating memory (C9): persist the window position while the strip is
    actually floating. last_achieved (not dock_mode) is the truth here - a
    degraded embed floats too, and its spot is just as worth keeping.
    dock_targets._restore_floating_pos is the read side."""
    strip = _STRIP
    if strip is None or not isValid(strip):
        return
    try:
        prefs = toolbar_prefs.load_prefs()
        if prefs.get("last_achieved") != "floating":
            return
        win = strip.window()
        prefs["floating_pos"] = [int(win.x()), int(win.y())]
        toolbar_prefs.save_prefs(prefs)
    except Exception:
        cmds.warning("FabricatorStudio toolbar: floating position save skipped")


def teardown() -> None:
    """The ONE cleanup path (used by toggle, relaunch, and the uninstaller).
    Order matters: the floating position is recorded FIRST (the strip must
    still exist to be measured), then scene listeners die (callbacks must
    not outlive the widgets they might touch), then orphan strips, then
    delete the workspaceControl — deleting the wc first would destroy a
    strip parented inside it. Consequence: teardown() runs at the start of
    every launch(), i.e. on every dock-mode switch — listener-backed
    state must read its truth from scene_listeners.is_registered(),
    never from a bare module flag."""
    global _STRIP
    _record_floating_pos()
    scene_listeners.stop_all()
    try:
        from maya_tools.framework.toolbar import ai_bridge
        ai_bridge.stop()
    except Exception:
        cmds.warning("FabricatorStudio toolbar: AI bridge stop skipped")
    for w in _existing_strips():
        try:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        except Exception:
            cmds.warning("FabricatorStudio toolbar: stale strip cleanup skipped one widget")
    dock_targets._delete_workspace_control()
    _STRIP = None


def _resolved_groups() -> list[dict]:
    """Load toolbar.json and resolve every command/state_query/popover
    string into a callable wrapped in the command runner. Items that fail
    to resolve are DROPPED with a warning (a broken entry must not kill
    the strip). Layout prefs (drag-to-reorder order + future hidden) are
    reconciled AFTER validation and BEFORE resolution - reorder/hide must
    never dodge the manifest gate."""
    from maya_tools.framework.toolbar import toolbar_commands, toolbar_manifest
    data = toolbar_manifest.load_manifest()
    toolbar_manifest.validate_manifest(data)
    layout = toolbar_prefs.load_prefs().get("layout")
    groups = []
    for group in toolbar_manifest.apply_layout_prefs(data["groups"], layout):
        items = []
        for item in group["items"]:
            try:
                items.append(_resolve_item(dict(item), toolbar_manifest,
                                           toolbar_commands))
            except Exception as exc:
                cmds.warning(f"FabricatorStudio toolbar: dropped item "
                             f"{item.get('id', '?')!r}: {exc}")
        groups.append({**group, "items": items})
    return groups


def _resolve_item(item, tm, tc):
    # A Cluster (Wave B) is a composite reorder-unit whose sub-items each
    # carry their own command/popover strings - resolve them recursively.
    if item.get("type") == "Cluster":
        item["items"] = [_resolve_item(dict(sub), tm, tc)
                         for sub in item.get("items", [])]
        return item
    # Undo/error label: title (popover buttons, item 4) or tooltip (plain
    # buttons) or the id as a last resort.
    label = item.get("title") or item.get("tooltip") or item["id"]
    if "command" in item and isinstance(item["command"], str):
        fn = tm.resolve_command(item["command"])
        if item["type"] == "ExpandingSlider":
            # Decision B15: the GESTURE owns the undo chunk; per-tick fires
            # are error-trapped but chunk-less. The widget invokes
            # gesture_begin at expand and gesture_end at collapse. A slider
            # may supply its OWN gesture_begin/gesture_end commands (idea A:
            # insert-joints captures the joint pair + manages the chunk
            # itself); otherwise the generic begin/end open+close the chunk.
            item["command"] = (lambda *a, _fn=fn, _label=label:
                               tc.run_in_gesture(_label, _fn, *a))
            gb, ge = item.get("gesture_begin"), item.get("gesture_end")
            if isinstance(gb, str) and isinstance(ge, str):
                gbf, gef = tm.resolve_command(gb), tm.resolve_command(ge)
                item["gesture_begin"] = (lambda _f=gbf, _label=label:
                                         tc.run_in_gesture(_label, _f))
                item["gesture_end"] = (lambda _f=gef, _label=label:
                                       tc.run_in_gesture(_label, _f))
            else:
                item["gesture_begin"] = (lambda _label=label:
                                         tc.begin_undo_gesture(_label))
                item["gesture_end"] = tc.end_undo_gesture
        elif item["type"] == "Toggle":
            # Toggle syncs its lit state from the command's bool return;
            # run_command returns None on error, so the widget falls back
            # to its state_query resync — trace verified, no special case.
            item["command"] = (lambda *a, _fn=fn, _label=label:
                               tc.run_command(_label, _fn, *a))
        else:
            item["command"] = (lambda *a, _fn=fn, _label=label:
                               tc.run_command(_label, _fn, *a))
    if "double_command" in item and isinstance(item["double_command"], str):
        # FabricatorButton's double-click open (floating), resolved + wrapped
        # like a plain command so it carries the same undo/error handling.
        dfn = tm.resolve_command(item["double_command"])
        item["double_command"] = (lambda *a, _fn=dfn, _label=label:
                                  tc.run_command(_label, _fn, *a))
    if "state_query" in item and isinstance(item["state_query"], str):
        item["state_query"] = tm.resolve_command(item["state_query"])
    if "can_open" in item and isinstance(item["can_open"], str):
        # Raw predicate (like state_query): no run_command wrap - it just
        # decides whether the popover opens (item 4).
        item["can_open"] = tm.resolve_command(item["can_open"])
    if "annotation" in item and isinstance(item["annotation"], str):
        # Custom hover-card source (2026-07-19): resolved raw like can_open —
        # it returns an Annotation, no undo chunk or error dialog wanted
        # (the controller already guards its getters).
        item["annotation"] = tm.resolve_command(item["annotation"])
    if "popover" in item and isinstance(item["popover"], str):
        item["popover"] = tm.resolve_command(item["popover"])
    if "menu_factory" in item and isinstance(item["menu_factory"], str):
        # A MenuButton's row source (2026-07-07): resolved to a callable like a
        # popover. No run_command wrap - the rows carry their own wrapped
        # handlers (each row's handler already routes through _run).
        item["menu_factory"] = tm.resolve_command(item["menu_factory"])
    if "options_popover" in item and isinstance(item["options_popover"], str):
        # A companion panel factory (idea B: the offset slider's axis+mode),
        # resolved to a callable exactly like a popover.
        item["options_popover"] = tm.resolve_command(item["options_popover"])
    if "menu" in item:
        entries = []
        for entry in item["menu"]:
            mfn = tm.resolve_command(entry["command"])
            entries.append({"label": entry["label"],
                            "command": (lambda _fn=mfn, _l=entry["label"]:
                                        tc.run_command(_l, _fn))})
        item["menu"] = entries
    return item


def refresh_fabricator_lit() -> None:
    """The Fabricator button no longer lights when a rig is present
    (Adrian, 2026-07-08: the rig-loaded border/glow is removed for a cleaner
    logo read). Kept as a no-op-shaped hook so the scene-change wiring and the
    Build/Unbuild post-action refresh callers stay valid; force UNLIT so any
    prior lit state clears."""
    strip = _STRIP
    if strip is None or not isValid(strip):
        return
    btn = strip.widget_by_id.get("fabricator")
    if btn is None:
        return
    btn.set_lit(False)


def restore() -> None:
    """Autostart entry (userSetup bootstrap): bring the toolbar back
    exactly as the user left it - and NOT back if they hid it."""
    if cmds.about(batch=True):
        return
    if toolbar_prefs.load_prefs().get("visible", True):
        launch()


def launch() -> None:
    global _STRIP
    # Quality review F2, 2026-07-06: capture whether the bridge was ALREADY
    # running BEFORE teardown() (below) unconditionally stops it. A bridge
    # started manually (Start pressed, autostart pref off) must survive an
    # internal relaunch (dock-mode switch, toggle, etc) - it must never
    # silently die just because the user moved the toolbar, with no Stop
    # ever pressed. Lazy import here too (mirrors every other ai_bridge
    # touch point in this module).
    try:
        from maya_tools.framework.toolbar import ai_bridge
        was_running = ai_bridge.is_running()
    except Exception:
        was_running = False
    teardown()
    prefs = toolbar_prefs.load_prefs()
    # Restart when EITHER it was already running before this teardown
    # (was_running - a manually-started bridge outliving our own relaunch)
    # OR the user's autostart pref says so (enabled_autostart - governs a
    # genuine cold Maya launch, where was_running is always False since
    # nothing has run yet this session). Either condition alone is enough;
    # neither implies the other.
    if was_running or (prefs.get("connect_ai") or {}).get("enabled_autostart"):
        try:
            from maya_tools.framework.toolbar import ai_bridge
            ai_bridge.start()
        except Exception:
            cmds.warning("FabricatorStudio toolbar: AI bridge (re)start failed "
                         "(port may be in use)")
    mode = dock_targets.normalize_mode(prefs["dock_mode"])
    orientation = "vertical" if mode in ("left", "right") else "horizontal"
    _STRIP = FSToolbarStrip(orientation=orientation, groups=_resolved_groups())
    achieved = dock_targets.dock(_STRIP, mode)
    prefs["dock_mode"] = mode            # the user's wish, normalized; always retried
    prefs["last_achieved"] = achieved    # diagnostics (spec 3.5)
    prefs["visible"] = True
    toolbar_prefs.save_prefs(prefs)
    refresh_fabricator_lit()
    scene_listeners.on_scene_change(refresh_fabricator_lit)


def toggle() -> None:
    strips = _existing_strips()
    visible = any(w.isVisible() for w in strips)
    prefs = toolbar_prefs.load_prefs()
    if visible:
        # Hidden toolbar = listeners OFF (decision C6): zero background
        # cost while hidden; the paint Toggle reads OFF automatically via
        # scene_listeners.is_registered().
        scene_listeners.stop_all()
        for w in strips:
            w.hide()
        dock_targets.set_host_visible(False)
        prefs["visible"] = False
        toolbar_prefs.save_prefs(prefs)
        return
    if strips:
        for w in strips:
            w.show()
        dock_targets.set_host_visible(True)
        prefs["visible"] = True
        toolbar_prefs.save_prefs(prefs)
        # Re-show (C6): re-register the context events, re-probe, and let
        # every stateful widget resync (the drained paint listener reads
        # OFF via the registry-backed state query).
        scene_listeners.on_scene_change(refresh_fabricator_lit)
        refresh_fabricator_lit()
        for w in strips:
            for child in getattr(w, "widget_instances", []):
                if hasattr(child, "refresh_state"):
                    child.refresh_state()
        return
    launch()


def is_visible() -> bool:
    """True when any live toolbar strip is currently shown. Public seam
    for the first-run tour (stop 2 advances when the user actually opens
    the toolbar) — polling this is deliberately path-agnostic: toggle(),
    launch(), and restore() all end in a visible strip. Never raises
    (polled from a QTimer; headless/batch reads False)."""
    try:
        return any(w.isVisible() for w in _existing_strips())
    except Exception:
        return False


def widget_for(item_id: str):
    """The live strip widget for a toolbar.json item id (e.g. 'settings',
    'fabricator'), or None when there is no strip / no such id. Public
    seam for the first-run tour's pointer cards; callers must handle
    None (a hidden toolbar has no widgets to point at). Never raises."""
    try:
        strip = _STRIP
        if strip is None or not isValid(strip):
            return None
        return strip.widget_by_id.get(item_id)
    except Exception:
        return None


def set_dock_mode(mode: str) -> None:
    """Move the toolbar: top | left | right | bottom | floating.
    Legacy Phase A names (timeline_bottom, shelf_bottom, viewport_top,
    side_left, side_right) are accepted and normalized."""
    _record_floating_pos()   # explicit (C9); launch()->teardown() would also catch it
    prefs = toolbar_prefs.load_prefs()
    prefs["dock_mode"] = mode
    toolbar_prefs.save_prefs(prefs)
    launch()


def active_project() -> str:
    """The remembered project-config folder name (Wave B2 chooser scaffold)."""
    return toolbar_prefs.load_prefs().get("active_project", "")


def set_active_project(name: str) -> None:
    prefs = toolbar_prefs.load_prefs()
    prefs["active_project"] = name or ""
    toolbar_prefs.save_prefs(prefs)
    # A window-side activation (Project Setup's Apply & Activate) must reach
    # a live dropdown immediately - the re-show resync loop only runs on the
    # next relaunch. Harmless on chooser-initiated commits (reload lands on
    # the value just saved).
    for strip in _existing_strips():
        for chooser in strip.findChildren(QtWidgets.QComboBox,
                                          "fs_project_chooser"):
            if hasattr(chooser, "refresh_state"):
                try:
                    chooser.refresh_state()
                except Exception:
                    cmds.warning("FabricatorStudio toolbar: project chooser "
                                 "resync skipped")
