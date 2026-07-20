"""AutoSkin installer tests. Run:
PYTHONIOENCODING=utf-8 py -3 maya_tools/skinning/autoskin/_dev/test_installer.py

Pure Python — no Maya, no GPU, no network. Every step is faked, because
the properties worth testing are ORCHESTRATION properties (does a re-run
skip completed work? does a half-finished install resume? does a lying
step get caught?) and those are exactly the ones a real 20-minute install
is too slow and too non-deterministic to prove.

The real install is verified separately, live, on Adrian's box — that is
the B0 checkpoint. This suite guards the logic around it.
"""
import contextlib
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from maya_tools.skinning.autoskin import backend, installer

FAILURES = []
PASSED = []


def check(name, fn):
    try:
        fn()
        PASSED.append(name)
        print(f'  ok: {name}')
    except Exception as exc:
        import traceback
        FAILURES.append(f'{name}: {exc!r}')
        print(f'FAIL: {name}: {exc!r}')
        traceback.print_exc()


@contextlib.contextmanager
def temp_root():
    old = os.environ.get('FABRICATOR_AUTOSKIN_ROOT')
    with tempfile.TemporaryDirectory() as td:
        os.environ['FABRICATOR_AUTOSKIN_ROOT'] = td
        try:
            yield Path(td)
        finally:
            if old is None:
                os.environ.pop('FABRICATOR_AUTOSKIN_ROOT', None)
            else:
                os.environ['FABRICATOR_AUTOSKIN_ROOT'] = old


@contextlib.contextmanager
def fake_steps(done_flags, smoke_ok=True, gpu_ok=True, lying_step=None):
    """Replace STEPS/smoke/gpu with fakes.

    done_flags: dict {label: bool} — the INITIAL done state of each step.
    A step's do() flips its own flag to True, so is_done() reflects work
    actually happening (that is what makes the lying-step test possible).
    lying_step: a label whose do() does NOT flip its flag — simulating a
    step that reports success but leaves nothing on disk.
    """
    real_steps = installer.STEPS
    real_smoke = installer.smoke
    real_gpu = backend.gpu_capability
    state = dict(done_flags)
    ran = []

    def make(label):
        def is_done():
            return state[label]

        def do():
            ran.append(label)
            if label != lying_step:
                state[label] = True
        return (label, is_done, do)

    installer.STEPS = tuple(make(l) for l in done_flags)
    installer.smoke = lambda: {'ok': smoke_ok,
                               'detail': 'faked' if smoke_ok else 'faked failure'}
    backend.gpu_capability = lambda: (
        {'ok': True, 'name': 'FakeGPU', 'vram_gb': 16.0, 'reason': ''}
        if gpu_ok else
        {'ok': False, 'name': '', 'vram_gb': 0.0, 'reason': 'No NVIDIA GPU detected.'})
    try:
        yield ran
    finally:
        installer.STEPS = real_steps
        installer.smoke = real_smoke
        backend.gpu_capability = real_gpu


ALL_UNDONE = {'Fetching engine': False, 'Building environment': False,
              'Installing PyTorch + dependencies': False,
              'Downloading models': False}
ALL_DONE = {k: True for k in ALL_UNDONE}


def test_fresh_install_runs_every_step():
    with temp_root():
        with fake_steps(ALL_UNDONE) as ran:
            r = installer.install()
    assert r['ok'], r
    assert ran == list(ALL_UNDONE), ran
    assert r['steps_skipped'] == []


def test_second_run_is_idempotent_and_does_no_work():
    """Running the installer twice must be cheap and safe — it re-checks
    and skips, it does not rebuild a 7 GB venv."""
    with temp_root():
        with fake_steps(ALL_DONE) as ran:
            r = installer.install()
    assert r['ok']
    assert ran == [], f'idempotent run did work: {ran}'
    assert len(r['steps_skipped']) == 4


