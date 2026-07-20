# Python/maya_tools/rigging/fabricator/modules/ik_arm.py
"""IKArm — the FREE-core 3-joint IK/FK arm (shoulder, elbow, wrist).

The ribbon arm this strips ships as RibbonIKArm (ribbon_ik_arm.py, paid
Advanced Ribbon pack); this component reuses the vacated 'IKArm' type
string (old scenes migrate on load, version-gated — never assume a
pre-rename 'IKArm' is this component). Design:
docs/superpowers/specs/2026-07-09-basicikarm-module-design.md.

Keeps: SimpleIK's IK/FK/PV/switch/spaces/stretchy (inherited verbatim);
limb-node fingers + metacarpal heuristic + per-finger FK + the layered
fist-curl master (all via _limb_common's shared hand orchestration —
one code path with RibbonIKArm); basic forearm fractional twist
(_build_fractional_twist below — per twist joint, t x the wrist BIND
joint's bone-axis rotation; twist_upper members ride rigid in v1).
Strips: ribbon segments, roll joints, ribbon options. Free/paid law:
this file must never import _ribbon_common / ribbon_* — enforced by
_dev/test_open_core_import_boundary.py.
"""
__author__ = "Adrian Melian"

from maya_tools.rigging.fabricator.modules.component import (
    Contract, JointRole, OptionField,
)
from maya_tools.rigging.fabricator.modules.simple_ik import (
    SimpleIKComponent, SIMPLE_IK_CONTRACT,
)


IK_ARM_CONTRACT = Contract(
    type='IKArm',
    display_name='IK Arm',
    description=(
        'Three-joint IK/FK arm (shoulder, elbow, wrist). SimpleIK parity — '
        'bind + FK + IK + BLEND chains, IK/FK switch, auto-tracked '
        'polevector — plus limb-driven fingers with a layered fist-curl '
        'master and basic forearm twist. The ribbon-deformation arm is '
        'the Advanced Ribbon pack\'s RibbonIKArm.'
    ),
    min_joints=SIMPLE_IK_CONTRACT.min_joints,
    max_joints=SIMPLE_IK_CONTRACT.max_joints,
    parent_strategy=SIMPLE_IK_CONTRACT.parent_strategy,
    inputs=SIMPLE_IK_CONTRACT.inputs,
    outputs=SIMPLE_IK_CONTRACT.outputs,
    options_schema={
        **SIMPLE_IK_CONTRACT.options_schema,
        # IK family color: amber (all IKs are orange shades).
        'ctrl_color': OptionField(type='color_enum', default='amber'),
        'curl_axis': OptionField('enum', 'z', choices=('x', 'y', 'z'),
            description='Local rotate axis every included phalange curls '
                        'about. Uniform across all fingers on this arm.'),
        'finger_fk_shape': OptionField('shape_enum', 'capsule_ramp',
            description='Shape for the per-finger FK ctrls (separate '
                        'from the arm\'s own ctrl_shape so fingers read '
                        'at their smaller scale).'),
        'fingers_ctrl_shape': OptionField('shape_enum', 'sine_handle',
            description='Shape for the fist-curl master ctrl '
                        '(fingers_ctrl), built at the wrist. The default '
                        'sine_handle supplies its own +Y standoff.'),
        'fingers_ctrl_color': OptionField('color_enum', '',
            description='Color for the fist-curl master ctrl. Empty '
                        '(default) follows this arm\'s own ctrl_color; '
                        'set explicitly to override.'),
    },
    side_supported=SIMPLE_IK_CONTRACT.side_supported,
    color='#FFB84D',                    # amber — IK family
    default_region='arm',
    limb_features=('fingers', 'twists'),   # Derived Limbs: arm = fingers + twists
    joint_roles=(
        JointRole('start', 'Shoulder joint'),
        JointRole('mid', 'Elbow joint', descendant_of='start'),
        JointRole('end', 'Wrist joint', descendant_of='mid'),
    ),
    space_consumers=SIMPLE_IK_CONTRACT.space_consumers,
    actions=SIMPLE_IK_CONTRACT.actions,
    mirror_rules=SIMPLE_IK_CONTRACT.mirror_rules,
)

