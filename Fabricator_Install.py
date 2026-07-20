# Fabricator_Install.py
"""FabricatorStudio toolset — drag-and-drop installer.

Drag this file into the Maya viewport. Maya's native Python-drop hook calls
onMayaDroppedPythonFile(), which shows a Mindmeld-branded confirm dialog.
Nothing installs until you click Install.

Two install modes:
  COPY (default): copies Fabricator_Data/maya_tools/ + Fabricator_Data/
     icons/ into ONE brand folder nested under the chosen Install
     Directory — <install_dir>/fabricator_studio/{maya_tools, icons} —
     preserving the sibling relationship fs_shelf.py's icon-path walk
     assumes (see install_payload()). The default Install Directory is
     the shared, un-versioned <maya_root>/scripts (one install serves
     every Maya version), asked of Maya itself so redirected-Documents
     machines resolve correctly.
  RUN FROM CURRENT LOCATION (wire-only): no copy; this user's Maya is
     pointed at the payload where it already sits (a network/P4 share).

Both modes then:
  - Version-guard (refuse below Maya 2022), preserve + restore any
    existing project_configs/ across a reinstall wipe (copy mode).
  - Append a sentinel-guarded bootstrap block to the user's own
    userSetup.py (always <maya_root>/scripts/userSetup.py) wiring
    sys.path (+ _vendor) and firing maya_startup.run() + the JSON menu/
    shelf/hotkey builders. Idempotent — re-running Install replaces only
    the FABRICATOR_START/FABRICATOR_END block; the block carries a
    FABRICATOR_MODE marker so the uninstaller knows a linked payload is
    never ours to delete.
  - Build the toolbar immediately so the user sees the result without
    restarting Maya (the first-welcome tour fires here). The menu and
    shelves are OFF by default (Adrian, 2026-07-20) — the build_* calls
    below run, but fs_menu/fs_shelf return early unless the user has
    enabled them in Settings > MAYA INTEGRATION.

Zero network calls anywhere in this file — pure local filesystem + Maya
UI. No admin rights required (see docs/superpowers/... INSTALLER.md for
the friction rationale).
"""
from __future__ import division
from __future__ import print_function
from __future__ import absolute_import

__author__ = "Adrian Melian"

import os
import re
import sys
import shutil
import platform

THIS_FILE_PATH = os.path.abspath(__file__)
THIS_DIR = os.path.dirname(THIS_FILE_PATH)

_PAYLOAD_DIR_NAME = 'Fabricator_Data'
_MIN_MAYA_VERSION = 2022

_SENTINEL_START = '# FABRICATOR_START'
_SENTINEL_END = '# FABRICATOR_END'

try:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtGui import QGuiApplication
    from shiboken6 import wrapInstance
    PYSIDE_VERSION = 6
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
    from PySide2.QtGui import QGuiApplication
    from shiboken2 import wrapInstance
    PYSIDE_VERSION = 2

import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMayaUI as omui


# ─────────────────────────────────────────────
# Maya / path helpers
# ─────────────────────────────────────────────

def get_maya_version() -> int:
    """Parse cmds.about(version=True) into a 4-digit year int. Falls back
    to 0 (treated as "too old, refuse") if it can't be parsed, rather than
    silently assuming a recent version."""
    try:
        version_string = cmds.about(version=True)
        for part in version_string.split():
            if part.isdigit() and len(part) == 4:
                return int(part)
            if '.' in part:
                major = part.split('.')[0]
                if major.isdigit() and len(major) == 4:
                    return int(major)
    except Exception:
        pass
    return 0


def get_maya_scripts_dir() -> str:
    """Shared, UN-VERSIONED Documents/maya/scripts — same install-once,
    every-version trick Animo uses. See get_maya_version() for the only
    place version matters (the guard, not path selection)."""
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
    """The user's UN-versioned maya folder (e.g. D:/Documents/maya),
    asked of Maya itself so a redirected Documents folder resolves
    correctly (the USERPROFILE guess above does not follow redirection).
    Order: MAYA_APP_DIR env (authoritative when set) -> internalVar's
    versioned userAppDir minus the version segment -> the legacy
    env-var guess."""
    base = os.environ.get('MAYA_APP_DIR')
    if base:
        return os.path.normpath(base)
    try:
        app_dir = cmds.internalVar(userAppDir=True)   # .../maya/2025/
        if app_dir:
            return os.path.normpath(os.path.dirname(app_dir.rstrip('/\\')))
    except Exception:
        pass
    return os.path.normpath(os.path.dirname(get_maya_scripts_dir()))


