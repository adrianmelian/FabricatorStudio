# Python/maya_tools/rigging/fabricator/ui/fs_window.py
"""FSWindow — main authoring window for KS v2.

Shell containing: brand bar (top), SkeletonHelpersBar (above splitter),
three-panel QSplitter (Components / Rig Outliner / Properties), two phase-button
rows below the splitter, LoggerWidget in CollapsibleSection (expanded by
default), and status bar at the bottom.

Tasks 17-21 fill in the panel content and wire up signals between them.
Conform to spec Section 3's Mindmeld composition checklist on every commit.
"""
__author__ = "Adrian Melian"

import os
import traceback
from pathlib import Path

from PySide6 import QtWidgets, QtCore, QtGui
from shiboken6 import isValid

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.maya.live_tool import LiveBackend, SubscriptionSet
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget
from maya_tools.utils.qt.mindmeld import frameless, mindmeld_style
from maya_tools.utils.qt import progress_card_guard as card_guard

from maya_tools.rigging.fabricator import (blueprint_library, fs_app,
                                           modules, nodes)
from maya_tools.rigging.fabricator.ui import state
from maya_tools.rigging.fabricator.ui.skeleton_helpers_bar import SkeletonHelpersBar
from maya_tools.rigging.fabricator.ui.build_options_bar import BuildOptionsBar
from maya_tools.utils.maya import side_tokens


_OPTION_VAR_W = 'FSFab_window_size_w'
_OPTION_VAR_H = 'FSFab_window_size_h'
_OPTION_VAR_SPLITTER = 'FSFab_main_splitter_state'
# Pre-rebrand optionVar names (2026-07-11, Tools Engineer S3) — read as
# a one-time fallback when the new key is absent, then the new key is
# written and takes over. Remove after v1.
_OLD_OPTION_VAR_W = 'KSFab_window_size_w'
_OLD_OPTION_VAR_H = 'KSFab_window_size_h'
_OLD_OPTION_VAR_SPLITTER = 'KSFab_main_splitter_state'

# Status-bar display labels for the internal mode constants. Decoupled
# from state.MODE_* so the logic-side names stay stable while the
# user-facing labels can match Adrian's workflow vocabulary (skeleton
# = Edit mode, modules_built = Animation mode).
_MODE_DISPLAY_NAMES = {
    'empty':         'Empty',
    'guides':        'Guides',
    'skeleton':      'Edit',
    'modules_built': 'Animation',
}

_DEFAULT_W, _DEFAULT_H = 1100, 720
# Floor low enough to shove the window aside while viewport-focused
# (Adrian, 2026-07-06: the old 920x640 minimum was "like half the
# screen"). The splitter panels squeeze and collapse when narrow.
_MIN_W, _MIN_H = 520, 400
# Animation-Mode cockpit (Adrian, 2026-07-05: "super stripped down") —
# rig name + Edit Rig + log needs a fraction of the Edit Mode frame.
_ANIM_W, _ANIM_H = 520, 300
_ANIM_MIN_W, _ANIM_MIN_H = 460, 240

_win = None


def _auto_detect_component_meta(joint_name: str, contract) -> tuple:
    """Auto-fill side / region / ctrl_color from a joint name token scan.

    Returns (side, region, options) where:
      - side is 'lf' | 'rt' | 'md' (md when no side token matches).
      - region is 'arm' | 'leg' | 'spine' | 'neck' | 'head' or '' (no
        token match falls back to contract.default_region, which is also
        usually '' except for IKLeg which sets 'leg').
      - options is a dict containing ctrl_color when the contract
        declares it, seeded from the detected side (lf=blue, rt=red,
        md=yellow — side seeding RESTORED 2026-07-14; the 2026-07-12
        family-color seeding made built ctrls carry the family color
        instead of the side convention, and Adrian reverted that. The
        family color language lives on in CONTRACT.color and the
        contract ctrl_color defaults for palette/canvas/outliner
        surfaces, never as the built ctrl color). Always writing the
        color — even when it happens to match the schema default —
        keeps the value explicit on the network node so build-time
        joint coloring can read it back without consulting the schema.

    Called by _add_component for both single-joint and multi-joint
    branches. Sensible defaults if any detection fails — never blocks
    the add; the user can always edit values in Properties afterward.
    """
    # Strip namespace + DAG path; detect_side/detect_region split on those.
    leaf = joint_name.split('|')[-1].split(':')[-1]
    side = side_tokens.detect_side(leaf)
    region = side_tokens.detect_region(leaf) or contract.default_region
    options = {}
    schema = getattr(contract, 'options_schema', None) or {}
    if 'ctrl_color' in schema:
        options['ctrl_color'] = side_tokens.side_to_ctrl_color(side)
    return side, region, options


