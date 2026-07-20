# _dev/test_canvas_limb_units.py
"""Headless, no-Maya-scene tests for the canvas limb-collapse grouping
PURE FUNCTION (maya_tools/rigging/fabricator/ui/limb_grouping.py) —
SPEC 2026-07-09 Limbs + Follower Joints §3.5, Phase P4.

This file exercises `limb_grouping.build_render_tree` directly against
synthetic hierarchy fixtures (plain duck-typed nodes, plus one fixture
using the REAL `scene_canvas_model.CanvasNode` to prove the duck-typing
actually holds against production data) and plain `LimbRecord`s — no
`cmds` call anywhere in this file, no scene, no Qt. The Qt-consuming
half (CanvasPanel building tree items from a render tree, default
collapsed/expanded state, expand-reveals-children) lives in the
companion suite `_dev/test_canvas_limb_units_ui.py`.

Naming note: this is deliberately NOT `_dev/test_limb_units.py` /
`_dev/test_limb_units_ui.py` — those file names are already spoken for
by the P1 follower-joint-primitive suites (pure follow_rules math /
FollowRuleField widget tests respectively); reusing them here would
silently bury this phase's own tests inside an unrelated suite instead
of adding a new one.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_canvas_limb_units.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


class Skip(Exception):
    """Raise from a test body to mark it SKIPPED — never silently folded
    into the pass count (see test_ribbon_ik_arm.py's identical class for
    the full rationale)."""


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


# ─────────────────────────────────────────────────────────────────────────
# Fixture helpers
# ─────────────────────────────────────────────────────────────────────────

class _Node:
    """Minimal duck-type satisfying limb_grouping's own contract:
    `.joint_name: str`, `.children: Iterable[_Node]`. Deliberately NOT
    scene_canvas_model.CanvasNode for most fixtures below, to prove
    build_render_tree needs nothing else from a hierarchy node — see
    test_build_render_tree_accepts_real_canvas_node for the one test
    that DOES use the real class."""

    def __init__(self, joint_name, children=()):
        self.joint_name = joint_name
        self.children = tuple(children)


def _full_biped_fixture():
    """World -> Spine -> {Legs x2, Arms x2, Neck, Head} — the SPEC 3.5
    target reading's own worked example, built as a synthetic tree:

        world_root
          pelvis                     (Spine top_joint)
            hip_l -> knee_l -> ankle_l         (Leg_L top_joint = hip_l)
            hip_r -> knee_r -> ankle_r         (Leg_R top_joint = hip_r)
            spine1 -> spine2 -> spine3 -> chest
              clavicle_l -> shoulder_l -> elbow_l -> wrist_l  (Arm_L)
              clavicle_r -> shoulder_r -> elbow_r -> wrist_r  (Arm_R)
              neck -> neck2                     (Neck top_joint = neck)
              head_top -> head_tip              (Head top_joint = head_top)

    Legs attach directly ON the spine's own top_joint (pelvis); Arms/
    Neck/Head attach several joints further down the spine's own chain
    (chest) — both attachment depths must trip the interior-attachment
    exception, not just an immediate-child one.
    """
    from maya_tools.rigging.fabricator.ui.limb_grouping import LimbRecord

    ankle_l = _Node('ankle_l')
    knee_l = _Node('knee_l', [ankle_l])
    hip_l = _Node('hip_l', [knee_l])

    ankle_r = _Node('ankle_r')
    knee_r = _Node('knee_r', [ankle_r])
    hip_r = _Node('hip_r', [knee_r])

    wrist_l = _Node('wrist_l')
    elbow_l = _Node('elbow_l', [wrist_l])
    shoulder_l = _Node('shoulder_l', [elbow_l])
    clavicle_l = _Node('clavicle_l', [shoulder_l])

    wrist_r = _Node('wrist_r')
    elbow_r = _Node('elbow_r', [wrist_r])
    shoulder_r = _Node('shoulder_r', [elbow_r])
    clavicle_r = _Node('clavicle_r', [shoulder_r])

    neck2 = _Node('neck2')
    neck = _Node('neck', [neck2])

    head_tip = _Node('head_tip')
    head_top = _Node('head_top', [head_tip])

    chest = _Node('chest', [clavicle_l, clavicle_r, neck, head_top])
    spine3 = _Node('spine3', [chest])
    spine2 = _Node('spine2', [spine3])
    spine1 = _Node('spine1', [spine2])

    pelvis = _Node('pelvis', [hip_l, hip_r, spine1])
    world_root = _Node('world_root', [pelvis])

    roots = [world_root]
    limb_records = [
        LimbRecord('pelvis', 'Spine', is_implicit=False),
        LimbRecord('hip_l', 'Leg', is_implicit=False),
        LimbRecord('hip_r', 'Leg', is_implicit=False),
        LimbRecord('clavicle_l', 'Arm', is_implicit=False),
        LimbRecord('clavicle_r', 'Arm', is_implicit=False),
        LimbRecord('neck', 'Neck', is_implicit=False),
        LimbRecord('head_top', 'Head', is_implicit=False),
    ]
    return roots, limb_records


# ─────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────

def test_module_imports_and_public_api_shape():
    from maya_tools.rigging.fabricator.ui import limb_grouping as lg
    for name in ('LimbRecord', 'RenderNode', 'build_render_tree', 'find_render_node'):
        assert hasattr(lg, name), f'limb_grouping missing public API: {name}'


def test_module_has_no_maya_import():
    """Pure-function guarantee, checked mechanically via the AST (not a
    naive substring scan — the module's own docstring mentions
    'maya.cmds' in prose, which a text search would misfire on):
    limb_grouping.py's source never actually imports anything under
    `maya`, at module scope or inside any function — a future edit that
    accidentally reaches for a live scene read would break the "unit-
    tested headless" contract SPEC 3.5's own phasing plan promises."""
    import ast
    from maya_tools.rigging.fabricator.ui import limb_grouping as lg
    src = Path(lg.__file__).read_text(encoding='utf-8')
    tree = ast.parse(src, filename=lg.__file__)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders.extend(a.name for a in node.names if a.name.split('.')[0] == 'maya')
        elif isinstance(node, ast.ImportFrom):
            if (node.module or '').split('.')[0] == 'maya':
                offenders.append(node.module)
    assert not offenders, f'limb_grouping.py must stay Maya-import-free: {offenders!r}'


