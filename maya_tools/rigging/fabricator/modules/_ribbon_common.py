"""_ribbon_common — shared ribbon substrate (surface, skin, ride, deformers).

Consumed by Ribbon and the ribbon limb modules (RibbonSpine, RibbonIKArm,
RibbonIKLeg, ...). Same contract as _chain_common: helpers are
parameterized; no component owns another's name prefixes or bucket names.

FREE/PAID BOUNDARY (commercial seam split): this module is the PAID side
(the Advanced Ribbon pack's substrate). It is free to import the FREE side
(_limb_common, _chain_common) — see below — but no free-side module may
ever import this one.

History (commercial seam split): build_ribbon_segment (originally named
build_arm_ribbon_segment), ribbon_segment_chain_name,
_drive_control_joints_position_only, _drive_one_control_joint_position_only,
build_roll_joint, ROLL_AXES_UPPER, ROLL_AXES_LOWER, and
_ROLL_OFFSET_FRACTION all moved here from _limb_common.py's predecessor
free-side module — they were RibbonIKArm-owned Ribbon-family machinery
misfiled on the free side. They now live at the bottom of this file,
alongside the rest of the paid ribbon substrate.

Ride layer note (export contract): whatever rides the surface must drive the
bind joints through their TRS CHANNELS (the uvPin chain: uvPin -> multMatrix
-> decomposeMatrix -> TRS channels, jointOrient fold on a rotate-only chain,
compensating scale). NEVER drive them through offsetParentMatrix:
anim_export_runner bakes channels and severs their inputs
(disableImplicitControl); a live OPM input would be neither baked nor severed
and double-transforms the export.
"""
__author__ = "Adrian Melian"

import math

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.rigging.fabricator.modules import _chain_common as cc
from maya_tools.rigging.fabricator.modules import _limb_common as lc


def resolve_ribbon_width(opts, joints):
    w = opts.get('ribbon_width') or 0.0
    if w > 0.0:
        return float(w)
    pts = [cmds.xform(j, q=True, ws=True, t=True) for j in joints]
    total = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) or 1.0
    seg_min = min((math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)),
                  default=1.0)
    return max(0.10 * total, 0.25 * seg_min)


def side_world_per_cv(joints, cv_positions):
    """Per-CV side axis = local +Z (matrix rows 8-10) of the nearest bind joint."""
    jpts = [cmds.xform(j, q=True, ws=True, t=True) for j in joints]
    jz = []
    for j in joints:
        m = cmds.xform(j, q=True, ws=True, matrix=True)
        jz.append((m[8], m[9], m[10]))
    out = []
    for p in cv_positions:
        nearest = min(range(len(jpts)), key=lambda i: math.dist(p, jpts[i]))
        out.append(jz[nearest])
    return out


def surface_shape(surf):
    return cc.deformed_shape(surf, 'nurbsSurface')


def mfn_surface(surf):
    sel = om.MSelectionList()
    sel.add(surface_shape(surf))
    return om.MFnNurbsSurface(sel.getDagPath(0))


def build_surface(chain, center_crv, row_count, width, side_world, setup_grp,
                   up_world, component_node):
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.utils.maya import network_nodes as nn

    half = width / 2.0
    edges = []
    for sign, ename in ((+1.0, f'{chain}_ribbon_edge0_crv'),
                        (-1.0, f'{chain}_ribbon_edge1_crv')):
        e = cmds.duplicate(center_crv, name=ename)[0]
        cc.disconnect_outgoing_message(e)
        for k in range(row_count):
            sz = side_world[k]
            cmds.move(sign * half * sz[0], sign * half * sz[1], sign * half * sz[2],
                      f'{e}.cv[{k}]', relative=True, worldSpace=True)
        edges.append(e)
    # edges[0]=+Z, edges[1]=-Z. Loft -Z then +Z (nominal); the normal guard +
    # the U/V swap below make the result deterministic regardless of flags.
    surf = cmds.loft(edges[1], edges[0], ch=0, ar=0, ss=1, d=1, po=0,
                     name=f'{chain}_ribbon_surf')[0]

    # Normalize axes: we want U = across (degree 1, 2 CVs), V = along the chain
    # (degree 3). loft assigns these to U/V unpredictably; detect by degree and
    # swap with reverseSurface(direction=3) if the along-axis landed on U.
    pre = surface_shape(surf)
    du, dv = cmds.getAttr(f'{pre}.degreeUV')[0]
    if du > dv:
        cmds.reverseSurface(surf, direction=3, ch=0, rpo=1)  # swap U <-> V

    cmds.rebuildSurface(surf, ch=0, rpo=1, rt=0, end=1, kr=0, kcp=0, kc=0,
                        su=1, du=1, sv=max(1, row_count - 3), dv=3, dir=2)
    cmds.delete(edges)

    shape = surface_shape(surf)
    spans_u, spans_v = cmds.getAttr(f'{shape}.spansUV')[0]
    deg_u, deg_v = cmds.getAttr(f'{shape}.degreeUV')[0]
    v_cvs = spans_v + deg_v
    u_cvs = spans_u + deg_u
    if v_cvs != row_count or u_cvs != 2:
        raise RuntimeError(
            f'_ribbon_common: surface has {u_cvs}x{v_cvs} CVs, expected 2x{row_count}')

    cmds.parent(surf, setup_grp)
    cmds.setAttr(f'{surf}.inheritsTransform', 0)
    cmds.xform(surf, ws=True, t=(0, 0, 0))
    nodes.hide_internal(surf)
    nn.connect_message_multi(surf, component_node, 'ribbon_dg_nodes')

    # NORMAL GUARD: normal at (0.5, 0.5) must point +Y (up). Runs AFTER any
    # swap (which flips handedness). reverseSurface direction PINNED at
    # Checkpoint 1 (direction=0 reverses U/cross here).
    fn = mfn_surface(surf)
    n = fn.normal(0.5, 0.5, om.MSpace.kWorld)
    if (n.x * up_world[0] + n.y * up_world[1] + n.z * up_world[2]) < 0.0:
        cmds.reverseSurface(surf, direction=0, ch=0, rpo=1)
        fn = mfn_surface(surf)
        n = fn.normal(0.5, 0.5, om.MSpace.kWorld)
        if (n.x * up_world[0] + n.y * up_world[1] + n.z * up_world[2]) < 0.0:
            cmds.warning('_ribbon_common: normal still flipped after reverseSurface; '
                         'verify the direction flag (Checkpoint 1).')
    return surf


def surface_row_centers(surf, row_count):
    """World position of the U=0.5 midpoint of each V CV-row (control-joint
    placement -> co-located with the row it skins)."""
    centers = []
    for k in range(row_count):
        a = cmds.pointPosition(f'{surf}.cv[0][{k}]', world=True)
        b = cmds.pointPosition(f'{surf}.cv[1][{k}]', world=True)
        centers.append([(a[i] + b[i]) * 0.5 for i in range(3)])
    return centers


def skin_surface(chain, control_joints, surf, row_count, component_node,
                 mode='per_row'):
    """mode: 'per_row' hard-sets one control-joint per CV row (FK-cascade
    ribbons); 'falloff' (spine work) binds few controls with smooth
    dropoff — the end rows (V=0 and V=row_count-1) are hard-pinned to
    control_joints[0]/control_joints[-1] so tips still track their controls
    exactly. control_joints must be ordered root-to-tip matching the
    surface's V direction (both modes place/weight by list position, not by
    any inherent joint identity)."""
    from maya_tools.utils.maya import network_nodes as nn

    if mode not in ('per_row', 'falloff'):
        raise ValueError(f'skin_surface: unknown mode {mode!r}')

    skin = cmds.skinCluster(control_joints, surf, toSelectedBones=True,
                            maximumInfluences=2, dropoffRate=2,
                            name=f'{chain}_ribbon_skin')[0]
    if mode == 'per_row':
        # Hard-set BOTH U CVs of each V-row to its control-joint (roll, not shear).
        for k, cj in enumerate(control_joints):
            for u in (0, 1):
                cmds.skinPercent(skin, f'{surf}.cv[{u}][{k}]',
                                 transformValue=[(cj, 1.0)])
    else:
        # falloff (antCGi Part 18): few control joints, smooth dropoff does
        # the interpolation — this IS the spine's free twist distribution.
        # Hard-set only the END rows so tips track their controls exactly.
        for u in (0, 1):
            cmds.skinPercent(skin, f'{surf}.cv[{u}][0]',
                             transformValue=[(control_joints[0], 1.0)])
            cmds.skinPercent(skin, f'{surf}.cv[{u}][{row_count - 1}]',
                             transformValue=[(control_joints[-1], 1.0)])
    for bp in cmds.listConnections(skin, type='dagPose') or []:
        if cmds.objExists(bp):
            cmds.delete(bp)
    nn.connect_message_multi(skin, component_node, 'ribbon_skin_nodes')
    for n in cmds.listHistory(skin, pruneDagObjects=True) or []:
        if cmds.nodeType(n) in ('tweak', 'groupParts', 'groupId'):
            nn.connect_message_multi(n, component_node, 'ribbon_skin_nodes')
    for s in cmds.listConnections(skin, type='objectSet') or []:
        nn.connect_message_multi(s, component_node, 'ribbon_skin_nodes')
    return skin


def v_param_for_joint(fn, j, v_min, v_max):
    """Metric V (0..1) for a bind joint via closestPoint on the surface."""
    p = cmds.xform(j, q=True, ws=True, t=True)
    res = fn.closestPoint(om.MPoint(p[0], p[1], p[2]), space=om.MSpace.kWorld)
    v = res[2]
    rng = (v_max - v_min) or 1.0
    return min(max((v - v_min) / rng, 0.0), 1.0)


