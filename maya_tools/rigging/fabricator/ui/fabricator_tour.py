# maya_tools/rigging/fabricator/ui/fabricator_tour.py
"""The Fabricator guided tour — eight stops in two acts, between two
unnumbered bookend cards. Spec: MrMiata workspace/2026-07-21_fabricator-tour.

    welcome gate
    ACT 1 - THE LAYOUT              Armature Tools, Components,
                                    Rig Outliner, Properties
    ACT 2 - FIRST RIG WALKTHROUGH   intro, Templates, Build the rig,
                                    Changing it later
    outro

**Act 1 is read, act 2 is do.** Every act-1 stop names a region of the
window and advances on Next. Every act-2 stop after its intro advances
only when the real thing happens: the template actually loads, the build
actually completes, the unbuild actually lands. None of them can be
clicked past, which is why they carry no Next button at all — and why
each one spells its action out in a `hint`. Without that, Skip is the
only thing on the card that looks clickable and the card reads as
"skip or nothing" (Adrian hit this on the templates card).

Two things the runner has to get right here, both recorded in the spec:

1. **The window dies mid-tour, twice.** `_on_phase_action` calls
   `FSWindow._force_reopen()` after Build AND after Unbuild, which
   destroys this window and constructs a fresh one on the next event-loop
   tick. So anchors resolve through `FSWindow.widget_for(id)` at show
   time, never captured, and the two stops that trigger a reopen carry
   `settle=True` so the next card waits for the rebuild.
2. **Never instruct a done action.** Each stop's `skip_if` reads the live
   scene mode, so opening the tour on a scene that already has a rig
   starts where that user actually is.

Fires once ever on the first Fabricator open (seen-flag
'fabricator_tour'), and on demand from File > Take the Tour.
"""
from __future__ import annotations

__author__ = "Adrian Melian"

from pathlib import Path

from maya_tools.framework import tour_engine
from maya_tools.framework.tour_engine import Step

TOUR_FLAG = 'fabricator_tour'

_ACT1 = 'The Layout'
_ACT2 = 'First Rig Walkthrough'

# maya_tools/docs/media/tour/ — this module sits at
# maya_tools/rigging/fabricator/ui/fabricator_tour.py
_MEDIA = Path(__file__).resolve().parents[3] / 'docs' / 'media' / 'tour'


def _media(name: str) -> str:
    """Absolute path to a tour clip/still, or '' when it is not on disk.
    A missing file costs the card its picture, never the card."""
    p = _MEDIA / name
    return str(p) if p.is_file() else ''


def _banner() -> str:
    try:
        from maya_tools.framework.toolbar.widgets import icon_button
        p = icon_button._ICON_DIR / 'fs_fabricator_banner.png'
        return str(p) if p.is_file() else ''
    except Exception:
        return ''


# ─────────────────────────────────────────────
# Scene-mode predicates (pure reads, never raise)
# ─────────────────────────────────────────────

def _mode() -> str:
    try:
        from maya_tools.rigging.fabricator.ui import state
        return state.detect_mode()
    except Exception:
        return ''


def _is_built() -> bool:
    from maya_tools.rigging.fabricator.ui import state
    return _mode() == state.MODE_MODULES_BUILT


def _has_rig() -> bool:
    from maya_tools.rigging.fabricator.ui import state
    return _mode() in (state.MODE_SKELETON, state.MODE_MODULES_BUILT)


def _is_editable() -> bool:
    from maya_tools.rigging.fabricator.ui import state
    return _mode() == state.MODE_SKELETON


# ─────────────────────────────────────────────
# The steps
# ─────────────────────────────────────────────

