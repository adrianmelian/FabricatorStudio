"""AutoSkin backend tests (paths / capability / health). Run:
PYTHONIOENCODING=utf-8 py -3 maya_tools/skinning/autoskin/_dev/test_backend.py

Pure Python — no Maya, no GPU required. The nvidia-smi probe is faked so
the gating logic is testable on any box (including a CI runner with no
GPU at all, which is exactly the state we must handle gracefully).

FABRICATOR_AUTOSKIN_ROOT is pointed at a temp dir per test, so nothing
here ever touches a real backend install.
"""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from maya_tools.skinning.autoskin import backend

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
def fake_nvidia_smi(stdout='', returncode=0, raises=None):
    """Swap subprocess.run so the GPU probe is deterministic."""
    real = subprocess.run

    def fake(*args, **kwargs):
        if raises is not None:
            raise raises
        class R:
            pass
        r = R()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = ''
        return r
    backend.subprocess.run = fake
    try:
        yield
    finally:
        backend.subprocess.run = real


def _install_fake_backend(root: Path, pin=backend.PIN_TAG,
                          repo=True, venv=True, models=True):
    """Lay down the exact files install_state() probes for."""
    if repo:
        (root / 'repo').mkdir(parents=True, exist_ok=True)
        (root / 'repo' / 'demo.py').write_text('# fake', encoding='utf-8')
    if venv:
        py = backend.venv_python()
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text('# fake', encoding='utf-8')
    if models:
        for c in backend.checkpoint_paths():
            c.parent.mkdir(parents=True, exist_ok=True)
            c.write_text('ckpt', encoding='utf-8')
        llm = backend.repo_dir() / backend.LLM_CONFIG_DIR
        llm.mkdir(parents=True, exist_ok=True)
        (llm / 'config.json').write_text('{}', encoding='utf-8')
    if pin is not None:
        backend.write_state(pin=pin)


# ── Paths ────────────────────────────────────────────────────────────────────

def test_root_honors_override():
    with temp_root() as td:
        assert backend.backend_root() == td
        assert backend.repo_dir() == td / 'repo'
        assert backend.venv_dir() == td / 'venv'
        # Checkpoints live INSIDE repo/ (upstream download.py hardcodes
        # local_dir='.') - not in a sibling models/ dir.
        assert backend.checkpoint_paths()[0].is_relative_to(td / 'repo')


def test_root_defaults_under_localappdata():
    old_override = os.environ.pop('FABRICATOR_AUTOSKIN_ROOT', None)
    old_local = os.environ.get('LOCALAPPDATA')
    os.environ['LOCALAPPDATA'] = r'C:\Users\test\AppData\Local'
    try:
        root = backend.backend_root()
        assert root.parts[-2:] == ('FabricatorStudio', 'autoskin'), root
    finally:
        if old_override is not None:
            os.environ['FABRICATOR_AUTOSKIN_ROOT'] = old_override
        if old_local is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = old_local


def test_root_survives_missing_localappdata():
    """A stripped environment must not raise — it must fall back."""
    old_override = os.environ.pop('FABRICATOR_AUTOSKIN_ROOT', None)
    old_local = os.environ.pop('LOCALAPPDATA', None)
    try:
        assert backend.backend_root()   # no raise
    finally:
        if old_override is not None:
            os.environ['FABRICATOR_AUTOSKIN_ROOT'] = old_override
        if old_local is not None:
            os.environ['LOCALAPPDATA'] = old_local


# ── State ────────────────────────────────────────────────────────────────────

def test_state_round_trip_and_corruption_reads_empty():
    with temp_root():
        assert backend.read_state() == {}          # absent
        backend.write_state(pin='x', smoke='pass')
        assert backend.read_state()['pin'] == 'x'
        backend.write_state(pin='y')               # merge, not clobber
        st = backend.read_state()
        assert st['pin'] == 'y' and st['smoke'] == 'pass'
        backend.state_path().write_text('{not json', encoding='utf-8')
        assert backend.read_state() == {}, 'corrupt state must read empty'


# ── Capability ───────────────────────────────────────────────────────────────

def test_gpu_ok_on_a_big_card():
    with fake_nvidia_smi(stdout='NVIDIA GeForce RTX 5070 Ti, 16384\n'):
        gpu = backend.gpu_capability()
    assert gpu['ok'], gpu
    assert gpu['name'] == 'NVIDIA GeForce RTX 5070 Ti'
    assert abs(gpu['vram_gb'] - 16.0) < 0.01, gpu
    assert gpu['reason'] == ''


