# build_ribbon_pack.py
"""Build the Advanced Ribbon Pack distribution zip — the paid counterpart
to package_release.py's free zip. Same BINDING RULE: reads from `git
archive ref`, never a working-directory copy, for everything the paid
manifest names (the manifest itself is read from the archived tree too,
so a stale on-disk copy can never leak into a build).

Single source of truth: maya_tools/rigging/fabricator/paid_manifest.json
(SPEC workspace/2026-07-10_ribbon-pack-packaging/SPEC.md §2). This script,
package_release.py's free-zip exclusion, and
_dev/test_open_core_import_boundary.py all read the SAME manifest file,
so the paid file list cannot drift between the three.

Output shape (matches RibbonPack_Install.py's expectations):
    build/FabricatorRibbonPack_v<version>/
        RibbonPack_Install.py
        RibbonPack_Uninstall.py
        RibbonPack_README.md
        LICENSE-RIBBON-PACK.txt
        ribbon_pack_manifest.json   (bundled copy — see write_bundled_manifest())
        RibbonPack_Data/
            <every paid_manifest.json file, at its own repo-relative path>
    build/FabricatorRibbonPack_v<version>.zip   <- the artifact to distribute

Usage:
    py -3 build_ribbon_pack.py                  # uses HEAD, auto version
    py -3 build_ribbon_pack.py --ref my-branch
    py -3 build_ribbon_pack.py --version 1.0.0
"""
__author__ = "Adrian Melian"

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_BUILD_DIR = os.path.join(_REPO_ROOT, 'build')

_MANIFEST_REL_PATH = 'maya_tools/rigging/fabricator/paid_manifest.json'
_VERSION_PY_REL_PATH = 'maya_tools/rigging/fabricator/version.py'

_PACK_ENTRY_FILES = (
    'RibbonPack_Install.py',
    'RibbonPack_Uninstall.py',
    'RibbonPack_README.md',
    'LICENSE-RIBBON-PACK.txt',
)


def _run_git(args: list, cwd: str = _REPO_ROOT) -> str:
    result = subprocess.run(
        ['git'] + args, cwd=cwd, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)} failed: {result.stderr.strip()}')
    return result.stdout


def _resolve_version(explicit_version: str, ref: str) -> str:
    if explicit_version:
        return explicit_version
    short_sha = _run_git(['rev-parse', '--short', ref]).strip()
    date_str = datetime.date.today().isoformat()
    return f'{date_str}-{short_sha}'


def _git_tracked_files(ref: str) -> list:
    output = _run_git(['ls-tree', '-r', '--name-only', ref])
    return [line for line in output.splitlines() if line]


def _extract_ref_tree(ref: str, tmp: str) -> str:
    """git archive `ref` into a fresh temp dir, return its path. Shared
    shape with package_release.py's build_archive_tree() two-step
    (archive-to-temp, then read/copy) — same rationale: one readable pass
    over a real directory tree."""
    tar_path = os.path.join(tmp, 'archive.tar')
    with open(tar_path, 'wb') as fh:
        proc = subprocess.run(
            ['git', 'archive', '--format=tar', ref],
            cwd=_REPO_ROOT, stdout=fh, stderr=subprocess.PIPE, check=False,
        )
    if proc.returncode != 0:
        raise RuntimeError(f'git archive failed: {proc.stderr.decode(errors="replace")}')

    extract_dir = os.path.join(tmp, 'extracted')
    os.makedirs(extract_dir)
    with tarfile.open(tar_path, 'r') as tf:
        tf.extractall(extract_dir)  # noqa: S202 — trusted source (our own git archive output)
    return extract_dir


