"""Offscreen tests for tour_engine + the Fabricator tour's step list.

Qt is needed for the runner, so run under mayapy with the offscreen
platform plugin:

    "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" \
        maya_tools/framework/_dev/test_tour_engine.py

The interesting cases are all failure modes that would show up as a tour
that quietly dies in the user's hands:
  - a missing anchor must skip its step, never the tour
  - skip_if must be honoured, and a raising skip_if must not swallow a step
  - anchors must resolve LATE, once per card, so the window can be
    destroyed and rebuilt mid-tour (Fabricator does this on every Build
    and every Unbuild)
  - act numbering must be per-act, never global
"""
from __future__ import annotations

__author__ = "Adrian Melian"

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from PySide6 import QtWidgets                       # noqa: E402

# Qt first, THEN Maya: initialising standalone before a QApplication
# exists makes userSetup fight the Qt import and the process dies quietly.
_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone                              # noqa: E402
maya.standalone.initialize(name='python')

from maya_tools.framework import tour_engine        # noqa: E402
from maya_tools.framework.tour_engine import Step   # noqa: E402
_failures = []


def check(label, cond):
    print(('  PASS  ' if cond else '  FAIL  ') + label)
    if not cond:
        _failures.append(label)


# ── pure: numbering ──────────────────────────────────────────────────

def test_numbering_is_per_act():
    steps = [
        Step('gate', 'g', 'b'),
        Step('a1', 't', 'b', act='One'), Step('a2', 't', 'b', act='One'),
        Step('b1', 't', 'b', act='Two'), Step('b2', 't', 'b', act='Two'),
        Step('b3', 't', 'b', act='Two'),
        Step('outro', 'o', 'b'),
    ]
    n = tour_engine.number_steps(steps)
    check('act One numbers 1..2', n['a1'] == (1, 2) and n['a2'] == (2, 2))
    check('act Two restarts at 1 of 3',
          n['b1'] == (1, 3) and n['b3'] == (3, 3))
    check('bookends carry no number',
          'gate' not in n and 'outro' not in n)


# ── pure: skip_if ────────────────────────────────────────────────────

def test_next_index_skips_and_stops():
    steps = [Step('a', 't', 'b', skip_if=lambda: True),
             Step('b', 't', 'b', skip_if=lambda: True),
             Step('c', 't', 'b')]
    check('skips a run of vetoed steps', tour_engine.next_index(steps, 0) == 2)
    check('past the end returns len', tour_engine.next_index(steps, 3) == 3)

    allskip = [Step('a', 't', 'b', skip_if=lambda: True)]
    check('all-skipped ends the tour',
          tour_engine.next_index(allskip, 0) == 1)


def test_raising_skip_if_shows_the_step():
    def boom():
        raise RuntimeError('scene read failed')
    steps = [Step('a', 't', 'b', skip_if=boom), Step('b', 't', 'b')]
    check('a raising skip_if shows the step rather than swallowing it',
          tour_engine.next_index(steps, 0) == 0)


# ── runner ───────────────────────────────────────────────────────────

def _run(steps, resolver):
    ended = {'v': False}
    t = tour_engine.Tour(steps, resolver, on_end=lambda: ended.__setitem__('v', True))
    t.start()
    return t, ended


def test_missing_anchor_skips_its_step_not_the_tour():
    seen = []

    def resolve(aid):
        seen.append(aid)
        return None if aid == 'gone' else QtWidgets.QWidget()

    steps = [Step('a', 't', 'b', anchor='gone'),
             Step('b', 't', 'b', anchor='here')]
    t, ended = _run(steps, resolve)
    check('the absent anchor was attempted', 'gone' in seen)
    check('the tour moved on to the next step', 'here' in seen)
    check('the tour did not end early', ended['v'] is False)


def test_all_anchors_missing_ends_cleanly():
    steps = [Step('a', 't', 'b', anchor='x'), Step('b', 't', 'b', anchor='y')]
    t, ended = _run(steps, lambda aid: None)
    check('every anchor missing ends the tour, without raising',
          ended['v'] is True)


def test_anchors_resolve_late_and_once_per_card():
    """Fabricator destroys and rebuilds its window on Build and Unbuild,
    so a resolver result captured up front is a dead widget by the next
    step. Nothing may be resolved before its own card is shown."""
    calls = []
    widgets = {}

    def resolve(aid):
        calls.append(aid)
        widgets[aid] = QtWidgets.QWidget()   # a NEW widget each time
        return widgets[aid]

    steps = [Step('a', 't', 'b', anchor='one'),
             Step('b', 't', 'b', anchor='two'),
             Step('c', 't', 'b', anchor='three')]
    t, _ = _run(steps, resolve)
    check('only the first card resolved on start', calls == ['one'])
    t._advance_from(0, False)
    check('the second resolved only when shown', calls == ['one', 'two'])
    t._advance_from(1, False)
    check('the third resolved only when shown',
          calls == ['one', 'two', 'three'])


def test_probe_steps_have_no_next_button():
    """An act-2 stop must not be clickable past: doing the thing is the
    only way forward."""
    steps = [Step('p', 't', 'b', advance='probe', probe=lambda: False)]
    t, _ = _run(steps, lambda aid: None)
    card = t._live[0]
    labels = [b.text().upper()
              for b in card.card.findChildren(QtWidgets.QPushButton)]
    check('a probe step offers no Next', 'NEXT' not in labels)
    check('a probe step still offers Skip', 'SKIP' in labels)