def build_ride_uvpin(chain, joints, surf, setup_grp, component_node):
    """One uvPin drives every bind joint through its TRS CHANNELS.
    Per joint the base chain multMatrix([C, pin.outputMatrix[i],
    parent.worldInverseMatrix]) -> decomposeMatrix gives the local target
    M' = C x pin x parentInv (the parentInverse term converts world to the
    joint's local space); C (constant, built here) = jointWorldBind x
    pinWorldBind^-1. TRANSLATE always comes from this base chain. ROTATE:
    joint local rotation composes R x jointOrient, so R = rot(M') x JO^-1 —
    when jointOrient is nonzero the JO^-1 fold lives on a SEPARATE
    rotate-only chain; folding it into the base chain would rotate the
    translate row and corrupt position (review finding, Task 3). Each
    decomposeMatrix takes joint.rotateOrder -> inputRotateOrder so non-xyz
    joints land correctly. setup_grp is accepted only for call-site symmetry
    (DG-only nodes need no DAG parent). Scale channels ARE connected
    (compensating; normalize-to-1 contract per CP1): dm.outputScaleX/Y/Z ->
    j.scaleX/Y/Z per-child (never the compound .scale), reproducing the
    retired follicle scaleConstraint's world-scale normalization under a
    scaled parent. The volume layer (Task 7) still owns scaleY/Z there,
    overriding with force=True; scaleX has no other writer and must survive
    on this wire. Every ride joint also gets segmentScaleCompensate forced
    off: left on, it double-compensates against the wire above and world
    scale stops normalizing evenly down the chain.

    Preconditions, enforced BEFORE the uvPin node (or any other scene node)
    is created, and before any joint is touched: rotateAxis == (0,0,0) and
    bind scale == (1,1,1), else RuntimeError naming the joint — a validation
    raise here leaves zero scene nodes created (fully mutation-free raise).
    Bind matrices are captured in a first pass, before SSC is flipped on ANY
    joint (an already-flipped ancestor could otherwise move a descendant's
    world matrix between reads) — belt-and-braces, since the scale guard
    above already rules that move out. NEVER wire offsetParentMatrix (see
    module docstring: export bake contract)."""
    from maya_tools.utils.maya import network_nodes as nn

    # setup_grp: unused here (kept for call-site symmetry with the rest of
    # the substrate's builders) -- these are DG-only nodes, no DAG parent
    # needed.

    surf_shape = surface_shape(surf)
    fn = mfn_surface(surf)
    v_min, v_max = cmds.getAttr(f'{surf_shape}.minMaxRangeV')[0]

    # Validate BEFORE any mutation — including before the uvPin node itself
    # is created — so a raise here is fully mutation-free (no stray nodes).
    tol = 1e-6
    for j in joints:
        ra = cmds.getAttr(f'{j}.rotateAxis')[0]
        if any(abs(a) > tol for a in ra):
            raise RuntimeError(
                f'_ribbon_common: joint {j!r} has non-zero rotateAxis {ra}; '
                'the uvPin ride requires rotateAxis == (0,0,0).')
        sc = cmds.getAttr(f'{j}.scale')[0]
        if any(abs(s - 1.0) > tol for s in sc):
            raise RuntimeError(
                f'_ribbon_common: joint {j!r} has non-unit bind scale {sc}; '
                'the uvPin ride requires scale == (1,1,1).')

    pin = cmds.createNode('uvPin', name=f'{chain}_ribbon_uvpin')
    # Cosmetic-only: Maya prints "No (valid) geometry specified" on this pin
    # interactively right after creation (headless-clean; a UI eager-eval
    # artifact, not a connect-order bug here). Deferred per Step 4.4.
    cmds.connectAttr(f'{surf_shape}.worldSpace[0]', f'{pin}.deformedGeometry')
    orig = cmds.listRelatives(surf, shapes=True, fullPath=True) or []
    orig = [s for s in orig if cmds.getAttr(f'{s}.intermediateObject')]
    if orig:
        cmds.connectAttr(f'{orig[0]}.local', f'{pin}.originalGeometry')
    cmds.setAttr(f'{pin}.normalizedIsoParms', 1)
    nn.connect_message_multi(pin, component_node, 'ribbon_dg_nodes')

    # Pass 1: capture every joint's bind world matrix and pin outputMatrix
    # snapshot (validation already happened above). No joint is mutated in
    # this pass.
    captured = []
    for i, j in enumerate(joints):
        bj = j.split('|')[-1].split(':')[-1]
        v_norm = v_param_for_joint(fn, j, v_min, v_max)
        cmds.setAttr(f'{pin}.coordinate[{i}].coordinateU', 0.5)
        cmds.setAttr(f'{pin}.coordinate[{i}].coordinateV', v_norm)

        jw = om.MMatrix(cmds.xform(j, q=True, ws=True, matrix=True))
        pw = om.MMatrix(cmds.getAttr(f'{pin}.outputMatrix[{i}]'))
        captured.append((j, bj, jw, pw))

    # THEN flip SSC on every ride joint, only once every capture above is done.
    for j, _bj, _jw, _pw in captured:
        cmds.setAttr(f'{j}.segmentScaleCompensate', 0)

    # Pass 2: build the wiring from the CAPTURED matrices.
    for i, (j, bj, jw, pw) in enumerate(captured):
        # C = jointWorldBind x pinWorldBind^-1  (world offset at bind)
        c_mat = jw * pw.inverse()

        # Base chain: M' = C x pin x parentInv. Translate ALWAYS reads here.
        mm = cmds.createNode('multMatrix', name=f'{bj}_ribbon_pin_mm')
        cmds.setAttr(f'{mm}.matrixIn[0]', list(c_mat), type='matrix')
        cmds.connectAttr(f'{pin}.outputMatrix[{i}]', f'{mm}.matrixIn[1]')
        parent = (cmds.listRelatives(j, parent=True, fullPath=True) or [None])[0]
        if parent:
            cmds.connectAttr(f'{parent}.worldInverseMatrix[0]', f'{mm}.matrixIn[2]')

        dm = cmds.createNode('decomposeMatrix', name=f'{bj}_ribbon_pin_dm')
        cmds.connectAttr(f'{mm}.matrixSum', f'{dm}.inputMatrix')
        cmds.connectAttr(f'{j}.rotateOrder', f'{dm}.inputRotateOrder')
        cmds.connectAttr(f'{dm}.outputTranslate', f'{j}.translate')
        # Compensating scale (contract a, CP1): per-child, off the BASE dm —
        # never the rotate-only dm_r, never the compound .scale (the volume
        # layer force=True-overrides scaleY/Z later; scaleX has no other
        # writer and must survive here).
        cmds.connectAttr(f'{dm}.outputScaleX', f'{j}.scaleX')
        cmds.connectAttr(f'{dm}.outputScaleY', f'{j}.scaleY')
        cmds.connectAttr(f'{dm}.outputScaleZ', f'{j}.scaleZ')
        new_nodes = [mm, dm]

        jo = cmds.getAttr(f'{j}.jointOrient')[0]
        if max(abs(a) for a in jo) < 1e-6:
            # Armature-contract joints (jointOrient collapsed to zero):
            # R = rot(M') directly.
            cmds.connectAttr(f'{dm}.outputRotate', f'{j}.rotate')
        else:
            # Legacy oriented joints: R = rot(M') x JO^-1, scoped to a
            # rotate-only chain — never folded into the base chain (it
            # would rotate the translate row and corrupt position).
            eu = om.MEulerRotation(math.radians(jo[0]), math.radians(jo[1]),
                                   math.radians(jo[2]))
            mm_r = cmds.createNode('multMatrix', name=f'{bj}_ribbon_pin_rot_mm')
            cmds.connectAttr(f'{mm}.matrixSum', f'{mm_r}.matrixIn[0]')
            cmds.setAttr(f'{mm_r}.matrixIn[1]', list(eu.asMatrix().inverse()),
                         type='matrix')
            dm_r = cmds.createNode('decomposeMatrix',
                                   name=f'{bj}_ribbon_pin_rot_dm')
            cmds.connectAttr(f'{mm_r}.matrixSum', f'{dm_r}.inputMatrix')
            cmds.connectAttr(f'{j}.rotateOrder', f'{dm_r}.inputRotateOrder')
            cmds.connectAttr(f'{dm_r}.outputRotate', f'{j}.rotate')
            new_nodes += [mm_r, dm_r]

        for n in new_nodes:
            nn.connect_message_multi(n, component_node, 'ribbon_dg_nodes')
    return pin


