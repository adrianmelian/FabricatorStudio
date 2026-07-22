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


def test_notch_frame_paints_every_side():
    f = coach_card._NotchFrame(8)
    check('notch sits on top by default', f.notch_side == 'top')
    f.resize(200, 120)
    for side in coach_card._NotchFrame._SIDES:
        f.notch_side = side
        f.notch_pos = 10_000        # far past the edge; clamped in paintEvent
        try:
            f.render(QtGui.QPixmap(f.size()))
            ok = True
        except Exception:
            ok = False
        check(f'notch_side={side!r} paints without raising', ok)


def _screen_rect():
    return QtGui.QGuiApplication.primaryScreen().availableGeometry()


def _place(anchor_geo, card_w, card_h):
    """Run the real placement against a synthetic anchor, return the
    card's final geometry plus which edge the notch landed on."""
    r = _screen_rect()
    host = QtWidgets.QWidget()
    host.setGeometry(*anchor_geo)
    c = coach_card.CoachCard(host, 'T', 'B')
    c.card.setFixedSize(card_w, card_h)
    c._place_against(host)
    g = QtCore.QRect(c.card.pos(), QtCore.QSize(card_w, card_h))
    return g, c.card.notch_side, r


def test_card_never_leaves_the_screen():
    """The bug Adrian hit: a tall anchor flipped the card above itself
    and off the top of the screen, because only x was ever clamped."""
    r = _screen_rect()
    cases = [
        ('short anchor near the top',   (400, 40, 120, 30)),
        ('TALL panel anchor',           (400, 120, 260, max(40, r.height() - 260))),
        ('anchor at the very bottom',   (400, max(0, r.bottom() - 40), 120, 30)),
        ('anchor at the far right',     (max(0, r.right() - 130), 300, 120, 30)),
        ('anchor at the far left',      (0, 300, 120, 30)),
        ('anchor taller than screen',   (400, 0, 200, r.height() + 400)),
    ]
    for label, geo in cases:
        g, side, _ = _place(geo, coach_card.CARD_W_GIF, 560)
        on = (g.left() >= r.left() and g.top() >= r.top()
              and g.right() <= r.right() and g.bottom() <= r.bottom())
        check(f'{label}: card fully on-screen (notch={side})', on)


def test_tall_anchor_goes_beside_not_above():
    """A full-height panel has no room above or below, so the card must
    move to its side. Geometry is derived from the REAL screen: the
    offscreen plugin gives an 800x800 virtual desktop, narrow enough that
    a hardcoded anchor leaves no room either side and the test passes
    vacuously on 'none'."""
    r = _screen_rect()
    card_w = min(coach_card.CARD_W_GIF, r.width() // 2 - 40)
    aw = 200
    if r.width() < card_w + aw + 60:
        check('tall anchor sits beside (skipped, screen too narrow)', True)
        return
    # Hard against the left edge, so the whole card fits to its right.
    tall = (r.left() + 10, r.top() + 60, aw, max(40, r.height() - 120))
    _, side, _ = _place(tall, card_w, min(560, r.height() - 40))
    check(f'tall anchor places the card to its side (got {side!r})',
          side == 'left')


def test_notch_points_back_at_the_anchor():
    """Wherever the card lands, the notch must sit over the anchor - a
    pointer aimed at empty space is worse than no pointer."""
    r = _screen_rect()
    geo = (r.left() + 300, r.top() + 60, 120, 30)
    host = QtWidgets.QWidget()
    host.setGeometry(*geo)
    c = coach_card.CoachCard(host, 'T', 'B')
    c.card.setFixedSize(coach_card.CARD_W, 200)
    c._place_against(host)

    check(f'short anchor keeps the card below it (got {c.card.notch_side!r})',
          c.card.notch_side == 'top')
    notch_x = c.card.pos().x() + c.card.notch_pos
    anchor_centre_x = geo[0] + geo[2] // 2
    check(f'notch lands on the anchor centre '
          f'(notch {notch_x}, anchor {anchor_centre_x})',
          abs(notch_x - anchor_centre_x) <= 1)


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
