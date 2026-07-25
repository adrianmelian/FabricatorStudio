# _dev/test_installer_logic.py
"""Offscreen tests for Fabricator_Install.py / Fabricator_Uninstall.py
pure logic: Maya-derived path resolution, sentinel parsing, mode-aware
bootstrap blocks, and mode-aware uninstall decisions.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_installer_logic.py
(QT_QPA_PLATFORM=offscreen)
"""
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
# NO maya.standalone here, deliberately (same pattern as test_first_run.py):
# an initialized standalone session hard-crashes the offscreen QApplication
# these tests need (native abort, exit 127 — reproduced 2026-07-19, with
# and without userSetup.py). Importing Fabricator_Install only needs the
# maya modules to be importable, not initialized: every tested code path
# either avoids cmds or falls back cleanly when a cmds call raises
# (get_maya_root's internalVar guard), and the dialog test passes an
# explicit parent so get_maya_main_window() is never reached.

FAILURES = []


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


# ─── path resolution ─────────────────────────────────────────────────────

def test_maya_root_prefers_maya_app_dir_env():
    import Fabricator_Install as inst
    old = os.environ.get('MAYA_APP_DIR')
    os.environ['MAYA_APP_DIR'] = r'D:\Documents\maya'
    try:
        assert inst.get_maya_root() == r'D:\Documents\maya'
        assert inst.get_default_install_dir() == os.path.join(
            r'D:\Documents\maya', 'scripts')
        assert inst.get_user_setup_path() == os.path.join(
            r'D:\Documents\maya', 'scripts', 'userSetup.py')
    finally:
        if old is None:
            os.environ.pop('MAYA_APP_DIR', None)
        else:
            os.environ['MAYA_APP_DIR'] = old


def test_maya_root_without_env_is_a_real_absolute_path():
    """Without the env var it falls to internalVar (standalone sets one)
    or the shell fallback: either way, absolute and non-empty, and the
    default install dir hangs a 'scripts' off it."""
    import Fabricator_Install as inst
    old = os.environ.pop('MAYA_APP_DIR', None)
    try:
        root = inst.get_maya_root()
        assert root and os.path.isabs(root), root
        assert inst.get_default_install_dir() == os.path.join(root, 'scripts')
    finally:
        if old is not None:
            os.environ['MAYA_APP_DIR'] = old


# ─── sentinel parsing ────────────────────────────────────────────────────

def test_parse_wired_install_reads_root_and_mode():
    import Fabricator_Install as inst
    block = inst._build_bootstrap_block(r'C:\share\Fabricator_Data',
                                        mode='linked')
    root, mode = inst.parse_wired_install('# mine\n' + block)
    assert root == 'C:/share/Fabricator_Data', root
    assert mode == 'linked', mode


def test_parse_wired_install_legacy_block_defaults_to_copy():
    """A pre-v1 block has no FABRICATOR_MODE line: mode reads 'copy'."""
    import Fabricator_Install as inst
    legacy = (f"{inst._SENTINEL_START}\n"
              "_FAB_REPO_ROOT = r'C:/Users/x/Documents/maya/scripts'\n"
              f"{inst._SENTINEL_END}\n")
    root, mode = inst.parse_wired_install(legacy)
    assert root == 'C:/Users/x/Documents/maya/scripts'
    assert mode == 'copy'


def test_parse_wired_install_no_block_is_empty():
    import Fabricator_Install as inst
    assert inst.parse_wired_install('') == ('', '')
    assert inst.parse_wired_install('# just a userSetup\n') == ('', '')


# ─── bootstrap write round-trip ──────────────────────────────────────────

def test_bootstrap_write_roundtrip_linked_mode():
    import Fabricator_Install as inst
    tmp = Path(tempfile.mkdtemp()) / 'scripts' / 'userSetup.py'
    inst.write_user_setup_bootstrap(r'\\srv\share\Fabricator_Data',
                                    str(tmp), mode='linked')
    text = tmp.read_text(encoding='utf-8')
    root, mode = inst.parse_wired_install(text)
    assert root == '//srv/share/Fabricator_Data', root
    assert mode == 'linked'