def get_default_install_dir() -> str:
    """Default payload destination: <maya_root>/scripts (shared,
    un-versioned — one install serves every Maya version)."""
    return os.path.join(get_maya_root(), 'scripts')


_INSTALL_SUBDIR = 'fabricator_studio'


def install_root_for(install_dir: str) -> str:
    """The payload nests inside the chosen Install Directory under one
    brand folder — <install_dir>/fabricator_studio/{maya_tools, icons} —
    so an install drops exactly one folder into the user's location
    (Adrian, 2026-07-19)."""
    return os.path.normpath(os.path.join(install_dir, _INSTALL_SUBDIR))


def get_user_setup_path() -> str:
    """Where the bootstrap block ALWAYS lives, in BOTH install modes:
    the user's own <maya_root>/scripts/userSetup.py. Maya scans this
    folder on every version; the payload location is independent."""
    return os.path.join(get_maya_root(), 'scripts', 'userSetup.py')


_REPO_ROOT_RE = re.compile(r"_FAB_REPO_ROOT\s*=\s*r?['\"](.+?)['\"]")
_MODE_RE = re.compile(r'#\s*FABRICATOR_MODE:\s*(\w+)')


def parse_wired_install(user_setup_text: str) -> tuple:
    """(repo_root, mode) from an existing sentinel block, or ('', '').
    mode is 'copy' | 'linked'; a block without the marker is a pre-v1
    copy install."""
    if _SENTINEL_START not in user_setup_text:
        return '', ''
    m = _REPO_ROOT_RE.search(user_setup_text)
    if not m:
        return '', ''
    mode_m = _MODE_RE.search(user_setup_text)
    return m.group(1), (mode_m.group(1) if mode_m else 'copy')


def _initial_install_dir(user_setup_path: str = None) -> str:
    """Prefill for the Install Directory field: a previously wired COPY
    install's root (so re-running the installer = update in place),
    else the Maya-derived default. A LINKED wire never prefills — the
    copy destination is a different question from the share it points
    at."""
    path = user_setup_path or get_user_setup_path()
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            root, mode = parse_wired_install(fh.read())
        if root and mode == 'copy' and os.path.isdir(root):
            root = os.path.normpath(root)
            # The wired root is the NESTED fabricator_studio folder; the
            # field shows the user-chosen parent so re-running lands on
            # top of itself instead of nesting again.
            if os.path.basename(root) == _INSTALL_SUBDIR:
                return os.path.dirname(root)
            return root
    except OSError:
        pass
    return get_default_install_dir()


def get_maya_main_window():
    main_window_ptr = omui.MQtUtil.mainWindow()
    if main_window_ptr is None:
        return None
    return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)


# ─────────────────────────────────────────────
# Install-tree copy + prefs preservation
# ─────────────────────────────────────────────

def _preserve_paths(target_maya_tools: str) -> dict:
    """Snapshot bytes for paths that must survive a reinstall wipe:
    the project_configs/ tree (studio may have added custom configs) and
    any per-user Fabricator prefs directory. Returns a dict suitable for
    _restore_paths(). Missing paths are simply absent from the dict — no
    placeholder entries, so restore is a straight "if present, write it
    back" loop."""
    preserved = {}

    project_configs = os.path.join(
        target_maya_tools, 'framework', 'project_configs'
    )
    if os.path.isdir(project_configs):
        preserved['project_configs'] = _snapshot_tree(project_configs)

    return preserved


def _snapshot_tree(path: str) -> dict:
    """Read every file under `path` into memory as {relpath: bytes}.
    Small trees only (project configs are a handful of JSON files) —
    not intended for the whole payload."""
    snapshot = {}
    for root, _dirs, files in os.walk(path):
        for fname in files:
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, path)
            try:
                with open(full, 'rb') as fh:
                    snapshot[rel] = fh.read()
            except OSError:
                pass
    return snapshot


def _restore_paths(target_maya_tools: str, preserved: dict) -> None:
    if 'project_configs' in preserved:
        project_configs = os.path.join(
            target_maya_tools, 'framework', 'project_configs'
        )
        for rel, data in preserved['project_configs'].items():
            dest = os.path.join(project_configs, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, 'wb') as fh:
                fh.write(data)


