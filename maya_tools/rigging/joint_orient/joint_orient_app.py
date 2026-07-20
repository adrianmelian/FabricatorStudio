# joint_orient_app.py
# Joint Orient Tool — headless API. No UI imports.
#
# Architecture overview:
#   Each joint gets an offset transform + XYZ arrow curve (the "aimer"):
#       JntOrient_GRP
#       └── {jnt}_JntOrient_offset   ← pointConstraint + aimConstraint land here
#           └── {jnt}_JntOrient      ← XYZ NURBS curve, zeroed; user rotates this freely
#
#   Enum dispatch (no scriptJobs):
#     A multi-target aimConstraint on the offset is wired to one condition node per
#     target. aimer.aimTarget (int enum) feeds condition.firstTerm; each condition
#     outputs 1.0 or 0.0 to aimConstraint.target[i].targetWeight.
#
#   Enum targets:  child_0 : child_1 : … : Local : World
#     Local → localRef_LOC parented under the joint at aim_axis*scale; exact local aim.
#     World → worldRef_LOC under a worldNull (pointConstrained to the joint,
#             rotation pinned 0,0,0, never frozen). A choice node swaps the
#             aimConstraint's worldUpMatrix to the null in World mode so the
#             result is exact world identity — no roll contamination from
#             the joint's own up.
#
#   Axes come from utils/maya/orientation_convention (house: +X aim, +Y up).
#
#   Orient flow (orient_all_aimers):
#     1. Delete offset constraints (prevents conflict during unparent).
#     2. Unparent all joints to world (preserves world transform).
#     3. Zero jointOrient, orient-constrain joint to aimer ctrl, delete constraint.
#     4. Reparent top-down — cmds.parent preserves world transform, recomputes local rotate.
#     5. Delete all aimers.
__author__ = "Adrian Melian"

import math

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.utils.maya import side_tokens
from maya_tools.utils.maya import orientation_convention as oc

# Active convention. The standalone tool runs house; Fabricator's Armature
# may rebind this per-rig via oc.resolve(registry) before creating
# aimers (spec 2026-07-04 §11).
CONVENTION = oc.HOUSE


def _resolve_convention() -> 'oc.Convention':
    """The active mirror convention: the Fabricator registry stamp when
    a rig is in the scene, else house. Lazily imports fabricator (it
    imports this module, so a top-level import would be circular); a
    scene with no registry — the standalone Joint Orient tool's normal
    life — resolves house. (t48 Phase A, 2026-07-19: mirror_aimers on a
    Standard-stamped rig must apply Standard maps.)"""
    try:
        from maya_tools.rigging.fabricator import nodes as fab_nodes
        return oc.resolve(fab_nodes.get_registry() or '')
    except Exception:
        return oc.HOUSE

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

_AIMER_GRP     = 'JntOrient_GRP'
_AIMERS_LAYER  = '_aimers'     # display layer wrapping the aimer grp
# The radius at which a `scale` argument applies verbatim — the house
# skeleton's body-joint radius. Aimer size scales linearly with joint
# radius from there (Adrian 2026-07-05, matching the Armature ctrls):
# radius-3 bodies keep the familiar scale-10 aimer, radius-1 fingers
# get one a third the size.
_RADIUS_REF    = 3.0


def _add_to_layer(layer: str, members) -> None:
    """Additive display-layer membership — plain layer (visible,
    selectable): a visibility toggle for the user, not a lock."""
    if not cmds.objExists(layer):
        cmds.createDisplayLayer(name=layer, empty=True)
    cmds.editDisplayLayerMembers(layer, members, noRecurse=True)
_AIMER_SUFFIX  = '_JntOrient'
_OFFSET_SUFFIX = '_JntOrient_offset'

# Raw CV data calibrated for scale=10. Multiply by (scale/10) at creation time.
_K = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]

_X_PTS_RAW = [
    (0,       0,        0),
    (5.61e-16, 0,       0),
    (5.61e-16, 0,      -0.3199),
    (4.8052,  0,        0),
    (5.61e-16, 0,       0.3199),
    (5.61e-16, 0,       0),
    (5.61e-16,-0.3199,  0),
    (4.8052,  0,        0),
    (5.61e-16, 0.3199,  0),
    (5.61e-16, 0,       0),
]
_Y_PTS_RAW = [
    (0,        0,       0),
    (0,       -5.61e-16, 0),
    (-0.3199, -5.61e-16, 0),
    (0,        4.8359,  0),
    (0.3199,  -5.61e-16, 0),
    (0,       -5.61e-16, 0),
    (0,       -5.61e-16,-0.3199),
    (0,        4.8359,  0),
    (0,       -5.61e-16, 0.3199),
    (0,       -5.61e-16, 0),
]
_Z_PTS_RAW = [
    (-0.0081, -0.0081, 0),
    (-0.0081, -0.0081, 0),
    (-0.3280, -0.0081, 0),
    (-0.0081, -0.0081, 4.8359),
    ( 0.3118, -0.0081, 0),
    (-0.0081, -0.0081, 0),
    (-0.0081, -0.3280, 0),
    (-0.0081, -0.0081, 4.8359),
    (-0.0081,  0.3118, 0),
    (-0.0081, -0.0081, 0),
]


# ─────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────

class undo_chunk:
    def __init__(self, name='JointOrient'):
        self.name = name

    def __enter__(self):
        cmds.undoInfo(openChunk=True, chunkName=self.name)

    def __exit__(self, *_):
        cmds.undoInfo(closeChunk=True)


# ─────────────────────────────────────────────
# Name helpers
# ─────────────────────────────────────────────

def _short(jnt: str) -> str:
    return jnt.split('|')[-1].split(':')[-1]


def aimer_name(jnt: str) -> str:
    """Return the aimer curve name for a joint, stripping DAG path and namespace."""
    return f'{_short(jnt)}{_AIMER_SUFFIX}'


