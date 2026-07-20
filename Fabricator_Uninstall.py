# Fabricator_Uninstall.py
"""Fabricator toolset — uninstaller.

Two ways to run this:
  1. Drag this file into the Maya viewport (same drop hook as the
     installer) — shows a confirm dialog, click Uninstall.
  2. Run directly from the Script Editor / mayapy: import and call
     uninstall_fabricator() for a scripted/headless removal.

What Uninstall does:
  0. Tears down the live FabricatorStudio toolbar (if running inside Maya)
     and deletes its prefs file (fabricator_toolbar_prefs.json at the
     un-versioned maya root).
  1. Deletes the installed tree at the WIRED root read from userSetup.py:
     a nested <install_dir>/fabricator_studio/ folder is removed whole
     (maya_tools + icons are both ours there); a legacy flat install
     removes only maya_tools/. A 'linked' (shared/network) install
     deletes nothing — only this machine's wiring is stripped.
  2. Strips EXACTLY the FABRICATOR_START..FABRICATOR_END sentinel block
     from userSetup.py — every other line in that file (the user's own
     code, KS_START/KS_END if this IS the KS dev machine, ANIMO_START/
     ANIMO_END, anything else) is left untouched byte-for-byte.
  3. Removes the FSToolsMenu menu (and any pre-rebrand KSToolsMenu
     leftover) and the fsAnim / fsModel shelves (and any pre-rebrand
     ksAnim / ksModel leftovers) from the current Maya session (if
     running inside Maya).
  4. Never touches the user's project configs — they live OUTSIDE the
     install tree at <maya_root>/fabricator_project_configs/ (see
     maya_tools/framework/project_config_paths.py), so an uninstall can
     not destroy user-authored configs.

Zero network calls.
"""
from __future__ import division
from __future__ import print_function
from __future__ import absolute_import

__author__ = "Adrian Melian"

import os
import re
import shutil
import platform

THIS_FILE_PATH = os.path.abspath(__file__)
THIS_DIR = os.path.dirname(THIS_FILE_PATH)

_SENTINEL_START = '# FABRICATOR_START'
_SENTINEL_END = '# FABRICATOR_END'
_MENU_NAME = 'FSToolsMenu'
# Pre-rebrand menu name (2026-07-11, Tools Engineer S3) — a session
# that never rebuilt its menu after upgrading can still carry the old
# name; clean it up too. Remove after v1.
_OLD_MENU_NAME = 'KSToolsMenu'
# Pre-rebrand shelf names (2026-07-11, Tools Engineer S4) — a session
# that never rebuilt its shelves after upgrading can still carry the old
# tab names; clean those up too. Remove after v1.
_SHELF_NAMES = ('fsAnim', 'fsModel', 'ksAnim', 'ksModel')

try:
    from PySide6 import QtWidgets, QtCore
    from shiboken6 import wrapInstance
except ImportError:
    from PySide2 import QtWidgets, QtCore
    from shiboken2 import wrapInstance

import maya.cmds as cmds
import maya.OpenMayaUI as omui


# ─────────────────────────────────────────────
# Path helpers — duplicated from Fabricator_Install.py deliberately.
# This file must be runnable standalone (e.g. after the installer's own
# copy has already been deleted from the payload), so it can't import
# from Fabricator_Install.py.
# ─────────────────────────────────────────────

def get_maya_scripts_dir() -> str:
    if platform.system() == "Windows":
        user_profile = os.environ.get('USERPROFILE', '')
        if not user_profile:
            home_drive = os.environ.get('HOMEDRIVE', 'C:')
            home_path = os.environ.get('HOMEPATH', '\\Users\\Default')
            user_profile = home_drive + home_path
        return os.path.join(user_profile, 'Documents', 'maya', 'scripts')
    elif platform.system() == "Darwin":
        home = os.environ.get('HOME', os.path.expanduser("~"))
        candidate = os.path.join(home, "Library", "Preferences", "Autodesk", "maya", "scripts")
        if not os.path.exists(os.path.dirname(os.path.dirname(candidate))):
            candidate = os.path.join(home, "Documents", "maya", "scripts")
        return candidate
    else:
        home = os.environ.get('HOME', os.path.expanduser("~"))
        return os.path.join(home, "maya", "scripts")


