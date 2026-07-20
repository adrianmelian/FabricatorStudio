# _dev/test_paid_manifest_doc_parity.py
"""Paid-boundary DOC parity: if a module is paid, its doc page is paid too.

Companion to test_open_core_import_boundary.py, which checks IMPORTS and is
therefore blind to documentation. That blind spot shipped a real defect: the
ribbon component doc pages ribbon-ik-arm.md / ribbon-ik-leg.md were added to
the tree (3f8887a) without being registered in paid_manifest.json, so the FREE
zip shipped documentation for paid-only modules. paid_manifest.json already
listed ribbon-spine.md, so the rule existed — nothing enforced it.

The invariant: for every paid module `modules/<name>.py` in the manifest, if
`maya_tools/docs/components/<name-with-hyphens>.md` exists in the tree, that
doc must ALSO be listed in the manifest. Any future doc page for a paid module
fails here instead of leaking into the free package.

Reads paid_manifest.json — the same single source of truth used by
package_release.py, build_ribbon_pack.py, and the import-boundary test — so
this can never drift out of step with them.

Run: py -3 _dev/test_paid_manifest_doc_parity.py
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (REPO_ROOT / 'maya_tools' / 'rigging' / 'fabricator'
                 / 'paid_manifest.json')

_MODULES_PREFIX = 'maya_tools/rigging/fabricator/modules/'
_DOCS_DIR_REL = 'maya_tools/docs/components'


def _manifest_entries() -> set:
    """Every manifest path, slash-normalized. Raises loudly when the manifest
    is missing or empty: a silently-empty set would make this check a no-op
    pass, which is worse than not running it at all."""
    if not MANIFEST_PATH.is_file():
        raise RuntimeError(
            f'paid_manifest.json not found at {MANIFEST_PATH} — the boundary '
            f'manifest is required to run this check.')
    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    entries = {str(p).replace('\\', '/') for p in manifest.get('files', [])}
    if not entries:
        raise RuntimeError('paid_manifest.json lists zero files — check the '
                            'manifest before trusting this check.')
    return entries


def expected_doc_path(module_rel: str) -> str:
    """'…/modules/ribbon_ik_arm.py' -> 'maya_tools/docs/components/ribbon-ik-arm.md'

    Underscores become hyphens, matching the doc-slug convention in
    framework/annotations.py. Private helpers (_ribbon_common) resolve to doc
    names that simply do not exist, and are skipped by the caller.
    """
    stem = Path(module_rel).stem
    return f'{_DOCS_DIR_REL}/{stem.replace("_", "-")}.md'


def main():
    entries = _manifest_entries()
    paid_modules = sorted(
        e for e in entries
        if e.startswith(_MODULES_PREFIX) and e.endswith('.py')
    )
    if not paid_modules:
        raise RuntimeError('paid_manifest.json produced zero paid modules — '
                            'check the manifest\'s modules/ entries.')

    failures = []
    for module_rel in paid_modules:
        doc_rel = expected_doc_path(module_rel)
        if not (REPO_ROOT / doc_rel).is_file():
            continue                    # no doc page authored yet — fine
        if doc_rel not in entries:
            failures.append(
                f'{doc_rel} documents paid module {Path(module_rel).name} '
                f'but is NOT in paid_manifest.json (it would ship in the '
                f'free zip)')

    if failures:
        print('PAID DOC PARITY: FAILED')
        print('\n'.join(f'  {f}' for f in failures))
        sys.exit(1)
    print(f'PAID DOC PARITY: OK ({len(paid_modules)} paid modules checked)')


if __name__ == '__main__':
    main()