def install_payload(scripts_dir: str, icons_dir: str, payload_dir: str) -> tuple:
    """Copy Fabricator_Data/maya_tools -> scripts_dir/maya_tools and
    Fabricator_Data/icons -> icons_dir, preserving project_configs/ across
    a reinstall. Returns (installed_maya_tools_path, installed_icons_path).

    Raises RuntimeError if the payload folder or its maya_tools/ subtree
    is missing — never partially installs silently.
    """
    src_maya_tools = os.path.join(payload_dir, 'maya_tools')
    src_icons = os.path.join(payload_dir, 'icons')

    if not os.path.isdir(src_maya_tools):
        raise RuntimeError(
            f'{_PAYLOAD_DIR_NAME}/maya_tools not found next to the installer. '
            f'Expected: {src_maya_tools}'
        )

    if not os.path.isdir(scripts_dir):
        os.makedirs(scripts_dir)
    if not os.path.isdir(icons_dir):
        os.makedirs(icons_dir)

    dest_maya_tools = os.path.join(scripts_dir, 'maya_tools')
    # icons_dir is <install root>/icons — a sibling of maya_tools/. fs_shelf.py
    # resolves its icon dir by walking up exactly 4 levels from itself
    # (maya_tools/framework/shelves/fs_shelf.py -> install root) and
    # appending 'icons'; only this exact sibling layout resolves, any other
    # placement silently blanks every shelf icon.
    dest_icons = icons_dir  # icons/ contents merge into the destination icons dir

    preserved = {}
    if os.path.isdir(dest_maya_tools):
        preserved = _preserve_paths(dest_maya_tools)
        shutil.rmtree(dest_maya_tools)

    shutil.copytree(src_maya_tools, dest_maya_tools)
    _restore_paths(dest_maya_tools, preserved)

    if os.path.isdir(src_icons):
        _merge_copytree(src_icons, dest_icons)

    # VERSION.txt rides the payload root (written by package_release at cut
    # time); copy it beside maya_tools/ so the Bridge button's About card can
    # report the installed build. Wire-only installs read it straight from
    # Fabricator_Data/ (the same parent-of-maya_tools lookup); a missing file
    # means a development checkout, never an error.
    src_version = os.path.join(payload_dir, 'VERSION.txt')
    if os.path.isfile(src_version):
        shutil.copy2(src_version,
                     os.path.join(os.path.dirname(dest_maya_tools),
                                  'VERSION.txt'))

    return dest_maya_tools, dest_icons


def _merge_copytree(src: str, dest: str) -> None:
    """Copy src/* into dest/, overwriting files but never deleting
    anything already in dest that isn't part of this payload (icons dir
    is shared with whatever else the user has there)."""
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        dest_root = dest if rel == '.' else os.path.join(dest, rel)
        os.makedirs(dest_root, exist_ok=True)
        for fname in files:
            shutil.copy2(os.path.join(root, fname), os.path.join(dest_root, fname))


# ─────────────────────────────────────────────
# userSetup.py sentinel-guarded bootstrap
# ─────────────────────────────────────────────

_BOOTSTRAP_BLOCK_TEMPLATE = '''{start}
# FABRICATOR_MODE: {mode}
# Fabricator toolset bootstrap — auto-managed by Fabricator_Install.py /
# Fabricator_Uninstall.py. Safe to re-run the installer; this exact block
# (between the sentinel comments) is replaced in place. Do not hand-edit
# inside these markers — Uninstall removes everything between them.
import maya.cmds as _fab_cmds
import sys as _fab_sys
import os as _fab_os

_FAB_REPO_ROOT = r'{repo_root}'
if _FAB_REPO_ROOT not in _fab_sys.path:
    _fab_sys.path.insert(0, _FAB_REPO_ROOT)

_FAB_VENDOR = _fab_os.path.join(_FAB_REPO_ROOT, 'maya_tools', '_vendor')
if _FAB_VENDOR not in _fab_sys.path:
    _fab_sys.path.insert(0, _FAB_VENDOR)


def _fab_run_startup():
    from maya_tools.framework.maya_startup import run
    run()


def _fab_build_menu():
    from maya_tools.framework.menu.fs_menu import build_menu
    build_menu()


def _fab_build_shelves():
    from maya_tools.framework.shelves.fs_shelf import build_shelves
    build_shelves()


def _fab_build_hotkeys():
    from maya_tools.framework.hotkeys.fs_hotkeys import build_hotkeys
    build_hotkeys()


def _fab_restore_toolbar():
    from maya_tools.framework.toolbar import toolbar_app
    toolbar_app.restore()


_fab_cmds.evalDeferred(_fab_run_startup,   lowestPriority=True)
_fab_cmds.evalDeferred(_fab_build_menu,    lowestPriority=True)
_fab_cmds.evalDeferred(_fab_build_shelves, lowestPriority=True)
_fab_cmds.evalDeferred(_fab_build_hotkeys, lowestPriority=True)
_fab_cmds.evalDeferred(_fab_restore_toolbar, lowestPriority=True)
{end}
'''


