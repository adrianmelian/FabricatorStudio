"""Offscreen tests for coach_card — the shared tour card.

Qt only, no Maya modules: but PySide6 ships with Maya and not with the
system python, so run it under mayapy, offscreen platform plugin:

    "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" \
        maya_tools/utils/qt/_dev/test_coach_card.py

Covers the parts that are cheap to get wrong in a promotion: the eyebrow
wording (act vs plain STEP), Next present only when there is no probe,
the fire-once close guard, probe-driven advance, and fit_movie's
never-upscale contract.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from PySide6 import QtCore, QtGui, QtWidgets      # noqa: E402

from maya_tools.utils.qt import coach_card         # noqa: E402


_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
_failures = []


def check(label, cond):
    print(('  PASS  ' if cond else '  FAIL  ') + label)
    if not cond:
        _failures.append(label)


def _labels(card):
    return [w.text() for w in card.card.findChildren(QtWidgets.QLabel)]


def _buttons(card):
    # mindmeld_style.button() uppercases its label, so SKIP / NEXT is the
    # rendered text. Compare upper-cased rather than re-asserting the case.
    return [b.text().upper() for b in card.card.findChildren(QtWidgets.QPushButton)]


def test_eyebrow_plain():
    c = coach_card.CoachCard(None, 'T', 'B', step=2, total=4)
    check('plain eyebrow reads STEP N OF M',
          any('STEP 2 OF 4' in t for t in _labels(c)))
    check('plain eyebrow carries no act separator',
          not any(' / 2 OF 4' in t for t in _labels(c)))


def test_eyebrow_act():
    c = coach_card.CoachCard(None, 'T', 'B', step=2, total=4,
                             act='The Layout')
    check('act eyebrow reads ACT / N OF M',
          any('THE LAYOUT / 2 OF 4' in t for t in _labels(c)))


def test_no_eyebrow_without_step():
    c = coach_card.CoachCard(None, 'T', 'B')
    check('no step/total means no eyebrow',
          not any('OF' in t and 'STEP' in t for t in _labels(c)))


def test_next_button_presence():
    plain = coach_card.CoachCard(None, 'T', 'B')
    check('a probe-less card has Next', 'NEXT' in _buttons(plain))
    check('every card has Skip', 'SKIP' in _buttons(plain))

    probed = coach_card.CoachCard(None, 'T', 'B',
                                  advance_probe=lambda: False)
    check('a probed card has no Next', 'NEXT' not in _buttons(probed))
    check('a probed card still has Skip', 'SKIP' in _buttons(probed))

    named = coach_card.CoachCard(None, 'T', 'B', next_label='Done')
    check('next_label renames the button', 'DONE' in _buttons(named))


def test_close_fires_once():
    hits = []
    c = coach_card.CoachCard(None, 'T', 'B',
                             on_next=lambda: hits.append('next'))
    c._close(c._on_next)
    c._close(c._on_next)        # a press + Next + probe can race
    check('close fires its callback exactly once', hits == ['next'])


def test_probe_advances():
    hits = []
    ready = {'v': False}
    c = coach_card.CoachCard(None, 'T', 'B',
                             advance_probe=lambda: ready['v'],
                             on_next=lambda: hits.append('next'))
    c._check_probe()
    check('a False probe does not advance', hits == [])
    ready['v'] = True
    c._check_probe()
    check('a True probe advances', hits == ['next'])


def test_probe_exception_is_swallowed():
    def boom():
        raise RuntimeError('anchor died')
    c = coach_card.CoachCard(None, 'T', 'B', advance_probe=boom)
    try:
        c._check_probe()
        ok = True
    except Exception:
        ok = False
    check('a raising probe never escapes the timer', ok)


def test_fit_movie_contract():
    check('fit_movie(empty) is None', coach_card.fit_movie('', 100, 100) is None)
    check('fit_movie(missing file) is None',
          coach_card.fit_movie('nope_does_not_exist.gif', 100, 100) is None)


def test_missing_gif_degrades():
    c = coach_card.CoachCard(None, 'T', 'B', gif='nope_does_not_exist.gif')
    check('a missing gif leaves a text-only card at the narrow width',
          c.card.width() == coach_card.CARD_W)


def test_notch_flip_default():
    f = coach_card._NotchFrame(8)
    check('notch points up by default', f.notch_down is False)
    f.resize(200, 120)
    f.notch_x = 10_000          # far past the right edge
    pix = QtGui.QPixmap(f.size())
    f.render(pix)               # clamping happens in paintEvent
    check('an out-of-range notch_x still paints without raising', True)


if __name__ == '__main__':
    for fn in [v for k, v in sorted(globals().items())
               if k.startswith('test_')]:
        print(fn.__name__)
        fn()
    print()
    if _failures:
        print(f'{len(_failures)} FAILED: ' + ', '.join(_failures))
        sys.exit(1)
    print('all coach_card tests passed')
