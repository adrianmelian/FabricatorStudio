# maya_tools/framework/installer/ml_deps_app.py
"""ML skinning dependency installer — headless engine.

Installs the optional pip dependencies used by DemBones Bake
(`skinning/dem_bones_bake`) into mayapy's user site-packages via
`mayapy -m pip install --user`.

Zero network calls happen at *repo install* time — this module is only ever
invoked interactively, either from the Fabricator_Install.py confirm dialog
(opt-in checkbox) or from a guided first-run prompt inside the ML tools
themselves. Never auto-run on Maya startup.

Windows/py3.11 (Maya 2025's bundled mayapy) wheel availability — verified
2026-07-01 against PyPI's JSON API (see WAVE2-REPORT.md for the full
methodology):
  - numpy   : latest stable release with a cp311 win_amd64 wheel is 2.4.6
              (numpy 2.5.0 dropped Python 3.11 wheels entirely). PIN.
  - scipy   : latest stable release with a cp311 win_amd64 wheel is 1.17.1
              (scipy 1.18.0 dropped Python 3.11 wheels entirely). PIN.
  - py_dem_bones: latest stable ships a cp311 win_amd64 wheel today. No pin
              required, but a future release could drop 3.11 the same way
              numpy/scipy did — if installs start failing, re-run the same
              PyPI JSON wheel check before assuming it's a local problem.

(libigl / tetgen / pymeshfix were pinned here for the BBW tet solver and were
dropped with it on 2026-07-18. If BBW is ever revived, the 2026-07-01 finding
was: libigl PIN 2.6.1, since 2.6.2 dropped Python 3.11 Windows wheels.)

Public API:
    plan = build_install_plan(mode='surface')   # or 'tet' or 'full'
    result = install_ml_deps(progress_cb=None)  # -> InstallReport
    ok, detail = check_ml_deps_installed(mode='surface')
"""
__author__ = "Adrian Melian"

import subprocess
import sys
from dataclasses import dataclass, field

from maya_tools.utils.maya.skin_subprocess import resolve_mayapy

# ─────────────────────────────────────────────
# Pinned / unpinned package spec
# ─────────────────────────────────────────────

# Order matters: numpy/scipy first (everything else depends on their ABI).
# libigl / tetgen / pymeshfix were the BBW tet-solver's deps and went with it
# when BBW was retired (2026-07-18); DemBones needs only numpy + scipy +
# py_dem_bones, so the one-time install no longer builds three heavy wheels
# nothing imports.
ML_DEPENDENCIES = [
    'numpy==2.4.6',
    'scipy==1.17.1',
    'py_dem_bones',
]

# Modules probed post-install to confirm each package actually imports.
_PROBE_IMPORTS = {
    'numpy==2.4.6':   'numpy',
    'scipy==1.17.1':  'scipy',
    'py_dem_bones':   'py_dem_bones',
}

_PROBE_TIMEOUT_S = 30
_INSTALL_TIMEOUT_S = 600  # pip resolving + downloading + building can be slow


@dataclass
class PackageResult:
    spec: str
    ok: bool
    detail: str = ''


@dataclass
class InstallReport:
    mayapy_path: str = ''
    packages: list = field(default_factory=list)  # list[PackageResult]
    offline: bool = False
    fatal_error: str = ''

    @property
    def all_ok(self) -> bool:
        return bool(self.packages) and all(p.ok for p in self.packages) and not self.fatal_error

    @property
    def failed(self) -> list:
        return [p for p in self.packages if not p.ok]


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def build_install_plan() -> list:
    """Return the ordered list of pip specs this installer will apply.

    Kept as a function (not a bare module constant re-export) so callers
    always see live values, and so a future per-mode plan (surface-only vs
    full tet-mode) can be introduced without changing the call sites.
    """
    return list(ML_DEPENDENCIES)


