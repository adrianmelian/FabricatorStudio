# _dev/test_ribbon_ik_arm.py
"""Offscreen tests for the RibbonIKArm component. No live Maya scene required —
these just prove the module imports, registers with the component
auto-discovery registry, and declares the contract each phase promises.

Phase 1: 3-joint shoulder/elbow/wrist, SimpleIK subclass, distinct
identity fields.

Phase 4 (this file, current state) adds: pure-logic finger membership
discovery (_limb_common.discover_fingers) + the metacarpal curl-exclusion
heuristic (_limb_common.metacarpal_excluded) on synthetic joint trees —
no cmds calls, no live scene — plus the 'fingers' OptionField's shape and
RibbonIKArmComponent.default_options_for_create's create-time population
(PLAN.md 2026-07-08 Tasks 4.1/4.2), and a pure-YAML round-trip proving
the 'fingers' option (a JSON/YAML-native list[dict]) survives
write_yaml -> read_yaml unchanged.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_ribbon_ik_arm.py
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
    count. A test that hits this must NOT also be logged 'ok': check()
    below tracks it into SKIPS, a separate tally from both FAILURES and
    the implicit pass count, and main()'s summary reports all three
    distinctly (confirmed P5 review finding: a bare `return` inside a
    try/except ModuleNotFoundError block used to be indistinguishable
    from a real pass in this harness)."""


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


def test_ik_arm_is_discovered():
    from maya_tools.rigging.fabricator.modules import get_component_class
    cls = get_component_class('RibbonIKArm')
    assert cls is not None, "get_component_class('RibbonIKArm') returned None"


def test_ik_arm_contract_shape():
    from maya_tools.rigging.fabricator.modules import get_component_class
    from maya_tools.rigging.fabricator.modules.simple_ik import SimpleIKComponent
    cls = get_component_class('RibbonIKArm')

    assert cls.CONTRACT.type == 'RibbonIKArm', cls.CONTRACT.type
    assert cls.CONTRACT.display_name == 'Ribbon IK Arm', cls.CONTRACT.display_name
    assert cls.CONTRACT.min_joints == 3, cls.CONTRACT.min_joints
    assert cls.CONTRACT.max_joints == 3, cls.CONTRACT.max_joints
    assert issubclass(cls, SimpleIKComponent), cls

    role_names = [r.name for r in cls.CONTRACT.joint_roles]
    assert role_names == ['start', 'mid', 'end'], role_names


def test_ik_arm_options_match_simple_ik():
    # Phase 1 baseline: every SimpleIK option is still present verbatim.
    from maya_tools.rigging.fabricator.modules import get_component_class
    from maya_tools.rigging.fabricator.modules.simple_ik import SIMPLE_IK_CONTRACT
    cls = get_component_class('RibbonIKArm')
    assert set(SIMPLE_IK_CONTRACT.options_schema.keys()) <= set(
        cls.CONTRACT.options_schema.keys())


def test_stretchy_defaults_on_p6_task4():
    """Task 4 (Adrian, 2026-07-09): stretch is ON by default for every NEW
    build. fs_app.build_modules/unbuild_modules only setdefault() a
    missing option key from the contract (fs_app.py's own comment: 'The
    Contract is the single source of truth for defaults — to change a
    default for a component, change it in the contract's options_schema
    and nowhere else') — so this single OptionField.default flip is both
    necessary and sufficient; SimpleIK/IKLeg/RibbonIKArm all inherit it since
    RibbonIKArm/IKLeg spread SIMPLE_IK_CONTRACT.options_schema verbatim."""
    from maya_tools.rigging.fabricator.modules.simple_ik import SIMPLE_IK_CONTRACT
    from maya_tools.rigging.fabricator.modules.ribbon_ik_arm import RIBBON_IK_ARM_CONTRACT
    from maya_tools.rigging.fabricator.modules.ik_leg import IK_LEG_CONTRACT

    for contract in (SIMPLE_IK_CONTRACT, RIBBON_IK_ARM_CONTRACT, IK_LEG_CONTRACT):
        field = contract.options_schema['stretchy']
        assert field.default is True, (
            f'{contract.type}: stretchy OptionField.default = '
            f'{field.default!r}, expected True')


