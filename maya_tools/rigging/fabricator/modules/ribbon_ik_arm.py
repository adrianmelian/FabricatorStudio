# Python/maya_tools/rigging/fabricator/modules/ribbon_ik_arm.py
"""RibbonIKArm — 3-joint IK/FK arm (shoulder, elbow, wrist) with per-bone ribbon
twist segments driven by antCGi aim+IK roll joints.

Paid Advanced Ribbon pack. Formerly IKArm — renamed (commercial seam split,
mechanical rename) to free 'IKArm' for a future free basic-arm component. A
version-gated legacy-type mapping in modules/__init__.py loads any old
blueprint/scene component_type='IKArm' data as RibbonIKArm.

Subclass of SimpleIKComponent, following the same subclass seam IKLeg uses
over SimpleIK (see ik_leg.py). Phase 1 proved the subclass seam (contract
mirrors SimpleIK's 3-joint IK/FK/PV chain verbatim). Phase 2 added two
per-bone ribbon segments — upper-arm (shoulder->elbow) and forearm
(elbow->wrist) — each a mini-RibbonSpine (driven ends + N floating mid
ctrls + falloff-skin twist distribution + composed volume preservation),
built via modules/_ribbon_common.py:build_ribbon_segment. No sine/jiggle.

Phase 3 (this file, current state) builds one antCGi-inspired aim+IK roll
joint per bone (modules/_ribbon_common.py:build_roll_joint — ROLL-METHOD.md
§2, NOT quaternion swing-twist) and feeds its clean twist into that
segment's TWISTING END — replacing Phase 2's direct-bind-joint twist drive
at that one end (SPEC 2026-07-08-ikarm-ribbon-module-design.md §3.3). The
shoulder roll drives the upper segment's start end; the forearm/wrist roll
(whose roll_aim locator is parented to the wrist BIND joint) drives the
forearm segment's wrist end, so pronation twist stays available in both IK
and FK. What actually filters (shoulder) vs. passes through (wrist) twist
is the roll_aim locator's WORLD POSITION relationship to its reference
joint (pointConstraint translate-only vs. rigid DAG parent) — see
build_roll_joint's docstring for the empirically-verified mechanism.
Cleanup (2026-07-08): build_roll_joint's follower/follow_tip/ikHandle/
pole-vector graph, proven inert, has been stripped entirely; the roll
joint and its roll_aim locator are now also explicitly hidden from the
animator's viewport (visibility off).

Phase 4 (this file, current state) adds skeleton-only finger FK ownership:
fingers are discovered (modules/_limb_common.py:discover_fingers) at create
time from the wrist joint's child chains into the editable 'fingers' option
(RibbonIKArmComponent.default_options_for_create, hooked into the component-add
path via fs_app._default_options_for_create), and RibbonIKArm.build() builds a
tagged FK ctrl for every member joint — including metacarpals — chain-
parented under the wrist's IK/FK switch ctrl (_limb_common.py:
build_finger_fk_chain, reusing the same per-joint helper FKChain's own
build() uses — modules/_chain_common.py:build_fk_joint_ctrl). No curl
wiring yet.

Phase 5 (this file, current state) adds the layered fist-curl master:
one 'fingers_ctrl' near the wrist (modules/_limb_common.py:
build_fingers_ctrl) whose local curl-axis rotation sums into every
INCLUDED member phalange's <joint>_ctrl_offset curl axis, so a
per-finger animator key on <joint>_ctrl composes on top rather than
overriding it. curl_axis is a single, uniform RIBBON_IK_ARM_CONTRACT option;
curl_excluded joints (metacarpals) get no curl input.

Phase 6 (this file, current state, Adrian 2026-07-09 follow-up — color
fix): fingers_ctrl used to always build a fixed red, regardless of the
arm's own side-derived group color (bug: a blue-group left arm's fist
ctrl built red, mismatching the rest of the arm). 'fingers_ctrl_color'
now defaults to '' (unset) and RibbonIKArmComponent.build resolves it to THIS
instance's own 'ctrl_color' — the same option every other arm ctrl
(FK/IK/PV/switch) reads for its color — whenever no explicit override is
authored, so fingers_ctrl matches the group's color on any side without
re-deriving side itself. An explicit non-empty 'fingers_ctrl_color'
still always wins.

See SPEC.md / PLAN.md (workspace/2026-07-08_ikarm-ribbon-module) for the
full design.
"""
__author__ = "Adrian Melian"