# Fist-curl DG nodes (plusMinusAverage/unitConversion, no DAG parent) and
# the forearm-twist scalar nodes — both OUTSIDE rig_grp, both swept
# explicitly at unbuild (never rely on the rig_grp cascade for these).
_HAND_BUCKETS = ('curl_dg_nodes',)
_TWIST_BUCKETS = ('twist_dg_nodes',)


def _resolve_component_node(instance_id):
    from maya_tools.rigging.fabricator import nodes as ks_nodes
    for cn in ks_nodes.get_all_component_nodes():
        if ks_nodes.get_component_id(cn) == instance_id:
            return cn
    return None


class IKArmComponent(SimpleIKComponent):
    CONTRACT = IK_ARM_CONTRACT

    @classmethod
    def build(cls, instance, context) -> None:
        """SimpleIK parity, then the shared hand orchestration (fingers +
        fist curl — the SAME _limb_common.build_hand_from_limb code path
        RibbonIKArm calls, ribbon_ik_arm.py:406-436), then basic forearm
        fractional twist. No ribbon, no roll joints."""
        import maya.cmds as cmds
        from maya_tools.rigging.fabricator.modules import _limb_common as lc

        super().build(instance, context)

        joints = list(instance.joints)
        if len(joints) != 3 or not all(cmds.objExists(j) for j in joints):
            raise RuntimeError(
                f"IKArm on {instance.id!r} needs 3 existing joints; got {joints}.")
        shoulder, elbow, wrist = joints

        component_node = _resolve_component_node(instance.id)

        # ─── Fingers + fist curl, via the hoisted _limb_common.
        # build_hand_from_limb (Task 2.3 / SPEC 3.2) — one code path, two
        # consumers. This component's own fab_limb node is resolved by
        # joints[0] (the shoulder — every limb-creation path, implicit AND
        # fragment-drop, connects top_joint to the arm's own top joint,
        # never the wrist — see limb_node.create_limb_node callers).
        # IKArmComponent.creates_implicit_limb=True guarantees one exists
        # by the time build() runs for any component created through
        # nodes.create_component_node (every real caller — UI add,
        # template load, mirror, tests); find_limb_for_joint degrading to
        # None here (e.g. a hand-rolled instance bypassing that choke
        # point) is handled by build_hand_from_limb itself: zero
        # membership, fingers_ctrl still builds inert, matching the old
        # "fingers=[]" contract.
        from maya_tools.rigging.fabricator import limb_node as ln
        limb = ln.find_limb_for_joint(shoulder)

        opts = instance.options
        persisted = instance.persisted or {}
        # Shape-only reset: 'Reset Control Shapes' must blank ONLY the
        # per-ctrl CV map, mirroring every sibling module's own scoping of
        # this flag (RibbonIKArm included) — rest_len/board-style state
        # doesn't exist on this free arm's hand, so there's nothing else
        # to preserve here.
        reset_shapes = bool(context.options.get('reset_ctrl_shapes'))

        wrist_ctrl = getattr(instance, '_switch_ctrl', None)
        # fingers_ctrl_color follow-the-group resolution (color-fix
        # semantics carried over from RibbonIKArm, see the OptionField's
        # own docstring above): build_hand_from_limb applies the SAME
        # ''-follow-'ctrl_color' fallback internally, so passing opts
        # through verbatim (rather than pre-resolving here) is equivalent
        # and avoids duplicating the fallback logic.
        hand_opts = dict(opts)
        cv_block = {
            'finger_ctrl_cv_data': (
                {} if reset_shapes else persisted.get('finger_ctrl_cv_data') or {}),
            'fingers_ctrl_cv_data': (
                {} if reset_shapes else persisted.get('fingers_ctrl_cv_data') or {}),
        }
        hand = lc.build_hand_from_limb(
            limb, wrist, wrist_ctrl, hand_opts, component_node, cv_block,
            index_offset=len(joints))
        instance._fingers_built = hand['finger_built']
        instance._fingers_ctrl = hand['fingers_ctrl']

        _build_fractional_twist(instance, component_node)   # SPEC §3.4

    @classmethod
    def unbuild(cls, instance) -> dict:
        """Hand captures + DG-bucket sweeps, then SimpleIK teardown.

        The bucket sweep runs UNCONDITIONALLY — it only needs
        component_node, never a clean shoulder/elbow/wrist unpack (the
        shipped ribbon arm's lesson: gating DG cleanup on len(joints)==3
        leaves zombie DG nodes when joints[] has drifted; only the
        joint-NAME-dependent captures sit behind that guard).

        Finger-side capture mirrors RibbonIKArm.unbuild's Task-2.3 form
        (ribbon_ik_arm.py:571-623): membership is re-read from the limb
        node and each root walked live (lc.walk_finger_chain) — there is
        no stored joint list to consult. Best-effort like every unbuild
        step: a missing/unresolvable limb degrades to "no fingers to
        capture/sweep", never raises. Finger ctrls/offsets themselves are
        rig_grp descendants (via the wrist switch ctrl) and cascade away
        with it; only the constraints left on the bind finger JOINTS and
        the bucket-tracked DG nodes (curl plusMinusAverage/unitConversion,
        Task-5 twist scalars) live outside rig_grp and need explicit
        handling here.
        """
        import maya.cmds as cmds
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
        from maya_tools.utils.maya import network_nodes as nn
        from maya_tools.rigging.fabricator.modules import _limb_common as lc

        joints = list(instance.joints)
        component_node = _resolve_component_node(instance.id)

        # ─── Finger ctrl CV capture + bind-joint constraint sweep ────────
        finger_cv_data = {}
        finger_joint_lists = []
        if len(joints) == 3:
            from maya_tools.rigging.fabricator import limb_node as ln
            limb = ln.find_limb_for_joint(joints[0])
            if limb:
                for root in ln.list_finger_roots(limb):
                    if cmds.objExists(root):
                        finger_joint_lists.append(lc.walk_finger_chain(root))
        for finger_joints in finger_joint_lists:
            for j in finger_joints:
                if not cmds.objExists(j):
                    continue
                ctrl = f'{lc.short_name(j)}_ctrl'
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

        # ─── fingers_ctrl (fist master) CV capture — deterministic name
        # from the wrist joint alone, same convention as the ribbon arm ──
        fingers_ctrl_cv_data = {}
        if len(joints) == 3:
            wrist = joints[2]
            fc_name = f'{lc.short_name(wrist)}_fingers_ctrl'
            if cmds.objExists(fc_name):
                try:
                    sd = com.serialize_shape(fc_name)
                    if sd.get('shapes'):
                        fingers_ctrl_cv_data[fc_name] = sd['shapes']
                except RuntimeError:
                    pass

        # ─── Curl + twist DG buckets — UNCONDITIONAL sweep ───────────────
        if component_node:
            for bucket in _HAND_BUCKETS + _TWIST_BUCKETS:
                for n in nn.get_message_targets(component_node, bucket):
                    if cmds.objExists(n):
                        cmds.delete(n)

        captured = super().unbuild(instance)
        captured['finger_ctrl_cv_data'] = finger_cv_data
        captured['fingers_ctrl_cv_data'] = fingers_ctrl_cv_data
        return captured