def test_bootstrap_rewrite_replaces_block_preserves_user_code():
    import Fabricator_Install as inst
    tmp = Path(tempfile.mkdtemp()) / 'userSetup.py'
    tmp.write_text('# my own startup\nprint("hi")\n', encoding='utf-8')
    inst.write_user_setup_bootstrap('C:/a', str(tmp), mode='copy')
    inst.write_user_setup_bootstrap('C:/b', str(tmp), mode='linked')
    text = tmp.read_text(encoding='utf-8')
    assert text.startswith('# my own startup\nprint("hi")\n'), text[:60]
    assert text.count(inst._SENTINEL_START) == 1, 'block not idempotent'
    root, mode = inst.parse_wired_install(text)
    assert (root, mode) == ('C:/b', 'linked')


# ─── dialog construction (offscreen) ─────────────────────────────────────

def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def test_installer_dialog_has_directory_field_and_mode_radios():
    qt_app()
    from PySide6.QtWidgets import QWidget
    import Fabricator_Install as inst
    host = QWidget()   # explicit parent: short-circuits get_maya_main_window()
    ui = inst.FabricatorInstallerUI(installer_dir=str(REPO_ROOT), parent=host)
    assert ui._dir_edit.text(), 'Install Directory must be prefilled'
    assert ui._mode_copy.isChecked(), 'copy is the default mode'
    assert not ui._mode_linked.isChecked()
    # linked mode disables the directory chooser
    ui._mode_linked.setChecked(True)
    assert not ui._dir_edit.isEnabled()
    assert not ui._browse_btn.isEnabled()
    ui._mode_copy.setChecked(True)
    assert ui._dir_edit.isEnabled()
    ui.close()


def test_installer_dialog_is_a_frameless_draggable_card():
    """Popover styling (Adrian, 2026-07-20): no OS title bar, a rounded
    plasma-bordered card, and a drag handler that works on BOTH PySide
    generations (Maya 2022-2024 ship PySide2, which has no
    globalPosition)."""
    qt_app()
    from PySide6 import QtCore
    from PySide6.QtWidgets import QWidget
    import Fabricator_Install as inst
    host = QWidget()
    ui = inst.FabricatorInstallerUI(installer_dir=str(REPO_ROOT), parent=host)
    flags = ui.windowFlags()
    assert flags & QtCore.Qt.FramelessWindowHint, 'title bar still present'
    assert ui._card.objectName() == 'FabInstallerCard'
    qss = ui.styleSheet()
    assert 'FabInstallerCard' in qss and 'border-radius' in qss

    class _P2Event:
        """A PySide2-shaped event: globalPos(), no globalPosition()."""
        def globalPos(self):
            return QtCore.QPoint(120, 90)

    assert ui._global_pos(_P2Event()) == QtCore.QPoint(120, 90)
    ui.close()


def test_install_progress_card_matches_the_toolset_card():
    """The installer's self-contained progress popover (it cannot import
    maya_tools.utils.qt.progress_card — that module does not exist until
    the payload lands): frameless, plasma-accented, and it flips to ember
    on a failed finish."""
    qt_app()
    from PySide6 import QtCore
    import Fabricator_Install as inst
    card = inst._InstallProgressCard(None)
    assert card.windowFlags() & QtCore.Qt.FramelessWindowHint
    card.start('Preparing...')
    assert card._bar.value() == 0
    card.step(55, 'Wiring Maya startup...')
    assert card._bar.value() == 55
    assert card._status.text() == 'Wiring Maya startup...'
    assert inst._PLASMA in card.styleSheet()
    card.finish('Installed successfully.')
    assert card._bar.value() == 100
    assert inst._PLASMA in card.styleSheet()
    card.finish('Installed. Restart Maya to finish loading.', ok=False)
    assert inst._EMBER in card.styleSheet(), 'failed finish must read ember'
    card.close()