class FSWindow(QtWidgets.QDialog):
    WINDOW_NAME = 'FSFab'
    WINDOW_TITLE = 'Fabricator — (no blueprint)'

    # State matrix: mode -> (label, variant, enabled) for the Build Rig button.
    # (MODE_GUIDES retired 2026-07-04 with the transient guide layer —
    # the Armature is the editable stage.)
    _BUILD_RIG_STATES = {
        state.MODE_EMPTY:          ('Build Rig',   'default', False),
        state.MODE_SKELETON:       ('Build Rig',   'primary', True),
        state.MODE_MODULES_BUILT:  ('Edit Rig',    'danger',  True),
    }

    def __init__(self, parent=None, docked=False):
        super().__init__(parent or get_maya_window())
        self.setObjectName(self.WINDOW_NAME)
        self.setWindowTitle(self.WINDOW_TITLE)
        # Two-window design (Adrian, 2026-07-06): docked=True means we
        # live as a child of the Fabricator workspaceControl
        # (fabricator_dock.py). Maya owns geometry and chrome there —
        # no frameless shell, no size prefs, no X.
        self._docked = bool(docked)
        # Animation Mode opens as the stripped cockpit (Adrian,
        # 2026-07-05): rig name, Edit Rig, log — nothing else. The mode
        # is fixed for this instance's lifetime (build/unbuild/scene-open
        # all reopen the window), so sizing and layout commit to it here.
        self._compact_mode = state.detect_mode() == state.MODE_MODULES_BUILT
        # saveWindowPref only for the floating Edit layout: at show time
        # Maya restores its saved geometry OVER our constructor resize,
        # which is exactly what flattened the cockpit's compact size
        # (Adrian: "the resizing didn't fire"). The cockpit opts out of
        # pref save and restore; _force_reopen hands position across.
        self.setProperty('saveWindowPref',
                         not (self._compact_mode or self._docked))
        if self._docked:
            # The workspaceControl owns geometry; the floating window's
            # minimum would force the dock pane huge.
            self.setMinimumSize(420, 320)
        elif self._compact_mode:
            self.setMinimumSize(_ANIM_MIN_W, _ANIM_MIN_H)
            self.resize(_ANIM_W, _ANIM_H)
        else:
            self.setMinimumSize(_MIN_W, _MIN_H)
            self.resize(*_load_window_size())

        # Frameless shell (Adrian, 2026-07-05): no OS title bar — the
        # brand row (create_layout) is the drag bar and carries the X.
        # Before apply(): the mindmeld="frameless" border rides the
        # dynamic property, which must exist when the style lands.
        # Docked face skips it: Maya draws the dock chrome.
        if not self._docked:
            frameless.adopt(self)

        mindmeld_style.apply(self)

        self.create_layout()
        self.connect_signals()
        self._restore_splitter_state()
        self._populate_palette_templates()
        # Live scene-event subscription. Hooks bound to CanvasPanel methods.
        # node_type_filter='joint' restricts DG add/remove callbacks to
        # joints — DAG firehose + global rename are unfiltered (handler-side).
        self._live_backend = LiveBackend(
            on_nodes_added=self.canvas_panel._on_live_nodes_added,
            on_nodes_removed=self.canvas_panel._on_live_nodes_removed,
            on_dag_changed=self.canvas_panel._on_live_dag_changed,
            on_renamed=self.canvas_panel._on_live_renamed,
            on_scene_reset=self.canvas_panel._on_live_scene_reset,
            debug=False,  # flip True for callback-fire / drain-size logging
            logger=self.logger,
        )
        self._live_backend.start(SubscriptionSet(
            scene_lifecycle=True,
            dg_node_added=True,
            dg_node_removed=True,
            dag_firehose=True,
            global_rename=True,
            node_type_filter='joint',
        ))

        # DAG watchers: ctrl-under-ctrl parenting (p / Edit > Parent /
        # outliner drag) funnels into the real branch reparent, and a
        # ctrl delete funnels into the real branch delete (joints +
        # aimers + modules). Removed in closeEvent next to the
        # Symmetry failsafe; results land in this window's logger and
        # the canvas repaints after each watcher-driven op.
        try:
            from maya_tools.rigging.fabricator import armature_watch
            armature_watch.install()
            armature_watch.set_reporter(self.logger.info)
            armature_watch.set_refresher(self.refresh_all)
        except Exception:
            traceback.print_exc()

        # Armature progress (item 5, 2026-07-05): armature.py reports
        # every build through this module-level hook — one install
        # covers all eleven call sites. Removed in closeEvent.
        try:
            from maya_tools.rigging.fabricator import armature
            armature.set_progress_reporter(self._on_armature_progress)
        except Exception:
            traceback.print_exc()

        # Scene-open / scene-new → auto-reopen the UI. Stale Edit-Mode state
        # surviving across a rig load was the original symptom; reopening
        # is the simple, sledgehammer fix.
        self._scene_callback_ids = []
        for ev in (om.MSceneMessage.kAfterOpen, om.MSceneMessage.kAfterNew):
            try:
                cid = om.MSceneMessage.addCallback(
                    ev, lambda *_: FSWindow._force_reopen()
                )
                self._scene_callback_ids.append(cid)
            except Exception:
                traceback.print_exc()

        if self._docked:
            # Dock death path: deleting the workspaceControl destroys
            # children WITHOUT closeEvent (deleteLater), so the Maya-
            # side teardown rides the destroyed signal. Captured refs
            # only — the C++ widget is already gone when this fires;
            # every step is idempotent against a prior closeEvent run.
            backend = self._live_backend
            cb_ids = self._scene_callback_ids
            canvas = self.canvas_panel

            def _dock_teardown(*_):
                try:
                    from maya_tools.rigging.fabricator import armature_mirror
                    if armature_mirror.is_enabled():
                        armature_mirror.disable(quiet=True)
                except Exception:
                    pass
                try:
                    from maya_tools.rigging.fabricator import armature_watch
                    armature_watch.set_reporter(None)
                    armature_watch.set_refresher(None)
                    armature_watch.remove()
                except Exception:
                    pass
                try:
                    from maya_tools.rigging.fabricator import armature
                    armature.set_progress_reporter(None)
                except Exception:
                    pass
                try:
                    backend.stop()
                except Exception:
                    pass
                try:
                    canvas.cleanup()   # Maya callback ids live Python-side
                except Exception:
                    pass
                for cid in list(cb_ids):
                    try:
                        om.MMessage.removeCallback(cid)
                    except Exception:
                        pass
                del cb_ids[:]

            self.destroyed.connect(_dock_teardown)

        self.refresh_all()

    def create_layout(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        # File menu (item 10, 2026-07-05): the menu BAR is retired — a
        # folder button in the brand row pops the same QMenu.
        file_menu = QtWidgets.QMenu('File', self)
        self._file_menu = file_menu
        # New Rig — the bring-your-own-skeleton entry point: adopts the
        # scene's existing skeleton (or seeds a root joint at origin in
        # an empty scene), names the rig, stands the Armature up, and
        # drops a World module on the root.
        self._new_rig_action = QtGui.QAction('New Rig...', self)
        self._new_rig_action.triggered.connect(self._on_new_rig_action)
        file_menu.addAction(self._new_rig_action)
        # Save Template — snapshot the current rig as a reusable template
        # in the project library. Loading lives in the palette's TEMPLATES
        # section (drag or double-click), not the File menu.
        #
        # Labeled for what it PRODUCES, not what it reads (2026-07-19,
        # Adrian): the output is a template and the palette already calls
        # it one, so "Save Rig" read as if it saved the scene. New Rig and
        # Delete Rig keep "Rig" — they act on the scene's rig, not the
        # library. (Attr/handler names stay _save_rig_* : internal, and
        # renaming them buys nothing a reader doesn't get from this note.)
        self._save_rig_action = QtGui.QAction('Save Template...', self)
        self._save_rig_action.triggered.connect(self._on_save_rig_action)
        file_menu.addAction(self._save_rig_action)
        # Destructive — separator + ellipsis label signals "prompts first".
        # Wipes rig_grp + guides_grp + pivots_grp + all Fabricator network
        # nodes + FAB_RigBinding nodes + _Joints/_Geo display layers.
        # Joints, geo, and skin clusters are preserved.
        file_menu.addSeparator()
        self._delete_rig_action = QtGui.QAction('Delete Rig...', self)
        self._delete_rig_action.triggered.connect(self._on_delete_rig_action)
        file_menu.addAction(self._delete_rig_action)
        # Re-entry for the guided tour. Its own separator: this is help,
        # not a rig operation, and it sits below the destructive row so a
        # stray click lands here rather than on Delete Rig.
        file_menu.addSeparator()
        self._tour_action = QtGui.QAction('Take the Tour', self)
        self._tour_action.triggered.connect(self._on_take_the_tour)
        file_menu.addAction(self._tour_action)
        for act in (self._new_rig_action, self._save_rig_action,
                    self._delete_rig_action):
            self.addAction(act)          # keep Ctrl+N / Ctrl+S alive

        # Brand bar — wrapped in the frameless shell's DragBar: with the
        # OS title bar gone this row is the window's grab handle, and the
        # ember X at its right edge is the close (runs the normal close()
        # path, so closeEvent teardown is untouched).
        self._brand_bar = frameless.DragBar()
        brand_row = QtWidgets.QHBoxLayout(self._brand_bar)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(10)
        # File button (item 10): RigBot's folder icon, InstantPopup on
        # the File menu built above. Left end — where a menu belongs.
        from maya_tools.framework.toolbar.widgets.icon_button import load_icon
        self._file_btn = QtWidgets.QToolButton()
        self._file_btn.setObjectName('fab_file_btn')
        _folder = load_icon('fs_folder.png')
        if _folder is not None:
            self._file_btn.setIcon(_folder)
            self._file_btn.setIconSize(QtCore.QSize(22, 22))
        else:
            self._file_btn.setText('FILE')   # icon missing — stay usable
        self._file_btn.setToolTip('File — New / Save / Delete Rig')
        self._file_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self._file_btn.setMenu(self._file_menu)
        brand_row.addWidget(self._file_btn)
        # Right-click the brand row to switch faces (two-window
        # design, 2026-07-06): floating offers Dock, docked offers
        # Undock.
        self._brand_bar.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._brand_bar.customContextMenuRequested.connect(
            self._on_brand_bar_menu)
        # Brand banner (Adrian, 2026-07-09): the FS Fabricator banner replaces
        # the Fabricator text. 1024x256 source shown at 152x38 (aspect 4.0,
        # no stretch). Text brand returns if the file ever goes missing.
        _logo = load_icon('fs_fabricator_banner.png')
        if _logo is not None:
            self._brand_logo = QtWidgets.QLabel()
            self._brand_logo.setPixmap(_logo.pixmap(QtCore.QSize(152, 38)))
            brand_row.addWidget(self._brand_logo)
        else:
            brand_row.addWidget(mindmeld_style.brand_label('Fabricator'))
        brand_row.addWidget(mindmeld_style.caps_label('// animation rig authoring'),
                            0, QtCore.Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        if not self._docked:
            # Docked face: Maya's dock tab carries the close; our X
            # would only hollow out the workspaceControl.
            brand_row.addWidget(frameless.close_button(self), 0,
                                QtCore.Qt.AlignmentFlag.AlignTop)
        root.addWidget(self._brand_bar)
        root.addWidget(mindmeld_style.horizontal_rule())

        # SkeletonHelpersBar (the Armature toolbox) sits ABOVE the
        # rig-name input (Adrian, 2026-07-05 brain dump, item 4).
        self.skeleton_helpers_bar = SkeletonHelpersBar()
        root.addWidget(self.skeleton_helpers_bar)

        # Rig-name row — auto-fills from the registry (or derive on first
        # template drop) and lets the user override. The override persists
        # on the fab_registry's rig_label string attr so it survives
        # scene save/load and Save As. Hidden in MODE_MODULES_BUILT
        # (the bottom status bar shows the name there; renaming a built
        # rig isn't supported in this pass).
        self.rig_name_row = QtWidgets.QWidget()
        rig_name_layout = QtWidgets.QHBoxLayout(self.rig_name_row)
        rig_name_layout.setContentsMargins(0, 0, 0, 0)
        rig_name_layout.setSpacing(6)
        rig_name_layout.addWidget(mindmeld_style.field_label('Rig Name:'))
        self.rig_name_edit = QtWidgets.QLineEdit()
        self.rig_name_edit.setPlaceholderText('(no rig in scene)')
        # Locked by default — renaming is a deliberate act (Edit button),
        # not a stray click into the field. Re-locks on commit.
        self.rig_name_edit.setReadOnly(True)
        self.rig_name_edit.editingFinished.connect(self._on_rig_name_committed)
        rig_name_layout.addWidget(self.rig_name_edit, stretch=1)
        self.rig_name_edit_btn = mindmeld_style.button('Edit', kind='ghost')
        self.rig_name_edit_btn.setToolTip(
            'Rename the rig. The field locks again when you press Enter '
            'or click away.')
        self.rig_name_edit_btn.clicked.connect(self._on_rig_name_edit_clicked)
        rig_name_layout.addWidget(self.rig_name_edit_btn)
        root.addWidget(self.rig_name_row)

        # Three middle panels in a QSplitter (Tasks 18-20). ToolsPanel
        # (curve-o-matic + control list) retired 2026-07-05 — Adrian
        # never used it; Animation Mode is the stripped cockpit now, so
        # the splitter is Edit Mode only and Properties sits in slot 2
        # directly (the old QStackedWidget went with the panel).
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        # Named for the QSS panel-gap treatment (item 7): the handle is
        # a 15px invisible gap scoped to this splitter only.
        self.splitter.setObjectName('fab_splitter')
        from maya_tools.rigging.fabricator.ui.palette_panel import PalettePanel
        from maya_tools.rigging.fabricator.ui.canvas_panel import CanvasPanel
        from maya_tools.rigging.fabricator.ui.properties_panel import PropertiesPanel
        self.palette_panel = PalettePanel()
        self.canvas_panel = CanvasPanel()
        self.properties_panel = PropertiesPanel()
        self.splitter.addWidget(self.palette_panel)
        self.splitter.addWidget(self.canvas_panel)
        self.splitter.addWidget(self.properties_panel)

        # Wire palette ↔ canvas: canvas tells palette how many joints are
        # selected (drives palette enable/disable); canvas asks palette for
        # the right-click 'Add Component' submenu.
        self.canvas_panel.add_canvas_selection_observer(
            self.palette_panel.set_canvas_selection)
        self.canvas_panel.set_palette_menu_provider(
            self.palette_panel.menu_for_canvas_selection)

        # Wire canvas → properties (stacked when both selected).
        self._pending_joint_selection = ''
        self._pending_component_selection = ''
        self.canvas_panel.joint_selected.connect(self._on_canvas_joint_selected)
        self.canvas_panel.component_selected.connect(self._on_canvas_component_selected)
        self.splitter.setStretchFactor(0, 18)
        self.splitter.setStretchFactor(1, 50)
        self.splitter.setStretchFactor(2, 32)
        root.addWidget(self.splitter, stretch=1)

        # Per-build flag bar above the Build button — mode-hidden in
        # MODE_MODULES_BUILT (refresh_all routes refresh_state to it).
        # Checkboxes flow into build_modules(options=…) and auto-uncheck
        # after a successful build.
        # Build Options — collapsible, collapsed by default (Adrian,
        # 2026-07-04). Holds the per-build flag bar (Reset Control
        # Shapes today; future options land in the same body).
        self.build_options_bar = BuildOptionsBar()
        self.options_section = CollapsibleSection('// BUILD OPTIONS')
        options_body = QtWidgets.QVBoxLayout()
        options_body.setContentsMargins(0, 0, 0, 0)
        options_body.addWidget(self.build_options_bar)
        self.options_section.set_body_layout(options_body)
        self.options_section.set_expanded(False)
        root.addWidget(self.options_section)

        # Phase buttons under the canvas — one row, Build Rig alone.
        # (Skip-guides experiment: template load now chains create_guides
        # + build_skeleton internally, so users see one button only.)
        self.build_rig_btn = mindmeld_style.button('Build Rig', kind='primary')
        self.build_rig_btn.clicked.connect(self._on_build_rig_clicked)
        root.addWidget(self.build_rig_btn)

        # Logger in CollapsibleSection. The collapsed header shows the
        # latest log line automatically — CollapsibleSection auto-wires
        # any LoggerWidget in its body (base feature), so no manual
        # signal handling is needed here.
        self.log_section = CollapsibleSection('// LOG')
        self.logger = LoggerWidget()
        log_layout = QtWidgets.QVBoxLayout()
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addWidget(self.logger)
        self.log_section.set_body_layout(log_layout)
        self.log_section.set_expanded(False)
        root.addWidget(self.log_section)

        # Status bar — live observers wired in refresh_all(). Wrapped in
        # a widget so the cockpit (Animation Mode) can hide it whole —
        # a bare layout has no setVisible.
        self.status_bar_widget = QtWidgets.QWidget()
        self.status_bar = QtWidgets.QHBoxLayout(self.status_bar_widget)
        self.status_bar.setContentsMargins(0, 0, 0, 0)
        self.status_bar.setSpacing(8)
        self._status_pill_mindmeld = mindmeld_style.pill('MINDMELD', 'idle')
        self._status_blueprint_label = mindmeld_style.caps_label('// no blueprint')
        self._status_mode_label = mindmeld_style.caps_label('// mode: empty')
        self._status_components_label = mindmeld_style.caps_label('// components: 0')
        self._status_maya_label = mindmeld_style.caps_label(
            f'// maya {cmds.about(version=True)}')
        self._status_progress = QtWidgets.QProgressBar()
        self._status_progress.setVisible(False)
        self._status_progress.setMaximumWidth(180)
        self._status_progress.setMaximumHeight(14)
        self._status_progress.setTextVisible(True)
        self._status_progress.setFormat('Building: %v / %m')
        for w in (self._status_pill_mindmeld, self._status_blueprint_label,
                  self._status_mode_label, self._status_components_label):
            self.status_bar.addWidget(w)
        self.status_bar.addWidget(self._status_progress)
        self.status_bar.addStretch(1)
        self.status_bar.addWidget(self._status_maya_label)
        # Progress-only (brain dump items 1+6, 2026-07-05): the info bar
        # stays dark until a build runs, then vanishes again.
        self._progress_active = False
        self.status_bar_widget.setVisible(False)
        # Template-load reentrancy latch — see _on_blueprint_selected.
        self._template_load_active = False
        root.addWidget(self.status_bar_widget)

        if self._compact_mode:
            # Cockpit: keep the few rows top-anchored — without this,
            # leftover height (the splitter's old stretch slot) spreads
            # the rows apart instead of pooling at the bottom.
            root.addStretch(1)

    def connect_signals(self):
        bar = self.skeleton_helpers_bar
        bar.add_joint_requested.connect(self._on_add_joint_requested)
        bar.insert_between_requested.connect(
            self._on_insert_between_requested)
        bar.build_engine_ik_requested.connect(
            self._on_build_engine_ik_requested)
        bar.aim_joints_requested.connect(self._on_aim_joints_requested)
        bar.aim_all_at_child_requested.connect(
            self._on_aim_all_at_child_requested)
        bar.straighten_mid_requested.connect(
            self._on_straighten_mid_requested)
        bar.solo_visibility_toggle_requested.connect(
            self._on_solo_visibility_toggle_requested)
        bar.solo_reset_requested.connect(self._on_solo_reset_requested)
        bar.aimers_mirror_selected_requested.connect(
            self._on_aimers_mirror_selected_requested)
        bar.aimers_rebuild_all_requested.connect(
            self._on_aimers_rebuild_all_requested)
        bar.aimers_reset_all_requested.connect(
            self._on_aimers_reset_all_requested)
        bar.aimers_visibility_toggle_requested.connect(
            self._on_aimers_visibility_toggle_requested)
        bar.mirror_limb_requested.connect(self._on_mirror_limb_requested)
        bar.mirror_joints_requested.connect(
            self._on_mirror_joints_requested)
        bar.mirror_module_requested.connect(
            self._on_mirror_module_requested)
        bar.duplicate_limb_requested.connect(
            self._on_duplicate_limb_requested)
        bar.duplicate_joints_requested.connect(
            self._on_duplicate_joints_requested)
        bar.live_mirror_toggled.connect(self._on_live_mirror_toggled)
        self.palette_panel.template_selected.connect(self._on_blueprint_selected)
        self.palette_panel.templates_changed.connect(
            self._populate_palette_templates)
        self.palette_panel.library_message.connect(self._on_library_message)
        self.canvas_panel.template_drop_requested.connect(self._on_blueprint_selected)

        # Palette double-click → add component to canvas selection
        self.palette_panel.component_double_clicked.connect(self._on_palette_double_click)

        # Canvas → host: add/remove component lifecycle
        self.canvas_panel.add_component_requested.connect(self._on_add_component_requested)
        self.canvas_panel.remove_component_requested.connect(self._on_remove_component_requested)

        # Canvas → host: joint rename via inline edit (double-click / F2)
        self.canvas_panel.rename_requested.connect(self._commit_joint_rename)

        self.canvas_panel.save_limb_requested.connect(self._on_save_limb_requested)
        self.canvas_panel.limb_drop_requested.connect(self._on_limb_drop_requested)

        # Canvas → host: MMB drag-drop reparent (branch onto new parent)
        self.canvas_panel.reparent_requested.connect(self._on_reparent_requested)
        # SingleJoint drop shares the Skeleton Helpers Bar's handler —
        # the drop passes its target row, the bar passes nothing.
        self.canvas_panel.add_joint_requested.connect(
            self._on_add_joint_requested)

        # Canvas → host: right-click branch ops (rooted at clicked row)
        self.canvas_panel.mirror_branch_requested.connect(
            self._on_canvas_mirror_branch)
        self.canvas_panel.duplicate_branch_requested.connect(
            self._on_canvas_duplicate_branch)
        self.canvas_panel.delete_branches_requested.connect(
            self._on_delete_branches_requested)

        # Esc clears the canvas selection. Window-scoped, so it never shadows
        # Maya's global Esc. (Delete is deliberately NOT bound — it was
        # overriding Maya's viewport delete.)
        self._install_shortcuts()

    def _on_take_the_tour(self):
        """File > Take the Tour — run it regardless of the seen-flag, and
        do not spend the flag (an explicit request is not a first run)."""
        try:
            from maya_tools.rigging.fabricator.ui import fabricator_tour
            fabricator_tour.run(force=True)
        except Exception as e:
            self._handle_error(e, brief='Tour failed to start')

    def showEvent(self, event):
        """First show of the session offers the guided tour, once ever.

        Deferred inside maybe_run_on_open so the splitter has laid out and
        the anchors have real geometry to point at. Guarded twice over:
        once here, once inside the tour, because onboarding must never
        wound the window it is describing. The docked face opts out — a
        workspaceControl can show during a Maya workspace restore with no
        one looking at it.
        """
        super().showEvent(event)
        if getattr(self, '_tour_checked', False):
            return
        self._tour_checked = True
        if self._docked or self._compact_mode:
            return
        try:
            from maya_tools.rigging.fabricator.ui import fabricator_tour
            fabricator_tour.maybe_run_on_open()
        except Exception:
            traceback.print_exc()

    def _refresh_phase_buttons(self, mode: str):
        """Update label / variant / enabled for the single Build Rig button.

        Skip-guides experiment: Create Guides + Build Skeleton are gone;
        template load chains them internally. Only Build Rig (or its
        Unbuild Rig variant in MODULES_BUILT mode) is visible.
        """
        label, variant, enabled = self._BUILD_RIG_STATES.get(
            mode, self._BUILD_RIG_STATES[state.MODE_EMPTY])
        has_registry = nodes.get_registry() is not None
        self.build_rig_btn.setText(label.upper())
        mindmeld_style.tag(self.build_rig_btn, variant)
        self.build_rig_btn.setEnabled(enabled and has_registry)

    def _on_build_rig_clicked(self):
        mode = state.detect_mode()
        action = 'unbuild_modules' if mode == state.MODE_MODULES_BUILT else 'build_modules'
        self._on_phase_action(action)

    def _on_canvas_joint_selected(self, joint_name: str):
        self._pending_joint_selection = joint_name or ''
        self._refresh_properties_panel()

    def _on_canvas_component_selected(self, component_id: str):
        self._pending_component_selection = component_id or ''
        self._refresh_properties_panel()

    def _commit_joint_rename(self, old_name: str, new_name: str) -> None:
        """Rename a joint + migrate its aimer. Routes through
        cmds.rename which fires LiveBackend's wildcard rename hook;
        CanvasPanel.rename_row updates the canvas surgically.

        Maya may auto-bump the new name's suffix on collision (e.g.
        'spine' becomes 'spine1' if 'spine' is taken). The returned
        actual_new name reflects what Maya actually used.

        Aimer migration: aimer node names are keyed to the joint name,
        so a rename would strand the old aimer and crash the next
        orientation bake. Capture state → delete old aimer → rename →
        recreate + reapply. Wrapped in its own try/except so a locked/
        odd aimer never blocks the joint rename itself. Armature ctrl
        names stay stale until the next Aim/rebuild — harmless.

        Scene-is-truth: no in-memory blueprint to sync. PropertiesPanel
        re-queries the scene on the next selection."""
        new_name = (new_name or '').strip()
        if not new_name or new_name == old_name:
            return  # silent no-op

        # Capture + drop the old-named aimer BEFORE the rename.
        aimer_state = None
        had_aimer = False
        try:
            from maya_tools.rigging.joint_orient import joint_orient_app as joa
            if joa.aimer_exists(old_name):
                had_aimer = True
                aimer_state = joa.get_aimer_state(old_name)
                joa.delete_aimer(old_name)
        except Exception as ea:
            self.logger.warning(f'Aimer capture failed for {old_name}: {ea}')

        try:
            actual_new = cmds.rename(old_name, new_name)
        except Exception as e:
            self._handle_error(e, brief=f'Rename failed: {old_name} → {new_name}')
            self.canvas_panel._refresh_all_labels()
            if self._pending_joint_selection == old_name:
                self._refresh_properties_panel()
            return

        # Recreate the aimer under the new name, state preserved.
        if had_aimer:
            try:
                from maya_tools.rigging.joint_orient import joint_orient_app as joa
                joa.create_aimer(actual_new)
                if aimer_state:
                    joa.apply_aimer_state(
                        actual_new,
                        aim_target=aimer_state['aim_target'],
                        aim_offset=aimer_state['aim_offset'])
            except Exception as ea:
                self.logger.warning(
                    f'Aimer rebuild failed for {actual_new}: {ea}. '
                    f'Run Aim Joints to regenerate.')

        # The parent's aimer enum lists child NAMES — rebuild it so the
        # label tracks the rename, remapping its target if it aimed at
        # this child. (Aim constraints connect by plug, so aiming never
        # broke — only the label and persisted name would go stale.)
        try:
            from maya_tools.rigging.joint_orient import joint_orient_app as joa
            par_list = cmds.listRelatives(actual_new, parent=True,
                                          type='joint') or []
            par = par_list[0] if par_list else None
            if par and joa.aimer_exists(par):
                pstate = joa.get_aimer_state(par)
                if pstate and pstate['aim_target'] == old_name:
                    pstate['aim_target'] = actual_new.split('|')[-1]
                joa.delete_aimer(par)
                joa.create_aimer(par)
                if pstate:
                    joa.apply_aimer_state(par,
                                          aim_target=pstate['aim_target'],
                                          aim_offset=pstate['aim_offset'])
        except Exception as ea:
            self.logger.warning(
                f'Parent aimer refresh failed after rename: {ea}. '
                f'Run Aim Joints to regenerate.')

        self.logger.info(f'Renamed {old_name} → {actual_new}')
        # Properties panel re-show if currently showing for old joint.
        if self._pending_joint_selection == old_name:
            self._pending_joint_selection = actual_new
            self._refresh_properties_panel()

    def _refresh_properties_panel(self):
        """Route the pending canvas selection to the right show_for_* call.

        canvas emits joint_selected and component_selected as TWO independent
        signals back-to-back on every selection change, and each handler calls
        this refresh — so a selection change rebuilds the panel TWICE, the
        first pass transiently pairing the new value with the other signal's
        not-yet-updated pending value. Invisible in practice (both fire
        synchronously, before any repaint) and cheap, so the double rebuild
        is accepted rather than deferred/coalesced (depot review, 2026-07-10
        — a zero-timer coalesce would make the rebuild async and change what
        callers and tests observe synchronously)."""
        if self._pending_joint_selection and self._pending_component_selection:
            self.properties_panel.show_for_both(
                self._pending_joint_selection, self._pending_component_selection)
        elif self._pending_joint_selection:
            self.properties_panel.show_for_joint(self._pending_joint_selection)
        elif self._pending_component_selection:
            self.properties_panel.show_for_component(self._pending_component_selection)
        else:
            self.properties_panel.clear()

    # ─── Keyboard shortcuts ──────────────────────────────────────────────────

    def _install_shortcuts(self):
        # Esc only, window-scoped (QShortcut default context = WindowShortcut),
        # so it fires solely when this window has focus and never shadows Maya's
        # global Esc. Delete is deliberately unbound — it was overriding Maya's
        # viewport delete.
        QtGui.QShortcut(QtGui.QKeySequence('Esc'), self,
                        activated=self._on_escape)

    def _on_escape(self):
        self.canvas_panel.tree.clearSelection()

    # ─── Phase actions ───────────────────────────────────────────────────────

    def _on_phase_action(self, action: str):
        try:
            if action == 'build_modules':
                # Standardized pre-build gate: every detected build
                # break (duplicated joints, orphaned modules, missing
                # aimers, ...) in ONE window with per-issue fixes.
                # Detection + fixers live in fabricator/build_checks.py.
                from maya_tools.rigging.fabricator import build_checks
                from maya_tools.rigging.fabricator.ui.build_issues_dialog import (
                    BuildIssuesDialog)
                issues = build_checks.run_prebuild_checks()
                force_missing = False
                if issues:
                    decision = BuildIssuesDialog.run_gate(
                        self, issues, logger=self.logger)
                    if decision is None:
                        self.logger.info(
                            'Build cancelled from pre-build checks.')
                        return
                    force_missing = decision.get(
                        'force_missing_aimers', False)
                self._begin_build_progress()
                owns_card = card_guard.start('Building rig',
                                             'Preparing scene...')
                # Snapshot BuildOptionsBar checkboxes into a dict for
                # this build. Empty dict (no flags checked) preserves
                # the legacy build path exactly.
                build_options = self.build_options_bar.collect_options()
                build_ok = False
                try:
                    fs_app.build_modules(
                        progress_cb=self._on_build_progress,
                        options=build_options,
                        force_missing_aimers=force_missing,
                    )
                    build_ok = True
                finally:
                    self._end_build_progress()
                    if owns_card:
                        if build_ok:
                            card_guard.finish('Rig built')
                        else:
                            card_guard.fail('Build failed. See the log.')
                # Auto-uncheck destructive one-shot flags so the next
                # build doesn't accidentally repeat them.
                self.build_options_bar.reset_after_build()
                # Mention used options in the log so it's obvious which
                # build behaviors fired.
                if build_options:
                    used = ', '.join(sorted(build_options.keys()))
                    self.logger.info(f'Built modules. (options: {used})')
                else:
                    self.logger.info('Built modules.')
                self._toast('Success! Rig Built')
            elif action == 'unbuild_modules':
                # Busy card: unbuild has no countable phases, but its
                # armature re-stand epilogue ticks the same card
                # determinate via _on_armature_progress.
                owns_card = card_guard.start('Unbuilding rig',
                                             'Returning to Edit Mode...',
                                             busy=True)
                unbuild_ok = False
                try:
                    fs_app.unbuild_modules()
                    unbuild_ok = True
                finally:
                    if owns_card:
                        if unbuild_ok:
                            card_guard.finish('Rig unbuilt')
                        else:
                            card_guard.fail('Unbuild failed. See the log.')
                self.logger.info('Unbuilt modules — state captured.')
                self._toast('Success! Rig Unbuilt')
        except Exception as e:
            self._handle_error(e, brief=f'{action} failed')
            return
        self.refresh_all()
        # Build/unbuild flips mode (Edit ↔ Animation). Auto-reopen the
        # window so the right panel and canvas are reconstructed cleanly
        # — sidesteps any cached UI state.
        if action in ('build_modules', 'unbuild_modules'):
            FSWindow._force_reopen()

    def _toast(self, message: str) -> None:
        """Centered Mindmeld confirmation popover. Parented to Maya's main
        window (not this one), so it survives the build/unbuild reopen.
        Fully guarded — a toast never blocks the action it confirms."""
        try:
            from maya_tools.utils.qt import toast
            toast.show_toast(message)
        except Exception:
            pass

    def _on_build_engine_ik_requested(self) -> None:
        """Skeleton Helpers Bar — Build Engine IK handler. Creates or
        re-snaps the UE5 engine-reference joint set (engine_ik.py) and
        reports per-joint results through the logger."""
        try:
            from maya_tools.rigging.fabricator import engine_ik
            report = engine_ik.build_engine_ik_joints()
        except Exception as e:
            self._handle_error(e, brief='Build Engine IK failed')
            return
        if report['created']:
            self.logger.info(
                f"Engine IK: created {', '.join(report['created'])}")
        if report['snapped']:
            self.logger.info(
                f"Engine IK: snapped {', '.join(report['snapped'])}")
        if report['components_created']:
            self.logger.info(
                f"Engine IK: follow components "
                f"{', '.join(report['components_created'])}")
        for joint, target in report['missing_targets'].items():
            self.logger.warning(
                f'Engine IK: no {target!r} in this scene for {joint!r} — '
                f'joint parked at its parent, follow target recorded.')
        self.refresh_all()

    def _on_add_joint_requested(self, target: str = '') -> None:
        """Add Joint handler — serves BOTH gestures.

        Skeleton Helpers Bar > Add Joint arrives with no target: the
        smart joint create, same command RigBot's Create popover runs
        (Alt+J behavior), child of the last-selected joint so presses
        chain, else a free joint at world origin. A SingleJoint dropped
        on a canvas row arrives with that row's joint as `target`.

        fs_app owns the scene work: create the joint AND wrap it into
        the Armature (ctrl, aimer, `_Joints` layer). refresh_all() is
        mandatory here, not cosmetic — the rebuild replaces the whole
        ctrl tree, so a surgical canvas row insert would not cover it.
        """
        try:
            from maya_tools.rigging.fabricator import fs_app
            new_joint = fs_app.add_single_joint(target=target or None)
        except Exception as e:
            self._handle_error(e, brief='Add Joint failed')
            return
        self.logger.info(f'Added joint: {new_joint}')
        self.refresh_all()

    def _on_insert_between_requested(self) -> None:
        """Joints panel — Insert Joints Between. Thin: validation, the
        count prompt, and the chain call live in skeleton_utils (same
        helper as the shelf button)."""
        try:
            from maya_tools.skeleton.skeleton_utils import (
                insert_joints_between_selection,
            )
            count = insert_joints_between_selection(parent_window=self)
            if count is None:
                return  # cancelled / validation failed (helper showed dialog)
            self.logger.info(
                f'Inserted {count} joint(s) between selection.')
            self.refresh_all()
        except Exception as e:
            self._handle_error(e, brief='Insert Between failed')

    def _on_aimers_mirror_selected_requested(self) -> None:
        """Aimers panel — Mirror Selected Aimers: copy the selected
        aimer(s) onto their opposite-side counterparts.

        Same call the framework toolbar's Aimers popover runs, so both
        entry points behave identically. One-shot and aimers-only: it
        does NOT re-orient the joints (Aim Joints at Aimers or a build
        does that), and it does not rebuild the Armature — nothing
        structural changes, only aimer rotations."""
        try:
            from maya_tools.rigging.joint_orient import (
                joint_orient_app as joa,
            )
            result = joa.mirror_aimers()
            mirrored = result.get('mirrored', 0)
            skipped = result.get('skipped', 0)
            if mirrored:
                self.logger.info(
                    f'Mirrored {mirrored} aimer(s) to the opposite '
                    f'side. Joints are NOT re-oriented yet — run Aim '
                    f'Joints at Aimers, or build.')
            else:
                self.logger.warning(
                    'No aimer was mirrored. Select the aimer ctrl(s) '
                    'themselves (the XYZ arrows under JntOrient_GRP), '
                    'not the joints or Armature ctrls.')
            for reason in (result.get('reasons') or [])[:8]:
                self.logger.warning(f'skipped: {reason}')
            if skipped > 8:
                self.logger.warning(
                    f'... and {skipped - 8} more skipped.')
        except Exception as e:
            self._handle_error(e, brief='Mirror Selected Aimers failed')

    def _on_aimers_rebuild_all_requested(self) -> None:
        """Aimers panel — Rebuild All Aimers: recreate every aimer with
        its state preserved (fixes stale child enums after topology
        edits), then rebuild the Armature so edges re-derive."""
        owns_card = card_guard.start('Rebuilding aimers',
                                     'Recreating aimers...', busy=True)
        try:
            from maya_tools.rigging.joint_orient import (
                joint_orient_app as joa,
            )
            from maya_tools.rigging.fabricator import armature
            with joa.undo_chunk('RebuildAllAimers'):
                count = 0
                for j in cmds.ls(type='joint') or []:
                    if joa.aimer_exists(j):
                        joa.rebuild_aimer(j)
                        count += 1
                armature.build_armature()
        except Exception as e:
            if owns_card:
                card_guard.fail('Rebuild All Aimers failed. See the log.')
            self._handle_error(e, brief='Rebuild All Aimers failed')
            return
        if owns_card:
            card_guard.finish(f'{count} aimer(s) rebuilt')
        self.logger.info(
            f'Rebuilt {count} aimer(s), state preserved; Armature '
            f'rebuilt.')
        self.refresh_all()

    def _on_aimers_reset_all_requested(self) -> None:
        """Aimers panel — Reset All Aimers: delete every aimer and
        re-seed fresh from geometric detection (build_armature's
        _ensure_aimers). Authored targets/offsets are lost — confirms
        first."""
        reply = QtWidgets.QMessageBox.question(
            self, 'Reset All Aimers',
            'Delete every aimer and re-seed from geometric detection?\n\n'
            'Authored aim targets and offsets (Local/World, twists) '
            'are LOST — detection only recognizes joints already '
            'aiming at a child.',
            QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        owns_card = card_guard.start('Resetting aimers',
                                     'Re-seeding from detection...',
                                     busy=True)
        try:
            from maya_tools.rigging.joint_orient import (
                joint_orient_app as joa,
            )
            from maya_tools.rigging.fabricator import armature
            with joa.undo_chunk('ResetAllAimers'):
                joa.delete_all_aimers()
                armature.build_armature()
        except Exception as e:
            if owns_card:
                card_guard.fail('Reset All Aimers failed. See the log.')
            self._handle_error(e, brief='Reset All Aimers failed')
            return
        if owns_card:
            card_guard.finish('Aimers reset')
        self.logger.info('All aimers reset — re-seeded from '
                         'geometric detection.')
        self.refresh_all()

    def _on_aimers_visibility_toggle_requested(self) -> None:
        """Aimers panel — Show/Hide: flip the _aimers display layer."""
        layer = '_aimers'
        if not cmds.objExists(layer):
            self.logger.warning(
                'No _aimers layer yet — build the Armature first.')
            return
        vis = cmds.getAttr(f'{layer}.visibility')
        cmds.setAttr(f'{layer}.visibility', not vis)
        self.logger.info(f'Aimers {"hidden" if vis else "shown"}.')

    # ─── Kris requests, 2026-07-21 ───────────────────────────────────────────

    def _log_reasons(self, reasons: list, cap: int = 40) -> None:
        """Spill an audit trail into the log without flooding it."""
        for line in reasons[:cap]:
            self.logger.info(f'  {line}')
        if len(reasons) > cap:
            self.logger.info(f'  ... and {len(reasons) - cap} more.')

    def _on_aim_all_at_child_requested(self) -> None:
        """Aimers panel — Aim All at Child: fabricate aim targets from
        the hierarchy.

        The counterpart to the conservative seeder, which only assigns a
        target when a joint ALREADY aims at a child within 0.75 degrees.
        On an imported skeleton nothing matches, so every aimer sits on
        Local and the bake is a no-op — this is the fix for that.

        Deliberately does NOT re-orient the joints or rebuild: same
        grammar as Mirror Selected Aimers, so the rigger can eyeball the
        aimers first and then commit with Aim Joints at Aimers.
        """
        # Short on purpose (Adrian, 2026-07-21). The long-form warning
        # was moved to the menu tooltip and the post-run log: the log
        # NAMES every authored state replaced, which is more useful
        # after the fact than a wall of text before it.
        reply = QtWidgets.QMessageBox.question(
            self, 'Aim All at Child',
            'Aim every aimer at its child?',
            QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        owns_card = card_guard.start('Aiming at children',
                                     'Walking the hierarchy...', busy=True)
        try:
            from maya_tools.rigging.joint_orient import (
                joint_orient_app as joa,
            )
            result = joa.aim_all_at_child()
        except Exception as e:
            if owns_card:
                card_guard.fail('Aim All at Child failed. See the log.')
            self._handle_error(e, brief='Aim All at Child failed')
            return
        if owns_card:
            card_guard.finish(f"{result['aimed']} aimer(s) aimed")
        try:
            self.logger.success(
                f"Aim All at Child: {result['aimed']} aimed, "
                f"{result['skipped']} skipped. Run Aim Joints at Aimers "
                f"to bake it in.")
            self._log_reasons(result['reasons'])
            self.refresh_all()
        except Exception as e:
            self._handle_error(e, brief='Aim All at Child failed')

    def _on_straighten_mid_requested(self) -> None:
        """Skeleton panel — Straighten Mid Joint: flatten a knee/elbow on
        one axis, keeping its deliberate bend on the other two.

        Writes the CTRL when a live Armature owns the joint — joints are
        driven during the edit stage, so a direct joint write gets
        stomped on the next evaluation.
        """
        owns_card = card_guard.start('Straightening', 'Solving...',
                                     busy=True)
        try:
            from maya_tools.rigging.fabricator import planar_align, armature
            r = planar_align.align_mid_joint()
            dev = r['deviations']
            self.logger.success(
                f"Straightened {r['mid']} on {r['axis'].upper()} "
                f"(moved the {r['drove']}).")
            self.logger.info(
                f"  deviations x={dev['x']:.4f} y={dev['y']:.4f} "
                f"z={dev['z']:.4f}; bend on the other two axes kept.")
            # Commit, same rule as putting the cages away (Adrian,
            # 2026-07-21). The straighten lands in a solo handle, so
            # without this the correction is real but invisible: the
            # joint sits off its ctrl with nothing on screen saying why.
            # No-op when there is nothing pending (a plain skeleton with
            # no Armature never gets here with a nudge). Commits every
            # outstanding nudge, not just this one — the rebuild is
            # global by nature — so the count is logged rather than
            # implied.
            committed = armature.commit_solo_nudges()
            if committed:
                extra = (f' (plus {committed - 1} earlier nudge(s))'
                         if committed > 1 else '')
                self.logger.info(
                    f'  committed to placement{extra}; Armature rebuilt, '
                    f'handles re-zeroed.')
                self.refresh_all()
        except Exception as e:
            if owns_card:
                card_guard.fail('Straighten failed. See the log.')
            self._handle_error(e, brief='Straighten Mid Joint failed')
            return
        if owns_card:
            card_guard.finish(f"{r['mid']} straightened")

    def _on_solo_visibility_toggle_requested(self) -> None:
        """Skeleton panel — Show/Hide Solo Handles: flip the _solo layer.

        A solo handle moves ONLY its own joint; the main ctrl still
        drags the whole subtree. Visibility is display-only, so the
        drive network keeps evaluating while they are hidden.
        """
        # Bound before the try: the card is opened partway down, so an
        # exception raised above it would otherwise hit the handler's
        # own `if owns_card` with the name unbound.
        owns_card = False
        try:
            from maya_tools.rigging.fabricator import armature
            if not armature.solo_handles():
                self.logger.warning(
                    'No solo handles yet — build the Armature first.')
                return
            hiding = armature.solo_handles_visible()
            committed = 0
            # Card only when there is a rebuild to wait on — a plain
            # show/hide is instant and a card would just flash.
            owns_card = (card_guard.start('Committing nudges',
                                          'Rebuilding Armature...',
                                          busy=True)
                         if hiding and armature.pending_solo_nudges()
                         else False)
            if hiding:
                # Commit BEFORE hiding (Adrian, 2026-07-21): putting the
                # cages away is the natural "I'm done nudging" moment,
                # and a nonzero handle behind a hidden cage is invisible
                # state — the joint sits off its ctrl with nothing on
                # screen explaining it. Committing first also means a
                # failed rebuild leaves everything visible and unchanged
                # rather than half-applied. No nudges = no rebuild, so
                # a look-and-hide stays instant.
                committed = armature.commit_solo_nudges()
            shown = armature.toggle_solo_handles()
        except Exception as e:
            if owns_card:
                card_guard.fail('Commit failed. See the log.')
            self._handle_error(e, brief='Show / Hide Solo Handles failed')
            return
        if owns_card:
            card_guard.finish(f'{committed} nudge(s) committed')
        if committed:
            self.logger.success(
                f'Solo handles hidden — {committed} nudge(s) '
                f'committed, Armature rebuilt, handles re-zeroed.')
            self.refresh_all()
        else:
            self.logger.info(
                f'Solo handles {"shown" if shown else "hidden"}.')

    def _on_solo_reset_requested(self) -> None:
        """Skeleton panel — Reset Solo Handles: zero the nudges.

        Selection-scoped when the rigger has joints, ctrls or handles
        selected; whole-Armature otherwise. Nothing below a reset joint
        moves, exactly as when the nudge was made.
        """
        try:
            from maya_tools.rigging.fabricator import armature
            joints = self._selected_armature_joints()
            count = armature.reset_solo_handles(joints or None)
            scope = f'{len(joints)} selected' if joints else 'all'
            self.logger.success(
                f'Reset {count} solo handle(s) ({scope}).')
        except Exception as e:
            self._handle_error(e, brief='Reset Solo Handles failed')

    @staticmethod
    def _selected_armature_joints() -> list:
        """Selected nodes resolved back to joints — accepts the joint
        itself, its `_amt_CTL`, or its `_amtSolo` handle, since all
        three are things a rigger might have clicked in the viewport."""
        out = []
        for node in cmds.ls(selection=True, long=True) or []:
            short = node.split('|')[-1]
            if cmds.objExists(node) and cmds.nodeType(node) == 'joint':
                out.append(short)
                continue
            for suffix in ('_amt_CTL', '_amtSolo'):
                if short.endswith(suffix):
                    candidate = short[:-len(suffix)]
                    if (cmds.objExists(candidate)
                            and cmds.nodeType(candidate) == 'joint'):
                        out.append(candidate)
                    break
        return out

    def _run_mirror_branch(self, include_modules: bool, label: str,
                           root: str = None):
        """root=None resolves from the viewport selection (toolbar
        path); the canvas context menu passes its clicked row."""
        # Busy card: the far-side armature rebuild ticks it determinate
        # via _on_armature_progress.
        owns_card = card_guard.start(
            f'Mirroring {label.split()[-1].lower()}', busy=True)
        try:
            from maya_tools.rigging.fabricator import branch_ops
            result = branch_ops.mirror_branch(
                root=root, include_modules=include_modules)
        except Exception as e:
            if owns_card:
                card_guard.fail(f'{label} failed. See the log.')
            self._handle_error(e, brief=f'{label} failed')
            return
        if owns_card:
            card_guard.finish(f'{label} done')
        mode = result['mode']
        if mode == 'full':
            msg = (f"{label}: mirrored {result['joints']} joint(s)")
            if include_modules:
                msg += f" + {result['components']} module(s)"
            self.logger.info(msg + '.')
        elif mode == 'modules_only':
            msg = (f"{label}: far side already built — mirrored "
                   f"{result['components']} module(s)")
            if result['skipped']:
                msg += f" (skipped {result['skipped']} — see warnings)"
            self.logger.info(msg + '.')
        else:
            self.logger.info(
                f'{label}: far side already built — nothing to mirror.')
        self.refresh_all()

    def _on_mirror_limb_requested(self) -> None:
        """Mirror panel — Mirror Limb: smart branch mirror. Joints
        missing on the far side → joints + aimer states + every module
        down the chain; all present → modules only; partially present
        → readable error (all children are checked, not just the
        root)."""
        self._run_mirror_branch(True, 'Mirror Limb')

    def _on_mirror_joints_requested(self) -> None:
        """Mirror panel — Mirror Joints: joints + aimer states only,
        one-shot (no live-constraint network)."""
        self._run_mirror_branch(False, 'Mirror Joints')

    def _on_mirror_module_requested(self) -> None:
        """Mirror panel — Mirror Module: only the module(s) on the
        selected joint, nothing downstream. Network-node work only —
        instant, no armature rebuild."""
        try:
            from maya_tools.rigging.fabricator import branch_ops
            result = branch_ops.mirror_module()
        except Exception as e:
            self._handle_error(e, brief='Mirror Module failed')
            return
        self.logger.info(
            f"Mirror Module: mirrored {result['components']} "
            f"module(s): {', '.join(result['ids'])}.")
        self.refresh_all()

    def _prompt_variant_tag(self):
        """Editable-dropdown region/variant prompt (Adrian, 2026-07-05):
        preset tags in the dropdown, free text welcome — pick one, add
        to it, or type your own. Returns the raw text or None."""
        from maya_tools.rigging.fabricator import branch_ops
        dlg = QtWidgets.QDialog(self)
        mindmeld_style.apply(dlg)
        dlg.setWindowTitle('Duplicate Branch')
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(mindmeld_style.field_label('Region / variant tag'))
        combo = QtWidgets.QComboBox()
        combo.setEditable(True)
        combo.addItems(list(branch_ops.VARIANT_PRESETS))
        combo.setCurrentText('')
        combo.lineEdit().setPlaceholderText(
            'up / dn / fr / bk... or type your own')
        lay.addWidget(combo)
        lay.addWidget(mindmeld_style.helper_label(
            'Inserted before the side token on every joint: '
            'clavicle_l + bk = clavicle_bk_l'))
        row = QtWidgets.QHBoxLayout()
        cancel_btn = mindmeld_style.button('Cancel', kind='ghost')
        ok_btn = mindmeld_style.button('Duplicate', kind='primary')
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn.clicked.connect(dlg.accept)
        row.addStretch(1)
        row.addWidget(cancel_btn)
        row.addWidget(ok_btn)
        lay.addLayout(row)
        combo.setFocus()
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        return combo.currentText()

    def _run_duplicate_branch(self, include_modules: bool, label: str,
                              root: str = None):
        """root=None resolves from the viewport selection (toolbar
        path); the canvas context menu passes its clicked row."""
        tag = self._prompt_variant_tag()
        if tag is None:
            return
        owns_card = card_guard.start(
            f'Duplicating {label.split()[-1].lower()}', busy=True)
        try:
            from maya_tools.rigging.fabricator import branch_ops
            result = branch_ops.duplicate_branch(
                tag, root=root, include_modules=include_modules)
        except Exception as e:
            if owns_card:
                card_guard.fail(f'{label} failed. See the log.')
            self._handle_error(e, brief=f'{label} failed')
            return
        if owns_card:
            card_guard.finish(f'{label} done')
        msg = (f"{label}: {result['root']} — {result['joints']} "
               f"joint(s)")
        if include_modules:
            msg += f", {result['components']} module(s)"
        self.logger.info(msg + '; new root ctrl selected — move away.')
        self.refresh_all()

    def _on_duplicate_limb_requested(self) -> None:
        """Duplicate panel — Duplicate Limb: whole-subtree variant
        rename (joints + modules), lands in place, new root ctrl
        selected."""
        self._run_duplicate_branch(True, 'Duplicate Limb')

    def _on_duplicate_joints_requested(self) -> None:
        """Duplicate panel — Duplicate Joints: joints + aimer states
        only, same variant rename."""
        self._run_duplicate_branch(False, 'Duplicate Joints')

    def _on_canvas_mirror_branch(self, root: str) -> None:
        """Canvas right-click — Mirror Limb rooted at the clicked row."""
        self._run_mirror_branch(True, 'Mirror Limb', root=root)

    def _on_canvas_duplicate_branch(self, root: str) -> None:
        """Canvas right-click — Duplicate Limb rooted at the clicked
        row (same variant-tag prompt as the toolbar)."""
        self._run_duplicate_branch(True, 'Duplicate Limb', root=root)

    def _on_delete_branches_requested(self, roots: list) -> None:
        """Canvas right-click — Delete Limb: joints + subtree, their
        aimers and their modules, one sandwich, one undo. The same op
        the ctrl-delete gesture funnels into."""
        try:
            from maya_tools.rigging.fabricator import branch_ops
            from maya_tools.rigging.joint_orient import (
                joint_orient_app as joa,
            )
            with joa.undo_chunk('DeleteLimb'):
                result = branch_ops.delete_branches(list(roots))
        except Exception as e:
            self._handle_error(e, brief='Delete Limb failed')
            return
        self.logger.info(
            f"Delete: removed {', '.join(result['roots'])} — "
            f"{result['joints']} joint(s), "
            f"{result['components']} module(s).")
        self.refresh_all()

    def _on_reparent_requested(self, roots: list, new_parent: str) -> None:
        """Canvas MMB drag-drop — move branch(es) under a new parent
        joint through the teardown→surgery→rebuild sandwich. Names and
        world positions never change; only hierarchy (and the moved
        roots' jointOrient collapses back into rotate). One undo."""
        try:
            from maya_tools.rigging.fabricator import branch_ops
            from maya_tools.rigging.joint_orient import (
                joint_orient_app as joa,
            )
            with joa.undo_chunk('ReparentBranch'):
                result = branch_ops.reparent_branches(
                    list(roots), new_parent)
        except Exception as e:
            self._handle_error(e, brief='Reparent failed')
            return
        moved = result['moved']
        if not moved:
            self.logger.info(
                f"Reparent: already under {result['new_parent']} — "
                f"nothing to do.")
            return
        msg = (f"Reparent: {', '.join(moved)} → "
               f"{result['new_parent']} (ctrls selected)")
        if result['already']:
            msg += (f"; skipped {len(result['already'])} already "
                    f"there")
        self.logger.info(msg + '.')
        self.refresh_all()

    def _on_live_mirror_toggled(self, on: bool) -> None:
        """Skeleton Helpers Bar — Live Mirror toggle handler.

        Selection-based symmetry: whichever side's ctrl/aimer you grab
        drives its counterpart. Branch ops (Mirror / Duplicate /
        Reparent) preserve an active mirror across their rebuild
        (armature_mirror.preserved()); other Armature rebuilds (Aim,
        limb drop, unbuild) still turn it off — re-toggle to resume.
        """
        try:
            from maya_tools.rigging.fabricator import armature_mirror
            if on:
                result = armature_mirror.enable()
                self.logger.info(
                    f"Live Mirror ON — {result['pairs']} pair(s), "
                    f"{result['driver']} driving. Grab either side; "
                    f"the counterpart follows.")
            else:
                armature_mirror.disable()
                self.logger.info(
                    'Live Mirror OFF — sides move independently.')
        except Exception as e:
            self._sync_symmetry_button()
            self._handle_error(e, brief='Live Mirror failed')

    def _sync_symmetry_button(self) -> None:
        """Make the Symmetry button read the live mirror's ACTUAL
        state. preserved() usually keeps them aligned across branch
        ops, but a failed re-enable (e.g. a whole side deleted — zero
        pairs left) turns the mirror off while the button stays lit;
        refresh_all calls this so the drift never survives a
        repaint."""
        try:
            from maya_tools.rigging.fabricator import armature_mirror
            on = armature_mirror.is_enabled()
        except Exception:
            return
        btn = self.skeleton_helpers_bar.live_mirror_btn
        if btn.isChecked() != on:
            btn.blockSignals(True)
            btn.setChecked(on)
            btn.blockSignals(False)
            if not on:
                # Adrian, 2026-07-05: turn it off ON the user — the
                # visible flip plus this line tell them what happened
                # if they expected symmetry to still be live.
                self.logger.info(
                    'Symmetry turned OFF (Armature was rebuilt). '
                    'Re-toggle to resume.')

    def _on_aim_joints_requested(self) -> None:
        """Skeleton Helpers Bar — Aim Joints at Aimers handler.

        Bakes every joint's orientation to its aimer (rotate carries
        orient, jointOrient=0) and rebuilds the Armature; aimers come
        back in their stored state. Skins are defensively detached
        inside build_armature before the bake.
        """
        owns_card = card_guard.start('Aiming joints',
                                     'Baking orientation...', busy=True)
        try:
            from maya_tools.rigging.fabricator import armature
            result = armature.build_armature()
        except Exception as e:
            if owns_card:
                card_guard.fail('Aim Joints failed. See the log.')
            self._handle_error(e, brief='Aim Joints failed')
            return
        if owns_card:
            card_guard.finish('Joints aimed')
        try:
            self.logger.info(
                f"Aim Joints: orientation baked, Armature rebuilt "
                f"({result['ik_edges']} aim edges, "
                f"{result['point_edges']} placed ctrls).")
            # Aim deliberately does NOT preserve Symmetry (Adrian,
            # 2026-07-05: turn it off on the user so they know) —
            # make the button follow and say so.
            self._sync_symmetry_button()
        except Exception as e:
            self._handle_error(e, brief='Aim Joints failed')

    # ─── Blueprint lifecycle ─────────────────────────────────────────────────

    def _on_new_rig_action(self):
        """File → New Rig — start a rig without a blueprint file.

        Bring-your-own-skeleton flow (e.g. an imported UE skeleton):
        1. If a Fabricator rig exists, confirm and tear it down
           (skeleton, geo and skins are preserved — fs_app.new_rig).
        2. Prompt for a rig name (auto-filled, overrideable).
        3. fs_app.init_rig: adopt the scene skeleton (or create a root
           joint at origin in an empty scene), registry + reference
           layer + Armature.
        4. World module on the root joint.
        """
        if nodes.get_registry():
            reply = QtWidgets.QMessageBox.question(
                self, 'New Rig',
                'A Fabricator rig already exists in this scene.\n\n'
                'Delete it and start a new rig? (Skeleton joints, geo '
                'and skin clusters are preserved.)',
                QtWidgets.QMessageBox.StandardButton.Yes |
                    QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            try:
                fs_app.new_rig()
            except Exception as e:
                self._handle_error(e, brief='New Rig: teardown failed')
                return

        # Name prompt — auto-filled from the scene name, overrideable.
        scene_stem = cmds.file(q=True, sceneName=True, shortName=True) or ''
        for ext in ('.ma', '.mb'):
            if scene_stem.endswith(ext):
                scene_stem = scene_stem[:-len(ext)]
        default_name = scene_stem or 'untitled'
        name, ok = QtWidgets.QInputDialog.getText(
            self, 'New Rig', 'Rig name:',
            QtWidgets.QLineEdit.Normal, default_name)
        if not ok:
            return
        self._create_new_rig(name.strip() or default_name)

    def _create_new_rig(self, name: str) -> None:
        """Shared New Rig core — the File menu action and the rig-name
        field's CREATE mode both land here. Adopts the scene skeleton
        (or seeds a root at origin), then World on the root."""
        try:
            root = fs_app.init_rig(name)
        except Exception as e:
            self._handle_error(e, brief='New Rig failed')
            return
        root_short = root.split('|')[-1]
        self._add_component('World', [root_short])
        self.logger.info(f'New rig "{name}" — Armature standing on '
                         f'root joint {root_short}.')
        self.refresh_all()

    def _on_blueprint_selected(self, path: str):
        # UI-level reentrancy latch. card_guard.start() pumps
        # processEvents, which can redispatch the queued tail of the
        # launching double-click back into this handler before load()
        # even starts (the same hazard fs_app.load's _LOAD_IN_FLIGHT
        # guard closes for the nested case, 2026-07-10). Sequential
        # re-entry would run the whole load twice; refuse it here.
        if self._template_load_active:
            return
        self._template_load_active = True
        owns_card = card_guard.start('Loading template', Path(path).name,
                                     busy=True)
        ok = False
        try:
            fs_app.load(path)
            ok = True
        except Exception as e:
            self._handle_error(e, brief='Load failed')
        finally:
            self._template_load_active = False
            if owns_card:
                if ok:
                    card_guard.finish('Template loaded')
                else:
                    card_guard.fail('Template load failed. See the log.')
        if not ok:
            return
        self.logger.info(f'Loaded {path}')
        self.refresh_all()

    def _on_save_rig_action(self):
        """File → Save Template. Single name prompt (auto-filled with the
        rig label); snapshots the current rig as <name>.blueprint.yaml in
        the active project's blueprints folder (blueprint_library — the
        factory templates dir is read-only). No folder picker; overwrite
        confirmation only when the file exists. Warns and aborts when no
        project library is configured.
        """
        if not nodes.get_registry():
            self.logger.warning('Save Template: no rig loaded.')
            return

        default_name = self._character_name() or 'untitled'
        name, ok = QtWidgets.QInputDialog.getText(
            self, 'Save Template', 'Template name:',
            QtWidgets.QLineEdit.Normal, default_name,
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        # User may type the extension by habit; strip it so we always
        # produce '<name>.blueprint.yaml' regardless of what they entered.
        if name.endswith('.blueprint.yaml'):
            name = name[:-len('.blueprint.yaml')]
        elif name.endswith('.yaml'):
            name = name[:-len('.yaml')]
        if not name:
            return
        try:
            dest_dir = blueprint_library.require_project_dir()
        except blueprint_library.LibraryError as e:
            self.logger.warning(f'Save Template: {e}')
            return
        path = dest_dir / f'{name}.blueprint.yaml'
        if path.exists():
            choice = QtWidgets.QMessageBox.question(
                self, 'Overwrite Template',
                f'"{name}.blueprint.yaml" already exists in the project '
                f'library.\n\nOverwrite with current rig?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes,
            )
            if choice != QtWidgets.QMessageBox.Yes:
                return

        # Busy card: the full-rig scene snapshot has no countable
        # phases but can run long on big rigs.
        owns_card = card_guard.start('Saving template',
                                     f'{name}.blueprint.yaml', busy=True)
        try:
            saved_path = fs_app.save(path=str(path))
        except Exception as e:
            if owns_card:
                card_guard.fail('Template save failed. See the log.')
            self._handle_error(e, brief='Save Template failed')
            return
        if owns_card:
            card_guard.finish('Template saved')
        self.logger.info(f'Saved template to {saved_path}')
        self._populate_palette_templates()
        self.refresh_all()

    def _on_delete_rig_action(self):
        """File → Delete Rig. Destructive scene cleanup — wipes everything
        Fabricator built/owns (rig_grp, guides_grp, pivots_grp, every
        fab_* network node, FAB_RigBinding nodes, _Joints/_Geo display
        layers) while preserving the skeleton, geo_grp, and skin clusters.

        Useful when the scene gets into a weird state (broken parenting,
        orphan ctrls, stale registry) and you want to start the rigging
        pass over without losing the underlying joints + skin.

        Prompts first — there's no undo for the deleted network nodes.
        """
        reply = QtWidgets.QMessageBox.question(
            self,
            'Delete Rig?',
            'This will delete:\n'
            '  • rig_grp (all ctrls + nulls)\n'
            '  • guides_grp + pivots_grp\n'
            '  • Every Fabricator component + the registry\n'
            '  • FAB_RigBinding nodes\n'
            '  • _Joints / _Geo display layers\n\n'
            'Preserved: skeleton joints, geo, skin clusters, '
            'ksMirror constraints.\n\n'
            'No undo for the deleted network nodes. Continue?',
            QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            fs_app.new_rig()
        except Exception as e:
            self._handle_error(e, brief='Delete Rig failed')
            return
        self.logger.info('Deleted rig — skeleton preserved.')
        self.refresh_all()
        # Mode flip likely (modules_built → empty). Auto-reopen mirrors
        # what _on_phase_action does after build/unbuild so panels
        # rebuild cleanly without stale state.
        FSWindow._force_reopen()

    def _character_name(self) -> str:
        """Resolve the character/rig name from the registry's rig_label
        attr — auto-derived + persisted on first read (see
        nodes.get_or_init_rig_label). Returns '' if no registry or no
        root joint exists yet."""
        return nodes.get_or_init_rig_label()

    # -- Rig-name field --------------------------------------------

    def _refresh_rig_name(self, mode):
        """Sync the rig-name field with the registry's stored value. In
        MODE_MODULES_BUILT the row is the cockpit's header (Adrian,
        2026-07-05) — visible but locked, Edit hidden: renaming a built
        rig is still unsupported, unbuild to rename. Signals blocked
        during populate so the editingFinished slot doesn't ping-pong
        back into a set_rig_label.

        No rig in the scene → the field flips into CREATE mode: unlocked
        with a 'type name to create' placeholder, so a brand-new user's
        very first affordance is the field itself (committing a name runs
        the File → New Rig flow). With a rig present it is the locked
        rename field behind the Edit button."""
        has_rig = bool(nodes.get_registry())
        current = nodes.get_or_init_rig_label() or ''
        blocker = QtCore.QSignalBlocker(self.rig_name_edit)
        self.rig_name_edit.setText(current)
        del blocker  # release the signal blocker explicitly
        if has_rig:
            self.rig_name_edit.setReadOnly(True)
            self.rig_name_edit.setPlaceholderText('(no rig in scene)')
        else:
            self.rig_name_edit.setReadOnly(False)
            self.rig_name_edit.setPlaceholderText(
                'Type name to create new rig...')
        built = mode == state.MODE_MODULES_BUILT
        if built:
            self.rig_name_edit.setReadOnly(True)
        self.rig_name_row.setVisible(True)
        # Edit toggles the rename lock — only meaningful with a rig,
        # and hidden in the cockpit (rename requires unbuild).
        self.rig_name_edit_btn.setVisible(not built)
        self.rig_name_edit_btn.setEnabled(has_rig)

    def _on_rig_name_edit_clicked(self):
        """Unlock the rig-name field for one edit — focus it with the
        text pre-selected. editingFinished (Enter or click-away)
        commits and re-locks."""
        self.rig_name_edit.setReadOnly(False)
        self.rig_name_edit.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        self.rig_name_edit.selectAll()

    def _on_rig_name_committed(self):
        """User pressed Enter / moved focus away from the field.

        CREATE mode (no rig in scene): a non-empty name runs the New
        Rig flow with it — the field is the File → New Rig shortcut.

        Rename mode: re-lock first (every exit path), then trim, drop
        the empty/no-change case, persist the new label, and refresh
        downstream UI (status bar uses _character_name)."""
        new_label = (self.rig_name_edit.text() or '').strip()

        if not nodes.get_registry():
            if new_label:
                self._create_new_rig(new_label)
            # Success → refresh_all re-renders the row locked with the
            # label; failure/empty → back to the create placeholder.
            self._refresh_rig_name(state.detect_mode())
            return

        self.rig_name_edit.setReadOnly(True)
        if not new_label:
            # Empty submission — re-populate from the stored value
            # rather than wipe it.
            self._refresh_rig_name(state.detect_mode())
            return
        current = nodes.get_or_init_rig_label() or ''
        if new_label == current:
            return
        nodes.set_rig_label(new_label)
        self.refresh_all()

    # ─── Component lifecycle ─────────────────────────────────────────────────

    def _on_palette_double_click(self, type_str: str):
        joints = self.canvas_panel.selected_joint_names()
        if not joints:
            self.logger.warning('Select joints in the canvas first.')
            return
        self._add_component(type_str, joints)

    def _on_add_component_requested(self, type_str: str, joints: list):
        self._add_component(type_str, joints)

    def _add_component(self, type_str: str, joints: list):
        if not nodes.get_registry():
            self.logger.warning('Load a blueprint first.')
            return
        try:
            cls = modules.get_component_class(type_str)
        except KeyError:
            self.logger.error(f'Unknown component type: {type_str!r}')
            return

        # Pre-add validation. Pass a transient scene snapshot to can_apply.
        bp_snap = fs_app._snapshot_blueprint_from_scene()
        contract = cls.CONTRACT

        if contract.max_joints == 1:
            # Single-joint per instance: create one component node per selected
            # joint. Validate PER JOINT — a multi-joint selection is valid here
            # (it fans out into one component each), even though can_apply on the
            # whole list would reject it against max_joints=1. Skip + warn the
            # joints that fail their own check; add the rest.
            added = []
            for jn in joints:
                ok, reason = cls.can_apply([jn], bp_snap)
                if not ok:
                    self.logger.warning(f'Skipped {type_str} on {jn}: {reason}')
                    continue
                # Remove any existing component owned by this joint as joints[0].
                for existing_cnode in nodes.get_all_component_nodes():
                    if nodes.get_component_joints(existing_cnode):
                        first = nodes.get_component_joints(existing_cnode)[0]
                        if first.split('|')[-1].split(':')[-1] == jn:
                            nodes.delete_component_node(existing_cnode)
                cid = cls.default_id([jn])
                side, region, options = _auto_detect_component_meta(jn, contract)
                parent_plug = self._auto_wire_parent_plug(jn)
                nodes.create_component_node(
                    component_id=cid,
                    component_type=type_str,
                    joints=[jn],
                    joint_names=[jn],
                    parent_plug=parent_plug,
                    side=side,
                    role='',
                    region=region,
                    options=options,
                    persisted={},
                )
                added.append(jn)
            if added:
                self.logger.info(
                    f'Added {len(added)} {type_str} component(s) to: '
                    + ', '.join(added))
            else:
                self.logger.warning(
                    f'No {type_str} components added (no selected joint '
                    f'accepted {type_str}).')
        else:
            # Multi-joint single-instance. Validate the whole selection up front.
            ok, reason = cls.can_apply(list(joints), bp_snap)
            if not ok:
                self.logger.warning(f'Cannot add {type_str}: {reason}')
                return
            cursor = joints[0] if joints else ''
            # _resolve_initial_joints still takes bp for descendancy walks —
            # use a transient snapshot.
            bp = fs_app._snapshot_blueprint_from_scene() if nodes.get_registry() else None
            if bp is not None:
                resolved = fs_app._resolve_initial_joints(
                    bp, contract.joint_roles, list(joints), cursor,
                    max_joints=contract.max_joints)
            else:
                resolved = list(joints)
            primary = resolved[0]
            for existing_cnode in nodes.get_all_component_nodes():
                if nodes.get_component_joints(existing_cnode):
                    first = nodes.get_component_joints(existing_cnode)[0]
                    if first.split('|')[-1].split(':')[-1] == primary:
                        nodes.delete_component_node(existing_cnode)
            cid = cls.default_id(resolved)
            side, region, options = _auto_detect_component_meta(primary, contract)
            # Give the component class a chance to seed extra create-time
            # default options — e.g. RibbonIKArm's auto-discovered finger
            # membership (PLAN.md 2026-07-08 Task 4.2: "Hook discovery
            # into the create/add path so the option is populated when
            # the RibbonIKArm is placed"). No-op ({}) for every component that
            # doesn't override Component.default_options_for_create.
            options.update(fs_app._default_options_for_create(cls, resolved, bp))
            parent_plug = self._auto_wire_parent_plug(primary)
            nodes.create_component_node(
                component_id=cid,
                component_type=type_str,
                joints=list(resolved),
                joint_names=list(resolved),
                parent_plug=parent_plug,
                side=side,
                role='',
                region=region,
                options=options,
                persisted={},
            )
            self.logger.info(
                f'Added {type_str} on {len(resolved)} joints: '
                + ', '.join(resolved))

        self.refresh_all()

    def _auto_wire_parent_plug(self, primary_joint: str) -> str:
        """Derive parent_plug for a component being added on primary_joint.

        Walks up to primary_joint's DAG parent (joint type), finds the
        component that owns that parent (anywhere in its joints list —
        including chain interiors), and returns '<owner_id>.<plug>' for
        the output that matches the parent joint's POSITION in the
        owner's joint chain (e.g. an interior joint's own joint_out_i)
        via Component.output_for_owned_joint. Falls back to the owning
        component's first declared output plug when there's no positional
        match (short chains, components with no positional outputs, or
        the parent joint can't be located in the owner's joint list).

        Returns '' (no plug) when:
          - primary_joint has no joint parent in the scene
          - the parent joint is raw (not owned by any Fabricator component)
          - the owning component declares no output plugs

        Auto-wires ONLY empty plugs; never overwrites manual edits (callers
        invoke this at component CREATION, before any user wiring exists).
        """
        if not cmds.objExists(primary_joint):
            return ''
        parents = cmds.listRelatives(primary_joint, parent=True, type='joint') or []
        if not parents:
            return ''
        parent_jnt = parents[0]
        owner_cnode = nodes.find_component_for_joint(parent_jnt)
        if not owner_cnode:
            return ''
        owner_type = nodes.get_component_type(owner_cnode)
        try:
            owner_cls = modules.get_component_class(owner_type)
        except KeyError:
            return ''
        outputs = owner_cls.CONTRACT.outputs
        if not outputs:
            return ''
        owner_id = nodes.get_component_id(owner_cnode)

        # Positional pick: locate the parent joint's index within the
        # owner's joints[] list, using the same live-message-multi-first,
        # joint_names-fallback resolution _snapshot_single_component_spec
        # uses (fs_app.py) — so the index agrees with what build() indexes
        # joint_out_i against. Short-name-normalize both sides (namespaces /
        # partial DAG paths) before comparing.
        owner_joints = [j.split('|')[-1].split(':')[-1]
                       for j in nodes.get_component_joints(owner_cnode)]
        if not owner_joints:
            owner_joints = nodes.get_component_joint_names(owner_cnode)
        parent_short = parent_jnt.split('|')[-1].split(':')[-1]
        if parent_short in owner_joints:
            parent_index = owner_joints.index(parent_short)
            chosen = owner_cls.output_for_owned_joint(parent_index, len(owner_joints))
            if chosen and any(o.name == chosen for o in outputs):
                return f'{owner_id}.{chosen}'

        return f'{owner_id}.{outputs[0].name}'

    def _on_remove_component_requested(self, component_id: str):
        cnode = nodes.find_component_node_by_id(component_id)
        if not cnode:
            return
        nodes.delete_component_node(cnode)
        # Take the component's extra-guide pivots with it (same orphan
        # bug the branch delete had): a removed IKLeg must not leave its
        # heel/toe-tip locators behind under fab_pivots_grp.
        fs_app.delete_component_pivots([component_id])

        # Clear dangling parent_plug refs in dependents.
        cleared = []
        for c in nodes.get_all_component_nodes():
            pp = nodes.get_component_parent_plug(c)
            if pp and pp.split('.', 1)[0] == component_id:
                cmds.setAttr(f'{c}.parent_plug', '', type='string')
                cleared.append(nodes.get_component_id(c))

        if cleared:
            self.logger.info(
                f'Removed component {component_id}; '
                f'cleared dangling parent_plug on {len(cleared)} dependent(s): '
                f'{", ".join(cleared)}.'
            )
        else:
            self.logger.info(f'Removed component {component_id}.')
        self.refresh_all()

    def _on_save_limb_requested(self, anchor_joint: str, path: str) -> None:
        try:
            fs_app.save_limb(anchor_joint, path)
            self.logger.info(f'Saved limb: {Path(path).name}')
            self.palette_panel.refresh_limbs()
        except Exception as exc:
            traceback.print_exc()
            self.logger.error(f'Save limb failed: {exc}')

    def _on_limb_drop_requested(self, path: str, target_joint: str) -> None:
        # Busy card: the armature re-stand inside load_limb ticks it
        # determinate via _on_armature_progress.
        owns_card = card_guard.start('Building limb', Path(path).name,
                                     busy=True)
        ok = False
        try:
            fs_app.load_limb(path, target_joint)
            ok = True
            self.refresh_all()
            self.logger.info(f'Loaded limb: {Path(path).name} → {target_joint}')
        except Exception as exc:
            traceback.print_exc()
            self.logger.error(f'Load limb failed: {exc}')
        finally:
            if owns_card:
                if ok:
                    card_guard.finish('Limb built')
                else:
                    card_guard.fail('Limb build failed. See the log.')

    # ─── refresh_all + status bar ────────────────────────────────────────────

    def refresh_all(self):
        mode = state.detect_mode()

        # Rig-name field — repopulate from registry, set read-only in
        # MODE_MODULES_BUILT. Must run before downstream refreshes that
        # consume the label (status bar, etc).
        self._refresh_rig_name(mode)

        # PalettePanel — hide on MODE_MODULES_BUILT
        self.palette_panel.refresh_state(mode)
        self.skeleton_helpers_bar.refresh_state(mode)
        self._sync_symmetry_button()
        self.build_options_bar.refresh_state(mode)
        # The wrapping Options section follows the bar's visibility —
        # otherwise a bare '// OPTIONS' header lingers in Animation Mode.
        self.options_section.setVisible(mode != state.MODE_MODULES_BUILT)

        # Animation Mode is the stripped cockpit (Adrian, 2026-07-05):
        # rig name + Edit Rig + log, nothing else. The whole splitter
        # (palette, canvas, properties) goes dark; ToolsPanel
        # (curve-o-matic + control list) is retired outright.
        built = mode == state.MODE_MODULES_BUILT
        self.splitter.setVisible(not built)
        # Info bar is progress-only in EVERY mode (items 1+6): visible
        # solely between _begin/_end_build_progress.
        self.status_bar_widget.setVisible(self._progress_active)

        # Phase buttons under canvas — three buttons, mode-driven state
        self._refresh_phase_buttons(mode)

        # CanvasPanel — reads scene directly
        self.canvas_panel.populate_from_scene()

        # Status bar — character identity (not blueprint name)
        self._refresh_status_bar(mode)

        # Window title — fixed (no dirty marker)
        self.setWindowTitle('Fabricator')

    # ─── Build progress helpers ──────────────────────────────────────────────

    def _begin_build_progress(self):
        """Show the build progress bar and hide the components count
        (they share status-bar real estate). Progress sits in the same
        slot to keep the bar visually steady."""
        self._status_components_label.setVisible(False)
        self._status_progress.setValue(0)
        self._status_progress.setMaximum(1)  # placeholder until first cb
        self._status_progress.setVisible(True)
        # The info bar itself only exists while progress runs (items
        # 1+6, 2026-07-05).
        self._progress_active = True
        self.status_bar_widget.setVisible(True)
        QtWidgets.QApplication.processEvents()

    def _on_build_progress(self, index: int, total: int, component_id: str):
        """Build progress callback — called once before each component
        builds, plus a final call with index==total when the build is
        complete."""
        self._status_progress.setMaximum(max(1, total))
        self._status_progress.setValue(index)
        if component_id:
            self._status_progress.setFormat(
                f'Building {component_id}: %v / %m')
        pct = int(index / max(1, total) * 100)
        card_guard.update(
            pct,
            f'Building {component_id}...' if component_id else 'Finishing...')
        QtWidgets.QApplication.processEvents()

    def _end_build_progress(self):
        self._status_progress.setVisible(False)
        self._status_components_label.setVisible(True)
        self._status_progress.setFormat('Building: %v / %m')
        self._progress_active = False
        self.status_bar_widget.setVisible(False)

    def _on_armature_progress(self, index: int, total: int,
                              joint_name: str):
        """Armature build progress (item 5, 2026-07-05): fed by
        armature.set_progress_reporter, so New Rig, limb drops, the
        unbuild epilogue, and mirror/duplicate rebuilds all drive the
        same bar build_modules uses. Self-contained: the first tick
        raises the bar, the final tick (index == total) drops it."""
        if index >= total:
            self._end_build_progress()
            return
        if not self._progress_active:
            self._begin_build_progress()
        self._status_progress.setMaximum(max(1, total))
        self._status_progress.setValue(index)
        self._status_progress.setFormat('Armature: %v / %m')
        # Armature rebuilds run nested inside card-owning operations
        # (unbuild epilogue, limb drop, template load): tick the outer
        # card determinate. No card up = no-op.
        card_guard.update(int(index / max(1, total) * 100),
                          f'Standing armature: {joint_name}')
        QtWidgets.QApplication.processEvents()

    def _refresh_status_bar(self, mode: str):
        # Character name via the registry's stored rig_label (auto-derived
        # + persisted on first read). Falls back to scene stem when no
        # rig is in the scene yet.
        label = nodes.get_or_init_rig_label()
        if not label:
            scene_stem = cmds.file(q=True, sceneName=True, shortName=True) or ''
            if scene_stem.endswith('.ma'):
                scene_stem = scene_stem[:-3]
            elif scene_stem.endswith('.mb'):
                scene_stem = scene_stem[:-3]
            label = scene_stem or 'untitled'

        self._status_blueprint_label.setText(f'// Name: {label}')
        components_count = len(nodes.get_all_component_nodes())
        self._status_components_label.setText(f'// COMPONENTS: {components_count}')
        mode_label = _MODE_DISPLAY_NAMES.get(mode, mode)
        self._status_mode_label.setText(f'// MODE: {mode_label}')

    # ─── Blueprint discovery (dropdown population) ───────────────────────────

    def _populate_palette_templates(self):
        """Feed the palette's TEMPLATES section from both library
        sources: factory (shipped with Fabricator, read-only) and the
        active project's blueprints folder (user-made)."""
        items = [(e.name, str(e.path), e.source)
                 for e in blueprint_library.scan_templates()]
        self.palette_panel.populate_templates(items)

    def _on_library_message(self, level: str, text: str) -> None:
        """Palette right-click library ops report through here."""
        if level == 'warning':
            self.logger.warning(text)
        else:
            self.logger.info(text)

    # ─── closeEvent — persist window/splitter state, no save prompt ──────────

    def closeEvent(self, event):
        # Failsafe (Adrian, 2026-07-05): closing Fabricator with
        # Symmetry on would leave the live mirror network + selection
        # callback running headless in the scene — turn it off.
        try:
            from maya_tools.rigging.fabricator import armature_mirror
            if armature_mirror.is_enabled():
                armature_mirror.disable(quiet=True)
        except Exception:
            pass
        # Drop the DAG watchers — with Fabricator closed, ctrl
        # parenting and deleting are nobody's business (and the hooks
        # must not point at dead widgets).
        try:
            from maya_tools.rigging.fabricator import armature_watch
            armature_watch.set_reporter(None)
            armature_watch.set_refresher(None)
            armature_watch.remove()
        except Exception:
            pass
        # Drop the Armature progress hook (it must not point at a
        # dead progress bar).
        try:
            from maya_tools.rigging.fabricator import armature
            armature.set_progress_reporter(None)
        except Exception:
            pass
        # Stop LiveBackend FIRST so callbacks unregister cleanly even if
        # subsequent persistence steps fail. stop() is idempotent and
        # never raises; defensive try/except just in case.
        try:
            self._live_backend.stop()
        except Exception:
            pass
        # Tear down canvas Maya callbacks (linked-selection sync).
        try:
            self.canvas_panel.cleanup()
        except Exception:
            pass
        # Tear down scene-open / scene-new auto-reopen callbacks.
        for cid in getattr(self, '_scene_callback_ids', []):
            try:
                om.MMessage.removeCallback(cid)
            except Exception:
                pass
        self._scene_callback_ids = []
        # No save-before-close prompt. The scene's network nodes are the
        # durable per-character state — Maya's regular scene save preserves
        # them. Disk YAML is the blueprint recipe; users only Save when
        # they're authoring a new template, which is a deliberate action,
        # not something to nag on every close.
        # Persist window/splitter state for next session. Size belongs
        # to the full Edit Mode layout only — the Animation cockpit's
        # compact frame must not poison the saved size. isVisibleTo
        # (not isVisible): _force_reopen hides the window before
        # closing it, which would zero a plain isVisible check.
        size = self.size()
        if self.splitter.isVisibleTo(self) and not self._docked:
            # Docked sizes belong to the workspaceControl, not the
            # floating window's optionVars.
            cmds.optionVar(intValue=(_OPTION_VAR_W, size.width()))
            cmds.optionVar(intValue=(_OPTION_VAR_H, size.height()))
        # Only persist splitter state when the layout is fully visible
        # (Edit Mode). Saving from Animation Mode records a 0-width
        # palette slot, which then fails to restore correctly when next
        # opened in Edit Mode.
        if self.palette_panel.isVisible():
            state_bytes = bytes(self.splitter.saveState())
            cmds.optionVar(stringValue=(_OPTION_VAR_SPLITTER, state_bytes.hex()))
        super().closeEvent(event)

    # ─── Dock / undock (two-window design, 2026-07-06) ───────────────────────

    def reject(self):
        """Escape must not hollow out the workspaceControl — the
        docked face ignores it; the floating window closes as any
        QDialog does."""
        if self._docked:
            return
        super().reject()

    def _on_brand_bar_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        if self._docked:
            act = menu.addAction('Undock Fabricator')
            act.triggered.connect(self._undock_to_floating)
        else:
            act = menu.addAction('Dock Fabricator')
            act.triggered.connect(self._dock_from_floating)
        menu.exec(self._brand_bar.mapToGlobal(pos))

    def _dock_from_floating(self):
        # Deferred one tick: open_docked() closes this window on its
        # way up, and the menu click must fully unwind first.
        def _do():
            try:
                from maya_tools.rigging.fabricator.ui import fabricator_dock
                fabricator_dock.open_docked()
            except Exception:
                traceback.print_exc()
        QtCore.QTimer.singleShot(0, _do)

    def _undock_to_floating(self):
        # Deferred: closing the dock deletes THIS widget.
        def _do():
            try:
                from maya_tools.rigging.fabricator.ui import fabricator_dock
                fabricator_dock.close_dock()
                FSWindow.show_window()
            except Exception:
                traceback.print_exc()
        QtCore.QTimer.singleShot(0, _do)

    # ─── Error handling ──────────────────────────────────────────────────────

    def _handle_error(self, e: Exception, brief: str = ''):
        traceback.print_exc()
        msg = brief or str(e)
        self.logger.error(f'{msg}: {e}')

    def _restore_splitter_state(self):
        # Read-old-fallback (2026-07-11, Tools Engineer S3): prefer the
        # rebranded key; fall back to the pre-rebrand key when the new
        # one hasn't been written yet. Remove after v1.
        var = None
        if cmds.optionVar(exists=_OPTION_VAR_SPLITTER):
            var = _OPTION_VAR_SPLITTER
        elif cmds.optionVar(exists=_OLD_OPTION_VAR_SPLITTER):
            var = _OLD_OPTION_VAR_SPLITTER
        if var is not None:
            try:
                state_hex = cmds.optionVar(q=var)
                self.splitter.restoreState(QtCore.QByteArray(bytes.fromhex(state_hex)))
                # Sanity check — a slot at near-zero width means a prior
                # session saved layout while a panel was hidden (palette
                # in Animation Mode collapses its splitter slot). Fall
                # back to stretch-factor defaults so reopening in Edit
                # Mode doesn't render with the palette invisible.
                sizes = self.splitter.sizes()
                if sizes and any(s < 30 for s in sizes):
                    total = sum(sizes) or self.width()
                    self.splitter.setSizes([
                        int(total * 0.18),
                        int(total * 0.50),
                        total - int(total * 0.18) - int(total * 0.50),
                    ])
            except Exception:
                pass  # Bad cached state — ignore, defaults apply.

    @staticmethod
    def _force_reopen():
        """Schedule a close-and-reconstruct of the FSWindow on the next
        event-loop tick. Used by build/unbuild and Maya scene-open as the
        simple-and-correct cure for stale UI state. Deferred so it's safe
        to call from inside event handlers or scene callbacks.

        No-op when the window isn't currently open.
        """
        def _do():
            global _win
            # Shared finder: a first-match sweep can return a predecessor
            # that is closed but not yet deleted, which would leave the
            # live window untouched and build a THIRD one.
            existing = FSWindow._find_top_level()
            if existing is None:
                # No top-level instance — but a DOCKED Fabricator is a
                # child of its workspaceControl, invisible to the
                # finder above. Rebuild the dock's content instead.
                try:
                    from maya_tools.rigging.fabricator.ui import (
                        fabricator_dock)
                    fabricator_dock.rebuild_if_open()
                except Exception:
                    traceback.print_exc()
                return
            # Geometry continuity: the replacement opens where the old
            # window sat. Size is deliberately NOT carried — it is
            # mode-owned (Edit restores the saved size, the Animation
            # cockpit opens compact).
            try:
                old_pos = existing.frameGeometry().topLeft()
            except Exception:
                old_pos = None
            try:
                existing.hide()
                existing.close()
                existing.deleteLater()
            except Exception:
                pass
            _win = FSWindow()
            # setWindowFlags REPLACES flags — keep the frameless hint or
            # the OS title bar comes back (frameless.adopt set it).
            _win.setWindowFlags(QtCore.Qt.WindowType.Window
                                | QtCore.Qt.WindowType.FramelessWindowHint)
            if old_pos is not None:
                _win.move(old_pos)
            _win.show()

        QtCore.QTimer.singleShot(0, _do)

    # Tour anchor ids -> the live widget each card points at. Values are
    # accessors, not widgets: Build and Unbuild both call _force_reopen(),
    # which destroys this window and builds a new one, so anything the
    # tour captured up front is a dangling C++ object by the next step.
    _TOUR_ANCHORS = {
        'armature_tools': lambda w: w.skeleton_helpers_bar,
        'components':     lambda w: w.palette_panel,
        'rig_outliner':   lambda w: w.canvas_panel,
        'properties':     lambda w: w.properties_panel,
        'templates':      lambda w: w.palette_panel,
        'build_rig':      lambda w: w.build_rig_btn,
    }

    @staticmethod
    def _find_top_level():
        """Best top-level FSWindow, or None.

        There can be TWO at once: _force_reopen closes the old window and
        constructs the replacement immediately, but the closed one stays
        in topLevelWidgets until its deleteLater is processed. Taking the
        first match therefore hands back the DEAD window mid-reopen, whose
        widgets are all invisible — which is precisely the window the
        Fabricator tour is pointing at when it crosses a Build or an
        Unbuild. Prefer a visible instance, newest first.
        """
        found = []
        for w in QtWidgets.QApplication.topLevelWidgets():
            try:
                if w.objectName() == FSWindow.WINDOW_NAME:
                    found.append(w)
            except RuntimeError:        # C++ side already gone
                continue
        for w in reversed(found):
            try:
                if w.isVisible():
                    return w
            except RuntimeError:
                continue
        return found[-1] if found else None

    @staticmethod
    def current():
        """The live FSWindow, or None. Found by objectName rather than
        trusting the module `_win` global, which goes stale on a shelf
        reload and across _force_reopen."""
        try:
            win = FSWindow._find_top_level()
            if win is not None:
                return win
            # A DOCKED Fabricator is a child of its workspaceControl and
            # invisible to the top-level sweep above. _populate() parks
            # the live instance on the module's _DOCK_WIN.
            from maya_tools.rigging.fabricator.ui import fabricator_dock
            docked = getattr(fabricator_dock, '_DOCK_WIN', None)
            return docked if (docked is not None and isValid(docked)) else None
        except Exception:
            return None

    @staticmethod
    def widget_for(anchor_id: str):
        """Live widget for a tour anchor id, or None when there is no
        window / no such id / the widget is gone. Public seam for the
        Fabricator tour's cards; callers must handle None. Never raises.

        Resolve LATE — call this at the moment a card is shown, never
        earlier (see _TOUR_ANCHORS)."""
        try:
            win = FSWindow.current()
            if win is None:
                return None
            accessor = FSWindow._TOUR_ANCHORS.get(anchor_id)
            if accessor is None:
                return None
            widget = accessor(win)
            # A widget whose C++ side is gone, or one hidden by the
            # current mode, is not something to point at.
            return widget if (widget is not None
                              and isValid(widget)
                              and widget.isVisible()) else None
        except Exception:
            return None

    @staticmethod
    def show_window():
        """Open (or focus, if already open) the FSWindow.

        Singleton tool-window pattern that survives module reload. Shelf
        buttons typically `importlib.reload(fs_window)` before calling
        show_window, which resets this module's `_win` global to None —
        but the underlying Qt widget from the prior call is still alive.
        Look up existing instances via objectName across top-level
        widgets so the singleton check works regardless of Python-side
        module state.

        When found:
        - If isinstance matches (same Python class — no reload happened)
          AND visible: raise + activate.
        - Otherwise (stale class from a reloaded module): hide
          synchronously, close, deleteLater, then construct fresh.

        The synchronous `hide()` before construction is what avoids the
        old "two windows briefly visible" artifact — `close()` +
        `deleteLater()` alone is async and Qt can paint both windows
        before the cleanup runs.
        """
        global _win

        # Two-window law (2026-07-06): one Fabricator at a time — the
        # floating flagship closes the docked face on its way up.
        try:
            from maya_tools.rigging.fabricator.ui import fabricator_dock
            fabricator_dock.close_dock()
        except Exception:
            pass

        # Find any prior instance by objectName among top-level widgets.
        # setWindowFlags(WindowType.Window) re-parents to top-level, so
        # this is the right scope to search.
        existing = None
        for w in QtWidgets.QApplication.topLevelWidgets():
            try:
                if w.objectName() == FSWindow.WINDOW_NAME:
                    existing = w
                    break
            except Exception:
                continue

        if existing is not None:
            try:
                same_class = isinstance(existing, FSWindow)
                visible = existing.isVisible()
            except Exception:
                same_class = False
                visible = False
            if same_class and visible:
                existing.raise_()
                existing.activateWindow()
                _win = existing
                return
            # Stale instance (likely a different class object after
            # module reload). Hide synchronously before constructing the
            # replacement so they're never co-visible.
            try:
                existing.hide()
                existing.close()
                existing.deleteLater()
            except Exception:
                pass

        # Geometry continuity (mirrors _force_reopen): a shelf-reload
        # replacement opens where the outgoing window sat.
        old_pos = None
        if existing is not None:
            try:
                old_pos = existing.frameGeometry().topLeft()
            except Exception:
                pass

        _win = FSWindow()
        # setWindowFlags REPLACES flags — keep the frameless hint or
        # the OS title bar comes back (frameless.adopt set it).
        _win.setWindowFlags(QtCore.Qt.WindowType.Window
                            | QtCore.Qt.WindowType.FramelessWindowHint)
        if old_pos is not None:
            _win.move(old_pos)
        _win.show()
        # Register the KS animation marking menu (Ctrl+Alt+RMB on viewPanes).
        # Idempotent — re-registers cleanly if user reopens FSWindow.
        try:
            from maya_tools.rigging.fabricator.ui import animation_menu
            animation_menu.register_menu()
        except Exception:
            import traceback
            traceback.print_exc()


def _load_window_size():
    # Read-old-fallback (2026-07-11, Tools Engineer S3): prefer the
    # rebranded key; fall back to the pre-rebrand key when the new one
    # hasn't been written yet. Remove after v1.
    if cmds.optionVar(exists=_OPTION_VAR_W):
        w = cmds.optionVar(q=_OPTION_VAR_W)
    elif cmds.optionVar(exists=_OLD_OPTION_VAR_W):
        w = cmds.optionVar(q=_OLD_OPTION_VAR_W)
    else:
        w = 0
    if cmds.optionVar(exists=_OPTION_VAR_H):
        h = cmds.optionVar(q=_OPTION_VAR_H)
    elif cmds.optionVar(exists=_OLD_OPTION_VAR_H):
        h = cmds.optionVar(q=_OLD_OPTION_VAR_H)
    else:
        h = 0
    return (w or _DEFAULT_W, h or _DEFAULT_H)


