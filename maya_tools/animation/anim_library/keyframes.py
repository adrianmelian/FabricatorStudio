# maya_tools/animation/anim_library/keyframes.py
"""Per-attribute keyframe capture + apply.

Pulled out of app.py so the curve math is unit-isolated:
- `list_animated_attrs(ctrl)` — which attrs on a ctrl have keys.
- `capture_attr_curve(ctrl, attr, start, end)` — curve + keys + tangents
  + pre/post infinity for one (ctrl, attr) within a time range.
  Injects synthetic anchor keys at start and end frames.
- `apply_attr_curve(ctrl, attr, attr_data, time_offset, anchor_filters,
                    start_frame, end_frame)` — write the captured data
  back onto a target plug, with time-offset and anchor filtering.

Reads/writes through cmds.keyframe + cmds.keyTangent + cmds.setInfinity,
mirroring the API choices the v1 anim_library.py prototyped.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds


def list_animated_attrs(ctrl: str) -> list:
    """Return keyable-attribute names on `ctrl` that have at least one key."""
    out = []
    for attr in (cmds.listAttr(ctrl, keyable=True) or []):
        if not cmds.attributeQuery(attr, node=ctrl, exists=True):
            continue
        plug = f'{ctrl}.{attr}'
        try:
            count = cmds.keyframe(plug, q=True, keyframeCount=True) or 0
        except RuntimeError:
            continue
        if count >= 1:
            out.append(attr)
    return out


def capture_attr_curve(ctrl: str, attr: str,
                       start: float, end: float) -> dict:
    """Capture every key on (ctrl, attr) in [start, end] plus curve props.

    Synthetic anchor keys are injected at `start` and `end` unless a real
    key already exists exactly at that frame. Anchor value is read via
    cmds.getAttr(plug, time=frame). Anchors get 'auto' tangents and are
    flagged `synthetic: True` so the load side can filter them.

    Returns a dict shaped like the attrs[<attr>] entry in the JSON schema.
    """
    plug = f'{ctrl}.{attr}'

    weighted = bool((cmds.keyTangent(plug, q=True, weightedTangents=True)
                     or [False])[0])
    pre, post = _query_infinity(plug)

    times = cmds.keyframe(plug, q=True, time=(start, end),
                          timeChange=True) or []
    values = cmds.keyframe(plug, q=True, time=(start, end),
                           valueChange=True) or []

    if times:
        in_types  = cmds.keyTangent(plug, q=True, time=(start, end), itt=True) or []
        out_types = cmds.keyTangent(plug, q=True, time=(start, end), ott=True) or []
        in_xs  = cmds.keyTangent(plug, q=True, time=(start, end), ix=True) or []
        in_ys  = cmds.keyTangent(plug, q=True, time=(start, end), iy=True) or []
        out_xs = cmds.keyTangent(plug, q=True, time=(start, end), ox=True) or []
        out_ys = cmds.keyTangent(plug, q=True, time=(start, end), oy=True) or []
        in_ws  = cmds.keyTangent(plug, q=True, time=(start, end), iw=True) or []
        out_ws = cmds.keyTangent(plug, q=True, time=(start, end), ow=True) or []
        locks  = cmds.keyTangent(plug, q=True, time=(start, end), lock=True) or []
        wlocks = cmds.keyTangent(plug, q=True, time=(start, end), wl=True) or []
    else:
        in_types = out_types = []
        in_xs = in_ys = out_xs = out_ys = []
        in_ws = out_ws = []
        locks = wlocks = []

    keys = []
    rounded_times = [round(float(t), 6) for t in times]
    has_start_key = round(float(start), 6) in rounded_times
    has_end_key   = round(float(end),   6) in rounded_times

    for i, t in enumerate(times):
        keys.append({
            'time': float(t),
            'value': float(values[i]),
            'in_type':  str(in_types[i])  if i < len(in_types)  else 'auto',
            'out_type': str(out_types[i]) if i < len(out_types) else 'auto',
            'in_x':  float(in_xs[i])  if i < len(in_xs)  else 1.0,
            'in_y':  float(in_ys[i])  if i < len(in_ys)  else 0.0,
            'out_x': float(out_xs[i]) if i < len(out_xs) else 1.0,
            'out_y': float(out_ys[i]) if i < len(out_ys) else 0.0,
            'in_weight':  float(in_ws[i])  if i < len(in_ws)  else 1.0,
            'out_weight': float(out_ws[i]) if i < len(out_ws) else 1.0,
            'lock':        bool(locks[i])  if i < len(locks)  else True,
            'weight_lock': bool(wlocks[i]) if i < len(wlocks) else True,
            'synthetic': False,
        })

    if not has_start_key:
        keys.append(_synthetic_anchor_key(start, _evaluate_at(plug, start)))
    if not has_end_key:
        keys.append(_synthetic_anchor_key(end, _evaluate_at(plug, end)))

    keys.sort(key=lambda k: k['time'])

    return {
        'weighted_tangents': weighted,
        'pre_infinity': pre,
        'post_infinity': post,
        'keys': keys,
    }


def apply_attr_curve(ctrl: str, attr: str, attr_data: dict, *,
                    time_offset: float = 0.0,
                    filter_start_anchor: bool = False,
                    filter_end_anchor: bool = False,
                    start_frame: float = 0.0,
                    end_frame: float = 0.0) -> int:
    """Write captured curve data back onto a target (ctrl, attr).

    time_offset: added to each saved key's time. For Place at currentTime,
        offset = currentTime - saved.time_range.start.
    filter_start_anchor / filter_end_anchor: drop the synthetic key at
        start_frame / end_frame (the target-side frames, already offset).
    start_frame / end_frame: target-side new start and end frames; used
        only to match synthetic anchors. Pass the offset frames, not
        the source frames.

    Returns the count of keys actually written. Silent-skips locked
    plugs and plugs that don't exist on the target ctrl.
    """
    plug = f'{ctrl}.{attr}'
    if not cmds.attributeQuery(attr, node=ctrl, exists=True):
        return 0
    if cmds.getAttr(plug, lock=True):
        return 0

    keys = attr_data.get('keys') or []
    weighted = bool(attr_data.get('weighted_tangents', False))

    written = 0
    for key in keys:
        new_time = float(key['time']) + time_offset

        if key.get('synthetic'):
            if filter_start_anchor and abs(new_time - start_frame) < 1e-6:
                continue
            if filter_end_anchor and abs(new_time - end_frame) < 1e-6:
                continue

        cmds.setKeyframe(plug, time=new_time, value=float(key['value']))
        cmds.keyTangent(plug, time=(new_time, new_time),
                        weightedTangents=weighted)

        in_type  = key.get('in_type',  'auto')
        out_type = key.get('out_type', 'auto')

        if in_type != 'fixed':
            cmds.keyTangent(plug, time=(new_time, new_time), itt=in_type)
        else:
            cmds.keyTangent(plug, time=(new_time, new_time),
                            ix=float(key.get('in_x', 1.0)),
                            iy=float(key.get('in_y', 0.0)))

        if out_type != 'fixed':
            cmds.keyTangent(plug, time=(new_time, new_time), ott=out_type)
        else:
            cmds.keyTangent(plug, time=(new_time, new_time),
                            ox=float(key.get('out_x', 1.0)),
                            oy=float(key.get('out_y', 0.0)))

        if weighted:
            cmds.keyTangent(plug, time=(new_time, new_time),
                            iw=float(key.get('in_weight',  1.0)),
                            ow=float(key.get('out_weight', 1.0)),
                            wl=bool(key.get('weight_lock', True)))

        cmds.keyTangent(plug, time=(new_time, new_time),
                        lock=bool(key.get('lock', True)))
        written += 1

    pre  = attr_data.get('pre_infinity',  'constant')
    post = attr_data.get('post_infinity', 'constant')
    try:
        cmds.setInfinity(plug, pri=pre, poi=post)
    except (RuntimeError, TypeError):
        pass

    return written


def _synthetic_anchor_key(time: float, value: float) -> dict:
    """A bake-start/end anchor key with safe default tangents."""
    return {
        'time': float(time),
        'value': float(value),
        'in_type': 'auto', 'out_type': 'auto',
        'in_x': 1.0, 'in_y': 0.0,
        'out_x': 1.0, 'out_y': 0.0,
        'in_weight': 1.0, 'out_weight': 1.0,
        'lock': True, 'weight_lock': True,
        'synthetic': True,
    }


def _evaluate_at(plug: str, time: float) -> float:
    """Evaluate a plug's value at a specific frame."""
    return float(cmds.getAttr(plug, time=float(time)))