def test_initial_install_dir_prefills_from_wired_copy_install():
    import Fabricator_Install as inst
    tmpdir = Path(tempfile.mkdtemp())
    # Nested layout (the shipping shape): the wired root is
    # <install_dir>/fabricator_studio and the field shows the PARENT.
    nested = tmpdir / 'chosen' / 'fabricator_studio'
    nested.mkdir(parents=True)
    setup = tmpdir / 'userSetup.py'
    inst.write_user_setup_bootstrap(str(nested), str(setup), mode='copy')
    got = inst._initial_install_dir(user_setup_path=str(setup))
    assert got == os.path.normpath(str(tmpdir / 'chosen')), got
    # A legacy flat wire prefills itself (no un-nesting to invent).
    flat = tmpdir / 'wired_root'
    flat.mkdir()
    inst.write_user_setup_bootstrap(str(flat), str(setup), mode='copy')
    got = inst._initial_install_dir(user_setup_path=str(setup))
    assert got == os.path.normpath(str(flat)), got
    # a LINKED wire never prefills the copy destination
    inst.write_user_setup_bootstrap(str(flat), str(setup), mode='linked')
    got = inst._initial_install_dir(user_setup_path=str(setup))
    assert got == inst.get_default_install_dir(), got


def test_install_root_nests_one_brand_folder():
    import Fabricator_Install as inst
    root = inst.install_root_for(r'D:\Documents\maya\scripts')
    assert root == os.path.normpath(
        r'D:\Documents\maya\scripts\fabricator_studio'), root


def test_default_install_dir_has_consistent_separators():
    """MAYA_APP_DIR often arrives with forward slashes (Maya sets it that
    way); the dialog must not show a D:/Documents/maya\\scripts mix."""
    import Fabricator_Install as inst
    old = os.environ.get('MAYA_APP_DIR')
    os.environ['MAYA_APP_DIR'] = 'D:/Documents/maya'
    try:
        got = inst.get_default_install_dir()
        assert '/' not in got, got
        assert got == r'D:\Documents\maya\scripts', got
    finally:
        if old is None:
            os.environ.pop('MAYA_APP_DIR', None)
        else:
            os.environ['MAYA_APP_DIR'] = old


# ─── uninstaller mode awareness ──────────────────────────────────────────

def _fake_install(tmpdir: Path, mode: str):
    """A wired install under tmpdir: payload tree + userSetup block."""
    import Fabricator_Install as inst
    payload_root = tmpdir / 'payload'
    (payload_root / 'maya_tools').mkdir(parents=True)
    (payload_root / 'maya_tools' / 'x.py').write_text('# x', encoding='utf-8')
    scripts = tmpdir / 'maya' / 'scripts'
    scripts.mkdir(parents=True)
    setup = scripts / 'userSetup.py'
    setup.write_text('# mine\n', encoding='utf-8')
    inst.write_user_setup_bootstrap(str(payload_root), str(setup), mode=mode)
    return payload_root, setup


class _isolated_uninstall:
    """Sandbox every real-machine path the uninstaller can reach: the
    prefs file (MAYA_APP_DIR) and the legacy USERPROFILE sweep. A test
    must never delete or edit files in the developer's actual maya
    folders."""

    def __init__(self, uninst, tmpdir: Path):
        self._uninst = uninst
        self._tmpdir = tmpdir

    def __enter__(self):
        self._old_env = os.environ.get('MAYA_APP_DIR')
        os.environ['MAYA_APP_DIR'] = str(self._tmpdir / 'maya')
        self._old_legacy = self._uninst.get_maya_scripts_dir
        legacy = str(self._tmpdir / 'legacy' / 'scripts')
        self._uninst.get_maya_scripts_dir = lambda: legacy
        return self

    def __exit__(self, *exc):
        if self._old_env is None:
            os.environ.pop('MAYA_APP_DIR', None)
        else:
            os.environ['MAYA_APP_DIR'] = self._old_env
        self._uninst.get_maya_scripts_dir = self._old_legacy
        return False


