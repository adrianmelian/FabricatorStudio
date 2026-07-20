# package_release.py
"""Build the public Fabricator distribution zip from git-tracked files ONLY.

BINDING RULE: this script builds the zip via `git archive`, never a
working-directory copy. Untracked private files exist on disk (personal
project configs preserved outside version control, local experiment
scratch, etc.) and must be structurally impossible to leak — `git archive`
reads from a git tree/commit, not the filesystem, so anything not
`git add`-ed simply cannot appear in the output regardless of what sits
next to it on disk.

Output shape (matches Fabricator_Install.py's expectations):
    build/Fabricator_v<version>/
        Fabricator_Install.py
        Fabricator_Uninstall.py
        Fabricator_Data/
            maya_tools/      (curated — see _EXCLUDE_MAYA_TOOLS_PREFIXES / _EXCLUDE_MAYA_TOOLS_FILES below)
            icons/           (curated — see _EXCLUDE_ICONS_PREFIXES below)
            README.md        (explicit allowlist — see build_archive_tree)
            DEPENDENCIES.md  (explicit allowlist — see build_archive_tree)
            LICENSE          (BSL 1.1 — REQUIRED, see build_archive_tree)
            COMMERCIAL-LICENSE.md
            TRADEMARKS.md
            VERSION.txt
    build/Fabricator_v<version>.zip   <- the artifact to distribute

Usage:
    py -3 package_release.py                  # uses HEAD, auto version
    py -3 package_release.py --ref my-branch
    py -3 package_release.py --version 1.0.0

`build/` is gitignored (added by this task — see the diff). Re-running is
idempotent: the target folder is wiped and rebuilt each time.
"""
__author__ = "Adrian Melian"

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_BUILD_DIR = os.path.join(_REPO_ROOT, 'build')

# Directory prefixes (relative to maya_tools/, POSIX-style, trailing slash)
# excluded from the shipping package per CODE-AUDIT.md Lens 5's package
# surface. Excluded trees STAY in the git-tracked repo (this script reads
# from git, it does not delete anything) — they are simply not copied into
# Fabricator_Data/. Directory-prefix match only, never a substring match
# (e.g. "pipeline/" must not accidentally exclude "export/anim_pipeline.py").
_EXCLUDE_MAYA_TOOLS_PREFIXES = (
    'rigging/rigger/',       # legacy PyMEL rigger — TODO-riddled, PyMEL dep, superseded by Fabricator
    'pipeline/',             # rigged_model_updater — docstring uses personal asset names, not on Lens 5's include list
    # DemBones ships as a SEPARATE add-on package (Adrian 2026-07-19), like
    # AutoSkin's model distribution — the tool and its pip-deps installer
    # both leave the free zip. The skin popover probes for the package and
    # hides the entry when absent, so nothing shipped dangles.
    'skinning/dem_bones_bake/',
    'framework/installer/',  # ml_deps pip step — DemBones-only consumer, rides with it
    # scene_batch/ now SHIPS (Adrian 2026-07-11): promoted to the toolbar's Scene folder menu; the arbitrary-Python power is the point of the tool
    # NOTE: _dev/ folders are excluded at ANY depth by _is_excluded()'s
    # path-segment check, not by prefixes here — see that function.
)

# Individual files (not whole directories) excluded the same way.
_EXCLUDE_MAYA_TOOLS_FILES = {
    # Orphaned PyMEL utility — only ever called from the excluded legacy
    # rigger (rigging/rigger/rigger_tools.py), still had Python-2 print
    # statements (SyntaxError under Python 3.11) until this scan caught it.
    # No live caller once rigging/rigger/ is excluded; not worth carrying.
    'rigging/rig_utils.py',
    # Orphaned, unreferenced module found by the 2020-import descendant
    # scan (DESCENDANT-SCAN.md) — byte-for-byte identical to the 2020
    # import commit, zero live callers anywhere in the tree today (its
    # sibling pose_ghost_ui.py was already cut as dead code in Wave 1).
    # No functional loss to cut it; keeps the shipping surface free of
    # unreachable code.
    'animation/pose_ghost.py',
    # Internal dev backlog for Fabricator (sprint/task references, "Adrian
    # validated this manually" notes) — not user-facing. Caught by the W4
    # package-leak audit. COMPONENT_AUTHORING.md in the same directory is
    # intentionally kept; it's genuine dev-facing documentation with no
    # internal references.
    'rigging/fabricator/IDEAS.md',
    # 2017 "Animation Toolbox" registry relics (Adrian's ship-review,
    # 2026-07-19): the registry system that read settings.json and its
    # icon is long gone; zero consumers (animation/icons/ rides the
    # ship_manifest rule).
    'animation/icon.png',
    'animation/settings.json',
}

# Directory prefixes (relative to icons/, POSIX-style, trailing slash)
# excluded from the shipping package. The icon *concept-generation pipeline*
# is internal dev tooling (ComfyUI workflow JSON, API-key handling notes,
# absolute paths to Adrian's local ComfyUI install) — never shippable art,
# and its README leaks personal setup details. Caught by the W4 package-leak
# audit.
_EXCLUDE_ICONS_PREFIXES = (
    '_concepts/',
)

# The Advanced Ribbon Pack boundary manifest — SINGLE SOURCE OF TRUTH for
# which files are paid (packaging spec §2/§6.1, workspace/2026-07-10_
# ribbon-pack-packaging/SPEC.md). Consumed here (free zip EXCLUDES every
# manifest entry), by build_ribbon_pack.py (paid zip INCLUDES exactly
# these), and by _dev/test_open_core_import_boundary.py (free code may
# never import a manifest module) — so the paid file list can never drift
# between the three. Read from the ARCHIVED ref inside build_archive_tree()
# (not the live working tree) to stay consistent with this script's own
# BINDING RULE: package what `ref` actually contains, nothing from disk
# that isn't committed there.
_PAID_MANIFEST_REL_PATH = 'maya_tools/rigging/fabricator/paid_manifest.json'


