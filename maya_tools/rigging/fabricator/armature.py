# maya_tools/rigging/fabricator/armature.py
"""Armature — the control-based skeleton-building rig (spec 2026-07-04).

The Armature is what the rigger manipulates during the edit stage:
NOT an animation rig. Every joint gets a translate-only ctrl; every
pure-aim joint→child edge gets a single-chain IK so the parent keeps
aiming at its child while the ctrl is dragged — orientation stays
aligned during placement by construction. Aimers (joint_orient
subsystem) stay alive the whole time and own twist + authored
orientation; they are the source of truth baked at build time.

Topology rules (spec §6, via joint_orient_app.classify_aimer — the
aimTarget enum IS the intent, Adrian 2026-07-04):
  * parent's aim_target = the child → SC-IK edge: ikHandle
    parent→child lives under the child's ctrl; stretch drives the
    child's aim-axis translate from distanceBetween(parent, ctrl) so
    the child lands ON the ctrl. Offsets ride on top — twist, and the
    ±180 non-twist flip mirrored sides carry, still count as aimed.
    Geometry gate: SC-IK + stretch + snap assume the child sits on
    the ±aim axis; a child-targeted edge with a genuine off-axis
    deviation demotes to a translate-only ctrl (placement is sacred).
  * parent authored (aim_target = Local/World), or child is not the
    aim target (branch joints) → translate-only ctrl: a
    pointConstraint drives the child; nothing touches the parent's
    orientation.
  * root joint → the ROOT ctrl (circle-four-arrow, Adrian 2026-07-18):
    a pointConstraint drives it like any placed ctrl, and every child
    ctrl parents under it, so it moves the whole armature as one
    piece. (This reverses the 2026-07-05 rule that gave the root no
    ctrl at all and parented its children's ctrls to the group.)

build_armature() always runs the aimer orientation bake first
(orient joints to aimers, preserving world transforms), then
normalizes pure-aim children onto the aim axis (tx-only, FP-exact),
then constructs ctrls + solvers. Aimer state survives via
capture/restore around the bake.

Ctrl hierarchy mirrors the joint hierarchy: dragging a parent ctrl
brings its subtree along. Ctrls are world-aligned with scale locked;
translate places joints and rotate is free for FK-style rough-in
(rotate the parent to swing its branch, then refine the children) —
final roll always belongs to the aimers.

Every ctrl is an X-RAY BALL (Adrian, 2026-07-13): a low-poly sphere,
flat-shaded, that draws THROUGH the character mesh the way joints do
under X-Ray Joints — so joints can be placed with the skin on. Side
colored (blue left, red right, kind color center); the template guide
lines carry the connection story. Transforms stay world-aligned. The
ROOT is the one exception: a NURBS circle-four-arrow (_make_root_ctrl),
sized well clear of the orbs because it moves the whole armature.

The x-ray is `mesh.alwaysDrawOnTop`, a Viewport 2.0 depth-test
override. TWO measured facts drive the whole design here:

  * Maya exposes alwaysDrawOnTop on `mesh` and on `nurbsCurve` and
    NOT on `nurbsSurface`. That is why the ctrl is a POLYGON ball and
    not the NURBS sphere originally wanted — no material, shader or
    display flag can x-ray a nurbsSurface, because x-ray is not a
    material property at all.
  * alwaysDrawOnTop is NEVER SAVED. Maya writes `.bck`, `.csh` and
    `.rcsh` into the file but not `.adot` — it is a session-only
    display flag. Without re-arming it on scene open the balls come
    back BURIED inside the mesh. See reapply_xray().

The flat unlit surfaceShader is not decoration: with the depth test
off, a lit ball's back faces can win over its front faces. When every
fragment is one flat color there is no visible depth fight to lose,
so the ball reads as a clean silhouette. It is also the cheapest
material in VP2 (no lighting evaluation), and it is SHARED — one per
color, four for the whole Armature, not one per ctrl.

Node naming (all cleanup is name+type driven):
  fab_armature_grp          top group (ctrl tree + ik handles)
  {jnt}_amt_CTL               ctrl
  {jnt}_amtIkh / {jnt}_amtEff ik handle / effector
  {jnt}_amtDist               distanceBetween (stretch)
  {jnt}_amtStretch            multDoubleLinear (stretch sign)
  {jnt}_amt_ptcon             pointConstraint (translate-only edges)
  {jnt}_amtLine               template guide line parent→child ctrl
  {ctrl}_amtLinePos           pointMatrixMult (line CV world driver)

Ctrl colors: left blue / right red (side_tokens); center keeps the
kind color (yellow aiming / cyan placed).

Follow rules (SPEC 2026-07-09 Limbs + Follower Joints §3.1, Task 1.2)
are the POSITIONAL SIBLING of the aimers. A joint carrying a follow
rule (follow_rules.get_follow_rule) gets no ctrl and no SC-IK/
pointConstraint edge at all — build_armature() skips it in the ctrl
loop below and leaves its translate channel UNLOCKED so a live update
can write straight to it. Live evaluation itself lives in the "Follow
rules — live evaluation" section near the bottom of this module: an
attributeChangedCallback per ctrl (armed once, after every ctrl in
this build exists and is positioned) calls follow_rules.evaluate() the
moment any ctrl's translate/rotate changes — same interaction, same
DG-pull-driven immediacy as an SC-IK edge reorienting its parent live,
just via one Python hop instead of a pure DG network, because the
lerp/snap math follow_rules does isn't expressible as a static DG
graph the way a two-target weighted pointConstraint almost is.

DETERMINISTIC ORDER (the module contract, chosen and documented here
per Task 1.2): followers position FIRST, aimers orient AFTER — except
'match' rules, which set BOTH position and orientation and therefore
WIN outright over any aimer for that joint. In practice this is not a
runtime race to referee: it falls out of what each rule kind touches.
  * 'distribute': evaluate() only ever writes translate for these (see
    follow_rules._resolve_orientation_from_rule — it returns None for
    'distribute' by design, "orientation stays the aimers' job"). A
    distribute-ruled joint's rotate is therefore whatever its OWN
    aimer already baked into it at Armature-build time
    (_bake_and_restore_aimers, before this ctrl loop runs) and stays
    LOCKED thereafter — "aimers orient" here means the ALREADY-BAKED
    aimer orientation is left completely alone by the live hook, not
    that it re-solves every tick (a leaf follower has no SC-IK edge of
    its own to keep re-aiming with).
  * 'match': evaluate() writes translate AND rotate straight from the
    target's world matrix, so build_armature() also leaves rotate
    UNLOCKED for these joints — there is no separate "aimer pass" to
    order against; the rule IS the orientation.
This matches the aimer pipeline's own convention (aimTarget enum sets
intent once; SC-IK / bake are just how it's realized) rather than
inventing a new precedence system.
"""
__author__ = "Adrian Melian"

import contextlib
import functools
import math
import traceback

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
from maya_tools.rigging.fabricator import follow_rules as fr
from maya_tools.rigging.fabricator import nodes
from maya_tools.rigging.joint_orient import joint_orient_app as joa
from maya_tools.skinning import skin_connect_app
from maya_tools.utils.maya import orientation_convention as oc
from maya_tools.utils.maya import side_tokens

_GRP = 'fab_armature_grp'
_ARMATURE_LAYER = '_armature'   # display layer wrapping the grp

# Ctrl look (Maya override colors). Every ctrl is the same x-ray ball;
# COLOR carries the only distinction — which is exactly what the old
# '_joint' cube did too (both kinds were the same shape, differing only
# in color), so going to a ball loses no information. The guide lines
# and the aimer tip still carry the aim story.
_COLOR_AIMING = 17   # yellow — aimer targets a child
_COLOR_PLACED = 18   # cyan   — Local/World aim, or leaf

# Side coloring overrides the kind color (Adrian, 2026-07-04): left
# blue, right red — classic rig convention. Center keeps the kind
# color (the tip direction already tells aim vs authored).
_COLOR_LEFT  = 6    # blue
_COLOR_RIGHT = 13   # red

# The ball. 12x8 = 168 triangles, 86 verts — a rounding error next to
# the skinned character it draws through.
_BALL_AXIS   = 12
_BALL_HEIGHT = 8

# The orb renders at a FRACTION of the joint-radius sizing (Adrian, 2026-07-13):
# even proportional, an orb the full size of the joint's display radius is too
# fat for a tight finger chain — the neighbours overlap and you cannot grab the
# one you want. 0.4 gives finger orbs room to breathe. This scales EVERY orb;
# it is folded back out of measure_live_ctrl_scale so the exporter still
# recovers the user's ctrl_scale exactly.
_BALL_RADIUS_FACTOR = 0.4

# The ROOT ctrl (restored 2026-07-18 on Adrian's call, reversing the
# 2026-07-05 no-root-ctrl rule). It is the whole-armature mover, so it
# reads as a different object from the placement orbs: a NURBS
# circle-four-arrow from the Curve-O-Matic library rather than a ball,
# sized well clear of the root's own orb-scale so it is grabbable
# without fighting the joint ctrls sitting inside it. One number to
# tune if it reads too big or small in the viewport.
_ROOT_CTRL_SHAPE = 'circle_four_arrow'
_ROOT_CTRL_RADIUS_FACTOR = 4.0

_SHADER_PREFIX = 'fab_amtCtl'

# Maya's override-color palette is a UI resource: cmds.colorIndex() is
# authoritative in the GUI (it honors a customized palette) but returns
# None in a headless mayapy, where there is no color table to ask. The
# shaders need real RGB, so: ask Maya first, fall back to the stock
# table. Only the four colors the Armature actually uses are listed;
# anything a component picks falls back to bone gray rather than lying.
_INDEX_RGB = {
    _COLOR_LEFT:   (0.000, 0.000, 1.000),   # blue
    _COLOR_RIGHT:  (1.000, 0.000, 0.000),   # red
    _COLOR_AIMING: (1.000, 1.000, 0.000),   # yellow
    _COLOR_PLACED: (0.392, 0.863, 1.000),   # cyan
}
_RGB_FALLBACK = (0.800, 0.800, 0.800)