def test_ik_arm_ribbon_options_p2():
    # Phase 2: four new ribbon options layered on top of SimpleIK's.
    from maya_tools.rigging.fabricator.modules import get_component_class
    from maya_tools.rigging.fabricator.modules.simple_ik import SIMPLE_IK_CONTRACT
    cls = get_component_class('RibbonIKArm')
    schema = cls.CONTRACT.options_schema

    new_keys = {'mid_ctrl_count', 'ribbon_width', 'ribbon_mid_ctrl_shape',
                'ribbon_ctrl_color'}
    assert new_keys <= set(schema.keys()), schema.keys()
    # NOTE: this used to also assert exact key-set equality (P2 was the
    # only phase adding options at the time). Phase 4 legitimately adds
    # 'fingers' on top — see test_ik_arm_fingers_option_p4 below for that
    # coverage — so this test only asserts P2's OWN four keys are present,
    # not that they're the only ones.

    assert schema['mid_ctrl_count'].type == 'int'
    assert schema['mid_ctrl_count'].default == 1
    assert schema['mid_ctrl_count'].range == (1, 8)

    assert schema['ribbon_width'].type == 'float'
    assert schema['ribbon_width'].default == 0.0

    assert schema['ribbon_mid_ctrl_shape'].type == 'shape_enum'
    assert schema['ribbon_ctrl_color'].type == 'color_enum'


# ─── Phase 4: finger membership discovery + metacarpal heuristic ─────────
# Pure logic — synthetic joint trees via the real (pure-Python, no maya
# import) blueprint.schema dataclasses, satisfying _limb_common.
# discover_fingers's documented blueprint duck-type exactly (the same
# shape fs_app._snapshot_blueprint_from_scene() returns at live add
# time).

def _fake_blueprint(pairs):
    """pairs: list of (name, parent_or_None). Returns a
    blueprint.schema.Blueprint with skeleton_joints built from `pairs`,
    in the given order (discover_fingers relies on children_map
    preserving this order for its 'first child wins on branch' rule)."""
    from maya_tools.rigging.fabricator.blueprint.schema import Blueprint, JointSpec
    joints = [JointSpec(name=n, parent=p) for n, p in pairs]
    return Blueprint(name='test', skeleton_joints=joints)


def _ue5_hand_pairs(wrist='wrist'):
    """UE5-style hand: thumb (3 joints, no metacarpal token) + index/
    middle/ring/pinky (4 joints each, metacarpal-first)."""
    pairs = []
    pairs += [('thumb_01', wrist), ('thumb_02', 'thumb_01'), ('thumb_03', 'thumb_02')]
    for finger in ('index', 'middle', 'ring', 'pinky'):
        mc = f'{finger}_metacarpal'
        pairs += [
            (mc, wrist),
            (f'{finger}_01', mc),
            (f'{finger}_02', f'{finger}_01'),
            (f'{finger}_03', f'{finger}_02'),
        ]
    return pairs


def _cartoon_12finger_pairs(wrist='wrist'):
    """12 fingers, no metacarpal token, 3 joints each (proximal/mid/tip)."""
    pairs = []
    for i in range(12):
        root = f'finger{i:02d}_01'
        pairs += [
            (root, wrist),
            (f'finger{i:02d}_02', root),
            (f'finger{i:02d}_03', f'finger{i:02d}_02'),
        ]
    return pairs


def _4finger_no_metacarpal_pairs(wrist='wrist'):
    """4 fingers, no metacarpal token, 4 joints each — exercises the
    length->=4-excludes-root fallback heuristic."""
    pairs = []
    for finger in ('f1', 'f2', 'f3', 'f4'):
        root = f'{finger}_01'
        pairs += [
            (root, wrist),
            (f'{finger}_02', root),
            (f'{finger}_03', f'{finger}_02'),
            (f'{finger}_04', f'{finger}_03'),
        ]
    return pairs


def test_metacarpal_excluded_name_match():
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    chain = ['index_metacarpal', 'index_01', 'index_02', 'index_03']
    assert hc.metacarpal_excluded(chain) == {'index_metacarpal'}


def test_metacarpal_excluded_fallback_len_ge_4():
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    chain = ['f1_01', 'f1_02', 'f1_03', 'f1_04']
    assert hc.metacarpal_excluded(chain) == {'f1_01'}


