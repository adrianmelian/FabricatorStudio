# _dev/test_simple_ik_pref_angle.py
"""mayapy scene tests for SimpleIKComponent._compute_straight_chain_bend_hint
— the straight-chain preferredAngle fix documented (previously as a known,
unimplemented gap) in maya_tools/docs/components/simple-ik.md's Gotchas
section and CLAUDE.md's Fabricator gotcha 3.

Background: _create_chain_duplicate bakes the IK chain's rotate to 0 via
makeIdentity (gotcha 2), so cmds.ikHandle's auto setPreferredAngles=True
always writes (0, 0, 0) onto the chain regardless of how bent it actually
is. On a normally-bent chain that's harmless — the mid joint's own bind
position already gives the RP solver an unambiguous bend plane. On a
near-straight chain (no bend deviation to read a plane from) it can lock
the RP solver dead-straight, because the pole-vector plane computation is
ALSO singular at exact collinearity. The fix re-stamps the mid IK joint's
preferredAngle from the resolved PV ctrl position, but ONLY when the
chain's own geometric bend is below a documented epsilon — a genuinely
bent chain must keep whatever preferredAngle auto-spa already wrote.

This file proves:
  - STRAIGHT CHAIN, DETERMINISTIC, ISOLATED (test_compute_straight_chain_
    bend_hint_matches_pv_side_directly): calling the classmethod directly
    on a hand-built, exactly-collinear 3-joint chain (no ikHandle involved
    at all, so rotate is provably 0 the whole time) returns a single
    nonzero preferredAngle component whose axis/sign independently
    (re-derived in this file, not by calling the method under test)
    matches the side the PV was placed on.
  - BENT CHAIN, ISOLATED (test_compute_straight_chain_bend_hint_returns_
    none_for_bent_chain): same harness with a real (tens-of-degrees) bend
    returns None — the epsilon guard does not fire on a working chain.
  - STRAIGHT CHAIN, FUNCTIONAL / END-TO-END (test_straight_chain_bends_
    toward_pv_after_seeding): a full SimpleIK build via fs_app.build_
    modules() on an exactly-collinear chain, then moving the IK end ctrl
    to a target the rigid chain can only reach by bending, produces a bind
    elbow that has genuinely left the shoulder-to-wrist line (not locked
    straight) and landed on the PV's side of it (not flipped to the other
    side) — the actual end-to-end behavior the gotcha describes.
  - BENT CHAIN UNTOUCHED, FULL BUILD (test_bent_chain_leaves_preferred_
    angle_untouched): a full SimpleIK build with a real bind bend keeps
    preferredAngle at (0, 0, 0) — exactly what auto-spa wrote — proving
    the guard holds through the real build() call sites, not just the
    isolated classmethod.
  - INHERITANCE (test_ikarm_straight_chain_also_seeds_preferred_angle):
    IKArm (a SimpleIKComponent subclass, per CLAUDE.md's inheritance
    requirement) gets the same seeding through the shared build() path
    without any IKArm-specific code — proves the fix lives in the
    shared path, not bolted onto one subclass.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_simple_ik_pref_angle.py
"""
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


class Skip(Exception):
    """Raise from a test body to mark it SKIPPED — never silently folded
    into the pass count (see _dev/test_ribbon_ik_arm.py's identical class for the
    full rationale)."""


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


def _add_world_component(component_id, root_joint):
    from maya_tools.rigging.fabricator import nodes
    nodes.create_component_node(
        component_id=component_id, component_type='World',
        joints=[root_joint], parent_plug='', side='md',
        options={}, persisted={},
    )


def _make_straight_arm_chain(prefix):
    """Root -> shoulder -> elbow -> wrist, EXACTLY collinear along world X
    (bend deviation at the elbow is 0 degrees) — the degenerate case
    _seed_straight_chain_bend_hint exists for."""
    import maya.cmds as cmds
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 10, 0), name=f'{prefix}_root')
    shoulder = cmds.joint(p=(0, 10, 0), name=f'{prefix}_shoulder')
    elbow = cmds.joint(p=(5, 10, 0), name=f'{prefix}_elbow')
    wrist = cmds.joint(p=(10, 10, 0), name=f'{prefix}_wrist')
    cmds.select(clear=True)
    return root, shoulder, elbow, wrist


def _make_bent_arm_chain(prefix):
    """Root -> shoulder -> elbow -> wrist with a real, tens-of-degrees
    elbow bend (~64.6 degrees between the two segments) — matches the
    geometry _dev/test_ik_arm_maya.py's _make_arm_chain already uses for
    its own regression suite."""
    import maya.cmds as cmds
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 10, 0), name=f'{prefix}_root')
    shoulder = cmds.joint(p=(0, 10, 0), name=f'{prefix}_shoulder')
    elbow = cmds.joint(p=(5, 7, 1), name=f'{prefix}_elbow')
    wrist = cmds.joint(p=(10, 10, 0), name=f'{prefix}_wrist')
    cmds.select(clear=True)
    return root, shoulder, elbow, wrist