def offset_name(jnt: str) -> str:
    return f'{_short(jnt)}{_OFFSET_SUFFIX}'


def aimer_exists(jnt: str) -> bool:
    return cmds.objExists(aimer_name(jnt))


# ─────────────────────────────────────────────
# Private build helpers
# ─────────────────────────────────────────────

def _get_direct_children(jnt: str) -> list:
    return cmds.listRelatives(jnt, children=True, type='joint') or []


def _build_enum_string(children: list, has_parent: bool = False) -> str:
    base = ':'.join(children) + ':' if children else ''
    tail = 'Local:World'
    if has_parent:
        # Parent goes LAST so it never shifts the Local/World indices existing
        # aimers were built with (Adrian, 2026-07-13). A joint can aim back up
        # the chain at its parent — and a twist joint, which sits on the
        # parent->segment-end line, uses Parent + a 180 RZ to point at the end.
        tail += ':Parent'
    return f'{base}{tail}'


def _scale_pts(raw: list, s: float) -> list:
    return [(x * s, y * s, z * s) for x, y, z in raw]


def _create_xyz_curve(name: str, scale: float) -> str:
    """Build a three-arrow XYZ NURBS curve. Colors set after shape reparenting."""
    s = scale / 10.0
    x_tfm = cmds.curve(name=name,   p=_scale_pts(_X_PTS_RAW, s), d=1, k=_K)
    y_tfm = cmds.curve(name=name + '_Y_tmp', p=_scale_pts(_Y_PTS_RAW, s), d=1, k=_K)
    z_tfm = cmds.curve(name=name + '_Z_tmp', p=_scale_pts(_Z_PTS_RAW, s), d=1, k=_K)

    y_shape = cmds.listRelatives(y_tfm, shapes=True)[0]
    z_shape = cmds.listRelatives(z_tfm, shapes=True)[0]

    cmds.parent(y_shape, x_tfm, add=True, shape=True)
    cmds.parent(z_shape, x_tfm, add=True, shape=True)
    cmds.delete(y_tfm, z_tfm)

    shapes = cmds.listRelatives(x_tfm, shapes=True)  # [X, Y, Z] after reparent
    for shp, color in zip(shapes, [13, 14, 6]):
        cmds.setAttr(f'{shp}.overrideEnabled', 1)
        cmds.setAttr(f'{shp}.overrideColor', color)
        _xray_shape(shp)

    return x_tfm


# ─────────────────────────────────────────────
# X-ray (Adrian, 2026-07-13)
# ─────────────────────────────────────────────
#
# The Armature ctrls became x-ray balls that draw THROUGH the character
# mesh — and promptly drew through the aimers too, burying them. The
# aimers have to win the same way, or the thing that fixed one problem
# creates another.
#
# Maya exposes alwaysDrawOnTop on `mesh` and `nurbsCurve` (and NOT on
# `nurbsSurface` or `locator`). The aimer arms are nurbsCurves, so they
# qualify. The localRef/worldRef LOCATORS do not, and cannot — nothing
# native reaches a locator. They are secondary reference geometry, not
# something the rigger drags, so that is a cost worth paying rather than
# a reason to rebuild them as curves.
#
# alwaysDrawOnTop is NOT SAVED to the file (measured 2026-07-13: Maya
# writes .bck/.csh/.rcsh but never .adot). It must be re-armed on every
# scene open or the aimers come back buried. Hence reapply_xray().


def _xray_shape(shape: str) -> None:
    try:
        cmds.setAttr(f'{shape}.alwaysDrawOnTop', 1)
    except Exception:
        pass    # a display flag must never break aimer creation


def aimer_shapes() -> list:
    """Every aimer's curve shapes, or [] when there are no aimers."""
    if not cmds.objExists(_AIMER_GRP):
        return []
    return cmds.listRelatives(_AIMER_GRP, allDescendents=True,
                              type='nurbsCurve', fullPath=True) or []


def reapply_xray() -> int:
    """Re-arm alwaysDrawOnTop on every aimer curve. Returns the count.

    Maya does not serialize the flag, so a saved scene reopens with the
    aimers buried under the Armature's x-ray balls."""
    n = 0
    for shp in aimer_shapes():
        try:
            cmds.setAttr(f'{shp}.alwaysDrawOnTop', 1)
            n += 1
        except Exception:
            pass
    return n


_XRAY_HOOK_INSTALLED = False


def _on_scene_opened(*_) -> None:
    reapply_xray()


def install_xray_scene_hook() -> None:
    """Re-arm the aimer x-ray after a scene load. Module-global, never
    removed — a cheap no-op when no aimers are present."""
    global _XRAY_HOOK_INSTALLED
    if _XRAY_HOOK_INSTALLED:
        return
    try:
        om.MSceneMessage.addCallback(om.MSceneMessage.kAfterOpen,
                                     _on_scene_opened)
        om.MSceneMessage.addCallback(om.MSceneMessage.kAfterImport,
                                     _on_scene_opened)
        _XRAY_HOOK_INSTALLED = True
    except Exception:
        pass



def _build_local_ref(jnt: str, scale: float) -> str:
    """Create a locator parented under the joint, offset down the
    convention aim axis in the joint's LOCAL space — aiming at it
    reproduces the joint's exact current aim."""
    loc = cmds.spaceLocator(name=f'{_short(jnt)}_localRef_LOC')[0]
    cmds.parent(loc, jnt)
    ax, ay, az = CONVENTION.aim_vector
    cmds.setAttr(f'{loc}.translate',
                 ax * scale, ay * scale, az * scale, type='double3')
    cmds.setAttr(f'{loc}.rotate',    0.0, 0.0, 0.0, type='double3')
    cmds.setAttr(f'{loc}.visibility', 0)
    for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz'):
        cmds.setAttr(f'{loc}.{attr}', lock=True)
    return loc