from maya_tools.rigging.fabricator.modules.component import Contract, OptionField
from maya_tools.rigging.fabricator.modules.simple_ik import (
    SimpleIKComponent, SIMPLE_IK_CONTRACT,
)


# Phase 2: RibbonIKArm's contract is SimpleIK's, plus four new ribbon options
# (mid_ctrl_count/ribbon_width mirror RibbonSpine's fields verbatim;
# ribbon_mid_ctrl_shape/ribbon_ctrl_color are dedicated fields so ribbon
# mid ctrls don't just inherit SimpleIK's FK-ctrl-flavored ctrl_shape/
# ctrl_color — SPEC 3.1: "the ribbon ctrl shape/color fields (mirror
# RibbonSpine)"). Fingers/curl_axis land in P4.
RIBBON_IK_ARM_CONTRACT = Contract(
    type='RibbonIKArm',
    display_name='Ribbon IK Arm',
    description=(
        'Three-joint IK/FK arm (shoulder, elbow, wrist). SimpleIK parity — '
        'bind + FK + IK + BLEND joint chains, IK/FK switch, auto-tracked '
        'polevector — plus two per-bone ribbon segments (upper-arm, '
        'forearm) with twist + volume + N floating mid ctrls, twisting '
        'ends driven by antCGi aim+IK roll joints. Later phases add an '
        'owned finger/fist-curl rig.'
    ),
    min_joints=SIMPLE_IK_CONTRACT.min_joints,
    max_joints=SIMPLE_IK_CONTRACT.max_joints,
    parent_strategy=SIMPLE_IK_CONTRACT.parent_strategy,
    inputs=SIMPLE_IK_CONTRACT.inputs,
    outputs=SIMPLE_IK_CONTRACT.outputs,
    options_schema={
        **SIMPLE_IK_CONTRACT.options_schema,
        # Ribbon family color: mint (all ribbons are green shades).
        'ctrl_color': OptionField(type='color_enum', default='mint'),
        'mid_ctrl_count': OptionField('int', 1, range=(1, 8),
            description='Floating mid controls per ribbon segment '
                        '(upper-arm and forearm each get this many). '
                        'Build option: change in Edit Mode and rebuild.'),
        'ribbon_width': OptionField('float', 0.0, range=(0.0, 1000.0),
            description='Ribbon cross-width for both segments. 0.0 = '
                        'auto — 25% of the bone\'s length (each RibbonIKArm '
                        'ribbon segment spans exactly one bone, so '
                        '_ribbon_common.resolve_ribbon_width\'s shared '
                        'max(10% of total, 25% of the shortest sub-'
                        'segment) formula collapses to 25% here, not '
                        '10% — see that helper for the general-chain '
                        'formula). Resolved value persisted so rebuilds '
                        'match.'),
        'ribbon_mid_ctrl_shape': OptionField('shape_enum', 'sphere',
            description='Shape for the floating ribbon mid ctrls.'),
        'ribbon_ctrl_color': OptionField('color_enum', '',
            description='Color for the floating ribbon mid ctrls. Empty '
                        '(default) follows this arm\'s own ctrl_color (its '
                        'side color); set a value only to make the ribbon '
                        'ctrls read as a distinct layer in the viewport.'),
        # P4/Task 2.3: finger membership used to live here as a name-
        # string 'fingers' option (list[{root_joint, joints[],
        # curl_excluded[]}]), auto-discovered at create time and hand-
        # edited afterward via option_widgets.FingerListField. RETIRED
        # (SPEC 2026-07-09 Limbs + Follower Joints §3.4, "Always-limb
        # membership"): membership now lives ONLY on this component's
        # fab_limb node (limb_node.py's finger_roots[]/curl_excluded[]
        # message multis, resolved via limb_node.find_limb_for_joint and
        # walked live at build time by _limb_common.build_hand_from_limb)
        # — rename- and reorder-proof, and an inserted joint auto-joins
        # its finger on the next rebuild. Pre-1.2.0 blueprint/scene data
        # still carrying this option is migrated one-time on load (see
        # fs_app._migrate_legacy_finger_membership) and the option is
        # dropped from stored options afterward.
        #
        # P5 (PLAN.md Task 5.1 / SPEC 3.5): the layered fist-curl master.
        # curl_axis is a SINGLE, uniform RibbonIKArm-level option (not
        # per-finger) — consistent finger-joint orientation is the
        # rigger's responsibility (SPEC 3.5). fingers_ctrl_shape is a
        # dedicated field (mirrors the ribbon_* precedent above) so the
        # fist master reads as its own distinct layer in the viewport
        # rather than inheriting the finger FK ctrls' own ctrl_shape.
        #
        # fingers_ctrl_color (P6 color fix, Adrian 2026-07-09 follow-up):
        # UNLIKE fingers_ctrl_shape, this does NOT get its own fixed
        # default color — it defaults to '' ("unset"), which
        # RibbonIKArmComponent.build resolves to THIS SAME instance's own
        # 'ctrl_color' option (the exact option every other arm ctrl —
        # FK/IK/PV/switch, see simple_ik.py's build()) reads for its
        # color, side-derived at add time by fs_window._auto_detect_
        # component_meta (lf->blue, rt->red, md->yellow). Before this
        # fix, fingers_ctrl_color's schema default was a fixed 'red',
        # so the fist ctrl built red on EVERY side/rig regardless of the
        # rest of the arm's group color (bug: a blue-group left arm's
        # fist ctrl built red). '' is never a real color_enum choice
        # (CTRL_COLORS in ui/option_widgets.py has no empty entry), so
        # it's a safe "no explicit override" sentinel — an explicit
        # non-empty value here still always wins (genuine per-ctrl
        # override, e.g. to make the fist master a distinct color from
        # the rest of the group on purpose).
        'curl_axis': OptionField('enum', 'z', choices=('x', 'y', 'z'),
            description='Local rotate axis every included phalange '
                        'curls about. Uniform across all fingers on '
                        'this arm — pick whichever axis your finger '
                        'joints are oriented to flex on.'),
        'finger_fk_shape': OptionField('shape_enum', 'capsule_ramp',
            description='Shape for the per-finger FK ctrls (separate '
                        'from the arm\'s own ctrl_shape so fingers read '
                        'at their smaller scale).'),
        'fingers_ctrl_shape': OptionField('shape_enum', 'sine_handle',
            description='Shape for the fist-curl master ctrl '
                        '(fingers_ctrl), built near the wrist. Defaults '
                        'to the CtrlEditor "sine_handle" library '
                        'shape (Adrian\'s manual-shifter-knob, authored '
                        'for this ctrl) — its own +Y handle geometry '
                        'supplies the standoff from the wrist anchor, so '
                        'the ctrl pivot sits AT the wrist. Still a plain '
                        'shape_enum — pick any other library shape here '
                        'if preferred.'),
        'fingers_ctrl_color': OptionField('color_enum', '',
            description='Color for the fist-curl master ctrl '
                        '(fingers_ctrl). Empty (default) follows this '
                        'arm\'s own \'ctrl_color\' option — the same '
                        'source every other arm ctrl (FK/IK/PV/switch) '
                        'reads for its color — so fingers_ctrl matches '
                        'the rest of the group\'s color on any side. Set '
                        'explicitly to a specific color to override that '
                        'and make the fist ctrl its own distinct color '
                        'regardless of ctrl_color/side.'),
    },
    side_supported=SIMPLE_IK_CONTRACT.side_supported,
    color='#80E699',                        # mint — ribbon family, lightest
    default_region='arm',                   # pose library auto-set tag
    limb_features=('fingers', 'twists'),    # Derived Limbs: arm = fingers + twists
    joint_roles=(
        SIMPLE_IK_CONTRACT.joint_roles[0].__class__('start', 'Shoulder joint'),
        SIMPLE_IK_CONTRACT.joint_roles[1].__class__(
            'mid', 'Elbow joint', descendant_of='start'),
        SIMPLE_IK_CONTRACT.joint_roles[2].__class__(
            'end', 'Wrist joint', descendant_of='mid'),
    ),
    space_consumers=SIMPLE_IK_CONTRACT.space_consumers,
    actions=SIMPLE_IK_CONTRACT.actions,
    mirror_rules=SIMPLE_IK_CONTRACT.mirror_rules,
)

