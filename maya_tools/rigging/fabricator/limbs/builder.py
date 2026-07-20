"""Apply a parsed LimbFragment to a host rig at a chosen target joint.

The load pipeline:
  1. Require Edit Mode (MODE_SKELETON) — rejects in Animation Mode
     (MODULES_BUILT) because limb load is a structural rig edit.
  2. Find the host component owning target_joint.
  3. Validate no joint-name or component-id collisions.
  4. Rewrite <EXTERNAL> placeholders to point at the host component.
  5. Create joints (root reparented under target_joint).
  6. Create network nodes for each component.

After load the host rig is still in SKELETON mode — user runs Build
Modules manually to materialize the new ctrls. This keeps the Edit
Mode pillar honest and avoids the "rebuild blows away animation"
hazard of in-place attaching to a built rig.
"""
__author__ = "Adrian Melian"

import traceback

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes as ks_nodes
from maya_tools.rigging.fabricator.limbs.schema import (
    LimbFragment, EXTERNAL_PLACEHOLDER,
)


def _member_attach_plug(cls, idx: int, n_joints: int, names: set):
    """Resolve the output plug a child should attach to when dropped on the
    member joint at index `idx` of a host component with `n_joints` joints.

    Contract-driven, not component-specific:
      * idx 0 (primary joint)        -> the component's first output plug
        (its main attach point; unchanged from the joints[0]-only era).
      * last member AND a 'tip_out'  -> 'tip_out'. RibbonSpine documents
        tip_out as the "neck/arm parent" (the chest CONTROL), the right
        parent for a limb landing on the top of the chain — better than
        the raw ribbon-following bind joint.
      * interior member with a       -> 'joint_out_<idx>'. RibbonSpine
        declared 'joint_out_<idx>'      exposes one parent-space output per
                                         bind joint precisely so an interior
                                         joint (which has no control node of
                                         its own) is an exact attach point.
      * anything else                -> None. The component exposes no
        attach point for this member joint; the caller treats a None here
        as "not a valid drop target" and keeps looking / errors out. This
        preserves the old restriction for components (e.g. a plain arm)
        that never declared per-joint outputs.
    """
    if idx == 0:
        return cls.CONTRACT.outputs[0].name
    if idx == n_joints - 1 and 'tip_out' in names:
        return 'tip_out'
    cand = f'joint_out_{idx}'
    if cand in names:
        return cand
    return None


def find_host_component_for_joint(target_joint: str):
    """Return (component_node, output_plug_name) for the component owning
    target_joint, or None if no component exposes an attach point there.

    Attach points (see _member_attach_plug):
      * A component's PRIMARY joint (joints[0]) -> its first output plug.
      * Any INTERIOR/last member joint of a component that declares a
        per-joint / tip output for it (RibbonSpine's joint_out_<i> /
        tip_out) -> that plug. This is the documented "hang a clavicle/
        arm off an interior spine joint like spine_04" case (RibbonSpine
        CONTRACT) — the joints[0]-only restriction that used to live here
        contradicted that contract and rejected exactly it.

    Two passes so an exact primary-joint match always wins over an
    interior-membership match on a different component (the joint tree is
    1:1 in practice, but this keeps the pre-membership behavior byte-for-
    byte for every joints[0] drop).
    """
    from maya_tools.rigging.fabricator.modules import get_component_class

    comps = list(ks_nodes.get_all_component_nodes())

    # Pass 1: exact primary-joint match (unchanged legacy behavior).
    for cnode in comps:
        joints = ks_nodes.get_component_joints(cnode) or []
        if joints and joints[0] == target_joint:
            try:
                cls = get_component_class(ks_nodes.get_component_type(cnode))
            except KeyError:
                cls = None
            if cls is None or not cls.CONTRACT.outputs:
                return cnode, None
            return cnode, cls.CONTRACT.outputs[0].name

    # Pass 2: interior/last membership on a component that exposes an
    # attach plug for exactly that joint.
    for cnode in comps:
        joints = ks_nodes.get_component_joints(cnode) or []
        if target_joint not in joints:
            continue
        try:
            cls = get_component_class(ks_nodes.get_component_type(cnode))
        except KeyError:
            cls = None
        if cls is None or not cls.CONTRACT.outputs:
            continue
        names = {p.name for p in cls.CONTRACT.outputs}
        plug = _member_attach_plug(cls, joints.index(target_joint),
                                   len(joints), names)
        if plug is not None:
            return cnode, plug
    return None