def _build_world_ref(jnt: str, scale: float) -> tuple:
    """World-orientation reference (Adrian's spec 2026-07-04): a null
    pointConstrained to the joint with rotation pinned at 0,0,0 and
    transforms never frozen, plus a child ref locator offset down the
    convention aim axis — in WORLD axes, since the null never rotates.
    Aiming at the ref with up sourced from the null yields exact world
    identity even while the joint is dragged around.

    Returns:
        (world_null, world_ref)
    """
    s    = _short(jnt)
    null = cmds.createNode('transform',
                           name=f'{s}{_AIMER_SUFFIX}_worldNull')
    cmds.parent(null, _AIMER_GRP)
    cmds.pointConstraint(jnt, null, mo=False)  # before locking anything

    ref = cmds.spaceLocator(name=f'{s}_worldRef_LOC')[0]
    cmds.parent(ref, null)
    ax, ay, az = CONVENTION.aim_vector
    cmds.setAttr(f'{ref}.translate',
                 ax * scale, ay * scale, az * scale, type='double3')
    cmds.setAttr(f'{ref}.rotate', 0.0, 0.0, 0.0, type='double3')
    cmds.setAttr(f'{ref}.visibility', 0)
    for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz'):
        cmds.setAttr(f'{ref}.{attr}', lock=True)

    # Rotation IS the point of this null — pin it. Translate stays
    # driven by the pointConstraint (locked after wiring would sever
    # nothing, but manual edits are blocked via rotate/scale only).
    for attr in ('rx', 'ry', 'rz', 'sx', 'sy', 'sz'):
        cmds.setAttr(f'{null}.{attr}', lock=True)
    return null, ref


def _wire_aim_rig(aimer: str, offset: str, jnt: str, children: list,
                  local_ref: str, world_ref: str,
                  world_null: str, parent_target: str = None) -> None:
    """Build multi-target aimConstraint + condition node network for enum dispatch.

    aimConstraint is applied to the offset node so the aimer ctrl stays zeroed.
    The enum attribute lives on the aimer ctrl (user-facing node).

    Up handling: child/Local modes take up from the joint's rotation
    (objectrotation) — unchanged legacy behavior. World mode swaps the
    constraint's worldUpMatrix to the never-rotating worldNull via a
    choice node keyed off the World condition, so World lands on exact
    world identity instead of inheriting the joint's roll.

    parent_target: when the joint has a parent joint, it is added as the LAST
    target (the enum's 'Parent'), so the aimer can aim back up the chain. Kept
    after Local/World so those indices, and the world-mode up swap keyed off
    world_idx, are unchanged.
    """
    all_targets = children + [local_ref, world_ref]
    if parent_target:
        all_targets = all_targets + [parent_target]
    local_idx   = len(children)
    world_idx   = len(children) + 1

    ac_result = cmds.aimConstraint(
        *all_targets, offset,
        aimVector=list(CONVENTION.aim_vector),
        upVector=list(CONVENTION.up_vector),
        worldUpType='objectrotation',
        worldUpObject=jnt,
        worldUpVector=list(CONVENTION.up_vector),
        mo=False,
    )
    ac_node = ac_result[0]

    for i, _target in enumerate(all_targets):
        cond = cmds.createNode('condition', name=f'{aimer}_cond{i}')
        cmds.setAttr(f'{cond}.operation',    0)      # Equal
        cmds.setAttr(f'{cond}.secondTerm',   float(i))
        cmds.setAttr(f'{cond}.colorIfTrueR', 1.0)
        cmds.setAttr(f'{cond}.colorIfFalseR', 0.0)
        cmds.connectAttr(f'{aimer}.aimTarget', f'{cond}.firstTerm')
        cmds.connectAttr(f'{cond}.outColorR',  f'{ac_node}.target[{i}].targetWeight', force=True)

    # World-mode up swap: input[0] = joint (legacy), input[1] = the
    # never-rotating null. The World condition's 0/1 output doubles as
    # the choice selector.
    choice = cmds.createNode('choice', name=f'{aimer}_upchoice')
    cmds.connectAttr(f'{jnt}.worldMatrix[0]',        f'{choice}.input[0]')
    cmds.connectAttr(f'{world_null}.worldMatrix[0]', f'{choice}.input[1]')
    cmds.connectAttr(f'{aimer}_cond{world_idx}.outColorR',
                     f'{choice}.selector')
    cmds.connectAttr(f'{choice}.output', f'{ac_node}.worldUpMatrix',
                     force=True)

    cmds.setAttr(f'{aimer}.aimTarget', local_idx)


def _collect_hierarchy(root: str) -> list:
    """BFS from root, returns parents-before-children joint list."""
    result = []
    queue  = [root]
    while queue:
        jnt = queue.pop(0)
        result.append(jnt)
        queue.extend(_get_direct_children(jnt))
    return result


# ─────────────────────────────────────────────
# Public API — create / delete
# ─────────────────────────────────────────────