def check_ml_deps_installed(specs: list = None) -> tuple:
    """Probe mayapy for each package's import module.

    Returns (all_ok: bool, per_package: dict[str, bool]). Does not raise —
    a missing mayapy or a probe failure both report as not-installed so
    callers can render a "what's missing" message rather than crash.
    """
    specs = specs or ML_DEPENDENCIES
    per_package = {}
    try:
        mayapy = resolve_mayapy()
    except RuntimeError:
        return False, {spec: False for spec in specs}

    modules = sorted({_PROBE_IMPORTS[spec] for spec in specs})
    probe = '; '.join(f'import {m}' for m in modules) + '; print("ok")'
    try:
        result = subprocess.run(
            [mayapy, '-c', probe],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False, {spec: False for spec in specs}

    if result.returncode == 0 and 'ok' in result.stdout:
        return True, {spec: True for spec in specs}

    # Fall back to a per-module probe so the report can say exactly what's
    # missing instead of just "something failed".
    for spec in specs:
        mod = _PROBE_IMPORTS[spec]
        try:
            r = subprocess.run(
                [mayapy, '-c', f'import {mod}'],
                capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S, check=False,
            )
            per_package[spec] = (r.returncode == 0)
        except (subprocess.TimeoutExpired, OSError):
            per_package[spec] = False
    return all(per_package.values()), per_package


def install_ml_deps(specs: list = None, progress_cb=None) -> InstallReport:
    """Run `mayapy -m pip install --user <spec>` for each dependency in order.

    Args:
      specs: pip specs to install (default: ML_DEPENDENCIES, pinned set above).
      progress_cb: optional callable(index: int, total: int, spec: str) invoked
                   before each package installs. Never raise from progress_cb —
                   this function does not guard against callback exceptions.

    Returns an InstallReport. Never raises for network/pip failures — those
    are captured per-package in `.packages`. Only resolve_mayapy() failing
    (no mayapy.exe found alongside Maya) is treated as fatal, since nothing
    downstream can run without it.

    Offline / no-network behavior: pip itself fails per-package with a
    connection error; that failure is captured as a normal PackageResult
    with `ok=False` and the pip stderr in `.detail` — the caller renders
    that as a graceful "couldn't reach PyPI" message, not a crash. If EVERY
    package fails and the stderr smells like a connection issue, `.offline`
    is set True so UI can show one clear offline message instead of N
    per-package tracebacks.
    """
    specs = specs or ML_DEPENDENCIES
    report = InstallReport()

    try:
        report.mayapy_path = resolve_mayapy()
    except RuntimeError as exc:
        report.fatal_error = str(exc)
        return report

    total = len(specs)
    for i, spec in enumerate(specs, start=1):
        if progress_cb is not None:
            try:
                progress_cb(i, total, spec)
            except Exception:
                pass

        try:
            result = subprocess.run(
                [report.mayapy_path, '-m', 'pip', 'install', '--user', spec],
                capture_output=True, text=True, timeout=_INSTALL_TIMEOUT_S, check=False,
            )
        except subprocess.TimeoutExpired:
            report.packages.append(PackageResult(
                spec, False, f'pip install timed out after {_INSTALL_TIMEOUT_S}s'
            ))
            continue
        except OSError as exc:
            report.packages.append(PackageResult(spec, False, f'subprocess failed to launch: {exc}'))
            continue

        if result.returncode == 0:
            report.packages.append(PackageResult(spec, True, 'installed'))
        else:
            tail = (result.stderr or result.stdout or '').strip().splitlines()
            detail = tail[-1] if tail else f'pip exited {result.returncode}'
            report.packages.append(PackageResult(spec, False, detail))

    if report.packages and all(not p.ok for p in report.packages):
        combined = ' '.join(p.detail.lower() for p in report.packages)
        offline_markers = (
            'newconnectionerror', 'failed to establish a new connection',
            'name or service not known', 'temporary failure in name resolution',
            'connection refused', 'read timed out', 'max retries exceeded',
            'no address associated with hostname',
        )
        report.offline = any(marker in combined for marker in offline_markers)

    return report


def format_report_text(report: InstallReport) -> str:
    """Render an InstallReport as a plain-text summary for logs / dialogs."""
    if report.fatal_error:
        return f'ML dependency install could not start: {report.fatal_error}'

    lines = [f'mayapy: {report.mayapy_path}']
    for pkg in report.packages:
        tag = 'OK' if pkg.ok else 'FAILED'
        lines.append(f'  [{tag}] {pkg.spec} — {pkg.detail}')

    if report.all_ok:
        lines.append('All ML dependencies installed successfully.')
    elif report.offline:
        lines.append(
            'Could not reach PyPI — this looks like a network/offline issue. '
            'The ML tools remain visible; connect to the internet and retry, '
            'or install manually (see each failed package above).'
        )
    else:
        failed_specs = ', '.join(p.spec for p in report.failed)
        lines.append(f'{len(report.failed)} package(s) failed: {failed_specs}')

    return '\n'.join(lines)
