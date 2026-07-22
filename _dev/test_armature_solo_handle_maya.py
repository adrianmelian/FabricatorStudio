"""Armature SOLO handles (Adrian, 2026-07-21).

    mayapy -m pytest is not how this repo runs; invoke directly:
    PYTHONNOUSERSITE=1 mayapy _dev/test_armature_solo_handle_maya.py

The feature: every ctrl carries a small hidden handle underneath it, and
the JOINT'S DRIVE NETWORK reads the handle instead of the ctrl. Move the
ctrl, the subtree comes (unchanged behaviour). Move the handle, only that
one joint moves.

The traps this suite exists to catch, in the order they would bite:

  1. HALF A MIGRATION. A pure-aim edge has TWO consumers of the ctrl's
     transform — the ikHandle's DAG parent (which direction the parent
     aims) and the distanceBetween's second matrix (how far down that
     ray the child sits). Repoint one and not the other and the joint
     lands somewhere NEITHER handle is: it snaps back toward the ctrl,
     or slides off the ray. Both directions are asserted separately, on
     the wiring AND on the resulting geometry.

     MEASURED (2026-07-21) by sabotaging _wire_aim_edge three ways —
     ikHandle-only migrated, distance-only migrated, neither — and
     re-running. Which test caught what:

       sabotage              lands_ON  ikhandle_lives  distance_reads
       ikHandle only            RED        green            RED
       distance only            RED         RED            green
       neither                  RED         RED             RED

     So the two wiring tests each catch exactly their own half, and
     test_the_nudged_joint_lands_ON_its_handle is the one that catches
     ALL THREE — it measures the joint against the handle rather than
     the graph against an expectation. If this suite is ever trimmed,
     that is the test to keep. Note what does NOT catch a half
     migration: test_nudging_an_aim_edge_solo_moves_only_that_joint
     stayed GREEN for both half cases, because a joint that lands in
     the wrong place still leaves its descendants alone. "Descendants
     didn't move" is not evidence the joint moved correctly.

  2. A NON-ZERO REST. The whole no-op-by-default promise rests on the
     handle's local matrix being a true identity. cmds.parent defaults
     to ABSOLUTE (world-preserving), which would have left every cage at
     the world origin with a compensating translate — visibly wrong, and
     silently non-zero. Asserted as exact equality, not a tolerance.

  3. THE ctrl_scale MEASUREMENT, again. measure_live_ctrl_scale divides
     a ctrl's shape extent by a probe's. The solo cage is a CURVE, it is
     BIGGER than the orb, and it lives under the ctrl — if component
     specs descended into child transforms the exporter's break/export/
     rebuild round trip would silently resize every one of the user's
     ctrls. (They do not descend. Measured. Guarded here anyway, because
     the failure is silent and plausible.)

  4. BURIAL. The cage is a nurbsCurve, so it takes alwaysDrawOnTop —
     which Maya never saves. Same trap as the orbs and the aimers: works
     perfectly right up until you save.

NOT COVERED HERE (and not coverable headlessly): whether any of this
reads correctly in a GUI viewport — that the cage is grabbable next to
the orb it surrounds, that the plasma is legible against the character,
that hiding the layer does what an artist expects on screen. This repo
has a recorded headless-green/GUI-dies history on exactly this class of
change. Headless green means the graph is right, not that the tool is.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import maya.standalone

maya.standalone.initialize(name='python')

import maya.cmds as cmds  # noqa: E402

from maya_tools.rigging.fabricator import armature as amt  # noqa: E402

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


# ── fixtures ──────────────────────────────────────────────────

# WHICH EDGE KIND A FIXTURE PRODUCES IS DECIDED BY ITS AXIS, and getting
# that backwards is how the first draft of this suite "passed" while
# testing the point path twice. Measured, not assumed: the house aim axis
# is +X (orientation_convention.stretch_channel == 'tx'), and
# seed_aimer_from_detection only targets a child that sits on it. So a
# chain laid out along +X gives SC-IK aim edges, and a chain along +Y
# gives translate-only pointConstraint edges. Both fixtures exist because
# the two paths repoint DIFFERENT nodes at the solo handle (one
# constraint vs. an ikHandle parent + a distanceBetween), and a suite
# that only exercised one would miss half the migration.

def _aim_chain(n=3, spacing=5.0):
    """root + n children along +X: every child is its parent's aim
    target, so xj1..xjN are all SC-IK aim edges."""
    cmds.select(clear=True)
    joints = []
    for i in range(n + 1):
        joints.append(cmds.joint(name=f'xj{i}', position=(i * spacing, 0, 0)))
    cmds.select(clear=True)
    return joints


def _point_chain(n=3, spacing=5.0):
    """root + n children along +Y: off the aim axis, so every edge
    demotes to a translate-only pointConstraint ctrl."""
    cmds.select(clear=True)
    joints = []
    for i in range(n + 1):
        joints.append(cmds.joint(name=f'jnt{i}', position=(0, i * spacing, 0)))
    cmds.select(clear=True)
    return joints


def _branchy():
    """BOTH edge kinds in one scene: a +Y spine (point edges) with a +X
    arm hanging off the chest (aim edges)."""
    cmds.select(clear=True)
    cmds.joint(name='root_jnt', position=(0, 0, 0))
    cmds.joint(name='spine_jnt', position=(0, 10, 0))
    cmds.joint(name='chest_jnt', position=(0, 20, 0))
    cmds.joint(name='neck_jnt', position=(0, 26, 0))
    cmds.select('chest_jnt')
    cmds.joint(name='lf_upperarm_jnt', position=(6, 20, 0))
    cmds.joint(name='lf_lowerarm_jnt', position=(16, 20, 0))
    cmds.joint(name='lf_hand_jnt', position=(24, 20, 0))
    cmds.select(clear=True)


def _wpos(node):
    return cmds.xform(node, q=True, ws=True, t=True)


def _wm(node):
    return cmds.xform(node, q=True, ws=True, matrix=True)


def _snapshot(joints):
    return {j: _wpos(j) for j in joints}


def _assert_unmoved(before, joints, tol=1e-6, why=''):
    moved = []
    for j in joints:
        d = max(abs(a - b) for a, b in zip(before[j], _wpos(j)))
        if d > tol:
            moved.append(f'{j} moved {d:.6f}')
    assert not moved, f'{why}: {moved}'


def _edge_kind(joint):
    """'aim' when the joint is driven by an SC-IK + stretch edge,
    'point' when a pointConstraint drives it."""
    return 'aim' if cmds.objExists(f'{joint}_amtIkh') else 'point'


# ── 1. the handle itself ──────────────────────────────────────

def test_every_ctrl_gets_a_solo_handle():
    _branchy()
    amt.build_armature(root='root_jnt')
    ctrls = [n for n in cmds.ls('*_amt_CTL', type='transform') or []]
    assert len(ctrls) >= 7, f'expected a full ctrl tree, got {ctrls}'
    for ctrl in ctrls:
        jnt = ctrl[:-len('_amt_CTL')]
        solo = amt.solo_handle_for_joint(jnt)
        assert solo, f'{jnt} has a ctrl but no solo handle'
        parent = (cmds.listRelatives(solo, parent=True) or [None])[0]
        assert parent == ctrl, \
            f'{solo} is parented under {parent}, must be under {ctrl}'


def test_solo_handle_rest_pose_is_an_exact_identity():
    """Trap 2. cmds.parent defaults to ABSOLUTE — a world-preserving
    parent would leave the cage at the origin with a compensating
    translate. Exact equality, not a tolerance: this is the assumption
    the whole no-op-by-default promise is built on."""
    _point_chain()
    amt.build_armature(root='jnt0')
    solo = amt.solo_handle_for_joint('jnt1')
    assert cmds.getAttr(f'{solo}.translate')[0] == (0.0, 0.0, 0.0), \
        f'solo translate {cmds.getAttr(f"{solo}.translate")} is not zero'
    assert cmds.getAttr(f'{solo}.rotate')[0] == (0.0, 0.0, 0.0)
    assert cmds.getAttr(f'{solo}.scale')[0] == (1.0, 1.0, 1.0)


def test_zeroed_solo_world_matrix_equals_its_ctrls_exactly():
    """The reason a built-but-untouched Armature is byte-for-byte what
    it was before solo handles existed."""
    _branchy()
    amt.build_armature(root='root_jnt')
    for jnt in ('spine_jnt', 'lf_lowerarm_jnt', 'root_jnt'):
        ctrl = amt.ctrl_for_joint(jnt)
        solo = amt.solo_handle_for_joint(jnt)
        assert _wm(ctrl) == _wm(solo), \
            f'{solo} world matrix drifted from {ctrl}'


def test_child_ctrls_still_parent_under_the_MAIN_ctrl():
    """The architecture guard. Parenting children under the SOLO instead
    would make a solo nudge drag the subtree — i.e. exactly the thing the
    feature exists to avoid, with extra steps."""
    _point_chain()
    amt.build_armature(root='jnt0')
    child_ctrl = amt.ctrl_for_joint('jnt2')
    parent = (cmds.listRelatives(child_ctrl, parent=True) or [None])[0]
    assert parent == amt.ctrl_for_joint('jnt1'), \
        f'{child_ctrl} hangs off {parent}, must hang off jnt1_amt_CTL'


def test_translate_free_rotate_and_scale_locked():
    _point_chain()
    amt.build_armature(root='jnt0')
    solo = amt.solo_handle_for_joint('jnt1')
    for attr in ('tx', 'ty', 'tz'):
        assert not cmds.getAttr(f'{solo}.{attr}', lock=True), \
            f'{attr} is locked — the handle cannot be nudged'
    for attr in ('sx', 'sy', 'sz', 'rx', 'ry', 'rz'):
        assert cmds.getAttr(f'{solo}.{attr}', lock=True), \
            f'{attr} is unlocked — it drives nothing and must not invite a drag'


# ── 2. the drive network points at the handle ─────────────────

def test_pointconstraint_targets_the_solo_not_the_ctrl():
    _branchy()
    amt.build_armature(root='root_jnt')
    jnt = 'spine_jnt'
    assert _edge_kind(jnt) == 'point', 'fixture drifted — expected a point edge'
    con = f'{jnt}_amt_ptcon'
    assert cmds.objExists(con), f'{con} missing'
    targets = cmds.pointConstraint(con, q=True, targetList=True) or []
    assert targets == [amt.solo_handle_for_joint(jnt)], \
        (f'{con} targets {targets} — it must target the solo handle, or a '
         f'nudge does nothing at all')


def test_aim_edge_ikhandle_lives_under_the_solo():
    """Trap 1, half one: the ikHandle's DAG parent is what the SC solver
    aims the parent joint AT."""
    _aim_chain()
    amt.build_armature(root='xj0')
    assert _edge_kind('xj1') == 'aim', 'fixture drifted — expected an aim edge'
    ikh = 'xj1_amtIkh'
    parent = (cmds.listRelatives(ikh, parent=True) or [None])[0]
    assert parent == amt.solo_handle_for_joint('xj1'), \
        (f'{ikh} hangs off {parent}; under the main ctrl the parent joint '
         f'would keep aiming at the ctrl and the handle would do nothing')


def test_aim_edge_distance_reads_the_solo():
    """Trap 1, half two: the stretch decides how far down the aim ray the
    child sits. Left on the ctrl, the child snaps back toward it."""
    _aim_chain()
    amt.build_armature(root='xj0')
    src = cmds.listConnections('xj1_amtDist.inMatrix2', source=True,
                               destination=False, plugs=True) or []
    assert src, 'nothing drives xj1_amtDist.inMatrix2'
    assert src[0].split('.')[0] == amt.solo_handle_for_joint('xj1'), \
        (f'inMatrix2 reads {src[0]} — with the ikHandle on the solo and the '
         f'stretch on the ctrl the joint lands on NEITHER')
    # inMatrix1 must still be the parent JOINT — the other half of the
    # measurement, and repointing it would be a different bug entirely.
    src1 = cmds.listConnections('xj1_amtDist.inMatrix1', source=True,
                                destination=False, plugs=True) or []
    assert src1[0].split('.')[0] == 'xj0', f'inMatrix1 reads {src1}'


def test_guide_line_is_fed_by_the_solo_but_named_for_the_ctrl():
    """The line has to tell the truth about where the bone runs; the name
    stays ctrl-derived so the cleanup glob and cache key don't move."""
    _point_chain()
    amt.build_armature(root='jnt0')
    pmm = 'jnt1_amt_CTL_amtLinePos'
    assert cmds.objExists(pmm), \
        f'{pmm} missing — the documented naming changed under cleanup'
    src = cmds.listConnections(f'{pmm}.inMatrix', source=True,
                               destination=False, plugs=True) or []
    assert src[0].split('.')[0] == amt.solo_handle_for_joint('jnt1'), \
        f'{pmm} reads {src} — the guide line would draw a bone that is not there'