def _create_aimer_impl(jnt: str, scale: float = 10.0) -> str:
    if not cmds.objExists(jnt):
        raise RuntimeError(f"Joint '{jnt}' does not exist.")
    if cmds.nodeType(jnt) != 'joint':
        raise RuntimeError(f"'{jnt}' is not a joint node.")
    if aimer_exists(jnt):
        raise RuntimeError(f"Aimer already exists for '{jnt}'.")

    # Radius-proportional sizing (see _RADIUS_REF). Adjusted once
    # here — the arrow curve, local ref and world ref all consume the
    # same local `scale`, so the whole aimer stays coherent.
    try:
        radius = max(cmds.getAttr(f'{jnt}.radius'), 1e-3)
    except Exception:
        radius = _RADIUS_REF
    scale = scale * radius / _RADIUS_REF

    if not cmds.objExists(_AIMER_GRP):
        cmds.createNode('transform', name=_AIMER_GRP)
    # '_aimers' display layer — a visibility toggle for the whole
    # aimer system (plain layer: visible + selectable). Group-level
    # membership covers every aimer; re-asserted per create so a
    # user-deleted layer comes back.
    _add_to_layer(_AIMERS_LAYER, _AIMER_GRP)
    # Maya never saves alwaysDrawOnTop — re-arm it on every scene open
    # or the aimers reopen buried under the Armature's x-ray balls.
    install_xray_scene_hook()

    children = _get_direct_children(jnt)
    parent_joint = (cmds.listRelatives(jnt, parent=True, type='joint')
                    or [None])[0]
    name     = aimer_name(jnt)

    # Offset node sits directly under group; receives pointConstraint + aimConstraint.
    offset = cmds.createNode('transform', name=offset_name(jnt))
    cmds.parent(offset, _AIMER_GRP)

    # Aimer curve is zeroed under the offset so the user can rotate it freely.
    aimer = _create_xyz_curve(name, scale)
    cmds.parent(aimer, offset)
    cmds.setAttr(f'{aimer}.translate', 0.0, 0.0, 0.0, type='double3')
    cmds.setAttr(f'{aimer}.rotate',    0.0, 0.0, 0.0, type='double3')

    # Aimers expose rotation + aimTarget only. Translate and scale are
    # locked and hidden: position comes from the pointConstraint on the
    # offset node, and scale is meaningless on an aimer.
    for attr in ('tx', 'ty', 'tz', 'sx', 'sy', 'sz'):
        cmds.setAttr(f'{aimer}.{attr}', lock=True, keyable=False,
                     channelBox=False)

    local_ref = _build_local_ref(jnt, scale)
    world_null, world_ref = _build_world_ref(jnt, scale)

    cmds.pointConstraint(jnt, offset, mo=False)

    enum_str = _build_enum_string(children, has_parent=bool(parent_joint))
    cmds.addAttr(aimer, longName='aimTarget', attributeType='enum',
                 enumName=enum_str, keyable=True)

    _wire_aim_rig(aimer, offset, jnt, children, local_ref,
                  world_ref, world_null, parent_target=parent_joint)

    return aimer


def create_aimer(jnt: str, scale: float = 10.0) -> str:
    """Create a single XYZ aimer for jnt.

    Returns:
        Name of the created aimer curve.
    """
    with undo_chunk('CreateAimer'):
        return _create_aimer_impl(jnt, scale)


def point_aimer_at_parent_flipped(jnt: str) -> bool:
    """Aim jnt's aimer at its PARENT, then flip 180 in RZ so it points the
    OTHER way (Adrian, 2026-07-13).

    For a twist joint — no child of its own, sitting on the parent->segment-end
    line — that lands the aim on the segment end (the elbow): a live, visible
    aim like every other joint's aimer, without threading the end target
    through. Returns False (no-op) if the aimer or a 'Parent' enum target is
    absent (e.g. a childless root)."""
    aimer = aimer_name(jnt)
    if not cmds.objExists(aimer):
        return False
    try:
        enum = (cmds.attributeQuery('aimTarget', node=aimer, listEnum=True)
                or [''])[0].split(':')
    except Exception:
        return False
    if 'Parent' not in enum:
        return False
    cmds.setAttr(f'{aimer}.aimTarget', enum.index('Parent'))
    cmds.setAttr(f'{aimer}.rotateZ', 180.0)
    return True


def create_aimers(joints: list, scale: float = 10.0) -> list:
    """Create aimers for a list of joints.

    Returns:
        List of created aimer names.
    """
    with undo_chunk('CreateAimers'):
        return [_create_aimer_impl(j, scale) for j in joints]


def create_aimers_from_root(root: str, scale: float = 10.0) -> list:
    """Create aimers for root and all descendant joints.

    Returns:
        List of created aimer names.
    """
    with undo_chunk('CreateAimers'):
        return [_create_aimer_impl(j, scale) for j in _collect_hierarchy(root)]


def delete_aimer(jnt: str) -> None:
    """Delete every fragment of a joint's aimer. Robust to PARTIAL
    states: a hand-deleted aimer curve leaves its offset, localRef,
    worldNull, and DG nodes behind — each piece below is deleted
    independently when present, so stale fragments never survive to
    collide with a fresh create_aimer."""
    s = _short(jnt)
    with undo_chunk('DeleteAimer'):
        # localRef is parented under the joint.
        loc = f'{s}_localRef_LOC'
        if cmds.objExists(loc):
            cmds.delete(loc)
        # worldNull is a sibling of the offset under the group; deleting
        # it cascades the worldRef locator and its pointConstraint.
        null = f'{s}{_AIMER_SUFFIX}_worldNull'
        if cmds.objExists(null):
            cmds.delete(null)
        # Condition + choice nodes are loose DG nodes.
        for cond in cmds.ls(f'{s}{_AIMER_SUFFIX}_cond*', type='condition') or []:
            if cmds.objExists(cond):
                cmds.delete(cond)
        chc = f'{s}{_AIMER_SUFFIX}_upchoice'
        if cmds.objExists(chc):
            cmds.delete(chc)
        # Deleting the offset also deletes the aimer curve (its child).
        off = offset_name(jnt)
        if cmds.objExists(off):
            cmds.delete(off)


def get_all_aimer_joints() -> list:
    """Return joint names inferred from all aimers currently in the scene."""
    if not cmds.objExists(_AIMER_GRP):
        return []
    children   = cmds.listRelatives(_AIMER_GRP, children=True, type='transform') or []
    suffix_len = len(_OFFSET_SUFFIX)
    return [c[:-suffix_len] for c in children if c.endswith(_OFFSET_SUFFIX)]


