# project_setup_ui.py
# Mindmeld project setup manager - create / edit / duplicate / delete / apply
# project configs, no raw JSON. Internal name stays "project setup"; the
# user-facing product name "Mindmeld" is display text ONLY (window title,
# brand chrome) and is never a code identifier here - see
# docs/superpowers/specs/2026-07-07-project-setup-tool-design.md sec 8.
#
# Two-file split, zero business logic in this module: every mutating action
# (create/edit/duplicate/delete/apply/scaffold/list) goes through
# project_setup_app; reads that already have their own fixed public API
# (project_config_io.load_project/slugify, config_validation.validate_config,
# project_scaffold.candidate_dirs) are called directly rather than
# re-invented here. The Shared Project Configs pointer (Browse / Use Local
# Default / status) drives through settings_app.configs_root_status /
# set_configs_root / clear_configs_root - moved here from settings_ui
# 2026-07-20 (the pointer names project settings, so it belongs with the
# project list); see
# docs/superpowers/specs/2026-07-20-mindmeld-bindings-unify-design.md.
__author__ = "Adrian Melian"

import importlib
import json
import os
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from maya_tools.framework import config_validation
from maya_tools.framework import project_config_io
from maya_tools.framework import project_config_resolve
from maya_tools.framework import project_scaffold
from maya_tools.framework import project_setup_app
from maya_tools.framework import settings_app
from maya_tools.framework.toolbar import toolbar_prefs
from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget

importlib.reload(project_setup_app)
importlib.reload(settings_app)

LINEAR_UNITS = ("mm", "cm", "m", "km", "in", "ft", "yd", "mi")
ANGULAR_UNITS = ("deg", "rad")
FRAME_RATES = ("game", "film", "pal", "ntsc", "show", "palf", "ntscf")
ASSET_CLASS_COLUMNS = ("id", "label", "export_types", "source_dir", "dest_dir",
                       "anim_gameplay", "anim_cinematic")
_DERIVED_POSE_SUBDIR = "SourceArt/_pose"
_DERIVED_ANIM_SUBDIR = "SourceArt/_anim"
_CONFIGS_SOURCE_LABELS = {"env": "ENV VAR", "pointer": "POINTER",
                          "local": "LOCAL DEFAULT"}


class _ScaffoldChecklistDialog(QtWidgets.QDialog):
    """Post-save checklist (spec sec 6): create ONLY the directories the
    user checks - no filler, no speculative tree."""

    def __init__(self, candidates, parent=None):
        super().__init__(parent)
        mindmeld_style.apply(self)
        self.setWindowTitle("Scaffold Directories")
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(mindmeld_style.caps_label("// create on disk?"))
        self._boxes = {}
        for key, label, abspath in candidates:
            cb = QtWidgets.QCheckBox(f"{label}  ({abspath})")
            cb.setChecked(True)
            self._boxes[key] = cb
            layout.addWidget(cb)
        btn_row = QtWidgets.QHBoxLayout()
        self.skip_btn = mindmeld_style.button("Skip", kind="ghost")
        self.create_btn = mindmeld_style.button("Create Selected", kind="primary")
        btn_row.addWidget(self.skip_btn)
        btn_row.addWidget(self.create_btn)
        layout.addLayout(btn_row)
        self.create_btn.clicked.connect(self.accept)
        self.skip_btn.clicked.connect(self.reject)

    def checked_keys(self):
        return [key for key, cb in self._boxes.items() if cb.isChecked()]


