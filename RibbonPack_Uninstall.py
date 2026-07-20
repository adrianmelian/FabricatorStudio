# RibbonPack_Uninstall.py
"""Advanced Ribbon Pack — uninstaller.

Two ways to run this:
  1. Drag this file into the Maya viewport (same drop hook as the
     installer) — shows a confirm dialog, click Uninstall.
  2. Run directly from the Script Editor / mayapy: import and call
     uninstall_ribbon_pack() for a scripted/headless removal.

What Uninstall does:
  1. Locates the installed Fabricator core the same way
     RibbonPack_Install.py does (parses userSetup.py's
     FABRICATOR_START..END sentinel block for `_FAB_REPO_ROOT`; falls
     back to a browse dialog).
  2. Deletes EXACTLY the files this bundled `ribbon_pack_manifest.json`
     lists — the five paid modules, the ribbon leg limb fragment, and
     the two ribbon doc pages — and nothing else. No directory is ever
     removed, only the individual files the manifest names.
  3. Refreshes component discovery (best-effort, via the core's own
     `modules.reload_all()`) so the ribbon components disappear from the
     live session immediately where possible; otherwise a restart clears
     them.

Scene safety: already-built ribbon rigs KEEP ANIMATING after this
uninstall. The deformation those rigs use (skinCluster, uvPin, blendShape,
etc.) is baked Maya scene content, not Python — removing the pack's
build-time module files cannot touch it. Only re-BUILDING a ribbon
component (Build Modules on an unbuilt rig) would fail once the pack is
gone.

Zero network calls.
"""
from __future__ import division
from __future__ import print_function
from __future__ import absolute_import

__author__ = "Adrian Melian"

import json
import os
import platform
import re

THIS_FILE_PATH = os.path.abspath(__file__)
THIS_DIR = os.path.dirname(THIS_FILE_PATH)

_MANIFEST_NAME = 'ribbon_pack_manifest.json'
_VERSION_FILE_REL = os.path.join('maya_tools', 'rigging', 'fabricator', 'version.py')

_SENTINEL_START = '# FABRICATOR_START'
_SENTINEL_END = '# FABRICATOR_END'
_REPO_ROOT_PATTERN = re.compile(r"_FAB_REPO_ROOT\s*=\s*r?['\"](.+?)['\"]")

try:
    from PySide6 import QtWidgets, QtCore
    from shiboken6 import wrapInstance
except ImportError:
    from PySide2 import QtWidgets, QtCore
    from shiboken2 import wrapInstance

import maya.cmds as cmds
import maya.OpenMayaUI as omui


# ─────────────────────────────────────────────
# Path / core-discovery helpers — duplicated from RibbonPack_Install.py
# deliberately. This file must be runnable standalone (e.g. after the
# installer's own copy has already been deleted from the payload), same
# rationale as Fabricator_Uninstall.py's own duplicated helpers.
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


def get_maya_main_window():
    main_window_ptr = omui.MQtUtil.mainWindow()
    if main_window_ptr is None:
        return None
    return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)


def find_core_root_from_user_setup() -> str:
    scripts_dir = get_maya_scripts_dir()
    user_setup_path = os.path.join(scripts_dir, 'userSetup.py')
    if not os.path.isfile(user_setup_path):
        return ''
    with open(user_setup_path, 'r', encoding='utf-8') as fh:
        text = fh.read()
    if _SENTINEL_START not in text or _SENTINEL_END not in text:
        return ''
    start = text.index(_SENTINEL_START)
    end = text.index(_SENTINEL_END, start)
    block = text[start:end]
    match = _REPO_ROOT_PATTERN.search(block)
    if not match:
        return ''
    return match.group(1).replace('/', os.sep)


def is_valid_core_root(path: str) -> bool:
    return bool(path) and os.path.isfile(os.path.join(path, _VERSION_FILE_REL))