def lock_joint_orient_to(joint, driver, component_node, bucket='ribbon_dg_nodes'):
    """Rigidly lock joint's ROTATION to driver's world orientation, leaving
    joint's TRANSLATE (and any existing SCALE wiring) exactly as-is.

    Fix for CP3 Manny feedback: RibbonSpine's falloff skin hard-pins the
    END ROW POSITIONS to the end control joints, but build_ride_uvpin's
    rotate path still reads the uvPin's surface-tangent frame at the V
    boundary -- a frame shaped by the row ADJACENT to that boundary (the
    "falloff-blended penultimate row": ~95% the same end control + a
    trickle from its neighbor, so it does not rotate perfectly rigidly with
    the end control). Rotating an end ctrl leaves that end's bind joint
    POSITION fixed (correct -- the boundary row is 100% hard-pinned) but
    its computed ORIENTATION drifts away from a pure rigid copy of the end
    control joint's own rotation (measured on a 7-joint test spine:
    hip_ctrl.rz 30 gives the root bind joint an actual world-rotation of
    ~28.53 degrees, not 30 -- a ~1.47 degree mismatch that grows with pose;
    the tip end is unaffected, and symmetrically for the chest/tip pair).
    Anything parented under that end joint (clavicles/neck off the tip,
    legs off the root/pelvis) inherits the drift as an unwanted arc/swing.

    The end bind joints' rotation should instead come rigidly from their
    end CONTROL joints (which already follow the hip/chest ctrls through
    drive_control_joints) -- the standard spine contract. Call AFTER
    build_ride_uvpin, once per end joint, with driver = the matching end
    control joint. Do NOT apply to interior joints (their rotation is the
    free twist distribution) or to generic Ribbon (a tail tip SHOULD follow
    the surface frame).

    Same math as build_ride_uvpin's rotate path, with driver.worldMatrix[0]
    standing in for the pin's outputMatrix: C_rot = jointWorldBind x
    driverWorldBind^-1 (captured at build/bind pose, before any node is
    created or connection touched); base chain M' = C_rot x driverWorld x
    parentInv; ROTATE = rot(M') directly when jointOrient is zero, else
    folded through JO^-1 on a separate rotate-only chain exactly like the
    ride (folding JO^-1 into the base chain would corrupt a translate row
    this helper does not even drive, but the convention is kept identical
    for consistency). joint.rotateOrder wires into inputRotateOrder on both
    decomposeMatrix nodes, matching the ride.

    Precondition: joint.rotateAxis == (0,0,0), else RuntimeError before any
    node is created (mutation-free raise) -- identical to build_ride_uvpin's
    guard. Bind SCALE is deliberately NOT re-validated here: build_ride_uvpin
    already did, immediately upstream of every call site, and this helper
    never touches scale.

    Whatever currently drives joint.rotate (the ride's base or rotate-only
    decomposeMatrix outputRotate) is disconnected first, cleanly, the same
    compound-attribute way the ride's own wiring was connected (and the way
    unbuild's channel sweep already queries/disconnects '.rotate') -- so the
    pin's dm/dm_r nodes stay exactly as build_ride_uvpin left them (tracked
    in their own bucket, just no longer feeding .rotate) and are still swept
    on unbuild. The new nodes are tracked into `bucket` (ribbon_dg_nodes,
    same as the ride) so they die with it too."""
    from maya_tools.utils.maya import network_nodes as nn

    ra = cmds.getAttr(f'{joint}.rotateAxis')[0]
    tol = 1e-6
    if any(abs(a) > tol for a in ra):
        raise RuntimeError(
            f'_ribbon_common: joint {joint!r} has non-zero rotateAxis {ra}; '
            'lock_joint_orient_to requires rotateAxis == (0,0,0).')

    bj = joint.split('|')[-1].split(':')[-1]

    # Capture bind-pose world matrices BEFORE touching any connection.
    jw = om.MMatrix(cmds.xform(joint, q=True, ws=True, matrix=True))
    dw = om.MMatrix(cmds.xform(driver, q=True, ws=True, matrix=True))
    c_mat = jw * dw.inverse()

    # Disconnect whatever currently drives .rotate (the ride's base or
    # rotate-only decomposeMatrix outputRotate) -- same compound-attribute
    # idiom the ride connected it with, and the same one unbuild's sweep
    # already relies on to find it.
    for src in cmds.listConnections(f'{joint}.rotate', s=True, d=False,
                                    plugs=True) or []:
        cmds.disconnectAttr(src, f'{joint}.rotate')

    mm = cmds.createNode('multMatrix', name=f'{bj}_ribbon_olock_mm')
    cmds.setAttr(f'{mm}.matrixIn[0]', list(c_mat), type='matrix')
    cmds.connectAttr(f'{driver}.worldMatrix[0]', f'{mm}.matrixIn[1]')
    parent = (cmds.listRelatives(joint, parent=True, fullPath=True) or [None])[0]
    if parent:
        cmds.connectAttr(f'{parent}.worldInverseMatrix[0]', f'{mm}.matrixIn[2]')

    dm = cmds.createNode('decomposeMatrix', name=f'{bj}_ribbon_olock_dm')
    cmds.connectAttr(f'{mm}.matrixSum', f'{dm}.inputMatrix')
    cmds.connectAttr(f'{joint}.rotateOrder', f'{dm}.inputRotateOrder')
    new_nodes = [mm, dm]

    jo = cmds.getAttr(f'{joint}.jointOrient')[0]
    if max(abs(a) for a in jo) < 1e-6:
        # Armature-contract joints (jointOrient collapsed to zero):
        # R = rot(M') directly.
        cmds.connectAttr(f'{dm}.outputRotate', f'{joint}.rotate')
    else:
        # Legacy oriented joints: R = rot(M') x JO^-1, scoped to a
        # rotate-only chain -- never folded into the base chain (it would
        # rotate a translate row if this helper drove one; kept identical
        # to the ride's convention for consistency regardless).
        eu = om.MEulerRotation(math.radians(jo[0]), math.radians(jo[1]),
                               math.radians(jo[2]))
        mm_rot = cmds.createNode('multMatrix', name=f'{bj}_ribbon_olock_rot_mm')
        cmds.connectAttr(f'{mm}.matrixSum', f'{mm_rot}.matrixIn[0]')
        cmds.setAttr(f'{mm_rot}.matrixIn[1]', list(eu.asMatrix().inverse()),
                     type='matrix')
        dm_rot = cmds.createNode('decomposeMatrix',
                                 name=f'{bj}_ribbon_olock_rot_dm')
        cmds.connectAttr(f'{mm_rot}.matrixSum', f'{dm_rot}.inputMatrix')
        cmds.connectAttr(f'{joint}.rotateOrder', f'{dm_rot}.inputRotateOrder')
        cmds.connectAttr(f'{dm_rot}.outputRotate', f'{joint}.rotate')
        new_nodes += [mm_rot, dm_rot]

    for n in new_nodes:
        nn.connect_message_multi(n, component_node, bucket)
    return dm


BOARD_ATTRS = (
    # (name, default, min, max) — channel-box order. enable gates sine+jiggle
    # (block first, polish later); twist/volume are pose dials, ungated.
    ('enable',          0.0, 0.0, 1.0),
    ('twist_root',      0.0, None, None),   # degrees -> twist handle startAngle
    ('twist_tip',       0.0, None, None),   # degrees -> twist handle endAngle
    ('sine_amplitude',  0.0, None, None),
    ('sine_wavelength', 2.0, 0.01, None),
    ('sine_orientation', 0.0, None, None),  # degrees, spins the wave plane
    ('jiggle_amount',   0.5, 0.0, 2.0),     # -> jiggle.jiggleWeight
    ('jiggle_stiffness', 0.25, 0.0, 1.0),   # -> jiggle.stiffness
    ('jiggle_weight',   0.05, 0.0, 1.0),    # -> jiggle.damping (antCGi's own
                                            #    rename of his ambiguous dial)
    ('volume',          0.0, 0.0, 1.0),
)


def add_board_attrs(ctrl, component_node, role='ribbon_settings_ctrl'):
    """Add the 10 BOARD_ATTRS (addAttr loop, min/max handling) to an EXISTING
    ctrl, then tag_ctrl(ctrl, role, ..., joint_index=-1).

    Shared by build_settings_ctrl (its own dedicated, brand-new, untagged
    settings ctrl) and any component that instead hangs the board on an
    already-tagged control of its own (e.g. RibbonSpine's cog_ctrl, tagged
    'spine_cog_ctrl' before this is ever called). fab_role is a single
    scalar attr -- tag_ctrl's setAttr overwrites it, it does not accumulate
    -- and that role is a load-bearing identity other systems key off by
    exact string match (pose library, anim_root_motion.find_world_ctrl /
    find_ik_end_ctrls, marking menus). So: if `ctrl` already carries a
    DIFFERENT role, that existing role wins and this leaves it alone,
    adding only the attrs. The board attrs need no role tag to function --
    every consumer (wire_board, build_jiggle, build_volume, capture_board,
    apply_board) addresses them by literal attribute name on whatever node
    is handed in as `settings`.
    """
    from maya_tools.rigging.fabricator import nodes

    for attr, default, lo, hi in BOARD_ATTRS:
        kw = {'ln': attr, 'at': 'double', 'dv': default, 'k': True}
        if lo is not None:
            kw['min'] = lo
        if hi is not None:
            kw['max'] = hi
        cmds.addAttr(ctrl, **kw)
    existing_role = (cmds.getAttr(f'{ctrl}.fab_role')
                     if cmds.attributeQuery('fab_role', node=ctrl, exists=True)
                     else None)
    if existing_role and existing_role != role:
        return
    nodes.tag_ctrl(ctrl, role, component_node=component_node, joint_index=-1)


def build_settings_ctrl(chain, parent_ctrl, opts, cv_block, component_node,
                        role='ribbon_settings_ctrl'):
    """Locked-TRS 'cog' ctrl carrying BOARD_ATTRS. Parented under parent_ctrl."""
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
    from maya_tools.rigging.fabricator.modules.world import _apply_color

    name = f'{chain}_ribbon_settings_ctrl'
    buf = cmds.createNode('transform', name=f'{name}_offset')
    if cmds.objExists(parent_ctrl):
        cmds.matchTransform(buf, parent_ctrl, position=True, rotation=True,
                            scale=False)
        cmds.parent(buf, parent_ctrl)
    ctrl = com.build_shape('cog', name)
    cmds.parent(ctrl, buf)
    for a in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
        cmds.setAttr(f'{ctrl}.{a}', 0)
    for a in ('sx', 'sy', 'sz'):
        cmds.setAttr(f'{ctrl}.{a}', 1)
    _apply_color(ctrl, opts.get('ctrl_color', 'yellow'))
    for a in ('translateX', 'translateY', 'translateZ', 'rotateX', 'rotateY',
              'rotateZ', 'scaleX', 'scaleY', 'scaleZ', 'visibility'):
        cmds.setAttr(f'{ctrl}.{a}', lock=True, keyable=False, channelBox=False)
    add_board_attrs(ctrl, component_node, role)
    cc._restore_shape(com, ctrl, cv_block.get(ctrl))
    return ctrl


def capture_board(settings):
    """Snapshot BOARD_ATTRS values off a settings ctrl into a plain dict
    (the persistence payload a component's unbuild carries forward)."""
    board = {}
    for attr, _default, _lo, _hi in BOARD_ATTRS:
        if cmds.attributeQuery(attr, node=settings, exists=True):
            board[attr] = cmds.getAttr(f'{settings}.{attr}')
    return board


def apply_board(settings, saved):
    """Restore a capture_board() snapshot onto a (rebuilt) settings ctrl."""
    saved = saved or {}
    for attr, _default, _lo, _hi in BOARD_ATTRS:
        if attr in saved and cmds.attributeQuery(attr, node=settings, exists=True):
            try:
                cmds.setAttr(f'{settings}.{attr}', float(saved[attr]))
            except Exception:
                pass


