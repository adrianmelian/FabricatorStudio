# maya_tools/rigging/fabricator/armature_mirror.py
"""Selection-based live mirror for the Armature (spec 2026-07-04 §12).

The feel of Maya's native Move-tool symmetry, for Armature ctrls and
aimers: toggle on, grab the LEFT shoulder ctrl and move it — the right
follows; grab the RIGHT elbow aimer and twist — the left follows.
Direction follows whatever you selected, not a fixed L→R.

Mechanism — no DG cycles, no scriptJobs:
  * The mirror network is ONE-directional at any instant: the driver
    side's ctrl translates feed the counterpart through a -X flip
    (world-aligned ctrl trees make the local mirror exactly tx → -tx),
    ctrl rotations through rx, -ry, -rz (world-aligned frames on BOTH
    sides — convention-independent, a pure S-conjugation), and the
    driver side's aimer rotations feed the counterpart through the
    active convention's measured general mirror map
    (orientation_convention.mirror_channel_terms, probes 6-7): per
    channel dst = scale*src + offset, where the (scale, offset) pairs
    switch between the reversing and non-reversing frame classes off
    the LIVE aimTarget (a child slot or Parent reverses with the
    skeleton; Local / World does not — see _reverses_plug). Plus a
    direct aimTarget enum copy (child slots are positionally
    symmetric). Condition-driven, not baked, so retargeting an aimer
    mid-session retargets the map with it.
  * An om.MEventMessage 'SelectionChanged' callback watches for
    Armature ctrls / aimers in the selection; selecting the non-driver
    side rewires the network the other way. Selection always precedes
    the drag, so by the time you pull, your side is the driver.

Lifecycle: enable() builds pairs from the live scene and registers the
callback; disable() removes both. delete_armature() disables the
mirror defensively — a rebuilt Armature starts with the mirror off.
Scene new/open also auto-disables.

Pairing is name-based via side_tokens (lf ↔ rt); center joints have no
counterpart and never mirror.
"""
__author__ = "Adrian Melian"

import contextlib

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.utils.maya import orientation_convention as oc
from maya_tools.utils.maya import side_tokens

_CTRL_SUFFIX = '_amt_CTL'
_AIMER_SUFFIX = '_JntOrient'
_NODE_TAG = '_amtMirror_'      # every mirror DG node carries this

_STATE = {
    'enabled': False,
    'driver_side': 'lf',
    'cb_id': None,
    'scene_cb_ids': [],
    'pairs': [],               # [(lf_jnt, rt_jnt), ...]
}


# ─────────────────────────────────────────────
# Queries
# ─────────────────────────────────────────────

def is_enabled() -> bool:
    return _STATE['enabled']


def driver_side() -> str:
    return _STATE['driver_side']


def _find_pairs() -> list:
    """(lf_jnt, rt_jnt) for every left Armature ctrl whose right
    counterpart ctrl also exists."""
    pairs = []
    for ctrl in cmds.ls(f'*{_CTRL_SUFFIX}', type='transform') or []:
        jnt = ctrl[:-len(_CTRL_SUFFIX)]
        if side_tokens.detect_side(jnt) != 'lf':
            continue
        other = side_tokens.flip_side_token(jnt)
        if not other or other == jnt:
            continue
        if cmds.objExists(f'{other}{_CTRL_SUFFIX}'):
            pairs.append((jnt, other))
    return pairs


# ─────────────────────────────────────────────
# Wiring
# ─────────────────────────────────────────────