def test_skip_ends_the_whole_tour():
    steps = [Step('a', 't', 'b'), Step('b', 't', 'b')]
    t, ended = _run(steps, lambda aid: None)
    t._live[0]._close(t._live[0]._on_skip)
    check('Skip on any card ends the tour', ended['v'] is True)


def test_on_end_fires_exactly_once():
    hits = []
    t = tour_engine.Tour([Step('a', 't', 'b')], lambda a: None,
                         on_end=lambda: hits.append(1))
    t._end()
    t._end()
    check('on_end is idempotent', hits == [1])


# ── the real Fabricator tour's authored data ─────────────────────────

def test_fabricator_tour_shape():
    from maya_tools.rigging.fabricator.ui import fabricator_tour as ft
    steps = ft.steps()
    ids = [s.id for s in steps]
    check('tour has the authored stops in order',
          ids == ['welcome', 'redirect', 'armature_tools', 'components',
                  'rig_outliner', 'properties', 'templates', 'build',
                  'edit', 'outro'])

    n = tour_engine.number_steps(steps)
    check('act 1 numbers 1..4 of 4', n['armature_tools'] == (1, 4)
          and n['properties'] == (4, 4))
    check('act 2 restarts at 1 of 3',
          n['templates'] == (1, 3) and n['edit'] == (3, 3))
    check('the bookends are unnumbered',
          'welcome' not in n and 'outro' not in n)

    act2 = [s for s in steps if s.act == 'Build One']
    check('every act-2 stop advances on a real event',
          all(s.advance == 'probe' and s.probe for s in act2))
    check('every act-2 stop settles for the window rebuild',
          all(s.settle for s in act2))

    act1 = [s for s in steps if s.act == 'The Layout']
    check('every act-1 stop advances on Next',
          all(s.advance == 'next' for s in act1))
    check('every act-1 stop points at something',
          all(s.anchor for s in act1))

    check('bookends are centered and anchorless',
          all(s.centered and not s.anchor
              for s in steps if s.id in ('welcome', 'outro')))


def test_fabricator_tour_media_all_resolve():
    """Every media path the tour names must be on disk. A typo here is
    invisible: the card just renders text-only."""
    from maya_tools.rigging.fabricator.ui import fabricator_tour as ft
    expected = {'armature_tools', 'components', 'rig_outliner', 'properties',
                'templates', 'build', 'edit'}
    for s in ft.steps():
        if s.id in expected:
            check(f'{s.id}: media resolved ({Path(s.media).name or "MISSING"})',
                  bool(s.media) and Path(s.media).is_file())


def test_fabricator_tour_anchor_ids_are_known():
    """Each anchor id must exist in FSWindow's map, or that stop silently
    skips itself forever."""
    from maya_tools.rigging.fabricator.ui import fabricator_tour as ft
    from maya_tools.rigging.fabricator.ui.fs_window import FSWindow
    known = set(FSWindow._TOUR_ANCHORS)
    for s in ft.steps():
        if s.anchor:
            check(f'{s.id}: anchor {s.anchor!r} is a known id',
                  s.anchor in known)


def test_window_finder_prefers_the_live_instance():
    """The reopen hazard, exercised end to end.

    Fabricator's _force_reopen closes the old window and constructs the
    replacement immediately, so BOTH sit in topLevelWidgets until the
    dead one's deleteLater is processed. A first-match finder returns the
    corpse, whose widgets are all invisible, so every anchor resolves to
    None and act 2 silently skips its remaining stops. Act 2 crosses this
    twice, on Build and on Unbuild.
    """
    from maya_tools.rigging.fabricator.ui.fs_window import FSWindow
    # Maya's main window does not exist headless, so parent explicitly.
    host = QtWidgets.QWidget()
    host.show()

    first = FSWindow(parent=host)
    first.show()
    _app.processEvents()
    check('the live window is found', FSWindow.current() is first)
    check('its anchors resolve',
          FSWindow.widget_for('build_rig') is not None)

    # Reopen: close without letting deleteLater run, then rebuild. This
    # is the exact window in which both instances coexist.
    first.close()
    second = FSWindow(parent=host)
    second.show()
    _app.processEvents()

    names = [w for w in QtWidgets.QApplication.topLevelWidgets()
             if w.objectName() == FSWindow.WINDOW_NAME]
    check(f'both instances really are present ({len(names)})', len(names) >= 2)
    check('the finder returns the LIVE window, not the closed one',
          FSWindow.current() is second)
    check('so anchors still resolve across a reopen',
          FSWindow.widget_for('build_rig') is not None)

    for w in (first, second, host):
        try:
            w.close(); w.deleteLater()
        except Exception:
            pass
    _app.processEvents()


if __name__ == '__main__':
    for fn in [v for k, v in sorted(globals().items())
               if k.startswith('test_')]:
        print(fn.__name__)
        fn()
    print()
    if _failures:
        print(f'{len(_failures)} FAILED: ' + ', '.join(_failures))
        sys.exit(1)
    print('all tour_engine tests passed')