def build_sculpt_target(chain, surf, setup_grp, effect, component_node, joints):
    """Rest-pose duplicate of the live surface carrying one nonlinear deformer
    ('twist' or 'sine'). Returns (copy, deformer, handle).

    joints: the component's bind chain, root-to-tip. RIDER (Task 8, ribbon-limb
    reuse finding): nonLinear() auto-fits the handle's position and a UNIFORM
    scale to the copy's bounding box (empirically verified orientation-
    agnostic — it uses the single largest bbox half-extent, which for a
    ribbon shape is the along-chain length regardless of which world axis
    that runs along), but leaves rotation at identity, so the deformer's
    gradient axis (local +Y — twist rotates around it; sine's wavelength
    runs along it; sine_orientation spins the plane around it) defaults to
    world Y. That is bit-exact correct for a vertical chain, but wrong for a
    horizontal one: the whole surface then sits near gradient-parameter 0
    and both ends receive nearly the same effect (measured: a twist_tip
    dial leaking motion into the root row on a horizontal +X chain). Fix:
    aimConstraint the handle's +Y at a temp locator placed along the
    root->tip direction (deleted right after — the generic TA technique),
    up vector from cc.resolve_axes(joints); delete leaves the solved
    rotation baked as a static value, translate/scale untouched. A residual
    roll around the now-correct Y axis is harmless: verified empirically
    that changing ONLY rotateY (which is all the live sine_orientation wire
    ever touches) never moves where local Y itself points, for either rx/rz
    combination this solve produces — so wire_board's live roll and this
    static aim never fight each other."""
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.utils.maya import network_nodes as nn

    copy = cmds.duplicate(surf, name=f'{chain}_ribbon_{effect}_bsh')[0]
    cc.disconnect_outgoing_message(copy)
    # A duplicate of a skinned surface arrives at rest but may carry copied
    # history; nuke it so the target is a clean rest shape.
    cmds.delete(copy, constructionHistory=True)
    try:
        cmds.parent(copy, setup_grp)
    except (RuntimeError, ValueError):
        pass
    cmds.setAttr(f'{copy}.inheritsTransform', 0)
    nodes.hide_internal(copy)

    deformer, handle = cmds.nonLinear(copy, type=effect)
    deformer = cmds.rename(deformer, f'{chain}_ribbon_{effect}_def')
    handle = cmds.rename(handle, f'{chain}_ribbon_{effect}_def_handle')
    # nonLinear() already auto-fits the handle to the copy's bounding box
    # (correctly spans the surface's V/length axis wherever the chain sits
    # in world space). Do NOT matchTransform the handle to surf's pivot —
    # surf's pivot is reset to world origin in build_surface, so that drags
    # the handle to the origin and mis-spans it on any off-origin chain
    # (measured: a twist_tip dial leaked into the root row). cmds.parent
    # below preserves the auto-fit world placement (Maya's default parent
    # behavior keeps world transform).
    cmds.parent(handle, setup_grp)
    nodes.hide_internal(handle)

    # RIDER: aim the handle's +Y down the chain (root -> tip) instead of
    # leaving it at the auto-fit's world-Y default. See docstring above.
    root_pos = cmds.xform(joints[0], q=True, ws=True, t=True)
    tip_pos = cmds.xform(joints[-1], q=True, ws=True, t=True)
    direction = om.MVector(tip_pos[0] - root_pos[0], tip_pos[1] - root_pos[1],
                          tip_pos[2] - root_pos[2])
    if direction.length() > 1e-6:
        direction = direction.normal()
        _, _, up_world = cc.resolve_axes(joints)
        handle_pos = cmds.xform(handle, q=True, ws=True, t=True)
        aim_loc = cmds.spaceLocator(name=f'{chain}_ribbon_{effect}_aim_tmp')[0]
        cmds.xform(aim_loc, ws=True, t=(handle_pos[0] + direction.x,
                                        handle_pos[1] + direction.y,
                                        handle_pos[2] + direction.z))
        ac = cmds.aimConstraint(aim_loc, handle, aimVector=(0, 1, 0),
                                upVector=(1, 0, 0), worldUpType='vector',
                                worldUpVector=up_world, maintainOffset=False)
        cmds.delete(ac)
        cmds.delete(aim_loc)
    # else: degenerate zero-length chain (min_joints on every ribbon
    # component guarantees distinct root/tip in practice) -- leave the
    # auto-fit's identity rotation as-is rather than aim at a null vector.

    for n in (copy, deformer, handle):
        nn.connect_message_multi(n, component_node, 'ribbon_board_nodes')
    return copy, deformer, handle


def add_board_blend(chain, surf, targets, skin, component_node):
    """blendShape the sculpt targets onto the live surface, evaluated BEFORE
    the skin (rest-space sculpt injected upstream; the skin then poses
    base+sculpt together — antCGi's literal Part 27 command). Corrected
    doctrine (was wrong twice): a POST-skin blendShape with a constant
    weight-1.0 target REPLACES the posed skin output entirely and the rig
    goes dead while looking fine at bind pose (measured: control joint +2.0
    -> 0.0 surface displacement with the earlier post-skin ordering).
    reorderDeformers is load-bearing; validated, not trusted."""
    from maya_tools.utils.maya import network_nodes as nn

    bs = cmds.blendShape(*targets, surf, name=f'{chain}_ribbon_board_blend')[0]
    # DOCTRINE (corrected twice, final, empirically proven): the board blend
    # must evaluate BEFORE the skin. reorderDeformers(A, B, geo) places A
    # AFTER B, so placing the SKIN after the BS puts the rest-space sculpt
    # upstream; the skin then poses base+sculpt together (antCGi's literal
    # command in Part 27; Part 18's "else control joints stop working").
    # A POST-skin blendShape with a weight-1.0 target REPLACES the skin
    # output entirely (out = in + w*(tgt - rest_orig) with in == posed) —
    # the rig goes dead while looking fine at bind pose.
    cmds.reorderDeformers(skin, bs, surf)
    order = [n for n in (cmds.listHistory(surf, pruneDagObjects=True) or [])
             if cmds.nodeType(n) in ('skinCluster', 'blendShape')]
    # listHistory is downstream-first: the SKIN must precede bs in this list
    # (skin most-downstream). Raise if the bs ended up post-skin.
    if order.index(bs) < order.index(skin):
        raise RuntimeError(
            f'{chain}: board blendShape evaluates after the skin — it would '
            'replace the posed surface (dead rig); reorder failed.')
    nn.connect_message_multi(bs, component_node, 'ribbon_board_nodes')
    return bs


def wire_board(settings, bs, twist_handle_deformer, sine_handle_deformer,
               sine_handle, targets_index):
    """targets_index: {'twist': i, 'sine': j} — blendShape weight indices.
    twist weight = 1 (identity at zero angles); sine weight = enable."""
    twist_def, sine_def = twist_handle_deformer, sine_handle_deformer
    cmds.setAttr(f'{bs}.weight[{targets_index["twist"]}]', 1.0)
    cmds.connectAttr(f'{settings}.enable',
                     f'{bs}.weight[{targets_index["sine"]}]')
    cmds.connectAttr(f'{settings}.twist_root', f'{twist_def}.startAngle')
    cmds.connectAttr(f'{settings}.twist_tip', f'{twist_def}.endAngle')
    cmds.connectAttr(f'{settings}.sine_amplitude', f'{sine_def}.amplitude')
    cmds.connectAttr(f'{settings}.sine_wavelength', f'{sine_def}.wavelength')
    cmds.connectAttr(f'{settings}.sine_orientation', f'{sine_handle}.rotateY')


def build_jiggle(chain, surf, row_count, skin, settings, component_node):
    # Stack doctrine (corrected, Task 6 panel): board blend PRE-skin, skin
    # second, jiggle MOST-downstream (a spatial sim on the posed surface —
    # post-skin is correct for it; it is not a blendShape, no orig-snap).
    # reorderDeformers(A, B, geo) places A AFTER B. Step 7.3's downstream-
    # first assert [jiggle, skinCluster, blendShape] is the authority.
    from maya_tools.utils.maya import network_nodes as nn

    jig = cmds.deformer(surf, type='jiggle', name=f'{chain}_ribbon_jiggle')[0]
    cmds.connectAttr('time1.outTime', f'{jig}.currentTime')  # never auto-wired
    # Auto-paint: quadratic root->tip falloff, both U columns per row.
    for k in range(row_count):
        t = k / (row_count - 1) if row_count > 1 else 1.0
        for u in (0, 1):
            cmds.percent(jig, f'{surf}.cv[{u}][{k}]', value=t * t)
    # .enable is a 3-way ENUM (0=Enable 1=Disable 2=After-stop): SET it, never
    # float-connect (1.0 lands on Disable and silently kills the deformer).
    cmds.setAttr(f'{jig}.enable', 0)
    cmds.setAttr(f'{jig}.forceOnTangent', 1)   # reacts along travel (antCGi P27)
    cmds.setAttr(f'{jig}.forceAlongNormal', 1)
    cmds.connectAttr(f'{settings}.enable', f'{jig}.envelope')
    cmds.connectAttr(f'{settings}.jiggle_amount', f'{jig}.jiggleWeight')
    cmds.connectAttr(f'{settings}.jiggle_stiffness', f'{jig}.stiffness')
    cmds.connectAttr(f'{settings}.jiggle_weight', f'{jig}.damping')
    # Stack order: jiggle most-downstream (reads the posed surface):
    # place jig AFTER the skin.
    cmds.reorderDeformers(jig, skin, surf)
    nn.connect_message_multi(jig, component_node, 'ribbon_board_nodes')
    return jig


