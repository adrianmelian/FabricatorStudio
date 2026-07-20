# settings_ui.py
# Settings - the per-machine face over the _user/ state families: the
# shared-configs-root pointer (_user/settings.json) and per-project machine
# bindings (_user/<slug>.json). Mindmeld owns the project's truth; Settings
# owns "me on this machine". Two-file split, zero business logic here:
# every mutating action goes through settings_app. See
# docs/superpowers/specs/2026-07-18-settings-tool-design.md.
__author__ = "Adrian Melian"

import importlib
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from maya_tools.framework import config_validation
from maya_tools.framework import project_setup_app
from maya_tools.framework import settings_app
from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget

importlib.reload(settings_app)

_BINDING_FIELDS = (
    ("source_art_root", "Source Art Root", "Select Source Art Root"),
    ("content_root", "Content Root", "Select Content Root"),
    ("pose_library_root_override", "Pose Library Root (override)",
     "Select Pose Library Root"),
    ("anim_library_root_override", "Anim Library Root (override)",
     "Select Anim Library Root"),
    ("blueprints_dir_override", "Blueprints Dir (override)",
     "Select Blueprints Directory"),
)
_SOURCE_LABELS = {"env": "ENV VAR", "pointer": "POINTER",
                  "local": "LOCAL DEFAULT"}
_INTEGRATION_ROWS = (
    ("load_menu", "Load FabricatorStudio menu"),
    ("load_shelf", "Load FabricatorStudio shelves"),
    ("load_hotkeys", "Load FabricatorStudio hotkeys"),
    ("anim_marking_menu", "Animation marking menu (Ctrl+Alt+RMB)"),
)


