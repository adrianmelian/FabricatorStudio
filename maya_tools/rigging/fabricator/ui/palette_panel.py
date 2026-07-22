# Python/maya_tools/rigging/fabricator/ui/palette_panel.py
"""PalettePanel — component-type palette (left of canvas).

A single search bar sits at the top (always reachable) and filters the
whole palette below it: ONE tree widget with three collapsible parents,
matching the canvas's joint-tree interaction —

- TEMPLATES: blueprint templates. Drag emits TEMPLATE_PATH_MIME_TYPE;
  double-click loads.
- LIMBS: saved limb fragments. Drag emits LIMB_PATH_MIME_TYPE.
- COMPONENTS: 'Add Joint' fake component (white swatch,
  JOINT_PRIMITIVE_MIME_TYPE drag) followed by all real component types
  (COMPONENT_MIME_TYPE drag). Double-click on a real component emits
  component_double_clicked.

COMPONENTS and LIMBS grey out while no rig exists in the scene —
templates and the rig-name field's create mode are the ways in.

TEMPLATES and LIMBS list both library sources (blueprint_library):
factory (shipped with Fabricator, read-only) then the active project's
blueprints folder (user-made). Right-click an entry for library ops —
Duplicate always works (copy lands in the project library); Rename and
Delete are project-only.

(Replaced the previous three CollapsibleSection + QListWidget stacks
2026-07-04 — one scrollable tree navigates far better as the palette
grows.)
"""
__author__ = "Adrian Melian"

from pathlib import Path

from PySide6 import QtWidgets, QtCore, QtGui

from maya_tools.rigging.fabricator import blueprint_library, modules
from maya_tools.rigging.fabricator.ui import state
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import MindmeldTreeWidget


COMPONENT_MIME_TYPE       = 'application/x-ks-component-type'
JOINT_PRIMITIVE_MIME_TYPE = 'application/x-ks-joint-primitive'
TEMPLATE_PATH_MIME_TYPE   = 'application/x-fab-template-path'
LIMB_PATH_MIME_TYPE       = 'application/x-fab-limb-path'

# Components that are auto-attached / structurally required and should NOT
# appear in the user-facing palette. Empty in Phase 2 — World is now a
# user-facing Modules entry.
_HIDDEN_COMPONENT_TYPES = frozenset()

# Data role that identifies which kind of entry an item is.
# Values: 'templates' | 'joints' | 'modules' | 'limbs' | 'header'
_ROLE_SECTION = QtCore.Qt.UserRole + 3
# Library source for templates/limbs entries:
# blueprint_library.FACTORY (shipped, read-only) | .PROJECT (user-made)
_ROLE_SOURCE = QtCore.Qt.UserRole + 4
# Component-row annotation text (description + joint-requirement + any
# selection-aware warning). Held OFF the native ToolTipRole on purpose: the
# AnimBot-style hover card is the sole annotation for component rows, and a
# native tooltip here would double up with it. The card reads this role.
_ROLE_ANNO_TEXT = QtCore.Qt.UserRole + 5

# Section → drag MIME. 'header' rows never drag.
_SECTION_MIME = {
    'templates': TEMPLATE_PATH_MIME_TYPE,
    'joints':    JOINT_PRIMITIVE_MIME_TYPE,
    'modules':   COMPONENT_MIME_TYPE,
    'limbs':     LIMB_PATH_MIME_TYPE,
}