def get_maya_root() -> str:
    """The user's UN-versioned maya folder, asked of Maya itself (see
    Fabricator_Install.get_maya_root — duplicated because this file must
    run standalone)."""
    base = os.environ.get('MAYA_APP_DIR')
    if base:
        return os.path.normpath(base)
    try:
        app_dir = cmds.internalVar(userAppDir=True)
        if app_dir:
            return os.path.normpath(os.path.dirname(app_dir.rstrip('/\\')))
    except Exception:
        pass
    return os.path.normpath(os.path.dirname(get_maya_scripts_dir()))


def get_user_setup_path() -> str:
    return os.path.join(get_maya_root(), 'scripts', 'userSetup.py')


_REPO_ROOT_RE = re.compile(r"_FAB_REPO_ROOT\s*=\s*r?['\"](.+?)['\"]")
_MODE_RE = re.compile(r'#\s*FABRICATOR_MODE:\s*(\w+)')


def parse_wired_install(user_setup_text: str) -> tuple:
    if _SENTINEL_START not in user_setup_text:
        return '', ''
    m = _REPO_ROOT_RE.search(user_setup_text)
    if not m:
        return '', ''
    mode_m = _MODE_RE.search(user_setup_text)
    return m.group(1), (mode_m.group(1) if mode_m else 'copy')


def get_maya_main_window():
    main_window_ptr = omui.MQtUtil.mainWindow()
    if main_window_ptr is None:
        return None
    return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)


# ─────────────────────────────────────────────
# Core removal
# ─────────────────────────────────────────────

def strip_sentinel_block(text: str) -> str:
    pattern = re.compile(
        re.escape(_SENTINEL_START) + r'.*?' + re.escape(_SENTINEL_END) + r'\n?',
        re.DOTALL,
    )
    return pattern.sub('', text)


def remove_user_setup_bootstrap(user_setup_path: str) -> bool:
    """Strip the FABRICATOR_START..END block from userSetup.py.
    Returns True if a block was found and removed, False if the file
    didn't exist or had no such block (both are fine — not an error)."""
    if not os.path.exists(user_setup_path):
        return False
    with open(user_setup_path, 'r', encoding='utf-8') as fh:
        original = fh.read()
    if _SENTINEL_START not in original:
        return False
    stripped = strip_sentinel_block(original)
    with open(user_setup_path, 'w', encoding='utf-8') as fh:
        fh.write(stripped)
    return True


def _teardown_toolbar():
    """Remove the live toolbar + its prefs before deleting the tree.
    Guarded: works even when the package is already broken/missing."""
    try:
        from maya_tools.framework.toolbar import toolbar_app
        toolbar_app.teardown()
    except Exception:
        try:
            if cmds.workspaceControl("fabricatorToolbar", exists=True):
                cmds.deleteUI("fabricatorToolbar", control=True)
        except Exception:
            pass
    # prefs live at the UN-versioned maya root (one level ABOVE
    # scripts_dir) - duplicate prefs_path() logic incl. the env override.
    base = os.environ.get("MAYA_APP_DIR") or os.path.join(
        os.path.expanduser("~"), "Documents", "maya")
    prefs = os.path.join(base, "fabricator_toolbar_prefs.json")
    if os.path.exists(prefs):
        try:
            os.remove(prefs)
        except OSError:
            pass


def remove_installed_tree(scripts_dir: str) -> bool:
    """Delete scripts_dir/maya_tools/. Returns True if it existed and was
    removed, False if there was nothing to remove."""
    target = os.path.join(scripts_dir, 'maya_tools')
    if not os.path.isdir(target):
        return False
    shutil.rmtree(target)
    return True


def remove_live_menu_and_shelves() -> list:
    """Best-effort removal of the live menu + shelves from the CURRENT
    Maya session. No-ops safely if this is being run headless (mayapy) —
    cmds.menu/cmds.shelfLayout raise RuntimeError outside a UI session,
    which is caught per-item so one failure doesn't block the rest."""
    removed = []
    try:
        if cmds.menu(_MENU_NAME, exists=True):
            cmds.deleteUI(_MENU_NAME, menu=True)
            removed.append(f'menu:{_MENU_NAME}')
    except Exception:
        pass
    try:
        if cmds.menu(_OLD_MENU_NAME, exists=True):
            cmds.deleteUI(_OLD_MENU_NAME, menu=True)
            removed.append(f'menu:{_OLD_MENU_NAME}')
    except Exception:
        pass

    for shelf_name in _SHELF_NAMES:
        try:
            if cmds.shelfLayout(shelf_name, exists=True):
                cmds.deleteUI(shelf_name, layout=True)
                removed.append(f'shelf:{shelf_name}')
        except Exception:
            pass

    return removed


