"""Armature x-ray ball ctrl (Adrian, 2026-07-13).

    mayapy -m pytest is not how this repo runs; invoke directly:
    PYTHONNOUSERSITE=1 mayapy _dev/test_armature_xray_ctrl_maya.py

The two traps this suite exists to catch, both MEASURED, not assumed:

  1. Maya does not serialize `alwaysDrawOnTop`. Save a scene, reopen it,
     and every ball is buried inside the character again — with nothing
     in the file to say anything is wrong. A test that only checks the
     attribute right after build() would pass forever while the feature
     was broken for every artist who ever saved their work.

  2. The ctrl stopped being a curve. `measure_live_ctrl_scale()` recovers
     the user's ctrl_scale by measuring shape points, and it used to read
     `.cv[*]` only — which returns [] on a mesh. It would have gone on
     returning None forever, and the skeletal exporter's break/export/
     rebuild round trip would have silently resized every ctrl to 1.0.
     Silent, plausible, and wrong: exactly the failure worth a test.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import maya.standalone

maya.standalone.initialize(name='python')

import maya.cmds as cmds  # noqa: E402

from maya_tools.rigging.fabricator import armature as amt  # noqa: E402
from maya_tools.rigging.joint_orient import joint_orient_app as joa  # noqa: E402

PASSED = []
FAILED = []


def check(name, fn):
    cmds.file(new=True, force=True)
    try:
        fn()
    except AssertionError as e:
        FAILED.append((name, str(e)))
        print(f'  FAIL  {name}\n        {e}', flush=True)
    except Exception as e:                                    # noqa: BLE001
        import traceback
        FAILED.append((name, f'{type(e).__name__}: {e}'))
        print(f'  ERROR {name}\n        {type(e).__name__}: {e}', flush=True)
        traceback.print_exc()
    else:
        PASSED.append(name)
        print(f'  ok    {name}', flush=True)


def _chain(n=3, spacing=5.0):
    """A root + n children down +Y. The root wears NO ctrl by design."""
    cmds.select(clear=True)
    joints = []
    for i in range(n + 1):
        j = cmds.joint(name=f'jnt{i}', position=(0, i * spacing, 0))
        joints.append(j)
    cmds.select(clear=True)
    return joints


def _ctrl_shape(ctrl):
    return cmds.listRelatives(ctrl, shapes=True, fullPath=True)[0]


# ── the ball itself ───────────────────────────────────────────

def test_ctrl_is_a_mesh_not_a_curve():
    """The whole point. nurbsSurface has no alwaysDrawOnTop; a mesh does."""
    _chain()
    amt.build_armature(root='jnt0')
    ctrl = amt.ctrl_for_joint('jnt1')
    assert ctrl, 'jnt1 got no ctrl'
    shp = _ctrl_shape(ctrl)
    assert cmds.nodeType(shp) == 'mesh', \
        f'ctrl shape is {cmds.nodeType(shp)}, must be mesh'


def test_ctrl_draws_on_top():
    _chain()
    amt.build_armature(root='jnt0')
    shp = _ctrl_shape(amt.ctrl_for_joint('jnt1'))
    assert cmds.getAttr(f'{shp}.alwaysDrawOnTop') is True, \
        'the x-ray is off — the ball will be buried in the mesh'
    assert cmds.getAttr(f'{shp}.backfaceCulling') == 3, \
        'backfaces not culled: with the depth test off they fight the front'
    assert cmds.getAttr(f'{shp}.castsShadows') is False
    assert cmds.getAttr(f'{shp}.receiveShadows') is False


def test_root_still_wears_no_ctrl():
    """Unchanged by the ball: the root is the origin anchor."""
    _chain()
    amt.build_armature(root='jnt0')
    assert amt.ctrl_for_joint('jnt0') is None, 'the root grew a ctrl'


def test_orb_is_the_radius_factor_of_the_joint_radius():
    """Adrian, 2026-07-13: finger orbs were too fat for the tight chain. The
    orb renders at _BALL_RADIUS_FACTOR x the joint's display radius (x
    ctrl_scale). The baked object-space extent IS that world size, because the
    transform's own scale is frozen to 1,1,1."""
    _chain()
    cmds.setAttr('jnt1.radius', 2.0)
    amt.build_armature(root='jnt0', ctrl_scale=1.0)
    ctrl = amt.ctrl_for_joint('jnt1')
    extent = amt._max_shape_extent(ctrl)
    expected = 2.0 * amt._BALL_RADIUS_FACTOR
    assert abs(extent - expected) < 0.02, \
        f'orb extent {extent:.3f}, expected {expected:.3f} ' \
        f'(radius 2.0 x factor {amt._BALL_RADIUS_FACTOR})'
    assert amt._BALL_RADIUS_FACTOR < 1.0, \
        'the factor must shrink the orb, not grow it'


