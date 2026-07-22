# maya_tools/framework/tour_engine.py
"""Guided tours — the generic runner behind every coachmark chain.

A tour is a list of Steps and a way to resolve an anchor id to a live
widget. The runner walks the list, shows one CoachCard at a time, and
advances on whatever that step says advances it.

Extracted 2026-07-21 so Fabricator's tour did not have to re-implement
the install tour's hand-chained `_stop_*` methods. Steps are data, so a
third tour is a list rather than a class.

Three rules the whole design serves, all inherited from the install tour
and all of them load-bearing:

1. **A tour never wounds its host.** Every failure is caught and printed.
   A step that raises ends up skipped, not fatal.
2. **A missing anchor skips its step, never the tour.** Panels come and
   go with mode; a tour that dies because one widget was absent is worse
   than one that quietly moves on.
3. **Anchors resolve LATE.** `resolve_anchor` is called at the moment a
   card is shown, never earlier. Fabricator destroys and rebuilds its
   whole window on Build and on Unbuild (`FSWindow._force_reopen`), so a
   widget captured when the tour started is a dangling C++ object by the
   time a later step points at it. This is why the resolver is a callable
   and not a dict of widgets.

Seen-flags live in toolbar prefs under 'onboarding', shared with
first_run.py, so a tour is one-time in the same way the install welcome
is.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

ONBOARDING_KEY = 'onboarding'       # dict inside toolbar prefs

# Breather after a step that advanced on a scene/mode change, before the
# next card is shown. Fabricator's build and unbuild flip the mode and
# THEN reopen the window on the next event-loop tick, so a probe can fire
# while the window the next card wants to point at is still being torn
# down. 500ms clears both the deferred reopen and its first layout pass.
SETTLE_MS = 500


# ─────────────────────────────────────────────
# Seen-flags (pure storage — no Qt)
# ─────────────────────────────────────────────

def has_seen(moment: str, prefs: dict = None) -> bool:
    """True when `moment` has already been spent. Missing key = unseen."""
    if prefs is None:
        from maya_tools.framework.toolbar import toolbar_prefs
        prefs = toolbar_prefs.load_prefs()
    seen = (prefs.get(ONBOARDING_KEY) or {}) if isinstance(prefs, dict) else {}
    return bool(isinstance(seen, dict) and seen.get(moment))


def mark_seen(moment: str) -> None:
    """Spend a moment's flag (atomic prefs write)."""
    from maya_tools.framework.toolbar import toolbar_prefs
    prefs = toolbar_prefs.load_prefs()
    onboarding = dict(prefs.get(ONBOARDING_KEY) or {})
    onboarding[moment] = True
    prefs[ONBOARDING_KEY] = onboarding
    toolbar_prefs.save_prefs(prefs)


def reset(moment: str) -> None:
    """Un-spend a flag so the tour runs again. QA seam, and what a
    'Take the Tour' re-entry calls."""
    from maya_tools.framework.toolbar import toolbar_prefs
    prefs = toolbar_prefs.load_prefs()
    onboarding = dict(prefs.get(ONBOARDING_KEY) or {})
    onboarding.pop(moment, None)
    prefs[ONBOARDING_KEY] = onboarding
    toolbar_prefs.save_prefs(prefs)


# ─────────────────────────────────────────────
# Step model (pure — offscreen-testable)
# ─────────────────────────────────────────────

class Step:
    """One card.

    anchor      anchor id handed to resolve_anchor; '' means a centered,
                anchorless card (the welcome gate and the outro).
    advance     'next'  — the Next button only
                'probe' — poll `probe()` 5x/s; True advances. No Next
                          button, so the step cannot be clicked past.
                'press' — clicking the anchor advances, Next also works.
    probe       zero-arg callable, required for advance='probe'.
    skip_if     zero-arg callable; True skips this step entirely. Used to
                never instruct an already-done action.
    settle      wait SETTLE_MS before showing the NEXT card (set on steps
                whose action rebuilds the window).
    hint        the action that advances a 'probe' step, spelled out in
                the button row. Without one, a probe card shows Skip and
                nothing else, and reads as though skipping is the only
                thing on offer (Adrian hit exactly this on the templates
                card, 2026-07-21). Required in spirit on every probe step.
    """

    def __init__(self, id, title, body, *, act=None, anchor='', media='',
                 banner='', advance='next', probe=None, skip_if=None,
                 next_label='Next', settle=False, centered=False,
                 hint=''):
        self.id = id
        self.title = title
        self.body = body
        self.act = act
        self.anchor = anchor
        self.media = media
        self.banner = banner
        self.advance = advance
        self.probe = probe
        self.skip_if = skip_if
        self.next_label = next_label
        self.settle = settle
        self.centered = centered
        self.hint = hint

    def __repr__(self):
        return f'<Step {self.id!r} act={self.act!r} advance={self.advance!r}>'


