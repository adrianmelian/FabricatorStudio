# Python/maya_tools/export/anim_root_motion.py
"""Anim exporter — root motion transfer.

Pre-bake step for the anim runner. Reads a source ctrl's worldspace
trajectory over [start, end], computes the delta from frame `start`,
and keys the rig's world ctrl with that curve. The existing joint
bake then decomposes back to local TRS — root joint carries the
motion via its constraint, hip joint's local curves become residual.

Works in both interactive Maya and the mayapy subprocess (no Qt deps,
no UI imports).
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.utils.maya import rig_binding


# ─────────────────────────────────────────────────────────────────────────────
# World ctrl discovery
# ─────────────────────────────────────────────────────────────────────────────

def find_world_ctrl(binding: str) -> str | None:
    """Find the rig's world ctrl by walking its controls_grp for a transform
    tagged fab_role='world_ctrl' (set by Fabricator's WorldComponent).

    Returns the ctrl path, or None if the binding has no controls_grp or
    no World component was built.
    """
    matches = _find_ctrls_by_role(binding, {'world_ctrl'})
    return matches[0] if matches else None


def find_ik_end_ctrls(binding: str) -> list[str]:
    """Find IK end ctrls under a binding's controls_grp (transforms tagged
    fab_role='ik_end_ctrl'). These animator-authored worldspace anchors
    (feet, hands) get DAG-dragged when the world ctrl moves and need
    compensation during root motion transfer. Pass the result as the
    `extra_compensate` argument to `transfer_root_motion`.
    """
    return _find_ctrls_by_role(binding, {'ik_end_ctrl'})


def _find_ctrls_by_role(binding: str, roles: set[str]) -> list[str]:
    """Walk a binding's controls_grp and return every transform whose
    fab_role attr matches one of `roles`. Returns [] when no controls_grp
    is connected or the binding's rig was never built.
    """
    controls_grp = rig_binding.get_controls_grp(binding)
    if not controls_grp or not cmds.objExists(controls_grp):
        return []
    descendants = cmds.listRelatives(controls_grp, allDescendents=True,
                                     type='transform', fullPath=True) or []
    out: list[str] = []
    for node in descendants:
        if not cmds.attributeQuery('fab_role', node=node, exists=True):
            continue
        if cmds.getAttr(f'{node}.fab_role') in roles:
            out.append(node)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Transfer
# ─────────────────────────────────────────────────────────────────────────────

_AXIS_TABLE = {
    'x':   ['tx'],
    'z':   ['tz'],
    'xz':  ['tx', 'tz'],
    'xyz': ['tx', 'ty', 'tz'],
}


def parse_axes(axes: str) -> list[str]:
    """Map an axes spec to translate channel names. Unknown spec → 'xz'."""
    return list(_AXIS_TABLE.get((axes or 'xz').lower(), ['tx', 'tz']))


def transfer_root_motion(world_ctrl: str, source_ctrl: str,
                         start: int, end: int, axes: str = 'xz',
                         extra_compensate: list[str] | None = None) -> None:
    """Transfer the source ctrl's worldspace motion onto the world ctrl over
    [start, end], so the rig's root joint will carry the displacement after
    the joint bake.

    Three phases per frame range:
      A. Pre-sample worldspace translation for the source ctrl AND every
         ctrl listed in `extra_compensate`, at every frame.
      B. Key the world ctrl's translate channels with delta-from-rest values
         (source ctrl's worldspace at frame `start` is the rest reference).
      C. Compensate each worldspace-anchored ctrl (source + extras) so its
         worldspace position stays put. Without this, ctrls that are DAG
         descendants of world_ctrl (typical Fabricator hierarchy — pelvis
         and IK foot ctrls are children of root_ctrl) get dragged by the
         world ctrl in phase B and produce double-displacement / stretched
         IK chains.

    axes:
        'xz'  — tx, tz only (default, horizontal root motion)
        'x'   — tx only
        'z'   — tz only
        'xyz' — tx, ty, tz (use for flying/swimming)

    extra_compensate: additional worldspace-anchored ctrls besides
        `source_ctrl`. Typically the rig's IK end ctrls (feet, hands).
        Pass `find_ik_end_ctrls(binding)` for a Fabricator rig. Pass an
        empty list or None to compensate only `source_ctrl`.

    Destructive on:
      - world ctrl translate keys inside [start, end] on the targeted channels
      - local translate keys on each compensated ctrl inside [start, end]
        on the targeted channels (compensation pass)

    Non-targeted axes (e.g. Y bounce when axes='xz') are not keyed and
    survive untouched. Channels and keys outside the range are left alone.

    Safe in the export runner's mayapy subprocess (throwaway scene). For
    interactive smoke testing, save a copy first or revert after.
    """
    if not cmds.objExists(world_ctrl):
        raise RuntimeError(f'transfer_root_motion: world_ctrl missing: {world_ctrl!r}')
    if not cmds.objExists(source_ctrl):
        raise RuntimeError(f'transfer_root_motion: source_ctrl missing: {source_ctrl!r}')
    if cmds.ls(world_ctrl, long=True) == cmds.ls(source_ctrl, long=True):
        raise RuntimeError('transfer_root_motion: source and world ctrls cannot be the same node')

    extras = list(extra_compensate or [])
    world_long = cmds.ls(world_ctrl, long=True)
    for c in extras:
        if not cmds.objExists(c):
            raise RuntimeError(f'transfer_root_motion: extra_compensate ctrl missing: {c!r}')
        if cmds.ls(c, long=True) == world_long:
            raise RuntimeError(
                f'transfer_root_motion: extra_compensate cannot contain the world ctrl: {c!r}'
            )

    # De-dup (caller may pass the source as part of extras) and preserve order.
    ctrls_to_compensate: list[str] = [source_ctrl]
    seen_long = {cmds.ls(source_ctrl, long=True)[0]}
    for c in extras:
        long = cmds.ls(c, long=True)[0]
        if long not in seen_long:
            ctrls_to_compensate.append(c)
            seen_long.add(long)

    channels = parse_axes(axes)
    axis_to_idx = {'tx': 0, 'ty': 1, 'tz': 2}
    attr_long = {'tx': 'translateX', 'ty': 'translateY', 'tz': 'translateZ'}

    # Pre-flight: bail before any DG mutation if a target channel is locked
    # on the world ctrl OR any compensated ctrl. Mid-loop abort would leave
    # the scene half-keyed.
    for ch in channels:
        if cmds.getAttr(f'{world_ctrl}.{ch}', lock=True):
            raise RuntimeError(
                f'transfer_root_motion: world ctrl channel locked: '
                f'{world_ctrl}.{ch} (unlock or narrow the axes spec)'
            )
        for c in ctrls_to_compensate:
            if cmds.getAttr(f'{c}.{ch}', lock=True):
                raise RuntimeError(
                    f'transfer_root_motion: compensated ctrl channel locked: '
                    f'{c}.{ch} (unlock or narrow the axes spec)'
                )

    # Resolve parent for each compensated ctrl (DAG is stable across frames,
    # so resolve once).
    parents: dict[str, str | None] = {}
    for c in ctrls_to_compensate:
        pp = cmds.listRelatives(c, parent=True, fullPath=True)
        parents[c] = pp[0] if pp else None

    # Phase A: pre-sample worldspace AND pre-Phase-B parent worldMatrix for
    # the source AND every extra ctrl, at every frame in the range. Must
    # happen BEFORE any DG mutation — phase B's keys would otherwise pollute
    # later samples through the DAG.
    samples: dict[str, dict[int, list[float]]] = {c: {} for c in ctrls_to_compensate}
    parent_wms_pre_B: dict[str, dict[int, list[float]]] = {
        c: {} for c in ctrls_to_compensate if parents[c]
    }
    for f in range(int(start), int(end) + 1):
        cmds.currentTime(f, edit=True)
        for c in ctrls_to_compensate:
            samples[c][f] = cmds.xform(c, query=True,
                                       worldSpace=True, translation=True)
            if parents[c]:
                parent_wms_pre_B[c][f] = cmds.getAttr(
                    f'{parents[c]}.worldMatrix[0]'
                )

    rest = samples[source_ctrl][start]

    # Phase B: write world ctrl with delta from source's rest on targeted channels.
    for ch in channels:
        idx = axis_to_idx[ch]
        for f, sw in samples[source_ctrl].items():
            cmds.setKeyframe(world_ctrl, attribute=ch, time=f,
                             value=sw[idx] - rest[idx])

    # Phase C: compensate each ctrl so its worldspace matches the pre-sample.
    #
    # Compute the new local translation as `target_world * inverse(parent_worldMatrix)`
    # via OpenMaya matrix math, then key it directly. Bypasses cmds.xform's
    # set-worldspace path (which in headless mayapy didn't survive the
    # subsequent setKeyframe — anim-curve re-evaluation reverted the local
    # plug). Also bypasses the DG-eval-order pitfall in cmds.xform.
    #
    # parent_worldMatrix is read at frame f after currentTime sets the time,
    # so it reflects Phase B's new world_ctrl keys through the DAG. Direct
    # axis subtraction (an earlier attempt) was wrong for ctrls whose
    # parent has bind rotation baked in (e.g. pelvis_ctrl_offset on a UE5
    # convention rig) — local axes don't align with world axes.
    # Construct the post-Phase-B parent worldMatrix MANUALLY rather than
    # reading it back from the DG. Headless mayapy didn't reliably propagate
    # Phase B's new world_ctrl keys to parent.worldMatrix reads (neither
    # currentTime+getAttr nor getAttr(time=f) worked) — Phase C's inverse
    # math then produced no compensation and Adrian saw doubled FBX output.
    #
    # The math is straightforward when world_ctrl translates only (axes
    # spec is XZ-only here, no rotation): every DAG descendant's
    # worldMatrix translation row is incremented by the same delta;
    # rotation/scale rows are unchanged. So:
    #     new_parent_wm = pre_B_parent_wm with [12]+=dx, [13]+=dy, [14]+=dz
    # Yaw transfer would invalidate this — flag for future rotation-aware
    # compensation.
    for c in ctrls_to_compensate:
        parent = parents[c]
        for f, target_world in samples[c].items():
            delta = [0.0, 0.0, 0.0]
            for ch in channels:
                idx = axis_to_idx[ch]
                delta[idx] = samples[source_ctrl][f][idx] - rest[idx]
            if parent is None:
                local_pt = om.MPoint(*target_world)
            else:
                wm_list = list(parent_wms_pre_B[c][f])
                wm_list[12] += delta[0]
                wm_list[13] += delta[1]
                wm_list[14] += delta[2]
                new_parent_wm = om.MMatrix(wm_list)
                local_pt = om.MPoint(*target_world) * new_parent_wm.inverse()
            for ch in channels:
                idx = axis_to_idx[ch]
                cmds.setKeyframe(c, attribute=attr_long[ch],
                                 time=f, value=local_pt[idx])
