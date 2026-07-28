# _dev/test_ctrl_shape_swap_preserves_vis.py
"""mayapy scene tests: replacing a ctrl's curve shape must not sever the
IK/FK visibility switch.

Field report 2026-07-28 (community, ex-Bungie): swapping the upper/lower
arm FK ctrl shapes in the Ctrl Editor left those FK shapes drawn in IK
mode. Root cause: the IK/FK switch drives visibility on the ctrl's SHAPE
nodes (simple_ik.py, `<start>_fk_vis_cond.outColorR` -> shape.visibility)
so the transform's channels stay clean for animators, and
curve_o_matic_app.swap_shape deleted the old shapes while carrying only
the override colour across.

The in-house fix for the identical bug (2026-07-12) had landed in
deserialize_shape_to and never in swap_shape. Both now share
_capture_shape_state / _apply_shape_state, and this suite locks BOTH
paths so a third shape-replacing function cannot quietly skip it.

Every vis assertion here is FUNCTIONAL, not just topological: a
connection to the wrong condition node passes a listConnections check
and fails the "does the ctrl actually hide" check.

Run (userSetup prepends the build copy to sys.path during standalone
init, so this harness re-pins its target AFTER initialize; default is
the depot source, FS_TEST_TARGET=build exercises the shipped copy):
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_ctrl_shape_swap_preserves_vis.py
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

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


# ── scene scaffolding (chain + World parent copied from
#    _dev/test_ik_arm_maya.py: SimpleIK's parent_in is required, so every
#    IK component needs a World-owning ancestor or build_modules raises) ──

def _build_arm(prefix='vis'):
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 10, 0), name=f'{prefix}_root')
    shoulder = cmds.joint(p=(0, 10, 0), name=f'{prefix}_shoulder')
    elbow = cmds.joint(p=(5, 7, 1), name=f'{prefix}_elbow')
    wrist = cmds.joint(p=(10, 10, 0), name=f'{prefix}_wrist')
    cmds.select(clear=True)

    nodes.create_registry(f'{prefix}_bp')
    nodes.create_component_node(
        component_id=f'{prefix}_world', component_type='World',
        joints=[root], parent_plug='', side='md', options={}, persisted={},
    )
    nodes.create_component_node(
        component_id=f'{prefix}_C0', component_type='IKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()
    return root, shoulder, elbow, wrist


def _shapes(ctrl):
    import maya.cmds as cmds
    return cmds.listRelatives(ctrl, shapes=True, type='nurbsCurve',
                              fullPath=True) or []


def _vis_source(shape):
    import maya.cmds as cmds
    conns = cmds.listConnections(f'{shape}.visibility', source=True,
                                 destination=False, plugs=True) or []
    return conns[0] if conns else ''


def _assert_driven_by(ctrl, expected_plug, label):
    """Every shape under ctrl is driven by expected_plug. Asserting on
    EVERY shape, not just the first, is the point: the multi-shape
    library curves (capsule is 4 shapes, sphere is 3) are exactly where a
    'reconnect the first one' fix would look green and still ship broken."""
    shapes = _shapes(ctrl)
    assert shapes, f'{label}: {ctrl} has no nurbsCurve shapes'
    for s in shapes:
        src = _vis_source(s)
        assert src == expected_plug, (
            f'{label}: {s} visibility driven by {src!r}, '
            f'expected {expected_plug!r}')


def _assert_hides(ctrl, switch_ctrl, blend_value, label):
    """Functional check: drive the switch and read the shapes' evaluated
    visibility. Catches a connection to the WRONG condition node, which a
    topology-only assertion cannot."""
    import maya.cmds as cmds
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', blend_value)
    for s in _shapes(ctrl):
        vis = cmds.getAttr(f'{s}.visibility')
        assert vis == 0, (
            f'{label}: {s} still visible at ik_fk_blend={blend_value}')


# ── tests ───────────────────────────────────────────────────────────────

def test_swap_preserves_fk_vis_connection():
    """The reporter's exact repro: swap both arm FK ctrl shapes, then
    switch to IK and confirm they hide."""
    import maya.cmds as cmds
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

    _root, shoulder, elbow, wrist = _build_arm('fkvis')
    cond = f'{shoulder}_fk_vis_cond.outColorR'
    switch = f'{wrist}_IKFK_ctrl'

    # Baseline: the build wired it.
    _assert_driven_by(f'{shoulder}_FK_ctrl', cond, 'pre-swap upper')

    # Upper AND lower arm, as reported. capsule (4 shapes) -> sphere (3).
    com.swap_shape('sphere', f'{shoulder}_FK_ctrl')
    com.swap_shape('sphere', f'{elbow}_FK_ctrl')

    _assert_driven_by(f'{shoulder}_FK_ctrl', cond, 'post-swap upper')
    _assert_driven_by(f'{elbow}_FK_ctrl', cond, 'post-swap lower')

    # Functional: IK mode hides them, FK mode brings them back.
    _assert_hides(f'{shoulder}_FK_ctrl', switch, 1.0, 'post-swap upper')
    _assert_hides(f'{elbow}_FK_ctrl', switch, 1.0, 'post-swap lower')
    cmds.setAttr(f'{switch}.ik_fk_blend', 0.0)
    for ctrl in (f'{shoulder}_FK_ctrl', f'{elbow}_FK_ctrl'):
        for s in _shapes(ctrl):
            assert cmds.getAttr(f'{s}.visibility') == 1, (
                f'{s} hidden in FK mode')


def test_swap_preserves_ik_and_pv_vis_connection():
    """The other half of the switch: the IK end ctrl and the PV ctrl are
    driven by the ik_vis_cond and must hide in FK mode after a swap."""
    import maya.cmds as cmds
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

    _root, shoulder, elbow, wrist = _build_arm('ikvis')
    cond = f'{shoulder}_ik_vis_cond.outColorR'
    switch = f'{wrist}_IKFK_ctrl'

    com.swap_shape('sphere', f'{wrist}_IK_ctrl')
    com.swap_shape('sphere', f'{elbow}_PV_ctrl')

    _assert_driven_by(f'{wrist}_IK_ctrl', cond, 'post-swap ik')
    _assert_driven_by(f'{elbow}_PV_ctrl', cond, 'post-swap pv')

    _assert_hides(f'{wrist}_IK_ctrl', switch, 0.0, 'post-swap ik')
    _assert_hides(f'{elbow}_PV_ctrl', switch, 0.0, 'post-swap pv')


def test_swap_worldspace_preserves_vis_connection():
    """worldspace=True takes the _flatten_cvs_to_world branch before the
    reparent. Same carry contract."""
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

    _root, shoulder, elbow, wrist = _build_arm('wsvis')
    cond = f'{shoulder}_fk_vis_cond.outColorR'

    com.swap_shape('cube', f'{elbow}_FK_ctrl', worldspace=True)
    _assert_driven_by(f'{elbow}_FK_ctrl', cond, 'post-worldspace-swap')
    _assert_hides(f'{elbow}_FK_ctrl', f'{wrist}_IKFK_ctrl', 1.0,
                  'post-worldspace-swap')


def test_swap_on_selection_preserves_vis_connection():
    """The multi-select path the UI uses when a whole arm is selected."""
    import maya.cmds as cmds
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

    _root, shoulder, elbow, wrist = _build_arm('selvis')
    cond = f'{shoulder}_fk_vis_cond.outColorR'

    cmds.select([f'{shoulder}_FK_ctrl', f'{elbow}_FK_ctrl',
                 f'{wrist}_FK_ctrl'], replace=True)
    com.swap_shape_on_selection('cube')

    for ctrl in (f'{shoulder}_FK_ctrl', f'{elbow}_FK_ctrl',
                 f'{wrist}_FK_ctrl'):
        _assert_driven_by(ctrl, cond, 'post-selection-swap')
        _assert_hides(ctrl, f'{wrist}_IKFK_ctrl', 1.0, 'post-selection-swap')


def test_swap_still_preserves_override_colour():
    """Guard the behaviour that already worked. The fix rewrote the
    capture/apply pair, so colour carry has to be re-proved, not assumed."""
    import maya.cmds as cmds
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

    _root, shoulder, _elbow, _wrist = _build_arm('colvis')
    ctrl = f'{shoulder}_FK_ctrl'
    before = [cmds.getAttr(f'{_shapes(ctrl)[0]}.overrideColor{c}')
              for c in 'RGB']
    assert cmds.getAttr(f'{_shapes(ctrl)[0]}.overrideEnabled')

    com.swap_shape('sphere', ctrl)

    for s in _shapes(ctrl):
        assert cmds.getAttr(f'{s}.overrideEnabled'), f'{s} lost overrideEnabled'
        after = [cmds.getAttr(f'{s}.overrideColor{c}') for c in 'RGB']
        assert all(abs(a - b) < 1e-5 for a, b in zip(after, before)), (
            f'{s} colour {after} != {before}')


def test_swap_carries_override_display_type():
    """overrideDisplayType (the template flag SimpleIK stamps on the PV
    guide line) was outside the old colour-only carry set, so a swap
    silently un-templated a curve. Now in _SHAPE_CARRY_ATTRS."""
    import maya.cmds as cmds
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

    _root, shoulder, _elbow, _wrist = _build_arm('dtvis')
    ctrl = f'{shoulder}_FK_ctrl'
    for s in _shapes(ctrl):
        cmds.setAttr(f'{s}.overrideEnabled', 1)
        cmds.setAttr(f'{s}.overrideDisplayType', 1)   # template

    com.swap_shape('sphere', ctrl)

    for s in _shapes(ctrl):
        assert cmds.getAttr(f'{s}.overrideDisplayType') == 1, (
            f'{s} lost overrideDisplayType')


def test_deserialize_path_still_preserves_vis():
    """deserialize_shape_to was the already-fixed path. It now shares the
    helper pair, so re-prove it rather than trusting the refactor."""
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

    _root, shoulder, elbow, wrist = _build_arm('dsvis')
    cond = f'{shoulder}_fk_vis_cond.outColorR'
    data = com.serialize_shape(f'{wrist}_FK_ctrl')

    com.deserialize_shape_to(f'{elbow}_FK_ctrl', data)
    _assert_driven_by(f'{elbow}_FK_ctrl', cond, 'post-deserialize')
    _assert_hides(f'{elbow}_FK_ctrl', f'{wrist}_IKFK_ctrl', 1.0,
                  'post-deserialize')


def test_swap_on_unwired_ctrl_is_a_noop_not_a_raise():
    """A ctrl with no incoming vis connection (spine FK, fingers, the
    switch ctrl itself) must swap cleanly and end up with visibility
    unconnected — not raising, and not inheriting some other ctrl's
    condition."""
    import maya.cmds as cmds
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

    _root, _shoulder, _elbow, wrist = _build_arm('bare')
    ctrl = f'{wrist}_IKFK_ctrl'
    assert not _vis_source(_shapes(ctrl)[0]), (
        'switch ctrl unexpectedly vis-driven; test premise is stale')

    com.swap_shape('sphere', ctrl)

    for s in _shapes(ctrl):
        assert not _vis_source(s), f'{s} gained a vis connection from nowhere'
        assert cmds.getAttr(f'{s}.visibility') == 1


def test_swap_survives_unbuild_rebuild_round_trip():
    """Swapped shapes persist across unbuild/build (cv_data capture) and
    the rebuilt rig re-wires vis. Proves the fix and the persistence path
    do not fight each other."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

    _root, shoulder, elbow, wrist = _build_arm('rtvis')
    com.swap_shape('sphere', f'{elbow}_FK_ctrl')
    n_swapped = len(_shapes(f'{elbow}_FK_ctrl'))

    fs_app.unbuild_modules()
    fs_app.build_modules()

    assert len(_shapes(f'{elbow}_FK_ctrl')) == n_swapped, (
        'rebuild did not restore the swapped shape count')
    _assert_driven_by(f'{elbow}_FK_ctrl',
                      f'{shoulder}_fk_vis_cond.outColorR', 'post-rebuild')
    _assert_hides(f'{elbow}_FK_ctrl', f'{wrist}_IKFK_ctrl', 1.0,
                  'post-rebuild')
    cmds.setAttr(f'{wrist}_IKFK_ctrl.ik_fk_blend', 0.0)


