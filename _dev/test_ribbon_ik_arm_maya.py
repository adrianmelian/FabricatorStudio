# _dev/test_ribbon_ik_arm_maya.py
"""mayapy scene tests for the RibbonIKArm component.

Phase 1 promises: RibbonIKArm(SimpleIKComponent) builds/unbuilds a clean IK/FK/PV
arm identical to SimpleIK. Phase 2 adds: two per-bone ribbon segments
(upper-arm shoulder->elbow, forearm elbow->wrist), each with twist + volume
+ N floating mid ctrls. This file proves:
  - Build creates the FK/IK/PV/switch ctrls SimpleIK always creates.
  - The ik_fk_blend switch actually drives the bind chain in both
    directions (IK ctrl move -> bind follows; FK ctrl rotate -> bind
    follows after switching modes).
  - Unbuild leaves zero orphans: rig_grp is gone, every ctrl/scaffolding
    node RibbonIKArm's build created is gone, and every DG node TYPE the build
    creates returns to its pre-build count (extended in P2 to cover the
    ribbon's skinCluster/uvPin/blendShape/twist-deformer/matrix-chain
    node-set on top of Phase 1's set).
  - SimpleIK and IKLeg (the sibling components RibbonIKArm's module addition
    could have destabilized via auto-discovery) still build/unbuild
    cleanly — a regression guard, since no dedicated suite exists yet
    for either in this repo.
  - P2: both ribbon segments exist with mid_ctrl_count mids each, and the
    shared elbow control joints are two distinct, read-only-driven nodes
    (no fight — SPEC §7 / PLAN Task 2.2).
  - P2 CRITICAL (studio deformer-order lesson): twist distribution and
    volume preservation are verified at POSED DISPLACEMENT on an
    OFF-ORIGIN chain — never bind pose. A driver is posed (elbow bent via
    the IK ctrl, wrist twisted via the IK ctrl's rotate), then geometry
    (ride-joint rotate/scale) is measured off that pose, not the rest
    shape.
  - P2: no double-transform on global rig scale (scaling the World ctrl
    must NOT read as ribbon "stretch" — volume compensation must stay
    inert under a pure uniform rig-scale change).

Phase 3 (this file, current state) adds: one antCGi aim+IK roll joint per
bone (_limb_common.build_roll_joint — ROLL-METHOD.md §2), feeding clean
twist into each ribbon segment's twisting end. This file additionally
proves:
  - ANTI-FLIP (test_ikarm_roll_anti_flip_and_twist_response): a PURE BEND
    (no wrist rotation — an IK-ctrl POSITION move only) leaves BOTH roll
    joints' twist reading ~unchanged from bind. A wrist TWIST (IK ctrl
    rotateX) moves the FOREARM roll's twist reading substantially and
    MONOTONICALLY with the twist angle (both signs), while the UPPER
    roll's twist reading stays pinned near zero throughout that SAME
    stimulus — but a wrist-only stimulus can never kinematically reach
    the upper roll's shoulder anchor, so this test ALSO drives a genuine
    shoulder-axial-twist stimulus (FK mode, shoulder_FK_ctrl.rotateX) and
    re-measures, proving the upper roll's locator wiring genuinely
    filters twist rather than merely never being exercised. What actually
    filters (upper) vs. passes through (forearm) is the roll_aim
    locator's WORLD POSITION relationship to its reference joint — a
    pointConstraint (translate-only) for upper, a rigid DAG parent for
    forearm (see _limb_common.build_roll_joint's docstring). Twist is
    measured via a parallel-transport-corrected signed roll angle
    (isolates true twist from swing contamination — see that helper's
    docstring for why a naive fixed-axis comparison fails once the aim
    direction has swung substantially).
  - CLEAN DISTRIBUTION, NO FLIP (test_ikarm_roll_driven_twist_no_flip):
    sweeping wrist rotateX across +/-90 deg in steps produces a
    monotonic, continuous forearm-ribbon-tip roll reading — no ~180 deg
    jump between adjacent samples. NOTE: this range can't distinguish a
    working forearm mechanism from a broken one (a disconnected up-vector
    would ALSO look monotonic here) — see
    test_ikarm_roll_forearm_locator_mechanism_is_load_bearing below for
    the discriminative check.
  - LOAD-BEARING (test_ikarm_roll_forearm_locator_mechanism_is_load_
    bearing): mutation-style gate — severs the forearm roll_aim locator's
    live parenting to the wrist bind joint mid-scene and asserts the same
    wrist-twist stimulus that produces a large response on the intact rig
    produces ~none once severed, proving that specific parenting (not
    some other path) is what makes forearm twist pass through.
  - IK AND FK (test_ikarm_roll_twist_works_ik_and_fk): the SAME nominal
    wrist twist produces closely-matching forearm-ribbon-tip roll whether
    driven via the IK ctrl (IK mode) or the FK ctrl (FK mode) — proves the
    roll joint reads the BIND chain (downstream of the blend), not an
    IK-only or FK-only sub-chain.
  - MIRROR (test_ikarm_roll_mirror_offset_sign_flip): building the 'rt'
    side as a REAL mirrorJoint reflection of 'md' (not a second identical-
    coordinate build) and asserting the roll_aim locator lands on the
    geometrically mirrored side, for both roll joints.
  - UNBUILD (test_ikarm_roll_unbuild_leaves_zero_orphans): the roll node-
    set (roll joint, locator, aimConstraint, and — upper branch only —
    the locator's pointConstraint — all living OUTSIDE rig_grp) is fully
    deleted; DG type counts return to baseline; Phase 1/2 tests (above)
    stay green. Also asserts the removed follower/follow_tip/ikHandle
    nodes never exist at all (Cleanup 2026-07-08 regression gate).
  - HIDDEN (test_ikarm_roll_internals_hidden_from_viewport): both roll
    rigs' roll joint and roll_aim locator are visibility=0 after build —
    internal scaffolding must never leak into the animator's viewport
    (Cleanup 2026-07-08).

Phase 5 (this file, current state) adds: the layered fist-curl master
(fingers_ctrl) + skeleton-only LimbFragment auto-add. This file proves:
  - LAYERED CURL, POSED (test_ikarm_fingers_ctrl_layered_curl_and_
    metacarpal_exclusion): with the arm posed off bind, rotating
    fingers_ctrl curls every INCLUDED phalange while curl_excluded
    (metacarpal) joints get ZERO wiring (no incoming connection at all,
    not just a coincidentally-unchanged value) and don't move; a
    per-finger <joint>_ctrl key SUMS on top of the master via pure
    superposition (rot_both - rot0 == (rot_master - rot0) +
    (rot_key - rot0)), robust to whatever baseline rotation the offset
    already carried — not an override/fight. fingers_ctrl's non-curl
    channels (both other rotates, all translate/scale, visibility) are
    locked; the curl axis itself is keyable.
  - PLUSMINUSAVERAGE BRANCH + UNBUILD (test_ikarm_fingers_ctrl_
    plusminusaverage_branch_and_unbuild_clean): seeding one finger joint
    with a nonzero bind rotation before build forces
    _limb_common.build_fingers_ctrl's "authored baseline" branch (a
    plusMinusAverage(sum) node instead of a direct connect) — proven by
    inspecting the incoming connection type, the sum still equals
    baseline + master, and unbuild deletes the tracked node (DG type
    counts return to baseline, reusing _OWNED_DG_TYPES/_dg_type_counts
    since plusMinusAverage was already tracked there for the ribbon
    board). fingers_ctrl's own authored CV shape persists across
    unbuild -> build.
  - SKELETON-ONLY LIMB (test_limb_fragment_skeleton_only_loads_clean):
    an in-memory LimbFragment with components=[] loads via
    apply_limb_fragment onto a plain joint with no error, creates only
    joints (no component network nodes at all).
  - FINGER LIMB ASSET (test_finger_limb_asset_drops_clean_chain): the
    shipped finger.limb.yaml (read via limbs.io.read_yaml, SKIPPED when
    PyYAML isn't importable under this mayapy — same documented gap as
    the offscreen suite) drops a clean 4-joint chain.
  - AUTO-ADD (test_limb_auto_add_finger_joins_existing_ikarm_fist /
    test_limb_auto_add_no_ikarm_is_noop): dropping the finger limb onto
    a wrist whose owning component is an RibbonIKArm appends the new chain to
    that arm's 'fingers' option (with heuristic curl_excluded, verified
    via metacarpal_excluded parity) and — end to end — the new finger
    actually gets curled by fingers_ctrl on the next Build Rig; dropping
    onto a joint with no owning RibbonIKArm (a bare joint, and a joint owned
    by a non-RibbonIKArm component) does nothing extra, no error either way.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_ribbon_ik_arm_maya.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


class Skip(Exception):
    """Raise from a test body to mark it SKIPPED (environment gap, e.g.
    missing optional dependency) — NEVER silently absorbed into the pass
    count. See test_ribbon_ik_arm.py's identical Skip class for the full
    rationale (P5 review finding: a bare `return` inside a try/except
    ModuleNotFoundError block used to be indistinguishable from a real
    pass in this harness, including in the live-Maya run's own summary)."""


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


# DG node types created somewhere in SimpleIK's build (reverse/condition
# switch nodes, the pv_line clusters, stretchy/pin distance+blend nodes,
# the auto-PV matrix graph) PLUS (P2) the ribbon segments' skin/deformer/
# matrix-chain node-set (skinCluster + its tweak/groupParts/groupId
# ancillaries, uvPin, multMatrix, blendShape, the twist nonlinear
# deformer, curveInfo, multDoubleLinear). None of these types are created
# by a bare new scene or by Fabricator's rig-binding/organize-scene
# bookkeeping, so a return to the pre-build count after unbuild is a
# clean, well-scoped "no DG orphans" signal that doesn't depend on
# guessing every node name. (multiplyDivide/decomposeMatrix/blendTwoAttr
# were already in the Phase 1 set — the ribbon reuses those same types.)
_OWNED_DG_TYPES = (
    'reverse', 'condition', 'cluster', 'ikHandle', 'ikEffector',
    'distanceBetween', 'blendTwoAttr', 'multiplyDivide', 'vectorProduct',
    'plusMinusAverage', 'composeMatrix', 'decomposeMatrix',
    'skinCluster', 'tweak', 'groupParts', 'groupId', 'uvPin', 'multMatrix',
    'blendShape', 'twist', 'curveInfo', 'multDoubleLinear',
    # objectSet: the skinCluster's own set (COMPONENT_AUTHORING §6-B "full
    # skin node-set" = cluster + tweak + groupParts + groupId + an
    # objectSet + a bindPose) — omitting it left the regression baseline
    # blind to a regression that stopped tracking/deleting it.
    'objectSet',
    # curveFromSurfaceIso: build_volume's duplicateCurve(surf.u[0.5], ch=1)
    # construction-history node — a DG-only node with no DAG parent (not
    # caught by the rig_grp cascade), tracked via 'ribbon_board_nodes' but
    # likewise absent from this baseline until now.
    'curveFromSurfaceIso',
    # aimConstraint: P3's roll-joint aim constraint (_limb_common.
    # build_roll_joint) — a NEW node type this phase introduces, tracked
    # in the 'roll_dg_nodes' bucket. Not created by any Phase 1/2 path.
    'aimConstraint',
    # pointConstraint: the upper/proximal roll's roll_aim locator position
    # driver (_limb_common.build_roll_joint's `loc_pc` — a PERMANENT,
    # non-temp constraint) — also tracked in 'roll_dg_nodes'. Closing this
    # blind spot the same way aimConstraint
    # was added above: a regression that stopped tracking/deleting it
    # would otherwise leak silently past this baseline check.
    'pointConstraint',
    # dagPose: ribbon_ik_arm.py's unbuild() explicitly special-cases skinCluster
    # nodes in the ribbon bucket sweep to ALSO delete their connected
    # dagPose node before deleting the skinCluster itself (the correct
    # "full skin node-set" rule, SPEC §4) — but until now no test in this
    # file's shared baseline actually counted dagPose nodes, so a
    # regression in that specific cleanup line would have leaked past
    # every '...unbuild_leaves_zero_orphans' check silently. RibbonIKArm builds
    # TWO independent ribbon skinClusters per build (upper + forearm), so
    # this exposure is doubled vs. RibbonSpine's single-segment precedent.
    'dagPose',
)


def _dg_type_counts():
    return {t: len(__import__('maya.cmds', fromlist=['cmds']).ls(type=t) or [])
            for t in _OWNED_DG_TYPES}


# ─── P3 shared vector-math helpers (roll-joint twist measurement) ────────
import math as _math


def _v_normalize(v):
    m = sum(c * c for c in v) ** 0.5
    return [c / m for c in v] if m > 1e-9 else list(v)


def _v_sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _v_cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _v_dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _world_dir(node, local_vec):
    """World-space direction of a LOCAL vector rotated by node's current
    world matrix (rotation part only, translation ignored)."""
    import maya.cmds as cmds
    m = cmds.xform(node, q=True, ws=True, matrix=True)
    x = local_vec[0] * m[0] + local_vec[1] * m[4] + local_vec[2] * m[8]
    y = local_vec[0] * m[1] + local_vec[1] * m[5] + local_vec[2] * m[9]
    z = local_vec[0] * m[2] + local_vec[1] * m[6] + local_vec[2] * m[10]
    return [x, y, z]


def _signed_roll_deg(v_ref, v, axis):
    """Signed angle (degrees) from v_ref to v, measured about axis."""
    v_ref, v, axis = _v_normalize(v_ref), _v_normalize(v), _v_normalize(axis)
    d = _v_dot(v_ref, v)
    cr = _v_cross(v_ref, v)
    sin_c = _v_dot(cr, axis)
    return _math.degrees(_math.atan2(sin_c, d))


def _rotate_vec_about_axis(v, axis, angle_rad):
    """Rodrigues' rotation formula."""
    axis = _v_normalize(axis)
    cos_a, sin_a = _math.cos(angle_rad), _math.sin(angle_rad)
    t1 = [c * cos_a for c in v]
    t2 = [c * sin_a for c in _v_cross(axis, v)]
    t3 = [c * _v_dot(axis, v) * (1 - cos_a) for c in axis]
    return [t1[i] + t2[i] + t3[i] for i in range(3)]


def _true_twist_deg(axis_bind, axis_now, up_bind_world, up_now_world):
    """Parallel-transport-corrected twist angle (degrees): rotates
    up_bind_world by the MINIMAL swing that takes axis_bind -> axis_now,
    then measures the residual angle to up_now_world about axis_now.
    Isolates genuine twist from swing — a naive fixed-axis signed-roll
    comparison conflates the two once the aim direction has swung
    substantially (a bent arm's roll-joint aim axis moves a lot even with
    zero deliberate twist). Measured empirically during this phase's
    development: a fixed-axis reading showed ~27 deg of "twist" on a pure
    bend that collapsed to <1 deg once corrected for swing — the fixed-
    axis number was swing contamination, not real twist."""
    axis_bind, axis_now = _v_normalize(axis_bind), _v_normalize(axis_now)
    d = max(-1.0, min(1.0, _v_dot(axis_bind, axis_now)))
    swing_angle = _math.acos(d)
    if swing_angle < 1e-9:
        up_transported = up_bind_world
    else:
        swing_axis = _v_normalize(_v_cross(axis_bind, axis_now))
        if sum(c * c for c in swing_axis) < 1e-12:
            fallback = [1.0, 0.0, 0.0] if abs(axis_bind[0]) < 0.9 else [0.0, 1.0, 0.0]
            swing_axis = _v_normalize(_v_cross(axis_bind, fallback))
        up_transported = _rotate_vec_about_axis(up_bind_world, swing_axis, swing_angle)
    return _signed_roll_deg(up_transported, up_now_world, axis_now)


def _make_arm_chain(prefix):
    """Root -> shoulder -> elbow -> wrist. The root gets a World component
    (see _add_world_component) — SimpleIK's parent_in input is required,
    so every IK component in these tests needs a World-owning ancestor or
    build_modules' _auto_resolve_parent_plugs raises before ever reaching
    component build code."""
    import maya.cmds as cmds
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 10, 0), name=f'{prefix}_root')
    shoulder = cmds.joint(p=(0, 10, 0), name=f'{prefix}_shoulder')
    elbow = cmds.joint(p=(5, 7, 1), name=f'{prefix}_elbow')
    wrist = cmds.joint(p=(10, 10, 0), name=f'{prefix}_wrist')
    cmds.select(clear=True)
    return root, shoulder, elbow, wrist


def _make_arm_chain_offset(prefix, offset=(37.0, 52.0, -19.0)):
    """Same shoulder/elbow/wrist geometry as _make_arm_chain, but shifted
    well away from world origin. Required for the P2 posed-displacement /
    global-scale tests per the studio's deformer-order lesson (bind-pose-
    only or origin-centered asserts can't catch matrix-math bugs that only
    show up once the chain has moved away from identity)."""
    import maya.cmds as cmds
    ox, oy, oz = offset
    cmds.select(clear=True)
    root = cmds.joint(p=(0 + ox, 10 + oy, 0 + oz), name=f'{prefix}_root')
    shoulder = cmds.joint(p=(0 + ox, 10 + oy, 0 + oz), name=f'{prefix}_shoulder')
    elbow = cmds.joint(p=(5 + ox, 7 + oy, 1 + oz), name=f'{prefix}_elbow')
    wrist = cmds.joint(p=(10 + ox, 10 + oy, 0 + oz), name=f'{prefix}_wrist')
    cmds.select(clear=True)
    return root, shoulder, elbow, wrist


def _add_world_component(component_id, root_joint):
    from maya_tools.rigging.fabricator import nodes
    nodes.create_component_node(
        component_id=component_id, component_type='World',
        joints=[root_joint], parent_plug='', side='md',
        options={}, persisted={},
    )


def test_ikarm_build_creates_ik_fk_pv_switch_and_blend_works():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain('ikarmtest')

    nodes.create_registry('ikarm_test_bp')
    _add_world_component('ikarmtest_world', root)
    nodes.create_component_node(
        component_id='ikarmtest_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )

    fs_app.build_modules()

    fk_ctrls = [f'{shoulder}_FK_ctrl', f'{elbow}_FK_ctrl', f'{wrist}_FK_ctrl']
    ik_ctrl = f'{wrist}_IK_ctrl'
    pv_ctrl = f'{elbow}_PV_ctrl'
    switch_ctrl = f'{wrist}_IKFK_ctrl'

    for c in fk_ctrls + [ik_ctrl, pv_ctrl, switch_ctrl]:
        assert cmds.objExists(c), f'expected ctrl {c!r} to exist after build'
    assert cmds.attributeQuery('ik_fk_blend', node=switch_ctrl, exists=True)

    # IK mode (default ik_fk_blend=1.0): moving the IK ctrl must move the
    # bind wrist joint through the ikHandle -> blend -> bind chain. The
    # move must stay within the chain's rigid (non-stretchy) reach —
    # shoulder->elbow->wrist here totals ~11.83 vs. a 10.0 rest distance,
    # so a 1.0 nudge (well under the ~1.83 slack) tracks 1:1; a larger
    # move would clamp at full chain extension and under-report delta,
    # which is correct non-stretchy-IK behavior, not a bug.
    assert abs(cmds.getAttr(f'{switch_ctrl}.ik_fk_blend') - 1.0) < 1e-6
    before = cmds.xform(wrist, q=True, ws=True, t=True)
    cmds.xform(ik_ctrl, ws=True, r=True, t=(1.0, 0.0, 0.0))
    after = cmds.xform(wrist, q=True, ws=True, t=True)
    delta = [after[i] - before[i] for i in range(3)]
    assert abs(delta[0] - 1.0) < 1e-2, (
        f'IK ctrl move did not drive bind wrist: before={before} after={after}')

    # FK mode: switch, then rotate an FK ctrl and confirm the bind elbow
    # joint's rotation actually changes (proves the switch drives the
    # blend the OTHER direction too, not just a static IK pass-through).
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 0.0)
    elbow_rot_before = cmds.getAttr(f'{elbow}.rotate')[0]
    cmds.setAttr(f'{elbow}_FK_ctrl.rotateZ', 30.0)
    elbow_rot_after = cmds.getAttr(f'{elbow}.rotate')[0]
    assert elbow_rot_before != elbow_rot_after, (
        f'FK ctrl rotate did not drive bind elbow: '
        f'before={elbow_rot_before} after={elbow_rot_after}')

    fs_app.unbuild_modules()