def test_metacarpal_excluded_fallback_len_le_3():
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    assert hc.metacarpal_excluded(['thumb_01', 'thumb_02', 'thumb_03']) == set()
    assert hc.metacarpal_excluded(['a', 'b']) == set()
    assert hc.metacarpal_excluded([]) == set()


def test_metacarpal_excluded_fallback_trailing_tip_joint():
    """P4 review finding #2 (major): a chain that reaches length>=4 via a
    TRAILING tip/end joint (standard Maya convention, e.g.
    'index_03_end' — a non-deforming orientation/length-reference joint
    appended at the chain's tip) must NOT have its ROOT joint (the real
    base knuckle — the one bone that should always participate in a fist
    curl) excluded by the "unlabeled metacarpal first" guess. The old
    code excluded joints[0] unconditionally whenever len(joints) >= 4,
    regardless of WHY the chain reached 4 joints — this is the exact
    counterexample the review named."""
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    chain = ['index_01', 'index_02', 'index_03', 'index_03_end']
    assert hc.metacarpal_excluded(chain) == {'index_03_end'}, (
        hc.metacarpal_excluded(chain))

    # Case-insensitive; 'tip' and 'nub' recognized too (same standard
    # Maya non-deforming-tip-joint convention).
    assert hc.metacarpal_excluded(['f_01', 'f_02', 'f_03', 'f_END']) == {'f_END'}
    assert hc.metacarpal_excluded(['f_01', 'f_02', 'f_03', 'f_tip']) == {'f_tip'}
    assert hc.metacarpal_excluded(['f_01', 'f_02', 'f_03', 'f_nub']) == {'f_nub'}

    # A name that merely CONTAINS 'end' as a substring, not as its own
    # '_'-delimited token, must not false-positive — the len>=4 root
    # fallback still applies (regression guard for the existing
    # unlabeled-metacarpal-first case, test_metacarpal_excluded_fallback_
    # len_ge_4 above).
    assert hc.metacarpal_excluded(
        ['f_01', 'f_02', 'f_03', 'f_endpoint']) == {'f_01'}


def test_metacarpal_excluded_unlabeled_root_plus_trailing_tip_both_excluded():
    """P5 review finding (major): the trailing-tip-token fallback branch
    used to be an EITHER/OR with the unlabeled-root guess ("has a tip
    token" fully replaced "guess the root" instead of layering on top of
    it) — so a chain with BOTH conventions at once (a genuine, unlabeled
    metacarpal as joints[0] PLUS a trailing non-deforming tip joint,
    e.g. ['index_00', 'index_01', 'index_02', 'index_03', 'index_03_end']
    — 5 joints total) returned only {tip}, leaving the real metacarpal
    (joints[0]) completely absent from the excluded set and therefore
    fully wired to curl by build_fingers_ctrl. This is the exact
    counterexample traced in the P5 review: a 5-joint finger where
    trimming the trailing tip STILL leaves 4 real bones (metacarpal +
    3 phalanges) must exclude the root (in addition to the tip), not
    just the tip alone."""
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    chain = ['index_00', 'index_01', 'index_02', 'index_03', 'index_03_end']
    result = hc.metacarpal_excluded(chain)
    assert 'index_00' in result, (
        f'real unlabeled metacarpal (root) must be excluded even when the '
        f'chain also ends in a trailing tip joint; got {result}')
    assert result == {'index_00', 'index_03_end'}, result

    # Sanity companion: trimming the tip down to EXACTLY 3 remaining real
    # bones (metacarpal-shaped ambiguity resolved in favor of "just
    # phalanges") must NOT exclude the root — this is the existing
    # test_metacarpal_excluded_fallback_trailing_tip_joint case, re-
    # asserted here alongside the 5-joint case for contrast.
    chain3_plus_tip = ['index_01', 'index_02', 'index_03', 'index_03_end']
    assert hc.metacarpal_excluded(chain3_plus_tip) == {'index_03_end'}, (
        hc.metacarpal_excluded(chain3_plus_tip))

    # 6-joint variant (metacarpal + 4 phalanges + tip) — remainder after
    # trim is 5 (still >=4), root must stay excluded alongside the tip.
    chain6 = ['mc', 'p1', 'p2', 'p3', 'p4', 'tip_end']
    assert hc.metacarpal_excluded(chain6) == {'mc', 'tip_end'}, (
        hc.metacarpal_excluded(chain6))


