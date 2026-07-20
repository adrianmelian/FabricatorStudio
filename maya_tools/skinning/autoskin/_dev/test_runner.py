"""AutoSkin runner (Maya-side) tests. Run:
PYTHONIOENCODING=utf-8 py -3 maya_tools/skinning/autoskin/_dev/test_runner.py

Pure Python — no Maya, no venv, no GPU. runner.py's job is the SUBPROCESS
CONTRACT: gate on health, launch the backend, stream its progress, and
turn whatever comes back into either a result or a loud, honest error.
Those are exactly the paths a real generation is too slow (and too
GPU-bound) to exercise, so the child process is faked here.

The pipeline's numerical core is tested separately, inside the venv
(test_runner_backend.py), and end-to-end against a real character live.
"""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from maya_tools.skinning.autoskin import backend, runner

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


class FakeProc:
    """Stands in for the backend subprocess."""

    def __init__(self, stdout_lines, returncode=0, stderr=''):
        self.stdout = iter(stdout_lines)
        self.stderr = _FakeStderr(stderr)
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


class SilentProc:
    """A backend that says NOTHING and never exits — the real shape of the
    engine run.

    This is the process the old runner could not survive. `for line in
    proc.stdout` blocked in readline() forever, so Cancel was never polled, the
    UI was never pumped, and the timeout (which only guarded proc.wait(), AFTER
    the loop) could never fire. Maya hung with no escape.

    Nothing in the suite used to look like this: every fake handed the runner a
    tidy list of lines that ended. A test that never makes the child go quiet
    cannot discover that silence is fatal.
    """

    def __init__(self, hold=threading.Event()):
        self._hold = hold
        self.killed = False
        self.returncode = 0
        self.stderr = _FakeStderr('')
        self.stdout = self

    def __iter__(self):
        return self

    def __next__(self):
        # Block like a real readline() on a silent child. Released only by kill.
        while not self.killed:
            time.sleep(0.01)
        raise StopIteration

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


class _FakeStderr:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text


@contextlib.contextmanager
def fake_backend(stdout_lines, returncode=0, stderr='', ready=True,
                 weights_written=True):
    """Fake health, the subprocess, and the weights file it claims to write."""
    real_health = backend.health
    real_popen = subprocess.Popen
    backend.health = lambda: (
        {'ready': True, 'reason': ''} if ready else
        {'ready': False, 'reason': 'AutoSkin backend is not installed.'})

    with tempfile.TemporaryDirectory() as td:
        in_fbx = Path(td) / 'in.fbx'
        in_fbx.write_text('fake', encoding='utf-8')
        out_json = Path(td) / 'weights.json'
        probe = Path(td) / 'probe.f32'
        probe.write_bytes(b'\x00' * 12)

        # Let the result line point at weights that do (or do not) exist.
        lines = [l.replace('__OUT__', str(out_json).replace('\\', '\\\\'))
                 for l in stdout_lines]
        if weights_written:
            out_json.write_text('{}', encoding='utf-8')

        runner.subprocess.Popen = lambda *a, **k: FakeProc(lines, returncode, stderr)
        try:
            yield in_fbx, out_json, probe
        finally:
            backend.health = real_health
            runner.subprocess.Popen = real_popen


OK_RESULT = json.dumps({'ok': True, 'weights_json': '__OUT__', 'joints': 80,
                        'verts': 7559, 'influences': 79,
                        'fidelity': {'residual': 8e-8, 'radius': 91.4}})


def test_happy_path_returns_the_backends_result():
    lines = ['[AUTOSKIN] stage: prep',
             '[AUTOSKIN] coverage: 80 joint(s) covered',
             '[AUTOSKIN] stage: done',
             OK_RESULT]
    with fake_backend(lines) as (in_fbx, out_fbx, probe):
        r = runner.run(in_fbx, out_fbx, probe)
    assert r['ok'] and r['joints'] == 80 and r['verts'] == 7559, r