def test_ikarm_unbuild_leaves_zero_orphans():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain('ikarmorphan')

    nodes.create_registry('ikarm_orphan_test_bp')
    _add_world_component('ikarmorphan_world', root)
    nodes.create_component_node(
        component_id='ikarmorphan_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )

    dg_before = _dg_type_counts()
    fs_app.build_modules()
    # Sanity: build actually created scaffolding of these types, otherwise
    # the "returns to baseline" check below would be vacuous.
    dg_during = _dg_type_counts()
    assert any(dg_during[t] > dg_before[t] for t in _OWNED_DG_TYPES), (
        f'build created none of the tracked DG types — test is vacuous: '
        f'before={dg_before} during={dg_during}')

    fs_app.unbuild_modules()

    assert not cmds.objExists('rig_grp'), 'rig_grp survived unbuild'

    named_nodes = (
        f'{shoulder}_ik_grp', f'{shoulder}_ctrl_offset',
        f'{shoulder}_FK_ctrl_offset', f'{shoulder}_FK_ctrl',
        f'{elbow}_FK_ctrl_offset', f'{elbow}_FK_ctrl',
        f'{wrist}_FK_ctrl_offset', f'{wrist}_FK_ctrl',
        f'{wrist}_IK_ctrl', f'{elbow}_PV_ctrl',
        f'{wrist}_IKFK_ctrl_offset', f'{wrist}_IKFK_ctrl',
        f'{shoulder}_setup_grp', f'{shoulder}_ik_anchor',
        f'{shoulder}_ikHandle', f'{elbow}_pv_line',
    )
    leaked = [n for n in named_nodes if cmds.objExists(n)]
    assert not leaked, f'named scaffolding nodes survived unbuild: {leaked}'

    dg_after = _dg_type_counts()
    assert dg_after == dg_before, (
        f'DG node counts did not return to pre-build baseline: '
        f'before={dg_before} after={dg_after}')

    # Bind joints themselves must survive (unbuild tears down the RIG, not
    # the skeleton) with their own constraints gone.
    for j in (shoulder, elbow, wrist):
        assert cmds.objExists(j), f'bind joint {j!r} should survive unbuild'
        cons = (cmds.listRelatives(j, type='parentConstraint') or []) + \
               (cmds.listRelatives(j, type='scaleConstraint') or [])
        assert not cons, f'bind joint {j!r} still has constraints: {cons}'


def test_regression_simple_ik_still_builds():
    # RibbonIKArm's module addition to modules/ (auto-discovered via pkgutil)
    # must not destabilize SimpleIK — same registry, same discovery walk.
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain('simpleiktest')

    nodes.create_registry('simple_ik_regress_bp')
    _add_world_component('simpleiktest_world', root)
    nodes.create_component_node(
        component_id='simpleiktest_C0', component_type='SimpleIK',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()
    for c in (f'{shoulder}_FK_ctrl', f'{wrist}_IK_ctrl', f'{elbow}_PV_ctrl',
              f'{wrist}_IKFK_ctrl'):
        assert cmds.objExists(c), f'SimpleIK regression: {c!r} missing'
    fs_app.unbuild_modules()
    assert not cmds.objExists('rig_grp'), 'SimpleIK regression: rig_grp survived unbuild'


def test_regression_ik_leg_still_builds():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 10, 0), name='iklegtest_root')
    hip = cmds.joint(p=(0, 10, 0), name='iklegtest_hip')
    knee = cmds.joint(p=(0, 5, 1), name='iklegtest_knee')
    ankle = cmds.joint(p=(0, 1, 0), name='iklegtest_ankle')
    ball = cmds.joint(p=(0, 0, 2), name='iklegtest_ball')
    cmds.select(clear=True)

    nodes.create_registry('ik_leg_regress_bp')
    _add_world_component('iklegtest_world', root)
    nodes.create_component_node(
        component_id='iklegtest_C0', component_type='IKLeg',
        joints=[hip, knee, ankle, ball], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()
    assert cmds.objExists(f'{ankle}_IK_ctrl'), 'IKLeg regression: foot ctrl missing'
    assert cmds.objExists(f'{ball}_ctrl'), 'IKLeg regression: ball ctrl missing'
    fs_app.unbuild_modules()
    assert not cmds.objExists('rig_grp'), 'IKLeg regression: rig_grp survived unbuild'


def _assert_switch_ctrl_is_ikfk_shape(switch_ctrl, radius_joint, label):
    """Shared CV-level proof (mirrors test_ikarm_fingers_ctrl_orientation_
    matches_wrist_and_offset's sine_handle check) that `switch_ctrl`'s
    built shape actually resolved to the curve-o-matic 'ikfk' library
    shape — not just "some shape got built without raising". Reads
    curve_data/ikfk.json directly (Adrian's authored gear+lettering mark)
    rather than hardcoding CV numbers here, so this stays correct if he
    ever re-edits the shape's CVs. build_shape() scales every CV by
    radius_joint's .radius uniformly (curve_o_matic_app.build_shape /
    _scale_curve_cvs), so we scale the library extent by that same radius
    before comparing.
    """
    import json
    import maya.cmds as cmds
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

    lib_path = com.CURVE_DATA_DIR / 'ikfk.json'
    lib_data = json.loads(lib_path.read_text())
    lib_cvs = [cv for shape in lib_data.get('shapes', []) for cv in shape['cvs']]
    assert lib_cvs, f"curve_data/ikfk.json has no CVs — can't verify {label}"
    expected_max_abs = max(abs(c) for cv in lib_cvs for c in cv[:2])

    radius = float(cmds.getAttr(f'{radius_joint}.radius'))
    expected_scaled = expected_max_abs * (radius if radius > 0 else 1.0)

    shape_data = com.serialize_shape(switch_ctrl)
    built_cvs = [cv for shape in shape_data.get('shapes', []) for cv in shape['cvs']]
    assert built_cvs, f'{switch_ctrl!r}: no CVs found on its shape node'
    actual_max_abs = max(abs(c) for cv in built_cvs for c in cv[:2])

    assert abs(actual_max_abs - expected_scaled) < 1e-3, (
        f'{label}: {switch_ctrl!r} max abs CV coord is {actual_max_abs}, '
        f'expected {expected_scaled} (curve_data/ikfk.json extent '
        f'{expected_max_abs} x radius {radius}) — the switch ctrl default '
        f"shape does not appear to have resolved to the 'ikfk' library "
        f'shape.')


def test_ikarm_switch_ctrl_default_shape_is_ikfk():
    """Task (2026-07-09): switch_ctrl_shape's OptionField default changed
    from 'cog' to 'ikfk' (Adrian's authored gear+lettering Curve-O-Matic
    shape) for every SimpleIK-family limb. This proves the default
    actually resolves on an RibbonIKArm build — a CV-level check, not just
    "no exception raised" — and that the switch ctrl's placement (world
    position) is untouched by the shape swap, matching the existing
    orientation/offset tests' expectations for this ctrl."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain('ikarmikfkshape')

    nodes.create_registry('ikarm_ikfk_shape_bp')
    _add_world_component('ikarmikfkshape_world', root)
    nodes.create_component_node(
        component_id='ikarmikfkshape_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()

    switch_ctrl = f'{wrist}_IKFK_ctrl'
    assert cmds.objExists(switch_ctrl), f'{switch_ctrl!r} missing'
    _assert_switch_ctrl_is_ikfk_shape(switch_ctrl, wrist, 'RibbonIKArm')

    # Placement unaffected: switch ctrl offset still sits at the wrist
    # world position plus the documented +10Z object-space nudge (same
    # invariant test_ikarm_fingers_ctrl_orientation_matches_wrist_and_offset
    # and the mirror-symmetry tests already cover for orientation — this
    # is just a presence-of-offset sanity check local to this test).
    switch_offset = f'{wrist}_IKFK_ctrl_offset'
    assert cmds.objExists(switch_offset), f'{switch_offset!r} missing'

    fs_app.unbuild_modules()
    assert not cmds.objExists('rig_grp'), (
        "ikfk-shape RibbonIKArm: rig_grp survived unbuild")


def test_ikleg_switch_ctrl_default_shape_is_ikfk():
    """Same proof as test_ikarm_switch_ctrl_default_shape_is_ikfk, but on
    the IKLeg regression fixture (test_regression_ik_leg_still_builds'
    joint layout) — SimpleIKComponent.build is shared by both, but IKLeg
    overrides/rebuilds enough around the switch ctrl's neighbors (foot
    ctrl reposition, reverse-foot pivot stack) that the base default must
    be proven on this fixture too, not just RibbonIKArm."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 10, 0), name='iklegikfkshape_root')
    hip = cmds.joint(p=(0, 10, 0), name='iklegikfkshape_hip')
    knee = cmds.joint(p=(0, 5, 1), name='iklegikfkshape_knee')
    ankle = cmds.joint(p=(0, 1, 0), name='iklegikfkshape_ankle')
    ball = cmds.joint(p=(0, 0, 2), name='iklegikfkshape_ball')
    cmds.select(clear=True)

    nodes.create_registry('ikleg_ikfk_shape_bp')
    _add_world_component('iklegikfkshape_world', root)
    nodes.create_component_node(
        component_id='iklegikfkshape_C0', component_type='IKLeg',
        joints=[hip, knee, ankle, ball], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()

    switch_ctrl = f'{ankle}_IKFK_ctrl'
    assert cmds.objExists(switch_ctrl), f'{switch_ctrl!r} missing'
    _assert_switch_ctrl_is_ikfk_shape(switch_ctrl, ankle, 'IKLeg')

    fs_app.unbuild_modules()
    assert not cmds.objExists('rig_grp'), (
        "ikfk-shape IKLeg: rig_grp survived unbuild")


# (test_regression_fk_chain_still_builds retired 2026-07-12 with the
# FKChain component itself: FKChain, Ribbon, and SplineFK are removed
# from the shipped component set at Adrian's final polish.)


def test_ikarm_ribbon_segments_exist_with_mids():
    """P2 Task 2.2 gate: two ribbon segments (upper-arm, forearm), each
    with mid_ctrl_count mids, and the shared-elbow control joints resolved
    as two distinct, independently-driven nodes (SPEC §7 elbow-ownership).
    Checked at BOTH bind pose and a live posed state (bent via the IK
    ctrl) — bind-pose-only asserts can't catch pose-induced drift between
    the two independently-constrained followers."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikribbon')

    nodes.create_registry('ikribbon_bp')
    _add_world_component('ikribbon_world', root)
    mid_count = 2
    nodes.create_component_node(
        component_id='ikribbon_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': mid_count}, persisted={},
    )
    fs_app.build_modules()

    upper_chain = rc.ribbon_segment_chain_name(shoulder, elbow)
    forearm_chain = rc.ribbon_segment_chain_name(elbow, wrist)

    for chain in (upper_chain, forearm_chain):
        assert cmds.objExists(f'{chain}_ribbon_surf'), f'{chain}: ribbon surface missing'
        assert cmds.objExists(f'{chain}_ribbon_uvpin'), f'{chain}: uvPin ride missing'
        assert cmds.objExists(f'{chain}_ribbon_skin'), f'{chain}: falloff skin missing'
        assert cmds.objExists(f'{chain}_ribbon_settings'), f'{chain}: board settings missing'
        for m in range(mid_count):
            ctrl = f'{chain}_mid_{m:02d}_ctrl'
            assert cmds.objExists(ctrl), f'{chain}: mid ctrl {m} missing'
            # Finding 1: mid ctrl scale is dead (no scaleConstraint reads
            # it — control-joint scale must stay locked at (1,1,1) or
            # global rig scale double-transforms the ribbon). It must be
            # locked + hidden, not exposed-but-inert.
            for a in ('sx', 'sy', 'sz'):
                assert cmds.getAttr(f'{ctrl}.{a}', lock=True), (
                    f'{ctrl}.{a} should be locked (dead, unwired channel)')
                assert not cmds.getAttr(f'{ctrl}.{a}', keyable=True), (
                    f'{ctrl}.{a} should not be keyable (dead, unwired channel)')
        assert not cmds.objExists(f'{chain}_mid_{mid_count:02d}_ctrl'), (
            f'{chain}: extra mid ctrl beyond mid_ctrl_count exists')

    # Elbow ownership: distinct nodes, both read-only-tracking the shared
    # elbow bind joint (no fight — see _limb_common.build_ribbon_segment
    # docstring for the full resolution).
    upper_end_cj = f'{upper_chain}_cj_{mid_count + 1:02d}'
    forearm_start_cj = f'{forearm_chain}_cj_00'
    assert cmds.objExists(upper_end_cj) and cmds.objExists(forearm_start_cj)
    assert upper_end_cj != forearm_start_cj, (
        'upper and forearm elbow control joints must be distinct nodes')
    elbow_pos = cmds.xform(elbow, q=True, ws=True, t=True)
    for cj in (upper_end_cj, forearm_start_cj):
        p = cmds.xform(cj, q=True, ws=True, t=True)
        d = sum((p[i] - elbow_pos[i]) ** 2 for i in range(3)) ** 0.5
        assert d < 0.5, f'{cj} not tracking elbow position: {p} vs {elbow_pos}'

    # ── SPEC §7 / PLAN Task 2.2 elbow-ownership, POSED (studio lesson:
    # bind-pose-only asserts can't catch pose-induced drift). Bend the arm
    # via the IK ctrl (same technique the posed-displacement test uses),
    # then re-query BOTH elbow-end control joints' world position AND
    # rotation. Position: both must still track the (now-posed) elbow
    # within the same tolerance as the bind-pose check above. Rotation:
    # a single-target parentConstraint(mo=True) guarantees
    # cj_rotation_current == deltaR(elbow) * cj_rotation_atBind exactly —
    # i.e. the ROTATION CHANGE each control joint undergoes from bind to
    # posed must equal elbow's own rotation change exactly (mo=True banks
    # a fixed offset; only elbow's live delta propagates). That equality
    # is the precise, coordinate-invariant form of "no fight, no drift"
    # between the two independently-constrained followers. ──
    import math as _math
    import maya.api.OpenMaya as _om

    def _world_quat(node):
        m = _om.MMatrix(cmds.xform(node, q=True, ws=True, matrix=True))
        return _om.MTransformationMatrix(m).rotation(asQuaternion=True)

    def _delta_angle_deg(q_before, q_after):
        delta = q_after * q_before.inverse()
        w = max(-1.0, min(1.0, delta.w))
        return _math.degrees(2.0 * _math.acos(abs(w)))

    elbow_quat_bind = _world_quat(elbow)
    upper_quat_bind = _world_quat(upper_end_cj)
    forearm_quat_bind = _world_quat(forearm_start_cj)

    ik_ctrl = f'{wrist}_IK_ctrl'
    assert cmds.objExists(ik_ctrl)
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(0.0, -1.0, 0.5))

    posed_elbow_pos = cmds.xform(elbow, q=True, ws=True, t=True)
    for cj in (upper_end_cj, forearm_start_cj):
        p = cmds.xform(cj, q=True, ws=True, t=True)
        d = sum((p[i] - posed_elbow_pos[i]) ** 2 for i in range(3)) ** 0.5
        assert d < 0.5, (
            f'{cj} drifted from the POSED elbow position: {p} vs '
            f'{posed_elbow_pos} (SPEC §7: no drift under posing)')

    elbow_delta = _delta_angle_deg(elbow_quat_bind, _world_quat(elbow))
    assert elbow_delta > 1.0, (
        f'pose did not actually rotate the elbow ({elbow_delta} deg) — '
        f'this check would be vacuous')

    for name, quat_bind in (('upper_end_cj', upper_quat_bind),
                            ('forearm_start_cj', forearm_quat_bind)):
        cj = upper_end_cj if name == 'upper_end_cj' else forearm_start_cj
        cj_delta = _delta_angle_deg(quat_bind, _world_quat(cj))
        assert abs(cj_delta - elbow_delta) < 2.0, (
            f'{name} rotation delta under pose ({cj_delta} deg) does not '
            f'match elbow\'s own rotation delta ({elbow_delta} deg) — the '
            f'control joint drifted from its read-only elbow constraint '
            f'under posing (SPEC §7 "no fight, no drift")')

    fs_app.unbuild_modules()


def test_ikarm_ribbon_width_auto_resolves_to_25_percent_of_bone_length():
    """Ship-gate review coverage gap: ribbon_ik_arm.py's ribbon_width OptionField
    default (0.0 = 'auto') resolves via the shared _ribbon_common.
    resolve_ribbon_width helper, which for RibbonIKArm's always-exactly-2-joint
    per-bone segment calls (joints=[start, end]) collapses its
    max(10% of total, 25% of shortest sub-segment) formula to EXACTLY 25%
    of the bone's length (total and seg_min are the same single value for
    a 2-point call) — NOT the 10% a stale draft of the OptionField
    description once implied (now corrected there too). Pins the resolved
    fraction, measured directly off each ribbon surface's first-row CV
    span (the same technique RibbonIKArmComponent.unbuild uses to capture
    ribbon_width for persistence), so a change to either the shared
    formula or RibbonIKArm's call into it can't silently drift undetected.
    test_ikarm_ribbon_persistence_round_trip covers round-tripping and is
    deliberately agnostic to the exact fraction — this test is the one
    that pins it."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikwidth')

    nodes.create_registry('ikwidth_bp')
    _add_world_component('ikwidth_world', root)
    nodes.create_component_node(
        component_id='ikwidth_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        # ribbon_width left unset -> schema default 0.0 (auto).
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    def _bone_len(a, b):
        pa = cmds.xform(a, q=True, ws=True, t=True)
        pb = cmds.xform(b, q=True, ws=True, t=True)
        return sum((pa[i] - pb[i]) ** 2 for i in range(3)) ** 0.5

    def _measured_width(chain):
        surf = f'{chain}_ribbon_surf'
        a = cmds.pointPosition(f'{surf}.cv[0][0]', world=True)
        b = cmds.pointPosition(f'{surf}.cv[1][0]', world=True)
        return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5

    for chain, start_j, end_j in (
            (rc.ribbon_segment_chain_name(shoulder, elbow), shoulder, elbow),
            (rc.ribbon_segment_chain_name(elbow, wrist), elbow, wrist)):
        bone_len = _bone_len(start_j, end_j)
        expected = 0.25 * bone_len
        measured = _measured_width(chain)
        assert abs(measured - expected) < 1e-3, (
            f'{chain}: auto ribbon_width resolved to {measured} for a '
            f'{bone_len} bone-length segment — expected exactly 25% '
            f'({expected}) per resolve_ribbon_width\'s formula collapsing '
            f'to 25% for a 2-joint RibbonIKArm segment call. If this legitimately '
            f'changed, also update ribbon_ik_arm.py\'s ribbon_width OptionField '
            f'description to match.')

    fs_app.unbuild_modules()


def test_ikarm_ribbon_twist_and_volume_posed_displacement():
    """P2 CRITICAL gate: twist distribution + volume preservation measured
    at POSED DISPLACEMENT on an off-origin chain, never bind pose (studio
    deformer-order lesson — bind-pose asserts can't catch deformer-order/
    ride bugs). Bends the elbow AND twists the wrist via the IK ctrl, then
    reads ride-joint rotate/scale off that live pose. Also exercises: an
    interior ride joint's actual ROTATE channel before/after the pose
    (build_ride_uvpin's decomposeMatrix -> j.rotate wire — the literal
    ride a mesh skin would bind to, not just the deformed surface's raw
    CVs), and the twist BOARD dial (twist_root/twist_tip) end-to-end,
    since the falloff-skin twist measured elsewhere in this test is a
    no-op stand-in for the sculpted board path while the dial sits at 0."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikpose')

    nodes.create_registry('ikpose_bp')
    _add_world_component('ikpose_world', root)
    mid_count = 2
    nodes.create_component_node(
        component_id='ikpose_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': mid_count}, persisted={},
    )
    fs_app.build_modules()

    forearm_chain = rc.ribbon_segment_chain_name(elbow, wrist)
    forearm_ride = sorted(cmds.ls(f'{forearm_chain}_ride_*', type='joint'))
    assert len(forearm_ride) >= 4, (
        f'expected several forearm ride joints, got {forearm_ride}')

    # ── Ride-joint ROTATE channel, captured BEFORE the pose (finding 4):
    # the twist assertions below read the deformed SURFACE geometry, which
    # exercises the falloff skin but NOT build_ride_uvpin's decomposeMatrix
    # -> j.rotate wire specifically — the literal "ribbon ride" a mesh
    # would actually skin to. Capture a before/after baseline on one
    # interior ride joint's rotateX so a broken/disconnected rotate wire
    # (wrong node, dead connectAttr, etc.) fails this test instead of
    # passing silently. ──
    row_count = len(forearm_ride)
    mid_i = row_count // 2
    ride_probe = forearm_ride[mid_i]
    ride_rotateX_before = cmds.getAttr(f'{ride_probe}.rotateX')

    # ── Pose a driver: bend the elbow (nudge the IK ctrl within the
    # chain's rigid non-stretchy slack: rest reach ~11.83 vs. shoulder-
    # wrist rest distance 10.0 -> ~1.83 slack) AND twist the wrist
    # (rotate the IK ctrl about its local, bone-aligned X). ──
    ik_ctrl = f'{wrist}_IK_ctrl'
    assert cmds.objExists(ik_ctrl)
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(0.0, -1.0, 0.5))
    cmds.setAttr(f'{ik_ctrl}.rotateX', 45.0)

    # ── Twist distribution: measured directly off the DEFORMED ribbon
    # surface's geometry (cross-section vector roll), not a joint's Euler
    # 'rotate' reading. decomposeMatrix's Euler XYZ output on a bending
    # (not pure-twist) chain trades rotation between axes non-uniquely
    # near gimbal-adjacent poses, which makes raw rotateX comparisons
    # unreliable between rows even when the underlying twist genuinely IS
    # distributing correctly. A signed roll angle between each row's
    # surface cross-section vector (surf.cv[0][k] -> surf.cv[1][k]) and
    # the root row's cross vector, measured about the segment's own posed
    # axis, is immune to that — exactly the "sample geometry displacement"
    # approach the studio's deformer-order lesson calls for. ──
    import math

    def _norm(v):
        m = sum(c * c for c in v) ** 0.5
        return [c / m for c in v] if m > 1e-9 else v

    def _cross_vec(row):
        a = cmds.pointPosition(f'{surf}.cv[0][{row}]', world=True)
        b = cmds.pointPosition(f'{surf}.cv[1][{row}]', world=True)
        return [b[i] - a[i] for i in range(3)]

    def _signed_roll_deg(v_ref, v, axis):
        v_ref, v, axis = _norm(v_ref), _norm(v), _norm(axis)
        dot = sum(v_ref[i] * v[i] for i in range(3))
        cr = [v_ref[1] * v[2] - v_ref[2] * v[1],
              v_ref[2] * v[0] - v_ref[0] * v[2],
              v_ref[0] * v[1] - v_ref[1] * v[0]]
        sin_component = sum(cr[i] * axis[i] for i in range(3))
        return math.degrees(math.atan2(sin_component, dot))

    surf = f'{forearm_chain}_ribbon_surf'
    assert cmds.objExists(surf)

    p_root = cmds.xform(forearm_ride[0], q=True, ws=True, t=True)
    p_tip = cmds.xform(forearm_ride[-1], q=True, ws=True, t=True)
    axis = [p_tip[i] - p_root[i] for i in range(3)]

    # The two floating mid ctrls are "always-on aimers" (SPEC 3.2): their
    # orientation tracks end_joint's world ROTATION via the aimConstraint's
    # objectrotation up-vector (by design — it's what keeps a mid ctrl from
    # independently flipping/gimballing as the chain bends), so a mid
    # control joint near the WRIST-side mid picks up substantial twist
    # almost immediately, not a slow linear ramp. The genuinely gradient
    # region is the falloff skin's interpolation between the ELBOW-side
    # control joint (which reads ONLY start_joint=elbow — untouched by the
    # wrist rotation) and the first mid — so the meaningful, always-true
    # claim is: near the elbow end, roll stays small; by the wrist end, it
    # tracks the driven twist. That is what "twist distributes along the
    # segment" (not "is rigidly uniform along the whole segment") means
    # here.
    v_ref = _cross_vec(0)
    roll_near_root = _signed_roll_deg(v_ref, _cross_vec(1), axis)
    roll_tip = _signed_roll_deg(v_ref, _cross_vec(row_count - 1), axis)

    assert abs(roll_tip) > 20.0, (
        f'twist did not reach the wrist end: roll_tip={roll_tip} deg '
        f'(expected it to track the 45 deg wrist twist)')
    assert abs(roll_near_root) < abs(roll_tip) - 10.0, (
        f'twist did not distribute FROM the elbow end (near-root roll '
        f'should read well below the tip): roll_near_root={roll_near_root} '
        f'roll_tip={roll_tip}')

    # ── Interior-row distribution gate (fix for a P3 finding: the mid
    # ctrls' own aimConstraint used to read end_joint's RAW rotation for
    # its up-vector, wired up before P3's roll-joint substitution ever
    # touches them — so only the single hard-pinned boundary row (the
    # twisting end) carried the roll joint's clean signal; every OTHER
    # row, including every interior falloff-blended row a mid ctrl
    # drives, got ZERO contribution from it, contradicting SPEC 3.2's
    # "twist distributes ... along the segment" claim). A genuinely
    # INTERIOR row — not near-root, not the tip — must show a
    # SUBSTANTIAL fraction of the tip's twist; under the old (broken)
    # wiring this row read ~0 regardless of wrist twist. ──
    interior_row = row_count // 4  # e.g. row 2 of 8 — inside the segment,
                                    # nowhere near either boundary control
                                    # joint or the tip.
    assert 0 < interior_row < row_count - 1
    roll_interior = _signed_roll_deg(v_ref, _cross_vec(interior_row), axis)
    assert abs(roll_interior) > 0.3 * abs(roll_tip), (
        f'twist did not reach an interior row (row {interior_row} of '
        f'{row_count}) — expected a substantial fraction of the tip '
        f'twist, not the near-zero reading a mid ctrl driven purely by '
        f'the raw bind joint (pre-roll-joint P2 wiring) would produce: '
        f'roll_interior={roll_interior} roll_tip={roll_tip}')

    # ── Ride-joint ROTATE wire, after/before (finding 4): this is the
    # channel build_ride_uvpin's decomposeMatrix -> j.rotate connectAttr
    # (base chain, or the jointOrient-fold branch on legacy-oriented
    # joints) actually drives — the thing a downstream mesh skin binds to.
    # A dead/disconnected wire would leave rotateX frozen at its build-time
    # value regardless of the wrist-twist pose above; assert it moved. ──
    ride_rotateX_after = cmds.getAttr(f'{ride_probe}.rotateX')
    assert abs(ride_rotateX_after - ride_rotateX_before) > 5.0, (
        f'forearm ride joint {ride_probe!r} rotateX did not change under '
        f'the wrist-twist pose (build_ride_uvpin\'s rotate wire may be '
        f'dead): before={ride_rotateX_before} after={ride_rotateX_after}')

    # ── Volume: measured OFF a posed, locally-stretched ribbon. Moving a
    # mid ctrl increases the segment's arc length WITHOUT any change to
    # rootScale, so — with volume=1 (default ON) — the cross-section
    # (ride-joint scaleY) must measurably shrink; with volume=0, it must
    # stay ~unit at the SAME stretched pose (proves causality, not an
    # unrelated artifact). build_volume's ratio is a single arc-length
    # value shared by every ride joint on the segment (uniform along-
    # ribbon squash/stretch), so any ride joint works as the probe. ──
    mid0 = f'{forearm_chain}_mid_00_ctrl'
    settings = f'{forearm_chain}_ribbon_settings'
    assert cmds.objExists(mid0) and cmds.objExists(settings)
    assert abs(cmds.getAttr(f'{settings}.volume') - 1.0) < 1e-6, (
        'volume preservation should default ON for the arm ribbon (SPEC 3.2)')

    probe = forearm_ride[mid_i]
    scale_before = cmds.getAttr(f'{probe}.scaleY')
    assert abs(scale_before - 1.0) < 0.1, (
        f'expected ~unit cross-section before the local stretch: {scale_before}')

    cmds.setAttr(f'{mid0}.ty', 3.0)   # local stretch, off-origin, posed
    scale_stretched = cmds.getAttr(f'{probe}.scaleY')
    assert scale_stretched < scale_before - 0.02, (
        f'volume=1 should shrink the cross-section on stretch: '
        f'before={scale_before} after={scale_stretched}')

    cmds.setAttr(f'{settings}.volume', 0.0)
    scale_no_volume = cmds.getAttr(f'{probe}.scaleY')
    assert abs(scale_no_volume - 1.0) < 0.1, (
        f'volume=0 should leave the cross-section ~unit even at the same '
        f'stretched pose: got {scale_no_volume}')

    # ── Twist BOARD dial coverage (finding 7): everything above measures
    # twist produced by the falloff SKIN distributing the wrist bind
    # joint's rotation — with twist_root/twist_tip left at their
    # BOARD_ATTRS default of 0.0 the whole test, the sculpted twist-board
    # path (build_sculpt_target -> add_board_blend -> connectAttr into the
    # nonlinear twist deformer) is a mathematical no-op regardless of
    # whether its wiring is actually correct. Dial it explicitly and
    # re-read the SAME signed-roll-via-CV-cross-vector measurement: the
    # roll at the tip must move measurably and monotonically with the
    # dial, and revert when the dial returns to 0 — proving the
    # connectAttr chain from settings through the twist deformer into the
    # deformed surface is live, not just topologically ordered correctly. ──
    roll_tip_dial_0 = roll_tip   # baseline from the wrist-twist pose above

    # Direction-agnostic: the twist deformer's startAngle/endAngle sign
    # convention relative to this measurement's axis isn't asserted
    # anywhere else, so a positive dial could legitimately move the
    # measured roll in either direction — what matters is that it moves,
    # consistently, and monotonically with magnitude.
    cmds.setAttr(f'{settings}.twist_tip', 30.0)
    roll_tip_dial_30 = _signed_roll_deg(v_ref, _cross_vec(row_count - 1), axis)
    delta_30 = roll_tip_dial_30 - roll_tip_dial_0
    assert abs(delta_30) > 10.0, (
        f'twist_tip dial did not move the deformed surface: '
        f'baseline={roll_tip_dial_0} dialed(30)={roll_tip_dial_30}')

    cmds.setAttr(f'{settings}.twist_tip', 60.0)
    roll_tip_dial_60 = _signed_roll_deg(v_ref, _cross_vec(row_count - 1), axis)
    delta_60 = roll_tip_dial_60 - roll_tip_dial_0
    assert (delta_30 > 0) == (delta_60 > 0), (
        f'twist_tip dial reversed direction between 30deg and 60deg — not '
        f'monotonic: 0deg={roll_tip_dial_0} 30deg={roll_tip_dial_30} '
        f'60deg={roll_tip_dial_60}')
    assert abs(delta_60) > abs(delta_30) + 5.0, (
        f'twist_tip dial did not move the roll monotonically: '
        f'0deg={roll_tip_dial_0} 30deg={roll_tip_dial_30} '
        f'60deg={roll_tip_dial_60}')

    cmds.setAttr(f'{settings}.twist_tip', 0.0)
    roll_tip_dial_reverted = _signed_roll_deg(v_ref, _cross_vec(row_count - 1), axis)
    assert abs(roll_tip_dial_reverted - roll_tip_dial_0) < 2.0, (
        f'twist_tip dial did not revert the roll back to baseline when set '
        f'to 0: baseline={roll_tip_dial_0} '
        f'after-revert={roll_tip_dial_reverted}')

    fs_app.unbuild_modules()


def test_ikarm_ribbon_no_double_transform_on_global_scale():
    """P2 hard constraint: a pure uniform World-ctrl scale must NOT read as
    ribbon stretch. build_volume's root_matrix_plug (RibbonIKArm's anchor_out)
    cancels the global scale out of the arc-length ratio; verified at a
    live, off-origin, 2x-scaled pose."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikscale')

    nodes.create_registry('ikscale_bp')
    _add_world_component('ikscale_world', root)
    nodes.create_component_node(
        component_id='ikscale_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    forearm_chain = rc.ribbon_segment_chain_name(elbow, wrist)
    ride = sorted(cmds.ls(f'{forearm_chain}_ride_*', type='joint'))
    assert len(ride) >= 4
    probe = ride[len(ride) // 2]

    scale_before = cmds.getAttr(f'{probe}.scaleY')
    pos_a_before = cmds.xform(ride[0], q=True, ws=True, t=True)
    pos_b_before = cmds.xform(ride[-1], q=True, ws=True, t=True)
    dist_before = sum((pos_a_before[i] - pos_b_before[i]) ** 2
                      for i in range(3)) ** 0.5

    world_ctrl = f'{root}_ctrl'
    assert cmds.objExists(world_ctrl), f'expected World ctrl {world_ctrl!r}'
    # World's default 'channels' option locks scale (not in the keyable
    # whitelist) — unlock for this test's purposes only.
    for a in ('sx', 'sy', 'sz'):
        cmds.setAttr(f'{world_ctrl}.{a}', lock=False)
        cmds.setAttr(f'{world_ctrl}.{a}', 2.0)

    # Sanity: the scale actually propagated to the ribbon's world geometry
    # (otherwise the volume assertion below would pass for the wrong
    # reason — nothing moved at all).
    pos_a_after = cmds.xform(ride[0], q=True, ws=True, t=True)
    pos_b_after = cmds.xform(ride[-1], q=True, ws=True, t=True)
    dist_after = sum((pos_a_after[i] - pos_b_after[i]) ** 2
                     for i in range(3)) ** 0.5
    assert dist_after > dist_before * 1.5, (
        f'global scale did not propagate to the ribbon geometry: '
        f'before={dist_before} after={dist_after}')

    scale_after = cmds.getAttr(f'{probe}.scaleY')
    assert abs(scale_after - scale_before) < 0.05, (
        f'global rig scale leaked into volume compensation (double-'
        f'transform): scaleY before={scale_before} after={scale_after}')

    fs_app.unbuild_modules()


def test_ikarm_ribbon_unbuild_leaves_zero_orphans():
    """P2: unbuild deletes the FULL skin node-set for BOTH segments
    (COMPONENT_AUTHORING §6-A/B) — DG type counts return to pre-build
    baseline, and every ribbon-named node (surfaces, curves, control/ride
    joints, mid ctrls, settings, skin) is gone. Bind joints survive."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikribbonorphan')

    nodes.create_registry('ikribbonorphan_bp')
    _add_world_component('ikribbonorphan_world', root)
    mid_count = 2
    nodes.create_component_node(
        component_id='ikribbonorphan_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': mid_count}, persisted={},
    )

    dg_before = _dg_type_counts()
    fs_app.build_modules()
    dg_during = _dg_type_counts()
    assert any(dg_during[t] > dg_before[t] for t in _OWNED_DG_TYPES), (
        f'build created none of the tracked DG types — test is vacuous: '
        f'before={dg_before} during={dg_during}')
    assert dg_during.get('skinCluster', 0) >= 2, (
        'expected a falloff skinCluster per ribbon segment (2 total)')

    upper_chain = rc.ribbon_segment_chain_name(shoulder, elbow)
    forearm_chain = rc.ribbon_segment_chain_name(elbow, wrist)

    fs_app.unbuild_modules()

    assert not cmds.objExists('rig_grp'), 'rig_grp survived unbuild'

    dg_after = _dg_type_counts()
    assert dg_after == dg_before, (
        f'DG node counts did not return to pre-build baseline: '
        f'before={dg_before} after={dg_after}')

    leaked = []
    for chain in (upper_chain, forearm_chain):
        leaked += [n for n in (
            f'{chain}_ribbon_surf', f'{chain}_center_crv',
            f'{chain}_ribbon_skin', f'{chain}_ribbon_uvpin',
            f'{chain}_ribbon_settings', f'{chain}_setup_grp',
            f'{chain}_cj_00', f'{chain}_ride_00',
        ) if cmds.objExists(n)]
        for m in range(mid_count):
            for suffix in (f'_mid_{m:02d}_ctrl', f'_mid_{m:02d}_ctrl_offset'):
                n = f'{chain}{suffix}'
                if cmds.objExists(n):
                    leaked.append(n)
    assert not leaked, f'ribbon nodes survived unbuild: {leaked}'

    for j in (shoulder, elbow, wrist):
        assert cmds.objExists(j), f'bind joint {j!r} should survive unbuild'
        cons = (cmds.listRelatives(j, type='parentConstraint') or []) + \
               (cmds.listRelatives(j, type='scaleConstraint') or [])
        assert not cons, f'bind joint {j!r} still has constraints: {cons}'


def test_ikarm_ribbon_persistence_round_trip():
    """Findings 6/8/9 regression: the board dials (twist_root/twist_tip/
    volume), the resolved rest_len, and the resolved auto ribbon_width
    must all round-trip through unbuild -> build, and 'Reset Control
    Shapes' must blank ONLY the per-ctrl CV map — never rest_len, the
    board dials, or the resolved width. Every other test in this suite
    does a single build->unbuild cycle with reset_ctrl_shapes never set
    and never rebuilds twice, so this path was previously unexercised."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikpersist')

    nodes.create_registry('ikpersist_bp')
    _add_world_component('ikpersist_world', root)
    nodes.create_component_node(
        component_id='ikpersist_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    forearm_chain = rc.ribbon_segment_chain_name(elbow, wrist)
    settings = f'{forearm_chain}_ribbon_settings'
    rest_node = f'{forearm_chain}_ribbon_vol_rest'
    surf = f'{forearm_chain}_ribbon_surf'
    assert cmds.objExists(settings) and cmds.objExists(rest_node)

    def _measured_width():
        a = cmds.pointPosition(f'{surf}.cv[0][0]', world=True)
        b = cmds.pointPosition(f'{surf}.cv[1][0]', world=True)
        return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5

    width_built = _measured_width()
    rest_len_built = float(cmds.getAttr(f'{rest_node}.input1'))

    # Dial the board away from its BOARD_ATTRS defaults.
    cmds.setAttr(f'{settings}.twist_root', 12.0)
    cmds.setAttr(f'{settings}.twist_tip', -18.0)
    cmds.setAttr(f'{settings}.volume', 0.4)

    elbow_pos0 = cmds.xform(elbow, q=True, ws=True, t=True)
    wrist_pos0 = cmds.xform(wrist, q=True, ws=True, t=True)
    bone_vec = [wrist_pos0[i] - elbow_pos0[i] for i in range(3)]
    bone_len0 = sum(c * c for c in bone_vec) ** 0.5
    bone_unit = [c / bone_len0 for c in bone_vec]

    fs_app.unbuild_modules()

    # Simulate a rigger's Edit-Mode skeleton tweak BETWEEN unbuild and the
    # next build: directly extend the elbow-wrist bone now that unbuild
    # has disconnected the non-stretchy IK/FK constraints (bind joints are
    # freely movable again, exactly like Edit Mode). A live IK-ctrl move
    # can't produce this signal: a rigid (non-stretchy) rotate-plane IK
    # chain preserves each bone's own length by construction regardless of
    # target position (verified empirically — moving the IK ctrl leaves
    # elbow-wrist distance unchanged to float precision, since the whole
    # rigid chain just re-orients). Bone length can only actually differ
    # between builds via a real skeleton edit — precisely the scenario
    # finding 9 describes ("posed/stretched AT THE MOMENT Edit Mode/
    # unbuild is invoked").
    cmds.xform(wrist, ws=True, relative=True,
              t=tuple(c * 1.0 for c in bone_unit))

    elbow_pos1 = cmds.xform(elbow, q=True, ws=True, t=True)
    wrist_pos1 = cmds.xform(wrist, q=True, ws=True, t=True)
    bone_len1 = sum((wrist_pos1[i] - elbow_pos1[i]) ** 2
                    for i in range(3)) ** 0.5
    assert bone_len1 > bone_len0 + 0.3, (
        f'the skeleton edit did not extend the bone enough for this test '
        f'to be meaningful: before={bone_len0} after={bone_len1}')
    # Self-referential estimate of what a BUGGY re-measurement (instead of
    # using the persisted width) would produce — robust to the exact
    # auto-width fraction resolve_ribbon_width uses, since that formula is
    # linear in bone length either way.
    wrong_width_estimate = width_built * (bone_len1 / bone_len0)
    assert abs(wrong_width_estimate - width_built) > 0.1, (
        'expected the re-measured estimate to differ from the persisted '
        'width by a clear margin; test setup is too close to call')

    fs_app.build_modules()   # rebuild WITHOUT reset_ctrl_shapes

    assert cmds.objExists(settings) and cmds.objExists(rest_node)
    assert abs(cmds.getAttr(f'{settings}.twist_root') - 12.0) < 1e-4, (
        'twist_root did not round-trip through unbuild -> build')
    assert abs(cmds.getAttr(f'{settings}.twist_tip') - (-18.0)) < 1e-4, (
        'twist_tip did not round-trip through unbuild -> build')
    assert abs(cmds.getAttr(f'{settings}.volume') - 0.4) < 1e-4, (
        'volume dial did not round-trip through unbuild -> build')
    assert abs(float(cmds.getAttr(f'{rest_node}.input1')) - rest_len_built) < 0.05, (
        'rest_len drifted on a plain rebuild (no reset_ctrl_shapes) — '
        'should have been sourced from the persisted value, not re-'
        'measured off the (now posed) surface')
    rebuilt_width = _measured_width()
    assert abs(rebuilt_width - width_built) < abs(wrong_width_estimate - width_built) * 0.4, (
        f'rebuilt ribbon_width ({rebuilt_width}) looks re-measured off the '
        f'CURRENT (posed) bone length instead of using the persisted '
        f'resolved value: built={width_built} '
        f'would-be-wrong≈{wrong_width_estimate}')

    # Unbuild + rebuild WITH reset_ctrl_shapes=True: the flag must be
    # shape-only — rest_len/board/resolved-width must survive it.
    fs_app.unbuild_modules()
    fs_app.build_modules(options={'reset_ctrl_shapes': True})

    assert abs(cmds.getAttr(f'{settings}.twist_root') - 12.0) < 1e-4, (
        'reset_ctrl_shapes wiped twist_root — the flag must be shape-only')
    assert abs(cmds.getAttr(f'{settings}.twist_tip') - (-18.0)) < 1e-4, (
        'reset_ctrl_shapes wiped twist_tip — the flag must be shape-only')
    assert abs(cmds.getAttr(f'{settings}.volume') - 0.4) < 1e-4, (
        'reset_ctrl_shapes wiped the volume dial — the flag must be '
        'shape-only')
    assert abs(float(cmds.getAttr(f'{rest_node}.input1')) - rest_len_built) < 0.05, (
        'reset_ctrl_shapes re-baselined rest_len from the (posed) rebuild '
        '— the flag must be shape-only')
    rebuilt_width2 = _measured_width()
    assert abs(rebuilt_width2 - width_built) < abs(wrong_width_estimate - width_built) * 0.4, (
        'reset_ctrl_shapes disturbed the persisted resolved ribbon_width — '
        'the flag must be shape-only')

    fs_app.unbuild_modules()


def test_ikarm_unbuild_ribbon_cleanup_survives_joint_count_drift():
    """Finding 2 regression: unbuild()'s ribbon DG-bucket sweep must run
    even when instance.joints[] has drifted from exactly 3 (e.g. an
    unrepaired duplicate/mirror overconnection propagating .message into
    the joints[] multi — COMPONENT_AUTHORING §4 Gotcha 1, and the reason
    fs_app.find_overconnected_components() exists). A prior version of
    unbuild() early-returned super().unbuild(instance) whenever
    len(joints) != 3, skipping the ENTIRE ribbon skin/DG node-set teardown
    and leaking every ribbon node as a permanent zombie (no DAG parent, so
    rig_grp's cascade never catches them)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc
    from maya_tools.rigging.fabricator.modules import get_component_class
    from maya_tools.rigging.fabricator.modules.component import ComponentInstance

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikdrift')

    nodes.create_registry('ikdrift_bp')
    _add_world_component('ikdrift_world', root)
    component_id = 'ikdrift_C0'
    nodes.create_component_node(
        component_id=component_id, component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    forearm_chain = rc.ribbon_segment_chain_name(elbow, wrist)
    upper_chain = rc.ribbon_segment_chain_name(shoulder, elbow)
    ribbon_nodes = [
        f'{forearm_chain}_ribbon_surf', f'{forearm_chain}_ribbon_skin',
        f'{forearm_chain}_ribbon_uvpin', f'{forearm_chain}_ribbon_settings',
        f'{upper_chain}_ribbon_surf', f'{upper_chain}_ribbon_skin',
        f'{upper_chain}_ribbon_uvpin', f'{upper_chain}_ribbon_settings',
    ]
    assert all(cmds.objExists(n) for n in ribbon_nodes), (
        f'setup: expected ribbon nodes to exist before the drift test: '
        f'{[n for n in ribbon_nodes if not cmds.objExists(n)]}')

    # Simulate joints[]-multi drift (an extra 4th connection, matching the
    # COMPONENT_AUTHORING §4 Gotcha 1 signature) by calling unbuild()
    # directly with a hand-built instance carrying 4 joints. Same
    # component id, so _resolve_component_node still finds the REAL built
    # component node and its tracked ribbon DG buckets.
    cls = get_component_class('RibbonIKArm')
    drifted = ComponentInstance(
        id=component_id, type='RibbonIKArm',
        joints=[shoulder, elbow, wrist, root],
        parent_plug='', side='md', options={'mid_ctrl_count': 1},
        persisted={},
    )
    cls.unbuild(drifted)

    leaked = [n for n in ribbon_nodes if cmds.objExists(n)]
    assert not leaked, (
        f'ribbon DG node-set survived unbuild() on a drifted (4-joint) '
        f'instance — the bucket sweep must run regardless of joint count: '
        f'{leaked}')


# ─── Phase 3: antCGi aim+IK roll joints ───────────────────────────────────

def test_ikarm_roll_anti_flip_and_twist_response():
    """ANTI-FLIP + twist-response gate (task requirement): pose a PURE
    BEND (an IK-ctrl POSITION move only — no wrist rotation) and assert
    BOTH roll joints' twist reading stays ~ZERO. Then pose a wrist TWIST
    and assert the FOREARM roll's twist reading responds substantially and
    MONOTONICALLY (both twist directions), while the UPPER roll's stays
    pinned near zero throughout — proving the forearm roll's driver-
    parented locator PASSES twist through (SPEC 3.3), exactly as designed.

    NOTE: the wrist-rotate stimulus above can NEVER kinematically reach
    the shoulder/upper-roll anchor in this rig (the IK handle's rotate-
    plane solve and the wrist joint's orientConstraint are two fully
    decoupled paths — see simple_ik.py build()) — so the upper-roll
    assertions against THAT stimulus are trivially true regardless of
    whether the upper roll's filtering mechanism works at all. The block
    below drives a GENUINE shoulder-twist stimulus instead (FK mode,
    shoulder_FK_ctrl.rotateX — a pure axial rotation about the bone,
    which DOES reach the upper roll's anchor joint) — the only stimulus
    that can actually exercise the upper roll's claimed twist-filtering
    behavior, proving the locator's pointConstrained-to-anchor wiring
    (see _limb_common.build_roll_joint's docstring) genuinely filters
    twist rather than merely never being tested."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikrollflip')

    nodes.create_registry('ikrollflip_bp')
    _add_world_component('ikrollflip_world', root)
    nodes.create_component_node(
        component_id='ikrollflip_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    forearm_roll_jnt = f'{hc.short_name(elbow)}_{hc.short_name(wrist)}_roll_jnt'
    upper_roll_jnt = f'{hc.short_name(shoulder)}_{hc.short_name(elbow)}_roll_jnt'
    assert cmds.objExists(forearm_roll_jnt), 'forearm roll joint missing'
    assert cmds.objExists(upper_roll_jnt), 'upper roll joint missing'

    up_local_lower = rc.ROLL_AXES_LOWER[1]
    up_local_upper = rc.ROLL_AXES_UPPER[1]

    shoulder_pos_bind = cmds.xform(shoulder, q=True, ws=True, t=True)
    elbow_pos_bind = cmds.xform(elbow, q=True, ws=True, t=True)
    wrist_pos_bind = cmds.xform(wrist, q=True, ws=True, t=True)
    axis_upper_bind = _v_sub(elbow_pos_bind, shoulder_pos_bind)
    axis_forearm_bind = _v_sub(wrist_pos_bind, elbow_pos_bind)
    up_upper_bind = _world_dir(upper_roll_jnt, up_local_upper)
    up_forearm_bind = _world_dir(forearm_roll_jnt, up_local_lower)

    def _measure():
        s = cmds.xform(shoulder, q=True, ws=True, t=True)
        e = cmds.xform(elbow, q=True, ws=True, t=True)
        w = cmds.xform(wrist, q=True, ws=True, t=True)
        axis_upper_now = _v_sub(e, s)
        axis_forearm_now = _v_sub(w, e)
        up_upper_now = _world_dir(upper_roll_jnt, up_local_upper)
        up_forearm_now = _world_dir(forearm_roll_jnt, up_local_lower)
        t_upper = _true_twist_deg(axis_upper_bind, axis_upper_now,
                                  up_upper_bind, up_upper_now)
        t_forearm = _true_twist_deg(axis_forearm_bind, axis_forearm_now,
                                    up_forearm_bind, up_forearm_now)
        return t_upper, t_forearm

    ik_ctrl = f'{wrist}_IK_ctrl'
    assert cmds.objExists(ik_ctrl)

    # ── PURE BEND: position-only IK ctrl move, no rotate. ──
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(0.0, -1.0, 0.5))
    t_upper_bend, t_forearm_bend = _measure()
    assert abs(t_upper_bend) < 5.0, (
        f'upper roll twist should stay ~zero on a pure bend (anti-flip): '
        f'{t_upper_bend} deg')
    assert abs(t_forearm_bend) < 5.0, (
        f'forearm roll twist should stay ~zero on a pure bend (anti-flip, '
        f'pole-vector-zero trick): {t_forearm_bend} deg')

    # ── WRIST TWIST on top of the same bend. ──
    cmds.setAttr(f'{ik_ctrl}.rotateX', 45.0)
    t_upper_p45, t_forearm_p45 = _measure()
    cmds.setAttr(f'{ik_ctrl}.rotateX', -45.0)
    t_upper_m45, t_forearm_m45 = _measure()

    # Forearm roll DOES twist: clear, monotonic response to wrist rotate.
    assert t_forearm_m45 < t_forearm_bend < t_forearm_p45, (
        f'forearm roll twist did not respond monotonically to wrist '
        f'rotate: -45={t_forearm_m45} bend={t_forearm_bend} '
        f'+45={t_forearm_p45}')
    assert abs(t_forearm_p45 - t_forearm_bend) > 15.0, (
        f'forearm roll twist response too small at +45 wrist twist: '
        f'delta={t_forearm_p45 - t_forearm_bend}')
    assert abs(t_forearm_m45 - t_forearm_bend) > 15.0, (
        f'forearm roll twist response too small at -45 wrist twist: '
        f'delta={t_forearm_m45 - t_forearm_bend}')

    # Upper roll stays filtered: near-zero throughout, even with the
    # wrist twisted (the roll_aim locator's pointConstraint anti-candy-
    # wrap design).
    assert abs(t_upper_p45) < 5.0 and abs(t_upper_m45) < 5.0, (
        f'upper roll twist should stay filtered (~zero) regardless of '
        f'wrist twist: +45={t_upper_p45} -45={t_upper_m45}')

    # ── GENUINE SHOULDER-TWIST stimulus (the fix for the vacuous-test
    # finding above): switch to FK and dial shoulder_FK_ctrl.rotateX — a
    # pure axial rotation about the shoulder->elbow bone, which DOES reach
    # the upper roll's anchor joint (shoulder) directly, unlike the wrist
    # ctrl stimulus above. This is the only stimulus that can actually
    # prove the upper roll's locator wiring filters twist. ──
    switch_ctrl = f'{wrist}_IKFK_ctrl'
    shoulder_fk_ctrl = f'{shoulder}_FK_ctrl'
    assert cmds.objExists(switch_ctrl), 'switch ctrl missing'
    assert cmds.objExists(shoulder_fk_ctrl), 'shoulder FK ctrl missing'
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 0.0)   # FK mode

    t_upper_fk0, _ = _measure()
    cmds.setAttr(f'{shoulder_fk_ctrl}.rotateX', 45.0)
    t_upper_fk_p45, _ = _measure()
    cmds.setAttr(f'{shoulder_fk_ctrl}.rotateX', -45.0)
    t_upper_fk_m45, _ = _measure()
    cmds.setAttr(f'{shoulder_fk_ctrl}.rotateX', 0.0)

    assert abs(t_upper_fk_p45 - t_upper_fk0) < 5.0, (
        f'upper roll twist should stay filtered (~zero) under a GENUINE '
        f'shoulder axial-twist stimulus (FK rotateX=+45): '
        f'baseline={t_upper_fk0} +45={t_upper_fk_p45} '
        f'delta={t_upper_fk_p45 - t_upper_fk0}')
    assert abs(t_upper_fk_m45 - t_upper_fk0) < 5.0, (
        f'upper roll twist should stay filtered (~zero) under a GENUINE '
        f'shoulder axial-twist stimulus (FK rotateX=-45): '
        f'baseline={t_upper_fk0} -45={t_upper_fk_m45} '
        f'delta={t_upper_fk_m45 - t_upper_fk0}')

    fs_app.unbuild_modules()


def test_ikarm_roll_driven_twist_no_flip():
    """Clean twist distribution / no-flip gate (PLAN Task 3.2 Step 1):
    sweeping wrist rotateX across +/-90 deg produces a monotonic, smoothly
    varying forearm-ribbon-tip roll — no ~180 deg jump between adjacent
    samples (the aim-based approximation's known failure mode at extreme/
    pole-aligned poses; SPEC §7 documents it as accepted beyond ~+/-90,
    not tested here as a pass condition)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikrollsweep')

    nodes.create_registry('ikrollsweep_bp')
    _add_world_component('ikrollsweep_world', root)
    nodes.create_component_node(
        component_id='ikrollsweep_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    forearm_chain = rc.ribbon_segment_chain_name(elbow, wrist)
    surf = f'{forearm_chain}_ribbon_surf'
    assert cmds.objExists(surf)
    row_count = len(sorted(cmds.ls(f'{forearm_chain}_ride_*', type='joint')))
    assert row_count >= 4

    def _cross_vec(row):
        a = cmds.pointPosition(f'{surf}.cv[0][{row}]', world=True)
        b = cmds.pointPosition(f'{surf}.cv[1][{row}]', world=True)
        return [b[i] - a[i] for i in range(3)]

    def _axis():
        e = cmds.xform(elbow, q=True, ws=True, t=True)
        w = cmds.xform(wrist, q=True, ws=True, t=True)
        return [w[i] - e[i] for i in range(3)]

    ik_ctrl = f'{wrist}_IK_ctrl'
    assert cmds.objExists(ik_ctrl)
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(0.0, -1.0, 0.5))

    v_ref = _cross_vec(0)
    readings = []
    for angle in range(-90, 91, 15):
        cmds.setAttr(f'{ik_ctrl}.rotateX', float(angle))
        readings.append(_signed_roll_deg(v_ref, _cross_vec(row_count - 1), _axis()))

    # No ~180 deg pop between adjacent samples (15 deg steps).
    for i in range(1, len(readings)):
        delta = abs(readings[i] - readings[i - 1])
        assert delta < 90.0, (
            f'roll flip detected between wrist rotateX samples at index '
            f'{i}: {readings[i - 1]} -> {readings[i]} (delta={delta}); '
            f'full sweep={readings}')

    # Broadly monotonic across the whole sweep: no individual step
    # meaningfully reverses the overall trend direction.
    overall = readings[-1] - readings[0]
    assert abs(overall) > 20.0, (
        f'sweep did not produce a meaningful overall roll change: '
        f'{readings}')
    sign = 1.0 if overall > 0 else -1.0
    reversed_steps = [
        readings[i] - readings[i - 1] for i in range(1, len(readings))
        if (readings[i] - readings[i - 1]) * sign < -5.0
    ]
    assert not reversed_steps, (
        f'roll readings not broadly monotonic across the sweep: '
        f'{readings} (reversed steps={reversed_steps})')

    fs_app.unbuild_modules()


def test_ikarm_roll_forearm_locator_mechanism_is_load_bearing():
    """Mutation-style gate (fix for a test-quality finding on the two
    dedicated forearm 'flip-prevention' tests above): both only sweep the
    +/-90 deg single-axis range, which is exactly the range where a
    disconnected/broken forearm up-vector mechanism would ALSO look
    monotonic and non-flipping — so neither test can actually tell a
    working mechanism from a broken one. Rather than chase a pole-aligned
    extreme pose (fragile, hard to reproduce deterministically), this
    directly SEVERS the mechanism under test — the forearm roll_aim
    locator's rigid, live-tracking parent relationship to the wrist BIND
    joint (_limb_common.build_roll_joint's driver_parent branch; see that
    function's docstring for why THIS is what makes twist "pass through")
    — by reparenting the locator onto a static, non-tracking group
    mid-scene, and asserts the
    SAME wrist-twist stimulus that produces a large, genuine response on
    the intact rig produces ~NO response once the locator is frozen. If a
    future regression breaks/bypasses the driver_parent parenting (the
    thing that actually makes twist pass through), the 'intact' reading
    would collapse toward the 'severed' reading and this test would fail
    — unlike the +/-90 sweep tests, which can't distinguish the two."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikrollsevered')

    nodes.create_registry('ikrollsevered_bp')
    _add_world_component('ikrollsevered_world', root)
    nodes.create_component_node(
        component_id='ikrollsevered_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    forearm_roll_jnt = f'{hc.short_name(elbow)}_{hc.short_name(wrist)}_roll_jnt'
    forearm_loc = f'{hc.short_name(elbow)}_{hc.short_name(wrist)}_roll_aim_loc'
    assert cmds.objExists(forearm_roll_jnt) and cmds.objExists(forearm_loc)

    up_local_lower = rc.ROLL_AXES_LOWER[1]

    def _axis_bind_now():
        e = cmds.xform(elbow, q=True, ws=True, t=True)
        w = cmds.xform(wrist, q=True, ws=True, t=True)
        return _v_sub(w, e)

    ik_ctrl = f'{wrist}_IK_ctrl'
    assert cmds.objExists(ik_ctrl)
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(0.0, -1.0, 0.5))  # pure bend

    axis_bind = _axis_bind_now()
    up_bind = _world_dir(forearm_roll_jnt, up_local_lower)

    def _measure():
        axis_now = _axis_bind_now()
        up_now = _world_dir(forearm_roll_jnt, up_local_lower)
        return _true_twist_deg(axis_bind, axis_now, up_bind, up_now)

    # ── INTACT: baseline, then a genuine wrist twist. ──
    intact_baseline = _measure()
    cmds.setAttr(f'{ik_ctrl}.rotateX', 45.0)
    intact_twisted = _measure()
    intact_delta = intact_twisted - intact_baseline
    cmds.setAttr(f'{ik_ctrl}.rotateX', 0.0)
    assert abs(intact_delta) > 15.0, (
        f'setup: expected the intact rig to show a genuine wrist-twist '
        f'response before severing the mechanism: delta={intact_delta}')

    # ── SEVER: reparent the locator off the wrist BIND joint onto a
    # static, non-tracking group (preserving its CURRENT world transform
    # — a plain cmds.parent keeps world position/orientation exactly, it
    # just stops it moving with wrist from here on). This is the specific
    # thing build_roll_joint's driver_parent branch does that makes
    # twist pass through; severing it is the mutation. ──
    cmds.parent(forearm_loc, nodes.ensure_nulls_grp())

    severed_baseline = _measure()
    cmds.setAttr(f'{ik_ctrl}.rotateX', 45.0)
    severed_twisted = _measure()
    severed_delta = severed_twisted - severed_baseline
    cmds.setAttr(f'{ik_ctrl}.rotateX', 0.0)

    assert abs(severed_delta) < 5.0, (
        f'forearm roll should show ~NO twist response once the locator '
        f'is severed from the live wrist rotation — a nonzero response '
        f'here means the wrist-twist signal is reaching roll_jnt through '
        f'some OTHER path than the locator parenting under test: '
        f'severed_delta={severed_delta}')
    assert abs(intact_delta) > 3.0 * abs(severed_delta) + 10.0, (
        f'severing the locator did not meaningfully reduce the twist '
        f'response — the mechanism under test may not be load-bearing: '
        f'intact_delta={intact_delta} severed_delta={severed_delta}')

    fs_app.unbuild_modules()


def test_ikarm_ribbon_mid_ctrls_read_roll_joint_not_raw_bind_joint():
    """Structural gate (fix for a ribbon-feed-integration finding): every
    mid ctrl's own aimConstraint up-vector reference must resolve to the
    segment's roll joint, not the raw bind joint — otherwise a roll
    joint's filtered/pass-through twist reaches only the single hard-
    pinned boundary control joint, and the falloff-skinned INTERIOR rows
    a mid ctrl actually drives stay on the pre-P3 'crude' raw-bind-joint
    signal regardless of how correct build_roll_joint's own math is
    (SPEC 3.2 / PLAN Task 3.2: twist 'distributes ... along' the
    segment, not just at one row). This is a direct, deterministic
    wiring check — the aimConstraint's worldUpMatrix input plug's source
    — rather than an indirect numeric threshold: a same-pose numeric
    comparison can't reliably tell 'reads the roll joint' from 'reads
    the raw bind joint' apart, because in many simple poses (e.g. a
    translate-only bend + a clean single-axis wrist rotate) the raw
    joint's Euler reading and the roll joint's extracted twist happen to
    coincide closely."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikridwire')

    nodes.create_registry('ikridwire_bp')
    _add_world_component('ikridwire_world', root)
    mid_count = 2
    nodes.create_component_node(
        component_id='ikridwire_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': mid_count}, persisted={},
    )
    fs_app.build_modules()

    upper_chain = rc.ribbon_segment_chain_name(shoulder, elbow)
    forearm_chain = rc.ribbon_segment_chain_name(elbow, wrist)
    upper_roll_jnt = f'{hc.short_name(shoulder)}_{hc.short_name(elbow)}_roll_jnt'
    forearm_roll_jnt = f'{hc.short_name(elbow)}_{hc.short_name(wrist)}_roll_jnt'
    assert cmds.objExists(upper_roll_jnt) and cmds.objExists(forearm_roll_jnt)

    def _mid_up_ref(chain, m):
        mbuf = f'{chain}_mid_{m:02d}_ctrl_offset'
        assert cmds.objExists(mbuf), f'{mbuf} missing'
        acs = cmds.listRelatives(mbuf, type='aimConstraint') or []
        assert len(acs) == 1, (
            f'{mbuf}: expected exactly one aimConstraint, got {acs}')
        src = cmds.listConnections(f'{acs[0]}.worldUpMatrix',
                                   source=True, destination=False) or []
        assert len(src) == 1, (
            f'{acs[0]}.worldUpMatrix: expected exactly one source, got {src}')
        return src[0]

    for m in range(mid_count):
        got = _mid_up_ref(upper_chain, m)
        assert got == upper_roll_jnt, (
            f'upper mid_{m:02d} ctrl aims its up-vector at {got!r}, '
            f'expected the roll joint {upper_roll_jnt!r} — it is reading '
            f'a raw bind joint instead of the roll joint\'s clean twist '
            f'signal')
        got = _mid_up_ref(forearm_chain, m)
        assert got == forearm_roll_jnt, (
            f'forearm mid_{m:02d} ctrl aims its up-vector at {got!r}, '
            f'expected the roll joint {forearm_roll_jnt!r} — it is '
            f'reading a raw bind joint instead of the roll joint\'s '
            f'clean twist signal')

    fs_app.unbuild_modules()


def test_ikarm_roll_twist_works_ik_and_fk():
    """IK AND FK gate: the SAME nominal wrist twist produces closely-
    matching forearm-ribbon-tip roll whether driven via the IK ctrl (IK
    mode) or the FK ctrl (FK mode) — proves the roll joint reads the BIND
    chain (downstream of SimpleIK's IK/FK blend), not an IK-only or
    FK-only sub-chain (ROLL-METHOD §2 lower-bone note: "twist available
    in both IK and FK")."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikrollikfk')

    nodes.create_registry('ikrollikfk_bp')
    _add_world_component('ikrollikfk_world', root)
    nodes.create_component_node(
        component_id='ikrollikfk_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    forearm_chain = rc.ribbon_segment_chain_name(elbow, wrist)
    surf = f'{forearm_chain}_ribbon_surf'
    assert cmds.objExists(surf)
    row_count = len(sorted(cmds.ls(f'{forearm_chain}_ride_*', type='joint')))
    assert row_count >= 4

    def _cross_vec(row):
        a = cmds.pointPosition(f'{surf}.cv[0][{row}]', world=True)
        b = cmds.pointPosition(f'{surf}.cv[1][{row}]', world=True)
        return [b[i] - a[i] for i in range(3)]

    def _axis():
        e = cmds.xform(elbow, q=True, ws=True, t=True)
        w = cmds.xform(wrist, q=True, ws=True, t=True)
        return [w[i] - e[i] for i in range(3)]

    v_ref = _cross_vec(0)

    ik_ctrl = f'{wrist}_IK_ctrl'
    switch_ctrl = f'{wrist}_IKFK_ctrl'
    fk_wrist_ctrl = f'{wrist}_FK_ctrl'
    assert (cmds.objExists(ik_ctrl) and cmds.objExists(switch_ctrl)
           and cmds.objExists(fk_wrist_ctrl))

    # IK mode (default): baseline, then twist via the IK ctrl.
    assert abs(cmds.getAttr(f'{switch_ctrl}.ik_fk_blend') - 1.0) < 1e-6
    baseline_ik = _signed_roll_deg(v_ref, _cross_vec(row_count - 1), _axis())
    cmds.setAttr(f'{ik_ctrl}.rotateX', 45.0)
    roll_ik = _signed_roll_deg(v_ref, _cross_vec(row_count - 1), _axis())
    delta_ik = roll_ik - baseline_ik
    assert abs(delta_ik) > 15.0, (
        f'IK-mode wrist twist did not move the forearm roll: '
        f'delta={delta_ik}')
    cmds.setAttr(f'{ik_ctrl}.rotateX', 0.0)

    # FK mode: re-baseline right after the switch (mode-switch itself may
    # introduce a small discontinuity), then twist via the FK ctrl.
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 0.0)
    baseline_fk = _signed_roll_deg(v_ref, _cross_vec(row_count - 1), _axis())
    cmds.setAttr(f'{fk_wrist_ctrl}.rotateX', 45.0)
    roll_fk = _signed_roll_deg(v_ref, _cross_vec(row_count - 1), _axis())
    delta_fk = roll_fk - baseline_fk
    assert abs(delta_fk) > 15.0, (
        f'FK-mode wrist twist did not move the forearm roll: '
        f'delta={delta_fk}')

    # Both modes' responses agree in direction and rough magnitude for the
    # SAME nominal 45 deg wrist twist.
    assert (delta_ik > 0) == (delta_fk > 0), (
        f'IK-mode and FK-mode twist responses disagree in direction: '
        f'IK delta={delta_ik} FK delta={delta_fk}')
    assert abs(delta_ik - delta_fk) < 0.5 * max(abs(delta_ik), abs(delta_fk)), (
        f'IK-mode and FK-mode twist responses differ too much in '
        f'magnitude: IK delta={delta_ik} FK delta={delta_fk}')

    fs_app.unbuild_modules()


def test_ikarm_roll_mirror_offset_sign_flip():
    """MIRROR gate (fix for a mirror-unbuild-tracking finding): builds the
    'rt' side as an ACTUAL mirrorJoint reflection of a real 'lf' chain
    (mirrorYZ, mirrorBehavior — matching branch_ops.py's
    `cmds.mirrorJoint(root, mirrorYZ=True, mirrorBehavior=True)`, the tool
    that produces every real right-side rig), not a second build at the
    SAME hardcoded coordinates. A prior version of this test called
    `_make_arm_chain_offset` twice with identical coordinates for both
    'md' and 'rt', so it could only prove the side_sign LITERAL fires —
    not that the sign convention is correct against real mirrored
    geometry (both builds were byte-identical, non-reflected chains).

    Because build_roll_joint's `side_vec` = cross(bone_dir, up_world) does
    NOT itself mirror as a simple per-component negation under a real
    X-axis skeletal reflection (up_world is `_chain_common.resolve_axes`'s
    averaged LIVE local +Y of the two bone joints, which mirrorBehavior
    correctly reflects, but the cross product's own transform under that
    reflection isn't 'negate one axis' in general) — the correct,
    geometry-agnostic invariant isn't 'offsets sum to ~0' (that only held
    for the old test's non-reflected, identical-coordinate degenerate
    case). Instead: recompute EACH side's own side_vec fresh from its
    OWN real (possibly-mirrored) geometry, and assert the roll_aim
    locator's offset (seeded at `follower_pos` — see build_roll_joint's
    Cleanup docstring note) projects onto that side's own side_vec with
    OPPOSITE SIGN between 'lf' and 'rt' (same magnitude) — this is
    exactly what build_roll_joint's `side_sign` is supposed to produce,
    and it's falsifiable: removing the side_sign multiply would make BOTH
    sides' locator offsets project onto their own side_vec with the SAME
    (positive) sign, since side_vec is used as a positive unit direction
    otherwise."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    from maya_tools.rigging.fabricator.modules import _chain_common as cc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikrollmirlf')

    # Real mirror: mirrorYZ (negate world X) + mirrorBehavior (correct FK
    # rotation-axis reflection, not just position) — the exact call
    # branch_ops.py's real right-side-rig tool uses. searchReplace mirrors
    # this test's own 'lf'->'rt' naming convention so the joint-name
    # lookups below still resolve.
    created = cmds.mirrorJoint(root, mirrorYZ=True, mirrorBehavior=True,
                               searchReplace=('lf', 'rt'))
    rt_root = created[0]
    rt_shoulder, rt_elbow, rt_wrist = (
        rt_root.replace('_root', '_shoulder'),
        rt_root.replace('_root', '_elbow'),
        rt_root.replace('_root', '_wrist'))
    for j in (rt_shoulder, rt_elbow, rt_wrist):
        assert cmds.objExists(j), f'mirrorJoint did not produce {j!r}'

    # Sanity: this really is a reflection (X negates, Y/Z match) — not a
    # coincidental identical-coordinate build.
    for lf_j, rt_j in ((shoulder, rt_shoulder), (elbow, rt_elbow),
                       (wrist, rt_wrist)):
        lp = cmds.xform(lf_j, q=True, ws=True, t=True)
        rp = cmds.xform(rt_j, q=True, ws=True, t=True)
        assert abs(rp[0] - (-lp[0])) < 1e-4, (
            f'{rt_j}: X should be the negation of {lf_j}\'s: {rp[0]} vs '
            f'{-lp[0]}')
        assert abs(rp[1] - lp[1]) < 1e-4 and abs(rp[2] - lp[2]) < 1e-4, (
            f'{rt_j}: Y/Z should match {lf_j}\'s under a YZ-plane mirror: '
            f'{rp} vs {lp}')

    nodes.create_registry('ikrollmir_bp')
    _add_world_component('ikrollmirlf_world', root)
    nodes.create_component_node(
        component_id='ikrollmirlf_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='lf',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    _add_world_component('ikrollmirrt_world', rt_root)
    nodes.create_component_node(
        component_id='ikrollmirrt_C0', component_type='RibbonIKArm',
        joints=[rt_shoulder, rt_elbow, rt_wrist], parent_plug='', side='rt',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    def _side_vec(bone_start, bone_end):
        a = cmds.xform(bone_start, q=True, ws=True, t=True)
        t = cmds.xform(bone_end, q=True, ws=True, t=True)
        bone_vec = _v_sub(t, a)
        bone_dir = _v_normalize(bone_vec)
        _, _, up_world = cc.resolve_axes([bone_start, bone_end])
        sv = _v_cross(bone_dir, up_world)
        sl = sum(c * c for c in sv) ** 0.5
        return [c / sl for c in sv] if sl > 1e-6 else [0.0, 0.0, 1.0]

    dots = {}
    for side, sh, el, wr in (('lf', shoulder, elbow, wrist),
                             ('rt', rt_shoulder, rt_elbow, rt_wrist)):
        upper_loc = f'{hc.short_name(sh)}_{hc.short_name(el)}_roll_aim_loc'
        forearm_loc = f'{hc.short_name(el)}_{hc.short_name(wr)}_roll_aim_loc'
        assert cmds.objExists(upper_loc) and cmds.objExists(forearm_loc), (
            f'{side}: roll_aim locators missing')

        sh_pos = cmds.xform(sh, q=True, ws=True, t=True)
        wr_pos = cmds.xform(wr, q=True, ws=True, t=True)
        up_off = _v_sub(cmds.xform(upper_loc, q=True, ws=True, t=True), sh_pos)
        fa_off = _v_sub(cmds.xform(forearm_loc, q=True, ws=True, t=True), wr_pos)

        sv_upper = _side_vec(sh, el)
        sv_forearm = _side_vec(el, wr)
        dots[side] = {
            'upper': _v_dot(up_off, sv_upper),
            'forearm': _v_dot(fa_off, sv_forearm),
        }

    for seg in ('upper', 'forearm'):
        d_lf = dots['lf'][seg]
        d_rt = dots['rt'][seg]
        assert abs(d_lf) > 1e-3 and abs(d_rt) > 1e-3, (
            f'{seg}: degenerate side_vec projection: lf={d_lf} rt={d_rt}')
        assert d_lf * d_rt < 0.0, (
            f'{seg}: locator offset should project onto its OWN side\'s '
            f'side_vec with OPPOSITE sign between lf and rt (side_sign '
            f'not flipping correctly against real mirrored geometry): '
            f'lf={d_lf} rt={d_rt}')
        assert abs(abs(d_lf) - abs(d_rt)) < 0.05 * abs(d_lf), (
            f'{seg}: lf/rt projections should match in MAGNITUDE (same '
            f'offset fraction, opposite side): lf={d_lf} rt={d_rt}')

    fs_app.unbuild_modules()


def test_ikarm_roll_unbuild_leaves_zero_orphans():
    """UNBUILD gate: the roll node-set (roll joint, locator, aimConstraint
    for BOTH segments — all living OUTSIDE rig_grp) is fully deleted on
    unbuild; DG type counts return to the pre-build baseline.

    Also a Cleanup (2026-07-08) regression gate: build_roll_joint's
    follower/follow_tip/ikHandle graph was proven inert and stripped
    entirely (docs/superpowers/specs/2026-07-08-ikarm-ribbon-module-roll-
    method.md §7) — asserts those nodes never exist at ANY point, not just
    that they're gone after unbuild, so a regression that reintroduces the
    graph fails loudly here even if its own cleanup would otherwise be
    clean."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikrollorphan')

    nodes.create_registry('ikrollorphan_bp')
    _add_world_component('ikrollorphan_world', root)
    nodes.create_component_node(
        component_id='ikrollorphan_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )

    dg_before = _dg_type_counts()
    fs_app.build_modules()
    dg_during = _dg_type_counts()
    assert dg_during.get('aimConstraint', 0) >= 2, (
        f'expected 2 aimConstraints (one per roll joint), got '
        f'{dg_during.get("aimConstraint")}')

    upper_prefix = f'{hc.short_name(shoulder)}_{hc.short_name(elbow)}_roll'
    forearm_prefix = f'{hc.short_name(elbow)}_{hc.short_name(wrist)}_roll'
    roll_named_nodes = []
    removed_graph_nodes = []
    for prefix in (upper_prefix, forearm_prefix):
        roll_named_nodes += [f'{prefix}_jnt', f'{prefix}_aim_loc']
        removed_graph_nodes += [
            f'{prefix}_follower', f'{prefix}_follow_tip', f'{prefix}_ikHandle',
        ]
    assert all(cmds.objExists(n) for n in roll_named_nodes), (
        f'setup: expected roll nodes to exist before unbuild: '
        f'{[n for n in roll_named_nodes if not cmds.objExists(n)]}')

    # Cleanup (2026-07-08) regression gate: the stripped follower/
    # follow_tip/ikHandle graph must never exist, not even mid-build.
    leaked_graph = [n for n in removed_graph_nodes if cmds.objExists(n)]
    assert not leaked_graph, (
        f'build_roll_joint should no longer build the follower/follow_tip/'
        f'ikHandle graph (proven inert, stripped 2026-07-08) but these '
        f'nodes exist: {leaked_graph}')

    fs_app.unbuild_modules()

    assert not cmds.objExists('rig_grp'), 'rig_grp survived unbuild'
    leaked = [n for n in roll_named_nodes if cmds.objExists(n)]
    assert not leaked, f'roll nodes survived unbuild: {leaked}'

    dg_after = _dg_type_counts()
    assert dg_after == dg_before, (
        f'DG node counts did not return to pre-build baseline: '
        f'before={dg_before} after={dg_after}')

    # Bind joints survive with ONLY their legitimate skeleton children
    # (shoulder->elbow->wrist) — the roll rig's own joint child (roll_jnt,
    # parented directly under a bind joint, outside rig_grp) must be gone;
    # already covered above by the roll_named_nodes existence check,
    # cross-checked here by NAME so a regression that renames but doesn't
    # delete it still fails loudly.
    for j in (shoulder, elbow, wrist):
        assert cmds.objExists(j), f'bind joint {j!r} should survive unbuild'
        children = cmds.listRelatives(j, children=True, type='joint') or []
        leaked_children = [c for c in children if c not in (shoulder, elbow, wrist)]
        assert not leaked_children, (
            f'bind joint {j!r} still has non-skeleton child joints: '
            f'{leaked_children}')


def test_ikarm_roll_internals_hidden_from_viewport():
    """HIDDEN gate (Cleanup 2026-07-08): the roll rig's remaining internals
    — the roll joint and the roll_aim locator, for BOTH segments — must not
    be visible in the animator's viewport. Neither node lives under a
    hidden group (roll rides a live bind joint; the locator rides
    driver_parent or fab_nulls_grp depending on branch), so each needs its
    own explicit visibility=0 — nodes.hide_internal alone does NOT set
    this (it only clears keyable/channel-box state), which is exactly why
    these nodes used to leak into the viewport before this fix. Checks the
    resolved (query) visibility so it also fails if a regression only sets
    the attribute without it actually reading back False."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikrollhidden')

    nodes.create_registry('ikrollhidden_bp')
    _add_world_component('ikrollhidden_world', root)
    nodes.create_component_node(
        component_id='ikrollhidden_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    upper_prefix = f'{hc.short_name(shoulder)}_{hc.short_name(elbow)}_roll'
    forearm_prefix = f'{hc.short_name(elbow)}_{hc.short_name(wrist)}_roll'
    internals = []
    for prefix in (upper_prefix, forearm_prefix):
        internals += [f'{prefix}_jnt', f'{prefix}_aim_loc']

    assert all(cmds.objExists(n) for n in internals), (
        f'setup: expected roll internals to exist: '
        f'{[n for n in internals if not cmds.objExists(n)]}')

    leaked = [n for n in internals if cmds.getAttr(f'{n}.visibility')]
    assert not leaked, (
        f'roll-rig internals still visible in the viewport (visibility '
        f'attribute reads True): {leaked}')

    fs_app.unbuild_modules()


# ─── Phase 4: per-finger FK ctrls ─────────────────────────────────────────
# Reuses _make_arm_chain_offset for the shoulder/elbow/wrist triple, then
# hangs synthetic finger chains off the wrist. hc.discover_fingers is fed a
# REAL blueprint.schema.Blueprint built from the live scene's joint
# hierarchy (mirroring what fs_app._snapshot_blueprint_from_scene() would
# produce at live add time) so these tests exercise the SAME discovery path
# Task 4.2's create-time hook uses, not a hand-typed 'fingers' option.

def _make_hand_chain(prefix, finger_defs, offset=(41.0, 15.0, -7.0)):
    """Arm chain (root->shoulder->elbow->wrist) + one straight joint chain
    per finger_defs entry, parented under wrist.

    finger_defs: list of lists of short joint-name SUFFIXES (chain order,
    root first) — e.g. [['thumb_01', 'thumb_02', 'thumb_03'], ...].
    Returns (root, shoulder, elbow, wrist, fingers_joints) where
    fingers_joints is a list (parallel to finger_defs) of lists of the
    full joint names actually created.
    """
    import maya.cmds as cmds
    root, shoulder, elbow, wrist = _make_arm_chain_offset(prefix, offset)
    wrist_pos = cmds.xform(wrist, q=True, ws=True, t=True)
    fingers_joints = []
    for fi, chain_names in enumerate(finger_defs):
        parent = wrist
        chain = []
        for ci, suffix in enumerate(chain_names):
            name = f'{prefix}_{suffix}'
            pos = (wrist_pos[0] + fi * 0.5,
                   wrist_pos[1] - (ci + 1) * 0.3,
                   wrist_pos[2] + fi * 0.1)
            cmds.select(clear=True)
            j = cmds.joint(name=name, position=pos)
            cmds.parent(j, parent)
            cmds.xform(j, ws=True, t=pos)
            parent = j
            chain.append(j)
        fingers_joints.append(chain)
    cmds.select(clear=True)
    return root, shoulder, elbow, wrist, fingers_joints


def _blueprint_snapshot_from_joints(all_joints):
    """A minimal blueprint.schema.Blueprint built directly from the live
    scene's joint DAG — same duck-type _limb_common.discover_fingers
    documents, same shape fs_app._snapshot_blueprint_from_scene() would
    produce for these joints at live add time."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator.blueprint.schema import Blueprint, JointSpec
    joints = []
    for j in all_joints:
        parents = cmds.listRelatives(j, parent=True, type='joint') or []
        joints.append(JointSpec(name=j, parent=parents[0] if parents else None))
    return Blueprint(name='test', skeleton_joints=joints)


_UE5_FINGER_DEFS = (
    ['thumb_01', 'thumb_02', 'thumb_03'],
    ['index_metacarpal', 'index_01', 'index_02', 'index_03'],
    ['middle_metacarpal', 'middle_01', 'middle_02', 'middle_03'],
    ['ring_metacarpal', 'ring_01', 'ring_02', 'ring_03'],
    ['pinky_metacarpal', 'pinky_01', 'pinky_02', 'pinky_03'],
)

_CARTOON_12FINGER_DEFS = tuple(
    [f'f{i:02d}_01', f'f{i:02d}_02', f'f{i:02d}_03'] for i in range(12)
)

_4FINGER_NO_METACARPAL_DEFS = (
    ['f1_01', 'f1_02', 'f1_03', 'f1_04'],
    ['f2_01', 'f2_02', 'f2_03', 'f2_04'],
    ['f3_01', 'f3_02', 'f3_03', 'f3_04'],
    ['f4_01', 'f4_02', 'f4_03', 'f4_04'],
)


def _build_ikarm_with_fingers(prefix, finger_defs, component_id=None,
                              extra_options=None):
    """Shared scaffold: build a hand skeleton, create + build an
    RibbonIKArm component, then set its LIMB NODE's finger_roots[]/
    curl_excluded[] (Task 2.3: membership lives on the limb, not an
    option) to exactly discover_fingers()'s own read of the live DAG —
    in ITS order, deterministically, regardless of whatever order Maya's
    own listRelatives happens to return wrist's children in.

    nodes._maybe_create_implicit_limb ALSO ran this same discovery
    (harmlessly redundant — every ADD helper below is idempotent against
    an already-connected target) during create_component_node, since the
    finger joints already exist live in the scene by that point and
    RibbonIKArm.creates_implicit_limb is True; this just makes the
    authoring ORDER explicit and test-deterministic on top of that.

    Returns (root, shoulder, elbow, wrist, fingers_joints, fingers_option,
    component_id) — fingers_option is unchanged in shape (discover_
    fingers()'s own return value) and kept in the tuple purely as
    reference data every caller in this file already destructures to
    check chain/curl_excluded shape; it no longer drives the build."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist, fingers_joints = _make_hand_chain(prefix, finger_defs)
    all_joints = [root, shoulder, elbow, wrist] + [j for c in fingers_joints for j in c]
    bp = _blueprint_snapshot_from_joints(all_joints)
    fingers_option = hc.discover_fingers(wrist, bp)
    assert len(fingers_option) == len(finger_defs), (
        f'discover_fingers found {len(fingers_option)} fingers, '
        f'expected {len(finger_defs)}')

    nodes.create_registry(f'{prefix}_bp')
    _add_world_component(f'{prefix}_world', root)
    cid = component_id or f'{prefix}_C0'
    options = {'mid_ctrl_count': 1}
    if extra_options:
        options.update(extra_options)
    nodes.create_component_node(
        component_id=cid, component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options=options, persisted={},
    )

    limb = ln.find_limb_for_joint(shoulder)
    assert limb is not None, (
        f'{prefix}: implicit limb was not auto-created for the '
        f'standalone RibbonIKArm add — fixture/production regression')
    for finger in fingers_option:
        root_j = finger['root_joint']
        ln.add_finger_root(limb, root_j)
        for excl in finger.get('curl_excluded') or []:
            ln.add_curl_excluded(limb, excl)

    fs_app.build_modules()

    return root, shoulder, elbow, wrist, fingers_joints, fingers_option, cid


def _assert_finger_ctrls_built_correctly(fingers_joints, wrist):
    """Every joint in every finger got a tagged, chain-parented FK ctrl —
    the shared assertion body for all three skeleton-size tests below.

    fab_joint_index (P4 review finding #6 fix): finger ctrls now get a
    RUNNING counter, unique across every finger on the component and
    starting AFTER RibbonIKArm's own 3 primary joints[] slots (0/1/2 =
    shoulder/elbow/wrist) — never resetting to 0 per finger the way the
    old per-finger-local `enumerate` did. This test asserts BOTH the
    exact expected value (matching ribbon_ik_arm.py's own running-counter
    scheme) AND global uniqueness/no-collision-with-primary-slots
    directly, so a regression back to a per-finger-local (or any other
    colliding) scheme fails here instead of only surfacing downstream in
    pose_library.address's disambiguation."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    wrist_switch_ctrl = f'{wrist}_IKFK_ctrl'
    assert cmds.objExists(wrist_switch_ctrl), (
        f'expected wrist switch ctrl {wrist_switch_ctrl!r} to exist')

    seen_indices = set()
    next_index = 3  # RibbonIKArm's joints[] is always [shoulder, elbow, wrist]
    for chain in fingers_joints:
        prev_ctrl = wrist_switch_ctrl
        for j in chain:
            short = hc.short_name(j)
            offset_ctrl = f'{short}_ctrl_offset'
            ctrl = f'{short}_ctrl'
            assert cmds.objExists(offset_ctrl), (
                f'{offset_ctrl!r} missing for finger joint {j!r}')
            assert cmds.objExists(ctrl), (
                f'{ctrl!r} missing for finger joint {j!r}')

            role = cmds.getAttr(f'{ctrl}.fab_role')
            assert role == 'finger_fk_ctrl', (
                f'{ctrl!r} tagged {role!r}, expected "finger_fk_ctrl"')
            idx = cmds.getAttr(f'{ctrl}.fab_joint_index')
            assert idx == next_index, (
                f'{ctrl!r} joint_index {idx} != expected running index '
                f'{next_index} (component-wide-unique scheme)')
            assert idx not in seen_indices, (
                f'{ctrl!r} joint_index {idx} collides with another '
                f'finger ctrl already seen on this component — '
                f'pose_library.address could not tell them apart')
            assert idx >= 3, (
                f'{ctrl!r} joint_index {idx} collides with the arm\'s '
                f'own primary joints[] slots (0=shoulder, 1=elbow, '
                f'2=wrist)')
            seen_indices.add(idx)
            next_index += 1

            # Chain-parented: offset_ctrl's parent is the PREVIOUS joint's
            # ctrl (or the wrist switch ctrl for the finger root).
            parent = (cmds.listRelatives(offset_ctrl, parent=True) or [None])[0]
            assert parent == prev_ctrl, (
                f'{offset_ctrl!r} parented under {parent!r}, expected '
                f'{prev_ctrl!r} (chain order)')
            prev_ctrl = ctrl


def test_ikarm_finger_fk_ctrls_ue5_5finger_with_metacarpals():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app

    _root, _shoulder, _elbow, wrist, fingers_joints, fingers_option, _cid = (
        _build_ikarm_with_fingers('ikf5', _UE5_FINGER_DEFS))

    # Metacarpal membership sanity — every 4-joint finger's root is
    # curl_excluded; the 3-joint thumb's is not (mirrors the offscreen
    # discover_fingers assertions, now through the live build path).
    by_root = {f['root_joint']: f for f in fingers_option}
    assert by_root['ikf5_thumb_01']['curl_excluded'] == []
    for finger in ('index', 'middle', 'ring', 'pinky'):
        mc = f'ikf5_{finger}_metacarpal'
        assert by_root[mc]['curl_excluded'] == [mc]

    _assert_finger_ctrls_built_correctly(fingers_joints, wrist)

    # Metacarpal joints STILL get a full FK ctrl (no curl wiring yet, but
    # the ctrl itself must exist — SPEC 3.4).
    for chain in fingers_joints:
        assert cmds.objExists(f'{chain[0]}_ctrl'), (
            f'metacarpal/root joint {chain[0]!r} missing its FK ctrl')

    fs_app.unbuild_modules()


def test_ikarm_finger_fk_ctrls_12finger_cartoon_no_metacarpal():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app

    _root, _shoulder, _elbow, wrist, fingers_joints, fingers_option, _cid = (
        _build_ikarm_with_fingers('ikf12', _CARTOON_12FINGER_DEFS))

    assert len(fingers_option) == 12
    assert all(f['curl_excluded'] == [] for f in fingers_option)

    _assert_finger_ctrls_built_correctly(fingers_joints, wrist)
    fs_app.unbuild_modules()


def test_ikarm_finger_fk_ctrls_4finger_no_metacarpal():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app

    _root, _shoulder, _elbow, wrist, fingers_joints, fingers_option, _cid = (
        _build_ikarm_with_fingers('ikf4', _4FINGER_NO_METACARPAL_DEFS))

    assert len(fingers_option) == 4
    for f in fingers_option:
        assert f['curl_excluded'] == [f['root_joint']], f

    _assert_finger_ctrls_built_correctly(fingers_joints, wrist)
    fs_app.unbuild_modules()


def test_ikarm_finger_ctrl_actually_drives_joint():
    """Not just node existence — pose a finger ctrl and measure the bind
    joint actually moving."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app

    _root, _shoulder, _elbow, wrist, fingers_joints, _fingers_option, _cid = (
        _build_ikarm_with_fingers('ikfdrive', _UE5_FINGER_DEFS))

    thumb_chain = fingers_joints[0]  # ['ikfdrive_thumb_01', ..._02, ..._03]
    mid_joint = thumb_chain[1]
    ctrl = f'{mid_joint}_ctrl'
    assert cmds.objExists(ctrl)

    rot_before = cmds.getAttr(f'{mid_joint}.rotate')[0]
    cmds.setAttr(f'{ctrl}.rotateZ', 35.0)
    rot_after = cmds.getAttr(f'{mid_joint}.rotate')[0]
    assert rot_before != rot_after, (
        f'rotating {ctrl!r} did not move bind joint {mid_joint!r}: '
        f'before={rot_before} after={rot_after}')

    # Downstream joint (child of the posed one) must follow too — proves
    # the chain-parenting is live, not just the single constrained joint.
    tip_joint = thumb_chain[2]
    tip_pos_before = cmds.xform(tip_joint, q=True, ws=True, t=True)
    cmds.setAttr(f'{ctrl}.rotateZ', 0.0)
    tip_pos_after_reset = cmds.xform(tip_joint, q=True, ws=True, t=True)
    cmds.setAttr(f'{ctrl}.rotateZ', 35.0)
    tip_pos_posed = cmds.xform(tip_joint, q=True, ws=True, t=True)
    d = sum((tip_pos_posed[i] - tip_pos_after_reset[i]) ** 2
            for i in range(3)) ** 0.5
    assert d > 0.05, (
        f'child joint {tip_joint!r} did not move when the parent finger '
        f'ctrl rotated: before={tip_pos_after_reset} after={tip_pos_posed}')

    fs_app.unbuild_modules()


def test_ikarm_finger_ctrl_cv_persists_across_unbuild_build():
    import maya.cmds as cmds
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
    from maya_tools.rigging.fabricator import fs_app

    _root, _shoulder, _elbow, _wrist, fingers_joints, _fingers_option, _cid = (
        _build_ikarm_with_fingers('ikfcv', _UE5_FINGER_DEFS))

    thumb_root_joint = fingers_joints[0][0]
    ctrl = f'{thumb_root_joint}_ctrl'
    assert cmds.objExists(ctrl)

    # Author a distinctive shape edit (uniform scale of the ctrl's CVs)
    # like a rigger would in Edit Mode.
    cmds.select(f'{ctrl}.cv[*]')
    cmds.scale(2.0, 2.0, 2.0, relative=True)
    cmds.select(clear=True)
    edited_shape = com.serialize_shape(ctrl)
    assert edited_shape.get('shapes')

    fs_app.unbuild_modules()
    fs_app.build_modules()

    assert cmds.objExists(ctrl), 'ctrl should exist again after rebuild'
    rebuilt_shape = com.serialize_shape(ctrl)
    assert rebuilt_shape.get('shapes') == edited_shape.get('shapes'), (
        'finger ctrl CV edit did not persist across unbuild -> build')

    fs_app.unbuild_modules()


def _finger_bind_joint_constraints(joints):
    """{joint: (parentConstraint_count, scaleConstraint_count)} for each
    joint in `joints` — mirrors the P1/P2/P3 shoulder/elbow/wrist orphan
    checks (lines ~422-428/544-548/1028-1032) applied to finger bind
    joints instead."""
    import maya.cmds as cmds
    counts = {}
    for j in joints:
        pc = cmds.listRelatives(j, type='parentConstraint') or []
        sc = cmds.listRelatives(j, type='scaleConstraint') or []
        counts[j] = (len(pc), len(sc))
    return counts


def test_ikarm_finger_bind_joint_constraint_sweep_on_unbuild():
    """P5 review finding (major): RibbonIKArmComponent.unbuild's finger
    bind-joint constraint sweep (ribbon_ik_arm.py: 'Only the constraints THIS
    leaves on each bind finger JOINT ... need an explicit sweep here')
    was completely unexercised by any test — unlike the equivalent
    shoulder/elbow/wrist sweep, checked in three separate P1-P3 orphan
    tests. End-to-end pipeline guard, across a realistic multi-finger
    build (both curl-included phalanges AND curl_excluded metacarpals):
      1. after build, every finger joint has EXACTLY one parentConstraint
         + one scaleConstraint (sanity — otherwise the zero-after-unbuild
         check below would be vacuous);
      2. after a full fs_app.unbuild_modules() pass, every finger joint
         has ZERO of either;
      3. after a SECOND build, every finger joint is back to EXACTLY one
         of each — not two (the double-drive symptom).
    NOTE: this end-to-end path alone is NOT sufficient to pin the
    regression on the explicit sweep specifically — see
    test_ikarm_finger_bind_joint_constraint_sweep_is_explicit_not_
    incidental below for why, and for the test that actually isolates
    it. Kept here anyway as a realistic full-pipeline regression guard
    (mirrors the P1-P3 shoulder/elbow/wrist precedent's own style,
    which checks the same full-pipeline end state).
    """
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app

    _root, _shoulder, _elbow, _wrist, fingers_joints, fingers_option, _cid = (
        _build_ikarm_with_fingers('ikfconstraint', _UE5_FINGER_DEFS))

    all_finger_joints = [j for chain in fingers_joints for j in chain]
    # Sanity: this fixture actually mixes curl-included phalanges and
    # curl_excluded metacarpals — otherwise this test wouldn't cover the
    # finding's "both curl-included and curl_excluded members" ask.
    excluded_joints = {j for f in fingers_option for j in f['curl_excluded']}
    assert excluded_joints, 'fixture has no curl_excluded members — test would not cover them'
    assert set(all_finger_joints) - excluded_joints, (
        'fixture has no curl-included members — test would not cover them')

    counts_after_build_1 = _finger_bind_joint_constraints(all_finger_joints)
    for j, (pc, sc) in counts_after_build_1.items():
        assert (pc, sc) == (1, 1), (
            f'{j!r}: expected exactly 1 parentConstraint + 1 '
            f'scaleConstraint after build, got parentConstraint={pc} '
            f'scaleConstraint={sc} — test would be vacuous otherwise')

    fs_app.unbuild_modules()

    counts_after_unbuild = _finger_bind_joint_constraints(all_finger_joints)
    leaked = {j: c for j, c in counts_after_unbuild.items() if c != (0, 0)}
    assert not leaked, (
        f'finger bind joint(s) still have constraints after unbuild: {leaked}')

    fs_app.build_modules()

    counts_after_build_2 = _finger_bind_joint_constraints(all_finger_joints)
    double_driven = {j: c for j, c in counts_after_build_2.items() if c != (1, 1)}
    assert not double_driven, (
        f'finger bind joint(s) have != 1 constraint of each type after a '
        f'SECOND build — a stray first-build constraint likely survived '
        f'unbuild and a second driver was added on top (double-drive): '
        f'{double_driven}')

    fs_app.unbuild_modules()


def test_ikarm_finger_bind_joint_constraint_sweep_is_explicit_not_incidental():
    """Strengthens test_ikarm_finger_bind_joint_constraint_sweep_on_
    unbuild above, which — verified empirically during this P5 fix pass
    — is a FALSE GREEN for the exact regression the review finding
    describes (silently removing ribbon_ik_arm.py's explicit finger-joint
    constraint-delete call). Root cause: a finger bind joint's
    parentConstraint/scaleConstraint has exactly ONE target
    (connector_null, from _chain_common.build_fk_joint_ctrl), and
    connector_null is a DAG descendant of rig_grp (nodes.build_null_pair
    parents it under fab_nulls_grp, which nodes.ensure_nulls_grp parents
    under rig_grp). Maya itself auto-deletes a constraint once its LAST
    remaining target is deleted (confirmed via an isolated mayapy probe:
    parentConstraint/scaleConstraint a joint to a second joint, delete
    the driver, the constraint node vanishes with it — no explicit
    cmds.delete needed). Since fs_app.unbuild_modules() ALWAYS deletes
    rig_grp (cascading away connector_null) shortly after calling
    RibbonIKArmComponent.unbuild(), the full-pipeline test above passes
    whether or not ribbon_ik_arm.py's own explicit
    `cmds.delete(constraints)` call actually runs — reverting it to a
    no-op (`if constraints: pass`) was verified to leave every assertion
    in that test passing (0 constraints after unbuild, exactly
    1 after rebuild) purely from Maya's incidental cascade cleanup.

    This test isolates the EXPLICIT sweep from that incidental cleanup
    by calling RibbonIKArmComponent.unbuild() DIRECTLY — the same classmethod
    fs_app.unbuild_modules() calls internally — WITHOUT the later
    `cmds.delete('rig_grp')` that orchestrator step performs afterward.
    rig_grp (and therefore connector_null) is still alive when the
    assertion runs, so a working explicit sweep is the ONLY thing that
    can make it pass; a broken one leaves the constraints in place for
    this check to catch, red before the fix, green after — see this
    task's red/green verification transcript."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import get_component_class
    from maya_tools.rigging.fabricator.modules.component import ComponentInstance

    _root, shoulder, elbow, wrist, fingers_joints, fingers_option, cid = (
        _build_ikarm_with_fingers('ikfconstraint2', _UE5_FINGER_DEFS))

    all_finger_joints = [j for chain in fingers_joints for j in chain]
    excluded_joints = {j for f in fingers_option for j in f['curl_excluded']}
    assert excluded_joints and (set(all_finger_joints) - excluded_joints), (
        'fixture must mix curl-included and curl_excluded members')

    for j, c in _finger_bind_joint_constraints(all_finger_joints).items():
        assert c == (1, 1), f'{j!r}: expected (1, 1) after build, got {c}'

    cnode = nodes.find_component_node_by_id(cid)
    assert cnode, f'expected a component node for {cid!r}'
    cls = get_component_class('RibbonIKArm')
    instance = ComponentInstance(
        id=cid, type='RibbonIKArm',
        joints=nodes.get_component_joint_names(cnode),
        options=nodes.get_component_options(cnode),
        persisted=nodes.get_component_persisted(cnode),
    )

    assert cmds.objExists('rig_grp'), (
        'rig_grp must still exist going into the direct unbuild() call '
        'for this isolation to be meaningful')
    cls.unbuild(instance)
    assert cmds.objExists('rig_grp'), (
        "RibbonIKArmComponent.unbuild() must NOT itself delete rig_grp (that's "
        "fs_app.unbuild_modules' job, run centrally afterward) — if it "
        "did, this test would no longer isolate the explicit sweep from "
        "the rig_grp-cascade's own incidental constraint cleanup")

    counts = _finger_bind_joint_constraints(all_finger_joints)
    leaked = {j: c for j, c in counts.items() if c != (0, 0)}
    assert not leaked, (
        f'finger bind joint(s) still have constraints immediately after '
        f'RibbonIKArmComponent.unbuild() returned, WHILE rig_grp (and its '
        f'connector_null descendants) still exist — the explicit finger '
        f'constraint sweep did not run: {leaked}')

    # Finish the teardown this direct call intentionally skipped, so the
    # scene is left sane (harmless even though the next test resets the
    # scene via cmds.file(new=True) anyway).
    if cmds.objExists('rig_grp'):
        cmds.delete('rig_grp')
def test_ikarm_finger_rename_survives_rebuild_fist_still_works_zero_warnings():
    """THE Task 2.3 headline gate (SPEC 2026-07-09 Limbs + Follower
    Joints §3.4): finger membership lives on the limb node via MESSAGE
    connections now, not name strings — renaming a finger's ROOT joint
    between unbuild and rebuild must be a complete non-event. Contrast
    with the pre-Task-2.3 world (see git history: the option-driven
    'fingers' list used to go silently stale on exactly this rename,
    losing the finger with a warning at best — P4 review finding #4).

    Proves, end to end:
      - The limb's finger_roots[] connection tracks the rename (no name
        string anywhere to go stale).
      - A rebuild produces FK ctrls for the renamed finger under its NEW
        name, with zero fingers/membership-related warnings.
      - The fist-curl master still actually drives the renamed finger's
        joints (functional proof, not just ctrl presence).
      - The other, untouched fingers are completely unaffected."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator import limb_node as ln

    (_root, shoulder, _elbow, wrist, fingers_joints, _fingers_option, _cid) = (
        _build_ikarm_with_fingers('ikfrename', _UE5_FINGER_DEFS,
                                  extra_options={'curl_axis': 'z'}))

    thumb_chain = fingers_joints[0]
    thumb_root_old = thumb_chain[0]
    thumb_ctrl_old = f'{thumb_root_old}_ctrl'
    assert cmds.objExists(thumb_ctrl_old)

    fs_app.unbuild_modules()

    # Rename the finger's ROOT joint — the exact case that used to go
    # stale under the retired name-string 'fingers' option.
    new_root = cmds.rename(thumb_root_old, f'{thumb_root_old}_RENAMED')
    assert new_root != thumb_root_old

    limb = ln.find_limb_for_joint(shoulder)
    assert limb, 'expected to find the limb node for this arm'
    assert new_root in ln.list_finger_roots(limb), (
        f'finger_roots[] did not track the rename (message connections '
        f'should be rename-proof by construction): '
        f'{ln.list_finger_roots(limb)}')
    assert thumb_root_old not in ln.list_finger_roots(limb)

    warnings = []
    orig_warning = cmds.warning

    def _capture(msg, *args, **kwargs):
        warnings.append(msg)
        return orig_warning(msg, *args, **kwargs)

    cmds.warning = _capture
    try:
        fs_app.build_modules()
    finally:
        cmds.warning = orig_warning

    finger_warnings = [w for w in warnings if 'finger' in w.lower()]
    assert not finger_warnings, (
        f'a finger rename must rebuild with ZERO finger-related '
        f'warnings; got {finger_warnings}')

    new_thumb_ctrl = f'{new_root}_ctrl'
    assert cmds.objExists(new_thumb_ctrl), (
        'renamed finger should still get an FK ctrl, under its NEW name')
    assert not cmds.objExists(thumb_ctrl_old), (
        'the OLD-named ctrl should not linger')

    # Functional proof: the fist-curl master still drives the renamed
    # finger's phalange. thumb_02/thumb_03 weren't renamed — only the
    # root was — so they keep their original names; the chain still
    # walks through them via walk_finger_chain regardless of the root's
    # name (scene-is-truth, no name string anywhere in the chain).
    thumb_mid = thumb_chain[1]
    assert cmds.objExists(thumb_mid), (
        f'{thumb_mid!r} (non-renamed chain member) should still exist '
        f'and still be a member of the renamed finger')
    fingers_ctrl = f'{wrist}_fingers_ctrl'
    assert cmds.objExists(fingers_ctrl)
    rot0 = cmds.getAttr(f'{thumb_mid}.rotateZ')
    cmds.setAttr(f'{fingers_ctrl}.rz', 25.0)
    rot1 = cmds.getAttr(f'{thumb_mid}.rotateZ')
    assert abs(rot1 - rot0) > 1.0, (
        f'the renamed finger did not curl with the fist master — '
        f'{thumb_mid!r} rotateZ: {rot0} -> {rot1}')

    # The OTHER fingers are completely unaffected.
    index_ctrl = f'{fingers_joints[1][0]}_ctrl'
    assert cmds.objExists(index_ctrl), (
        'an unrelated finger rename must not disturb the other fingers')

    fs_app.unbuild_modules()


def test_ikarm_finger_inserted_joint_auto_joins_on_rebuild():
    """Task 2.3 companion to the headline rename gate: since finger
    membership is now walked LIVE off the scene DAG at build time
    (_limb_common.walk_finger_chain, from each finger_roots[] root — see
    that function's own docstring), a joint INSERTED as a new child at an
    existing finger's tip between unbuild and rebuild automatically joins
    that finger on the very next build — no membership edit, no re-drop,
    nothing but ordinary Edit Mode joint authoring. Proves both the
    structural join (the new joint gets a chain-parented FK ctrl) and the
    functional one (the fist-curl master drives it too)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app

    (_root, _shoulder, _elbow, wrist, fingers_joints, _fingers_option, _cid) = (
        _build_ikarm_with_fingers('ikinsert', _UE5_FINGER_DEFS,
                                  extra_options={'curl_axis': 'z'}))

    thumb_chain = fingers_joints[0]
    thumb_tip = thumb_chain[-1]  # thumb_03 — the finger's current tip
    assert cmds.objExists(f'{thumb_tip}_ctrl')

    fs_app.unbuild_modules()

    # Insert a brand-new joint as the tip's child — an ordinary Edit
    # Mode authoring action, never touching any membership API at all.
    cmds.select(thumb_tip, replace=True)
    new_tip = cmds.joint(name=f'{thumb_tip}_new', position=(
        cmds.xform(thumb_tip, q=True, ws=True, t=True)[0] + 1.0,
        cmds.xform(thumb_tip, q=True, ws=True, t=True)[1],
        cmds.xform(thumb_tip, q=True, ws=True, t=True)[2]))
    cmds.select(clear=True)
    assert (cmds.listRelatives(new_tip, parent=True, type='joint') or
           [None])[0] == thumb_tip

    fs_app.build_modules()

    new_ctrl = f'{new_tip}_ctrl'
    assert cmds.objExists(new_ctrl), (
        f'the newly inserted joint {new_tip!r} did not get an FK ctrl on '
        f'rebuild — it should have auto-joined the thumb finger via the '
        f'live descendant walk')
    old_tip_ctrl = f'{thumb_tip}_ctrl'
    parent = (cmds.listRelatives(f'{new_tip}_ctrl_offset', parent=True)
             or [None])[0]
    assert parent == old_tip_ctrl, (
        f'{new_tip}_ctrl_offset should chain-parent under the PREVIOUS '
        f'tip\'s ctrl ({old_tip_ctrl!r}), got {parent!r}')

    fingers_ctrl = f'{wrist}_fingers_ctrl'
    assert cmds.objExists(fingers_ctrl)
    rot0 = cmds.getAttr(f'{new_tip}.rotateZ')
    cmds.setAttr(f'{fingers_ctrl}.rz', 30.0)
    rot1 = cmds.getAttr(f'{new_tip}.rotateZ')
    assert abs(rot1 - rot0) > 1.0, (
        f'the newly inserted, auto-joined joint did not curl with the '
        f'fist master: {new_tip!r} rotateZ {rot0} -> {rot1}')

    fs_app.unbuild_modules()
def test_ikarm_finger_ctrl_joint_index_disambiguates_via_pose_library():
    """P4 review finding #6 (major): finger FK ctrls used to get a
    PER-FINGER-LOCAL joint_index (0, 1, 2... restarting at 0 for every
    finger), colliding across every finger sharing the 'finger_fk_ctrl'
    role AND with the arm's own shoulder/elbow/wrist joint_index values.
    pose_library.address.address_to_ctrl's (fab_role,
    fab_joint_index) lookup returns the FIRST match, so a saved pose
    address for one finger's root ctrl could silently resolve to a
    DIFFERENT finger's ctrl. Proves the fix end-to-end through the real
    consumer: round-trips two different fingers' root ctrls through
    ctrl_to_address -> address_to_ctrl and checks each resolves back to
    ITS OWN ctrl, not the other's."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.utils.maya import rig_binding
    from maya_tools.animation.pose_library import address as pl_address

    root, _shoulder, _elbow, wrist, fingers_joints, _fingers_option, _cid = (
        _build_ikarm_with_fingers('ikfidx', _UE5_FINGER_DEFS))

    thumb_root_ctrl = f'{fingers_joints[0][0]}_ctrl'   # thumb_01
    index_root_ctrl = f'{fingers_joints[1][0]}_ctrl'   # index_metacarpal
    assert cmds.objExists(thumb_root_ctrl) and cmds.objExists(index_root_ctrl)

    thumb_idx = cmds.getAttr(f'{thumb_root_ctrl}.fab_joint_index')
    index_idx = cmds.getAttr(f'{index_root_ctrl}.fab_joint_index')
    assert thumb_idx != index_idx, (
        'two different fingers\' root ctrls share a joint_index — '
        'pose_library cannot disambiguate them')
    assert thumb_idx >= 3 and index_idx >= 3, (
        'finger ctrl joint_index collides with the arm\'s own primary '
        'joints[] slots (0=shoulder, 1=elbow, 2=wrist)')

    binding = rig_binding.find_binding_for_root(root)
    assert binding, f'no FAB_RigBinding found for root {root!r}'

    thumb_addr = pl_address.ctrl_to_address(thumb_root_ctrl)
    index_addr = pl_address.ctrl_to_address(index_root_ctrl)
    assert thumb_addr is not None and index_addr is not None
    assert thumb_addr.ctrl_role == 'finger_fk_ctrl' == index_addr.ctrl_role

    # address_to_ctrl walks full DAG paths (fullPath=True); compare short
    # names — these joint short names are unique in this test's scene.
    resolved_thumb = pl_address.address_to_ctrl(thumb_addr, binding)
    resolved_index = pl_address.address_to_ctrl(index_addr, binding)
    resolved_thumb_short = (resolved_thumb or '').split('|')[-1]
    resolved_index_short = (resolved_index or '').split('|')[-1]
    assert resolved_thumb_short == thumb_root_ctrl, (
        f'address round-trip resolved to {resolved_thumb!r}, expected '
        f'{thumb_root_ctrl!r}')
    assert resolved_index_short == index_root_ctrl, (
        f'address round-trip resolved to {resolved_index!r}, expected '
        f'{index_root_ctrl!r}')
    assert resolved_thumb != resolved_index, (
        'both addresses resolved to the SAME ctrl — disambiguation is '
        'still broken')

    fs_app.unbuild_modules()


def test_ikarm_mirror_component_mirrors_limb_finger_membership():
    """Derived Limbs (spec 2026-07-11): the mirrored side derives its
    OWN limb — fingers discovered fresh off R's live subtree, never
    flipped/unioned from L's lists. Proves:
      (1) the mirrored component's options carry no resurrected
          'fingers' key;
      (2) R gets its own limb with genuinely rt_-prefixed, live
          finger_roots[] (all 5 discovered);
      (3) a MANUAL curl_excluded edit on L does NOT propagate — R's
          exclusions stay the fresh heuristic (hand-edited membership
          is retired; derivation is the only source);
      (4) building BOTH sides for real, no finger joint ends up with a
          duplicate second driving constraint from the OTHER side's
          build."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist, fingers_joints = _make_hand_chain(
        'lf', _UE5_FINGER_DEFS)
    all_lf_joints = ([root, shoulder, elbow, wrist]
                     + [j for c in fingers_joints for j in c])

    # Real mirror — mirrorJoint reflection + searchReplace, the same
    # convention test_ikarm_roll_mirror_offset_sign_flip uses for its
    # real right-side rig (produces genuinely-reflected rt_ geometry,
    # not a coincidental identical-coordinate second build).
    created = cmds.mirrorJoint(root, mirrorYZ=True, mirrorBehavior=True,
                               searchReplace=('lf', 'rt'))
    assert created and created[0] == 'rt_root'
    for j in all_lf_joints:
        rt_j = j.replace('lf_', 'rt_', 1)
        assert cmds.objExists(rt_j), f'mirrorJoint did not produce {rt_j!r}'

    nodes.create_registry('ikfmirror_bp')
    _add_world_component('lf_world', root)
    nodes.create_component_node(
        component_id='lf_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='lf',
        options={'mid_ctrl_count': 1}, persisted={},
    )

    # L's own implicit-limb discovery (creates_implicit_limb, fresh live
    # wrist subtree at create time) should already have found all 5
    # fingers — fixture sanity before the actually-interesting part.
    l_limb = ln.find_limb_for_joint(shoulder)
    assert l_limb, 'expected the implicit limb to auto-create for L'
    assert len(ln.list_finger_roots(l_limb)) == len(_UE5_FINGER_DEFS)

    # Manual curl_excluded edit that a FRESH heuristic re-run would NOT
    # produce on its own: the thumb chain has no metacarpal token, so
    # metacarpal_excluded normally excludes nothing from it — hand-
    # exclude its tip anyway (nonsensical rig-wise, but proves the
    # mirror carries L's ACTUAL authored state, not a re-derived guess).
    thumb_chain = fingers_joints[0]
    thumb_tip = thumb_chain[-1]
    ln.add_curl_excluded(l_limb, thumb_tip)
    assert thumb_tip in ln.list_curl_excluded(l_limb)

    r_id = fs_app.mirror_component('lf_C0')

    r_node = nodes.find_component_node_by_id(r_id)
    assert r_node, f'expected a component node for mirrored id {r_id!r}'
    assert 'fingers' not in nodes.get_component_options(r_node), (
        "mirrored component's options carry a 'fingers' key — the "
        "retired option must never be (re)written")

    r_shoulder = shoulder.replace('lf_', 'rt_', 1)
    r_limb = ln.find_limb_for_joint(r_shoulder)
    assert r_limb, 'expected a limb node for the mirrored R component'

    r_roots = set(ln.list_finger_roots(r_limb))
    assert len(r_roots) == len(_UE5_FINGER_DEFS), (
        f'mirrored limb lost/gained finger roots: {sorted(r_roots)}')
    for j in r_roots:
        assert j.startswith('rt_'), (
            f'mirrored limb finger_roots still references {j!r} — '
            f'stale L-side joint name never remapped')
        assert cmds.objExists(j), f'{j!r} does not exist in the scene'

    r_thumb_tip = thumb_tip.replace('lf_', 'rt_', 1)
    assert r_thumb_tip not in ln.list_curl_excluded(r_limb), (
        f"L's manual curl_excluded edit ({thumb_tip!r}) leaked onto R's "
        f"limb — R must be a fresh derivation, got "
        f"{ln.list_curl_excluded(r_limb)}")

    # Full build regression: neither side's finger joints get a SECOND
    # driving constraint from the OTHER side's build.
    rt_root = root.replace('lf_', 'rt_', 1)
    _add_world_component('rt_world', rt_root)
    fs_app.build_modules()

    for chain in fingers_joints:  # the ORIGINAL lf_ joints
        for j in chain:
            constraints = cmds.listRelatives(j, type='parentConstraint') or []
            assert len(constraints) <= 1, (
                f'{j!r} has {len(constraints)} parentConstraints — the '
                f'mirrored R component built a duplicate driver onto '
                f'the L joint')

    for root_j in r_roots:
        for member in hc.walk_finger_chain(root_j):
            rt_ctrl = f'{hc.short_name(member)}_ctrl'
            assert cmds.objExists(rt_ctrl), (
                f'expected an R-side finger ctrl {rt_ctrl!r} for {member!r}')

    fs_app.unbuild_modules()


def test_ikarm_finger_regression_suites_stay_green():
    """Phase 1/2/3 ribbon+roll behavior on an arm WITH fingers attached —
    a regression guard that P4's finger build doesn't disturb the
    existing ribbon/roll subsystems sharing the same build()/unbuild()."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    _root, shoulder, elbow, wrist, _fingers_joints, _fingers_option, _cid = (
        _build_ikarm_with_fingers('ikfregress', _4FINGER_NO_METACARPAL_DEFS))

    upper_chain = rc.ribbon_segment_chain_name(shoulder, elbow)
    forearm_chain = rc.ribbon_segment_chain_name(elbow, wrist)
    for chain in (upper_chain, forearm_chain):
        assert cmds.objExists(f'{chain}_ribbon_surf'), (
            f'{chain}: ribbon surface missing with fingers attached')

    switch_ctrl = f'{wrist}_IKFK_ctrl'
    assert cmds.attributeQuery('ik_fk_blend', node=switch_ctrl, exists=True)

    fs_app.unbuild_modules()
    assert not cmds.objExists('rig_grp'), (
        'rig_grp survived unbuild on an arm with fingers attached')


# ─── Phase 5: layered fist-curl master + skeleton-only limbs ─────────────

def test_ikarm_fingers_ctrl_layered_curl_and_metacarpal_exclusion():
    """Task 5.1's core proof: with the arm POSED off bind, rotating
    fingers_ctrl curls every INCLUDED phalange; curl_excluded
    (metacarpal) joints get NO wiring (zero incoming connections) and
    don't move; a per-finger <joint>_ctrl key SUMS on top of the master
    (superposition, not override-loss); non-curl channels on
    fingers_ctrl are locked.

    Ship-gate review coverage gaps closed here:
      - The master/key/sum superposition probe used to run on the thumb
        ONLY (the one _UE5_FINGER_DEFS finger with no metacarpal). Now
        ALSO probed on index_02 — a metacarpal-bearing finger, exercising
        the SAME generic per-finger loop in _limb_common.build_fingers_
        ctrl (zip(joint_list, pairs)) at a different iteration position,
        so a per-finger indexing/keying regression that happened to spare
        finger 0 (thumb) specifically would no longer read as healthy.
      - The zero-wiring (curl_excluded) check used to inspect ONLY the
        index metacarpal. Now loops over all four UE5 metacarpal joints
        (index/middle/ring/pinky), all of which run through the identical
        exclusion code path but were previously never queried post-build.
    """
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app

    _root, _shoulder, _elbow, wrist, fingers_joints, fingers_option, _cid = (
        _build_ikarm_with_fingers('ikcurl', _UE5_FINGER_DEFS,
                                  extra_options={'curl_axis': 'z'}))

    # POSED: bend the elbow via the IK end ctrl before touching curl — the
    # whole hand (every finger included) moves off bind in worldspace.
    # Finger curl is measured via LOCAL rotate (parent-relative), which
    # must behave identically regardless of where the hand sits in world.
    ik_ctrl = f'{wrist}_IK_ctrl'
    assert cmds.objExists(ik_ctrl), f'expected {ik_ctrl!r} to exist'
    cmds.setAttr(f'{ik_ctrl}.translateY',
                cmds.getAttr(f'{ik_ctrl}.translateY') + 3.0)
    cmds.setAttr(f'{ik_ctrl}.translateZ',
                cmds.getAttr(f'{ik_ctrl}.translateZ') + 2.0)

    fingers_ctrl = f'{wrist}_fingers_ctrl'
    assert cmds.objExists(fingers_ctrl), f'expected {fingers_ctrl!r} to exist'

    # ─── Non-curl channels locked; curl axis (Z, per curl_axis='z') is
    # the only keyable channel. ────────────────────────────────────────
    for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'sx', 'sy', 'sz', 'v'):
        assert cmds.getAttr(f'{fingers_ctrl}.{attr}', lock=True), (
            f'{fingers_ctrl}.{attr} should be locked')
        assert not cmds.getAttr(f'{fingers_ctrl}.{attr}', keyable=True), (
            f'{fingers_ctrl}.{attr} should not be keyable')
    assert not cmds.getAttr(f'{fingers_ctrl}.rz', lock=True), (
        'fingers_ctrl.rz (the configured curl axis) should NOT be locked')
    assert cmds.getAttr(f'{fingers_ctrl}.rz', keyable=True), (
        'fingers_ctrl.rz should be keyable')

    by_root = {f['root_joint']: f for f in fingers_option}

    # thumb: 3-joint UE finger, no curl_excluded — every joint included.
    thumb_chain = fingers_joints[0]
    thumb_mid = thumb_chain[1]           # thumb_02
    thumb_ctrl = f'{thumb_mid}_ctrl'
    assert by_root[thumb_chain[0]]['curl_excluded'] == []

    # index: 4-joint UE finger, metacarpal excluded by name-match. Also
    # the metacarpal-bearing finger used for the layered-curl SUM probe
    # below (item 5) — a mid phalange one level deeper than the thumb's,
    # exercising the SAME zip(joint_list, pairs)-driven per-finger loop
    # at a non-zero iteration position.
    index_chain = fingers_joints[1]
    index_metacarpal = index_chain[0]
    index_mid = index_chain[2]           # index_02
    index_ctrl = f'{index_mid}_ctrl'
    assert by_root[index_metacarpal]['curl_excluded'] == [index_metacarpal]

    # curl_excluded gets ZERO wiring on EVERY UE5 metacarpal (index/
    # middle/ring/pinky — item 6), not just index's — a genuine absence
    # of any incoming connection, not just a coincidentally-unchanged
    # value.
    metacarpals = [chain[0] for chain in fingers_joints[1:]]  # skip thumb
    assert len(metacarpals) == 4, metacarpals
    mc_offsets = {}
    for mc in metacarpals:
        assert by_root[mc]['curl_excluded'] == [mc], (
            f'{mc!r} expected to be curl_excluded, got {by_root[mc]}')
        mc_offset = f'{mc}_ctrl_offset'
        mc_offsets[mc] = mc_offset
        incoming = cmds.listConnections(f'{mc_offset}.rotateZ', source=True,
                                        destination=False, plugs=True) or []
        assert incoming == [], (
            f'{mc_offset}.rotateZ should have NO incoming connection '
            f'(curl_excluded); got {incoming}')

    rot0_mcs = {mc: cmds.getAttr(f'{mc}.rotateZ') for mc in metacarpals}
    rot0_thumb = cmds.getAttr(f'{thumb_mid}.rotateZ')
    rot0_index = cmds.getAttr(f'{index_mid}.rotateZ')

    # ─── Master only ───────────────────────────────────────────────────
    cmds.setAttr(f'{fingers_ctrl}.rz', 40.0)
    rot_master_mcs = {mc: cmds.getAttr(f'{mc}.rotateZ') for mc in metacarpals}
    rot_master_thumb = cmds.getAttr(f'{thumb_mid}.rotateZ')
    rot_master_index = cmds.getAttr(f'{index_mid}.rotateZ')

    for mc in metacarpals:
        assert abs(rot_master_mcs[mc] - rot0_mcs[mc]) < 1e-6, (
            f'excluded metacarpal {mc!r} moved when the master rotated: '
            f'{rot0_mcs[mc]} -> {rot_master_mcs[mc]}')
    assert abs(rot_master_thumb - rot0_thumb) > 1.0, (
        f'included phalange {thumb_mid!r} did not curl with the master: '
        f'{rot0_thumb} -> {rot_master_thumb}')
    assert abs(rot_master_index - rot0_index) > 1.0, (
        f'included phalange {index_mid!r} did not curl with the master: '
        f'{rot0_index} -> {rot_master_index}')
    delta_master = rot_master_thumb - rot0_thumb
    delta_master_index = rot_master_index - rot0_index

    # ─── Per-finger key only (master back to 0) ────────────────────────
    cmds.setAttr(f'{fingers_ctrl}.rz', 0.0)
    cmds.setAttr(f'{thumb_ctrl}.rotateZ', 15.0)
    cmds.setAttr(f'{index_ctrl}.rotateZ', 20.0)
    rot_key_thumb = cmds.getAttr(f'{thumb_mid}.rotateZ')
    rot_key_index = cmds.getAttr(f'{index_mid}.rotateZ')
    delta_key = rot_key_thumb - rot0_thumb
    delta_key_index = rot_key_index - rot0_index
    assert abs(delta_key) > 1.0, (
        f'per-finger key on {thumb_ctrl!r} did not move {thumb_mid!r}')
    assert abs(delta_key_index) > 1.0, (
        f'per-finger key on {index_ctrl!r} did not move {index_mid!r}')

    # ─── Both together: SUM, not override-loss ─────────────────────────
    cmds.setAttr(f'{fingers_ctrl}.rz', 40.0)
    rot_both_thumb = cmds.getAttr(f'{thumb_mid}.rotateZ')
    rot_both_index = cmds.getAttr(f'{index_mid}.rotateZ')
    delta_both = rot_both_thumb - rot0_thumb
    delta_both_index = rot_both_index - rot0_index
    expected = delta_master + delta_key
    expected_index = delta_master_index + delta_key_index
    assert abs(delta_both - expected) < 1e-3, (
        f'layered curl not additive: master-only delta={delta_master}, '
        f'key-only delta={delta_key}, both delta={delta_both}, '
        f'expected {expected}')
    assert abs(delta_both_index - expected_index) < 1e-3, (
        f'layered curl not additive on metacarpal-bearing finger '
        f'{index_mid!r}: master-only delta={delta_master_index}, '
        f'key-only delta={delta_key_index}, both delta={delta_both_index}, '
        f'expected {expected_index}')

    fs_app.unbuild_modules()


def test_ikarm_fingers_ctrl_curl_axis_x_variant():
    """Ship-gate review coverage gap: every extra_options={'curl_axis': ...}
    call elsewhere in this file passes 'z' (also the OptionField's own
    default), so build_fingers_ctrl's axis-selection branch, keyable-
    channel whitelist, and per-offset plug string (f'{offset_ctrl}.
    {curl_attr}') were entirely unverified for any OTHER curl_axis value
    — a hypothetical regression that silently ignored curl_axis and
    always wired rotateZ would have passed every test in this suite
    undetected. Mirrors test_ikarm_fingers_ctrl_layered_curl_and_
    metacarpal_exclusion's core shape but with curl_axis='x': asserts
    fingers_ctrl.rx (not .rz) is the sole unlocked/keyable channel, and
    that rotateX (not rotateZ) is what the master sums into."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app

    _root, _shoulder, _elbow, wrist, fingers_joints, fingers_option, _cid = (
        _build_ikarm_with_fingers('ikcurlx', _UE5_FINGER_DEFS,
                                  extra_options={'curl_axis': 'x'}))

    fingers_ctrl = f'{wrist}_fingers_ctrl'
    assert cmds.objExists(fingers_ctrl), f'expected {fingers_ctrl!r} to exist'

    # ─── curl_axis='x': rx is the ONLY unlocked/keyable channel — ry/rz
    # (the OTHER two rotates, including 'z', the default/every-other-
    # test's axis) must be locked same as any non-curl channel. ────────
    for attr in ('tx', 'ty', 'tz', 'ry', 'rz', 'sx', 'sy', 'sz', 'v'):
        assert cmds.getAttr(f'{fingers_ctrl}.{attr}', lock=True), (
            f'{fingers_ctrl}.{attr} should be locked (curl_axis=x)')
        assert not cmds.getAttr(f'{fingers_ctrl}.{attr}', keyable=True), (
            f'{fingers_ctrl}.{attr} should not be keyable (curl_axis=x)')
    assert not cmds.getAttr(f'{fingers_ctrl}.rx', lock=True), (
        'fingers_ctrl.rx (the configured curl axis) should NOT be locked')
    assert cmds.getAttr(f'{fingers_ctrl}.rx', keyable=True), (
        'fingers_ctrl.rx should be keyable')

    by_root = {f['root_joint']: f for f in fingers_option}
    thumb_chain = fingers_joints[0]
    thumb_mid = thumb_chain[1]  # thumb_02, no curl_excluded
    assert by_root[thumb_chain[0]]['curl_excluded'] == []

    thumb_offset = f'{thumb_mid}_ctrl_offset'
    # The per-offset wiring must target rotateX, NOT rotateZ — a direct
    # check of the string-driven plug construction
    # (f'{offset_ctrl}.{curl_attr}') build_fingers_ctrl builds from axis.
    incoming_rx = cmds.listConnections(f'{thumb_offset}.rotateX', source=True,
                                       destination=False, plugs=True) or []
    assert incoming_rx and incoming_rx[0] == f'{fingers_ctrl}.rotateX', (
        f'{thumb_offset}.rotateX should be driven directly by '
        f'{fingers_ctrl}.rotateX (curl_axis=x); got {incoming_rx}')
    incoming_rz = cmds.listConnections(f'{thumb_offset}.rotateZ', source=True,
                                       destination=False, plugs=True) or []
    assert incoming_rz == [], (
        f'{thumb_offset}.rotateZ should have NO incoming connection when '
        f'curl_axis=x (only rotateX should be wired); got {incoming_rz}')

    rot0_x = cmds.getAttr(f'{thumb_mid}.rotateX')
    rot0_z = cmds.getAttr(f'{thumb_mid}.rotateZ')
    cmds.setAttr(f'{fingers_ctrl}.rx', 40.0)
    rot1_x = cmds.getAttr(f'{thumb_mid}.rotateX')
    rot1_z = cmds.getAttr(f'{thumb_mid}.rotateZ')

    assert abs(rot1_x - rot0_x) > 1.0, (
        f'{thumb_mid!r}.rotateX did not respond to fingers_ctrl.rx '
        f'(curl_axis=x): {rot0_x} -> {rot1_x}')
    assert abs(rot1_z - rot0_z) < 1e-6, (
        f'{thumb_mid!r}.rotateZ moved when curl_axis=x — the master should '
        f'only ever drive rotateX in this configuration: {rot0_z} -> '
        f'{rot1_z}')

    fs_app.unbuild_modules()


def test_ikarm_fingers_ctrl_plusminusaverage_branch_and_unbuild_clean():
    """Seeding a finger joint's bind rotation before build forces
    _limb_common.build_fingers_ctrl's 'authored baseline' branch (a
    plusMinusAverage(sum) node instead of a direct connect) — proves the
    sum is baseline + master, unbuild deletes the tracked node (DG type
    counts return to the pre-build baseline), and fingers_ctrl's own
    authored CV shape persists across unbuild -> build.

    This is also the ONE test that builds ALL FOUR RibbonIKArm subsystems
    together (2 ribbon segments + 2 roll rigs + fingers + fist curl) — a
    ship-gate review coverage gap noted it never NAME-checked the roll
    (*_roll_jnt/*_roll_aim_loc) or ribbon (*_ribbon_surf/
    *_ribbon_skin/*_ribbon_uvpin) nodes for this specific fully-combined
    scene the way test_ikarm_roll_unbuild_leaves_zero_orphans and
    test_ikarm_ribbon_unbuild_leaves_zero_orphans do for the arm ALONE,
    nor repeated the DG-type-count parity check on a SECOND build->
    unbuild cycle (a slow per-cycle accumulation leak wouldn't be caught
    by a single build->unbuild->build->unbuild pass that only checks
    parity once). Both gaps are closed inline below."""
    import maya.cmds as cmds
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    cmds.file(new=True, force=True)
    prefix = 'ikpma'
    root, shoulder, elbow, wrist, fingers_joints = _make_hand_chain(
        prefix, _UE5_FINGER_DEFS)
    thumb_mid = fingers_joints[0][1]  # thumb_02 — no metacarpal exclusion

    # Seed a nonzero AUTHORED bind rotation on the curl axis BEFORE the
    # RibbonIKArm ever builds — build_finger_fk_chain's matchTransform copies
    # this onto <thumb_mid>_ctrl_offset.rotateZ, forcing build_fingers_
    # ctrl's plusMinusAverage branch instead of a direct connect.
    cmds.setAttr(f'{thumb_mid}.rotateZ', 12.5)

    nodes.create_registry(f'{prefix}_bp')
    _add_world_component(f'{prefix}_world', root)
    # Task 2.3: no 'fingers' option — the finger joints already exist
    # live under wrist (built above), so nodes._maybe_create_implicit_
    # limb's own discovery (fired inside create_component_node, since
    # RibbonIKArm.creates_implicit_limb) connects finger_roots[]/
    # curl_excluded[] onto this component's limb node automatically.
    nodes.create_component_node(
        component_id=f'{prefix}_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1, 'curl_axis': 'z'},
        persisted={},
    )

    dg_before = _dg_type_counts()
    # unitConversion is tracked LOCALLY here rather than added to the
    # shared _OWNED_DG_TYPES tuple (which every other orphan test in this
    # file also reads) — Maya auto-inserts these bridging a GENERIC
    # plusMinusAverage.input1D/output1D to/from an angle-typed
    # .rotate<AXIS> (confirmed empirically: does NOT happen for a plain
    # rotate-to-rotate direct connect), and _limb_common.build_fingers_
    # ctrl's _connect_and_track_conversion helper tracks them into
    # 'curl_dg_nodes' precisely so this doesn't leak — this test proves
    # THAT fix without touching the shared baseline every other P1-P4
    # orphan test in this file also depends on.
    unit_conv_before = len(cmds.ls(type='unitConversion') or [])
    fs_app.build_modules()

    thumb_offset = f'{thumb_mid}_ctrl_offset'
    fingers_ctrl = f'{wrist}_fingers_ctrl'
    assert cmds.objExists(fingers_ctrl)

    # Ship-gate review coverage gap: name-check both segments' roll AND
    # ribbon nodes in THIS fully-combined (ribbon+roll+fingers+curl)
    # scene — mirrors test_ikarm_roll_unbuild_leaves_zero_orphans' /
    # test_ikarm_ribbon_unbuild_leaves_zero_orphans' own named-node
    # pattern, applied here instead of assuming the arm-alone tests
    # generalize to the fully-assembled build.
    upper_chain = rc.ribbon_segment_chain_name(shoulder, elbow)
    forearm_chain = rc.ribbon_segment_chain_name(elbow, wrist)
    roll_upper_prefix = f'{hc.short_name(shoulder)}_{hc.short_name(elbow)}_roll'
    roll_forearm_prefix = f'{hc.short_name(elbow)}_{hc.short_name(wrist)}_roll'
    combined_named_nodes = []
    for chain in (upper_chain, forearm_chain):
        combined_named_nodes += [
            f'{chain}_ribbon_surf', f'{chain}_ribbon_skin', f'{chain}_ribbon_uvpin',
        ]
    for roll_prefix in (roll_upper_prefix, roll_forearm_prefix):
        combined_named_nodes += [
            f'{roll_prefix}_jnt', f'{roll_prefix}_aim_loc',
        ]
    assert all(cmds.objExists(n) for n in combined_named_nodes), (
        f'setup: expected all roll+ribbon nodes to exist in this '
        f'fully-combined build before unbuild: '
        f'{[n for n in combined_named_nodes if not cmds.objExists(n)]}')
    # Post-build (fully-assembled, "during") DG counts — the correct
    # comparison point for the second build->unbuild cycle below (a
    # rebuild should reproduce this exact node-set, NOT the empty
    # pre-build baseline).
    dg_during_1 = _dg_type_counts()

    # skipConversionNodes=True: Maya auto-inserts a unitConversion node
    # between a unitless plusMinusAverage.output1D and an angle-typed
    # .rotateZ input — an implementation detail of the connection, not
    # the wiring this test is actually checking.
    incoming = cmds.listConnections(f'{thumb_offset}.rotateZ', source=True,
                                    destination=False,
                                    skipConversionNodes=True) or []
    assert incoming, f'{thumb_offset}.rotateZ should have an incoming connection'
    src_type = cmds.nodeType(incoming[0])
    assert src_type == 'plusMinusAverage', (
        f'expected the authored-baseline branch (plusMinusAverage) since '
        f'{thumb_mid!r} was seeded with a nonzero bind rotation; got '
        f'{incoming[0]!r} ({src_type!r})')

    baseline = 12.5
    cmds.setAttr(f'{fingers_ctrl}.rz', 30.0)
    summed = cmds.getAttr(f'{thumb_mid}.rotateZ')
    assert abs(summed - (baseline + 30.0)) < 1e-3, (
        f'expected baseline({baseline}) + master(30.0) = '
        f'{baseline + 30.0}, got {summed}')

    # Ship-gate review coverage gap: the SPEC 3.5 "baseline + master + key"
    # full additivity promise was only ever end-to-end-verified in the
    # direct-connect (zero-baseline) branch — this plusMinusAverage
    # (authored-baseline) branch stopped at baseline+master above. A
    # per-finger animator key on the ctrl (child of the driven offset)
    # must still sum on top here too — the branch a rig with any
    # naturally-curled resting finger would actually hit in production.
    thumb_ctrl = f'{thumb_mid}_ctrl'
    cmds.setAttr(f'{thumb_ctrl}.rotateZ', 8.0)
    summed_with_key = cmds.getAttr(f'{thumb_mid}.rotateZ')
    assert abs(summed_with_key - (baseline + 30.0 + 8.0)) < 1e-3, (
        f'expected baseline({baseline}) + master(30.0) + key(8.0) = '
        f'{baseline + 30.0 + 8.0}, got {summed_with_key} — the '
        f'plusMinusAverage (authored-baseline) branch must let a '
        f'per-finger animator key sum on top of baseline+master, not '
        f'just baseline+master alone')
    cmds.setAttr(f'{thumb_ctrl}.rotateZ', 0.0)

    # Author a distinctive fingers_ctrl shape edit, like a rigger would.
    cmds.select(f'{fingers_ctrl}.cv[*]')
    cmds.scale(1.5, 1.5, 1.5, relative=True)
    cmds.select(clear=True)
    edited_shape = com.serialize_shape(fingers_ctrl)
    assert edited_shape.get('shapes')

    fs_app.unbuild_modules()
    assert not cmds.objExists('rig_grp'), 'rig_grp survived unbuild'
    assert not cmds.objExists(incoming[0]), (
        f'curl-sum plusMinusAverage node {incoming[0]!r} survived unbuild')

    unit_conv_after = len(cmds.ls(type='unitConversion') or [])
    assert unit_conv_after == unit_conv_before, (
        f'unitConversion node(s) auto-inserted by the curl-sum wiring '
        f'leaked past unbuild: before={unit_conv_before} '
        f'after={unit_conv_after}')

    dg_after = _dg_type_counts()
    assert dg_after == dg_before, (
        f'DG node counts did not return to pre-build baseline after '
        f'unbuild: before={dg_before} after={dg_after}')

    # Ship-gate review coverage gap: NAME-check the roll+ribbon nodes are
    # actually gone too, not just DG-type counts (a regression that swaps
    # one tracked node for a same-type replacement — e.g. a differently-
    # named leftover skinCluster — could pass the type-count check above
    # while still leaking a specific named node).
    leaked_combined = [n for n in combined_named_nodes if cmds.objExists(n)]
    assert not leaked_combined, (
        f'roll/ribbon nodes survived unbuild on the fully-combined arm: '
        f'{leaked_combined}')

    fs_app.build_modules()
    assert cmds.objExists(fingers_ctrl), 'fingers_ctrl should exist again after rebuild'
    rebuilt_shape = com.serialize_shape(fingers_ctrl)
    assert rebuilt_shape.get('shapes') == edited_shape.get('shapes'), (
        'fingers_ctrl CV edit did not persist across unbuild -> build')

    # Ship-gate review coverage gap: repeat the DG-type-count parity check
    # (and the roll/ribbon named-node check) after a SECOND build->unbuild
    # cycle of the same fully-assembled instance — the first parity check
    # above only proves ONE cycle is clean; a slow per-cycle accumulation
    # (e.g. one extra node leaking each time) would not be caught by a
    # single before/after comparison. Compare against dg_during_1 (the
    # FIRST build's post-build, fully-assembled counts) — the correct
    # "a rebuild reproduces the identical node-set" comparison point, not
    # the empty pre-build baseline.
    dg_during_2 = _dg_type_counts()
    assert dg_during_2 == dg_during_1, (
        f'DG node counts after the SECOND build do not match the FIRST '
        f'build\'s post-build counts (a rebuild should reproduce the '
        f'identical node-set, not accumulate or lose nodes): '
        f'first_build={dg_during_1} second_build={dg_during_2}')

    fs_app.unbuild_modules()
    assert not cmds.objExists('rig_grp'), (
        'rig_grp survived unbuild on the SECOND build->unbuild cycle')
    leaked_combined_2 = [n for n in combined_named_nodes if cmds.objExists(n)]
    assert not leaked_combined_2, (
        f'roll/ribbon nodes survived the SECOND unbuild cycle: '
        f'{leaked_combined_2}')

    dg_after_2 = _dg_type_counts()
    assert dg_after_2 == dg_before, (
        f'DG node counts did not return to baseline after the SECOND '
        f'build->unbuild cycle — a slow per-cycle accumulation leak: '
        f'baseline={dg_before} after_second_cycle={dg_after_2}')


def _finger_fragment(prefix):
    """A LimbFragment matching the shipped finger.limb.yaml's shape
    (metacarpal + 3 phalanges, skeleton-only), with joint names scoped
    to `prefix` so multiple drops in the same test scene don't collide."""
    from maya_tools.rigging.fabricator.limbs.schema import LimbFragment, ExternalAnchor
    from maya_tools.rigging.fabricator.blueprint.schema import JointSpec

    return LimbFragment(
        name='finger',
        external_anchor=ExternalAnchor(plug_kind='matrix'),
        skeleton_joints=[
            JointSpec(name=f'{prefix}_metacarpal', parent='<EXTERNAL>',
                     translate=[0.0, 0.0, 0.0], radius=0.5),
            JointSpec(name=f'{prefix}_01', parent=f'{prefix}_metacarpal',
                     translate=[1.0, 0.0, 0.0], radius=0.4),
            JointSpec(name=f'{prefix}_02', parent=f'{prefix}_01',
                     translate=[1.0, 0.0, 0.0], radius=0.35),
            JointSpec(name=f'{prefix}_03', parent=f'{prefix}_02',
                     translate=[1.0, 0.0, 0.0], radius=0.3),
        ],
        components=[],
    )


def _shape_override_rgb(ctrl):
    """First shape node's overrideColorR/G/B triple for `ctrl` — the exact
    plug world._apply_color (modules/world.py) writes RGB into via
    overrideRGBColors — for direct ctrl-to-ctrl color comparison without
    hardcoding any CTRL_COLOR_RGB palette values in a test."""
    import maya.cmds as cmds
    shapes = cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []
    assert shapes, f'{ctrl!r}: no shape node found'
    s = shapes[0]
    return (cmds.getAttr(f'{s}.overrideColorR'),
            cmds.getAttr(f'{s}.overrideColorG'),
            cmds.getAttr(f'{s}.overrideColorB'))


def _rgb_close(a, b, tol=1e-5):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def test_ikarm_fingers_ctrl_color_follows_group_ctrl_color():
    """Color fix (Adrian 2026-07-09 follow-up): fingers_ctrl (the
    fist-curl master) used to always build a fixed red — RIBBON_IK_ARM_CONTRACT's
    old 'fingers_ctrl_color' schema default — regardless of the arm's own
    side-derived group color (bug report: a blue-group left arm's fist
    ctrl built red, mismatching every other ctrl on that same arm).
    'fingers_ctrl_color' now defaults to '' (unset), which
    RibbonIKArmComponent.build resolves to THIS instance's own 'ctrl_color' —
    the exact option every other arm ctrl (FK/IK/PV/switch, see
    simple_ik.py's build()) reads for its own color — whenever no
    explicit override is authored.

    Proves, using empty finger_defs (no fingers needed to exercise the
    fist ctrl itself — build_fingers_ctrl always builds fingers_ctrl per
    its own docstring, "even when fingers is empty"):
      - Blue-group fixture (ctrl_color='blue'): fingers_ctrl's override
        RGB matches switch_ctrl's AND the palette's literal 'blue' —
        no more mismatched red.
      - Red-group fixture (ctrl_color='red'): same parity check, with a
        DIFFERENT resolved color than the blue fixture — proves the
        default is genuinely side-aware (reads whatever 'ctrl_color' the
        group actually has), not a second hardcoded constant standing in
        for the first.
      - An explicit 'fingers_ctrl_color' override still wins over a blue
        group — the per-ctrl override the OptionField's docstring
        promises stays intact.
    """
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.utils.maya.side_tokens import CTRL_COLOR_RGB

    # ─── Blue-group fixture — no explicit fingers_ctrl_color. ────────────
    _root, _shoulder, _elbow, wrist_l, _fj, _fo, _cid = _build_ikarm_with_fingers(
        'ikfcolorlf', (), extra_options={'ctrl_color': 'blue'})
    switch_l = f'{wrist_l}_IKFK_ctrl'
    fingers_l = f'{wrist_l}_fingers_ctrl'
    assert cmds.objExists(switch_l), f'{switch_l!r} missing'
    assert cmds.objExists(fingers_l), f'{fingers_l!r} missing'
    switch_l_rgb = _shape_override_rgb(switch_l)
    fingers_l_rgb = _shape_override_rgb(fingers_l)
    assert _rgb_close(fingers_l_rgb, switch_l_rgb), (
        f'fingers_ctrl RGB {fingers_l_rgb} != switch_ctrl RGB '
        f'{switch_l_rgb} on a blue-group build — fingers_ctrl no longer '
        f'follows the group\'s ctrl_color')
    assert _rgb_close(fingers_l_rgb, CTRL_COLOR_RGB['blue']), (
        f'fingers_ctrl RGB {fingers_l_rgb} != palette blue '
        f'{CTRL_COLOR_RGB["blue"]} on a blue-group build')
    fs_app.unbuild_modules()

    # ─── Red-group fixture — same check, different side color; proves
    # this isn't just a second hardcoded constant. ────────────────────────
    _root, _shoulder, _elbow, wrist_r, _fj, _fo, _cid = _build_ikarm_with_fingers(
        'ikfcolorrt', (), extra_options={'ctrl_color': 'red'})
    switch_r = f'{wrist_r}_IKFK_ctrl'
    fingers_r = f'{wrist_r}_fingers_ctrl'
    assert cmds.objExists(switch_r), f'{switch_r!r} missing'
    assert cmds.objExists(fingers_r), f'{fingers_r!r} missing'
    switch_r_rgb = _shape_override_rgb(switch_r)
    fingers_r_rgb = _shape_override_rgb(fingers_r)
    assert _rgb_close(fingers_r_rgb, switch_r_rgb), (
        f'fingers_ctrl RGB {fingers_r_rgb} != switch_ctrl RGB '
        f'{switch_r_rgb} on a red-group build')
    assert _rgb_close(fingers_r_rgb, CTRL_COLOR_RGB['red']), (
        f'fingers_ctrl RGB {fingers_r_rgb} != palette red '
        f'{CTRL_COLOR_RGB["red"]} on a red-group build')
    assert not _rgb_close(fingers_r_rgb, fingers_l_rgb), (
        'fingers_ctrl built the SAME color on both a blue-group and a '
        'red-group build — the default is not actually side-aware '
        '(looks hardcoded)')
    fs_app.unbuild_modules()

    # ─── Explicit override still wins over a blue group. ──────────────────
    _root, _shoulder, _elbow, wrist_ov, _fj, _fo, _cid = _build_ikarm_with_fingers(
        'ikfcolorov', (),
        extra_options={'ctrl_color': 'blue', 'fingers_ctrl_color': 'green'})
    fingers_ov = f'{wrist_ov}_fingers_ctrl'
    assert cmds.objExists(fingers_ov), f'{fingers_ov!r} missing'
    fingers_ov_rgb = _shape_override_rgb(fingers_ov)
    assert _rgb_close(fingers_ov_rgb, CTRL_COLOR_RGB['green']), (
        f'fingers_ctrl RGB {fingers_ov_rgb} != palette green override '
        f'{CTRL_COLOR_RGB["green"]} — an explicit fingers_ctrl_color must '
        f'still win over the group\'s ctrl_color')
    fs_app.unbuild_modules()


def test_limb_fragment_skeleton_only_loads_clean():
    """PLAN.md Task 5.2 core: an in-memory LimbFragment with
    components=[] loads via apply_limb_fragment onto a plain joint with
    no error, creates only joints — no component network nodes at all."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(name='sklonly_root')
    cmds.select(clear=True)

    nodes.create_registry('sklonly_bp')
    nodes.set_registry_root_joint(root)
    _add_world_component('sklonly_world', root)

    frag = _finger_fragment('sklonly')
    before_components = set(nodes.get_all_component_nodes())

    apply_limb_fragment(frag, root)

    assert cmds.objExists('sklonly_metacarpal') and cmds.objExists('sklonly_03'), (
        'skeleton-only fragment did not create its joints')
    parent = cmds.listRelatives('sklonly_metacarpal', parent=True) or [None]
    assert parent[0] == root, (
        f'sklonly_metacarpal parented under {parent[0]!r}, expected {root!r}')

    after_components = set(nodes.get_all_component_nodes())
    assert after_components == before_components, (
        'a skeleton-only fragment created component network node(s): '
        f'{after_components - before_components}')
def test_limb_auto_add_finger_joins_existing_ikarm_fist():
    """PLAN.md Task 5.3 core, end to end (Task 2.3 update: the limb node
    is the membership home now, not an option): dropping the finger limb
    onto a wrist whose owning component is an RibbonIKArm connects the new
    chain into that arm's LIMB finger_roots[] (heuristic curl_excluded
    matches metacarpal_excluded), and — on rebuild — fingers_ctrl
    actually curls the NEW finger too (it 'joins the fist'), while the
    new metacarpal stays excluded."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    root, shoulder, _elbow, wrist, fingers_joints, _fingers_option, cid = (
        _build_ikarm_with_fingers('ikauto', _UE5_FINGER_DEFS,
                                  extra_options={'curl_axis': 'z'}))

    fs_app.unbuild_modules()
    # apply_limb_fragment refreshes the Armature, which requires a
    # resolvable registry root joint (_build_ikarm_with_fingers's own
    # scene never sets one — it only ever goes through build_modules,
    # which merely warns and proceeds on a missing root joint).
    nodes.set_registry_root_joint(root)

    limb = ln.find_limb_for_joint(shoulder)
    assert limb, 'expected to find the limb node for this arm'
    roots_before = set(ln.list_finger_roots(limb))
    assert len(roots_before) == len(_UE5_FINGER_DEFS)

    frag = _finger_fragment('ikauto_extra')
    apply_limb_fragment(frag, wrist)

    for j in ('ikauto_extra_metacarpal', 'ikauto_extra_01',
              'ikauto_extra_02', 'ikauto_extra_03'):
        assert cmds.objExists(j), f'{j!r} missing after finger limb drop'

    new_root = 'ikauto_extra_metacarpal'
    roots_after = set(ln.list_finger_roots(limb))
    assert new_root in roots_after, (
        f'new finger chain not auto-registered into the limb\'s '
        f'finger_roots[]: {sorted(roots_after)}')
    assert roots_after == roots_before | {new_root}, (
        f'auto-add must APPEND, never replace: before={sorted(roots_before)} '
        f'after={sorted(roots_after)}')

    new_chain = hc.walk_finger_chain(new_root)
    assert new_chain == ['ikauto_extra_metacarpal', 'ikauto_extra_01',
                         'ikauto_extra_02', 'ikauto_extra_03'], new_chain
    assert set(ln.list_curl_excluded(limb)) & set(new_chain) == {new_root}, (
        f'auto-added metacarpal should be curl-excluded (heuristic '
        f'match), and only it: {ln.list_curl_excluded(limb)}')

    fs_app.build_modules()

    fingers_ctrl = f'{wrist}_fingers_ctrl'
    assert cmds.objExists(fingers_ctrl)
    new_phalange = 'ikauto_extra_01'
    new_mc = 'ikauto_extra_metacarpal'
    assert cmds.objExists(f'{new_phalange}_ctrl'), (
        'newly auto-added finger did not get FK ctrls on rebuild')

    rot0 = cmds.getAttr(f'{new_phalange}.rotateZ')
    rot0_mc = cmds.getAttr(f'{new_mc}.rotateZ')
    cmds.setAttr(f'{fingers_ctrl}.rz', 25.0)
    rot1 = cmds.getAttr(f'{new_phalange}.rotateZ')
    rot1_mc = cmds.getAttr(f'{new_mc}.rotateZ')

    assert abs(rot1 - rot0) > 1.0, (
        f'auto-added finger phalange {new_phalange!r} did not curl with '
        f'the master — it did not join the fist')
    assert abs(rot1_mc - rot0_mc) < 1e-6, (
        f'auto-added finger metacarpal {new_mc!r} curled despite its '
        f'heuristic curl_excluded membership')

    fs_app.unbuild_modules()

    # Ship-gate review coverage gap: this is the only test exercising
    # apply_limb_fragment auto-add -> rebuild -> unbuild, and it used to
    # end at the line above with zero post-unbuild assertions — unlike
    # every other lifecycle stage in this suite (test_ikarm_unbuild_
    # leaves_zero_orphans, test_ikarm_ribbon_unbuild_leaves_zero_orphans,
    # test_ikarm_roll_unbuild_leaves_zero_orphans, test_ikarm_finger_
    # bind_joint_constraint_sweep_on_unbuild), which all assert rig_grp
    # is gone and/or constraint counts return to (0, 0). The auto-add
    # path uniquely combines apply_limb_fragment's fresh joint creation +
    # a limb finger_roots[] mutation + a second build — the one
    # lifecycle combination never checked for leaks before this addition.
    assert not cmds.objExists('rig_grp'), (
        'rig_grp survived unbuild on the limb-auto-add -> rebuild -> '
        'unbuild path')

    all_finger_joints = ([j for chain in fingers_joints for j in chain] +
                         ['ikauto_extra_metacarpal', 'ikauto_extra_01',
                          'ikauto_extra_02', 'ikauto_extra_03'])
    leaked = {j: c for j, c in
              _finger_bind_joint_constraints(all_finger_joints).items()
              if c != (0, 0)}
    assert not leaked, (
        f'finger bind joint(s) (original + auto-added) still have '
        f'constraints after unbuild on the limb-auto-add path: {leaked}')


def test_limb_auto_add_no_ikarm_is_noop():
    """SPEC 3.7 point 2's own wording: 'Dropping where there's no RibbonIKArm =
    no-op.' Drops the finger limb onto a joint owned by a real component
    that is NOT an RibbonIKArm (World) — must not raise, must not write a
    'fingers' option onto that component."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(name='noikarm_root')
    cmds.select(clear=True)

    nodes.create_registry('noikarm_bp')
    nodes.set_registry_root_joint(root)
    _add_world_component('noikarm_world', root)

    frag = _finger_fragment('noikarm_f')
    apply_limb_fragment(frag, root)  # must not raise

    assert cmds.objExists('noikarm_f_metacarpal'), 'joints should still be created'
    world_node = nodes.find_component_for_joint(root)
    assert world_node, 'expected root to still be owned by the World component'
    assert nodes.get_component_type(world_node) == 'World'
    opts = nodes.get_component_options(world_node)
    assert 'fingers' not in opts, (
        "auto-add hook wrote a 'fingers' option onto a non-RibbonIKArm component")


def test_limb_auto_add_only_on_wrist_not_shoulder_or_elbow():
    """P5 review finding (major): _auto_register_finger_chain used to
    resolve the owning component via nodes.find_component_for_joint,
    which matches ANY of an RibbonIKArm's 3 primary joints (shoulder, elbow,
    wrist all connect into the SAME joints[] message-multi) — with no
    check that target_joint specifically IS the wrist. Dropping a
    skeleton-only limb onto the SHOULDER or ELBOW of a built-out RibbonIKArm
    must be a no-op for finger auto-registration (the fragment's joints
    still get created — apply_limb_fragment itself places no such
    restriction on a components=[] fragment — but the new chain must NOT
    join the limb's finger_roots[] — Task 2.3: the membership home
    moved, the no-op guarantee didn't), exactly like dropping on a
    non-RibbonIKArm-owned joint already is (test_limb_auto_add_no_ikarm_
    is_noop above). Only a drop directly on the wrist (joints[-1]) may
    auto-register — covered by
    test_limb_auto_add_finger_joins_existing_ikarm_fist."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment

    root, shoulder, elbow, wrist, _fingers_joints, _fingers_option_before, cid = (
        _build_ikarm_with_fingers('ikwristonly', _UE5_FINGER_DEFS))

    fs_app.unbuild_modules()
    nodes.set_registry_root_joint(root)

    cnode = nodes.find_component_node_by_id(cid)
    assert cnode, f'expected a component node for {cid!r}'
    limb = ln.find_limb_for_joint(shoulder)
    assert limb, 'expected to find the limb node for this arm'
    roots_before = set(ln.list_finger_roots(limb))

    for label, target in (('shoulder', shoulder), ('elbow', elbow)):
        frag = _finger_fragment(f'ikwristonly_{label}')
        apply_limb_fragment(frag, target)  # must not raise

        for j in (f'ikwristonly_{label}_metacarpal', f'ikwristonly_{label}_01',
                 f'ikwristonly_{label}_02', f'ikwristonly_{label}_03'):
            assert cmds.objExists(j), (
                f'{j!r} missing after finger limb drop on {label}')

        roots_now = set(ln.list_finger_roots(limb))
        assert roots_now == roots_before, (
            f'dropping a finger limb on the {label} joint (not the wrist) '
            f'must NOT auto-register it into the limb\'s finger_roots[]: '
            f'before={sorted(roots_before)} after={sorted(roots_now)}')
        new_root = f'ikwristonly_{label}_metacarpal'
        assert new_root not in roots_now, (
            f'{new_root!r} was wrongly auto-registered from a drop on '
            f'the {label} joint')

    # The wrist itself still works (contrast case — a positive control
    # proving this test isn't just vacuously no-op-ing every drop).
    frag = _finger_fragment('ikwristonly_wrist')
    apply_limb_fragment(frag, wrist)
    assert 'ikwristonly_wrist_metacarpal' in set(ln.list_finger_roots(limb)), (
        'a drop directly on the wrist should still auto-register — '
        'positive control failed, this test would be vacuous otherwise')


def test_ikarm_p5_regression_suites_stay_green():
    """Phase 1/2/3/4 behavior on an arm whose fingers include the
    fist-curl master sharing the same build()/unbuild() — a regression
    guard that P5's curl wiring doesn't disturb the existing ribbon/roll/
    finger-FK subsystems."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

    _root, shoulder, elbow, wrist, fingers_joints, _fingers_option, _cid = (
        _build_ikarm_with_fingers('ikp5regress', _UE5_FINGER_DEFS,
                                  extra_options={'curl_axis': 'z'}))

    upper_chain = rc.ribbon_segment_chain_name(shoulder, elbow)
    forearm_chain = rc.ribbon_segment_chain_name(elbow, wrist)
    for chain in (upper_chain, forearm_chain):
        assert cmds.objExists(f'{chain}_ribbon_surf'), (
            f'{chain}: ribbon surface missing with fingers_ctrl attached')

    switch_ctrl = f'{wrist}_IKFK_ctrl'
    assert cmds.attributeQuery('ik_fk_blend', node=switch_ctrl, exists=True)

    for chain in fingers_joints:
        for j in chain:
            assert cmds.objExists(f'{j}_ctrl'), f'{j}_ctrl missing'

    assert cmds.objExists(f'{wrist}_fingers_ctrl')

    fs_app.unbuild_modules()
    assert not cmds.objExists('rig_grp'), (
        'rig_grp survived unbuild on an arm with a fist-curl master attached')


def test_ikarm_fingers_ctrl_orientation_matches_wrist_and_offset():
    """P6/P7 gate (Adrian tweak 1, 2026-07-09; position updated P7,
    2026-07-09): fingers_ctrl's OFFSET parent (NOT fingers_ctrl itself —
    see build_fingers_ctrl's docstring on why the curl wiring needs
    ctrl's LOCAL rotation to stay at 0) carries the wrist joint's world
    ORIENTATION exactly (unchanged since P6), and sits EXACTLY AT the
    wrist joint (P7: the old fixed +10-unit object-space nudge along the
    offset's own local Y is gone — the standoff now comes from
    fingers_ctrl's own shape, the curve-o-matic 'sine_handle' library
    shape, whose own +Y handle geometry extends to ~4.68 in the ctrl's
    local space)."""
    import math
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikfcoffset')

    nodes.create_registry('ikfcoffset_bp')
    _add_world_component('ikfcoffset_world', root)
    nodes.create_component_node(
        component_id='ikfcoffset_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    ctrl_offset = f'{wrist}_fingers_ctrl_offset'
    ctrl = f'{wrist}_fingers_ctrl'
    assert cmds.objExists(ctrl_offset), f'{ctrl_offset!r} missing'
    assert cmds.objExists(ctrl), f'{ctrl!r} missing'

    def _world_quat(node):
        m = om.MMatrix(cmds.xform(node, q=True, ws=True, matrix=True))
        return om.MTransformationMatrix(m).rotation(asQuaternion=True)

    wrist_quat = _world_quat(wrist)
    offset_quat = _world_quat(ctrl_offset)
    delta = offset_quat * wrist_quat.inverse()
    w = max(-1.0, min(1.0, delta.w))
    delta_deg = math.degrees(2.0 * math.acos(abs(w)))
    assert delta_deg < 0.5, (
        f'{ctrl_offset!r} orientation does not match the wrist joint '
        f'{wrist!r}: {delta_deg} deg off')

    # ctrl itself stays at LOCAL rotate=0 — the orientation match above
    # must be carried entirely by the offset parent (CRITICAL per spec:
    # the curl wiring reads ctrl's LOCAL rotation as the master signal).
    for a in ('rx', 'ry', 'rz'):
        v = cmds.getAttr(f'{ctrl}.{a}')
        assert abs(v) < 1e-6, (
            f'{ctrl}.{a} = {v}, expected 0 — orientation must live on '
            f'{ctrl_offset!r}, not baked onto the ctrl itself')

    # Position: ctrl_offset sits AT the wrist joint — no positional nudge
    # at all (P7: the old +10-unit standoff moved into the ctrl's shape).
    wrist_pos = om.MVector(*cmds.xform(wrist, q=True, ws=True, t=True))
    offset_pos = om.MVector(*cmds.xform(ctrl_offset, q=True, ws=True, t=True))
    delta_pos = offset_pos - wrist_pos
    assert delta_pos.length() < 1e-3, (
        f'{ctrl_offset!r} is {delta_pos.length()} units from the wrist '
        f'{wrist!r}, expected 0.0 (AT the wrist anchor)')

    # Shape resolution: the fist ctrl's default shape actually resolved to
    # 'sine_handle' — a cheap CV-level proof, not just "no exception was
    # raised". sine_handle's library data tops out at local Y=4.6761
    # (base at the origin, handle extending +Y); build_fingers_ctrl scales
    # every CV by radius*2.0 (radius >= the wrist joint's own .radius, so
    # never shrinks below the library value), so max CV Y > 4 proves the
    # sine_handle shape resolved rather than some other default (e.g. the
    # old 'circle').
    shape_data = com.serialize_shape(ctrl)
    all_cvs = [cv for shape in shape_data.get('shapes', []) for cv in shape['cvs']]
    assert all_cvs, f'{ctrl!r}: no CVs found on its shape node'
    max_local_y = max(cv[1] for cv in all_cvs)
    assert max_local_y > 4.0, (
        f'{ctrl!r} max CV local Y is {max_local_y}, expected > 4.0 — the '
        f"default 'sine_handle' shape (max Y ~4.68 in its library data) "
        f'does not appear to have resolved')

    fs_app.unbuild_modules()


def test_ikarm_fingers_ctrl_offset_mirror_symmetry():
    """MIRROR gate (Adrian tweak 1, continued; position updated P7,
    2026-07-09): building the 'rt' side as a REAL mirrorJoint reflection
    of 'lf' (mirrorYZ + mirrorBehavior — the exact call branch_ops.py's
    real right-side-rig tool uses; KS pre-mirrored joint frames), not a
    second identical-coordinate build. fingers_ctrl_offset now sits AT
    its own wrist anchor (P7 removed the +10-unit positional nudge) on
    BOTH sides — position mirrors trivially since there is no offset
    vector left to reflect — but the offset's ORIENTATION match to its
    own wrist joint must still hold on the geometrically mirrored 'rt'
    side, not just 'lf' (a real risk point: mirrorJoint's negative-scale
    reflection is exactly the kind of transform that can silently break a
    naive orientation-match convention)."""
    import math
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikfcmirlf')

    created = cmds.mirrorJoint(root, mirrorYZ=True, mirrorBehavior=True,
                               searchReplace=('lf', 'rt'))
    rt_root = created[0]
    rt_shoulder = rt_root.replace('_root', '_shoulder')
    rt_elbow = rt_root.replace('_root', '_elbow')
    rt_wrist = rt_root.replace('_root', '_wrist')
    for j in (rt_shoulder, rt_elbow, rt_wrist):
        assert cmds.objExists(j), f'mirrorJoint did not produce {j!r}'

    nodes.create_registry('ikfcmir_bp')
    _add_world_component('ikfcmirlf_world', root)
    nodes.create_component_node(
        component_id='ikfcmirlf_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='lf',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    _add_world_component('ikfcmirrt_world', rt_root)
    nodes.create_component_node(
        component_id='ikfcmirrt_C0', component_type='RibbonIKArm',
        joints=[rt_shoulder, rt_elbow, rt_wrist], parent_plug='', side='rt',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    def _world_quat(node):
        m = om.MMatrix(cmds.xform(node, q=True, ws=True, matrix=True))
        return om.MTransformationMatrix(m).rotation(asQuaternion=True)

    for w in (wrist, rt_wrist):
        ctrl_offset = f'{w}_fingers_ctrl_offset'
        assert cmds.objExists(ctrl_offset), f'{ctrl_offset!r} missing'

        # Position: AT the wrist, on both sides — no offset vector left
        # to mirror.
        wp = om.MVector(*cmds.xform(w, q=True, ws=True, t=True))
        op = om.MVector(*cmds.xform(ctrl_offset, q=True, ws=True, t=True))
        dist = (op - wp).length()
        assert dist < 1e-3, (
            f'{ctrl_offset!r} is {dist} units from {w!r}, expected 0.0 '
            f'(AT the wrist anchor) on the mirrored side too')

        # Orientation: still matches this side's OWN wrist joint exactly,
        # under the real mirrorJoint reflection.
        wrist_quat = _world_quat(w)
        offset_quat = _world_quat(ctrl_offset)
        delta = offset_quat * wrist_quat.inverse()
        dw = max(-1.0, min(1.0, delta.w))
        delta_deg = math.degrees(2.0 * math.acos(abs(dw)))
        assert delta_deg < 0.5, (
            f'{ctrl_offset!r} orientation does not match its own wrist '
            f'joint {w!r} on the mirrored side: {delta_deg} deg off')

    fs_app.unbuild_modules()


def test_ikarm_pv_ctrl_aims_at_fk_elbow_and_tracks_fk_poses():
    """P6 gate (Adrian tweak 2, 2026-07-09), UPDATED 2026-07-09 follow-up:
    the original ask targeted the BLEND-chain elbow but that provably froze
    the arm's own IK solve (SimpleIK's Schleifer pin mechanism used to read
    pv_ctrl's FULL worldMatrix, so driving pv_ctrl.rotate from anything
    downstream of this arm's own solve closed a same-frame DG cycle —
    reproduced with cycleCheck forced ON and Evaluation Manager forced OFF,
    so it was real, not an EM artifact). Task 1 fixed the root cause
    (simple_ik.py's `_build_ctrl_world_position_reader` — the pin distances
    now read pv_ctrl's world POSITION only, never its worldMatrix, so
    pv_ctrl.rotate carries no DG edge into them), which retired the
    FK-chain-elbow workaround this test originally proved: SimpleIK.build()
    now targets instance._blend_chain[1] (the TRUE blend elbow) directly.
    Function name kept for history — see hc.aim_pv_at_mid's docstring for
    the full empirical writeup and its resolution.

    This test proves: (1) pv_ctrl's +X aims at the BLEND-chain elbow across
    MULTIPLE poses in BOTH FK and IK mode (live tracking in both — the
    behavior originally asked for, and one the retired FK-chain-elbow
    target could never provide in IK mode), (2) rotate channels are locked
    + non-keyable (the constraint keeps driving after the lock), and (3)
    THE CRITICAL REGRESSION GUARD — moving the IK ctrl still bends the
    elbow by a substantial, non-vacuous amount (this is exactly the
    assertion that caught the pre-Task-1 blend-elbow-target cycle: it
    silently zeroed to 0.00 deg with that wiring)."""
    import math
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikpvaim')

    nodes.create_registry('ikpvaim_bp')
    _add_world_component('ikpvaim_world', root)
    nodes.create_component_node(
        component_id='ikpvaim_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    pv_ctrl = f'{elbow}_PV_ctrl'
    elbow_blend = f'{elbow}_blend'
    switch_ctrl = f'{wrist}_IKFK_ctrl'
    ik_ctrl = f'{wrist}_IK_ctrl'
    assert cmds.objExists(pv_ctrl), f'{pv_ctrl!r} missing'
    assert cmds.objExists(elbow_blend), f'{elbow_blend!r} missing'

    # Rotate channels locked + non-keyable (constraint keeps driving).
    for a in ('rx', 'ry', 'rz'):
        assert cmds.getAttr(f'{pv_ctrl}.{a}', lock=True), (
            f'{pv_ctrl}.{a} should be locked after aim_pv_at_mid')
        assert not cmds.getAttr(f'{pv_ctrl}.{a}', keyable=True), (
            f'{pv_ctrl}.{a} should not be keyable after aim_pv_at_mid')

    def _aim_dot():
        m = cmds.xform(pv_ctrl, q=True, ws=True, matrix=True)
        aim_world = [m[0], m[1], m[2]]  # local +X row
        mag = sum(c * c for c in aim_world) ** 0.5
        aim_world = [c / mag for c in aim_world]
        pv_pos = cmds.xform(pv_ctrl, q=True, ws=True, t=True)
        elbow_pos = cmds.xform(elbow_blend, q=True, ws=True, t=True)
        to_elbow = [elbow_pos[i] - pv_pos[i] for i in range(3)]
        d = sum(c * c for c in to_elbow) ** 0.5
        to_elbow = [c / d for c in to_elbow]
        return sum(a * b for a, b in zip(aim_world, to_elbow))

    # Bind pose first — must already be aimed correctly right after build.
    assert _aim_dot() > 0.999, f'bind pose: pv aim dot={_aim_dot()}'

    # FK mode, multiple poses — the aim must LIVE-TRACK the blend elbow.
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 0.0)
    for rz in (15.0, -20.0, 35.0):
        cmds.setAttr(f'{elbow}_FK_ctrl.rotateZ', rz)
        dot = _aim_dot()
        assert dot > 0.999, f'FK pose rz={rz}: pv aim dot={dot}'
    cmds.setAttr(f'{elbow}_FK_ctrl.rotateZ', 0.0)

    # IK mode, multiple poses — the aim must ALSO live-track the blend
    # elbow here. This is exactly the tracking the retired FK-chain-elbow
    # target could never provide (it holds at bind pose in IK mode) — the
    # whole point of Task 1's fix + Task 2's promotion.
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 1.0)
    for dx, dy, dz in ((0.4, -0.3, 0.2), (-0.5, 0.2, 0.3), (0.2, 0.4, -0.4)):
        cmds.xform(ik_ctrl, ws=True, relative=True, t=(dx, dy, dz))
        dot = _aim_dot()
        assert dot > 0.999, (
            f'IK pose delta=({dx},{dy},{dz}): pv aim dot={dot}')

    # ── CRITICAL REGRESSION GUARD: the elbow must still bend under an IK
    # ctrl move with this aim wired. This is the exact check that caught
    # the pre-Task-1 blend-elbow-target cycle (elbow_delta silently read
    # 0.0000 deg with that wiring, vs. a real response here). ──────────
    def _world_quat(node):
        m = om.MMatrix(cmds.xform(node, q=True, ws=True, matrix=True))
        return om.MTransformationMatrix(m).rotation(asQuaternion=True)

    elbow_quat_before = _world_quat(elbow)
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(0.0, -1.0, 0.5))
    elbow_quat_after = _world_quat(elbow)
    delta = elbow_quat_after * elbow_quat_before.inverse()
    w = max(-1.0, min(1.0, delta.w))
    elbow_delta_deg = math.degrees(2.0 * math.acos(abs(w)))
    assert elbow_delta_deg > 1.0, (
        f'elbow did not bend under an IK ctrl move with the PV aim wired '
        f'({elbow_delta_deg} deg) — this is the exact symptom of the '
        f'pre-Task-1 blend-elbow-target cycle (hc.aim_pv_at_mid docstring); '
        f'Task 1\'s pin fix must be intact for the blend-elbow target to '
        f'stay cycle-free')

    fs_app.unbuild_modules()


def test_ikarm_pv_ctrl_aim_no_cycle_and_unbuild_clean():
    """P6 gate (Adrian tweak 2, continued), UPDATED 2026-07-09 follow-up:
    the pv_ctrl aimConstraint (hc.aim_pv_at_mid, now targeting
    instance._blend_chain[1] — the TRUE blend elbow, wired from
    SimpleIKComponent.build() itself since Task 1's pin fix made it safe;
    see that function's docstring for the empirical history and its
    resolution) introduces NO dependency cycle — captured via Maya's command-
    output callback (the same channel Script Editor/cycle warnings route
    through, MGlobal::displayWarning) across build + multiple pose
    evaluations in both IK and FK — and cascades away with rig_grp on
    unbuild (the aimConstraint is a DAG child of pv_ctrl, itself a
    rig_grp descendant via ik_grp/fab_controls_grp) WITHOUT needing its
    own message-tracking bucket, unlike the roll joints' own aimConstraint
    (which lives OUTSIDE rig_grp on a bind-joint child and DOES need one —
    see rc.build_roll_joint). Also re-proves the elbow-still-bends
    regression guard independently, since a false 'cycle' substring match
    on the FIXTURE'S OWN joint-name prefix previously masked this exact
    class of bug in an earlier draft of this test (prefix deliberately
    avoids the substring 'cycle' here)."""
    import maya.cmds as cmds
    import maya.api.OpenMaya as om2
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('ikpvloop')

    nodes.create_registry('ikpvloop_bp')
    _add_world_component('ikpvloop_world', root)
    nodes.create_component_node(
        component_id='ikpvloop_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )

    captured = []

    def _on_output(message, msg_type, client_data):
        captured.append(message)

    cb_id = om2.MCommandMessage.addCommandOutputCallback(_on_output)
    aim_con = None
    try:
        fs_app.build_modules()

        pv_ctrl = f'{elbow}_PV_ctrl'
        assert cmds.objExists(pv_ctrl), f'{pv_ctrl!r} missing'
        aim_cons = cmds.listRelatives(pv_ctrl, type='aimConstraint') or []
        assert len(aim_cons) == 1, (
            f'expected exactly one aimConstraint on {pv_ctrl!r}, got {aim_cons}')
        aim_con = aim_cons[0]

        ik_ctrl = f'{wrist}_IK_ctrl'
        switch_ctrl = f'{wrist}_IKFK_ctrl'
        for dx, dy, dz in ((0.4, -0.2, 0.3), (-0.3, 0.4, -0.2)):
            cmds.xform(ik_ctrl, ws=True, relative=True, t=(dx, dy, dz))
            cmds.xform(pv_ctrl, q=True, ws=True, matrix=True)  # force eval
        cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 0.0)
        for rz in (20.0, -25.0):
            cmds.setAttr(f'{elbow}_FK_ctrl.rotateZ', rz)
            cmds.xform(pv_ctrl, q=True, ws=True, matrix=True)  # force eval
    finally:
        om2.MMessage.removeCallback(cb_id)

    cycle_msgs = [m for m in captured if 'cycle' in m.lower()]
    assert not cycle_msgs, (
        f'cycle warning(s) detected during build/eval: {cycle_msgs}')

    # Independent re-proof of the elbow-still-bends regression guard (see
    # test_ikarm_pv_ctrl_aims_at_fk_elbow_and_tracks_fk_poses) — the IK
    # moves above already happened in FK mode's shadow (ik_fk_blend was
    # flipped to 0.0 afterward), so re-verify fresh in IK mode here.
    cmds.setAttr(f'{wrist}_IKFK_ctrl.ik_fk_blend', 1.0)
    import math
    def _world_quat(node):
        m = om2.MMatrix(cmds.xform(node, q=True, ws=True, matrix=True))
        return om2.MTransformationMatrix(m).rotation(asQuaternion=True)
    q0 = _world_quat(elbow)
    cmds.xform(f'{wrist}_IK_ctrl', ws=True, relative=True, t=(0.1, -0.1, 0.1))
    q1 = _world_quat(elbow)
    d = q1 * q0.inverse()
    w = max(-1.0, min(1.0, d.w))
    ang = math.degrees(2.0 * math.acos(abs(w)))
    assert ang > 0.1, (
        f'elbow did not respond to a further IK ctrl move ({ang} deg) — '
        f'possible cycle-induced freeze not caught by the message capture')

    fs_app.unbuild_modules()
    assert not cmds.objExists('rig_grp'), 'rig_grp survived unbuild'
    assert aim_con and not cmds.objExists(aim_con), (
        f'pv_ctrl aimConstraint {aim_con!r} survived unbuild — should '
        f'have cascaded with rig_grp (pv_ctrl is a rig_grp descendant), '
        f'per hc.aim_pv_at_mid\'s docstring; if this fails, the fix is to '
        f'track it in a dedicated bucket like the roll joints\' own '
        f'aimConstraint.')

    leaked_aim_constraints = cmds.ls(type='aimConstraint') or []
    assert not leaked_aim_constraints, (
        f'aimConstraint nodes leaked after unbuild: {leaked_aim_constraints}')


def test_switch_ctrl_orientation_matches_end_joint_and_offset_z():
    """Task 3 gate (Adrian, 2026-07-09: 'love the fist ctrl orientation —
    same on the IK/FK controls, arm and leg, offset 10 in Z'). SimpleIK's
    IK/FK switch ctrl (build's `switch_offset`/`switch_ctrl`) now follows
    the exact fist-ctrl pattern (_limb_common.build_fingers_ctrl): the
    OFFSET parent carries the end joint's (wrist) world ORIENTATION
    (matchTransform), gets a +10-unit object-space nudge along its own
    local Z, and `switch_ctrl` itself stays at local rotate=0.

    GUARD THE INTERACTION: fingers_ctrl (the fist-curl master) anchors
    DAG-parented under this exact switch ctrl (_limb_common.
    build_fingers_ctrl: `cmds.parent(ctrl_offset, wrist_ctrl)`). This test
    proves the switch ctrl's reorientation does NOT perturb fingers_ctrl's
    own world placement — build_fingers_ctrl re-snaps fingers_ctrl_offset's
    world transform to the wrist JOINT directly (a second matchTransform
    call after parenting), so its world orientation/position must still
    match the wrist bind joint exactly (wrist orientation, +10 wrist-local
    Y) regardless of what orientation its new DAG parent (the switch ctrl)
    now carries — an explicit regression assertion on the fist ctrl's
    world transform, not just an unrelated pass/fail on its own suite."""
    import math
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('swctrl')

    nodes.create_registry('swctrl_bp')
    _add_world_component('swctrl_world', root)
    nodes.create_component_node(
        component_id='swctrl_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    switch_offset = f'{wrist}_IKFK_ctrl_offset'
    switch_ctrl = f'{wrist}_IKFK_ctrl'
    assert cmds.objExists(switch_offset), f'{switch_offset!r} missing'
    assert cmds.objExists(switch_ctrl), f'{switch_ctrl!r} missing'

    def _world_quat(node):
        m = om.MMatrix(cmds.xform(node, q=True, ws=True, matrix=True))
        return om.MTransformationMatrix(m).rotation(asQuaternion=True)

    # Orientation: switch_offset matches the wrist joint's world rotation.
    wrist_quat = _world_quat(wrist)
    offset_quat = _world_quat(switch_offset)
    delta = offset_quat * wrist_quat.inverse()
    w = max(-1.0, min(1.0, delta.w))
    delta_deg = math.degrees(2.0 * math.acos(abs(w)))
    assert delta_deg < 0.5, (
        f'{switch_offset!r} orientation does not match the wrist joint '
        f'{wrist!r}: {delta_deg} deg off')

    # switch_ctrl itself stays at LOCAL rotate=0 — orientation lives on
    # the offset parent only (matches the fist ctrl's own contract).
    for a in ('rx', 'ry', 'rz'):
        v = cmds.getAttr(f'{switch_ctrl}.{a}')
        assert abs(v) < 1e-6, (
            f'{switch_ctrl}.{a} = {v}, expected 0 — orientation must live '
            f'on {switch_offset!r}, not baked onto the ctrl itself')

    # Position: switch_offset sits exactly 10 units from the wrist along
    # the wrist's OWN local Z.
    wrist_pos = om.MVector(*cmds.xform(wrist, q=True, ws=True, t=True))
    offset_pos = om.MVector(*cmds.xform(switch_offset, q=True, ws=True, t=True))
    wrist_m = cmds.xform(wrist, q=True, ws=True, matrix=True)
    wrist_local_z = om.MVector(wrist_m[8], wrist_m[9], wrist_m[10]).normal()
    delta_pos = offset_pos - wrist_pos
    assert abs(delta_pos.length() - 10.0) < 1e-3, (
        f'{switch_offset!r} is {delta_pos.length()} units from the wrist '
        f'{wrist!r}, expected exactly 10.0')
    dot = delta_pos.normal() * wrist_local_z
    assert dot > 0.999, (
        f'{switch_offset!r} offset direction does not align with the '
        f'wrist\'s local Z axis: dot={dot}')

    # ── GUARD THE INTERACTION: fingers_ctrl (fist ctrl) world transform
    # is UNCHANGED by the switch ctrl's reorientation — explicit regression
    # assertion, not just "its own suite still passes". Position baseline
    # updated P7 (2026-07-09): fingers_ctrl_offset sits AT the wrist anchor
    # now (the old +10-unit standoff moved into the ctrl's own shape), so
    # this guard checks distance ~0 rather than exactly 10.0. ────────────
    fingers_ctrl_offset = f'{wrist}_fingers_ctrl_offset'
    assert cmds.objExists(fingers_ctrl_offset), (
        f'{fingers_ctrl_offset!r} missing (fist ctrl must still build even '
        f'with fingers=[] — see RibbonIKArm.build\'s own comment)')
    fc_quat = _world_quat(fingers_ctrl_offset)
    fc_delta = fc_quat * wrist_quat.inverse()
    fc_w = max(-1.0, min(1.0, fc_delta.w))
    fc_delta_deg = math.degrees(2.0 * math.acos(abs(fc_w)))
    assert fc_delta_deg < 0.5, (
        f'{fingers_ctrl_offset!r} orientation drifted off the wrist joint '
        f'{wrist!r} after the switch ctrl reorientation: {fc_delta_deg} '
        f'deg off — the fist ctrl\'s world transform must be UNCHANGED')
    fc_pos = om.MVector(*cmds.xform(fingers_ctrl_offset, q=True, ws=True, t=True))
    fc_delta_pos = fc_pos - wrist_pos
    assert fc_delta_pos.length() < 1e-3, (
        f'{fingers_ctrl_offset!r} is {fc_delta_pos.length()} units from '
        f'the wrist, expected 0.0 (AT the wrist anchor — fist ctrl '
        f'placement must be unaffected by the switch ctrl reorientation)')

    fs_app.unbuild_modules()


def test_switch_ctrl_offset_mirror_symmetry():
    """Task 3 MIRROR gate: building the 'rt' side as a REAL mirrorJoint
    reflection of 'lf' (mirrorYZ + mirrorBehavior — KS pre-mirrored joint
    frames), the switch ctrl's +10-unit wrist-local-Z placement must land
    on the geometrically mirrored side too — same rationale as the fist
    ctrl's own mirror gate (test_ikarm_fingers_ctrl_offset_mirror_
    symmetry): an OBJECT-SPACE move along the offset's own local Z
    auto-mirrors for free."""
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('swmirlf')

    created = cmds.mirrorJoint(root, mirrorYZ=True, mirrorBehavior=True,
                               searchReplace=('lf', 'rt'))
    rt_root = created[0]
    rt_shoulder = rt_root.replace('_root', '_shoulder')
    rt_elbow = rt_root.replace('_root', '_elbow')
    rt_wrist = rt_root.replace('_root', '_wrist')
    for j in (rt_shoulder, rt_elbow, rt_wrist):
        assert cmds.objExists(j), f'mirrorJoint did not produce {j!r}'

    nodes.create_registry('swmir_bp')
    _add_world_component('swmirlf_world', root)
    nodes.create_component_node(
        component_id='swmirlf_C0', component_type='RibbonIKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='lf',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    _add_world_component('swmirrt_world', rt_root)
    nodes.create_component_node(
        component_id='swmirrt_C0', component_type='RibbonIKArm',
        joints=[rt_shoulder, rt_elbow, rt_wrist], parent_plug='', side='rt',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    fs_app.build_modules()

    def _offset_delta(w):
        wp = om.MVector(*cmds.xform(w, q=True, ws=True, t=True))
        op = om.MVector(
            *cmds.xform(f'{w}_IKFK_ctrl_offset', q=True, ws=True, t=True))
        return op - wp

    lf_delta = _offset_delta(wrist)
    rt_delta = _offset_delta(rt_wrist)

    assert abs(lf_delta.length() - 10.0) < 1e-3, (
        f'lf switch_ctrl_offset distance {lf_delta.length()} != 10.0')
    assert abs(rt_delta.length() - 10.0) < 1e-3, (
        f'rt switch_ctrl_offset distance {rt_delta.length()} != 10.0')

    for w, delta in ((wrist, lf_delta), (rt_wrist, rt_delta)):
        m = cmds.xform(w, q=True, ws=True, matrix=True)
        local_z = om.MVector(m[8], m[9], m[10]).normal()
        dot = delta.normal() * local_z
        assert dot > 0.999, (
            f'{w}: switch_ctrl_offset direction does not align with its '
            f'own local Z: dot={dot}')

    fs_app.unbuild_modules()


def test_ikleg_switch_ctrl_inherits_orientation_treatment():
    """Task 3 INHERITANCE gate: IKLeg never overrides SimpleIK's switch
    ctrl build, so the ankle's IK/FK switch ctrl must get the exact same
    wrist/ankle-orientation + offset-10-local-Z treatment as RibbonIKArm's wrist,
    purely through the subclass seam (SimpleIKComponent.build runs before
    any of IKLeg's own foot-frame/pivot-stack code, and IKLeg's ik_end_ctrl
    reposition only touches the FOOT ctrl, a different node, never the
    switch ctrl)."""
    import math
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(p=(12, 20, -7), name='swleg_root')
    hip = cmds.joint(p=(12, 20, -7), name='swleg_hip')
    knee = cmds.joint(p=(12, 15, -6), name='swleg_knee')
    ankle = cmds.joint(p=(12, 11, -7), name='swleg_ankle')
    ball = cmds.joint(p=(12, 10, -5), name='swleg_ball')
    cmds.select(clear=True)

    nodes.create_registry('swleg_bp')
    _add_world_component('swleg_world', root)
    nodes.create_component_node(
        component_id='swleg_C0', component_type='IKLeg',
        joints=[hip, knee, ankle, ball], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()

    switch_offset = f'{ankle}_IKFK_ctrl_offset'
    switch_ctrl = f'{ankle}_IKFK_ctrl'
    assert cmds.objExists(switch_offset), f'{switch_offset!r} missing'

    def _world_quat(node):
        m = om.MMatrix(cmds.xform(node, q=True, ws=True, matrix=True))
        return om.MTransformationMatrix(m).rotation(asQuaternion=True)

    ankle_quat = _world_quat(ankle)
    offset_quat = _world_quat(switch_offset)
    delta = offset_quat * ankle_quat.inverse()
    w = max(-1.0, min(1.0, delta.w))
    delta_deg = math.degrees(2.0 * math.acos(abs(w)))
    assert delta_deg < 0.5, (
        f'{switch_offset!r} orientation does not match the ankle joint '
        f'{ankle!r}: {delta_deg} deg off — IKLeg must inherit SimpleIK\'s '
        f'switch ctrl orientation treatment')

    ankle_pos = om.MVector(*cmds.xform(ankle, q=True, ws=True, t=True))
    offset_pos = om.MVector(*cmds.xform(switch_offset, q=True, ws=True, t=True))
    ankle_m = cmds.xform(ankle, q=True, ws=True, matrix=True)
    ankle_local_z = om.MVector(ankle_m[8], ankle_m[9], ankle_m[10]).normal()
    delta_pos = offset_pos - ankle_pos
    assert abs(delta_pos.length() - 10.0) < 1e-3, (
        f'{switch_offset!r} is {delta_pos.length()} units from the ankle '
        f'{ankle!r}, expected exactly 10.0')
    assert delta_pos.normal() * ankle_local_z > 0.999, (
        f'{switch_offset!r} offset direction does not align with the '
        f'ankle\'s local Z axis')

    for a in ('rx', 'ry', 'rz'):
        v = cmds.getAttr(f'{switch_ctrl}.{a}')
        assert abs(v) < 1e-6, (
            f'{switch_ctrl}.{a} = {v}, expected 0')

    fs_app.unbuild_modules()


def test_stretchy_default_on_fresh_build_and_opt_out_round_trips():
    """Task 4 (Adrian, 2026-07-09): stretch defaults ON for fresh builds;
    an explicit opt-out (options={'stretchy': False}) survives an
    unbuild -> rebuild cycle untouched. fs_app.build_modules only
    setdefault()s a MISSING options key from the contract (its own
    comment: 'The Contract is the single source of truth for defaults' —
    see simple_ik.py's `stretchy` OptionField), so an explicitly authored
    False is never silently overwritten by the new True default.

    Behavioral, not structural: pulls the IK ctrl well past the chain's
    rest length (rest_total = sum of bone lengths, the ikRPsolver's
    natural max reach) and measures the actual shoulder->wrist WORLD
    distance. Stretch ON means that distance grows substantially past
    rest_total (the chain literally lengthens); stretch OFF means the
    classic rigid-chain max-reach clamp holds it at ~rest_total regardless
    of how far the IK ctrl is pulled."""
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    from maya_tools.rigging.fabricator import fs_app, nodes

    def _dist(a, b):
        pa = om.MVector(*cmds.xform(a, q=True, ws=True, t=True))
        pb = om.MVector(*cmds.xform(b, q=True, ws=True, t=True))
        return (pb - pa).length()

    def _rest_total(shoulder, elbow, wrist):
        return _dist(shoulder, elbow) + _dist(elbow, wrist)

    # ── Fresh build, no options — must default stretch ON ──────────────
    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('stretchon')
    nodes.create_registry('stretchon_bp')
    _add_world_component('stretchon_world', root)
    nodes.create_component_node(
        component_id='stretchon_C0', component_type='SimpleIK',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()

    switch_ctrl = f'{wrist}_IKFK_ctrl'
    ik_ctrl = f'{wrist}_IK_ctrl'
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 1.0)
    rest_total = _rest_total(shoulder, elbow, wrist)
    # Pull straight along world +X — this fixture's shoulder->wrist chord
    # IS world +X at bind (_make_arm_chain_offset's own geometry), so this
    # is a genuine collinear reach-past-rest stimulus.
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(rest_total * 2.0, 0.0, 0.0))
    stretched_dist = _dist(shoulder, wrist)
    assert stretched_dist > rest_total * 1.3, (
        f'fresh build (no options): expected stretch to engage well past '
        f'rest_total ({rest_total}), got {stretched_dist} — stretchy must '
        f'default ON')
    fs_app.unbuild_modules()

    # ── Explicit opt-out — must survive an unbuild -> rebuild cycle ────
    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain_offset('stretchoff')
    nodes.create_registry('stretchoff_bp')
    _add_world_component('stretchoff_world', root)
    nodes.create_component_node(
        component_id='stretchoff_C0', component_type='SimpleIK',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'stretchy': False}, persisted={},
    )
    fs_app.build_modules()

    switch_ctrl = f'{wrist}_IKFK_ctrl'
    ik_ctrl = f'{wrist}_IK_ctrl'
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 1.0)
    rest_total = _rest_total(shoulder, elbow, wrist)
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(rest_total * 2.0, 0.0, 0.0))
    clamped_dist = _dist(shoulder, wrist)
    assert clamped_dist < rest_total * 1.05, (
        f'opt-out build (stretchy=False): expected the classic rigid-chain '
        f'max-reach clamp near rest_total ({rest_total}), got '
        f'{clamped_dist} — the animator\'s explicit opt-out must hold')

    fs_app.unbuild_modules()

    # Rebuild WITHOUT touching options — the node's own options_json still
    # carries the explicit stretchy=False, and fs_app.build_modules only
    # setdefault()s a MISSING key, so it must stay False.
    fs_app.build_modules()
    switch_ctrl = f'{wrist}_IKFK_ctrl'
    ik_ctrl = f'{wrist}_IK_ctrl'
    cmds.setAttr(f'{switch_ctrl}.ik_fk_blend', 1.0)
    rest_total2 = _rest_total(shoulder, elbow, wrist)
    cmds.xform(ik_ctrl, ws=True, relative=True, t=(rest_total2 * 2.0, 0.0, 0.0))
    clamped_dist2 = _dist(shoulder, wrist)
    assert clamped_dist2 < rest_total2 * 1.05, (
        f'REBUILD of opt-out rig: expected the max-reach clamp to still '
        f'hold near rest_total ({rest_total2}) after unbuild -> rebuild, '
        f'got {clamped_dist2} — the captured stretchy=False option must '
        f'survive the round trip, not silently flip back to the new '
        f'True default')

    fs_app.unbuild_modules()


def test_legacy_ikarm_type_migration_is_version_gated():
    """Mechanical-rename gate (2026-07-09): IKArm was renamed to
    RibbonIKArm and 'IKArm' reserved for a future free basic-arm
    component (version.py 1.1.0). modules._VERSION_GATED_LEGACY_TYPE_MAP
    maps old component_type='IKArm' data to RibbonIKArm — but ONLY when
    it's actually old data; a future free IKArm build must not be
    silently hijacked into RibbonIKArm.

    Proves BOTH halves, on the SAME 'IKArm' type string:
      1. LEGACY (no registry fabricator_version stamp — the pre-
         versioning-era case AND every real blueprint/scene that predates
         this rename today, since it just landed): resolve_component_type
         maps 'IKArm' -> 'RibbonIKArm' with a loud cmds.warning, and
         get_component_class('IKArm') returns RibbonIKArmComponent
         itself — not a lookalike. Also exercised via the SCENE-NODE read
         path (nodes.get_component_type on a node whose component_type
         attr is literally 'IKArm', simulating an unrebuilt legacy rig),
         since get_component_class routes both the blueprint-load path
         and the scene-node path through the exact same resolve_
         component_type call.
      2. CURRENT (registry stamped at/after the 1.1.0 gate): the SAME
         'IKArm' string resolves to itself, UNMAPPED — get_component_class
         returns the free IKArmComponent (modules/ik_arm.py) itself, NOT
         RibbonIKArmComponent, proving the mapping does not apply to
         current-version data and so cannot hijack the free IKArm
         component now that one is registered.
    """
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes, modules
    from maya_tools.rigging.fabricator.modules.ik_arm import IKArmComponent
    from maya_tools.rigging.fabricator.modules.ribbon_ik_arm import (
        RibbonIKArmComponent,
    )
    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION

    # ─── 1. LEGACY: registry explicitly UNSTAMPED to simulate a genuinely
    # legacy (pre-versioning-era) scene. Task 2.3 closing sweep, item 1:
    # nodes.create_registry now stamps FABRICATOR_VERSION at creation (a
    # fresh registry has no legacy data behind it and must read as
    # current, not legacy — see that function's own docstring), so a
    # plain create_registry() call no longer lands in the unstamped
    # state a real pre-versioning-era scene, or a mid-load blueprint
    # spawn under the OLD code, used to. Clearing the attr back to ''
    # reproduces that state deliberately instead of relying on it as an
    # accidental default. ───────────────────────────────────────────────
    cmds.file(new=True, force=True)
    nodes.create_registry('legacyikarm_bp')
    reg = nodes.get_registry()
    cmds.setAttr(f'{reg}.fabricator_version', '', type='string')

    modules._LEGACY_WARNED.discard('IKArm@1.1.0')
    captured = []
    orig_warning = cmds.warning
    cmds.warning = lambda msg: captured.append(msg)
    try:
        resolved = modules.resolve_component_type('IKArm')
    finally:
        cmds.warning = orig_warning
    assert resolved == 'RibbonIKArm', (
        f"legacy (unstamped) scene: resolve_component_type('IKArm') = "
        f"{resolved!r}, expected 'RibbonIKArm'")
    assert captured, (
        "legacy (unstamped) scene: resolve_component_type('IKArm') did "
        "not warn — the migration must be loud, not silent")
    assert any('IKArm' in m and 'RibbonIKArm' in m for m in captured), (
        f"warning text does not name both the retired and current type: "
        f"{captured}")

    cls = modules.get_component_class('IKArm')
    assert cls is RibbonIKArmComponent, (
        f"legacy (unstamped) scene: get_component_class('IKArm') = "
        f"{cls!r}, expected RibbonIKArmComponent itself")

    # Scene-node read path: a node whose component_type ATTR is literally
    # 'IKArm' (simulating an unrebuilt pre-rename rig — the stored string
    # is never rewritten in place by this migration; only resolved at
    # read time), read via nodes.get_component_type -> get_component_class,
    # the exact call shape every rebuild/unbuild/Properties-panel lookup
    # uses on a scene node.
    root, shoulder, elbow, wrist = _make_arm_chain('legacyikarmnode')
    _add_world_component('legacyikarmnode_world', root)
    legacy_cnode = nodes.create_component_node(
        component_id='legacyikarmnode_C0', component_type='IKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={'mid_ctrl_count': 1}, persisted={},
    )
    stored_type = nodes.get_component_type(legacy_cnode)
    assert stored_type == 'IKArm', (
        f'component_type attr was not stored verbatim: {stored_type!r}')
    node_cls = modules.get_component_class(stored_type)
    assert node_cls is RibbonIKArmComponent, (
        f'scene-node read path: get_component_class(get_component_type('
        f'legacy node)) = {node_cls!r}, expected RibbonIKArmComponent')

    # ─── 2. CURRENT: registry stamped at (or after) the 1.1.0 gate — the
    # SAME 'IKArm' string must now resolve to ITSELF, unmapped, so a
    # future free IKArm component (once one exists) is never hijacked
    # into RibbonIKArm on current-version data. The explicit re-stamp
    # below is redundant since Task 2.3's closing sweep (create_registry
    # already stamps FABRICATOR_VERSION at creation) but kept as a
    # documented, unambiguous "current" fixture rather than leaning on
    # that auto-stamp implicitly; the auto-stamp itself — a PLAIN
    # create_registry() call, no manual re-stamp — is what
    # test_fresh_registry_is_not_legacy_for_version_gated_types (below)
    # exists to prove directly. ─────────────────────────────────────────
    cmds.file(new=True, force=True)
    nodes.create_registry('currentikarm_bp')
    nodes.set_registry_fabricator_version(FABRICATOR_VERSION)
    assert not modules._version_lt(FABRICATOR_VERSION, '1.1.0'), (
        'FABRICATOR_VERSION regressed below the 1.1.0 gate this test '
        'assumes — bump the gate/version together')

    resolved_current = modules.resolve_component_type('IKArm')
    assert resolved_current == 'IKArm', (
        f"current-version scene: resolve_component_type('IKArm') = "
        f"{resolved_current!r}, expected 'IKArm' unchanged (not "
        f"remapped — this data postdates the rename)")
    cls_current = modules.get_component_class('IKArm')
    assert cls_current is IKArmComponent, (
        f"current-version scene: get_component_class('IKArm') = "
        f"{cls_current!r}, expected the free IKArmComponent itself — the "
        f"legacy mapping must not apply here")
    assert cls_current is not RibbonIKArmComponent, (
        "current-version scene: get_component_class('IKArm') resolved to "
        "RibbonIKArmComponent — the retired version-gated mapping is "
        "hijacking current-version data")


def test_fresh_registry_is_not_legacy_for_version_gated_types():
    """Task 2.3 closing sweep, item 1's own regression guard: nodes.
    create_registry now stamps FABRICATOR_VERSION at creation, so a
    fresh, never-built registry must read as CURRENT the instant it
    exists — with no build and no manual set_registry_fabricator_
    version call. This is the guard that protects a future free 'IKArm'
    component's reserved name (modules._VERSION_GATED_LEGACY_TYPE_MAP):
    without the creation-time stamp, an unbuilt scene would fall through
    to the "no stamp = legacy" default and any brand-new 'IKArm'
    component would silently resolve to RibbonIKArm the moment it
    registered, before a single build ever ran.

    Unlike test_legacy_ikarm_type_migration_is_version_gated's own
    CURRENT half (which manually calls set_registry_fabricator_version
    to construct its fixture), this test touches the registry's
    fabricator_version attr NOWHERE in its first half — the stamp under
    test is whatever create_registry itself wrote.

    Proves both halves on the SAME 'IKArm' type string, on the SAME
    registry, differing only in whether the creation-time stamp survives
    or is explicitly cleared afterward:
      1. FRESH (untouched creation-time stamp): resolve_component_type(
         'IKArm') returns 'IKArm' unmapped, and get_component_class(
         'IKArm') returns the free IKArmComponent (modules/ik_arm.py)
         itself — not RibbonIKArmComponent.
      2. UNSTAMPED (same registry, stamp cleared to '' to simulate a
         genuinely legacy/pre-versioning scene): the SAME 'IKArm' string
         now DOES map to 'RibbonIKArm', with a loud cmds.warning — the
         real legacy-data escape hatch still works.
    """
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes, modules
    from maya_tools.rigging.fabricator.modules.ik_arm import IKArmComponent
    from maya_tools.rigging.fabricator.modules.ribbon_ik_arm import (
        RibbonIKArmComponent,
    )

    # ─── 1. FRESH: nothing touches fabricator_version after creation. ──
    cmds.file(new=True, force=True)
    reg = nodes.create_registry('freshgate_bp')
    stamped = nodes.get_registry_fabricator_version()
    assert stamped, (
        'nodes.create_registry must stamp fabricator_version at '
        'creation — a fresh registry read back empty means the '
        'creation-time stamp (Task 2.3 closing sweep item 1) regressed')

    resolved_fresh = modules.resolve_component_type('IKArm')
    assert resolved_fresh == 'IKArm', (
        f"fresh (stamped) registry: resolve_component_type('IKArm') = "
        f"{resolved_fresh!r}, expected 'IKArm' unchanged — a never-built "
        f"scene must never be treated as legacy data")
    cls_fresh = modules.get_component_class('IKArm')
    assert cls_fresh is IKArmComponent, (
        f"fresh (stamped) registry: get_component_class('IKArm') = "
        f"{cls_fresh!r}, expected the free IKArmComponent itself — a "
        f"never-built scene must resolve 'IKArm' to the real free "
        f"component, not raise and not fall through to the retired "
        f"version-gated mapping")
    assert cls_fresh is not RibbonIKArmComponent, (
        "fresh (stamped) registry: get_component_class('IKArm') resolved "
        "to RibbonIKArmComponent — this is the guard that protects the "
        "free IKArm component's name from being silently hijacked by the "
        "retired version-gated mapping")

    # ─── 2. UNSTAMPED: same registry, stamp cleared to simulate legacy
    # data — the mapping must still apply, with a warning. ─────────────
    cmds.setAttr(f'{reg}.fabricator_version', '', type='string')
    assert not nodes.get_registry_fabricator_version(), (
        'test setup: clearing fabricator_version did not read back empty')

    modules._LEGACY_WARNED.discard('IKArm@1.1.0')
    captured = []
    orig_warning = cmds.warning
    cmds.warning = lambda msg: captured.append(msg)
    try:
        resolved_legacy = modules.resolve_component_type('IKArm')
    finally:
        cmds.warning = orig_warning
    assert resolved_legacy == 'RibbonIKArm', (
        f"unstamped (simulated legacy) registry: resolve_component_type("
        f"'IKArm') = {resolved_legacy!r}, expected 'RibbonIKArm'")
    assert captured, (
        "unstamped (simulated legacy) registry: resolve_component_type("
        "'IKArm') did not warn — the migration must be loud, not silent")


def _legacy_fingers_blueprint(prefix):
    """A pre-1.2.0 Blueprint: RibbonIKArm with a stored 'fingers' option
    (the retired name-string membership) — exercises fs_app._migrate_
    legacy_finger_membership's reconcile path (Task 2.3, SPEC 2026-07-09
    Limbs + Follower Joints §3.4/§4). Deliberately includes:
      - a finger (thumb) whose curl_excluded is a MANUAL edit the
        metacarpal heuristic would NOT reproduce on a fresh re-discovery
        (thumb carries no metacarpal token, so the heuristic excludes
        nothing from it) — proves migration reads the stored option, not
        just a fresh discovery pass;
      - a finger entry whose root_joint (and a curl_excluded entry) never
        appear in skeleton_joints at all — the documented "unresolved
        name" case, which must be listed in the migration warning and
        never connected."""
    from maya_tools.rigging.fabricator.blueprint.schema import (
        Blueprint, ComponentSpec, JointSpec,
    )
    p = prefix
    joints = [
        JointSpec(name=f'{p}_root', parent=None),
        JointSpec(name=f'{p}_shoulder', parent=f'{p}_root'),
        JointSpec(name=f'{p}_elbow', parent=f'{p}_shoulder'),
        JointSpec(name=f'{p}_wrist', parent=f'{p}_elbow'),
        JointSpec(name=f'{p}_thumb_01', parent=f'{p}_wrist'),
        JointSpec(name=f'{p}_thumb_02', parent=f'{p}_thumb_01'),
        JointSpec(name=f'{p}_thumb_03', parent=f'{p}_thumb_02'),
        JointSpec(name=f'{p}_index_metacarpal', parent=f'{p}_wrist'),
        JointSpec(name=f'{p}_index_01', parent=f'{p}_index_metacarpal'),
        JointSpec(name=f'{p}_index_02', parent=f'{p}_index_01'),
        JointSpec(name=f'{p}_index_03', parent=f'{p}_index_02'),
    ]
    fingers_option = [
        {
            'root_joint': f'{p}_thumb_01',
            'joints': [f'{p}_thumb_01', f'{p}_thumb_02', f'{p}_thumb_03'],
            'curl_excluded': [f'{p}_thumb_03'],
        },
        {
            'root_joint': f'{p}_index_metacarpal',
            'joints': [f'{p}_index_metacarpal', f'{p}_index_01',
                      f'{p}_index_02', f'{p}_index_03'],
            'curl_excluded': [f'{p}_index_metacarpal'],
        },
        {
            'root_joint': f'{p}_ghost_root',
            'joints': [f'{p}_ghost_root'],
            'curl_excluded': [f'{p}_ghost_excluded'],
        },
    ]
    components = [
        ComponentSpec(type='World', joints=[f'{p}_root'], id=f'{p}_world',
                     parent_plug='', side='md'),
        ComponentSpec(
            type='RibbonIKArm',
            joints=[f'{p}_shoulder', f'{p}_elbow', f'{p}_wrist'],
            id=f'{p}_arm', parent_plug=f'{p}_world.joint_out', side='md',
            options={'mid_ctrl_count': 1, 'fingers': fingers_option},
        ),
    ]
    return Blueprint(name=f'{p}_legacy_bp', skeleton_joints=joints,
                     components=components)
def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    check('test_ikarm_build_creates_ik_fk_pv_switch_and_blend_works',
          test_ikarm_build_creates_ik_fk_pv_switch_and_blend_works)
    check('test_ikarm_unbuild_leaves_zero_orphans',
          test_ikarm_unbuild_leaves_zero_orphans)
    check('test_ikarm_ribbon_segments_exist_with_mids',
          test_ikarm_ribbon_segments_exist_with_mids)
    check('test_ikarm_ribbon_width_auto_resolves_to_25_percent_of_bone_length',
          test_ikarm_ribbon_width_auto_resolves_to_25_percent_of_bone_length)
    check('test_ikarm_ribbon_twist_and_volume_posed_displacement',
          test_ikarm_ribbon_twist_and_volume_posed_displacement)
    check('test_ikarm_ribbon_no_double_transform_on_global_scale',
          test_ikarm_ribbon_no_double_transform_on_global_scale)
    check('test_ikarm_ribbon_unbuild_leaves_zero_orphans',
          test_ikarm_ribbon_unbuild_leaves_zero_orphans)
    check('test_ikarm_ribbon_persistence_round_trip',
          test_ikarm_ribbon_persistence_round_trip)
    check('test_ikarm_unbuild_ribbon_cleanup_survives_joint_count_drift',
          test_ikarm_unbuild_ribbon_cleanup_survives_joint_count_drift)
    check('test_ikarm_roll_anti_flip_and_twist_response',
          test_ikarm_roll_anti_flip_and_twist_response)
    check('test_ikarm_roll_driven_twist_no_flip',
          test_ikarm_roll_driven_twist_no_flip)
    check('test_ikarm_roll_forearm_locator_mechanism_is_load_bearing',
          test_ikarm_roll_forearm_locator_mechanism_is_load_bearing)
    check('test_ikarm_ribbon_mid_ctrls_read_roll_joint_not_raw_bind_joint',
          test_ikarm_ribbon_mid_ctrls_read_roll_joint_not_raw_bind_joint)
    check('test_ikarm_roll_twist_works_ik_and_fk',
          test_ikarm_roll_twist_works_ik_and_fk)
    check('test_ikarm_roll_mirror_offset_sign_flip',
          test_ikarm_roll_mirror_offset_sign_flip)
    check('test_ikarm_roll_unbuild_leaves_zero_orphans',
          test_ikarm_roll_unbuild_leaves_zero_orphans)
    check('test_ikarm_roll_internals_hidden_from_viewport',
          test_ikarm_roll_internals_hidden_from_viewport)
    check('test_regression_simple_ik_still_builds',
          test_regression_simple_ik_still_builds)
    check('test_regression_ik_leg_still_builds',
          test_regression_ik_leg_still_builds)
    check('test_ikarm_switch_ctrl_default_shape_is_ikfk',
          test_ikarm_switch_ctrl_default_shape_is_ikfk)
    check('test_ikleg_switch_ctrl_default_shape_is_ikfk',
          test_ikleg_switch_ctrl_default_shape_is_ikfk)
    check('test_ikarm_finger_fk_ctrls_ue5_5finger_with_metacarpals',
          test_ikarm_finger_fk_ctrls_ue5_5finger_with_metacarpals)
    check('test_ikarm_finger_fk_ctrls_12finger_cartoon_no_metacarpal',
          test_ikarm_finger_fk_ctrls_12finger_cartoon_no_metacarpal)
    check('test_ikarm_finger_fk_ctrls_4finger_no_metacarpal',
          test_ikarm_finger_fk_ctrls_4finger_no_metacarpal)
    check('test_ikarm_finger_ctrl_actually_drives_joint',
          test_ikarm_finger_ctrl_actually_drives_joint)
    check('test_ikarm_finger_ctrl_cv_persists_across_unbuild_build',
          test_ikarm_finger_ctrl_cv_persists_across_unbuild_build)
    check('test_ikarm_finger_bind_joint_constraint_sweep_on_unbuild',
          test_ikarm_finger_bind_joint_constraint_sweep_on_unbuild)
    check('test_ikarm_finger_bind_joint_constraint_sweep_is_explicit_not_incidental',
          test_ikarm_finger_bind_joint_constraint_sweep_is_explicit_not_incidental)
    check('test_ikarm_finger_rename_survives_rebuild_fist_still_works_zero_warnings',
          test_ikarm_finger_rename_survives_rebuild_fist_still_works_zero_warnings)
    check('test_ikarm_finger_inserted_joint_auto_joins_on_rebuild',
          test_ikarm_finger_inserted_joint_auto_joins_on_rebuild)
    check('test_ikarm_finger_ctrl_joint_index_disambiguates_via_pose_library',
          test_ikarm_finger_ctrl_joint_index_disambiguates_via_pose_library)
    check('test_ikarm_mirror_component_mirrors_limb_finger_membership',
          test_ikarm_mirror_component_mirrors_limb_finger_membership)
    check('test_ikarm_finger_regression_suites_stay_green',
          test_ikarm_finger_regression_suites_stay_green)
    check('test_ikarm_fingers_ctrl_layered_curl_and_metacarpal_exclusion',
          test_ikarm_fingers_ctrl_layered_curl_and_metacarpal_exclusion)
    check('test_ikarm_fingers_ctrl_curl_axis_x_variant',
          test_ikarm_fingers_ctrl_curl_axis_x_variant)
    check('test_ikarm_fingers_ctrl_plusminusaverage_branch_and_unbuild_clean',
          test_ikarm_fingers_ctrl_plusminusaverage_branch_and_unbuild_clean)
    check('test_ikarm_fingers_ctrl_color_follows_group_ctrl_color',
          test_ikarm_fingers_ctrl_color_follows_group_ctrl_color)
    check('test_limb_fragment_skeleton_only_loads_clean',
          test_limb_fragment_skeleton_only_loads_clean)
    check('test_limb_auto_add_finger_joins_existing_ikarm_fist',
          test_limb_auto_add_finger_joins_existing_ikarm_fist)
    check('test_limb_auto_add_no_ikarm_is_noop',
          test_limb_auto_add_no_ikarm_is_noop)
    check('test_limb_auto_add_only_on_wrist_not_shoulder_or_elbow',
          test_limb_auto_add_only_on_wrist_not_shoulder_or_elbow)
    check('test_ikarm_p5_regression_suites_stay_green',
          test_ikarm_p5_regression_suites_stay_green)
    check('test_ikarm_fingers_ctrl_orientation_matches_wrist_and_offset',
          test_ikarm_fingers_ctrl_orientation_matches_wrist_and_offset)
    check('test_ikarm_fingers_ctrl_offset_mirror_symmetry',
          test_ikarm_fingers_ctrl_offset_mirror_symmetry)
    check('test_ikarm_pv_ctrl_aims_at_fk_elbow_and_tracks_fk_poses',
          test_ikarm_pv_ctrl_aims_at_fk_elbow_and_tracks_fk_poses)
    check('test_ikarm_pv_ctrl_aim_no_cycle_and_unbuild_clean',
          test_ikarm_pv_ctrl_aim_no_cycle_and_unbuild_clean)
    check('test_switch_ctrl_orientation_matches_end_joint_and_offset_z',
          test_switch_ctrl_orientation_matches_end_joint_and_offset_z)
    check('test_switch_ctrl_offset_mirror_symmetry',
          test_switch_ctrl_offset_mirror_symmetry)
    check('test_ikleg_switch_ctrl_inherits_orientation_treatment',
          test_ikleg_switch_ctrl_inherits_orientation_treatment)
    check('test_stretchy_default_on_fresh_build_and_opt_out_round_trips',
          test_stretchy_default_on_fresh_build_and_opt_out_round_trips)
    check('test_legacy_ikarm_type_migration_is_version_gated',
          test_legacy_ikarm_type_migration_is_version_gated)
    check('test_fresh_registry_is_not_legacy_for_version_gated_types',
          test_fresh_registry_is_not_legacy_for_version_gated_types)

    if FAILURES:
        print(f"IK ARM MAYA TESTS: {len(FAILURES)} FAILED ({len(SKIPS)} SKIP)")
        sys.exit(1)
    if SKIPS:
        print(f"IK ARM MAYA TESTS: OK - {len(SKIPS)} SKIP "
              f"(not counted as pass): {SKIPS}")
    else:
        print("IK ARM MAYA TESTS: OK - 0 SKIP")


if __name__ == '__main__':
    main()