# ── 3. THE BEHAVIOUR ──────────────────────────────────────────

def test_nudging_a_point_edge_solo_moves_only_that_joint():
    _branchy()
    amt.build_armature(root='root_jnt')
    jnt = 'spine_jnt'
    assert _edge_kind(jnt) == 'point', 'fixture drifted — expected a point edge'
    descendants = ['chest_jnt', 'neck_jnt', 'lf_upperarm_jnt',
                   'lf_lowerarm_jnt', 'lf_hand_jnt']
    others = ['root_jnt']
    before = _snapshot([jnt] + descendants + others)

    cmds.setAttr(f'{amt.solo_handle_for_joint(jnt)}.translate', 0, 7, 3,
                 type='double3')

    now = _wpos(jnt)
    assert max(abs(a - b) for a, b in zip(now, before[jnt])) > 1.0, \
        f'{jnt} did not move at all: {before[jnt]} -> {now}'
    _assert_unmoved(before, descendants, why='descendants followed the nudge')
    _assert_unmoved(before, others, why='unrelated joints moved')


def test_nudging_an_aim_edge_solo_moves_only_that_joint():
    """The hard case. The parent re-aims at the moved handle and the
    moved joint re-aims at its own child's UNMOVED handle, so orientation
    updates all the way through while positions below stay put."""
    _aim_chain(n=4)
    amt.build_armature(root='xj0')
    assert _edge_kind('xj2') == 'aim', 'fixture drifted — expected an aim edge'
    descendants = ['xj3', 'xj4']
    before = _snapshot(['xj0', 'xj1', 'xj2'] + descendants)

    cmds.setAttr(f'{amt.solo_handle_for_joint("xj2")}.translate', 0, 4, 2,
                 type='double3')

    now = _wpos('xj2')
    assert max(abs(a - b) for a, b in zip(now, before['xj2'])) > 1.0, \
        f'xj2 did not move: {before["xj2"]} -> {now}'
    _assert_unmoved(before, descendants,
                    why='the subtree followed an aim-edge solo nudge')
    _assert_unmoved(before, ['xj0'], why='the root moved')


