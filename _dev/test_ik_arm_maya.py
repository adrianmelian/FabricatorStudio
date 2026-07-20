# _dev/test_ik_arm_maya.py
"""mayapy scene tests for the FREE-core IKArm component (modules/ik_arm.py).

Task 2 (P1 parity): proves pure SimpleIK-inheritance passthrough is
sufficient for the free arm's contract — build creates the same FK/IK/
PV/switch ctrls SimpleIK always creates, the ik_fk_blend switch actually
drives the bind chain in both directions, unbuild leaves zero orphans,
and — the free-tier distinction — NONE of the paid ribbon/roll
scaffolding exists (no ribbon nodes, no roll joints, no skinCluster).

Companion offscreen suite: _dev/test_ik_arm.py (Task 1, contract only).
Design: docs/superpowers/specs/2026-07-09-basicikarm-module-design.md;
task plan: workspace/2026-07-09_basicikarm-module/PLAN.md (Task 2).

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_ik_arm_maya.py
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
    count. See test_ribbon_ik_arm_maya.py's identical Skip class for the
    full rationale."""


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


def _make_arm_chain(prefix):
    """Root -> shoulder -> elbow -> wrist. The root gets a World component
    (see _add_world_component) — SimpleIK's parent_in input is required,
    so every IK component in these tests needs a World-owning ancestor or
    build_modules' _auto_resolve_parent_plugs raises before ever reaching
    component build code. Copied verbatim from
    _dev/test_ribbon_ik_arm_maya.py per PLAN.md Task 2 Step 1."""
    import maya.cmds as cmds
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 10, 0), name=f'{prefix}_root')
    shoulder = cmds.joint(p=(0, 10, 0), name=f'{prefix}_shoulder')
    elbow = cmds.joint(p=(5, 7, 1), name=f'{prefix}_elbow')
    wrist = cmds.joint(p=(10, 10, 0), name=f'{prefix}_wrist')
    cmds.select(clear=True)
    return root, shoulder, elbow, wrist


def _add_world_component(component_id, root_joint):
    from maya_tools.rigging.fabricator import nodes
    nodes.create_component_node(
        component_id=component_id, component_type='World',
        joints=[root_joint], parent_plug='', side='md',
        options={}, persisted={},
    )


def _add_component_via_app(component_id, component_type, joints):
    """PINNED (Task 0 Step 5 / task instructions, verified against
    nodes.py:407): plain nodes.create_component_node already calls
    _maybe_create_implicit_limb(node) unconditionally (nodes.py:407) —
    that internal hook is what fires creates_implicit_limb (keyed off
    getattr(cls, 'creates_implicit_limb', False), nodes.py:754), so no
    separate 'app add path' exists to route through. This IS that path."""
    from maya_tools.rigging.fabricator import nodes
    nodes.create_component_node(
        component_id=component_id, component_type=component_type,
        joints=joints, parent_plug='', side='md', options={}, persisted={},
    )


def _add_finger(wrist, name):
    """4-joint chain under the wrist: <f>_metacarpal -> <f>_01 -> <f>_02 ->
    <f>_03, mirroring templates/finger.limb.yaml's shape (PLAN.md Task 3
    Step 1, verbatim)."""
    import maya.cmds as cmds
    cmds.select(wrist)
    j0 = cmds.joint(name=f'{name}_metacarpal', relative=True, p=(1.0, 0, 0))
    j1 = cmds.joint(name=f'{name}_01', relative=True, p=(1.0, 0, 0))
    j2 = cmds.joint(name=f'{name}_02', relative=True, p=(1.0, 0, 0))
    j3 = cmds.joint(name=f'{name}_03', relative=True, p=(1.0, 0, 0))
    cmds.select(clear=True)
    return [j0, j1, j2, j3]


