# Python/maya_tools/rigging/fabricator/components/follow_joint.py
"""FollowJoint — passive constraint follower. No ctrl, no UI surface.

Use cases:
- UE5 engine reference joints (ik_foot_*, ik_hand_*, ik_hand_gun) that
  need to follow their counterpart joints in the rig file so the FBX
  animation export carries them as keyed engine refs.
- Helper joints, prop attachers, or any other "this joint follows that
  matrix" use case.

The component reads its source matrix via parent_plug (resolved by
BuildContext) and applies parentConstraint + scaleConstraint. Skip flags
let the user opt out of t/r/s channels per instance.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator.modules.component import (
    Component, Contract, Plug, OptionField, JointRole,
)


FOLLOW_JOINT_CONTRACT = Contract(
    type='FollowJoint',
    display_name='Follow Joint',
    description=(
        'Constrains a joint to follow another joint. Pick the target in '
        "the Properties panel's Follow Target dropdown. For UE5 engine "
        'reference joints (ik_foot_*, ik_hand_*) and helper-joint follow '
        'setups. Leaving the target unset is a no-op (component does '
        'nothing — useful when the rig has a placeholder FollowJoint).'
    ),
    min_joints=1, max_joints=1,
    parent_strategy='walk_up',           # required by Contract; not used (no ctrl)
    inputs=(
        # Optional now — follow_target option supersedes this. Kept for
        # back-compat with rigs that wired parent_plug before the option
        # existed; build falls back to parent_plug only when
        # follow_target is empty.
        Plug(name='parent_in', kind='matrix', required=False,
             description='Optional fallback source matrix. follow_target option takes precedence.'),
    ),
    outputs=(
        Plug(name='joint_out', kind='matrix', space_target=False,
             description='Joint world matrix (post-constraint).'),
    ),
    options_schema={
        'follow_target':    OptionField('joint_picker', '',
                                         description='Joint this component should follow. Empty = no constraint (no-op build).'),
        'maintain_offset':  OptionField('bool', True,
                                         description='Preserve current offset between source and joint.'),
        'translate_axes':   OptionField('xyz_axes', (True, True, True),
                                         description='Which translation channels to drive. Master "All" toggles X/Y/Z together.'),
        'translate_alpha':  OptionField('float', 1.0, range=(0.0, 1.0),
                                         description='Translation follow weight (0 = no influence, 1 = fully follow).'),
        'rotate_axes':      OptionField('xyz_axes', (True, True, True),
                                         description='Which rotation channels to drive. Master "All" toggles X/Y/Z together.'),
        'rotate_alpha':     OptionField('float', 1.0, range=(0.0, 1.0),
                                         description='Rotation follow weight (0 = no influence, 1 = fully follow).'),
    },
    side_supported=False,
    color='#888888',                      # gray — passive helper
    joint_roles=(JointRole('joint', 'Follower joint'),),
)


def _coerce_axes(value) -> tuple:
    """Normalize an xyz_axes option value to a (bool, bool, bool) tuple.

    Accepts: tuple/list of 3 (the canonical form), or a single bool /
    int (legacy `translate=True` / `translate=False` mapping — all-on
    or all-off). Anything else falls back to all-on.
    """
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(bool(v) for v in value)
    if isinstance(value, (bool, int)):
        flag = bool(value)
        return (flag, flag, flag)
    return (True, True, True)


class FollowJointComponent(Component):
    CONTRACT = FOLLOW_JOINT_CONTRACT

    @classmethod
    def build(cls, instance, context) -> None:
        from maya_tools.rigging.fabricator import nodes as ks_nodes
        from maya_tools.utils.maya import network_nodes as nn

        joint = instance.joints[0]
        if not cmds.objExists(joint):
            raise RuntimeError(f"FollowJoint: joint {joint!r} doesn't exist.")

        opts = instance.options
        mo = bool(opts.get('maintain_offset', True))

        # Resolve source. follow_target option wins; parent_plug is the
        # back-compat fallback for rigs authored before the option existed.
        # Empty / unresolved → no-op build (still register output so
        # downstream consumers don't break).
        follow_target = (opts.get('follow_target') or '').strip()
        source_node = ''
        if follow_target:
            if cmds.objExists(follow_target):
                source_node = follow_target
            else:
                # Set-but-missing must NOT kill the whole Build Rig
                # (engine-IK design session, 2026-07-10): a FollowJoint
                # whose target name isn't in this scene (engine-IK
                # joints on a non-UE-named skeleton, a renamed
                # counterpart) degrades to the same loud no-op the
                # unset case gets — the joint builds unconstrained and
                # the warning names the expected target so the user can
                # wire or rename it later.
                cmds.warning(
                    f"FollowJoint: follow_target {follow_target!r} "
                    f"doesn't exist — building {joint!r} unconstrained "
                    f"(fix the name or repick in Properties)."
                )
        elif instance.parent_plug:
            source_plug = context.resolve_plug(instance.parent_plug)
            candidate = source_plug.split('.')[0]
            if cmds.objExists(candidate):
                source_node = candidate
            else:
                cmds.warning(
                    f"FollowJoint: parent_plug source {candidate!r} doesn't "
                    f"exist — skipping constraints (set follow_target in "
                    f"Properties to pick a target)."
                )

        if not source_node:
            # No target — silent no-op. Register output as joint's own
            # worldMatrix so downstream consumers can still reference it.
            context.register_output(instance.id, 'joint_out',
                                    f'{joint}.worldMatrix[0]')
            return

        # Per-axis skip lists for translate / rotate. xyz_axes stores
        # (x, y, z); skip the axes that are False.
        t_axes = _coerce_axes(opts.get('translate_axes', (True, True, True)))
        r_axes = _coerce_axes(opts.get('rotate_axes',    (True, True, True)))
        skip_t = [ax for ax, on in zip(('x', 'y', 'z'), t_axes) if not on]
        skip_r = [ax for ax, on in zip(('x', 'y', 'z'), r_axes) if not on]

        # Clamp alphas to [0, 1] in case persisted data is out of range.
        t_alpha = max(0.0, min(1.0, float(opts.get('translate_alpha', 1.0))))
        r_alpha = max(0.0, min(1.0, float(opts.get('rotate_alpha',    1.0))))

        # pointConstraint + orientConstraint split (instead of a single
        # parentConstraint) so translate and rotate can carry independent
        # weights. Skip all three axes → don't build that constraint at all.
        tcon = None
        if skip_t != ['x', 'y', 'z'] and t_alpha > 0.0:
            tcon = cmds.pointConstraint(
                source_node, joint, mo=mo, skip=skip_t, weight=t_alpha,
            )[0]
        rcon = None
        if skip_r != ['x', 'y', 'z'] and r_alpha > 0.0:
            rcon = cmds.orientConstraint(
                source_node, joint, mo=mo, skip=skip_r, weight=r_alpha,
            )[0]

        # Track on component network node for unbuild cleanup.
        component_node = None
        for cn in ks_nodes.get_all_component_nodes():
            if ks_nodes.get_component_id(cn) == instance.id:
                component_node = cn
                break
        if component_node:
            for con in (tcon, rcon):
                if con:
                    nn.connect_message_multi(con, component_node,
                                              'follow_joint_nodes')
        else:
            cmds.warning(
                f'FollowJoint: no component node for {instance.id!r} — '
                f'constraints not tracked; unbuild will leak them.'
            )

        # Register output.
        context.register_output(instance.id, 'joint_out',
                                f'{joint}.worldMatrix[0]')

    # ─── Follow-rule linkage (SPEC 2026-07-09 Limbs + Follower Joints §3.1,
    # rule source (c): "auto-derived from an attached FollowJoint component
    # — adding a FollowJoint targeting X seeds a match(X) rule on that joint
    # so Edit-mode armature behavior matches what the built rig will do.
    # Component-derived rules show as 'linked' in the Follow block ...
    # removing the component clears the linked rule."). These are ARMATURE/
    # EDIT-mode previews only — build() above is the actual rig behavior and
    # doesn't read follow_rules at all; the two are independent, deliberately
    # redundant expressions of the same follow_target.

    @classmethod
    def on_added(cls, node) -> None:
        """nodes.create_component_node's lifecycle hook. Seeds a linked
        match(follow_target) rule if follow_target is already set at add
        time (e.g. a mirrored/programmatically-created instance) — the
        common UI add (empty follow_target default) is a no-op here and
        gets seeded later via on_options_changed once the rigger picks a
        target."""
        from maya_tools.rigging.fabricator import nodes as ks_nodes
        cls._seed_linked_rule(node, ks_nodes.get_component_options(node))

    @classmethod
    def on_removed(cls, node) -> None:
        """nodes.delete_component_node's lifecycle hook. Clears ONLY a
        linked rule on this instance's joint — a rigger-authored
        (linked=False) rule is left in place; the joint just stops being
        FollowJoint-driven, same as any other joint carrying a hand-
        authored rule."""
        from maya_tools.rigging.fabricator import nodes as ks_nodes
        from maya_tools.rigging.fabricator import follow_rules as fr

        joints = ks_nodes.get_component_joints(node)
        if not joints:
            return
        joint = joints[0]
        # rule_linked_flag (not get_follow_rule) — a rule whose target
        # has since been deleted still has to be recognized as a rule
        # here; get_follow_rule would raise on it and a naive except
        # would degrade it to "no rule", leaking this component's own
        # broken linked rule on remove (same root cause as the
        # never-clobber bug in _seed_linked_rule below).
        if fr.rule_linked_flag(joint):
            fr.clear_follow_rule(joint)
            cls._engage_armature()   # ctrl comes back immediately

    @classmethod
    def on_options_changed(cls, node, old_options, new_options) -> None:
        """nodes.set_component_options's lifecycle hook. Re-seeds the
        linked rule to match a CHANGED follow_target (option edit in
        Properties, post-add — SPEC 3.1's "if FollowJoint's target can
        change after add, re-seed"). No-op if follow_target didn't
        change (some other option was edited)."""
        old_target = (old_options.get('follow_target') or '').strip()
        new_target = (new_options.get('follow_target') or '').strip()
        if old_target == new_target:
            return
        cls._seed_linked_rule(node, new_options)

    @classmethod
    def _seed_linked_rule(cls, node, options) -> None:
        """Shared by on_added/on_options_changed: make this instance's
        joint carry a linked match(follow_target) rule matching
        `options['follow_target']`, UNLESS a rigger-authored rule is
        already there (warn + keep it — never clobber, per SPEC 3.1).

        An empty follow_target clears a LINKED rule (the component is
        now a no-op, same as build()'s own empty-target no-op) but still
        never touches an authored one.
        """
        from maya_tools.rigging.fabricator import nodes as ks_nodes
        from maya_tools.rigging.fabricator import follow_rules as fr

        joints = ks_nodes.get_component_joints(node)
        if not joints:
            return
        joint = joints[0]
        if not cmds.objExists(joint):
            return

        target = (options.get('follow_target') or '').strip()

        # rule_linked_flag — NOT get_follow_rule/except RuntimeError.
        # get_follow_rule raises when a rule's target has been deleted
        # (follow_rules.py's own docstring), which is exactly the
        # "authored rule, currently unresolvable" case this guard must
        # still catch. Degrading that to "no rule" here would let the
        # set_follow_rule() call below silently clobber (delete) an
        # authored rule that just happens to be broken right now — the
        # never-clobber invariant (SPEC 3.1) doesn't have an exception
        # for "broken". linked is None (no rule at all), True (this
        # component's own prior rule — safe to replace), or False (a
        # rigger-authored rule, broken or not — never touch it here).
        linked = fr.rule_linked_flag(joint)

        if not target:
            # Nothing to seed — checked BEFORE the authored-rule guard
            # below so the common empty-target add near an unrelated
            # authored rule stays a true no-op (no spurious warning);
            # only ever CLEARS a rule this component itself put there.
            if linked:
                fr.clear_follow_rule(joint)
                cls._engage_armature()   # ctrl comes back immediately
            return

        if linked is not None and not linked:
            cmds.warning(
                f"FollowJoint: {joint!r} already carries a legacy hand-"
                f"authored follow rule — leaving it in place (never-"
                f"clobber). Clear it via follow_rules.clear_follow_rule "
                f"in the Script Editor if you want this component's "
                f"target to drive it."
            )
            return

        if not cmds.objExists(target):
            cmds.warning(
                f"FollowJoint: follow_target {target!r} on {joint!r} "
                f"doesn't exist — not seeding a follow rule."
            )
            return

        fr.set_follow_rule(joint, 'match', (target,), linked=True)
        cls._engage_armature()

    @classmethod
    def _engage_armature(cls) -> None:
        """Engage the follow on the live Armature IMMEDIATELY (Adrian
        2026-07-12): a rule seeded or cleared mid-session changes which
        joints get ctrls, but the standing Armature was built before
        this rule existed — the joint's old ctrl would keep fighting the
        follow until the next rebuild. If an Armature is up, rebuild it
        now so the ruled joint sheds its ctrl (or a cleared joint gets
        one back) the moment the target is picked. Best-effort + batch-
        safe: no Armature standing (e.g. template-load's component loop,
        which rebuilds afterwards anyway) is a silent no-op. Batch
        callers (Build Engine IK's six adds) suspend this via
        armature.engage_suspended() and run ONE rebuild at the end —
        rebuilding mid-batch against a half-mutated scene is the state
        the fingertip-collapse bug (2026-07-14) fired in."""
        try:
            from maya_tools.rigging.fabricator import armature
            if armature.is_engage_suspended():
                return
            if armature.armature_exists():
                armature.build_armature()
        except Exception:
            import traceback
            traceback.print_exc()

    @classmethod
    def unbuild(cls, instance) -> dict:
        from maya_tools.rigging.fabricator import nodes as ks_nodes
        from maya_tools.utils.maya import network_nodes as nn

        # Capture nothing — no CV data, no animator state to preserve.
        captured = {'starting_shape': '', 'cv_data': {}, 'enum_orders': {}}

        # Delete tracked constraints.
        component_node = None
        for cn in ks_nodes.get_all_component_nodes():
            if ks_nodes.get_component_id(cn) == instance.id:
                component_node = cn
                break
        if component_node:
            for n in nn.get_message_targets(component_node, 'follow_joint_nodes'):
                if cmds.objExists(n):
                    cmds.delete(n)

        return captured