def test_the_nudged_joint_lands_ON_its_handle():
    """Half-migration detector, geometric edition: with only one of the
    two consumers repointed the joint lands short of, or off, the handle
    rather than exactly on it."""
    _branchy()
    amt.build_armature(root='root_jnt')
    seen = set()
    for jnt in ('spine_jnt', 'chest_jnt', 'lf_upperarm_jnt',
                'lf_lowerarm_jnt', 'lf_hand_jnt'):
        solo = amt.solo_handle_for_joint(jnt)
        seen.add(_edge_kind(jnt))
        cmds.setAttr(f'{solo}.translate', 3, 1, 2, type='double3')
        d = max(abs(a - b) for a, b in zip(_wpos(jnt), _wpos(solo)))
        assert d < 1e-4, \
            (f'{jnt} sits {d:.5f} from its own handle ({_edge_kind(jnt)} edge) '
             f'— the ikHandle and the distanceBetween disagree about where '
             f'"the ctrl" is')
        cmds.setAttr(f'{solo}.translate', 0, 0, 0, type='double3')
    assert seen == {'aim', 'point'}, \
        f'only exercised {seen} — the fixture stopped covering both edge kinds'


def test_nudging_the_root_solo_leaves_the_body_put():
    _branchy()
    amt.build_armature(root='root_jnt')
    rest = ['spine_jnt', 'chest_jnt', 'neck_jnt', 'lf_upperarm_jnt',
            'lf_lowerarm_jnt', 'lf_hand_jnt']
    before = _snapshot(['root_jnt'] + rest)

    cmds.setAttr(f'{amt.solo_handle_for_joint("root_jnt")}.translate',
                 2, -3, 1, type='double3')

    assert max(abs(a - b) for a, b in
               zip(_wpos('root_jnt'), before['root_jnt'])) > 1.0, \
        'the root did not move'
    _assert_unmoved(before, rest, why='the whole rig followed the root nudge')