def _load_paid_manifest_files(extract_dir: str) -> list:
    """Read paid_manifest.json's 'files' list from the archived `ref`
    tree (extract_dir, produced by build_archive_tree()'s git-archive
    extraction). Returns the raw repo-relative POSIX paths (e.g.
    'maya_tools/rigging/fabricator/modules/ribbon.py'). Returns an empty
    list — never raises — if the manifest is absent from this ref (an
    older ref packaged before the ribbon pack split existed); a missing
    manifest means nothing to exclude, not a packaging failure."""
    manifest_path = os.path.join(extract_dir, *_PAID_MANIFEST_REL_PATH.split('/'))
    if not os.path.isfile(manifest_path):
        return []
    with open(manifest_path, 'r', encoding='utf-8') as fh:
        manifest = json.load(fh)
    return [p.replace('\\', '/') for p in manifest.get('files', [])]


def _paid_manifest_maya_tools_excludes(extract_dir: str) -> set:
    """Paid-manifest file paths that live under maya_tools/, converted to
    paths RELATIVE TO maya_tools/ (the shape _is_excluded() checks), plus
    the manifest file itself (internal packaging metadata — not user-
    facing, so it does not ship in the free public zip either)."""
    out = set()
    for rel in _load_paid_manifest_files(extract_dir):
        if rel.startswith('maya_tools/'):
            out.add(rel[len('maya_tools/'):])
    out.add(_PAID_MANIFEST_REL_PATH[len('maya_tools/'):])
    return out


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
    """All files git tracks at `ref`, POSIX-style relative paths."""
    output = _run_git(['ls-tree', '-r', '--name-only', ref])
    return [line for line in output.splitlines() if line]


def _is_excluded(rel_path_in_maya_tools: str,
                 extra_file_excludes: frozenset = frozenset(),
                 extra_prefixes: tuple = ()) -> bool:
    """extra_file_excludes carries the paid-manifest exclusion set (ribbon
    pack boundary), computed per-ref inside build_archive_tree() since it
    depends on paid_manifest.json's contents at that ref. extra_prefixes
    carries ship_manifest.json's no-ship directory rules (Adrian's
    ledger). Everything else here is a static, hand-authored constant."""
    if rel_path_in_maya_tools in _EXCLUDE_MAYA_TOOLS_FILES:
        return True
    if rel_path_in_maya_tools in extra_file_excludes:
        return True
    # Internal test suites live in a _dev/ folder at ANY depth. This was once
    # a hand-listed set of prefixes (framework/_dev/, framework/toolbar/_dev/),
    # which silently shipped every _dev/ folder added afterwards — export/,
    # rigging/fabricator/ and skinning/autoskin/ all leaked their tests into
    # the free zip. Matched as a path SEGMENT, so a file merely named
    # "my_dev_notes.py" is unaffected.
    if '_dev' in rel_path_in_maya_tools.split('/'):
        return True
    for prefix in _EXCLUDE_MAYA_TOOLS_PREFIXES + tuple(extra_prefixes):
        if rel_path_in_maya_tools.startswith(prefix):
            return True
    return False


def _is_icons_excluded(rel_path_in_icons: str) -> bool:
    for prefix in _EXCLUDE_ICONS_PREFIXES:
        if rel_path_in_icons.startswith(prefix):
            return True
    return False


# ─────────────────────────────────────────────
# ship_manifest.json — Adrian's last-line-of-defense ledger
# (2026-07-19). Directory-level ship/no-ship decisions live THERE,
# reviewed by Adrian before every cut; this script enforces them.
# ─────────────────────────────────────────────

SHIP_MANIFEST_PATH = os.path.join(_REPO_ROOT, 'ship_manifest.json')


def load_ship_manifest(path: str = None) -> dict:
    """Parse ship_manifest.json. Missing or malformed is a HARD build
    error — packaging without the ledger would defeat its purpose."""
    p = path or SHIP_MANIFEST_PATH
    try:
        with open(p, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f'package_release: cannot read {p}: {exc}')
    if not isinstance(data.get('rules'), dict) or \
            not isinstance(data.get('known_dirs'), list):
        raise RuntimeError(
            f'package_release: {p} malformed — needs a "rules" dict and a '
            f'"known_dirs" list')
    return data


def manifest_noship_prefixes(manifest: dict) -> tuple:
    """Directory prefixes (trailing slash, maya_tools-relative) the
    manifest rules OUT of the free zip."""
    return tuple(sorted(
        prefix.rstrip('/') + '/'
        for prefix, rule in manifest['rules'].items()
        if isinstance(rule, dict) and not rule.get('ship', False)))


def manifest_unknown_dirs(file_paths, manifest: dict) -> list:
    """maya_tools-relative file paths -> the depth-1/2 ancestor dirs
    that known_dirs does not acknowledge. _dev/ and __pycache__ segments
    are ruled elsewhere and skipped. Non-empty means the build refuses:
    a new tool directory cannot ship without being ruled in the ledger."""
    known = set(manifest['known_dirs'])
    unknown = set()
    for rel in file_paths:
        parts = rel.replace('\\', '/').split('/')
        if '_dev' in parts or '__pycache__' in parts:
            continue
        if len(parts) < 2:
            continue                    # loose file at maya_tools root
        for depth in (1, 2):
            if len(parts) > depth:
                d = '/'.join(parts[:depth])
                if d not in known:
                    unknown.add(d)
    return sorted(unknown)


