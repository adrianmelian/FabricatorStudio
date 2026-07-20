# _dev/test_mirror_ctrl_shapes.py
"""mayapy tests for Mirror Modules ctrl-shape transfer (Adrian,
2026-07-12): user ctrl-shape edits on a source component mirror onto the
opposite-side component at the NEXT BUILD, so the first build after a
limb mirror shows finished ctrls on both sides.

Mechanism under test: mirror_component sets
persisted.pending_cv_mirror = <source id> (and copies NO cv_data — the
old _mirror_cv_data flip was mis-keyed and frame-wrong; replaced, and
its unit test test_mirror_cv_data.py retired with it). The
build_modules tail runs curve_mirror.mirror_component_ctrls (world-YZ
CV mirror through both ctrls' live matrices) once, then clears the
flag — one-shot, so later sculpts on the mirrored side survive
rebuilds.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_mirror_ctrl_shapes.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import maya.standalone
maya.standalone.initialize(name='python')

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes, fs_app, curve_mirror

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


def _build_scene():
    """Registry + root + clavicle_l (behavior-mirrored to clavicle_r) +
    World + SimpleFK on the L side."""
    cmds.file(new=True, force=True)
    nodes.create_registry('MirrorCtrlShapesTest')
    cmds.select(clear=True)
    cmds.joint(name='root', p=(0, 0, 0))
    cmds.select('root')
    cmds.joint(name='clavicle_l', p=(2, 10, 0))
    cmds.joint('clavicle_l', e=True, oj='xyz', sao='yup', zso=True)
    cmds.mirrorJoint('clavicle_l', mirrorYZ=True, mirrorBehavior=True,
                     searchReplace=('_l', '_r'))
    nodes.create_component_node(
        component_id='world', component_type='World',
        joints=['root'], parent_plug='', side='', options={}, persisted={},
    )
    nodes.create_component_node(
        component_id='clavicle_l_simple_fk', component_type='SimpleFK',
        joints=['clavicle_l'], parent_plug='', side='lf',
        options={'ctrl_color': 'blue'}, persisted={},
    )


def _ctrl_of(component_id):
    cnode = nodes.find_component_node_by_id(component_id)
    assert cnode, f'component {component_id!r} missing'
    ctrls = curve_mirror._component_ctrls(cnode)
    assert ctrls, f'no fab_owner ctrls on {component_id!r}'
    return ctrls[0]


def _shape0(ctrl):
    shapes = cmds.listRelatives(ctrl, shapes=True, type='nurbsCurve') or []
    assert shapes, f'{ctrl!r} has no nurbsCurve shapes'
    return shapes[0]


def _cv0_world(ctrl):
    return cmds.xform(f'{_shape0(ctrl)}.cv[0]', q=True, ws=True, t=True)


_build_scene()
SCULPT_OFFSET = (0.0, 3.0, 1.5)


def test_sculpt_and_mirror_flow():
    """Build L, sculpt its ctrl, unbuild (captures cv_data), mirror the
    component: flag set, no cv_data copied."""
    fs_app.build_modules()
    l_ctrl = _ctrl_of('clavicle_l_simple_fk')
    cmds.move(*SCULPT_OFFSET, f'{_shape0(l_ctrl)}.cv[0]', relative=True,
              worldSpace=True)
    fs_app.unbuild_modules()

    r_id = fs_app.mirror_component('clavicle_l_simple_fk')
    assert r_id == 'clavicle_r_simple_fk', f'unexpected mirror id {r_id!r}'
    persisted = nodes.get_component_persisted(
        nodes.find_component_node_by_id(r_id)) or {}
    assert persisted.get('pending_cv_mirror') == 'clavicle_l_simple_fk'
    assert 'cv_data' not in persisted, 'mis-keyed cv_data copied to mirror'


def test_first_build_mirrors_sculpt():
    """The very next build gives the R ctrl the world-YZ mirror of the
    sculpted L shape, and the flag is spent."""
    fs_app.build_modules()
    l_cv = _cv0_world(_ctrl_of('clavicle_l_simple_fk'))
    r_cv = _cv0_world(_ctrl_of('clavicle_r_simple_fk'))
    assert abs(r_cv[0] + l_cv[0]) < 1e-3, f'X not mirrored: {l_cv} vs {r_cv}'
    assert abs(r_cv[1] - l_cv[1]) < 1e-3, f'Y drifted: {l_cv} vs {r_cv}'
    assert abs(r_cv[2] - l_cv[2]) < 1e-3, f'Z drifted: {l_cv} vs {r_cv}'
    persisted = nodes.get_component_persisted(
        nodes.find_component_node_by_id('clavicle_r_simple_fk')) or {}
    assert 'pending_cv_mirror' not in persisted, 'flag not spent'


def test_mirrored_shape_survives_rebuild():
    """Unbuild captures the mirrored shape as an ordinary user edit; the
    next build restores it without any flag."""
    r_before = _cv0_world(_ctrl_of('clavicle_r_simple_fk'))
    fs_app.unbuild_modules()
    fs_app.build_modules()
    r_after = _cv0_world(_ctrl_of('clavicle_r_simple_fk'))
    assert all(abs(a - b) < 1e-3 for a, b in zip(r_before, r_after)), \
        f'mirrored shape lost across rebuild: {r_before} vs {r_after}'


def test_one_shot_never_stomps_later_sculpts():
    """Sculpt the R side AFTER the mirror; rebuilds must keep it (the
    mirror pass ran once and must not fire again)."""
    r_ctrl = _ctrl_of('clavicle_r_simple_fk')
    cmds.move(0.0, -4.0, 2.0, f'{_shape0(r_ctrl)}.cv[0]', relative=True,
              worldSpace=True)
    r_sculpted = _cv0_world(r_ctrl)
    fs_app.unbuild_modules()
    fs_app.build_modules()
    r_after = _cv0_world(_ctrl_of('clavicle_r_simple_fk'))
    assert all(abs(a - b) < 1e-3 for a, b in zip(r_sculpted, r_after)), \
        f'R-side sculpt stomped by re-mirror: {r_sculpted} vs {r_after}'
    l_cv = _cv0_world(_ctrl_of('clavicle_l_simple_fk'))
    assert abs(r_after[0] + l_cv[0]) > 0.5 or abs(r_after[1] - l_cv[1]) > 0.5, \
        'R shape suspiciously identical to a fresh L mirror'


def test_flip_all_side_tokens():
    """Ribbon segment ctrls concatenate TWO sided joint names; the
    counterpart lookup must flip every token (review wf_2f4a49ba #2)."""
    from maya_tools.utils.maya import side_tokens as st
    assert st.flip_all_side_tokens('upperarm_l_lowerarm_l_ribbon_mid_00_ctrl') \
        == 'upperarm_r_lowerarm_r_ribbon_mid_00_ctrl'
    assert st.flip_all_side_tokens('thigh_r_calf_r_ribbon_mid_01_ctrl') \
        == 'thigh_l_calf_l_ribbon_mid_01_ctrl'
    assert st.flip_all_side_tokens('clavicle_l_ctrl') == 'clavicle_r_ctrl'
    assert st.flip_all_side_tokens('spine_02_ctrl') is None
    # Single-flip behavior unchanged for single-entity names.
    assert st.flip_side_token('upperarm_l_lowerarm_l_ribbon_mid_00_ctrl') \
        == 'upperarm_r_lowerarm_l_ribbon_mid_00_ctrl'


def test_reset_build_defers_mirror_and_flag_survives_unbuild():
    """Reset Control Shapes on the first post-mirror build must NOT burn
    the one-shot on the reset default (review #1), and the pending flag
    must survive an unbuild's full-replace persisted capture (review #0).
    Flow: fresh scene, sculpt L, mirror, build WITH reset (flag kept, R
    default), unbuild (flag still there), normal build (mirror lands)."""
    _build_scene()
    fs_app.build_modules()
    l_ctrl = _ctrl_of('clavicle_l_simple_fk')
    cmds.move(*SCULPT_OFFSET, f'{_shape0(l_ctrl)}.cv[0]', relative=True,
              worldSpace=True)
    l_sculpt_y = _cv0_world(l_ctrl)[1]
    fs_app.unbuild_modules()
    fs_app.mirror_component('clavicle_l_simple_fk')

    fs_app.build_modules(options={'reset_ctrl_shapes': True})
    r_node = nodes.find_component_node_by_id('clavicle_r_simple_fk')
    persisted = nodes.get_component_persisted(r_node) or {}
    assert persisted.get('pending_cv_mirror') == 'clavicle_l_simple_fk', \
        'reset build burned the pending flag'
    fs_app.unbuild_modules()
    persisted = nodes.get_component_persisted(r_node) or {}
    assert persisted.get('pending_cv_mirror') == 'clavicle_l_simple_fk', \
        'unbuild capture dropped the pending flag'

    fs_app.build_modules()
    l_cv = _cv0_world(_ctrl_of('clavicle_l_simple_fk'))
    r_cv = _cv0_world(_ctrl_of('clavicle_r_simple_fk'))
    assert abs(l_cv[1] - l_sculpt_y) < 1e-3, 'L sculpt did not return post-reset'
    assert abs(r_cv[0] + l_cv[0]) < 1e-3 and abs(r_cv[1] - l_cv[1]) < 1e-3, \
        f'deferred mirror never landed: {l_cv} vs {r_cv}'
    persisted = nodes.get_component_persisted(r_node) or {}
    assert 'pending_cv_mirror' not in persisted


def test_ribbon_mid_ctrl_mirrors():
    """Adrian's actual case: ribbon IK limbs — the sculpted floating MID
    ctrl (two side tokens in its name) must mirror onto the R limb."""
    cmds.file(new=True, force=True)
    nodes.create_registry('RibbonMirrorTest')
    cmds.select(clear=True)
    cmds.joint(name='root', p=(0, 0, 0))
    cmds.select('root')
    cmds.joint(name='upperarm_l', p=(2, 10, 0))
    cmds.joint(name='lowerarm_l', p=(6, 10, -1))
    cmds.joint(name='hand_l', p=(10, 10, 0))
    for j in ('upperarm_l', 'lowerarm_l'):
        cmds.joint(j, e=True, oj='xyz', sao='yup', zso=True)
    cmds.mirrorJoint('upperarm_l', mirrorYZ=True, mirrorBehavior=True,
                     searchReplace=('_l', '_r'))
    nodes.create_component_node(
        component_id='world', component_type='World',
        joints=['root'], parent_plug='', side='', options={}, persisted={},
    )
    nodes.create_component_node(
        component_id='upperarm_l_ribbon_ikarm', component_type='RibbonIKArm',
        joints=['upperarm_l', 'lowerarm_l', 'hand_l'], parent_plug='',
        side='lf', options={'ctrl_color': 'blue'}, persisted={},
        region='arm',
    )
    fs_app.build_modules()

    mids = [c for c in curve_mirror._component_ctrls(
                nodes.find_component_node_by_id('upperarm_l_ribbon_ikarm'))
            if '_mid_' in c]
    assert mids, 'no ribbon mid ctrls found on the L arm'
    mid = sorted(mids)[0]
    cmds.move(0.0, 2.5, 1.0, f'{_shape0(mid)}.cv[0]', relative=True,
              worldSpace=True)
    fs_app.unbuild_modules()

    fs_app.mirror_component('upperarm_l_ribbon_ikarm')

    # Nested strip: the L component's persisted carries per-segment
    # cv_data keyed by L ctrl names; NONE of it may ride into R
    # (top-level pop missed ribbon_segments.<seg>.cv_data — seen live
    # on reggie's R arm).
    r_persisted = nodes.get_component_persisted(
        nodes.find_component_node_by_id('upperarm_r_ribbon_ikarm')) or {}

    def _assert_no_cv_blocks(d, path=''):
        for k, v in d.items():
            assert not (k == 'cv_data' or k.endswith('_cv_data')), \
                f'source-keyed cv block leaked into mirror at {path}{k}'
            if isinstance(v, dict):
                _assert_no_cv_blocks(v, f'{path}{k}.')
    _assert_no_cv_blocks(r_persisted)

    fs_app.build_modules()

    l_mid = sorted(c for c in curve_mirror._component_ctrls(
        nodes.find_component_node_by_id('upperarm_l_ribbon_ikarm'))
        if '_mid_' in c)[0]
    from maya_tools.utils.maya import side_tokens as st
    r_mid = st.flip_all_side_tokens(l_mid)
    assert cmds.objExists(r_mid), f'R mid ctrl {r_mid!r} missing'
    l_cv = _cv0_world(l_mid)
    r_cv = _cv0_world(r_mid)
    assert abs(r_cv[0] + l_cv[0]) < 1e-3 and abs(r_cv[1] - l_cv[1]) < 1e-3 \
        and abs(r_cv[2] - l_cv[2]) < 1e-3, \
        f'ribbon mid ctrl not mirrored: {l_cv} vs {r_cv}'


def test_ikfk_visibility_wiring_survives_shape_mirror():
    """Adrian's R-arm IK/FK bug (2026-07-12): the mirror pass replaced
    ctrl shapes AFTER the vis conditions wired shape.visibility, severing
    the switch — FK and IK ctrls both stuck visible, only the PV line
    toggling. deserialize_shape_to must recarry the wiring. Runs on the
    ribbon-arm scene built by the previous test."""
    for side in ('l', 'r'):
        ctrl = f'upperarm_{side}_FK_ctrl'
        assert cmds.objExists(ctrl), f'{ctrl!r} missing'
        for s in cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []:
            conns = cmds.listConnections(f'{s}.visibility', source=True,
                                         destination=False) or []
            assert conns, f'{s} visibility unwired (side {side})'

    # The switch must actually toggle the R side, not just be wired.
    switch = 'upperarm_r_ribbon_ikarm'
    node = nodes.find_component_node_by_id(switch)
    switch_ctrls = [c for c in curve_mirror._component_ctrls(node)
                    if 'settings' in c or 'switch' in c]
    r_fk_shape = (cmds.listRelatives('upperarm_r_FK_ctrl', shapes=True,
                                     fullPath=True) or [])[0]
    src = cmds.listConnections(f'{r_fk_shape}.visibility', source=True,
                               destination=False, plugs=True)[0]
    cond = src.split('.')[0]
    blend_src = cmds.listConnections(f'{cond}.firstTerm', source=True,
                                     destination=False, plugs=True) or []
    assert blend_src, 'vis condition has no blend input'
    attr = blend_src[0]
    for blend, expect in ((0.0, True), (1.0, False)):
        cmds.setAttr(attr, blend)
        vis = cmds.getAttr(f'{r_fk_shape}.visibility')
        assert vis == expect, \
            f'R FK shape visibility {vis} at blend {blend}, expected {expect}'


check('sculpt -> unbuild -> mirror sets flag, copies no cv_data',
      test_sculpt_and_mirror_flow)
check('first build mirrors the sculpt onto the R ctrl',
      test_first_build_mirrors_sculpt)
check('mirrored shape survives unbuild/rebuild',
      test_mirrored_shape_survives_rebuild)
check('one-shot: later R-side sculpts never stomped',
      test_one_shot_never_stomps_later_sculpts)
check('flip_all_side_tokens handles two-token ribbon names',
      test_flip_all_side_tokens)
check('reset build defers mirror; flag survives unbuild',
      test_reset_build_defers_mirror_and_flag_survives_unbuild)
check('ribbon mid ctrl (two side tokens) mirrors',
      test_ribbon_mid_ctrl_mirrors)
check('IK/FK visibility wiring survives shape mirror',
      test_ikfk_visibility_wiring_survives_shape_mirror)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print('ALL PASS (8)')
