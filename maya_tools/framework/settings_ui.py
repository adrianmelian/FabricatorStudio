# settings_ui.py
# Settings - the per-machine face over FabricatorStudio TOOL settings only
# (the Maya integration toggles). Project settings - the project list,
# per-project machine bindings, and the Shared Project Configs pointer -
# live in Mindmeld (project_setup_ui.py). Two-file split, zero business
# logic here: every mutating action goes through settings_app. See
# docs/superpowers/specs/2026-07-20-mindmeld-bindings-unify-design.md.
__author__ = "Adrian Melian"

import importlib

from PySide6 import QtCore, QtGui, QtWidgets

from maya_tools.framework import settings_app
from maya_tools.utils.maya.gui import get_maya_window
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget

importlib.reload(settings_app)

_INTEGRATION_ROWS = (
    ("load_menu", "Load FabricatorStudio menu"),
    ("load_shelf", "Load FabricatorStudio shelves"),
    ("load_hotkeys", "Load FabricatorStudio hotkeys"),
    ("anim_marking_menu", "Animation marking menu (Ctrl+Alt+RMB)"),
)


class SettingsUI(QtWidgets.QDialog):
    WINDOW_NAME = "FSSettings"
    WINDOW_TITLE = "Settings"

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

        main.addWidget(self._build_integration_panel())
        main.addWidget(self._build_updates_panel())

        self.log_section = CollapsibleSection("LOG")
        self.logger = LoggerWidget()
        lbody = QtWidgets.QVBoxLayout()
        lbody.setContentsMargins(0, 0, 0, 0)
        lbody.addWidget(self.logger)
        self.log_section.set_body_layout(lbody)
        self.log_section.set_expanded(True)
        main.addWidget(self.log_section)

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

    def _build_updates_panel(self):
        section = CollapsibleSection("UPDATES")
        layout = QtWidgets.QVBoxLayout()
        self.update_startup_check = QtWidgets.QCheckBox(
            "Check for updates when Maya starts")
        layout.addWidget(self.update_startup_check)
        row = QtWidgets.QHBoxLayout()
        self.update_check_btn = mindmeld_style.button("Check Now")
        row.addWidget(self.update_check_btn)
        self.update_status = mindmeld_style.helper_label("")
        self.update_status.setWordWrap(True)
        row.addWidget(self.update_status, 1)
        layout.addLayout(row)
        section.set_body_layout(layout)
        section.set_expanded(True)
        return section

    # ─────────────────────────────────────────
    # Signals
    # ─────────────────────────────────────────

    def connect_signals(self):
        for key, cb in self.integration_checks.items():
            cb.toggled.connect(
                lambda checked, k=key: self._on_integration_toggled(k, checked))
        self.update_startup_check.toggled.connect(
            self._on_update_startup_toggled)
        self.update_check_btn.clicked.connect(self._on_check_updates)

    # ─────────────────────────────────────────
    # Populate / refresh
    # ─────────────────────────────────────────

    def populate(self):
        self._populating = True
        try:
            self._refresh_integration()
            self._refresh_updates()
        finally:
            self._populating = False

    def _refresh_updates(self):
        from maya_tools.framework.updater import updater_core
        self.update_startup_check.setChecked(
            updater_core.check_on_startup_enabled())
        install = updater_core.detect_install()
        if install["ok"]:
            self.update_status.setText(
                "Installed: Fabricator %s" % install["version"])
        else:
            self.update_status.setText(install["reason"])
            self.update_check_btn.setEnabled(False)

    def _refresh_integration(self):
        prefs = settings_app.integration_prefs()
        for key, cb in self.integration_checks.items():
            cb.setChecked(prefs[key])

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

    # ─────────────────────────────────────────
    # Updates
    # ─────────────────────────────────────────

    def _on_update_startup_toggled(self, checked):
        if self._populating:
            return
        try:
            from maya_tools.framework.updater import updater_core
            updater_core.set_check_on_startup(checked)
        except Exception as exc:
            self._handle_error(exc, "Could not save the setting")
            return
        self.logger.info("Startup update check: %s."
                         % ("on" if checked else "off"))

    def _on_check_updates(self):
        """Synchronous on purpose: the fetch timeout is 3 seconds, bounded,
        and the user just asked for it — a settings window may hold its
        breath that long. The STARTUP path is the one that must never
        block, and it runs threaded (updater_ui)."""
        from maya_tools.framework.updater import updater_core
        self.update_status.setText("Checking...")
        QtWidgets.QApplication.processEvents()
        try:
            offer, status = updater_core.check_for_update()
        except Exception as exc:
            self._handle_error(exc, "Update check failed")
            self.update_status.setText("Update check failed. See the log.")
            return
        self.update_status.setText(status)
        self.logger.info(status)
        if offer is not None:
            from maya_tools.framework.updater import updater_ui
            self._update_dialog = updater_ui.UpdateDialog(
                offer, parent=self).show()

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
