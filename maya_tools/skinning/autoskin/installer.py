# maya_tools/skinning/autoskin/installer.py
"""AutoSkin backend installer — pure Python, no Maya, no Qt.

Builds the ~8.8 GB inference backend the tool cannot ship inside a
release: the pinned SkinTokens fork, a Python 3.11 venv (torch cu128,
transformers, bpy, scipy), and ~1.6 GB of checkpoints. Ends with a smoke
test that runs a real mesh through the real engine and reports pass/fail
honestly — an installer that says "done" without proving the stack runs
is worse than one that fails.

Two properties the UI depends on:

IDEMPOTENT — every step asks "is this already done?" first, so running
the installer twice is cheap and safe.

RESUMABLE — a 7 GB download that dies at 90% must not force a full
redo. Each step's done-check probes the DISK, so a re-run picks up
exactly where it stopped. That is also why nothing here trusts
state.json to decide what work remains: it records what happened, it
does not authorize skipping.

Progress is reported through a callback rather than printed, so the same
code serves the UI, a headless call, and the test suite.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

import shutil
import subprocess
from pathlib import Path

from maya_tools.skinning.autoskin import backend

# torch is NOT in the fork's requirements.txt — it must come from
# PyTorch's own CUDA index or pip resolves a CPU-only build and every
# run fails at .cuda(). Pinned to the eval-proven pair.
TORCH_SPEC = 'torch==2.7.0'
TORCH_INDEX = 'https://download.pytorch.org/whl/cu128'
PYTHON_VERSION = '3.11'

# The bundled mesh the smoke test rigs. Ships with the fork.
SMOKE_MESH = 'examples/giraffe.glb'

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class InstallError(RuntimeError):
    """An install step failed. The message is user-facing and names the
    fix where one exists."""


def _run(cmd: list, cwd: Path | None = None, timeout: int = 3600) -> str:
    """Run a subprocess, returning stdout. Raises InstallError with the
    tail of the real output — never a bare 'command failed'."""
    try:
        p = subprocess.run(
            [str(c) for c in cmd], cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise InstallError(f'Timed out after {timeout}s: {" ".join(str(c) for c in cmd[:3])}...')
    except OSError as exc:
        raise InstallError(f'Could not run {cmd[0]!r}: {exc}')
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or '').strip().splitlines()[-12:]
        raise InstallError(
            f'{cmd[0]} failed (exit {p.returncode}):\n' + '\n'.join(tail))
    return p.stdout


# ─────────────────────────────────────────────────────────────────────────────
# Steps — each one is (label, is_done, do)
# ─────────────────────────────────────────────────────────────────────────────

def _uv() -> str:
    """The uv executable. uv builds the venv and resolves deps far faster
    than pip and is what the proven install used."""
    found = shutil.which('uv')
    if not found:
        raise InstallError(
            'uv is not installed (or not on PATH). AutoSkin uses it to '
            'build its Python environment. Install it with:\n'
            '    pip install uv')
    return found


def _git() -> str:
    found = shutil.which('git')
    if not found:
        raise InstallError('git is not installed (or not on PATH). '
                           'AutoSkin needs it to fetch its engine.')
    return found


def repo_done() -> bool:
    """Repo present AND parked on the pin — a repo left on some other
    commit is not a valid install."""
    if not (backend.repo_dir() / 'demo.py').is_file():
        return False
    try:
        out = _run([_git(), 'describe', '--tags', '--exact-match'],
                   cwd=backend.repo_dir(), timeout=60)
        return out.strip() == backend.PIN_TAG
    except InstallError:
        return False


def do_repo(timeout: int | None = None) -> None:
    """Clone the fork at the pin, or move an existing clone onto it.

    `timeout` bounds each git call (seconds). The full installer leaves it
    None (git's own default): a fresh clone can be slow on a cold cache. The
    auto-update path passes a short bound so a stalled fetch can never freeze
    Maya on window open.
    """
    git, repo = _git(), backend.repo_dir()
    kw = {'timeout': timeout} if timeout else {}
    if (repo / '.git').is_dir():
        _run([git, 'fetch', '--tags', '--quiet', 'origin'], cwd=repo, **kw)
        _run([git, 'checkout', '--quiet', backend.PIN_TAG], cwd=repo, **kw)
        return
    repo.parent.mkdir(parents=True, exist_ok=True)
    _run([git, 'clone', '--quiet', '--branch', backend.PIN_TAG,
          '--depth', '1', backend.REPO_URL, str(repo)], **kw)


def repo_update_available() -> bool:
    """True when the backend is fully installed but its repo is behind the
    pin — a CODE-ONLY update (a git checkout, no venv rebuild, no download).

    This is the shape every AutoSkin backend patch has taken so far — v1.3 is
    the bpy-server loopback security fix, a one-line engine change. A pin bump
    that ALSO needs new pip deps or model files is not code-only: `install_state`
    reports the missing pieces, `installed` is False, and it routes to the
    user-gated installer with its size/time warning instead of this fast path.
    (Bumping the pin to a tag that changes deps or models is therefore a
    release-process responsibility — force a reinstall, don't rely on this.)
    """
    inst = backend.install_state()
    return bool(inst['installed'] and not inst['pin_matches'])


def apply_repo_update(timeout: int = 120) -> dict:
    """Move the repo onto the pin and record it: a fast git fetch + checkout,
    never a venv rebuild or model download. The unattended security-patch path.

    Never raises — a failure (offline, stalled fetch) returns ok=False and the
    caller keeps the existing user-gated "Run Install Backend" prompt, so the
    tool degrades to today's behavior rather than breaking.

    Returns {'ok': bool, 'detail': str}.
    """
    try:
        do_repo(timeout=timeout)
        backend.write_state(pin=backend.PIN_TAG)
    except Exception as exc:                                       # noqa: BLE001
        return {'ok': False,
                'detail': f'AutoSkin auto-update to {backend.PIN_TAG} '
                          f'deferred: {exc}'}
    return {'ok': True,
            'detail': f'AutoSkin backend updated to {backend.PIN_TAG}.'}


def venv_done() -> bool:
    return backend.venv_python().is_file()


def do_venv() -> None:
    _run([_uv(), 'venv', '--python', PYTHON_VERSION, str(backend.venv_dir())])


def deps_done() -> bool:
    """torch importable AND CUDA-enabled inside the venv. A CPU-only
    torch installs cleanly and then fails at inference — so the done-check
    proves CUDA, not mere importability."""
    if not venv_done():
        return False
    try:
        out = _run([backend.venv_python(), '-c',
                    'import torch; print(torch.cuda.is_available())'],
                   timeout=180)
        return out.strip().endswith('True')
    except InstallError:
        return False


def do_deps() -> None:
    uv, py = _uv(), backend.venv_python()
    # torch first, from the CUDA index — otherwise the requirements
    # resolve pulls a CPU wheel and everything downstream is dead.
    _run([uv, 'pip', 'install', '--python', str(py),
          TORCH_SPEC, '--index-url', TORCH_INDEX])
    _run([uv, 'pip', 'install', '--python', str(py),
          '-r', str(backend.repo_dir() / 'requirements.txt')])


def models_done() -> bool:
    return backend.models_present()


def do_models() -> None:
    _run([backend.venv_python(), 'download.py', '--model'],
         cwd=backend.repo_dir(), timeout=7200)


def smoke() -> dict:
    """Rig the bundled mesh end to end. Returns {'ok', 'detail'} — never
    raises: a failed smoke is a REPORT, not a crash, because the install
    itself may still be fine and the user deserves the real reason."""
    repo = backend.repo_dir()
    mesh = repo / SMOKE_MESH
    if not mesh.is_file():
        return {'ok': False, 'detail': f'bundled smoke mesh missing: {SMOKE_MESH}'}

    # Hand demo.py an explicit output FILE, never a directory. It only
    # honors --output verbatim when the path carries a suffix; a bare
    # directory goes through map_output_path(), which calls
    # relative_to(input_root) on a FILE input — yielding an empty
    # relative path, so the rig lands at "<dir>.glb", a SIBLING of the
    # directory you asked for. (Cost a live smoke failure on the first
    # from-zero install, 2026-07-12: the engine was fine, the output was
    # simply somewhere else.)
    out_file = backend.backend_root() / 'smoke_out' / 'smoke.glb'
    out_file.parent.mkdir(parents=True, exist_ok=True)
    before = out_file.stat().st_mtime if out_file.is_file() else None

    try:
        out = _run([backend.venv_python(), 'demo.py',
                    '--input', str(mesh), '--output', str(out_file)],
                   cwd=repo, timeout=1800)
    except InstallError as exc:
        return {'ok': False, 'detail': str(exc)}

    if not out_file.is_file():
        tail = '\n'.join((out or '').strip().splitlines()[-6:])
        return {'ok': False,
                'detail': f'demo.py exited 0 but wrote no {out_file.name}.\n{tail}'}
    # A stale artifact from an earlier run must not read as a pass.
    if before is not None and out_file.stat().st_mtime <= before:
        return {'ok': False,
                'detail': f'{out_file.name} was not rewritten — stale artifact.'}

    size_mb = out_file.stat().st_size / 1e6
    return {'ok': True,
            'detail': f'rigged {mesh.name} -> {out_file.name} ({size_mb:.1f} MB)'}


STEPS = (
    ('Fetching engine',       repo_done,   do_repo),
    ('Building environment',  venv_done,   do_venv),
    ('Installing PyTorch + dependencies', deps_done, do_deps),
    ('Downloading models',    models_done, do_models),
)

# Everything the installer creates, relative to the backend root. Uninstall
# removes exactly these and nothing else.
_OWNED = ('repo', 'venv', 'smoke_out', 'smoke_out.glb', 'state.json')


def uninstall() -> dict:
    """Remove the backend. Returns {'removed': [...], 'root': str}.

    Deletes ONLY the children this installer creates, by name — never
    `rmtree(backend_root())`. FABRICATOR_AUTOSKIN_ROOT can point anywhere
    (a dev box aims it at a reference install), and a blind recursive
    delete of a user-supplied path is how tools eat someone's work. If a
    stranger's file is sitting in the root, it survives, and so does the
    root itself.
    """
    root = backend.backend_root()
    removed = []
    for name in _OWNED:
        target = root / name
        if not target.exists():
            continue
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)
        if not target.exists():
            removed.append(name)
    # Take the root too, but only if we left it empty — never force it.
    try:
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
    except OSError:
        pass
    return {'removed': removed, 'root': str(root)}


# ─────────────────────────────────────────────────────────────────────────────
# The one call the UI makes
# ─────────────────────────────────────────────────────────────────────────────

def install(progress=None, run_smoke: bool = True) -> dict:
    """Install (or repair, or resume) the backend.

    progress: optional callable(index, total, label) called before each
        step — including steps that turn out to be already done, so the
        UI shows the whole plan rather than a mysteriously short one.

    Returns {'ok', 'steps_run': [...], 'steps_skipped': [...], 'smoke': {...}}.
    Raises InstallError only when a step genuinely fails; a failed SMOKE
    comes back in the result with ok=False, because at that point the
    bytes are on disk and the user needs the reason, not an exception.
    """
    gpu = backend.gpu_capability()
    if not gpu['ok']:
        raise InstallError(gpu['reason'])

    total = len(STEPS) + (1 if run_smoke else 0)
    ran, skipped = [], []

    for i, (label, is_done, do) in enumerate(STEPS):
        if progress:
            progress(i, total, label)
        if is_done():
            skipped.append(label)
            continue
        do()
        if not is_done():
            raise InstallError(
                f'{label}: step reported success but its result is not on '
                f'disk. The install is incomplete; run it again.')
        ran.append(label)

    result = {'ok': True, 'steps_run': ran, 'steps_skipped': skipped,
              'smoke': None}

    if run_smoke:
        if progress:
            progress(len(STEPS), total, 'Smoke test')
        result['smoke'] = smoke()
        result['ok'] = bool(result['smoke']['ok'])

    backend.write_state(
        pin=backend.PIN_TAG,
        gpu=gpu['name'],
        smoke=('pass' if (result['smoke'] or {}).get('ok') else
               ('fail' if run_smoke else 'skipped')),
    )
    return result