def build_volume(chain, surf, setup_grp, joints, settings, rest_len,
                 component_node, root_matrix_plug=None):
    """Returns the resolved rest_len (persist it; rebuilds must not re-measure
    a posed scene). root_matrix_plug: worldMatrix[0] plug of the module's
    parent/root ctrl; when given, the rest length scales live with the root
    (uniform-scale assumption) so global rig scale is not read as stretch.
    Per joint the volume factor MULTIPLIES the ride dm's compensating
    outputScaleY/Z (SSC is off on ride joints; a shared replacement value
    would compound v^k down a parented chain)."""
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.utils.maya import network_nodes as nn

    dup = cmds.duplicateCurve(f'{surf}.u[0.5]', ch=1, rn=0, local=0,
                              name=f'{chain}_ribbon_vol_crv')
    iso, cfsi = dup[0], dup[1]
    cmds.parent(iso, setup_grp)
    # DOUBLE-SCALE FIX (Task 7 Step 7.3 empirical finding, matches the
    # existing surf/crv idiom in this module): iso's shape is a LIVE sample
    # of surf.worldSpace (already correctly reflects any control-joint-driven
    # stretch, including stretch induced by an ancestor scale propagating to
    # the control joints). Left plain-parented under setup_grp, iso's own
    # inheritsTransform would ALSO apply that same ancestor scale a SECOND
    # time on top of the already-scaled sample (measured: root scaled 2x
    # read as a 4x arc-length change, not 2x) -- breaking the
    # root_matrix_plug scale-invariance contract. Detach exactly like surf/
    # crv: identity transform, world position pinned to origin.
    cmds.setAttr(f'{iso}.inheritsTransform', 0)
    cmds.xform(iso, ws=True, t=(0, 0, 0))
    nodes.hide_internal(iso)
    iso_shape = cc.deformed_shape(iso, 'nurbsCurve')
    ci = cmds.createNode('curveInfo', name=f'{chain}_ribbon_vol_len')
    cmds.connectAttr(f'{iso_shape}.worldSpace[0]', f'{ci}.inputCurve')
    if not rest_len:
        rest_len = float(cmds.getAttr(f'{ci}.arcLength')) or 1.0

    ratio = cmds.createNode('multiplyDivide', name=f'{chain}_ribbon_vol_ratio')
    cmds.setAttr(f'{ratio}.operation', 2)
    cmds.connectAttr(f'{ci}.arcLength', f'{ratio}.input1X')
    tracked_extra = []
    if root_matrix_plug:
        # Live denominator: rest_len x rootScaleX. Uniform-scale assumption.
        rdm = cmds.createNode('decomposeMatrix', name=f'{chain}_ribbon_vol_rootdm')
        cmds.connectAttr(root_matrix_plug, f'{rdm}.inputMatrix')
        rmul = cmds.createNode('multDoubleLinear', name=f'{chain}_ribbon_vol_rest')
        cmds.setAttr(f'{rmul}.input1', rest_len)
        cmds.connectAttr(f'{rdm}.outputScaleX', f'{rmul}.input2')
        cmds.connectAttr(f'{rmul}.output', f'{ratio}.input2X')
        tracked_extra += [rdm, rmul]
    else:
        cmds.setAttr(f'{ratio}.input2X', rest_len)
    sq = cmds.createNode('multiplyDivide', name=f'{chain}_ribbon_vol_sqrt')
    cmds.setAttr(f'{sq}.operation', 3)
    cmds.connectAttr(f'{ratio}.outputX', f'{sq}.input1X')
    cmds.setAttr(f'{sq}.input2X', 0.5)
    inv = cmds.createNode('multiplyDivide', name=f'{chain}_ribbon_vol_inv')
    cmds.setAttr(f'{inv}.operation', 2)
    cmds.setAttr(f'{inv}.input1X', 1.0)
    cmds.connectAttr(f'{sq}.outputX', f'{inv}.input2X')
    blend = cmds.createNode('blendTwoAttr', name=f'{chain}_ribbon_vol_blend')
    cmds.setAttr(f'{blend}.input[0]', 1.0)
    cmds.connectAttr(f'{inv}.outputX', f'{blend}.input[1]')
    cmds.connectAttr(f'{settings}.volume', f'{blend}.attributesBlender')
    for j in joints:
        bj = j.split('|')[-1].split(':')[-1]
        dm = f'{bj}_ribbon_pin_dm'
        if not cmds.objExists(dm):
            raise RuntimeError(f'build_volume: ride dm missing for {j} — '
                               'volume layers on the uvPin ride, build it first.')
        vm = cmds.createNode('multiplyDivide', name=f'{bj}_ribbon_vol_mult')
        cmds.connectAttr(f'{dm}.outputScaleY', f'{vm}.input1X')
        cmds.connectAttr(f'{blend}.output', f'{vm}.input2X')
        cmds.connectAttr(f'{dm}.outputScaleZ', f'{vm}.input1Y')
        cmds.connectAttr(f'{blend}.output', f'{vm}.input2Y')
        cmds.connectAttr(f'{vm}.outputX', f'{j}.scaleY', force=True)
        cmds.connectAttr(f'{vm}.outputY', f'{j}.scaleZ', force=True)
        # scaleX stays on the ride dm's compensating wire untouched.
        tracked_extra.append(vm)
    for nd in [iso, cfsi, ci, ratio, sq, inv, blend] + tracked_extra:
        nn.connect_message_multi(nd, component_node, 'ribbon_board_nodes')
    return rest_len


def _drive_one_control_joint_position_only(dg_bucket, offset_ctrl, cj, component_node):
    """One (driver, control_joint) pair of _drive_control_joints_position_only
    (below) — factored out so build_ribbon_segment can call it per-item
    (needed once P3 substitutes a roll joint as the driver for just the
    segment's twisting end, without duplicating the null-pair/constraint
    plumbing)."""
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.utils.maya import network_nodes as nn
    cj_off, cj_con = nodes.build_null_pair(cj)
    cmds.parentConstraint(offset_ctrl, cj_con, maintainOffset=True)
    cmds.parentConstraint(cj_con, cj, maintainOffset=True)
    for n in (cj_off, cj_con):
        nn.connect_message_multi(n, component_node, dg_bucket)


def _drive_control_joints_position_only(dg_bucket, offset_ctrls, control_joints,
                                        component_node):
    """Like _chain_common.drive_control_joints, but position+rotation ONLY
    — the ribbon's skin-influence control joints keep their SCALE locked
    at (1,1,1), never constrained from the driver.

    Measured double-transform bug (HARD CONSTRAINT: no double-transform on
    global rig scale): start_joint/end_joint/mid ctrls are DAG descendants
    of the rig root, so their WORLD SCALE already carries any ancestor
    (global rig) scale. If that same scale were ALSO copied onto the skin-
    influence control joints' OWN .scale (via scaleConstraint, as
    _chain_common.drive_control_joints does for RibbonSpine's plain-
    transform ctrls), the skinCluster deforms `surf` from BOTH the
    influence's already-scaled WORLD POSITION *and* its own inflated
    SCALE — measured: a 2x root scale produced a ~4x ribbon arc-length
    reading (2x from position spacing, 2x again from skin-applied
    influence scale). build_volume's root_matrix_plug ratio already
    correctly compensates for the single, position-driven factor of
    scale; control-joint SCALE must not also carry it. Stretch/volume
    are driven entirely by control-joint POSITION spacing (mid ctrl
    translation, or a genuine ancestor scale change) — never by influence
    scale."""
    for offset_ctrl, cj in zip(offset_ctrls, control_joints):
        _drive_one_control_joint_position_only(dg_bucket, offset_ctrl, cj, component_node)


def ribbon_segment_chain_name(start_joint, end_joint):
    """Deterministic name prefix for a segment's nodes, derived only from
    the two driver joint names. Both build_ribbon_segment (below) and
    RibbonIKArm.unbuild (which has no access to build()'s returned dict — it's a
    separate call, possibly in a separate session) call this SAME function
    so node names never drift between the two."""
    return f'{lc.short_name(start_joint)}_{lc.short_name(end_joint)}_ribbon'


# ─── P3: antCGi aim+IK roll joint (ROLL-METHOD.md §2) ────────────────────
#
# Per-bone-segment axis sets for the roll joint's aim constraint. UPPER: the
# roll sits at bone_start (e.g. shoulder) and aims down-bone at bone_end —
# antCGi's femur case (aim local +X, up local -Z). LOWER/world-oriented: the
# same construction but for a bone whose roll axis reads as "world-
# oriented" in antCGi's rig (aim local +Y, up local +X) — his ankle/
# metacarpus case, our forearm/wrist. Both are (aim_vector, up_vector)
# tuples fed straight to cmds.aimConstraint.
ROLL_AXES_UPPER = ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0))
ROLL_AXES_LOWER = ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0))

# follower's perpendicular offset from bone_start, as a FRACTION of the
# bone's own length — scales sensibly across differently-scaled rigs
# (same spirit as quad_leg.py's seg_len*0.3 switch-ctrl offset) rather than
# antCGi's hardcoded "-5 in Z", which was tuned to his specific rig scale.
_ROLL_OFFSET_FRACTION = 0.15