def test_moving_the_MAIN_ctrl_still_drags_the_whole_subtree():
    """The behaviour that must NOT have changed. If this goes red the
    feature ate the Armature's primary gesture."""
    _aim_chain(n=4)
    amt.build_armature(root='xj0')
    before = _snapshot(['xj1', 'xj2', 'xj3', 'xj4'])

    cmds.setAttr(f'{amt.ctrl_for_joint("xj1")}.translate', 0, 6, 0,
                 type='double3')

    for j in ('xj1', 'xj2', 'xj3', 'xj4'):
        d = max(abs(a - b) for a, b in zip(before[j], _wpos(j)))
        assert d > 0.5, f'{j} did NOT follow its parent ctrl (moved {d:.4f})'


def test_a_nudge_survives_a_rebuild_and_the_handle_rezeroes():
    """Solo offsets are deliberately not persisted anywhere. A rebuild
    re-derives every ctrl from its joint's CURRENT world position, so the
    nudge is baked into the ctrl tree instead of lost."""
    _branchy()
    amt.build_armature(root='root_jnt')
    jnt = 'lf_upperarm_jnt'
    cmds.setAttr(f'{amt.solo_handle_for_joint(jnt)}.translate', 0, 6, 2,
                 type='double3')
    nudged = _snapshot(['root_jnt', 'spine_jnt', 'chest_jnt', jnt,
                        'lf_lowerarm_jnt', 'lf_hand_jnt'])

    amt.build_armature(root='root_jnt')

    _assert_unmoved(nudged, list(nudged), tol=1e-4,
                    why='the rebuild lost or double-applied the nudge')
    solo = amt.solo_handle_for_joint(jnt)
    assert cmds.getAttr(f'{solo}.translate')[0] == (0.0, 0.0, 0.0), \
        'the rebuilt handle did not come back at zero'


def test_hidden_handles_still_drive():
    """Visibility is display-only. If this ever stops being true the
    feature is broken by default, since the handles ship hidden."""
    _point_chain()
    amt.build_armature(root='jnt0')
    assert not amt.solo_handles_visible(), 'handles must ship hidden'
    before = _wpos('jnt1')
    cmds.setAttr(f'{amt.solo_handle_for_joint("jnt1")}.translate', 3, 0, 0,
                 type='double3')
    assert max(abs(a - b) for a, b in zip(before, _wpos('jnt1'))) > 1.0, \
        'a hidden handle did not drive its joint'


# ── 4. visibility ─────────────────────────────────────────────

def test_hidden_by_default_and_toggleable():
    """Shape-level visibility since 2026-07-21 (the `_solo` display
    layer is RETIRED: a hidden layer's override beats a member's own
    shape visibility, which would deadlock the enter_solo swap)."""
    _point_chain()
    amt.build_armature(root='jnt0')
    assert not cmds.objExists(amt._SOLO_LAYER), \
        'the retired _solo layer came back — build must not recreate it'
    assert amt.solo_handles_visible() is False, \
        ('the handles are visible on a fresh build — an untouched Armature '
         'must look exactly as it did before this feature')
    for solo in amt.solo_handles():
        for shp in cmds.listRelatives(solo, shapes=True, fullPath=True):
            assert cmds.getAttr(f'{shp}.visibility') == 0, \
                f'{shp} visible on a fresh build'
    assert amt.toggle_solo_handles() is True
    assert amt.solo_handles_visible() is True
    assert amt.toggle_solo_handles() is False
    assert amt.solo_handles_visible() is False


