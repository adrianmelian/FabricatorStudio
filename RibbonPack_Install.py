# RibbonPack_Install.py
"""Advanced Ribbon Pack — drag-drop installer.

Drag this file into the Maya viewport onto an existing Fabricator core
install. Maya's native Python-drop hook calls onMayaDroppedPythonFile(),
which shows a Mindmeld-branded confirm dialog. Nothing installs until you
click Install.

What Install does, in order:
  1. Locates the installed Fabricator core: parses the FABRICATOR_START..
     FABRICATOR_END sentinel block Fabricator_Install.py wrote into
     userSetup.py, for its `_FAB_REPO_ROOT` value (the exact same
     discovery source the core installer itself uses — no new mechanism
     invented). Falls back to a directory-browse dialog if no sentinel is
     found, or the path it names no longer has a Fabricator core at it.
  2. VERSION GATE: refuses to touch anything if the core's stamped
     `maya_tools/rigging/fabricator/version.py` FABRICATOR_VERSION is
     older than this pack's own `min_core_version` (read from the bundled
     `ribbon_pack_manifest.json`, stamped fresh at pack-build time by
     build_ribbon_pack.py). Clear Mindmeld-styled message, no partial
     copy on failure.
  3. Copies `RibbonPack_Data/<repo-relative path>` -> `<core_root>/<same
     path>` for every file the bundled manifest lists (the five paid
     modules, one ribbon-flavored limb fragment, two doc pages) —
     idempotent; re-running (a re-download after an update) simply
     overwrites in place.
  4. Refreshes component discovery through the core's own
     `modules.reload_all()` — the SAME hot-reload hook the dev repo
     already exposes for iteration; no new/riskier reload machinery
     invented here. If that import or call fails for any reason
     (Fabricator not currently live in this Maya session, or any other
     environment gap), the dialog says "restart Maya to finish loading"
     instead of guessing further.
  5. Nothing here ever touches menus, shelves, or hotkeys — the ribbon
     components surface through the existing Build Modules component
     picker and Load Limb fragment browser, both of which already read
     live from disk/registry.

Uninstall: drag RibbonPack_Uninstall.py into the viewport, or call its
headless `uninstall_ribbon_pack()` entry point directly. It deletes
EXACTLY the manifest's files and nothing else — already-built ribbon rigs
keep animating after an uninstall (the deformation is baked Maya nodes;
the pack files are build-time scaffolding, not scene content).

Zero network calls anywhere in this file.
"""
from __future__ import division
from __future__ import print_function
from __future__ import absolute_import

__author__ = "Adrian Melian"

import json
import os
import platform
import re
import shutil

THIS_FILE_PATH = os.path.abspath(__file__)
THIS_DIR = os.path.dirname(THIS_FILE_PATH)

_MANIFEST_NAME = 'ribbon_pack_manifest.json'
_PACK_DATA_DIR_NAME = 'RibbonPack_Data'
_VERSION_FILE_REL = os.path.join('maya_tools', 'rigging', 'fabricator', 'version.py')

_SENTINEL_START = '# FABRICATOR_START'
_SENTINEL_END = '# FABRICATOR_END'
_REPO_ROOT_PATTERN = re.compile(r"_FAB_REPO_ROOT\s*=\s*r?['\"](.+?)['\"]")

try:
    from PySide6 import QtWidgets, QtCore
    from shiboken6 import wrapInstance
    PYSIDE_VERSION = 6
except ImportError:
    from PySide2 import QtWidgets, QtCore
    from shiboken2 import wrapInstance
    PYSIDE_VERSION = 2

import maya.cmds as cmds
import maya.OpenMayaUI as omui


# ─────────────────────────────────────────────
# Path / core-discovery helpers
# ─────────────────────────────────────────────

def get_maya_scripts_dir() -> str:
    """Verbatim duplicate of Fabricator_Install.py's own helper — this
    file must be runnable standalone (before any core is located, and
    even if no core exists yet), so it can't import path logic from the
    core it's about to find."""
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
    """Parse userSetup.py's FABRICATOR_START..END block for the
    `_FAB_REPO_ROOT` sentinel Fabricator_Install.py wrote there (repo_root
    == scripts_dir in that installer's own terms — see its
    run_installation()). Returns '' if userSetup.py, the sentinel block,
    or the `_FAB_REPO_ROOT` line isn't found — the caller falls back to a
    browse dialog rather than ever guessing a path."""
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
    """A path is a real Fabricator core install iff it has the version
    stamp file — the one thing every core revision is guaranteed to
    carry."""
    return bool(path) and os.path.isfile(os.path.join(path, _VERSION_FILE_REL))