def build_roll_joint(bone_start, bone_end, driver_parent, axes, side, component_node):
    """antCGi-inspired aim+IK roll joint for ONE bone segment (ROLL-
    METHOD.md §2 — reference mechanism; PLAN.md Task 3.1). Not a
    quaternion/candy-wrap extraction — a stabilized aim constraint whose
    up-reference locator's WORLD POSITION is what actually filters or
    passes through twist (see the `loc` block below).

    Cleanup (2026-07-08): antCGi's reference rig also builds a follower/
    follow_tip 2-joint chain plus a rotate-plane ikHandle with a zeroed
    pole vector (ROLL-METHOD.md §2's literal description). That graph's
    own ROTATE output was PROVABLY INERT in both branches of this
    function — aimConstraint worldUpType='object' reads ONLY the
    worldUpObject's WORLD POSITION, never its rotation, so nothing
    downstream ever read that graph's own rotation — and it has been
    stripped from this builder entirely (see docs/superpowers/specs/
    2026-07-08-ikarm-ribbon-module-roll-method.md §7, which documented
    the inertness and proposed this strip). Only its load-bearing
    byproduct survives: `follower_pos`, the perpendicular bone-scaled
    offset point the removed follower joint used to occupy, is still
    computed below and now seeds the `roll_aim` locator's position
    directly instead of via that joint's DAG transform.

    ANCHOR (where `roll` lives, and — for the upper/proximal branch — the
    pointConstraint driver for `loc`): `driver_parent` when given, else
    `bone_start`. AIM TARGET (what roll's aim
    constraint points at): whichever of bone_start/bone_end is NOT the
    anchor. This matters for correctness:
      - The ribbon-driving contract (PLAN Task 3.2): `roll` fully replaces
        its bind joint as the ribbon segment's twisting-end driver
        (position AND rotation). Since `roll` is a ZERO-offset rigid
        child of its anchor, its world POSITION always exactly equals the
        anchor's — so anchoring at `driver_parent` (the joint being
        replaced) keeps that substitution position-exact.
      - The anti-flip geometry itself: the roll_aim locator (below) must
        sit NEAR `roll` for its up-reference to be meaningful — matching
        ROLL-METHOD's "moved out ~-5" (a small nudge, not a whole-bone-
        length translation).

    Builds, per call:
      - `roll` — the joint whose clean twist this function exists to
        produce. Parented to the anchor (rides the LIVE bind chain —
        SimpleIK's IK/FK BLEND output — automatically in both modes, since
        bone_start/bone_end/driver_parent are always bind joints,
        downstream of the blend regardless of mode). Its ROTATION is fully
        owned by the aim constraint below; its TRANSLATION is exactly the
        anchor's (zero-offset rigid parenting). Pure internal scaffolding
        — hidden from the animator's viewport (visibility off), not just
        channel-box-cleaned.
      - `roll_aim` locator (`loc`) — the up-vector reference that ACTUALLY
        determines filter-vs-pass-through, entirely via its WORLD
        POSITION, seeded at `follower_pos` (see the Cleanup note above):
          - driver_parent given (forearm/wrist): `loc` is a RIGID DAG
            child of driver_parent — driver_parent's live rotation (swing
            AND twist alike) orbits loc's nonzero offset, so twist passes
            through (ROLL-METHOD §2 lower-bone note: "parented to the main driver
            skeleton because we want the twist available in both IK and
            FK"). Verified non-vacuous by test_ikarm_roll_anti_flip_
            and_twist_response's forearm assertions.
          - driver_parent is None (upper/proximal): `loc` is positioned at
            the same offset point, parented under the world-stable
            fab_nulls_grp, with its TRANSLATION only driven by a
            pointConstraint(anchor, loc, maintainOffset=True). A point
            constraint reads just the anchor's translation, which a pure
            axial spin never changes (the anchor doesn't move when it
            spins about its own bone axis) — filtering out candy-wrap —
            while genuine swing still reaches roll's up-vector correctly
            because an aim constraint projects any up-reference onto the
            aim axis's current perpendicular plane. Measured on the real
            rig: 0.000deg spurious twist under a 45deg pure-axial-spin
            stimulus with this wiring (docs/superpowers/specs/2026-07-08-
            ikarm-ribbon-module-roll-method.md §7 has the full before/
            after measurement, including the ~86%-leak figure for the
            rejected rigid-parent-to-follower alternative).
        Also pure internal scaffolding — hidden from the animator's
        viewport in both branches — but still fully functional: its
        POSITION still drives the aim constraint's up-vector below.
      - An aim constraint on `roll`: aim axis points at the AIM TARGET
        (down-bone from the anchor), up axis reads the locator
        (worldUpType='object', worldUpObject=locator) — continuously-
        updated up-reference keeps the solve from flipping as the bone
        swings. `axes` = (aim_vector, up_vector), caller-supplied
        (ROLL_AXES_UPPER / ROLL_AXES_LOWER above) since upper vs.
        lower/world-oriented bones use different local axes.

    driver_parent: None for the upper/proximal roll (anchor=bone_start,
    aim target=bone_end, locator position pointConstrained to anchor,
    default). The bind joint to anchor on AND reparent the locator onto
    for the lower/forearm roll (pass bone_end, i.e. wrist) — see the
    ANCHOR note above.

    axes: (aim_vector, up_vector) 3-tuples, e.g. ROLL_AXES_UPPER /
    ROLL_AXES_LOWER.

    side: instance.side ('md' | 'lf' | 'rt') — mirrors the locator's
    offset sign for 'rt'.

    Every created node (roll, locator, the aim constraint, and — upper/
    proximal branch only — the loc pointConstraint) is message-tracked on
    component_node under the 'roll_dg_nodes' bucket. These live OUTSIDE
    rig_grp (roll is a child of a bind joint; the locator is a child of
    fab_nulls_grp or driver_parent) — the rig_grp cascade never catches
    them, so the caller's unbuild() must sweep this bucket explicitly
    (COMPONENT_AUTHORING §6).

    Returns a dict: roll_joint, locator, aim_constraint.
    """
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.utils.maya import network_nodes as nn

    for j in (bone_start, bone_end):
        if not cmds.objExists(j):
            raise RuntimeError(f'build_roll_joint: joint not found: {j!r}')
    if driver_parent and not cmds.objExists(driver_parent):
        raise RuntimeError(
            f'build_roll_joint: driver_parent not found: {driver_parent!r}')
    if not axes or len(axes) != 2:
        raise RuntimeError(
            f'build_roll_joint: axes must be (aim_vector, up_vector), got {axes!r}')

    aim_vec, up_vec = axes
    side_sign = -1.0 if side == 'rt' else 1.0
    bucket = 'roll_dg_nodes'
    prefix = f'{lc.short_name(bone_start)}_{lc.short_name(bone_end)}_roll'

    # Anchor + aim target — see the docstring's ANCHOR note. driver_parent,
    # when given, must be bone_start or bone_end (our two callers always
    # pass bone_end — the forearm/wrist case); the aim target is simply
    # whichever end the anchor is NOT.
    anchor = driver_parent if driver_parent else bone_start
    aim_target = bone_start if anchor == bone_end else bone_end

    anchor_pos = cmds.xform(anchor, q=True, ws=True, t=True)
    target_pos = cmds.xform(aim_target, q=True, ws=True, t=True)
    bone_vec = [target_pos[i] - anchor_pos[i] for i in range(3)]
    seg_len = sum(c * c for c in bone_vec) ** 0.5 or 1.0
    bone_dir = [c / seg_len for c in bone_vec]

    # Perpendicular offset direction for the roll_aim locator's seed
    # position (`follower_pos`, below): cross(bone_dir, up_world) — well-
    # defined regardless of the bone's world orientation (unlike antCGi's
    # fixed "Z"), degenerate only when the bone happens to be parallel to
    # up_world, handled with a world-Z fallback. This used to seed the
    # (now-removed) `follower` joint's position; the locator is seeded
    # here directly instead — see this function's docstring Cleanup note.
    _, _, up_world = cc.resolve_axes([bone_start, bone_end])
    side_vec = [
        bone_dir[1] * up_world[2] - bone_dir[2] * up_world[1],
        bone_dir[2] * up_world[0] - bone_dir[0] * up_world[2],
        bone_dir[0] * up_world[1] - bone_dir[1] * up_world[0],
    ]
    side_len = sum(c * c for c in side_vec) ** 0.5
    if side_len < 1e-6:
        side_vec, side_len = [0.0, 0.0, 1.0], 1.0
    side_vec = [c / side_len for c in side_vec]
    offset_len = seg_len * _ROLL_OFFSET_FRACTION * side_sign
    follower_pos = [anchor_pos[i] + side_vec[i] * offset_len for i in range(3)]

    # `roll`: rides the anchor rigidly (translate); ROTATE is fully
    # overridden by the aim constraint below.
    cmds.select(clear=True)
    roll_jnt = cmds.joint(name=f'{prefix}_jnt', position=anchor_pos)
    cmds.parent(roll_jnt, anchor)
    cmds.xform(roll_jnt, ws=True, t=anchor_pos)

    # roll_aim locator — the up-vector reference for roll_jnt's aim
    # constraint below, seeded directly at `follower_pos` (see this
    # function's docstring Cleanup note — this used to be seeded from a
    # removed `follower` joint's DAG transform). IMPORTANT, verified
    # empirically (mayapy probe, isolated 2-joint rig): aimConstraint
    # worldUpType='object' reads ONLY worldUpObject's WORLD POSITION —
    # rotating a worldUpObject in place (position held fixed) leaves the
    # resulting up-vector completely unchanged; translating it is the
    # only thing that moves it. So what determines "twist filters" vs.
    # "twist passes through" here is how `loc`'s POSITION responds to the
    # rig, never any joint's rotation.
    #
    #   - driver_parent given (forearm/wrist): `loc` is a RIGID DAG
    #     child of driver_parent (position AND rotation both inherited).
    #     Since loc sits at a nonzero offset from driver_parent, ANY of
    #     driver_parent's live rotation — swing or twist alike — orbits
    #     loc's world position, so both feed straight into roll_jnt's
    #     up-vector: pronation twist "passes through" (ROLL-METHOD's
    #     lower-bone note). Verified non-vacuous by
    #     test_ikarm_roll_anti_flip_and_twist_response's forearm
    #     assertions (a genuine wrist-rotate stimulus).
    #   - driver_parent is None (upper/proximal): twist must FILTER
    #     (anti-candy-wrap) — the up-reference has to track the anchor's
    #     SWING (so the aim keeps bending sensibly with the limb) but
    #     NOT the anchor's own axial TWIST. `loc`'s position instead comes
    #     from a translate-ONLY pointConstraint (maintainOffset=True)
    #     straight to `anchor`, parked under the world-stable
    #     fab_nulls_grp rather than under `anchor` itself. A point
    #     constraint reads only the driver's TRANSLATION — which a pure
    #     axial spin never changes (the anchor doesn't move when it
    #     spins about its own bone axis) — while genuine SWING still
    #     reaches roll_jnt's up-vector correctly, because an aim
    #     constraint projects ANY up-reference onto the current
    #     perpendicular plane of a moving aim axis; a translation-fixed
    #     reference swings along for free. Measured: 0.000deg spurious
    #     twist under a 45deg pure-axial-spin stimulus with this wiring
    #     (see docs/superpowers/specs/2026-07-08-ikarm-ribbon-module-
    #     roll-method.md §7 for the rejected rigid-parent alternative's
    #     ~86%-leak measurement).
    loc = cmds.spaceLocator(name=f'{prefix}_aim_loc')[0]
    cmds.xform(loc, ws=True, t=follower_pos)
    if driver_parent:
        cmds.parent(loc, driver_parent)
        cmds.xform(loc, ws=True, t=follower_pos)
        loc_pc = None
    else:
        cmds.parent(loc, nodes.ensure_nulls_grp())
        loc_pc = cmds.pointConstraint(anchor, loc, maintainOffset=True)[0]

    # Aim constraint: axes differ upper (aim X / up -Z) vs. lower/world-
    # oriented (aim Y / up X) — caller-supplied via `axes`. Target is the
    # AIM TARGET (down-bone from the anchor), not literally bone_end —
    # for the driver_parent case the anchor IS bone_end, so the target is
    # bone_start (ROLL-METHOD's "aims at the previous joint").
    ac = cmds.aimConstraint(
        aim_target, roll_jnt, aimVector=list(aim_vec), upVector=list(up_vec),
        worldUpType='object', worldUpObject=loc, maintainOffset=False,
    )[0]

    # Both `roll` and `loc` are pure internal scaffolding — an animator
    # never selects or keys them. Explicit visibility=0 (not just
    # hide_internal's channel-box cleanup, which never touches the actual
    # visibility attribute) so they don't leak into the viewport; neither
    # lives under a hidden group the way the ribbon's own setup_grp hides
    # ITS internals (roll rides a live bind joint, loc rides driver_parent
    # or fab_nulls_grp depending on branch — both real, visible parents),
    # so each needs its own explicit hide — matching the convention
    # simple_ik.py/ik_leg.py already use for their own ikHandles.
    for n in (roll_jnt, loc):
        cmds.setAttr(f'{n}.visibility', 0)
        nodes.hide_internal(n)

    tracked = [roll_jnt, loc, ac]
    if loc_pc:
        tracked.append(loc_pc)
    for n in tracked:
        nn.connect_message_multi(n, component_node, bucket)

    return {
        'roll_joint': roll_jnt,
        'locator': loc,
        'aim_constraint': ac,
    }