def test_gpu_rejected_when_vram_too_small():
    with fake_nvidia_smi(stdout='NVIDIA GeForce GTX 1650, 4096\n'):
        gpu = backend.gpu_capability()
    assert not gpu['ok']
    assert '4.0 GB' in gpu['reason'] and '6 GB' in gpu['reason'], gpu['reason']


def test_no_nvidia_smi_is_a_clean_no_not_a_crash():
    with fake_nvidia_smi(raises=FileNotFoundError()):
        gpu = backend.gpu_capability()
    assert not gpu['ok'] and 'NVIDIA' in gpu['reason']


def test_nvidia_smi_failure_is_a_clean_no():
    with fake_nvidia_smi(stdout='', returncode=9):
        gpu = backend.gpu_capability()
    assert not gpu['ok'] and 'NVIDIA' in gpu['reason']


# ── Health ───────────────────────────────────────────────────────────────────

def test_health_ready_when_gpu_and_install_are_good():
    with temp_root() as td:
        _install_fake_backend(td)
        with fake_nvidia_smi(stdout='RTX 5070 Ti, 16384\n'):
            h = backend.health()
    assert h['ready'], h
    assert h['reason'] == ''


def test_health_reports_missing_pieces_by_name():
    with temp_root() as td:
        _install_fake_backend(td, venv=False, models=False)
        with fake_nvidia_smi(stdout='RTX 5070 Ti, 16384\n'):
            h = backend.health()
    assert not h['ready']
    assert 'inference environment' in h['reason']
    assert 'model checkpoints' in h['reason']
    assert 'SkinTokens repo' not in h['reason'], 'repo IS present'


def test_health_flags_a_stale_pin():
    with temp_root() as td:
        _install_fake_backend(td, pin='some-older-tag')
        with fake_nvidia_smi(stdout='RTX 5070 Ti, 16384\n'):
            h = backend.health()
    assert not h['ready']
    assert 'out of date' in h['reason'] and backend.PIN_TAG in h['reason']


def test_gpu_problem_outranks_install_problem():
    """No install can fix a missing GPU — say the useful thing first."""
    with temp_root():
        with fake_nvidia_smi(raises=FileNotFoundError()):
            h = backend.health()
    assert not h['ready']
    assert 'NVIDIA' in h['reason']
    assert 'not installed' not in h['reason']


def test_half_deleted_backend_reads_as_broken():
    """state.json says installed, but the venv is gone — disk wins."""
    with temp_root() as td:
        _install_fake_backend(td)
        backend.venv_python().unlink()
        inst = backend.install_state()
    assert not inst['installed']
    assert 'inference environment' in inst['missing']


check('root honors FABRICATOR_AUTOSKIN_ROOT', test_root_honors_override)
check('root defaults under LOCALAPPDATA', test_root_defaults_under_localappdata)
check('root survives a stripped environment', test_root_survives_missing_localappdata)
check('state round-trips; corrupt reads empty', test_state_round_trip_and_corruption_reads_empty)
check('gpu ok on a big card', test_gpu_ok_on_a_big_card)
check('gpu rejected when VRAM too small', test_gpu_rejected_when_vram_too_small)
check('no nvidia-smi is a clean no', test_no_nvidia_smi_is_a_clean_no_not_a_crash)
check('nvidia-smi failure is a clean no', test_nvidia_smi_failure_is_a_clean_no)
check('health ready when gpu + install good', test_health_ready_when_gpu_and_install_are_good)
check('health names the missing pieces', test_health_reports_missing_pieces_by_name)
check('health flags a stale pin', test_health_flags_a_stale_pin)
check('gpu problem outranks install problem', test_gpu_problem_outranks_install_problem)
check('half-deleted backend reads as broken', test_half_deleted_backend_reads_as_broken)


def test_partial_model_download_reads_as_missing():
    """A 1.6 GB download that dies halfway must read as absent so a
    re-run RESUMES it - never as installed."""
    with temp_root() as td:
        _install_fake_backend(td)
        assert backend.models_present()
        backend.checkpoint_paths()[1].unlink()       # half a download
        assert not backend.models_present()
        assert not backend.install_state()['installed']
        assert 'model checkpoints' in backend.install_state()['missing']


check('partial model download reads as missing',
      test_partial_model_download_reads_as_missing)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print(f'ALL PASS ({14 - len(FAILURES)})')