def read_core_version(core_root: str) -> str:
    """Regex-read FABRICATOR_VERSION out of the target core's version.py
    — never imports it. The target core may be a different revision than
    whatever Python/Maya this installer happens to be dragged into; a
    plain text read carries zero import-path or version-skew risk."""
    version_path = os.path.join(core_root, _VERSION_FILE_REL)
    with open(version_path, 'r', encoding='utf-8') as fh:
        text = fh.read()
    match = re.search(r"FABRICATOR_VERSION\s*=\s*['\"]([\d.]+)['\"]", text)
    return match.group(1) if match else ''


def _version_tuple(version_str: str) -> tuple:
    """Dotted 'major.minor.patch' string -> comparable int tuple. Mirrors
    maya_tools/rigging/fabricator/modules/__init__.py's own
    _version_tuple — duplicated (not imported) since this file must run
    before the core is confirmed to exist at all."""
    parts = []
    for p in (version_str or '').split('.'):
        try:
            parts.append(int(p))
        except (TypeError, ValueError):
            parts.append(0)
    return tuple(parts) or (0,)


def version_lt(a: str, b: str) -> bool:
    ta, tb = _version_tuple(a), _version_tuple(b)
    n = max(len(ta), len(tb))
    ta = ta + (0,) * (n - len(ta))
    tb = tb + (0,) * (n - len(tb))
    return ta < tb