def build_archive_tree(ref: str, dest_dir: str) -> list:
    """Extract `git archive ref` into a temp dir, then copy the curated
    subset into dest_dir/Fabricator_Data/. Returns the list of excluded
    maya_tools/ paths for the report.

    Two-step (archive-to-temp, then filtered-copy) rather than piping
    `git archive` straight into the final layout, so the exclude filter is
    one readable pass over a real directory tree instead of juggling tar
    member selection.
    """
    with tempfile.TemporaryDirectory(prefix='fab_pkg_') as tmp:
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

        payload_dir = os.path.join(dest_dir, 'Fabricator_Data')
        os.makedirs(payload_dir, exist_ok=True)

        excluded = []

        # icons/ ships filtered — the icons/_concepts/ dev pipeline
        # (ComfyUI workflow + internal README) is excluded; see
        # _EXCLUDE_ICONS_PREFIXES.
        src_icons = os.path.join(extract_dir, 'icons')
        dest_icons = os.path.join(payload_dir, 'icons')
        if os.path.isdir(src_icons):
            for root, dirs, files in os.walk(src_icons):
                rel_root = os.path.relpath(root, src_icons).replace('\\', '/')
                rel_root = '' if rel_root == '.' else rel_root + '/'

                keep_dirs = []
                for d in dirs:
                    candidate = f'{rel_root}{d}/'
                    if _is_icons_excluded(candidate):
                        excluded.append(f'icons/{candidate}')
                        continue
                    keep_dirs.append(d)
                dirs[:] = keep_dirs

                if rel_root and _is_icons_excluded(rel_root):
                    continue  # defensive; the prune above should already skip this

                for fname in files:
                    rel_file = f'{rel_root}{fname}'
                    if _is_icons_excluded(rel_file):
                        excluded.append(f'icons/{rel_file}')
                        continue
                    src_file = os.path.join(root, fname)
                    dest_file = os.path.join(dest_icons, rel_root.replace('/', os.sep), fname)
                    os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                    shutil.copy2(src_file, dest_file)

        # Root-level user-facing docs ship at the top of Fabricator_Data/.
        # Explicit allowlist (not "copy everything at repo root") — repo
        # root also tracks CLAUDE.md and CHANGELOG.md, which are internal
        # and must never ship (see the W4 package-leak audit).
        #
        # LICENSE is REQUIRED, not optional: BSL 1.1 states "You must
        # conspicuously display this License on each original or modified
        # copy of the Licensed Work", and the sub-$2M revenue grant is the
        # whole commercial boundary — shipping the toolset without its terms
        # would undercut it. COMMERCIAL-LICENSE.md and TRADEMARKS.md ride
        # along because README links to them (broken links inside the zip
        # otherwise). Missing LICENSE is a hard failure below, never a
        # silent skip.
        for doc_name in ('README.md', 'DEPENDENCIES.md',
                          'COMMERCIAL-LICENSE.md', 'TRADEMARKS.md'):
            src_doc = os.path.join(extract_dir, doc_name)
            if os.path.isfile(src_doc):
                shutil.copy2(src_doc, os.path.join(payload_dir, doc_name))

        src_license = os.path.join(extract_dir, 'LICENSE')
        if not os.path.isfile(src_license):
            raise RuntimeError(
                'package_release: LICENSE is missing from the archived tree. '
                'BSL 1.1 requires the license to accompany every copy of the '
                'Licensed Work; refusing to build an unlicensed package.'
            )
        shutil.copy2(src_license, os.path.join(payload_dir, 'LICENSE'))

        # maya_tools/ ships filtered. The ribbon-pack boundary manifest is
        # read from THIS ref's own archived tree (paid_manifest.json is the
        # single source of truth shared with build_ribbon_pack.py and
        # _dev/test_open_core_import_boundary.py — see
        # _paid_manifest_maya_tools_excludes()'s docstring).
        paid_excludes = frozenset(_paid_manifest_maya_tools_excludes(extract_dir))
        src_maya_tools = os.path.join(extract_dir, 'maya_tools')
        dest_maya_tools = os.path.join(payload_dir, 'maya_tools')

        # Ship-manifest gate (Adrian's ledger): every directory in the
        # archived tree must be acknowledged in known_dirs BEFORE anything
        # copies — an unruled directory is a hard refusal, not a warning —
        # and the ledger's no-ship rules join the exclusion prefixes.
        manifest = load_ship_manifest()
        ledger_prefixes = manifest_noship_prefixes(manifest)
        _all_rel_files = []
        for root, _dirs, files in os.walk(src_maya_tools):
            rel_root = os.path.relpath(root, src_maya_tools).replace('\\', '/')
            rel_root = '' if rel_root == '.' else rel_root + '/'
            _all_rel_files.extend(f'{rel_root}{f}' for f in files)
        unknown = manifest_unknown_dirs(_all_rel_files, manifest)
        if unknown:
            raise RuntimeError(
                'package_release: directories not acknowledged in '
                'ship_manifest.json known_dirs — rule them (ship or not) '
                'before cutting: ' + ', '.join(unknown))

        for root, dirs, files in os.walk(src_maya_tools):
            rel_root = os.path.relpath(root, src_maya_tools).replace('\\', '/')
            rel_root = '' if rel_root == '.' else rel_root + '/'

            # Prune excluded directories in-place so os.walk doesn't descend.
            keep_dirs = []
            for d in dirs:
                candidate = f'{rel_root}{d}/'
                if _is_excluded(candidate, paid_excludes, ledger_prefixes):
                    excluded.append(f'maya_tools/{candidate}')
                    continue
                keep_dirs.append(d)
            dirs[:] = keep_dirs

            if rel_root and _is_excluded(rel_root, paid_excludes, ledger_prefixes):
                continue  # defensive; the prune above should already skip this

            for fname in files:
                rel_file = f'{rel_root}{fname}'
                if _is_excluded(rel_file, paid_excludes, ledger_prefixes):
                    excluded.append(f'maya_tools/{rel_file}')
                    continue
                src_file = os.path.join(root, fname)
                dest_file = os.path.join(dest_maya_tools, rel_root.replace('/', os.sep), fname)
                os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                shutil.copy2(src_file, dest_file)

        # CONTENT gate (Adrian's near-miss, 2026-07-19: a ribbon arm
        # nearly rode out INSIDE Simple_Biped): file-level exclusion
        # cannot see what a shipped YAML says. After every exclusion has
        # run, no YAML left in the payload may mention a paid Ribbon
        # component type.
        paid_hits = scan_payload_yaml_for_paid_types(payload_dir)
        if paid_hits:
            raise RuntimeError(
                'package_release: paid component references inside shipped '
                'YAML (a free template must never mention Ribbon types):\n  '
                + '\n  '.join(paid_hits[:20]))

    return excluded


