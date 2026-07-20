# Python/maya_tools/rigging/fabricator/ui/canvas_panel.py
"""CanvasPanel — center-of-window joint hierarchy + component annotations.

Displays the scene's joint hierarchy as a tree, projected via
`scene_canvas_model.read_scene_canvas()`. Annotates each joint with
[ComponentType] when a `fab_<id>` component owns it as its primary
joint (component.joints[0] match). Selection drives the properties
panel via joint_selected + component_selected signals (Option 2
stacked properties).

Drop target for palette drags (mime: application/x-ks-component-type).
Drop semantics per spec: replace requires confirmation, multi-selection
applies to all, single drop targets the cursor row.
"""
__author__ = "Adrian Melian"

import traceback

import maya.cmds as cmds
import maya.api.OpenMaya as om

from PySide6 import QtWidgets, QtCore, QtGui

from maya_tools.rigging.fabricator import blueprint_library, nodes
from maya_tools.rigging.fabricator.modules import get_component_class
from maya_tools.rigging.fabricator.ui import limb_grouping, scene_canvas_model, state
from maya_tools.rigging.fabricator.ui.palette_panel import (
    COMPONENT_MIME_TYPE,
    JOINT_PRIMITIVE_MIME_TYPE,
    TEMPLATE_PATH_MIME_TYPE,
    LIMB_PATH_MIME_TYPE,
)
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import MindmeldTreeWidget


# Roles for QTreeWidgetItem data
_ROLE_JOINT_NAME = QtCore.Qt.UserRole
_ROLE_COMPONENT_ID = QtCore.Qt.UserRole + 1
_ROLE_COMPONENT_TYPE = QtCore.Qt.UserRole + 2
# Animation-mode rows additionally store the ctrl's short name here so the
# linked-selection sync can map Maya viewport selections back to the row.
# Edit-mode rows leave this empty — the joint's Armature ctrl (when built)
# is the selection target, falling back to the joint itself.
_ROLE_CTRL_NAME = QtCore.Qt.UserRole + 3
# Limb-unit collapse (SPEC 2026-07-09 Limbs + Follower Joints §3.5) —
# True on a row that stands in for a whole collapsed limb (the row's
# _ROLE_JOINT_NAME is still the limb's top_joint; _ROLE_LIMB_TYPE holds
# the label text). Plain joint rows and Animation-mode ctrl rows leave
# this unset. See limb_grouping.RenderNode.is_limb_unit.
_ROLE_IS_LIMB_UNIT = QtCore.Qt.UserRole + 4
_ROLE_LIMB_TYPE = QtCore.Qt.UserRole + 5

# Armature ctrl naming — same local-constant pattern as branch_ops /
# armature_mirror ({joint}_amt_CTL).
_AMT_CTRL_SUFFIX = '_amt_CTL'

# Internal canvas drag — MMB on a joint row starts a reparent drag
# (Adrian, 2026-07-05: "select in the canvas the clavicle joint, and
# middle mouse drag drop it under spine3"). Payload: newline-joined
# branch-root joint names. MMB so left-click stays pure selection,
# matching Maya's own drag convention.
REPARENT_MIME_TYPE = 'application/x-fab-reparent-joints'