def _collision_check_joints(fragment: LimbFragment) -> list:
    """Return list of limb joint names that already exist in the scene."""
    return [j.name for j in fragment.skeleton_joints if cmds.objExists(j.name)]


def _collision_check_component_ids(fragment: LimbFragment) -> list:
    """Return list of component ids that collide with existing host components."""
    host_ids = {ks_nodes.get_component_id(c) for c in ks_nodes.get_all_component_nodes()}
    return [c.id for c in fragment.components if c.id in host_ids]


def _resolve_fragment_component_ids(fragment: LimbFragment) -> None:
    """Auto-derive any missing `ComponentSpec.id` (blueprint/schema.py's
    own docstring promise: "None = auto-derive at load") via the
    component class's own `default_id(joints)` — the SAME documented
    pattern every other id-derivation call site in the codebase uses
    (e.g. `fs_app.load`: `cid = cdata.id if cdata.id else cls.
    default_id(cdata.joints)`, modules/component.py). Runs BEFORE
    `_collision_check_component_ids` below, so its result is what that
    check (and every later step in `apply_limb_fragment` that reads
    `c.id`) actually sees — without this, a None id sails past that
    check untouched (`None in host_ids` is just False) and crashes much
    later, inside `nodes.create_component_node`'s `_safe_node_name`
    (`re.sub` on a non-string).

    An id this function derives is uniquified against every existing
    HOST component id — the same `host_ids` set `_collision_check_
    component_ids` reads — via a plain incrementing numeric suffix,
    Maya's own auto-uniquify convention (name, name1, name2, ...) that
    `limb_node._uniquify_finger_fragment` already uses for joint names
    in this same fragment-drop pipeline. Also uniquified against every
    OTHER id already resolved earlier in this same fragment, so two
    id-less components that would otherwise derive the identical
    default (e.g. sharing a joints[0]) never collide with each other
    either.

    An EXPLICIT id (already set on the ComponentSpec) is left
    completely untouched here — colliding with the host rig is still a
    hard error, raised by `_collision_check_component_ids` right after
    this function returns. Only an AUTO-DERIVED id silently uniquifies:
    nothing forces a specific name in that case, so silently picking the
    next free one is strictly more useful than raising.

    Mutates `fragment.components` in place — every later step in
    `apply_limb_fragment` (the id-collision check, the `create_
    component_node` loop, `_link_fragment_components_to_limb`'s `find_
    component_node_by_id` lookup) reads `c.id` directly, same as for a
    fragment that shipped with every id already authored.

    Best-effort on class resolution only (an unregistered `c.type` is a
    pre-existing, separately-surfaced problem elsewhere in this
    pipeline, not this function's job to raise on): falls back to a
    plain lower-cased type string so a bad type still gets SOME
    deterministic, uniquified id rather than crashing this function
    outright.
    """
    from maya_tools.rigging.fabricator.modules import get_component_class

    taken = {ks_nodes.get_component_id(c) for c in ks_nodes.get_all_component_nodes()}
    for c in fragment.components:
        if c.id:
            taken.add(c.id)
            continue
        try:
            base = get_component_class(c.type).default_id(c.joints)
        except Exception:
            base = (c.type or 'component').lower()
        candidate = base
        n = 0
        while candidate in taken:
            n += 1
            candidate = f'{base}{n}'
        c.id = candidate
        taken.add(candidate)


def apply_limb_fragment(fragment: LimbFragment, target_joint: str) -> None:
    """Append fragment to the host rig under target_joint.

    Atomic — raises on any validation failure with the host rig unchanged.

    Runs the whole drop under armature_watch.suspended(): a limb drop
    creates its joints (cmds.select+cmds.joint), component/limb nodes and
    stands the Armature up — the same SelectionChanged storm that stalled
    template load, where every event that reaches the live canvas callback
    forces a canvas + properties rebuild MID-DROP (the "super long pause").
    build_armature() at the tail already suspends the watch; widen it to
    the entire drop, where it belongs. suspended() is depth-counted, so the
    inner build_armature() suspend nests cleanly.
    """
    from maya_tools.rigging.fabricator import armature_watch
    with armature_watch.suspended():
        _apply_limb_fragment_impl(fragment, target_joint)