def test_free_ikarm_parity_build_switch_pv_unbuild():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain('bikarm')
    nodes.create_registry('bikarm_bp')
    _add_world_component('bikarm_world', root)
    nodes.create_component_node(
        component_id='bikarm_C0', component_type='IKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()

    for c in (f'{shoulder}_FK_ctrl', f'{elbow}_FK_ctrl', f'{wrist}_FK_ctrl',
              f'{wrist}_IK_ctrl', f'{elbow}_PV_ctrl', f'{wrist}_IKFK_ctrl'):
        assert cmds.objExists(c), c

    # IK drives bind (default blend = 1.0, move within rigid reach).
    before = cmds.xform(wrist, q=True, ws=True, t=True)
    cmds.xform(f'{wrist}_IK_ctrl', ws=True, r=True, t=(1.0, 0.0, 0.0))
    after = cmds.xform(wrist, q=True, ws=True, t=True)
    assert abs((after[0] - before[0]) - 1.0) < 1e-2

    # FK drives bind after the switch.
    cmds.setAttr(f'{wrist}_IKFK_ctrl.ik_fk_blend', 0.0)
    rb = cmds.getAttr(f'{elbow}.rotate')[0]
    cmds.setAttr(f'{elbow}_FK_ctrl.rotateZ', 30.0)
    assert cmds.getAttr(f'{elbow}.rotate')[0] != rb

    # NO ribbon, NO roll: parity means none of the paid scaffolding exists.
    assert not cmds.ls('*_ribbon_*'), cmds.ls('*_ribbon_*')
    assert not cmds.ls('*_roll_jnt'), cmds.ls('*_roll_jnt')
    assert not cmds.ls(type='skinCluster')

    fs_app.unbuild_modules()
    assert not cmds.objExists('rig_grp')


def test_free_ikarm_unbuild_leaves_zero_orphans():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain('bikorph')
    _TYPES = ('reverse', 'condition', 'distanceBetween', 'blendTwoAttr',
              'multMatrix', 'pointMatrixMult', 'plusMinusAverage',
              'multDoubleLinear', 'unitConversion')
    baseline = {t: len(cmds.ls(type=t) or []) for t in _TYPES}

    nodes.create_registry('bikorph_bp')
    _add_world_component('bikorph_world', root)
    nodes.create_component_node(
        component_id='bikorph_C0', component_type='IKArm',
        joints=[shoulder, elbow, wrist], parent_plug='', side='md',
        options={}, persisted={},
    )
    fs_app.build_modules()
    fs_app.unbuild_modules()

    for t in _TYPES:
        now = len(cmds.ls(type=t) or [])
        assert now == baseline[t], f'{t}: {baseline[t]} -> {now}'


def test_free_ikarm_fingers_from_limb_node():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain('bikfing')
    fingers = [_add_finger(wrist, f'bikfing_f{i}') for i in range(3)]

    nodes.create_registry('bikfing_bp')
    _add_world_component('bikfing_world', root)
    # PINNED (Task 0): the component-add path that fires the implicit-limb
    # hook + finger discovery — plain create_component_node, verified
    # against nodes.py:407/754 (see _add_component_via_app's docstring).
    _add_component_via_app('bikfing_C0', 'IKArm', [shoulder, elbow, wrist])
    fs_app.build_modules()

    # Every member joint got a tagged FK ctrl, metacarpals included,
    # chain-parented under the wrist switch ctrl.
    for chain in fingers:
        for j in chain:
            assert cmds.objExists(f'{j}_ctrl'), j
    # Metacarpals excluded from curl by the heuristic: FK ctrl exists but
    # no incoming connection on the curl axis of its offset.
    for chain in fingers:
        meta_off = f'{chain[0]}_ctrl_offset'
        assert not cmds.listConnections(f'{meta_off}.rotateZ',
                                        source=True, destination=False)
    fs_app.unbuild_modules()


def test_free_ikarm_finger_cv_persistence():
    """HISTORY: written in Task 3 and deliberately left RED (no unbuild()
    override existed yet, so finger CV edits were provably lost across an
    unbuild/build round trip — confirmed empirically then). Task 4's
    IKArmComponent.unbuild landed the 'finger_ctrl_cv_data' capture
    (mirroring ribbon_ik_arm.py:571-623) and turned this green — this
    test IS Task 4's red-to-green evidence for the finger-CV half."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain('bikcv')
    chain = _add_finger(wrist, 'bikcv_f0')
    nodes.create_registry('bikcv_bp')
    _add_world_component('bikcv_world', root)
    _add_component_via_app('bikcv_C0', 'IKArm', [shoulder, elbow, wrist])
    fs_app.build_modules()

    ctrl = f'{chain[1]}_ctrl'
    # The default ctrl_shape (SIMPLE_IK_CONTRACT's, simple_ik.py:64 —
    # 'capsule' since the 2026-07-10 launch polish; was the 3-curve
    # 'sphere' gizmo) may put multiple curve shapes under one transform,
    # making '{ctrl}.cv[0]' ambiguous (Maya raises TypeError: 'Too many
    # objects or values'), unrelated to persistence. Address the first
    # shape explicitly, same fix the shipped RibbonIKArm CV test avoids
    # needing by using com.serialize_shape/cv[*] selection instead
    # (test_ribbon_ik_arm_maya.py:2433) — kept as pointPosition here to
    # match PLAN.md Task 3's literal assertion shape.
    shape = cmds.listRelatives(ctrl, shapes=True, fullPath=True)[0]
    cv0 = cmds.pointPosition(f'{shape}.cv[0]', world=True)
    cmds.move(0.37, 0.0, 0.0, f'{shape}.cv[0]', relative=True)
    moved = cmds.pointPosition(f'{shape}.cv[0]', world=True)
    fs_app.unbuild_modules()
    fs_app.build_modules()
    shape = cmds.listRelatives(ctrl, shapes=True, fullPath=True)[0]
    restored = cmds.pointPosition(f'{shape}.cv[0]', world=True)
    assert abs(restored[0] - moved[0]) < 1e-4, (cv0, moved, restored)
    fs_app.unbuild_modules()


def test_free_ikarm_layered_fist_curl():
    """Task 4 (PLAN.md): the fist-curl master behaves identically to the
    ribbon arm's (same build_fingers_ctrl helper via build_hand_from_limb)
    — only the curl axis keyable, included phalanges curl with the master,
    a per-finger key SUMS on top (superposition, not override), excluded
    metacarpals receive nothing."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain('bikcurl')
    chain = _add_finger(wrist, 'bikcurl_f0')
    nodes.create_registry('bikcurl_bp')
    _add_world_component('bikcurl_world', root)
    _add_component_via_app('bikcurl_C0', 'IKArm', [shoulder, elbow, wrist])
    fs_app.build_modules()

    master = f'{wrist}_fingers_ctrl'
    assert cmds.objExists(master), master
    # Only the curl axis (default 'z') is keyable; everything else locked.
    assert cmds.getAttr(f'{master}.rz', keyable=True)
    for locked in ('tx', 'ty', 'tz', 'rx', 'ry', 'sx', 'sy', 'sz'):
        assert cmds.getAttr(f'{master}.{locked}', lock=True), locked

    phal = chain[1]           # first non-metacarpal phalange
    r0 = cmds.getAttr(f'{phal}.rotateZ')
    cmds.setAttr(f'{master}.rotateZ', 40.0)
    r_master = cmds.getAttr(f'{phal}.rotateZ')
    assert abs((r_master - r0) - 40.0) < 1e-3, (r0, r_master)
    # Per-finger key SUMS on top (superposition, not override).
    cmds.setAttr(f'{phal}_ctrl.rotateZ', 10.0)
    r_both = cmds.getAttr(f'{phal}.rotateZ')
    assert abs((r_both - r0) - 50.0) < 1e-3, (r0, r_both)
    # Metacarpal untouched by the master.
    assert abs(cmds.getAttr(f'{chain[0]}.rotateZ')) < 1e-6
    fs_app.unbuild_modules()


def test_free_ikarm_curl_bucket_and_fingers_ctrl_cv_round_trip():
    """Task 4 (PLAN.md): (a) seeding a phalange with a nonzero bind
    rotation forces build_fingers_ctrl's plusMinusAverage 'authored
    baseline' branch (plus its auto-inserted unitConversions) — all
    tracked in 'curl_dg_nodes' and swept by unbuild back to baseline
    counts; (b) an authored fingers_ctrl CV edit round-trips through
    unbuild -> build via 'fingers_ctrl_cv_data'; (c) unbuild leaves no
    constraints on the bind finger joints."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain('bikfcv')
    chain = _add_finger(wrist, 'bikfcv_f0')
    # Nonzero bind rotation on the first phalange -> matchTransform bakes
    # it onto that ctrl_offset -> curl wiring takes the pma branch.
    cmds.setAttr(f'{chain[1]}.rotateZ', 7.0)
    baseline_pma = len(cmds.ls(type='plusMinusAverage') or [])
    baseline_uc = len(cmds.ls(type='unitConversion') or [])

    nodes.create_registry('bikfcv_bp')
    _add_world_component('bikfcv_world', root)
    _add_component_via_app('bikfcv_C0', 'IKArm', [shoulder, elbow, wrist])
    fs_app.build_modules()

    # The seeded baseline genuinely produced the pma branch.
    assert len(cmds.ls(type='plusMinusAverage') or []) > baseline_pma

    master = f'{wrist}_fingers_ctrl'
    shape = cmds.listRelatives(master, shapes=True, fullPath=True)[0]
    cmds.move(0.4, 0.0, 0.0, f'{shape}.cv[0]', relative=True)
    moved = cmds.pointPosition(f'{shape}.cv[0]', world=True)

    fs_app.unbuild_modules()
    # Curl bucket fully swept — pma AND its unitConversions.
    assert len(cmds.ls(type='plusMinusAverage') or []) == baseline_pma
    assert len(cmds.ls(type='unitConversion') or []) == baseline_uc
    # No constraints left behind on the bind finger joints.
    for j in chain:
        assert not (cmds.listRelatives(j, type='parentConstraint') or []), j
        assert not (cmds.listRelatives(j, type='scaleConstraint') or []), j

    fs_app.build_modules()
    shape = cmds.listRelatives(master, shapes=True, fullPath=True)[0]
    restored = cmds.pointPosition(f'{shape}.cv[0]', world=True)
    assert abs(restored[0] - moved[0]) < 1e-4, (moved, restored)
    fs_app.unbuild_modules()


def _add_twist_joints(elbow, wrist, prefix, fractions=(1.0 / 3.0, 2.0 / 3.0)):
    """Twist joints as children of the forearm bone (elbow), placed at
    fractions along elbow->wrist — what the limb dial's follower
    distribute rules produce at armature time (PLAN.md Task 5)."""
    import maya.cmds as cmds
    ep = cmds.xform(elbow, q=True, ws=True, t=True)
    wp = cmds.xform(wrist, q=True, ws=True, t=True)
    out = []
    for i, t in enumerate(fractions):
        pos = [ep[k] + (wp[k] - ep[k]) * t for k in range(3)]
        cmds.select(elbow)
        j = cmds.joint(name=f'{prefix}_twist_{i:02d}', p=pos)
        cmds.select(clear=True)
        out.append(j)
    return out


def _connect_twist_members(shoulder, twist_joints, segment='lower'):
    """Attach twist members to the component's (implicit) limb node the
    way the limb dials would — limb_node's own add_ API (pinned getters,
    limb_node.py at the fabricator ROOT)."""
    from maya_tools.rigging.fabricator import limb_node as ln
    limb = ln.find_limb_for_joint(shoulder)
    assert limb, f'no limb node resolvable from {shoulder!r}'
    add = ln.add_twist_lower if segment == 'lower' else ln.add_twist_upper
    for j in twist_joints:
        add(limb, j)


def _bone_axis_letter_of(joint):
    """Test-local mirror of modules/ik_arm.py's _bone_axis_letter."""
    import maya.cmds as cmds
    t = cmds.getAttr(f'{joint}.translate')[0]
    return 'xyz'[max(range(3), key=lambda i: abs(t[i]))]


