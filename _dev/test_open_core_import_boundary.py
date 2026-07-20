# _dev/test_open_core_import_boundary.py
"""Open-core import law (t31 enforcement; BasicIKArm SPEC §3.6): no
FREE-core module may import a paid-boundary module. Static ast walk —
runs under plain `py -3` or mayapy, no Maya session needed.

Paid module list is READ FROM paid_manifest.json (the ribbon-pack
packaging SPEC's single source of truth, workspace/2026-07-10_ribbon-
pack-packaging/SPEC.md §2) rather than hardcoded here — the same
manifest also drives package_release.py's free-zip exclusion and
build_ribbon_pack.py's paid-zip inclusion, so this list can never drift
out of step with either of those.

Run: py -3 _dev/test_open_core_import_boundary.py
"""
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FAB = REPO_ROOT / 'maya_tools' / 'rigging' / 'fabricator'
MANIFEST_PATH = FAB / 'paid_manifest.json'


def _load_paid_modules() -> set:
    """Module stems (no .py) for every manifest file under modules/ —
    the only manifest entries that are importable Python modules (the
    fragment/doc entries aren't). Raises loudly if the manifest is
    missing or empty of module entries — a silently-empty boundary set
    would make this whole check a no-op pass, worse than not running it."""
    if not MANIFEST_PATH.is_file():
        raise RuntimeError(f'paid_manifest.json not found at {MANIFEST_PATH} '
                            f'— the boundary manifest is required to run this check.')
    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    modules_prefix = 'maya_tools/rigging/fabricator/modules/'
    stems = set()
    for rel in manifest.get('files', []):
        rel = rel.replace('\\', '/')
        if rel.startswith(modules_prefix) and rel.endswith('.py'):
            stems.add(Path(rel).stem)
    if not stems:
        raise RuntimeError('paid_manifest.json produced zero paid module '
                            'stems — check the manifest\'s modules/ entries.')
    return stems


PAID_MODULES = _load_paid_modules()


def imported_names(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ''
            for a in node.names:
                yield f'{node.module or ""}.{a.name}'


def main():
    failures = []
    for py in sorted(FAB.rglob('*.py')):
        if py.stem in PAID_MODULES:
            continue                       # paid may import paid
        tree = ast.parse(py.read_text(encoding='utf-8'), filename=str(py))
        for name in imported_names(tree):
            leaf = name.split('.')[-1]
            mod = name.split('.')[-2] if '.' in name else ''
            if leaf in PAID_MODULES or mod in PAID_MODULES:
                failures.append(f'{py.relative_to(REPO_ROOT)}: imports {name}')
    if failures:
        print('OPEN-CORE BOUNDARY: FAILED')
        print('\n'.join(f'  {f}' for f in failures))
        sys.exit(1)
    print('OPEN-CORE BOUNDARY: OK')


if __name__ == '__main__':
    main()