def test_interrupted_install_resumes_only_what_is_missing():
    """The 1.6 GB download died. A re-run must fetch ONLY the models —
    not re-clone, not rebuild the venv."""
    partial = dict(ALL_DONE)
    partial['Downloading models'] = False
    with temp_root():
        with fake_steps(partial) as ran:
            r = installer.install()
    assert r['ok']
    assert ran == ['Downloading models'], ran
    assert len(r['steps_skipped']) == 3


def test_no_gpu_fails_before_downloading_anything():
    """Refuse early. Never make a user wait through a 7 GB install to
    learn their machine cannot run it."""
    with temp_root():
        with fake_steps(ALL_UNDONE, gpu_ok=False) as ran:
            try:
                installer.install()
            except installer.InstallError as exc:
                assert 'NVIDIA' in str(exc)
            else:
                raise AssertionError('expected InstallError on a GPU-less box')
    assert ran == [], 'work was done despite no GPU'


def test_a_step_that_lies_about_success_is_caught():
    """A step whose do() returns cleanly but leaves nothing on disk must
    fail the install loudly — not be recorded as done."""
    with temp_root():
        with fake_steps(ALL_UNDONE, lying_step='Downloading models'):
            try:
                installer.install()
            except installer.InstallError as exc:
                assert 'not on disk' in str(exc), exc
            else:
                raise AssertionError('a lying step slipped through')


def test_failed_smoke_reports_rather_than_raises():
    """The bytes ARE on disk; the user needs the reason, not a traceback.
    ok=False, and state.json records the failure honestly."""
    with temp_root():
        with fake_steps(ALL_UNDONE, smoke_ok=False):
            r = installer.install()
        # read_state() must be INSIDE temp_root — outside it, the env
        # override is gone and this would read the REAL install.
        state = backend.read_state()
    assert r['ok'] is False
    assert r['smoke']['ok'] is False
    assert state['smoke'] == 'fail', state


def test_success_records_the_pin_it_installed():
    with temp_root():
        with fake_steps(ALL_UNDONE):
            installer.install()
        st = backend.read_state()
    assert st['pin'] == backend.PIN_TAG
    assert st['smoke'] == 'pass'


def test_progress_reports_the_whole_plan_including_skipped_steps():
    """The UI must show the full plan — a mostly-complete install that
    reports '1 step' looks broken."""
    seen = []
    with temp_root():
        with fake_steps(ALL_DONE):
            installer.install(progress=lambda i, n, label: seen.append((n, label)))
    assert len(seen) == 5, seen              # 4 steps + smoke
    assert all(n == 5 for n, _ in seen), seen
    assert seen[-1][1] == 'Smoke test'


def test_uninstall_removes_what_we_own_and_spares_what_we_do_not():
    """A stranger's file in the backend root must survive an uninstall —
    FABRICATOR_AUTOSKIN_ROOT can point at a directory the user chose, and
    a blind rmtree of a user-supplied path is how tools eat someone's
    work."""
    with temp_root() as td:
        (td / 'repo').mkdir()
        (td / 'repo' / 'demo.py').write_text('x', encoding='utf-8')
        (td / 'venv').mkdir()
        backend.write_state(pin='x')
        stranger = td / 'do_not_touch.txt'
        stranger.write_text('mine', encoding='utf-8')

        r = installer.uninstall()

        assert not (td / 'repo').exists()
        assert not (td / 'venv').exists()
        assert not backend.state_path().exists()
        assert 'repo' in r['removed'] and 'venv' in r['removed']
        assert stranger.is_file(), 'uninstall ate a file it did not own'
        assert td.is_dir(), 'root removed while it still held a stranger file'


def test_uninstall_then_install_is_a_clean_cycle():
    """The B0 contract: uninstall leaves nothing behind, and a following
    install runs every step again from zero."""
    with temp_root():
        with fake_steps(ALL_DONE) as ran:
            installer.install()
        assert ran == []                       # nothing to do while installed
        installer.uninstall()
        assert not backend.install_state()['installed']
        with fake_steps(ALL_UNDONE) as ran2:
            r = installer.install()
        assert ran2 == list(ALL_UNDONE), ran2   # full rebuild
        assert r['ok']


