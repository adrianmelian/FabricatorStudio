"""Validate joint orientations by POSING the rig, not by reading the bind pose.

WHY THIS EXISTS
---------------
A bind-pose assertion cannot catch the failures that matter. A joint frame rolled off its
bone, with the children compensating, reads as perfectly correct at bind and produces
wrong motion the moment anything rotates. That is precisely how Ninja shipped with a root
frame 90 degrees out while importing upright, looking right in the viewport, and only
being caught by measuring the file rather than the picture.

So this poses drivers and measures what actually moves, off-origin, in WORLD space.

THE TWO CHECKS
--------------

**1. Twist-vs-swing.** For any joint with a child, one axis points down the bone. Rotating
about that axis is TWIST: the child should barely move. Rotating about a perpendicular
axis is SWING: the child should sweep through an arc.

If the frame is rolled off the bone, those two swap, wholly or partially — the "twist"
axis starts swinging the child. That is a frame-independent invariant: it needs no
reference rig, no expected numbers, and no knowledge of the naming convention. It just
asks whether the joint's own axes agree with its own geometry.

**2. Live deformation.** Pose a driver and measure whether skinned vertices actually
moved. Catches a dead rig, a broken skinCluster, and deformer-order mistakes that leave
the skin behind. Measured as world displacement, because a local read after a bake shows
the residual rather than the input.

Everything restores what it touched. Nothing here is a mutation the caller has to undo.

No UI imports; headless-callable so it runs under mayapy.
"""
__author__ = "Adrian Melian"

import math

from maya import cmds

# How far to swing a joint when probing. Big enough that a rolled frame is unmistakable,
# small enough to stay inside sane rotation limits.
PROBE_ANGLE = 30.0

# A twist rotation should move the child by less than this fraction of the bone length.
# Anything above it means the "twist" axis is swinging, i.e. the frame is rolled.
TWIST_TOLERANCE = 0.10

# A swing rotation should move the child by at least this fraction of the ideal arc
# (2 * L * sin(angle/2)). Well under 1.0 because a joint mid-chain can be constrained.
SWING_MIN_FRACTION = 0.50

AXES = ('X', 'Y', 'Z')


class Finding(object):
    """One problem, in the shape the caller reports to a human."""

    def __init__(self, joint, kind, detail):
        self.joint = joint
        self.kind = kind
        self.detail = detail

    def __repr__(self):
        return '%s [%s] %s' % (self.joint, self.kind, self.detail)


def _world(node):
    return cmds.xform(node, q=True, ws=True, t=True)


def _dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def _bone_axis(joint, child):
    """Which local axis points at the child, and how long the bone is.

    Read from the CHILD'S LOCAL TRANSLATE, whose dominant component is the aim axis by
    construction: the Armature bake normalizes children onto the parent's aim axis. Do not
    infer this from world positions; the whole point is to test the joint's own frame
    against its own geometry.
    """
    t = cmds.getAttr('%s.translate' % child)[0]
    length = math.sqrt(sum(v * v for v in t))
    if length < 1e-6:
        return None, 0.0
    dominant = max(range(3), key=lambda i: abs(t[i]))
    return AXES[dominant], length


def _rotate_probe(joint, axis, angle, child):
    """Rotate `joint` by `angle` about local `axis`, return the child's world displacement.

    Restores the joint's rotation afterwards, whatever happens.
    """
    attr = '%s.rotate%s' % (joint, axis)
    if not cmds.getAttr(attr, settable=True):
        return None
    before = _world(child)
    original = cmds.getAttr(attr)
    try:
        cmds.setAttr(attr, original + angle)
        moved = _world(child)
    finally:
        cmds.setAttr(attr, original)
    return _dist(before, moved)


def candidate_joints(blueprint=None):
    """Every scene joint worth probing, i.e. the authored rig minus the engine bones.

    Unreal's IK bones (ik_foot_root, ik_hand_gun, ...) are convention markers, not
    anatomy. They form no real bone chain and carry no skin weights, so a twist-vs-swing
    probe and a deformation probe both fail on them for reasons that say nothing about
    the rig. Measured on Samurai: including them produced four findings, all false.

    Pass the export's blueprint to use its declared engine joints; otherwise the known UE
    set is assumed (ARMATURE-EXPORT-SPEC R5 asks for this to be declared per file, which
    would make this exact rather than assumed).
    """
    from maya_tools.rigging.armature_import import blueprint_source
    engine = blueprint_source.engine_joint_names(blueprint or {})
    return [j for j in (cmds.ls(type='joint', long=False) or []) if j not in engine]


