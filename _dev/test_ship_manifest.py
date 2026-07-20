# _dev/test_ship_manifest.py
"""Tests for ship_manifest.json + package_release's enforcement helpers.

Pure logic only — nothing here builds a zip or touches git. The
real-tree roll-call test keeps the ledger honest: adding a tool
directory without ruling it in the manifest fails this suite the same
way it would fail the build.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_ship_manifest.py
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import package_release as pr

FAILURES = []


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


def test_manifest_loads_and_is_well_formed():
    m = pr.load_ship_manifest()
    assert isinstance(m['rules'], dict) and m['rules']
    assert isinstance(m['known_dirs'], list) and m['known_dirs']
    for prefix, rule in m['rules'].items():
        assert isinstance(rule.get('ship'), bool), f'{prefix}: ship must be bool'
        assert rule.get('note'), f'{prefix}: every rule carries a note'


def test_noship_prefixes_cover_the_split_out_tools():
    m = pr.load_ship_manifest()
    prefixes = pr.manifest_noship_prefixes(m)
    for expected in ('skinning/dem_bones_bake/', 'skinning/unirig_export/',
                     'framework/installer/', 'pipeline/',
                     'skinning/auto_skin/', 'skinning/bbw_skin/',
                     'animation/icons/'):
        assert expected in prefixes, (expected, prefixes)


def test_animation_relics_are_file_excluded():
    """The 2017 Animation Toolbox registry relics (Adrian's ship-review,
    2026-07-19) never ship; the live pose/anim libraries and their
    load-bearing widgets do."""
    m = pr.load_ship_manifest()
    prefixes = pr.manifest_noship_prefixes(m)
    assert pr._is_excluded('animation/icon.png', frozenset(), prefixes)
    assert pr._is_excluded('animation/settings.json', frozenset(), prefixes)
    assert pr._is_excluded('animation/icons/icon.png', frozenset(), prefixes)
    assert not pr._is_excluded('animation/pose_library/ui.py',
                               frozenset(), prefixes)
    assert not pr._is_excluded('animation/selection_sets_widget.py',
                               frozenset(), prefixes)
    assert not pr._is_excluded('animation/characters_panel.py',
                               frozenset(), prefixes)


def test_noship_rules_actually_exclude():
    m = pr.load_ship_manifest()
    prefixes = pr.manifest_noship_prefixes(m)
    assert pr._is_excluded('skinning/dem_bones_bake/dem_bones_bake_app.py',
                           frozenset(), prefixes)
    assert pr._is_excluded('framework/installer/ml_deps_ui.py',
                           frozenset(), prefixes)
    assert not pr._is_excluded('skinning/autoskin/autoskin_ui.py',
                               frozenset(), prefixes)
    assert not pr._is_excluded('framework/first_run.py',
                               frozenset(), prefixes)


def test_unknown_dirs_flags_new_unruled_directories():
    m = pr.load_ship_manifest()
    paths = [
        'skinning/autoskin/autoskin_ui.py',        # known
        'brand_new_area/tool/thing.py',            # unknown at both depths
        'skinning/sneaky_new_tool/x.py',           # unknown at depth 2
        'framework/_dev/test_x.py',                # _dev: ruled elsewhere
        'loose_root_file.py',                      # maya_tools root: fine
    ]
    unknown = pr.manifest_unknown_dirs(paths, m)
    assert 'brand_new_area' in unknown, unknown
    assert 'brand_new_area/tool' in unknown, unknown
    assert 'skinning/sneaky_new_tool' in unknown, unknown
    assert not any(u.startswith('skinning/autoskin') for u in unknown)
    assert not any('_dev' in u for u in unknown)


def test_real_tree_is_fully_ruled():
    """THE honesty gate: the working tree's maya_tools/ must be fully
    acknowledged by known_dirs, exactly as the build will demand."""
    m = pr.load_ship_manifest()
    root = REPO_ROOT / 'maya_tools'
    rel_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        rel_root = os.path.relpath(dirpath, root).replace('\\', '/')
        rel_root = '' if rel_root == '.' else rel_root + '/'
        rel_files.extend(f'{rel_root}{f}' for f in filenames)
    unknown = pr.manifest_unknown_dirs(rel_files, m)
    assert unknown == [], (
        'directories on disk not acknowledged in ship_manifest.json — '
        f'rule them before cutting: {unknown}')


def test_free_templates_never_mention_ribbon_types():
    """Content gate (Adrian's near-miss 2026-07-19: a ribbon arm nearly
    rode out inside Simple_Biped): every template YAML the FREE zip ships
    (all templates NOT listed in paid_manifest.json) must contain zero
    Ribbon references. File-level exclusion cannot catch what a shipped
    file says; this can."""
    import json
    paid = json.load(open(
        REPO_ROOT / 'maya_tools/rigging/fabricator/paid_manifest.json',
        encoding='utf-8'))
    paid_files = {p.replace('maya_tools/', '', 1) for p in paid['files']}
    tpl_dir = REPO_ROOT / 'maya_tools/rigging/fabricator/templates'
    checked, hits = 0, []
    for p in sorted(tpl_dir.glob('*.yaml')):
        rel = f'rigging/fabricator/templates/{p.name}'
        if rel in paid_files:
            continue                      # paid templates may say Ribbon
        checked += 1
        for i, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
            if 'Ribbon' in line:
                hits.append(f'{p.name}:{i}: {line.strip()}')
    assert checked >= 5, f'suspiciously few free templates checked ({checked})'
    assert hits == [], ('PAID LEAK in free templates: ' + '; '.join(hits[:5]))


def test_payload_yaml_scanner_catches_and_passes():
    """The packaged-tree scanner itself: a payload with a contaminated
    YAML is caught; a clean one passes."""
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    tpl = tmp / 'maya_tools' / 'templates'
    tpl.mkdir(parents=True)
    (tpl / 'Simple_X.blueprint.yaml').write_text(
        'components:\n- type: SimpleFK\n', encoding='utf-8')
    assert pr.scan_payload_yaml_for_paid_types(str(tmp)) == []
    (tpl / 'Simple_Y.blueprint.yaml').write_text(
        'components:\n- type: RibbonIKArm\n', encoding='utf-8')
    hits = pr.scan_payload_yaml_for_paid_types(str(tmp))
    assert hits and 'Simple_Y' in hits[0] and 'RibbonIKArm' in hits[0], hits


def test_known_dirs_sorted_and_unique():
    m = pr.load_ship_manifest()
    dirs = m['known_dirs']
    assert dirs == sorted(dirs), 'keep known_dirs sorted for reviewability'
    assert len(dirs) == len(set(dirs)), 'duplicate entries in known_dirs'


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'Running {len(tests)} tests from test_ship_manifest.py...')
    for name, fn in tests:
        check(name, fn)
    print(f'\n{len(tests) - len(FAILURES)} passed, {len(FAILURES)} failed '
          f'(of {len(tests)})')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