def test_a_legacy_solo_layer_is_deleted_by_the_build():
    """A scene built before the swap carries the `_solo` layer with
    visibility=0. Its overrideVisibility would keep every cage
    invisible regardless of shape state, so build deletes it on sight."""
    _point_chain()
    cmds.createDisplayLayer(name=amt._SOLO_LAYER, empty=True)
    cmds.setAttr(f'{amt._SOLO_LAYER}.visibility', 0)
    amt.build_armature(root='jnt0')
    assert not cmds.objExists(amt._SOLO_LAYER), \
        'the legacy _solo layer survived the build'
    assert amt.set_solo_handles_visible(True) is True
    assert amt.solo_handles_visible() is True


def test_a_rebuild_does_not_slam_the_handles_back_off():
    """Set ON CREATE ONLY. Every limb drop and template load rebuilds the
    Armature; re-asserting the default would fight the user every time."""
    _point_chain()
    amt.build_armature(root='jnt0')
    amt.set_solo_handles_visible(True)
    amt.build_armature(root='jnt0')
    assert amt.solo_handles_visible() is True, \
        'the rebuild hid the handles the user had turned on'


def test_visibility_api_is_honest_with_no_armature():
    assert amt.solo_handles_visible() is False
    assert amt.solo_handles() == []
    assert amt.solo_handle_for_joint('nothing_jnt') is None
    try:
        amt.set_solo_handles_visible(True)
    except RuntimeError:
        pass
    else:
        raise AssertionError('showing handles that do not exist must raise')


def test_hiding_the_armature_still_hides_the_handles():
    """The `_armature` layer hide must still reach the cages now that
    they have no layer of their own: the hide propagates down the DAG
    through the ctrl each handle is parented under. A guard, in case a
    future Maya changes the propagation."""
    import maya.api.OpenMaya as om
    _point_chain()
    amt.build_armature(root='jnt0')
    amt.set_solo_handles_visible(True)
    solo = amt.solo_handle_for_joint('jnt1')
    sel = om.MSelectionList()
    sel.add(solo)
    dag = sel.getDagPath(0)
    assert dag.isVisible(), 'handle should be visible with both layers on'
    cmds.setAttr(f'{amt._ARMATURE_LAYER}.visibility', 0)
    assert not dag.isVisible(), \
        ('the solo handles survived hiding the whole Armature — nesting the '
         'layers is no longer safe and the toggle needs rethinking')


def test_reset_zeroes_the_handles_without_moving_the_subtree():
    _branchy()
    amt.build_armature(root='root_jnt')
    jnt = 'lf_upperarm_jnt'
    descendants = ['lf_lowerarm_jnt', 'lf_hand_jnt']
    before = _snapshot([jnt] + descendants)
    cmds.setAttr(f'{amt.solo_handle_for_joint(jnt)}.translate', 0, 9, 0,
                 type='double3')
    assert amt.reset_solo_handles([jnt]) == 1
    d = max(abs(a - b) for a, b in zip(before[jnt], _wpos(jnt)))
    assert d < 1e-6, f'{jnt} did not return to its ctrl (off by {d})'
    _assert_unmoved(before, descendants, why='reset disturbed the subtree')


# ── 5. teardown ───────────────────────────────────────────────

def test_unbuild_leaves_no_orphans():
    _branchy()
    amt.build_armature(root='root_jnt')
    assert amt.solo_handles(), 'the build produced no handles to tear down'
    amt.delete_armature()

    leftovers = {}
    for glob, kind in ((f'*{amt._SOLO_SUFFIX}', 'solo handles'),
                       ('*_amtIkh', 'ik handles'),
                       ('*_amtEff', 'effectors'),
                       ('*_amtDist', 'distanceBetween'),
                       ('*_amtStretch', 'stretch mults'),
                       ('*_amtLinePos', 'line drivers'),
                       ('*_amt_ptcon*', 'point constraints'),
                       ('*_amt_CTL', 'ctrls'),
                       (f'{amt._SHADER_PREFIX}*', 'ctrl shaders')):
        found = cmds.ls(glob) or []
        if found:
            leftovers[kind] = found
    assert not leftovers, f'unbuild left orphans: {leftovers}'
    assert not cmds.objExists(amt._GRP)


def test_a_handle_orphaned_outside_the_group_is_still_swept():
    """Cleanup is name+type driven for a reason: the group cascade only
    reaches what is still under the group."""
    _point_chain()
    amt.build_armature(root='jnt0')
    stray = amt.solo_handle_for_joint('jnt1')
    cmds.parent(stray, world=True)
    amt.delete_armature()
    assert not (cmds.ls(f'*{amt._SOLO_SUFFIX}') or []), \
        'a hand-reparented handle survived the teardown'