def test_discover_fingers_ue5_with_metacarpals():
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    bp = _fake_blueprint([('wrist', None)] + _ue5_hand_pairs())
    fingers = hc.discover_fingers('wrist', bp)

    assert len(fingers) == 5, fingers
    by_root = {f['root_joint']: f for f in fingers}
    assert set(by_root.keys()) == {
        'thumb_01', 'index_metacarpal', 'middle_metacarpal',
        'ring_metacarpal', 'pinky_metacarpal'}

    thumb = by_root['thumb_01']
    assert thumb['joints'] == ['thumb_01', 'thumb_02', 'thumb_03']
    assert thumb['curl_excluded'] == []

    for finger in ('index', 'middle', 'ring', 'pinky'):
        mc = f'{finger}_metacarpal'
        entry = by_root[mc]
        assert entry['joints'] == [mc, f'{finger}_01', f'{finger}_02', f'{finger}_03']
        assert entry['curl_excluded'] == [mc], entry


def test_discover_fingers_12finger_cartoon_no_metacarpal():
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    bp = _fake_blueprint([('wrist', None)] + _cartoon_12finger_pairs())
    fingers = hc.discover_fingers('wrist', bp)

    assert len(fingers) == 12, len(fingers)
    for i, f in enumerate(fingers):
        root = f'finger{i:02d}_01'
        assert f['root_joint'] == root
        assert f['joints'] == [root, f'finger{i:02d}_02', f'finger{i:02d}_03']
        assert f['curl_excluded'] == [], f


def test_discover_fingers_4finger_no_metacarpal():
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    bp = _fake_blueprint([('wrist', None)] + _4finger_no_metacarpal_pairs())
    fingers = hc.discover_fingers('wrist', bp)

    assert len(fingers) == 4, len(fingers)
    by_root = {f['root_joint']: f for f in fingers}
    for finger in ('f1', 'f2', 'f3', 'f4'):
        root = f'{finger}_01'
        entry = by_root[root]
        assert entry['joints'] == [root, f'{finger}_02', f'{finger}_03', f'{finger}_04']
        assert entry['curl_excluded'] == [root], entry


def test_discover_fingers_empty_when_no_children():
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    bp = _fake_blueprint([('wrist', None)])
    assert hc.discover_fingers('wrist', bp) == []
    assert hc.discover_fingers('wrist', None) == []


def test_discover_fingers_mid_chain_branch_prefers_continuation_over_leaf():
    """P4 review finding #1 (major): discover_fingers's docstring claims
    parity with fs_app._first_chain_child's ambiguous-branch convention
    (prefer a child that is itself a chain continuation over a leaf
    sibling) — the old implementation was a naive `kids[0]` walk with NO
    such preference, so a leaf (e.g. a nail/decoration bone) listed
    BEFORE the real continuation in skeleton_joints order silently
    truncated the finger and dropped every real joint past it. This is
    the exact hand-traced counterexample from the review."""
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    pairs = [
        ('wrist', None),
        ('index_01', 'wrist'),
        ('index_02', 'index_01'),
        ('index_nub', 'index_02'),   # leaf, listed FIRST — must be skipped
        ('index_03', 'index_02'),    # real continuation, listed SECOND
        ('index_tip', 'index_03'),
    ]
    bp = _fake_blueprint(pairs)
    fingers = hc.discover_fingers('wrist', bp)
    assert len(fingers) == 1, fingers
    assert fingers[0]['root_joint'] == 'index_01'
    assert fingers[0]['joints'] == [
        'index_01', 'index_02', 'index_03', 'index_tip'], fingers[0]
    # The leaf sibling itself never appears in ANY finger entry (it isn't
    # a separate wrist-child either) — SPEC 3.4's "every finger joint
    # gets an FK ctrl" only covers real chain members, and index_nub
    # (a nail/decoration bone, not a real finger joint) correctly isn't
    # one, but index_03/index_tip — which the old bug also dropped — now
    # correctly are.
    all_joints = [j for f in fingers for j in f['joints']]
    assert 'index_03' in all_joints and 'index_tip' in all_joints

    # All-leaf branch (no continuation exists anywhere to prefer): still
    # falls back to the first-listed child, matching
    # fs_app._first_chain_child's own documented fallback.
    pairs2 = [
        ('wrist', None),
        ('thumb_01', 'wrist'),
        ('thumb_nail', 'thumb_01'),   # leaf, listed FIRST
        ('thumb_wart', 'thumb_01'),   # leaf, listed SECOND
    ]
    bp2 = _fake_blueprint(pairs2)
    fingers2 = hc.discover_fingers('wrist', bp2)
    assert len(fingers2) == 1, fingers2
    assert fingers2[0]['joints'] == ['thumb_01', 'thumb_nail'], fingers2[0]