# ── colour: the wire AND the shaded body ──────────────────────

def test_side_colours_survive_the_switch_to_mesh():
    """Left blue, right red — Adrian's scheme, unchanged. On a mesh
    overrideColor tints the WIREFRAME, which is still what you see when
    the ctrl is selected, so the existing colour code still earns its keep."""
    cmds.select(clear=True)
    cmds.joint(name='root_jnt', position=(0, 0, 0))
    cmds.joint(name='lf_arm_jnt', position=(5, 0, 0))
    cmds.select(clear=True)
    cmds.joint(name='root2_jnt', position=(0, 0, 0))
    cmds.joint(name='rt_arm_jnt', position=(-5, 0, 0))
    cmds.parent('root2_jnt', 'root_jnt')
    cmds.select(clear=True)

    amt.build_armature(root='root_jnt')

    lf = _ctrl_shape(amt.ctrl_for_joint('lf_arm_jnt'))
    rt = _ctrl_shape(amt.ctrl_for_joint('rt_arm_jnt'))
    assert cmds.getAttr(f'{lf}.overrideColor') == amt._COLOR_LEFT, \
        'left ctrl is not blue'
    assert cmds.getAttr(f'{rt}.overrideColor') == amt._COLOR_RIGHT, \
        'right ctrl is not red'


def test_shaded_body_gets_a_flat_shader():
    """overrideColor does NOT colour a mesh's shaded surface — a shading
    engine does. Without this the balls are all default grey in shaded mode
    no matter what colour the component picked."""
    _chain()
    amt.build_armature(root='jnt0')
    ctrl = amt.ctrl_for_joint('jnt1')
    sgs = cmds.listConnections(_ctrl_shape(ctrl), type='shadingEngine') or []
    assert sgs, 'ctrl has no shading engine — it will render default grey'
    sg = sgs[0]
    shader = cmds.listConnections(f'{sg}.surfaceShader') or []
    assert shader, f'{sg} has nothing driving surfaceShader'
    assert cmds.nodeType(shader[0]) == 'surfaceShader', \
        (f'shader is {cmds.nodeType(shader[0])}, must be surfaceShader — a '
         f'lit material both costs more and breaks the depth-off silhouette')


def test_shaders_are_shared_not_one_per_ctrl():
    """A 50-joint rig must not create 50 shaders. One per COLOUR."""
    _chain(n=8)
    amt.build_armature(root='jnt0')
    ctrls = [amt.ctrl_for_joint(f'jnt{i}') for i in range(1, 9)]
    ctrls = [c for c in ctrls if c]
    assert len(ctrls) >= 5, f'expected a real chain of ctrls, got {len(ctrls)}'

    shaders = cmds.ls(f'{amt._SHADER_PREFIX}*_SS', type='surfaceShader') or []
    assert 0 < len(shaders) <= 4, \
        (f'{len(shaders)} shaders for {len(ctrls)} ctrls — they are not being '
         f'shared; at most 4 colours exist')


def test_shader_colour_matches_the_override_index():
    """The wire colour and the body colour are driven off the SAME index,
    so they cannot drift apart."""
    _chain()
    amt.build_armature(root='jnt0')
    shp = _ctrl_shape(amt.ctrl_for_joint('jnt1'))
    index = cmds.getAttr(f'{shp}.overrideColor')

    sg = (cmds.listConnections(shp, type='shadingEngine') or [])[0]
    shader = (cmds.listConnections(f'{sg}.surfaceShader') or [])[0]
    body = cmds.getAttr(f'{shader}.outColor')[0]
    expected = amt._index_rgb(index)
    for got, want in zip(body, expected):
        assert abs(got - want) < 1e-4, \
            f'shaded body {body} does not match index {index} -> {expected}'


