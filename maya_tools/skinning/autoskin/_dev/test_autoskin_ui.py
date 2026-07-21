"""AutoSkin UI tests (offscreen). Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen PYTHONIOENCODING=utf-8 \
  "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" \
  maya_tools/skinning/autoskin/_dev/test_autoskin_ui.py

Bare mayapy, no maya.standalone — the house offscreen-Qt idiom.

The UI's job is to tell the truth about the backend and to keep the
artist's hands on one button. So that is what is tested: the gating
states (ready / no GPU / needs setup), the one-button-one-checkbox
contract, and that a failure reaches the log instead of a traceback in
the Script Editor.
"""
import contextlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

# NO maya.standalone.initialize() — the house offscreen-Qt idiom (see
# _dev/test_canvas_limb_units_ui.py). A live Maya session plus Qt widget
# construction in mayapy is a hard crash (exit 127); bare mayapy is fine.
# Nothing here needs cmds: the window is given an explicit parent, and
# backend.health is faked, so no Maya call is ever reached.
from PySide6 import QtWidgets  # noqa: E402

# NOTE: visibility is asserted with isHidden(), not isVisible(). A child of
# a window that was never show()n reports isVisible() == False no matter
# what setVisible() said; isHidden() reflects the explicit state, which is
# the thing under test.

from maya_tools.skinning.autoskin import autoskin_ui as ui  # noqa: E402
from maya_tools.skinning.autoskin import backend  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

# get_maya_window() returns None in mayapy (there is no Maya GUI to parent
# to), so give the window a real parent instead of letting it look one up.
_PARENT = QtWidgets.QWidget()


class FakeLogger:
    """LoggerWidget mirrors every message to Maya's Script Editor through
    MGlobal, which SEGFAULTS in a bare mayapy with no Maya session. Swap it
    for a stub — which also lets the tests assert what the artist is
    actually told, not merely that nothing exploded."""

    def __init__(self):
        self.lines = []

    def info(self, m): self.lines.append(('info', m))
    def warning(self, m): self.lines.append(('warning', m))
    def error(self, m): self.lines.append(('error', m))
    def success(self, m): self.lines.append(('success', m))
    def clear(self): self.lines.clear()

    def text_of(self, kind):
        return ' '.join(m for k, m in self.lines if k == kind)


def make_window():
    w = ui.AutoSkinWindow(parent=_PARENT)
    w.logger = FakeLogger()
    return w

FAILURES = []


def check(name, fn):
    try:
        fn()
        print(f'  ok: {name}')
    except Exception as exc:
        import traceback
        FAILURES.append(f'{name}: {exc!r}')
        print(f'FAIL: {name}: {exc!r}')
        traceback.print_exc()


_REAL_HEALTH = backend.health


@contextlib.contextmanager
def health(ready=True, gpu_ok=True, installed=True, reason=''):
    backend.health = lambda: {
        'ready': ready, 'reason': reason,
        'gpu': {'ok': gpu_ok, 'name': 'FakeGPU', 'vram_gb': 16.0,
                'reason': '' if gpu_ok else 'No NVIDIA GPU detected.'},
        'install': {'installed': installed, 'missing': [],
                    'pin': backend.PIN_TAG, 'pin_matches': True},
    }
    try:
        yield
    finally:
        backend.health = _REAL_HEALTH


def test_ready_backend_enables_the_one_button():
    with health(ready=True):
        w = make_window()
    assert w.bind_btn.isEnabled()
    assert w.install_btn.isHidden()
    assert w.bind_btn.text().upper() == 'BIND SKIN'


def test_no_gpu_says_so_in_words_and_offers_no_install():
    """A missing GPU is a FACT, not a disabled button with no explanation.
    And no install can fix it, so do not offer one."""
    with health(ready=False, gpu_ok=False, installed=False,
                reason='No NVIDIA GPU detected. AutoSkin needs an NVIDIA GPU '
                       'with at least 6 GB of VRAM.'):
        w = make_window()
    assert not w.bind_btn.isEnabled()
    assert 'NVIDIA' in w.hint.text()
    assert w.install_btn.isHidden(), 'offered an install that cannot help'


def test_missing_backend_offers_the_install():
    with health(ready=False, gpu_ok=True, installed=False,
                reason='AutoSkin backend is not installed. Run Install Backend.'):
        w = make_window()
    assert not w.bind_btn.isEnabled()
    assert not w.install_btn.isHidden(), 'a fixable setup offered no fix'


def test_generate_joints_reveals_its_honest_caveat():
    """Generated joints are machine-placed and generically named. The
    artist is told BEFORE pressing, not after."""
    with health():
        w = make_window()
    assert w.generate_note.isHidden()
    w.generate_cb.setChecked(True)
    assert not w.generate_note.isHidden()
    note = w.generate_note.text()
    assert 'bone_00' in note and 'machine-placed' in note
    assert 'only your mesh' in w.hint.text()


def test_progress_is_hidden_until_something_runs():
    with health():
        w = make_window()
    assert w.progress_box.isHidden()
    w._begin('starting')
    assert not w.progress_box.isHidden()
    assert not w.bind_btn.isEnabled(), 'bind clickable while already running'
    w._end()
    assert w.progress_box.isHidden()