def _build_bootstrap_block(repo_root: str, mode: str = 'copy') -> str:
    return _BOOTSTRAP_BLOCK_TEMPLATE.format(
        start=_SENTINEL_START, end=_SENTINEL_END,
        repo_root=repo_root.replace('\\', '/'), mode=mode,
    )


def _strip_sentinel_block(text: str) -> str:
    """Remove any existing FABRICATOR_START..FABRICATOR_END block
    (inclusive), tolerant of blank-line variance. Used by both reinstall
    (strip then re-append) and the uninstaller."""
    pattern = re.compile(
        re.escape(_SENTINEL_START) + r'.*?' + re.escape(_SENTINEL_END) + r'\n?',
        re.DOTALL,
    )
    return pattern.sub('', text)


def write_user_setup_bootstrap(repo_root: str, user_setup_path: str,
                               mode: str = 'copy') -> None:
    """Idempotently append the sentinel-guarded bootstrap block to
    userSetup.py. Never touches anything outside the sentinels — an
    existing block is stripped and replaced; everything else in the file
    (the user's own code, KS_START/KS_END, ANIMO_START/ANIMO_END, whatever
    else lives there) is preserved byte-for-byte. `mode` is stamped into
    the block (`# FABRICATOR_MODE: copy|linked`) so the uninstaller knows
    whether the payload at repo_root is ours to delete ('copy') or a
    shared install it must never touch ('linked')."""
    existing = ''
    if os.path.exists(user_setup_path):
        with open(user_setup_path, 'r', encoding='utf-8') as fh:
            existing = fh.read()

    existing = _strip_sentinel_block(existing)
    if existing and not existing.endswith('\n'):
        existing += '\n'

    block = _build_bootstrap_block(repo_root, mode=mode)
    new_content = existing + ('\n' if existing else '') + block

    parent = os.path.dirname(user_setup_path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)

    with open(user_setup_path, 'w', encoding='utf-8') as fh:
        fh.write(new_content)


# ─────────────────────────────────────────────
# Live build (so the user sees results without restarting Maya)
# ─────────────────────────────────────────────

def build_now(repo_root: str) -> None:
    """Run the same five deferred calls the bootstrap block installs,
    right now, so menus/shelves/hotkeys appear immediately post-install
    without waiting for a Maya restart. Import errors here are caught by
    the caller (run_installation) and reported in the confirm dialog —
    they do not roll back the file copy, since the copy already succeeded."""
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    vendor = os.path.join(repo_root, 'maya_tools', '_vendor')
    if vendor not in sys.path:
        sys.path.insert(0, vendor)

    # Tear down any LIVE toolbar through the OLD cached module BEFORE the
    # purge below. teardown() drains scene_listeners' callback registry;
    # purging first would orphan those om2 callbacks forever (the fresh
    # module's registry starts empty, so the old cids become unreachable —
    # scene_listeners.py's documented invariant: drain before any purge).
    _old_app = sys.modules.get('maya_tools.framework.toolbar.toolbar_app')
    if _old_app is not None:
        try:
            _old_app.teardown()
        except Exception:
            pass

    # Purge any stale cached modules from a previous install in this
    # session (dev-iteration safety; harmless on a fresh Maya session).
    for name in [k for k in list(sys.modules) if k == 'maya_tools' or k.startswith('maya_tools.')]:
        del sys.modules[name]

    from maya_tools.framework.maya_startup import run as _run_startup
    from maya_tools.framework.menu.fs_menu import build_menu
    from maya_tools.framework.shelves.fs_shelf import build_shelves
    from maya_tools.framework.hotkeys.fs_hotkeys import build_hotkeys
    from maya_tools.framework.toolbar import toolbar_app

    _run_startup()
    build_menu()
    build_shelves()
    build_hotkeys()
    toolbar_app.restore()


# ─────────────────────────────────────────────
# Mindmeld-branded confirm dialog
# ─────────────────────────────────────────────