def _apply_limb_fragment_impl(fragment: LimbFragment, target_joint: str) -> None:
    """Body of apply_limb_fragment — see the watch-suspension wrapper."""
    from maya_tools.rigging.fabricator.ui import state as fab_state
    mode = fab_state.detect_mode()
    if mode != fab_state.MODE_SKELETON:
        raise RuntimeError(
            f'Limb load requires Edit Mode (skeleton) — current mode is '
            f'{mode!r}. Unbuild modules first, drop the limb, then Build '
            f'Modules to materialize the new ctrls alongside the rig.'
        )

    if not cmds.objExists(target_joint):
        raise RuntimeError(f'Target joint not in scene: {target_joint!r}')

    if not ks_nodes.get_registry():
        raise RuntimeError(
            'No fab_registry in the scene. Load or create a blueprint '
            'before applying a limb.'
        )

    # ComponentSpec.id docstring's own promise ("None = auto-derive at
    # load") — must run before the id-collision check just below, and
    # before every other step that reads c.id.
    _resolve_fragment_component_ids(fragment)

    # Host-plug resolution (+ the parent_plug rewrite below) exists ONLY
    # to wire fragment.components' root(s) — a skeleton-only fragment
    # (PLAN.md 2026-07-08 Task 5.2, LimbFragment.components == []) has
    # nothing to wire, so it must not be blocked by
    # find_host_component_for_joint's joints[0]-only "primary joint"
    # restriction (see that function's own docstring). This matters
    # concretely for P5's finger limb: fingers anchor at a hand's WRIST
    # joint, which is joints[-1] (not joints[0]) on an RibbonIKArm
    # (shoulder, elbow, wrist) — requiring joints[0] here would wrongly
    # reject exactly the "drop a finger limb onto the wrist" scenario
    # Task 5.3's auto-add hook (below) exists to support.
    host_cnode = host_cid = None
    if fragment.components:
        host = find_host_component_for_joint(target_joint)
        if host is None:
            raise RuntimeError(
                f"Drop target {target_joint!r} has no owning Fabricator component. "
                f"Drop onto a joint that's a primary joint of a built component."
            )
        host_cnode, host_plug = host
        if host_plug is None:
            raise RuntimeError(
                f"Host component {ks_nodes.get_component_id(host_cnode)!r} declares "
                f"no output plugs — can't attach a limb to it."
            )
        host_cid = ks_nodes.get_component_id(host_cnode)

    joint_collisions = _collision_check_joints(fragment)
    if joint_collisions:
        preview = joint_collisions[:5]
        more = f' (and {len(joint_collisions) - 5} more)' if len(joint_collisions) > 5 else ''
        raise RuntimeError(
            f'{len(joint_collisions)} joint(s) already exist in scene{more}: '
            f'{preview}. Rename source rig before re-saving.'
        )

    id_collisions = _collision_check_component_ids(fragment)
    if id_collisions:
        preview = id_collisions[:5]
        more = f' (and {len(id_collisions) - 5} more)' if len(id_collisions) > 5 else ''
        raise RuntimeError(
            f'{len(id_collisions)} component id(s) already exist in scene{more}: '
            f'{preview}.'
        )

    if fragment.components:
        from maya_tools.rigging.fabricator.modules import get_component_class
        try:
            host_cls = get_component_class(ks_nodes.get_component_type(host_cnode))
        except KeyError:
            host_cls = None
        available = {p.name for p in host_cls.CONTRACT.outputs} if host_cls else set()

        for c in fragment.components:
            if c.parent_plug.startswith(f'{EXTERNAL_PLACEHOLDER}.'):
                _, _, requested_plug = c.parent_plug.partition('.')
                chosen_plug = requested_plug if requested_plug in available else host_plug
                if requested_plug and requested_plug not in available:
                    cmds.warning(
                        f'Limb expected host plug {requested_plug!r} but host '
                        f'component exposes {sorted(available)!r}. Falling back '
                        f'to {chosen_plug!r}.'
                    )
                c.parent_plug = f'{host_cid}.{chosen_plug}'

    _create_limb_joints(fragment, target_joint)

    # Derived Limbs (spec 2026-07-11): NO fragment-side limb bookkeeping
    # remains. Each component's own create_component_node call below
    # runs nodes.derive_limb — a featured component (arm/leg) creates
    # its limb at its own top joint and derives fingers/twists from the
    # just-created subtree; unfeatured components (a clavicle SimpleFK,
    # a spine) carry no limb identity at all. The old fragment-limb
    # trio (_create_limb_node_for_fragment / _link_fragment_components_
    # to_limb / _apply_limb_block) and the `limb:` YAML block are
    # retired: a saved fragment's fingers and twist joints are plain
    # skeleton joints, re-discovered/re-adopted on every drop.
    from maya_tools.rigging.fabricator.modules import resolve_component_type
    for c in fragment.components:
        live_joints = [j for j in c.joints if cmds.objExists(j)]
        # Resolve the type against the FILE's own era (fragment parity,
        # 2026-07-20): the live scene's registry stamp can't date a
        # dropped file — the scene is current, the file may be ribbon-era
        # ('IKArm' pre-1.1.0). fragment.fabricator_version '' = unstamped
        # pre-versioning file, so the version-gated legacy map applies;
        # a stamped fragment's types pass through untouched.
        ctype = resolve_component_type(
            c.type, data_version=fragment.fabricator_version)
        ks_nodes.create_component_node(
            component_id=c.id,
            component_type=ctype,
            joints=live_joints,
            joint_names=list(c.joints),
            parent_plug=c.parent_plug,
            side=c.side,
            role=c.role,
            region=c.region,
            options=c.options,
            persisted=c.persisted,
        )

    _setup_limb_aimers(fragment, target_joint)

    # Fresh limb joints go straight into the _Joints reference layer —
    # visible, viewport-unselectable (Armature ctrls are the interface).
    from maya_tools.rigging.fabricator import fs_app
    fs_app.add_joints_to_reference_layer(
        [j.name for j in fragment.skeleton_joints])

    # Derived Limbs (spec 2026-07-11): a skeleton-only drop can land new
    # finger/twist joints inside an EXISTING limb's subtree (e.g. a
    # finger chain dropped on a wrist, or pre-authored twist joints).
    # Re-derive every component so the owning limb picks the new joints
    # up immediately — the same pass blueprint load and build preflight
    # run (replaces the bespoke _auto_register_finger_chain). MUST run
    # BEFORE armature.build_armature() below (same ordering as
    # fs_app._load_impl): twist adoption assigns each twist joint its
    # follow rule, and the Armature's ctrl loop skips follow-ruled
    # joints — deriving after the build would leave a fresh twist joint
    # with a stray Armature ctrl AND a follow rule fighting over it
    # (2026-07-11 review finding).
    for _cnode in ks_nodes.get_all_component_nodes():
        ks_nodes.derive_limb(_cnode)

    # Refresh the Armature so the new limb is immediately editable
    # (spec 2026-07-04 §2 — joints exist wrapped in the Armature).
    from maya_tools.rigging.fabricator import armature
    armature.build_armature()

    # Re-assert the AUTHORED ctrl placement over everything build_
    # armature() just re-derived (LimbFragment.armature_ctrls docstring)
    # — the "drops back in the exact place it was saved from" contract.
    _restore_fragment_ctrl_transforms(fragment)


