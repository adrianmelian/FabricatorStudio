# Python/maya_tools/rigging/fabricator/modules/ribbon_ik_leg.py
"""RibbonIKLeg — IKLeg (4-joint reverse-foot IK/FK leg) plus two per-bone
ribbon twist segments (thigh, shin) driven by antCGi aim roll joints.

Paid Advanced Ribbon pack, module 3 of 3 (RibbonSpine, RibbonIKArm,
RibbonIKLeg). Subclass seam mirrors ribbon_ik_arm.py over SimpleIK, one
level up: RibbonIKLeg(IKLegComponent) inherits the reverse foot verbatim.
P1 = pure parity (no build/unbuild override). P2 added the two per-bone
ribbon segments with plain driven ends. P3 inserts two antCGi aim roll
joints (build_roll_joint) as the segments' twisting-end drivers — thigh
filtered (anti-candy-wrap under hip swing), ankle pass-through (available
in both IK and FK, reverse-foot yaw included). P4 Task 6 (this file,
current state) adds twist riders sourced from the leg's fab_limb node:
when a limb resolves for the thigh joint, any twist_upper[]/twist_lower[]
member joints (however they got there — the Twist dial or hand-authored)
are pinned to their own segment's ribbon surface (thigh_seg for upper,
shin_seg for lower) via a second, additional build_ride_uvpin call per
segment — build_ribbon_segment's own internal ride joints are untouched
and remain the standalone fallback for no-limb/empty-multi builds. See
spec: docs/superpowers/specs/2026-07-09-ribbonikleg-module-design.md
"""
__author__ = "Adrian Melian"

from maya_tools.rigging.fabricator.modules.component import Contract, OptionField
from maya_tools.rigging.fabricator.modules.ik_leg import (
    IKLegComponent, IK_LEG_CONTRACT,
)

RIBBON_IK_LEG_CONTRACT = Contract(
    type='RibbonIKLeg',
    display_name='Ribbon IK Leg',
    description=(
        'Four-joint IK/FK leg with reverse foot (IKLeg parity — heel/toe '
        'pivot stack, foot_roll, ball FK ctrl) plus two per-bone ribbon '
        'segments (thigh, shin) with twist + volume + N floating mid '
        'ctrls, twisting ends driven by antCGi aim roll joints. Foot is '
        'not ribboned.'
    ),
    min_joints=IK_LEG_CONTRACT.min_joints,
    max_joints=IK_LEG_CONTRACT.max_joints,
    parent_strategy=IK_LEG_CONTRACT.parent_strategy,
    inputs=IK_LEG_CONTRACT.inputs,
    outputs=IK_LEG_CONTRACT.outputs,
    options_schema={
        **IK_LEG_CONTRACT.options_schema,
        # Ribbon family color: green (all ribbons are green shades).
        'ctrl_color': OptionField(type='color_enum', default='green'),
        # The four ribbon options mirror ribbon_ik_arm.py's fields
        # verbatim (same names/defaults/semantics) so riggers learn ONE
        # ribbon vocabulary across the pack.
        'mid_ctrl_count': OptionField('int', 1, range=(1, 8),
            description='Floating mid controls per ribbon segment '
                        '(thigh and shin each get this many). Build '
                        'option: change in Edit Mode and rebuild.'),
        'ribbon_width': OptionField('float', 0.0, range=(0.0, 1000.0),
            description='Ribbon cross-width for both segments. 0.0 = '
                        'auto (25% of the bone\'s length; each segment '
                        'spans exactly one bone). Resolved value '
                        'persisted so rebuilds match.'),
        'ribbon_mid_ctrl_shape': OptionField('shape_enum', 'sphere',
            description='Shape for the floating ribbon mid ctrls.'),
        'ribbon_ctrl_color': OptionField('color_enum', '',
            description='Color for the floating ribbon mid ctrls. Empty '
                        '(default) follows this leg\'s own ctrl_color (its '
                        'side color); set a value only to make the ribbon '
                        'ctrls read as a distinct layer in the viewport.'),
    },
    side_supported=IK_LEG_CONTRACT.side_supported,
    color='#1ACC1A',                        # green — ribbon family
    default_region='leg',
    limb_features=('twists',),              # Derived Limbs: legs twist, never finger
    joint_roles=IK_LEG_CONTRACT.joint_roles,
    extra_guides=IK_LEG_CONTRACT.extra_guides,
    space_consumers=IK_LEG_CONTRACT.space_consumers,
    actions=IK_LEG_CONTRACT.actions,
    mirror_rules=IK_LEG_CONTRACT.mirror_rules,
)