def test_shaders_die_with_the_armature():
    """They are SHARED, so deleting the ctrl meshes does not collect them.
    Without explicit teardown they pile up one set per build/unbuild."""
    _chain()
    amt.build_armature(root='jnt0')
    assert cmds.ls(f'{amt._SHADER_PREFIX}*_SS') , 'no shaders were made'
    amt.delete_armature()
    left = (cmds.ls(f'{amt._SHADER_PREFIX}*_SS') or []) \
        + (cmds.ls(f'{amt._SHADER_PREFIX}*_SG') or [])
    assert not left, f'{len(left)} shader node(s) survived the teardown: {left}'


# ── THE TRAP: alwaysDrawOnTop is not saved ────────────────────

def test_maya_does_not_save_alwaysDrawOnTop():
    """The premise the whole re-apply hook rests on, tested on a BARE mesh
    that is NOT an Armature ctrl — reapply_xray() only touches shapes under
    the Armature group, so it provably cannot reach this one. Without that
    isolation the hook would restore the flag and this test would report that
    Maya saves it, which is the opposite of the truth.

    Asserts Maya's actual (bad) behaviour on purpose: if a future Maya starts
    writing .adot, this fails LOUDLY and tells us the hook is now redundant."""
    ball = cmds.polySphere(name='loose_ball', constructionHistory=False)[0]
    shp = cmds.listRelatives(ball, shapes=True, fullPath=True)[0]
    cmds.setAttr(f'{shp}.alwaysDrawOnTop', 1)
    cmds.setAttr(f'{shp}.backfaceCulling', 3)
    assert not amt.armature_exists(), \
        'this test must run with NO Armature, or the hook contaminates it'

    tmp = os.path.join(cmds.internalVar(userTmpDir=True), 'loose_ball.ma')
    cmds.file(rename=tmp)
    cmds.file(save=True, type='mayaAscii', force=True)
    cmds.file(new=True, force=True)
    cmds.file(tmp, open=True, force=True)

    shp = cmds.listRelatives('loose_ball', shapes=True, fullPath=True)[0]
    # The control: backfaceCulling DOES save. It proves the round trip really
    # happened, so the assert below is not a false pass on a scene that never
    # went to disk.
    assert cmds.getAttr(f'{shp}.backfaceCulling') == 3, \
        'backfaceCulling did not survive — the round trip itself is suspect'
    assert cmds.getAttr(f'{shp}.alwaysDrawOnTop') is False, (
        'Maya now SAVES alwaysDrawOnTop. Good news — but reapply_xray() and '
        'its scene hook are now redundant and this test is the wrong shape.')


def test_xray_survives_a_save_and_reopen():
    """What the artist actually cares about, end to end: build, save, come
    back tomorrow, and the balls still draw through the mesh.

    This passes ONLY because the kAfterOpen hook re-arms the flag — Maya
    itself drops it (see test_maya_does_not_save_alwaysDrawOnTop). Remove the
    hook and this goes red, which is exactly the point."""
    _chain()
    amt.build_armature(root='jnt0')

    tmp = os.path.join(cmds.internalVar(userTmpDir=True), 'amt_xray.ma')
    cmds.file(rename=tmp)
    cmds.file(save=True, type='mayaAscii', force=True)
    cmds.file(new=True, force=True)
    cmds.file(tmp, open=True, force=True)

    shapes = amt.ctrl_shapes()
    assert len(shapes) >= 3, f'the Armature did not survive the reopen: {shapes}'
    for shp in shapes:
        assert cmds.getAttr(f'{shp}.alwaysDrawOnTop') is True, \
            f'{shp} came back BURIED — the scene-open hook did not re-arm it'


