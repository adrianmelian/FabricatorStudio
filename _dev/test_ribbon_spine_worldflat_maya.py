# _dev/test_ribbon_spine_worldflat_maya.py
"""mayapy scene tests for RibbonSpine's world-flat ctrl shapes (Adrian,
2026-07-11): the hip, COG, and chest ctrl SHAPES must read axis-aligned
in world space at build time — the build-time twin of Curve-O-Matic's
Swap (Worldspace) — while the ctrl TRANSFORMS keep their joint-matched
orientation (behavior untouched, only the drawn curve is flattened).
Mid ctrls are exempt (spheres; Adrian named exactly hip/COG/chest).

Proves:
  - test_spine_hip_cog_chest_shapes_world_flat: on a spine whose joints
    carry deliberately non-identity orientations, each of cog/hip/chest
    (a) keeps a non-identity world ROTATION on its transform, while
    (b) its shape CVs, read in WORLD space relative to the ctrl pivot,
    match the raw library shape verbatim (i.e. the drawn curve is
    axis-aligned in world). Mid ctrl object-space CVs still match the
    library verbatim (untouched by the flatten).
  - test_spine_world_flat_survives_rebuild: unbuild -> rebuild keeps
    cog/hip/chest world-flat with NO double-rotation, whichever path
    runs (authored cv_block restore of already-flat CVs, or a fresh
    library build + flatten).

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_ribbon_spine_worldflat_maya.py
"""
import sys
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


class Skip(Exception):
    """Raise from a test body to mark it SKIPPED (environment gap) — see
    test_ik_arm_maya.py's identical Skip class for the full rationale."""


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Skip as exc:
        SKIPS.append(f"{name}: {exc}")
        print(f"  SKIP: {name}: {exc}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


def _make_twisted_spine(prefix):
    """pelvis -> spine_01 -> spine_02 -> chest, rising in Y but slanted
    in X/Z, with a hand-authored jointOrient on every joint so NO ctrl in
    the build can end up world-aligned by accident — the flatten must do
    real work for the assertions to pass."""
    import maya.cmds as cmds
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 0, 0), name=f'{prefix}_root')
    pelvis = cmds.joint(p=(1, 90, 2), name=f'{prefix}_pelvis')
    s1 = cmds.joint(p=(3, 100, 5), name=f'{prefix}_spine_01')
    s2 = cmds.joint(p=(6, 110, 3), name=f'{prefix}_spine_02')
    chest = cmds.joint(p=(8, 120, -2), name=f'{prefix}_chest')
    for j, orient in ((pelvis, (28, 43, 17)), (s1, (5, -12, 9)),
                      (s2, (-8, 21, -4)), (chest, (33, -27, 12))):
        cmds.setAttr(f'{j}.jointOrient', *orient)
    cmds.select(clear=True)
    return root, pelvis, s1, s2, chest


def _add_world_component(component_id, root_joint):
    from maya_tools.rigging.fabricator import nodes
    nodes.create_component_node(
        component_id=component_id, component_type='World',
        joints=[root_joint], parent_plug='', side='md',
        options={}, persisted={},
    )


def _library_cvs(shape_name):
    """Object-space CVs of the named library shape, per curve shape, in
    builder order — the ground truth the world-flat assertion compares
    against. Built at origin/identity, so world == object space."""
    import maya.cmds as cmds
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
    ref = com.build_shape(shape_name, '_worldflat_ref_tmp')
    out = []
    for s in cmds.listRelatives(ref, shapes=True, type='nurbsCurve',
                                fullPath=True) or []:
        cvs = [tuple(cmds.xform(cv, q=True, ws=True, t=True))
               for cv in cmds.ls(f'{s}.cv[*]', flatten=True)]
        out.append(cvs)
    cmds.delete(ref)
    return out


def _ctrl_shape_cvs_world_rel_pivot(ctrl):
    """Each curve shape's CVs under ctrl, read in WORLD space, expressed
    relative to the ctrl's world pivot — what the eye actually sees."""
    import maya.cmds as cmds
    pivot = cmds.xform(ctrl, q=True, ws=True, t=True)
    out = []
    for s in cmds.listRelatives(ctrl, shapes=True, type='nurbsCurve',
                                fullPath=True) or []:
        cvs = []
        for cv in cmds.ls(f'{s}.cv[*]', flatten=True):
            w = cmds.xform(cv, q=True, ws=True, t=True)
            cvs.append((w[0] - pivot[0], w[1] - pivot[1], w[2] - pivot[2]))
        out.append(cvs)
    return out


def _ctrl_shape_cvs_object(ctrl):
    """Each curve shape's CVs under ctrl in OBJECT space (raw .getAttr on
    the controlPoints — position deltas only, no transform applied)."""
    import maya.cmds as cmds
    out = []
    for s in cmds.listRelatives(ctrl, shapes=True, type='nurbsCurve',
                                fullPath=True) or []:
        cvs = [tuple(cmds.xform(cv, q=True, os=True, t=True))
               for cv in cmds.ls(f'{s}.cv[*]', flatten=True)]
        out.append(cvs)
    return out