def test_full_biped_spine_stays_expanded_has_interior_attachments():
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        build_render_tree, find_render_node)
    roots, limb_records = _full_biped_fixture()
    render_roots = build_render_tree(roots, limb_records)
    spine = find_render_node(render_roots, 'pelvis')
    assert spine is not None, 'pelvis (Spine top_joint) missing from render tree'
    assert spine.is_limb_unit is False, (
        'Spine carries Legs/Arms/Neck/Head as interior attachments and '
        'must NOT collapse')
    # Its own children are still the real joints (spine1 etc.) AND the
    # attached limbs' own (collapsed) rows — never raw joints belonging
    # to another limb.
    child_names = {c.joint_name for c in spine.children}
    assert child_names == {'hip_l', 'hip_r', 'spine1'}, child_names


def test_full_biped_arms_legs_neck_head_all_collapse():
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        build_render_tree, find_render_node)
    roots, limb_records = _full_biped_fixture()
    render_roots = build_render_tree(roots, limb_records)
    for top, limb_type in (
        ('hip_l', 'Leg'), ('hip_r', 'Leg'),
        ('clavicle_l', 'Arm'), ('clavicle_r', 'Arm'),
        ('neck', 'Neck'), ('head_top', 'Head'),
    ):
        node = find_render_node(render_roots, top)
        assert node is not None, f'{top} missing from render tree'
        assert node.is_limb_unit is True, f'{top} ({limb_type}) should collapse'
        assert node.limb_type == limb_type, (top, node.limb_type)