def _v_sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _v_add(a, b):
    return [a[i] + b[i] for i in range(3)]


def _v_scale(a, k):
    return [c * k for c in a]


def _v_dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _v_cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _v_len(a):
    return _v_dot(a, a) ** 0.5


def _v_norm(a):
    m = _v_len(a)
    assert m > 1e-9, f'cannot normalize a near-zero vector: {a}'
    return [c / m for c in a]


def test_compute_straight_chain_bend_hint_matches_pv_side_directly():
    """Isolated unit test of the classmethod itself — no ikHandle, no full
    Fabricator build. _compute_straight_chain_bend_hint's contract requires
    rotate=0 on every ik_chain joint at call time (see its docstring); the
    only way to guarantee that for an independent re-derivation is to never
    let an ikHandle/RP solver touch the chain in the first place. A live
    query taken AFTER a real build's ikHandle has been evaluated can't be
    trusted for this (the solver owns the joints' rotate once it exists,
    which is exactly the ambiguity this method exists to preempt for an
    exactly-collinear chain) — see test_straight_chain_bends_toward_pv_
    after_seeding below for the end-to-end functional proof instead."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator.modules.simple_ik import SimpleIKComponent

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    start = cmds.joint(p=(0, 10, 0), name='directpa_start')
    mid = cmds.joint(p=(5, 10, 0), name='directpa_mid')
    end = cmds.joint(p=(10, 10, 0), name='directpa_end')
    cmds.select(clear=True)
    # Bake rotate into jointOrient exactly like _create_chain_duplicate does
    # for a real IK chain duplicate, so mid.rotate == 0 matches the real
    # build-time precondition this method requires.
    cmds.makeIdentity([start, mid, end], apply=True, t=0, r=1, s=0, n=0)

    pv_ctrl = cmds.createNode('transform', name='directpa_pv')
    cmds.xform(pv_ctrl, ws=True, t=(5, 10, -3))  # off to the -Z side

    seed = SimpleIKComponent._compute_straight_chain_bend_hint(
        [start, mid, end], pv_ctrl)
    assert seed is not None, 'expected a seed for an exactly collinear chain'

    nonzero = [(i, v) for i, v in enumerate(seed) if abs(v) > 1e-6]
    assert len(nonzero) == 1, (
        f'expected exactly one nonzero preferredAngle component, got {seed}')
    axis_idx, seeded_val = nonzero[0]

    expected_mag = SimpleIKComponent.STRAIGHT_CHAIN_SEED_DEG
    assert abs(abs(seeded_val) - expected_mag) < 1e-6, (
        f'seeded magnitude {seeded_val} does not match the documented '
        f'STRAIGHT_CHAIN_SEED_DEG={expected_mag}')

    # Independently re-derive which side the PV was actually placed on and
    # confirm the returned axis/sign is consistent with bending THAT way —
    # re-derived here from scratch (not by calling the method under test)
    # so this is a real check, not a tautology. rotate is still exactly 0
    # on every joint here (nothing has evaluated an IK solve in this
    # scenario — there is no ikHandle at all), so this live query is exact.
    s = cmds.xform(start, q=True, ws=True, t=True)
    m = cmds.xform(mid,   q=True, ws=True, t=True)
    e = cmds.xform(end,   q=True, ws=True, t=True)
    chain_axis = _v_norm(_v_sub(e, s))

    pv_pos = cmds.xform(pv_ctrl, q=True, ws=True, t=True)
    offset = _v_sub(pv_pos, m)
    pv_perp = _v_sub(offset, _v_scale(chain_axis, _v_dot(offset, chain_axis)))
    assert _v_len(pv_perp) > 1e-6, 'PV landed exactly on the chain axis'
    pv_perp = _v_norm(pv_perp)

    bend_axis = _v_norm(_v_cross(chain_axis, pv_perp))

    mat = cmds.xform(mid, q=True, ws=True, matrix=True)
    local_axes = (
        [mat[0], mat[1], mat[2]],
        [mat[4], mat[5], mat[6]],
        [mat[8], mat[9], mat[10]],
    )
    dots = [_v_dot(ax, bend_axis) for ax in local_axes]
    expected_axis_idx = max(range(3), key=lambda i: abs(dots[i]))
    expected_sign = 1.0 if dots[expected_axis_idx] >= 0 else -1.0

    assert axis_idx == expected_axis_idx, (
        f'seeded axis index {axis_idx} does not match the PV-derived '
        f'bend axis index {expected_axis_idx} (dots={dots})')
    actual_sign = 1.0 if seeded_val >= 0 else -1.0
    assert actual_sign == expected_sign, (
        f'seeded sign {actual_sign} does not match the PV side '
        f'(expected {expected_sign}, dots={dots})')


def test_compute_straight_chain_bend_hint_returns_none_for_bent_chain():
    """Same isolated harness as the straight-chain test above, but with a
    real (tens-of-degrees) bend — the method must return None so the
    caller leaves preferredAngle untouched."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator.modules.simple_ik import SimpleIKComponent

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    start = cmds.joint(p=(0, 10, 0), name='directbent_start')
    mid = cmds.joint(p=(5, 7, 1), name='directbent_mid')
    end = cmds.joint(p=(10, 10, 0), name='directbent_end')
    cmds.select(clear=True)
    cmds.makeIdentity([start, mid, end], apply=True, t=0, r=1, s=0, n=0)

    pv_ctrl = cmds.createNode('transform', name='directbent_pv')
    cmds.xform(pv_ctrl, ws=True, t=(5, 7, -4))

    seed = SimpleIKComponent._compute_straight_chain_bend_hint(
        [start, mid, end], pv_ctrl)
    assert seed is None, (
        f'expected None for a genuinely bent chain, got {seed}')