# Carbon / Iron / Bone / Plasma / Ember — matches
# maya_tools/utils/qt/mindmeld/mindmeld_style.py TOKENS. Duplicated here
# (not imported) deliberately: this file must run BEFORE the payload is on
# sys.path — it can't import the toolset it's about to install.
_CARBON = "#0B0E10"
_IRON = "#1C2126"
_IRON_2 = "#262C33"
_IRON_3 = "#353D45"
_BONE = "#E8E0D0"
_BONE_DIM = "#8A8378"
_PLASMA = "#7CFFB2"
_PLASMA_DIM = "#4FB888"
_EMBER = "#FF7A3D"
_EMBER_DIM = "#B5532A"


class _InstallProgressCard(QtWidgets.QWidget):
    """The install-progress popover — a deliberate self-contained twin of
    maya_tools/utils/qt/progress_card.py (Adrian, 2026-07-20).

    It cannot import the real one: this file runs BEFORE the payload is on
    sys.path (same reason the Mindmeld tokens are duplicated at the top).
    So the look is mirrored here and the two must be kept in step by hand;
    that is the price of a branded card at install time.

    Deliberately short-lived: finish() holds only briefly before fading,
    so this card is gone before the first-run welcome tour appears.
    """

    _HOLD_MS = 900
    _FADE_MS = 320

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.Window
                            | QtCore.Qt.FramelessWindowHint
                            | QtCore.Qt.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        self._card = QtWidgets.QFrame(self)
        self._card.setObjectName('FabProgressCard')
        self._card.setFixedWidth(380)
        outer.addWidget(self._card)

        lay = QtWidgets.QVBoxLayout(self._card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)

        self._title = QtWidgets.QLabel('Installing FabricatorStudio')
        self._title.setObjectName('FabProgressTitle')
        lay.addWidget(self._title)

        self._status = QtWidgets.QLabel('')
        self._status.setObjectName('FabProgressStatus')
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        self._bar = QtWidgets.QProgressBar(self._card)
        self._bar.setObjectName('FabProgressBar')
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.setRange(0, 100)
        lay.addWidget(self._bar)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setColor(QtGui.QColor(0, 0, 0, 190))
        shadow.setOffset(0, 7)
        self._card.setGraphicsEffect(shadow)

        self._fade = QtCore.QPropertyAnimation(self, b'windowOpacity', self)
        self._hold = QtCore.QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._fade_out)

        self._apply_theme(_PLASMA)

    def _apply_theme(self, accent: str) -> None:
        self.setStyleSheet(f"""
            QFrame#FabProgressCard {{
                background-color: {_CARBON};
                border: 1px solid {accent};
                border-radius: 8px;
            }}
            QLabel {{ background: transparent; }}
            QLabel#FabProgressTitle {{
                color: {_BONE}; font-size: 11pt; font-weight: bold;
            }}
            QLabel#FabProgressStatus {{ color: {_BONE_DIM}; font-size: 9pt; }}
            QProgressBar#FabProgressBar {{
                background-color: {_IRON_2};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar#FabProgressBar::chunk {{
                background-color: {accent};
                border-radius: 4px;
            }}
        """)

    def start(self, status: str) -> None:
        self._status.setText(status)
        self._bar.setValue(0)
        self.adjustSize()
        self._center()
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        QtWidgets.QApplication.processEvents()

    def step(self, value: int, status: str = None) -> None:
        self._bar.setValue(max(0, min(100, int(value))))
        if status is not None:
            self._status.setText(status)
        QtWidgets.QApplication.processEvents()

    def finish(self, message: str, ok: bool = True) -> None:
        self._bar.setValue(100)
        self._status.setText(message)
        self._apply_theme(_PLASMA if ok else _EMBER)
        QtWidgets.QApplication.processEvents()
        self._hold.start(self._HOLD_MS)

    def _center(self) -> None:
        host = self.parentWidget()
        if host is not None and host.isVisible():
            geo = host.frameGeometry()
            cx = geo.x() + geo.width() // 2
            cy = geo.y() + int(geo.height() * 0.42)
        else:
            r = QGuiApplication.primaryScreen().availableGeometry()
            cx = r.x() + r.width() // 2
            cy = r.y() + int(r.height() * 0.42)
        self.move(cx - self.width() // 2, cy - self.height() // 2)

    def _fade_out(self) -> None:
        self._fade.stop()
        try:
            self._fade.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._fade.setDuration(self._FADE_MS)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.close)
        self._fade.start()


