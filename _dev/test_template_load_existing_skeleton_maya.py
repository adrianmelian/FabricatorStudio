# _dev/test_template_load_existing_skeleton_maya.py
"""mayapy test: loading a template into a scene that ALREADY holds the
template's skeleton still wires the registry root joint.

Regression for the 2026-07-24 template-drop bug: _load_impl tears down
and respawns the registry, but _create_skeleton_from_blueprint only
registered the root from joints CREATED during that load. Dropping a
template onto a scene whose joints all pre-exist (the additive-load
case the skip-existing branch deliberately allows) created nothing, so
the fresh registry stayed rootless and build_armature() died at
_resolve_root with 'Armature: no root joint'.

Fix: resolve roots over every blueprint joint present in the scene,
created or pre-existing.

Run (userSetup prepends the build copy to sys.path during standalone
init, so this harness re-pins its target AFTER initialize; default is
the depot source, FS_TEST_TARGET=build exercises the shipped copy):
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_template_load_existing_skeleton_maya.py
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []

TEMPLATE = str(REPO_ROOT / 'maya_tools' / 'rigging' / 'fabricator'
               / 'templates' / 'Simple_Biped.blueprint.yaml')


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


def test_reload_onto_existing_skeleton_keeps_registry_root():
    """Load the template twice into one scene. The second load walks the
    skip-existing branch for every joint (all pre-exist from load one)
    and must still leave the registry wired to a root."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator import nodes

    cmds.file(new=True, force=True)
    fs_app.load(TEMPLATE)
    root_first = nodes.get_registry_root_joint()
    assert root_first and cmds.objExists(root_first), (
        f"first load left no registry root: {root_first!r}")

    # Second load onto the now-populated scene — the reported repro.
    fs_app.load(TEMPLATE)
    root_second = nodes.get_registry_root_joint()
    assert root_second and cmds.objExists(root_second), (
        f"reload onto existing skeleton left no registry root: "
        f"{root_second!r}")
    assert root_second == root_first, (
        f"reload switched roots: {root_first!r} -> {root_second!r}")


def test_partial_overlap_still_wires_root():
    """Only the ROOT pre-exists: every created joint has a joint parent,
    so the old created-only logic found no root here either."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator.blueprint import io as blueprint_io
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator import nodes

    bp = blueprint_io.read_yaml(TEMPLATE)
    bp_root = next(j.name for j in bp.skeleton_joints if j.parent is None)

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    cmds.joint(name=bp_root, position=(0.0, 0.0, 0.0))

    fs_app.load(TEMPLATE)
    root = nodes.get_registry_root_joint()
    assert root and cmds.objExists(root), (
        f"partial-overlap load left no registry root: {root!r}")
    assert root.split('|')[-1] == bp_root, (
        f"expected root {bp_root!r}, registry has {root!r}")


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')
    # userSetup's FABRICATOR block prepended the BUILD copy's root during
    # initialize and maya_tools may already be imported from it; purge and
    # re-pin so the test exercises the intended target.
    if os.environ.get('FS_TEST_TARGET') != 'build':
        for mod in [m for m in sys.modules if m.split('.')[0] == 'maya_tools']:
            del sys.modules[mod]
        sys.path.insert(0, str(REPO_ROOT))
    import maya_tools.rigging.fabricator.fs_app as _fs
    print(f"  target fs_app: {_fs.__file__}")
    try:
        check("test_reload_onto_existing_skeleton_keeps_registry_root",
              test_reload_onto_existing_skeleton_keeps_registry_root)
        check("test_partial_overlap_still_wires_root",
              test_partial_overlap_still_wires_root)
    finally:
        try:
            maya.standalone.uninitialize()
        except Exception:
            pass

    if FAILURES:
        print(f"TEMPLATE LOAD EXISTING SKELETON TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("TEMPLATE LOAD EXISTING SKELETON TESTS: OK")


if __name__ == "__main__":
    main()