def test_straight_chain_bends_toward_pv_after_seeding():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_straight_arm_chain('straightbend')

    nodes.create_registry('straight_bend_bp')
    _add_world_component('straightbend_world', root)
    nodes.create_component_node(
        component_id='straightbend_C0', component_type='SimpleIK',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()

    ik_ctrl = f'{wrist}_IK_ctrl'
    pv_ctrl = f'{elbow}_PV_ctrl'
    assert cmds.objExists(ik_ctrl) and cmds.objExists(pv_ctrl)

    shoulder_pos = cmds.xform(shoulder, q=True, ws=True, t=True)
    pv_pos = cmds.xform(pv_ctrl, q=True, ws=True, t=True)

    # Rest chain length is exactly 10 (5 + 5). Pull the wrist ctrl 2 units
    # back toward the shoulder along the chain axis (X) so the target
    # distance (8) is strictly less than rest length — the rigid,
    # non-stretchy chain can only satisfy that by bending. This is the
    # exact scenario the gotcha describes: an exactly-straight-at-build
    # chain that must bend the first time it's posed off the line.
    cmds.xform(ik_ctrl, ws=True, r=True, t=(-2.0, 0.0, 0.0))

    elbow_pos = cmds.xform(elbow, q=True, ws=True, t=True)
    wrist_pos = cmds.xform(wrist, q=True, ws=True, t=True)

    chain_axis = _v_norm(_v_sub(wrist_pos, shoulder_pos))
    elbow_offset = _v_sub(elbow_pos, shoulder_pos)
    elbow_perp = _v_sub(
        elbow_offset, _v_scale(chain_axis, _v_dot(elbow_offset, chain_axis)))

    # Must have actually left the line -- a solver that stayed "locked
    # dead-straight" (the documented bug) would leave this at ~0.
    assert _v_len(elbow_perp) > 0.1, (
        f'elbow did not bend off the shoulder-wrist line after the IK '
        f'ctrl move (locked straight?): elbow_perp={elbow_perp}')

    pv_offset = _v_sub(pv_pos, shoulder_pos)
    pv_perp = _v_sub(
        pv_offset, _v_scale(chain_axis, _v_dot(pv_offset, chain_axis)))

    # Must have bent toward the PV's side, not the opposite side.
    same_side = _v_dot(elbow_perp, pv_perp) > 0
    assert same_side, (
        f'elbow bent AWAY from the PV side: elbow_perp={elbow_perp} '
        f'pv_perp={pv_perp}')

    fs_app.unbuild_modules()


def test_bent_chain_leaves_preferred_angle_untouched():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_bent_arm_chain('bentpa')

    # Sanity: confirm this fixture really is bent well above the epsilon,
    # so a pass below isn't vacuous.
    s, m, e = (cmds.xform(j, q=True, ws=True, t=True)
               for j in (shoulder, elbow, wrist))
    v1, v2 = _v_sub(m, s), _v_sub(e, m)
    cos_a = _v_dot(v1, v2) / (_v_len(v1) * _v_len(v2))
    bend_deg = math.degrees(math.acos(max(-1.0, min(1.0, cos_a))))
    from maya_tools.rigging.fabricator.modules.simple_ik import SimpleIKComponent
    assert bend_deg >= SimpleIKComponent.STRAIGHT_CHAIN_EPSILON_DEG * 10, (
        f'fixture bend {bend_deg} deg is not comfortably above the '
        f'epsilon -- test would be vacuous')

    nodes.create_registry('bent_pa_bp')
    _add_world_component('bentpa_world', root)
    nodes.create_component_node(
        component_id='bentpa_C0', component_type='SimpleIK',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()

    mid_ik = f'{elbow}_ik'
    pref = list(cmds.getAttr(f'{mid_ik}.preferredAngle')[0])
    assert all(abs(v) < 1e-6 for v in pref), (
        f'bent chain preferredAngle was touched: {pref} '
        f'(should stay exactly what auto-setPreferredAngles wrote)')

    fs_app.unbuild_modules()


def test_ikarm_straight_chain_also_seeds_preferred_angle():
    """IKArm subclasses SimpleIKComponent and calls super().build() first
    thing (see modules/ribbon_ik_arm.py) -- the seeding must fire through that
    shared path with no IKArm-specific code involved.

    Also exercises the legacy component-type alias end to end (component_
    type='IKArm', the pre-1.1.0 name -- see modules._VERSION_GATED_LEGACY_
    TYPE_MAP): blueprint validation must resolve it to its successor
    'RibbonIKArm' rather than rejecting it as unregistered, the build must
    self-heal the node's stored type string onto that successor (so the
    "stops applying once this rig is rebuilt" promise in resolve_component_
    type's warning is actually true), and an Unbuild called immediately
    after -- in the SAME session, where build_modules' own end-of-build
    fabricator_version stamp has already flipped the registry to
    "current" -- must still resolve the (now self-healed) type instead of
    KeyError'ing.

    Registry is explicitly UNSTAMPED right after creation to simulate a
    genuinely legacy (pre-versioning-era) scene (Task 2.3 closing sweep,
    item 2): nodes.create_registry now stamps FABRICATOR_VERSION at
    creation, so a plain create_registry() call here would read as
    current-version, not legacy -- 'IKArm' would resolve to itself
    (unmapped) and get_component_class('IKArm') would KeyError, which
    would silently gut this test's whole point (see
    test_ribbon_ik_arm_maya.py's test_legacy_ikarm_type_migration_is_
    version_gated for the fresh-vs-legacy contrast this same mechanism
    is guarded by)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_straight_arm_chain('ikarmpa')

    nodes.create_registry('ikarm_pa_bp')
    reg = nodes.get_registry()
    cmds.setAttr(f'{reg}.fabricator_version', '', type='string')
    _add_world_component('ikarmpa_world', root)
    cnode = nodes.create_component_node(
        component_id='ikarmpa_C0', component_type='IKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()

    mid_ik = f'{elbow}_ik'
    assert cmds.objExists(mid_ik), f'expected IK duplicate {mid_ik!r}'
    pref = list(cmds.getAttr(f'{mid_ik}.preferredAngle')[0])
    nonzero = [v for v in pref if abs(v) > 1e-6]
    assert len(nonzero) == 1, (
        f'IKArm: expected exactly one nonzero preferredAngle component '
        f'on a near-straight chain, got {pref}')

    healed_type = nodes.get_component_type(cnode)
    assert healed_type == 'RibbonIKArm', (
        f"expected the legacy 'IKArm' type string to self-heal onto its "
        f"resolved successor 'RibbonIKArm' after a successful build, got "
        f"{healed_type!r} -- an un-healed legacy string left behind after "
        f"build_modules stamps the registry current will KeyError on the "
        f"very next read (e.g. the Unbuild right below)")

    fs_app.unbuild_modules()


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    check('test_compute_straight_chain_bend_hint_matches_pv_side_directly',
          test_compute_straight_chain_bend_hint_matches_pv_side_directly)
    check('test_compute_straight_chain_bend_hint_returns_none_for_bent_chain',
          test_compute_straight_chain_bend_hint_returns_none_for_bent_chain)
    check('test_straight_chain_bends_toward_pv_after_seeding',
          test_straight_chain_bends_toward_pv_after_seeding)
    check('test_bent_chain_leaves_preferred_angle_untouched',
          test_bent_chain_leaves_preferred_angle_untouched)
    check('test_ikarm_straight_chain_also_seeds_preferred_angle',
          test_ikarm_straight_chain_also_seeds_preferred_angle)

    if FAILURES:
        print(f"SIMPLE IK PREF ANGLE TESTS: {len(FAILURES)} FAILED ({len(SKIPS)} SKIP)")
        sys.exit(1)
    if SKIPS:
        print(f"SIMPLE IK PREF ANGLE TESTS: OK - {len(SKIPS)} SKIP "
              f"(not counted as pass): {SKIPS}")
    else:
        print("SIMPLE IK PREF ANGLE TESTS: OK - 0 SKIP")


if __name__ == '__main__':
    main()