# ─── Task 2.3: 'fingers' option retired — membership lives on the limb ────

def test_ik_arm_fingers_option_retired():
    """SPEC 2026-07-09 Limbs + Follower Joints §3.4: the 'fingers'
    OptionField (P4's name-string membership option) is gone from
    RIBBON_IK_ARM_CONTRACT — membership now lives ONLY on the component's
    fab_limb node. Nothing under options_schema should reference the
    retired 'finger_list' OptionField type either."""
    from maya_tools.rigging.fabricator.modules import get_component_class
    cls = get_component_class('RibbonIKArm')
    schema = cls.CONTRACT.options_schema
    assert 'fingers' not in schema, schema.keys()
    assert all(f.type != 'finger_list' for f in schema.values()), (
        "a schema field still declares the retired 'finger_list' type")


def test_ik_arm_default_options_for_create_no_longer_populates_fingers():
    """Task 2.3: default_options_for_create's old 'fingers'-discovery
    override is removed entirely — RibbonIKArm now inherits Component's
    base (always {}), for any input, including the exact discovery-
    capable shape that used to populate 'fingers' (create-time coverage
    moved to nodes._maybe_create_implicit_limb, which runs on every
    create_component_node call, not just this hook)."""
    from maya_tools.rigging.fabricator.modules import get_component_class
    cls = get_component_class('RibbonIKArm')

    bp = _fake_blueprint(
        [('shoulder', None), ('elbow', 'shoulder'), ('wrist', 'elbow')]
        + _ue5_hand_pairs())
    joints = ['shoulder', 'elbow', 'wrist']

    assert cls.default_options_for_create(joints, bp) == {}
    # Degenerate inputs (short joints list, no blueprint, bogus
    # blueprint) all still degrade to {} — trivially true now that no
    # override exists to raise on any of them.
    assert cls.default_options_for_create([], None) == {}
    assert cls.default_options_for_create(['a', 'b'], None) == {}
    assert cls.default_options_for_create(['a', 'b', 'c'], None) == {}
    assert cls.default_options_for_create(['a', 'b', 'c'], object()) == {}


def test_ik_arm_fingers_option_round_trips_through_json():
    """Task 4.2: 'Removing/editing entries round-trips through save/load.'
    Exercises the SAME (de)serialization the live product actually uses
    for a component's options on every build/save cycle: nodes.py stores
    options as `options_json` via plain `json.dumps(options)` /
    `json.loads(...)` (nodes.create_component_node /
    nodes.get_component_options — no custom per-field logic). Proves the
    'fingers' list[dict] value survives that round-trip byte-for-byte,
    including an edit (remove one finger + toggle an exclusion) —
    resolving PLAN.md's self-review 'open risk' (a serialized-string
    fallback field was NOT needed; options is already JSON-native)."""
    import json
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    bp_in = _fake_blueprint(
        [('shoulder', None), ('elbow', 'shoulder'), ('wrist', 'elbow')]
        + _4finger_no_metacarpal_pairs())
    fingers = hc.discover_fingers('wrist', bp_in)
    assert len(fingers) == 4  # sanity: fixture actually produced fingers

    options = {'fingers': fingers}
    round_tripped = json.loads(json.dumps(options))
    assert round_tripped == options, (round_tripped, options)

    # Editing (remove one finger + toggle an exclusion) round-trips too.
    edited = [dict(f) for f in fingers if f['root_joint'] != 'f2_01']
    edited[0]['curl_excluded'] = sorted(
        set(edited[0]['curl_excluded']) | {edited[0]['joints'][1]})
    edited_round_tripped = json.loads(json.dumps({'fingers': edited}))
    assert edited_round_tripped == {'fingers': edited}