_PAID_TYPE_MARKER = 'Ribbon'


def scan_payload_yaml_for_paid_types(payload_dir: str) -> list:
    """Scan every .yaml/.yml under the payload's maya_tools/ (skipping the
    vendored yaml LIBRARY at _vendor/) for the paid Ribbon marker. Returns
    'file:line: text' hits; the build refuses on any. Pure and
    path-driven so _dev/test_ship_manifest.py exercises it directly."""
    hits = []
    root = os.path.join(payload_dir, 'maya_tools')
    for dirpath, _dirnames, filenames in os.walk(root):
        if '_vendor' in dirpath.replace('\\', '/').split('/'):
            continue
        for f in filenames:
            if not f.endswith(('.yaml', '.yml')):
                continue
            p = os.path.join(dirpath, f)
            try:
                with open(p, 'r', encoding='utf-8') as fh:
                    for i, line in enumerate(fh, 1):
                        if _PAID_TYPE_MARKER in line:
                            rel = os.path.relpath(p, payload_dir).replace('\\', '/')
                            hits.append(f'{rel}:{i}: {line.strip()}')
            except OSError as exc:
                hits.append(f'{p}: unreadable ({exc})')
    return hits


def copy_installer_scripts(dest_dir: str) -> None:
    """Copy Fabricator_Install.py + Fabricator_Uninstall.py from the
    WORKING TREE (not git archive) — these two files ARE the distribution
    entry points and must exist to package a release at all; requiring
    them to be committed first is enforced by the pre-flight check in
    main(), not by reading them from disk here twice."""
    # INSTALL.txt rides along at the zip ROOT (Adrian, 2026-07-20): the
    # three-step quick start, the first thing a user sees on unzip,
    # sitting beside the file they are told to drag.
    for name in ('INSTALL.txt', 'Fabricator_Install.py',
                 'Fabricator_Uninstall.py'):
        shutil.copy2(os.path.join(_REPO_ROOT, name), os.path.join(dest_dir, name))


def write_version_file(dest_dir: str, version: str, ref: str) -> None:
    payload_dir = os.path.join(dest_dir, 'Fabricator_Data')
    with open(os.path.join(payload_dir, 'VERSION.txt'), 'w', encoding='utf-8') as fh:
        fh.write(f'Fabricator {version}\n')
        fh.write(f'Built from: {ref}\n')
        fh.write(f'Packaged: {datetime.datetime.now().isoformat(timespec="seconds")}\n')


def zip_directory(src_dir: str, zip_path: str) -> None:
    if os.path.exists(zip_path):
        os.remove(zip_path)
    base_name = os.path.basename(src_dir)
    parent_dir = os.path.dirname(src_dir)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for fname in files:
                full = os.path.join(root, fname)
                arcname = os.path.join(base_name, os.path.relpath(full, src_dir))
                zf.write(full, arcname)
    _ = parent_dir  # kept for clarity of intent; not used beyond documentation


def list_zip_contents(zip_path: str) -> list:
    with zipfile.ZipFile(zip_path, 'r') as zf:
        return zf.namelist()


# ─────────────────────────────────────────────
# Icon presence check
# ─────────────────────────────────────────────
#
# CP3 caught shelf icons missing entirely after install — root cause was
# that icons/*.png were never git-tracked (caught by a blanket *.png
# .gitignore rule), so git archive (the only source this script ever
# reads from — see the BINDING RULE at the top of this file) could not see
# them regardless of any exclusion-filter logic. This check makes that
# class of regression loud and immediate: if the repo-owned icon count in
# the built package ever drops back near zero, packaging fails outright
# instead of silently shipping an icon-less build again.
#
# The discriminator between "our icon" (must ship) and "Maya built-in
# icon" (skip — resolves from Maya's own search path, not this repo) is
# SOURCE-INDEPENDENT: since the icons/FSShelf/ flatten (2026-07-11) shelf
# JSONs reference bare filenames for both kinds (e.g. 'fs_export.png' and
# 'polyExtrudeFacet.png' side by side — there is no path prefix left to
# key off of). So this check intersects every bare .png name referenced by
# the packaged shelf JSONs against the actual *.png set sitting in this
# repo's own SOURCE icons/ directory (not the packaged copy) to isolate
# the ones this repo must ship.

# Every image filename any shelf_data/*.json currently references —
# computed once at build time from the actual JSON, not hardcoded, so this
# check tracks shelf_data/ automatically as buttons are added/removed/
# renamed rather than needing manual upkeep.
_SHELF_DATA_DIR_REL = os.path.join('maya_tools', 'framework', 'shelves', 'shelf_data')