def _assert_cv_sets_match(got, want, label, tol=1e-4):
    assert len(got) == len(want), (
        f'{label}: shape count mismatch — got {len(got)}, want {len(want)}')
    for si, (gs, ws) in enumerate(zip(got, want)):
        assert len(gs) == len(ws), (
            f'{label}: shape {si} CV count mismatch — {len(gs)} vs {len(ws)}')
        for ci, (g, w) in enumerate(zip(gs, ws)):
            d = math.dist(g, w)
            assert d < tol, (
                f'{label}: shape {si} cv {ci} off by {d:.6f} — '
                f'got {tuple(round(v, 4) for v in g)}, '
                f'want {tuple(round(v, 4) for v in w)}')


def _assert_nonidentity_world_rotation(ctrl):
    import maya.cmds as cmds
    rot = cmds.xform(ctrl, q=True, ws=True, ro=True)
    assert any(abs(r) > 1.0 for r in rot), (
        f'{ctrl}: world rotation {rot} is ~identity — the fixture is not '
        f'exercising the flatten (test would pass vacuously)')


def _build_spine_scene(prefix):
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes, fs_app
    cmds.file(new=True, force=True)
    root, pelvis, s1, s2, chest = _make_twisted_spine(prefix)
    nodes.create_registry(f'{prefix}_bp')
    nodes.set_registry_root_joint(root)
    _add_world_component(f'{prefix}_world', root)
    nodes.create_component_node(
        component_id=f'{prefix}_C0', component_type='RibbonSpine',
        joints=[pelvis, s1, s2, chest], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()
    return root, pelvis, s1, s2, chest


def _spine_ctrls():
    """Resolve (cog, hip, chest, mid) ctrl names post-build."""
    import maya.cmds as cmds
    cog = 'cog_ctrl'
    hips = cmds.ls('*_spine_hip_ctrl') or []
    chests = cmds.ls('*_spine_chest_ctrl') or []
    mids = cmds.ls('*_spine_mid_00_ctrl') or []
    assert cmds.objExists(cog), 'cog_ctrl not built'
    assert hips and chests and mids, (
        f'spine ctrls missing: hip={hips} chest={chests} mid={mids}')
    return cog, hips[0], chests[0], mids[0]


def _assert_flat_quad(opts_shapes, tol=1e-4):
    """Shared assertion body: cog/hip/chest AND mid world-flat,
    transforms still oriented. opts_shapes = {ctrl_kind:
    library_shape_name}. (Mids joined the world-flat set 2026-07-10,
    Adrian: circle shape, drawn world-flat like the rest of the spine —
    the original sphere exemption retired with the shape change.)"""
    cog, hip, chest, mid = _spine_ctrls()
    for ctrl, shape_name in ((cog, opts_shapes['cog']),
                             (hip, opts_shapes['hip']),
                             (chest, opts_shapes['chest']),
                             (mid, opts_shapes['mid'])):
        _assert_nonidentity_world_rotation(ctrl)
        _assert_cv_sets_match(
            _ctrl_shape_cvs_world_rel_pivot(ctrl),
            _library_cvs(shape_name),
            f'{ctrl} (world-flat)', tol)


_DEFAULT_SHAPES = {'cog': 'cog_ctrl', 'hip': 'hip_ctrl',
                   'chest': 'cube_open', 'mid': 'circle'}


def test_spine_hip_cog_chest_and_mid_shapes_world_flat():
    _build_spine_scene('wflat')
    _assert_flat_quad(_DEFAULT_SHAPES)


def test_spine_world_flat_survives_rebuild():
    """unbuild -> rebuild must keep all four world-flat with no double
    rotation: whichever path runs (cv_block restore of already-flat CVs,
    or fresh build + flatten), the world-space reading is identical."""
    from maya_tools.rigging.fabricator import fs_app
    _build_spine_scene('wflatrb')
    fs_app.unbuild_modules()
    fs_app.build_modules()
    _assert_flat_quad(_DEFAULT_SHAPES)


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith('test_') and callable(fn)
    ]
    print(f'Running {len(tests)} tests from '
          f'test_ribbon_spine_worldflat_maya.py...')
    for name, fn in tests:
        check(name, fn)

    print(f'\n{len(tests) - len(FAILURES) - len(SKIPS)} passed, '
          f'{len(FAILURES)} failed, {len(SKIPS)} skipped '
          f'(of {len(tests)})')
    if FAILURES:
        print('\nFAILURES:')
        for f in FAILURES:
            print(f'  - {f}')
    if SKIPS:
        print('\nSKIPS:')
        for s in SKIPS:
            print(f'  - {s}')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