def test_progress_is_streamed_stage_by_stage():
    """The UI must show real stages — a spinner that lies is worse than
    no spinner."""
    lines = ['[AUTOSKIN] stage: prep',
             '[AUTOSKIN] coverage: 80 joint(s) covered',
             '[AUTOSKIN] stage: generating',
             '[AUTOSKIN] stage: done',
             OK_RESULT]
    seen = []
    with fake_backend(lines) as (in_fbx, out_fbx, probe):
        runner.run(in_fbx, out_fbx, probe, progress=lambda s, m: seen.append((s, m)))
    assert ('stage', 'prep') in seen
    assert ('stage', 'generating') in seen
    assert ('coverage', '80 joint(s) covered') in seen


def test_unhealthy_backend_refuses_before_launching_anything():
    with fake_backend([OK_RESULT], ready=False) as (in_fbx, out_fbx, probe):
        try:
            runner.run(in_fbx, out_fbx, probe)
        except runner.RunnerError as exc:
            assert 'not installed' in str(exc)
        else:
            raise AssertionError('ran against an unhealthy backend')


def test_missing_input_is_named():
    with fake_backend([OK_RESULT]) as (in_fbx, out_fbx, probe):
        try:
            runner.run(Path(in_fbx).with_name('nope.fbx'), out_fbx, probe)
        except runner.RunnerError as exc:
            assert 'not found' in str(exc)
        else:
            raise AssertionError('accepted a missing input')


def test_backend_error_surfaces_its_real_reason():
    """A structured failure must reach the user verbatim — never
    'subprocess failed'."""
    err = json.dumps({'ok': False,
                      'error': 'no skeleton in the input FBX. Select bind '
                               'joints along with the mesh.'})
    with fake_backend(['[AUTOSKIN] stage: prep', err], returncode=1) as (i, o, probe):
        try:
            runner.run(i, o, probe)
        except runner.RunnerError as exc:
            assert 'no skeleton in the input FBX' in str(exc), exc
        else:
            raise AssertionError('a backend error was swallowed')


def test_crash_without_a_result_line_still_reports_the_tail():
    """A hard crash (segfault, CUDA OOM) prints no JSON at all. The user
    still gets the last thing the process actually said."""
    with fake_backend(['[AUTOSKIN] stage: generating'], returncode=-1073741819,
                      stderr='CUDA out of memory. Tried to allocate 2.00 GiB'
                      ) as (i, o, probe):
        try:
            runner.run(i, o, probe)
        except runner.RunnerError as exc:
            assert 'CUDA out of memory' in str(exc), exc
        else:
            raise AssertionError('a crash was reported as success')


def test_success_without_weights_on_disk_is_not_success():
    """Trust the disk, not the claim."""
    with fake_backend([OK_RESULT], weights_written=False) as (i, o, probe):
        try:
            runner.run(i, o, probe)
        except runner.RunnerError as exc:
            assert 'wrote no weights' in str(exc), exc
        else:
            raise AssertionError('accepted a weights file that does not exist')


def test_a_missing_probe_is_named():
    """Without the probe the backend cannot prove the vertex order, and the
    weights it returns would be indices nobody vouched for."""
    with fake_backend([OK_RESULT]) as (i, o, probe):
        Path(probe).unlink()
        try:
            runner.run(i, o, probe)
        except runner.RunnerError as exc:
            assert 'probe not found' in str(exc), exc
        else:
            raise AssertionError('ran without a vertex probe')


@contextlib.contextmanager
def silent_backend():
    """The engine run, faithfully: a child that prints nothing and does not
    exit until it is killed."""
    real_health = backend.health
    real_popen = subprocess.Popen
    backend.health = lambda: {'ready': True, 'reason': ''}
    proc = SilentProc()
    with tempfile.TemporaryDirectory() as td:
        in_fbx = Path(td) / 'in.fbx'
        in_fbx.write_text('fake', encoding='utf-8')
        out_json = Path(td) / 'weights.json'
        probe = Path(td) / 'probe.f32'
        probe.write_bytes(b'\x00' * 12)
        runner.subprocess.Popen = lambda *a, **k: proc
        try:
            yield in_fbx, out_json, probe, proc
        finally:
            proc.kill()
            backend.health = real_health
            runner.subprocess.Popen = real_popen