def _referenced_shelf_icon_names(dest_dir: str) -> set:
    """Scan every shelf_data/*.json in the built package and return the
    set of bare image filenames referenced (no '/' in the value, ending
    '.png'). This includes BOTH this repo's own icons and Maya's built-in
    icon names (e.g. 'polyExtrudeFacet.png') indiscriminately — the caller
    intersects against the source icons/ tree to isolate the ones this
    repo actually owns and must ship."""
    import json as _json

    shelf_data_dir = os.path.join(dest_dir, 'Fabricator_Data', _SHELF_DATA_DIR_REL)
    referenced = set()
    if not os.path.isdir(shelf_data_dir):
        return referenced

    for fname in os.listdir(shelf_data_dir):
        if not fname.endswith('.json'):
            continue
        with open(os.path.join(shelf_data_dir, fname), 'r', encoding='utf-8') as fh:
            data = _json.load(fh)
        for btn in data.get('buttons', []):
            image = btn.get('image', '')
            if image and '/' not in image and image.endswith('.png'):
                referenced.add(image)

    return referenced


def check_icons_present(dest_dir: str) -> tuple:
    """Assert the package actually contains every one of THIS REPO's own
    icons that its own shelf JSON files reference. Returns (ok: bool,
    detail: str).

    Deliberately checks against the LIVE shelf JSON contents (not a
    hardcoded minimum count) — a hardcoded "at least N icons" threshold
    would not have caught this exact bug landing (icons/ had zero tracked
    PNGs, which is very much "at least 0"), and would silently stop being
    meaningful the moment someone adds or removes a shelf button. Checking
    "every referenced icon this repo owns is present" is the actual
    invariant that matters.
    """
    referenced = _referenced_shelf_icon_names(dest_dir)
    if not referenced:
        return False, (
            'No bare *.png image references found in any shelf_data/*.json '
            '— either the shelf JSONs are missing from the package, or '
            'nothing on the shelves has an icon anymore. Either way, this is '
            'unexpected enough to fail packaging rather than assume it\'s fine.'
        )

    source_icons_dir = os.path.join(_REPO_ROOT, 'icons')
    source_icons = set()
    if os.path.isdir(source_icons_dir):
        source_icons = {f for f in os.listdir(source_icons_dir) if f.endswith('.png')}

    ours = referenced & source_icons
    if not ours:
        return False, (
            'None of the referenced shelf images match a *.png in this '
            f'repo\'s source {source_icons_dir!r} — either the referenced '
            'names all resolve to Maya built-ins (unexpected — this repo '
            'ships its own shelf icons) or the source icons/ tree itself '
            'is empty/missing. Either way, this is unexpected enough to '
            'fail packaging rather than assume it\'s fine.'
        )

    icons_dir = os.path.join(dest_dir, 'Fabricator_Data', 'icons')
    present = set()
    if os.path.isdir(icons_dir):
        present = {f for f in os.listdir(icons_dir) if f.endswith('.png')}

    missing = sorted(ours - present)
    if missing:
        return False, (
            f'{len(missing)} of {len(ours)} referenced icon(s) this repo '
            f'owns missing from the package: {missing}. Checked: {icons_dir}'
        )

    return True, f'{len(ours)}/{len(ours)} referenced icons present in the package.'


# ─────────────────────────────────────────────
# UI-construction smoke test
# ─────────────────────────────────────────────
#
# py_compile only catches syntax errors — it cannot catch a runtime
# AttributeError from calling a method that doesn't exist on a widget class
# (e.g. QCheckBox.setWordWrap, which only exists on QLabel). That exact bug
# shipped in Fabricator_v2026-07-01-df5289b and crashed the installer dialog
# at construction on Adrian's machine. This step actually IMPORTS each
# installer/dialog module and CONSTRUCTS the dialog classes (no .exec()/
# .show(), no event loop) inside a real mayapy.exe subprocess running
# PySide6 with QT_QPA_PLATFORM=offscreen — so it exercises the real Qt
# widget API, not a mock.
#
# Resolving mayapy here does NOT use utils.maya.skin_subprocess.resolve_mayapy()
# — that function derives the path from sys.executable, which is Maya's own
# exe when called from INSIDE Maya GUI. package_release.py runs under a
# plain `py -3` interpreter with no such relationship, so it needs the same
# env-var + known-install-path fallback chain already used by
# export/anim_pipeline.py._find_mayapy() and scene_batch/scene_batch_app.py.
# _find_mayapy() below is a local, packaging-time analogue of that pattern
# rather than a shared import, to keep this script runnable even before the
# package tree it's about to test is verified importable.

_SMOKE_TEST_TIMEOUT_S = 60

