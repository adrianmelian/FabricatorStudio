# _dev/test_first_run.py
"""Offscreen tests for first_run.py — the t34 one-time onboarding
moments (no-project prompt + toolbar callout).

Decision core is pure (pending_moments); seen-flag round-trip goes
through toolbar_prefs against a temp path; both cards construct
offscreen and their dismissal paths chain + spend flags correctly.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_first_run.py
(QT_QPA_PLATFORM=offscreen)
"""
import sys
import tempfile
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


def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


# ─── decision core (pure) ───────────────────────────────────────────────

def test_fresh_prefs_pend_the_tour():
    from maya_tools.framework import first_run as fr
    from maya_tools.framework.toolbar import toolbar_prefs
    assert fr.pending_moments(toolbar_prefs.default_prefs()) == \
        [fr.WELCOME_TOUR]


def test_seen_tour_pends_nothing_and_legacy_flags_are_ignored():
    from maya_tools.framework import first_run as fr
    assert fr.pending_moments({'onboarding': {fr.WELCOME_TOUR: True}}) == []
    # t34 legacy flags neither block nor trigger the tour
    legacy = {'onboarding': {'project_prompt': True, 'toolbar_callout': True}}
    assert fr.pending_moments(legacy) == [fr.WELCOME_TOUR]


def test_garbage_prefs_never_raise():
    from maya_tools.framework import first_run as fr
    assert fr.pending_moments({}) == [fr.WELCOME_TOUR]
    assert fr.pending_moments({'onboarding': None}) == [fr.WELCOME_TOUR]


# ─── seen-flag round trip (temp prefs path) ─────────────────────────────

def test_mark_seen_round_trips_through_prefs():
    from maya_tools.framework import first_run as fr
    from maya_tools.framework.toolbar import toolbar_prefs
    tmp = Path(tempfile.mkdtemp()) / 'prefs.json'
    orig = toolbar_prefs.prefs_path
    toolbar_prefs.prefs_path = lambda: tmp
    try:
        fr.mark_seen(fr.WELCOME_TOUR)
        prefs = toolbar_prefs.load_prefs(path=tmp)
        assert prefs['onboarding'][fr.WELCOME_TOUR] is True
        # unrelated flags/defaults survive
        assert prefs['visible'] is True
        # a second write merges rather than clobbers
        fr.mark_seen('some_future_moment')
        prefs = toolbar_prefs.load_prefs(path=tmp)
        assert prefs['onboarding'] == {fr.WELCOME_TOUR: True,
                                       'some_future_moment': True}
    finally:
        toolbar_prefs.prefs_path = orig


# ─── cards (offscreen construction + dismissal chaining) ────────────────

def test_tour_exit_is_the_project_setup_coachmark():
    """Step 4 anchors at the toolbar's project_setup button, says Done,
    and advances on the real button press (the exit IS the action)."""
    qt_app()
    from PySide6 import QtWidgets
    from maya_tools.framework import first_run as fr
    from maya_tools.framework.toolbar import toolbar_app
    anchor = QtWidgets.QWidget(); anchor.resize(26, 26)
    orig = toolbar_app.widget_for
    toolbar_app.widget_for = lambda item_id: (
        anchor if item_id == 'project_setup' else None)
    try:
        before = len(fr._live)
        fr._Tour()._stop_project_setup()
        assert len(fr._live) == before + 1, 'exit card did not show'
        card = fr._live[-1]
        labels = [l.text() for l in card.card.findChildren(QtWidgets.QLabel)]
        assert any('STEP 4 OF 4' in t for t in labels), labels
        buttons = {b.text().upper()
                   for b in card.card.findChildren(QtWidgets.QPushButton)}
        assert 'DONE' in buttons, buttons
        assert card._press_filter is not None, 'press-advance not armed'
        card._close(None)
    finally:
        toolbar_app.widget_for = orig


def test_welcome_card_skip_ends_tour_and_tour_button_advances():
    qt_app()
    from PySide6 import QtWidgets
    from maya_tools.framework import first_run as fr
    advanced, skipped = [], []
    card = fr._WelcomeCard(on_tour=lambda: advanced.append(True),
                           on_skip=lambda: skipped.append(True))
    buttons = {b.text().upper(): b
               for b in card.dialog.findChildren(QtWidgets.QPushButton)}
    assert 'SHOW ME AROUND' in buttons and 'SKIP' in buttons, buttons
    card.show()
    buttons['SHOW ME AROUND'].click()
    assert advanced == [True] and skipped == []


def test_pointer_card_next_and_skip_paths():
    qt_app()
    from PySide6 import QtWidgets
    from maya_tools.framework import first_run as fr
    anchor = QtWidgets.QWidget(); anchor.resize(26, 26)
    nexts, skips = [], []
    card = fr._PointerCard(anchor, '// TEST', 'body text',
                           on_next=lambda: nexts.append(True),
                           on_skip=lambda: skips.append(True))
    buttons = {b.text().upper(): b
               for b in card.card.findChildren(QtWidgets.QPushButton)}
    assert 'NEXT' in buttons, buttons
    card.show()
    buttons['NEXT'].click()
    assert nexts == [True] and skips == []