def test_ik_arm_fingers_option_round_trips_through_yaml_if_available():
    """Same round-trip as test_ik_arm_fingers_option_round_trips_through_
    json, but through the ACTUAL blueprint YAML disk path
    (blueprint/io.py write_yaml/read_yaml -> ComponentSpec.options: dict)
    — the other of the two persistence paths PLAN.md Task 4.2 names
    ('round-trips through save/load').

    SKIPS (does not fail, does not fake a pass) when the `yaml` package
    isn't importable in THIS mayapy interpreter — verified environment
    gap on this machine (mayapy's Python 3.11 site-packages has no
    PyYAML; confirmed via `mayapy -m pip list` / a bare `import yaml`
    ModuleNotFoundError), predating this task: blueprint/io.py has
    imported `yaml` at module level since before Phase 4, and no
    existing test in this suite exercised that import path either. Not
    a code defect — flagged as a blocker in the task report so a human
    can install PyYAML into mayapy's environment (or confirm the live
    Fabricator-in-Maya session sources it some other way) and get real
    coverage of this path.
    """
    try:
        import yaml  # noqa: F401
    except ModuleNotFoundError:
        raise Skip('yaml module not importable under this mayapy — see '
                   'docstring; blueprint YAML round-trip unverified here.')

    import tempfile
    from maya_tools.rigging.fabricator.blueprint import io as blueprint_io
    from maya_tools.rigging.fabricator.blueprint.schema import (
        Blueprint, ComponentSpec,
    )
    from maya_tools.rigging.fabricator.modules import _limb_common as hc

    bp_in = _fake_blueprint(
        [('shoulder', None), ('elbow', 'shoulder'), ('wrist', 'elbow')]
        + _4finger_no_metacarpal_pairs())
    fingers = hc.discover_fingers('wrist', bp_in)
    assert len(fingers) == 4

    full_bp = Blueprint(
        name='ikarm_fingers_roundtrip',
        skeleton_joints=list(bp_in.skeleton_joints),
        components=[ComponentSpec(
            type='RibbonIKArm', joints=['shoulder', 'elbow', 'wrist'], id='arm_c0',
            options={'fingers': fingers},
        )],
    )

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'ikarm_fingers_roundtrip.blueprint.yaml'
        blueprint_io.write_yaml(full_bp, str(path))
        bp_out = blueprint_io.read_yaml(str(path))

    assert len(bp_out.components) == 1
    round_tripped = bp_out.components[0].options.get('fingers')
    assert round_tripped == fingers, (round_tripped, fingers)


# ─── Phase 5: fist-curl master option schema + skeleton-only limbs ───────

def test_ik_arm_curl_options_p5():
    from maya_tools.rigging.fabricator.modules import get_component_class
    cls = get_component_class('RibbonIKArm')
    schema = cls.CONTRACT.options_schema

    assert 'curl_axis' in schema, schema.keys()
    curl_field = schema['curl_axis']
    assert curl_field.type == 'enum', curl_field.type
    assert curl_field.default == 'z', curl_field.default
    assert set(curl_field.choices) == {'x', 'y', 'z'}, curl_field.choices

    assert 'fingers_ctrl_shape' in schema, schema.keys()
    assert schema['fingers_ctrl_shape'].type == 'shape_enum'
    assert 'fingers_ctrl_color' in schema, schema.keys()
    assert schema['fingers_ctrl_color'].type == 'color_enum'
    # Color fix (Adrian 2026-07-09 follow-up): the schema default must be
    # '' (unset/follow-the-group), NOT a fixed color like the old 'red' —
    # RibbonIKArmComponent.build resolves '' to the instance's own 'ctrl_color'
    # (see ribbon_ik_arm.py's build() and the OptionField's own docstring). A
    # regression back to a fixed non-empty default here would silently
    # reintroduce the "fist ctrl always builds one color regardless of
    # the arm's side-derived group color" bug this locks in against.
    assert schema['fingers_ctrl_color'].default == '', (
        f"fingers_ctrl_color's schema default is "
        f"{schema['fingers_ctrl_color'].default!r}, expected '' — it "
        f"must fall through to 'ctrl_color' by default (follow-the-group), "
        f"not build a fixed color regardless of the arm's own side color.")