def test_build_unbuild_cycles_do_not_accumulate():
    _branchy()
    for _ in range(3):
        amt.build_armature(root='root_jnt')
        amt.delete_armature()
    amt.build_armature(root='root_jnt')
    handles = amt.solo_handles()
    ctrls = cmds.ls('*_amt_CTL', type='transform') or []
    assert len(handles) == len(ctrls), \
        f'{len(handles)} handles for {len(ctrls)} ctrls after four cycles'


# ── 6. the x-ray, again ───────────────────────────────────────

def test_solo_cage_draws_on_top():
    _point_chain()
    amt.build_armature(root='jnt0')
    solo = amt.solo_handle_for_joint('jnt1')
    shapes = cmds.listRelatives(solo, shapes=True, fullPath=True) or []
    assert shapes, 'the handle has no shape'
    for shp in shapes:
        assert cmds.nodeType(shp) == 'nurbsCurve', \
            (f'{shp} is {cmds.nodeType(shp)} — only mesh and nurbsCurve get '
             f'alwaysDrawOnTop, so any other type buries the handle')
        assert cmds.getAttr(f'{shp}.alwaysDrawOnTop') is True


def test_ctrl_shapes_sweeps_the_cages_so_reapply_reaches_them():
    _point_chain()
    amt.build_armature(root='jnt0')
    swept = {s.split('|')[-1] for s in amt.ctrl_shapes()}
    for solo in amt.solo_handles():
        for shp in cmds.listRelatives(solo, shapes=True, fullPath=True) or []:
            assert shp.split('|')[-1] in swept, \
                f'{shp} is outside ctrl_shapes() — reapply_xray cannot reach it'


def test_cages_survive_a_save_and_reopen():
    """Maya never writes .adot. Without the kAfterOpen hook every cage
    comes back buried inside the character."""
    _point_chain()
    amt.build_armature(root='jnt0')
    tmp = os.path.join(cmds.internalVar(userTmpDir=True), 'amt_solo.ma')
    cmds.file(rename=tmp)
    cmds.file(save=True, type='mayaAscii', force=True)
    cmds.file(new=True, force=True)
    cmds.file(tmp, open=True, force=True)

    handles = amt.solo_handles()
    assert handles, 'the handles did not survive the reopen'
    for solo in handles:
        for shp in cmds.listRelatives(solo, shapes=True, fullPath=True) or []:
            assert cmds.getAttr(f'{shp}.alwaysDrawOnTop') is True, \
                f'{shp} came back BURIED — the scene-open hook missed it'


def test_solo_wears_brand_plasma_as_exact_rgb():
    """Not a palette index: the brand accent has no index within reach,
    and the handle's whole job is to read as a different kind of object
    from the blue/red/yellow/cyan orbs."""
    _point_chain()
    amt.build_armature(root='jnt0')
    shp = cmds.listRelatives(amt.solo_handle_for_joint('jnt1'),
                             shapes=True, fullPath=True)[0]
    assert cmds.getAttr(f'{shp}.overrideEnabled') is True
    assert cmds.getAttr(f'{shp}.overrideRGBColors') is True, \
        'RGB override off — the shape falls back to an index colour'
    got = cmds.getAttr(f'{shp}.overrideColorRGB')[0]
    for a, b in zip(got, amt._SOLO_RGB):
        assert abs(a - b) < 1e-3, f'{got} is not plasma {amt._SOLO_RGB}'


def test_the_cage_reads_larger_than_the_orb():
    """If the cage sat inside the opaque draw-on-top orb there would be
    nothing to click, and the feature would be unreachable in the GUI."""
    _point_chain()
    cmds.setAttr('jnt1.radius', 2.0)
    amt.build_armature(root='jnt0', ctrl_scale=1.0)
    orb = amt._max_shape_extent(amt.ctrl_for_joint('jnt1'))
    cage = amt._max_shape_extent(amt.solo_handle_for_joint('jnt1'))
    assert cage > orb, f'cage extent {cage:.3f} <= orb extent {orb:.3f}'


# ── 7. trap 3: the ctrl_scale measurement ─────────────────────

def test_measure_live_ctrl_scale_is_not_poisoned_by_the_cage():
    """Silent, plausible, and wrong: the cage is a bigger curve living
    under every ctrl, and this ratio feeds the skeletal exporter's
    break/export/rebuild round trip. If component specs ever start
    descending into child transforms, every one of the user's ctrls gets
    silently resized and nothing says so."""
    _point_chain()
    for scale in (1.0, 2.5):
        amt.build_armature(root='jnt0', ctrl_scale=scale)
        got = amt.measure_live_ctrl_scale()
        assert got is not None, f'returned None at ctrl_scale={scale}'
        assert abs(got - scale) < 0.05, \
            (f'measured {got:.3f} for ctrl_scale={scale} — the solo cage is '
             f'leaking into _max_shape_extent')