class CanvasPanel(QtWidgets.QWidget):
    """Joint hierarchy tree with component annotations + drag-drop."""

    # Selection signals — both can fire on the same row (Option 2 stacked).
    joint_selected     = QtCore.Signal(str)   # joint name
    component_selected = QtCore.Signal(str)   # component id ('' = none on this row)

    # Component lifecycle signals (host handles, then refreshes the canvas).
    add_component_requested     = QtCore.Signal(str, list)  # type, list[joint_name]
    remove_component_requested  = QtCore.Signal(str)        # component_id
    replace_component_requested = QtCore.Signal(str, str)   # joint_name, new_type

    # Template drop — emitted when a template is dragged from the palette and
    # dropped anywhere on the canvas. Host routes to fs_app.load(path).
    template_drop_requested     = QtCore.Signal(str)        # path

    # Inline rename — emitted on edit-finished. Host (FSWindow) handles via
    # cmds.rename + guide propagation.
    rename_requested            = QtCore.Signal(str, str)   # old_name, new_name

    save_limb_requested         = QtCore.Signal(str, str)   # (anchor_joint, output_path)

    limb_drop_requested         = QtCore.Signal(str, str)   # (limb_path, target_joint)

    # Armature reparent — MMB drag joint row(s) onto their new parent
    # row. Host routes to branch_ops.reparent_branches (one sandwich
    # for the whole batch).
    reparent_requested          = QtCore.Signal(list, str)  # (branch_roots, new_parent)

    # Branch ops from the row context menu (Adrian, 2026-07-05) — the
    # same smart ops as the toolbar groups, rooted at the clicked row.
    mirror_branch_requested     = QtCore.Signal(str)         # branch root
    duplicate_branch_requested  = QtCore.Signal(str)         # branch root
    delete_branches_requested   = QtCore.Signal(list)        # branch roots

    # SingleJoint primitive dropped on a row — same FSWindow handler as
    # the Skeleton Helpers Bar's Add Joint, which also wraps the new
    # joint into the Armature.
    add_joint_requested         = QtCore.Signal(str)         # target joint

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = True
        self._scene_nodes = {}  # joint_name -> CanvasNode
        # Map: joint short name -> dict('id': str, 'type': str). Built when
        # populate_from_scene runs; used to render [ComponentType] annotations
        # and to decide replace-vs-add on drop.
        self._joint_to_component = {}
        # Map: joint short name -> component_type for EVERY joint owned by
        # a component (not just joints[0]). Drives the row tint so an IK
        # chain's mid + end joints get the same module color as the start.
        # Populated alongside _joint_to_component in populate_from_scene.
        self._joint_to_tint_type = {}
        # Map: component_id -> error glyph ('⚠' or '✗') for badge rendering.
        self._component_badges = {}
        # Map: joint short name -> error glyph for badge rendering.
        self._joint_badges = {}
        # Hooks the host wires up so the canvas can ask for the right submenu.
        self._palette_menu_provider = None  # callable -> QMenu
        self._canvas_selection_observers = []  # list[callable(joint_names)] — scene-is-truth: observers compute their own bp if needed

        # Linked-selection state (canvas→Maya only; the Maya→canvas
        # MEventMessage sync was REMOVED 2026-07-10 — its SelectionChanged
        # storm cascaded canvas + properties rebuilds during every bulk
        # scene mutation and locked the GUI on Build Rig; Adrian's call:
        # the viewport no longer drives the canvas).
        # _sync_in_flight still gates the push so a canvas-initiated
        # cmds.select never re-enters through the tree's own signals.
        self._sync_in_flight = False
        # Rename twin of _sync_in_flight — see rename_row.
        self._rename_in_flight = False

        self.create_layout()
        self.connect_signals()
        self._loading = False

    # ─── Layout ─────────────────────────────────────────────────────────────

    def create_layout(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        root.addWidget(mindmeld_style.caps_label('// RIG OUTLINER'))

        search_row = QtWidgets.QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(6)

        # Refresh button retired (Adrian, 2026-07-05 brain dump item 8):
        # LiveBackend keeps the canvas current; a manual refresh is dead
        # weight.
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText('search joints / components…')
        # Shared compact search height (item 7) — see mindmeld.qss
        # QLineEdit[mindmeld="search"]; the palette input wears it too.
        self.search_edit.setProperty('mindmeld', 'search')
        search_row.addWidget(self.search_edit, stretch=1)

        root.addLayout(search_row)

        self.tree = _CanvasTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.setAcceptDrops(True)
        self.tree.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)
        # Phase 4 inline rename — DoubleClicked enters edit mode on the
        # row text; F2 also works (EditKeyPressed). Per-item ItemIsEditable
        # gates whether the trigger fires (set in _make_item_for_joint).
        self.tree.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked |
            QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed
        )
        root.addWidget(self.tree, stretch=1)

    def connect_signals(self):
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.itemChanged.connect(self._on_item_text_changed)
        self.search_edit.textChanged.connect(self._on_search_text_changed)

    # ─── Public API ─────────────────────────────────────────────────────────

    def set_palette_menu_provider(self, callable_):
        """Host wires this so the canvas's right-click menu can ask the palette
        for the 'Add Component' submenu."""
        self._palette_menu_provider = callable_

    def add_canvas_selection_observer(self, fn):
        """Host registers a callback (joint_names: list) -> None.
        Called whenever canvas selection changes."""
        self._canvas_selection_observers.append(fn)

    def populate_from_scene(self):
        """Read the scene and rebuild the tree.

        Skips the full rebuild when the structure hasn't changed (same joints
        + same primary-joint-to-component mapping) — just refreshes labels.
        Preserves the canvas selection by joint name across rebuilds.
        Preserves the search-text filter across rebuilds.
        """
        roots = scene_canvas_model.read_scene_canvas()
        # Every fab_limb in the scene, plain-projected (SPEC 2026-07-09
        # Limbs + Follower Joints §3.5) — feeds limb_grouping.build_render_
        # tree below to decide which joints collapse into one limb-unit
        # row. Read unconditionally (cheap — a handful of nodes at most)
        # even though Animation Mode's flat ctrl population never
        # consumes it, so the signature (below) stays correct regardless
        # of mode.
        limb_records = scene_canvas_model.read_limb_records()

        # Flatten roots → joint -> CanvasNode map for label lookup, and a derived
        # joint -> {id, type} map matching the legacy _joint_to_component shape.
        flat_nodes = {}
        new_to_component = {}
        def _flatten(node):
            flat_nodes[node.joint_name] = node
            if node.component_id:
                new_to_component[node.joint_name] = {
                    'id': node.component_id,
                    'type': node.component_type,
                }
            for c in node.children:
                _flatten(c)
        for r in roots:
            _flatten(r)

        # Build the parallel tint map covering EVERY joint a component owns
        # (chain mids + ends, not just joints[0]). Drives row foreground
        # color so a multi-joint component reads as one continuous block.
        # Walks network nodes directly — scene_canvas_model only exposes
        # primary mapping by design.
        from maya_tools.rigging.fabricator import nodes as fab_nodes
        new_to_tint_type = {}
        for cnode in fab_nodes.get_all_component_nodes():
            ctype = fab_nodes.get_component_type(cnode)
            if not ctype:
                continue
            for j in fab_nodes.get_component_joints(cnode):
                short = j.split('|')[-1].split(':')[-1]
                # First-set wins — primary-component takes precedence if a
                # joint is referenced by more than one component (rare;
                # follow_joint on a chain joint, e.g.).
                new_to_tint_type.setdefault(short, ctype)
        # Limb MEMBERS (finger chains, dialed twists — KS #8) tint like
        # the limb's primary component. setdefault AFTER the component
        # loop above: a joint another component directly owns (e.g. a
        # weapon joint's AdvancedFK) keeps its own component's tint,
        # never the arm's.
        for j, ctype in scene_canvas_model.read_limb_member_tints().items():
            new_to_tint_type.setdefault(j, ctype)

        # Include mode in the signature — pre-build (SKELETON) and post-build
        # (MODULES_BUILT) have identical joint hierarchies but render entirely
        # different trees (joints vs flat ctrls), so we must rebuild on mode
        # change even when the joint structure is unchanged.
        new_signature = (
            state.detect_mode(),
            _scene_structure_signature(roots, new_to_component),
            # Tint membership in the signature — a chain extending from
            # 2 to 3 joints doesn't change the primary mapping but DOES
            # change tint coverage, so the fast-path label refresh must
            # NOT swallow it. Sorted tuple of (joint, type) pairs.
            tuple(sorted(new_to_tint_type.items())),
            # Limb membership in the signature — same reasoning as the
            # tint map above: a rigger renaming a limb's limb_type (or
            # any other limb_node.py mutation — add/remove finger,
            # twist count) can leave the raw joint hierarchy AND the
            # joint→component mapping both unchanged, which would
            # otherwise short-circuit into the fast label-only refresh
            # path below — but that path re-reads a COLLAPSED row's
            # label from the row's own cached _ROLE_LIMB_TYPE, not the
            # live scene, so a limb_type edit would never appear.
            # Sorted tuple of (top_joint, limb_type, is_implicit).
            tuple(sorted(
                (r.top_joint, r.limb_type, r.is_implicit)
                for r in limb_records
            )),
        )
        prev_signature = getattr(self, '_signature', None)

        if roots and new_signature == prev_signature:
            # Structure unchanged — refresh labels only.
            self._scene_nodes = flat_nodes
            self._joint_to_component = new_to_component
            self._joint_to_tint_type = new_to_tint_type
            self._refresh_all_labels()
            return

        # Capture pre-rebuild selection by joint name so we can restore it.
        prior_selected = self.selected_joint_names()
        prior_current = None
        cur_item = self.tree.currentItem()
        if cur_item is not None:
            prior_current = cur_item.data(0, _ROLE_JOINT_NAME)
        # Capture pre-rebuild collapse state (negative set — only joints the
        # user explicitly collapsed). Edit Mode default is fully-expanded;
        # this lets a user-collapsed branch stay collapsed across rebuilds.
        prior_collapsed = self._capture_collapsed_joints()
        # Limb-unit rows invert that default (collapsed unless the user
        # explicitly opened one) — SPEC 2026-07-09 Limbs + Follower
        # Joints §3.5 "always expandable", collapsed by default. Positive
        # set (top_joint names) mirrors prior_collapsed's own capture/
        # restore shape but for the opposite default. See
        # _capture_expanded_limb_units's docstring.
        prior_expanded_limb_units = self._capture_expanded_limb_units()

        self._loading = True
        try:
            self._scene_nodes = flat_nodes
            self._joint_to_component = new_to_component
            self._joint_to_tint_type = new_to_tint_type
            self._signature = new_signature
            self.tree.clear()
            if not roots:
                return
            # Mode-aware population:
            # - Animation Mode (MODE_MODULES_BUILT): flat list of ctrls.
            #   Animators don't care about the joint tree — they're moving
            #   ctrls. One row per Fabricator-tagged ctrl, ordered by
            #   joint hierarchy (parents first).
            # - Edit Mode / pre-build: full joint tree (riggers need it).
            is_anim_mode = state.detect_mode() == state.MODE_MODULES_BUILT
            self.search_edit.setPlaceholderText(
                'search controls / components…' if is_anim_mode
                else 'search joints / components…'
            )
            if is_anim_mode:
                self._populate_ctrls_flat()
            else:
                # SPEC 2026-07-09 Limbs + Follower Joints §3.5: project
                # the raw joint hierarchy + limb membership into a render
                # tree (limb_grouping.build_render_tree, pure/headless-
                # tested) BEFORE walking it into Qt items — a collapse-
                # eligible limb's top_joint becomes one _make_item_for_
                # limb_unit row; every other joint (including joints
                # inside an exception/implicit limb) is an ordinary
                # _make_item_for_joint row, exactly as before this
                # module existed.
                render_roots = limb_grouping.build_render_tree(
                    roots, limb_records)

                def _add(render_node, parent_item):
                    if render_node.is_limb_unit:
                        item = self._make_item_for_limb_unit(
                            render_node.joint_name, render_node.limb_type)
                    else:
                        item = self._make_item_for_joint(render_node.joint_name)
                    if parent_item is None:
                        self.tree.addTopLevelItem(item)
                    else:
                        parent_item.addChild(item)
                    for c in render_node.children:
                        _add(c, item)
                for r in render_roots:
                    _add(r, None)
                self.tree.expandAll()
                # Restore user-collapsed branches.
                if prior_collapsed:
                    for jname in prior_collapsed:
                        item = self._find_item_by_joint(jname)
                        if item is not None:
                            item.setExpanded(False)
                # Limb-unit rows default COLLAPSED (inverted from the
                # ordinary default-expanded convention above) — collapse
                # every fresh limb-unit row except ones the user had
                # explicitly expanded before this rebuild. Runs AFTER the
                # prior_collapsed restore so an ordinary joint's own
                # user-collapse choice (a plain branch nested inside an
                # EXPANDED — i.e. exception/implicit — limb) still wins.
                self._collapse_fresh_limb_units(prior_expanded_limb_units)
        finally:
            self._loading = False

        # Restore selection silently first; re-emit explicitly afterwards.
        restored_current = None
        if prior_selected:
            self.tree.blockSignals(True)
            try:
                for name in prior_selected:
                    item = self._find_item_by_joint(name)
                    if item:
                        item.setSelected(True)
                if prior_current:
                    cur = self._find_item_by_joint(prior_current)
                    if cur:
                        self.tree.setCurrentItem(cur)
                        restored_current = cur
            finally:
                self.tree.blockSignals(False)

        # Re-apply search filter post-rebuild.
        self._on_search_text_changed(self.search_edit.text())

        if restored_current is not None:
            self._on_selection_changed()

    def set_validation_badges(self, joint_errors: dict, component_errors: dict):
        """joint_errors / component_errors: {key: glyph} where glyph is '⚠' or '✗'.
        Empty dicts clear all badges."""
        self._joint_badges = dict(joint_errors or {})
        self._component_badges = dict(component_errors or {})
        self._refresh_all_labels()

    def selected_joint_names(self) -> list:
        """List of joint short names currently selected (in selection order)."""
        names = []
        for item in self.tree.selectedItems():
            n = item.data(0, _ROLE_JOINT_NAME)
            if n:
                names.append(str(n))
        return names

    def selected_component_ids(self) -> list:
        """List of unique component_ids for all currently selected rows.

        A component may own multiple joints (e.g. FK chain); if the user
        selects several rows in the same component, deduplicate so the
        caller receives each component_id once. Order is stable (first
        occurrence in selection order wins).
        """
        seen = set()
        ids = []
        for item in self.tree.selectedItems():
            cid = item.data(0, _ROLE_COMPONENT_ID)
            if cid and str(cid) not in seen:
                seen.add(str(cid))
                ids.append(str(cid))
        return ids

    def select_joint(self, joint_name: str):
        """Programmatically select a joint by name."""
        item = self._find_item_by_joint(joint_name)
        if item:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)

    # ─── Animation-mode ctrl-flat population ────────────────────────────────

    def _populate_ctrls_flat(self) -> None:
        """Populate the canvas as a flat list of Fabricator ctrls.

        Used in Animation Mode. Each row represents one ctrl; the row's
        _ROLE_JOINT_NAME is set to the joint behind the ctrl (resolved
        via fab_owner + fab_joint_index) so existing selection
        signals (joint_selected / component_selected) keep working.
        Ordered by primary-joint hierarchy so siblings appear together.
        """
        # Walk every joint in scene-order; for each joint that owns a
        # component, find the component's ctrls and add a row per ctrl.
        # Order matches the tree walk so animators see a stable layout.
        seen_ctrls = set()
        for joint_name, comp in self._joint_to_component.items():
            ctrls = self._find_ctrls_for_component(comp['id'])
            for ctrl in ctrls:
                if ctrl in seen_ctrls:
                    continue
                seen_ctrls.add(ctrl)
                item = self._make_item_for_ctrl(ctrl, comp, joint_name)
                self.tree.addTopLevelItem(item)

    def _find_ctrls_for_component(self, component_id: str) -> list:
        """Return ctrl transforms whose fab_owner points at the given
        component network node. Sorted by fab_joint_index for stable
        within-component order."""
        # Resolve component_id → network node via the marker query.
        from maya_tools.rigging.fabricator import nodes as fab_nodes
        cnode = fab_nodes.find_component_node_by_id(component_id)
        if not cnode:
            return []
        # ctrls back-reference the component via fab_owner. Walk inbound
        # connections on the network node — destination side (fab_owner
        # is a string/message attr on the ctrl pointing AT the component).
        # The bidirectional pattern: ctrl.fab_owner ← component.message.
        # So query: who reads our .message attr?
        readers = cmds.listConnections(f'{cnode}.message',
                                        source=False, destination=True,
                                        plugs=True) or []
        out = []
        for plug in readers:
            target = plug.split('.')[0]
            attr = plug.split('.')[-1]
            if attr == 'fab_owner':
                out.append(target)
        # Sort by fab_joint_index for stable ordering (parents first).
        def _idx(c):
            try:
                return int(cmds.getAttr(f'{c}.fab_joint_index'))
            except Exception:
                return -1
        return sorted(set(out), key=_idx)

    def _make_item_for_ctrl(self, ctrl: str, comp: dict,
                             primary_joint: str) -> QtWidgets.QTreeWidgetItem:
        """Build a tree row representing a ctrl. The row's _ROLE_JOINT_NAME
        is the joint behind the ctrl so downstream signal handlers still
        get a valid joint context. Label is the ctrl's short name +
        [fab_role] annotation.
        """
        item = QtWidgets.QTreeWidgetItem()
        # Resolve the joint behind this ctrl (fab_joint_index into the
        # component's joints[]). Falls back to the component's primary
        # joint if the index is unset / out of range.
        joint_for_ctrl = primary_joint
        try:
            idx = int(cmds.getAttr(f'{ctrl}.fab_joint_index'))
            from maya_tools.rigging.fabricator import nodes as fab_nodes
            cnode = fab_nodes.find_component_node_by_id(comp['id'])
            cjoints = fab_nodes.get_component_joints(cnode) if cnode else []
            cjoints_short = [j.split('|')[-1].split(':')[-1] for j in cjoints]
            if 0 <= idx < len(cjoints_short):
                joint_for_ctrl = cjoints_short[idx]
        except Exception:
            pass
        item.setData(0, _ROLE_JOINT_NAME, joint_for_ctrl)
        item.setData(0, _ROLE_COMPONENT_ID, comp['id'])
        item.setData(0, _ROLE_COMPONENT_TYPE, comp['type'])

        ctrl_short = ctrl.split('|')[-1].split(':')[-1]
        item.setData(0, _ROLE_CTRL_NAME, ctrl_short)
        role = ''
        try:
            role = cmds.getAttr(f'{ctrl}.fab_role') or ''
        except Exception:
            pass
        if role:
            item.setText(0, f'{ctrl_short}  [{role}]')
        else:
            item.setText(0, ctrl_short)
        # Animation Mode rows are not editable (no inline rename).
        return item

    # ─── Item construction + label rendering ────────────────────────────────

    def _make_item_for_joint(self, joint_name: str) -> QtWidgets.QTreeWidgetItem:
        item = QtWidgets.QTreeWidgetItem()
        item.setData(0, _ROLE_JOINT_NAME, joint_name)
        comp = self._joint_to_component.get(joint_name)
        if comp:
            item.setData(0, _ROLE_COMPONENT_ID, comp['id'])
            item.setData(0, _ROLE_COMPONENT_TYPE, comp['type'])
        item.setText(0, self._label_for_joint(joint_name))
        self._apply_row_tint(item, joint_name)
        # Phase 4 inline rename — pre-Build-Rig items are editable. Mode is
        # detected at populate time; mode transitions trigger refresh_all
        # → populate which re-creates rows with the right flag.
        if state.detect_mode() != state.MODE_MODULES_BUILT:
            item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
        return item

    def _label_for_joint(self, joint_name: str) -> str:
        comp = self._joint_to_component.get(joint_name)
        annotation = f' [{comp["type"]}]' if comp else ''
        # Badge: prefer joint-level glyph; if absent, look at component glyph.
        glyph = self._joint_badges.get(joint_name)
        if not glyph and comp:
            glyph = self._component_badges.get(comp['id'])
        prefix = f'{glyph} ' if glyph else ''
        return f'{prefix}{joint_name}{annotation}'

    def _make_item_for_limb_unit(self, top_joint: str, limb_type: str
                                  ) -> QtWidgets.QTreeWidgetItem:
        """A collapse-eligible limb's row (SPEC 2026-07-09 Limbs +
        Follower Joints §3.5). Built on top of _make_item_for_joint so
        it inherits every other joint-row behavior byte for byte — row
        tint, component-owner data roles (if the top_joint is ALSO
        joints[0] of a component), context menu (Mirror/Duplicate/Save/
        Delete Limb all key off _ROLE_JOINT_NAME, which is still the
        real top_joint here) — only the label and the editable flag
        differ:
          - label: "{top_joint} ({limb_type})" — joint name first, limb
            type as a parenthesized annotation (format per Adrian
            2026-07-10; parens vs the component rows' [Module] brackets).
            Reworked for KS #7: the original type-only label read as a
            JOINT RENAME whenever an implicit limb — whose
            machine-default type is the component's own type string —
            got promoted to explicit by a fragment round-trip and
            collapsed for the first time. A blank limb_type falls back
            to the bare joint name.
          - not inline-renameable: the visible text is a LIMB label, not
            a plain joint name, so accepting an edit here would try to
            cmds.rename() the joint to the whole label string — renaming
            a limb happens through the Properties panel's LIMB section,
            not canvas inline-rename.
        """
        item = self._make_item_for_joint(top_joint)
        item.setText(0, self._label_for_limb_unit(top_joint, limb_type))
        item.setData(0, _ROLE_IS_LIMB_UNIT, True)
        item.setData(0, _ROLE_LIMB_TYPE, limb_type)
        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
        return item

    def _label_for_limb_unit(self, top_joint: str, limb_type: str) -> str:
        """Same badge-prefix convention _label_for_joint uses (joint-
        level glyph, falling back to the owning component's). Base text
        is "{top_joint} ({limb_type})" (KS #7, format per Adrian
        2026-07-10 — joint name stays primary so a collapsed limb never
        reads as a renamed joint; parens distinguish the limb annotation
        from component rows' square-bracket [Module] tags); blank
        limb_type degrades to the bare joint name. Derived Limbs (spec
        2026-07-11 §6): limb_type is the primary component's TYPE string
        — render its contract display_name (legacy fragment-named types
        pass through verbatim via limb_display_label's fallback)."""
        from maya_tools.rigging.fabricator import limb_node as _ln
        shown = _ln.limb_display_label(limb_type)
        label = f'{top_joint} ({shown})' if shown else top_joint
        glyph = self._joint_badges.get(top_joint)
        if not glyph:
            comp = self._joint_to_component.get(top_joint)
            if comp:
                glyph = self._component_badges.get(comp['id'])
        prefix = f'{glyph} ' if glyph else ''
        return f'{prefix}{label}'

    def _apply_row_tint(self, item, joint_name: str):
        """Color the row's text foreground to match the owning component's
        contract color. Reads from _joint_to_tint_type which covers EVERY
        joint a component owns — chain mids + ends get the same color as
        the start. Falls back to _joint_to_component (primary-only) so
        legacy callers without the tint map still tint primaries.
        Joints with no component show in the default theme color."""
        ctype = self._joint_to_tint_type.get(joint_name, '')
        if not ctype:
            comp = self._joint_to_component.get(joint_name)
            if comp:
                ctype = comp.get('type', '')
        color_hex = ''
        if ctype:
            try:
                cls = get_component_class(ctype)
                color_hex = cls.CONTRACT.color or ''
            except KeyError:
                pass
        if color_hex:
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(color_hex)))
        else:
            item.setData(0, QtCore.Qt.ForegroundRole, None)

    def _refresh_all_labels(self):
        def walk(item):
            n = item.data(0, _ROLE_JOINT_NAME)
            if n:
                if item.data(0, _ROLE_IS_LIMB_UNIT):
                    # Fast-path (structure-unchanged) refresh for a
                    # collapsed limb row — re-derive from the item's own
                    # cached _ROLE_LIMB_TYPE, not a fresh scene read
                    # (populate_from_scene's signature already folds
                    # limb_type into its rebuild trigger, so a REAL
                    # limb_type edit forces the full rebuild path below
                    # instead of this one — see that signature's own
                    # comment).
                    limb_type = item.data(0, _ROLE_LIMB_TYPE) or ''
                    item.setText(0, self._label_for_limb_unit(str(n), str(limb_type)))
                else:
                    item.setText(0, self._label_for_joint(str(n)))
                self._apply_row_tint(item, str(n))
            for i in range(item.childCount()):
                walk(item.child(i))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _handle_error(self, e: Exception, brief: str = '') -> None:
        """Project-standard error reporting — full trace to Script Editor,
        brief warning surfaced via cmds.warning. Used by LiveBackend hook
        handlers; the parent FSWindow catches errors from synchronous
        canvas operations via its own _handle_error."""
        traceback.print_exc()
        msg = brief or str(e)
        cmds.warning(f'[CanvasPanel] {msg}')

    def rename_row(self, old_name: str, new_name: str) -> None:
        """Update one row's joint name + label without rebuilding the tree.

        Called by the live-rename hook handler when a joint is renamed in
        the outliner / via cmds.rename. Mutates internal state in place,
        refreshes label and row tint, invalidates self._signature so the
        next populate_from_scene call rebuilds correctly.

        No-op if old_name has no row (rename targeted something the canvas
        doesn't track — group, mesh, untracked external node)."""
        if old_name == new_name:
            return
        item = self._find_item_by_joint(old_name)
        if item is None:
            return  # row not tracked — no-op

        # Re-entrancy guard. Every setData/setText below fires
        # QTreeWidget.itemChanged (Qt emits it for ANY role, not just the
        # display text) and _on_item_text_changed reads that as a finished
        # user edit. The JOINT_NAME role write lands BEFORE the label write,
        # so the handler would observe role=new_name + text=<old label>,
        # conclude the user renamed the joint BACK, and emit
        # rename_requested in reverse. cmds.rename then re-fires the
        # wildcard hook and the two names ping-pong forever. Only
        # externally-originated renames (outliner, channel box) hit that
        # window — a canvas inline rename already has the new text in the
        # row when the role write lands, which is why it never looped.
        self._rename_in_flight = True
        try:
            # Rebind row data
            item.setData(0, _ROLE_JOINT_NAME, new_name)

            # Migrate name-keyed dicts. _scene_nodes is a write-only cache
            # post-populate; just drop the stale entry — next populate refreshes it.
            if old_name in self._joint_to_component:
                self._joint_to_component[new_name] = self._joint_to_component.pop(old_name)
            if old_name in self._joint_badges:
                self._joint_badges[new_name] = self._joint_badges.pop(old_name)
            self._scene_nodes.pop(old_name, None)

            # Sync component data roles on the item itself. _on_selection_changed
            # and _on_context_menu read these directly off the item — they must
            # match _joint_to_component for the post-rename row.
            comp = self._joint_to_component.get(new_name)
            if comp:
                item.setData(0, _ROLE_COMPONENT_ID, comp['id'])
                item.setData(0, _ROLE_COMPONENT_TYPE, comp['type'])
            else:
                item.setData(0, _ROLE_COMPONENT_ID, None)
                item.setData(0, _ROLE_COMPONENT_TYPE, None)

            # Refresh label + tint to reflect new name (badges, [Module] tag,
            # contract color all re-derive from joint name + component map).
            # A limb-unit row (SPEC 2026-07-09 Limbs + Follower Joints §3.5)
            # keeps its limb_type label — renaming its top_joint doesn't
            # change what limb it is — so re-derive through
            # _label_for_limb_unit instead, else this would clobber the row
            # with the ordinary joint-name label until the next full
            # rebuild (self._signature is invalidated below, but that only
            # takes effect on the NEXT populate_from_scene call).
            if item.data(0, _ROLE_IS_LIMB_UNIT):
                limb_type = item.data(0, _ROLE_LIMB_TYPE) or ''
                item.setText(0, self._label_for_limb_unit(new_name, str(limb_type)))
            else:
                item.setText(0, self._label_for_joint(new_name))
            self._apply_row_tint(item, new_name)
        finally:
            self._rename_in_flight = False

        # Invalidate self._signature so subsequent populate_from_scene calls
        # do a full rebuild rather than short-circuit on stale cached
        # signature. (Recomputing the post-rename signature exactly is
        # straightforward but provides no benefit here — the next event-
        # driven populate will rebuild from scratch anyway.)
        self._signature = None

    # ─── LiveBackend hook handlers ──────────────────────────────────────────

    def _on_live_nodes_added(self, mobjs: list) -> None:
        """LiveBackend drain — list[MObject] of newly-added joints.
        Subscription's node_type_filter='joint' ensures only joints arrive,
        so no in-handler filtering needed. Existing populate_from_scene
        signature-skip handles the no-structural-change case."""
        try:
            self.populate_from_scene()
        except Exception as e:
            self._handle_error(e, brief='_on_live_nodes_added failed')

    def _on_live_nodes_removed(self, uuids: list) -> None:
        """LiveBackend drain — list[str] of UUIDs for deleted joints. Same
        subscription filter applies; just rebuild via populate_from_scene."""
        try:
            self.populate_from_scene()
        except Exception as e:
            self._handle_error(e, brief='_on_live_nodes_removed failed')

    def _on_live_dag_changed(self, changes: list) -> None:
        """LiveBackend drain — list[DagChange] of structural changes.
        addAllDagChangesCallback fires for any DAG node, so filter to
        joint changes here before triggering a full rebuild."""
        try:
            joint_changes = [
                c for c in changes
                if c.child_handle.isValid() and c.child_handle.isAlive()
                and c.child_handle.object().hasFn(om.MFn.kJoint)
            ]
            if not joint_changes:
                return  # nothing affecting the canvas
            self.populate_from_scene()
        except Exception as e:
            self._handle_error(e, brief='_on_live_dag_changed failed')

    def _on_live_renamed(self, events: list) -> None:
        """LiveBackend drain — list[RenameEvent]. Surgical: filter to joint
        renames, dispatch each to rename_row(old_name, new_name)."""
        try:
            for ev in events:
                if not (ev.handle.isValid() and ev.handle.isAlive()):
                    continue
                mobj = ev.handle.object()
                if not mobj.hasFn(om.MFn.kJoint):
                    continue
                # MFnDagNode.name() returns the short (segment) name —
                # matches cmds.ls(long=False) used in scene_canvas_model.
                # Relies on the unique-short-name invariant enforced by
                # validate_rig() before build.
                new_name = om.MFnDagNode(mobj).name()
                self.rename_row(ev.old_name, new_name)
        except Exception as e:
            self._handle_error(e, brief='_on_live_renamed failed')

    def _on_live_scene_reset(self) -> None:
        """LiveBackend drain — fired on kAfterNew / kAfterOpen and on
        every set_subscription_mode() switch. Full rebuild from scene."""
        try:
            self.populate_from_scene()
        except Exception as e:
            self._handle_error(e, brief='_on_live_scene_reset failed')

    def _find_item_by_joint(self, joint_name: str):
        match = [None]

        def walk(item):
            if match[0] is not None:
                return
            if item.data(0, _ROLE_JOINT_NAME) == joint_name:
                match[0] = item
                return
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return match[0]

    def _capture_collapsed_joints(self) -> set:
        """Return joint names whose tree row is currently collapsed AND has
        children (leaves can't be collapsed). Used to restore the user's
        manual collapse choices across rebuilds.

        Skips limb-unit rows (SPEC 2026-07-09 Limbs + Follower Joints
        §3.5) — they default to COLLAPSED (the opposite of the ordinary
        default-expanded convention this set exists to override), so a
        limb-unit row simply being collapsed carries no "the user did
        something non-default here" information; see
        _capture_expanded_limb_units for their own, inverted capture.
        Still recurses INTO a limb-unit row's children — an ordinary
        joint nested inside one (only reachable when that limb rendered
        EXPANDED, i.e. the attachment-point exception or an implicit
        limb) keeps its own independent collapse state exactly as
        before."""
        collapsed = set()

        def walk(item):
            if (item.childCount() > 0 and not item.isExpanded()
                    and not item.data(0, _ROLE_IS_LIMB_UNIT)):
                jname = item.data(0, _ROLE_JOINT_NAME)
                if jname:
                    collapsed.add(str(jname))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return collapsed

    def _capture_expanded_limb_units(self) -> set:
        """Return top_joint names of limb-unit rows the user has
        explicitly EXPANDED. The positive-set mirror of
        _capture_collapsed_joints, needed because limb-unit rows default
        to COLLAPSED (SPEC 2026-07-09 Limbs + Follower Joints §3.5) —
        the inverse of the ordinary joint-row default-expanded
        convention that a negative (collapsed-only) set is built for."""
        expanded = set()

        def walk(item):
            if item.data(0, _ROLE_IS_LIMB_UNIT) and item.isExpanded():
                jname = item.data(0, _ROLE_JOINT_NAME)
                if jname:
                    expanded.add(str(jname))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return expanded

    def _collapse_fresh_limb_units(self, keep_expanded: set) -> None:
        """Collapse every limb-unit row NOT in `keep_expanded` (top_joint
        names — see _capture_expanded_limb_units). Called once per
        rebuild, after self.tree.expandAll() has (as a side effect of
        the ordinary default-expanded convention) opened every row
        including fresh limb-unit ones — this is what actually enforces
        their inverted, collapsed-by-default state."""
        def walk(item):
            if item.data(0, _ROLE_IS_LIMB_UNIT):
                jname = item.data(0, _ROLE_JOINT_NAME)
                if not (jname and str(jname) in keep_expanded):
                    item.setExpanded(False)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    # ─── Linked selection (canvas → Maya viewport, one-way) ─────────────────

    def cleanup(self):
        """Host teardown hook, called by FSWindow.closeEvent. Currently a
        no-op: the panel's only Maya-side callback (the viewport→canvas
        SelectionChanged sync) was removed 2026-07-10 (Build Rig event
        storm). Kept so the host's failsafe teardown contract survives
        any future scene-side handles this panel grows."""
        pass

    def _target_node_for_item(self, item) -> str:
        """The Maya node this row represents for selection purposes.
        Animation Mode: the anim ctrl short name. Edit Mode: the joint's
        Armature ctrl when one exists — joint channels are locked under
        the armature, so the ctrl is what the user actually grabs. The
        root joint has no ctrl (origin anchor) and falls back to the
        joint itself, as does any joint before the armature is built."""
        ctrl = item.data(0, _ROLE_CTRL_NAME)
        if ctrl:
            return str(ctrl)
        jname = item.data(0, _ROLE_JOINT_NAME)
        if not jname:
            return ''
        amt_ctrl = f'{jname}{_AMT_CTRL_SUFFIX}'
        if cmds.objExists(amt_ctrl):
            return amt_ctrl
        return str(jname)

    def _push_canvas_selection_to_maya(self):
        """Mirror the canvas tree selection into Maya's viewport selection.
        Selects ctrls in Animation Mode and joints in Edit Mode."""
        targets = []
        seen = set()
        for it in self.tree.selectedItems():
            target = self._target_node_for_item(it)
            if not target or target in seen:
                continue
            if cmds.objExists(target):
                targets.append(target)
                seen.add(target)
        try:
            self._sync_in_flight = True
            if targets:
                cmds.select(targets, replace=True)
            else:
                cmds.select(clear=True)
        finally:
            self._sync_in_flight = False

    # ─── Selection + context menu ───────────────────────────────────────────

    def _on_item_text_changed(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        """Fired when user finishes editing a row's text (Phase 4 inline
        rename). Strip the [Module] annotation if present, compare to
        stored joint name, emit rename_requested if changed.

        _loading suppresses spurious fires during populate_from_scene's
        _make_item_for_joint loop. The text-only refresh path
        (_refresh_all_labels) also triggers this handler but its setText
        calls leave new_name == old_name → silent no-op (revert to
        canonical label, which is what _refresh_all_labels just set anyway)."""
        if self._loading or self._rename_in_flight or column != 0:
            return
        old_name = item.data(0, _ROLE_JOINT_NAME)
        if not old_name:
            return  # safety — item without joint name role shouldn't happen
        new_text = item.text(0).strip()
        # User may have edited only the joint-name part or the full label
        # including the annotation suffix. Strip ' [...' (component tag)
        # and ' (...' (limb-unit tag) suffixes back to the raw joint name.
        new_name = new_text.split(' [', 1)[0].split(' (', 1)[0].strip()
        if not new_name or new_name == old_name:
            # Empty or unchanged — revert to the canonical label and
            # bail. Limb-unit rows revert to THEIR canonical form (KS
            # #7: joint (limb_type)); this handler fires on programmatic
            # setText too (rename_row, _refresh_all_labels), and the old
            # plain-joint revert here silently clobbered the limb label
            # a rename_row had just written.
            if item.data(0, _ROLE_IS_LIMB_UNIT):
                limb_type = item.data(0, _ROLE_LIMB_TYPE) or ''
                item.setText(0, self._label_for_limb_unit(
                    str(old_name), str(limb_type)))
            else:
                item.setText(0, self._label_for_joint(old_name))
            return
        # Emit; FSWindow handles the rename and any post-rename refresh.
        self.rename_requested.emit(str(old_name), new_name)

    def _on_selection_changed(self):
        if self._loading:
            return
        joint_names = self.selected_joint_names()
        # Scene-is-truth: observers that need a Blueprint compute their own
        # transient snapshot. Pass joint_names only; blueprint param removed.
        for fn in self._canvas_selection_observers:
            try:
                fn(joint_names)
            except Exception:
                pass
        # Push selection out to Maya's viewport — gated by _sync_in_flight
        # so the Maya→canvas callback can't bounce this back into a loop.
        if not self._sync_in_flight:
            try:
                self._push_canvas_selection_to_maya()
            except Exception:
                traceback.print_exc()
        # Emit selection signals for currentItem (most-recent focused row).
        item = self.tree.currentItem()
        if item is None:
            # Nothing selected (tree cleared, or a viewport selection that
            # maps to no row) — emit the explicit 'nothing selected' shape
            # so downstream _pending_joint_selection/_pending_component_
            # selection (fs_window) clear instead of freezing on whatever
            # was last shown.
            self.joint_selected.emit('')
            self.component_selected.emit('')
            return
        joint_name = item.data(0, _ROLE_JOINT_NAME)
        if joint_name:
            self.joint_selected.emit(str(joint_name))
        component_id = item.data(0, _ROLE_COMPONENT_ID) or ''
        # Always emit component_selected — empty string means "no component on this row".
        self.component_selected.emit(str(component_id))

    def _on_context_menu(self, pos: QtCore.QPoint):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        joint_name = item.data(0, _ROLE_JOINT_NAME)
        if not joint_name:
            return
        component_id = item.data(0, _ROLE_COMPONENT_ID) or ''

        menu = QtWidgets.QMenu(self.tree)

        # Add Component → palette submenu (if host wired it up)
        if self._palette_menu_provider is not None:
            sub = self._palette_menu_provider()
            if sub is not None:
                sub.setTitle('Add Component')
                sub.triggered.connect(self._on_add_component_action)
                menu.addMenu(sub)

        # Remove Component (only when this row owns one)
        if component_id:
            remove_act = menu.addAction(f'Remove Component ({component_id})')
            remove_act.triggered.connect(
                lambda _checked=False, cid=str(component_id):
                self.remove_component_requested.emit(cid))

        in_edit_mode = state.detect_mode() == state.MODE_SKELETON
        joint_in_scene = bool(cmds.objExists(str(joint_name)))

        # Branch ops — the same smart ops as the toolbar groups and
        # the ctrl-delete gesture, rooted at THIS row. Edit Mode only.
        # Menu order is Adrian's 2026-07-05 spec: components, divider,
        # Mirror, Duplicate, divider, Save, divider, Delete — the
        # destructive action isolated at the bottom.
        if in_edit_mode and joint_in_scene:
            menu.addSeparator()
            mirror_act = menu.addAction('Mirror Limb')
            mirror_act.setToolTip(
                'Smart mirror this branch to the other side (joints '
                'missing there → joints + modules; present → modules '
                'only).')
            mirror_act.triggered.connect(
                lambda _checked=False, jn=str(joint_name):
                self.mirror_branch_requested.emit(jn))
            dup_act = menu.addAction('Duplicate Limb...')
            dup_act.setToolTip(
                'Duplicate this branch (joints + modules) with a '
                'variant-tag rename of the whole subtree.')
            dup_act.triggered.connect(
                lambda _checked=False, jn=str(joint_name):
                self.duplicate_branch_requested.emit(jn))

        # Save as Limb — Edit Mode (MODE_SKELETON) only. Animation Mode
        # (MODE_MODULES_BUILT) hides editing affordances per the Edit Mode
        # pillar; persisted ctrl shapes captured at unbuild are present
        # on the network nodes for the snapshotter to read.
        #
        # The rig ROOT never offers it (2026-07-18, Adrian): saving from
        # the root is saving the whole rig, which is Save Template's job. The
        # root is the World component's own joints[0], so the gesture
        # produced a "limb" carrying every limb in the rig at once — a
        # template wearing a fragment's clothes.
        descendants = cmds.listRelatives(
            str(joint_name), allDescendents=True, type='joint',
        ) or []
        has_descendants = bool(descendants)
        is_rig_root = str(joint_name) == nodes.get_registry_root_joint()
        if in_edit_mode and has_descendants and not is_rig_root:
            menu.addSeparator()
            save_limb_act = menu.addAction('Save Limb')
            save_limb_act.triggered.connect(
                lambda _checked=False, jn=str(joint_name): self._save_limb(jn))

        # Delete — last, behind its own divider. The op is identical for
        # every joint row (branch_ops.delete_branches: joint + subtree +
        # aimers + any modules touched); only the LABEL is context-aware
        # (KS #5): 'Delete Limb' when the branch actually involves
        # components, plain 'Delete' for a bare helper/stray joint —
        # the limb wording on component-less joints read as "you can't
        # delete this without a component".
        if in_edit_mode and joint_in_scene:
            menu.addSeparator()
            del_label = self._branch_delete_label(item, component_id,
                                                  descendants)
            del_act = menu.addAction(del_label)
            del_act.setToolTip(
                'Delete this joint and its whole subtree, their aimers/'
                'armature parts, and any modules they own. One undo. '
                'Multi-selection deletes every selected branch.')
            del_act.triggered.connect(
                lambda _checked=False, jn=str(joint_name):
                self._emit_delete_branches(jn))

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _branch_delete_label(self, item, component_id: str,
                             descendants: list) -> str:
        """'Delete Limb' when this row's branch involves components (the
        row owns one, is a collapsed limb unit, or any descendant joint
        owns one) — plain 'Delete' for a component-less branch (KS #5).
        Purely cosmetic: both run the same delete_branches sandwich."""
        if component_id or item.data(0, _ROLE_IS_LIMB_UNIT):
            return 'Delete Limb'
        if any(d in self._joint_to_component for d in descendants):
            return 'Delete Limb'
        return 'Delete'

    def _emit_delete_branches(self, clicked: str) -> None:
        """Same multi-select convention as component drops: deleting a
        row that is part of the selection deletes every selected
        branch (one sandwich)."""
        selected = self.selected_joint_names()
        if len(selected) > 1 and clicked in selected:
            roots = list(selected)
        else:
            roots = [clicked]
        self.delete_branches_requested.emit(roots)

    def _save_limb(self, anchor_joint: str) -> None:
        """Prompt for a limb name, then save into the active project's
        blueprints folder (blueprint_library — the factory templates
        dir is read-only). Default name = the anchor's short joint
        name; user can type anything. Overwrite prompt appears only
        when a file of the chosen name already exists. Warns and
        aborts when no project library is configured.
        """
        try:
            dest_dir = blueprint_library.require_project_dir()
        except blueprint_library.LibraryError as e:
            QtWidgets.QMessageBox.warning(self, 'Save as Limb', str(e))
            return
        default_name = anchor_joint.rsplit(':', 1)[-1]
        name, ok = QtWidgets.QInputDialog.getText(
            self, 'Save Limb', 'Limb name:',
            QtWidgets.QLineEdit.Normal, default_name,
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        # User may type the extension by habit; strip it so we always
        # produce '<name>.limb.yaml' regardless of what they entered.
        if name.endswith('.limb.yaml'):
            name = name[:-len('.limb.yaml')]
        elif name.endswith('.yaml'):
            name = name[:-len('.yaml')]
        if not name:
            return
        path = dest_dir / f'{name}.limb.yaml'
        if path.exists():
            choice = QtWidgets.QMessageBox.question(
                self, 'Overwrite Limb',
                f'"{name}.limb.yaml" already exists in the project '
                f'library.\n\nOverwrite with current rig?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes,
            )
            if choice != QtWidgets.QMessageBox.Yes:
                return
        self.save_limb_requested.emit(anchor_joint, str(path))

    def _on_add_component_action(self, action: QtGui.QAction):
        type_str = action.data()
        if not type_str:
            return  # disabled fallback action
        joints = self.selected_joint_names()
        if not joints:
            return
        self.add_component_requested.emit(str(type_str), joints)

    # ─── Drop handling (delegated from _CanvasTreeWidget) ───────────────────

    def handle_drop(self, type_str: str, target_joint: str):
        """Decide whether to apply to multi-selection or single target,
        and whether to surface a replace-confirmation dialog."""
        if not type_str or not target_joint:
            return

        selected = self.selected_joint_names()
        # If the cursor row is part of a multi-selection, apply to all selected.
        if len(selected) > 1 and target_joint in selected:
            targets = selected
        else:
            targets = [target_joint]

        # Replace-vs-add: ask once if any target already has a component.
        replacing = [t for t in targets if t in self._joint_to_component]
        if replacing:
            existing_summary = ', '.join(
                f'{t} [{self._joint_to_component[t]["type"]}]' for t in replacing)
            choice = QtWidgets.QMessageBox.question(
                self, 'Replace component?',
                f'These joints already have a component:\n  {existing_summary}\n\n'
                f'Replace with [{type_str}]?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if choice != QtWidgets.QMessageBox.Yes:
                # Drop only adds to non-occupied joints.
                targets = [t for t in targets if t not in self._joint_to_component]
                if not targets:
                    return

        self.add_component_requested.emit(str(type_str), targets)

    def _handle_joint_primitive_drop(self, target_joint: str, type_str: str) -> None:
        """Joint primitive drop handler. Currently only 'SingleJoint' is
        a valid type; dispatch by type if more primitives ship later.

        Emits rather than doing the scene work itself: adding a joint
        also rebuilds the Armature (ctrl + aimer + `_Joints` layer), so
        it belongs on the same FSWindow handler the Skeleton Helpers
        Bar's Add Joint uses, which owns the logging and the full
        refresh a rebuild needs. A surgical LiveBackend row insert is
        not enough once the whole ctrl tree is replaced."""
        if type_str != 'SingleJoint':
            self._handle_error(
                RuntimeError(f"Unknown joint primitive type: '{type_str}'"),
                brief=f'Unknown joint primitive: {type_str}')
            return
        self.add_joint_requested.emit(str(target_joint))

    # ─── Search filtering ───────────────────────────────────────────────────

    def _on_search_text_changed(self, text: str):
        needle = (text or '').strip().lower()

        def matches(item) -> bool:
            label = item.text(0).lower()
            jn = (item.data(0, _ROLE_JOINT_NAME) or '').lower()
            cid = (item.data(0, _ROLE_COMPONENT_ID) or '').lower()
            ctype = (item.data(0, _ROLE_COMPONENT_TYPE) or '').lower()
            return needle in label or needle in jn or needle in cid or needle in ctype

        def walk(item) -> bool:
            """Returns True if item or any descendant matches; sets visibility."""
            self_match = matches(item) if needle else True
            any_child_match = False
            for i in range(item.childCount()):
                child = item.child(i)
                if walk(child):
                    any_child_match = True
            visible = bool(needle == '' or self_match or any_child_match)
            item.setHidden(not visible)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))


# ─── Custom QTreeWidget that delegates drop to its CanvasPanel ──────────────

class _CanvasTreeWidget(MindmeldTreeWidget):
    # Disclosure arrows come from MindmeldTreeWidget (plasma_dim
    # triangles — the Mindmeld QSS blanks Qt's native branch images).

    def __init__(self, panel: CanvasPanel):
        super().__init__()
        self._panel = panel
        # MMB reparent-drag state (press position + pressed row).
        self._mmb_press_pos = None
        self._mmb_item = None

    # ── MMB reparent drag (drag SOURCE side) ────────────────────────────
    # The tree's drag-drop mode stays DropOnly — Qt's built-in (LMB)
    # drag machinery never engages. The MMB drag is hand-rolled here so
    # left-click keeps meaning selection/rename, nothing else.

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.MouseButton.MiddleButton:
            item = self.itemAt(event.position().toPoint())
            if item is not None and item.data(0, _ROLE_JOINT_NAME):
                self._mmb_press_pos = event.position().toPoint()
                self._mmb_item = item
                return  # swallow — MMB has no other role in the tree
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        if (self._mmb_press_pos is not None
                and event.buttons() & QtCore.Qt.MouseButton.MiddleButton):
            moved = (event.position().toPoint()
                     - self._mmb_press_pos).manhattanLength()
            if moved >= QtWidgets.QApplication.startDragDistance():
                self._start_reparent_drag()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.MouseButton.MiddleButton:
            self._mmb_press_pos = None
            self._mmb_item = None
        super().mouseReleaseEvent(event)

    def _start_reparent_drag(self):
        item, self._mmb_item = self._mmb_item, None
        self._mmb_press_pos = None
        if item is None:
            return
        pressed = str(item.data(0, _ROLE_JOINT_NAME) or '')
        if not pressed:
            return
        # Same multi-select convention as component drops: dragging a
        # row that is part of the selection drags every selected row.
        selected = self._panel.selected_joint_names()
        if len(selected) > 1 and pressed in selected:
            roots = list(selected)
        else:
            roots = [pressed]
        mime = QtCore.QMimeData()
        mime.setData(REPARENT_MIME_TYPE,
                     '\n'.join(roots).encode('utf-8'))
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.exec(QtCore.Qt.DropAction.MoveAction)

    def _reparent_target_ok(self, event) -> bool:
        """Valid drop target: a joint row that is neither one of the
        dragged rows nor inside a dragged subtree (cycle). Walking the
        TREE ancestry mirrors the joint hierarchy exactly."""
        try:
            pos = event.position().toPoint()
        except AttributeError:
            pos = event.pos()
        target = self.itemAt(pos)
        if target is None or not target.data(0, _ROLE_JOINT_NAME):
            return False
        try:
            raw = bytes(event.mimeData().data(REPARENT_MIME_TYPE))
            roots = set(raw.decode('utf-8').split('\n'))
        except (UnicodeDecodeError, ValueError):
            return False
        it = target
        while it is not None:
            if str(it.data(0, _ROLE_JOINT_NAME) or '') in roots:
                return False
            it = it.parent()
        return True

    # ── Drop TARGET side ────────────────────────────────────────────────

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent):
        md = event.mimeData()
        if (md.hasFormat(COMPONENT_MIME_TYPE)
                or md.hasFormat(JOINT_PRIMITIVE_MIME_TYPE)
                or md.hasFormat(TEMPLATE_PATH_MIME_TYPE)
                or md.hasFormat(LIMB_PATH_MIME_TYPE)
                or md.hasFormat(REPARENT_MIME_TYPE)):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent):
        md = event.mimeData()
        if md.hasFormat(REPARENT_MIME_TYPE):
            # Live validity feedback: forbidden cursor over the dragged
            # branch itself, empty space, and non-joint rows.
            if self._reparent_target_ok(event):
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        if (md.hasFormat(COMPONENT_MIME_TYPE)
                or md.hasFormat(JOINT_PRIMITIVE_MIME_TYPE)
                or md.hasFormat(TEMPLATE_PATH_MIME_TYPE)
                or md.hasFormat(LIMB_PATH_MIME_TYPE)):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent):
        mime = event.mimeData()
        # itemAt expects QPoint; PySide6 .position() returns QPointF on QDropEvent.
        try:
            pos = event.position().toPoint()
        except AttributeError:
            pos = event.pos()
        target_item = self.itemAt(pos)
        target_joint = (target_item.data(0, _ROLE_JOINT_NAME)
                        if target_item is not None else None)

        if mime.hasFormat(REPARENT_MIME_TYPE):
            if not self._reparent_target_ok(event):
                event.ignore()
                return
            raw = bytes(mime.data(REPARENT_MIME_TYPE))
            roots = [r for r in raw.decode('utf-8').split('\n') if r]
            if not roots:
                event.ignore()
                return
            event.acceptProposedAction()
            self._panel.reparent_requested.emit(roots, str(target_joint))
            return

        if mime.hasFormat(TEMPLATE_PATH_MIME_TYPE):
            path_bytes = bytes(mime.data(TEMPLATE_PATH_MIME_TYPE))
            try:
                path_str = path_bytes.decode('utf-8')
            except UnicodeDecodeError:
                event.ignore()
                return
            event.acceptProposedAction()
            # Template drops are accepted anywhere on the canvas — they seed
            # the scene rather than target a specific joint row.
            self._panel.template_drop_requested.emit(path_str)
            return

        if mime.hasFormat(LIMB_PATH_MIME_TYPE):
            if target_joint is None:
                # Limb drops require a target joint to graft under; empty
                # space has nothing to anchor the limb to.
                event.ignore()
                return
            path_bytes = bytes(mime.data(LIMB_PATH_MIME_TYPE))
            try:
                path_str = path_bytes.decode('utf-8')
            except UnicodeDecodeError:
                event.ignore()
                return
            event.acceptProposedAction()
            self._panel.limb_drop_requested.emit(path_str, str(target_joint))
            return

        if mime.hasFormat(COMPONENT_MIME_TYPE):
            type_bytes = bytes(mime.data(COMPONENT_MIME_TYPE))
            try:
                type_str = type_bytes.decode('utf-8')
            except UnicodeDecodeError:
                event.ignore()
                return
            if not target_joint:
                event.ignore()
                return
            event.acceptProposedAction()
            self._panel.handle_drop(type_str, str(target_joint))
            return

        if mime.hasFormat(JOINT_PRIMITIVE_MIME_TYPE):
            if target_joint is None:
                # Drop on empty area is rejected — empty-scene seeding
                # is World's job, not SingleJoint's.
                event.ignore()
                return
            type_bytes = bytes(mime.data(JOINT_PRIMITIVE_MIME_TYPE))
            try:
                type_str = type_bytes.decode('utf-8')
            except UnicodeDecodeError:
                event.ignore()
                return
            event.acceptProposedAction()
            self._panel._handle_joint_primitive_drop(str(target_joint), type_str)
            return

        super().dropEvent(event)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _scene_structure_signature(roots, joint_to_component) -> tuple:
    """Hashable signature of the scene's canvas structure.

    Two snapshots produce equal signatures iff they have the same joint
    hierarchy + same primary-joint-to-component mapping. Used to skip
    full tree rebuilds when only labels need refreshing.
    """
    def _walk(node):
        return (
            node.joint_name,
            joint_to_component.get(node.joint_name, {}).get('id', ''),
            joint_to_component.get(node.joint_name, {}).get('type', ''),
            tuple(_walk(c) for c in node.children),
        )
    return tuple(_walk(r) for r in roots)


def _derive_id_for_display(cdata) -> str:
    """Best-effort id for display when cdata.id is empty. Mirrors
    Component.default_id without requiring the class lookup.

    Public helper used by fs_window.py for component-ID derivation in
    blueprint-write paths; future phases may consolidate into a shared
    utility.
    """
    if not cdata.joints:
        return cdata.type.lower()
    first = cdata.joints[0].split('|')[-1].split(':')[-1]
    snake = ''
    t = cdata.type
    for i, ch in enumerate(t):
        if i > 0 and ch.isupper() and t[i - 1].islower():
            snake += '_'
        snake += ch.lower()
    return f'{first}_{snake}'