def test_reapply_xray_restores_a_stripped_flag():
    """reapply_xray() in isolation, with the flag manually stripped — so the
    cure is tested even if the hook's own registration ever breaks."""
    _chain()
    amt.build_armature(root='jnt0')

    for shp in amt.ctrl_shapes():
        cmds.setAttr(f'{shp}.alwaysDrawOnTop', 0)
    assert all(cmds.getAttr(f'{s}.alwaysDrawOnTop') is False
               for s in amt.ctrl_shapes()), 'failed to strip the flag'

    n = amt.reapply_xray()
    assert n >= 3, f'reapply_xray only touched {n} ctrl(s)'
    for shp in amt.ctrl_shapes():
        assert cmds.getAttr(f'{shp}.alwaysDrawOnTop') is True, \
            f'{shp} is still buried'


def test_reapply_xray_is_safe_with_no_armature():
    """The scene hook fires on EVERY open, most of which have no Armature."""
    assert amt.reapply_xray() == 0
    assert amt.ctrl_shapes() == []


# ── THE OTHER TRAP: the ctrl_scale measurement ────────────────

def test_measure_live_ctrl_scale_reads_a_MESH_ctrl():
    """It used to read `.cv[*]` only. On a ball that is [], so it returned
    None — and the skeletal exporter would have silently resized every one
    of the user's ctrls back to 1.0 on its next round trip."""
    _chain()
    amt.build_armature(root='jnt0', ctrl_scale=1.0)
    got = amt.measure_live_ctrl_scale()
    assert got is not None, \
        'measure_live_ctrl_scale returned None on a mesh ctrl — the exporter ' \
        'round trip would silently reset the user\'s ctrl scale'
    assert abs(got - 1.0) < 0.02, f'expected ~1.0, got {got}'


def test_measure_live_ctrl_scale_recovers_a_custom_scale():
    """The real job: a non-default scale must survive break/export/rebuild."""
    _chain()
    amt.build_armature(root='jnt0', ctrl_scale=2.5)
    got = amt.measure_live_ctrl_scale()
    assert got is not None, 'returned None on a custom scale'
    assert abs(got - 2.5) < 0.05, f'expected ~2.5, got {got}'


def test_max_shape_extent_still_reads_a_CURVE():
    """curve_o_matic.swap_shape can still put a curve on a ctrl by hand, so
    the measurement must read BOTH. Reading only vtx would repeat the exact
    bug in the other direction."""
    # A degree-1 curve, so the CVs ARE the points — a NURBS circle's control
    # hull sits outside its radius (a radius-3 circle measures 3.32), which
    # would make the assertion a guess rather than a fact.
    crv = cmds.curve(name='probe_crv', degree=1,
                     point=[(0, 0, 0), (3.0, 0, 0), (0, 0, 1.0)])
    got = amt._max_shape_extent(crv)
    assert abs(got - 3.0) < 1e-3, f'curve extent {got}, expected 3.0'

    ball = amt._build_ball('probe_ball')
    got = amt._max_shape_extent(ball)
    assert abs(got - 1.0) < 1e-3, \
        (f'ball extent {got}, expected exactly 1.0 — _build_ball must stay '
         f'radius-1 or measure_live_ctrl_scale silently mis-scales')


# ── the aimers and the guide lines must win too ───────────────

def test_aimers_draw_on_top():
    """Adrian, 2026-07-13: "now the aimers are hard to see". The balls draw
    over EVERYTHING, aimers included — fixing the ctrls without fixing the
    aimers just moves the problem somewhere else."""
    _chain()
    amt.build_armature(root='jnt0')
    shapes = joa.aimer_shapes()
    assert shapes, 'the build produced no aimers to check'
    for shp in shapes:
        assert cmds.getAttr(f'{shp}.alwaysDrawOnTop') is True, \
            f'aimer {shp} is buried under the ctrl balls'


def test_guide_lines_draw_on_top():
    """The lines carry the connection story; buried, they carry nothing."""
    _chain()
    amt.build_armature(root='jnt0')
    lines = cmds.listRelatives(amt._GRP, allDescendents=True,
                               type='nurbsCurve', fullPath=True) or []
    assert lines, 'the build produced no guide lines to check'
    for shp in lines:
        assert cmds.getAttr(f'{shp}.alwaysDrawOnTop') is True, \
            f'guide line {shp} is buried under the ctrl balls'