def test_full_biped_collapsed_rows_still_carry_full_joint_subtree():
    """'Always expandable' (SPEC 3.5) — a collapsed row's children are
    never abridged; the full joint chain is present, just not the
    default-visible state (that's canvas_panel.py's job, tested in the
    UI companion suite)."""
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        build_render_tree, find_render_node)
    roots, limb_records = _full_biped_fixture()
    render_roots = build_render_tree(roots, limb_records)
    arm = find_render_node(render_roots, 'clavicle_l')
    assert arm is not None

    def _flat(node):
        names = [node.joint_name]
        for c in node.children:
            names.extend(_flat(c))
        return names

    names = _flat(arm)
    assert names == ['clavicle_l', 'shoulder_l', 'elbow_l', 'wrist_l'], names


def test_plain_joint_with_no_limb_renders_ordinary():
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        build_render_tree, find_render_node)
    roots, limb_records = _full_biped_fixture()
    render_roots = build_render_tree(roots, limb_records)
    for plain in ('world_root', 'spine1', 'spine2', 'spine3', 'chest',
                  'knee_l', 'ankle_l', 'shoulder_l', 'elbow_l', 'wrist_l',
                  'neck2', 'head_tip'):
        node = find_render_node(render_roots, plain)
        assert node is not None, plain
        assert node.is_limb_unit is False, f'{plain} is not a limb top_joint'
        assert node.limb_type == '', (plain, node.limb_type)


def test_implicit_limb_collapses_like_any_other():
    """Derived Limbs (spec 2026-07-11, as-built amendment 1): rule (a)
    is retired — every limb is derived (implicit=True is lifecycle
    bookkeeping only), and every limb collapses. This test used to pin
    the opposite."""
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        LimbRecord, build_render_tree, find_render_node)
    leaf = _Node('finger_tip')
    top = _Node('hand_extra', [leaf])
    root = _Node('root', [top])
    render_roots = build_render_tree(
        [root], [LimbRecord('hand_extra', 'IKArm', is_implicit=True)])
    node = find_render_node(render_roots, 'hand_extra')
    assert node is not None
    assert node.is_limb_unit is True, (
        'a derived (implicit) limb must collapse — the is_implicit '
        'collapse gate was retired (Derived Limbs 2026-07-11)')
    assert node.limb_type == 'IKArm'
    # Its children still resolve normally underneath.
    assert [c.joint_name for c in node.children] == ['finger_tip']


def test_implicit_flag_is_inert_for_collapse():
    """Derived Limbs (2026-07-11): is_implicit no longer participates in
    the collapse decision at all — only the interior-attachment
    exception does."""
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        LimbRecord, build_render_tree, find_render_node)
    root = _Node('shoulder_only')
    render_roots = build_render_tree(
        [root], [LimbRecord('shoulder_only', 'IKArm', is_implicit=True)])
    node = find_render_node(render_roots, 'shoulder_only')
    assert node.is_limb_unit is True


def test_componentless_explicit_limb_still_collapses():
    """The pure function is agnostic to whether a limb has any attached
    fab_component at all — it only ever looks at hierarchy + limb
    records. A skeleton-only, explicit (non-implicit), no-attachment
    limb collapses exactly like a fully-built one — its own joints
    still resolve underneath once expanded."""
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        LimbRecord, build_render_tree, find_render_node)
    j2 = _Node('bone_tip')
    j1 = _Node('bone_mid', [j2])
    top = _Node('bone_root', [j1])
    root = _Node('root', [top])
    render_roots = build_render_tree(
        [root], [LimbRecord('bone_root', 'Skeleton_Only', is_implicit=False)])
    node = find_render_node(render_roots, 'bone_root')
    assert node is not None
    assert node.is_limb_unit is True
    assert node.limb_type == 'Skeleton_Only'
    assert [c.joint_name for c in node.children] == ['bone_mid']