def test_stages_advance_the_bar_in_the_artists_words():
    with health():
        w = make_window()
    w._begin('starting')
    # This is the shape the backend ACTUALLY emits: top-level transitions
    # arrive as ('stage', '<name>'), with the name in the message. Feeding
    # _pump the tidy ('generating', '') shape is what let a broken progress
    # bar pass its own test.
    w._pump('stage', 'generating')
    assert 'generating weights' in w.stage_label.text(), w.stage_label.text()
    first = w.bar.value()
    assert first > 0, 'the bar never left zero on a real backend stage'
    w._pump('applying', '')
    assert w.bar.value() > first, 'the bar did not advance'
    assert 'applying weights' in w.stage_label.text()
    w._end()


def test_cancel_flag_is_what_the_runner_polls():
    with health():
        w = make_window()
    w._begin('starting')
    assert w._cancelled is False
    w._on_cancel()
    assert w._cancelled is True
    assert not w.cancel_btn.isEnabled(), 'cancel stayed clickable'
    w._end()


def test_an_expected_failure_lands_in_the_log_not_a_traceback():
    """AutoSkinError is a message for the artist, not a crash."""
    from maya_tools.skinning.autoskin import autoskin_app as app
    real = app.bind_skin
    app.bind_skin = lambda **kw: (_ for _ in ()).throw(
        app.AutoSkinError('Select your bind joints along with the mesh.'))
    try:
        with health():
            w = make_window()
            # _on_bind MUST run inside the health mock: _end() re-enables the
            # button off a fresh backend.health() call, so a bind outside the
            # context reads the REAL install state and makes this test pass or
            # fail on the machine's actual backend rather than on the code path
            # under test. (Bit us 2026-07-13 after a pin bump left the real
            # install reading not-ready.)
            w._on_bind()
    finally:
        app.bind_skin = real
    assert not w._running, 'the window stayed stuck in a running state'
    assert w.progress_box.isHidden()
    assert w.bind_btn.isEnabled(), 'the button never came back'
    assert 'bind joints' in w.logger.text_of('error'), (
        'the reason never reached the artist: ' + str(w.logger.lines))


def test_every_stage_the_backend_emits_has_words_on_the_bar():
    """The contract between what the pipeline SAYS and what the artist READS.

    These are the exact (key, message) pairs a real bind emitted on
    2026-07-13. Anything the backend starts saying must be added here and
    given words.

    This is the test that catches the bug the tidy version missed: the
    backend announces top-level transitions as ('stage', '<name>'), so a
    UI that reads only the key shows '// stage' and leaves the progress
    bar pinned at zero for the entire run.
    """
    EMITTED = [
        ('exporting', '1 mesh(es)'),
        ('stage', 'prep'),
        ('coverage', '80 joint(s) covered'),
        ('prep', '-> prepped.glb'),
        ('stage', 'generating'),
        ('generating', '-> skinned.glb'),
        ('stage', 'harvesting'),
        ('harvest', 'engine mesh ...'),
        ('align', 'umeyama residual 0.0013'),
        ('bone-map', '80 bone(s) mapped'),
        ('match', '7559 vertex/vertices matched'),
        ('generate', '47 joint(s) generated'),
        ('applying', ''),
        ('stage', 'done'),
        ('done', ''),
    ]
    for key, msg in EMITTED:
        label, _ = ui.label_for(key, msg)
        assert label in ui._ORDER, (
            f'{key!r} (msg {msg!r}) -> {label!r}, which is not a stage on '
            f'the progress bar: the artist would see jargon beside a bar '
            f'that never moves')


check('a ready backend enables the one button', test_ready_backend_enables_the_one_button)
check('no GPU says so in words, offers no install',
      test_no_gpu_says_so_in_words_and_offers_no_install)
check('a missing backend offers the install', test_missing_backend_offers_the_install)
check('Generate Joints reveals its honest caveat',
      test_generate_joints_reveals_its_honest_caveat)
check('progress is hidden until something runs', test_progress_is_hidden_until_something_runs)
check('stages advance the bar in the artist\'s words',
      test_stages_advance_the_bar_in_the_artists_words)
check('cancel flag is what the runner polls', test_cancel_flag_is_what_the_runner_polls)
check('an expected failure lands in the log', test_an_expected_failure_lands_in_the_log_not_a_traceback)
def test_the_banner_actually_loads():
    """The brand bar falls back to a text label when the PNG is missing — which
    means a lost or misnamed asset looks EXACTLY like a working one from the
    outside, and would ship unnoticed. Assert the asset is really there, Qt can
    really decode it, and the window really took the pixmap branch."""
    from PySide6 import QtGui
    from maya_tools.framework.toolbar.widgets import icon_button

    path = icon_button._ICON_DIR / 'fs_autoskin.png'
    assert path.is_file(), f'the banner asset is missing: {path}'
    pm = QtGui.QPixmap(str(path))
    assert not pm.isNull(), f'{path} exists but Qt cannot decode it'

    # make_window(), not AutoSkinWindow(): this suite runs with NO
    # maya.standalone, so get_maya_window() returns None and the window has to
    # be handed a real parent (the house offscreen-Qt idiom, see the header).
    win = make_window()
    assert win.banner_shown,         'the window fell back to the text brand — the banner did not render'
    win.deleteLater()


check('the banner actually loads (no silent text fallback)',
      test_the_banner_actually_loads)
check('every backend stage has words on the bar',
      test_every_stage_the_backend_emits_has_words_on_the_bar)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print('ALL PASS (10)')