# DG node-set buckets shared by BOTH ribbon segments on the same component
# node (same convention RibbonSpineComponent uses for its own single
# segment) — build_ribbon_segment message-tracks into these; unbuild
# sweeps all four in one pass, cleaning up both segments together.
_RIBBON_BUCKETS = ('ribbon_dg_nodes', 'ribbon_skin_nodes',
                   'ribbon_board_nodes', 'control_joints')

# P3: the antCGi roll-joint rigs (build_roll_joint) for BOTH bone segments
# land in this single bucket. The roll joint is a child of a bind joint and
# the locator is a child of fab_nulls_grp or driver_parent — none of them are
# under rig_grp, so (like _RIBBON_BUCKETS) the rig_grp cascade never catches
# them; swept explicitly alongside the ribbon buckets in the same
# unconditional pass below. (Cleanup 2026-07-08: build_roll_joint's
# follower/follow_tip/ikHandle graph, proven inert, was stripped — only the
# roll joint, its locator, and their constraints ever land in this bucket
# now.)
_ROLL_BUCKETS = ('roll_dg_nodes',)

# P5: the fist-curl master's plusMinusAverage(sum) nodes (_limb_common.
# build_fingers_ctrl's "authored baseline" branch) land here. These are
# plain DG nodes with no DAG parent (unlike fingers_ctrl/its offset,
# which ARE rig_grp descendants via the wrist switch ctrl and cascade
# away for free) — swept explicitly alongside the ribbon/roll buckets.
_CURL_BUCKETS = ('curl_dg_nodes',)