def delete_all_aimers() -> None:
    """Delete the entire JntOrient_GRP (aimers, worldNulls, DG nodes)
    and all localRef_LOCs."""
    with undo_chunk('DeleteAllAimers'):
        for loc in cmds.ls('*_localRef_LOC') or []:
            if cmds.objExists(loc):
                cmds.delete(loc)
        for cond in cmds.ls(f'*{_AIMER_SUFFIX}_cond*', type='condition') or []:
            if cmds.objExists(cond):
                cmds.delete(cond)
        for chc in cmds.ls(f'*{_AIMER_SUFFIX}_upchoice', type='choice') or []:
            if cmds.objExists(chc):
                cmds.delete(chc)
        # Group deletion cascades offsets, aimer curves, and worldNulls.
        if cmds.objExists(_AIMER_GRP):
            cmds.delete(_AIMER_GRP)


# ─────────────────────────────────────────────
# Public API — mirror
# ─────────────────────────────────────────────

def mirror_aimers(aimers: list = None) -> dict:
    """Mirror each given aimer onto its opposite-side counterpart.

    For each aimer:
      1. Strip the _JntOrient suffix to recover the joint short name.
      2. Resolve the mirror joint name via side_tokens.flip_side_token.
      3. Find the aimer on the mirror joint.
      4. Copy aimTarget enum INDEX (positional, not name-mapped — the
         child enum slots are symmetric between L/R chains).
      5. Map aimer.rotate through the active convention's measured
         general mirror map (orientation_convention._MIRROR_TERMS,
         probes 6-7): all three channels, per frame class. Reversing
         frames (a child target, or Parent) take the convention flip;
         Local does NOT — its frame is the joint's own already-mirrored
         orientation, the flip is spurious there, and the orientation
         bake would write it into the joint and un-flip every childless
         tip (2026-07-19). The convention resolves from the Fabricator
         registry stamp when a rig is present (Standard rigs mirror
         Standard), else house.

    Skips with a warning when:
      - source joint is center (md, no counterpart)
      - source joint name has no side token
      - mirror joint doesn't exist
      - mirror joint has no aimer

    Args:
      aimers: list of aimer ctrl names. None reads the live Maya selection
        and filters to aimer ctrls (names ending in _JntOrient).

    Returns:
      {'mirrored': int, 'skipped': int, 'reasons': list[str]}

    Raises:
      RuntimeError: nothing aimer-like in the resolved list.
    """
    if aimers is None:
        selection = cmds.ls(selection=True) or []
        aimers = [s for s in selection
                  if s.endswith(_AIMER_SUFFIX) and cmds.objExists(s)]
    if not aimers:
        raise RuntimeError(
            'mirror_aimers: no aimer in selection. Select one or more aimer '
            'ctrls (the XYZ arrow curves under JntOrient_GRP).'
        )

    mirrored = 0
    skipped = 0
    reasons: list = []

    with undo_chunk('MirrorAimers'):
        for src_aimer in aimers:
            src_jnt_short = src_aimer[:-len(_AIMER_SUFFIX)]

            side = side_tokens.detect_side(src_jnt_short)
            if side == 'md':
                skipped += 1
                reasons.append(f'{src_aimer}: center joint has no counterpart')
                continue

            dst_jnt_short = side_tokens.flip_side_token(src_jnt_short)
            if not dst_jnt_short:
                skipped += 1
                reasons.append(f'{src_aimer}: no side token in joint name')
                continue

            if not cmds.ls(dst_jnt_short, type='joint'):
                skipped += 1
                reasons.append(f'{src_aimer}: mirror joint {dst_jnt_short!r} not found')
                continue

            dst_aimer = f'{dst_jnt_short}{_AIMER_SUFFIX}'
            if not cmds.objExists(dst_aimer):
                skipped += 1
                reasons.append(f'{src_aimer}: no aimer on {dst_jnt_short!r}')
                continue

            try:
                src_idx = cmds.getAttr(f'{src_aimer}.aimTarget')
                src_rx  = cmds.getAttr(f'{src_aimer}.rotateX')
                src_ry  = cmds.getAttr(f'{src_aimer}.rotateY')
                src_rz  = cmds.getAttr(f'{src_aimer}.rotateZ')

                src_enum = (cmds.attributeQuery(
                    'aimTarget', node=src_aimer, listEnum=True)
                    or [''])[0].split(':')
                label = (src_enum[src_idx] if src_idx < len(src_enum)
                         else '')

                # Local's frame IS the joint's own orientation, already
                # behavior-mirrored, so nothing reverses and the flip is
                # spurious there. It would not even stay in the channel:
                # the orientation bake writes the aimer's world
                # orientation into the joint, so the 180 lands there and
                # un-flips every childless tip (2026-07-19). The rule
                # itself lives in the convention, never here.
                #
                # All THREE mapped channels are written: the measured
                # general maps move rotateX too (the old code discarded
                # the mapped rx and copied the source's — correct only
                # under the pre-#18 map, which happened to pass rx
                # through).
                conv = _resolve_convention()
                dst_rx, dst_ry, dst_rz = conv.mirror_aimer_rotation(
                    src_rx, src_ry, src_rz,
                    frame_reverses=(label != 'Local'))

                cmds.setAttr(f'{dst_aimer}.aimTarget', src_idx)
                cmds.setAttr(f'{dst_aimer}.rotateX', dst_rx)
                cmds.setAttr(f'{dst_aimer}.rotateY', dst_ry)
                cmds.setAttr(f'{dst_aimer}.rotateZ', dst_rz)
                mirrored += 1
            except Exception as e:
                skipped += 1
                reasons.append(f'{src_aimer} -> {dst_aimer}: {e}')

    return {'mirrored': mirrored, 'skipped': skipped, 'reasons': reasons}