def test_uninstall_copy_mode_removes_wired_tree():
    import Fabricator_Uninstall as uninst
    tmpdir = Path(tempfile.mkdtemp())
    payload_root, setup = _fake_install(tmpdir, 'copy')
    with _isolated_uninstall(uninst, tmpdir):
        report = uninst.uninstall_fabricator(user_setup_path=str(setup))
    assert report['mode'] == 'copy'
    assert report['tree_removed'] is True
    assert not (payload_root / 'maya_tools').exists()
    assert report['bootstrap_removed'] is True
    # The user's own content survives verbatim; only the joining blank
    # line the installer added may remain.
    text = setup.read_text(encoding='utf-8')
    assert text.strip() == '# mine', repr(text)
    import Fabricator_Install as inst
    assert inst._SENTINEL_START not in text


def test_uninstall_nested_layout_removes_whole_brand_folder():
    """A nested fabricator_studio install is entirely ours: maya_tools
    AND icons go, and the brand folder itself disappears."""
    import Fabricator_Install as inst
    import Fabricator_Uninstall as uninst
    tmpdir = Path(tempfile.mkdtemp())
    nested = tmpdir / 'scripts' / 'fabricator_studio'
    (nested / 'maya_tools').mkdir(parents=True)
    (nested / 'icons').mkdir()
    (nested / 'icons' / 'fs_logo.png').write_bytes(b'png')
    scripts = tmpdir / 'maya' / 'scripts'
    scripts.mkdir(parents=True)
    setup = scripts / 'userSetup.py'
    inst.write_user_setup_bootstrap(str(nested), str(setup), mode='copy')
    with _isolated_uninstall(uninst, tmpdir):
        report = uninst.uninstall_fabricator(user_setup_path=str(setup))
    assert report['tree_removed'] is True
    assert not nested.exists(), 'brand folder survived uninstall'


def test_uninstall_linked_mode_never_touches_the_share():
    import Fabricator_Uninstall as uninst
    tmpdir = Path(tempfile.mkdtemp())
    payload_root, setup = _fake_install(tmpdir, 'linked')
    with _isolated_uninstall(uninst, tmpdir):
        report = uninst.uninstall_fabricator(user_setup_path=str(setup))
    assert report['mode'] == 'linked'
    assert report['tree_removed'] is False
    assert (payload_root / 'maya_tools' / 'x.py').exists(), \
        'linked uninstall deleted the shared payload'
    assert report['bootstrap_removed'] is True


def test_fresh_install_defaults_toolbar_only():
    """Adrian, 2026-07-20: the toolbar is the front door. A machine with
    no prefs yet gets NO menu and NO shelves; hotkeys stay on (no visual
    clutter), and an existing prefs file keeps whatever it already said."""
    from maya_tools.framework.toolbar import toolbar_prefs
    d = toolbar_prefs.default_prefs()
    assert d['load_menu'] is False, 'menu must be off on a fresh install'
    assert d['load_shelf'] is False, 'shelves must be off on a fresh install'
    assert d['load_hotkeys'] is True
    assert d['visible'] is True, 'the toolbar itself must still show'
    # An explicit True in a saved prefs file is never downgraded.
    import tempfile, json
    from pathlib import Path as _P
    tmp = _P(tempfile.mkdtemp()) / 'prefs.json'
    tmp.write_text(json.dumps({'load_menu': True, 'load_shelf': True}),
                   encoding='utf-8')
    loaded = toolbar_prefs.load_prefs(path=tmp)
    assert loaded['load_menu'] is True and loaded['load_shelf'] is True


def _make_install_tree(version_line=None):
    """A destination dir holding an installed payload, as install_payload
    lays it out: <dir>/fabricator_studio/{maya_tools/, VERSION.txt}.
    version_line=None writes no VERSION.txt (pre-v1 or dev checkout)."""
    import Fabricator_Install as FI
    dest = Path(tempfile.mkdtemp())
    root = Path(FI.install_root_for(str(dest)))
    (root / 'maya_tools').mkdir(parents=True)
    if version_line is not None:
        (root / 'VERSION.txt').write_text(
            version_line + '\nBuilt from: HEAD\nPackaged: 2026-07-22T11:14:27\n',
            encoding='utf-8')
    return str(dest)