def _restore_fragment_ctrl_transforms(fragment: LimbFragment) -> None:
    """Set each fresh Armature ctrl to its saved LOCAL transform
    (fragment.armature_ctrls — see that field's docstring for why the
    ctrls, not the joint TRS snapshot, are the authored truth).

    Root-first order (fragment.skeleton_joints is written root-first by
    the save snapshot) so a parent ctrl is placed before its children's
    locals are asserted on top. Joints follow live through their SC-IK /
    pointConstraint edges — no extra push needed — EXCEPT follow-ruled
    joints (twists), which have no ctrl and re-derive through
    follow_rules.evaluate(); their live attributeChanged hook honors
    armature_watch.is_muted() and the whole drop runs suspended, so they
    are evaluated explicitly here.

    Per-ctrl best-effort: a locked channel or missing ctrl skips that
    entry rather than failing the drop — the joint then simply keeps the
    (already correct-shape) skeleton_joints placement.
    """
    data = fragment.armature_ctrls or {}
    if not data:
        return  # legacy fragment — pre-armature_ctrls behavior, untouched
    from maya_tools.rigging.fabricator import armature, follow_rules

    for jspec in fragment.skeleton_joints:
        trs = data.get(jspec.name)
        if not trs:
            continue
        ctrl = armature.ctrl_for_joint(jspec.name)
        if not ctrl:
            continue
        for attr in ('translate', 'rotate'):
            vals = trs.get(attr)
            if not vals or len(vals) != 3:
                continue
            try:
                if cmds.getAttr(f'{ctrl}.{attr}', settable=True):
                    cmds.setAttr(f'{ctrl}.{attr}', *vals)
            except Exception:
                cmds.warning(f'[Fabricator] limb drop: could not restore '
                             f'{ctrl}.{attr} — skipped.')

    ruled = [j.name for j in fragment.skeleton_joints
             if follow_rules.get_follow_rule(j.name)]
    if ruled:
        follow_rules.evaluate(ruled)