# ─────────────────────────────────────────────
# Public API — state persistence
# ─────────────────────────────────────────────

def get_aimer_state(jnt: str) -> dict:
    """Read an aimer's persistable state, or None when jnt has no aimer.

    Returns:
        {'aim_target': <child short name | 'Local' | 'World'>,
         'aim_offset': [rx, ry, rz],
         'world_rotation': [rx, ry, rz]}

    aim_offset is the ctl's LOCAL rotation — only meaningful relative to
    the offset's live aim frame, which itself derives from the joint's
    CURRENT rotation (worldUpType='objectrotation'). world_rotation is the
    ctl's WORLD orientation: frame-independent truth, what lifecycle
    capture/restore must reproduce (2026-07-14: local-only restore after a
    bake re-applied a twist flip on top of its own baked result — every
    build/unbuild cycle rotated twist aimers another 180).
    """
    name = aimer_name(jnt)
    if not cmds.objExists(name):
        return None
    enum_names = (cmds.attributeQuery('aimTarget', node=name,
                                      listEnum=True) or [''])[0].split(':')
    idx = cmds.getAttr(f'{name}.aimTarget')
    label = enum_names[idx] if idx < len(enum_names) else 'Local'
    return {
        'aim_target': label,
        'aim_offset': [cmds.getAttr(f'{name}.rotateX'),
                       cmds.getAttr(f'{name}.rotateY'),
                       cmds.getAttr(f'{name}.rotateZ')],
        'world_rotation': list(cmds.xform(name, q=True, ws=True,
                                          rotation=True)),
    }


def apply_aimer_state(jnt: str, aim_target: str = '',
                      aim_offset: list = None,
                      world_rotation: list = None) -> bool:
    """Apply persisted state to an existing aimer (spec 2026-07-04 §7).

    aim_target is matched against the enum labels (child short name,
    'Local', 'World'); '' leaves the creation default. A label that no
    longer exists (child renamed/removed) warns and keeps the default
    rather than guessing. Returns True when everything applied cleanly.

    world_rotation, when given, WINS over aim_offset: the ctl is driven to
    that world orientation (local computed against the offset's current
    frame), making the restore independent of whatever the joint's frame
    is now — the lifecycle capture/restore contract. aim_offset stays the
    legacy path AND the right choice when the state is deliberately being
    replayed into a DIFFERENT frame (mirror, branch duplicate, rename).
    """
    name = aimer_name(jnt)
    if not cmds.objExists(name):
        raise RuntimeError(f"No aimer found for '{jnt}'. Create it first.")

    ok = True
    if aim_target:
        enum_names = (cmds.attributeQuery('aimTarget', node=name,
                                          listEnum=True) or [''])[0].split(':')
        if aim_target in enum_names:
            cmds.setAttr(f'{name}.aimTarget',
                         enum_names.index(aim_target))
        else:
            cmds.warning(
                f'apply_aimer_state: {name} has no target '
                f'{aim_target!r} (children changed?) — keeping default.')
            ok = False
    if world_rotation is not None:
        cmds.xform(name, ws=True, rotation=world_rotation)
    elif aim_offset is not None:
        cmds.setAttr(f'{name}.rotate',
                     aim_offset[0], aim_offset[1], aim_offset[2],
                     type='double3')
    return ok


# ─────────────────────────────────────────────
# Public API — classification
# ─────────────────────────────────────────────

def classify_aimer(jnt: str, tol: float = 1e-4) -> dict:
    """Classify an aimer's state for Armature topology decisions
    (spec 2026-07-04 §6, simplified per Adrian 2026-07-04: the enum IS
    the intent).

    aim_child: aimTarget is a child slot — regardless of offsets.
    Twist rides on the aim axis, and mirrored sides legitimately carry
    ±180 on a non-twist axis while still pointing at the child; the
    offsets decorate the aim, they don't cancel it. Local/World are
    the built-in opt-outs when the user does NOT want child aiming —
    those classify authored and their orientation is preserved.

    Geometry consumers (translate snap, stretch) apply their own
    on-axis gates — classification carries intent, not mechanics.

    Returns:
        {
          'mode':         'aim_child' | 'authored',
          'target_index': int,          # aimTarget enum value
          'target_label': str,          # enum label (child name/'Local'/'World')
          'target_child': str | None,   # child joint when a child slot
          'twist':        float,        # rotation about the aim axis (deg)
          'offsets':      {axis: deg},  # the two non-twist rotations
        }

    Raises:
        RuntimeError: no aimer exists for jnt.
    """
    name = aimer_name(jnt)
    if not cmds.objExists(name):
        raise RuntimeError(f"No aimer found for '{jnt}'.")

    enum_names = (cmds.attributeQuery('aimTarget', node=name,
                                      listEnum=True) or [''])[0].split(':')
    idx    = cmds.getAttr(f'{name}.aimTarget')
    label  = enum_names[idx] if idx < len(enum_names) else ''
    n_kids = len(enum_names) - 2          # last two slots: Local, World

    rot = {a: cmds.getAttr(f'{name}.rotate{a.upper()}')
           for a in ('x', 'y', 'z')}
    twist_axis = CONVENTION.twist_axis
    offsets = {a: rot[a] for a in CONVENTION.offset_axes}

    is_child = idx < n_kids
    mode = 'aim_child' if is_child else 'authored'

    return {
        'mode':         mode,
        'target_index': idx,
        'target_label': label,
        'target_child': label if is_child else None,
        'twist':        rot[twist_axis],
        'offsets':      offsets,
    }


def detect_aim_child(jnt: str, tol_deg: float = 0.75) -> str:
    """Return the child joint the jnt's aim axis ALREADY points at —
    along EITHER polarity of the axis (within tol_deg) — else ''.

    Polarity matters to the seeder (a −axis match needs the flip
    captured into the aimer offset); use _detect_aim_child_signed /
    seed_aimer_from_detection for that. This plain form keeps the
    detect-never-fabricate contract for callers that only need the
    candidate child.
    """
    return _detect_aim_child_signed(jnt, tol_deg)[0]