def check_joint_frames(joints=None, angle=PROBE_ANGLE, blueprint=None):
    """Twist-vs-swing over every joint that has exactly one child.

    Returns (findings, tested_count). A joint is skipped, not failed, when it has no
    single child, a zero-length bone, or locked rotation channels.
    """
    findings = []
    tested = 0

    if joints is None:
        joints = candidate_joints(blueprint)

    ideal = 2.0 * math.sin(math.radians(angle) / 2.0)

    for joint in joints:
        children = cmds.listRelatives(joint, children=True, type='joint',
                                      fullPath=False) or []
        if len(children) != 1:
            continue
        child = children[0]

        axis, length = _bone_axis(joint, child)
        if not axis or length < 1e-4:
            continue

        twist = _rotate_probe(joint, axis, angle, child)
        if twist is None:
            continue

        perpendicular = [a for a in AXES if a != axis]
        swings = []
        for other in perpendicular:
            moved = _rotate_probe(joint, other, angle, child)
            if moved is not None:
                swings.append((other, moved))
        if not swings:
            continue

        tested += 1

        if twist > length * TWIST_TOLERANCE:
            findings.append(Finding(
                joint, 'rolled-frame',
                'rotating about %s (the bone axis, toward "%s") moved the child %.3f, '
                'which is %.0f%% of the %.3f bone length. That axis should twist, not '
                'swing, so this joint\'s frame is rolled off its bone.'
                % (axis, child, twist, 100.0 * twist / length, length)))

        best_axis, best_swing = max(swings, key=lambda s: s[1])
        expected = length * ideal
        if best_swing < expected * SWING_MIN_FRACTION:
            findings.append(Finding(
                joint, 'no-swing',
                'the best perpendicular axis (%s) moved the child only %.3f, against an '
                'expected arc of about %.3f. The joint may be constrained, locked, or '
                'driven by something upstream.'
                % (best_axis, best_swing, expected)))

    # A rig where NOTHING responds is one diagnosis, not one finding per joint.
    #
    # This is the state right after a blueprint load: build_armature() puts a single-chain
    # IK on every pure-aim joint-to-child edge, so the parent's rotation is solved rather
    # than settable, and every probe reads zero. Reporting that as N separate broken
    # joints is worse than useless — it buries the real message, which is that the check
    # is being run at the wrong moment.
    no_swing = [f for f in findings if f.kind == 'no-swing']
    if tested and len(no_swing) == tested:
        return [Finding(
            '<rig>', 'rig-is-driven',
            'not one of the %d joints tested responded to a rotation, so the skeleton is '
            'being driven rather than posed — the Armature edit rig (single-chain IK on '
            'every aim edge), a constraint, or a built control rig. Run this on the bare '
            'imported skeleton BEFORE the blueprint is loaded, or unbuild first.'
            % tested)], tested

    return findings, tested


def _mesh_points(shape):
    """Every world point of a mesh in one call.

    API 2.0 rather than a cmds.pointPosition loop because the loop forces a choice
    between speed and coverage, and that choice is a bug: sampling every Nth vertex
    silently misses any small weighted region (a finger, a tail), so a joint that moves
    nothing sampled reads as dead. Ninja has 94,977 vertices; getPoints returns them all
    at once and removes the tradeoff entirely.
    """
    import maya.api.OpenMaya as om2
    sel = om2.MSelectionList()
    sel.add(shape)
    return om2.MFnMesh(sel.getDagPath(0)).getPoints(om2.MSpace.kWorld)


def _max_point_delta(before, after):
    worst = 0.0
    for i in range(len(before)):
        a, b = before[i], after[i]
        d = math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)
        if d > worst:
            worst = d
    return worst