def _clear_network() -> None:
    for node in cmds.ls(f'*{_NODE_TAG}*') or []:
        if cmds.objExists(node):
            cmds.delete(node)
    # Direct connections (ty/tz, aimTarget) have no utility node to
    # delete — sever whatever remains between pair members. Aimer
    # rotateX stays in the list as legacy cleanup: pre-Phase-B networks
    # wired it as a direct connection (the old rx-passthrough map).
    for lf, rt in _STATE['pairs']:
        for a, b in ((lf, rt), (rt, lf)):
            for src_n, dst_n, attrs in (
                    (f'{a}{_CTRL_SUFFIX}', f'{b}{_CTRL_SUFFIX}',
                     ('ty', 'tz', 'rotateX')),
                    (f'{a}{_AIMER_SUFFIX}', f'{b}{_AIMER_SUFFIX}',
                     ('rotateX', 'aimTarget'))):
                if not (cmds.objExists(src_n) and cmds.objExists(dst_n)):
                    continue
                for attr in attrs:
                    plug = f'{dst_n}.{attr}'
                    for src_plug in cmds.listConnections(
                            plug, source=True, destination=False,
                            plugs=True) or []:
                        if src_plug.split('.')[0] == src_n:
                            cmds.disconnectAttr(src_plug, plug)


def _neg_node(src_plug: str, name: str) -> str:
    # skipSelect: createNode otherwise replaces the user's active
    # selection with the new utility node — during a direction flip
    # that read as "my ctrl deselected itself".
    mult = cmds.createNode('multDoubleLinear', name=name, skipSelect=True)
    cmds.connectAttr(src_plug, f'{mult}.input1')
    cmds.setAttr(f'{mult}.input2', -1.0)
    return f'{mult}.output'


def _reverses_plug(src_a: str, tag: str):
    """A live 0/1 'frame reverses' signal for src_a: 1 while its
    aimTarget sits on a REVERSING slot (a child, or Parent), 0 on
    Local / World.

    A child- or Parent-aimed offset node points down the bone, so its
    frame flips with the skeleton on the mirrored side. Local and World
    do NOT reverse — Local's frame IS the joint, which is already
    behavior-mirrored — and forcing a reversing map there does not stay
    in the channel: build_armature's orientation bake writes the
    aimer's world orientation into the joint, so the spurious flip
    lands in the joint and un-flips every childless tip. Same root
    cause as the Mirror Limb fix in branch_ops._mirror_branch_joints
    (2026-07-19).

    Live, not baked: the signal reads off src_a.aimTarget through
    condition nodes, so switching an aimer's enum mid-session retargets
    the map instead of going stale against a hardcoded constant.

    Returns a plug (str) to connect, or a constant (float): 1.0 when
    the enum is unreadable (legacy fallback — treat as reversing, the
    common case), 0.0 when the joint has no reversing slot at all.
    """
    try:
        enum = (cmds.attributeQuery('aimTarget', node=src_a,
                                    listEnum=True) or [''])[0].split(':')
    except Exception:
        return 1.0
    if 'Local' not in enum:
        return 1.0

    terms = []
    # Enum layout is children… : Local : World [: Parent], so every slot
    # before Local is a child.
    local_idx = enum.index('Local')
    if local_idx > 0:
        cond = cmds.createNode('condition', name=f'{tag}revKids',
                               skipSelect=True)
        cmds.setAttr(f'{cond}.operation', 4)          # Less Than
        cmds.setAttr(f'{cond}.secondTerm', float(local_idx))
        cmds.setAttr(f'{cond}.colorIfTrueR', 1.0)
        cmds.setAttr(f'{cond}.colorIfFalseR', 0.0)
        cmds.connectAttr(f'{src_a}.aimTarget', f'{cond}.firstTerm')
        terms.append(f'{cond}.outColorR')
    if 'Parent' in enum:
        cond = cmds.createNode('condition', name=f'{tag}revParent',
                               skipSelect=True)
        cmds.setAttr(f'{cond}.operation', 0)          # Equal
        cmds.setAttr(f'{cond}.secondTerm', float(enum.index('Parent')))
        cmds.setAttr(f'{cond}.colorIfTrueR', 1.0)
        cmds.setAttr(f'{cond}.colorIfFalseR', 0.0)
        cmds.connectAttr(f'{src_a}.aimTarget', f'{cond}.firstTerm')
        terms.append(f'{cond}.outColorR')

    if not terms:
        return 0.0
    if len(terms) == 1:
        return terms[0]
    # The slots are mutually exclusive, so summing them is a select.
    add = cmds.createNode('addDoubleLinear', name=f'{tag}revSel',
                          skipSelect=True)
    cmds.connectAttr(terms[0], f'{add}.input1')
    cmds.connectAttr(terms[1], f'{add}.input2')
    return f'{add}.output'