def _detect_aim_child_signed(jnt: str,
                             tol_deg: float = 0.75) -> tuple:
    """(child, anti): the child sitting on the jnt's ±aim axis within
    tol_deg — anti=True when it sits on the NEGATIVE axis (mirrored
    sides, e.g. a UE skeleton's right arm aims −X down the bone).
    ('', False) when no child aligns.
    """
    children = _get_direct_children(jnt)
    if not children:
        return '', False
    jm = om.MMatrix(cmds.xform(jnt, q=True, ws=True, matrix=True))
    aim_world = (om.MVector(*CONVENTION.aim_vector) * jm).normalize()
    jp = om.MVector(*cmds.xform(jnt, q=True, ws=True, t=True))

    best, anti = '', False
    best_dot = math.cos(math.radians(tol_deg))
    for child in children:
        cp = om.MVector(*cmds.xform(child, q=True, ws=True, t=True))
        d = cp - jp
        if d.length() < 1e-5:
            continue  # co-located joint — no direction to compare
        dot = d.normal() * aim_world
        if abs(dot) >= best_dot:
            best, best_dot, anti = child, abs(dot), dot < 0.0
    return best, anti


def seed_aimer_from_detection(jnt: str) -> str:
    """Seed a FRESH aimer's target from geometry — detect, never
    fabricate (spec §5; anti-parallel extension, Adrian 2026-07-04).

    Child on the +aim axis → plain child target (bake changes
    nothing). Child on the −aim axis (mirrored sides) → child target
    WITH the flip captured into the aimer offset: the exact rotation
    delta between the pure-aim frame and the joint's current frame,
    snapped to clean 0/±180 components — reads as the RY/RZ 180 the
    rigger expects, and the bake preserves orientation exactly. No
    geometric match → the aimer stays Local (authored orientation is
    never rewritten).

    The aimer must already exist. Returns the seeded child ('' when
    none matched).
    """
    child, anti = _detect_aim_child_signed(jnt)
    if not child:
        return ''
    apply_aimer_state(jnt, aim_target=child)
    if not anti:
        return child

    # Flip capture. The aimer curve sits under the offset node and the
    # bake orients the joint to the aimer's WORLD frame, so (row-vector
    # convention) aimer_local = joint_rot · offset_rot⁻¹. The offset is
    # read AFTER the target applies — the aimConstraint has solved the
    # +aim-at-child frame by then.
    off_wm = om.MMatrix(cmds.xform(offset_name(jnt), q=True, ws=True,
                                   matrix=True))
    jnt_wm = om.MMatrix(cmds.xform(jnt, q=True, ws=True, matrix=True))
    q_off = om.MTransformationMatrix(off_wm).rotation(asQuaternion=True)
    q_jnt = om.MTransformationMatrix(jnt_wm).rotation(asQuaternion=True)
    eul = (q_jnt * q_off.inverse()).asEulerRotation()
    eul = eul.reorder(cmds.getAttr(f'{aimer_name(jnt)}.rotateOrder'))
    offset = [_snap_180(math.degrees(v))
              for v in (eul.x, eul.y, eul.z)]
    apply_aimer_state(jnt, aim_target=child, aim_offset=offset)
    return child


def _snap_180(deg: float, tol: float = 2.0) -> float:
    """Snap to the nearest multiple of 180 when within tol. Detection
    guarantees the true delta is a clean flip up to sub-degree noise —
    and a channel reading 180.0 beats 179.98."""
    nearest = round(deg / 180.0) * 180.0
    return nearest if abs(deg - nearest) <= tol else deg


def rebuild_aimer(jnt: str, scale: float = 10.0) -> str:
    """Delete + re-create a joint's aimer, preserving its state. Needed
    when the joint's CHILD LIST changes (limb drop, joint insertion) —
    the aimTarget enum is baked at creation and goes stale otherwise.
    State is re-applied by label, so an existing child target survives
    the enum rebuild."""
    state = get_aimer_state(jnt)
    delete_aimer(jnt)
    aimer = create_aimer(jnt, scale=scale)
    if state:
        apply_aimer_state(jnt, aim_target=state['aim_target'],
                          aim_offset=state['aim_offset'])
    return aimer


def collapse_joint_orient(joints: list) -> None:
    """Fold each joint's jointOrient + rotateAxis into its rotate
    channels — the export-contract normalizer (rotate carries orient,
    jointOrient=0, rotateAxis=0). The joint's local matrix (and
    therefore its world transform) is preserved exactly.

    Needed because Maya's own joint reparenting compensates through
    jointOrient (cmds.parent rebases parent rotation into JO —
    COMPONENT_AUTHORING Gotcha 2), so any flow that reparents joints,
    including the orient bake itself, reintroduces JO. Per-joint
    independent: rewriting a joint's channels preserves its local
    matrix, so children are unaffected regardless of order."""
    for jnt in joints:
        if not cmds.objExists(jnt):
            continue
        m = om.MMatrix(cmds.xform(jnt, q=True, objectSpace=True,
                                  matrix=True))
        ro = cmds.getAttr(f'{jnt}.rotateOrder')
        rot = om.MTransformationMatrix(m).rotation().reorder(ro)
        cmds.setAttr(f'{jnt}.jointOrient', 0.0, 0.0, 0.0)
        cmds.setAttr(f'{jnt}.rotateAxis', 0.0, 0.0, 0.0)
        cmds.setAttr(f'{jnt}.rotate',
                     math.degrees(rot.x),
                     math.degrees(rot.y),
                     math.degrees(rot.z))


# ─────────────────────────────────────────────
# Public API — orient
# ─────────────────────────────────────────────

