# maya_tools/rigging/fabricator/curve_mirror.py
"""Curve Mirror — replace the opposite-side ctrl's NURBS curve with a
world-space YZ-mirrored copy of the selected ctrl's curve.

Workflow:
1. Animator/rigger customizes L_arm_ctrl's curve shape (via Curve-O-Matic
   or direct CV edits).
2. Selects L_arm_ctrl. Clicks 'Mirror Curve' in Curve-O-Matic.
3. This module:
   - flips the ctrl name via side_tokens.flip_side_token
   - errors if R ctrl doesn't exist OR selection has no side token
   - calls com.serialize_shape(L_ctrl), pushes each CV through L's
     worldMatrix, negates world X, pulls back through R's
     worldInverseMatrix. This handles ctrls whose two sides have
     mirror-flipped local frames (clavicles, fingers, anything aimed
     differently per side — naïve local-X negation lands them rotated
     180° around the aim axis).
   - calls com.deserialize_shape_to(R_ctrl, flipped_data) — replaces
     R's shapes entirely (handles "different shape type" — the old R shape
     is discarded, R now carries L's shape mirrored)
   - if R is a Fabricator ctrl (carries fab_owner connection), writes
     the mirrored CV data to R's component network node's persisted.cv_data
     so the change survives unbuild/rebuild

Does NOT touch L's curve. Only R's curve is replaced.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
from maya_tools.rigging.fabricator import nodes as fab_nodes
from maya_tools.utils.maya import side_tokens


def _world_yz_mirror_cvs(shape_data: dict,
                          source_ctrl: str,
                          target_ctrl: str) -> dict:
    """Return a shape-data dict whose CVs are the world-space YZ-mirror
    of the source, expressed in the target ctrl's LOCAL space.

    Naïve local-X negation only works when both ctrls share world
    orientation. Side ctrls bound to mirror-aimed joints (clavicles,
    fingers, anything where R's local frame is rolled vs L) end up
    shape-rotated-180-around-the-aim with that approach — Adrian's
    clavicle came back upside-down.

    Pipeline per CV:
      local_src → world (× source.worldMatrix)
      world     → mirrored world (negate X)
      mirrored  → target local (× target.worldInverseMatrix)

    Multi-shape entries are handled — every entry's `cvs` list is
    converted independently.
    """
    src_wm = om.MMatrix(cmds.xform(source_ctrl, q=True, ws=True, m=True))
    tgt_wim = om.MMatrix(
        cmds.xform(target_ctrl, q=True, ws=True, m=True)
    ).inverse()

    out = {'shapes': []}
    for shape in shape_data.get('shapes', []):
        new_shape = dict(shape)
        if 'cvs' in new_shape and new_shape['cvs']:
            new_cvs = []
            for cv in new_shape['cvs']:
                p_local_src = om.MPoint(cv[0], cv[1], cv[2])
                p_world     = p_local_src * src_wm
                p_world.x   = -p_world.x
                p_local_tgt = p_world * tgt_wim
                new_cvs.append([p_local_tgt.x, p_local_tgt.y, p_local_tgt.z])
            new_shape['cvs'] = new_cvs
        out['shapes'].append(new_shape)
    return out


def component_ctrl_map() -> dict:
    """One full-scene walk -> {component_network_node: [ctrl, ...]} for
    every transform carrying a fab_owner connection. Compute ONCE and
    hand buckets to mirror_component_ctrls when processing several
    components — the walk is O(scene transforms) and must not repeat per
    component (review wf_2f4a49ba finding #4)."""
    out: dict = {}
    for ctrl in cmds.ls(type='transform') or []:
        if not cmds.attributeQuery('fab_owner', node=ctrl, exists=True):
            continue
        owners = cmds.listConnections(f'{ctrl}.fab_owner',
                                      source=True, destination=False) or []
        for owner in owners:
            out.setdefault(owner, []).append(ctrl)
    return out


def _component_ctrls(component_node: str) -> list:
    """All ctrl transforms whose fab_owner message connection points back
    at component_node. Single-component convenience; batch callers use
    component_ctrl_map()."""
    return component_ctrl_map().get(component_node, [])


def _opposite_ctrl(ctrl: str) -> str:
    """The live opposite-side counterpart of a ctrl, or ''.

    Flips EVERY side token in the short name first — ribbon segment
    ctrls concatenate two sided joint names
    ('upperarm_l_lowerarm_l_ribbon_mid_00_ctrl'), and the mirror flips
    every joint name, so a first-token-only flip misses them (review
    wf_2f4a49ba finding #2). Falls back to the single-token flip for
    names where a later token is coincidental. Re-applies the source's
    namespace — same handling as mirror_curve below (finding #3)."""
    short = ctrl.split('|')[-1].split(':')[-1]
    ns = ''
    if ':' in ctrl.split('|')[-1]:
        ns = ctrl.split('|')[-1].rsplit(':', 1)[0] + ':'
    for flipped in (side_tokens.flip_all_side_tokens(short),
                    side_tokens.flip_side_token(short)):
        if flipped and flipped != short and cmds.objExists(f'{ns}{flipped}'):
            return f'{ns}{flipped}'
    return ''


def mirror_component_ctrls(src_component_id: str, ctrls: list = None) -> int:
    """Mirror every live ctrl curve of the source component onto its
    opposite-side counterpart ctrl (world-space YZ flip, the same math as
    mirror_curve — naive local-X negation lands rolled on mirror-aimed
    frames).

    Called from the build_modules tail for components flagged
    persisted.pending_cv_mirror at Mirror Modules time — the one moment
    both sides' ctrls exist with final world transforms. Persistence is
    the normal lifecycle's job: the next unbuild captures the mirrored
    shapes into each module's own cv_data blocks.

    ctrls: the source component's ctrl list (a component_ctrl_map()
    bucket); resolved via a fresh scene walk when omitted.

    Ctrls whose flipped name resolves to nothing live (tokenless
    settings ctrls, asymmetric extras) are skipped. Returns the number
    of ctrl curves replaced; per-ctrl failures warn and continue — a
    shape copy must never fail a build.
    """
    if ctrls is None:
        src_node = fab_nodes.find_component_node_by_id(src_component_id)
        if not src_node:
            cmds.warning(f'Mirror Ctrl Shapes: source component '
                         f'{src_component_id!r} not found; skipping.')
            return 0
        ctrls = _component_ctrls(src_node)
    n = 0
    for ctrl in ctrls:
        try:
            target = _opposite_ctrl(ctrl)
            if not target:
                continue
            shape_data = com.serialize_shape(ctrl)
            if not shape_data.get('shapes'):
                continue
            flipped = _world_yz_mirror_cvs(shape_data, ctrl, target)
            com.deserialize_shape_to(target, flipped)
            n += 1
        except Exception as exc:
            cmds.warning(f'Mirror Ctrl Shapes: {ctrl!r} -> opposite side '
                         f'failed: {exc}')
    return n


def mirror_curve(source_ctrl: str = '') -> str:
    """Replace the opposite-side ctrl's curve with a YZ-mirrored copy
    of source_ctrl's curve.

    source_ctrl='' reads the current Maya selection's first transform.

    Returns the target ctrl name on success. Raises RuntimeError on:
    - empty selection
    - source ctrl has no side token
    - target ctrl doesn't exist
    - source has no nurbsCurve shapes
    """
    if not source_ctrl:
        sel = cmds.ls(sl=True, type='transform') or []
        if not sel:
            raise RuntimeError(
                'Mirror Curve: select a ctrl first.'
            )
        source_ctrl = sel[0]

    if not cmds.objExists(source_ctrl):
        raise RuntimeError(
            f'Mirror Curve: source ctrl {source_ctrl!r} does not exist.'
        )

    # Find opposite-side ctrl by flipping the name's side token.
    short = source_ctrl.split('|')[-1].split(':')[-1]
    target_short = side_tokens.flip_side_token(short)
    if target_short is None:
        raise RuntimeError(
            f'Mirror Curve: {source_ctrl!r} has no recognized side token '
            f'(L/R, lf/rt, left/right). Rename to include one, or this op '
            f'doesn\'t apply (center ctrls have no opposite side).'
        )

    # Preserve any namespace on the source.
    ns = ''
    if ':' in source_ctrl:
        ns = source_ctrl.rsplit(':', 1)[0] + ':'
    target_ctrl = f'{ns}{target_short}'

    if not cmds.objExists(target_ctrl):
        raise RuntimeError(
            f'Mirror Curve: target ctrl {target_ctrl!r} does not exist. '
            f'Mirror the joints + components first, or hand-create the '
            f'opposite ctrl.'
        )

    # Serialize source's shapes, axis-flip the CVs, deserialize onto target.
    shape_data = com.serialize_shape(source_ctrl)
    if not shape_data.get('shapes'):
        raise RuntimeError(
            f'Mirror Curve: source ctrl {source_ctrl!r} has no nurbsCurve '
            f'shapes to copy.'
        )
    flipped = _world_yz_mirror_cvs(shape_data, source_ctrl, target_ctrl)
    com.deserialize_shape_to(target_ctrl, flipped)

    # Persist the mirrored CVs to the target's component network node so
    # the change survives unbuild/rebuild. Only applies when the target
    # is a Fabricator-tagged ctrl with a fab_owner connection.
    if cmds.attributeQuery('fab_owner', node=target_ctrl, exists=True):
        owners = cmds.listConnections(
            f'{target_ctrl}.fab_owner',
            source=True, destination=False,
        ) or []
        if owners:
            cnode = owners[0]
            try:
                persisted = fab_nodes.get_component_persisted(cnode)
                cv_data = dict(persisted.get('cv_data') or {})
                # Component's ctrl_shape option keys the cv_data dict.
                options = fab_nodes.get_component_options(cnode)
                shape_key = options.get('ctrl_shape', '')
                if shape_key and flipped['shapes']:
                    # Persist the full shape list so multi-shape library
                    # curves (sphere_arrow, sphere, …) survive the next
                    # unbuild/rebuild with all their shapes intact.
                    cv_data[shape_key] = flipped['shapes']
                    persisted['cv_data'] = cv_data
                    fab_nodes.set_component_persisted(cnode, persisted)
            except Exception as exc:
                cmds.warning(
                    f'Mirror Curve: persisted CV write failed for '
                    f'{target_ctrl!r}: {exc}. Visual change still applied; '
                    f'will revert on next unbuild/rebuild.'
                )

    return target_ctrl