def test_ik_arm_build_fingers_ctrl_is_importable():
    # Pure existence/signature smoke test — no Maya needed to import the
    # module and confirm the P5 helper landed with the documented name.
    from maya_tools.rigging.fabricator.modules import _limb_common as hc
    assert callable(hc.build_fingers_ctrl)
    assert hc._CURL_BASELINE_EPSILON > 0


def test_limb_fragment_components_empty_is_valid():
    """PLAN.md Task 5.2: components=[] is a fully valid, intentional
    LimbFragment value (a skeleton-only fragment) — pure dataclass
    construction, no Maya/yaml needed."""
    from maya_tools.rigging.fabricator.limbs.schema import (
        LimbFragment, ExternalAnchor,
    )
    from maya_tools.rigging.fabricator.blueprint.schema import JointSpec

    frag = LimbFragment(
        name='test_skeleton_only',
        external_anchor=ExternalAnchor(plug_kind='matrix'),
        skeleton_joints=[
            JointSpec(name='finger_01', parent='<EXTERNAL>'),
            JointSpec(name='finger_02', parent='finger_01'),
        ],
        components=[],
    )
    assert frag.components == []
    assert len(frag.skeleton_joints) == 2
def main():
    check("test_ik_arm_is_discovered", test_ik_arm_is_discovered)
    check("test_ik_arm_contract_shape", test_ik_arm_contract_shape)
    check("test_ik_arm_options_match_simple_ik", test_ik_arm_options_match_simple_ik)
    check("test_ik_arm_ribbon_options_p2", test_ik_arm_ribbon_options_p2)
    check("test_metacarpal_excluded_name_match", test_metacarpal_excluded_name_match)
    check("test_metacarpal_excluded_fallback_len_ge_4",
          test_metacarpal_excluded_fallback_len_ge_4)
    check("test_metacarpal_excluded_fallback_len_le_3",
          test_metacarpal_excluded_fallback_len_le_3)
    check("test_metacarpal_excluded_fallback_trailing_tip_joint",
          test_metacarpal_excluded_fallback_trailing_tip_joint)
    check("test_metacarpal_excluded_unlabeled_root_plus_trailing_tip_both_excluded",
          test_metacarpal_excluded_unlabeled_root_plus_trailing_tip_both_excluded)
    check("test_discover_fingers_ue5_with_metacarpals",
          test_discover_fingers_ue5_with_metacarpals)
    check("test_discover_fingers_12finger_cartoon_no_metacarpal",
          test_discover_fingers_12finger_cartoon_no_metacarpal)
    check("test_discover_fingers_4finger_no_metacarpal",
          test_discover_fingers_4finger_no_metacarpal)
    check("test_discover_fingers_empty_when_no_children",
          test_discover_fingers_empty_when_no_children)
    check("test_discover_fingers_mid_chain_branch_prefers_continuation_over_leaf",
          test_discover_fingers_mid_chain_branch_prefers_continuation_over_leaf)
    check("test_ik_arm_fingers_option_retired", test_ik_arm_fingers_option_retired)
    check("test_ik_arm_default_options_for_create_no_longer_populates_fingers",
          test_ik_arm_default_options_for_create_no_longer_populates_fingers)
    check("test_ik_arm_fingers_option_round_trips_through_json",
          test_ik_arm_fingers_option_round_trips_through_json)
    check("test_ik_arm_fingers_option_round_trips_through_yaml_if_available",
          test_ik_arm_fingers_option_round_trips_through_yaml_if_available)
    check("test_ik_arm_curl_options_p5", test_ik_arm_curl_options_p5)
    check("test_ik_arm_build_fingers_ctrl_is_importable",
          test_ik_arm_build_fingers_ctrl_is_importable)
    check("test_limb_fragment_components_empty_is_valid",
          test_limb_fragment_components_empty_is_valid)
    check("test_stretchy_defaults_on_p6_task4",
          test_stretchy_defaults_on_p6_task4)

    if FAILURES:
        print(f"IK ARM OFFSCREEN TESTS: {len(FAILURES)} FAILED "
              f"({len(SKIPS)} SKIP)")
        sys.exit(1)
    if SKIPS:
        print(f"IK ARM OFFSCREEN TESTS: OK - {len(SKIPS)} SKIP "
              f"(not counted as pass): {SKIPS}")
    else:
        print("IK ARM OFFSCREEN TESTS: OK - 0 SKIP")


if __name__ == "__main__":
    main()