def _wire_aimer_channels(src_a: str, dst_a: str, tag: str, conv) -> None:
    """Wire src aimer rotations onto dst through the active
    convention's measured general mirror map: per channel,
    dst = scale * src + offset, with (scale, offset) switching between
    the reversing and non-reversing frame classes off the live
    aimTarget (probe 7 proved the maps hold as channel formulas, so
    scalar wiring is sufficient).

    Two condition nodes carry all six selected values — scales in one
    (RGB = x/y/z), offsets in the other — keyed off _reverses_plug's
    0/1 signal. When that signal is a static constant (legacy enum, or
    no reversing slot at all), the terms are set as static values and
    no conditions are created.
    """
    t_rev = conv.mirror_channel_terms(True)
    t_non = conv.mirror_channel_terms(False)
    rev = _reverses_plug(src_a, tag)

    if isinstance(rev, str):
        scale_sel = cmds.createNode('condition', name=f'{tag}mapScale',
                                    skipSelect=True)
        offs_sel = cmds.createNode('condition', name=f'{tag}mapOffs',
                                   skipSelect=True)
        for sel, vals_true, vals_false in (
                (scale_sel, [s for s, _ in t_rev], [s for s, _ in t_non]),
                (offs_sel, [o for _, o in t_rev], [o for _, o in t_non])):
            cmds.setAttr(f'{sel}.operation', 0)       # Equal
            cmds.setAttr(f'{sel}.secondTerm', 1.0)
            cmds.setAttr(f'{sel}.colorIfTrue', *vals_true,
                         type='double3')
            cmds.setAttr(f'{sel}.colorIfFalse', *vals_false,
                         type='double3')
            cmds.connectAttr(rev, f'{sel}.firstTerm')

    for i, (chan, letter) in enumerate(
            (('rotateX', 'R'), ('rotateY', 'G'), ('rotateZ', 'B'))):
        mult = cmds.createNode('multDoubleLinear', name=f'{tag}m{letter}',
                               skipSelect=True)
        add = cmds.createNode('addDoubleLinear', name=f'{tag}a{letter}',
                              skipSelect=True)
        cmds.connectAttr(f'{src_a}.{chan}', f'{mult}.input1')
        cmds.connectAttr(f'{mult}.output', f'{add}.input1')
        if isinstance(rev, str):
            cmds.connectAttr(f'{scale_sel}.outColor{letter}',
                             f'{mult}.input2')
            cmds.connectAttr(f'{offs_sel}.outColor{letter}',
                             f'{add}.input2')
        else:
            s, o = (t_rev if rev == 1.0 else t_non)[i]
            cmds.setAttr(f'{mult}.input2', s)
            cmds.setAttr(f'{add}.input2', o)
        cmds.connectAttr(f'{add}.output', f'{dst_a}.{chan}', force=True)


