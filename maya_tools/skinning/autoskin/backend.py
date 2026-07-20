# maya_tools/skinning/autoskin/backend.py
"""AutoSkin backend: install paths, capability gating, install health.

Pure Python on purpose — no maya.cmds, no Qt, no torch. This module is
imported by the Maya-side tool to answer three questions BEFORE anything
heavy happens: where does the backend live, can this machine run it, and
is it installed and healthy? Importing it must never be expensive and
must never raise.

The backend cannot ship inside a tool release (a ~7 GB inference venv +
~1.6 GB of checkpoints), so it is installed once into a user-writable
root — no admin (spec §4):

    %LOCALAPPDATA%/FabricatorStudio/autoskin/
        repo/       the pinned SkinTokens fork (MIT, patched — see PIN_TAG)
          experiments/…  TokenRig + FSQ-CVAE checkpoints
          models/Qwen3-0.6B/  LLM config (weights come from the ckpt)
        venv/       Python 3.11, torch cu128, transformers, bpy, scipy
        state.json  install bookkeeping: pin, versions, last smoke result

The checkpoints live INSIDE repo/ rather than in a sibling models/ dir:
upstream's download.py hardcodes `local_dir="."`, so they land relative
to the repo it is run from. Fighting that (HF_HOME games, symlinks) buys
nothing and breaks on every upstream bump — so the layout follows the
tool instead.

FABRICATOR_AUTOSKIN_ROOT overrides the root — the escape hatch for dev
boxes (Adrian's reference install) and for studios that want the backend
on a shared/faster volume.

Capability gating is a FEATURE DETECTION, never a hard error at import:
no NVIDIA GPU means the AutoSkin button explains why and the rest of the
toolset is unaffected.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

import json
import os
import subprocess
from pathlib import Path

# The pinned fork the installer clones. The pin is what makes an install
# reproducible and immune to upstream drift (spec §4); it carries the
# flash-attn -> SDPA patches and the scipy requirement upstream omits.
#
# v1.3 (2026-07-13) is a SECURITY fix and every install should move onto it.
# The bpy helper deserialises its POST bodies with torch.load(weights_only=
# False) == pickle == arbitrary code execution on load, and upstream binds it
# to 0.0.0.0 with no auth. Demonstrated end-to-end: an unauthenticated POST to
# /export runs code on the host. v1.3 binds 127.0.0.1 so the endpoint is only
# reachable from the same machine (the client always talked to localhost, so
# nothing is lost). Reported upstream to VAST-AI-Research. do_repo() moves an
# existing clone forward on the next launch, so bumping this tag is the whole
# rollout.
REPO_URL = 'https://github.com/adrianmelian/SkinTokens.git'
PIN_TAG = 'autoskin-v1.3'

# Measured on the RTX 5070 Ti: ~4.7 GB peak. 6 GB is the floor with head-
# room; 8 GB is the comfortable recommendation (spec §5).
MIN_VRAM_GB = 6.0
RECOMMENDED_VRAM_GB = 8.0

# What `python download.py --model` fetches, as paths relative to repo/.
# Probed individually: a download interrupted halfway must read as "models
# missing", not as installed.
CHECKPOINTS = (
    'experiments/skin_vae_2_10_32768/last.ckpt',
    'experiments/articulation_xl_quantization_256_token_4/grpo_1400.ckpt',
)
LLM_CONFIG_DIR = 'models/Qwen3-0.6B'

_ROOT_ENV = 'FABRICATOR_AUTOSKIN_ROOT'


# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

def backend_root() -> Path:
    """Where the backend lives. FABRICATOR_AUTOSKIN_ROOT wins; otherwise
    %LOCALAPPDATA%/FabricatorStudio/autoskin. Falls back to ~/.fabricator
    when LOCALAPPDATA is unset (non-Windows / stripped environments) so
    this never raises at import time."""
    override = os.environ.get(_ROOT_ENV)
    if override:
        return Path(override)
    local_app = os.environ.get('LOCALAPPDATA')
    base = Path(local_app) if local_app else (Path.home() / '.fabricator')
    return base / 'FabricatorStudio' / 'autoskin'


def repo_dir() -> Path:
    return backend_root() / 'repo'


def venv_dir() -> Path:
    return backend_root() / 'venv'


def state_path() -> Path:
    return backend_root() / 'state.json'


def checkpoint_paths() -> list[Path]:
    """The model checkpoints, absolute. Live under repo/ — see the module
    docstring for why."""
    return [repo_dir() / c for c in CHECKPOINTS]


def models_present() -> bool:
    """Every checkpoint AND the LLM config dir on disk. A part-finished
    download reads as absent, so re-running the installer resumes it."""
    if not all(p.is_file() for p in checkpoint_paths()):
        return False
    llm = repo_dir() / LLM_CONFIG_DIR
    return llm.is_dir() and any(llm.iterdir())


def venv_python() -> Path:
    """The backend interpreter. Windows venvs put it in Scripts/."""
    if os.name == 'nt':
        return venv_dir() / 'Scripts' / 'python.exe'
    return venv_dir() / 'bin' / 'python'


# ─────────────────────────────────────────────────────────────────────────────
# Install state (bookkeeping written by the installer, read by everyone)
# ─────────────────────────────────────────────────────────────────────────────

def read_state() -> dict:
    """The installer's bookkeeping, or {} when absent/corrupt. A corrupt
    state file must read as 'not installed' and be re-installable over —
    never a hard failure the user cannot clear."""
    try:
        return json.loads(state_path().read_text(encoding='utf-8'))
    except Exception:
        return {}


def write_state(**fields) -> None:
    """Merge `fields` into state.json (atomic replace)."""
    root = backend_root()
    root.mkdir(parents=True, exist_ok=True)
    state = read_state()
    state.update(fields)
    tmp = state_path().with_suffix('.json.tmp')
    tmp.write_text(json.dumps(state, indent=2), encoding='utf-8')
    os.replace(tmp, state_path())


# ─────────────────────────────────────────────────────────────────────────────
# Capability gating
# ─────────────────────────────────────────────────────────────────────────────

def gpu_capability() -> dict:
    """Probe the machine for an NVIDIA GPU with enough VRAM.

    Returns {'ok': bool, 'name': str, 'vram_gb': float, 'reason': str}.
    `reason` is empty when ok; otherwise it is USER-FACING copy naming
    the fix. Never raises: a missing/broken nvidia-smi reads as "no
    NVIDIA GPU", which is exactly what it means for us.
    """
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,memory.total',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.SubprocessError):
        return {'ok': False, 'name': '', 'vram_gb': 0.0,
                'reason': 'No NVIDIA GPU detected (nvidia-smi not found). '
                          'AutoSkin needs an NVIDIA GPU with at least '
                          f'{MIN_VRAM_GB:.0f} GB of VRAM.'}

    if out.returncode != 0 or not out.stdout.strip():
        return {'ok': False, 'name': '', 'vram_gb': 0.0,
                'reason': 'No NVIDIA GPU detected. AutoSkin needs an '
                          f'NVIDIA GPU with at least {MIN_VRAM_GB:.0f} GB '
                          'of VRAM.'}

    # First GPU wins — multi-GPU boxes run inference on cuda:0.
    first = out.stdout.strip().splitlines()[0]
    name, _, mem = first.partition(',')
    name = name.strip()
    try:
        vram_gb = float(mem.strip()) / 1024.0     # nvidia-smi reports MiB
    except ValueError:
        vram_gb = 0.0

    if vram_gb < MIN_VRAM_GB:
        return {'ok': False, 'name': name, 'vram_gb': vram_gb,
                'reason': f'{name} has {vram_gb:.1f} GB of VRAM; AutoSkin '
                          f'needs at least {MIN_VRAM_GB:.0f} GB '
                          f'({RECOMMENDED_VRAM_GB:.0f} GB recommended).'}
    return {'ok': True, 'name': name, 'vram_gb': vram_gb, 'reason': ''}


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

def install_state() -> dict:
    """What is actually on disk right now, checked by probing the disk —
    never by trusting state.json alone (a half-deleted backend must read
    as broken, not as installed).

    Returns:
        {'installed': bool,          every piece present
         'missing': [str, ...],      user-facing names of what is absent
         'repo': bool, 'venv': bool, 'models': bool,
         'pin': str,                 the tag state.json claims is checked out
         'pin_matches': bool}        ... and whether it is the pin we want
    """
    have_repo = (repo_dir() / 'demo.py').is_file()
    have_venv = venv_python().is_file()
    have_models = have_repo and models_present()

    missing = []
    if not have_repo:
        missing.append('SkinTokens repo')
    if not have_venv:
        missing.append('inference environment')
    if not have_models:
        missing.append('model checkpoints')

    pin = str(read_state().get('pin') or '')
    return {
        'installed': not missing,
        'missing': missing,
        'repo': have_repo,
        'venv': have_venv,
        'models': have_models,
        'pin': pin,
        'pin_matches': pin == PIN_TAG,
    }


def health() -> dict:
    """The one call the UI makes to decide what to show.

    Returns {'ready': bool, 'reason': str, 'gpu': {...}, 'install': {...}}.
    `reason` is empty when ready; otherwise it is the single most useful
    sentence to put in front of the user — GPU problems outrank install
    problems, because no install can fix a missing GPU.
    """
    gpu = gpu_capability()
    inst = install_state()
    if not gpu['ok']:
        reason = gpu['reason']
    elif not inst['installed']:
        reason = ('AutoSkin backend is not installed ('
                  + ', '.join(inst['missing']) + '). Run Install Backend.')
    elif not inst['pin_matches']:
        reason = (f'AutoSkin backend is out of date (installed '
                  f'{inst["pin"] or "unknown"}, expected {PIN_TAG}). '
                  f'Run Install Backend to update.')
    else:
        reason = ''
    return {'ready': not reason, 'reason': reason, 'gpu': gpu,
            'install': inst}