# QApplication MUST exist before any MQtUtil call — omui.MQtUtil.mainWindow()
# segfaults (not a clean exception) when called with no QApplication
# instance yet. Confirmed empirically against this Maya install. Every
# dialog's __init__ eventually calls get_maya_main_window() when parent is
# falsy, so the smoke script constructs QApplication first, exactly the
# order production code hits (Maya GUI already has one running).
_SMOKE_TEST_SCRIPT = r'''
import os
import sys
import traceback

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PAYLOAD_DIR = sys.argv[1]
INSTALLER_SCRIPT_DIR = sys.argv[2]

sys.path.insert(0, os.path.join(PAYLOAD_DIR, 'maya_tools', '_vendor'))
sys.path.insert(0, PAYLOAD_DIR)

from PySide6 import QtWidgets  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

failures = []


def _try(label, fn):
    try:
        fn()
        print(f'[smoke] OK: {label}')
    except Exception:
        print(f'[smoke] FAIL: {label}')
        traceback.print_exc()
        failures.append(label)


def _load_module_from_path(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _construct_installer():
    mod = _load_module_from_path(
        'Fabricator_Install',
        os.path.join(INSTALLER_SCRIPT_DIR, 'Fabricator_Install.py'),
    )
    dlg = mod.FabricatorInstallerUI(installer_dir=INSTALLER_SCRIPT_DIR)
    dlg.deleteLater()


def _construct_uninstaller():
    mod = _load_module_from_path(
        'Fabricator_Uninstall',
        os.path.join(INSTALLER_SCRIPT_DIR, 'Fabricator_Uninstall.py'),
    )
    dlg = mod.FabricatorUninstallerUI()
    dlg.deleteLater()


# (MLDepsInstallDialog smoke removed 2026-07-19: framework/installer/
# left the free zip with the DemBones add-on split; the packaged tree
# deliberately has no such module to construct. ASCII only in this
# embedded script: it rides a subprocess command line.)


def _check_icon_resolves_under_simulated_install():
    """Simulate the ACTUAL installed layout (a fresh temp dir with
    maya_tools/ and icons/ copied in as siblings, exactly what
    Fabricator_Install.py produces under Documents/maya/scripts/) and
    confirm fs_shelf.py's _resolve_image() finds a real icon file there.

    This is what the previous CP3 pass on this bug got wrong just testing
    module construction from the packaging tree in place, since the packaging
    tree already happens to have maya_tools/ and icons/ as siblings
    (Fabricator_Data/maya_tools, Fabricator_Data/icons), which would
    PASS even with the installer's old sibling-of-scripts_dir bug, since
    that bug is specifically about what happens after install_payload()
    moves them to their FINAL install locations. So this check builds a
    separate, throwaway "simulated scripts_dir" and copies into it the
    same way install_payload() does, rather than importing straight out
    of Fabricator_Data/.
    """
    import shutil as _shutil
    import tempfile as _tempfile

    with _tempfile.TemporaryDirectory(prefix='fab_install_sim_') as sim_root:
        sim_scripts_dir = os.path.join(sim_root, 'scripts')
        os.makedirs(sim_scripts_dir)
        _shutil.copytree(
            os.path.join(PAYLOAD_DIR, 'maya_tools'),
            os.path.join(sim_scripts_dir, 'maya_tools'),
        )
        _shutil.copytree(
            os.path.join(PAYLOAD_DIR, 'icons'),
            os.path.join(sim_scripts_dir, 'icons'),
        )

        sys.path.insert(0, sim_scripts_dir)
        for name in [k for k in list(sys.modules)
                     if k == 'maya_tools' or k.startswith('maya_tools.')]:
            del sys.modules[name]

        from maya_tools.framework.shelves import fs_shelf

        resolved = fs_shelf._resolve_image('fs_fabricator_shelf.png')
        if not os.path.isfile(resolved):
            raise AssertionError(
                f'fs_shelf._resolve_image(\"fs_fabricator_shelf.png\") '
                f'resolved to {resolved!r}, which does not exist on disk '
                f'under the simulated install layout ({sim_scripts_dir}). '
                f'This is the exact class of bug that shipped with no '
                f'icons on the shelf after install.'
            )

        sys.path.remove(sim_scripts_dir)
        for name in [k for k in list(sys.modules)
                     if k == 'maya_tools' or k.startswith('maya_tools.')]:
            del sys.modules[name]


_try('FabricatorInstallerUI construction', _construct_installer)
_try('FabricatorUninstallerUI construction', _construct_uninstaller)
_try('icon path resolves under simulated install layout', _check_icon_resolves_under_simulated_install)

if failures:
    print(f'[smoke] {len(failures)} failure(s): {failures}')
    sys.exit(1)
print('[smoke] all dialog classes constructed cleanly, icon path resolves')
sys.exit(0)
'''


def _find_mayapy() -> str:
    """Resolve mayapy.exe via env var -> known install-path fallbacks.
    Same shape as export/anim_pipeline.py._find_mayapy() and
    scene_batch/scene_batch_app.py._find_mayapy() — packaging-time code
    can't assume sys.executable is Maya's own exe the way in-Maya-GUI
    callers can (that's what utils.maya.skin_subprocess.resolve_mayapy()
    assumes, and why it isn't reused here)."""
    maya_loc = os.environ.get('MAYA_LOCATION') or ''
    if maya_loc:
        candidate = os.path.join(maya_loc, 'bin', 'mayapy.exe')
        if os.path.isfile(candidate):
            return candidate

    for fallback in (
        r'C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe',
        r'C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe',
        r'C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe',
    ):
        if os.path.isfile(fallback):
            return fallback

    return ''


