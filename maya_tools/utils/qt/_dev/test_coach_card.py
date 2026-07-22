"""Offscreen tests for coach_card — the shared tour card.

Qt only, no Maya modules: but PySide6 ships with Maya and not with the
system python, so run it under mayapy, offscreen platform plugin:

    "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" \
        maya_tools/utils/qt/_dev/test_coach_card.py

Covers the parts that are cheap to get wrong in a promotion: the eyebrow
wording (act vs plain STEP), Next present only when there is no probe,
the fire-once close guard, probe-driven advance, load_media's
never-upscale contract, and animated-vs-static detection against the
real shipped tour media.
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


def test_load_media_contract():
    check('load_media(empty) is None', coach_card.load_media('', 100, 100) is None)
    check('load_media(missing file) is None',
          coach_card.load_media('nope_does_not_exist.gif', 100, 100) is None)


def test_load_media_against_the_real_shipped_files():
    """The delivered tour media, through the real loader. A static PNG
    must come back as a pixmap: routed through QMovie it reports
    frameCount() == 0 and renders an empty well."""
    media = (Path(__file__).resolve().parents[4]
             / 'maya_tools' / 'docs' / 'media' / 'tour')
    if not media.is_dir():
        check('tour media folder present (skipped, not installed)', True)
        return
    W, H = coach_card.GIF_MAX_W, coach_card.GIF_MAX_H

    for png in sorted(media.glob('*.png')):
        got = coach_card.load_media(str(png), W, H)
        check(f'{png.name} loads as a pixmap',
              got is not None and got[0] == 'pixmap')

    for gif in sorted(media.glob('*.gif')):
        got = coach_card.load_media(str(gif), W, H)
        check(f'{gif.name} loads as a movie',
              got is not None and got[0] == 'movie')
        if got:
            _, _, w, h = got
            check(f'{gif.name} renders 1:1 at {w}x{h}', (w, h) == (420, 378))


def test_missing_media_degrades():
    c = coach_card.CoachCard(None, 'T', 'B', media='nope_does_not_exist.gif')
    check('missing media leaves a text-only card at the narrow width',
          c.card.width() == coach_card.CARD_W)


def test_static_png_widens_the_card():
    media = (Path(__file__).resolve().parents[4]
             / 'maya_tools' / 'docs' / 'media' / 'tour' / 'components.png')
    if not media.is_file():
        check('png card widens (skipped, media not installed)', True)
        return
    c = coach_card.CoachCard(None, 'T', 'B', media=str(media))
    check('a still widens the card exactly like a clip does',
          c.card.width() == coach_card.CARD_W_GIF)


def test_fit_size():
    from maya_tools.utils.qt import hover_annotation as ha
    fit = coach_card.fit_size
    W, H = coach_card.GIF_MAX_W, coach_card.GIF_MAX_H

    # The house capture size must land 1:1 on BOTH cards — a clip shot to
    # spec should never be resampled.
    check('house 420x378 renders 1:1 on a tour card',
          fit(420, 378, W, H) == (420, 378))
    check('house 420x378 renders 1:1 on a hover card',
          fit(420, 378, ha.GIF_MAX_W, ha.GIF_MAX_H) == (420, 378))
    check('both cards cap to the same box',
          (W, H) == (ha.GIF_MAX_W, ha.GIF_MAX_H))

    check('never upscales a small clip', fit(100, 50, W, H) == (100, 50))
    check('an oversized clip scales down width-bound',
          fit(840, 756, W, H) == (420, 378))
    check('a landscape clip stays width-bound',
          fit(840, 200, W, H) == (420, 100))
    check('a very tall clip goes height-bound',
          fit(400, 1512, W, H) == (100, 378))
    check('zero-sized is None', fit(0, 10, W, H) is None)


def test_gif_card_fits_a_full_width_clip():
    usable = coach_card.CARD_W_GIF - 36      # 18px margin either side
    check('a full-width gif fits the widened card with no clipping',
          usable >= coach_card.GIF_MAX_W)


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