def load_manifest(extract_dir: str) -> dict:
    manifest_path = os.path.join(extract_dir, *_MANIFEST_REL_PATH.split('/'))
    if not os.path.isfile(manifest_path):
        raise RuntimeError(
            f'{_MANIFEST_REL_PATH} not found at the archived ref — cannot '
            f'build the ribbon pack without the boundary manifest.'
        )
    with open(manifest_path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def read_core_version(extract_dir: str) -> str:
    """Regex-read FABRICATOR_VERSION out of the archived ref's own
    version.py — this is what gets stamped as the pack's min_core_version,
    ALWAYS fresh from the ref being packaged, never trusted from a
    possibly-stale copy sitting in paid_manifest.json."""
    version_path = os.path.join(extract_dir, *_VERSION_PY_REL_PATH.split('/'))
    if not os.path.isfile(version_path):
        raise RuntimeError(f'{_VERSION_PY_REL_PATH} not found at the archived ref.')
    with open(version_path, 'r', encoding='utf-8') as fh:
        text = fh.read()
    match = re.search(r"FABRICATOR_VERSION\s*=\s*['\"]([\d.]+)['\"]", text)
    if not match:
        raise RuntimeError(f'Could not find FABRICATOR_VERSION in {version_path}')
    return match.group(1)


def copy_manifest_payload(extract_dir: str, manifest: dict, dest_data_dir: str) -> list:
    """Copy every manifest file, preserving its repo-relative path, from
    the archived ref into dest_data_dir/<relpath>. Returns the list of
    repo-relative paths actually copied. Raises if a manifest-listed file
    is missing from the archived ref — the manifest is the contract; a
    file it names must exist."""
    copied = []
    for rel in manifest.get('files', []):
        rel_native = rel.replace('/', os.sep)
        src = os.path.join(extract_dir, rel_native)
        if not os.path.isfile(src):
            raise RuntimeError(
                f'Manifest file missing from archived ref: {rel} '
                f'(expected at {src})'
            )
        dest = os.path.join(dest_data_dir, rel_native)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(rel)
    return copied


def _preflight_check_pack_entry_files_committed(ref: str) -> None:
    """Refuse to package if the pack's own entry files (installer,
    uninstaller, README, LICENSE placeholder) have uncommitted changes
    relative to `ref` — mirrors package_release.py's
    _preflight_check_scripts_committed for the same reason: never ship a
    zip whose installer differs from what's in version control."""
    tracked = set(_git_tracked_files(ref))
    missing = [name for name in _PACK_ENTRY_FILES if name not in tracked]
    if missing:
        raise RuntimeError(
            f'{", ".join(missing)} not tracked at {ref!r} — commit them '
            f'before packaging the ribbon pack.'
        )


def copy_pack_entry_files(dest_dir: str) -> None:
    """Copy the installer/uninstaller/README/LICENSE from the WORKING
    TREE (not git archive) — same rationale as package_release.py's
    copy_installer_scripts(): these ARE the distribution entry points and
    must exist to package a release at all; the preflight check above
    enforces that they're committed first."""
    for name in _PACK_ENTRY_FILES:
        shutil.copy2(os.path.join(_REPO_ROOT, name), os.path.join(dest_dir, name))


def write_bundled_manifest(dest_dir: str, manifest: dict, pack_version: str,
                            min_core_version: str) -> None:
    """Write ribbon_pack_manifest.json into the pack root — the SELF-
    CONTAINED copy RibbonPack_Install.py / RibbonPack_Uninstall.py read at
    install/uninstall time (a buyer has no access to the dev repo's own
    paid_manifest.json). min_core_version is ALWAYS the value freshly read
    from this build's own version.py (read_core_version()), never
    whatever paid_manifest.json happened to have on disk — so the version
    gate is never stale even if the source manifest wasn't hand-bumped."""
    bundled = {
        'version': manifest.get('version', '1.0'),
        'pack_version': pack_version,
        'min_core_version': min_core_version,
        'files': manifest.get('files', []),
    }
    with open(os.path.join(dest_dir, 'ribbon_pack_manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump(bundled, fh, indent=2)
        fh.write('\n')


def zip_directory(src_dir: str, zip_path: str) -> None:
    if os.path.exists(zip_path):
        os.remove(zip_path)
    base_name = os.path.basename(src_dir)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for fname in files:
                full = os.path.join(root, fname)
                arcname = os.path.join(base_name, os.path.relpath(full, src_dir))
                zf.write(full, arcname)


def list_zip_contents(zip_path: str) -> list:
    with zipfile.ZipFile(zip_path, 'r') as zf:
        return zf.namelist()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD', help='git ref to package (default: HEAD)')
    parser.add_argument('--version', default='', help='override the pack version string (default: date-shortsha)')
    args = parser.parse_args()

    ref = args.ref
    _preflight_check_pack_entry_files_committed(ref)

    pack_version = _resolve_version(args.version, ref)
    dist_name = f'FabricatorRibbonPack_v{pack_version}'
    dest_dir = os.path.join(_BUILD_DIR, dist_name)

    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir)

    print(f'[build_ribbon_pack] Packaging {ref} as {dist_name}...')

    with tempfile.TemporaryDirectory(prefix='fab_ribbonpack_') as tmp:
        extract_dir = _extract_ref_tree(ref, tmp)
        manifest = load_manifest(extract_dir)
        min_core_version = read_core_version(extract_dir)

        data_dir = os.path.join(dest_dir, 'RibbonPack_Data')
        os.makedirs(data_dir, exist_ok=True)
        copied = copy_manifest_payload(extract_dir, manifest, data_dir)

    copy_pack_entry_files(dest_dir)
    write_bundled_manifest(dest_dir, manifest, pack_version, min_core_version)

    zip_path = os.path.join(_BUILD_DIR, f'{dist_name}.zip')
    zip_directory(dest_dir, zip_path)

    contents = list_zip_contents(zip_path)
    print(f'[build_ribbon_pack] Wrote {zip_path}')
    print(f'[build_ribbon_pack] {len(contents)} files in zip.')
    print(f'[build_ribbon_pack] Pack version: {pack_version}')
    print(f'[build_ribbon_pack] min_core_version (fresh from version.py): {min_core_version}')
    print(f'[build_ribbon_pack] {len(copied)} manifest files packaged:')
    for rel in sorted(copied):
        print(f'    - {rel}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