class FabricatorInstallerUI(QtWidgets.QDialog):

    def __init__(self, installer_dir, parent=None):
        super(FabricatorInstallerUI, self).__init__(parent or get_maya_main_window())
        self.setObjectName("FabricatorInstallerWindow")
        self.installer_dir = installer_dir
        self.repo_root = None  # set on successful install

        # Frameless popover (Adrian, 2026-07-20): no OS title bar, so the
        # first thing a user ever sees from FabricatorStudio reads as one
        # of our cards, not a Maya dialog. Costs: nothing drags it (see
        # mousePressEvent/mouseMoveEvent) and there is no X (Cancel and
        # Esc both close, QDialog's built-in reject).
        self.setWindowFlags(QtCore.Qt.Window
                            | QtCore.Qt.FramelessWindowHint
                            | QtCore.Qt.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self._drag_from = None
        self.setMinimumWidth(440)
        self.setWindowTitle('FabricatorStudio Installer')

        self._build_ui()
        self._apply_theme()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        # Outer layout holds only the card, with margins for the shadow;
        # every real widget lives inside the card so the rounded corners
        # and border actually clip.
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        self._card = QtWidgets.QFrame(self)
        self._card.setObjectName('FabInstallerCard')
        outer.addWidget(self._card)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setColor(QtGui.QColor(0, 0, 0, 190))
        shadow.setOffset(0, 7)
        self._card.setGraphicsEffect(shadow)

        root = QtWidgets.QVBoxLayout(self._card)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        # Fullname banner from the payload (Adrian, 2026-07-19); this file
        # runs before the payload is on sys.path, so the image is loaded
        # straight off disk and the text mark is the fallback. No version
        # pill — the banner stands alone (Adrian, 2026-07-19).
        banner_path = os.path.join(self.installer_dir, _PAYLOAD_DIR_NAME,
                                   'icons', 'fs_fullname_banner.png')
        brand = None
        if os.path.isfile(banner_path):
            pix = QtGui.QPixmap(banner_path)
            if not pix.isNull():
                brand = QtWidgets.QLabel()
                brand.setPixmap(pix.scaledToWidth(
                    300, QtCore.Qt.SmoothTransformation))
        if brand is None:
            brand = QtWidgets.QLabel("FABRICATOR")
            brand.setStyleSheet(
                f"font-family:'Consolas','Courier New',monospace; font-size:22pt; "
                f"font-weight:bold; color:{_PLASMA}; letter-spacing:2px;"
            )
        root.addWidget(brand)

        rule = QtWidgets.QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background-color:{_IRON_3};")
        root.addWidget(rule)

        body = QtWidgets.QLabel(
            "Installs the FabricatorStudio pipeline toolset. No admin "
            "rights needed. Nothing is sent over the network by this "
            "installer."
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"color:{_BONE}; font-size:10pt;")
        root.addWidget(body)

        dir_caps = QtWidgets.QLabel("INSTALL DIRECTORY")
        dir_caps.setStyleSheet(
            f"color:{_PLASMA_DIM}; font-size:8pt; letter-spacing:1px;")
        root.addWidget(dir_caps)

        dir_row = QtWidgets.QHBoxLayout()
        dir_row.setSpacing(6)
        self._dir_edit = QtWidgets.QLineEdit(_initial_install_dir())
        self._dir_edit.setStyleSheet(
            f"background-color:{_IRON}; color:{_BONE}; font-size:9pt; "
            f"border:1px solid {_IRON_3}; border-radius:3px; padding:4px 6px;")
        dir_row.addWidget(self._dir_edit, 1)
        self._browse_btn = QtWidgets.QPushButton("Browse...")
        self._browse_btn.clicked.connect(self._browse_install_dir)
        dir_row.addWidget(self._browse_btn)
        root.addLayout(dir_row)

        self._mode_copy = QtWidgets.QRadioButton(
            "Copy the tools to the Install Directory")
        self._mode_copy.setChecked(True)
        self._mode_linked = QtWidgets.QRadioButton(
            "Run from current location (shared or network installs)")
        for rb in (self._mode_copy, self._mode_linked):
            rb.setStyleSheet(f"color:{_BONE}; font-size:9pt;")
            root.addWidget(rb)

        self._mode_linked.toggled.connect(self._on_mode_changed)

        self._status_label = QtWidgets.QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"color:{_EMBER}; font-size:9pt;")
        self._status_label.setVisible(False)
        root.addWidget(self._status_label)

        # Install (plasma) sits left of Cancel (ember), Adrian 2026-07-20.
        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch()

        self._install_button = QtWidgets.QPushButton("Install")
        self._install_button.setObjectName("FabInstallBtn")
        self._install_button.setMinimumWidth(120)
        self._install_button.clicked.connect(self.run_installation)
        button_row.addWidget(self._install_button)

        self._cancel_button = QtWidgets.QPushButton("Cancel")
        self._cancel_button.setObjectName("FabCancelBtn")
        self._cancel_button.clicked.connect(self.close)
        button_row.addWidget(self._cancel_button)
        root.addLayout(button_row)

        footer = QtWidgets.QLabel("FabricatorStudio. Free, no account required.")
        footer.setAlignment(QtCore.Qt.AlignCenter)
        footer.setStyleSheet(f"color:{_BONE_DIM}; font-size:8pt;")
        root.addWidget(footer)

    # ---------- frameless drag ----------

    @staticmethod
    def _global_pos(event):
        """PySide6 exposes globalPosition() (a QPointF); PySide2, which is
        what Maya 2022 to 2024 ship, only has globalPos(). This file has
        to run on both."""
        if hasattr(event, 'globalPosition'):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def mousePressEvent(self, event) -> None:
        # No title bar means the card itself is the handle. Only a press
        # on bare card background starts a drag; presses that land on a
        # child widget (field, button, checkbox) never reach here.
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_from = (self._global_pos(event)
                               - self.frameGeometry().topLeft())
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and \
                event.buttons() & QtCore.Qt.LeftButton:
            self.move(self._global_pos(event) - self._drag_from)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None

    def _apply_theme(self) -> None:
        self.setStyleSheet(f"""
            QFrame#FabInstallerCard {{
                background-color: {_CARBON};
                border: 1px solid {_PLASMA};
                border-radius: 8px;
            }}
            QLabel {{ color: {_BONE}; background: transparent; }}
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
            /* Go is plasma, dismiss is ember (Adrian, 2026-07-20). Dark
               text on both fills: bone on plasma fails contrast. */
            QPushButton#FabInstallBtn {{
                background-color: {_PLASMA};
                border: 1px solid {_PLASMA};
                color: {_CARBON};
                font-weight: bold;
            }}
            QPushButton#FabInstallBtn:hover {{
                background-color: {_PLASMA_DIM};
                border-color: {_PLASMA_DIM};
            }}
            QPushButton#FabInstallBtn:pressed {{
                background-color: {_PLASMA_DIM};
            }}
            QPushButton#FabInstallBtn:disabled {{
                background-color: {_IRON_2};
                border-color: {_IRON_3};
                color: {_BONE_DIM};
            }}
            QPushButton#FabCancelBtn {{
                background-color: {_EMBER};
                border: 1px solid {_EMBER};
                color: {_CARBON};
                font-weight: bold;
            }}
            QPushButton#FabCancelBtn:hover {{
                background-color: {_EMBER_DIM};
                border-color: {_EMBER_DIM};
            }}
            QPushButton#FabCancelBtn:pressed {{
                background-color: {_EMBER_DIM};
            }}
            QCheckBox {{ spacing: 8px; }}
            QCheckBox::indicator {{
                width: 14px; height: 14px;
                border: 1px solid {_IRON_3};
                background-color: {_IRON};
            }}
            QCheckBox::indicator:checked {{ background-color: {_PLASMA_DIM}; }}
        """)

    def _browse_install_dir(self) -> None:
        start = self._dir_edit.text().strip() or get_default_install_dir()
        picked = QtWidgets.QFileDialog.getExistingDirectory(
            self, 'Install Directory', start)
        if picked:
            self._dir_edit.setText(picked)

    def _on_mode_changed(self, linked: bool) -> None:
        self._dir_edit.setEnabled(not linked)
        self._browse_btn.setEnabled(not linked)

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

        # The dialog steps aside and the progress card carries the run
        # (Adrian, 2026-07-20). On failure the dialog comes back so the
        # error is readable and Install can be retried.
        self._progress = _InstallProgressCard(get_maya_main_window())
        self._progress.start('Preparing...')
        self.hide()

        try:
            maya_version = get_maya_version()
            if maya_version and maya_version < _MIN_MAYA_VERSION:
                raise RuntimeError(
                    f'Fabricator requires Maya {_MIN_MAYA_VERSION} or newer '
                    f'(detected {maya_version}). Not installing.'
                )

            payload_dir = os.path.join(self.installer_dir, _PAYLOAD_DIR_NAME)
            user_setup_path = get_user_setup_path()

            if self._mode_linked.isChecked():
                # Wire-only: the payload next to this installer IS the
                # install (network/P4 share). Nothing is copied; a shared
                # payload is never ours to delete (mode marker: 'linked').
                repo_root = payload_dir
                if not os.path.isdir(os.path.join(repo_root, 'maya_tools')):
                    raise RuntimeError(
                        f'{_PAYLOAD_DIR_NAME}/maya_tools not found next to '
                        f'the installer. Cannot run from this location.')
                self._set_status('Wiring userSetup.py to the shared install...')
                self._progress.step(45, 'Pointing Maya at the shared install...')
                write_user_setup_bootstrap(repo_root, user_setup_path,
                                           mode='linked')
            else:
                install_dir = (self._dir_edit.text().strip()
                               or get_default_install_dir())
                # Everything nests under ONE brand folder:
                # <install_dir>/fabricator_studio/{maya_tools, icons}.
                # icons/ must sit INSIDE that folder as a sibling of
                # maya_tools/ — fs_shelf.py resolves its icon dir by
                # walking up 4 levels from itself and appending 'icons',
                # so only this exact layout resolves (see install_payload).
                nested_root = install_root_for(install_dir)
                icons_dir = os.path.join(nested_root, 'icons')
                self._set_status('Copying toolset files...')
                self._progress.step(15, 'Copying toolset files...')
                install_payload(nested_root, icons_dir, payload_dir)
                repo_root = nested_root
                self._set_status('Wiring userSetup.py bootstrap...')
                self._progress.step(55, 'Wiring Maya startup...')
                write_user_setup_bootstrap(repo_root, user_setup_path,
                                           mode='copy')

            self.repo_root = repo_root

            self._set_status('Building menu, shelves, hotkeys...')
            self._progress.step(75, 'Building the toolbar...')
            try:
                build_now(repo_root)
            except Exception as build_exc:
                # File copy + userSetup wiring already succeeded — a restart
                # of Maya will pick those up via userSetup.py regardless.
                # Report but do not treat as a failed install.
                self._set_status(
                    f'Installed. Menu/shelf build hit an error and will '
                    f'retry on next Maya launch: {build_exc}', ok=False,
                )
                self._progress.finish(
                    'Installed. Restart Maya to finish loading.', ok=False)
                self._finish()
                return

            # No inViewMessage here (Adrian, 2026-07-20): the progress card
            # reports success, and the welcome card that follows IS the
            # "you're installed" moment. A viewport banner on top of both
            # was one success message too many.
            self._set_status('Installed successfully.', ok=True)
            self._progress.finish('Installed successfully.')
            self._finish()

        except Exception as exc:
            cmds.waitCursor(state=False)
            # Failure: drop the card immediately and bring the dialog back
            # with the error — a lingering progress card over a dead run
            # would read as "still working".
            try:
                self._progress.close()
            except Exception:
                pass
            self._set_status(f'Installation failed: {exc}', ok=False)
            self._install_button.setEnabled(True)
            self._install_button.setText("Install")
            self.show()
            self.raise_()

    def _finish(self) -> None:
        cmds.waitCursor(state=False)
        self._install_button.setText("Done")
        QtCore.QTimer.singleShot(600, self.close)


# ─────────────────────────────────────────────
# Maya drop hook
# ─────────────────────────────────────────────

_fabricator_installer_instance = None


def onMayaDroppedPythonFile(*args, **kwargs):
    """Called by Maya when this file is dragged onto the viewport."""
    create_installer_ui(os.path.dirname(__file__))


def create_installer_ui(installer_dir=None):
    global _fabricator_installer_instance

    if installer_dir is None:
        installer_dir = THIS_DIR

    if _fabricator_installer_instance is not None:
        try:
            _fabricator_installer_instance.close()
            _fabricator_installer_instance.deleteLater()
        except Exception:
            pass
        _fabricator_installer_instance = None

    main_window = get_maya_main_window()
    if main_window is not None:
        for child in main_window.children():
            if child.objectName() == "FabricatorInstallerWindow":
                try:
                    child.close()
                    child.deleteLater()
                except Exception:
                    pass

    _fabricator_installer_instance = FabricatorInstallerUI(installer_dir)
    _fabricator_installer_instance.show()
    return _fabricator_installer_instance


if __name__ == '__main__':
    # Allow manual invocation from the Script Editor for testing, in
    # addition to the drag-drop hook.
    create_installer_ui(THIS_DIR)