def uninstall_fabricator(user_setup_path: str = None) -> dict:
    """Headless-callable entrypoint. Reads the wired install (root +
    mode) from userSetup.py. 'copy' installs delete the wired tree;
    'linked' (shared/network) installs only strip this user's bootstrap
    — the share is never ours to delete. Returns a report dict:
        {'tree_removed', 'bootstrap_removed', 'ui_removed',
         'repo_root', 'mode'}
    Safe to call multiple times — every step already no-ops cleanly if
    there's nothing to remove.
    """
    if user_setup_path is None:
        user_setup_path = get_user_setup_path()

    text = ''
    if os.path.exists(user_setup_path):
        with open(user_setup_path, 'r', encoding='utf-8') as fh:
            text = fh.read()
    repo_root, mode = parse_wired_install(text)

    _teardown_toolbar()

    tree_removed = False
    if mode != 'linked':
        target_root = repo_root or os.path.join(get_maya_root(), 'scripts')
        if os.path.basename(os.path.normpath(target_root)) == 'fabricator_studio':
            # Nested-layout install: the whole brand folder is ours
            # (maya_tools + icons and nothing else) — remove it entirely.
            if os.path.isdir(target_root):
                shutil.rmtree(target_root)
                tree_removed = True
        else:
            # Legacy flat layout (payload at scripts/ root): remove only
            # maya_tools/; the flat Icons/ dir was shared with whatever
            # else the user kept there, so it stays (no ownership manifest).
            tree_removed = remove_installed_tree(target_root)
        if not tree_removed:
            # Pre-v1 installs used a USERPROFILE guess that ignores
            # redirected Documents; sweep that location too.
            legacy = get_maya_scripts_dir()
            if os.path.normcase(legacy) != os.path.normcase(target_root):
                tree_removed = remove_installed_tree(legacy)

    bootstrap_removed = remove_user_setup_bootstrap(user_setup_path)
    # Same legacy sweep for a stranded userSetup at the old guessed path.
    legacy_setup = os.path.join(get_maya_scripts_dir(), 'userSetup.py')
    if os.path.normcase(legacy_setup) != os.path.normcase(user_setup_path):
        remove_user_setup_bootstrap(legacy_setup)

    ui_removed = remove_live_menu_and_shelves()

    return {
        'tree_removed': tree_removed,
        'bootstrap_removed': bootstrap_removed,
        'ui_removed': ui_removed,
        'repo_root': repo_root,
        'mode': mode or 'none',
    }


# ─────────────────────────────────────────────
# Confirm dialog
# ─────────────────────────────────────────────

_CARBON = "#0B0E10"
_IRON = "#1C2126"
_IRON_2 = "#262C33"
_IRON_3 = "#353D45"
_BONE = "#E8E0D0"
_BONE_DIM = "#8A8378"
_PLASMA = "#7CFFB2"
_PLASMA_DIM = "#4FB888"
_EMBER = "#FF7A3D"