def test_reapply_xray_covers_ctrls_lines_AND_aimers():
    """One hook, everything it must reach. Maya drops the flag on all three."""
    _chain()
    amt.build_armature(root='jnt0')

    everything = amt.ctrl_shapes() + joa.aimer_shapes()
    for shp in everything:
        cmds.setAttr(f'{shp}.alwaysDrawOnTop', 0)

    n = amt.reapply_xray()
    assert n == len(everything), \
        f'reapply_xray touched {n} of {len(everything)} shapes'
    for shp in everything:
        assert cmds.getAttr(f'{shp}.alwaysDrawOnTop') is True, \
            f'{shp} was left buried'


def test_aimers_survive_a_save_and_reopen():
    """Same trap as the balls: Maya drops .adot on the aimer curves too."""
    _chain()
    amt.build_armature(root='jnt0')

    tmp = os.path.join(cmds.internalVar(userTmpDir=True), 'amt_aimers.ma')
    cmds.file(rename=tmp)
    cmds.file(save=True, type='mayaAscii', force=True)
    cmds.file(new=True, force=True)
    cmds.file(tmp, open=True, force=True)

    shapes = joa.aimer_shapes()
    assert shapes, 'the aimers did not survive the reopen'
    for shp in shapes:
        assert cmds.getAttr(f'{shp}.alwaysDrawOnTop') is True, \
            f'aimer {shp} came back buried — the scene hook missed it'


# ── the regression the mesh ctrls introduced ──────────────────

def test_scene_mesh_scan_excludes_the_ctrl_balls():
    """Making the ctrls MESHES made them visible to every scene-wide geometry
    scan — and a ctrl ball is, technically, an unskinned mesh. Without this,
    'Select unskinned meshes' hands a rigger in edit mode fifty ctrl balls.

    Asserts BOTH directions: the ctrls are gone AND the real geometry survives.
    An exclusion that also ate the character would pass a one-sided test."""
    from maya_tools.framework import utilities as utils

    _chain()
    body = cmds.polySphere(name='character_body')[0]
    amt.build_armature(root='jnt0')

    found = [s.split('|')[-1] for s in utils.scene_mesh_shapes()]

    assert any('character_body' in n for n in found), \
        f'the real geometry was excluded too — the filter is too greedy: {found}'
    leaked = [n for n in found if '_amt_CTL' in n]
    assert not leaked, f'{len(leaked)} ctrl ball(s) leaked into the mesh scan: {leaked}'


def test_is_rig_widget_is_specific():
    """A loose ctrl ball outside the Armature group is NOT a rig widget, and a
    character mesh never is."""
    from maya_tools.framework import utilities as utils

    _chain()
    body = cmds.polySphere(name='character_body')[0]
    amt.build_armature(root='jnt0')

    ctrl = amt.ctrl_for_joint('jnt1')
    assert utils.is_rig_widget(ctrl), 'an Armature ctrl is a rig widget'
    assert not utils.is_rig_widget(body), 'the character mesh is NOT a rig widget'


# ── the palette ───────────────────────────────────────────────

def test_index_rgb_has_a_headless_fallback():
    """cmds.colorIndex() returns None in a headless mayapy (no colour table).
    The shaders need real RGB, so a None must never reach setAttr."""
    for index in (amt._COLOR_LEFT, amt._COLOR_RIGHT,
                  amt._COLOR_AIMING, amt._COLOR_PLACED):
        rgb = amt._index_rgb(index)
        assert isinstance(rgb, tuple) and len(rgb) == 3, \
            f'index {index} -> {rgb!r}'
        assert all(isinstance(c, float) and 0.0 <= c <= 1.0 for c in rgb), \
            f'index {index} -> {rgb!r} is not a valid RGB'
    unknown = amt._index_rgb(29)
    assert isinstance(unknown, tuple) and len(unknown) == 3, \
        'an unmapped index must fall back, not return None'


if __name__ == '__main__':
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'\nArmature x-ray ball ctrl — {len(tests)} tests\n')
    for name, fn in tests:
        check(name, fn)

    print()
    if FAILED:
        print(f'FAILED ({len(FAILED)}/{len(tests)}):')
        for name, err in FAILED:
            print(f'  {name}: {err}')
        sys.exit(1)
    print(f'ALL PASS ({len(PASSED)})')
