"""Toolbar manifest: load, validate, resolve (spec 3.3).

Pure Python on purpose — no Maya, no Qt — so the manifest layer is fully
offscreen-testable and the packaging gates can validate toolbar.json
without a Maya session. Command strings are the spec's lazy form
"pkg.mod:attr.chain"; resolution imports on first use only.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

MANIFEST_FILENAME = "toolbar.json"

WIDGET_TYPES = {
    "IconButton":      {"required": ("command",),        "optional": ("menu",)},
    "Toggle":          {"required": ("command", "state_query"), "optional": ("menu", "icon_on")},
    "ExpandingSlider": {"required": ("command", "min", "max", "default"),
                        "optional": ("live", "reset_on_apply", "center_return",
                                     "options_popover", "handle_label",
                                     "handle_color", "notches", "range_inputs",
                                     "gesture_begin", "gesture_end")},
    "PopoverButton":   {"required": ("popover",),         "optional": ("trigger", "command", "can_open", "menu")},
    # MenuButton (2026-07-07): a native QMenu whose rows come from
    # menu_factory() (pkg.mod:attr -> [(label, tooltip, handler), ...]).
    # 2026-07-10: the menu moved to RIGHT-click; the optional command fires
    # on left-click (Skeleton/Skin Tools open their IO windows).
    "MenuButton":      {"required": ("menu_factory",),    "optional": ("command",)},
    "WindowLauncher":  {"required": ("command",),         "optional": ("menu",)},
    # FabricatorButton (2026-07-08): click -> command (docked open), double
    # click -> double_command (floating open), right-click -> menu_factory
    # (Build/Unbuild rows), hover -> annotation.
    "FabricatorButton": {"required": ("command",),
                         "optional": ("double_command", "menu_factory")},
    # RenamerInline lost its popover 2026-07-05 — the strip IS the whole
    # renamer (four modes, mode menu on the ## button).
    "RenamerInline":   {"required": (),                   "optional": ()},
    "ProjectChooser":  {"required": (),                   "optional": ()},
}
# glyph is the text fallback; icon (optional, all types) is an icons/ png
# filename per decision B25. icon_on (Toggle only) is the lit-state icon
# variant - the widget swaps icon/icon_on as state flips (Phase D).
# PopoverButton's optional command = a click
# action alongside a hover popover (Smoosh: click applies, hover tunes).
_COMMON_REQUIRED = ("type", "id", "glyph")


def manifest_path() -> Path:
    return Path(__file__).resolve().parent / MANIFEST_FILENAME


def load_manifest(path: Path | None = None) -> dict:
    p = Path(path) if path else manifest_path()
    return json.loads(p.read_text(encoding="utf-8"))


def validate_manifest(data: dict) -> None:
    """Raise ValueError naming the offending group/item/key; return None if sane."""
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        raise ValueError("manifest root must be a dict with a 'groups' list")
    seen_ids: set[str] = set()
    seen_group_ids: set[str] = set()
    for group in data["groups"]:
        if not isinstance(group, dict):
            raise ValueError(f"group entries must be dicts, got {type(group).__name__}")
        gid = group.get("id", "<missing group id>")
        if gid in seen_group_ids:
            raise ValueError(f"duplicate group id {gid!r}")
        seen_group_ids.add(gid)
        if not isinstance(group.get("items"), list):
            raise ValueError(f"group {gid!r}: 'items' must be a list")
        for item in group["items"]:
            _validate_item(item, gid, seen_ids, nested=False)


def _validate_item(item: dict, gid: str, seen_ids: set, nested: bool) -> None:
    """Validate one manifest item. A Cluster (2026-07-03, Wave B) is a
    composite that moves as a single reorder unit and carries 2+ sub-items;
    its sub-items are validated recursively. Clusters cannot nest."""
    iid = item.get("id", "<missing item id>")
    if "type" not in item:
        raise ValueError(f"group {gid!r} item {iid!r}: missing 'type'")
    if "id" not in item:
        raise ValueError(f"group {gid!r} item {iid!r}: missing 'id'")
    if item["id"] in seen_ids:
        raise ValueError(f"duplicate item id {item['id']!r}")
    seen_ids.add(item["id"])
    wtype = item["type"]

    if wtype == "Cluster":
        if nested:
            raise ValueError(f"group {gid!r} cluster {iid!r}: clusters cannot nest")
        subs = item.get("items")
        if not isinstance(subs, list) or not subs:
            raise ValueError(
                f"group {gid!r} cluster {iid!r}: 'items' must be a non-empty list")
        allowed = {"type", "id", "items", "label", "title"}
        for key in item:
            if key not in allowed:
                raise ValueError(
                    f"group {gid!r} cluster {iid!r}: unknown key {key!r} "
                    f"(allowed: {sorted(allowed)})")
        for sub in subs:
            _validate_item(sub, gid, seen_ids, nested=True)
        return

    for key in _COMMON_REQUIRED:
        if key not in item:
            raise ValueError(f"group {gid!r} item {iid!r}: missing {key!r}")
    if wtype not in WIDGET_TYPES:
        raise ValueError(
            f"group {gid!r} item {iid!r}: unknown type {wtype!r} "
            f"(known: {sorted(WIDGET_TYPES)} + Cluster)")
    for key in WIDGET_TYPES[wtype]["required"]:
        if key not in item:
            raise ValueError(
                f"group {gid!r} item {iid!r} ({wtype}): missing {key!r}")
    # Unknown keys are REJECTED, not silently ignored (Phase C hardening):
    # a typo'd key used to vanish without a trace. tooltip + icon + title
    # are universal extras. tooltip is the hover annotation (item 4,
    # 2026-07-03: dropped from popover buttons so the panel IS the
    # affordance); title names a popover's drag bar; icon is the optional
    # KSShelf png (B25); width/height (pre-DPI px, CP-D wave-1) size the
    # button per item, 26x26 default. menu is legal only where the per-type
    # optional sets grant it. label (2026-07-08) is the optional persistent
    # text beside the icon that turns an icon button into the Symmetry-style
    # labeled mode toggle; universal like icon/title. annotation
    # (2026-07-19) is an optional custom hover-card source — a
    # "pkg.mod:attr" callable returning an Annotation — that outranks
    # doc/tooltip in toolbar_ui._maybe_annotate (first user: the Bridge
    # button, whose hover card IS its About card).
    allowed = (set(_COMMON_REQUIRED) | {"tooltip", "title", "icon", "label",
                                        "width", "height", "doc",
                                        "annotation"}
               | set(WIDGET_TYPES[wtype]["required"])
               | set(WIDGET_TYPES[wtype]["optional"]))
    for key in item:
        if key not in allowed:
            raise ValueError(
                f"group {gid!r} item {iid!r} ({wtype}): unknown key "
                f"{key!r} (allowed: {sorted(allowed)})")
    if (wtype == "PopoverButton" and "command" in item
            and item.get("trigger", "hover") == "click"):
        # CP-C: HOVER is the widget default, so an absent trigger with a
        # command is the legal Smoosh shape (hover tunes, click fires). Only
        # an EXPLICIT click trigger makes the command dead weight (click
        # already opens the panel).
        raise ValueError(
            f"group {gid!r} item {iid!r} (PopoverButton): 'command' "
            "requires trigger='hover' (click already opens the popover)")
    menu = item.get("menu", [])
    if not isinstance(menu, list):
        raise ValueError(f"group {gid!r} item {iid!r}: 'menu' must be a list")
    for entry in menu:
        if not isinstance(entry, dict) or "label" not in entry or "command" not in entry:
            raise ValueError(
                f"group {gid!r} item {iid!r}: menu entries need label+command")


def apply_layout_prefs(groups: list[dict], layout) -> list[dict]:
    """Reconcile manifest groups against the user's layout prefs (Phase D
    drag-to-reorder). Pure Python on purpose - offscreen-testable, matching
    this layer's philosophy. Never mutates the input.

    layout shape: {"version": 1, "groups": {group_id: {"order": [item ids],
    "hidden": [item ids]}}}. Rules (locked 2026-07-02):
      * prefs ids unknown to the manifest -> dropped silently,
      * duplicate ids in the order -> first occurrence wins; non-string
        entries -> dropped (hand-edited prefs must never raise),
      * manifest ids missing from the prefs order -> APPENDED in manifest
        order (new tools always appear),
      * ids in hidden -> filtered out (no UI writes hidden in v1; honoring
        it here makes the future show/hide feature prefs-only),
      * groups absent from prefs (or malformed anywhere) -> untouched.
    """
    if not isinstance(layout, dict):
        return groups
    layout_groups = layout.get("groups")
    if not isinstance(layout_groups, dict):
        return groups
    out = []
    for group in groups:
        entry = layout_groups.get(group.get("id"))
        if not isinstance(entry, dict):
            out.append(group)
            continue
        order = entry.get("order")
        order = order if isinstance(order, list) else []
        hidden = entry.get("hidden")
        hidden = hidden if isinstance(hidden, list) else []
        by_id = {item["id"]: item for item in group["items"]}
        # filter BEFORE deduping: dict.fromkeys raises TypeError on an
        # unhashable (hand-edited) entry, so non-strings drop out first;
        # dict.fromkeys then dedupes with first-occurrence-wins order
        placed = list(dict.fromkeys(
            iid for iid in order if isinstance(iid, str) and iid in by_id))
        seen = set(placed)
        items = [by_id[iid] for iid in placed]
        items += [item for item in group["items"] if item["id"] not in seen]
        items = [item for item in items if item["id"] not in hidden]
        out.append({**group, "items": items})
    return out


def resolve_command(spec: str):
    """'pkg.mod:attr.chain' -> callable. Imports lazily; raises with context.

    Mirrors the marking-menu dispatch pattern (fabricator/ui/animation_menu.py
    :218-239) but uses the spec's colon form so class attributes work:
    'maya_tools.skinning.autoskin.autoskin_ui:AutoSkinWindow.show_window'.
    """
    module_path, sep, attr_chain = spec.partition(":")
    if not sep or not module_path or not attr_chain:
        raise ValueError(f"command {spec!r} is not 'pkg.mod:attr.chain' form")
    obj = importlib.import_module(module_path)
    for attr in attr_chain.split("."):
        obj = getattr(obj, attr)
    if not callable(obj):
        raise ValueError(f"command {spec!r} resolved to non-callable {obj!r}")
    return obj