def number_steps(steps: list) -> dict:
    """{step.id: (n, total)} numbered WITHIN each act; steps with no act
    (the gate, the outro, mode redirects) get no number.

    Numbering is static, from the authored list, not from what actually
    ran. A skipped step leaving a gap ('2 OF 4' straight after '4 OF 4')
    is far less confusing than the count changing under the user
    mid-tour, and the alternative needs the whole tour resolved up front,
    which rule 3 forbids.
    """
    out = {}
    acts = {}
    for s in steps:
        if s.act:
            acts.setdefault(s.act, []).append(s.id)
    for act, ids in acts.items():
        for i, sid in enumerate(ids, 1):
            out[sid] = (i, len(ids))
    return out


def next_index(steps: list, start: int) -> int:
    """First index >= start whose skip_if does not veto it, or len(steps).

    A raising skip_if is treated as 'do not skip': showing a card that
    might be redundant beats silently swallowing a step.
    """
    i = start
    while i < len(steps):
        pred = steps[i].skip_if
        if pred is None:
            return i
        try:
            if not pred():
                return i
        except Exception:
            import traceback
            traceback.print_exc()
            return i
        i += 1
    return i


# ─────────────────────────────────────────────
# Runner (Qt — GUI sessions only)
# ─────────────────────────────────────────────

class Tour:
    """Walks a step list, one CoachCard at a time.

    resolve_anchor(anchor_id) -> QWidget or None, called at show time.
    on_end() fires once when the tour finishes, is skipped, or runs out.
    """

    def __init__(self, steps, resolve_anchor, *, on_end=None, parent=None):
        self._steps = list(steps)
        self._resolve = resolve_anchor
        self._on_end = on_end
        self._parent = parent
        self._numbers = number_steps(self._steps)
        self._ended = False
        self._live = []

    def start(self) -> None:
        self._show(0)

    # --- internals ---------------------------------------------------

    def _end(self) -> None:
        if self._ended:
            return
        self._ended = True
        if self._on_end:
            try:
                self._on_end()
            except Exception:
                import traceback
                traceback.print_exc()

    def _advance_from(self, index: int, settle: bool) -> None:
        """Move to the step after `index`, optionally after a breather."""
        if settle:
            from PySide6 import QtCore
            QtCore.QTimer.singleShot(SETTLE_MS, lambda: self._show(index + 1))
        else:
            self._show(index + 1)

    def _show(self, index: int) -> None:
        try:
            index = next_index(self._steps, index)
            if index >= len(self._steps):
                self._end()
                return
            step = self._steps[index]

            anchor = None
            if step.anchor:
                try:
                    anchor = self._resolve(step.anchor)
                except Exception:
                    import traceback
                    traceback.print_exc()
                    anchor = None
                if anchor is None:
                    # Rule 2: a missing anchor costs its step, not the tour.
                    print(f'[FS] tour: no anchor for {step.id!r}, skipping')
                    self._show(index + 1)
                    return

            # A resolver may hand back (widget, rect) to point at a
            # region inside a big widget rather than its centre.
            anchor_rect = None
            if isinstance(anchor, tuple):
                anchor, anchor_rect = anchor

            n, total = self._numbers.get(step.id, (None, None))
            from maya_tools.utils.qt import coach_card

            card = coach_card.CoachCard(
                anchor, step.title, step.body,
                anchor_rect=anchor_rect,
                hint=step.hint,
                media=step.media,
                banner=step.banner,
                on_next=lambda i=index, s=step: self._advance_from(i, s.settle),
                on_skip=self._end,
                advance_probe=step.probe if step.advance == 'probe' else None,
                advance_on_anchor_press=(step.advance == 'press'),
                next_label=step.next_label,
                step=n, total=total, act=step.act,
                centered=step.centered or not step.anchor,
                parent=self._parent)
            self._live.append(card)
            coach_card.keep(card)
            card.show()
        except Exception:
            import traceback
            traceback.print_exc()
            self._end()
