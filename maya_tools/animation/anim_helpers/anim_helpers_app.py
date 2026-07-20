# maya_tools/animation/anim_helpers/anim_helpers_app.py
"""Anim Helpers — shelf-button-level convenience for animation work.

Four functions, all rig-aware (active FAB_RigBinding):

- select_all_controls()      — replace selection with every Fabricator ctrl
- select_opposite_controls() — replace selection with each ctrl's mirror counterpart
- mirror_pose()              — swap-mirror the entire rig's pose across YZ
- mirror_selected_controls() — same swap-mirror, limited to current selection

Mirror semantics: SWAP. L↔R simultaneously, both sides reflect across YZ.
Center (md) ctrls mirror in place. Same call always produces a symmetric
pose regardless of which side is more posed.

Mirror math is per-channel. The per-ctrl rule (which channels flip
sign on the swap) lives on each component's CONTRACT — see
component.py:MirrorRule and the mirror_rules tuple on each module
(SimpleFK, FKChain, AdvancedFK, SimpleIK, IKLeg, ...). Each component
owns its own mirror semantics; the anim helper is a thin dispatcher.

    PAIR (L↔R) swap:    look up contract.mirror_rules[ctrl_role].negate;
                        flip those channels, copy the rest verbatim.
                        Components without a rule for a role: no flips.
    CENTER in-place:    negate translateY, translateZ, rotateX, rotateY.
                        Preserve translateX, rotateZ, scale.
                        (Global — center ctrls sit on the symmetry plane
                        regardless of component type. Empirically derived
                        from Adrian's UE5 mannequin pelvis convention;
                        per-component overrides can move here later if a
                        center ctrl with a different local frame needs
                        a different rule.)

The default behavior for unrecognized ctrls (no Fabricator tag, unknown
component) is verbatim swap with no flips — safe for mirror-behavior
rigs (UE5 mannequin convention, Maya's `mirrorJoint -mirrorBehavior 1`,
joint_mirror_app's output) where L and R local frames are pre-mirrored
at bind so the same Euler values on both sides produce a symmetric pose.

No matrix decomposition, no bind-matrix bookkeeping, no parentMatrix /
offsetParentMatrix arithmetic. Each channel is independent so write
order doesn't matter, and the result lands as channel-box values an
animator can read directly. Animated channels get setKeyframe at the
current time; static channels get setAttr; rig-driven channels
(constraints / expressions / blendMatrix) are left alone.

Pair resolution: Fabricator address tuple first (cross-rig portable,
namespace + rename resilient), then side_tokens.flip_side_token on the
short name as a fallback for non-Fabricator ctrls.

Custom keyable attrs (fk_ik_blend, stretchy switch, space enum) swap
verbatim across the pair — they have no spatial component to flip.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.animation.pose_library import address
from maya_tools.animation.pose_library import app as pose_app
from maya_tools.framework.decorators import undo_chunk
from maya_tools.rigging.fabricator import modules as fab_modules
from maya_tools.utils.maya import side_tokens


# ---------------------------------------------------------------------------
# Per-channel mirror dispatch
# ---------------------------------------------------------------------------
#
# Mirror rules are owned by each component's contract — see
# component.py:MirrorRule and the mirror_rules tuple on each component
# (SimpleFK, FKChain, AdvancedFK, SimpleIK, IKLeg, ...). The anim helper
# looks up the per-ctrl rule via fab_modules.get_mirror_negate and
# applies it.
#
# Default for ctrls without a contract rule (untagged ctrls, unknown
# components): empty frozenset = verbatim swap, no sign flips. Mirror-
# behavior rigs work correctly with no-flip by default; components that
# need flips declare them on their contract.
#
# Center (md) ctrls don't go through the contract lookup — they sit on
# the symmetry plane and use _CENTER_NEGATE for in-place mirror.

_CENTER_NEGATE = frozenset({
    'translateY', 'translateZ', 'rotateX', 'rotateY',
})


def _negate_set_for(ctrl: str) -> frozenset:
    """Look up the pair-swap negate set for `ctrl` via its component's
    contract.

    Returns an empty frozenset (verbatim swap, no sign flips) for
    untagged ctrls or components/roles with no declared rule — safe
    default for mirror-behavior rigs.

    Caller handles the md-center branch separately — _CENTER_NEGATE is
    NOT returned here.
    """
    addr = address.ctrl_to_address(ctrl)
    if not addr or not addr.ctrl_role:
        return frozenset()
    return fab_modules.get_mirror_negate(addr.component_type, addr.ctrl_role)


_TRS_CHANNELS = (
    'translateX', 'translateY', 'translateZ',
    'rotateX', 'rotateY', 'rotateZ',
    'scaleX', 'scaleY', 'scaleZ',
)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def select_all_controls() -> None:
    """Replace selection with every Fabricator ctrl on the active rig."""
    try:
        binding = pose_app._active_binding()
    except RuntimeError as exc:
        cmds.warning(str(exc))
        return
    ctrls = pose_app._walk_rig_ctrls(binding)
    if not ctrls:
        cmds.warning('Anim Helpers: no Fabricator ctrls under active rig.')
        return
    cmds.select(ctrls, r=True)
    cmds.inViewMessage(
        msg=f'Selected {len(ctrls)} ctrl(s)',
        pos='midCenter', fade=True,
    )


def select_opposite_controls() -> None:
    """Replace selection with each selected ctrl's mirror counterpart.

    Drops ctrls with no counterpart (center ctrls, untagged non-Fabricator
    ctrls without a side token in their name). Warns if none of the
    selection has a counterpart.
    """
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning('Anim Helpers: nothing selected.')
        return
    try:
        binding = pose_app._active_binding()
    except RuntimeError as exc:
        cmds.warning(str(exc))
        return
    opposites = []
    for ctrl in sel:
        opp = _find_opposite(ctrl, binding)
        if opp:
            opposites.append(opp)
    if not opposites:
        cmds.warning(
            'Anim Helpers: no opposites found '
            '(center ctrls and untagged ctrls have none).'
        )
        return
    cmds.select(opposites, r=True)


def reverse_selected_keys() -> int:
    """Reverse the time-order of selected keys in the Graph Editor.

    Scales selected keys by -1 in time around the midpoint of the
    selection's time range. After the operation the keys occupy the
    same overall time window but are reversed within it — first key
    becomes last and vice versa.

    Works across multiple curves at once: every selected key on every
    curve gets the same scale + pivot, so a multi-curve selection
    reverses as one block.

    Returns the count of keys reversed. 0 on no-op (no selection or
    all selected keys at the same time).
    """
    times = cmds.keyframe(q=True, sl=True, timeChange=True) or []
    if not times:
        cmds.warning('Reverse Keys: no keys selected in the Graph Editor.')
        return 0
    if min(times) == max(times):
        cmds.warning('Reverse Keys: selected keys are all at the same time; nothing to reverse.')
        return 0

    pivot = (min(times) + max(times)) / 2.0
    with undo_chunk('Reverse Keys'):
        cmds.scaleKey(animation='keys', timeScale=-1, timePivot=pivot)
    return len(times)


def mirror_pose() -> None:
    """Swap-mirror every Fabricator ctrl on the active rig across YZ."""
    try:
        binding = pose_app._active_binding()
    except RuntimeError as exc:
        cmds.warning(str(exc))
        return
    ctrls = pose_app._walk_rig_ctrls(binding)
    if not ctrls:
        cmds.warning('Anim Helpers: no Fabricator ctrls under active rig.')
        return
    with undo_chunk('Mirror Pose'):
        n = _mirror_ctrl_set(ctrls, binding)
    _refresh_viewport_at_current_time()
    cmds.inViewMessage(
        msg=f'Mirrored {n} ctrl(s)', pos='midCenter', fade=True,
    )


def mirror_selected_controls() -> None:
    """Swap-mirror just the currently-selected ctrls across YZ.

    Selecting only one side of a pair still mirrors the pair atomically —
    the opposite ctrl gets pulled in. Selecting both sides processes the
    pair once. Center ctrls mirror in place.
    """
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning('Anim Helpers: nothing selected.')
        return
    try:
        binding = pose_app._active_binding()
    except RuntimeError as exc:
        cmds.warning(str(exc))
        return
    with undo_chunk('Mirror Selected Controls'):
        n = _mirror_ctrl_set(sel, binding)
    _refresh_viewport_at_current_time()
    cmds.inViewMessage(
        msg=f'Mirrored {n} ctrl(s)', pos='midCenter', fade=True,
    )


# ---------------------------------------------------------------------------
# Mirror dispatch
# ---------------------------------------------------------------------------

def _mirror_ctrl_set(ctrls: list, binding: str) -> int:
    """Per-channel Euler swap. Returns count of ctrls touched.

    Pass 1 — resolve pairs: (src, dst) for L↔R; (src, None) for center /
             untagged-no-side-token.
    Pass 2 — snapshot every involved ctrl's TRS + custom attrs.
    Pass 3 — apply mirrored values. Order doesn't matter (channels are
             sibling-independent), but pairs swap atomically: src gets
             mirrored values from dst and vice versa, computed against
             snapshots so reads are never polluted by prior writes.
    """
    # Pass 1: pair resolution.
    pairs = []
    seen = set()
    for ctrl in ctrls:
        if ctrl in seen:
            continue
        seen.add(ctrl)
        opp = _find_opposite(ctrl, binding)
        if opp:
            seen.add(opp)
            pairs.append((ctrl, opp))
            continue
        if _is_center_or_untagged(ctrl):
            pairs.append((ctrl, None))
            continue
        cmds.warning(
            f'Anim Helpers: {ctrl!r} has a side token but no opposite '
            f'was found; skipped.'
        )

    if not pairs:
        return 0

    # Pass 2: snapshot TRS + custom attrs.
    snap_trs = {}
    snap_custom = {}
    for src, dst in pairs:
        for c in (src, dst):
            if c and c not in snap_trs:
                snap_trs[c] = _capture_trs(c)
                snap_custom[c] = _capture_custom_attrs(c)

    # Pass 3: apply mirrored values. Pair swap uses the per-role profile
    # (_negate_set_for); md-side in-place uses _CENTER_NEGATE.
    count = 0
    for src, dst in pairs:
        if dst:
            negate = _negate_set_for(src)
            _apply_trs(src, snap_trs[dst], negate)
            _apply_trs(dst, snap_trs[src], negate)
            _apply_custom_attrs(src, snap_custom[dst])
            _apply_custom_attrs(dst, snap_custom[src])
            count += 2
        else:
            _apply_trs(src, snap_trs[src], _CENTER_NEGATE)
            count += 1
    return count


def _find_opposite(ctrl: str, binding: str) -> str | None:
    """Return ctrl's mirror counterpart, or None if center/untagged-without-
    side-token / opposite-missing.

    Tries the Fabricator address path first (survives namespaces and
    renames). Falls back to side_tokens.flip_side_token on the short
    name for non-Fabricator ctrls.
    """
    addr = address.ctrl_to_address(ctrl)
    if addr and addr.side != 'md':
        opp_addr = address.Address(
            component_type=addr.component_type,
            side='rt' if addr.side == 'lf' else 'lf',
            role=addr.role,
            ctrl_role=addr.ctrl_role,
            joint_index=addr.joint_index,
        )
        resolved = address.address_to_ctrl(opp_addr, binding)
        if resolved:
            return resolved
    short = ctrl.split('|')[-1]
    flipped = side_tokens.flip_side_token(short)
    if flipped and flipped != short and cmds.objExists(flipped):
        return flipped
    return None


def _is_center_or_untagged(ctrl: str) -> bool:
    """True for center (md) Fabricator ctrls AND for untagged ctrls whose
    name has no side token. These mirror in place. Untagged side ctrls
    whose partner is missing return False (caller skips them).
    """
    addr = address.ctrl_to_address(ctrl)
    if addr:
        return addr.side == 'md'
    return side_tokens.detect_side(ctrl.split('|')[-1]) == 'md'


# ---------------------------------------------------------------------------
# Per-channel TRS capture / apply
# ---------------------------------------------------------------------------

def _capture_trs(ctrl: str) -> dict:
    """Snapshot non-locked, non-rig-driven TRS channel values.
    Returns {attr_name: value}. Animated and static channels both captured
    (getAttr returns the evaluated value at the current time either way).
    """
    out = {}
    for attr in _TRS_CHANNELS:
        if not cmds.attributeQuery(attr, node=ctrl, exists=True):
            continue
        plug = f'{ctrl}.{attr}'
        if cmds.getAttr(plug, lock=True):
            continue
        if _classify_plug_source(plug) == 'rig':
            continue
        out[attr] = cmds.getAttr(plug)
    return out


def _apply_trs(ctrl: str, src_values: dict, negate: frozenset) -> None:
    """Apply src_values to ctrl, negating channels listed in `negate`.
    Routes through _write_plug so animated channels land as keyframes at
    the current time; static channels get setAttr; rig-driven channels
    are left alone.
    """
    for attr, value in src_values.items():
        if attr in negate:
            value = -value
        _write_plug(f'{ctrl}.{attr}', value)


# ---------------------------------------------------------------------------
# Custom keyable user attrs (verbatim swap, no negation)
# ---------------------------------------------------------------------------

def _capture_custom_attrs(ctrl: str) -> dict:
    """Capture custom keyable user attrs (not TRS, not fab_*).
    Skips locked and rig-driven attrs. Animated and static attrs are
    captured by evaluated value.
    """
    out = {}
    for attr in (cmds.listAttr(ctrl, userDefined=True, keyable=True) or []):
        if attr.startswith('fab_'):
            continue
        plug = f'{ctrl}.{attr}'
        if cmds.getAttr(plug, lock=True):
            continue
        if _classify_plug_source(plug) == 'rig':
            continue
        try:
            out[attr] = cmds.getAttr(plug)
        except RuntimeError:
            continue
    return out


def _apply_custom_attrs(ctrl: str, attrs: dict) -> None:
    """Apply a custom-attr dict. Verbatim values (no sign flip — enum
    indices and blend factors are mirror-symmetric across the pair).
    """
    for attr, value in attrs.items():
        if not cmds.attributeQuery(attr, node=ctrl, exists=True):
            continue
        try:
            _write_plug(f'{ctrl}.{attr}', value)
        except RuntimeError:
            continue


# ---------------------------------------------------------------------------
# Plug-state classification + write strategy
# ---------------------------------------------------------------------------

def _classify_plug_source(plug: str) -> str:
    """Return how `plug` is currently being driven:

    - 'static':   no incoming connection. Use setAttr.
    - 'animated': driven by an animCurve (possibly through a
                  unitConversion / blendWeighted pass-through, which is
                  Maya's default for imported FBX rotation channels).
                  Use setKeyframe at the current time.
    - 'rig':     driven by something else (constraint, expression,
                 blendMatrix, character set, …). Leave alone — the rig
                 owns it; setAttr would fail.

    Uses cmds.keyframe's keyframeCount query, which walks past
    unitConversion etc. to find the underlying animCurve. A naive
    `nodeType startswith animCurve` check would misclassify
    unitConversion-mediated rotations as 'rig' and silent-skip them.
    """
    sources = cmds.listConnections(plug, source=True, destination=False,
                                    plugs=False) or []
    if not sources:
        return 'static'
    key_count = cmds.keyframe(plug, q=True, keyframeCount=True) or 0
    if key_count > 0:
        return 'animated'
    return 'rig'


def _write_plug(plug: str, value) -> None:
    """Write `value` to `plug` using the strategy that matches its drive
    state. Silent no-op on locked or rig-driven plugs.
    """
    if cmds.getAttr(plug, lock=True):
        return
    src = _classify_plug_source(plug)
    if src == 'static':
        cmds.setAttr(plug, value)
    elif src == 'animated':
        cmds.setKeyframe(plug, value=value)
    # else: 'rig' — leave alone.


# ---------------------------------------------------------------------------
# Viewport
# ---------------------------------------------------------------------------

def _refresh_viewport_at_current_time() -> None:
    """Force a viewport redraw after a batch of setKeyframe calls.
    Parallel EM caches per-frame evaluations; the no-op currentTime edit
    invalidates the cache, then refresh(force=True) redraws.
    """
    t = cmds.currentTime(q=True)
    cmds.currentTime(t, edit=True)
    cmds.refresh(force=True)