class PalettePanel(QtWidgets.QWidget):
    """Component-type palette — one search bar + one tree with three
    collapsible parents (TEMPLATES / LIMBS / COMPONENTS)."""

    # Emitted on double-click. Host (FSWindow) translates to add-to-selection.
    component_double_clicked = QtCore.Signal(str)  # component type string
    # Emitted on Templates double-click. Host (FSWindow) calls fs_app.load.
    template_selected = QtCore.Signal(str)  # blueprint path
    # Emitted after a right-click library op changed template files on
    # disk — the host owns the template scan and repopulates.
    templates_changed = QtCore.Signal()
    # Log line for the host's logger: (level, text), level 'info'|'warning'.
    library_message = QtCore.Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = True
        self._canvas_selection_count = 0
        self._joint_names = []
        self._blueprint = None
        self._template_items = []  # set by populate_templates(items)
        # Rig-existence state from the LAST refresh — drives the one-shot
        # TEMPLATES roll-up on the no-rig → rig transition. None = not
        # yet evaluated.
        self._had_rig = None
        # Snapshot of the three parents' expanded states, taken when a
        # search goes empty->non-empty, so clearing the search restores
        # the user's prior layout after the auto-expand. None = no
        # search in progress.
        self._expansion_before_search = None

        self.create_layout()
        self._rebuild_templates()
        self._rebuild_components()
        self._rebuild_limbs()
        self.connect_signals()
        self._loading = False
        self.refresh_enabled_states()

    # --- Layout -------------------------------------------------------------

    def create_layout(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        root.addWidget(mindmeld_style.caps_label('// COMPONENTS', accent=True))

        # Consolidated search — one bar for the whole palette, above the
        # tree so it stays reachable regardless of collapse state.
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText('search palette...')
        # Shared compact search height (item 7) — see mindmeld.qss
        # QLineEdit[mindmeld="search"]; the canvas input wears it too.
        self.search_edit.setProperty('mindmeld', 'search')
        root.addWidget(self.search_edit)

        self.tree = MindmeldTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(14)
        self.tree.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        # Headers toggle on single click (see _on_item_clicked); double
        # click must not toggle a second time on top of that.
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QtWidgets.QAbstractItemView.DragOnly)
        self.tree.startDrag = self._start_drag
        root.addWidget(self.tree, stretch=1)

        # Top-to-bottom order (Adrian, 2026-07-04): TEMPLATES, LIMBS,
        # COMPONENTS — the whole-rig entries first, per-joint modules
        # last.
        self._templates_root = self._make_header('TEMPLATES')
        self._limbs_root = self._make_header('LIMBS')
        self._components_root = self._make_header('COMPONENTS')
        for header in (self._templates_root, self._limbs_root,
                       self._components_root):
            header.setExpanded(True)

    def _make_header(self, title: str) -> QtWidgets.QTreeWidgetItem:
        item = QtWidgets.QTreeWidgetItem(self.tree, [title])
        item.setData(0, _ROLE_SECTION, 'header')
        # Headers collapse/expand but never select or drag.
        item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
        return item

    # --- Private section builders -------------------------------------------

    @staticmethod
    def _clear_children(header: QtWidgets.QTreeWidgetItem) -> None:
        for child in header.takeChildren():
            del child

    @staticmethod
    def _child(header, label, section, payload=None) -> QtWidgets.QTreeWidgetItem:
        item = QtWidgets.QTreeWidgetItem(header, [label])
        item.setData(0, _ROLE_SECTION, section)
        if payload is not None:
            item.setData(0, QtCore.Qt.UserRole, payload)
        item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled
                      | QtCore.Qt.ItemFlag.ItemIsSelectable
                      | QtCore.Qt.ItemFlag.ItemIsDragEnabled)
        return item

    def _rebuild_templates(self) -> None:
        self._clear_children(self._templates_root)
        for label, path, source in self._template_items:
            item = self._child(self._templates_root, label,
                               'templates', path)
            item.setData(0, _ROLE_SOURCE, source)
            item.setToolTip(0, _entry_tooltip(
                'Drag onto the canvas or double-click to load.',
                source, path))

    def _rebuild_components(self) -> None:
        self._clear_children(self._components_root)
        # 'Add Joint' fake-component entry — same parent, but emits the
        # joint primitive MIME on drag. White swatch distinguishes it
        # from real components which carry their own contract colors.
        add_joint = self._child(self._components_root, 'Add Joint',
                                'joints', 'SingleJoint')
        add_joint.setIcon(0, _color_swatch_icon('#FFFFFF'))
        add_joint.setToolTip(0,
            'Drag onto a canvas joint to add one child joint.\n\n'
            'Drop on a row under cursor; drop on empty area is a no-op '
            "(use World to seed an empty scene's root joint).")
        for cls in modules.all_component_classes():
            contract = cls.CONTRACT
            if contract.type in _HIDDEN_COMPONENT_TYPES:
                continue
            item = self._child(self._components_root,
                               contract.display_name, 'modules',
                               contract.type)
            item.setData(0, QtCore.Qt.UserRole + 1, contract)
            item.setData(0, QtCore.Qt.UserRole + 2, cls)
            if contract.color:
                item.setIcon(0, _color_swatch_icon(contract.color))

    def _rebuild_limbs(self) -> None:
        self._clear_children(self._limbs_root)
        entries = blueprint_library.scan_limbs()
        if not entries:
            item = QtWidgets.QTreeWidgetItem(self._limbs_root,
                                             ['No saved limbs yet'])
            item.setData(0, _ROLE_SECTION, 'limbs')
            item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            item.setToolTip(0,
                'Save a limb via right-click → Save as Limb...')
            return
        for entry in entries:
            item = self._child(self._limbs_root, entry.name, 'limbs',
                               str(entry.path))
            item.setData(0, _ROLE_SOURCE, entry.source)
            item.setToolTip(0, _entry_tooltip(
                'Drag onto a joint to add this limb.',
                entry.source, entry.path))

    def populate_templates(self, items: list = None):
        """Store (label, path, source) tuples and rebuild TEMPLATES —
        source is blueprint_library.FACTORY or .PROJECT. FSWindow calls
        this after __init__ and whenever the library changes on disk."""
        self._template_items = list(items) if items else []
        self._rebuild_templates()

    def refresh_limbs(self) -> None:
        """Re-scan the limb library and rebuild LIMBS.
        FSWindow calls this after Save as Limb completes."""
        self._rebuild_limbs()
        self.refresh_enabled_states()  # fresh rows honor the no-rig grey

    def connect_signals(self):
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        # Hover annotations: a Mindmeld popover with the component's demo gif
        # plus its existing (selection-aware) tooltip text. Additive and fully
        # guarded — a failure here never breaks the palette.
        try:
            from maya_tools.utils.qt.hover_annotation import AnnotationController
            self._anno = AnnotationController(self)
            self._anno.attach_tree(self.tree, self._annotation_for_item)
        except Exception:
            import traceback
            traceback.print_exc()

    def _annotation_for_item(self, item):
        """Hover annotation for a palette row: the demo gif comes from the
        component's doc, the text from the row's own tooltip (which already
        carries the description plus any selection-aware warning). Non-component
        rows return None, so nothing pops."""
        if item is None:
            return None
        contract = item.data(0, QtCore.Qt.UserRole + 1)
        if contract is None:
            return None
        try:
            from maya_tools.framework import annotations
            return annotations.Annotation(
                title=(getattr(contract, "display_name", "") or contract.type),
                text=(item.data(0, _ROLE_ANNO_TEXT) or contract.description),
                gif=annotations.for_component(contract.type).gif,
            )
        except Exception:
            return None

    # --- Public API (host calls these) --------------------------------------

    def set_canvas_selection(self, joint_names: list) -> None:
        """Drives per-item tooltip via component.can_apply()."""
        self._joint_names = list(joint_names or [])
        self._canvas_selection_count = len(self._joint_names)
        try:
            from maya_tools.rigging.fabricator import fs_app, nodes
            self._blueprint = (fs_app._snapshot_blueprint_from_scene()
                               if nodes.get_registry() else None)
        except Exception:
            self._blueprint = None
        self.refresh_enabled_states()

    def refresh_state(self, mode: str) -> None:
        """Hide the palette when the rig is built; re-evaluate the
        no-rig grey-out (rig create/delete always routes through
        refresh_all → here)."""
        self.setVisible(mode != state.MODE_MODULES_BUILT)
        self.refresh_enabled_states()

    def _component_items(self):
        for i in range(self._components_root.childCount()):
            yield self._components_root.child(i)

    def refresh_enabled_states(self):
        """No rig in the scene → grey out every COMPONENTS and LIMBS
        entry (nothing to apply them to; templates and the rig-name
        field are the ways in). With a rig, entries enable and the
        COMPONENTS tooltips re-evaluate via can_apply."""
        try:
            from maya_tools.rigging.fabricator import nodes
            has_rig = bool(nodes.get_registry())
        except Exception:
            has_rig = False

        # One-shot TEMPLATES roll-up on the no-rig → rig transition
        # (template loaded / New Rig): the section's job is done — give
        # the space to LIMBS + COMPONENTS. Expands again when the rig
        # goes away. Transition-only, so a manual toggle isn't fought.
        if has_rig != self._had_rig:
            self._templates_root.setExpanded(not has_rig)
            self._had_rig = has_rig

        tint = QtGui.QBrush(_disabled_tint())
        for header in (self._components_root, self._limbs_root):
            for i in range(header.childCount()):
                child = header.child(i)
                child.setDisabled(not has_rig)
                if not has_rig:
                    child.setForeground(0, tint)
                else:
                    child.setData(0, QtCore.Qt.ForegroundRole, None)

        for item in self._component_items():
            contract = item.data(0, QtCore.Qt.UserRole + 1)
            cls = item.data(0, QtCore.Qt.UserRole + 2)
            if contract is None or cls is None:
                continue
            req_text = _joint_requirement_text(contract.min_joints,
                                               contract.max_joints)
            tip = f'{contract.description}\n\n{req_text}'
            if contract.max_joints == 1 and len(self._joint_names) > 1:
                # Single-joint component fans out: one instance per selected
                # joint (fs_window._add_component). Applicable if ANY selected
                # joint accepts it, so don't warn on the whole-list count.
                per = [cls.can_apply([jn], self._blueprint)
                       for jn in self._joint_names]
                n_ok = sum(1 for ok, _ in per if ok)
                if n_ok:
                    tip += (f'\n\nAdds one per selected joint '
                            f'({n_ok} of {len(self._joint_names)}).')
                else:
                    tip += (f'\n\n⚠ Won\'t apply with current selection — '
                            f'{per[0][1]}')
            else:
                ok, reason = cls.can_apply(list(self._joint_names),
                                           self._blueprint)
                if not ok:
                    tip += (f'\n\n⚠ Won\'t apply with current selection — '
                            f'{reason}')
            # Feed the AnimBot hover card via a custom role, NOT setToolTip —
            # a native tooltip here would render alongside the card (the old
            # "double annotation" bug).
            item.setData(0, _ROLE_ANNO_TEXT, tip)

    def menu_for_canvas_selection(self) -> QtWidgets.QMenu:
        """Build an 'Add Component' QMenu showing every Component."""
        menu = QtWidgets.QMenu('Add Component')
        for item in self._component_items():
            contract = item.data(0, QtCore.Qt.UserRole + 1)
            if contract is None:
                continue
            action = menu.addAction(contract.display_name)
            action.setData(item.data(0, QtCore.Qt.UserRole))
        if menu.isEmpty():
            empty = menu.addAction('(no Components registered)')
            empty.setEnabled(False)
        return menu

    # --- Drag handler ---------------------------------------------------------

    def _start_drag(self, _supported_actions):
        """One tree, one drag entry point: MIME dispatch by the item's
        section role (templates / joints / modules / limbs)."""
        items = self.tree.selectedItems()
        if not items:
            return
        item = items[0]
        section = item.data(0, _ROLE_SECTION)
        mime_type = _SECTION_MIME.get(section)
        payload = item.data(0, QtCore.Qt.UserRole) or ''
        if not mime_type or not payload:
            return
        mime = QtCore.QMimeData()
        mime.setData(mime_type,
                     QtCore.QByteArray(str(payload).encode('utf-8')))
        mime.setText(item.text(0))
        drag = QtGui.QDrag(self.tree)
        drag.setMimeData(mime)
        drag.exec(QtCore.Qt.CopyAction)

    # --- Library context menu -------------------------------------------------

    def _on_context_menu(self, pos):
        """Right-click library ops on template/limb entries. Components
        and Add Joint are code-defined (no files) — no menu. Factory
        entries: Duplicate only; Rename/Delete disabled with the reason
        shown in their tooltip."""
        item = self.tree.itemAt(pos)
        if item is None:
            return
        section = item.data(0, _ROLE_SECTION)
        if section not in ('templates', 'limbs'):
            return
        path = item.data(0, QtCore.Qt.UserRole)
        if not path:
            return  # 'No saved limbs yet' placeholder
        is_factory = (item.data(0, _ROLE_SOURCE)
                      == blueprint_library.FACTORY)

        menu = QtWidgets.QMenu(self)
        menu.setToolTipsVisible(True)
        dup_act = menu.addAction('Duplicate...')
        ren_act = menu.addAction('Rename...')
        del_act = menu.addAction('Delete...')
        if is_factory:
            for act in (ren_act, del_act):
                act.setEnabled(False)
                act.setToolTip('Factory blueprint — read-only. '
                               'Duplicate it into the project library '
                               'instead.')
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is dup_act:
            self._duplicate_entry(item, section, path)
        elif chosen is ren_act:
            self._rename_entry(item, section, path)
        elif chosen is del_act:
            self._delete_entry(item, section, path)

    def _refresh_section(self, section: str) -> None:
        if section == 'templates':
            self.templates_changed.emit()  # host owns the template scan
        else:
            self._rebuild_limbs()

    def _duplicate_entry(self, item, section, path):
        name, ok = QtWidgets.QInputDialog.getText(
            self, 'Duplicate Blueprint',
            'New name (lands in the project library):',
            QtWidgets.QLineEdit.Normal, f'{item.text(0)}_copy')
        if not ok or not name.strip():
            return
        try:
            dest = blueprint_library.duplicate(path, name)
        except blueprint_library.LibraryError as e:
            self._library_warn(str(e))
            return
        self.library_message.emit('info', f'Duplicated to {dest}')
        self._refresh_section(section)

    def _rename_entry(self, item, section, path):
        name, ok = QtWidgets.QInputDialog.getText(
            self, 'Rename Blueprint', 'New name:',
            QtWidgets.QLineEdit.Normal, item.text(0))
        if not ok or not name.strip():
            return
        try:
            dest = blueprint_library.rename(path, name)
        except blueprint_library.LibraryError as e:
            self._library_warn(str(e))
            return
        self.library_message.emit('info', f'Renamed to {dest.name}')
        self._refresh_section(section)

    def _delete_entry(self, item, section, path):
        fname = Path(str(path)).name
        choice = QtWidgets.QMessageBox.question(
            self, 'Delete Blueprint',
            f'Permanently delete "{fname}" from the project library?\n\n'
            f'The file is removed from disk.',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if choice != QtWidgets.QMessageBox.Yes:
            return
        try:
            blueprint_library.delete(path)
        except blueprint_library.LibraryError as e:
            self._library_warn(str(e))
            return
        self.library_message.emit('info', f'Deleted {fname}')
        self._refresh_section(section)

    def _library_warn(self, text: str) -> None:
        self.library_message.emit('warning', text)
        QtWidgets.QMessageBox.warning(self, 'Blueprint Library', text)

    # --- Click handlers -------------------------------------------------------

    def _on_item_clicked(self, item: QtWidgets.QTreeWidgetItem, _col: int):
        """Header rows toggle on a single click anywhere on the row —
        the same feel as the CollapsibleSection headers this tree
        replaced. (Clicks on the branch arrow itself are consumed by Qt
        before itemClicked fires, so the arrow doesn't double-toggle.)"""
        if item.data(0, _ROLE_SECTION) == 'header':
            item.setExpanded(not item.isExpanded())

    def _on_double_clicked(self, item: QtWidgets.QTreeWidgetItem, _col: int):
        section = item.data(0, _ROLE_SECTION)
        if section == 'templates':
            path = item.data(0, QtCore.Qt.UserRole)
            if path:
                self.template_selected.emit(str(path))
        elif section == 'modules':
            type_str = item.data(0, QtCore.Qt.UserRole)
            if type_str:
                self.component_double_clicked.emit(str(type_str))
        # 'joints' (Add Joint) and 'limbs' have no double-click action.

    # --- Search filtering ---------------------------------------------------

    def _on_search_text_changed(self, text: str):
        """Single palette search — filters every entry by substring.

        While a query is active all three parents are force-expanded so
        matches stay visible even if the user had them collapsed; each
        parent's prior expansion state is restored when the search is
        cleared. (Mid-search manual toggles are overwritten on clear —
        accepted corner case.)
        """
        needle = (text or '').strip().lower()
        headers = (self._templates_root, self._components_root,
                   self._limbs_root)

        for header in headers:
            for i in range(header.childCount()):
                child = header.child(i)
                label = child.text(0).lower()
                child.setHidden(bool(needle) and needle not in label)

        if needle:
            if self._expansion_before_search is None:
                self._expansion_before_search = [h.isExpanded()
                                                 for h in headers]
            for h in headers:
                h.setExpanded(True)
        elif self._expansion_before_search is not None:
            for h, was_expanded in zip(headers,
                                       self._expansion_before_search):
                h.setExpanded(was_expanded)
            self._expansion_before_search = None


# --- Helpers ----------------------------------------------------------------

def _disabled_tint() -> QtGui.QColor:
    """The no-rig grey — same color as a follow-joint row in the canvas
    (Adrian, 2026-07-04: the Qt-default disabled grey read too light).
    Sourced live from the FollowJoint contract so the two can never
    drift; the fallback pins the contract's current value."""
    try:
        return QtGui.QColor(
            modules.get_component_class('FollowJoint').CONTRACT.color)
    except Exception:
        return QtGui.QColor('#888888')


def _entry_tooltip(action_line: str, source: str, path) -> str:
    origin = ('Factory blueprint (read-only) — duplicate to edit.'
              if source == blueprint_library.FACTORY
              else 'Project blueprint.')
    return f'{action_line}\n{origin}\n\n{path}'


def _color_swatch_icon(hex_color: str, size: int = 12) -> QtGui.QIcon:
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pm)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        painter.fillRect(1, 1, size - 2, size - 2, QtGui.QColor(hex_color))
    finally:
        painter.end()
    return QtGui.QIcon(pm)


def _joint_requirement_text(min_j: int, max_j: int) -> str:
    if max_j == 1:
        return 'Select 1+ joints (one component per joint)'
    if max_j == -1:
        return f'Requires {min_j}+ joints selected'
    if min_j == max_j:
        word = 'joint' if min_j == 1 else 'joints'
        return f'Requires exactly {min_j} {word} selected'
    return f'Requires {min_j}--{max_j} joints selected'