def _side_color(jnt: str, default: int) -> int:
    side = side_tokens.detect_side(jnt)
    if side == 'lf':
        return _COLOR_LEFT
    if side == 'rt':
        return _COLOR_RIGHT
    return default


# ─────────────────────────────────────────────
# The x-ray ball ctrl
# ─────────────────────────────────────────────

def _index_rgb(index: int) -> tuple:
    """Maya color index → RGB. cmds.colorIndex is authoritative when a
    color table exists (the GUI, including a palette the user has
    customized) and returns None when one does not (headless mayapy)."""
    try:
        rgb = cmds.colorIndex(index, q=True)
    except Exception:
        rgb = None
    if rgb:
        return tuple(rgb)
    return _INDEX_RGB.get(index, _RGB_FALLBACK)


def _flat_shader_for(index: int) -> str:
    """The shading engine for one ctrl color, created on demand and
    SHARED — four colors means four shaders for the whole Armature, not
    one per ctrl. surfaceShader is unlit: VP2 skips lighting evaluation
    entirely, and a constant color is what makes a depth-test-off ball
    read as a clean silhouette instead of a scrambled one."""
    sg = f'{_SHADER_PREFIX}{index}_SG'
    if cmds.objExists(sg):
        return sg
    r, g, b = _index_rgb(index)
    shader = cmds.shadingNode('surfaceShader', asShader=True,
                              name=f'{_SHADER_PREFIX}{index}_SS')
    cmds.setAttr(f'{shader}.outColor', r, g, b, type='double3')
    cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg)
    cmds.connectAttr(f'{shader}.outColor', f'{sg}.surfaceShader',
                     force=True)
    return sg


def _delete_ctrl_shaders() -> None:
    """Drop the shared shaders with the Armature. They are shared, so
    nothing else deletes them when the ctrl meshes go; without this they
    accumulate across every build/unbuild cycle."""
    for node in (cmds.ls(f'{_SHADER_PREFIX}*_SG', type='shadingEngine') or []) \
            + (cmds.ls(f'{_SHADER_PREFIX}*_SS', type='surfaceShader') or []):
        if cmds.objExists(node):
            cmds.delete(node)


def _build_ball(name: str) -> str:
    """A radius-1 ball. Radius 1 is load-bearing: measure_live_ctrl_scale
    divides an existing ctrl's extent by a fresh probe's, so the probe and
    the ctrl must be built by this same function."""
    return cmds.polySphere(name=name, radius=1.0,
                           subdivisionsAxis=_BALL_AXIS,
                           subdivisionsHeight=_BALL_HEIGHT,
                           constructionHistory=False)[0]


def _dress_ball(ctrl: str, color: int) -> None:
    """Make a ball an x-ray ctrl: draw through the mesh, cull the back
    faces, wear its color in BOTH viewport modes.

    overrideColor on a mesh tints the WIREFRAME (not the shaded surface),
    which is what you see when the ctrl is selected and in wireframe mode
    — so it still earns its keep, and the shader carries the shaded body.
    Both are driven off the same index, so they cannot drift apart."""
    for shp in cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []:
        # THE X-RAY. Not saved to the file — reapply_xray() re-arms it.
        cmds.setAttr(f'{shp}.alwaysDrawOnTop', 1)
        # With the depth test off, back faces can win over front faces.
        # Culling them removes the fight outright (and halves the
        # fragments). 3 = Full.
        cmds.setAttr(f'{shp}.backfaceCulling', 3)
        cmds.setAttr(f'{shp}.castsShadows', 0)
        cmds.setAttr(f'{shp}.receiveShadows', 0)
        cmds.setAttr(f'{shp}.overrideEnabled', 1)
        cmds.setAttr(f'{shp}.overrideColor', color)
    cmds.sets(ctrl, edit=True, forceElement=_flat_shader_for(color))


def ctrl_shapes() -> list:
    """Every Armature shape that wants the x-ray, or [] when there is no
    Armature: the ctrl BALLS (mesh) and the guide LINES (nurbsCurve).
    Both are shape types Maya gives alwaysDrawOnTop to; a nurbsSurface or
    a locator could not be included here even if we wanted it."""
    if not cmds.objExists(_GRP):
        return []
    return (cmds.listRelatives(_GRP, allDescendents=True, type='mesh',
                               fullPath=True) or []) \
        + (cmds.listRelatives(_GRP, allDescendents=True, type='nurbsCurve',
                              fullPath=True) or [])


def reapply_xray() -> int:
    """Re-arm alwaysDrawOnTop across the Armature AND the aimers. Returns
    the count.

    MEASURED (2026-07-13): Maya does not serialize alwaysDrawOnTop.
    Dump every setAttr in a saved .ma and `.bck`, `.csh` and `.rcsh` are
    all there — `.adot` is not. It is a session-only viewport flag.

    So a saved Armature reopens with its balls BURIED inside the mesh,
    and nothing in the file records that anything is wrong. This is the
    single sharpest edge on the whole feature: it works perfectly right
    up until you save, and then quietly does not.

    The aimers are included because the x-ray balls draw over THEM too
    (Adrian, 2026-07-13: "now the aimers are hard to see"). Fixing the
    ctrls without fixing the aimers just moves the problem."""
    n = 0
    for shp in ctrl_shapes():
        try:
            cmds.setAttr(f'{shp}.alwaysDrawOnTop', 1)
            n += 1
        except Exception:
            traceback.print_exc()   # never let a display flag break a load
    return n + joa.reapply_xray()


_XRAY_HOOK_INSTALLED = False


def _on_scene_opened(*_) -> None:
    reapply_xray()


def _install_xray_scene_hook() -> None:
    """Re-arm the x-ray after a scene load. Module-global and never
    removed — like the follow-rule hook below, it is a cheap no-op when
    no Armature is present, so leaving it registered across scenes is
    harmless."""
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
        traceback.print_exc()


class undo_chunk:
    def __init__(self, name='Armature'):
        self.name = name

    def __enter__(self):
        cmds.undoInfo(openChunk=True, chunkName=self.name)

    def __exit__(self, *_):
        cmds.undoInfo(closeChunk=True)


