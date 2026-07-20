# _dev/test_blueprint_version_stamp.py
"""Offscreen tests: the blueprint YAML carries its own fabricator_version
stamp (the FS build that WROTE the file).

Regression for the 2026-07-19 ribbon-arms template bug: fs_app.load()
blanks the scene registry's fabricator_version for the component-spawn
window so the version-gated legacy type map can fire, but the Blueprint
format carried no version of its own — so EVERY load read as legacy and
'IKArm' (the free basic arm, which reclaimed the vacated type string)
misloaded as 'RibbonIKArm' on 100% of template round-trips. The file
stamp is the missing signal: load now gates the legacy window on the
FILE's version, not on an unconditional blank.

Run:
py -3 _dev/test_blueprint_version_stamp.py
"""
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


def _roundtrip(bp):
    from maya_tools.rigging.fabricator.blueprint import io as blueprint_io
    with tempfile.TemporaryDirectory() as td:
        p = str(Path(td) / 'stamp_test.blueprint.yaml')
        blueprint_io.write_yaml(bp, p)
        return blueprint_io.read_yaml(p), Path(p).read_text(encoding='utf-8')


def test_schema_has_fabricator_version_default_empty():
    from maya_tools.rigging.fabricator.blueprint.schema import Blueprint
    bp = Blueprint(name='t')
    assert hasattr(bp, 'fabricator_version'), \
        'Blueprint lacks a fabricator_version field'
    assert bp.fabricator_version == '', \
        'default must be "" (legacy/unstamped)'


def test_io_roundtrips_stamp():
    from maya_tools.rigging.fabricator.blueprint.schema import Blueprint
    bp = Blueprint(name='t', fabricator_version='1.4.0')
    loaded, raw = _roundtrip(bp)
    assert loaded.fabricator_version == '1.4.0', \
        f'stamp lost in round-trip: {loaded.fabricator_version!r}'
    assert 'fabricator_version' in raw, 'stamp key missing from YAML text'


def test_io_unstamped_reads_empty():
    # A stamped-era build reading a pre-stamp legacy file must see ''.
    from maya_tools.rigging.fabricator.blueprint.schema import Blueprint
    bp = Blueprint(name='t')  # no stamp
    loaded, raw = _roundtrip(bp)
    assert loaded.fabricator_version == '', \
        f'unstamped file must read back "": {loaded.fabricator_version!r}'
    # Legacy files round-trip without churn: no key emitted when unset.
    assert 'fabricator_version' not in raw, \
        'unset stamp must not be emitted (legacy round-trip churn)'


def test_limb_io_roundtrips_stamp():
    # Fragment parity (2026-07-20): .limb.yaml carries the same file
    # stamp .blueprint.yaml gained in the ribbon-arms fix.
    from maya_tools.rigging.fabricator.limbs import io as limbs_io
    from maya_tools.rigging.fabricator.limbs.schema import LimbFragment
    from maya_tools.rigging.fabricator.blueprint.schema import JointSpec

    def _frag(**kw):
        return LimbFragment(
            name='t',
            skeleton_joints=[JointSpec(name='j1', parent='<EXTERNAL>')],
            **kw)

    with tempfile.TemporaryDirectory() as td:
        p = str(Path(td) / 'stamp_test.limb.yaml')

        limbs_io.write_yaml(_frag(fabricator_version='1.4.0'), p)
        loaded = limbs_io.read_yaml(p)
        assert loaded.fabricator_version == '1.4.0', \
            f'fragment stamp lost: {loaded.fabricator_version!r}'
        assert 'fabricator_version' in Path(p).read_text(encoding='utf-8')

        limbs_io.write_yaml(_frag(), p)   # unstamped (legacy-era shape)
        loaded = limbs_io.read_yaml(p)
        assert loaded.fabricator_version == '', \
            f'unstamped fragment must read "": {loaded.fabricator_version!r}'
        assert 'fabricator_version' not in Path(p).read_text(encoding='utf-8'), \
            'unset fragment stamp must not be emitted (round-trip churn)'


def test_save_path_stamps_current():
    # fs_app._snapshot_blueprint_from_scene needs Maya; instead pin the
    # contract that CURRENT is what save stamps by checking the constant
    # is importable and dotted-numeric (the mayapy suite covers the live
    # save path end-to-end).
    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION
    parts = FABRICATOR_VERSION.split('.')
    assert len(parts) == 3 and all(p.isdigit() for p in parts)


def main():
    check("test_schema_has_fabricator_version_default_empty",
          test_schema_has_fabricator_version_default_empty)
    check("test_io_roundtrips_stamp", test_io_roundtrips_stamp)
    check("test_io_unstamped_reads_empty", test_io_unstamped_reads_empty)
    check("test_limb_io_roundtrips_stamp", test_limb_io_roundtrips_stamp)
    check("test_save_path_stamps_current", test_save_path_stamps_current)

    if FAILURES:
        print(f"BLUEPRINT VERSION STAMP TESTS: {len(FAILURES)} FAILED "
              f"({len(SKIPS)} SKIP)")
        sys.exit(1)
    print("BLUEPRINT VERSION STAMP TESTS: OK - 0 SKIP")


if __name__ == "__main__":
    main()