def _wrap_pair_aimer_channels() -> None:
    """Snap every pair aimer's rotate channels into (-180, 180].

    The measured general maps are affine per channel and one channel
    per map carries scale +1 with a nonzero offset (unreal reversing:
    rx - 180; standard Local: rz + 180), so a full L->R->L driver-flip
    round trip drifts that channel by 360 — same rotation, growing
    numbers (observed live at Checkpoint B: -360 / 540 after a couple
    of flips). Wrapping at each rewire boundary is rotation-preserving
    and runs while _clear_network has everything severed, so nothing
    downstream sees the snap as a user edit."""
    for lf, rt in _STATE['pairs']:
        for jnt in (lf, rt):
            aimer = f'{jnt}{_AIMER_SUFFIX}'
            if not cmds.objExists(aimer):
                continue
            for chan in ('rotateX', 'rotateY', 'rotateZ'):
                plug = f'{aimer}.{chan}'
                v = cmds.getAttr(plug)
                w = v - 360.0 * ((v + 180.0) // 360.0)
                if w <= -180.0:      # keep (-180, 180]
                    w += 360.0
                if abs(w - v) > 1e-9:
                    cmds.setAttr(plug, w)


def _wire_direction(driver: str) -> None:
    """Wire every pair driver-side → counterpart. driver: 'lf'|'rt'."""
    from maya_tools.rigging.fabricator import nodes as fab_nodes
    conv = oc.resolve(fab_nodes.get_registry() or '')
    _clear_network()
    _wrap_pair_aimer_channels()
    for lf, rt in _STATE['pairs']:
        src_j, dst_j = (lf, rt) if driver == 'lf' else (rt, lf)

        src_c = f'{src_j}{_CTRL_SUFFIX}'
        dst_c = f'{dst_j}{_CTRL_SUFFIX}'
        if cmds.objExists(src_c) and cmds.objExists(dst_c):
            out = _neg_node(f'{src_c}.tx', f'{dst_j}{_NODE_TAG}tx')
            cmds.connectAttr(out, f'{dst_c}.tx', force=True)
            cmds.connectAttr(f'{src_c}.ty', f'{dst_c}.ty', force=True)
            cmds.connectAttr(f'{src_c}.tz', f'{dst_c}.tz', force=True)
            # Ctrl rotations — both sides are world-aligned frames, so
            # the YZ-plane mirror is rx direct, -ry, -rz (no +180 term;
            # that belongs to the aimers, whose frames flip with the
            # skeleton). Guard on lock state: armatures built before
            # rotation was freed still carry locked rotate channels.
            if not cmds.getAttr(f'{dst_c}.rotateX', lock=True):
                cmds.connectAttr(f'{src_c}.rotateX', f'{dst_c}.rotateX',
                                 force=True)
                out_ry = _neg_node(f'{src_c}.rotateY',
                                   f'{dst_j}{_NODE_TAG}ctlRy')
                cmds.connectAttr(out_ry, f'{dst_c}.rotateY', force=True)
                out_rz = _neg_node(f'{src_c}.rotateZ',
                                   f'{dst_j}{_NODE_TAG}ctlRz')
                cmds.connectAttr(out_rz, f'{dst_c}.rotateZ', force=True)

        src_a = f'{src_j}{_AIMER_SUFFIX}'
        dst_a = f'{dst_j}{_AIMER_SUFFIX}'
        if cmds.objExists(src_a) and cmds.objExists(dst_a):
            # The active convention's measured general mirror map, per
            # channel, frame-class-switched off the live aimTarget —
            # see _wire_aimer_channels / _reverses_plug.
            _wire_aimer_channels(src_a, dst_a, f'{dst_j}{_NODE_TAG}',
                                 conv)
            try:
                cmds.connectAttr(f'{src_a}.aimTarget',
                                 f'{dst_a}.aimTarget', force=True)
            except Exception:
                pass  # asymmetric child counts — enum copy impossible
    _STATE['driver_side'] = driver


# ─────────────────────────────────────────────
# Selection callback
# ─────────────────────────────────────────────

def _side_of_selection() -> str:
    """'lf' / 'rt' when the selection contains an Armature ctrl or
    aimer of that side (last match wins), else ''."""
    side = ''
    for node in cmds.ls(selection=True) or []:
        short = node.split('|')[-1]
        if short.endswith(_CTRL_SUFFIX):
            jnt = short[:-len(_CTRL_SUFFIX)]
        elif short.endswith(_AIMER_SUFFIX):
            jnt = short[:-len(_AIMER_SUFFIX)]
        else:
            continue
        s = side_tokens.detect_side(jnt)
        if s in ('lf', 'rt'):
            side = s
    return side


def _rewire_preserving_selection(side: str) -> None:
    """Deferred direction flip that leaves the user's selection intact.

    skipSelect on the utility createNodes covers the common case; the
    capture/restore here guarantees it — the flip happens right after
    the user selects the new driver side, and losing that selection
    forces them to click twice."""
    if not _STATE['enabled']:
        return
    keep = cmds.ls(selection=True) or []
    _wire_direction(side)
    keep = [n for n in keep if cmds.objExists(n)]
    if (cmds.ls(selection=True) or []) != keep:
        if keep:
            cmds.select(keep, replace=True)
        else:
            cmds.select(clear=True)


def _on_selection_changed(*_):
    if not _STATE['enabled']:
        return
    side = _side_of_selection()
    if side and side != _STATE['driver_side']:
        # Defer the rewire out of the selection event — editing the DG
        # mid-callback is asking for evaluation-order trouble.
        cmds.evalDeferred(
            lambda s=side: _rewire_preserving_selection(s))


def _on_scene_reset(*_):
    disable(quiet=True)


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def enable() -> dict:
    """Turn the live mirror on: pair up sides from the live Armature,
    wire lf→rt initially, watch selection for direction flips.

    Returns {'pairs': int, 'driver': str}. Raises when there is
    nothing to mirror (no Armature ctrls with side counterparts).
    """
    if _STATE['enabled']:
        disable(quiet=True)

    pairs = _find_pairs()
    if not pairs:
        raise RuntimeError(
            'Live Mirror: no left/right Armature ctrl pairs in the '
            'scene. Build the Armature (with side-tokened joint names) '
            'first.')

    _STATE['pairs'] = pairs
    _wire_direction('lf')

    _STATE['cb_id'] = om.MEventMessage.addEventCallback(
        'SelectionChanged', _on_selection_changed)
    for ev in (om.MSceneMessage.kAfterNew, om.MSceneMessage.kAfterOpen):
        _STATE['scene_cb_ids'].append(
            om.MSceneMessage.addCallback(ev, _on_scene_reset))

    _STATE['enabled'] = True
    return {'pairs': len(pairs), 'driver': _STATE['driver_side']}


@contextlib.contextmanager
def preserved():
    """Keep the live mirror alive across an Armature teardown→rebuild.

    delete_armature() disables the mirror defensively, which is right
    for a bare teardown — but ops that rebuild immediately after
    (branch mirror / duplicate / reparent) left the Symmetry toggle
    reading ON while the network was actually dead. Wrap the whole
    sandwich in this: if the mirror was on going in and got knocked
    off, it re-enables against the rebuilt ctrls on the way out
    (pairs re-derive from the live scene, so branches created inside
    the sandwich pair up too). Restore is best-effort — a failure
    warns instead of masking the op's own result."""
    was_on = is_enabled()
    try:
        yield
    finally:
        if was_on and not is_enabled():
            try:
                enable()
            except Exception as e:
                cmds.warning(
                    f'Live Mirror: could not re-enable after the '
                    f'Armature rebuild: {e}')


def disable(quiet: bool = False) -> None:
    """Turn the live mirror off: sever the network, drop callbacks.
    Counterpart ctrls/aimers freeze at their current mirrored values."""
    if _STATE['cb_id'] is not None:
        try:
            om.MMessage.removeCallback(_STATE['cb_id'])
        except Exception:
            pass
        _STATE['cb_id'] = None
    for cid in _STATE['scene_cb_ids']:
        try:
            om.MMessage.removeCallback(cid)
        except Exception:
            pass
    _STATE['scene_cb_ids'] = []

    try:
        _clear_network()
    except Exception:
        if not quiet:
            raise
    _STATE['pairs'] = []
    _STATE['enabled'] = False
