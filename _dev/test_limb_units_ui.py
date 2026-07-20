# _dev/test_limb_units_ui.py
"""Headless smoke tests for the Follow block's widget
(the retired FollowRuleField widget and
SPEC 2026-07-09 Limbs + Follower Joints §3.1 Task 1.3).

Mirrors _dev/test_ik_arm_ui.py's established offscreen-Qt idiom exactly:
bare `mayapy` with QT_QPA_PLATFORM=offscreen and NO
`maya.standalone.initialize()` (that repo-verified-safe combination for
constructing real Qt widgets headlessly).

Scope, honestly: this file proves construction, get_value()/set_value()
round-tripping for both 'distribute' and 'match' kinds, the linked=True
read-only state (editing controls disabled), and kind-switch show/hide of
the t slider — all via direct method calls on the widget, not simulated
mouse clicks. It does NOT simulate a mouse click on 'Use Selected' or
'Clear' (those need a live Qt event loop) and does NOT exercise
_on_use_selected_clicked's maya.cmds.ls(selection=...) path (that needs a
live Maya scene with real joints selected). Per the task's explicit
instruction, this file does NOT fake a green Qt-interaction test for any
of that — see uiValidationNote in the Task 1.3 report for exactly what's
deferred to live-Maya validation.

This file does NOT exercise properties_panel.py's
PropertiesPanel._build_joint_section/_write_follow_rule/_read_follow_value
— PropertiesPanel imports `maya.cmds` at module scope and is constructed
from a live joint selection + network-node scene state, so it isn't
headless-testable without a live Maya session (same boundary
test_ik_arm_ui.py draws around its own PropertiesPanel integration).

One exception: test_properties_panel_handle_error_exists_and_reports_
without_raising below. PropertiesPanel() itself constructs fine bare (its
__init__ only builds the empty-state layout, no scene reads), and
_handle_error's only Maya call is `cmds.warning(...)`, which bare mayapy
(no maya.standalone.initialize()) doesn't provide — `cmds.warning` is
absent until a session is initialized. That test stubs `cmds.warning`
for the duration of the call so it can prove the v1-audit t07 contract
(method exists, is callable, reports through the stub, never raises)
without pulling in a live Maya session.

Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_limb_units_ui.py
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




def test_option_widgets_module_imports_offscreen():
    """Smoke check, not a feature test: option_widgets.py must import
    cleanly under bare mayapy + QT_QPA_PLATFORM=offscreen with no Maya
    session — proves FollowRuleField's own module-level code never
    touches maya.cmds (only lazily, inside _on_use_selected_clicked)."""
    import maya_tools.rigging.fabricator.ui.option_widgets as ow
def test_properties_panel_handle_error_exists_and_reports_without_raising():
    """v1-audit t07 — KS UI error-reporting pillar (CLAUDE.md): every UI
    class has a `_handle_error` that never swallows an exception. Proves
    the method exists, is callable, and reports (here: through a stubbed
    cmds.warning, since bare mayapy has no live Maya session for the real
    one) instead of raising or silently doing nothing.

    Stubs `cmds.warning` rather than calling the real one — see the file
    docstring for why bare mayapy doesn't have it. Restores whatever was
    there (nothing, on bare mayapy) afterward so this test doesn't leak
    state into any test that runs after it in the same process.
    """
    qt_app()
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator.ui.properties_panel import PropertiesPanel

    had_warning = hasattr(cmds, 'warning')
    original_warning = getattr(cmds, 'warning', None)
    reported = []
    cmds.warning = lambda msg: reported.append(msg)
    try:
        w = PropertiesPanel()
        assert hasattr(w, '_handle_error')
        assert callable(w._handle_error)
        w._handle_error(ValueError('boom'), brief='test brief')
        assert reported, '_handle_error did not report through cmds.warning'
        assert 'test brief' in reported[0], reported
        # No brief — falls back to str(exception), still reports, still
        # doesn't raise.
        reported.clear()
        w._handle_error(RuntimeError('bare boom'))
        assert reported and 'bare boom' in reported[0], reported
        w.close()
    finally:
        if had_warning:
            cmds.warning = original_warning
        else:
            del cmds.warning


# ─────────────────────────────────────────────────────────────────────────
# Task 3.1 (SPEC 2026-07-09 Limbs + Follower Joints §3.3): headless smoke
# for PropertiesPanel's LIMB section (_build_limb_section /
# _build_fingers_dial / _make_finger_row / the button-and-checkbox
# handlers). PropertiesPanel._build_joint_section and friends need a
# live Maya scene (see the file docstring's boundary note); the LIMB
# section's own builders are different — every scene read they perform
# funnels through EXACTLY two module references PropertiesPanel imports
# at its own module scope (`ln` = limb_node, `hc` = _limb_common), so
# monkeypatching those two references in-place (same technique the
# existing _handle_error test uses for cmds.warning — replace, restore
# in a finally) lets this file exercise the REAL PropertiesPanel code
# path — construction, "reflects N fingers", and button/checkbox
# call-through via DIRECT method invocation (never a simulated Qt
# click) — without a live Maya session at all.
#
# What stays live-Qt-only (not covered here, see the report's
# uiValidationNote): an actual mouse click firing '+ Add Finger' / '−'
# / a checkbox (QTest.mouseClick or a real event loop), tooltip text
# visibility, and the LIMB section's on-screen look/spacing next to a
# REAL '// JOINT' + '// COMPONENT' section in a live fs_window.py
# session with a real RibbonIKArm-owned wrist selected.
# ─────────────────────────────────────────────────────────────────────────

def _patch(obj, attr, value):
    """Return (restore_fn) after setting obj.attr = value. Tiny local
    helper so each test's monkeypatch/restore pairs stay one line each
    instead of manually stashing every original."""
    had = hasattr(obj, attr)
    original = getattr(obj, attr, None)
    setattr(obj, attr, value)

    def _restore():
        if had:
            setattr(obj, attr, original)
        else:
            delattr(obj, attr)
    return _restore


def test_properties_panel_limb_section_absent_when_no_limb():
    """_build_limb_section returns None (no LIMB block at all) when the
    selected joint isn't part of any fab_limb — the overwhelming
    majority of joints, pre-P3."""
    qt_app()
    import maya_tools.rigging.fabricator.ui.properties_panel as pp

    w = pp.PropertiesPanel()
    restore = _patch(pp.ln, 'find_limb_for_joint', lambda joint: None)
    try:
        section = w._build_limb_section('some_joint')
        assert section is None
    finally:
        restore()
    w.close()


def test_properties_panel_limb_section_constructs_and_reflects_finger_count():
    """Derived Limbs (spec 2026-07-11): the LIMB section renders fingers
    READ-ONLY — a '// FINGERS — N (discovered)' caps header plus one
    plain label per discovered root (curl-excluded joints noted in a
    '(no curl: ...)' suffix). No add/remove buttons, no checkboxes, no
    limb_type editor, no color combo."""
    qt_app()
    from PySide6 import QtWidgets
    import maya_tools.rigging.fabricator.ui.properties_panel as pp

    w = pp.PropertiesPanel()
    fake_limb = 'FAKE_LIMB_NODE'
    fake_roots = ['index_metacarpal', 'thumb_01']
    fake_chains = {
        'index_metacarpal': ['index_metacarpal', 'index_01', 'index_02'],
        'thumb_01': ['thumb_01', 'thumb_02'],
    }
    restores = [
        _patch(pp.ln, 'find_limb_for_joint', lambda joint: fake_limb),
        _patch(pp.ln, 'get_limb_type', lambda limb: 'RibbonIKArm'),
        _patch(pp.ln, 'limb_display_label', lambda t: 'Ribbon IK Arm'),
        _patch(pp.ln, 'limb_has_feature', lambda limb, f: True),
        _patch(pp.ln, 'list_finger_roots', lambda limb: list(fake_roots)),
        _patch(pp.ln, 'list_curl_excluded', lambda limb: ['index_metacarpal']),
        _patch(pp.hc, 'walk_finger_chain', lambda root: list(fake_chains[root])),
        _patch(pp.ln, 'list_twist_upper', lambda limb: []),
        _patch(pp.ln, 'list_twist_lower', lambda limb: []),
    ]
    try:
        section = w._build_limb_section('index_metacarpal')
        assert section is not None

        caps_labels = [lbl.text() for lbl in section.findChildren(QtWidgets.QLabel)
                       if lbl.property('mindmeld') == 'caps']
        assert any('2' in t and 'discovered' in t.lower() for t in caps_labels), (
            f'section must show the discovered finger count: {caps_labels}')

        all_labels = [lbl.text() for lbl in section.findChildren(QtWidgets.QLabel)]
        assert any('index_metacarpal' in t and 'no curl' in t for t in all_labels), (
            f'curl-excluded suffix missing: {all_labels}')
        assert any('thumb_01' in t for t in all_labels), all_labels
        # Derived limb label renders the contract display name.
        assert any('Ribbon IK Arm' in t for t in all_labels), all_labels

        # READ-ONLY: no buttons, no checkboxes. (The Twist dial's
        # spinboxes carry internal QLineEdits — Qt implementation
        # detail, not an editing affordance of the fingers block.)
        assert section.findChildren(QtWidgets.QPushButton) == []
        assert section.findChildren(QtWidgets.QCheckBox) == []
    finally:
        for restore in restores:
            restore()
    w.close()


def _find_button(section, text):
    from PySide6 import QtWidgets
    matches = [b for b in section.findChildren(QtWidgets.QPushButton)
              if b.text() == text]
    assert matches, (
        f'no button with text {text!r} found: '
        f'{[b.text() for b in section.findChildren(QtWidgets.QPushButton)]}')
    return matches[0]
def test_properties_panel_limb_section_constructs_and_reflects_twist_counts():
    """Twist block reflects the mocked upper/lower counts as spinbox
    values."""
    qt_app()
    from PySide6 import QtWidgets
    import maya_tools.rigging.fabricator.ui.properties_panel as pp

    w = pp.PropertiesPanel()
    fake_limb = 'FAKE_LIMB_NODE'
    restores = [
        _patch(pp.ln, 'find_limb_for_joint', lambda joint: fake_limb),
        _patch(pp.ln, 'get_limb_type', lambda limb: 'Arm_RibbonIK'),
        _patch(pp.ln, 'limb_has_feature', lambda limb, f: True),
        _patch(pp.ln, 'list_finger_roots', lambda limb: []),
        _patch(pp.ln, 'list_curl_excluded', lambda limb: []),
        _patch(pp.ln, 'list_twist_upper', lambda limb: ['tw_u_01', 'tw_u_02']),
        _patch(pp.ln, 'list_twist_lower', lambda limb: ['tw_l_01']),
        # (Derived Limbs, spec 2026-07-11: the fingers block is
        # read-only; no dial/color patches needed anymore.)
    ]
    try:
        section = w._build_limb_section('shoulder')
        assert section is not None

        spinboxes = section.findChildren(QtWidgets.QSpinBox)
        values = sorted(sb.value() for sb in spinboxes)
        assert values == [1, 2], (
            f'twist block must reflect the mocked upper=2/lower=1 counts: {values}')
    finally:
        for restore in restores:
            restore()
    w.close()


def test_properties_panel_write_twist_count_calls_through_and_skips_noop():
    """Direct method invocation (not a simulated spinbox drag) — proves
    the twist spinbox's connected slot (_write_twist_count) reaches
    limb_node.limb_set_twist_count with the resolved (limb, segment, n),
    and no-ops when the new value already matches the current scene
    count, matching every other writer method in this panel."""
    qt_app()
    import maya_tools.rigging.fabricator.ui.properties_panel as pp

    w = pp.PropertiesPanel()
    fake_limb = 'FAKE_LIMB_NODE'
    calls = []
    rebuild_calls = []
    restores = [
        _patch(pp.ln, 'list_twist_upper', lambda limb: ['tw_01', 'tw_02']),
        _patch(pp.ln, 'limb_set_twist_count',
              lambda limb, seg, n: calls.append((limb, seg, n))),
        _patch(w, '_rebuild', lambda: rebuild_calls.append(True)),
    ]
    try:
        w._write_twist_count(fake_limb, 'upper', 2)  # same as current -> no-op
        assert calls == [], 'writing the unchanged count must no-op'
        assert rebuild_calls == [], 'a no-op write must not rebuild'

        w._write_twist_count(fake_limb, 'upper', 3)
        assert calls == [(fake_limb, 'upper', 3)], calls
        assert rebuild_calls, '_write_twist_count must rebuild on success'
    finally:
        for restore in restores:
            restore()
    w.close()


def test_properties_panel_rebuild_wraps_sections_in_collapsible_frames():
    """2026-07-10 launch polish: _rebuild wraps every section in its own
    compact CollapsibleSection frame ('// JOINT', '// FOLLOW', ...),
    expanded by default so nothing hides on a fresh selection. Drives
    the REAL _rebuild (unlike the write-handler tests above, which patch
    it out) through show_for_joint with the scene reads mocked."""
    qt_app()
    import maya_tools.rigging.fabricator.ui.properties_panel as pp
    import maya_tools.rigging.fabricator.ui.state as st
    from maya_tools.utils.qt.widgets import CollapsibleSection

    w = pp.PropertiesPanel()
    restores = [
        _patch(st, 'detect_mode', lambda: st.MODE_SKELETON),
        _patch(w, '_resolve_joint_source', lambda jn: ('joint', jn)),
        _patch(w, '_read_rotate_order', lambda k, n: 'xyz'),
        _patch(w, '_read_radius', lambda k, n: 1.0),
        _patch(pp.ln, 'find_limb_for_joint', lambda jn: None),
    ]
    try:
        w.show_for_joint('framer_joint')
        assert isinstance(w._joint_section, CollapsibleSection), (
            type(w._joint_section))
        assert w._joint_section.is_expanded() is True, (
            'section frames must default EXPANDED')
        # (Follow section retired 2026-07-12 — no frame to assert.)
        assert w._limb_section is None, (
            'no limb -> no LIMB frame at all')
        assert w._component_section is None
    finally:
        for restore in restores:
            restore()
    w.close()


def test_properties_panel_write_twist_count_error_routes_through_handle_error():
    """A limb_set_twist_count failure (e.g. the skinning guard) must
    surface via _handle_error, naming the guarded joint, never raise
    out of the spinbox handler — AND the panel must _rebuild() on the
    failure path too, matching the success path (line 422), so the
    spinbox re-reads the real (unchanged) scene count instead of being
    left showing the rejected, never-applied value the user dragged to.
    limb_set_twist_count's own guard contract makes zero mutations on
    a raise, so anything short of a resync leaves the widget lying
    about scene state until the joint is reselected."""
    qt_app()
    import maya.cmds as cmds
    import maya_tools.rigging.fabricator.ui.properties_panel as pp

    had_warning = hasattr(cmds, 'warning')
    original_warning = getattr(cmds, 'warning', None)
    reported = []
    cmds.warning = lambda msg: reported.append(msg)

    w = pp.PropertiesPanel()
    rebuild_calls = []

    def _boom(limb, segment, n):
        raise RuntimeError(
            f"cannot change the {segment!r} twist count on {limb!r} — "
            f"['tw_01'] carries skinCluster weights")

    restores = [
        _patch(pp.ln, 'list_twist_lower', lambda limb: ['tw_01']),
        _patch(pp.ln, 'limb_set_twist_count', _boom),
        _patch(w, '_rebuild', lambda: rebuild_calls.append(True)),
    ]
    try:
        w._write_twist_count('FAKE_LIMB', 'lower', 2)  # must not raise
        assert reported, (
            '_write_twist_count must report the failure via _handle_error')
        assert 'tw_01' in reported[0], reported
        assert rebuild_calls, (
            '_write_twist_count must _rebuild() on a rejected write too, '
            'so the spinbox resyncs to the unchanged scene count instead '
            'of showing the value the guard rejected')
    finally:
        for restore in restores:
            restore()
        if had_warning:
            cmds.warning = original_warning
        else:
            del cmds.warning
    w.close()
def main():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith('test_') and callable(fn)
    ]
    print(f'Running {len(tests)} tests from test_limb_units_ui.py...')
    for name, fn in tests:
        check(name, fn)

    if FAILURES:
        print(f"\nLIMB UNITS UI OFFSCREEN TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print(f"\nLIMB UNITS UI OFFSCREEN TESTS: OK ({len(tests)} passed)")


if __name__ == '__main__':
    main()