def run_ui_smoke_test(dist_dir: str) -> tuple:
    """Import + construct (no exec/show) FabricatorInstallerUI,
    FabricatorUninstallerUI, and MLDepsInstallDialog inside a real
    mayapy.exe subprocess running PySide6 offscreen. Catches the class of
    bug py_compile cannot: a method call that doesn't exist on the actual
    widget class (e.g. QCheckBox.setWordWrap — a QLabel-only method).

    Returns (ok: bool, detail: str). ok=False fails packaging (see main()).
    If mayapy can't be located at all, returns (False, <reason>) rather
    than silently skipping — a missing verification step is itself a
    finding to report, not something to swallow. Falls back to
    run_ast_widget_method_check() as an ADDITIONAL check either way (never
    a substitute) so a mayapy-less environment still gets some coverage.
    """
    mayapy = _find_mayapy()
    payload_dir = os.path.join(dist_dir, 'Fabricator_Data')

    if not mayapy:
        return False, (
            'mayapy.exe not found (checked MAYA_LOCATION and standard '
            'Maya 2024/2025/2026 install paths) — cannot run the real '
            'PySide6 UI-construction smoke test. This is a verification '
            'gap, not a pass: fix the mayapy path or run this check '
            'manually before shipping.'
        )

    # PYTHONDONTWRITEBYTECODE: without it, importing modules out of
    # dist_dir/Fabricator_Data writes __pycache__/*.pyc directly into the
    # packaging tree we're about to zip — caught once already (12 stray
    # .pyc/__pycache__ entries showed up in a real build before this fix).
    # _purge_pycache() below is the belt-and-suspenders cleanup in case
    # anything else (this script's own AST walk imports nothing, but a
    # future check might) leaves cache files behind regardless of env var.
    env = dict(os.environ)
    env['PYTHONDONTWRITEBYTECODE'] = '1'

    try:
        result = subprocess.run(
            [mayapy, '-c', _SMOKE_TEST_SCRIPT, payload_dir, dist_dir],
            capture_output=True, text=True, timeout=_SMOKE_TEST_TIMEOUT_S,
            check=False, env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f'UI smoke test timed out after {_SMOKE_TEST_TIMEOUT_S}s'
    except OSError as exc:
        return False, f'Could not launch mayapy subprocess: {exc}'
    finally:
        _purge_pycache(dist_dir)

    output = (result.stdout or '') + (result.stderr or '')
    if result.returncode != 0:
        return False, f'UI smoke test failed (exit {result.returncode}):\n{output}'
    return True, output


def _purge_pycache(dist_dir: str) -> None:
    """Delete every __pycache__/ dir and stray .pyc under dist_dir.
    Runs in a `finally` after the smoke test regardless of outcome —
    importing modules for the smoke test can write bytecode cache
    directly into the packaging tree; this must not survive into the
    zip whether the test passed or failed."""
    for root, dirs, files in os.walk(dist_dir):
        if '__pycache__' in dirs:
            shutil.rmtree(os.path.join(root, '__pycache__'), ignore_errors=True)
            dirs.remove('__pycache__')
        for fname in files:
            if fname.endswith('.pyc'):
                try:
                    os.remove(os.path.join(root, fname))
                except OSError:
                    pass


# ─────────────────────────────────────────────
# AST fallback — runs regardless, in addition to the mayapy smoke test
# ─────────────────────────────────────────────
#
# Static, no interpreter needed: flags calls to known QLabel-only methods
# on variables assigned from a non-QLabel Qt widget constructor in the same
# function. Deliberately narrow (this is a supplement, not a substitute for
# the real construction test above) — it only catches the exact shape of
# bug that shipped (a label-only method invoked on a widget instantiated
# a few lines earlier in the same scope), not the general case of methods
# resolved through helper functions, inheritance, or cross-function state.

import ast  # noqa: E402

# method_name -> the ONE QWidget subclass (by simple class name) it's
# actually defined on, for every method this codebase calls that has a
# real chance of being label-only. Extend this table if a new label-only
# (or otherwise single-widget-only) method shows up in a dialog file.
_LABEL_ONLY_METHODS = {
    'setWordWrap': 'QLabel',
    'setAlignment': None,  # too many valid owners (QLabel, layouts, etc.) — not checked
}

# Qt constructor call -> the widget class it produces, for the constructors
# actually used in this repo's installer/dialog files.
_KNOWN_WIDGET_CONSTRUCTORS = {
    'QLabel', 'QCheckBox', 'QPushButton', 'QRadioButton', 'QComboBox',
    'QSpinBox', 'QDoubleSpinBox', 'QLineEdit', 'QPlainTextEdit',
    'QTextEdit', 'QProgressBar', 'QFrame', 'QDialog', 'QWidget',
    'QGroupBox', 'QListWidget', 'QTreeWidget', 'QTabWidget',
}


def _ast_widget_class_of_call(node) -> str:
    """Return 'QLabel' for `QtWidgets.QLabel(...)` or `QLabel(...)`."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ''


def check_ast_widget_methods(py_source_paths: list) -> list:
    """Static check: for each function body, track `var = Qt<Widget>(...)`
    assignments, then flag `var.<label_only_method>(...)` calls where the
    tracked class isn't the one the method actually belongs to.

    Returns a list of human-readable finding strings (empty = clean).
    Intentionally conservative — only flags variables whose constructor
    call is directly visible in the same function scope, so it won't
    false-positive on `mindmeld_style.button(...)`-style factory returns
    (unknown class, skipped) the way a naive check might.
    """
    findings = []
    for path in py_source_paths:
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                source = fh.read()
            tree = ast.parse(source, filename=path)
        except (OSError, SyntaxError):
            continue

        for func_node in ast.walk(tree):
            if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            var_classes = {}
            for node in ast.walk(func_node):
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                        and len(node.targets) == 1):
                    cls = _ast_widget_class_of_call(node.value)
                    if cls in _KNOWN_WIDGET_CONSTRUCTORS:
                        target = node.targets[0]
                        var_name = None
                        if isinstance(target, ast.Name):
                            var_name = target.id
                        elif isinstance(target, ast.Attribute):
                            var_name = target.attr  # self._foo -> "_foo"
                        if var_name:
                            var_classes[var_name] = cls

            for node in ast.walk(func_node):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                method_name = node.func.attr
                if method_name not in _LABEL_ONLY_METHODS:
                    continue
                owner_class = _LABEL_ONLY_METHODS[method_name]
                if owner_class is None:
                    continue

                obj = node.func.value
                var_name = None
                if isinstance(obj, ast.Name):
                    var_name = obj.id
                elif isinstance(obj, ast.Attribute):
                    var_name = obj.attr  # self._foo.method() -> "_foo"

                if var_name and var_name in var_classes and var_classes[var_name] != owner_class:
                    findings.append(
                        f'{path}:{node.lineno}: {var_name}.{method_name}() called, but '
                        f'{var_name} was constructed as {var_classes[var_name]} '
                        f'(only {owner_class} has {method_name})'
                    )
    return findings


def _preflight_check_scripts_committed(ref: str) -> None:
    """Refuse to package if Fabricator_Install.py / Fabricator_Uninstall.py
    have uncommitted changes relative to `ref` when ref is a commit-ish
    that should already contain them — guards against shipping a zip whose
    installer differs from what's in version control. Skipped when ref is
    a dirty/working concept the caller explicitly opted into (not
    currently exposed as a flag; keep this strict by default)."""
    tracked = set(_git_tracked_files(ref))
    missing = [
        name for name in ('INSTALL.txt', 'Fabricator_Install.py',
                          'Fabricator_Uninstall.py')
        if name not in tracked
    ]
    if missing:
        raise RuntimeError(
            f'{", ".join(missing)} not tracked at {ref!r} — commit them before '
            f'packaging a release. package_release.py only ships committed code.'
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD', help='git ref to package (default: HEAD)')
    parser.add_argument('--version', default='', help='override the version string (default: date-shortsha)')
    parser.add_argument('--keep-tree', action='store_true',
                         help='keep the unzipped build/Fabricator_v.../ folder after zipping (default: keep — this flag is a no-op reserved for a future --zip-only mode)')
    args = parser.parse_args()

    ref = args.ref
    _preflight_check_scripts_committed(ref)

    version = _resolve_version(args.version, ref)
    dist_name = f'Fabricator_v{version}'
    dest_dir = os.path.join(_BUILD_DIR, dist_name)

    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir)

    print(f'[package_release] Packaging {ref} as {dist_name}...')
    excluded = build_archive_tree(ref, dest_dir)
    copy_installer_scripts(dest_dir)
    write_version_file(dest_dir, version, ref)

    # ---- Verification gate — runs BEFORE the zip is reported as done ----
    print('[package_release] Checking icons/ shelf icon coverage...')
    icons_ok, icons_detail = check_icons_present(dest_dir)
    print(f'[package_release] {icons_detail}')
    if not icons_ok:
        raise RuntimeError(
            'Icon coverage check failed — see detail above. This is the '
            'exact class of bug that shipped an icon-less shelf after '
            'install (see WAVE2-REPORT.md).'
        )

    # AST check always runs (fast, no dependencies). The real mayapy+PySide6
    # construction test is the authoritative one; AST is a supplement that
    # still gives coverage if mayapy can't be found in this environment.
    print('[package_release] Running AST widget-method check...')
    py_files = []
    for root, _dirs, files in os.walk(dest_dir):
        for fname in files:
            if fname.endswith('.py'):
                py_files.append(os.path.join(root, fname))
    ast_findings = check_ast_widget_methods(py_files)
    if ast_findings:
        print(f'[package_release] AST check FOUND {len(ast_findings)} issue(s):')
        for finding in ast_findings:
            print(f'    - {finding}')
        raise RuntimeError(
            'AST widget-method check failed — a label-only method call was '
            'found on a non-label widget. Fix before packaging (see above).'
        )
    print('[package_release] AST check clean.')

    print('[package_release] Running mayapy + PySide6 offscreen UI smoke test...')
    smoke_ok, smoke_detail = run_ui_smoke_test(dest_dir)
    print(smoke_detail)
    if not smoke_ok:
        raise RuntimeError(
            'UI-construction smoke test failed — see output above. Fix the '
            'dialog construction bug before packaging (this is exactly the '
            'class of bug py_compile cannot catch).'
        )
    print('[package_release] UI smoke test clean — all dialog classes construct without error.')

    # Final defensive sweep — _purge_pycache() already runs after the smoke
    # test itself, but zip whatever's actually in dest_dir at zip time, not
    # what should be there.
    _purge_pycache(dest_dir)

    zip_path = os.path.join(_BUILD_DIR, f'{dist_name}.zip')
    zip_directory(dest_dir, zip_path)

    contents = list_zip_contents(zip_path)

    # ---- About-card stamp gate (Adrian, 2026-07-19) ----
    # The Bridge button's About card reports the build from
    # Fabricator_Data/VERSION.txt (the installer copies it beside
    # maya_tools/). Every cut zip MUST carry the stamp with THIS cut's
    # version string, or installed users see 'Development build' where the
    # build number belongs. Verified against the ZIP, not the staging tree:
    # what shipped, not what should have.
    stamp_arc = f'{dist_name}/Fabricator_Data/VERSION.txt'
    stamp_names = [n for n in contents if n.replace('\\', '/') == stamp_arc]
    if not stamp_names:
        raise RuntimeError(
            f'About-card stamp gate FAILED: {stamp_arc} is not in the zip. '
            'The installed About card would read "Development build".'
        )
    with zipfile.ZipFile(zip_path, 'r') as zf:
        stamp_first = zf.read(stamp_names[0]).decode('utf-8').splitlines()[0].strip()
    expected_stamp = f'Fabricator {version}'
    if stamp_first != expected_stamp:
        raise RuntimeError(
            f'About-card stamp gate FAILED: VERSION.txt opens with '
            f'{stamp_first!r}, expected {expected_stamp!r}. The About card '
            'would report a stale build number.'
        )
    print(f'[package_release] About-card stamp gate: {stamp_first!r} in zip, '
          'matches this cut.')

    print(f'[package_release] Wrote {zip_path}')
    print(f'[package_release] {len(contents)} files in zip.')
    print(f'[package_release] Excluded {len(excluded)} paths (see below).')
    for path in sorted(excluded):
        print(f'    - excluded: {path}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