def _suspends_armature_watch(fn):
    """Mute the DAG watchers (parent + delete) while Fabricator builds
    or tears down the Armature — both parent AND delete ctrls
    constantly, and the watchers must only react to the USER doing
    that."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            from maya_tools.rigging.fabricator import armature_watch
            ctx = armature_watch.suspended()
        except Exception:
            return fn(*args, **kwargs)
        with ctx:
            return fn(*args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────
# Engagement suspension
# ─────────────────────────────────────────────

# While True, FollowJoint._engage_armature (and any other "rebuild the
# Armature right now" reactor) must no-op. Batch operations that add
# several rule-seeding components in one gesture (Build Engine IK's six
# FollowJoints) set this and run ONE rebuild at the end — six full
# rebuilds mid-mutation is wasted work, and rebuilding against a scene
# whose joints/rules are mid-edit is exactly the state the fingertip-
# collapse bug (2026-07-14) fired in.
_ENGAGE_SUSPENDED = False


class engage_suspended:
    """Context manager: suspend immediate armature re-engagement for a
    batch of component adds. The caller owns running build_armature()
    once afterwards (if an Armature is standing)."""

    def __enter__(self):
        global _ENGAGE_SUSPENDED
        self._prev = _ENGAGE_SUSPENDED
        _ENGAGE_SUSPENDED = True

    def __exit__(self, *_):
        global _ENGAGE_SUSPENDED
        _ENGAGE_SUSPENDED = self._prev


def is_engage_suspended() -> bool:
    return _ENGAGE_SUSPENDED


# ─────────────────────────────────────────────
# Queries
# ─────────────────────────────────────────────

def armature_exists() -> bool:
    return cmds.objExists(_GRP)


def ctrl_for_joint(joint: str) -> str | None:
    """The live Armature ctrl driving `joint`'s placement, or None.

    Every ctrl this module builds is literally named `{joint}_amt_CTL`
    (`_make_ctrl`'s own naming, documented in this module's docstring
    node-naming table). This is the one callable resolver for that
    mapping — added so a caller OUTSIDE this module (e.g. the
    Properties panel's Add Finger success path, selecting a fresh
    joint's ctrl so the rigger can drag it straight into place) has
    something to import instead of re-deriving the `_amt_CTL` suffix
    locally, the way branch_ops.py / armature_mirror.py / canvas_panel.py
    each already do today for their own, unrelated reasons (structural
    checks, mirror DG naming, viewport<->canvas selection sync).

    Returns None whenever `joint` has no ctrl right now: no live
    Armature at all, or `joint` carries a follow rule (position is entirely
    rule-driven; build_armature's ctrl loop skips it — see this
    module's docstring, 'Follow rules' section).
    """
    if not joint:
        return None
    ctrl = f'{joint}_amt_CTL'
    return ctrl if cmds.objExists(ctrl) else None


def measure_live_ctrl_scale() -> float | None:
    """Best-effort recovery of the ctrl_scale the live Armature was
    built with (spec gap flagged 2026-07-08: ctrl_scale is not
    persisted anywhere -- no UI exposes it yet, and build_armature()
    forgets it the moment the call returns). Added for the skeletal
    exporter, whose break/export/rebuild round-trip must not silently
    resize the user's controls back to the 1.0 default.

    _make_ctrl bakes `radius * ctrl_scale * _BALL_RADIUS_FACTOR` into a
    ctrl's SHAPE POINTS via makeIdentity (the transform's own .scale reads
    back 1,1,1 afterwards), so this runs that formula in reverse: measure an
    existing ctrl's point extent, divide out a freshly-built unscaled
    reference ball's extent, the ctrl's own joint's radius, AND the ball
    radius factor. Sampling
    every ctrl and taking the median guards against one ctrl whose shape
    was swapped by hand (curve_o_matic.swap_shape) skewing the result;
    the measurement is distance-from-origin, so a rotated shape does not
    perturb it -- only a rescale would.

    The probe MUST be built by _build_ball, the same function that builds
    the ctrl: this is a ratio, so the two have to share a construction.

    Returns None when the Armature doesn't exist or no ctrl can be
    measured (e.g. a lone root-only skeleton -- the root ctrl is a
    circle-four-arrow on its own radius factor, so it is deliberately
    excluded from the sampling below). Callers should fall back to the
    1.0 default and warn loudly that a custom scale isn't preserved.
    """
    if not armature_exists():
        return None

    ref = _build_ball('_fab_scale_probe')
    try:
        ref_extent = _max_shape_extent(ref)
    finally:
        if cmds.objExists(ref):
            cmds.delete(ref)
    if ref_extent <= 1e-9:
        return None

    samples = []
    for node in cmds.listRelatives(_GRP, allDescendents=True,
                                   type='transform') or []:
        if not node.endswith('_amt_CTL'):
            continue
        jnt = node[:-len('_amt_CTL')]
        if not cmds.objExists(jnt) or cmds.nodeType(jnt) != 'joint':
            continue
        # The root ctrl is a circle-four-arrow on its own radius factor,
        # not an orb (2026-07-18) — measuring it against the ball probe
        # would report a wildly wrong ctrl_scale. Skip it: on a small
        # skeleton it could carry the median outright, and on a lone-root
        # skeleton it would be the only sample.
        if not cmds.listRelatives(jnt, parent=True, type='joint'):
            continue
        try:
            radius = max(cmds.getAttr(f'{jnt}.radius'), 1e-3)
        except Exception:
            continue
        extent = _max_shape_extent(node)
        if extent <= 1e-9:
            continue
        # _make_ctrl bakes radius * ctrl_scale * _BALL_RADIUS_FACTOR into the
        # shape; divide the factor back out so this returns the user's
        # ctrl_scale, not a value 0.4x too small (which the exporter would
        # then re-bake, shrinking the orbs 0.4x again every round trip).
        samples.append(extent / ref_extent / radius / _BALL_RADIUS_FACTOR)

    if not samples:
        return None
    samples.sort()
    return samples[len(samples) // 2]


def _max_shape_extent(node: str) -> float:
    """Largest shape-point distance from the node's own origin, in object
    space (rotation-invariant -- only a rescale disturbs it).

    Reads VERTICES (the ball ctrl) *and* CVs (a curve): the Armature ctrl
    became a mesh on 2026-07-13, but curve_o_matic.swap_shape can still
    put a curve on a ctrl by hand, and this measurement is the ONLY thing
    standing between the skeletal exporter's break/export/rebuild round
    trip and silently resizing every one of the user's ctrls back to 1.0.
    A CV-only reader would have returned 0.0 for every ball and taken the
    whole recovery down with it, silently."""
    best = 0.0
    points = (cmds.ls(f'{node}.vtx[*]', flatten=True) or []) \
        + (cmds.ls(f'{node}.cv[*]', flatten=True) or [])
    for pt in points:
        x, y, z = cmds.xform(pt, q=True, os=True, t=True)
        d = (x * x + y * y + z * z) ** 0.5
        if d > best:
            best = d
    return best


def _resolve_root(root: str = None) -> str:
    if root:
        if not cmds.objExists(root):
            raise RuntimeError(f'Armature: root {root!r} not in scene.')
        return root
    reg_root = nodes.get_registry_root_joint() if nodes.get_registry() else ''
    if reg_root and cmds.objExists(reg_root):
        return reg_root
    raise RuntimeError(
        'Armature: no root joint. Pass root= or load a rig with a '
        'registry root_joint.')


def _hierarchy(root: str) -> list:
    """BFS, parents before children."""
    result, queue = [], [root]
    while queue:
        j = queue.pop(0)
        result.append(j)
        queue.extend(cmds.listRelatives(j, children=True,
                                        type='joint') or [])
    return result


def _distance(a: str, b: str) -> float:
    pa = cmds.xform(a, q=True, ws=True, t=True)
    pb = cmds.xform(b, q=True, ws=True, t=True)
    return ((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2
            + (pa[2] - pb[2]) ** 2) ** 0.5


# ─────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────

def _ensure_aimers(joints: list, scale: float) -> list:
    """Every joint has an aimer, always (spec §5). Freshly created
    aimers seed from geometric detection (joa.seed_aimer_from_detection):
    a joint whose aim axis points at a child along EITHER polarity
    targets that child — a −axis match (mirrored sides) captures the
    ±180 flip into the aimer offset so orientation is preserved
    exactly; anything else stays Local so authored orientation is
    never silently rewritten. Existing aimers are never touched.

    Returns the joints whose aimers were created FRESH here — the only
    aimers creation defaults (e.g. the twist parent-flip) may touch."""
    fresh = []
    for j in joints:
        if joa.aimer_exists(j):
            continue
        joa.create_aimer(j, scale=scale)
        joa.seed_aimer_from_detection(j)
        fresh.append(j)
    return fresh


def is_twist_joint(j: str) -> bool:
    """A CHILDLESS joint driven by a distribute follow rule is a twist joint:
    it rides a segment, has nothing of its own to aim at, and wants its aimer
    pointed at the segment end (the elbow).

    Public because the limb-fragment drop needs the SAME test (limbs/
    builder._setup_limb_aimers, 2026-07-20): a dropped twist gets its
    aimer created by the drop, so it is never 'fresh' by the time
    _bake_and_restore_aimers below runs, and the convention has to be
    applied at drop time instead. One definition, both callers."""
    if cmds.listRelatives(j, children=True, type='joint'):
        return False
    rule = fr.get_follow_rule(j)
    return bool(rule and rule['kind'] == 'distribute')


def _bake_and_restore_aimers(joints: list, scale: float,
                             fresh: list = None) -> None:
    """Orient every joint to its aimer (world-preserving), then bring
    the aimers back in the exact same WORLD state. orient_all_aimers
    deletes aimers as its final step, so state is captured around it.

    `fresh` is the list _ensure_aimers just created — the only aimers a
    creation default may touch. The restore reproduces each aimer's WORLD
    orientation (get/apply_aimer_state world_rotation), never the local
    offset: a local-only restore layered the twist flip onto its own baked
    result, rotating twist aimers a further 180 on every Armature rebuild
    — including the one inside Unbuild (found 2026-07-14, Adrian's manny
    round-trip). Authored aimer state must survive every cycle unchanged."""
    # A twist joint has no child for its aimer to point at, so its aimer
    # defaults to Local (pointing nowhere useful). Aim it at the PARENT and flip
    # 180 RZ so it lands on the segment end — the elbow (Adrian's trick,
    # 2026-07-13, confirmed working on both sides). CREATION DEFAULT ONLY:
    # applied to aimers _ensure_aimers made fresh this build, never to an
    # aimer carrying authored/restored state (2026-07-14 — the unconditional
    # flip discarded the user's state on every rebuild).
    #
    # (History: briefly reverted when a candy-wrapper hit the export, but that
    # was an unrelated bug — the bind_joints set had swept in transient ribbon
    # roll joints and the mesh got skinned to them; the roll joints die and
    # respawn every build, so those verts collapsed to origin. This aim was
    # never the cause. Restored.)
    for j in (fresh or []):
        if cmds.objExists(j) and is_twist_joint(j):
            joa.point_aimer_at_parent_flipped(j)

    captured = {}
    for j in joints:
        state = joa.get_aimer_state(j)
        if state:
            captured[j] = state
    joa.orient_all_aimers()
    for j in joints:
        if not cmds.objExists(j):
            continue
        joa.create_aimer(j, scale=scale)
        state = captured.get(j)
        if state:
            joa.apply_aimer_state(
                j, aim_target=state['aim_target'],
                aim_offset=state['aim_offset'],
                world_rotation=state.get('world_rotation'))


def _normalize_pure_aim_children(joints: list, conv) -> None:
    """Post-bake, an aimed parent points at its target child along the
    ± aim axis up to FP noise (a mirrored side's ±180 flip lands the
    child on the NEGATIVE axis — the sign handles it). Snap that child
    onto the axis: aim channel = signed bone length, other channels = 0.
    World positions preserved within noise; stretch math assumes this.

    Geometry gate: only children already ON the ±axis snap. A
    child-targeted aimer carrying a genuine Y/Z deviation leaves its
    child untouched — snapping would MOVE the joint, and placement is
    sacred."""
    axis = conv.stretch_channel[1]          # 'x' from 'tx'
    others = [a for a in ('x', 'y', 'z') if a != axis]
    for p in joints:
        verdict = joa.classify_aimer(p)
        child = verdict['target_child']
        if verdict['mode'] != 'aim_child' or not child:
            continue
        if not cmds.objExists(child):
            continue
        d = _distance(p, child)
        if d < 1e-4:
            continue
        off_axis = (cmds.getAttr(f'{child}.t{others[0]}') ** 2
                    + cmds.getAttr(f'{child}.t{others[1]}') ** 2) ** 0.5
        if off_axis > max(d * 0.01, 1e-3):
            continue  # authored deviation — leave placement alone
        cur = cmds.getAttr(f'{child}.t{axis}')
        sign = 1.0 if cur >= 0.0 else -1.0
        cmds.setAttr(f'{child}.t{axis}', sign * d)
        for a in others:
            cmds.setAttr(f'{child}.t{a}', 0.0)


# _dress_joint_ctrl (rotated a '_joint' cube's CVs to face the aim
# target) was removed with the cube on 2026-07-13. A ball has no
# orientation to dress, and on a mesh the function was a silent no-op —
# `cmds.ls(ctrl.cv[*])` returns [] and it returned early, doing nothing
# while still looking like it did something. The aim story is carried by
# the guide lines, the aimer tip, and the yellow/cyan ctrl color, which
# is what the old code's own comments already said. Git has it if the
# cube ever comes back.


def _connect_line(parent_ctrl: str, child_ctrl: str, name: str,
                  grp: str, pmm_cache: dict) -> str:
    """Template-grey guide line between two ctrls — the pole-vector
    annotation look. A 2-CV linear curve whose points are DRIVEN by
    the ctrls' world positions (worldMatrix → pointMatrixMult →
    controlPoints), so it follows every drag live with no constraint.
    The curve transform stays identity under the armature grp (also
    identity), which makes world == local for the CVs. Template
    display: grey, unselectable. One pointMatrixMult per ctrl, shared
    across its lines via pmm_cache."""
    line = cmds.curve(degree=1, point=[(0, 0, 0), (0, 0, 1)],
                      name=name)
    shape = cmds.listRelatives(line, shapes=True)[0]
    cmds.setAttr(f'{shape}.overrideEnabled', 1)
    cmds.setAttr(f'{shape}.overrideDisplayType', 1)   # template
    # The lines carry the connection story, so they have to survive the
    # x-ray balls that now draw over everything. nurbsCurve is the OTHER
    # shape type Maya gives alwaysDrawOnTop to, so they can win the same
    # way the balls do. (Not saved to the file — reapply_xray re-arms it.)
    cmds.setAttr(f'{shape}.alwaysDrawOnTop', 1)
    cmds.parent(line, grp)
    for i, ctrl in enumerate((parent_ctrl, child_ctrl)):
        pmm = pmm_cache.get(ctrl)
        if pmm is None:
            pmm = cmds.createNode('pointMatrixMult',
                                  name=f'{ctrl}_amtLinePos',
                                  skipSelect=True)
            cmds.connectAttr(f'{ctrl}.worldMatrix[0]',
                             f'{pmm}.inMatrix')
            pmm_cache[ctrl] = pmm
        cmds.connectAttr(f'{pmm}.output',
                         f'{shape}.controlPoints[{i}]')
    return line


def _wire_aim_edge(parent_jnt: str, child: str, ctrl: str,
                   stretch_channel: str) -> None:
    """Build the SC-IK aim edge parent_jnt -> child: ikHandle(startJoint=
    parent_jnt, endEffector=child) parented under child's ctrl (so the
    solver aims parent_jnt at wherever the ctrl sits), plus the stretch
    network (|parent_jnt -> ctrl| x sign -> child.stretch_channel) that
    puts child ON the ctrl.

    Factored out of build_armature's ctrl loop (Task 1.2 follow-up) so
    _follow_retrofit_reaim_parent can build the IDENTICAL edge when a
    follow rule orphans a parent's aim child post-build — a rewired
    edge must be indistinguishable from one built fresh, not a
    hand-rolled approximation living in two places.

    Caller's responsibility: child.stretch_channel must already be
    UNLOCKED (a fresh build's ctrl loop always calls this before
    _lock_joint_channels; a retrofit rewire must unlock it itself —
    Maya refuses a NEW connection onto a locked attribute even though
    an EXISTING one keeps driving it, see this module's own docstring)."""
    ikh, eff = cmds.ikHandle(
        startJoint=parent_jnt, endEffector=child,
        solver='ikSCsolver', name=f'{child}_amtIkh')
    cmds.rename(eff, f'{child}_amtEff')
    cmds.parent(ikh, ctrl)
    cmds.setAttr(f'{ikh}.visibility', 0)

    # Stretch: |parent → ctrl| × sign → child aim translate. The SC
    # solver aims the parent at the ctrl; the stretch puts the child
    # ON it.
    sign = (1.0 if cmds.getAttr(f'{child}.{stretch_channel}') >= 0.0
            else -1.0)
    dist = cmds.createNode('distanceBetween', name=f'{child}_amtDist')
    cmds.connectAttr(f'{parent_jnt}.worldMatrix[0]', f'{dist}.inMatrix1')
    cmds.connectAttr(f'{ctrl}.worldMatrix[0]', f'{dist}.inMatrix2')
    mult = cmds.createNode('multDoubleLinear', name=f'{child}_amtStretch')
    cmds.connectAttr(f'{dist}.distance', f'{mult}.input1')
    cmds.setAttr(f'{mult}.input2', sign)
    cmds.connectAttr(f'{mult}.output', f'{child}.{stretch_channel}',
                     force=True)


def _make_ctrl(jnt: str, kind: int, ctrl_scale: float,
               parent: str) -> str:
    color = _side_color(jnt, kind)
    ctrl = _build_ball(f'{jnt}_amt_CTL')
    _dress_ball(ctrl, color)
    # Size tracks the JOINT'S RADIUS (Adrian, 2026-07-05: flat sizing
    # made radius-1 finger ctrls 3x too big next to radius-3 bodies).
    # ctrl_scale is a multiplier on the radius, 1.0 = ctrl matches
    # the joint's authored display size; _BALL_RADIUS_FACTOR then trims
    # every orb down so tight finger chains stay grabbable.
    try:
        radius = max(cmds.getAttr(f'{jnt}.radius'), 1e-3)
    except Exception:
        radius = 1.0
    size = radius * ctrl_scale * _BALL_RADIUS_FACTOR
    cmds.setAttr(f'{ctrl}.scale', size, size, size,
                 type='double3')
    cmds.makeIdentity(ctrl, apply=True, scale=True)
    cmds.parent(ctrl, parent)
    pos = cmds.xform(jnt, q=True, ws=True, t=True)
    cmds.xform(ctrl, ws=True, t=pos)
    # World-aligned; scale stays locked. Rotation is FREE: the ctrl
    # tree mirrors the joint hierarchy, so rotating a parent ctrl
    # swings its whole child branch FK-style while the SC-IK edges
    # keep every parent aiming at its child — rough-in with the
    # parent, fine-tune the children. Roll still belongs to the
    # aimers at bake time.
    for attr in ('sx', 'sy', 'sz'):
        cmds.setAttr(f'{ctrl}.{attr}', lock=True, keyable=False,
                     channelBox=False)
    return ctrl


def _make_root_ctrl(jnt: str, kind: int, ctrl_scale: float,
                    parent: str) -> str:
    """The root ctrl: a NURBS circle-four-arrow instead of an orb.

    Restored 2026-07-18 (Adrian) after the 2026-07-05 rule that gave the
    root no ctrl at all. Two things it buys back: the root joint becomes
    draggable like every other joint, and every child ctrl now parents
    UNDER it, so the root ctrl moves the whole armature as one piece
    (the ctrl tree finally mirrors the joint tree all the way up, which
    is what makes the root-to-first-child guide line resolve too).

    build_shape() bakes the size into the CVs and leaves the transform
    at identity, so unlike the orb there is nothing to freeze afterward.
    Being a nurbsCurve it takes alwaysDrawOnTop the same way the guide
    lines do, and ctrl_shapes() already sweeps curves under the group,
    so reapply_xray() re-arms it on scene open with no extra wiring.
    """
    color = _side_color(jnt, kind)
    try:
        radius = max(cmds.getAttr(f'{jnt}.radius'), 1e-3)
    except Exception:
        radius = 1.0
    ctrl = com.build_shape(
        _ROOT_CTRL_SHAPE, f'{jnt}_amt_CTL',
        radius=radius * ctrl_scale * _ROOT_CTRL_RADIUS_FACTOR)
    for shp in cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []:
        cmds.setAttr(f'{shp}.alwaysDrawOnTop', 1)
        cmds.setAttr(f'{shp}.overrideEnabled', 1)
        cmds.setAttr(f'{shp}.overrideColor', color)
    cmds.parent(ctrl, parent)
    pos = cmds.xform(jnt, q=True, ws=True, t=True)
    cmds.xform(ctrl, ws=True, t=pos)
    for attr in ('sx', 'sy', 'sz'):
        cmds.setAttr(f'{ctrl}.{attr}', lock=True, keyable=False,
                     channelBox=False)
    return ctrl


# Module-level progress hook (Adrian, 2026-07-05 brain dump item 5):
# the host window installs its bar once and EVERY build_armature call —
# New Rig, limb drop, unbuild epilogue, mirror/duplicate/reparent,
# aimer rebuilds — reports through it, instead of threading a callback
# into eleven call sites. Contract mirrors build_modules' progress_cb:
# cb(index, total, joint) per joint, final tick cb(total, total, '').
_PROGRESS_CB = None


def set_progress_reporter(cb) -> None:
    """Install cb(index, total, joint) (or None to remove)."""
    global _PROGRESS_CB
    _PROGRESS_CB = cb


# Throttle (2026-07-10 template-load perf): the GUI reporter pumps
# QApplication.processEvents() per call — 74 full event-queue drains on
# a UE5 mannequin, each repainting the whole canvas mid-build, while the
# HEADLESS load of the same template measures 2.0s total. Report at most
# ~8x/sec; first and final ticks always pass so the bar starts and
# completes crisply. (import time is stdlib — cheap, no Maya coupling.)
_PROGRESS_MIN_INTERVAL = 0.125  # seconds
_PROGRESS_LAST_TS = 0.0


def _report_progress(index: int, total: int, joint: str) -> None:
    global _PROGRESS_LAST_TS
    if _PROGRESS_CB is None:
        return
    import time
    now = time.monotonic()
    is_edge = index <= 1 or index >= total  # first + final ticks always report
    if not is_edge and (now - _PROGRESS_LAST_TS) < _PROGRESS_MIN_INTERVAL:
        return
    _PROGRESS_LAST_TS = now
    try:
        _PROGRESS_CB(index, total, joint)
    except Exception:
        pass  # a UI hiccup must never block an Armature build


@_suspends_armature_watch
def build_armature(root: str = None, ctrl_scale: float = 1.0,
                    aimer_scale: float = 10.0) -> dict:
    """Build the Armature over the skeleton at/under `root` (default:
    the registry's root joint).

    ctrl_scale is a MULTIPLIER on each joint's radius (1.0 = ctrl
    matches the joint's authored display size). Radius-3 body joints
    therefore keep the familiar size while radius-1 fingers finally
    fit. The root gets the circle-four-arrow ctrl on its own factor.

    Sequence: defensive skin detach → tear down any existing Armature
    → ensure aimers everywhere → orientation bake + aimer restore →
    normalize pure-aim chains → ctrl tree + SC-IK / pointConstraint
    edges + stretch.

    Returns:
        {'ctrls': [...], 'ik_edges': int, 'point_edges': int}
    """
    root = _resolve_root(root)
    conv = oc.resolve(nodes.get_registry() or '')

    with undo_chunk('BuildArmature'):
        # Orientation bake corrupts live skins — detach (no-op if none).
        skin_connect_app.disconnect_all_skins()

        if armature_exists():
            delete_armature()

        joints = _hierarchy(root)
        fresh = _ensure_aimers(joints, aimer_scale)
        _bake_and_restore_aimers(joints, aimer_scale, fresh=fresh)
        _normalize_pure_aim_children(joints, conv)

        grp = cmds.createNode('transform', name=_GRP)
        # '_armature' display layer — one visibility toggle for the
        # whole Armature (ctrls, ik handles, guide lines). Plain
        # layer: visible + selectable; survives delete_armature like
        # _Joints survives unbuild.
        if not cmds.objExists(_ARMATURE_LAYER):
            cmds.createDisplayLayer(name=_ARMATURE_LAYER, empty=True)
        cmds.editDisplayLayerMembers(_ARMATURE_LAYER, grp,
                                     noRecurse=True)

        ctrls = {}
        ik_parents = set()
        ik_edges = 0
        point_edges = 0
        ruled_kinds = {}   # joint -> 'distribute' | 'match' (Task 1.2)
        stretch_channel = conv.stretch_channel  # 'tx' in house
        total = len(joints)

        for jnt_index, j in enumerate(joints):
            _report_progress(jnt_index, total, j)
            par_list = cmds.listRelatives(j, parent=True,
                                          type='joint') or []
            parent_jnt = par_list[0] if par_list else None
            ctrl_parent = ctrls.get(parent_jnt, grp)

            if parent_jnt is None:
                # Root joint: the circle-four-arrow ctrl (Adrian,
                # 2026-07-18, reversing the 2026-07-05 no-root-ctrl
                # rule). Point-constrained like any placed ctrl, and it
                # becomes ctrl_parent for every child ctrl below, so it
                # carries the entire armature as one piece. No aim edge
                # is possible here (nothing above it to aim), which is
                # why this stays its own branch rather than folding into
                # the aim/point decision underneath.
                own = joa.classify_aimer(j)
                kind = (_COLOR_AIMING if own['mode'] == 'aim_child'
                        else _COLOR_PLACED)
                ctrl = _make_root_ctrl(j, kind, ctrl_scale, grp)
                cmds.pointConstraint(ctrl, j, mo=False,
                                     name=f'{j}_amt_ptcon')
                point_edges += 1
                ctrls[j] = ctrl
                continue

            # Follow-ruled joint (Task 1.2, SPEC 3.1): position (and,
            # for 'match', orientation) is entirely rule-driven — no
            # ctrl, no SC-IK/pointConstraint edge at all. A rule whose
            # target(s) got deleted raises; that must not take down
            # the whole Armature build, so it falls back to a normal
            # ctrl (with a loud warning) same as any other build-time
            # recoverable inconsistency in this loop.
            try:
                rule = fr.get_follow_rule(j)
            except RuntimeError as e:
                cmds.warning(
                    f'Armature: follow rule on {j!r} is broken ({e}); '
                    f'building a normal ctrl instead.')
                rule = None
            if rule is not None:
                ruled_kinds[j] = rule['kind']
                continue
            else:
                verdict = joa.classify_aimer(parent_jnt)
                aim_edge = (verdict['mode'] == 'aim_child'
                            and verdict['target_child'] == j)
                # Geometry gate: SC-IK + stretch assume the child sits
                # ON the ±aim axis (post-normalize). The enum carries
                # intent; an aimed edge whose child holds a genuine
                # off-axis deviation demotes to a placed ctrl.
                bone_len = _distance(parent_jnt, j)
                aim_val = cmds.getAttr(f'{j}.{stretch_channel}')
                off_sq = bone_len ** 2 - aim_val ** 2
                off_axis = off_sq ** 0.5 if off_sq > 0.0 else 0.0
                on_axis = off_axis <= max(bone_len * 0.01, 1e-3)
                # Color from j's OWN aimer (see _COLOR_* note above).
                own = joa.classify_aimer(j)
                kind = (_COLOR_AIMING if own['mode'] == 'aim_child'
                        else _COLOR_PLACED)
                if aim_edge and bone_len > 1e-4 and on_axis:
                    ctrl = _make_ctrl(j, kind, ctrl_scale,
                                      ctrl_parent)
                    _wire_aim_edge(parent_jnt, j, ctrl, stretch_channel)
                    ik_parents.add(parent_jnt)
                    ik_edges += 1
                else:
                    if aim_edge and bone_len <= 1e-4:
                        cmds.warning(
                            f'Armature: {parent_jnt}→{j} bone length '
                            f'~0; falling back to translate-only ctrl.')
                    elif aim_edge:
                        cmds.warning(
                            f'Armature: {parent_jnt}→{j} aims at its '
                            f'child but sits {off_axis:.3f} off the aim '
                            f'axis (authored deviation) — translate-only '
                            f'ctrl, placement untouched.')
                    ctrl = _make_ctrl(j, kind, ctrl_scale,
                                      ctrl_parent)
                    cmds.pointConstraint(ctrl, j, mo=False,
                                         name=f'{j}_amt_ptcon')
                    point_edges += 1

            ctrls[j] = ctrl

        # Guide lines: parent ctrl → each child ctrl (branch joints
        # get one line per child). Template grey, live via worldMatrix
        # — pure display, the canvas-in-viewport read.
        pmm_cache = {}
        for j in joints:
            par_list = cmds.listRelatives(j, parent=True,
                                          type='joint') or []
            if not par_list:
                continue
            pc, cc = ctrls.get(par_list[0]), ctrls.get(j)
            if pc and cc:
                _connect_line(pc, cc, f'{j}_amtLine', grp, pmm_cache)

        _lock_joint_channels(joints, ik_parents, ruled_kinds)

        # Arm the live follow-rule hook AFTER every ctrl in this build
        # exists and is positioned (Task 1.2) — registering earlier
        # would fire on this loop's own _make_ctrl placement xforms.
        _follow_watch_ctrls(list(ctrls.values()))
        # Re-arm the x-ray on every future scene open: Maya does not save
        # alwaysDrawOnTop, so a saved Armature reopens with its ctrls
        # buried inside the mesh unless something puts the flag back.
        _install_xray_scene_hook()
        # And snap every ruled joint to its rule ONCE, right now — same
        # reasoning as the aimer bake above: don't wait for the first
        # drag to make a fresh build correct. No-op (empty list) when
        # no rules exist.
        if ruled_kinds:
            fr.evaluate(list(ruled_kinds.keys()))

        _report_progress(total, total, '')   # final tick — bar hides

        return {'ctrls': list(ctrls.values()),
                'ik_edges': ik_edges,
                'point_edges': point_edges}


def _lock_joint_channels(joints: list, ik_parents: set,
                         ruled_kinds: dict = None) -> None:
    """Lock translate + rotate on the skeleton so joints can't be
    moved by hand in Armature mode (the ctrls and aimers — and, as of
    Task 1.2, the follow-rule live hook — are the interface). Locking
    happens AFTER wiring, so constraint, stretch, and (see
    _follow_watch_ctrls) follow-rule writes all keep working (Maya
    only blocks NEW connections and manual edits on locked attrs).

    Rotate stays UNLOCKED on SC-IK aim parents — Maya's solvers
    refuse to drive locked rotation channels.

    ruled_kinds: {joint: 'distribute' | 'match'} from build_armature's
    ctrl loop (Task 1.2). Translate stays UNLOCKED on every ruled
    joint (follow_rules.evaluate() writes it directly, no ctrl exists
    to do it instead). Rotate ALSO stays unlocked for 'match' joints
    only — a 'distribute' joint's rotate is the aimer's job (see this
    module's docstring "DETERMINISTIC ORDER") and stays locked at
    whatever _bake_and_restore_aimers already baked into it."""
    ruled_kinds = ruled_kinds or {}
    for j in joints:
        if not cmds.objExists(j):
            continue
        kind = ruled_kinds.get(j)
        if kind is None:
            for attr in ('tx', 'ty', 'tz'):
                cmds.setAttr(f'{j}.{attr}', lock=True)
        if j not in ik_parents and kind != 'match':
            for attr in ('rx', 'ry', 'rz'):
                cmds.setAttr(f'{j}.{attr}', lock=True)


def _unlock_joint_channels() -> None:
    """Unlock translate + rotate on every joint the Armature wraps
    (derived from the ctrl names under the group). Must run before
    the group is deleted, and before any orientation bake — the bake
    writes rotate and reparents joints."""
    grp_children = cmds.listRelatives(_GRP, allDescendents=True,
                                      type='transform') or []
    joints = set()
    for node in grp_children:
        if not node.endswith('_amt_CTL'):
            continue
        j = node[:-len('_amt_CTL')]
        if cmds.objExists(j):
            joints.add(j)
    # The root carries its own ctrl since 2026-07-18, so the scan above
    # already finds it; this parent walk is now belt-and-braces rather
    # than the root's only route in. The registry root still joins
    # explicitly: a follow-ruled or otherwise ctrl-less joint can still
    # leave a gap, and the bake that follows teardown writes its rotate.
    for j in list(joints):
        for p in cmds.listRelatives(j, parent=True, type='joint') or []:
            joints.add(p)
    reg_root = nodes.get_registry_root_joint()
    if reg_root and cmds.objExists(reg_root):
        joints.add(reg_root)
    # Follow-ruled joints (Task 1.2) have no ctrl, so they're invisible
    # to the derivation above — pull them in explicitly. A 'distribute'
    # joint's rotate is locked by _lock_joint_channels (aimer-owned);
    # without this it would still be locked the next time a build's
    # aimer bake tries to write it.
    for j in fr.ruled_joints():
        if cmds.objExists(j):
            joints.add(j)
    for j in joints:
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{j}.{attr}', lock=False)


# ─────────────────────────────────────────────
# Export contract validation
# ─────────────────────────────────────────────

def validate_orientation_contract(root: str = None,
                                  tol: float = 1e-4) -> list:
    """Verify every joint at/under root satisfies the game-export
    orientation contract: rotate carries orientation, jointOrient == 0,
    rotateAxis == 0. (A joint carrying both a jointOrient and a
    rotateAxis silently world-aligns on UE/Unity import — the classic
    cross-DCC corruption.)

    Returns a list of human-readable violation strings; empty = clean.
    The Aim Joints bake produces contract-clean joints, so the fix for
    any violation is: run Aim Joints at Aimers, then rebuild.
    """
    root = _resolve_root(root)
    bad = []
    for j in _hierarchy(root):
        jo = cmds.getAttr(f'{j}.jointOrient')[0]
        ra = cmds.getAttr(f'{j}.rotateAxis')[0]
        if any(abs(v) > tol for v in jo):
            bad.append(
                f'{j}: jointOrient ({jo[0]:.4f}, {jo[1]:.4f}, '
                f'{jo[2]:.4f}) != 0')
        if any(abs(v) > tol for v in ra):
            bad.append(
                f'{j}: rotateAxis ({ra[0]:.4f}, {ra[1]:.4f}, '
                f'{ra[2]:.4f}) != 0')

    # WARN-ONLY (never a violation — authored data is the author's,
    # 2026-07-14): a root frame that is neither world identity nor the
    # Z-up engine arrangement (world RX(-90), a UE-import-derived
    # skeleton) is unusual and worth surfacing before it reaches an
    # engine. Both expected frames export correctly — the Z-up export
    # conversion is target-based.
    try:
        wr = cmds.xform(root, q=True, ws=True, rotation=True)
        is_identity = all(abs(v) < 0.1 for v in wr)
        is_z_up = (abs(wr[0] + 90.0) < 0.1 and abs(wr[1]) < 0.1
                   and abs(wr[2]) < 0.1)
        if not (is_identity or is_z_up):
            cmds.warning(
                f'[Fabricator] Root {root!r} world rotation is '
                f'({wr[0]:.2f}, {wr[1]:.2f}, {wr[2]:.2f}) — neither world '
                f'identity nor the Z-up engine frame (-90, 0, 0). Fine if '
                f'authored deliberately; engine imports may not match '
                f'engine-native skeletons.')
    except Exception:
        pass
    return bad


# ─────────────────────────────────────────────
# Teardown
# ─────────────────────────────────────────────

@_suspends_armature_watch
def delete_armature() -> None:
    """Remove the Armature completely. Joints keep their current
    positions and rotations (solver/stretch outputs stick as plain
    values once their drivers are deleted). Aimers are NOT touched —
    they outlive the Armature until Animation-Rig build."""
    with undo_chunk('DeleteArmature'):
        # Live mirror rides on the ctrls being deleted — drop it first.
        from maya_tools.rigging.fabricator import armature_mirror
        armature_mirror.disable(quiet=True)
        # Follow-rule live watchers (Task 1.2) are per-ctrl callbacks;
        # the ctrls are about to die with the group below, but drop our
        # own tracked ids defensively rather than rely solely on Maya's
        # automatic per-node cleanup.
        _follow_unwatch_all()
        # Unlock joint channels first — the orientation bake that
        # typically follows writes rotate and reparents joints.
        _unlock_joint_channels()
        # Constraints live under the constrained joints, not the group.
        for con in cmds.ls('*_amt_ptcon*', type='pointConstraint') or []:
            if cmds.objExists(con):
                cmds.delete(con)
        # Stretch + guide-line DG nodes (the line curves themselves
        # fall with the group cascade below).
        for node in (cmds.ls('*_amtDist', type='distanceBetween') or []) \
                + (cmds.ls('*_amtStretch', type='multDoubleLinear') or []) \
                + (cmds.ls('*_amtLinePos', type='pointMatrixMult') or []):
            if cmds.objExists(node):
                cmds.delete(node)
        # Group cascade: ctrl tree + ik handles.
        if cmds.objExists(_GRP):
            cmds.delete(_GRP)
        # The ctrl shaders are SHARED, so deleting the ctrl meshes does
        # not take them with it — without this they pile up one set per
        # build/unbuild cycle.
        _delete_ctrl_shaders()
        # Effectors are children of the joints — deleting the handles
        # can orphan them.
        for eff in cmds.ls('*_amtEff', type='ikEffector') or []:
            if cmds.objExists(eff):
                cmds.delete(eff)


# ─────────────────────────────────────────────
# Follow rules — live evaluation (Task 1.2, SPEC 3.1)
# ─────────────────────────────────────────────
#
# The mechanism: an om.MNodeMessage.addAttributeChangedCallback per
# Armature ctrl (armed once, at the end of build_armature, after every
# ctrl in the build already exists and is positioned — see the call
# site above). The moment ANY ctrl's translate/rotate changes, the
# callback re-runs follow_rules.evaluate() over a CACHED list of every
# ruled joint in the scene — not just this armature's, and not just
# whatever the moved ctrl geometrically affects, because evaluate()
# is already O(ruled joints) and cheap, and computing a precise
# per-ctrl affected-set would cost more than it saves. This is why
# "guard cheaply" here means "cheap per-callback early-out on an empty
# cache", not "narrow the trigger set" (SPEC 3.1 perf note).
#
# This reuses armature_watch's suspend/undo/redo/scene-teardown guard
# (armature_watch.is_muted()) rather than inventing a second one — any
# op that already suspends armature_watch (build_armature/
# delete_armature themselves, branch mirror/duplicate/reparent) mutes
# this hook for free, which matters: _make_ctrl's own placement xform
# would otherwise be indistinguishable from a user drag. Registering
# the callbacks only AFTER the ctrl loop finishes is what keeps THIS
# build's own ctrl placement from ever reaching the callback in the
# first place, independent of the mute guard.
#
# The ruled-joint cache is invalidated via follow_rules.add_change_
# listener — see follow_rules.py's own docstring for why that hook
# exists — so a rule created or cleared after the Armature already
# exists (e.g. via the Properties Follow block, a later task) is
# picked up on the NEXT ctrl move without any per-tick scene scan.
#
# DELIBERATE DEPARTURE from armature_watch's registration/batching
# pattern (not a shortcut — armature_watch.is_muted() is reused above
# precisely because everything else here differs on purpose):
#   * Registration count. armature_watch gets away with exactly two
#     SCENE-SCOPED native callbacks (addParentAddedCallback,
#     addNodeRemovedCallback) because Maya's DAG-message API offers
#     scene-wide forms of those two events. There is no scene-wide
#     equivalent for "an attribute changed on any node" —
#     MNodeMessage.addAttributeChangedCallback is inherently a
#     PER-NODE registration in Maya's API, full stop. Matching
#     armature_watch's O(1) registration count is not on offer for
#     this event class; one callback per ctrl is the API's floor, not
#     a missed opportunity to do what armature_watch does.
#   * Batching. armature_watch defers its two gestures (parent/delete)
#     to one evalDeferred flush per idle cycle because those are
#     discrete, complete-once ops — a multi-select delete is one
#     command and should read as one sandwich, and the user never sees
#     the "before" state again once the gesture starts. A ctrl move is
#     the opposite: it's a CONTINUOUS live drag, and the whole point of
#     Task 1.2 (see test_armature_live_follow_rules_evaluate_on_ctrl_
#     move) is that followers sit in their new position by the time
#     the move command returns — no idle-tick lag. Deferring evaluate()
#     to the next idle cycle, armature_watch-style, would desync every
#     follower from the ctrl by one tick during a drag, which is
#     exactly the live-update guarantee this hook exists to provide.
#     So evaluate() runs synchronously, in the same callback, every
#     time. A single logical drag step CAN fire it more than once (Maya
#     reports tx/ty/tz as separate plug writes) — accepted cost, not
#     an oversight: evaluate() is already O(ruled joints) and cheap
#     (see the perf note above), so a same-tick redundant re-evaluate
#     costs far less than the correctness risk of coalescing across an
#     idle boundary.

_FOLLOW_STATE = {
    'ruled_cache': None,        # None = never built; else a list
    'dirty': True,
    'watch_ids': [],            # this build's attributeChangedCallback ids
    'listener_installed': False,
    'evaluating': False,        # re-entrancy guard, see _follow_on_ctrl_attr_changed
}


# Scene-teardown hook for the state dict above — flake hunt, Task 3.2
# item 0 (2026-07-10). _FOLLOW_STATE is process-lifetime module state:
# it survives a bare cmds.file(new=True)/cmds.file(open=True) same as
# any other Python global, because a scene wipe fires no follow_rules
# change-listener (there is nothing left to notify — every rule node
# just vanished with the scene) and armature_watch's own scene-teardown
# guard (MFileIO.isNewingFile/isOpeningFile) only mutes ITS callbacks,
# not this module's. Left alone, 'watch_ids' accumulates one dead
# MNodeMessage callback id per ctrl per build forever across a long
# mayapy/GUI session (verified harmless-but-real: Maya auto-invalidates
# a per-node attributeChangedCallback the moment its node is destroyed
# — a stale id never refires against an unrelated later node, confirmed
# against this Maya version directly — but the id itself is never
# reclaimed from the list either), and 'ruled_cache'/'dirty' would
# describe a scene that no longer exists until the NEXT real
# build_armature() call happens to force dirty=True again — a gap this
# module's own sibling, armature_mirror.py, already closed for its
# analogous per-scene state via an identical
# MSceneMessage(kAfterNew/kAfterOpen) hook (_on_scene_reset there);
# this is the same fix for the same reason, applied here. Installed
# once, alongside the follow_rules change-listener, in
# _follow_watch_ctrls below — see that function's own docstring.
def _on_follow_scene_reset(*_):
    _FOLLOW_STATE['ruled_cache'] = None
    _FOLLOW_STATE['dirty'] = True
    _FOLLOW_STATE['watch_ids'] = []


def _follow_cache_dirty(joint: str = None) -> None:
    """follow_rules.add_change_listener target. Two jobs:

    1. Mark the ruled-joint cache stale — lazily rebuilt by the next
       ctrl-move callback (never eagerly, so a rule edit itself never
       pays a scene scan, only the next drag does).
    2. RETROFIT: a rule created on `joint` after a live Armature
       already exists (SPEC P1 gate — "mark twist joints on a live
       armature") targets a joint that got a NORMAL ctrl at build
       time, since nothing marked it as a follower yet. That ctrl (and
       whatever SC-IK/pointConstraint edge came with it) is now stale
       and, worse, has the joint's translate LOCKED — evaluate() could
       never write to it. _follow_retrofit_joint reconciles that one
       joint in place: drop its ctrl/edge, unlock what the rule needs.
       A rule CLEAR needs no retrofit here — the joint just freezes at
       its last position; getting a proper ctrl back is a rebuild,
       like any other topology change."""
    _FOLLOW_STATE['dirty'] = True
    if not joint or not armature_exists():
        return
    try:
        rule = fr.get_follow_rule(joint)
    except RuntimeError:
        rule = None
    if rule is not None:
        try:
            _run_follow_retrofit(joint, rule)
        except Exception:
            traceback.print_exc()


def _run_follow_retrofit(joint: str, rule: dict) -> None:
    """_follow_retrofit_joint (and, reached from the same call stack,
    _follow_retrofit_reaim_parent's edge surgery) under the SAME
    armature_watch suspend guard build_armature/delete_armature use
    (_suspends_armature_watch) — plus its own undo chunk, matching
    that pair's convention of "suspend outside, undo_chunk inside".

    Bug this closes (Adrian, 2026-07-09 live repro — "a ton of cycle
    warnings, then it broke"): _follow_retrofit_joint's own
    cmds.delete(ctrl) is indistinguishable, from armature_watch's
    point of view, from a user deleting that ctrl by hand. Unsuspended,
    armature_watch's delete-sync (armature_watch.py) queues it and,
    once Maya goes idle (or immediately, in mayapy batch — evalDeferred
    runs synchronously there), reads it as "the user deleted this
    branch" and runs branch_ops.delete_branches() on the very joint
    that JUST gained the rule. delete_branches() always wraps a live
    Armature in its own delete_armature() + build_armature() pair — a
    FULL teardown/rebuild of every ctrl/SC-IK/pointConstraint in the
    rig, not just the ruled joint's — which is why Adrian's repro saw
    Cycle warnings on translateX across essentially the whole skeleton
    (fingers, spine, neck, legs) and not just the two twists: it is a
    complete Armature rebuild firing re-entrantly, mid-retrofit,
    followed by branch_ops reporting the ruled joints themselves as
    deleted. One suspend closes both symptoms at once — see
    _dev/test_limb_units_maya.py's
    test_ue_twin_arm_retrofit_ruled_joints_survive_watch_race (and
    sibling battery) for the batch-visible regression coverage,
    including a red-before/green-after run with this guard disabled.
    """
    try:
        from maya_tools.rigging.fabricator import armature_watch
        watch_ctx = armature_watch.suspended()
    except Exception:
        watch_ctx = contextlib.nullcontext()
    with watch_ctx, undo_chunk('FollowRetrofit'):
        _follow_retrofit_joint(joint, rule)


def _follow_retrofit_joint(joint: str, rule: dict) -> None:
    """Reconcile ONE joint that just gained a follow rule while a live
    Armature already exists: tear down whatever normal ctrl/edge
    build_armature gave it (translate-only pointConstraint, or an
    SC-IK aim edge — both are checked, harmlessly no-op if absent) and
    unlock the channels the rule now owns. No-op if the joint never
    had a ctrl (root, or already ruled at build time — the ordinary
    build-time path already left it correctly unlocked).

    Scope note: if `joint` was itself an SC-IK aim PARENT for some
    other child edge, that child's ikHandle lives under joint's own
    ctrl and is destroyed along with it — an existing, documented
    build-time constraint (aim parents don't carry follow rules in the
    fixtures this task tests); a full re-triage of the child's edge is
    an Armature rebuild concern, not this hook's.

    Mirror-image case, HANDLED here (SPEC follow-up, "a ton of cycle
    warnings, then it broke" — Adrian 2026-07-09): `joint` may instead
    be the AIM CHILD — its OWN parent's ikHandle (startJoint=parent,
    endEffector=joint) lives under `joint`'s ctrl, so tearing that ctrl
    down orphans the PARENT's only rotate-driving edge, not just
    `joint`'s own. Left alone this silently freezes the parent's
    rotate forever (confirmed: it never budges across further ctrl
    drags) and — worse — persists across a full Armature rebuild too,
    since classify_aimer keeps reporting the parent's STALE aimTarget
    (the ruled joint is still a real DAG child, just ctrl-less, so the
    enum still lists it and the aimer curve still points at it) and no
    sibling is ever offered the edge in its place. See
    _follow_retrofit_reaim_parent, called below whenever `joint` was
    an aim child.

    DAG note: `joint` need not be a leaf. Because build_armature's ctrl
    tree mirrors the joint tree one-to-one (_make_ctrl parents every
    ctrl under its joint's PARENT joint's ctrl), any joint ruled after
    the Armature already exists may have live descendant ctrls
    parented directly under its own ctrl (e.g. an elbow with a wrist
    child). A plain cmds.delete(ctrl) would cascade-delete that whole
    child-ctrl subtree along with it. So before deleting `joint`'s own
    ctrl, every direct *_amt_CTL child is reparented up to that ctrl's
    OWN current parent (the joint's former parent's ctrl, or the
    Armature group for a joint whose parent has no ctrl) — the same
    slot a rebuild would put them in."""
    ctrl = f'{joint}_amt_CTL'
    if not cmds.objExists(ctrl):
        return
    # Captured BEFORE teardown: was `joint` its own parent's SC-IK aim
    # child? (mirror-image case above.) The ikHandle name is the tell
    # regardless of which branch built it — fresh via _wire_aim_edge or
    # rewired by a previous retrofit pass.
    was_aim_child = cmds.objExists(f'{joint}_amtIkh')
    parent_list = cmds.listRelatives(joint, parent=True, type='joint') or []
    parent_jnt = parent_list[0] if parent_list else None

    for con in cmds.listRelatives(joint, type='pointConstraint') or []:
        if cmds.objExists(con):
            cmds.delete(con)
    for node in (f'{joint}_amtIkh', f'{joint}_amtDist',
                f'{joint}_amtStretch', f'{joint}_amtEff',
                f'{joint}_amtLine'):
        if cmds.objExists(node):
            cmds.delete(node)
    if cmds.objExists(ctrl):
        # Rescue descendant ctrls BEFORE the delete below would take
        # them down with it (see "DAG note" above).
        parent_list = cmds.listRelatives(ctrl, parent=True) or []
        rescue_parent = parent_list[0] if parent_list else _GRP
        child_ctrls = [
            c for c in
            (cmds.listRelatives(ctrl, children=True, type='transform')
             or [])
            if c.endswith('_amt_CTL')]
        if child_ctrls:
            cmds.parent(child_ctrls, rescue_parent)
        cmds.delete(ctrl)
    for attr in ('tx', 'ty', 'tz'):
        cmds.setAttr(f'{joint}.{attr}', lock=False)
    if rule['kind'] == 'match':
        for attr in ('rx', 'ry', 'rz'):
            cmds.setAttr(f'{joint}.{attr}', lock=False)

    if was_aim_child and parent_jnt:
        _follow_retrofit_reaim_parent(joint, parent_jnt)


def _detect_aim_child_excluding(parent_jnt: str, exclude: set, conv,
                                tol_deg: float = 0.75) -> str:
    """Geometric on-axis child detection — the SAME heuristic
    joint_orient_app._detect_aim_child_signed uses (best |dot| with
    parent_jnt's aim axis; Maya's listRelatives order breaks ties, per
    that function's own '>=' comparison) — but restricted to children
    NOT in `exclude`.

    joint_orient_app's own detector has no exclusion hook, and this
    module doesn't own that file (rewiring is a follow-rules/armature
    concern — see collaboration-protocol's cross-repo seam for
    joint_orient), so the filter lives here instead. A follow-ruled
    sibling must never win this pick — it can never hold a ctrl again,
    so choosing it would just reproduce the exact orphaning
    _follow_retrofit_reaim_parent exists to fix, the next time another
    sibling is ruled."""
    children = cmds.listRelatives(parent_jnt, children=True,
                                  type='joint') or []
    candidates = [c for c in children if c not in exclude]
    if not candidates:
        return ''
    jm = om.MMatrix(cmds.xform(parent_jnt, q=True, ws=True, matrix=True))
    aim_world = (om.MVector(*conv.aim_vector) * jm).normalize()
    jp = om.MVector(*cmds.xform(parent_jnt, q=True, ws=True, t=True))

    best, best_dot = '', math.cos(math.radians(tol_deg))
    for child in candidates:
        cp = om.MVector(*cmds.xform(child, q=True, ws=True, t=True))
        d = cp - jp
        if d.length() < 1e-5:
            continue   # co-located joint — no direction to compare
        dot = abs(d.normal() * aim_world)
        if dot >= best_dot:
            best, best_dot = child, dot
    return best


def _follow_retrofit_reaim_parent(joint: str, parent_jnt: str) -> None:
    """`joint` (just retired above) was parent_jnt's SC-IK aim child —
    the ikHandle _follow_retrofit_joint just deleted was the ONLY
    thing driving parent_jnt's rotate. Re-triage parent_jnt so it
    doesn't silently freeze forever (see _follow_retrofit_joint's
    "Mirror-image case" docstring section for the full writeup):

    1. Already fixed? (a second stray notification, or a sibling
       retrofit already re-triaged this same parent) — defensive
       no-op if some child already carries a live aim edge.
    2. Re-detect a valid aim child among parent_jnt's CURRENT children
       via the same on-axis geometry classify_aimer's seed step uses,
       EXCLUDING every child that itself already carries a follow rule
       (_detect_aim_child_excluding) — a ruled sibling can never hold
       a ctrl again, so picking one would just recreate the exact same
       orphaning the NEXT time another sibling is ruled. This matters
       when multiple siblings are ruled in sequence sharing one
       parent (Adrian's repro): by the time the LAST one retrofits,
       every other ruled sibling must already be off the table, not
       just the one being retrofitted right now.
    3. Found one: upgrade its existing translate-only ctrl (built
       under the now-stale classification) to the SC-IK edge via
       _wire_aim_edge — the identical edge a fresh build would give
       it — and re-seed parent_jnt's aimer to match, so a LATER full
       rebuild stays consistent instead of re-discovering the same
       stale target.
    4. None found: demote parent_jnt's aimer to 'Local' (stop
       persistently pointing at a dead target — a rebuild would
       otherwise keep refusing every remaining sibling the edge too,
       per the docstring above) and warn; parent_jnt's rotate stays
       exactly where it is — locked-but-coherent, not a live edge to
       nothing."""
    if not armature_exists() or not cmds.objExists(parent_jnt):
        return
    parent_ctrl = f'{parent_jnt}_amt_CTL'
    if not cmds.objExists(parent_ctrl):
        return   # parent has no ctrl (follow-ruled) -- nothing to rewire

    kids = cmds.listRelatives(parent_jnt, children=True, type='joint') or []
    if any(cmds.objExists(f'{k}_amtIkh') for k in kids):
        return   # already has a live aim edge -- nothing orphaned

    conv = oc.resolve(nodes.get_registry() or '')
    ruled_kids = {k for k in kids if fr.get_follow_rule(k) is not None}
    candidate = _detect_aim_child_excluding(parent_jnt, ruled_kids, conv)

    if not candidate or not cmds.objExists(f'{candidate}_amt_CTL'):
        cmds.warning(
            f"Armature: {parent_jnt!r}'s aim edge was orphaned by a "
            f"follow rule on {joint!r} and no other child sits on its "
            f"aim axis -- demoting to Local (authored). Rebuild the "
            f"Armature after placing/orienting a new target child to "
            f"restore aiming.")
        joa.apply_aimer_state(parent_jnt, aim_target='Local')
        return

    candidate_ctrl = f'{candidate}_amt_CTL'
    for con in cmds.listRelatives(candidate, type='pointConstraint') or []:
        if cmds.objExists(con):
            cmds.delete(con)

    stretch_channel = conv.stretch_channel
    # A fresh build wires this edge BEFORE _lock_joint_channels runs;
    # here the candidate's stretch channel is already locked from the
    # ORIGINAL build (it was a plain translate-only ctrl then) -- Maya
    # refuses a NEW connection onto a locked attribute even though an
    # existing one keeps driving it (this module's own docstring).
    cmds.setAttr(f'{candidate}.{stretch_channel}', lock=False)
    _wire_aim_edge(parent_jnt, candidate, candidate_ctrl, stretch_channel)
    joa.apply_aimer_state(parent_jnt, aim_target=candidate)
    cmds.warning(
        f"Armature: {parent_jnt!r}'s aim edge was orphaned by a follow "
        f"rule on {joint!r} -- rewired to {candidate!r}.")


def _follow_ruled_joints() -> list:
    """Cheap in the common case: a plain list read. Only rebuilds
    (one follow_rules.ruled_joints() scene scan) when dirty — on the
    first call ever, or on the first ctrl move after a rule was set or
    cleared anywhere in the scene."""
    if _FOLLOW_STATE['dirty'] or _FOLLOW_STATE['ruled_cache'] is None:
        _FOLLOW_STATE['ruled_cache'] = fr.ruled_joints()
        _FOLLOW_STATE['dirty'] = False
    return _FOLLOW_STATE['ruled_cache']


def _follow_on_ctrl_attr_changed(msg, plug, other_plug, client_data):
    if not (msg & om.MNodeMessage.kAttributeSet):
        return
    name = plug.partialName(useLongNames=True)
    if not (name.startswith('translate') or name.startswith('rotate')):
        return
    try:
        from maya_tools.rigging.fabricator import armature_watch
        if armature_watch.is_muted():
            return
    except Exception:
        return

    # Re-entrancy guard: evaluate() only ever writes JOINT translate/
    # rotate (never a ctrl's — this callback is armed on ctrl nodes
    # exclusively, per _follow_watch_ctrls), so this should be
    # unreachable in ordinary operation. It's cheap insurance against
    # exactly the failure shape a same-frame DG cycle produces
    # elsewhere in this studio (see _limb_common.py's aim-pin cycle
    # writeup, and this module's "Mirror-image case" retrofit note) —
    # a write made INSIDE evaluate() landing back on a watched ctrl
    # plug and re-entering this same callback mid-evaluate, rather
    # than let Maya's evaluator find out the hard way.
    if _FOLLOW_STATE['evaluating']:
        return

    joints = _follow_ruled_joints()
    if not joints:
        return   # zero rules anywhere — cheap early-out, no eval call
    _FOLLOW_STATE['evaluating'] = True
    try:
        fr.evaluate(joints)
    except Exception:
        # A live drag must never hard-fail on a broken rule mid-drag.
        traceback.print_exc()
    finally:
        _FOLLOW_STATE['evaluating'] = False


def _follow_on_undo_redo(*_) -> None:
    """Re-sync ruled joints after an undo or redo.

    _follow_on_ctrl_attr_changed writes a follow-ruled joint's position (and,
    for a childless twist, its orientation) from INSIDE the drag callback, so
    those joint writes are not part of the drag's own undo step. Undo restores
    the ctrl but leaves the twist joints where the callback last put them
    (Adrian, 2026-07-13: "the twist joints do not go until I move anything").
    Re-running evaluate() here brings them back in line with the just-restored
    ctrls, and refreshes so the viewport is not left showing the stale frame.

    The re-eval is NON-UNDOABLE (undo disabled without flushing the queue) so it
    never lands its own entry on the stack — otherwise the next Ctrl+Z would
    undo this re-sync instead of the user's previous action. It is a pure
    function of current scene state, so re-deriving it every undo/redo is
    idempotent and safe."""
    if not armature_exists():
        return
    try:
        from maya_tools.rigging.fabricator import armature_watch
        if armature_watch.is_muted():
            return
    except Exception:
        return
    joints = _follow_ruled_joints()
    if not joints:
        return
    prev = cmds.undoInfo(q=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    _FOLLOW_STATE['evaluating'] = True
    try:
        fr.evaluate(joints)
    except Exception:
        traceback.print_exc()
    finally:
        _FOLLOW_STATE['evaluating'] = False
        cmds.undoInfo(stateWithoutFlush=prev)
    try:
        cmds.refresh()
    except Exception:
        pass


def _follow_watch_ctrls(ctrls: list) -> None:
    """Arm the live follow-rule hook: one attributeChangedCallback per
    ctrl in THIS build. Installs the follow_rules change-listener AND
    the scene-teardown reset hook (_on_follow_scene_reset) on first use
    (module-global, never removed — flipping a dirty flag, or clearing
    it on a scene wipe, is harmless to leave registered across builds
    and across scenes; see that function's own docstring for why it
    exists). Safe to call with an empty/no-rules armature: the
    callbacks are cheap no-ops until a rule exists (see
    _follow_on_ctrl_attr_changed)."""
    if not _FOLLOW_STATE['listener_installed']:
        fr.add_change_listener(_follow_cache_dirty)
        for ev in (om.MSceneMessage.kAfterNew, om.MSceneMessage.kAfterOpen):
            om.MSceneMessage.addCallback(ev, _on_follow_scene_reset)
        # Undo/redo re-sync (Adrian, 2026-07-13): the ctrl-attr callback writes
        # twist positions OUTSIDE the drag's undo, so an undo leaves them stale
        # until the next edit. Re-evaluate (non-undoable) after every undo/redo.
        # Module-global and never removed, like the scene hooks above — it is a
        # cheap no-op whenever no Armature is present.
        for ev in ('Undo', 'Redo'):
            try:
                om.MEventMessage.addEventCallback(ev, _follow_on_undo_redo)
            except Exception:
                traceback.print_exc()
        _FOLLOW_STATE['listener_installed'] = True
    _FOLLOW_STATE['dirty'] = True   # this build may have new rules

    for ctrl in ctrls:
        if not cmds.objExists(ctrl):
            continue
        try:
            sel = om.MSelectionList()
            sel.add(ctrl)
            node = sel.getDependNode(0)
            cb_id = om.MNodeMessage.addAttributeChangedCallback(
                node, _follow_on_ctrl_attr_changed)
            _FOLLOW_STATE['watch_ids'].append(cb_id)
        except Exception:
            traceback.print_exc()


def _follow_unwatch_all() -> None:
    """Drop every tracked follow-rule watcher callback. Best-effort —
    Maya also auto-invalidates a node's own callbacks when the node is
    deleted, so a failure here (e.g. an id already gone) is harmless."""
    for cb_id in _FOLLOW_STATE['watch_ids']:
        try:
            om.MMessage.removeCallback(cb_id)
        except Exception:
            pass
    _FOLLOW_STATE['watch_ids'] = []