# preInfinity / postInfinity enum mapping. Maya's animCurve node stores
# these as integers; the cmds.setInfinity setter accepts string names.
# Capture queries the int from the underlying animCurve and maps to the
# string name; apply hands the string back to cmds.setInfinity.
_INFINITY_INT_TO_NAME = {
    0: 'constant',
    1: 'linear',
    2: 'cycle',
    3: 'cycleRelative',
    4: 'oscillate',
}


def _query_infinity(plug: str) -> tuple:
    """Return (pre_infinity, post_infinity) as enum string names.

    cmds.setInfinity's -pri / -poi flags don't work in query mode (they
    are set-only). Walk the plug's upstream history to find the
    underlying animCurve — using cmds.listHistory so we traverse
    through pass-through intermediaries like unitConversion (Maya
    inserts these on rotation channels and FBX-imported animation),
    blendWeighted, character sets, etc. Read preInfinity / postInfinity
    int enum attrs on the curve directly and map to strings.
    Falls back to ('constant', 'constant') if no animCurve is reachable
    through the source chain.
    """
    history = cmds.listHistory(plug) or []
    curves = [n for n in history
              if (cmds.nodeType(n) or '').startswith('animCurve')]
    if not curves:
        return ('constant', 'constant')
    crv = curves[0]
    try:
        pre_int  = int(cmds.getAttr(f'{crv}.preInfinity'))
        post_int = int(cmds.getAttr(f'{crv}.postInfinity'))
    except RuntimeError:
        return ('constant', 'constant')
    return (
        _INFINITY_INT_TO_NAME.get(pre_int,  'constant'),
        _INFINITY_INT_TO_NAME.get(post_int, 'constant'),
    )