def test_prebuild_check_is_silent_on_a_healthy_rig():
    """_check_ctrl_vis_severed must not fire on a rig built by the fixed
    code, including after a swap. A check that cries wolf gets ignored."""
    from maya_tools.rigging.fabricator import build_checks
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

    _root, shoulder, _elbow, _wrist = _build_arm('chkok')
    assert not build_checks._check_ctrl_vis_severed(), 'fired on a fresh build'
    com.swap_shape('sphere', f'{shoulder}_FK_ctrl')
    assert not build_checks._check_ctrl_vis_severed(), 'fired after a good swap'


def test_prebuild_check_detects_and_fixes_a_severed_rig():
    """Simulate a scene broken by a pre-fix tool version, then prove the
    check sees it and its Fix repairs it functionally.

    Modelling detail that matters: a real pre-fix swap built BRAND NEW
    shape nodes, which carry visibility=1. disconnectAttr alone freezes
    the shape at its last driven value, and the rig defaults to IK mode
    (blend 1.0), so the FK shapes would sit at 0 and the 'stuck visible'
    symptom would not reproduce at all. Set them back to 1 to match what
    the user actually had in front of them."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import build_checks

    _root, shoulder, elbow, wrist = _build_arm('chkbad')
    cond = f'{shoulder}_fk_vis_cond.outColorR'
    victim = f'{elbow}_FK_ctrl'
    for s in _shapes(victim):
        cmds.disconnectAttr(cond, f'{s}.visibility')
        cmds.setAttr(f'{s}.visibility', 1)

    issues = build_checks._check_ctrl_vis_severed()
    assert len(issues) == 1, f'expected 1 issue, got {issues}'
    issue = issues[0]
    assert issue.key == 'ctrl_vis_severed'
    assert issue.severity == 'warning', 'must never gate a build'
    assert issue.fixable and issue.fix is not None

    # Symptom present before the fix.
    cmds.setAttr(f'{wrist}_IKFK_ctrl.ik_fk_blend', 1.0)
    assert any(cmds.getAttr(f'{s}.visibility') == 1 for s in _shapes(victim))

    issue.fix()

    _assert_driven_by(victim, cond, 'post-check-fix')
    _assert_hides(victim, f'{wrist}_IKFK_ctrl', 1.0, 'post-check-fix')
    assert not build_checks._check_ctrl_vis_severed(), 'issue persists after fix'


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
    import maya_tools.rigging.curve_o_matic.curve_o_matic_app as _com
    print(f"  target curve_o_matic_app: {_com.__file__}")

    try:
        check("test_swap_preserves_fk_vis_connection",
              test_swap_preserves_fk_vis_connection)
        check("test_swap_preserves_ik_and_pv_vis_connection",
              test_swap_preserves_ik_and_pv_vis_connection)
        check("test_swap_worldspace_preserves_vis_connection",
              test_swap_worldspace_preserves_vis_connection)
        check("test_swap_on_selection_preserves_vis_connection",
              test_swap_on_selection_preserves_vis_connection)
        check("test_swap_still_preserves_override_colour",
              test_swap_still_preserves_override_colour)
        check("test_swap_carries_override_display_type",
              test_swap_carries_override_display_type)
        check("test_deserialize_path_still_preserves_vis",
              test_deserialize_path_still_preserves_vis)
        check("test_swap_on_unwired_ctrl_is_a_noop_not_a_raise",
              test_swap_on_unwired_ctrl_is_a_noop_not_a_raise)
        check("test_swap_survives_unbuild_rebuild_round_trip",
              test_swap_survives_unbuild_rebuild_round_trip)
        check("test_prebuild_check_is_silent_on_a_healthy_rig",
              test_prebuild_check_is_silent_on_a_healthy_rig)
        check("test_prebuild_check_detects_and_fixes_a_severed_rig",
              test_prebuild_check_detects_and_fixes_a_severed_rig)
    finally:
        try:
            maya.standalone.uninitialize()
        except Exception:
            pass

    if FAILURES:
        print(f"CTRL SHAPE SWAP VIS TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("CTRL SHAPE SWAP VIS TESTS: OK")


if __name__ == "__main__":
    main()