def steps() -> list:
    """The authored step list. Built fresh each run so media presence and
    the scene mode are read now, not at import."""
    return [
        # Bookend: the consent gate. Skip here spends the flag, same as
        # finishing — a skipped tour is as final as a completed one.
        Step('welcome', '',
             'Fabricator builds animation rigs out of components you drop '
             'onto a skeleton. Two minutes, and you will have built one.',
             banner=_banner(), next_label='Show Me Around', centered=True),

        # Opened onto a built rig: the whole editing UI is hidden, so
        # there is nothing to tour until they come back to Edit Mode.
        Step('redirect', 'This rig is built',
             'Fabricator hides the editing tools while a rig is built. '
             'Press Edit Rig to drop back to the skeleton and I will pick '
             'up from there.',
             anchor='build_rig', advance='probe', probe=_is_editable,
             skip_if=lambda: not _is_built(), settle=True,
             hint='Press Edit Rig to continue.'),

        # ── ACT 1 — THE LAYOUT ────────────────────────────────────────
        Step('armature_tools', 'Armature Tools',
             'Everything for building the skeleton lives here. Skeleton '
             'adds and inserts joints, Aimers control orientation, Mirror '
             'and Duplicate copy limbs across, and Symmetry keeps both '
             'sides matched while you work. Right-click any of them for '
             'its options.',
             act=_ACT1, anchor='armature_tools', media=_media('add_joint.gif')),

        Step('components', 'Components',
             'The parts you build a rig out of. Templates are whole rigs to '
             'start from, Limbs are saved arms, legs and spines, and '
             'Components are the individual pieces. The list stays greyed '
             'out until there is a rig in the scene.',
             act=_ACT1, anchor='components', media=_media('components.png')),

        Step('rig_outliner', 'Rig Outliner',
             'Your skeleton joint by joint, with the component driving each '
             'one shown next to it. Drop components onto rows to attach '
             'them, middle-drag a row onto another to reparent it, '
             'right-click for mirror, duplicate and delete.',
             act=_ACT1, anchor='rig_outliner', media=_media('outliner.png')),

        Step('properties', 'Properties',
             'Select a joint or a component in the Rig Outliner and its '
             'settings appear here. Every option explains itself: hover any '
             'of them for a card like this one.',
             act=_ACT1, anchor='properties', media=_media('properties.png')),

        # ── ACT 2 — FIRST RIG WALKTHROUGH ─────────────────────────────
        # After the intro, each stop advances ONLY on the real thing
        # happening, carries no Next button, and states its action in a
        # hint so Skip never looks like the only option.
        #
        # Act-2 opener. Names what is about to happen and gets the user
        # into a clean scene first, so the template lands somewhere sane.
        # Next-driven on purpose: this is the last card before the tour
        # starts asking for real actions.
        Step('walkthrough_intro', "Let's build your first rig",
             'The rest of the tour is hands-on. Start a new scene if this '
             'one has anything in it, then continue.',
             act=_ACT2, next_label='Continue'),

        Step('templates', 'Templates',
             'A template is a whole skeleton with its rig already assembled '
             'on it, ready to build.',
             act=_ACT2, anchor='templates', media=_media('template.gif'),
             advance='probe', probe=_has_rig,
             skip_if=_has_rig, settle=True,
             hint='Drag Simple Biped into the scene to continue, '
                  'or double-click it.'),

        Step('build', 'Build the rig',
             'Fabricator checks the skeleton, bakes the orientation, and '
             'builds the controls.',
             act=_ACT2, anchor='build_rig', media=_media('build.gif'),
             advance='probe', probe=_is_built, settle=True,
             hint='Press Build Rig to continue.'),

        Step('edit', 'Changing it later',
             'Everything structural happens in Edit Mode. Nothing is lost '
             'going back, and Build Rig puts it all together again.',
             act=_ACT2, anchor='build_rig', media=_media('unbuild.gif'),
             advance='probe', probe=_is_editable, settle=True,
             hint='Press Edit Rig to continue.'),

        # Bookend: the payoff.
        Step('outro', 'That\'s the round trip',
             'Build to animate, Edit Rig to change the skeleton, build '
             'again. Everything else in Fabricator is a variation on that '
             'loop.\n\nMore at youtube.com/@Fabricator.Studio',
             banner=_banner(), next_label='Done', centered=True),
    ]


# ─────────────────────────────────────────────
# Running it
# ─────────────────────────────────────────────

_live_tour = None


def run(force: bool = False) -> bool:
    """Show the tour. Returns True if it started.

    force=True is File > Take the Tour: run regardless of the flag, and
    do not re-spend it. Otherwise the flag is spent AT SHOW TIME, so
    closing Maya mid-tour never re-nags.
    """
    global _live_tour
    try:
        import maya.cmds as cmds
        if cmds.about(batch=True):
            return False
        if not force:
            if tour_engine.has_seen(TOUR_FLAG):
                return False
            tour_engine.mark_seen(TOUR_FLAG)

        from maya_tools.rigging.fabricator.ui.fs_window import FSWindow
        from maya_tools.utils.maya.gui import get_maya_window

        _live_tour = tour_engine.Tour(
            steps(),
            FSWindow.widget_for,        # late resolution, see module docstring
            parent=get_maya_window())
        _live_tour.start()
        return True
    except Exception:
        import traceback
        traceback.print_exc()
        return False


def maybe_run_on_open() -> None:
    """Called from FSWindow's first show. Deferred so the splitter has
    laid out and the anchors have real geometry; guarded so onboarding
    can never wound the window it is describing."""
    try:
        import maya.cmds as cmds
        if cmds.about(batch=True):
            return
        if tour_engine.has_seen(TOUR_FLAG):
            return
        from PySide6 import QtCore
        QtCore.QTimer.singleShot(700, run)
    except Exception:
        import traceback
        traceback.print_exc()


def reset() -> None:
    """Un-spend the flag so the tour fires again on the next open."""
    tour_engine.reset(TOUR_FLAG)