def test_free_ikarm_forearm_fractional_twist_ik_and_fk():
    """Task 5 (PLAN.md / SPEC §3.4): each twist_lower member rotates
    t x the wrist bind joint's bone-axis rotation — in IK and in FK
    (wrist bind is post-blend)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain('biktw')
    twists = _add_twist_joints(elbow, wrist, 'biktw')
    nodes.create_registry('biktw_bp')
    _add_world_component('biktw_world', root)
    _add_component_via_app('biktw_C0', 'IKArm', [shoulder, elbow, wrist])
    _connect_twist_members(shoulder, twists, segment='lower')
    fs_app.build_modules()

    axis = _bone_axis_letter_of(wrist).upper()
    # IK mode: pronate via the IK ctrl.
    cmds.setAttr(f'{wrist}_IK_ctrl.rotate{axis}', 60.0)
    w = cmds.getAttr(f'{wrist}.rotate{axis}')
    r = [cmds.getAttr(f'{j}.rotate{axis}') for j in twists]
    assert abs(w) > 1.0, f'wrist never pronated in IK (w={w})'
    assert abs(r[0] - w / 3.0) < 0.5 and abs(r[1] - w * 2.0 / 3.0) < 0.5, (r, w)
    cmds.setAttr(f'{wrist}_IK_ctrl.rotate{axis}', 0.0)

    # FK mode: same contract through the blend.
    cmds.setAttr(f'{wrist}_IKFK_ctrl.ik_fk_blend', 0.0)
    cmds.setAttr(f'{wrist}_FK_ctrl.rotate{axis}', 60.0)
    w = cmds.getAttr(f'{wrist}.rotate{axis}')
    r = [cmds.getAttr(f'{j}.rotate{axis}') for j in twists]
    assert abs(w) > 1.0, f'wrist never pronated in FK (w={w})'
    assert abs(r[0] - w / 3.0) < 0.5 and abs(r[1] - w * 2.0 / 3.0) < 0.5, (r, w)
    fs_app.unbuild_modules()


def test_free_ikarm_twist_zero_members_zero_nodes_and_unbuild_restores():
    """Task 5 (PLAN.md): zero twist members -> zero twist nodes; with
    members, the scalar chain appears on build, is fully swept on unbuild
    (bucket incl. unitConversions), and the twist joints keep no incoming
    connections. twist_upper members ride rigid v1 (no wiring)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    cmds.file(new=True, force=True)
    root, shoulder, elbow, wrist = _make_arm_chain('biktw0')
    base_mdl = len(cmds.ls(type='multDoubleLinear') or [])
    base_uc = len(cmds.ls(type='unitConversion') or [])

    nodes.create_registry('biktw0_bp')
    _add_world_component('biktw0_world', root)
    _add_component_via_app('biktw0_C0', 'IKArm', [shoulder, elbow, wrist])
    fs_app.build_modules()
    assert len(cmds.ls(type='multDoubleLinear') or []) == base_mdl, \
        'zero members must build zero twist nodes'
    fs_app.unbuild_modules()

    # With members: nodes appear on build, vanish on unbuild; upper
    # members stay rigid (unwired) per SPEC §3.4 v1.
    twists = _add_twist_joints(elbow, wrist, 'biktw0')
    uppers = _add_twist_joints(shoulder, elbow, 'biktw0_up', fractions=(0.5,))
    _connect_twist_members(shoulder, twists, segment='lower')
    _connect_twist_members(shoulder, uppers, segment='upper')
    fs_app.build_modules()
    assert len(cmds.ls(type='multDoubleLinear') or []) > base_mdl
    axis = _bone_axis_letter_of(wrist).upper()
    for j in uppers:
        assert not cmds.listConnections(f'{j}.rotate{axis}', source=True,
                                        destination=False), \
            f'twist_upper member {j} must ride rigid in v1'
    fs_app.unbuild_modules()
    assert len(cmds.ls(type='multDoubleLinear') or []) == base_mdl
    assert len(cmds.ls(type='unitConversion') or []) == base_uc
    for j in twists:
        assert cmds.objExists(j), f'twist joint deleted by unbuild: {j}'
        assert not cmds.listConnections(f'{j}.rotate{axis}', source=True,
                                        destination=False), j

    # REBUILD-REVERIFY (review finding 2026-07-10): _build_fractional_twist
    # skips any already-driven channel with only a warning — if unbuild's
    # sweep ever left a stale mult connected, the next build would silently
    # leave that joint unwired forever. Prove the round trip: build again
    # on the SAME membership and re-assert the live fractional ratios.
    fs_app.build_modules()
    cmds.setAttr(f'{wrist}_IK_ctrl.rotate{axis}', 40.0)
    w = cmds.getAttr(f'{wrist}.rotate{axis}')
    r = [cmds.getAttr(f'{j}.rotate{axis}') for j in twists]
    assert abs(w) > 1.0, f'wrist never pronated on rebuild (w={w})'
    assert abs(r[0] - w / 3.0) < 0.5 and abs(r[1] - w * 2.0 / 3.0) < 0.5, (
        'twist wiring did not survive an unbuild->build round trip', r, w)
    fs_app.unbuild_modules()