def load_pack_manifest(pack_dir: str) -> dict:
    manifest_path = os.path.join(pack_dir, _MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        raise RuntimeError(
            f'{_MANIFEST_NAME} not found next to the uninstaller. '
            f'Expected: {manifest_path}'
        )
    with open(manifest_path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def refresh_component_discovery(core_root: str) -> bool:
    """Mirrors RibbonPack_Install.py's own helper — best-effort registry
    refresh via the core's existing modules.reload_all() hot-reload hook.
    Returns True if it ran, False if it couldn't (caller may fall back to
    a 'restart Maya' note)."""
    import sys
    added = core_root not in sys.path
    if added:
        sys.path.insert(0, core_root)
    try:
        from maya_tools.rigging.fabricator import modules as fab_modules
        fab_modules.reload_all()
        return True
    except Exception:
        return False
    finally:
        if added and core_root in sys.path:
            sys.path.remove(core_root)


# ─────────────────────────────────────────────
# Core removal
# ─────────────────────────────────────────────

def remove_pack_files(core_root: str, manifest: dict) -> list:
    """Delete EXACTLY the manifest's files from core_root, nothing else.
    Missing files are simply skipped (already removed / never installed)
    — not an error. Returns the list of files actually removed."""
    removed = []
    for rel in manifest.get('files', []):
        rel_native = rel.replace('/', os.sep)
        target = os.path.join(core_root, rel_native)
        if os.path.isfile(target):
            os.remove(target)
            removed.append(target)
    return removed


def uninstall_ribbon_pack(pack_dir: str = None, core_root: str = None) -> dict:
    """Headless-callable entrypoint. Returns:
        {'core_root':, 'removed_files': [...], 'refreshed': bool}
    Safe to call multiple times — already-removed files are just skipped.
    Raises RuntimeError only if the manifest can't be read or no core can
    be located at all (never partially guesses a target)."""
    pack_dir = pack_dir or THIS_DIR
    manifest = load_pack_manifest(pack_dir)

    if not core_root:
        core_root = find_core_root_from_user_setup()
    if not is_valid_core_root(core_root):
        raise RuntimeError(
            'Could not locate an installed Fabricator core (no valid '
            f'{_VERSION_FILE_REL} found at {core_root!r}).'
        )

    removed_files = remove_pack_files(core_root, manifest)
    refreshed = refresh_component_discovery(core_root)

    return {
        'core_root': core_root,
        'removed_files': removed_files,
        'refreshed': refreshed,
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
_EMBER = "#FF7A3D"


class RibbonPackUninstallerUI(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super(RibbonPackUninstallerUI, self).__init__(parent or get_maya_main_window())
        self.setObjectName("RibbonPackUninstallerWindow")
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(440)
        self.setWindowTitle('Advanced Ribbon Pack Uninstaller')
        self._explicit_core_root = None
        self._build_ui()
        self._apply_theme()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        brand = QtWidgets.QLabel("ADVANCED RIBBON PACK — UNINSTALL")
        brand.setStyleSheet(
            f"font-family:'Consolas','Courier New',monospace; font-size:14pt; "
            f"font-weight:bold; color:{_EMBER};"
        )
        root.addWidget(brand)

        rule = QtWidgets.QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background-color:{_IRON_3};")
        root.addWidget(rule)

        body = QtWidgets.QLabel(
            "Removes exactly the ribbon pack's files (the paid modules, "
            "the ribbon leg fragment, and their doc pages) from your "
            "Fabricator core. Already-built ribbon rigs keep animating — "
            "the deformation is baked Maya nodes, not Python. Your scenes, "
            "Maya prefs, and the rest of Fabricator are left untouched."
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"color:{_BONE}; font-size:10pt;")
        root.addWidget(body)

        self._target_label = QtWidgets.QLabel("Target: (locating Fabricator core...)")
        self._target_label.setStyleSheet(f"color:{_BONE_DIM}; font-size:8pt;")
        self._target_label.setWordWrap(True)
        root.addWidget(self._target_label)
        self._refresh_target_label()

        self._status_label = QtWidgets.QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setVisible(False)
        root.addWidget(self._status_label)

        button_row = QtWidgets.QHBoxLayout()
        self._browse_button = QtWidgets.QPushButton("Browse for core...")
        self._browse_button.clicked.connect(self._browse_for_core)
        button_row.addWidget(self._browse_button)
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

    def _current_core_root(self) -> str:
        if self._explicit_core_root:
            return self._explicit_core_root
        return find_core_root_from_user_setup()

    def _refresh_target_label(self) -> None:
        core_root = self._current_core_root()
        if is_valid_core_root(core_root):
            self._target_label.setText(f"Target: {core_root}")
        else:
            self._target_label.setText(
                "Target: no Fabricator core found automatically — "
                "use Browse for core..."
            )

    def _browse_for_core(self) -> None:
        start_dir = self._current_core_root() or get_maya_scripts_dir()
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Locate the installed Fabricator core", start_dir,
        )
        if chosen and is_valid_core_root(chosen):
            self._explicit_core_root = chosen
            self._set_status('')
        elif chosen:
            self._set_status(
                f'{chosen} does not look like a Fabricator core '
                f'(no {_VERSION_FILE_REL} found there).', ok=False,
            )
        self._refresh_target_label()

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
            report = uninstall_ribbon_pack(
                pack_dir=THIS_DIR, core_root=self._current_core_root(),
            )
            n = len(report['removed_files'])
            parts = [f'{n} file(s) removed' if n else 'no pack files found to remove']
            if not report['refreshed']:
                parts.append('restart Maya to fully clear ribbon components from the UI')
            self._set_status('Done — ' + '; '.join(parts) + '.', ok=True)
            cmds.inViewMessage(
                amg=f'<span style="color:{_PLASMA};">Advanced Ribbon Pack</span> uninstalled.',
                pos='midCenter', fade=True,
            )
            self._uninstall_button.setText("Done")
            QtCore.QTimer.singleShot(1200, self.close)
        except Exception as exc:
            self._set_status(f'Uninstall failed: {exc}', ok=False)
            self._uninstall_button.setEnabled(True)
            self._uninstall_button.setText("Uninstall")


_ribbon_pack_uninstaller_instance = None


def onMayaDroppedPythonFile(*args, **kwargs):
    create_uninstaller_ui()


def create_uninstaller_ui():
    global _ribbon_pack_uninstaller_instance
    if _ribbon_pack_uninstaller_instance is not None:
        try:
            _ribbon_pack_uninstaller_instance.close()
            _ribbon_pack_uninstaller_instance.deleteLater()
        except Exception:
            pass
        _ribbon_pack_uninstaller_instance = None

    main_window = get_maya_main_window()
    if main_window is not None:
        for child in main_window.children():
            if child.objectName() == "RibbonPackUninstallerWindow":
                try:
                    child.close()
                    child.deleteLater()
                except Exception:
                    pass

    _ribbon_pack_uninstaller_instance = RibbonPackUninstallerUI()
    _ribbon_pack_uninstaller_instance.show()
    return _ribbon_pack_uninstaller_instance


if __name__ == '__main__':
    create_uninstaller_ui()
