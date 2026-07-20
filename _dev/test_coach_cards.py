# _dev/test_coach_cards.py
"""Offscreen smoke tests for the standard coach-card consumers
(Adrian, 2026-07-19): hover annotations, toasts, and the ProgressCard
template all construct, style from the shared mindmeld helpers, and
run their lifecycles without a live Maya.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_coach_cards.py
(QT_QPA_PLATFORM=offscreen)
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

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


def test_coach_frame_qss_uses_tokens_only():
    from maya_tools.utils.qt.mindmeld import mindmeld_style as mm
    qss = mm.coach_frame_qss('QFrame#x')
    assert mm.IRON in qss and mm.PLASMA in qss
    assert mm.TOKENS['radius_md'] in qss
    warn = mm.coach_frame_qss('QFrame#x', mm.FLARE)
    assert mm.FLARE in warn and mm.PLASMA not in warn


def test_hover_annotation_wears_the_standard_frame():
    qt_app()
    from maya_tools.utils.qt import hover_annotation as ha
    from maya_tools.utils.qt.mindmeld import mindmeld_style as mm
    assert mm.PLASMA in ha._QSS, 'standard frame border missing'
    assert 'VT323' not in ha._QSS
    from maya_tools.framework.annotations import Annotation
    card = ha.HoverAnnotation()
    card.show_annotation(Annotation(title='T', text='b'), None, pinned=True)
    card.hide_annotation()


def test_toast_lifecycle_offscreen_and_no_vt323():
    qt_app()
    from maya_tools.utils.qt import toast
    t = toast.show_toast('Rig built', parent=None)
    assert t is not None
    assert 'VT323' not in t.styleSheet(), 'Mindmeld 1.0 font resurfaced'
    from maya_tools.utils.qt.mindmeld import mindmeld_style as mm
    assert mm.PLASMA in t.styleSheet()
    err = toast.show_toast('Export failed', kind='error', parent=None)
    assert mm.FLARE in err.styleSheet()
    err.hide()


def test_progress_card_lifecycle():
    qt_app()
    from maya_tools.utils.qt.progress_card import ProgressCard
    from maya_tools.utils.qt.mindmeld import mindmeld_style as mm
    card = ProgressCard.start('Building rig', 'Preparing...', parent=None)
    assert card._bar.value() == 0
    card.update(55, 'Wiring spaces...')
    assert card._bar.value() == 55
    assert card._status.text() == 'Wiring spaces...'
    card.finish('Rig built')
    assert card._bar.value() == 100
    assert mm.PLASMA in card.styleSheet()
    card.close()
    busy = ProgressCard.start('Exporting', busy=True, parent=None)
    assert busy._bar.maximum() == 0, 'busy mode must be indeterminate'
    busy.update(10)
    assert busy._bar.maximum() == 100
    busy.fail('Export failed')
    assert mm.FLARE in busy.styleSheet()
    busy.close()


def test_progress_card_guard_lifecycle_and_no_stacking():
    qt_app()
    from maya_tools.utils.qt import progress_card_guard as card
    # Clean slate whatever earlier tests did.
    card.finish()
    assert not card.is_active()
    owns = card.start('Building rig', 'Preparing scene...')
    assert owns, 'first start() must open and grant ownership'
    assert card.is_active()
    # A nested operation must NOT get a second card.
    assert card.start('Nested op') is False
    assert card.is_active()
    card.update(40, 'Building components...')
    card.finish('Rig built')
    assert not card.is_active()
    # After the owner closes, the next operation can open again.
    assert card.start('Exporting', busy=True)
    card.fail('Export failed. See the log.')
    assert not card.is_active()


def test_progress_card_guard_batch_is_silent_noop():
    qt_app()
    from maya_tools.utils.qt import progress_card_guard as card
    original = card._in_batch
    card._in_batch = lambda: True
    try:
        assert card.start('Building rig') is False
        assert not card.is_active()
        card.update(50)          # all no-ops, none may raise
        card.finish('done')
        card.fail('failed')
    finally:
        card._in_batch = original


def test_progress_card_guard_swallows_wounded_card():
    """A broken card must never wound the operation: update/finish/fail
    on a card that raises are swallowed and clear the active slot."""
    qt_app()
    from maya_tools.utils.qt import progress_card_guard as card

    class Wounded:
        def update(self, *a, **k):
            raise RuntimeError('boom')

        def finish(self, *a, **k):
            raise RuntimeError('boom')

        def fail(self, *a, **k):
            raise RuntimeError('boom')

    card._active = Wounded()
    card.update(10, 'tick')      # must not raise
    assert not card.is_active(), 'a raising card is dropped'
    card._active = Wounded()
    card.finish('done')          # must not raise
    assert not card.is_active()
    card._active = Wounded()
    card.fail('failed')          # must not raise
    assert not card.is_active()


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    print(f'Running {len(tests)} tests from test_coach_cards.py...')
    for name, fn in tests:
        check(name, fn)
    print(f'\n{len(tests) - len(FAILURES)} passed, {len(FAILURES)} failed '
          f'(of {len(tests)})')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