def test_max_shape_extent_does_not_descend_into_child_transforms():
    """The measured premise the test above rests on, isolated so a Maya
    change fails LOUDLY here rather than as a mystery in the exporter."""
    ball = amt._build_ball('probe_ball')
    crv = cmds.curve(name='probe_child', degree=1,
                     point=[(0, 0, 0), (99.0, 0, 0)])
    cmds.parent(crv, ball, relative=True)
    got = amt._max_shape_extent(ball)
    assert abs(got - 1.0) < 1e-3, \
        (f'extent {got} — a child transform\'s shape leaked into the parent\'s '
         f'component spec; _max_shape_extent must switch to an explicit '
         f'listRelatives(shapes=True) walk')


# ── 8. the drive network, on a real rig ───────────────────────

def test_every_edge_kind_on_the_shipped_biped_reads_its_handle():
    """The synthetic fixtures cover both edge kinds; this covers them at
    scale, on the template an artist actually loads, including the
    demoted zero-length and off-axis edges."""
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator import nodes as fab_nodes
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fs_app.load(os.path.join(
        repo, 'maya_tools/rigging/fabricator/templates',
        'Advanced_Biped.blueprint.yaml'))
    amt.build_armature()
    root = fab_nodes.get_registry_root_joint()

    checked_aim = checked_point = 0
    for jnt in amt._hierarchy(root):
        solo = amt.solo_handle_for_joint(jnt)
        if not solo:
            continue          # follow-ruled: no ctrl, no handle, by design
        if _edge_kind(jnt) == 'aim':
            parent = (cmds.listRelatives(f'{jnt}_amtIkh', parent=True)
                      or [None])[0]
            assert parent == solo, f'{jnt}_amtIkh hangs off {parent}'
            src = cmds.listConnections(f'{jnt}_amtDist.inMatrix2',
                                       source=True, destination=False,
                                       plugs=True) or []
            assert src and src[0].split('.')[0] == solo, \
                f'{jnt}_amtDist.inMatrix2 reads {src}'
            checked_aim += 1
        else:
            con = f'{jnt}_amt_ptcon'
            assert cmds.objExists(con), f'{jnt} has neither edge kind'
            targets = cmds.pointConstraint(con, q=True, targetList=True) or []
            assert targets == [solo], f'{con} targets {targets}'
            checked_point += 1

    assert checked_aim > 20 and checked_point > 20, \
        f'thin coverage: {checked_aim} aim / {checked_point} point edges'


def test_biped_nudge_leaves_ctrl_driven_descendants_put():
    """At scale, with follow rules in play. Scoped to ctrl-driven
    descendants on purpose: a follow-ruled twist RIDES the segment it
    belongs to, so it is SUPPOSED to move when one of that segment's
    endpoints does — asserting otherwise would be asserting the twist
    system is broken."""
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator import nodes as fab_nodes
    from maya_tools.rigging.fabricator import follow_rules as fr
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fs_app.load(os.path.join(
        repo, 'maya_tools/rigging/fabricator/templates',
        'Advanced_Biped.blueprint.yaml'))
    amt.build_armature()
    root = fab_nodes.get_registry_root_joint()

    target = next(j for j in amt._hierarchy(root)
                  if j.startswith('lowerarm_l'))
    ruled = set(fr.ruled_joints() or [])
    below = [j for j in (cmds.listRelatives(target, allDescendents=True,
                                            type='joint') or [])
             if j not in ruled]
    assert len(below) > 10, f'thin subtree under {target}: {len(below)}'
    elsewhere = [j for j in amt._hierarchy(root)
                 if j.startswith(('thigh_r', 'spine_', 'neck_'))
                 and j not in ruled]
    before = _snapshot([target] + below + elsewhere)

    cmds.setAttr(f'{amt.solo_handle_for_joint(target)}.translate', 0, 4, 3,
                 type='double3')

    assert max(abs(a - b) for a, b in
               zip(before[target], _wpos(target))) > 1.0, 'the joint did not move'
    _assert_unmoved(before, below, tol=1e-4,
                    why=f'the hand/fingers followed a nudge on {target}')
    _assert_unmoved(before, elsewhere, tol=1e-4,
                    why='an unrelated limb moved')


# ─── the per-ctrl swap (marking menu: Nudge This Joint / Back to Ctrl) ─

def _shape_vis(node):
    return [cmds.getAttr(f'{s}.visibility') for s in
            (cmds.listRelatives(node, shapes=True, fullPath=True) or [])]


def test_enter_solo_swaps_the_manipulator():
    """enter_solo: ctrl shapes off, cage on, cage selected, nothing
    moves. Shape-level on both sides — hiding the ctrl TRANSFORM would
    take the cage (its own child) with it."""
    _branchy()
    amt.build_armature(root='root_jnt')
    jnt = 'lf_upperarm_jnt'
    before = _snapshot(['root_jnt', 'spine_jnt', 'chest_jnt', jnt,
                        'lf_lowerarm_jnt', 'lf_hand_jnt'])

    solo = amt.enter_solo(jnt)

    ctrl = amt.ctrl_for_joint(jnt)
    assert not any(_shape_vis(ctrl)), 'ctrl shapes still visible'
    assert all(_shape_vis(solo)), 'cage shapes not shown'
    assert amt.solo_active(jnt) is True
    sel = (cmds.ls(selection=True) or [''])[0].split('|')[-1]
    assert sel == solo.split('|')[-1], f'cage not selected: {sel!r}'
    _assert_unmoved(before, list(before),
                    why='entering solo must be display-only')
    # The subtree's own manipulators are untouched — the swap is
    # strictly per-joint.
    child_ctrl = amt.ctrl_for_joint('lf_lowerarm_jnt')
    assert all(_shape_vis(child_ctrl)), 'child ctrl shapes were hidden'