def test_pointer_card_advances_on_probe_without_a_next_press():
    qt_app()
    import time
    from PySide6 import QtWidgets
    from PySide6.QtCore import QCoreApplication
    from maya_tools.framework import first_run as fr
    anchor = QtWidgets.QWidget(); anchor.resize(26, 26)
    state = {'open': False}
    nexts = []
    card = fr._PointerCard(anchor, '// PRESS IT', 'press the thing',
                           advance_probe=lambda: state['open'],
                           on_next=lambda: nexts.append(True),
                           on_skip=lambda: None)
    card.show()
    state['open'] = True
    deadline = time.monotonic() + 5.0
    while not nexts and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    assert nexts == [True], 'probe flip did not advance the tour'


def test_tour_spends_flag_at_welcome_show_time():
    qt_app()
    from maya_tools.framework import first_run as fr
    from maya_tools.framework.toolbar import toolbar_prefs
    tmp = Path(tempfile.mkdtemp()) / 'prefs.json'
    orig = toolbar_prefs.prefs_path
    toolbar_prefs.prefs_path = lambda: tmp
    try:
        fr._show_chain([fr.WELCOME_TOUR])
        prefs = toolbar_prefs.load_prefs(path=tmp)
        assert (prefs.get('onboarding') or {}).get(fr.WELCOME_TOUR) is True
    finally:
        toolbar_prefs.prefs_path = orig


def test_pointer_card_clamps_on_screen_for_right_edge_anchor():
    """A far-right anchor (the Settings button) must not push the card
    off-screen; the notch chases the anchor instead."""
    qt_app()
    from PySide6 import QtGui, QtWidgets
    from maya_tools.framework import first_run as fr
    r = QtGui.QGuiApplication.primaryScreen().availableGeometry()
    anchor = QtWidgets.QWidget()
    anchor.resize(26, 26)
    anchor.move(r.right() - 30, r.top() + 40)
    card = fr._PointerCard(anchor, '// EDGE', 'body',
                           on_next=lambda: None, on_skip=lambda: None)
    card.show()
    geo = card.card.geometry()
    assert geo.right() <= r.right(), (geo, r)
    assert geo.left() >= r.left(), (geo, r)
    assert card.card.notch_x > 40, (
        'notch did not chase the anchor after clamping: '
        f'{card.card.notch_x}')
    card._close(None)


def test_pointer_card_advances_on_anchor_press_exactly_once():
    qt_app()
    from PySide6 import QtCore, QtWidgets
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtTest import QTest
    from maya_tools.framework import first_run as fr
    anchor = QtWidgets.QWidget()
    anchor.resize(26, 26)
    anchor.show()
    nexts = []
    card = fr._PointerCard(anchor, '// PRESS THE THING', 'body',
                           on_next=lambda: nexts.append(True),
                           on_skip=lambda: None,
                           advance_on_anchor_press=True)
    card.show()
    QTest.mouseClick(anchor, QtCore.Qt.LeftButton)
    QTest.mouseClick(anchor, QtCore.Qt.LeftButton)   # double-fire guard
    for _ in range(20):
        QCoreApplication.processEvents()
    assert nexts == [True], nexts


def test_pointer_card_shows_step_eyebrow_dots_and_both_buttons():
    """The coachmark anatomy from Adrian's mockup (2026-07-19): STEP N
    OF M eyebrow, progress dots, ghost Skip + primary Next."""
    qt_app()
    from PySide6 import QtWidgets
    from maya_tools.framework import first_run as fr
    anchor = QtWidgets.QWidget(); anchor.resize(26, 26)
    card = fr._PointerCard(anchor, 'Your toolset', 'body text',
                           on_next=lambda: None, on_skip=lambda: None,
                           step=1, total=4)
    labels = [l.text() for l in card.card.findChildren(QtWidgets.QLabel)]
    assert any('STEP 1 OF 4' in t for t in labels), labels
    dots = [t for t in labels if '9679' in t or '●' in t]
    assert dots and dots[0].count('9679') + dots[0].count('●') == 4, (
        'progress dots missing or wrong count', labels)
    buttons = {b.text().upper()
               for b in card.card.findChildren(QtWidgets.QPushButton)}
    assert 'SKIP' in buttons and 'NEXT' in buttons, buttons
    card._close(None)


# ─── tour seams ──────────────────────────────────────────────────────────

def test_hover_annotation_pinned_skips_the_distance_watch():
    qt_app()
    from maya_tools.utils.qt.hover_annotation import HoverAnnotation
    from maya_tools.framework.annotations import Annotation
    card = HoverAnnotation()
    card.show_annotation(Annotation(title='T', text='body'), None,
                         pinned=True)
    assert not card._watch.isActive(), 'pinned card must not auto-hide'
    card.hide_annotation()
    card.show_annotation(Annotation(title='T', text='body'), None)
    assert card._watch.isActive(), 'default hover behavior regressed'
    card.hide_annotation()


def test_toolbar_widget_for_is_none_safe_without_a_strip():
    from maya_tools.framework.toolbar import toolbar_app
    assert toolbar_app.widget_for('settings') is None
    assert toolbar_app.is_visible() is False


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'Running {len(tests)} tests from test_first_run.py...')
    for name, fn in tests:
        check(name, fn)
    print(f'\n{len(tests) - len(FAILURES)} passed, {len(FAILURES)} failed '
          f'(of {len(tests)})')
    if FAILURES:
        print('\nFAILURES:')
        for f in FAILURES:
            print(f'  - {f}')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