def _orient_joint_impl(jnt: str) -> None:
    name = aimer_name(jnt)
    if not cmds.objExists(name):
        raise RuntimeError(f"No aimer found for '{jnt}'. Create aimers first.")

    aimer_mat = om.MMatrix(cmds.xform(name, q=True, ws=True, matrix=True))
    par_jnts  = cmds.listRelatives(jnt, parent=True, type='joint') or []

    if par_jnts:
        par_mat   = om.MMatrix(cmds.xform(par_jnts[0], q=True, ws=True, matrix=True))
        local_mat = aimer_mat * par_mat.inverse()
    else:
        local_mat = aimer_mat

    ro        = cmds.getAttr(f'{jnt}.rotateOrder')
    local_rot = om.MTransformationMatrix(local_mat).rotation()
    local_rot = local_rot.reorder(ro)  # API 2.0 returns a new object

    # Export contract: rotate carries orientation; jointOrient AND
    # rotateAxis are zeroed (a joint carrying both a jointOrient and a
    # rotateAxis silently world-aligns on UE/Unity import).
    cmds.setAttr(f'{jnt}.jointOrient', 0.0, 0.0, 0.0)
    cmds.setAttr(f'{jnt}.rotateAxis', 0.0, 0.0, 0.0)
    cmds.setAttr(f'{jnt}.rotate',
                 math.degrees(local_rot.x),
                 math.degrees(local_rot.y),
                 math.degrees(local_rot.z))


def orient_joint(jnt: str) -> None:
    """Apply aimer rotation to jnt as rotate values. jointOrient is zeroed."""
    with undo_chunk('OrientJoint'):
        _orient_joint_impl(jnt)


def orient_joints(joints: list) -> None:
    """Orient a list of joints. Caller must pass a parent-before-child ordered list."""
    with undo_chunk('OrientJoints'):
        for jnt in joints:
            _orient_joint_impl(jnt)


def orient_joints_from_root(root: str) -> None:
    """Orient all joints in the hierarchy under root (BFS order)."""
    with undo_chunk('OrientJoints'):
        for jnt in _collect_hierarchy(root):
            _orient_joint_impl(jnt)


def orient_all_aimers(force: bool = False) -> list:
    """Orient every joint that has an aimer.

    Unparents all joints first so that orienting one joint can't shift another,
    then orients each, reparents, and collapses the reparent-reintroduced
    jointOrient back into rotate (export contract).

    Args:
        force: a viewport-deleted aimer curve leaves its offset node
            behind, which would abort the bake mid-flight. force=False
            (default) refuses up front with a readable list of the
            affected joints; force=True skips them — they keep their
            current orientation and their stale nodes are cleaned up
            with the rest.

    Returns:
        Ordered list of joint names that were oriented.
    """
    joints = get_all_aimer_joints()
    if not joints:
        cmds.warning('No aimers in scene.')
        return []

    missing = [j for j in joints if not cmds.objExists(aimer_name(j))]
    if missing:
        if not force:
            raise RuntimeError(
                f'{len(missing)} joint(s) have no aimer curve (deleted '
                f'in the viewport?): {", ".join(missing)}. Re-create '
                f'them first, or run with force=True to skip them and '
                f'keep their current orientation.')
        cmds.warning(
            f'Orient: skipping {len(missing)} joint(s) with missing '
            f'aimers (orientation kept): {", ".join(missing)}')
        missing_set = set(missing)
        joints = [j for j in joints if j not in missing_set]
        if not joints:
            delete_all_aimers()
            return []

    jnt_set = set(joints)

    # Record each joint's current parent (may be a non-joint transform or None).
    parent_map = {}
    for jnt in joints:
        parents = cmds.listRelatives(jnt, parent=True) or []
        parent_map[jnt] = parents[0] if parents else None

    # Topological sort: roots first so reparenting goes top-down.
    def _depth(j):
        d, cur = 0, j
        while parent_map.get(cur) in jnt_set:
            cur = parent_map[cur]
            d += 1
        return d

    ordered = sorted(joints, key=_depth)

    with undo_chunk('OrientAllAimers'):
        # 1. Delete point/aim constraints from offset nodes so they don't fight
        #    the orient step or interfere with unparenting.
        for jnt in ordered:
            off = offset_name(jnt)
            if cmds.objExists(off):
                for ctype in ('pointConstraint', 'aimConstraint'):
                    cons = cmds.listRelatives(off, children=True, type=ctype) or []
                    if cons:
                        cmds.delete(cons)

        # 2. Unparent all joints — preserves world transform, decouples hierarchy.
        for jnt in ordered:
            if parent_map[jnt] is not None:
                cmds.parent(jnt, world=True)

        # 3. Orient each joint: zero jointOrient + rotateAxis (export
        #    contract — dual orientation corrupts on UE/Unity import),
        #    orient-constrain to aimer, delete constraint.
        for jnt in ordered:
            cmds.setAttr(f'{jnt}.jointOrient', 0.0, 0.0, 0.0)
            cmds.setAttr(f'{jnt}.rotateAxis', 0.0, 0.0, 0.0)
            con = cmds.orientConstraint(aimer_name(jnt), jnt, mo=False)[0]
            cmds.delete(con)

        # 4. Reparent top-down — preserves world transform, but Maya
        #    compensates joint reparenting through JOINTORIENT (Gotcha
        #    2), so JO is non-zero again after this step.
        for jnt in ordered:
            if parent_map[jnt] is not None:
                cmds.parent(jnt, parent_map[jnt])

        # 4.5. Collapse the reintroduced jointOrient back into rotate —
        #    lands the export contract exactly (JO=0, RA=0), world
        #    transforms untouched.
        collapse_joint_orient(ordered)

        # 5. Clean up all aimers.
        delete_all_aimers()

    return ordered