def test_leaf_limb_top_with_no_children_still_collapses_trivially():
    """A limb whose top_joint has no descendants at all (nothing to
    hide) is still collapse-eligible per the rule — collapsing it is a
    no-visual-op (one row either way) but the LABEL still switches to
    limb_type, which is the whole point of the row existing."""
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        LimbRecord, build_render_tree, find_render_node)
    top = _Node('lone_joint')
    render_roots = build_render_tree(
        [top], [LimbRecord('lone_joint', 'Prop', is_implicit=False)])
    node = find_render_node(render_roots, 'lone_joint')
    assert node.is_limb_unit is True
    assert node.children == ()


def test_attachment_directly_on_top_joint_trips_exception():
    """The interior-attachment check must fire even when the OTHER
    limb's top_joint is an immediate child of THIS limb's own
    top_joint (not several joints down the chain) — SPEC 3.5's own
    'legs parented right on the spine's own top_joint' worked case."""
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        LimbRecord, build_render_tree, find_render_node)
    other_top = _Node('other_top')
    this_top = _Node('this_top', [other_top])
    render_roots = build_render_tree(
        [this_top],
        [LimbRecord('this_top', 'Spine'), LimbRecord('other_top', 'Leg')],
    )
    spine = find_render_node(render_roots, 'this_top')
    assert spine.is_limb_unit is False
    leg = find_render_node(render_roots, 'other_top')
    assert leg.is_limb_unit is True


def test_nested_exception_limb_inside_another_exception_limb():
    """A -> B -> C, all three limbs, C nested at the bottom of B's own
    chain and B nested inside A's — A and B both stay expanded (each
    has an interior attachment: A contains B's top_joint, B contains
    C's), C itself collapses (nothing attached inside IT)."""
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        LimbRecord, build_render_tree, find_render_node)
    c_top = _Node('c_top')
    b_mid = _Node('b_mid', [c_top])
    b_top = _Node('b_top', [b_mid])
    a_mid = _Node('a_mid', [b_top])
    a_top = _Node('a_top', [a_mid])
    render_roots = build_render_tree(
        [a_top],
        [LimbRecord('a_top', 'A'), LimbRecord('b_top', 'B'), LimbRecord('c_top', 'C')],
    )
    assert find_render_node(render_roots, 'a_top').is_limb_unit is False
    assert find_render_node(render_roots, 'b_top').is_limb_unit is False
    assert find_render_node(render_roots, 'c_top').is_limb_unit is True


def test_stale_limb_record_with_no_matching_joint_is_a_silent_no_op():
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        LimbRecord, build_render_tree, find_render_node)
    top = _Node('real_joint')
    render_roots = build_render_tree(
        [top], [LimbRecord('deleted_joint_ghost', 'Ghost')])
    node = find_render_node(render_roots, 'real_joint')
    assert node.is_limb_unit is False
    assert find_render_node(render_roots, 'deleted_joint_ghost') is None


def test_duplicate_top_joint_records_last_one_wins_no_crash():
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        LimbRecord, build_render_tree, find_render_node)
    top = _Node('joint_a')
    render_roots = build_render_tree(
        [top], [LimbRecord('joint_a', 'First'), LimbRecord('joint_a', 'Second')])
    node = find_render_node(render_roots, 'joint_a')
    assert node.is_limb_unit is True
    assert node.limb_type == 'Second', node.limb_type


def test_empty_roots_and_empty_limb_records_return_empty_tree():
    from maya_tools.rigging.fabricator.ui.limb_grouping import build_render_tree
    assert build_render_tree([], []) == ()


def test_no_limb_records_at_all_renders_everything_ordinary():
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        build_render_tree, find_render_node)
    roots, _ = _full_biped_fixture()
    render_roots = build_render_tree(roots, [])
    node = find_render_node(render_roots, 'clavicle_l')
    assert node.is_limb_unit is False


