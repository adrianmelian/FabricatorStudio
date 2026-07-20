# maya_tools/rigging/fabricator/actions/common.py
"""Universal animation actions — appear on every Fabricator ctrl regardless
of component type. Injected by animation_menu._build_menu_items as a
'Common' section ahead of the per-component contract actions.

Each handler signature matches the contract.actions dispatch shape
(`handler(component_id, ctrl, **kwargs)`) but applies to the FULL Maya
selection of KS-tagged ctrls — animators expect Zero Translates / Zero
Rotations to fan out across whatever they have selected, not just the
single ctrl that triggered the marking menu.
"""
__author__ = "Adrian Melian"

import traceback

import maya.cmds as cmds


_T_PLUGS = ('translateX', 'translateY', 'translateZ')
_R_PLUGS = ('rotateX', 'rotateY', 'rotateZ')
_S_PLUGS = ('scaleX', 'scaleY', 'scaleZ')
_S_DEFAULTS = {'scaleX': 1.0, 'scaleY': 1.0, 'scaleZ': 1.0}


def _ks_ctrls_in_selection() -> list:
    sel = cmds.ls(selection=True, type='transform') or []
    return [n for n in sel
            if cmds.attributeQuery('fab_role', node=n, exists=True)]


def _reset_plugs(node: str, plugs: tuple, defaults: dict = None) -> None:
    """Set each keyable, unlocked plug to its default (0.0 unless overridden
    in `defaults`) at the current frame.

    Per-plug behavior:
      - not keyable / locked → skip (Zero Rotate on a ctrl with just RY
        keyable+unlocked touches only RY).
      - animated (has keyframes) → setKeyframe at the current time with
        an explicit value. Animated channels are connected destinations,
        so a plain setAttr would raise 'locked or connected'; keying is
        the only way to zero them. Explicit time+value is the reliable
        form — the bare value-only form was not applying on referenced
        rigs.
      - driven (constraint/expression) but not keyed → skip; setAttr
        would raise.
      - free → plain setAttr.

    Exceptions are printed, not swallowed — a silent except here is what
    masked the original failure.
    """
    defaults = defaults or {}
    now = cmds.currentTime(query=True)
    for p in plugs:
        plug = f'{node}.{p}'
        if not cmds.objExists(plug):
            continue
        if cmds.getAttr(plug, lock=True) or not cmds.getAttr(plug, keyable=True):
            continue
        value = defaults.get(p, 0.0)
        try:
            if cmds.keyframe(plug, query=True, keyframeCount=True):
                cmds.setKeyframe(plug, time=now, value=value)
            elif cmds.connectionInfo(plug, isDestination=True):
                continue  # constraint/expression-driven, no keys — leave it
            else:
                cmds.setAttr(plug, value)
        except Exception:
            traceback.print_exc()


def _refresh_eval() -> None:
    """Force the viewport to reflect freshly-set key values.

    setKeyframe at the current frame updates the animCurve, but Maya's
    Evaluation Manager keeps the cached pose for the current frame until a
    time change invalidates it — so the zeroed pose doesn't show until you
    scrub. Re-stamping the current time invalidates that cache and redraws,
    matching a manual frame change.
    """
    cmds.currentTime(cmds.currentTime(query=True), update=True)


def zero_all(component_id: str, ctrl: str, **_kwargs) -> None:
    """Zero T+R and reset S to 1 on every KS-tagged ctrl in the current
    selection. Locked + driven plugs are skipped — safe on partially-
    locked ctrls (PV, master switch, etc.)."""
    targets = _ks_ctrls_in_selection() or ([ctrl] if cmds.objExists(ctrl) else [])
    for n in targets:
        _reset_plugs(n, _T_PLUGS)
        _reset_plugs(n, _R_PLUGS)
        _reset_plugs(n, _S_PLUGS, _S_DEFAULTS)
    _refresh_eval()


def zero_translates(component_id: str, ctrl: str, **_kwargs) -> None:
    """Zero tx/ty/tz on every KS-tagged ctrl in the current selection.
    Locked + driven plugs are skipped silently."""
    targets = _ks_ctrls_in_selection() or ([ctrl] if cmds.objExists(ctrl) else [])
    for n in targets:
        _reset_plugs(n, _T_PLUGS)
    _refresh_eval()


def zero_rotations(component_id: str, ctrl: str, **_kwargs) -> None:
    """Zero rx/ry/rz on every KS-tagged ctrl in the current selection.
    Locked + driven plugs are skipped silently."""
    targets = _ks_ctrls_in_selection() or ([ctrl] if cmds.objExists(ctrl) else [])
    for n in targets:
        _reset_plugs(n, _R_PLUGS)
    _refresh_eval()


def zero_scale(component_id: str, ctrl: str, **_kwargs) -> None:
    """Reset sx/sy/sz to 1.0 on every KS-tagged ctrl in the current
    selection. Locked + driven plugs are skipped silently."""
    targets = _ks_ctrls_in_selection() or ([ctrl] if cmds.objExists(ctrl) else [])
    for n in targets:
        _reset_plugs(n, _S_PLUGS, _S_DEFAULTS)
    _refresh_eval()