class FabricatorUninstallerUI(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super(FabricatorUninstallerUI, self).__init__(parent or get_maya_main_window())
        self.setObjectName("FabricatorUninstallerWindow")
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(420)
        self.setWindowTitle('Fabricator Uninstaller')
        self._build_ui()
        self._apply_theme()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        brand = QtWidgets.QLabel("FABRICATOR — UNINSTALL")
        brand.setStyleSheet(
            f"font-family:'Consolas','Courier New',monospace; font-size:16pt; "
            f"font-weight:bold; color:{_EMBER};"
        )
        root.addWidget(brand)

        rule = QtWidgets.QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background-color:{_IRON_3};")
        root.addWidget(rule)

        body = QtWidgets.QLabel(
            "Removes the installed Fabricator tree, strips the userSetup.py "
            "bootstrap block, and removes the FabricatorStudio menu, shelves, and "
            "FabricatorStudio toolbar (plus its prefs file) from this Maya "
            "session. Your own scenes, Maya prefs, and any other content "
            "in userSetup.py are left untouched."
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"color:{_BONE}; font-size:10pt;")
        root.addWidget(body)

        wired_root, wired_mode = '', ''
        try:
            with open(get_user_setup_path(), 'r', encoding='utf-8') as fh:
                wired_root, wired_mode = parse_wired_install(fh.read())
        except OSError:
            pass
        if wired_mode == 'linked':
            target_text = (f"Shared install at: {wired_root}\n"
                           f"Only this machine's wiring is removed; the "
                           f"shared toolset files are left in place.")
        elif wired_root:
            target_text = f"Installed at: {wired_root}"
        else:
            target_text = "No wired install found in userSetup.py."
        target_label = QtWidgets.QLabel(target_text)
        target_label.setStyleSheet(f"color:{_BONE_DIM}; font-size:8pt;")
        target_label.setWordWrap(True)
        root.addWidget(target_label)

        self._status_label = QtWidgets.QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setVisible(False)
        root.addWidget(self._status_label)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch()
        cancel_button = QtWidgets.QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)
        button_row.addWidget(cancel_button)

        self._uninstall_button = QtWidgets.QPushButton("Uninstall")
        self._uninstall_button.setMinimumWidth(120)
        self._uninstall_button.clicked.connect(self.run_uninstall)
        button_row.addWidget(self._uninstall_button)
        root.addLayout(button_row)

    def _apply_theme(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{ background-color: {_CARBON}; }}
            QLabel {{ color: {_BONE}; }}
            QPushButton {{
                background-color: {_IRON_2};
                color: {_BONE};
                border: 1px solid {_IRON_3};
                border-radius: 4px;
                padding: 6px 14px;
                font-size: 10pt;
            }}
            QPushButton:hover {{ background-color: {_IRON_3}; border-color: {_EMBER}; }}
            QPushButton:pressed {{ background-color: {_IRON}; }}
        """)

    def _set_status(self, text: str, ok: bool = True) -> None:
        self._status_label.setStyleSheet(f"color:{_PLASMA if ok else _EMBER}; font-size:9pt;")
        self._status_label.setText(text)
        self._status_label.setVisible(bool(text))
        QtWidgets.QApplication.processEvents()

    def run_uninstall(self) -> None:
        self._uninstall_button.setEnabled(False)
        self._uninstall_button.setText("Uninstalling...")
        QtWidgets.QApplication.processEvents()
        try:
            report = uninstall_fabricator()
            parts = []
            parts.append('toolset tree removed' if report['tree_removed'] else 'no installed tree found')
            parts.append('userSetup.py bootstrap stripped' if report['bootstrap_removed'] else 'no bootstrap block found')
            if report['mode'] == 'linked':
                parts.append('shared toolset left in place')
            if report['ui_removed']:
                parts.append(f"removed: {', '.join(report['ui_removed'])}")
            self._set_status('Done — ' + '; '.join(parts) + '.', ok=True)
            cmds.inViewMessage(
                amg=f'<span style="color:{_PLASMA};">Fabricator</span> uninstalled.',
                pos='midCenter', fade=True,
            )
            self._uninstall_button.setText("Done")
            QtCore.QTimer.singleShot(1200, self.close)
        except Exception as exc:
            self._set_status(f'Uninstall failed: {exc}', ok=False)
            self._uninstall_button.setEnabled(True)
            self._uninstall_button.setText("Uninstall")


_fabricator_uninstaller_instance = None


def onMayaDroppedPythonFile(*args, **kwargs):
    create_uninstaller_ui()


def create_uninstaller_ui():
    global _fabricator_uninstaller_instance
    if _fabricator_uninstaller_instance is not None:
        try:
            _fabricator_uninstaller_instance.close()
            _fabricator_uninstaller_instance.deleteLater()
        except Exception:
            pass
        _fabricator_uninstaller_instance = None

    main_window = get_maya_main_window()
    if main_window is not None:
        for child in main_window.children():
            if child.objectName() == "FabricatorUninstallerWindow":
                try:
                    child.close()
                    child.deleteLater()
                except Exception:
                    pass

    _fabricator_uninstaller_instance = FabricatorUninstallerUI()
    _fabricator_uninstaller_instance.show()
    return _fabricator_uninstaller_instance


if __name__ == '__main__':
    create_uninstaller_ui()