def _resolve_component_node(instance_id):
    from maya_tools.rigging.fabricator import nodes as ks_nodes
    for cn in ks_nodes.get_all_component_nodes():
        if ks_nodes.get_component_id(cn) == instance_id:
            return cn
    return None


class RibbonIKArmComponent(SimpleIKComponent):
    CONTRACT = RIBBON_IK_ARM_CONTRACT

    # RibbonIKArm carries finger membership on its fab_limb node's
    # finger_roots[]/curl_excluded[] — limb identity + membership are
    # DERIVED via CONTRACT.limb_features at every seam (Derived Limbs
    # spec 2026-07-11; the old creates_implicit_limb bool is retired).

    # can_apply is inherited from SimpleIKComponent verbatim — a 3-joint
    # parent-chain check is exactly what a shoulder/elbow/wrist arm needs.
    # No override needed.

    # default_options_for_create: RETIRED (Task 2.3). Used to seed the
    # (now-retired) 'fingers' option at ADD time by discovering the
    # wrist's child chains; that discovery is now nodes._maybe_create_
    # implicit_limb's job, seeding finger_roots[]/curl_excluded[] on this
    # component's limb node directly instead — it runs on EVERY
    # create_component_node call (not just the UI's add path), so
    # create-time coverage is a strict superset of what this hook used to
    # provide. No override needed; the Component base's default (no
    # extra options) is correct.

    @classmethod
    def build(cls, instance, context) -> None:
        """SimpleIK parity, then per-bone antCGi roll joints (P3), then two
        per-bone ribbon segments (upper-arm shoulder->elbow, forearm
        elbow->wrist) whose twisting ends are driven by those roll joints."""
        import maya.cmds as cmds
        from maya_tools.rigging.fabricator.modules import _limb_common as hc
        from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

        super().build(instance, context)

        joints = list(instance.joints)
        if len(joints) != 3 or not all(cmds.objExists(j) for j in joints):
            raise RuntimeError(
                f"RibbonIKArm on {instance.id!r} needs 3 existing joints; got {joints}.")
        shoulder, elbow, wrist = joints

        component_node = _resolve_component_node(instance.id)

        # ─── PV ctrl aim-at-elbow — promoted to SimpleIKComponent.build
        # (2026-07-09) ──────────────────────────────────────────────────
        # No longer wired here. SimpleIK's own build() now calls
        # hc.aim_pv_at_mid(pv_ctrl, instance._blend_chain[1], ...) for
        # every SimpleIK-family limb (SimpleIK/IKLeg/RibbonIKArm alike),
        # targeting the TRUE blend-chain elbow — the behavior originally
        # asked for, and one this file's own earlier attempt couldn't
        # safely reach (it targeted the FK-chain elbow instead, as a
        # documented cycle workaround — see git history / hc.aim_pv_at_mid
        # for the empirical writeup). That workaround is obsolete: Task 1's
        # fix to SimpleIK's Schleifer pin mechanism
        # (_build_ctrl_world_position_reader — pin distances now read
        # pv_ctrl's world POSITION only, never its full worldMatrix) severed
        # the DG edge that made the blend-elbow target cycle in the first
        # place. See _dev/test_ik_arm_maya.py's
        # test_ikarm_pv_ctrl_aims_at_fk_elbow_and_tracks_fk_poses (name kept
        # for history; now asserts against the blend elbow) and
        # test_ikarm_pv_ctrl_aim_no_cycle_and_unbuild_clean for the
        # regression coverage.

        opts = instance.options
        persisted = instance.persisted or {}
        segments_persisted = persisted.get('ribbon_segments') or {}
        # Shape-only reset: 'Reset Control Shapes' must blank ONLY the
        # per-ctrl CV map, mirroring every sibling module's own scoping of
        # this flag (SimpleIK/world/fk_chain/advanced_fk/fk_aim/simple_fk/
        # RibbonSpine/ribbon/spline_fk all scope it to shape/cv_data only).
        # rest_len, resolved ribbon_width, and the board dials are NOT
        # shape state and must survive the flag untouched — wiping the
        # WHOLE segments_persisted dict here silently re-baselined volume
        # rest length (and, pre-fix, discarded twist/volume dials too) from
        # whatever pose the arm happened to be in at reset time.
        reset_shapes = bool(context.options.get('reset_ctrl_shapes'))

        mid_count = opts.get('mid_ctrl_count', 1)
        raw_ribbon_width = opts.get('ribbon_width', 0.0)
        base_seg_opts = {
            'ribbon_width': raw_ribbon_width,
            'mid_ctrl_shape': opts.get('ribbon_mid_ctrl_shape', 'sphere'),
            # Empty ribbon_ctrl_color follows the arm's own ctrl_color (the
            # side color: lf=blue, rt=red) — same ''-follows-ctrl_color
            # contract as fingers_ctrl_color; set it only for a distinct
            # ribbon layer. (Adrian 2026-07-11: mids were defaulting yellow.)
            'ctrl_color': opts.get('ribbon_ctrl_color') or opts.get('ctrl_color', 'yellow'),
            # anchor_out is SimpleIK's offset_ctrl worldMatrix — the same
            # node the whole arm's parent-scale chain flows through, so
            # both ribbon segments share ONE global-scale reference. Using
            # this (rather than each segment's own start_joint) is what
            # keeps global rig scale from being double-read as stretch.
            '_root_matrix_plug': context.resolve_plug(f'{instance.id}.anchor_out'),
        }

        def _seg_opts(seg_name):
            seg_persist = segments_persisted.get(seg_name) or {}
            seg_opts = dict(base_seg_opts)
            # ribbon_width round-trip: when the option is 0.0 (auto) and a
            # prior unbuild resolved+persisted a real width, use THAT
            # instead of re-measuring the current (possibly posed/
            # stretched) joint distance every rebuild — the field's own
            # docstring promise ("Resolved value persisted so rebuilds
            # match") and SPEC.md §4's explicit round-trip requirement.
            if not raw_ribbon_width and seg_persist.get('ribbon_width'):
                seg_opts['ribbon_width'] = seg_persist['ribbon_width']
            seg_opts['_rest_len'] = seg_persist.get('rest_len')
            seg_opts['_cv_data'] = ({} if reset_shapes else
                                    (seg_persist.get('cv_data') or {}))
            seg_opts['_board'] = seg_persist.get('board') or {}
            return seg_opts

        # ─── P3: antCGi-inspired aim+IK roll joints (ROLL-METHOD.md §2) ────
        # Upper (shoulder->elbow): driver_parent=None — the roll_aim
        # locator's position is pointConstrained (translate-only) to the
        # shoulder, FILTERING OUT twist (the anti-candy-wrap case; a plain
        # bend must not twist the shoulder roll). Lower/forearm
        # (elbow->wrist): driver_parent=wrist — the locator rigidly parents
        # to the wrist BIND joint instead, so pronation twist passes
        # through and stays available in both IK and FK (ROLL-METHOD §2
        # lower-bone note; SPEC 3.3). See build_roll_joint's docstring for
        # why the locator's WORLD POSITION (not any joint's rotation) is
        # what actually does the filtering/pass-through here.
        upper_roll = rc.build_roll_joint(
            shoulder, elbow, None, rc.ROLL_AXES_UPPER, instance.side,
            component_node)
        forearm_roll = rc.build_roll_joint(
            elbow, wrist, wrist, rc.ROLL_AXES_LOWER, instance.side,
            component_node)

        # ─── Two ribbon segments, twisting ends fed by the roll joints ────
        # Upper segment's START (shoulder-side) reads the upper roll joint;
        # forearm segment's END (wrist-side) reads the forearm roll joint —
        # SPEC 3.2. The shared elbow end of each segment is untouched
        # (still the direct P2 elbow-bind-joint drive — elbow-ownership,
        # _ribbon_common.build_ribbon_segment docstring). Every mid
        # ctrl's own up-vector reference ALSO reads its segment's roll
        # joint now (not just the twisting-end boundary control joint), so
        # twist reaches the interior falloff-skinned rows too — MUST pass
        # the SAME up_vector given to build_roll_joint for that roll
        # joint (ROLL_AXES_UPPER[1]/ROLL_AXES_LOWER[1]), since a roll
        # joint's twist-sensitive local axis differs from a plain bind
        # joint's (see build_ribbon_segment's twist_driver_up_axis
        # note).
        upper = rc.build_ribbon_segment(
            shoulder, elbow, mid_count, _seg_opts('upper'), component_node,
            start_twist_driver=upper_roll['roll_joint'],
            twist_driver_up_axis=rc.ROLL_AXES_UPPER[1])
        forearm = rc.build_ribbon_segment(
            elbow, wrist, mid_count, _seg_opts('forearm'), component_node,
            end_twist_driver=forearm_roll['roll_joint'],
            twist_driver_up_axis=rc.ROLL_AXES_LOWER[1])

        instance._ribbon_upper = upper
        instance._ribbon_forearm = forearm
        instance._roll_upper = upper_roll
        instance._roll_forearm = forearm_roll

        # ─── Task 2.3: always-limb membership -> finger FK -> fist curl,
        # via the hoisted _limb_common.build_hand_from_limb (SPEC
        # 2026-07-09 Limbs + Follower Joints §3.4 + §3.2's standing
        # BasicIKArm request) ──────────────────────────────────────────
        # This component's own fab_limb node is resolved by joints[0]
        # (the shoulder — every limb-creation path, implicit AND
        # fragment-drop, connects top_joint to the ARM's own top joint,
        # never the wrist — see limb_node.create_limb_node callers).
        # RibbonIKArm.creates_implicit_limb=True guarantees one exists by
        # the time build() runs for any component created through
        # nodes.create_component_node (every real caller — UI add,
        # template load, mirror, tests); find_limb_for_joint degrading to
        # None here (e.g. a hand-rolled instance bypassing that choke
        # point) is handled by build_hand_from_limb itself: zero
        # membership, fingers_ctrl still builds inert, matching the old
        # "fingers=[]" contract.
        from maya_tools.rigging.fabricator import limb_node as ln
        limb = ln.find_limb_for_joint(shoulder)

        wrist_ctrl = getattr(instance, '_switch_ctrl', None)
        # fingers_ctrl_color follow-the-group resolution (color fix,
        # Adrian 2026-07-09 follow-up — see the OptionField's own
        # docstring above): an explicit non-empty 'fingers_ctrl_color'
        # always wins (genuine per-ctrl override); the '' schema default
        # falls through to THIS SAME instance's 'ctrl_color' — the exact
        # option every other arm ctrl (FK/IK/PV/switch) reads for its own
        # color (simple_ik.py's build()) — so fingers_ctrl matches the
        # rest of the group's color by default, correctly on any side,
        # without re-deriving side here: side-awareness lives entirely in
        # 'ctrl_color' itself (populated at add time by fs_window.
        # _auto_detect_component_meta — lf->blue, rt->red, md->yellow).
        # build_hand_from_limb applies the SAME 'fingers_ctrl_color' or
        # 'ctrl_color' fallback internally, so passing opts through
        # verbatim (rather than pre-resolving here) is equivalent and
        # avoids duplicating the fallback logic.
        hand_opts = dict(opts)
        cv_block = {
            'finger_ctrl_cv_data': (
                {} if reset_shapes else persisted.get('finger_ctrl_cv_data') or {}),
            'fingers_ctrl_cv_data': (
                {} if reset_shapes else persisted.get('fingers_ctrl_cv_data') or {}),
        }
        hand = hc.build_hand_from_limb(
            limb, wrist, wrist_ctrl, hand_opts, component_node, cv_block,
            index_offset=len(joints))
        instance._fingers_built = hand['finger_built']
        instance._fingers_ctrl = hand['fingers_ctrl']

    @classmethod
    def unbuild(cls, instance) -> dict:
        """Capture + tear down both ribbon segments' full node-sets, then
        delegate to SimpleIK for the FK/IK/PV/switch teardown + capture.

        The ribbon DG-bucket sweep below runs UNCONDITIONALLY — it only
        needs `component_node` (resolved independently of the joints[]
        unpack), not a clean shoulder/elbow/wrist triple. A prior version
        of this method early-returned `super().unbuild(instance)` whenever
        `len(joints) != 3`, which skipped the ENTIRE `_RIBBON_BUCKETS`
        sweep too — conflating "can't safely derive chain names for the
        capture" with "skip all DG cleanup". Those are separable: an
        instance whose joints[] has drifted from exactly 3 (e.g. an
        unrepaired duplicate/mirror overconnection propagating `.message`
        into the joints[] multi — COMPONENT_AUTHORING §4 Gotcha 1, and
        `find_overconnected_components()`'s own reason for existing) would
        otherwise leave both segments' full skinCluster/tweak/groupParts/
        groupId/objectSet node-sets, uvPin/matrix ride chains, twist-board
        blendShapes, and volume chains as permanent zombie DG nodes (they
        have no DAG parent, so the `rig_grp` cascade never catches them).
        Only the joint-NAME-dependent work — the cv_data/rest_len/
        ribbon_width/board CAPTURE, and the by-name mid-ctrl-buffer
        cleanup, both of which need the shoulder/elbow/wrist chain-name
        derivation — stays guarded behind `len(joints) == 3`.
        """
        import maya.cmds as cmds
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.utils.maya import network_nodes as nn
        from maya_tools.rigging.fabricator.modules import _limb_common as hc
        from maya_tools.rigging.fabricator.modules import _ribbon_common as rc

        joints = list(instance.joints)
        opts = instance.options
        mid_count = max(1, min(8, int(opts.get('mid_ctrl_count') or 1)))

        component_node = _resolve_component_node(instance.id)

        # ─── Capture (read-only) before anything is deleted — requires the
        # clean shoulder/elbow/wrist chain-name unpack. ──────────────────
        segments_persisted = {}
        if len(joints) == 3:
            shoulder, elbow, wrist = joints
            for seg_name, start_j, end_j in (('upper', shoulder, elbow),
                                             ('forearm', elbow, wrist)):
                chain = rc.ribbon_segment_chain_name(start_j, end_j)
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
                # mirrors ribbon_spine.py's unbuild() so rigger-authored
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

        # ─── Delete the tracked skin/DG node-sets (both segments share the
        # same 4 ribbon buckets + the roll bucket on this component node) —
        # UNCONDITIONAL: only needs component_node, never the joints[]
        # unpack. Roll nodes (P3) are plain joints/locator/constraints, never
        # skinClusters, so the type-specific branch below is a no-op for
        # them — the same loop sweeps both node-sets safely. ─────────────
        if component_node:
            for bucket in _RIBBON_BUCKETS + _ROLL_BUCKETS + _CURL_BUCKETS:
                for n in nn.get_message_targets(component_node, bucket):
                    if cmds.objExists(n) and cmds.nodeType(n) == 'skinCluster':
                        for dp in cmds.listConnections(n, type='dagPose') or []:
                            if cmds.objExists(dp):
                                cmds.delete(dp)
                    if cmds.objExists(n):
                        cmds.delete(n)

        # Mid ctrl buffers live under fab_controls_grp, not in a bucket
        # (matches RibbonSpine's own unbuild — defensive by-name cleanup
        # for idempotent rebuilds; rig_grp cascade would also catch these).
        # By-name cleanup needs the clean shoulder/elbow/wrist chain names.
        if len(joints) == 3:
            shoulder, elbow, wrist = joints
            for seg_name, start_j, end_j in (('upper', shoulder, elbow),
                                             ('forearm', elbow, wrist)):
                chain = rc.ribbon_segment_chain_name(start_j, end_j)
                for m in range(mid_count):
                    buf = f'{chain}_mid_{m:02d}_ctrl_offset'
                    if cmds.objExists(buf):
                        cmds.delete(buf)

        # ─── P4: finger ctrl CV capture + bind-joint constraint cleanup ──
        # Mirrors FKChainComponent.unbuild's own pattern exactly: finger
        # ctrls/offsets/null-pairs are DAG descendants of rig_grp (via the
        # wrist switch ctrl they're chain-parented under), so they
        # cascade-delete with rig_grp — no dedicated node-set bucket
        # needed (unlike the ribbon/roll subsystems, which live OUTSIDE
        # rig_grp). Only the constraints left on each bind finger JOINT
        # (also outside rig_grp, like every other component's bind-joint
        # constraints) need an explicit sweep here.
        #
        # Task 2.3: membership is read from the limb node's
        # finger_roots[], each walked live (the SAME hc.walk_finger_chain
        # build() itself uses) — the retired 'fingers' option can no
        # longer supply a frozen joint list here. Best-effort like every
        # other unbuild step: a missing/unresolvable limb degrades to "no
        # fingers to capture/sweep", never raises.
        finger_cv_data = {}
        finger_joint_lists = []
        if len(joints) == 3:
            from maya_tools.rigging.fabricator import limb_node as ln
            limb = ln.find_limb_for_joint(joints[0])
            if limb:
                for root in ln.list_finger_roots(limb):
                    if cmds.objExists(root):
                        finger_joint_lists.append(hc.walk_finger_chain(root))
        for finger_joints in finger_joint_lists:
            for j in finger_joints:
                if not cmds.objExists(j):
                    continue
                ctrl = f'{hc.short_name(j)}_ctrl'
                if cmds.objExists(ctrl):
                    try:
                        sd = com.serialize_shape(ctrl)
                        if sd.get('shapes'):
                            finger_cv_data[ctrl] = sd['shapes']
                    except RuntimeError:
                        pass
                constraints = (
                    (cmds.listRelatives(j, type='parentConstraint') or [])
                    + (cmds.listRelatives(j, type='scaleConstraint') or [])
                )
                if constraints:
                    cmds.delete(constraints)

        # ─── P5: fingers_ctrl (fist-curl master) CV capture ──────────────
        # Deterministic name lookup (hc.build_fingers_ctrl derives it from
        # the wrist joint alone — same convention the ribbon segments' own
        # by-name capture above uses) — no dedicated node-set bucket
        # needed for the ctrl/offset themselves (DAG descendants of
        # rig_grp via the wrist switch ctrl, cascade-deleted with it,
        # exactly like the finger ctrls above); only its authored SHAPE
        # needs explicit capture to round-trip (SPEC §4).
        fingers_ctrl_cv_data = {}
        if len(joints) == 3:
            _shoulder, _elbow, wrist = joints
            fc_name = f'{hc.short_name(wrist)}_fingers_ctrl'
            if cmds.objExists(fc_name):
                try:
                    sd = com.serialize_shape(fc_name)
                    if sd.get('shapes'):
                        fingers_ctrl_cv_data[fc_name] = sd['shapes']
                except RuntimeError:
                    pass

        captured = super().unbuild(instance)
        captured['ribbon_segments'] = segments_persisted
        captured['mid_ctrl_count'] = mid_count
        captured['finger_ctrl_cv_data'] = finger_cv_data
        captured['fingers_ctrl_cv_data'] = fingers_ctrl_cv_data
        return captured
