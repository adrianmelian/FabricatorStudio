# Python/maya_tools/rigging/fabricator/modules/_limb_common.py
"""Free-side limb helpers, not a component of their own.

FREE/PAID BOUNDARY (commercial seam split): this module is the FREE side.
It may be imported by any component, free or paid (simple_ik.py, nodes.py,
limbs/builder.py, ui/option_widgets.py, ribbon_ik_arm.py, ...). It must
NEVER import _ribbon_common (the paid Advanced Ribbon pack's substrate) —
that would leak a paid-only dependency into free code paths. The reverse
is fine: _ribbon_common.py and ribbon_ik_arm.py both import THIS module
freely (e.g. for short_name).

History: this file used to also own the per-bone ribbon-segment builder
(build_arm_ribbon_segment, now build_ribbon_segment), the antCGi aim+IK
roll-joint builder (build_roll_joint, ROLL_AXES_UPPER/LOWER,
_ROLL_OFFSET_FRACTION) and ribbon_segment_chain_name/
_drive_control_joints_position_only/_drive_one_control_joint_position_only
— all Ribbon-family (paid) machinery. The commercial seam split moved all
of that to _ribbon_common.py (the paid side); see that file's own history
note for where it landed. What remains here is genuinely free: the finger
FK chain builder, the fist-curl master, aim_pv_at_mid, and the finger
discovery/metacarpal heuristics, all consumed by simple_ik.py, nodes.py,
limbs/builder.py and ui/option_widgets.py regardless of whether any paid
Ribbon component is even installed.

Phase 4 (this file, current state) adds: finger membership discovery
(discover_fingers) + the metacarpal curl-exclusion heuristic
(metacarpal_excluded) — pure logic, no cmds calls, offscreen-testable —
and build_finger_fk_chain, the per-finger FK-ctrl builder that reuses
_chain_common.build_fk_joint_ctrl (the same per-joint unit FKChain's own
build() uses — factored there in this same phase for DRY reuse). No curl
wiring yet (P5).

Phase 5 (this file, current state) adds: build_fingers_ctrl, the layered
fist-curl master (PLAN.md Task 5.1 / SPEC 3.5) — one 'fingers_ctrl' near
the wrist whose single curl-axis rotation sums into every INCLUDED member
phalange's <joint>_ctrl_offset curl-axis rotation (direct connect, or a
plusMinusAverage(sum) when that offset already carries an authored
value from build_finger_fk_chain's own matchTransform), so a per-finger
animator key on <joint>_ctrl composes ADDITIVELY on top rather than
fighting/overriding the master. Joints in curl_excluded[] get no curl
input at all.

See PLAN.md / SPEC.md in the MrMiata workspace
(workspace/2026-07-08_ikarm-ribbon-module) for the full design.

Phase 6 (this file, current state) adds two interface tweaks (Adrian,
2026-07-09): fingers_ctrl (build_fingers_ctrl, below) now orients its
OFFSET parent to the wrist joint's world orientation and sits a fixed +10
units along that offset's own local Y (previously a bone-radius-scaled
world-space nudge); and aim_pv_at_mid (below) — a small, RibbonIKArm-local
helper called from RibbonIKArm.build() after super().build() — aims the PV
ctrl's local +X at a given joint, then locks+hides its rotate channels.
RibbonIKArm.build() targets the FK-chain elbow specifically, NOT the blend or
bind elbow as originally speced — see aim_pv_at_mid's own docstring for
the empirically-verified reason (SimpleIK's always-present Schleifer pin
mechanism reads pv_ctrl's FULL worldMatrix, so driving pv_ctrl.rotate from
anything downstream of this same arm's own IK solve closes a genuine
same-frame DG cycle that silently freezes the elbow — invisible in
mayapy/batch mode because cycle-check-on-evaluation defaults off there).
RibbonIKArm-local for now; promotion to the SimpleIK base (so IKLeg's knee gets
the same aim) is deferred until another thread's held simple_ik.py lane
lands — and would need to resolve this same cycle constraint there too.

RESOLVED (2026-07-09 follow-up): the held simple_ik.py lane landed. The
pin mechanism now reads pv_ctrl's world POSITION only (simple_ik.py's
_build_ctrl_world_position_reader), never its full worldMatrix, so
pv_ctrl.rotate carries no DG edge into the pin distances and the cycle
described above can no longer form. The call moved from RibbonIKArm.build()
into SimpleIKComponent.build() itself (every SimpleIK-family limb gets it
for free) and now targets instance._blend_chain[1] — the TRUE blend elbow
originally asked for — instead of the FK-chain workaround target. See
aim_pv_at_mid's own docstring, below, for the updated detail.

Phase 7 (this file, current state, Adrian 2026-07-09): Phase 6's fixed
+10-unit object-space nudge on fingers_ctrl's OFFSET parent is REMOVED —
build_fingers_ctrl no longer moves ctrl_offset off the wrist anchor at
all; its position stays exactly at the wrist joint (orientation still
matched to the wrist, as Phase 6 left it). The standoff an animator needs
now comes from the ctrl's own shape: RIBBON_IK_ARM_CONTRACT's
'fingers_ctrl_shape' option defaults to curve_o_matic's new 'sine_handle'
library shape, a manual-shifter-knob Adrian authored specifically for
this ctrl (base at the shape's local origin, handle extending along +Y to
~4.68) — still a plain shape_enum, so any other library shape can be
picked instead, with whatever standoff (if any) that shape provides.

Phase 8 (this file, current state, Adrian 2026-07-09 follow-up — color
fix): fingers_ctrl used to always build a fixed red (RIBBON_IK_ARM_CONTRACT's
old 'fingers_ctrl_color' default), regardless of the arm's own
side-derived group color — a blue-group left arm's fist ctrl built red,
mismatching the rest of the arm. build_fingers_ctrl itself is unchanged
here (it always just applied whatever 'fingers_ctrl_color' opts value it
was given); the fix lives in the CALLER, RibbonIKArmComponent.build
(ribbon_ik_arm.py), which now resolves 'fingers_ctrl_color' to the instance's
own 'ctrl_color' — the exact option every other arm ctrl (FK/IK/PV/
switch) reads for its color — whenever the schema's new '' default
(unset) is in effect, so fingers_ctrl matches the group's color on any
side without re-deriving side. An explicit non-empty
'fingers_ctrl_color' override still always wins. See
RIBBON_IK_ARM_CONTRACT.options_schema['fingers_ctrl_color'] in
ribbon_ik_arm.py for the full writeup.

Task 2.3 (this file, current state — SPEC 2026-07-09 Limbs + Follower
Joints §3.4, "Always-limb membership"): the 'fingers' OptionField is
RETIRED. Finger membership now lives ONLY on the component's `fab_limb`
node (limb_node.py's finger_roots[]/curl_excluded[] message multis) —
discover_fingers/metacarpal_excluded stay exactly as they were (still the
pure-logic discovery engine, now consumed by nodes._maybe_create_
implicit_limb at create time and limbs/builder.py's _auto_register_
finger_chain at fragment-drop time, both of which write straight into the
limb node instead of an option). Two new pieces land here:

  - walk_finger_chain(root_joint): the BUILD-TIME membership reader's
    live-scene walk — given one finger_roots[] entry, walks its
    descendant chain off the CURRENT scene DAG (cmds.listRelatives), not
    a stored joint-name list. This is what makes membership rename- and
    insert-proof: a joint inserted between two existing finger joints
    (or appended at the tip) automatically joins the finger on the very
    next rebuild, and a renamed member never goes stale, because the
    chain is re-discovered from the live hierarchy every build rather
    than replayed from a frozen list captured at add time.

  - build_hand_from_limb(...): the RibbonIKArm.build()/BasicIKArm-future
    orchestration loop — membership resolution (walk_finger_chain over
    the limb's finger_roots[]) -> per-finger FK chain (build_finger_fk_
    chain, unchanged) -> the layered fist-curl master (build_fingers_ctrl,
    unchanged) — hoisted out of ribbon_ik_arm.py so any SimpleIK-family
    hand-owning component gets the same behavior for free (SPEC 3.2's
    standing request from the BasicIKArm thread). RibbonIKArm.build()
    becomes a thin caller: resolve its own limb node, call this, stash
    the result.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator.modules import _chain_common as cc


def short_name(name):
    return name.split('|')[-1].split(':')[-1]


# ─── P6: PV ctrl aim-at-elbow (Adrian tweak 2, 2026-07-09) ────────────────

def aim_pv_at_mid(pv_ctrl, mid_joint, component_node):
    """Aim `pv_ctrl`'s local +X at `mid_joint` (the elbow), then lock+hide
    the ctrl's rotate channels — a purely cosmetic "point at what you're
    steering" affordance; the PV ctrl's actual function (poleVectorConstraint
    on the ikHandle) reads ONLY its translate/parentMatrix, never rotate, so
    this aim wiring by itself changes nothing about how the arm solves.

    Target MUST be the ctrl itself, never an ancestor/offset group (aiming
    an ancestor would double-drive the same translate chain the
    poleVectorConstraint reads through that ancestor's own worldMatrix —
    a straightforward cycle). Aiming the ctrl ITSELF is safe FROM THAT
    SPECIFIC risk, since poleVectorConstraint's own inputs
    (targetTranslate/targetParentMatrix, verified via listConnections) never
    read pv_ctrl's rotate.

    HISTORICAL constraint on `mid_joint`, RESOLVED 2026-07-09 (P6 review
    finding, originally reproduced with a mayapy probe, confirmed under
    BOTH Evaluation Manager and classic serial DG mode, so it was a real
    plug-level graph cycle, not an EM scheduling artifact): `mid_joint`
    used to have to avoid anything downstream of THIS SAME arm's own IK
    solve — that ruled out the bind elbow, the BLEND-chain elbow, AND the
    IK-chain elbow (instance._blend_chain[1] / instance._ik_chain[1] / the
    bind `elbow` joint itself all failed this). The reason was NOT the
    poleVectorConstraint (which is innocent, see above) — it was SimpleIK's
    always-present "Schleifer Elbow Pin" stretchy mechanism
    (simple_ik.py's `pin_nodes` block, built whenever rest_total > 1e-6,
    i.e. on every real arm): `dist_a`/`dist_b` used to connect
    `pv_ctrl.worldMatrix[0]` (the FULL matrix — translate AND rotate
    combined, unlike the poleVectorConstraint's decomposed inputs) into a
    blendTwoAttr that writes `ik_jnt.translateX` on both the IK mid and end
    joints. So: pv_ctrl.rotate (written here) -> pv_ctrl.worldMatrix ->
    dist_a/dist_b -> the pin blend -> ik_mid_jnt.translateX -> (poisons
    that joint's WHOLE ikHandle-driven compute, rotate included, once any
    one of its plugs is cyclic) -> blend elbow -> bind elbow -> back into
    THIS aim constraint's target read, closing the loop. Reproduced
    directly: aiming at the blend or bind elbow left the bind elbow's
    world rotation COMPLETELY frozen (0.00 deg delta) after moving the IK
    ctrl, on a scene where the same move produces a ~7 deg bend with no
    aim wired at all or with the aim targeting anything outside this
    entanglement (a plain locator, or the FK-chain elbow) — confirmed with
    cycleCheck evaluation forced ON and Evaluation Manager forced OFF, so
    this was a genuine same-frame DG cycle, invisible in mayapy/batch mode
    specifically because cmds.cycleCheck(query=True, evaluation=True)
    defaults to False there (no warning is ever printed; the arm just
    silently stopped bending).

    FIX (2026-07-09 follow-up): simple_ik.py's pin mechanism now reads
    pv_ctrl's world POSITION only (`_build_ctrl_world_position_reader` —
    reads pv_ctrl.translate + offsetParentMatrix + parentMatrix via
    multMatrix/pointMatrixMult, feeding distanceBetween.point1/point2
    instead of pv_ctrl.worldMatrix[0] into inMatrix1/inMatrix2), which
    carries NO DG dependency on pv_ctrl.rotate — the edge that closed the
    loop above simply no longer exists, for ANY `mid_joint` target,
    including the blend elbow. SimpleIKComponent.build() now calls this
    function itself, targeting instance._blend_chain[1] (the TRUE blend
    elbow originally asked for, live-tracking in both IK and FK) — the
    FK-chain-elbow workaround described above is retired. See
    _dev/test_ik_arm_maya.py's test_ikarm_pv_ctrl_aim_no_cycle_and_
    unbuild_clean (asserts the elbow still bends after an IK move with the
    aim wired) and test_ikarm_pv_ctrl_aims_at_fk_elbow_and_tracks_fk_poses
    (both now exercise the blend-elbow target) for the regression
    coverage.

    worldUpType='none': no up-vector reference is used — genuinely
    unnecessary for a purely cosmetic point-at affordance (no downstream
    consumer reads the ctrl's swing/twist split), and skips having to find a
    stable, cycle-free up-object.

    Locking happens AFTER connecting the aim constraint — the constraint's
    connectAttr onto pv_ctrl's rotate channels keeps driving them at the DG
    level regardless of the Maya-level lock (lock only blocks interactive/
    keyframe edits, not incoming connections), matching the studio's usual
    "constrain then lock" ctrl convention.

    component_node: unused for message-tracking here (see below) but kept
    in the signature for parity with this module's other component_node-
    threading builders and in case a future caller needs it.

    Node lifecycle: the created aimConstraint node is a DAG CHILD of
    pv_ctrl (standard Maya constraint-node parenting), and pv_ctrl is
    itself a rig_grp descendant (ik_grp -> fab_controls_grp -> rig_grp, per
    SimpleIKComponent.build) — so it cascades away for free when rig_grp is
    deleted at unbuild, the same way the existing (untracked)
    poleVectorConstraint(pv_ctrl, ik_handle) does. This is the OPPOSITE
    situation from build_roll_joint's aimConstraint, which lives on `roll`
    (a child of a BIND joint, outside rig_grp) and therefore DOES need
    explicit 'roll_dg_nodes' bucket tracking. Verified empirically:
    _dev/test_ik_arm_maya.py's unbuild gate confirms zero surviving
    aimConstraint nodes (and the specific node by name) after
    fs_app.unbuild_modules() — no dedicated bucket was needed.

    Returns the created aimConstraint node name.
    """
    if not cmds.objExists(pv_ctrl):
        raise RuntimeError(f'aim_pv_at_mid: pv_ctrl not found: {pv_ctrl!r}')
    if not cmds.objExists(mid_joint):
        raise RuntimeError(f'aim_pv_at_mid: mid_joint not found: {mid_joint!r}')

    ac = cmds.aimConstraint(
        mid_joint, pv_ctrl, aimVector=(1.0, 0.0, 0.0), worldUpType='none',
        maintainOffset=False,
    )[0]

    # Lock AFTER connecting — see docstring. Same per-flag ordering
    # world._apply_channels uses (lock, then keyable, then channelBox) —
    # stacking flags in one setAttr call has been observed unreliable on
    # rotate channels specifically.
    for attr in ('rx', 'ry', 'rz'):
        plug = f'{pv_ctrl}.{attr}'
        cmds.setAttr(plug, lock=True)
        cmds.setAttr(plug, keyable=False)
        cmds.setAttr(plug, channelBox=False)

    return ac


# ─── P4: finger membership discovery + metacarpal heuristic (pure logic,
# no cmds calls — offscreen-testable per PLAN.md Task 4.1) ────────────────

# Trailing-joint name tokens (split on '_', case-insensitive) that mark a
# non-deforming tip/orientation joint — standard Maya convention (e.g.
# 'index_03_end'). Only ever checked against the LAST joint of a chain
# (metacarpal_excluded's len>=4 fallback, below) — a real chain's tip is
# the one place this convention appears.
_TIP_TOKENS = frozenset({'end', 'tip', 'nub'})


def metacarpal_excluded(joints):
    """Best-effort curl-exclusion heuristic for ONE finger's joint chain
    (PLAN.md Task 4.1 / SPEC 3.5). `joints`: list[str], chain order (root
    first) — short or full names, either works (matched via short_name).

    Rule:
      - name match `*metacarpal*` (case-insensitive substring — the UE5
        convention, e.g. 'index_metacarpal_r') -> exclude every joint in
        the chain whose name matches (normally exactly one — the
        proximal-most joint of a UE-style finger).
      - else: chain length >= 4 ->
          - if the LAST joint's name carries a tip/end/nub token (e.g.
            'index_03_end' — the standard Maya convention for a
            non-deforming orientation/length-reference joint appended at
            a chain's tip): exclude that joint TOO — see below for
            whether the root also gets excluded alongside it.
          - the ROOT joint (joints[0]) is excluded whenever, after
            setting aside a trailing tip joint (if any), 4 or more joints
            remain — i.e. `len(joints) - (1 if has_tip else 0) >= 4`.
            That remainder is the count of genuine phalange-or-metacarpal
            bones; 4+ of them can only be explained by an unlabeled
            metacarpal plus 3 phalanges. A tip-trimmed remainder of 3 or
            fewer means the chain is fully explained by "N phalanges (+
            optional trailing tip)" alone, with no room left for a
            metacarpal, so the root is a real phalange and stays curling.
            This is a confirmed P5 review finding: the two conventions
            (unlabeled-root-metacarpal vs. trailing-tip-joint) are not
            mutually exclusive — a 5-joint chain of [unlabeled metacarpal,
            3 phalanges, trailing tip] must exclude the root even though
            its last joint also carries a tip token; the old code treated
            "has a tip token" as fully replacing the "guess the root"
            branch instead of layering on top of it, so it never added
            the root to the excluded set in that case, silently leaving
            a real metacarpal fully wired to curl.
      - else (nothing left after a possible tip trim reaches 4): exclude
        nothing beyond the tip (if any) — a 3-joint finger (proximal,
        intermediate, distal), with or without a trailing tip joint, has
        no metacarpal to exclude.

    Always editable in Properties (SPEC 3.5: "the heuristic WILL be wrong
    sometimes") — this is only the CREATE-TIME default.

    Returns a set of joint name strings (same form as passed in) — the
    caller (discover_fingers) converts to a sorted list for the
    JSON/YAML-serializable option value.
    """
    if not joints:
        return set()
    matched = {j for j in joints if 'metacarpal' in short_name(j).lower()}
    if matched:
        return matched
    if len(joints) >= 4:
        tip = joints[-1]
        tip_tokens = short_name(tip).lower().split('_')
        has_tip = any(t in _TIP_TOKENS for t in tip_tokens)
        excluded = {tip} if has_tip else set()
        remainder = len(joints) - (1 if has_tip else 0)
        if remainder >= 4:
            excluded.add(joints[0])
        return excluded
    return set()


def discover_fingers(wrist_joint, blueprint):
    """Discover finger chains as the wrist joint's child subtrees (PLAN.md
    Task 4.1 / SPEC 3.4). Pure logic — walks `blueprint`'s parent/child
    map only, no live Maya scene required (offscreen-testable).

    wrist_joint: joint name string (matches a `blueprint.skeleton_joints`
    entry's `.name`).
    blueprint: any object exposing `.skeleton_joints` — an iterable of
    objects with `.name` and `.parent` string attributes. Satisfied by
    both the real `blueprint.schema.Blueprint`/`JointSpec` dataclasses
    (what `fs_app._snapshot_blueprint_from_scene()` returns — the live,
    create-time case) and a lightweight duck-typed stand-in (offscreen
    tests).

    One entry per DIRECT child of wrist_joint that has no siblings
    forming a separate branch of ITS OWN (a finger, by SPEC 3.4
    definition, is a single straight chain — no interior branching). If
    a joint deeper in a chain happens to have more than one child
    (unusual, but not disallowed), this PREFERS a child that itself has
    children (a real chain continuation) and only falls back to the
    first-listed child (by `blueprint.skeleton_joints` order) when every
    candidate is a leaf — matching the studio's existing ambiguous-branch
    convention (fs_app._first_chain_child) step for step, not just by
    name: a leaf sibling listed BEFORE the real continuation (e.g. a
    'index_nub' nail/decoration bone preceding 'index_03' in
    skeleton_joints order) must not truncate the chain and silently drop
    the real joints past it (confirmed P4 review finding — a naive
    "first-listed child wins" walk does exactly that, contradicting this
    docstring's own claim of parity with fs_app._first_chain_child).
    fs_app._first_chain_child itself isn't called here — it lives in
    fs_app.py, which imports `maya.cmds` at module level, and this
    function must stay pure/offscreen-testable (no cmds calls) — so the
    same rule is reimplemented locally instead.

    Returns: list of {'root_joint': str, 'joints': [str, ...],
    'curl_excluded': [str, ...]} — one per finger, in wrist-child
    (skeleton_joints) order. 'joints' is root-first chain order.
    'curl_excluded' is metacarpal_excluded(chain) as a SORTED LIST (JSON/
    YAML-serializable — this is the exact shape the 'fingers' option
    round-trips through, see RIBBON_IK_ARM_CONTRACT.options_schema['fingers']).

    A finger requires AT LEAST 2 joints in its chain (root + at least one
    child) — a wrist-child with NO children of its own (chain length 1)
    is excluded structurally, by chain shape alone, never by name (2026-
    07-09 checkpoint-blocker fix: a single-joint prop/weapon/attach joint
    hung directly off the wrist — e.g. a UE mannequin's weapon_l socket —
    was being auto-registered as a "finger", a false positive that then
    propagated into finger_roots[] via both live-discovery call sites
    below). A 2-joint stub finger (root + one child, no further
    descendants) is the shortest chain that still counts.
    """
    if blueprint is None:
        return []
    skel = getattr(blueprint, 'skeleton_joints', None) or []
    children_map = {}
    for j in skel:
        if j.parent:
            children_map.setdefault(j.parent, []).append(j.name)

    fingers = []
    for root in children_map.get(wrist_joint, []):
        chain = [root]
        cur = root
        while True:
            kids = children_map.get(cur, [])
            if not kids:
                break
            # Prefer a child that is itself a chain continuation (has its
            # own children) over a leaf sibling, regardless of listed
            # order — fs_app._first_chain_child's rule, reimplemented.
            # Falls back to the first-listed child only when every
            # candidate is a leaf (still ambiguous, but no worse choice
            # exists).
            cur = next((k for k in kids if children_map.get(k)), kids[0])
            chain.append(cur)
        if len(chain) < 2:
            # Structural exclusion, not a name check — see the docstring
            # note above. A childless wrist-child (a prop socket, an
            # IK-hand joint, an attach point) is not a finger.
            continue
        fingers.append({
            'root_joint': root,
            'joints': chain,
            'curl_excluded': sorted(metacarpal_excluded(chain)),
        })
    return fingers


# ─── P4: per-finger FK ctrls (PLAN.md Task 4.4) ───────────────────────────

def build_finger_fk_chain(finger, wrist_ctrl, opts, cv_block, component_node,
                          index_offset=0):
    """Build per-joint FK ctrls for ONE finger's joints[] — INCLUDING
    metacarpals (curl exclusion only matters once P5 wires the fist-curl
    master; every member joint still gets a full, tagged FK ctrl here —
    SPEC 3.4: "Each finger joint gets an FK ctrl... including
    metacarpals"), chain-parented in joint order under `wrist_ctrl`
    (SPEC 3.4: "chain-parented under the wrist ctrl").

    Reuses _chain_common.build_fk_joint_ctrl per joint — the SAME
    per-joint offset+ctrl+null-pair+CV-persistence unit FKChain's own
    build() uses (PLAN Task 4.4: "reusing FKChain's per-joint... builder
    via a shared helper, factor it, DRY"). No curl wiring here — that's
    P5 (SPEC 3.5's fist-curl master); this function only builds the
    skeleton-only finger's animator-facing FK layer.

    finger: one discover_fingers() entry — {'root_joint', 'joints',
    'curl_excluded'}. Only 'joints' (chain order, root first) drives this
    builder; 'curl_excluded' is read by P5's curl wiring, not here.
    wrist_ctrl: the ctrl every finger root chain-parents under — RibbonIKArm
    passes its wrist switch ctrl (instance._switch_ctrl, from the
    inherited SimpleIK build — the always-visible, IK/FK-mode-independent
    ctrl at the wrist, so fingers track the wrist correctly in BOTH IK
    and FK modes without their own mode-blend).
    opts: dict with 'ctrl_shape' / 'ctrl_color' / 'channels' — passed
    straight through to build_fk_joint_ctrl per joint (same keys
    FKChain's own options_schema declares).
    cv_block: {ctrl_name: shapes_list} persisted CV map — SPEC §4 / PLAN
    Task 4.4: "Persist per-ctrl CVs like FKChain".
    component_node: the RibbonIKArm instance's component network node, for
    nodes.tag_ctrl's fab_owner back-link.
    index_offset: the fab_joint_index value for this finger's FIRST
    joint; subsequent joints in the SAME finger get index_offset+1,
    +2, ... The caller (RibbonIKArm.build) must pass a value that's unique
    across EVERY finger on the same component AND outside the arm's own
    primary joints[] index range (0/1/2 = shoulder/elbow/wrist) — e.g. a
    running counter that starts at len(instance.joints) and increases by
    len(finger['joints']) after each finger, never resetting to 0 per
    finger. Confirmed P4 review finding: this used to always start at 0
    for EVERY finger (`enumerate(finger['joints'])`), so every finger's
    root ctrl shared (component_node, role='finger_fk_ctrl',
    joint_index=0) with every OTHER finger's root ctrl on the same arm —
    pose_library.address.address_to_ctrl's (fab_role, fab_joint_index)
    lookup returns the FIRST match, so a saved pose address for one
    finger could silently resolve to a different finger's ctrl; and
    canvas_panel.py's _make_item_for_ctrl indexed straight into the arm's
    OWN 3-entry joints[] with that same small index, mapping finger
    ctrls onto shoulder/elbow/wrist. A component-wide-unique index outside
    0..2 fixes the pose-library collision outright and turns the
    canvas_panel mis-map into a safe "index out of range, fall back to
    the component's primary joint" instead of an actively wrong one.

    Returns a list of (offset_ctrl, ctrl) tuples, one per joint in
    finger['joints'] order (root first).

    Node cleanup note: unlike the ribbon/roll subsystems, these ctrls
    need NO dedicated message-tracking bucket — offset_ctrl/ctrl/the null
    pair are all DAG descendants of rig_grp (via wrist_ctrl's own
    ancestry), so they cascade-delete when rig_grp is deleted at unbuild,
    exactly like FKChain's own ctrls. Only the CONSTRAINTS this leaves on
    each bind finger JOINT (outside rig_grp, like every other component's
    bind-joint constraints) need an explicit unbuild sweep — RibbonIKArm.unbuild
    does this the same way FKChain.unbuild does for its own joints.
    """
    prev_parent = wrist_ctrl
    built = []
    for local_idx, jnt in enumerate(finger.get('joints') or []):
        offset_ctrl, ctrl = cc.build_fk_joint_ctrl(
            jnt, prev_parent, opts, cv_block, component_node,
            'finger_fk_ctrl', index_offset + local_idx)
        built.append((offset_ctrl, ctrl))
        prev_parent = ctrl
    return built


# ─── P5: layered fist-curl master (PLAN.md Task 5.1 / SPEC 3.5) ──────────

# Below this, an offset_ctrl's authored curl-axis rotation (from
# build_finger_fk_chain's own matchTransform, captured BEFORE curl wiring
# runs) is treated as "no authored value" — a plain direct connect is
# used instead of a plusMinusAverage(sum) node. Angular degrees; well
# under any real bind-pose joint-orient delta.
_CURL_BASELINE_EPSILON = 1e-6


def build_fingers_ctrl(wrist_joint, wrist_ctrl, fingers, finger_built, curl_axis,
                       opts, cv_block, component_node):
    """The fist-curl master: one 'fingers_ctrl' ROTATE control near the
    wrist, ONLY curl_axis keyable (every other channel locked —
    world._apply_channels' whitelist branch, SPEC 3.5: "all other
    channels locked"). Its local curl-axis rotation is summed into every
    INCLUDED member phalange's <joint>_ctrl_offset curl-axis rotation.

    Layering mechanism (SPEC 3.5 / PLAN Task 5.1): build_finger_fk_chain
    already set each offset_ctrl's rotation via matchTransform BEFORE
    this function runs, so an offset_ctrl can already carry a nonzero
    authored curl-axis value at bind (a natural resting curl, or a
    joint-orient delta between consecutive finger joints). To layer the
    master's contribution ON TOP of that baseline rather than clobbering
    it:
      - baseline ~0 (|value| < _CURL_BASELINE_EPSILON): a plain
        connectAttr(master, offset.rotate<AXIS>) — no baseline to
        preserve, so a direct connection keeps the DG graph minimal
        (SPEC 3.5: "direct connect... ").
      - baseline != 0: a plusMinusAverage(operation=sum) node —
        input1D[0] <- master's curl attr (live), input1D[1] = the
        captured static baseline, output1D -> offset.rotate<AXIS>
        (SPEC 3.5: "...or a plusMinusAverage when the offset already
        carries an authored value").
    Because the animator's own <joint>_ctrl is a CHILD of <joint>_
    ctrl_offset (build_fk_joint_ctrl's parenting) with zero local
    rotation in between, and both this wiring and the animator's key
    ultimately rotate about the exact same single local axis
    (curl_axis), the two contributions compose by simple angle addition
    all the way down to the bind joint — a finger key ADDS on top of the
    master, it never overrides/fights it (SPEC 3.5's "layered curl").

    Joints in a finger's curl_excluded[] get NO wiring at all (metacarpals
    keep their P4 FK ctrl — this function just never touches their
    offset — SPEC 3.5).

    wrist_joint: the RibbonIKArm's END/wrist BIND joint — used only for
    fingers_ctrl's position (near the wrist) and radius-based sizing.
    wrist_ctrl: instance._switch_ctrl — the always-visible, IK/FK-mode-
    independent ctrl fingers_ctrl chain-parents under (same anchor
    build_finger_fk_chain's own fingers use).
    fingers: the RibbonIKArm's 'fingers' option value (list of {root_joint,
    joints[], curl_excluded[]} — discover_fingers' shape).
    finger_built: {root_joint: [(offset_ctrl, ctrl), ...]} — EXACTLY
    RibbonIKArm.build's own accumulator from its build_finger_fk_chain calls,
    one list per finger, root-first, parallel to that finger's 'joints'
    (build_finger_fk_chain's documented return order).
    curl_axis: 'x' | 'y' | 'z' — RIBBON_IK_ARM_CONTRACT's single, uniform
    'curl_axis' option (SPEC 3.5: "a single RibbonIKArm-level option
    (uniform)"). Invalid/missing values fall back to 'z'.
    opts: dict with 'fingers_ctrl_shape' / 'fingers_ctrl_color'. The
    caller (RibbonIKArmComponent.build) is responsible for resolving
    'fingers_ctrl_color' to its final value BEFORE calling this function
    (follow-the-group default: falls through to the arm's own
    'ctrl_color' option when no explicit fingers_ctrl_color override is
    authored — see RIBBON_IK_ARM_CONTRACT's 'fingers_ctrl_color' OptionField
    docstring) — this function just applies whatever color string it's
    given, with 'yellow' as its own last-resort fallback if the key is
    missing entirely.
    cv_block: {ctrl_name: shapes_list} persisted CV map for fingers_ctrl
    itself (SPEC §4: "any authored fingers_ctrl shape" persists).
    component_node: for nodes.tag_ctrl's fab_owner back-link and the
    curl-sum plusMinusAverage nodes' message-tracking bucket
    ('curl_dg_nodes' — these are plain DG nodes with no DAG parent, so
    (like the ribbon/roll buckets) the rig_grp cascade never catches
    them; the caller's unbuild() must sweep this bucket explicitly).

    Returns a dict: ctrl, ctrl_offset, curl_axis, driven (list of every
    offset_ctrl this call wired — test/inspection convenience). Still
    builds the ctrl itself when `fingers` is empty (an armless-hand
    edit-mode state gets an inert fist master) as long as wrist_ctrl
    exists — the caller guards that.
    """
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.utils.maya import network_nodes as nn
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
    from maya_tools.rigging.fabricator.modules.world import _apply_color, _apply_channels

    axis = (curl_axis or 'z').lower()
    if axis not in ('x', 'y', 'z'):
        axis = 'z'
    curl_attr = f'rotate{axis.upper()}'
    short_wrist = short_name(wrist_joint)
    opts = opts or {}

    ctrl_offset = cmds.createNode(
        'transform', name=f'{short_wrist}_fingers_ctrl_offset')
    cmds.matchTransform(ctrl_offset, wrist_joint, position=True, rotation=True, scale=False)
    cmds.parent(ctrl_offset, wrist_ctrl)
    cmds.matchTransform(ctrl_offset, wrist_joint, position=True, rotation=True, scale=False)

    # ctrl_offset's ROTATION now matches the wrist joint's world
    # orientation exactly (both matchTransform calls above) — this is what
    # carries the wrist-aligned orientation. `ctrl` itself deliberately
    # does NOT get this baked onto it (see below: its own local rotate
    # channels are zeroed) — build_fingers_ctrl's curl wiring reads ctrl's
    # LOCAL rotation as the master curl signal (master_attr, below), so
    # baking a world orientation directly onto ctrl would inject that
    # orientation into every phalange's layered-sum curl wiring too,
    # breaking the superposition contract this function's own docstring
    # describes.
    #
    # Phase 7 (2026-07-09, Adrian): the old fixed +10-unit object-space
    # nudge along ctrl_offset's own local Y is GONE — ctrl_offset's
    # position stays exactly AT the wrist anchor (no move call at all).
    # The standoff an animator needs to grab the ctrl now comes from the
    # ctrl's own SHAPE instead: fingers_ctrl_shape's new default,
    # curve_o_matic's 'sine_handle' library shape (a manual-shifter-knob
    # Adrian authored specifically for this ctrl — base at the shape's
    # local origin, handle extending along +Y to ~4.68), places that
    # standoff in the shape geometry, which still sits under ctrl_offset's
    # wrist-matched orientation, so the handle still reads as "along the
    # wrist's local Y" — just drawn there instead of translated there.
    # Still mirrors correctly for free (an unrotated shape under an
    # already-mirror-correct offset needs no extra handling), and a rigger
    # who swaps fingers_ctrl_shape for a different library shape simply
    # gets whatever standoff (if any) that shape's own geometry provides.
    radius = cmds.getAttr(f'{wrist_joint}.radius') or 1.0

    ctrl_name = f'{short_wrist}_fingers_ctrl'
    ctrl = com.build_shape(opts.get('fingers_ctrl_shape', 'sine_handle'),
                           ctrl_name, radius=radius * 2.0)
    cmds.parent(ctrl, ctrl_offset)
    for a in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
        cmds.setAttr(f'{ctrl}.{a}', 0)
    for a in ('sx', 'sy', 'sz'):
        cmds.setAttr(f'{ctrl}.{a}', 1)
    _apply_color(ctrl, opts.get('fingers_ctrl_color') or 'yellow')
    cc._restore_shape(com, ctrl, (cv_block or {}).get(ctrl_name))

    # ONLY curl_attr keyable — every other channel (both other rotates,
    # all translate/scale, visibility) locked + hidden — SPEC 3.5: "all
    # other channels locked".
    _apply_channels(ctrl, {'keyable': [f'r{axis}']})

    nodes.tag_ctrl(ctrl, 'fingers_ctrl', component_node=component_node, joint_index=-1)

    def _connect_and_track_conversion(src_plug, dst_plug):
        """connectAttr, then track any unitConversion node Maya silently
        auto-inserts on top of the connection (empirically confirmed:
        happens bridging a GENERIC double, like plusMinusAverage's
        input1D/output1D, to/from an angle-typed .rotate<AXIS> — does
        NOT happen for a plain rotate-to-rotate direct connect). These
        have no DAG parent (like the plusMinusAverage itself), so
        without this they'd leak past unbuild's node-set sweep — a
        regression this function's own caller's unbuild-orphan test
        would otherwise miss, since 'unitConversion' isn't one of the
        DG types the studio's generic orphan-count check tracks."""
        cmds.connectAttr(src_plug, dst_plug, force=True)
        for src in cmds.listConnections(dst_plug, source=True,
                                        destination=False) or []:
            if cmds.nodeType(src) == 'unitConversion':
                nn.connect_message_multi(src, component_node, 'curl_dg_nodes')

    master_attr = f'{ctrl}.{curl_attr}'
    driven = []
    for finger in fingers or []:
        joint_list = finger.get('joints') or []
        root = finger.get('root_joint') or (joint_list[0] if joint_list else None)
        pairs = finger_built.get(root) or []
        excluded = set(finger.get('curl_excluded') or [])
        for finger_joint, pair in zip(joint_list, pairs):
            if finger_joint in excluded:
                continue
            offset_ctrl, _fk_ctrl = pair
            if not offset_ctrl or not cmds.objExists(offset_ctrl):
                continue
            plug = f'{offset_ctrl}.{curl_attr}'
            baseline = cmds.getAttr(plug)
            if abs(baseline) < _CURL_BASELINE_EPSILON:
                _connect_and_track_conversion(master_attr, plug)
            else:
                pma = cmds.createNode(
                    'plusMinusAverage',
                    name=f'{short_name(finger_joint)}_curl_sum')
                cmds.setAttr(f'{pma}.operation', 1)  # sum
                _connect_and_track_conversion(master_attr, f'{pma}.input1D[0]')
                cmds.setAttr(f'{pma}.input1D[1]', baseline)
                _connect_and_track_conversion(f'{pma}.output1D', plug)
                nn.connect_message_multi(pma, component_node, 'curl_dg_nodes')
            driven.append(offset_ctrl)

    return {
        'ctrl': ctrl,
        'ctrl_offset': ctrl_offset,
        'curl_axis': axis,
        'driven': driven,
    }


# ─── Task 2.3: always-limb membership — build-time chain walk + hoisted
# hand orchestration (SPEC 2026-07-09 Limbs + Follower Joints §3.4) ───────

def walk_finger_chain(root_joint):
    """Live-walk `root_joint`'s descendant chain off the CURRENT scene DAG
    — scene-is-truth membership resolution (Task 2.3 / SPEC 3.4: "the
    component resolves membership by ... walking each root's descendant
    chain at build"). Unlike discover_fingers (which walks a BLUEPRINT's
    parent/child map, used only at create/migration time to seed
    finger_roots[]), this walks live `cmds.listRelatives` every call — so
    a joint inserted between two existing finger joints (or appended past
    the old tip) automatically joins the finger on the very next rebuild,
    and a renamed member never goes stale, because nothing here is ever
    read from a stored name list.

    Same ambiguous-branch preference discover_fingers documents (prefer a
    child that is itself a chain continuation — has its own children —
    over a leaf sibling, falling back to the first-listed child only when
    every candidate is a leaf), but reading live DAG child order
    (cmds.listRelatives) instead of a blueprint's skeleton_joints order —
    finger_roots[] is a bare message connection with no per-child
    ordering data of its own, so live scene child order is the best (and
    only) available proxy at build time.

    Returns [root_joint, ...], chain order, root first. root_joint is
    always included verbatim (never re-resolved) — the caller already
    holds a live, existing joint name (limb_node.list_finger_roots only
    ever reports connected, therefore existing, targets).
    """
    chain = [root_joint]
    cur = root_joint
    while True:
        kids = cmds.listRelatives(cur, children=True, type='joint') or []
        if not kids:
            break
        cur = next((k for k in kids
                   if cmds.listRelatives(k, children=True, type='joint')),
                  kids[0])
        chain.append(cur)
    return chain


def build_hand_from_limb(limb, wrist_joint, switch_ctrl, opts, component_node,
                         cv_block, index_offset=0):
    """Component-agnostic hand orchestration: membership (this limb's
    finger_roots[]/curl_excluded[], walked live) -> per-finger FK chains
    (build_finger_fk_chain) -> the layered fist-curl master
    (build_fingers_ctrl). Hoisted out of RibbonIKArm.build() (Task 2.3 /
    SPEC 3.2's standing BasicIKArm request) so any SimpleIK-family
    component that owns a hand can call this directly instead of
    reimplementing the loop. FREE-side (this file) — never imports
    _ribbon_common.

    limb: the caller's own fab_limb node (limb_node.find_limb_for_joint
    resolution is the CALLER's job — this function only ever reads
    finger_roots[]/curl_excluded[] off whatever node it's handed). None
    is accepted (a degenerate/defensive case — e.g. a component whose
    limb somehow isn't resolvable) and treated as "no membership": zero
    fingers, but fingers_ctrl STILL builds (see build_fingers_ctrl's own
    "builds even when fingers is empty" contract), matching pre-Task-2.3
    behavior for an empty 'fingers' option.
    wrist_joint: the hand's anchor BIND joint — passed straight through
    to build_fingers_ctrl for its position/name derivation (short_name(
    wrist_joint) + '_fingers_ctrl').
    switch_ctrl: the always-visible, mode-independent ctrl every finger
    root AND fingers_ctrl itself chain-parent under (RibbonIKArm passes
    instance._switch_ctrl, the wrist IK/FK switch).
    opts: dict merging every knob build_finger_fk_chain/build_fingers_ctrl
    need — 'ctrl_shape'/'ctrl_color'/'channels' (finger FK ctrls,
    unchanged keys FKChain's own options_schema declares), 'curl_axis'
    (fist master rotate axis), 'fingers_ctrl_shape'/'fingers_ctrl_color'
    (fist master shape/color — the caller is responsible for resolving
    'fingers_ctrl_color's follow-the-group '' sentinel to a real color
    BEFORE calling this, same contract build_fingers_ctrl's own docstring
    already documents).
    component_node: this instance's component network node, threaded
    straight through to both builders for their own fab_owner/message-
    tracking bucket needs.
    cv_block: {'finger_ctrl_cv_data': {...}, 'fingers_ctrl_cv_data': {...}}
    — both keys optional (default {}), the exact two persisted-CV blocks
    RibbonIKArm.unbuild already captures under those names.
    index_offset: the fab_joint_index value for the FIRST finger's
    FIRST joint (same running-counter contract build_finger_fk_chain's
    own index_offset documents) — the caller must pass a value outside
    its own primary joints[] index range (e.g. len(instance.joints)) so
    finger ctrls never collide with the arm's own shoulder/elbow/wrist
    (or hip/knee/ankle, for a future leg) slots.

    Returns {'finger_built': {root_joint: [(offset_ctrl, ctrl), ...]},
    'fingers_ctrl': build_fingers_ctrl's own return dict (or None if
    switch_ctrl doesn't exist — the one case nothing here builds at
    all)}.
    """
    from maya_tools.rigging.fabricator import limb_node as ln

    opts = opts or {}
    cv_block = cv_block or {}
    finger_cv_block = cv_block.get('finger_ctrl_cv_data') or {}
    fingers_ctrl_cv_block = cv_block.get('fingers_ctrl_cv_data') or {}

    result = {'finger_built': {}, 'fingers_ctrl': None}
    if not switch_ctrl or not cmds.objExists(switch_ctrl):
        return result

    finger_ctrl_opts = {
        # Finger FK ctrls read their own dedicated shape when the owning
        # component declares one (both IK arms: 'finger_fk_shape',
        # default capsule_ramp — Adrian, 2026-07-10 launch polish),
        # falling back to the component-wide ctrl_shape for any caller
        # without the dedicated option.
        'ctrl_shape': opts.get('finger_fk_shape',
                               opts.get('ctrl_shape', 'capsule')),
        'ctrl_color': opts.get('ctrl_color', 'yellow'),
        'channels': opts.get('channels', {}),
    }

    roots = ln.list_finger_roots(limb) if limb else []
    limb_curl_excluded = set(ln.list_curl_excluded(limb)) if limb else set()

    # Membership resolution: walk every root's descendant chain LIVE
    # (scene-is-truth) and reconstruct build_fingers_ctrl's expected
    # per-finger 'curl_excluded' shape by intersecting the chain against
    # the limb's own FLAT curl_excluded[] multi (curl_excluded is not
    # nested per finger on the limb node — SPEC 3.2 — it's one shared
    # set of excluded joints across the whole limb).
    #
    # Duplicate-across-fingers guard (same tone as the old option-driven
    # check it replaces): a limb node's finger_roots[] is only ever
    # editable script-side today (P3's Properties dials land later), but
    # nothing stops a script from connecting a joint that's already a
    # DESCENDANT of an earlier root as its OWN separate finger_root too
    # — build_finger_fk_chain would then run twice on that joint, and a
    # second parentConstraint/scaleConstraint call against an
    # already-constrained joint ADDS a second weighted target instead of
    # raising (Maya's own behavior), leaving it blend-driven by two
    # competing drivers. A later-authored root whose chain collides with
    # an earlier one is skipped WHOLESALE, with a warning.
    fingers = []
    seen_joints = set()
    for root in roots:
        if not root or not cmds.objExists(root):
            # Defensive only — list_finger_roots only ever reports live,
            # connected (therefore existing) targets; Maya auto-drops a
            # dangling message connection when its source node is
            # deleted, so this branch should be unreachable in practice.
            continue
        chain = walk_finger_chain(root)
        dupes = [j for j in chain if j in seen_joints]
        if dupes:
            cmds.warning(
                f"build_hand_from_limb: finger rooted at {root!r} skipped "
                f"— {dupes!r} already claimed by an earlier finger_roots[] "
                f"entry on this limb (duplicate membership). No FK ctrls "
                f"were built for it this build — a joint can only be "
                f"driven by one finger. Fix the duplicate connection on "
                f"the limb node to clear this warning.")
            continue
        seen_joints.update(chain)
        fingers.append({
            'root_joint': root,
            'joints': chain,
            'curl_excluded': sorted(j for j in chain if j in limb_curl_excluded),
        })

    finger_built = {}
    next_index = index_offset
    for finger in fingers:
        finger_built[finger['root_joint']] = build_finger_fk_chain(
            finger, switch_ctrl, finger_ctrl_opts, finger_cv_block,
            component_node, index_offset=next_index)
        next_index += len(finger['joints'])
    result['finger_built'] = finger_built

    curl_axis = opts.get('curl_axis', 'z')
    fingers_color = opts.get('fingers_ctrl_color') or opts.get('ctrl_color', 'yellow')
    fingers_ctrl_opts = {
        'fingers_ctrl_shape': opts.get('fingers_ctrl_shape', 'sine_handle'),
        'fingers_ctrl_color': fingers_color,
    }
    result['fingers_ctrl'] = build_fingers_ctrl(
        wrist_joint, switch_ctrl, fingers, finger_built, curl_axis,
        fingers_ctrl_opts, fingers_ctrl_cv_block, component_node)

    return result


def bone_axis_letter(joint):
    """Down-bone twist axis, from `joint`'s dominant local-translate
    component (KS skeletons orient +X down-chain; stays correct for
    Y/Z-oriented imports). Hoisted from ik_arm 2026-07-10 for the
    free-leg twist pass — bone-generic, evidenced by the arm/leg test
    twins."""
    t = cmds.getAttr(f'{joint}.translate')[0]
    return 'xyz'[max(range(3), key=lambda i: abs(t[i]))]


def build_fractional_twist(members, seg_top, seg_end, driver_joint,
                           component_node, owner_label):
    """The most basic segment twist (free-tier; SPEC §3.4, hoisted from
    ik_arm 2026-07-10 for the free leg): each member joint gets
    rotate<A> = t x `driver_joint`'s rotate<A>, where A is the down-bone
    axis and t is the member's fraction along seg_top->seg_end, derived
    GEOMETRICALLY from rest positions (never from follow-rule metadata —
    stays correct for hand-authored AND adopted twist joints alike).
    The driver is a post-blend BIND joint, so the same wiring works in
    IK and FK for free. One multDoubleLinear per member; the mults AND
    the unitConversions Maya auto-inserts on both hops are
    message-tracked in 'twist_dg_nodes' for the owner's unbuild sweep.
    Zero members -> zero nodes. Already-driven channels are skipped with
    a warning (belt-and-suspenders against a ribbon rider or authored
    connection owning the joint)."""
    from maya_tools.utils.maya import network_nodes as nn

    if not members:
        return None

    axis = bone_axis_letter(driver_joint).upper()
    tp = cmds.xform(seg_top, q=True, ws=True, t=True)
    ep = cmds.xform(seg_end, q=True, ws=True, t=True)
    bone = [ep[i] - tp[i] for i in range(3)]
    bone_len2 = sum(c * c for c in bone) or 1.0

    built = []
    for j in members:
        if not cmds.objExists(j):
            cmds.warning(f"{owner_label}: twist member {j!r} no longer "
                         f"exists — skipped this build.")
            continue
        dst = f'{j}.rotate{axis}'
        if cmds.listConnections(dst, source=True, destination=False):
            cmds.warning(f"{owner_label}: {dst} already driven — twist "
                         f"wiring skipped for this joint.")
            continue
        jp = cmds.xform(j, q=True, ws=True, t=True)
        t = sum((jp[i] - tp[i]) * bone[i] for i in range(3)) / bone_len2
        t = max(0.0, min(1.0, t))
        mdl = cmds.createNode('multDoubleLinear',
                              name=f'{j.split("|")[-1]}_twist_frac')
        cmds.setAttr(f'{mdl}.input2', t)
        cmds.connectAttr(f'{driver_joint}.rotate{axis}', f'{mdl}.input1')
        cmds.connectAttr(f'{mdl}.output', dst)
        nn.connect_message_multi(mdl, component_node, 'twist_dg_nodes')
        for plug in (f'{mdl}.input1', dst):
            for src in cmds.listConnections(plug, source=True,
                                            destination=False) or []:
                if cmds.nodeType(src) == 'unitConversion':
                    nn.connect_message_multi(src, component_node,
                                             'twist_dg_nodes')
        built.append(mdl)
    return built