# (Derived Limbs, spec 2026-07-11: the fragment-drop limb bookkeeping
# quartet — _create_limb_node_for_fragment, _link_fragment_components_
# to_limb, _apply_limb_block, _auto_register_finger_chain — is retired.
# nodes.derive_limb at component creation + the post-drop derive-all
# pass in _apply_limb_fragment_impl replace all four.)


def _setup_limb_aimers(fragment: LimbFragment, target_joint: str) -> None:
    """Aimers for the freshly dropped limb (spec 2026-07-04 §5+§7):
    every new joint gets an aimer restored to its persisted state
    (aim_target by label, rotation offset), falling back to creation
    defaults for unset joints. The HOST joint's aimer is rebuilt because
    its child list just changed — the aimTarget enum is baked at
    creation and would otherwise go stale.

    Two creation defaults, same split armature._bake_and_restore_aimers
    uses at build time (2026-07-20): a TWIST joint (childless, distribute
    -ruled) gets the negative-parent convention — aim at Parent, flip 180
    RZ so it lands on the segment end (Adrian's 2026-07-13 trick) —
    because geometric detection only ever targets a CHILD and a twist has
    none, leaving it stranded at Local. Everything else keeps geometric
    ±aim seeding. The build-time pass can't cover dropped twists: it only
    touches aimers created FRESH during that build, and the drop creates
    them here first."""
    from maya_tools.rigging.joint_orient import joint_orient_app as joa
    from maya_tools.rigging.fabricator import armature

    # Host gained a child — rebuild its enum, state preserved by label.
    if joa.aimer_exists(target_joint):
        joa.rebuild_aimer(target_joint)

    for jspec in fragment.skeleton_joints:
        if not cmds.objExists(jspec.name):
            continue
        fresh = not joa.aimer_exists(jspec.name)
        if fresh:
            joa.create_aimer(jspec.name)
        target = jspec.aim_target
        offset = list(jspec.aim_offset or [0.0, 0.0, 0.0])
        if not target and fresh:
            # Fragment carries no aimer data — creation defaults.
            if armature.is_twist_joint(jspec.name):
                joa.point_aimer_at_parent_flipped(jspec.name)
            else:
                # Geometric ±aim seeding (the seeder captures a mirror
                # flip into the offset).
                joa.seed_aimer_from_detection(jspec.name)
        else:
            joa.apply_aimer_state(jspec.name, aim_target=target,
                                  aim_offset=offset)


def _create_limb_joints(fragment: LimbFragment, target_joint: str) -> None:
    """Create joints from fragment.skeleton_joints. Root parent='<EXTERNAL>'
    gets reparented under target_joint. Iterates in saved order — Task 2's
    snapshot writes root-first so parents always exist before children."""
    rotate_order_names = ['xyz', 'yzx', 'zxy', 'xzy', 'yxz', 'zyx']
    for jspec in fragment.skeleton_joints:
        parent = target_joint if jspec.parent == EXTERNAL_PLACEHOLDER else jspec.parent
        cmds.select(clear=True)
        new_joint = cmds.joint(name=jspec.name)
        if parent and cmds.objExists(parent):
            new_joint = cmds.parent(new_joint, parent)[0]
        cmds.setAttr(f'{new_joint}.translate', *jspec.translate)
        cmds.setAttr(f'{new_joint}.rotate', *jspec.rotate)
        cmds.setAttr(f'{new_joint}.jointOrient', *jspec.joint_orient)
        if jspec.rotate_order in rotate_order_names:
            cmds.setAttr(f'{new_joint}.rotateOrder',
                         rotate_order_names.index(jspec.rotate_order))
        cmds.setAttr(f'{new_joint}.radius', jspec.radius)
