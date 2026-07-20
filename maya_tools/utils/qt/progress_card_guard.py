# maya_tools/utils/qt/progress_card_guard.py
"""Guarded driver for ProgressCard — the sanctioned way a tool's UI
layer wraps a blocking operation in the standard coach-card progress
window (progress_card.py).

Why a module and not bare ProgressCard calls at every site:
  - Every call here is swallowed-guarded, so a card can never wound the
    operation it reports. Call sites stay one-liners.
  - One module-level active card enforces the no-stacking rule across
    tools: one operation, one card. A nested operation's start() is
    declined and its update() ticks ride the outer card.
  - Batch / headless Maya (cmds.about(batch=True)) is a silent no-op.

Ownership contract:

    from maya_tools.utils.qt import progress_card_guard as card

    owns = card.start('Building rig', 'Preparing scene...')
    try:
        ...blocking work, optionally card.update(pct, 'status')...
    finally:
        if owns:
            card.finish('Rig built')   # or card.fail('...') on error

Only the caller whose start() returned True closes the card; finish or
fail must fire on every exit path of that caller. update() is safe from
anywhere — non-owners simply tick the outer card (or no-op when none is
up). App-layer code never imports this module; cards are driven from
*_ui.py only.
"""
from __future__ import annotations

from typing import Optional

_active = None   # the one live ProgressCard, module-wide


def _in_batch() -> bool:
    try:
        import maya.cmds as cmds
        return bool(cmds.about(batch=True))
    except Exception:
        return False


def start(title: str, status: str = '', *, busy: bool = False) -> bool:
    """Open the standard progress card. Returns True when THIS call
    opened it — the caller then owns finish()/fail(). Returns False in
    batch mode, on any internal error, or when a card is already live
    (a nested operation rides the outer card)."""
    global _active
    try:
        if _active is not None or _in_batch():
            return False
        from maya_tools.utils.qt.progress_card import ProgressCard
        _active = ProgressCard.start(title, status, busy=busy)
        return _active is not None
    except Exception:
        _active = None
        return False


def update(value: int, status: Optional[str] = None) -> None:
    """Tick the live card: progress 0-100 plus an optional status line.
    Safe from any caller; no-op when no card is up."""
    global _active
    if _active is None:
        return
    try:
        _active.update(value, status)
    except Exception:
        _active = None


def finish(message: str = '') -> None:
    """Success close — flips to the success accent and auto-fades.
    Owner only (the caller whose start() returned True)."""
    global _active
    if _active is None:
        return
    try:
        _active.finish(message)
    except Exception:
        pass
    _active = None


def fail(message: str = '') -> None:
    """Failure close — error accent, then fades. Owner only. The caller
    still logs the real error; the card is a signal, not the record."""
    global _active
    if _active is None:
        return
    try:
        _active.fail(message)
    except Exception:
        pass
    _active = None


def is_active() -> bool:
    return _active is not None