def build_ribbon_segment(start_joint, end_joint, mid_count, opts,
                             component_node, start_twist_driver=None,
                             end_twist_driver=None, twist_driver_up_axis=None):
    """Build a per-bone ribbon segment between two driver joints. An end
    with no twist_driver rides its plain bind joint, position+rotation
    (P2 baseline). An end WITH a twist_driver (P3 — the antCGi roll joint
    from build_roll_joint, above) is driven by THAT instead — a full
    substitution, position AND rotation both — SPEC 3.2 / PLAN Task 3.2:
    "the shoulder roll joint feeds clean twist into the [upper segment's]
    start end"; "the forearm/wrist roll joint ... feeds clean pronation
    twist into the [forearm segment's] wrist end". Position-exact because
    build_roll_joint anchors its roll joint as a ZERO-offset rigid child
    of the very bind joint being replaced (see that function's ANCHOR
    note) — only rotation actually differs. Only ONE end per segment ever
    gets a twist_driver (start_twist_driver for the upper-arm segment,
    end_twist_driver for the forearm segment) — the shared elbow end of
    each segment keeps riding the elbow bind joint directly, per the
    elbow-ownership note below.

    twist_driver_up_axis: the twist_driver roll joint's LOCAL "up" axis
    (its ROLL_AXES_UPPER/ROLL_AXES_LOWER up_vector — the axis whose world
    orientation actually carries the roll joint's twist signal, per
    build_roll_joint's aim constraint). Every mid ctrl's own aimConstraint
    (below) reads this axis, via worldUpType='objectrotation', off
    whichever twist_driver was given, so the CLEAN roll signal — not just
    the raw bind joint's rotation — reaches the interior falloff-skinned
    rows (SPEC 3.2: twist "distributes ... along" the segment). Passing
    the WRONG local axis here (e.g. the roll joint's AIM axis instead of
    its up axis) silently reads a twist-insensitive direction and the
    interior mids stay flat — verified empirically, this is why the
    caller MUST pass the same up_vector it gave build_roll_joint for that
    same twist_driver. Defaults to (0, 1, 0) — end_joint's own local Y,
    the P2 baseline convention — when no twist_driver is given.

    Recipe (adapted from RibbonSpineComponent.build over _ribbon_common):
      - A ribbon surface spanning start_joint -> end_joint (fixed internal
        resolution, independent of mid_count — mirrors RibbonSpine's
        row_count being independent of mid_ctrl_count).
      - mid_count + 2 control joints at even arc fractions, falloff-skinned
        to the surface (skin_surface mode='falloff' — the free twist
        distribution).
      - mid_count floating, animator-facing mid ctrls (aim+point
        constrained between start_joint/end_joint, same always-on-aimer
        pattern as RibbonSpine's mids) driving the interior control
        joints; the two END control joints are driven directly by
        start_joint/end_joint (read-only constraints — see elbow-ownership
        note below).
      - A twist-only sculpted board (nonlinear twist deformer, PRE-skin
        blendShape) + composed volume preservation. Sine + jiggle are
        deliberately NOT built (SPEC 3.2 / Task 2.1) — only the twist_root/
        twist_tip/volume BOARD_ATTRS are wired; the rest of the fixed
        10-attr schema exists (add_board_attrs always adds all 10) but
        stays inert.
      - N fresh internal "ride" joints (one per surface row), uvPin-driven
        off the DEFORMED ribbon surface (build_ride_uvpin — COMPONENT_
        AUTHORING §6-A: reads the deformed shape, filters
        intermediateObject). These CANNOT be start_joint/end_joint
        themselves: build_ride_uvpin drives translate/rotate/scale via
        connectAttr, and those two joints already carry incoming
        constraint connections from SimpleIK's IK/FK blend chain —
        connectAttr onto an already-constrained channel raises. Fresh
        joints sidestep that. These ride joints are the twist+volume
        measurement points (and the eventual mesh-skin target once a
        downstream skin-binding step exists — ROLL-METHOD.md §4: "roll
        joints -> ribbon -> mesh"; P2 stands in with plain internal
        joints since roll joints don't land until P3).

    Elbow ownership (SPEC §7 / PLAN Task 2.2): when this is called twice
    with a shared middle joint (upper: shoulder->elbow, forearm:
    elbow->wrist), each call creates its OWN independent end/start control
    joint at the elbow position, each just a READ-ONLY constrained
    follower of the SAME `elbow` bind joint (mo=True parent/scaleConstraint
    via cc.drive_control_joints). Neither segment ever WRITES to `elbow`
    itself (that stays solely owned by SimpleIK's IK/FK blend chain) and
    the two control-joint nodes are never the same Maya node — so there is
    no fight. (P3's roll joints will slot into the same invariant: the
    upper segment's tip reads the elbow-side roll joint, the forearm
    segment's root reads the lower/wrist-side roll joint — antCGi parents
    the lower roll's locator to the driver joint specifically so both
    still ultimately anchor off the same elbow, still as two distinct
    read-only-driven nodes.)

    opts: a plain dict (NOT the Contract's raw options_schema value — the
    caller pre-resolves it). Recognized keys:
      'ribbon_width'      (float, 0.0 = auto — see resolve_ribbon_width)
      'mid_ctrl_shape'    (str, shape_enum key, default 'sphere')
      'ctrl_color'        (str, color_enum key, default 'yellow')
      '_root_matrix_plug' (str, a 'node.worldMatrix[0]'-shaped Maya plug —
                           the global-scale anchor for build_volume's
                           scale-invariance contract; RibbonIKArm.build resolves
                           this once via context.resolve_plug(
                           '<id>.anchor_out') and threads it through here
                           since this helper's signature is fixed and
                           takes no `context`)
      '_rest_len'         (float or None — persisted rest_len from a prior
                           unbuild, so a rebuild on a posed/stretched scene
                           doesn't re-measure the wrong rest length)
      '_cv_data'          (dict: mid ctrl name -> shapes list, restored
                           onto the mid ctrls exactly like every other
                           component's cv_data persistence)
      '_board'            (dict or None — a prior capture_board()
                           snapshot of this segment's twist_root/twist_tip/
                           volume/... BOARD_ATTRS dials, restored onto the
                           fresh settings node via apply_board() AFTER
                           the volume=1.0 default is set, mirroring
                           ribbon_spine.py's build()/unbuild() round-trip
                           so rigger-dialed twist/volume values survive an
                           Edit-Mode rebuild instead of silently resetting)

    Returns a dict describing everything created (chain, surf, setup_grp,
    control_joints, mid_ctrls, skin, ride_joints, settings, twist_deformer,
    row_count, ribbon_width, rest_len) for the caller's own bookkeeping.
    Every created node is ALSO message-tracked on component_node (buckets:
    'ribbon_dg_nodes', 'ribbon_skin_nodes', 'ribbon_board_nodes',
    'control_joints' — identical bucket names to RibbonSpineComponent, so
    RibbonIKArm.unbuild can sweep both segments' nodes with the same 4-bucket
    loop RibbonSpine's own unbuild uses).
    """
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.utils.maya import network_nodes as nn
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
    from maya_tools.rigging.fabricator.modules.world import _apply_color, _apply_channels

    for j in (start_joint, end_joint):
        if not cmds.objExists(j):
            raise RuntimeError(f'build_ribbon_segment: joint not found: {j!r}')
    opts = opts or {}
    mid_count = max(1, min(8, int(mid_count or 1)))
    cv_block = opts.get('_cv_data') or {}

    chain = ribbon_segment_chain_name(start_joint, end_joint)
    joints = [start_joint, end_joint]

    nulls_grp = nodes.ensure_nulls_grp()
    controls_grp = nodes.ensure_controls_grp()
    setup_grp_name = f'{chain}_setup_grp'
    if cmds.objExists(setup_grp_name):
        cmds.delete(setup_grp_name)  # idempotent if a prior build orphaned one
    setup_grp = cmds.createNode('transform', name=setup_grp_name, parent=nulls_grp)
    cmds.setAttr(f'{setup_grp}.inheritsTransform', 0)
    cmds.setAttr(f'{setup_grp}.visibility', 0)

    total = mid_count + 2                 # start + mids + end control joints
    row_count = max(8, total + 2)         # surface resolution, independent
                                          # of mid_count (mirrors RibbonSpine
                                          # deriving row_count from a real
                                          # bind chain we don't have here).

    _, _, up_world = cc.resolve_axes(joints)
    width = resolve_ribbon_width(opts, joints)
    center_crv = cc.build_center_curve(joints, row_count, setup_grp,
                                       f'{chain}_center_crv',
                                       component_node, 'ribbon_dg_nodes')
    cvs = [cmds.pointPosition(f'{center_crv}.cv[{k}]', world=True)
           for k in range(row_count)]
    side_world = side_world_per_cv(joints, cvs)
    surf = build_surface(chain, center_crv, row_count, width,
                            side_world, setup_grp, up_world, component_node)

    # Bind-pose row-center snapshot for the ride joints, captured BEFORE
    # any skin/deformer touches the surface (pure loft/rebuild rest shape).
    row_positions = surface_row_centers(surf, row_count)

    # Control joints at even arc fractions: start, mids..., end.
    crv_shape = cc.deformed_shape(center_crv, 'nurbsCurve')
    cj_positions = []
    for i in range(total):
        param = i / (total - 1)
        cj_positions.append(cmds.pointOnCurve(crv_shape, top=True,
                                              parameter=param, position=True))
    control_joints = cc.build_control_joints(
        f'{chain}_cj_', cj_positions, up_world, setup_grp,
        component_node, 'control_joints')
    skin = skin_surface(chain, control_joints, surf, row_count,
                           component_node, mode='falloff')

    # Mid controls — animator-facing, floating (always-on aimer between
    # start_joint and end_joint), parented under the world controls_grp
    # (no per-arm "COG" equivalent exists in P2 — see the elbow-ownership
    # note above for why anchoring under a joint would be the wrong move).
    #
    # up-vector reference: every mid's aimConstraint below reads
    # `up_ref_joint`'s rotation for its up-vector (worldUpType=
    # 'objectrotation'). P3 feeds this the segment's CLEAN roll-joint
    # signal (start_twist_driver or end_twist_driver — exactly one is
    # ever set per the docstring above) instead of the raw bind joint,
    # so the roll's filtered/pass-through twist actually reaches the
    # interior falloff-skinned rows the mid ctrls drive — not just the
    # single hard-pinned boundary row the twisting-end control joint
    # owns (SPEC 3.2 / PLAN Task 3.2: twist "distributes ... along" the
    # segment). Falls back to end_joint (P2 baseline) when no
    # twist_driver is given. MUST pair with the matching
    # twist_driver_up_axis (see docstring) — worldUpVector is expressed
    # in the up-object's OWN local space, and a roll joint's twist-
    # sensitive axis is NOT (0, 1, 0) the way a plain bind joint's is.
    twist_driver = end_twist_driver or start_twist_driver
    if twist_driver and not twist_driver_up_axis:
        raise RuntimeError(
            'build_ribbon_segment: a twist_driver was given without '
            'twist_driver_up_axis — the caller must pass the SAME '
            'up_vector it gave build_roll_joint for that driver, or the '
            'mid ctrls silently read a twist-insensitive axis.')
    up_ref_joint = twist_driver or end_joint
    up_ref_vec = (tuple(twist_driver_up_axis) if twist_driver
                 else (0.0, 1.0, 0.0))
    mid_ctrls = []
    for m in range(mid_count):
        pos_joint = control_joints[m + 1]
        mbuf = cmds.createNode('transform', name=f'{chain}_mid_{m:02d}_ctrl_offset')
        cmds.matchTransform(mbuf, pos_joint, position=True, rotation=True, scale=False)
        cmds.parent(mbuf, controls_grp)
        cmds.matchTransform(mbuf, pos_joint, position=True, rotation=True, scale=False)

        # Add targets ONE CALL AT A TIME with maintainOffset=True while
        # mbuf still rests at its true curve-sampled position — same fix
        # RibbonSpine's mids use (Task 8 panel finding): captures the
        # small offset between the linear start/end blend and the actual
        # curve position, so the rest pose stays exactly on the curve.
        t = (m + 1) / (total - 1)
        pc = cmds.pointConstraint(start_joint, mbuf, weight=1.0 - t,
                                  maintainOffset=True)[0]
        cmds.pointConstraint(end_joint, mbuf, weight=t, maintainOffset=True)
        aliases = cmds.pointConstraint(pc, q=True, weightAliasList=True)
        targets = cmds.pointConstraint(pc, q=True, targetList=True)
        weights = [cmds.getAttr(f'{pc}.{a}') for a in aliases]
        expected = {start_joint: 1.0 - t, end_joint: t}
        for tgt, w in zip(targets, weights):
            exp_w = expected.get(tgt)
            if exp_w is not None and abs(w - exp_w) > 1e-6:
                raise RuntimeError(
                    f'{mbuf}: pointConstraint weight for {tgt} is {w}, '
                    f'expected {exp_w}')
        cmds.aimConstraint(end_joint, mbuf, maintainOffset=True,
                           aimVector=(1, 0, 0), upVector=(0, 1, 0),
                           worldUpType='objectrotation',
                           worldUpVector=up_ref_vec, worldUpObject=up_ref_joint)

        mid_ctrl = com.build_shape(opts.get('mid_ctrl_shape', 'sphere'),
                                   f'{chain}_mid_{m:02d}_ctrl')
        cmds.parent(mid_ctrl, mbuf)
        for a in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{mid_ctrl}.{a}', 0)
        for a in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{mid_ctrl}.{a}', 1)
        # Scale is dead here — no scaleConstraint reads it (see
        # _drive_control_joints_position_only's docstring: control-joint
        # SCALE must stay locked at (1,1,1) or global rig scale double-
        # transforms). Lock + hide it so the channel box doesn't lie about
        # what an animator's scale gesture will do — the same studio
        # convention RibbonSpine's own mid ctrls get for free via
        # _apply_channels' default keyable whitelist.
        _apply_channels(mid_ctrl, {'keyable': ['tx', 'ty', 'tz', 'rx', 'ry', 'rz']})
        _apply_color(mid_ctrl, opts.get('ctrl_color', 'yellow'))
        nodes.tag_ctrl(mid_ctrl, 'ribbon_mid_ctrl',
                       component_node=component_node, joint_index=m)
        cc._restore_shape(com, mid_ctrl, cv_block.get(mid_ctrl))
        nn.connect_message_multi(mbuf, component_node, 'ribbon_dg_nodes')
        mid_ctrls.append(mid_ctrl)

    # P3: the segment's twisting end (at most one of start/end — see
    # docstring) is driven by its roll joint INSTEAD of the plain bind
    # joint — a full substitution (position AND rotation), not a split.
    # This is safe/position-exact because build_roll_joint anchors `roll`
    # as a ZERO-offset rigid child of the very joint it's replacing here
    # (bone_start for start_twist_driver, bone_end for end_twist_driver —
    # see that function's ANCHOR note), so roll_joint.worldPosition ==
    # that bind joint's worldPosition at every frame; only ROTATION
    # differs (the roll's stabilized aim+IK twist vs. the bind joint's
    # raw rotation). Every other control joint (mids + the non-twisting
    # end) keeps the P2 direct bind-joint drive.
    all_drivers = ([start_twist_driver or start_joint] + mid_ctrls +
                   [end_twist_driver or end_joint])
    _drive_control_joints_position_only('ribbon_dg_nodes', all_drivers,
                                        control_joints, component_node)

    # Twist-only sculpted board + composed volume. Sine + jiggle
    # deliberately NOT built (SPEC 3.2 / Task 2.1).
    settings = cmds.createNode('transform', name=f'{chain}_ribbon_settings',
                               parent=setup_grp)
    add_board_attrs(settings, component_node, role='arm_ribbon_settings_ctrl')
    # Volume preservation ON by default for this component (SPEC 3.2 —
    # "Volume preservation ON"), unlike RibbonSpine where BOARD_ATTRS'
    # own base default (0.0) leaves it off until the rigger dials it up.
    cmds.setAttr(f'{settings}.volume', 1.0)
    # Restore a prior unbuild's captured dial values (twist_root/twist_tip/
    # volume/...) AFTER the volume=1.0 default, mirroring ribbon_spine.py's
    # own build()/unbuild() round-trip (capture_board / apply_board).
    # A fresh build (no persisted board) leaves this a no-op, so the
    # volume=1.0 default above still holds; a rebuild restores whatever the
    # rigger last dialed, including a deliberately-disabled volume.
    apply_board(settings, opts.get('_board') or {})
    nodes.hide_internal(settings)
    nn.connect_message_multi(settings, component_node, 'ribbon_dg_nodes')

    t_copy, t_def, t_handle = build_sculpt_target(
        chain, surf, setup_grp, 'twist', component_node, joints)
    bs = add_board_blend(chain, surf, [t_copy], skin, component_node)
    cmds.setAttr(f'{bs}.weight[0]', 1.0)   # identity at zero twist angles
    cmds.connectAttr(f'{settings}.twist_root', f'{t_def}.startAngle')
    cmds.connectAttr(f'{settings}.twist_tip', f'{t_def}.endAngle')

    # Ride joints: fresh internal joints, one per surface row, positioned
    # from the bind-pose row-center snapshot above and oriented the same
    # way build_control_joints orients everything (+X down-chain), so
    # their rotateX post-ride reads as twist around the bone axis.
    ride_joints = cc.build_control_joints(
        f'{chain}_ride_', row_positions, up_world, setup_grp,
        component_node, 'ribbon_dg_nodes')
    build_ride_uvpin(chain, ride_joints, surf, setup_grp, component_node)

    # Lock the two END ride joints rigidly to their end control joints —
    # same CP3 fix RibbonSpine applies (the falloff skin hard-pins END ROW
    # POSITIONS but the ride's rotate path still reads a falloff-blended
    # penultimate-row tangent frame that drifts slightly from the true end
    # control orientation).
    lock_joint_orient_to(ride_joints[0], control_joints[0], component_node)
    lock_joint_orient_to(ride_joints[-1], control_joints[-1], component_node)

    resolved_rest_len = build_volume(
        chain, surf, setup_grp, ride_joints, settings,
        opts.get('_rest_len'), component_node,
        root_matrix_plug=opts.get('_root_matrix_plug'))

    return {
        'chain': chain,
        'setup_grp': setup_grp,
        'surf': surf,
        'control_joints': control_joints,
        'start_cj': control_joints[0],
        'end_cj': control_joints[-1],
        'mid_ctrls': mid_ctrls,
        'skin': skin,
        'settings': settings,
        'twist_deformer': t_def,
        'ride_joints': ride_joints,
        'row_count': row_count,
        'ribbon_width': width,
        'rest_len': resolved_rest_len,
    }
