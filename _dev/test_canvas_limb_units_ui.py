# _dev/test_canvas_limb_units_ui.py
"""Headless Qt smoke tests for CanvasPanel's limb-collapse rendering
(maya_tools/rigging/fabricator/ui/canvas_panel.py) — SPEC 2026-07-09
Limbs + Follower Joints §3.5, Phase P4.

Mirrors _dev/test_limb_units_ui.py's established offscreen-Qt idiom
exactly: bare `mayapy` with QT_QPA_PLATFORM=offscreen and NO
`maya.standalone.initialize()`. Bare mayapy has no live Maya session, so
`maya.cmds` functions (objExists, ls, ...) are literally ABSENT until a
session initializes — confirmed empirically (AttributeError, not a
stale/empty read). Every code path this file exercises is therefore
reached either because it never touches cmds (limb_grouping.py, the
label/item-construction helpers) or because the specific cmds-touching
call is monkeypatched out — same `_patch(obj, attr, value)` technique
_dev/test_limb_units_ui.py uses for `pp.ln`/`pp.hc`/`cmds.warning`,
applied here to `state.detect_mode`, `scene_canvas_model.
read_scene_canvas`/`read_limb_records`, and `maya_tools.rigging.
fabricator.nodes.get_all_component_nodes`.

Two tiers of coverage:
  1. Direct construction of a single limb-unit row
     (_make_item_for_limb_unit / _label_for_limb_unit) — proves the new
     item-building helpers in isolation.
  2. Full CanvasPanel.populate_from_scene() runs against a mocked
     scene (scene_canvas_model + state + fab_nodes patched) — proves
     the REAL populate_from_scene wiring (the `_add` closure, the
     limb_grouping.build_render_tree call, default-collapsed limb-unit
     rows, expand-across-rebuild persistence) end to end, not just the
     two new helper methods in isolation.

Tree item manipulation (.setExpanded(), .childCount(), .text()) is
headless-safe per the studio's own established convention — no
QApplication.exec()/event loop needed to read back a QTreeWidgetItem's
programmatic state. Where the task explicitly calls for signal-driven
interaction (the 2026-07-10 lesson: never direct-invoke what a signal
can drive), this file uses `.setExpanded()` on the real QTreeWidgetItem
(the same call the tree's own disclosure-arrow click handler makes —
there is no separate wrapper method to accidentally bypass) and drives
the search filter through `search_edit.setText()` (a live QLineEdit,
which genuinely emits `textChanged` — not a direct call to
`_on_search_text_changed`).

Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_canvas_limb_units_ui.py
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
    finally:
        _flush_qt()


_APP = None


def qt_app():
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication
        _APP = QApplication.instance() or QApplication([])
    return _APP


def _flush_qt():
    if _APP is None:
        return
    import gc
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    gc.collect()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.processEvents()


def _patch(obj, attr, value):
    """Return a restore_fn after setting obj.attr = value. Same helper
    _dev/test_limb_units_ui.py establishes (see its own docstring) —
    duplicated locally rather than imported since these _dev test files
    are each standalone `mayapy <file>` entry points, not a shared test
    package."""
    had = hasattr(obj, attr)
    original = getattr(obj, attr, None)
    setattr(obj, attr, value)

    def _restore():
        if had:
            setattr(obj, attr, original)
        else:
            delattr(obj, attr)
    return _restore


# ─────────────────────────────────────────────────────────────────────────
# Tier 1 — direct construction of the new item-building helpers
# ─────────────────────────────────────────────────────────────────────────

def test_canvas_panel_module_imports_offscreen():
    """Smoke check: canvas_panel.py must import cleanly under bare
    mayapy + QT_QPA_PLATFORM=offscreen with no Maya session (confirmed
    separately: PySide6 imports fine, maya.cmds/maya.api.OpenMaya import
    fine too — only CALLING their functions needs a live session)."""
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp
    assert hasattr(cp, 'CanvasPanel')
    assert hasattr(cp, 'limb_grouping')


def test_canvas_panel_constructs_bare_offscreen():
    """CanvasPanel() itself constructs fine bare — create_layout/
    connect_signals are pure Qt (the panel installs no Maya-side
    callbacks since the 2026-07-10 viewport-sync removal)."""
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp
    w = cp.CanvasPanel()
    assert w.tree.topLevelItemCount() == 0
    w.close()


def test_make_item_for_limb_unit_labels_joint_name_with_type_annotation():
    """KS #7 label contract: joint name FIRST, limb type as the
    annotation ('clavicle_l (Arm)') — the original type-only label read
    as a joint rename when an implicit limb (machine-default type =
    the component's type string) was promoted to explicit by a fragment
    round-trip and collapsed for the first time."""
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp
    import maya_tools.rigging.fabricator.ui.state as st

    restore = _patch(st, 'detect_mode', lambda: st.MODE_SKELETON)
    try:
        w = cp.CanvasPanel()
        item = w._make_item_for_limb_unit('clavicle_l', 'Arm')
        assert item.text(0) == 'clavicle_l (Arm)', item.text(0)
        assert item.data(0, cp._ROLE_JOINT_NAME) == 'clavicle_l'
        assert item.data(0, cp._ROLE_IS_LIMB_UNIT) is True
        assert item.data(0, cp._ROLE_LIMB_TYPE) == 'Arm'
        w.close()
    finally:
        restore()


def test_make_item_for_limb_unit_falls_back_to_joint_name_when_type_blank():
    """limb_type is editable and may be blank (SPEC 3.2) — the row must
    still show SOMETHING identifiable, not an empty label."""
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp
    import maya_tools.rigging.fabricator.ui.state as st

    restore = _patch(st, 'detect_mode', lambda: st.MODE_SKELETON)
    try:
        w = cp.CanvasPanel()
        item = w._make_item_for_limb_unit('shoulder_r', '')
        assert item.text(0) == 'shoulder_r', item.text(0)
        w.close()
    finally:
        restore()


def test_make_item_for_limb_unit_is_not_inline_renameable():
    """A limb-unit row's visible text is a LIMB label, not a joint
    name — accepting an inline edit here would rename the joint to
    whatever the user typed as if it were a limb-type string. Renaming
    a limb happens through the Properties panel's LIMB section, not
    canvas inline-rename."""
    qt_app()
    from PySide6 import QtCore
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp
    import maya_tools.rigging.fabricator.ui.state as st

    restore = _patch(st, 'detect_mode', lambda: st.MODE_SKELETON)
    try:
        w = cp.CanvasPanel()
        item = w._make_item_for_limb_unit('clavicle_l', 'Arm')
        assert not (item.flags() & QtCore.Qt.ItemIsEditable), (
            'limb-unit row must not be inline-renameable')
        # Sanity contrast: an ordinary joint row in the SAME mode IS
        # editable, proving the flag really was stripped rather than
        # just never set for this mode.
        plain = w._make_item_for_joint('elbow_l')
        assert bool(plain.flags() & QtCore.Qt.ItemIsEditable), (
            'ordinary joint rows should stay inline-renameable')
        w.close()
    finally:
        restore()


def test_label_for_limb_unit_prefers_joint_badge_glyph():
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp

    w = cp.CanvasPanel()
    w._joint_badges['clavicle_l'] = '⚠'
    label = w._label_for_limb_unit('clavicle_l', 'Arm')
    assert label == '⚠ clavicle_l (Arm)', repr(label)
    w.close()


def test_rename_row_preserves_limb_unit_label():
    """The live-rename hook (_on_live_renamed -> rename_row) must not
    clobber a collapsed limb row's limb_type label with the ordinary
    joint-name format when its top_joint gets renamed in the outliner."""
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp
    import maya_tools.rigging.fabricator.ui.state as st

    restore = _patch(st, 'detect_mode', lambda: st.MODE_SKELETON)
    try:
        w = cp.CanvasPanel()
        item = w._make_item_for_limb_unit('clavicle_l', 'Arm')
        w.tree.addTopLevelItem(item)
        w.rename_row('clavicle_l', 'clavicle_left')
        assert item.data(0, cp._ROLE_JOINT_NAME) == 'clavicle_left'
        assert item.text(0) == 'clavicle_left (Arm)', (
            f'rename must refresh the joint half and keep the limb-type '
            f'annotation, got {item.text(0)!r}')
        w.close()
    finally:
        restore()


# ─────────────────────────────────────────────────────────────────────────
# Tier 2 — full populate_from_scene() against a mocked scene
# ─────────────────────────────────────────────────────────────────────────

def _biped_slice_fixture():
    """world_root -> spine_top (Spine, EXCEPTION — arm_top attaches
    inside it) -> arm_top (Arm, collapses) -> arm_mid -> arm_tip.
    Small deliberately — the full-biped shape is already covered by
    _dev/test_canvas_limb_units.py's pure-function suite; this fixture
    only needs to be big enough to prove both branches (collapse vs
    stay-expanded) render correctly through REAL Qt items."""
    import maya_tools.rigging.fabricator.ui.scene_canvas_model as scm
    from maya_tools.rigging.fabricator.ui.limb_grouping import LimbRecord

    arm_tip = scm.CanvasNode(joint_name='arm_tip')
    arm_mid = scm.CanvasNode(joint_name='arm_mid', children=(arm_tip,))
    arm_top = scm.CanvasNode(joint_name='arm_top', children=(arm_mid,))
    spine_top = scm.CanvasNode(joint_name='spine_top', children=(arm_top,))
    world_root = scm.CanvasNode(joint_name='world_root', children=(spine_top,))

    roots = [world_root]
    limb_records = [
        LimbRecord('spine_top', 'Spine', is_implicit=False),
        LimbRecord('arm_top', 'Arm', is_implicit=False),
    ]
    return roots, limb_records


def _patch_scene_for_populate(roots, limb_records, member_tints=None):
    """Patch the five seams populate_from_scene needs to run to
    completion with zero live cmds calls: the three scene_canvas_model
    reads (scene, limb records, limb-member tints — KS #8), mode
    detection, and the tint map's component walk. Returns a single
    restore_fn that undoes all of them."""
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp
    import maya_tools.rigging.fabricator.ui.state as st
    import maya_tools.rigging.fabricator.nodes as fab_nodes_mod

    tints = dict(member_tints or {})
    restores = [
        _patch(cp.scene_canvas_model, 'read_scene_canvas', lambda: list(roots)),
        _patch(cp.scene_canvas_model, 'read_limb_records', lambda: list(limb_records)),
        _patch(cp.scene_canvas_model, 'read_limb_member_tints', lambda: dict(tints)),
        _patch(st, 'detect_mode', lambda: st.MODE_SKELETON),
        _patch(fab_nodes_mod, 'get_all_component_nodes', lambda: []),
    ]

    def _restore_all():
        for r in restores:
            r()
    return _restore_all


def test_populate_from_scene_collapses_arm_keeps_spine_expanded():
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp

    roots, limb_records = _biped_slice_fixture()
    restore = _patch_scene_for_populate(roots, limb_records)
    try:
        w = cp.CanvasPanel()
        w.populate_from_scene()

        assert w.tree.topLevelItemCount() == 1
        root_item = w.tree.topLevelItem(0)
        assert root_item.data(0, cp._ROLE_IS_LIMB_UNIT) is None
        assert root_item.childCount() == 1

        spine_item = root_item.child(0)
        assert spine_item.data(0, cp._ROLE_JOINT_NAME) == 'spine_top'
        assert not spine_item.data(0, cp._ROLE_IS_LIMB_UNIT), (
            'Spine has an interior attachment (Arm) and must render expanded')
        assert spine_item.text(0) == 'spine_top', spine_item.text(0)
        assert spine_item.isExpanded() is True, (
            'ordinary (non-limb-unit) rows default expanded, unchanged '
            'from pre-P4 behavior')

        arm_item = spine_item.child(0)
        assert arm_item.data(0, cp._ROLE_JOINT_NAME) == 'arm_top'
        assert arm_item.data(0, cp._ROLE_IS_LIMB_UNIT) is True
        assert arm_item.text(0) == 'arm_top (Arm)', arm_item.text(0)
        assert arm_item.childCount() == 1, (
            'collapsed row must still carry its full joint subtree')
        w.close()
    finally:
        restore()


def test_populate_from_scene_limb_unit_row_defaults_collapsed():
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp

    roots, limb_records = _biped_slice_fixture()
    restore = _patch_scene_for_populate(roots, limb_records)
    try:
        w = cp.CanvasPanel()
        w.populate_from_scene()
        arm_item = w.tree.topLevelItem(0).child(0).child(0)
        assert arm_item.text(0) == 'arm_top (Arm)'
        assert arm_item.isExpanded() is False, (
            'a fresh limb-unit row must default COLLAPSED (SPEC 3.5 '
            '"always expandable" — collapsed is the default state)')
        w.close()
    finally:
        restore()


def test_populate_from_scene_limb_unit_expand_reveals_full_joints():
    """Expandable = standard tree expand/collapse on the limb's item —
    driven via .setExpanded(True), the same call the tree's own
    disclosure-arrow click makes (not a simulated mouse click, but not
    a bypass of any wrapper method either — there is none)."""
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp

    roots, limb_records = _biped_slice_fixture()
    restore = _patch_scene_for_populate(roots, limb_records)
    try:
        w = cp.CanvasPanel()
        w.populate_from_scene()
        arm_item = w.tree.topLevelItem(0).child(0).child(0)
        assert arm_item.isExpanded() is False

        arm_item.setExpanded(True)

        assert arm_item.isExpanded() is True
        mid_item = arm_item.child(0)
        assert mid_item.data(0, cp._ROLE_JOINT_NAME) == 'arm_mid'
        assert mid_item.data(0, cp._ROLE_IS_LIMB_UNIT) in (None, False)
        tip_item = mid_item.child(0)
        assert tip_item.data(0, cp._ROLE_JOINT_NAME) == 'arm_tip'
        w.close()
    finally:
        restore()


def test_populate_from_scene_persists_user_expanded_limb_unit_across_rebuild():
    """The expansion-state finding: pre-existing prior_collapsed idiom
    (negative set, default-expanded joints) is mirrored, not reused
    verbatim, for limb-unit rows (positive set, default-collapsed) —
    see _capture_expanded_limb_units. Proves the mirrored idiom
    actually survives a full tree.clear() + rebuild, the same way
    prior_collapsed already does for ordinary joints."""
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp

    roots, limb_records = _biped_slice_fixture()
    restore = _patch_scene_for_populate(roots, limb_records)
    try:
        w = cp.CanvasPanel()
        w.populate_from_scene()
        arm_item = w.tree.topLevelItem(0).child(0).child(0)
        arm_item.setExpanded(True)

        # Force a full rebuild (not the fast label-only path) by
        # invalidating the cached signature directly — mirrors what any
        # real structural scene change does.
        w._signature = None
        w.populate_from_scene()

        arm_item_2 = w.tree.topLevelItem(0).child(0).child(0)
        assert arm_item_2 is not arm_item, (
            'a full rebuild must produce fresh items (tree.clear() ran)')
        assert arm_item_2.data(0, cp._ROLE_JOINT_NAME) == 'arm_top'
        assert arm_item_2.isExpanded() is True, (
            'a user-expanded limb unit must stay expanded across a rebuild')
        w.close()
    finally:
        restore()


def test_populate_from_scene_implicit_limb_never_collapses():
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp
    import maya_tools.rigging.fabricator.ui.scene_canvas_model as scm
    from maya_tools.rigging.fabricator.ui.limb_grouping import LimbRecord

    hand_tip = scm.CanvasNode(joint_name='hand_tip')
    hand_top = scm.CanvasNode(joint_name='hand_top', children=(hand_tip,))
    root = scm.CanvasNode(joint_name='root', children=(hand_top,))

    restore = _patch_scene_for_populate(
        [root], [LimbRecord('hand_top', 'IKArm', is_implicit=True)])
    try:
        w = cp.CanvasPanel()
        w.populate_from_scene()
        hand_item = w.tree.topLevelItem(0).child(0)
        assert hand_item.data(0, cp._ROLE_JOINT_NAME) == 'hand_top'
        assert not hand_item.data(0, cp._ROLE_IS_LIMB_UNIT), (
            'an implicit limb must render as a plain joint row, invisible '
            'ceremony per SPEC 3.4')
        assert hand_item.text(0) == 'hand_top', hand_item.text(0)
        w.close()
    finally:
        restore()


def test_populate_from_scene_search_filter_reaches_inside_collapsed_limb():
    """Signal-driven: setText() on the real search QLineEdit, which is
    connected via textChanged -> _on_search_text_changed (not a direct
    call). A match on 'arm_tip' (buried inside the collapsed Arm row)
    must keep every ancestor row visible (setHidden(False)) even though
    the Arm row itself stays collapsed — same pre-existing "ancestor
    visible, not auto-expanded" behavior a manually-collapsed ordinary
    branch already has today; P4 doesn't change that contract, just
    confirms it still holds for a limb-unit row."""
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp

    roots, limb_records = _biped_slice_fixture()
    restore = _patch_scene_for_populate(roots, limb_records)
    try:
        w = cp.CanvasPanel()
        w.populate_from_scene()
        arm_item = w.tree.topLevelItem(0).child(0).child(0)
        assert arm_item.isExpanded() is False

        w.search_edit.setText('arm_tip')

        assert arm_item.isHidden() is False, (
            'Arm row must stay visible — a descendant (arm_tip) matches')
        spine_item = w.tree.topLevelItem(0).child(0)
        assert spine_item.isHidden() is False
        w.search_edit.setText('')
        w.close()
    finally:
        restore()


# ─────────────────────────────────────────────────────────────────────────
# (Tier 3 removed 2026-07-10: the viewport→canvas SelectionChanged sync it
# covered was deleted outright — its event storm cascaded canvas +
# properties rebuilds during every bulk scene mutation and locked the GUI
# on Build Rig. The canvas no longer observes Maya selection; selection
# flows one way, canvas → viewport.)
# ─────────────────────────────────────────────────────────────────────────


def test_populate_from_scene_limb_member_tints_reach_tint_map():
    """KS #8: finger/twist joints (fab_limb members, owned by no
    component) tint like the limb's primary component. Members that a
    component directly owns keep their own tint (merge is setdefault
    AFTER the component walk)."""
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp

    roots, limb_records = _biped_slice_fixture()
    restore = _patch_scene_for_populate(
        roots, limb_records,
        member_tints={'finger_meta': 'RibbonIKArm',
                      'twist_upper_01': 'RibbonIKArm'})
    try:
        w = cp.CanvasPanel()
        w.populate_from_scene()
        assert w._joint_to_tint_type.get('finger_meta') == 'RibbonIKArm', (
            w._joint_to_tint_type)
        assert w._joint_to_tint_type.get('twist_upper_01') == 'RibbonIKArm'
        w.close()
    finally:
        restore()


def test_branch_delete_label_is_context_aware():
    """KS #5: the context-menu delete action reads 'Delete Limb' only
    when the branch actually involves components (row owns one, row is a
    collapsed limb unit, or a descendant owns one); a bare helper joint
    gets plain 'Delete'. Same operation either way — label only."""
    qt_app()
    import maya_tools.rigging.fabricator.ui.canvas_panel as cp
    import maya_tools.rigging.fabricator.ui.state as st

    restore = _patch(st, 'detect_mode', lambda: st.MODE_SKELETON)
    try:
        w = cp.CanvasPanel()

        plain = w._make_item_for_joint('stray_helper')
        assert w._branch_delete_label(plain, '', []) == 'Delete'

        assert w._branch_delete_label(plain, 'fab_C0', []) == 'Delete Limb'

        limb_row = w._make_item_for_limb_unit('clavicle_l', 'Arm')
        assert w._branch_delete_label(limb_row, '', []) == 'Delete Limb'

        # Component on a DESCENDANT (right-clicking a spine joint above
        # an arm component) still reads Delete Limb.
        w._joint_to_component['upperarm_l'] = {'id': 'fab_C1',
                                               'type': 'RibbonIKArm'}
        assert w._branch_delete_label(
            plain, '', ['upperarm_l', 'lowerarm_l']) == 'Delete Limb'
        assert w._branch_delete_label(
            plain, '', ['toe_l']) == 'Delete'
        w.close()
    finally:
        restore()


def main():
    check("test_canvas_panel_module_imports_offscreen",
          test_canvas_panel_module_imports_offscreen)
    check("test_canvas_panel_constructs_bare_offscreen",
          test_canvas_panel_constructs_bare_offscreen)
    check("test_make_item_for_limb_unit_labels_joint_name_with_type_annotation",
          test_make_item_for_limb_unit_labels_joint_name_with_type_annotation)
    check("test_make_item_for_limb_unit_falls_back_to_joint_name_when_type_blank",
          test_make_item_for_limb_unit_falls_back_to_joint_name_when_type_blank)
    check("test_make_item_for_limb_unit_is_not_inline_renameable",
          test_make_item_for_limb_unit_is_not_inline_renameable)
    check("test_label_for_limb_unit_prefers_joint_badge_glyph",
          test_label_for_limb_unit_prefers_joint_badge_glyph)
    check("test_rename_row_preserves_limb_unit_label",
          test_rename_row_preserves_limb_unit_label)
    check("test_populate_from_scene_collapses_arm_keeps_spine_expanded",
          test_populate_from_scene_collapses_arm_keeps_spine_expanded)
    check("test_populate_from_scene_limb_unit_row_defaults_collapsed",
          test_populate_from_scene_limb_unit_row_defaults_collapsed)
    check("test_populate_from_scene_limb_unit_expand_reveals_full_joints",
          test_populate_from_scene_limb_unit_expand_reveals_full_joints)
    check("test_populate_from_scene_persists_user_expanded_limb_unit_across_rebuild",
          test_populate_from_scene_persists_user_expanded_limb_unit_across_rebuild)
    check("test_populate_from_scene_implicit_limb_never_collapses",
          test_populate_from_scene_implicit_limb_never_collapses)
    check("test_populate_from_scene_search_filter_reaches_inside_collapsed_limb",
          test_populate_from_scene_search_filter_reaches_inside_collapsed_limb)
    check("test_populate_from_scene_limb_member_tints_reach_tint_map",
          test_populate_from_scene_limb_member_tints_reach_tint_map)
    check("test_branch_delete_label_is_context_aware",
          test_branch_delete_label_is_context_aware)

    if FAILURES:
        print(f"CANVAS LIMB UNITS (UI) TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("CANVAS LIMB UNITS (UI) TESTS: OK")


if __name__ == "__main__":
    main()