def load_pack_manifest(pack_dir: str) -> dict:
    manifest_path = os.path.join(pack_dir, _MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        raise RuntimeError(
            f'{_MANIFEST_NAME} not found next to the installer. '
            f'Expected: {manifest_path}'
        )
    with open(manifest_path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


# ─────────────────────────────────────────────
# Install
# ─────────────────────────────────────────────

def copy_pack_files(core_root: str, pack_dir: str, manifest: dict) -> list:
    """Copy every manifest file from `<pack_dir>/RibbonPack_Data/<rel>` to
    `<core_root>/<rel>`, creating parent directories as needed.
    Idempotent — overwrite IS the update path (re-download, re-drag).
    Returns the list of installed absolute destination paths. Raises
    RuntimeError (no partial copy reported as success) if any expected
    payload file is missing."""
    data_dir = os.path.join(pack_dir, _PACK_DATA_DIR_NAME)
    installed = []
    for rel in manifest.get('files', []):
        rel_native = rel.replace('/', os.sep)
        src = os.path.join(data_dir, rel_native)
        if not os.path.isfile(src):
            raise RuntimeError(f'Pack payload missing expected file: {src}')
        dest = os.path.join(core_root, rel_native)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
        installed.append(dest)
    return installed


def refresh_component_discovery(core_root: str) -> bool:
    """Best-effort: import the core's own
    `maya_tools.rigging.fabricator.modules` and call its existing
    `reload_all()` (the same hook the dev repo uses for hot-reload during
    iteration) so the newly-copied ribbon components appear without a
    Maya restart. Returns True if the refresh ran, False if it couldn't
    (caller shows a 'restart Maya' message in that case — never invents
    riskier reload machinery, per the packaging SPEC)."""
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


def install_ribbon_pack(pack_dir: str = None, core_root: str = None) -> dict:
    """Headless-callable entry point (the drag-drop UI calls this too).

    Returns a report dict:
        {'core_root':, 'installed_files': [...], 'refreshed': bool,
         'pack_version':, 'min_core_version':, 'core_version':}
    Raises RuntimeError on any gate failure (missing manifest, no valid
    core found, core too old, missing payload file) — never partially
    copies and reports success.
    """
    pack_dir = pack_dir or THIS_DIR
    manifest = load_pack_manifest(pack_dir)

    if not core_root:
        core_root = find_core_root_from_user_setup()
    if not is_valid_core_root(core_root):
        raise RuntimeError(
            'Could not locate an installed Fabricator core (no valid '
            f'{_VERSION_FILE_REL} found at {core_root!r}). Install '
            'Fabricator first, or browse to the correct folder.'
        )

    core_version = read_core_version(core_root)
    min_core_version = manifest.get('min_core_version', '0.0.0')
    if not core_version or version_lt(core_version, min_core_version):
        raise RuntimeError(
            f'Installed Fabricator core is {core_version or "unknown"}; '
            f'this Advanced Ribbon Pack requires {min_core_version} or '
            f'newer. Update Fabricator first, then re-run this installer. '
            f'Nothing was copied.'
        )

    installed_files = copy_pack_files(core_root, pack_dir, manifest)
    refreshed = refresh_component_discovery(core_root)

    return {
        'core_root': core_root,
        'installed_files': installed_files,
        'refreshed': refreshed,
        'pack_version': manifest.get('pack_version', ''),
        'min_core_version': min_core_version,
        'core_version': core_version,
    }


# ─────────────────────────────────────────────
# Mindmeld-branded confirm dialog
# ─────────────────────────────────────────────

# Carbon / Iron / Bone / Plasma / Ember — matches
# maya_tools/utils/qt/mindmeld/mindmeld_style.py TOKENS. Duplicated here
# (not imported) deliberately: this file must run BEFORE the payload is
# on sys.path, same rationale as Fabricator_Install.py's own copy.
_CARBON = "#0B0E10"
_IRON = "#1C2126"
_IRON_2 = "#262C33"
_IRON_3 = "#353D45"
_BONE = "#E8E0D0"
_BONE_DIM = "#8A8378"
_PLASMA = "#7CFFB2"
_PLASMA_DIM = "#4FB888"
_EMBER = "#FF7A3D"


class RibbonPackInstallerUI(QtWidgets.QDialog):

    def __init__(self, installer_dir, parent=None):
        super(RibbonPackInstallerUI, self).__init__(parent or get_maya_main_window())
        self.setObjectName("RibbonPackInstallerWindow")
        self.installer_dir = installer_dir

        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(460)
        self.setWindowTitle('Advanced Ribbon Pack Installer')

        self._build_ui()
        self._apply_theme()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        brand_row = QtWidgets.QHBoxLayout()
        brand = QtWidgets.QLabel("ADVANCED RIBBON PACK")
        brand.setStyleSheet(
            f"font-family:'Consolas','Courier New',monospace; font-size:16pt; "
            f"font-weight:bold; color:{_PLASMA}; letter-spacing:1px;"
        )
        brand_row.addWidget(brand)
        brand_row.addStretch()
        version = QtWidgets.QLabel(_read_pack_version_string(self.installer_dir))
        version.setStyleSheet(
            f"font-size:9pt; color:{_BONE_DIM}; background-color:{_IRON_2}; "
            f"padding:3px 10px; border-radius:8px;"
        )
        brand_row.addWidget(version)
        root.addLayout(brand_row)

        rule = QtWidgets.QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background-color:{_IRON_3};")
        root.addWidget(rule)

        body = QtWidgets.QLabel(
            "Installs the ribbon-deformation modules (Ribbon, RibbonSpine, "
            "RibbonIKArm, RibbonIKLeg), the ribbon leg limb fragment, and "
            "their doc pages into your existing Fabricator install. No "
            "admin rights needed. Nothing is sent over the network."
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
        self._status_label.setStyleSheet(f"color:{_EMBER}; font-size:9pt;")
        self._status_label.setVisible(False)
        root.addWidget(self._status_label)

        button_row = QtWidgets.QHBoxLayout()
        self._browse_button = QtWidgets.QPushButton("Browse for core...")
        self._browse_button.clicked.connect(self._browse_for_core)
        button_row.addWidget(self._browse_button)
        button_row.addStretch()
        self._cancel_button = QtWidgets.QPushButton("Cancel")
        self._cancel_button.clicked.connect(self.close)
        button_row.addWidget(self._cancel_button)

        self._install_button = QtWidgets.QPushButton("Install")
        self._install_button.setMinimumWidth(120)
        self._install_button.clicked.connect(self.run_installation)
        button_row.addWidget(self._install_button)
        root.addLayout(button_row)

        footer = QtWidgets.QLabel("FabricatorStudio — Advanced Ribbon Pack.")
        footer.setAlignment(QtCore.Qt.AlignCenter)
        footer.setStyleSheet(f"color:{_BONE_DIM}; font-size:8pt;")
        root.addWidget(footer)

        self._explicit_core_root = None

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
            QPushButton:hover {{ background-color: {_IRON_3}; border-color: {_PLASMA_DIM}; }}
            QPushButton:pressed {{ background-color: {_IRON}; }}
            QPushButton:disabled {{ color: {_BONE_DIM}; }}
        """)

    # ---------- core discovery ----------

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

    # ---------- install ----------

    def _set_status(self, text: str, ok: bool = True) -> None:
        self._status_label.setStyleSheet(
            f"color:{_PLASMA if ok else _EMBER}; font-size:9pt;"
        )
        self._status_label.setText(text)
        self._status_label.setVisible(bool(text))
        QtWidgets.QApplication.processEvents()

    def run_installation(self) -> None:
        self._install_button.setEnabled(False)
        self._install_button.setText("Installing...")
        QtWidgets.QApplication.processEvents()
        cmds.waitCursor(state=True)

        try:
            core_root = self._current_core_root()
            self._set_status('Installing ribbon modules...')
            report = install_ribbon_pack(
                pack_dir=self.installer_dir, core_root=core_root,
            )

            if report['refreshed']:
                self._set_status(
                    'Installed. Ribbon components are available now — no '
                    'restart needed.', ok=True,
                )
                cmds.inViewMessage(
                    amg=f'<span style="color:{_PLASMA};">Advanced Ribbon Pack</span> '
                        f'installed — components available now.',
                    pos='midCenter', fade=True,
                )
            else:
                self._set_status(
                    'Installed. Restart Maya to finish loading the new '
                    'ribbon components.', ok=True,
                )
                cmds.inViewMessage(
                    amg=f'<span style="color:{_PLASMA};">Advanced Ribbon Pack</span> '
                        f'installed. Restart Maya to finish loading.',
                    pos='midCenter', fade=True,
                )
            self._install_button.setText("Done")
            cmds.waitCursor(state=False)
            QtCore.QTimer.singleShot(900, self.close)

        except Exception as exc:
            cmds.waitCursor(state=False)
            self._set_status(f'Installation failed: {exc}', ok=False)
            self._install_button.setEnabled(True)
            self._install_button.setText("Install")


def _read_pack_version_string(installer_dir: str) -> str:
    """Best-effort version label for the dialog header, read from the
    bundled manifest. Falls back to a plain label so the dialog never
    breaks on its absence (mirrors Fabricator_Install.py's
    _read_version_string)."""
    try:
        manifest = load_pack_manifest(installer_dir)
        v = manifest.get('pack_version', '')
        if v:
            return v
    except Exception:
        pass
    return "Advanced Ribbon Pack"


# ─────────────────────────────────────────────
# Maya drop hook
# ─────────────────────────────────────────────

_ribbon_pack_installer_instance = None


def onMayaDroppedPythonFile(*args, **kwargs):
    """Called by Maya when this file is dragged onto the viewport."""
    create_installer_ui(os.path.dirname(__file__))


def create_installer_ui(installer_dir=None):
    global _ribbon_pack_installer_instance

    if installer_dir is None:
        installer_dir = THIS_DIR

    if _ribbon_pack_installer_instance is not None:
        try:
            _ribbon_pack_installer_instance.close()
            _ribbon_pack_installer_instance.deleteLater()
        except Exception:
            pass
        _ribbon_pack_installer_instance = None

    main_window = get_maya_main_window()
    if main_window is not None:
        for child in main_window.children():
            if child.objectName() == "RibbonPackInstallerWindow":
                try:
                    child.close()
                    child.deleteLater()
                except Exception:
                    pass

    _ribbon_pack_installer_instance = RibbonPackInstallerUI(installer_dir)
    _ribbon_pack_installer_instance.show()
    return _ribbon_pack_installer_instance


if __name__ == '__main__':
    # Allow manual invocation from the Script Editor for testing, in
    # addition to the drag-drop hook.
    create_installer_ui(THIS_DIR)