def check_deformation(drivers=None, angle=PROBE_ANGLE, sample=None, blueprint=None):
    """Pose drivers and confirm skinned geometry actually moves.

    Catches a dead rig: a skinCluster that exists, binds, and deforms nothing, which a
    bind-pose assertion reports as perfectly healthy.

    Separates two states that look identical from the outside and are not the same thing:

      * a joint that CARRIES WEIGHTS but moves no skin — a real defect (deformer order,
        a broken bind, something evaluating ahead of the skinCluster)
      * a joint bound as an influence that carries NO WEIGHTS anywhere — not a defect,
        just an unpainted influence, and reporting it as a dead rig is a false alarm

    `weightedInfluence` is what tells them apart. Ninja's five tail joints are the real
    example: bound, entirely unweighted, and previously reported as a dead rig.

    Returns (findings, tested_count). `sample` is accepted and ignored, kept so older
    callers do not break; every vertex is compared now.
    """
    findings = []
    tested = 0

    skins = cmds.ls(type='skinCluster') or []
    if not skins:
        return [Finding('<scene>', 'no-skin', 'no skinCluster in the scene')], 0

    meshes = []
    for skin in skins:
        for shape in cmds.skinCluster(skin, q=True, geometry=True) or []:
            if cmds.nodeType(shape) == 'mesh' or cmds.listRelatives(shape, shapes=True):
                meshes.append((skin, shape))
    if not meshes:
        return [Finding('<scene>', 'no-geo', 'skinCluster drives no geometry')], 0

    skin, mesh = meshes[0]
    influences = cmds.skinCluster(skin, q=True, inf=True) or []
    weighted = set(cmds.skinCluster(skin, q=True, weightedInfluence=True) or [])
    if drivers is None:
        allowed = set(candidate_joints(blueprint))
        drivers = [d for d in influences if d in allowed]

    if not cmds.polyEvaluate(mesh, vertex=True):
        return [Finding(mesh, 'no-verts', 'mesh reports no vertices')], 0

    unpainted = []

    for driver in drivers:
        if driver not in influences or not cmds.objExists(driver):
            continue
        children = cmds.listRelatives(driver, children=True, type='joint') or []
        if not children:
            continue
        axis, length = _bone_axis(driver, children[0])
        if not axis or length < 1e-4:
            continue

        if driver not in weighted:
            # A root joint with no weights is the norm, not news. Reporting it on every
            # character trains the reader to skip the whole finding, which is how a real
            # one (Ninja's five unweighted tail joints) gets missed.
            if not (cmds.listRelatives(driver, parent=True, type='joint') or []):
                continue
            unpainted.append(driver)
            continue

        swing = [a for a in AXES if a != axis][0]
        attr = '%s.rotate%s' % (driver, swing)
        if not cmds.getAttr(attr, settable=True):
            continue

        before = _mesh_points(mesh)
        original = cmds.getAttr(attr)
        try:
            cmds.setAttr(attr, original + angle)
            after = _mesh_points(mesh)
        finally:
            cmds.setAttr(attr, original)

        tested += 1
        worst = _max_point_delta(before, after)
        if worst < length * 0.01:
            findings.append(Finding(
                driver, 'dead-skin',
                'carries skin weights, but rotating %s by %.0f degrees moved the mesh by '
                'at most %.4f. The weights exist and do not deform.'
                % (swing, angle, worst)))

    if unpainted:
        findings.append(Finding(
            ', '.join(sorted(unpainted)[:6]) + (' ...' if len(unpainted) > 6 else ''),
            'unweighted-influence',
            '%d joint(s) are bound to the skinCluster but carry no weights anywhere, so '
            'they deform nothing. Not a rig defect on its own (an unpainted influence), '
            'but worth knowing they are inert.' % len(unpainted)))

    return findings, tested


def run(joints=None, angle=PROBE_ANGLE, log=None, blueprint=None):
    """Both checks. Returns (findings, summary_lines).

    Deliberately does NOT raise. A caller that wants a hard gate checks the findings list;
    a caller that wants a report prints the lines.
    """
    log = log or (lambda _m: None)

    log('posing joints to test their frames (%.0f degree probe)' % angle)
    frame_findings, frames_tested = check_joint_frames(joints, angle, blueprint)
    log('  %d joints tested, %d problem(s)' % (frames_tested, len(frame_findings)))

    log('posing drivers to test deformation')
    deform_findings, deform_tested = check_deformation(None, angle, blueprint=blueprint)
    log('  %d drivers tested, %d problem(s)' % (deform_tested, len(deform_findings)))

    findings = frame_findings + deform_findings
    summary = [
        'orientation check: %d joint frames and %d drivers tested'
        % (frames_tested, deform_tested),
    ]
    if not findings:
        summary.append('  no problems found')
    else:
        summary.append('  %d PROBLEM(S):' % len(findings))
        for f in findings:
            summary.append('    %s [%s] %s' % (f.joint, f.kind, f.detail))
    return findings, summary