def test_read_installed_version_parses_the_package_release_stamp():
    import Fabricator_Install as FI
    dest = _make_install_tree('Fabricator 1.0.1')
    root = FI.install_root_for(dest)
    assert FI.read_installed_version(root) == '1.0.1'


def test_read_installed_version_is_empty_when_absent_or_malformed():
    import Fabricator_Install as FI
    # No VERSION.txt at all (dev checkout) — must not raise.
    dest = _make_install_tree(None)
    assert FI.read_installed_version(FI.install_root_for(dest)) == ''
    # Present but not the expected shape.
    dest2 = _make_install_tree('some other header')
    assert FI.read_installed_version(FI.install_root_for(dest2)) == ''
    # A path that does not exist at all.
    assert FI.read_installed_version(str(Path(tempfile.mkdtemp()) / 'nope')) == ''


def test_detect_existing_install_keys_off_maya_tools_not_version_txt():
    import Fabricator_Install as FI
    # Empty dir: first install.
    assert FI.detect_existing_install(tempfile.mkdtemp()) == (False, '')
    # Installed WITH a version stamp.
    assert FI.detect_existing_install(
        _make_install_tree('Fabricator 1.0.1')) == (True, '1.0.1')
    # Installed WITHOUT a stamp: still an update, version unknown. This is
    # the case that must not report False — install_payload wipes
    # maya_tools/ regardless of whether VERSION.txt was ever written.
    assert FI.detect_existing_install(_make_install_tree(None)) == (True, '')


def test_update_copy_names_both_versions_and_switches_the_verb():
    import Fabricator_Install as FI
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    dest = _make_install_tree('Fabricator 1.0.1')
    # Stand in for the payload beside the installer, so _payload_version()
    # reads 1.1.0 without needing a real Fabricator_Data tree.
    installer_dir = Path(tempfile.mkdtemp())
    payload = installer_dir / FI._PAYLOAD_DIR_NAME
    payload.mkdir()
    (payload / 'VERSION.txt').write_text('Fabricator 1.1.0\n', encoding='utf-8')

    host = QtWidgets.QWidget()   # named, so GC cannot delete the children
    dlg = FI.FabricatorInstallerUI(str(installer_dir), parent=host)
    dlg._dir_edit.setText(dest)

    body = dlg._body.text()
    assert '1.0.1' in body and '1.1.0' in body, body
    assert 'project configs are kept' in body, body
    assert 'Restart Maya' in body, body
    assert dlg._install_button.text() == 'Update'
    assert dlg._is_update is True

    # Point somewhere empty: back to first-install copy.
    dlg._dir_edit.setText(tempfile.mkdtemp())
    assert dlg._install_button.text() == 'Install'
    assert dlg._is_update is False
    assert 'No admin' in dlg._body.text()

    # Linked mode replaces nothing, so it must never claim an update even
    # when the directory does hold an install.
    dlg._dir_edit.setText(dest)
    assert dlg._is_update is True
    dlg._mode_linked.setChecked(True)
    assert dlg._is_update is False, 'linked mode wires, it does not replace'
    assert dlg._install_button.text() == 'Install'
    dlg.deleteLater()


def test_same_version_reinstall_says_so_rather_than_claiming_an_update():
    import Fabricator_Install as FI
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    dest = _make_install_tree('Fabricator 1.1.0')
    installer_dir = Path(tempfile.mkdtemp())
    payload = installer_dir / FI._PAYLOAD_DIR_NAME
    payload.mkdir()
    (payload / 'VERSION.txt').write_text('Fabricator 1.1.0\n', encoding='utf-8')

    host = QtWidgets.QWidget()   # named, so GC cannot delete the children
    dlg = FI.FabricatorInstallerUI(str(installer_dir), parent=host)
    dlg._dir_edit.setText(dest)
    body = dlg._body.text()
    assert 'already installed' in body, body
    assert 'same version' in body, body
    dlg.deleteLater()