def test_cancel_works_while_the_backend_is_SILENT():
    """BLOCKER REGRESSION (2026-07-13). The backend prints nothing for the whole
    ~100s engine run (demo.py is launched with capture_output=True). Cancel has
    to work through that silence — it is the ONLY window Cancel exists for."""
    with silent_backend() as (i, o, probe, proc):
        # The artist clicks Cancel a moment after the run starts.
        cancelled = {'v': False}
        threading.Timer(0.4, lambda: cancelled.update(v=True)).start()

        t0 = time.monotonic()
        try:
            runner.run(i, o, probe, cancel_check=lambda: cancelled['v'])
        except runner.RunnerCancelled:
            pass
        else:
            raise AssertionError('a silent backend swallowed the cancel')
        took = time.monotonic() - t0

    assert proc.killed, 'the backend process was not killed'
    assert took < 5.0, f'cancel took {took:.1f}s — it is not being polled'


def test_the_ui_is_pumped_while_the_backend_is_SILENT():
    """Maya froze ('Not Responding') for the entire generation because the
    progress callback — which is what pumps Qt's event loop — only ran when the
    child printed. It must now tick on a wall clock."""
    with silent_backend() as (i, o, probe, proc):
        ticks = []
        threading.Timer(1.0, proc.kill).start()
        try:
            runner.run(i, o, probe,
                       progress=lambda s, m: ticks.append(s),
                       cancel_check=lambda: False)
        except runner.RunnerError:
            pass        # no result line; we only care that it ticked

    assert len(ticks) >= 3, (
        f'only {len(ticks)} progress tick(s) during a 1s silent run — the UI '
        f'would be frozen and Cancel unclickable')


def test_timeout_fires_on_a_backend_that_hangs_holding_stdout_open():
    """The documented `timeout` could never fire: it guarded proc.wait(), which
    is reached only AFTER stdout hits EOF. A child that hangs while holding
    stdout open hung Maya forever."""
    with silent_backend() as (i, o, probe, proc):
        t0 = time.monotonic()
        try:
            runner.run(i, o, probe, timeout=1)
        except runner.RunnerError as exc:
            assert 'timed out' in str(exc), exc
        else:
            raise AssertionError('a hung backend was never timed out')
        took = time.monotonic() - t0

    assert proc.killed, 'the hung backend was not killed'
    assert took < 6.0, f'timeout took {took:.1f}s to fire'


def test_a_progress_callback_that_throws_never_kills_a_generation():
    """A UI hiccup must not destroy a 100-second GPU run."""
    lines = ['[AUTOSKIN] stage: prep', OK_RESULT]

    def boom(stage, msg):
        raise RuntimeError('UI exploded')

    with fake_backend(lines) as (i, o, probe):
        r = runner.run(i, o, probe, progress=boom)
    assert r['ok']


check('happy path returns the backend result', test_happy_path_returns_the_backends_result)
check('progress is streamed stage by stage', test_progress_is_streamed_stage_by_stage)
check('unhealthy backend refuses early', test_unhealthy_backend_refuses_before_launching_anything)
check('missing input is named', test_missing_input_is_named)
check('backend error surfaces its real reason', test_backend_error_surfaces_its_real_reason)
check('crash without a result still reports the tail', test_crash_without_a_result_line_still_reports_the_tail)
check('success without weights on disk is not success', test_success_without_weights_on_disk_is_not_success)
check('a missing probe is named', test_a_missing_probe_is_named)
check('BLOCKER cancel works while the backend is SILENT',
      test_cancel_works_while_the_backend_is_SILENT)
check('BLOCKER the UI is pumped while the backend is SILENT',
      test_the_ui_is_pumped_while_the_backend_is_SILENT)
check('BLOCKER timeout fires on a backend that hangs holding stdout open',
      test_timeout_fires_on_a_backend_that_hangs_holding_stdout_open)
check('a throwing progress callback never kills a run',
      test_a_progress_callback_that_throws_never_kills_a_generation)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print(f'ALL PASS ({len(PASSED)})')