class ProjectSetupUI(QtWidgets.QDialog):
    WINDOW_NAME = "FSProjectSetup"
    WINDOW_TITLE = "Mindmeld"

    def __init__(self, parent=None):
        # parent stays None unless explicitly passed (show_window() passes
        # get_maya_window()) - constructing with no parent must never touch
        # a live Maya session, so the offscreen test can build this dialog
        # headless.
        super().__init__(parent)
        mindmeld_style.apply(self)

        self.setObjectName(self.WINDOW_NAME)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setProperty("saveWindowPref", True)
        self.setMinimumWidth(660)
        self.setMinimumHeight(640)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowType.Tool)

        self._editing_slug = None
        self._fbx_presets = {}
        # Project-tier carries with no dedicated widget: populated on
        # load, emitted by _collect_authored. Forgetting one of these is
        # the wipe class the full-dict round-trip test guards against.
        self._pose_subpath = _DERIVED_POSE_SUBDIR
        self._anim_subpath = _DERIVED_ANIM_SUBDIR
        self._blueprints_subpath = ""
        self._engine_up_axis = ""
        # The convention loaded with the project being edited — the
        # baseline _on_mirror_convention_changed warns against.
        self._loaded_convention = "unreal"
        # True while blueprints_dir_edit shows a src+subpath PREVIEW (not
        # user-entered text): the preview re-derives when roots re-bind
        # and is never folded into a machine override on collect.
        self._bp_showing_preview = False
        # Guard: populate sets dozens of widgets whose change-signals each
        # trigger _revalidate against a HALF-populated form; the last one
        # to fire froze a stale error set into the panel (and wrongly
        # disabled Save) until the user touched any field. Suppress
        # mid-populate revalidates and run exactly one at the end.
        self._populating = False
        # Right-pane bindings editor (Phase 3): which slug's bindings the
        # right pane currently shows, and whether the user has typed
        # anything into it since that load. _bindings_dirty gates both the
        # "skip noisy required-field errors on an untouched/empty unbound
        # project" posture (mirrors the removed settings_ui._revalidate)
        # and the discarded-edits log note on selection switch.
        self._bindings_slug = None
        self._bindings_dirty = False

        self.create_layout()
        self.connect_signals()
        self.populate()

    # ─────────────────────────────────────────
    # Layout
    # ─────────────────────────────────────────

    def create_layout(self):
        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.setSpacing(10)
        self.main_layout.setContentsMargins(14, 14, 14, 12)

        brand_row = QtWidgets.QHBoxLayout()
        brand_row.setSpacing(10)
        # FS Mindmeld banner (Adrian, 2026-07-11); text title returns if missing.
        from PySide6.QtGui import QPixmap
        from maya_tools.framework.toolbar.widgets import icon_button
        _banner = QPixmap(str(icon_button._ICON_DIR / 'fs_mindmeld_banner.png'))
        if not _banner.isNull():
            _brand = QtWidgets.QLabel()
            _brand.setPixmap(_banner.scaledToHeight(
                40, QtCore.Qt.TransformationMode.SmoothTransformation))
            brand_row.addWidget(_brand)
        else:
            brand_row.addWidget(mindmeld_style.brand_label(self.WINDOW_TITLE))
        brand_row.addWidget(mindmeld_style.caps_label("// project setup"),
                            0, QtCore.Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        self.main_layout.addLayout(brand_row)
        self.main_layout.addWidget(mindmeld_style.horizontal_rule())

        self.pages = QtWidgets.QStackedWidget()
        self.pages.addWidget(self._build_list_page())     # index 0
        self.pages.addWidget(self._build_editor_page())    # index 1
        self.main_layout.addWidget(self.pages, 1)

        # Shared Project Configs sits BELOW the project list and ABOVE
        # VALIDATION (Adrian, 2026-07-20): it is a machine-wide pointer that
        # scopes the whole list, not a per-project field, so it reads as a
        # footer control rather than a header.
        self.main_layout.addWidget(mindmeld_style.horizontal_rule())
        self.main_layout.addWidget(self._build_configs_panel())

        self.validation_section = CollapsibleSection("// VALIDATION")
        self.validation_logger = LoggerWidget()
        validation_body = QtWidgets.QVBoxLayout()
        validation_body.setContentsMargins(0, 0, 0, 0)
        validation_body.addWidget(self.validation_logger)
        self.validation_section.set_body_layout(validation_body)
        self.validation_section.set_expanded(False)
        self.main_layout.addWidget(self.validation_section)

        self.log_section = CollapsibleSection("// LOG")
        self.logger = LoggerWidget()
        log_body = QtWidgets.QVBoxLayout()
        log_body.setContentsMargins(0, 0, 0, 0)
        log_body.addWidget(self.logger)
        self.log_section.set_body_layout(log_body)
        self.log_section.set_expanded(False)
        self.main_layout.addWidget(self.log_section)

    def _build_configs_panel(self):
        """The SHARED PROJECT CONFIGS pointer - which configs root THIS
        machine reads project definitions from. Lifted from the
        just-removed settings_ui._build_pointer_panel (same shape, same
        settings_app calls); it moved here because the pointer names
        project settings, and Mindmeld is the project-settings tool."""
        section = CollapsibleSection("SHARED PROJECT CONFIGS")
        layout = QtWidgets.QVBoxLayout()
        row = QtWidgets.QHBoxLayout()
        self.configs_root_edit = QtWidgets.QLineEdit()
        self.configs_root_edit.setReadOnly(True)   # Browse is the only writer
        mindmeld_style.tag(self.configs_root_edit, "panel")
        self.configs_browse_btn = mindmeld_style.button("Browse...",
                                                        kind="default")
        self.configs_local_btn = mindmeld_style.button("Use Local Default",
                                                       kind="ghost")
        row.addWidget(self.configs_root_edit, 1)
        row.addWidget(self.configs_browse_btn)
        row.addWidget(self.configs_local_btn)
        layout.addLayout(row)
        self.configs_status_label = mindmeld_style.helper_label("")
        layout.addWidget(self.configs_status_label)
        layout.addWidget(mindmeld_style.helper_label(
            "Where this machine reads project definitions from. Set once; "
            "teams point this at a shared location."))
        section.set_body_layout(layout)
        section.set_expanded(True)
        return section

    def _build_list_page(self):
        """Two-pane landing: the project list on the left (Set / New /
        Duplicate / Edit / Delete), an EDITABLE bindings
        view of the selected project on the right (Phase 3) - see
        docs/superpowers/plans/2026-07-20-mindmeld-bindings-unify.md."""
        page = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(page)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(mindmeld_style.field_label("Project Configs"))
        self.project_list = QtWidgets.QListWidget()
        left_layout.addWidget(self.project_list)

        # Empty-list affordance (Phase 4): when list_projects() comes back
        # empty, point the user at the SHARED PROJECT CONFIGS pointer at the
        # top of this same window - no "Change in Settings..." hop since
        # the pointer moved here in Phase 1b. Hidden whenever the list has
        # rows; toggled in populate().
        self.empty_list_label = mindmeld_style.helper_label("")
        self.empty_list_label.setWordWrap(True)
        self.empty_list_label.setVisible(False)
        left_layout.addWidget(self.empty_list_label)

        btn_row = QtWidgets.QHBoxLayout()
        self.new_btn = mindmeld_style.button("New", kind="default")
        self.duplicate_btn = mindmeld_style.button("Duplicate", kind="default")
        self.edit_btn = mindmeld_style.button("Edit", kind="default")
        self.delete_btn = mindmeld_style.button("Delete", kind="danger")
        # "Set" = apply the project's session settings + make it active
        # (project_setup_app.apply_and_activate). Placed first, to the left
        # of New, per Adrian 2026-07-20. Handler/var name unchanged.
        self.apply_activate_btn = mindmeld_style.button("Set", kind="primary")
        for b in (self.apply_activate_btn, self.new_btn, self.duplicate_btn,
                  self.edit_btn, self.delete_btn):
            btn_row.addWidget(b)
        left_layout.addLayout(btn_row)

        outer.addWidget(left, 1)

        # Right pane mirrors the left: the "Local Bindings" label sits ABOVE
        # the frame (group box), just as "Project Configs" sits above the
        # list (Adrian, 2026-07-20).
        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(mindmeld_style.field_label("Local Bindings"))
        right_layout.addWidget(self._build_bindings_view())
        outer.addWidget(right, 1)
        return page

    @staticmethod
    def _project_label(proj, active_slug):
        label = f'{proj["name"]}   //{proj["slug"]}   [{proj["engine"]}]'
        if proj["slug"] == active_slug:
            label += "   * ACTIVE"
        return label

    @staticmethod
    def _project_row_text_and_color(label, slug):
        """Plain-text row rendering (DPI-robust: no custom widget, no
        setItemWidget, no sizeHint floor - the list's natural row height is
        always readable). Status is conveyed by a compact text token PLUS a
        foreground color, reusing the exact hex values the right pane's
        BOUND/UNBOUND pill uses (mindmeld.qss pill-ok/pill-warn text
        color), so the list matches the pill without needing one."""
        status = settings_app.binding_status(slug)
        is_green = status["state"] == "green"
        text = f'{label}   {"BOUND" if is_green else "UNBOUND"}'
        color = QtGui.QColor(mindmeld_style.PLASMA if is_green
                              else mindmeld_style.EMBER)
        return text, color

    def _refresh_project_row_status(self, slug):
        """Recompute ONE row's text/color in place (no full populate(), so
        the current selection and scroll position never move) - called
        after a successful Bind (spec: 'recompute that row's green/orange
        status')."""
        proj = next((p for p in project_setup_app.list_projects()
                     if p["slug"] == slug), None)
        if proj is None:
            return
        active_slug = toolbar_prefs.load_prefs().get("active_project", "")
        label = self._project_label(proj, active_slug)
        text, color = self._project_row_text_and_color(label, slug)
        for row in range(self.project_list.count()):
            item = self.project_list.item(row)
            if item.data(QtCore.Qt.ItemDataRole.UserRole) == slug:
                item.setText(text)
                item.setForeground(QtGui.QBrush(color))
                return

    def _build_bindings_view(self):
        """The right-pane EDITABLE bindings view (Phase 3): the five
        binding fields as QLineEdit + Browse, plus Revert/Bind. Mirrors the
        shape of the removed settings_ui._build_editor / _BINDING_FIELDS
        pattern. Bind persists through the LOCAL-ONLY settings_app.bind_project
        path - never edit_project/create_project - so binding still works
        against a read-only shared configs root; see "Why Settings' bind
        path is not redundant" in
        docs/superpowers/specs/2026-07-20-mindmeld-bindings-unify-design.md."""
        box = QtWidgets.QGroupBox()
        layout = QtWidgets.QVBoxLayout(box)

        status_row = QtWidgets.QHBoxLayout()
        self.bindings_status_pill = mindmeld_style.pill("", kind="idle")
        self.bindings_status_label = mindmeld_style.helper_label("")
        status_row.addWidget(self.bindings_status_pill)
        status_row.addWidget(self.bindings_status_label, 1)
        layout.addLayout(status_row)

        # Derived-state affordance (Phase 4): visible only when the fields
        # below are pre-filled from the v1 derivation rather than a
        # persisted _user/<slug>.json - mirrors the removed settings_ui's
        # derived_label. The pre-fill is a suggestion shown in the fields,
        # never auto-committed; it persists only once the user clicks Bind.
        self.bindings_derived_label = mindmeld_style.helper_label("")
        self.bindings_derived_label.setVisible(False)
        layout.addWidget(self.bindings_derived_label)

        def _field_row():
            edit = QtWidgets.QLineEdit()
            browse_btn = mindmeld_style.button("Browse...", kind="ghost")
            row = QtWidgets.QHBoxLayout()
            row.addWidget(edit)
            row.addWidget(browse_btn)
            return edit, browse_btn, row

        form = QtWidgets.QFormLayout()
        self.bindings_source_edit, self.bindings_source_browse_btn, source_row = _field_row()
        self.bindings_content_edit, self.bindings_content_browse_btn, content_row = _field_row()
        (self.bindings_pose_override_edit, self.bindings_pose_override_browse_btn,
         pose_row) = _field_row()
        (self.bindings_anim_override_edit, self.bindings_anim_override_browse_btn,
         anim_row) = _field_row()
        (self.bindings_blueprints_override_edit,
         self.bindings_blueprints_override_browse_btn, bp_row) = _field_row()
        form.addRow(mindmeld_style.field_label("Source Art Root"), source_row)
        form.addRow(mindmeld_style.field_label("Content Root"), content_row)
        form.addRow(mindmeld_style.field_label("Pose Root Override"), pose_row)
        form.addRow(mindmeld_style.field_label("Anim Root Override"), anim_row)
        form.addRow(mindmeld_style.field_label("Blueprints Dir Override"), bp_row)
        layout.addLayout(form)
        layout.addWidget(mindmeld_style.helper_label(
            "Overrides are optional. Blank = under Source Art Root per the "
            "project's subpaths."))

        btn_row = QtWidgets.QHBoxLayout()
        self.bindings_revert_btn = mindmeld_style.button("Revert", kind="ghost")
        self.bindings_bind_btn = mindmeld_style.button("Bind", kind="primary")
        btn_row.addStretch()
        btn_row.addWidget(self.bindings_revert_btn)
        btn_row.addWidget(self.bindings_bind_btn)
        layout.addLayout(btn_row)
        layout.addStretch()
        return box

    def _build_editor_page(self):
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        form_layout = QtWidgets.QVBoxLayout(inner)
        form_layout.addWidget(self._build_identity_section())
        form_layout.addWidget(mindmeld_style.horizontal_rule())
        form_layout.addWidget(self._build_roots_section())
        form_layout.addWidget(mindmeld_style.horizontal_rule())
        form_layout.addWidget(self._build_session_section())
        form_layout.addWidget(mindmeld_style.horizontal_rule())
        form_layout.addWidget(mindmeld_style.field_label("Asset Classes"))
        form_layout.addWidget(self._build_asset_classes_editor())
        form_layout.addWidget(mindmeld_style.horizontal_rule())
        form_layout.addWidget(self._build_library_roots_section())
        form_layout.addWidget(mindmeld_style.horizontal_rule())
        form_layout.addWidget(self._build_blueprints_section())
        form_layout.addWidget(mindmeld_style.horizontal_rule())
        form_layout.addWidget(self._build_advanced_section())
        form_layout.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        btn_row = QtWidgets.QHBoxLayout()
        self.cancel_btn = mindmeld_style.button("Cancel", kind="ghost")
        self.save_btn = mindmeld_style.button("Save", kind="primary")
        btn_row.addStretch()
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.save_btn)
        outer.addLayout(btn_row)
        return page

    def _build_identity_section(self):
        box = QtWidgets.QGroupBox()
        form = QtWidgets.QFormLayout(box)

        self.name_edit = QtWidgets.QLineEdit()
        self.slug_preview_label = mindmeld_style.helper_label("")
        name_col = QtWidgets.QVBoxLayout()
        name_col.addWidget(self.name_edit)
        name_col.addWidget(self.slug_preview_label)
        form.addRow(mindmeld_style.field_label("Name"), name_col)

        self.engine_combo = QtWidgets.QComboBox()
        for template in project_setup_app.list_engine_templates():
            self.engine_combo.addItem(template["label"], userData=template["id"])
        form.addRow(mindmeld_style.field_label("Engine"), self.engine_combo)

        # Mirror convention (t48): the project-level default stamped
        # onto NEW rigs at creation. Existing rigs keep their stamp
        # (the rig registry is the truth, for portability), which is
        # exactly what _on_mirror_convention_changed warns about.
        self.mirror_convention_combo = QtWidgets.QComboBox()
        self.mirror_convention_combo.addItem(
            "Mirrored Behavior (Unreal)", userData="unreal")
        self.mirror_convention_combo.addItem(
            "Mirrored Orientation (Standard)", userData="standard")
        self.mirror_convention_combo.setToolTip(
            "How the mirrored side of a rig is oriented.\n"
            "Mirrored Behavior (Unreal): -X down the mirrored bone; both "
            "sides animate from identical local rotate values (Maya's "
            "mirrorBehavior).\n"
            "Mirrored Orientation (Standard): +X down the bone on BOTH "
            "sides with +Y matching; the common convention outside "
            "Unreal.\n"
            "Stamped onto each rig at creation. Existing rigs keep "
            "their stamp.\n"
            "Restart Maya after changing this setting for it to take "
            "effect.")
        form.addRow(mindmeld_style.field_label("Mirror Convention"),
                    self.mirror_convention_combo)
        return box

    def _build_roots_section(self):
        """The two machine roots (user tier, per spec sec 3): Source Art
        Root anchors scene matching, libraries, and scaffold; Content
        Root anchors export destinations."""
        box = QtWidgets.QGroupBox()
        form = QtWidgets.QFormLayout(box)
        self.source_art_root_edit = QtWidgets.QLineEdit()
        self.source_art_root_edit.setReadOnly(True)
        self.source_art_root_browse_btn = mindmeld_style.button("Browse", kind="ghost")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.source_art_root_edit)
        row.addWidget(self.source_art_root_browse_btn)
        form.addRow(mindmeld_style.field_label("Source Art Root"), row)

        self.content_root_edit = QtWidgets.QLineEdit()
        self.content_root_edit.setReadOnly(True)
        self.content_root_browse_btn = mindmeld_style.button("Browse", kind="ghost")
        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(self.content_root_edit)
        row2.addWidget(self.content_root_browse_btn)
        form.addRow(mindmeld_style.field_label("Content Root"), row2)

        form.addRow("", mindmeld_style.helper_label(
            "Per-machine paths (never shared): where THIS machine keeps "
            "the source art tree and the engine content tree."))
        return box

    def _build_session_section(self):
        box = QtWidgets.QGroupBox()
        form = QtWidgets.QFormLayout(box)

        self.linear_unit_combo = QtWidgets.QComboBox()
        self.linear_unit_combo.addItems(LINEAR_UNITS)
        form.addRow(mindmeld_style.field_label("Linear Unit"), self.linear_unit_combo)

        self.angular_unit_combo = QtWidgets.QComboBox()
        self.angular_unit_combo.addItems(ANGULAR_UNITS)
        form.addRow(mindmeld_style.field_label("Angular Unit"), self.angular_unit_combo)

        self.frame_rate_combo = QtWidgets.QComboBox()
        self.frame_rate_combo.setEditable(True)   # allows the "<n>fps" custom form
        self.frame_rate_combo.addItems(FRAME_RATES)
        form.addRow(mindmeld_style.field_label("Frame Rate"), self.frame_rate_combo)

        self.playback_enabled_cb = QtWidgets.QCheckBox("Set playback range")
        self.playback_start_spin = QtWidgets.QSpinBox()
        self.playback_start_spin.setRange(-100000, 100000)
        self.playback_end_spin = QtWidgets.QSpinBox()
        self.playback_end_spin.setRange(-100000, 100000)
        playback_row = QtWidgets.QHBoxLayout()
        playback_row.addWidget(self.playback_enabled_cb)
        playback_row.addWidget(QtWidgets.QLabel("Start"))
        playback_row.addWidget(self.playback_start_spin)
        playback_row.addWidget(QtWidgets.QLabel("End"))
        playback_row.addWidget(self.playback_end_spin)
        form.addRow(mindmeld_style.field_label("Playback"), playback_row)

        self.undo_depth_spin = QtWidgets.QSpinBox()
        self.undo_depth_spin.setRange(0, 100000)
        form.addRow(mindmeld_style.field_label("Undo Depth"), self.undo_depth_spin)

        self.camera_near_spin = QtWidgets.QDoubleSpinBox()
        self.camera_near_spin.setRange(0.0001, 100000.0)
        self.camera_near_spin.setDecimals(4)
        self.camera_far_spin = QtWidgets.QDoubleSpinBox()
        self.camera_far_spin.setRange(0.0001, 1000000.0)
        self.camera_far_spin.setDecimals(2)
        clip_row = QtWidgets.QHBoxLayout()
        clip_row.addWidget(QtWidgets.QLabel("Near"))
        clip_row.addWidget(self.camera_near_spin)
        clip_row.addWidget(QtWidgets.QLabel("Far"))
        clip_row.addWidget(self.camera_far_spin)
        form.addRow(mindmeld_style.field_label("Camera Clip"), clip_row)

        self.grid_section = CollapsibleSection("Grid (optional)")
        grid_body = QtWidgets.QFormLayout()
        self.grid_enabled_cb = QtWidgets.QCheckBox("Set grid")
        self.grid_size_spin = QtWidgets.QSpinBox()
        self.grid_size_spin.setRange(1, 100000)
        self.grid_spacing_spin = QtWidgets.QSpinBox()
        self.grid_spacing_spin.setRange(1, 10000)
        self.grid_divisions_spin = QtWidgets.QSpinBox()
        self.grid_divisions_spin.setRange(1, 1000)
        grid_body.addRow(self.grid_enabled_cb)
        grid_body.addRow("Size", self.grid_size_spin)
        grid_body.addRow("Spacing", self.grid_spacing_spin)
        grid_body.addRow("Divisions", self.grid_divisions_spin)
        self.grid_section.set_body_layout(grid_body)
        form.addRow(self.grid_section)

        return box

    def _build_asset_classes_editor(self):
        box = QtWidgets.QGroupBox()
        layout = QtWidgets.QVBoxLayout(box)
        self.asset_table = QtWidgets.QTableWidget(0, len(ASSET_CLASS_COLUMNS))
        self.asset_table.setHorizontalHeaderLabels(
            ["ID", "Label", "Export Types (comma)", "Source Dir", "Dest Dir",
             "Anim Gameplay", "Anim Cinematic"])
        self.asset_table.setMinimumHeight(180)
        self.asset_table.verticalHeader().setDefaultSectionSize(24)
        header = self.asset_table.horizontalHeader()
        header.setStretchLastSection(True)
        for col, width in enumerate((70, 90, 140, 160, 160, 140)):
            self.asset_table.setColumnWidth(col, width)
        layout.addWidget(self.asset_table)

        btn_row = QtWidgets.QHBoxLayout()
        self.add_asset_class_btn = mindmeld_style.button("Add Class", kind="ghost")
        self.remove_asset_class_btn = mindmeld_style.button("Remove Class", kind="ghost")
        btn_row.addWidget(self.add_asset_class_btn)
        btn_row.addWidget(self.remove_asset_class_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return box

    def _on_add_asset_class(self):
        row = self.asset_table.rowCount()
        self.asset_table.insertRow(row)
        for col, default in enumerate(("", "", "static_mesh", "", "", "", "")):
            self.asset_table.setItem(row, col, QtWidgets.QTableWidgetItem(default))
        self._on_field_changed()

    def _on_remove_asset_class(self):
        row = self.asset_table.currentRow()
        if row >= 0:
            self.asset_table.removeRow(row)
            self._on_field_changed()

    def _collect_asset_classes(self):
        classes = []
        for row in range(self.asset_table.rowCount()):
            def cell(col):
                item = self.asset_table.item(row, col)
                return item.text().strip() if item else ""
            id_ = cell(0)
            if not id_:
                continue   # a blank-id row is a not-yet-authored placeholder row
            export_types = [t.strip() for t in cell(2).split(",") if t.strip()]
            classes.append({
                "id": id_,
                "label": cell(1) or id_,
                "export_types": export_types,
                "source_dir": cell(3),
                "dest_dir": cell(4),
                "anim": {"gameplay": cell(5), "cinematic": cell(6)},
            })
        return classes

    def _populate_asset_classes(self, classes):
        self.asset_table.blockSignals(True)
        self.asset_table.setRowCount(0)
        for ac in classes:
            row = self.asset_table.rowCount()
            self.asset_table.insertRow(row)
            anim = ac.get("anim") or {}
            values = [ac.get("id", ""), ac.get("label", ""),
                      ", ".join(ac.get("export_types", [])),
                      ac.get("source_dir", ""), ac.get("dest_dir", ""),
                      anim.get("gameplay", ""), anim.get("cinematic", "")]
            for col, value in enumerate(values):
                self.asset_table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
        self.asset_table.blockSignals(False)

    def _build_library_roots_section(self):
        box = QtWidgets.QGroupBox()
        form = QtWidgets.QFormLayout(box)

        self.pose_derive_cb = QtWidgets.QCheckBox("Under Source Art Root")
        self.pose_root_edit = QtWidgets.QLineEdit()
        self.pose_browse_btn = mindmeld_style.button("Browse", kind="ghost")
        pose_row = QtWidgets.QHBoxLayout()
        pose_row.addWidget(self.pose_root_edit)
        pose_row.addWidget(self.pose_browse_btn)
        form.addRow(mindmeld_style.field_label("Pose Studio Root"), self.pose_derive_cb)
        form.addRow("", pose_row)

        self.anim_derive_cb = QtWidgets.QCheckBox("Under Source Art Root")
        self.anim_root_edit = QtWidgets.QLineEdit()
        self.anim_browse_btn = mindmeld_style.button("Browse", kind="ghost")
        anim_row = QtWidgets.QHBoxLayout()
        anim_row.addWidget(self.anim_root_edit)
        anim_row.addWidget(self.anim_browse_btn)
        form.addRow(mindmeld_style.field_label("Animation Studio Root"), self.anim_derive_cb)
        form.addRow("", anim_row)

        # Mesh Group Name (Adrian, 2026-07-09): the group the Rig Exporter's
        # "+ Create Entry" uses to find the mesh; blank falls back to a
        # geo_grp / mesh_grp scan.
        self.mesh_group_edit = QtWidgets.QLineEdit()
        self.mesh_group_edit.setPlaceholderText("geo_grp")
        form.addRow(mindmeld_style.field_label("Mesh Group Name"),
                    self.mesh_group_edit)
        return box

    def _build_blueprints_section(self):
        box = QtWidgets.QGroupBox()
        form = QtWidgets.QFormLayout(box)
        self.blueprints_blank_cb = QtWidgets.QCheckBox("Leave blank (factory blueprints only)")
        self.blueprints_dir_edit = QtWidgets.QLineEdit()
        self.blueprints_dir_edit.setReadOnly(True)
        self.blueprints_browse_btn = mindmeld_style.button("Browse", kind="ghost")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.blueprints_dir_edit)
        row.addWidget(self.blueprints_browse_btn)
        form.addRow(mindmeld_style.field_label("Blueprints Dir"), self.blueprints_blank_cb)
        form.addRow("", row)
        return box

    def _set_derived_pose_root(self):
        root = self.source_art_root_edit.text().strip()
        if root:
            self.pose_root_edit.setText(
                str(Path(root) / self._pose_subpath).replace("\\", "/"))

    def _set_derived_anim_root(self):
        root = self.source_art_root_edit.text().strip()
        if root:
            self.anim_root_edit.setText(
                str(Path(root) / self._anim_subpath).replace("\\", "/"))

    @staticmethod
    def _rel_under_root(abs_path, root):
        """abs_path relative to root (forward slashes) or None - the UI
        mirror of the loader's fold: a browsed path under the Source Art
        Root is stored as a portable project subpath, not a machine
        override."""
        if not abs_path or not root:
            return None
        a = os.path.normpath(abs_path)
        r = os.path.normpath(root)
        if not os.path.normcase(a).startswith(
                os.path.normcase(r).rstrip("\\/") + os.sep):
            return None
        return os.path.relpath(a, r).replace("\\", "/")

    def _on_pose_derive_toggled(self, checked):
        self.pose_root_edit.setReadOnly(checked)
        self.pose_browse_btn.setEnabled(not checked)
        if checked:
            self._set_derived_pose_root()
        self._on_field_changed()

    def _on_anim_derive_toggled(self, checked):
        self.anim_root_edit.setReadOnly(checked)
        self.anim_browse_btn.setEnabled(not checked)
        if checked:
            self._set_derived_anim_root()
        self._on_field_changed()

    def _on_browse_pose_root(self):
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Pose Studio Root", self.pose_root_edit.text() or str(Path.home()))
        if chosen:
            self.pose_root_edit.setText(chosen.replace("\\", "/"))
            self._on_field_changed()

    def _on_browse_anim_root(self):
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Animation Studio Root", self.anim_root_edit.text() or str(Path.home()))
        if chosen:
            self.anim_root_edit.setText(chosen.replace("\\", "/"))
            self._on_field_changed()

    def _on_blueprints_blank_toggled(self, checked):
        self.blueprints_browse_btn.setEnabled(not checked)
        self._bp_showing_preview = False
        if checked:
            self.blueprints_dir_edit.clear()
        self._on_field_changed()

    def _on_browse_blueprints_dir(self):
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Blueprints Directory", self.blueprints_dir_edit.text() or str(Path.home()))
        if chosen:
            self._bp_showing_preview = False   # user intent, not a preview
            self.blueprints_dir_edit.setText(chosen.replace("\\", "/"))
            self._on_field_changed()

    def _build_advanced_section(self):
        section = CollapsibleSection("Advanced (FBX Presets + Prefixes)")
        body = QtWidgets.QVBoxLayout()

        prefixes = QtWidgets.QFormLayout()
        self.prefix_static_edit = QtWidgets.QLineEdit()
        self.prefix_skeletal_edit = QtWidgets.QLineEdit()
        self.anim_prefix_edit = QtWidgets.QLineEdit()
        prefixes.addRow(mindmeld_style.field_label("Static Mesh Prefix"), self.prefix_static_edit)
        prefixes.addRow(mindmeld_style.field_label("Skeletal Mesh Prefix"), self.prefix_skeletal_edit)
        prefixes.addRow(mindmeld_style.field_label("Anim Prefix"), self.anim_prefix_edit)
        body.addLayout(prefixes)

        body.addWidget(mindmeld_style.field_label("FBX Presets (from engine template)"))
        body.addWidget(mindmeld_style.helper_label(
            "Presets come from the engine template and are whitelist-validated "
            "on save; edit the template to change them."))
        self.fbx_preview = QtWidgets.QPlainTextEdit()
        self.fbx_preview.setReadOnly(True)
        self.fbx_preview.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        # Mindmeld's QSS box-styles QPlainTextEdit (padding/border + min-height:
        # 16px). Once a widget is QSS-box-styled, Qt honors the stylesheet's
        # min-height OVER a code setMinimumHeight(), so a code-side height set is
        # silently ignored. Size it through QSS (the mechanism the engine
        # respects); the inherited border/bg/padding still cascade from the app
        # stylesheet since this rule only specifies min-height.
        self.fbx_preview.setStyleSheet("QPlainTextEdit { min-height: 300px; }")
        self.fbx_preview.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding)
        # Added to a QVBoxLayout with a stretch factor (not a QFormLayout row,
        # which sizes to the widget's small sizeHint and ignores minimumHeight),
        # so the preview actually claims its 300px+ and grows with the section.
        body.addWidget(self.fbx_preview, 1)

        section.set_body_layout(body)
        section.set_expanded(False)
        return section

    # ─────────────────────────────────────────
    # Signals
    # ─────────────────────────────────────────

    def connect_signals(self):
        self.configs_browse_btn.clicked.connect(self._on_configs_browse)
        self.configs_local_btn.clicked.connect(self._on_configs_local)
        self.project_list.currentItemChanged.connect(
            self._on_project_selection_changed)
        self.bindings_source_browse_btn.clicked.connect(
            lambda: self._on_browse_bindings_field(
                self.bindings_source_edit, "Select Source Art Root"))
        self.bindings_content_browse_btn.clicked.connect(
            lambda: self._on_browse_bindings_field(
                self.bindings_content_edit, "Select Content Root"))
        self.bindings_pose_override_browse_btn.clicked.connect(
            lambda: self._on_browse_bindings_field(
                self.bindings_pose_override_edit, "Select Pose Root Override"))
        self.bindings_anim_override_browse_btn.clicked.connect(
            lambda: self._on_browse_bindings_field(
                self.bindings_anim_override_edit, "Select Anim Root Override"))
        self.bindings_blueprints_override_browse_btn.clicked.connect(
            lambda: self._on_browse_bindings_field(
                self.bindings_blueprints_override_edit,
                "Select Blueprints Dir Override"))
        for edit in (self.bindings_source_edit, self.bindings_content_edit,
                     self.bindings_pose_override_edit, self.bindings_anim_override_edit,
                     self.bindings_blueprints_override_edit):
            edit.textChanged.connect(self._on_bindings_field_changed)
        self.bindings_revert_btn.clicked.connect(self._on_bindings_revert)
        self.bindings_bind_btn.clicked.connect(self._on_bindings_bind)
        self.new_btn.clicked.connect(self._on_new)
        self.duplicate_btn.clicked.connect(self._on_duplicate)
        self.edit_btn.clicked.connect(self._on_edit)
        self.delete_btn.clicked.connect(self._on_delete)
        self.apply_activate_btn.clicked.connect(self._on_apply_activate)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        self.mirror_convention_combo.currentIndexChanged.connect(
            self._on_mirror_convention_changed)
        self.name_edit.textChanged.connect(self._on_name_changed)
        self.source_art_root_browse_btn.clicked.connect(self._on_browse_source_art_root)
        self.content_root_browse_btn.clicked.connect(self._on_browse_content_root)
        self.source_art_root_edit.textChanged.connect(lambda _t: self._on_roots_changed())
        self.content_root_edit.textChanged.connect(lambda _t: self._on_field_changed())
        self.save_btn.clicked.connect(self._on_save)
        for widget, signal in (
            (self.linear_unit_combo, "currentIndexChanged"),
            (self.angular_unit_combo, "currentIndexChanged"),
            (self.frame_rate_combo, "currentTextChanged"),
            (self.playback_enabled_cb, "toggled"),
            (self.playback_start_spin, "valueChanged"),
            (self.playback_end_spin, "valueChanged"),
            (self.undo_depth_spin, "valueChanged"),
            (self.camera_near_spin, "valueChanged"),
            (self.camera_far_spin, "valueChanged"),
            (self.grid_enabled_cb, "toggled"),
            (self.grid_size_spin, "valueChanged"),
            (self.grid_spacing_spin, "valueChanged"),
            (self.grid_divisions_spin, "valueChanged"),
        ):
            getattr(widget, signal).connect(self._on_field_changed)
        self.asset_table.itemChanged.connect(self._on_field_changed)
        self.add_asset_class_btn.clicked.connect(self._on_add_asset_class)
        self.remove_asset_class_btn.clicked.connect(self._on_remove_asset_class)
        self.pose_derive_cb.toggled.connect(self._on_pose_derive_toggled)
        self.pose_browse_btn.clicked.connect(self._on_browse_pose_root)
        self.anim_derive_cb.toggled.connect(self._on_anim_derive_toggled)
        self.anim_browse_btn.clicked.connect(self._on_browse_anim_root)
        self.blueprints_blank_cb.toggled.connect(self._on_blueprints_blank_toggled)
        self.blueprints_browse_btn.clicked.connect(self._on_browse_blueprints_dir)
        for widget in (self.prefix_static_edit, self.prefix_skeletal_edit, self.anim_prefix_edit):
            widget.textChanged.connect(self._on_field_changed)

    # ─────────────────────────────────────────
    # Populate
    # ─────────────────────────────────────────

    def populate(self):
        self._refresh_configs_panel()
        # Rebuilding the list re-fires currentItemChanged (Qt auto-selects
        # row 0 the moment the first item lands) - suppress the right-pane
        # handler during the rebuild with the same _populating guard the
        # editor form uses, then do exactly one deliberate refresh below.
        self._populating = True
        try:
            self.project_list.clear()
            active_slug = toolbar_prefs.load_prefs().get("active_project", "")
            projects = project_setup_app.list_projects()
            for proj in projects:
                label = self._project_label(proj, active_slug)
                text, color = self._project_row_text_and_color(label, proj["slug"])
                item = QtWidgets.QListWidgetItem(text)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, proj["slug"])
                item.setForeground(QtGui.QBrush(color))
                self.project_list.addItem(item)
            if projects:
                self.empty_list_label.setText("")
                self.empty_list_label.setVisible(False)
            else:
                self.empty_list_label.setText(
                    "No projects at this configs root - set Shared "
                    "Project Configs above.")
                self.empty_list_label.setVisible(True)
        finally:
            self._populating = False
        self._refresh_bindings_panel()

    def _refresh_configs_panel(self):
        status = settings_app.configs_root_status()
        self._configs_root_status = status
        self.configs_root_edit.setText(status["effective_root"])
        env = status["source"] == "env"
        self.configs_browse_btn.setEnabled(not env)
        self.configs_local_btn.setEnabled(status["source"] == "pointer")
        text = f"Source: {_CONFIGS_SOURCE_LABELS[status['source']]}"
        if env:
            text += ("   Set by FABRICATOR_PROJECT_CONFIGS. The pointer is "
                     "ignored while it is set.")
        self.configs_status_label.setText(text)

    # ─────────────────────────────────────────
    # Shared Project Configs pointer
    # ─────────────────────────────────────────

    def _on_configs_browse(self):
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Shared Configs Root",
            getattr(self, "_configs_root_status", {}).get("pointer_value")
            or self.configs_root_edit.text() or str(Path.home()))
        if not chosen:
            return
        try:
            settings_app.set_configs_root(chosen)
        except settings_app.ValidationError as exc:
            message = "; ".join(issue.message for issue in exc.issues)
            self.logger.error(message)
            return
        except Exception as exc:
            self.logger.error(f"Could not save the configs-root pointer: {exc}")
            return
        self.logger.success(f"Shared configs root set to {chosen}.")
        self.populate()

    def _on_configs_local(self):
        try:
            settings_app.clear_configs_root()
        except Exception as exc:
            self.logger.error(f"Could not clear the configs-root pointer: {exc}")
            return
        self.logger.success("Configs root reset to the local default.")
        self.populate()

    # ─────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────

    def _selected_slug(self):
        item = self.project_list.currentItem()
        if item is None:
            return None
        return item.data(QtCore.Qt.ItemDataRole.UserRole)

    # ─────────────────────────────────────────
    # Right pane: editable bindings + local-only Bind (Phase 3)
    # ─────────────────────────────────────────

    def _on_project_selection_changed(self, _current, _previous):
        if self._populating:
            return
        if self._bindings_dirty and self._bindings_slug:
            self.logger.info(
                f'Discarded unsaved binding edits for "{self._bindings_slug}".')
        self._refresh_bindings_panel()

    def _bindings_widgets(self):
        return (self.bindings_source_edit, self.bindings_content_edit,
                self.bindings_pose_override_edit, self.bindings_anim_override_edit,
                self.bindings_blueprints_override_edit)

    def _bindings_browse_buttons(self):
        return (self.bindings_source_browse_btn, self.bindings_content_browse_btn,
                self.bindings_pose_override_browse_btn,
                self.bindings_anim_override_browse_btn,
                self.bindings_blueprints_override_browse_btn)

    def _set_bindings_editing_enabled(self, enabled):
        for widget in (*self._bindings_widgets(), *self._bindings_browse_buttons(),
                       self.bindings_revert_btn):
            widget.setEnabled(enabled)
        if not enabled:
            self.bindings_bind_btn.setEnabled(False)

    def _bindings_are_derived(self, slug):
        """True iff `slug` has no persisted _user/<slug>.json but
        project_config_io.derive_v1_bindings yields roots - same precedence
        settings_app.list_projects_with_status uses ('bound and not
        persisted'). Read-only: never writes, so selecting a never-bound
        project to check this can't itself persist anything."""
        if project_config_resolve.load_bindings(slug):
            return False
        derived = project_config_io.derive_v1_bindings(slug)
        return bool(derived and derived.get("source_art_root"))

    def _refresh_bindings_panel(self):
        """(Re)populate the right pane from the CURRENTLY selected row (or
        blank + disable it when nothing is selected), reset the dirty flag,
        and run one revalidate. Guarded by _populating so the five
        textChanged signals fired by the setText() calls below don't each
        trigger a half-populated _revalidate_bindings."""
        slug = self._selected_slug()
        self._bindings_slug = slug
        self._bindings_dirty = False
        self._populating = True
        try:
            if slug is None:
                for edit in self._bindings_widgets():
                    edit.clear()
                mindmeld_style.tag(self.bindings_status_pill, "pill-idle")
                self.bindings_status_pill.setText("")
                self.bindings_status_label.setText(
                    "Select a project to view its bindings.")
                self.bindings_derived_label.setText("")
                self.bindings_derived_label.setVisible(False)
            else:
                bindings = settings_app.get_bindings(slug)
                self.bindings_source_edit.setText(bindings.get("source_art_root", ""))
                self.bindings_content_edit.setText(bindings.get("content_root", ""))
                self.bindings_pose_override_edit.setText(
                    bindings.get("pose_library_root_override", ""))
                self.bindings_anim_override_edit.setText(
                    bindings.get("anim_library_root_override", ""))
                self.bindings_blueprints_override_edit.setText(
                    bindings.get("blueprints_dir_override", ""))
                self._refresh_bindings_status_pill(slug)
                # Derived pre-fill is a SUGGESTION shown in the fields, never
                # auto-committed - it persists only when the user clicks
                # Bind (settings_app.bind_project below).
                if self._bindings_are_derived(slug):
                    self.bindings_derived_label.setText(
                        "Derived from the v1 config - Bind to persist for "
                        "this machine.")
                    self.bindings_derived_label.setVisible(True)
                else:
                    self.bindings_derived_label.setText("")
                    self.bindings_derived_label.setVisible(False)
        finally:
            self._populating = False
        self._set_bindings_editing_enabled(slug is not None)
        self._revalidate_bindings()

    def _refresh_bindings_status_pill(self, slug):
        status = settings_app.binding_status(slug)
        if status["state"] == "green":
            mindmeld_style.tag(self.bindings_status_pill, "pill-ok")
            self.bindings_status_pill.setText("BOUND")
            self.bindings_status_label.setText(
                "Both roots resolve on this machine.")
        else:
            mindmeld_style.tag(self.bindings_status_pill, "pill-warn")
            self.bindings_status_pill.setText("UNBOUND")
            reason = status["reason"]
            if reason.startswith("missing:"):
                path = reason.split("missing:", 1)[1]
                self.bindings_status_label.setText(
                    f"Path no longer exists on this machine: {path}")
            else:
                self.bindings_status_label.setText(
                    "Not bound on this machine yet.")

    def _collect_right_pane_bindings(self):
        return {
            "source_art_root": self.bindings_source_edit.text().strip(),
            "content_root": self.bindings_content_edit.text().strip(),
            "pose_library_root_override": self.bindings_pose_override_edit.text().strip(),
            "anim_library_root_override": self.bindings_anim_override_edit.text().strip(),
            "blueprints_dir_override": self.bindings_blueprints_override_edit.text().strip(),
        }

    def _on_bindings_field_changed(self, *_args):
        if self._populating:
            return
        self._bindings_dirty = True
        self._revalidate_bindings()

    def _revalidate_bindings(self):
        """Inline validation for the right pane, same posture as the main
        editor's _revalidate: an untouched, still-empty, never-bound
        project skips noisy required-field errors (no error spam the
        moment a project is selected); anything else runs
        config_validation.validate_bindings for real, errors disable
        Bind, warnings do not."""
        if self._bindings_slug is None:
            self.bindings_bind_btn.setEnabled(False)
            return
        bindings = self._collect_right_pane_bindings()
        if not self._bindings_dirty and not any(bindings.values()):
            self.validation_logger.clear()
            self.bindings_bind_btn.setEnabled(False)
            return
        issues = config_validation.validate_bindings(
            bindings,
            existing_bindings=project_setup_app._existing_bindings(
                exclude_slug=self._bindings_slug))
        self._show_issues(issues, self.bindings_bind_btn)

    def _on_browse_bindings_field(self, edit, title):
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, title, edit.text() or str(Path.home()))
        if chosen:
            edit.setText(chosen.replace("\\", "/"))

    def _on_bindings_revert(self):
        """Reload the PERSISTED bindings for the selected project and clear
        the dirty flag - mirrors the removed settings_ui._on_revert."""
        if self._bindings_slug is None:
            return
        slug = self._bindings_slug
        self._refresh_bindings_panel()
        self.logger.info(f'Reverted binding edits for "{slug}".')

    def _on_bindings_bind(self):
        """The right-pane Bind action: settings_app.bind_project persists
        ONLY _user/<slug>.json (project_config_resolve.save_bindings) -
        never edit_project/create_project, so this works even when the
        shared configs root is read-only. On success: log, recompute this
        row's pill + the right-pane status pill, and keep the project
        selected."""
        slug = self._bindings_slug
        if slug is None:
            return
        bindings = self._collect_right_pane_bindings()
        try:
            settings_app.bind_project(slug, bindings)
        except settings_app.ValidationError as exc:
            self._show_issues(exc.issues, self.bindings_bind_btn)
            return
        except Exception as exc:
            self.logger.error(f'Could not bind "{slug}": {exc}')
            return
        self.logger.success(f'Bound "{slug}".')
        self._bindings_dirty = False
        self._refresh_bindings_status_pill(slug)
        self._refresh_project_row_status(slug)
        self._revalidate_bindings()

    def _populate_editor(self, authored, bindings=None):
        bindings = bindings or {}
        self._populating = True
        try:
            self._populate_editor_body(authored, bindings)
        finally:
            self._populating = False
        self._revalidate()

    def _populate_editor_body(self, authored, bindings):
        self.name_edit.setText(authored.get("name", ""))
        engine = authored.get("engine", "")
        idx = self.engine_combo.findData(engine)
        self.engine_combo.setCurrentIndex(idx if idx >= 0 else 0)
        conv = authored.get("orient_convention") or "unreal"
        cidx = self.mirror_convention_combo.findData(conv)
        self.mirror_convention_combo.setCurrentIndex(cidx if cidx >= 0 else 0)
        self._loaded_convention = conv if cidx >= 0 else "unreal"
        self.source_art_root_edit.setText(bindings.get("source_art_root", ""))
        self.content_root_edit.setText(bindings.get("content_root", ""))
        self.linear_unit_combo.setCurrentText(authored.get("linear_unit", "cm"))
        self.angular_unit_combo.setCurrentText(authored.get("angular_unit", "deg"))
        self.frame_rate_combo.setCurrentText(authored.get("frame_rate", "ntsc"))
        start, end = authored.get("playback_start"), authored.get("playback_end")
        has_playback = start is not None and end is not None
        self.playback_enabled_cb.setChecked(has_playback)
        self.playback_start_spin.setValue(start if has_playback else 0)
        self.playback_end_spin.setValue(end if has_playback else 0)
        self.undo_depth_spin.setValue(authored.get("undo_depth", 0))
        self.camera_near_spin.setValue(authored.get("camera_near_clip", 0.1))
        self.camera_far_spin.setValue(authored.get("camera_far_clip", 10000.0))
        grid = authored.get("grid") or {}
        self.grid_enabled_cb.setChecked(bool(grid))
        self.grid_size_spin.setValue(grid.get("size", 12))
        self.grid_spacing_spin.setValue(grid.get("spacing", 5))
        self.grid_divisions_spin.setValue(grid.get("divisions", 5))
        self._populate_asset_classes(authored.get("asset_classes", []))

        # Project-tier carries (no dedicated widgets). A PRESENT-but-blank
        # subpath is a real state (exporter-fallback naming) and must
        # survive Edit -> Save untouched - coercing it to the default here
        # would silently change TEAMMATES' resolution; the default seeds
        # only when the key is absent entirely (bare New).
        self._pose_subpath = authored.get("pose_library_subpath", _DERIVED_POSE_SUBDIR) or ""
        self._anim_subpath = authored.get("anim_library_subpath", _DERIVED_ANIM_SUBDIR) or ""
        self._blueprints_subpath = authored.get("blueprints_subpath") or ""
        self._engine_up_axis = authored.get("engine_up_axis") or ""

        src = bindings.get("source_art_root", "")
        pose_override = bindings.get("pose_library_root_override", "")
        anim_override = bindings.get("anim_library_root_override", "")
        bp_override = bindings.get("blueprints_dir_override", "")

        # Derived = no machine override; the edit previews the resolved path.
        self.pose_derive_cb.setChecked(not pose_override)
        self.pose_root_edit.setText(
            pose_override or (str(Path(src) / self._pose_subpath).replace("\\", "/")
                              if src else ""))
        self.anim_derive_cb.setChecked(not anim_override)
        self.anim_root_edit.setText(
            anim_override or (str(Path(src) / self._anim_subpath).replace("\\", "/")
                              if src else ""))

        has_blueprints = bool(self._blueprints_subpath or bp_override)
        self.blueprints_blank_cb.setChecked(not has_blueprints)
        self.blueprints_dir_edit.setText(
            bp_override or (str(Path(src) / self._blueprints_subpath).replace("\\", "/")
                            if (src and self._blueprints_subpath) else ""))
        self._bp_showing_preview = bool(
            has_blueprints and not bp_override and self._blueprints_subpath)
        self.mesh_group_edit.setText(authored.get("mesh_group_name", ""))
        prefixes = authored.get("prefixes") or {}
        self.prefix_static_edit.setText(prefixes.get("static_mesh", ""))
        self.prefix_skeletal_edit.setText(prefixes.get("skeletal_mesh", ""))
        self.anim_prefix_edit.setText(authored.get("anim_prefix", ""))
        self._fbx_presets = authored.get("fbx_presets", {})
        self.fbx_preview.setPlainText(json.dumps(self._fbx_presets, indent=2))

    def _fold_library(self, derive_cb, edit, carried_subpath):
        """(subpath, override) for one library row. Derived -> the carried
        project subpath, no override. Custom -> a browsed path under the
        Source Art Root folds to a portable subpath (same rule as the v1
        loader migration); anything else is a machine override."""
        if derive_cb.isChecked():
            return carried_subpath, ""
        text = edit.text().strip()
        if not text:
            return carried_subpath, ""
        rel = self._rel_under_root(text, self.source_art_root_edit.text().strip())
        if rel is not None:
            return rel, ""
        return carried_subpath, text.replace("\\", "/")

    def _fold_blueprints(self):
        """(subpath, override) for the blueprints row. A showing PREVIEW is
        carried project state, never folded into an override - the text is
        ours, not the user's."""
        if self.blueprints_blank_cb.isChecked():
            return "", ""
        if self._bp_showing_preview:
            return self._blueprints_subpath, ""
        text = self.blueprints_dir_edit.text().strip()
        if not text:
            return self._blueprints_subpath, ""
        rel = self._rel_under_root(text, self.source_art_root_edit.text().strip())
        if rel is not None:
            return rel, ""
        return self._blueprints_subpath, text.replace("\\", "/")

    def _collect_authored(self):
        playback_enabled = self.playback_enabled_cb.isChecked()
        grid_enabled = self.grid_enabled_cb.isChecked()
        pose_subpath, _ = self._fold_library(
            self.pose_derive_cb, self.pose_root_edit, self._pose_subpath)
        anim_subpath, _ = self._fold_library(
            self.anim_derive_cb, self.anim_root_edit, self._anim_subpath)
        bp_subpath, _ = self._fold_blueprints()
        return {
            "name": self.name_edit.text().strip(),
            "engine": self.engine_combo.currentData(),
            "linear_unit": self.linear_unit_combo.currentText(),
            "angular_unit": self.angular_unit_combo.currentText(),
            "frame_rate": self.frame_rate_combo.currentText().strip(),
            "playback_start": self.playback_start_spin.value() if playback_enabled else None,
            "playback_end": self.playback_end_spin.value() if playback_enabled else None,
            "undo_depth": self.undo_depth_spin.value(),
            "camera_near_clip": self.camera_near_spin.value(),
            "camera_far_clip": self.camera_far_spin.value(),
            "grid": ({"size": self.grid_size_spin.value(),
                      "spacing": self.grid_spacing_spin.value(),
                      "divisions": self.grid_divisions_spin.value()}
                     if grid_enabled else {}),
            "asset_classes": self._collect_asset_classes(),
            "pose_library_subpath": pose_subpath,
            "anim_library_subpath": anim_subpath,
            "blueprints_subpath": bp_subpath,
            "mesh_group_name": self.mesh_group_edit.text().strip(),
            "engine_up_axis": self._engine_up_axis,
            "orient_convention": self.mirror_convention_combo.currentData(),
            "prefixes": {"static_mesh": self.prefix_static_edit.text().strip(),
                         "skeletal_mesh": self.prefix_skeletal_edit.text().strip()},
            "anim_prefix": self.anim_prefix_edit.text().strip(),
            "fbx_presets": self._fbx_presets,
        }

    def _collect_bindings(self):
        _, pose_override = self._fold_library(
            self.pose_derive_cb, self.pose_root_edit, self._pose_subpath)
        _, anim_override = self._fold_library(
            self.anim_derive_cb, self.anim_root_edit, self._anim_subpath)
        _, bp_override = self._fold_blueprints()
        return {
            "source_art_root": self.source_art_root_edit.text().strip(),
            "content_root": self.content_root_edit.text().strip(),
            "pose_library_root_override": pose_override,
            "anim_library_root_override": anim_override,
            "blueprints_dir_override": bp_override,
        }

    def _on_name_changed(self, text):
        slug = project_config_io.slugify(text)
        self.slug_preview_label.setText(f"Folder: {slug}" if slug else "")
        self._on_field_changed()

    def _on_browse_source_art_root(self):
        start_dir = self.source_art_root_edit.text() or str(Path.home())
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Source Art Root", start_dir)
        if chosen:
            self.source_art_root_edit.setText(chosen.replace("\\", "/"))
            self._on_roots_changed()

    def _on_browse_content_root(self):
        start_dir = self.content_root_edit.text() or str(Path.home())
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Content Root", start_dir)
        if chosen:
            self.content_root_edit.setText(chosen.replace("\\", "/"))
            self._on_roots_changed()

    def _on_roots_changed(self):
        if self.pose_derive_cb.isChecked():
            self._set_derived_pose_root()
        if self.anim_derive_cb.isChecked():
            self._set_derived_anim_root()
        if self._bp_showing_preview and not self.blueprints_blank_cb.isChecked():
            # Re-derive the blueprints preview too, or a re-bound Source
            # Art Root leaves the OLD root's text to be collected as a
            # machine override the user never authored.
            src = self.source_art_root_edit.text().strip()
            self.blueprints_dir_edit.setText(
                str(Path(src) / self._blueprints_subpath).replace("\\", "/")
                if (src and self._blueprints_subpath) else "")
        self._on_field_changed()

    def _existing_authored_excluding_current(self):
        out = []
        for proj in project_setup_app.list_projects():
            if proj["slug"] == self._editing_slug:
                continue
            try:
                out.append(project_config_io.load_project(proj["slug"]))
            except Exception:
                continue
        return out

    def _show_issues(self, issues, button=None):
        """Render errors/warnings into the shared VALIDATION logger and
        disable/enable `button` (defaults to save_btn; the right-pane Bind
        wiring passes bindings_bind_btn) on any error-severity issue."""
        button = button or self.save_btn
        self.validation_logger.clear()
        for issue in issues:
            if issue.severity == "error":
                self.validation_logger.error(f"{issue.field}: {issue.message}")
            else:
                self.validation_logger.warning(f"{issue.field}: {issue.message}")
        button.setEnabled(not any(i.severity == "error" for i in issues))

    def _revalidate(self):
        if (self._editing_slug is None
                and not self.source_art_root_edit.text().strip()
                and not self.name_edit.text().strip()):
            # Fresh New page before anything is filled in: skip noisy validation.
            self.validation_logger.clear()
            self.save_btn.setEnabled(False)
            return
        authored = self._collect_authored()
        existing = self._existing_authored_excluding_current()
        issues = list(config_validation.validate_config(
            authored, existing_projects=existing))
        issues += config_validation.validate_bindings(
            self._collect_bindings(),
            existing_bindings=project_setup_app._existing_bindings(
                exclude_slug=self._editing_slug))
        self._show_issues(issues)

    def _on_field_changed(self, *_args):
        if self._populating:
            return
        self._revalidate()

    def _on_new(self):
        self._editing_slug = None
        templates = project_setup_app.list_engine_templates()
        default_id = templates[0]["id"] if templates else None
        try:
            authored = project_setup_app.template_defaults(default_id) if default_id else {}
        except Exception:
            authored = {}
        self._populate_editor(authored, {})   # New: roots start unbound
        self.name_edit.clear()             # New starts blank; template only seeds the rest
        self.engine_combo.setEnabled(True)
        self.pages.setCurrentIndex(1)

    def _on_mirror_convention_changed(self, _index):
        """The big red gate (t48): changing an EXISTING project's mirror
        convention affects NEW rigs only. Rigs already created keep the
        convention stamped on their registry (the rig is the truth, for
        portability), so a mid-production change leaves the project
        mixed-convention with no automated migration. Confirm or
        revert. New-project mode passes through silently; programmatic
        populate is guarded by _populating."""
        if self._populating:
            return
        new = self.mirror_convention_combo.currentData()
        if self._editing_slug is None or new == self._loaded_convention:
            self._on_field_changed()
            return
        reply = QtWidgets.QMessageBox.warning(
            self, "Change Mirror Convention?",
            "This setting only affects rigs created AFTER the change.\n\n"
            "Rigs that already exist keep the mirror convention stamped "
            "on them at creation. Nothing is migrated, so this project "
            "will contain rigs with MIXED conventions. Mirroring, "
            "Symmetry, and builds will behave differently between old "
            "and new rigs.\n\n"
            "Restart Maya after this change for it to take effect.\n\n"
            "Change the mirror convention for new rigs?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No)
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            self._populating = True
            try:
                idx = self.mirror_convention_combo.findData(
                    self._loaded_convention)
                self.mirror_convention_combo.setCurrentIndex(max(idx, 0))
            finally:
                self._populating = False
            return
        self._on_field_changed()

    def _on_engine_changed(self, index):
        if self._editing_slug is not None:
            return   # engine is fixed once a project already exists
        template_id = self.engine_combo.itemData(index)
        if not template_id:
            return
        try:
            authored = project_setup_app.template_defaults(template_id)
        except Exception as exc:
            self.logger.warning(f"Could not load {template_id} template: {exc}")
            return
        name = self.name_edit.text()
        bindings = self._collect_bindings()   # template switch keeps typed roots
        # Library rows are user/machine intent, not template content: a
        # template switch changes session defaults, classes, presets, and
        # the engine axis - never where THIS user keeps libraries. Restore
        # the rows wholesale (subpath carries included, or an under-root
        # custom pick would be silently replaced by the template default).
        lib_state = (self._pose_subpath, self._anim_subpath,
                     self._blueprints_subpath, self._bp_showing_preview,
                     self.pose_derive_cb.isChecked(), self.pose_root_edit.text(),
                     self.anim_derive_cb.isChecked(), self.anim_root_edit.text(),
                     self.blueprints_blank_cb.isChecked(),
                     self.blueprints_dir_edit.text())
        self._populate_editor(authored, bindings)
        (self._pose_subpath, self._anim_subpath,
         self._blueprints_subpath, self._bp_showing_preview) = lib_state[:4]
        self.pose_derive_cb.setChecked(lib_state[4])
        self.pose_root_edit.setText(lib_state[5])
        self.anim_derive_cb.setChecked(lib_state[6])
        self.anim_root_edit.setText(lib_state[7])
        self.blueprints_blank_cb.setChecked(lib_state[8])
        self.blueprints_dir_edit.setText(lib_state[9])
        self.name_edit.setText(name)       # don't clobber what the user already typed

    def _on_edit(self):
        slug = self._selected_slug()
        if slug is None:
            return
        try:
            authored = project_config_io.load_project(slug)
        except Exception as exc:
            self.logger.error(f"Could not load {slug}: {exc}")
            return
        self._editing_slug = slug
        bindings = (project_config_resolve.load_bindings(slug)
                    or project_config_io.derive_v1_bindings(slug) or {})
        self._populate_editor(authored, bindings)
        if not bindings.get("source_art_root"):
            self.logger.warning(
                f'"{slug}" is not bound on this machine yet - set Source '
                f'Art Root and Content Root, then Save.')
        self.engine_combo.setEnabled(False)
        self.pages.setCurrentIndex(1)

    def _on_duplicate(self):
        slug = self._selected_slug()
        if slug is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Duplicate Project", "New project name:")
        if not ok or not name.strip():
            return
        try:
            project_setup_app.duplicate_project(slug, name.strip())
        except Exception as exc:
            self.logger.error(f"Could not duplicate: {exc}")
            return
        new_slug = project_config_io.slugify(name.strip())
        self.populate()
        self._editing_slug = new_slug
        try:
            self._populate_editor(
                project_config_io.load_project(new_slug),
                project_config_resolve.load_bindings(new_slug))
        except Exception as exc:
            self.logger.error(f"Duplicated but could not reload: {exc}")
            return
        self.engine_combo.setEnabled(False)
        self.pages.setCurrentIndex(1)

    def _on_delete(self):
        slug = self._selected_slug()
        if slug is None:
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Delete Project", f'Move "{slug}" to the trash?',
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            project_setup_app.delete_project(slug)
        except Exception as exc:
            self.logger.error(f"Could not delete: {exc}")
            return
        self.logger.success(f'Moved "{slug}" to trash.')
        self.populate()

    def _on_apply_activate(self):
        slug = self._selected_slug()
        if slug is None:
            return
        try:
            project_setup_app.apply_and_activate(slug)
        except Exception as exc:
            self.logger.error(f"Apply & Activate failed: {exc}")
            return
        self.logger.success(f'Applied and activated "{slug}".')
        self.populate()

    def _on_cancel(self):
        self._editing_slug = None
        self.pages.setCurrentIndex(0)

    def _on_save(self):
        authored = self._collect_authored()
        bindings = self._collect_bindings()
        existing = self._existing_authored_excluding_current()
        issues = list(config_validation.validate_config(
            authored, existing_projects=existing))
        issues += config_validation.validate_bindings(
            bindings,
            existing_bindings=project_setup_app._existing_bindings(
                exclude_slug=self._editing_slug))
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            self._show_issues(issues)
            return
        try:
            if self._editing_slug is None:
                path = project_setup_app.create_project(authored, bindings)
            else:
                path = project_setup_app.edit_project(
                    self._editing_slug, authored, bindings)
        except project_setup_app.ValidationError as exc:
            self._show_issues(exc.issues)
            return
        except (FileExistsError, OSError) as exc:
            self.logger.error(f"Save failed: {exc}")
            return
        slug = path.name
        self.logger.success(f'Saved: {authored.get("name", slug)}  ({path})')
        self._offer_scaffold(slug, authored, bindings)
        self._editing_slug = None
        self.populate()
        self.pages.setCurrentIndex(0)

    def _offer_scaffold(self, slug, authored, bindings):
        resolved = dict(authored)
        resolved["source_art_root"] = bindings.get("source_art_root", "")
        resolved["content_root"] = bindings.get("content_root", "")
        resolved["pose_library_root"] = (
            bindings.get("pose_library_root_override", "")
            or (str(Path(resolved["source_art_root"]) / authored.get("pose_library_subpath", ""))
                .replace("\\", "/")
                if authored.get("pose_library_subpath") else ""))
        resolved["anim_library_root"] = (
            bindings.get("anim_library_root_override", "")
            or (str(Path(resolved["source_art_root"]) / authored.get("anim_library_subpath", ""))
                .replace("\\", "/")
                if authored.get("anim_library_subpath") else ""))
        resolved["blueprints_dir"] = (
            bindings.get("blueprints_dir_override", "")
            or (str(Path(resolved["source_art_root"]) / authored.get("blueprints_subpath", ""))
                .replace("\\", "/")
                if authored.get("blueprints_subpath") else ""))
        candidates = project_scaffold.candidate_dirs(resolved)
        if not candidates:
            return
        dialog = _ScaffoldChecklistDialog(candidates, parent=self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            keys = dialog.checked_keys()
            if keys:
                created = project_setup_app.scaffold(slug, keys)
                self.logger.info(f"Scaffolded {len(created)} folder(s).")

    # ─────────────────────────────────────────
    # Show
    # ─────────────────────────────────────────

    @classmethod
    def show_window(cls):
        global _win
        # Closing the dialog only HIDES it (no WA_DeleteOnClose; the Maya
        # main window parent keeps the C++ widget alive), so a closed
        # instance still matches by objectName. Only a visible match counts
        # as "already open"; hidden leftovers are swept and rebuilt, else
        # the tool opens once per session.
        stale = []
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if widget.objectName() != cls.WINDOW_NAME:
                continue
            if widget.isVisible():
                widget.raise_()
                widget.activateWindow()
                return
            stale.append(widget)
        for widget in stale:
            widget.close()
            widget.deleteLater()
        _win = cls(get_maya_window())
        _win.show()


_win = None