def test_exit_solo_restores_and_keeps_the_nudge():
    _branchy()
    amt.build_armature(root='root_jnt')
    jnt = 'lf_upperarm_jnt'
    solo = amt.enter_solo(jnt)
    cmds.setAttr(f'{solo}.translate', 0, 4, 1.5, type='double3')
    nudged = _snapshot([jnt, 'lf_lowerarm_jnt', 'lf_hand_jnt'])

    ctrl = amt.exit_solo(jnt)

    assert all(_shape_vis(ctrl)), 'ctrl shapes not restored'
    assert not any(_shape_vis(solo)), 'cage still visible after exit'
    assert amt.solo_active(jnt) is False
    sel = (cmds.ls(selection=True) or [''])[0].split('|')[-1]
    assert sel == ctrl.split('|')[-1], f'ctrl not selected: {sel!r}'
    assert cmds.getAttr(f'{solo}.translate')[0] == (0.0, 4.0, 1.5), \
        'exit_solo must not touch the nudge value'
    _assert_unmoved(nudged, list(nudged), tol=1e-4,
                    why='exiting solo must be display-only')


def test_bulk_hide_exits_an_active_swap():
    """set_solo_handles_visible(False) while a swap is live must
    restore that ctrl's shapes — never strand a joint with no visible
    manipulator at all."""
    _branchy()
    amt.build_armature(root='root_jnt')
    jnt = 'lf_upperarm_jnt'
    amt.enter_solo(jnt)

    amt.set_solo_handles_visible(False)

    ctrl = amt.ctrl_for_joint(jnt)
    assert all(_shape_vis(ctrl)), \
        'bulk hide left the swapped ctrl invisible — unclickable joint'
    assert amt.solo_active(jnt) is False
    assert amt.solo_handles_visible() is False


def test_commit_bakes_the_nudge_into_placement_and_rezeroes():
    """Adrian, 2026-07-21: putting the cages away is the 'I'm done
    nudging' moment. The rebuild IS the commit — every ctrl re-derives
    from its joint's CURRENT world position, so nothing moves, the
    nudge becomes plain placement, and the handles come back at zero."""
    _branchy()
    amt.build_armature(root='root_jnt')
    jnt = 'lf_upperarm_jnt'
    solo = amt.solo_handle_for_joint(jnt)
    cmds.setAttr(f'{solo}.translate', 0, 5, 2, type='double3')
    nudged = _snapshot(['root_jnt', 'spine_jnt', 'chest_jnt', jnt,
                        'lf_lowerarm_jnt', 'lf_hand_jnt'])
    assert amt.pending_solo_nudges() == [jnt], amt.pending_solo_nudges()

    assert amt.commit_solo_nudges(root='root_jnt') == 1

    _assert_unmoved(nudged, list(nudged), tol=1e-4,
                    why='committing moved joints — it must be placement-only')
    assert amt.pending_solo_nudges() == [], 'handles did not re-zero'
    assert cmds.getAttr(
        f'{amt.solo_handle_for_joint(jnt)}.translate')[0] == (0.0, 0.0, 0.0)


def test_commit_is_a_noop_with_nothing_nudged():
    """Toggle on, look, toggle off must stay instant and side-effect
    free — no rebuild, no orientation bake, no skin detach."""
    _point_chain()
    amt.build_armature(root='jnt0')
    assert amt.pending_solo_nudges() == []
    assert amt.commit_solo_nudges(root='jnt0') == 0


def test_marking_menu_resolver_knows_armature_objects():
    """_armature_context: name-suffix + live-joint confirmation. A
    suffixed transform with NO matching joint must fall through to the
    role-tag path (('', '')), never guess."""
    from maya_tools.rigging.fabricator.ui import animation_menu as am
    _point_chain()
    amt.build_armature(root='jnt0')
    ctrl = amt.ctrl_for_joint('jnt1')
    solo = amt.solo_handle_for_joint('jnt1')

    assert am._armature_context(ctrl) == ('ctrl', 'jnt1')
    assert am._armature_context(solo) == ('solo', 'jnt1')
    assert am._armature_context('jnt1') == ('', '')          # a bare joint
    cmds.createNode('transform', name='fake_amt_CTL')        # no 'fake' jnt
    assert am._armature_context('fake_amt_CTL') == ('', '')


if __name__ == '__main__':
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'\nArmature SOLO handles — {len(tests)} tests\n')
    for name, fn in tests:
        check(name, fn)

    print()
    if FAILED:
        print(f'FAILED ({len(FAILED)}/{len(tests)}):')
        for name, err in FAILED:
            print(f'  {name}: {err}')
        sys.exit(1)
    print(f'ALL PASS ({len(PASSED)})')