# DG node-set buckets shared by BOTH ribbon segments on the same component
# node (same convention RibbonIKArm/RibbonSpine use for their own segments)
# — build_ribbon_segment message-tracks into these; unbuild sweeps all four
# in one pass, cleaning up both segments together.
_RIBBON_BUCKETS = ('ribbon_dg_nodes', 'ribbon_skin_nodes',
                   'ribbon_board_nodes', 'control_joints')

# P3: the antCGi roll-joint rigs (build_roll_joint) for BOTH bone segments
# land in this single bucket — swept unconditionally alongside the ribbon
# buckets below (mirrors ribbon_ik_arm.py's own _ROLL_BUCKETS).
_ROLL_BUCKETS = ('roll_dg_nodes',)


def _resolve_component_node(instance_id):
    from maya_tools.rigging.fabricator import nodes as ks_nodes
    for cn in ks_nodes.get_all_component_nodes():
        if ks_nodes.get_component_id(cn) == instance_id:
            return cn
    return None


class RibbonIKLegComponent(IKLegComponent):
    """P1: pure parity (build/unbuild/can_apply/guides all inherited).
    P2 added two per-bone ribbon segments — thigh (thigh->knee), shin
    (knee->ankle). P3: the segments' twisting ends are driven by two
    antCGi aim roll joints (thigh filtered, ankle pass-through — see
    build()'s own comment for the mapping). P4 Task 6 (this file, current
    state): when the thigh joint resolves a fab_limb node, any
    twist_upper[]/twist_lower[] member joints ride their own segment's
    ribbon surface too (thigh_seg for upper, shin_seg for lower) — see
    build()'s own Task 6 comment block. Foot is not ribboned."""
    CONTRACT = RIBBON_IK_LEG_CONTRACT
    # Inherited free-leg fractional shin twist stays OFF here
    # (2026-07-10): the ribbon segment riders own the twist joints —
    # two drivers on one channel is a fight the already-driven guard
    # should never have to referee.
    free_fractional_lower_twist = False

    @classmethod
    def build(cls, instance, context) -> None:
        """IKLeg parity (reverse foot etc.), then two antCGi aim roll
        joints (thigh: filtered, ankle: pass-through), then two per-bone
        ribbon segments: thigh (thigh->knee), shin (knee->ankle) with
        their twisting ends driven by those roll joints. Foot is not
        ribboned."""
        import maya.cmds as cmds
        # RENAME-SWAP done (2026-07-09): the leader's rename/seam-split
        # commit landed mid-task — the old free-side hand-helpers module is
        # gone, replaced by free-side _limb_common.py + paid
        # _ribbon_common.py (which now holds build_ribbon_segment, née
        # build_arm_ribbon_segment, same signature — verified directly
        # against the landed source).
        from maya_tools.rigging.fabricator.modules._ribbon_common import (
            build_ribbon_segment, build_roll_joint, build_ride_uvpin,
            ROLL_AXES_UPPER, ROLL_AXES_LOWER,
        )

        super().build(instance, context)

        thigh, knee, ankle, ball = list(instance.joints)
        component_node = _resolve_component_node(instance.id)

        opts = instance.options
        persisted = instance.persisted or {}
        segments_persisted = persisted.get('ribbon_segments') or {}
        # Shape-only reset: 'Reset Control Shapes' must blank ONLY the
        # per-ctrl CV map (mirrors every sibling ribbon module's own
        # scoping of this flag — see ribbon_ik_arm.py's identical note). rest_len,
        # resolved ribbon_width, and the board dials are NOT shape state
        # and must survive the flag untouched.
        reset_shapes = bool(context.options.get('reset_ctrl_shapes'))

        mid_count = opts.get('mid_ctrl_count', 1)
        raw_ribbon_width = opts.get('ribbon_width', 0.0)
        base_seg_opts = {
            'ribbon_width': raw_ribbon_width,
            'mid_ctrl_shape': opts.get('ribbon_mid_ctrl_shape', 'sphere'),
            # Empty ribbon_ctrl_color follows the leg's own ctrl_color (the
            # side color: lf=blue, rt=red) — same ''-follows-ctrl_color
            # contract as fingers_ctrl_color; set it only for a distinct
            # ribbon layer. (Adrian 2026-07-11: mids were defaulting yellow.)
            'ctrl_color': opts.get('ribbon_ctrl_color') or opts.get('ctrl_color', 'yellow'),
            # One global-scale reference for both segments (the arm's
            # anti-double-transform doctrine): SimpleIK's anchor_out —
            # registered under THIS instance's id by SimpleIKComponent.
            # build (called via IKLeg's super().build() above, with
            # instance.joints shadowed to 3 internally but instance.id
            # untouched) — verified against the real plug: IKLeg's own
            # build() never overrides or re-registers anchor_out, so the
            # leg exposes the exact same offset_ctrl.worldMatrix[0] plug
            # the arm's own anchor_out resolves to. No deviation from the
            # arm's pattern was needed here.
            '_root_matrix_plug': context.resolve_plug(
                f'{instance.id}.anchor_out'),
        }

        def _seg_opts(seg_name):
            seg_persist = segments_persisted.get(seg_name) or {}
            seg_opts = dict(base_seg_opts)
            # ribbon_width round-trip: when the option is 0.0 (auto) and a
            # prior unbuild resolved+persisted a real width, use THAT
            # instead of re-measuring the current (possibly posed/
            # stretched) joint distance every rebuild.
            if not raw_ribbon_width and seg_persist.get('ribbon_width'):
                seg_opts['ribbon_width'] = seg_persist['ribbon_width']
            seg_opts['_rest_len'] = seg_persist.get('rest_len')
            seg_opts['_cv_data'] = ({} if reset_shapes else
                                    (seg_persist.get('cv_data') or {}))
            seg_opts['_board'] = seg_persist.get('board') or {}
            return seg_opts

        # P3: antCGi aim roll joints (build_roll_joint — leg mapping,
        # SPEC 3.3). Thigh (thigh->knee): driver_parent=None -> roll_aim
        # locator pointConstrained translate-only = FILTERS twist (hip
        # swing must not candy-wrap the upper thigh — the femur/shoulder
        # case). Ankle (knee->ankle): driver_parent=ankle -> locator
        # rigid-parented to the ankle BIND joint = twist PASSES THROUGH
        # and stays available in both IK and FK (reverse-foot yaw
        # included — the metacarpus/wrist case). This is the arm's shipped
        # P3 pattern verbatim, leg joints substituted; ROLL_AXES_UPPER/
        # LOWER's semantics are self-contained to the roll joint's own
        # constraint-solved local frame (build_roll_joint creates `roll`
        # with jointOrient=0 and fully overrides its rotate via the aim
        # constraint every frame — the axis labels are an internal,
        # self-consistent bookkeeping convention, not a read of the bind
        # chain's own joint orientation), so they carry over to the leg's
        # differently-oriented bone directions unchanged; verified
        # against the live test fixture (Task 5) rather than assumed.
        thigh_roll = build_roll_joint(
            thigh, knee, None, ROLL_AXES_UPPER, instance.side,
            component_node)
        ankle_roll = build_roll_joint(
            knee, ankle, ankle, ROLL_AXES_LOWER, instance.side,
            component_node)

        # Twisting ends fed by the roll joints; the shared knee end of
        # each segment is untouched (still the direct elbow-ownership-
        # style knee-bind-joint drive from P2). Every mid ctrl's own
        # up-vector reference ALSO reads its segment's roll joint (via
        # twist_driver_up_axis) so twist reaches the interior
        # falloff-skinned rows too, not just the hard-pinned boundary row
        # — the arm's hard-won rule (build_ribbon_segment's docstring).
        thigh_seg = build_ribbon_segment(
            thigh, knee, mid_count, _seg_opts('thigh'), component_node,
            start_twist_driver=thigh_roll['roll_joint'],
            twist_driver_up_axis=ROLL_AXES_UPPER[1])
        shin_seg = build_ribbon_segment(
            knee, ankle, mid_count, _seg_opts('shin'), component_node,
            end_twist_driver=ankle_roll['roll_joint'],
            twist_driver_up_axis=ROLL_AXES_LOWER[1])

        instance._ribbon_thigh = thigh_seg
        instance._ribbon_shin = shin_seg
        instance._roll_thigh = thigh_roll
        instance._roll_ankle = ankle_roll

        # P4 Task 6 (SPEC 2026-07-09 Limbs + Follower Joints §3.2/3.3,
        # PLAN-AMENDMENT GATE resolved 2026-07-10, partnership +
        # limb-system owner): twist riders from the limb node. Thin-caller
        # idiom (mirrors ribbon_ik_arm.py's own limb_node import inside
        # build()) — resolved LIVE at build time (scene-is-truth, never
        # persisted), so a rename/re-add is picked up transparently on the
        # next build. Confirmed mapping: twist_upper[] = the thigh bone
        # (thigh->knee, thigh_seg above), twist_lower[] = the shin bone
        # (knee->ankle, shin_seg above). The Twist dial (limb_node.
        # limb_set_twist_count) is not called here — this only READS
        # whatever twist_upper[]/twist_lower[] membership already exists
        # on the limb, however it got there (dial or hand-wired).
        #
        # build_ribbon_segment's own INTERNAL ride joints (thigh_seg/
        # shin_seg['ride_joints']) are left completely untouched — they
        # remain the standalone fallback, so no limb (or a limb with empty
        # twist multis) leaves build() bit-identical to P3. Each EXTERNAL
        # twist-joint set is pinned to its OWN segment's surface via a
        # SECOND build_ride_uvpin call per segment, using THAT segment's
        # own returned 'surf'/'setup_grp' — with a '_twist'-suffixed chain
        # name so its pin node (the one node build_ride_uvpin names off
        # `chain` — every other node it creates is named off the JOINT,
        # already collision-free by construction) never collides with
        # that segment's own ride-joint uvPin.
        #
        # No new DG-node bucket needed: build_ride_uvpin hardcodes its own
        # tracking into 'ribbon_dg_nodes' regardless of caller (verified
        # by direct read, Task 6 Step 1) — already in _RIBBON_BUCKETS,
        # already swept unconditionally by unbuild() below, so unbuild()
        # needed zero changes for this task. Step 1 also verified that
        # build_ride_uvpin never message-tracks the JOINTS it drives into
        # any bucket, only the new pin/matrix nodes it creates — so
        # unbuild()'s bucket sweep deletes the pin infrastructure but
        # never the external twist joints themselves, which may carry
        # skin weights (the same guarantee the Twist dial's own skinning
        # guard depends on).
        from maya_tools.rigging.fabricator import limb_node as ln
        limb = ln.find_limb_for_joint(thigh)
        if limb:
            upper_twists = [j for j in ln.list_twist_upper(limb)
                           if cmds.objExists(j)]
            lower_twists = [j for j in ln.list_twist_lower(limb)
                           if cmds.objExists(j)]
            if upper_twists:
                build_ride_uvpin(
                    f"{thigh_seg['chain']}_twist", upper_twists,
                    thigh_seg['surf'], thigh_seg['setup_grp'], component_node)
            if lower_twists:
                build_ride_uvpin(
                    f"{shin_seg['chain']}_twist", lower_twists,
                    shin_seg['surf'], shin_seg['setup_grp'], component_node)

    @classmethod
    def unbuild(cls, instance) -> dict:
        """Capture + tear down both ribbon segments' full node-sets, then
        delegate to IKLeg (reverse foot) -> SimpleIK for FK/IK/PV/switch
        teardown + capture. Mirrors ribbon_ik_arm.py's unbuild exactly —
        the ribbon DG-bucket sweep runs UNCONDITIONALLY (needs only
        component_node, not a clean 4-joint unpack); only the joint-NAME-
        dependent capture/by-name cleanup is guarded behind a clean
        thigh/knee/ankle/ball unpack, so an instance whose joints[] has
        drifted from exactly 4 still gets its full ribbon node-set swept
        instead of leaking as permanent zombie DG nodes."""
        import maya.cmds as cmds
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.utils.maya import network_nodes as nn
        # RENAME-SWAP done (2026-07-09): see build()'s identical note.
        from maya_tools.rigging.fabricator.modules._ribbon_common import (
            ribbon_segment_chain_name,
        )
        from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

        joints = list(instance.joints)
        opts = instance.options
        mid_count = max(1, min(8, int(opts.get('mid_ctrl_count') or 1)))

        component_node = _resolve_component_node(instance.id)

        # ─── Capture (read-only) before anything is deleted — requires
        # the clean thigh/knee/ankle/ball chain-name unpack. ─────────────
        segments_persisted = {}
        if len(joints) == 4:
            thigh, knee, ankle, ball = joints
            for seg_name, start_j, end_j in (('thigh', thigh, knee),
                                             ('shin', knee, ankle)):
                chain = ribbon_segment_chain_name(start_j, end_j)
                cv_data = {}
                for m in range(mid_count):
                    ctrl_name = f'{chain}_mid_{m:02d}_ctrl'
                    if not cmds.objExists(ctrl_name):
                        continue
                    try:
                        sd = com.serialize_shape(ctrl_name)
                        if sd.get('shapes'):
                            cv_data[ctrl_name] = sd['shapes']
                    except RuntimeError:
                        pass

                rest_len = None
                rest_node = f'{chain}_ribbon_vol_rest'
                ratio_node = f'{chain}_ribbon_vol_ratio'
                if cmds.objExists(rest_node):
                    rest_len = float(cmds.getAttr(f'{rest_node}.input1'))
                elif cmds.objExists(ratio_node):
                    rest_len = float(cmds.getAttr(f'{ratio_node}.input2X'))

                ribbon_width = 0.0
                surf = f'{chain}_ribbon_surf'
                if cmds.objExists(f'{surf}.cv[0][0]'):
                    a = cmds.pointPosition(f'{surf}.cv[0][0]', world=True)
                    b = cmds.pointPosition(f'{surf}.cv[1][0]', world=True)
                    ribbon_width = sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5

                # Board dial capture (twist_root/twist_tip/volume/...) —
                # mirrors ribbon_ik_arm.py's unbuild() so rigger-authored
                # dials round-trip instead of silently resetting to
                # BOARD_ATTRS defaults on the next build.
                settings_node = f'{chain}_ribbon_settings'
                board = (rc.capture_board(settings_node)
                        if cmds.objExists(settings_node) else {})

                segments_persisted[seg_name] = {
                    'cv_data': cv_data,
                    'rest_len': rest_len,
                    'ribbon_width': ribbon_width,
                    'board': board,
                }

        # ─── Delete the tracked skin/DG node-sets (both segments share
        # the same 4 ribbon buckets + the roll bucket on this component
        # node) — UNCONDITIONAL: only needs component_node, never the
        # joints[] unpack. ─────────────────────────────────────────────
        if component_node:
            for bucket in _RIBBON_BUCKETS + _ROLL_BUCKETS:
                for n in nn.get_message_targets(component_node, bucket):
                    if cmds.objExists(n) and cmds.nodeType(n) == 'skinCluster':
                        for dp in cmds.listConnections(n, type='dagPose') or []:
                            if cmds.objExists(dp):
                                cmds.delete(dp)
                    if cmds.objExists(n):
                        cmds.delete(n)

        # Mid ctrl buffers live under fab_controls_grp, not in a bucket
        # (matches ribbon_ik_arm.py's own unbuild — defensive by-name
        # cleanup for idempotent rebuilds). By-name cleanup needs the
        # clean thigh/knee/ankle/ball chain names.
        if len(joints) == 4:
            thigh, knee, ankle, ball = joints
            for seg_name, start_j, end_j in (('thigh', thigh, knee),
                                             ('shin', knee, ankle)):
                chain = ribbon_segment_chain_name(start_j, end_j)
                for m in range(mid_count):
                    buf = f'{chain}_mid_{m:02d}_ctrl_offset'
                    if cmds.objExists(buf):
                        cmds.delete(buf)

        captured = super().unbuild(instance)
        captured['ribbon_segments'] = segments_persisted
        captured['mid_ctrl_count'] = mid_count
        return captured