# ── code-only security auto-update (2026-07-13) ──────────────────────────

@contextlib.contextmanager
def fake_install_state(installed, pin_matches):
    real = backend.install_state
    backend.install_state = lambda: {
        'installed': installed, 'missing': [] if installed else ['inference env'],
        'repo': installed, 'venv': installed, 'models': installed,
        'pin': backend.PIN_TAG if pin_matches else 'autoskin-old',
        'pin_matches': pin_matches,
    }
    try:
        yield
    finally:
        backend.install_state = real


def test_update_available_only_when_installed_and_pin_behind():
    """The gate is precise: fully installed AND behind the pin. That is the
    code-only case (a git checkout), the shape a security patch takes."""
    with fake_install_state(installed=True, pin_matches=False):
        assert installer.repo_update_available() is True
    with fake_install_state(installed=True, pin_matches=True):
        assert installer.repo_update_available() is False, 'current pin: nothing to do'
    with fake_install_state(installed=False, pin_matches=False):
        assert installer.repo_update_available() is False, \
            'a not-fully-installed backend must go to the GATED installer, ' \
            'never silently auto-fetch (it may need the venv or GB of models)'


def test_apply_update_checks_out_and_records_the_pin():
    """The happy path: fetch+checkout (bounded), then state.json records the
    pin so health() reads current. do_repo is faked — the git itself is the
    installer's, proven live, not this suite's job."""
    with temp_root():
        backend.write_state(pin='autoskin-old')
        calls = []
        real = installer.do_repo
        installer.do_repo = lambda timeout=None: calls.append(timeout)
        try:
            res = installer.apply_repo_update()
        finally:
            installer.do_repo = real
        assert res['ok'], res
        assert calls == [120], f'a bounded timeout must guard window-open: {calls}'
        assert backend.read_state()['pin'] == backend.PIN_TAG


def test_apply_update_defers_on_failure_and_leaves_state_untouched():
    """Offline / stalled fetch must NOT crash the tool and must NOT half-write
    a pin it didn't actually check out — the caller falls back to the gated
    prompt, and state still tells the truth."""
    with temp_root():
        backend.write_state(pin='autoskin-old')
        real = installer.do_repo

        def boom(timeout=None):
            raise installer.InstallError('could not reach origin')

        installer.do_repo = boom
        try:
            res = installer.apply_repo_update()
        finally:
            installer.do_repo = real
        assert not res['ok'], res
        assert 'deferred' in res['detail'], res
        assert backend.read_state()['pin'] == 'autoskin-old', \
            'a failed update must not advance the recorded pin'


check('update available only when installed and pin behind',
      test_update_available_only_when_installed_and_pin_behind)
check('apply update checks out and records the pin',
      test_apply_update_checks_out_and_records_the_pin)
check('apply update defers on failure, state untouched',
      test_apply_update_defers_on_failure_and_leaves_state_untouched)

check('fresh install runs every step', test_fresh_install_runs_every_step)
check('second run is idempotent (no work)', test_second_run_is_idempotent_and_does_no_work)
check('interrupted install resumes only what is missing',
      test_interrupted_install_resumes_only_what_is_missing)
check('no GPU fails before downloading anything', test_no_gpu_fails_before_downloading_anything)
check('a step that lies about success is caught', test_a_step_that_lies_about_success_is_caught)
check('failed smoke reports rather than raises', test_failed_smoke_reports_rather_than_raises)
check('success records the pin it installed', test_success_records_the_pin_it_installed)
check('progress reports the whole plan', test_progress_reports_the_whole_plan_including_skipped_steps)
check('uninstall spares what it does not own',
      test_uninstall_removes_what_we_own_and_spares_what_we_do_not)
check('uninstall -> install is a clean cycle', test_uninstall_then_install_is_a_clean_cycle)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print(f'ALL PASS ({len(PASSED)})')