def test_arm_limb_fragment_drops_and_builds():
    """The shipped canon Simple_Arm fragment (Derived Limbs, 2026-07-11
    -- the interim arm.limb.yaml is retired) round-trips through the
    real loader and builder -- drops onto a host's anchor_out plug and
    Build Modules produces a real IKArm. limbs.io is the only yaml-dependent import in this path
    (limbs.builder has none -- confirmed against its own module
    imports) -- guarded here so a yaml-less mayapy SKIPs cleanly
    instead of failing on an unrelated ImportError, matching the
    finger-fragment precedent in _dev/test_limb_units_maya.py.
    set_registry_root_joint is required here (unlike every other test
    in this file) because apply_limb_fragment's own body calls
    armature.build_armature() with no root= argument, which raises
    RuntimeError without a registry root joint set -- pinned against
    _dev/test_limb_units_maya.py's own
    test_limb_fragment_drop_creates_limb_with_correct_connections."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment

    try:
        from maya_tools.rigging.fabricator.limbs import io as limb_io
    except ModuleNotFoundError as exc:
        raise Skip(f'yaml not importable under this mayapy: {exc}')

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    host = cmds.joint(name='armfrag_clav', p=(0, 10, 0))
    cmds.select(clear=True)
    nodes.create_registry('armfrag_bp')
    nodes.set_registry_root_joint(host)
    _add_world_component('armfrag_world', host)

    frag = limb_io.read_yaml(
        'maya_tools/rigging/fabricator/templates/Simple_Arm.limb.yaml')
    apply_limb_fragment(frag, host)

    assert cmds.objExists('upperarm_l') and cmds.objExists('hand_l')
    fs_app.build_modules()
    assert cmds.objExists('hand_l_IK_ctrl')
    fs_app.unbuild_modules()


def test_free_only_module_dir_simulated_install_discovery_and_build():
    """Retires the standing t31 skip (the old anti-crippleware gate raised
    a documented Skip whenever component auto-discovery had ALREADY
    imported every modules/*.py — including the paid ribbon set — before
    this test's own build ran, since the dev repo's modules/ directory
    physically contains the paid .py files on disk). Ribbon-pack
    packaging SPEC §6.3 retires that skip for good: under the no-DRM
    open-core model, a REAL free install's modules/ directory simply
    LACKS the paid files (package_release.py's manifest-driven exclusion,
    paid_manifest.json). Reproduce that exact file-presence contract
    directly instead of asserting around dev-repo pre-population:

      1. Read paid_manifest.json (the packaging boundary's single source
         of truth) for the paid modules/ file stems.
      2. Stage a scratch copy of modules/ with those stems removed —
         a literal free-only modules/ directory, byte-for-byte the free
         files, nothing invented.
      3. Monkeypatch pkgutil.iter_modules so modules/__init__.py's own
         _discover() (UNMODIFIED) enumerates the staged directory instead
         of the real one. iter_modules() over the staged dir structurally
         never yields the paid module names — no ImportError, no Skip,
         just absence, exactly what a real free install's on-disk
         modules/ folder gives pkgutil for free.
      4. Assert the resulting registry contains zero paid component
         types, and a free biped (World + IKArm + IKLeg) still builds and
         unbuilds cleanly off that restricted registry.
      5. Assert the registry raises KeyError for every paid type — the
         free registry cannot resolve a component it never discovered.

    Always restores the real registry in a `finally` regardless of
    outcome, since this mutates process-global module state
    (pkgutil.iter_modules, modules.__init__'s _REGISTRY) other tests in
    this same mayapy process could depend on.
    """
    import json
    import pkgutil
    import shutil
    import tempfile
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    from maya_tools.rigging.fabricator import modules as fab_modules

    manifest_path = (REPO_ROOT / 'maya_tools' / 'rigging' / 'fabricator'
                      / 'paid_manifest.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    modules_prefix = 'maya_tools/rigging/fabricator/modules/'
    paid_module_stems = {
        Path(p).stem for p in manifest['files']
        if p.replace('\\', '/').startswith(modules_prefix)
    }
    assert paid_module_stems, (
        'manifest produced zero paid module stems — check the '
        'modules/ prefix match against paid_manifest.json\'s files list')

    # Real (pre-restriction) registry -> which CONTRACT.type strings the
    # paid module stems actually produce. Derived from the LIVE registry
    # rather than hardcoded literals, so this test tracks any future
    # component-type rename automatically.
    real_paid_types = set()
    for cls in fab_modules.all_component_classes():
        mod_stem = cls.__module__.rsplit('.', 1)[-1]
        if mod_stem in paid_module_stems:
            real_paid_types.add(cls.CONTRACT.type)
    assert real_paid_types, (
        'no CONTRACT.type resolved from the manifest\'s paid module '
        'stems against the current (unrestricted) registry')

    real_modules_dir = str(Path(fab_modules.__file__).parent)
    real_iter_modules = pkgutil.iter_modules

    def _patched_iter_modules(path=None, prefix=''):
        if path == [real_modules_dir]:
            return real_iter_modules([staged_dir], prefix)
        return real_iter_modules(path, prefix)

    staged_root = tempfile.mkdtemp(prefix='fab_free_modules_')
    staged_dir = staged_root  # plain str, matches pkgutil's own path list shape
    try:
        for py_file in Path(real_modules_dir).glob('*.py'):
            if py_file.stem in paid_module_stems:
                continue
            shutil.copy2(py_file, Path(staged_dir) / py_file.name)

        pkgutil.iter_modules = _patched_iter_modules
        try:
            fab_modules.reload_all()
            registry_types = set(fab_modules.all_component_types())
        finally:
            pkgutil.iter_modules = real_iter_modules

        leaked = real_paid_types & registry_types
        assert not leaked, (
            f'simulated free-only discovery still exposed: {sorted(leaked)}')
        assert 'IKArm' in registry_types and 'IKLeg' in registry_types

        # A free biped build must still succeed off that restricted
        # registry — the real runtime contract, not just a static list.
        pkgutil.iter_modules = _patched_iter_modules
        try:
            fab_modules.reload_all()

            cmds.file(new=True, force=True)
            root, shoulder, elbow, wrist = _make_arm_chain('bikfreeonly')
            _add_finger(wrist, 'bikfreeonly_f0')
            twists = _add_twist_joints(elbow, wrist, 'bikfreeonly')
            cmds.select(root)
            hip = cmds.joint(name='bikfreeonly_hip', p=(2, 90, 0))
            knee = cmds.joint(name='bikfreeonly_knee', p=(2, 50, 4))
            ankle = cmds.joint(name='bikfreeonly_ankle', p=(2, 10, 0))
            ball = cmds.joint(name='bikfreeonly_ball', p=(2, 2, 12))
            cmds.select(clear=True)

            nodes.create_registry('bikfreeonly_bp')
            _add_world_component('bikfreeonly_world', root)
            _add_component_via_app('bikfreeonly_arm', 'IKArm', [shoulder, elbow, wrist])
            _connect_twist_members(shoulder, twists, segment='lower')
            nodes.create_component_node(
                component_id='bikfreeonly_leg', component_type='IKLeg',
                joints=[hip, knee, ankle, ball], parent_plug='', side='md',
                options={}, persisted={},
            )
            fs_app.build_modules()
            assert cmds.objExists(f'{wrist}_IK_ctrl')
            assert cmds.objExists(f'{wrist}_fingers_ctrl')
            assert cmds.objExists(f'{ankle}_IK_ctrl')
            fs_app.unbuild_modules()

            for paid_type in sorted(real_paid_types):
                try:
                    fab_modules.get_component_class(paid_type)
                except KeyError:
                    pass
                else:
                    raise AssertionError(
                        f'{paid_type} resolvable from the simulated '
                        f'free-only registry — discovery leak')
        finally:
            pkgutil.iter_modules = real_iter_modules
    finally:
        shutil.rmtree(staged_root, ignore_errors=True)
        fab_modules.reload_all()  # restore the real, full registry


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    check("test_free_ikarm_parity_build_switch_pv_unbuild",
          test_free_ikarm_parity_build_switch_pv_unbuild)
    check("test_free_ikarm_unbuild_leaves_zero_orphans",
          test_free_ikarm_unbuild_leaves_zero_orphans)
    check("test_free_ikarm_fingers_from_limb_node",
          test_free_ikarm_fingers_from_limb_node)
    check("test_free_ikarm_finger_cv_persistence",
          test_free_ikarm_finger_cv_persistence)
    check("test_free_ikarm_layered_fist_curl",
          test_free_ikarm_layered_fist_curl)
    check("test_free_ikarm_curl_bucket_and_fingers_ctrl_cv_round_trip",
          test_free_ikarm_curl_bucket_and_fingers_ctrl_cv_round_trip)
    check("test_free_ikarm_forearm_fractional_twist_ik_and_fk",
          test_free_ikarm_forearm_fractional_twist_ik_and_fk)
    check("test_free_ikarm_twist_zero_members_zero_nodes_and_unbuild_restores",
          test_free_ikarm_twist_zero_members_zero_nodes_and_unbuild_restores)
    check("test_arm_limb_fragment_drops_and_builds",
          test_arm_limb_fragment_drops_and_builds)
    check("test_free_only_module_dir_simulated_install_discovery_and_build",
          test_free_only_module_dir_simulated_install_discovery_and_build)

    if FAILURES:
        print(f"IK ARM (FREE) SCENE TESTS: {len(FAILURES)} FAILED "
              f"({len(SKIPS)} SKIP)")
        sys.exit(1)
    if SKIPS:
        print(f"IK ARM (FREE) SCENE TESTS: OK - {len(SKIPS)} SKIP "
              f"(not counted as pass): {SKIPS}")
    else:
        print("IK ARM (FREE) SCENE TESTS: OK - 0 SKIP")


if __name__ == "__main__":
    main()