# ─── add-on pack survival across a core update ──────────────────────────
# Regression for the 2026-07-25 field report (first outside Ribbon Pack
# install): install_payload's rmtree(maya_tools) wipe deleted every pack
# overlay file on a core update, silently uninstalling the Ribbon Pack.

def _pack_update_fixture(with_manifest):
    """An installed tree carrying pack overlay files (+ optional in-tree
    pack manifest at the install root), and a fresh payload dir that does
    NOT ship those files. Returns (root, payload_dir, pack_rels)."""
    import json as _json
    import Fabricator_Install as FI

    root = Path(FI.install_root_for(tempfile.mkdtemp()))
    mt = root / 'maya_tools'
    (mt / 'rigging' / 'fabricator' / 'modules').mkdir(parents=True)
    (mt / 'rigging' / 'fabricator' / 'templates').mkdir(parents=True)
    (mt / 'rigging' / 'fabricator' / 'fs_app.py').write_text('OLD CORE\n')
    pack_rels = [
        'maya_tools/rigging/fabricator/modules/ribbon_spine.py',
        'maya_tools/rigging/fabricator/modules/_ribbon_common.py',
        'maya_tools/rigging/fabricator/templates/Advanced_Biped.blueprint.yaml',
    ]
    for rel in pack_rels:
        p = root / Path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'PACK CONTENT {rel}\n')
    if with_manifest:
        (root / 'ribbon_pack_manifest.json').write_text(
            _json.dumps({'version': '1.0', 'pack_version': '1.0.1',
                         'files': pack_rels}), encoding='utf-8')

    payload = Path(tempfile.mkdtemp()) / FI._PAYLOAD_DIR_NAME
    (payload / 'maya_tools' / 'rigging' / 'fabricator').mkdir(parents=True)
    (payload / 'maya_tools' / 'rigging' / 'fabricator'
     / 'fs_app.py').write_text('NEW CORE\n')
    return root, payload, pack_rels


def test_core_update_preserves_pack_files_listed_by_in_tree_manifest():
    import Fabricator_Install as FI
    root, payload, pack_rels = _pack_update_fixture(with_manifest=True)
    FI.install_payload(str(root), str(root / 'icons'), str(payload))
    core = (root / 'maya_tools' / 'rigging' / 'fabricator' / 'fs_app.py')
    assert core.read_text() == 'NEW CORE\n', 'payload did not land'
    for rel in pack_rels:
        p = root / Path(rel)
        assert p.is_file(), f'pack file wiped by update: {rel}'
        assert p.read_text() == f'PACK CONTENT {rel}\n', f'corrupted: {rel}'
    assert (root / 'ribbon_pack_manifest.json').is_file(), \
        'in-tree manifest lost'


def test_core_update_preserves_legacy_ribbon_files_without_manifest():
    """Field installs made by RibbonPack 1.0.0 have no in-tree manifest;
    the known ribbon overlay paths must survive by pattern fallback."""
    import Fabricator_Install as FI
    root, payload, pack_rels = _pack_update_fixture(with_manifest=False)
    FI.install_payload(str(root), str(root / 'icons'), str(payload))
    for rel in pack_rels:
        assert (root / Path(rel)).is_file(), \
            f'legacy pack file wiped by update: {rel}'


def test_core_update_payload_wins_over_preserved_pack_file():
    """If a future free payload ships a path the pack also carried, the
    payload's copy must win (the preserved pack copy is stale)."""
    import Fabricator_Install as FI
    root, payload, pack_rels = _pack_update_fixture(with_manifest=True)
    promoted = Path(pack_rels[0])
    dest = payload / promoted
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text('PROMOTED TO FREE\n')
    FI.install_payload(str(root), str(root / 'icons'), str(payload))
    assert (root / promoted).read_text() == 'PROMOTED TO FREE\n', \
        'stale pack copy clobbered the payload file'


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'Running {len(tests)} tests from test_installer_logic.py...')
    for name, fn in tests:
        check(name, fn)
    print(f'\n{len(tests) - len(FAILURES)} passed, {len(FAILURES)} failed '
          f'(of {len(tests)})')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