def test_build_render_tree_accepts_real_canvas_node():
    """Duck-typing proof: scene_canvas_model.CanvasNode (the actual
    production hierarchy type canvas_panel.py feeds this function) is
    never imported by limb_grouping.py, but its instances satisfy the
    contract (.joint_name / .children) without any adapter step."""
    from maya_tools.rigging.fabricator.ui.scene_canvas_model import CanvasNode
    from maya_tools.rigging.fabricator.ui.limb_grouping import (
        LimbRecord, build_render_tree, find_render_node)

    child = CanvasNode(joint_name='wrist_l')
    mid = CanvasNode(joint_name='elbow_l', children=(child,))
    top = CanvasNode(joint_name='clavicle_l', children=(mid,))
    root = CanvasNode(joint_name='world_root', children=(top,))

    render_roots = build_render_tree(
        [root], [LimbRecord('clavicle_l', 'Arm')])
    node = find_render_node(render_roots, 'clavicle_l')
    assert node.is_limb_unit is True
    assert node.limb_type == 'Arm'
    assert [c.joint_name for c in node.children] == ['elbow_l']
    assert [c.joint_name for c in node.children[0].children] == ['wrist_l']


def main():
    check("test_module_imports_and_public_api_shape",
          test_module_imports_and_public_api_shape)
    check("test_module_has_no_maya_import", test_module_has_no_maya_import)
    check("test_full_biped_spine_stays_expanded_has_interior_attachments",
          test_full_biped_spine_stays_expanded_has_interior_attachments)
    check("test_full_biped_arms_legs_neck_head_all_collapse",
          test_full_biped_arms_legs_neck_head_all_collapse)
    check("test_full_biped_collapsed_rows_still_carry_full_joint_subtree",
          test_full_biped_collapsed_rows_still_carry_full_joint_subtree)
    check("test_plain_joint_with_no_limb_renders_ordinary",
          test_plain_joint_with_no_limb_renders_ordinary)
    check("test_implicit_limb_collapses_like_any_other",
          test_implicit_limb_collapses_like_any_other)
    check("test_implicit_flag_is_inert_for_collapse",
          test_implicit_flag_is_inert_for_collapse)
    check("test_componentless_explicit_limb_still_collapses",
          test_componentless_explicit_limb_still_collapses)
    check("test_leaf_limb_top_with_no_children_still_collapses_trivially",
          test_leaf_limb_top_with_no_children_still_collapses_trivially)
    check("test_attachment_directly_on_top_joint_trips_exception",
          test_attachment_directly_on_top_joint_trips_exception)
    check("test_nested_exception_limb_inside_another_exception_limb",
          test_nested_exception_limb_inside_another_exception_limb)
    check("test_stale_limb_record_with_no_matching_joint_is_a_silent_no_op",
          test_stale_limb_record_with_no_matching_joint_is_a_silent_no_op)
    check("test_duplicate_top_joint_records_last_one_wins_no_crash",
          test_duplicate_top_joint_records_last_one_wins_no_crash)
    check("test_empty_roots_and_empty_limb_records_return_empty_tree",
          test_empty_roots_and_empty_limb_records_return_empty_tree)
    check("test_no_limb_records_at_all_renders_everything_ordinary",
          test_no_limb_records_at_all_renders_everything_ordinary)
    check("test_build_render_tree_accepts_real_canvas_node",
          test_build_render_tree_accepts_real_canvas_node)

    if FAILURES:
        print(f"CANVAS LIMB UNITS (PURE) TESTS: {len(FAILURES)} FAILED "
              f"({len(SKIPS)} SKIP)")
        sys.exit(1)
    if SKIPS:
        print(f"CANVAS LIMB UNITS (PURE) TESTS: OK - {len(SKIPS)} SKIP "
              f"(not counted as pass): {SKIPS}")
    else:
        print("CANVAS LIMB UNITS (PURE) TESTS: OK - 0 SKIP")


if __name__ == "__main__":
    main()