def _bone_axis_letter(joint):
    """Thin delegate — hoisted to _limb_common.bone_axis_letter for the
    free-leg twist pass (2026-07-10; the shipped-arm review's relocation
    ask, bone-generic by construction). Kept as a module name so the
    arm's tests and any external callers stay valid."""
    from maya_tools.rigging.fabricator.modules import _limb_common as lc
    return lc.bone_axis_letter(joint)


def _build_fractional_twist(instance, component_node):
    """SPEC §3.4 (Adrian 2026-07-09): the most basic forearm twist —
    twist_lower members rotate t x the wrist BIND's bone-axis rotation;
    twist_upper members ride rigid in v1 (deliberate free-tier trade —
    the ribbon arm is the upgrade). Body hoisted to _limb_common.
    build_fractional_twist (2026-07-10, free-leg twist pass) — see that
    helper for the full wiring/tracking contract; this stays the arm's
    thin caller (member resolution + segment endpoints)."""
    # limb_node.py lives at the fabricator ROOT, not in limbs/.
    from maya_tools.rigging.fabricator import limb_node as ln
    from maya_tools.rigging.fabricator.modules import _limb_common as lc

    _shoulder, elbow, wrist = list(instance.joints)
    limb = ln.find_limb_for_joint(_shoulder)
    members = ln.list_twist_lower(limb) if limb else []
    return lc.build_fractional_twist(
        members, elbow, wrist, wrist, component_node,
        f"IKArm {instance.id!r}")
