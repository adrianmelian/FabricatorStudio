"""FSToolbarStrip — the toolbar strip widget (spec sections 3.1-3.2).
Phase B: builds from validated toolbar.json manifest structures via a
type registry (decision B1: `type` key, ClassCase names). Commands,
popovers, and state queries arrive as CALLABLES — string resolution
happened upstream (toolbar_manifest.resolve_command, wired by the app
layer). Group rhythm per Animo (barMod.py:284-287): 2px within a group,
9px between.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from maya_tools.framework.toolbar import dpi
from maya_tools.framework.toolbar.widgets.expanding_slider import ExpandingSlider
from maya_tools.framework.toolbar.widgets.fabricator_button import FabricatorButton
from maya_tools.framework.toolbar.widgets.icon_button import IconButton
from maya_tools.framework.toolbar.widgets.menu_button import MenuButton
from maya_tools.framework.toolbar.widgets.popover_button import PopoverButton
from maya_tools.framework.toolbar.widgets.renamer_inline import RenamerInline
from maya_tools.framework.toolbar.widgets.toggle import Toggle
from maya_tools.framework.toolbar.widgets.window_launcher import WindowLauncher
from maya_tools.utils.qt.mindmeld import mindmeld_style

OBJECT_NAME = "fabricator_toolbar"
CROSS_AXIS_H = 38        # horizontal strip thickness, px pre-DPI
CROSS_AXIS_V = 160       # vertical strip WIDTH, px pre-DPI (CP-A verdict 2026-07-01:
                         # side mode must be wide enough for field widgets)
# Wave B (2026-07-03): three justified zones (left / center / right), no
# dividers. ITEM_SPACING gives every button breathing room ("more space");
# a Cluster composite packs its 2-3 buttons at CLUSTER_SPACING so the pair /
# trio reads as one unit and drags as one. ZONE_GAP separates adjacent
# groups that share a zone.
ITEM_SPACING = 8
CLUSTER_SPACING = 1
ZONE_GAP = 8
# The logo and settings sit at the extreme ends with just a LITTLE space off
# the wall (Adrian, 2026-07-03: "by itself, a little bit of space from the
# wall"). The gap is a squishy spacer - it holds WALL_GAP when there's room
# and shrinks first when the strip is compacted.
WALL_GAP = 16
# The four content columns pack together as a tight block: a FIXED gap between
# columns (~3.5x ITEM_SPACING, Adrian 2026-07-03: "3-4x the width between the
# widgets in a column") rather than a stretch, so they read as one cluster.
# The stretch lives only OUTSIDE the block (logo -> block, block -> settings).
COLUMN_GAP = 28
EDGE_INSET = 130        # retained for the (dormant) vertical strip margins


# -- constructor adapters (manifest keys -> ctor args) -----------------------
# Commands/popovers/state_queries are already callables here; the app layer
# resolved the "pkg.mod:attr" strings before the strip ever sees them.

def _menu_pairs(item: dict) -> list[tuple] | None:
    """[{"label", "command"}] manifest dicts -> [(label, callable)]."""
    menu = item.get("menu")
    if not menu:
        return None
    return [(entry["label"], entry["command"]) for entry in menu]


def _make_icon_button(item: dict) -> QWidget:
    return IconButton(item["glyph"], tooltip=item.get("tooltip", ""),
                      command=item.get("command"),
                      menu=_menu_pairs(item),
                      icon=item.get("icon"),
                      width=item.get("width"), height=item.get("height"))


def _make_toggle(item: dict) -> QWidget:
    return Toggle(item["glyph"], tooltip=item.get("tooltip", ""),
                  command=item.get("command"),
                  state_query=item.get("state_query"),
                  menu=_menu_pairs(item),
                  icon=item.get("icon"),
                  icon_on=item.get("icon_on"),
                  label=item.get("label"),
                  width=item.get("width"), height=item.get("height"))


def _make_expanding_slider(item: dict) -> QWidget:
    # No manifest entry uses ExpandingSlider again since the insert/offset
    # joints sliders retired 2026-07-18 (obsolete under the Armature rig
    # system); the class ships spec-complete for re-adds (gesture callables
    # injected by the app layer, Task 11).
    return ExpandingSlider(item["glyph"], minimum=item.get("min", 0.0),
                           maximum=item.get("max", 1.0),
                           default=item.get("default", 0.5),
                           tooltip=item.get("tooltip", ""),
                           command=item.get("command"),
                           live=item.get("live", False),
                           reset_on_apply=item.get("reset_on_apply", False),
                           center_return=item.get("center_return", False),
                           options_factory=item.get("options_popover"),
                           handle_label=item.get("handle_label", ""),
                           handle_color=item.get("handle_color", ""),
                           notches=item.get("notches"),
                           range_inputs=item.get("range_inputs", False),
                           gesture_begin=item.get("gesture_begin"),
                           gesture_end=item.get("gesture_end"))


def _make_popover_button(item: dict) -> QWidget:
    # The widget owns the trigger default (hover, CP-C) - the adapter only
    # forwards an EXPLICIT manifest value (a stale default here silently
    # overrode the widget's: the round-1 hover rollout reached only Smoosh).
    kwargs = {"tooltip": item.get("tooltip", ""),
              "popover_factory": item.get("popover"),
              "command": item.get("command"),
              "menu": _menu_pairs(item),
              "icon": item.get("icon"),
              "title": item.get("title"),
              "can_open": item.get("can_open"),
              "width": item.get("width"), "height": item.get("height")}
    if "trigger" in item:
        kwargs["trigger"] = item["trigger"]
    return PopoverButton(item["glyph"], **kwargs)


def _make_menu_button(item: dict) -> QWidget:
    # menu_factory is already a callable here (the app layer resolved the
    # "pkg.mod:attr" string), as is the optional command (run_command-wrapped).
    # Menu on RIGHT-click; command (if any) on left-click (Adrian, 2026-07-10).
    return MenuButton(item["glyph"], tooltip=item.get("tooltip", ""),
                      menu_factory=item.get("menu_factory"),
                      icon=item.get("icon"),
                      title=item.get("title"),
                      command=item.get("command"),
                      width=item.get("width"), height=item.get("height"))


def _make_window_launcher(item: dict) -> QWidget:
    return WindowLauncher(item["glyph"], tooltip=item.get("tooltip", ""),
                          command=item.get("command"),
                          menu=_menu_pairs(item),
                          icon=item.get("icon"),
                          width=item.get("width"), height=item.get("height"))


def _make_fabricator_button(item: dict) -> QWidget:
    # The Fabricator launcher: click resolves to the docked open, double_command
    # to the floating open, menu_factory to the Build/Unbuild rows - all already
    # callables here (the app layer resolved the "pkg.mod:attr" strings).
    return FabricatorButton(item["glyph"], tooltip=item.get("tooltip", ""),
                            command=item.get("command"),
                            double_command=item.get("double_command"),
                            menu_factory=item.get("menu_factory"),
                            icon=item.get("icon"),
                            title=item.get("title"),
                            width=item.get("width"), height=item.get("height"))


def _make_renamer_inline(item: dict) -> QWidget:
    # The strip IS the whole renamer (2026-07-05) — the popover and the
    # full window retired with the 4-mode consolidation.
    return RenamerInline()


def _make_project_chooser(item: dict) -> QWidget:
    from maya_tools.framework.toolbar.widgets.project_chooser import ProjectChooser
    return ProjectChooser()


_WIDGET_REGISTRY = {
    "IconButton": _make_icon_button,
    "Toggle": _make_toggle,
    "ExpandingSlider": _make_expanding_slider,
    "PopoverButton": _make_popover_button,
    "MenuButton": _make_menu_button,
    "WindowLauncher": _make_window_launcher,
    "FabricatorButton": _make_fabricator_button,
    "RenamerInline": _make_renamer_inline,
    "ProjectChooser": _make_project_chooser,
}


def _build_widget(item: dict) -> QWidget:
    wtype = item["type"]
    if wtype not in _WIDGET_REGISTRY:
        raise ValueError(
            f"Unknown toolbar widget type {wtype!r} (item {item.get('id')!r})")
    return _WIDGET_REGISTRY[wtype](item)


class FSToolbarStrip(QWidget):
    def __init__(self, orientation: str = "horizontal",
                 groups: list[dict] | None = None, parent=None):
        super().__init__(parent)
        # Decision B8: the strip adopts the design system wholesale — one
        # stylesheet (mindmeld.qss, TOOLBAR section) styles the whole tree;
        # fonts load as a side effect of the first apply().
        mindmeld_style.apply(self)
        if orientation not in ("horizontal", "vertical"):
            raise ValueError(f"FSToolbarStrip orientation {orientation!r}")
        self.setObjectName(OBJECT_NAME)
        # Review fix 2026-07-01: QWidget SUBCLASSES don't auto-paint stylesheet
        # backgrounds (QStyleSheetStyle::polish checks exact QWidget) — without
        # this attribute the carbon background renders nothing.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.orientation = orientation
        self.widget_instances: list[QWidget] = []
        self.widget_by_id: dict[str, QWidget] = {}   # PHASE-B-NOTES #14
        self.id_by_widget: dict[QWidget, str] = {}   # reverse map (reorder.py)

        horizontal = orientation == "horizontal"

        # Six justified zones (Adrian, 2026-07-03): the logo sits alone at the
        # far left and settings alone at the far right, with the four content
        # columns floated apart between them.
        zones = ("logo", "left", "left_center", "center", "right_center", "settings")
        by_zone: dict[str, list[dict]] = {z: [] for z in zones}
        for group in (groups or []):
            z = group.get("zone", "center")
            by_zone[z if z in by_zone else "center"].append(group)

        # Wave B (Adrian, 2026-07-03): three justified zones, no dividers.
        # LEFT packs to the start, RIGHT to the end, CENTER floats between two
        # stretches. Overflow just clips off-screen (Adrian, 2026-07-03: the
        # wrap experiment tanked resize perf; the answer is Show/Hide Tools).
        # The wall INSET is a SQUISHY spacer (Maximum policy: sits at EDGE_INSET
        # when there's room, shrinks toward 0 when compacted) rather than a
        # fixed margin, so a narrowing strip eats the empty edge space before
        # anything is pushed off (Adrian, 2026-07-03).
        lay = QHBoxLayout(self) if horizontal else QVBoxLayout(self)
        lay.setContentsMargins(0, 3, 0, 3) if horizontal else \
            lay.setContentsMargins(6, dpi.scaled(EDGE_INSET), 6, dpi.scaled(EDGE_INSET))
        lay.setSpacing(dpi.scaled(ZONE_GAP))
        if horizontal:
            self.setFixedHeight(dpi.scaled(CROSS_AXIS_H))
        else:
            self.setFixedWidth(dpi.scaled(CROSS_AXIS_V))

        align = Qt.AlignVCenter if horizontal else Qt.AlignHCenter
        if horizontal:
            lay.addItem(self._squishy_edge())        # left wall gap, shrinkable
        # Logo and settings anchor the ends; the content columns pack as one
        # tight block in the middle. The gaps just after the logo and just
        # before settings STRETCH (floating the block to center + isolating the
        # anchors); the gaps BETWEEN content columns are a fixed COLUMN_GAP
        # (Adrian, 2026-07-03: much closer together).
        populated = [z for z in zones if by_zone[z]]
        for zi, zname in enumerate(populated):
            if zi > 0:
                if zi == 1 or zi == len(populated) - 1:
                    lay.addStretch(1)                       # anchor <-> block
                else:
                    lay.addSpacing(dpi.scaled(COLUMN_GAP))  # column <-> column
            for group in by_zone[zname]:
                lay.addWidget(self._group_box(group, horizontal), 0, align)
        if horizontal:
            lay.addItem(self._squishy_edge())        # right wall gap, shrinkable

    @staticmethod
    def _squishy_edge():
        """A wall gap that PREFERS WALL_GAP but can shrink to nothing (Maximum
        h-policy: never grows past the gap, gives it up first when the strip is
        compacted) - the little space that keeps the logo/settings off the
        wall."""
        from PySide6.QtWidgets import QSizePolicy, QSpacerItem
        return QSpacerItem(dpi.scaled(WALL_GAP), 0,
                           QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)

    def _group_box(self, group: dict, horizontal: bool) -> QWidget:
        box = QWidget()
        # reorder.py persistence key: the box carries its group id.
        box.setProperty("fs_group_id", group.get("id", ""))
        box_lay = QHBoxLayout(box) if horizontal else QVBoxLayout(box)
        box_lay.setContentsMargins(0, 0, 0, 0)
        box_lay.setSpacing(dpi.scaled(ITEM_SPACING))
        for item in group.get("items", []):
            box_lay.addWidget(self._make_item(item, horizontal))
        return box

    def _make_item(self, item: dict, horizontal: bool) -> QWidget:
        """Build one item, register it as a reorder unit, and return it. A
        Cluster becomes a tight composite registered as a SINGLE unit: its
        sub-buttons are real (their commands fire) but are NOT reorder items,
        so reorder.py's _item_of walks a sub-button up to the cluster and the
        pair/trio drags as one."""
        if item.get("type") == "Cluster":
            w = self._build_cluster(item, horizontal)
        else:
            w = _build_widget(item)
        self.widget_instances.append(w)
        self.widget_by_id[item["id"]] = w
        self.id_by_widget[w] = item["id"]
        self._maybe_annotate(w, item)
        return w

    @staticmethod
    def _wants_annotation(item: dict) -> bool:
        """Annotate every button that does NOT already open a panel on hover.
        A hover-triggered PopoverButton is its own hover affordance; a Cluster
        is a container (its sub-buttons annotate individually). MenuButtons
        annotate too (Adrian, 2026-07-10): their menus moved off left-click to
        right-click, freeing hover for the card - same style as every other
        annotated button."""
        t = item.get("type")
        if t == "Cluster":
            return False
        if t == "PopoverButton" and item.get("trigger", "hover") != "click":
            return False
        return True

    def _maybe_annotate(self, widget: QWidget, item: dict) -> None:
        """Attach a rich hover annotation to a non-hover-popover button: a
        custom 'annotation' callable when the item carries one (2026-07-19,
        the Bridge button — its hover card IS its About card), else the
        tool doc when the item names one ('doc'), otherwise the button's own
        tooltip text plus an optional docs/media/toolbar/<id>.gif. Fully
        guarded so a failure never affects the toolbar."""
        if not self._wants_annotation(item):
            return
        try:
            if getattr(self, "_anno", None) is None:
                from maya_tools.utils.qt.hover_annotation import AnnotationController
                self._anno = AnnotationController(self)
            custom = item.get("annotation")
            if callable(custom):
                self._anno.attach_widget(widget, custom)
                return
            from maya_tools.framework import annotations
            doc = item.get("doc") or ""
            iid = item.get("id", "")
            title = item.get("title", "")
            tip = item.get("tooltip", "")

            def getter(doc=doc, iid=iid, title=title, tip=tip):
                if doc:
                    a = annotations.for_tool(doc)
                    if a:            # a real tool doc exists for this button
                        if tip:      # keep the button's own usage hint too:
                                     # tool-doc block, then the interaction line
                            text = f"{a.text}\n\n{tip}" if a.text else tip
                            return annotations.Annotation(
                                title=a.title, text=text, gif=a.gif)
                        return a
                return annotations.for_toolbar(iid, title, tip)

            self._anno.attach_widget(widget, getter)
        except Exception:
            import traceback
            traceback.print_exc()

    def _build_cluster(self, item: dict, horizontal: bool) -> QWidget:
        box = QWidget()
        box.setObjectName("fabricator_toolbar_cluster")
        box.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(box) if horizontal else QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(dpi.scaled(CLUSTER_SPACING))
        for sub in item.get("items", []):
            sw = _build_widget(sub)             # sub-buttons: real, not reorder units
            lay.addWidget(sw)
            self._maybe_annotate(sw, sub)       # ...but still individually annotated
        return box