class SettingsUI(QtWidgets.QDialog):
    WINDOW_NAME = "FSSettings"
    WINDOW_TITLE = "Settings"
    SLUG_ROLE = QtCore.Qt.ItemDataRole.UserRole

    def __init__(self, parent=None):
        # parent stays None unless explicitly passed (show_window() passes
        # get_maya_window()) so the offscreen suite can build this headless.
        super().__init__(parent)
        mindmeld_style.apply(self)
        self.setObjectName(self.WINDOW_NAME)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setProperty("saveWindowPref", True)
        self.setMinimumWidth(700)
        self.setMinimumHeight(540)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowType.Tool)

        self._selected_slug = None
        self._loaded_bindings = {}
        self._rows = {}
        self._root_status = {}
        self._dirty = False
        # Guard: populate/load set many widgets whose change signals would
        # each mark dirty + revalidate a half-filled form (the Mindmeld
        # stale-revalidate class); suppress during programmatic writes.
        self._populating = False

        self.create_layout()
        self.connect_signals()
        self.populate()

    # ─────────────────────────────────────────
    # Layout
    # ─────────────────────────────────────────

    def create_layout(self):
        main = QtWidgets.QVBoxLayout(self)
        main.setSpacing(10)
        main.setContentsMargins(14, 14, 14, 12)

        brand_row = QtWidgets.QHBoxLayout()
        brand_row.setSpacing(10)
        from maya_tools.framework.toolbar.widgets import icon_button
        banner = QtGui.QPixmap(
            str(icon_button._ICON_DIR / "fs_settings_banner.png"))
        if not banner.isNull():
            label = QtWidgets.QLabel()
            label.setPixmap(banner.scaledToHeight(
                40, QtCore.Qt.TransformationMode.SmoothTransformation))
            brand_row.addWidget(label)
        else:
            brand_row.addWidget(mindmeld_style.brand_label(self.WINDOW_TITLE))
        brand_row.addWidget(mindmeld_style.caps_label("machine · bind + point"),
                            0, QtCore.Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        main.addLayout(brand_row)
        main.addWidget(mindmeld_style.horizontal_rule())

        main.addWidget(self._build_pointer_panel())
        main.addWidget(self._build_bindings_panel(), 1)
        main.addWidget(self._build_integration_panel())

        self.validation_section = CollapsibleSection("VALIDATION")
        self.validation_logger = LoggerWidget()
        vbody = QtWidgets.QVBoxLayout()
        vbody.setContentsMargins(0, 0, 0, 0)
        vbody.addWidget(self.validation_logger)
        self.validation_section.set_body_layout(vbody)
        self.validation_section.set_expanded(False)
        main.addWidget(self.validation_section)

        self.log_section = CollapsibleSection("LOG")
        self.logger = LoggerWidget()
        lbody = QtWidgets.QVBoxLayout()
        lbody.setContentsMargins(0, 0, 0, 0)
        lbody.addWidget(self.logger)
        self.log_section.set_body_layout(lbody)
        self.log_section.set_expanded(True)
        main.addWidget(self.log_section)

    def _build_pointer_panel(self):
        section = CollapsibleSection("SHARED CONFIGS ROOT")
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
        section.set_body_layout(layout)
        section.set_expanded(True)
        return section

    def _build_bindings_panel(self):
        section = CollapsibleSection("PROJECT BINDINGS")
        layout = QtWidgets.QVBoxLayout()
        split = QtWidgets.QHBoxLayout()
        self.project_list = QtWidgets.QListWidget()
        self.project_list.setFixedWidth(220)
        split.addWidget(self.project_list)

        self.editor_stack = QtWidgets.QStackedWidget()
        self.editor_stack.addWidget(self._build_editor())      # index 0
        self.empty_label = mindmeld_style.helper_label("")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.editor_stack.addWidget(self.empty_label)          # index 1
        split.addWidget(self.editor_stack, 1)
        layout.addLayout(split, 1)
        section.set_body_layout(layout)
        section.set_expanded(True)
        return section

    def _build_editor(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self.derived_label = mindmeld_style.helper_label(
            "Derived from the v1 config. Bind to persist for this machine.")
        self.derived_label.setVisible(False)
        layout.addWidget(self.derived_label)

        form = QtWidgets.QFormLayout()
        self.binding_edits = {}
        self.binding_browse = {}
        for key, label, _title in _BINDING_FIELDS:
            edit = QtWidgets.QLineEdit()
            mindmeld_style.tag(edit, "panel")
            btn = mindmeld_style.button("Browse...", kind="default")
            cell = QtWidgets.QHBoxLayout()
            cell.addWidget(edit, 1)
            cell.addWidget(btn)
            form.addRow(mindmeld_style.field_label(label), cell)
            self.binding_edits[key] = edit
            self.binding_browse[key] = btn
        layout.addLayout(form)
        layout.addWidget(mindmeld_style.helper_label(
            "Overrides are optional. Blank = under Source Art Root per the "
            "project's subpaths."))
        layout.addStretch()

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        self.revert_btn = mindmeld_style.button("Revert", kind="ghost")
        self.bind_btn = mindmeld_style.button("Bind", kind="primary")
        btn_row.addWidget(self.revert_btn)
        btn_row.addWidget(self.bind_btn)
        layout.addLayout(btn_row)
        return page

    def _build_integration_panel(self):
        section = CollapsibleSection("MAYA INTEGRATION")
        layout = QtWidgets.QVBoxLayout()
        self.integration_checks = {}
        for key, label in _INTEGRATION_ROWS:
            cb = QtWidgets.QCheckBox(label)
            self.integration_checks[key] = cb
            layout.addWidget(cb)
        layout.addWidget(mindmeld_style.helper_label(
            "Applies at Maya startup. Turning one on builds it now; "
            "turning one off takes effect next Maya start."))
        section.set_body_layout(layout)
        section.set_expanded(True)
        return section

    # ─────────────────────────────────────────
    # Signals
    # ─────────────────────────────────────────

    def connect_signals(self):
        self.configs_browse_btn.clicked.connect(self._on_configs_browse)
        self.configs_local_btn.clicked.connect(self._on_configs_local)
        self.project_list.currentItemChanged.connect(self._on_selection_changed)
        for edit in self.binding_edits.values():
            edit.textChanged.connect(self._on_field_changed)
        for key, btn in self.binding_browse.items():
            btn.clicked.connect(lambda _=False, k=key: self._on_browse_field(k))
        self.bind_btn.clicked.connect(self._on_bind)
        self.revert_btn.clicked.connect(self._on_revert)
        for key, cb in self.integration_checks.items():
            cb.toggled.connect(
                lambda checked, k=key: self._on_integration_toggled(k, checked))

    # ─────────────────────────────────────────
    # Populate / refresh
    # ─────────────────────────────────────────

    def populate(self):
        self._populating = True
        try:
            self._refresh_pointer_panel()
            self._refresh_project_list()
            self._refresh_integration()
        finally:
            self._populating = False
        self._load_selected()

    def _refresh_integration(self):
        prefs = settings_app.integration_prefs()
        for key, cb in self.integration_checks.items():
            cb.setChecked(prefs[key])

    def _refresh_pointer_panel(self):
        status = settings_app.configs_root_status()
        self._root_status = status
        self.configs_root_edit.setText(status["effective_root"])
        env = status["source"] == "env"
        self.configs_browse_btn.setEnabled(not env)
        self.configs_local_btn.setEnabled(status["source"] == "pointer")
        text = f"Source: {_SOURCE_LABELS[status['source']]}"
        if env:
            text += ("   Set by FABRICATOR_PROJECT_CONFIGS. The pointer is "
                     "ignored while it is set.")
        self.configs_status_label.setText(text)

    def _refresh_project_list(self):
        rows = settings_app.list_projects_with_status()
        self._rows = {r["slug"]: r for r in rows}
        previous = self._selected_slug
        self.project_list.blockSignals(True)
        self.project_list.clear()
        for r in rows:
            item = QtWidgets.QListWidgetItem()
            item.setData(self.SLUG_ROLE, r["slug"])
            self.project_list.addItem(item)
            widget = self._make_project_row(r)
            self.project_list.setItemWidget(item, widget)
            # Measure AFTER insertion (styles/QSS apply on reparent; a
            # pre-insert sizeHint smooshes every row) and keep a floor so
            # rows stay readable regardless of style metrics.
            hint = widget.sizeHint()
            hint.setHeight(max(hint.height(), 32))
            item.setSizeHint(hint)
        index = 0
        for i in range(self.project_list.count()):
            if self.project_list.item(i).data(self.SLUG_ROLE) == previous:
                index = i
                break
        if rows:
            self.project_list.setCurrentRow(index)
        self.project_list.blockSignals(False)
        if rows:
            self.editor_stack.setCurrentIndex(0)
        else:
            self._selected_slug = None
            root = self._root_status.get("effective_root", "")
            self.empty_label.setText(
                f"No projects found at {root}. Point Shared Configs Root at "
                f"your studio's configs folder, or create a project in "
                f"Mindmeld.")
            self.editor_stack.setCurrentIndex(1)
            self.configs_status_label.setText(
                self.configs_status_label.text()
                + "   0 projects found. Is this the right folder?")

    def _make_project_row(self, row):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(QtWidgets.QLabel(row["name"]))
        layout.addStretch()
        if row["bound"]:
            layout.addWidget(mindmeld_style.pill("BOUND", "ok"))
        else:
            layout.addWidget(mindmeld_style.pill("UNBOUND", "warn"))
        return widget

    # ─────────────────────────────────────────
    # Selection / editor
    # ─────────────────────────────────────────

    def _on_selection_changed(self, *_args):
        if self._populating:
            return
        if self._dirty and self._selected_slug:
            name = self._rows.get(self._selected_slug, {}).get(
                "name", self._selected_slug)
            self.logger.info(f"Unsaved binding edits to {name} discarded.")
        self._load_selected()

    def _load_selected(self):
        item = self.project_list.currentItem()
        slug = item.data(self.SLUG_ROLE) if item else None
        self._selected_slug = slug
        if slug is None:
            return
        bindings = settings_app.get_bindings(slug)
        self._loaded_bindings = dict(bindings)
        self._populating = True
        try:
            for key, _label, _title in _BINDING_FIELDS:
                value = (bindings.get(key) or "").replace("\\", "/")
                self.binding_edits[key].setText(value)
            self.derived_label.setVisible(
                bool(self._rows.get(slug, {}).get("derived")))
        finally:
            self._populating = False
        self._dirty = False
        self._revalidate()

    def _collect_bindings(self):
        return {key: self.binding_edits[key].text().strip().replace("\\", "/")
                for key, _label, _title in _BINDING_FIELDS}

    def _on_field_changed(self, *_args):
        if self._populating:
            return
        self._dirty = True
        self._revalidate()

    def _revalidate(self):
        if self._selected_slug is None:
            return
        bindings = self._collect_bindings()
        if not any(bindings.values()):
            # Untouched unbound project: skip noisy required-field errors.
            self.validation_logger.clear()
            self.bind_btn.setEnabled(False)
            return
        issues = config_validation.validate_bindings(
            bindings,
            existing_bindings=project_setup_app._existing_bindings(
                exclude_slug=self._selected_slug))
        self._show_issues(issues)

    def _show_issues(self, issues):
        self.validation_logger.clear()
        for issue in issues:
            if issue.severity == "error":
                self.validation_logger.error(f"{issue.field}: {issue.message}")
            else:
                self.validation_logger.warning(
                    f"{issue.field}: {issue.message}")
        self.bind_btn.setEnabled(
            not any(i.severity == "error" for i in issues))

    def _on_browse_field(self, key):
        titles = {k: title for k, _label, title in _BINDING_FIELDS}
        edit = self.binding_edits[key]
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, titles[key], edit.text() or str(Path.home()))
        if chosen:
            edit.setText(chosen.replace("\\", "/"))

    def _on_bind(self):
        if self._selected_slug is None:
            return
        try:
            path = settings_app.bind_project(self._selected_slug,
                                             self._collect_bindings())
        except settings_app.ValidationError as exc:
            self._show_issues(exc.issues)
            return
        except Exception as exc:
            self._handle_error(exc, "Bind failed")
            return
        self._dirty = False
        self.logger.success(
            f"Bound {self._selected_slug} on this machine ({path}).")
        self.populate()

    def _on_revert(self):
        self._load_selected()
        self.logger.info("Reverted to the saved bindings.")

    # ─────────────────────────────────────────
    # Maya integration toggles
    # ─────────────────────────────────────────

    def _on_integration_toggled(self, key, checked):
        if self._populating:
            return
        try:
            settings_app.set_integration_pref(key, checked)
        except Exception as exc:
            self._handle_error(exc, "Could not save the setting")
            return
        label = dict(_INTEGRATION_ROWS)[key]
        if checked:
            if self._apply_integration_live(key):
                self.logger.success(f"{label}: on (built now).")
            else:
                self.logger.info(f"{label}: on. Applies at next Maya start.")
        else:
            self.logger.info(f"{label}: off. Takes effect next Maya start.")

    def _apply_integration_live(self, key):
        """Best-effort immediate build on enable; False = next-start.
        Lazy imports on purpose: these modules need a live Maya session
        (offscreen/headless the attempt fails and the toggle simply waits
        for the next startup)."""
        try:
            if key == "load_menu":
                from maya_tools.framework.menu.fs_menu import build_menu
                build_menu()
            elif key == "load_shelf":
                from maya_tools.framework.shelves.fs_shelf import build_shelves
                build_shelves()
            elif key == "load_hotkeys":
                from maya_tools.framework.hotkeys.fs_hotkeys import build_hotkeys
                build_hotkeys()
            elif key == "anim_marking_menu":
                from maya_tools.rigging.fabricator.ui import animation_menu
                animation_menu.register_menu()
            return True
        except Exception:
            import traceback
            traceback.print_exc()
            return False

    # ─────────────────────────────────────────
    # Pointer actions
    # ─────────────────────────────────────────

    def _on_configs_browse(self):
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Shared Configs Root",
            self._root_status.get("pointer_value")
            or self.configs_root_edit.text() or str(Path.home()))
        if not chosen:
            return
        try:
            settings_app.set_configs_root(chosen)
        except settings_app.ValidationError as exc:
            message = "; ".join(issue.message for issue in exc.issues)
            self.configs_status_label.setText(f"Not saved: {message}")
            self.logger.error(message)
            return
        except Exception as exc:
            self._handle_error(exc, "Could not save the configs-root pointer")
            return
        self.logger.success(f"Shared configs root set to {chosen}.")
        self.populate()

    def _on_configs_local(self):
        try:
            settings_app.clear_configs_root()
        except Exception as exc:
            self._handle_error(exc, "Could not clear the configs-root pointer")
            return
        self.logger.success("Configs root reset to the local default.")
        self.populate()

    # ─────────────────────────────────────────
    # Errors / show
    # ─────────────────────────────────────────

    def _handle_error(self, exc, brief=""):
        import traceback
        traceback.print_exc()
        self.logger.error(f"{brief}: {exc}" if brief else str(exc))

    @classmethod
    def show_window(cls):
        global _win
        # Closing only HIDES the dialog (Maya parent keeps the C++ widget
        # alive): only a VISIBLE match counts as open; hidden leftovers are
        # swept and rebuilt (the Mindmeld open-once fix, 2026-07-18).
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
