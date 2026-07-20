---
title: FollowJoint
summary: Constrains one joint to follow another joint's position and rotation, with per-axis skip and weight control; the pick for UE5 engine reference joints and helper/prop-follow setups.
category: component
gif: ../media/follow-joint.gif
video:
---

# FollowJoint

## What it builds

FollowJoint is a passive component: it builds no control and has no visible rig-canvas UI surface beyond its options. It drives exactly one joint (`joints[0]`) by constraining it to a source joint picked in the Properties panel's Follow Target field. Build applies a `pointConstraint` and a separate `orientConstraint` between the source and the follower joint (not a combined `parentConstraint`), each with its own per-axis skip list and follow weight, so translate and rotate can be partially applied or blended independently. The constraint nodes are message-connected onto the component's network node so `unbuild` can find and delete them; the joint's own `worldMatrix[0]` is always registered as the `joint_out` plug, whether or not a constraint actually got built.

## When to use it

- UE5 engine reference joints (`ik_foot_*`, `ik_hand_*`, `ik_hand_gun`) that need to follow their counterpart rig joints so the FBX animation export carries them as keyed engine refs.
- Generic helper joints, prop attachers, or any other "this joint should just follow that matrix" case that doesn't need a control of its own.
- As a placeholder in a rig template: leaving Follow Target empty is an explicit, supported no-op, so a template can ship a FollowJoint instance that each rig sets a real target on later.

## Options

Pulled from `FOLLOW_JOINT_CONTRACT.options_schema` in `modules/follow_joint.py`:

| Option | Default | What it does |
|---|---|---|
| `follow_target` | `''` (empty) | Joint this component should follow, picked from the Properties panel's Follow Target dropdown (a `joint_picker` widget listing every joint in the scene, alphabetical, with a `(none)` sentinel at index 0). Empty means no constraint is built — a no-op. |
| `maintain_offset` | `True` | Preserve the current offset between the source and the joint when the constraints are created (`mo=` flag on both constraints). |
| `translate_axes` | `(True, True, True)` | Which translate channels the point constraint drives. Stored as an `xyz_axes` compound; the widget's master "All" toggle flips X/Y/Z together. |
| `translate_alpha` | `1.0`, range `0.0`-`1.0` | Translate follow weight. `0` = no influence, `1` = fully follow. Clamped to range at build time even if persisted data is out of bounds. |
| `rotate_axes` | `(True, True, True)` | Which rotate channels the orient constraint drives. Same `xyz_axes` master-toggle widget as `translate_axes`. |
| `rotate_alpha` | `1.0`, range `0.0`-`1.0` | Rotate follow weight. `0` = no influence, `1` = fully follow. Clamped the same way as `translate_alpha`. |

## Plugs and spaces

- Input: `parent_in` (matrix, not required) — a back-compat fallback source. It's only consulted when `follow_target` is empty; if `instance.parent_plug` is wired, build resolves it and uses that node as the constraint source instead. `follow_target` always wins when it's set. This plug predates the `follow_target` option and exists for rigs authored before it did.
- Output: `joint_out` (matrix, `space_target=False`) — the follower joint's `worldMatrix[0]`, registered after build regardless of whether a constraint was actually created, so downstream components never fail to resolve this plug.
- `parent_strategy='walk_up'` is set on the contract because the base `Contract` dataclass requires the field, but the component's own comment notes it isn't actually used — there's no control to parent, so nothing walks up anything.
- `side_supported=False` — not mirrorable through the L/R pair-mirror system.
- `joint_roles=(JointRole('joint', 'Follower joint'),)` — single joint role, no multi-joint chain.
- `color='#888888'` (gray) — the contract flags it as a passive helper for the component's canvas tint.

## Animator features

None. FollowJoint builds no control, so there is nothing for a marking menu, IK/FK match, or space switching to attach to. The contract declares no `actions` and no `space_consumers`, and `side_supported=False` keeps it out of the mirror table. It also doesn't participate in the FK/IK selection-set tagging that control-building components get, since that tagging keys off a built control's role, not a joint.

## Gotchas

- Leaving `follow_target` empty is a deliberate, silent no-op: `build()` still registers `joint_out` as the joint's own world matrix, but no constraint is created and no warning is printed. Easy to forget a target was never set on a template-authored instance.
- If `follow_target` is set to a name that doesn't exist in the scene, `build()` raises a `RuntimeError` and the build stops.
- If `follow_target` is empty and the `parent_plug` fallback resolves to a node that doesn't exist, build does **not** raise — it prints a `cmds.warning()` and skips the constraints entirely, so the rig can "build" successfully with this joint not actually following anything.
- Setting either `translate_alpha` or `rotate_alpha` to `0`, or turning all three axes off in `translate_axes`/`rotate_axes`, means that constraint is not built at all (not built with a zero weight — the node simply doesn't exist). Raising the alpha or re-enabling axes afterward on a live instance requires rebuilding the component; there's no existing constraint node to retarget.
- The module's own header docstring says it "applies parentConstraint + scaleConstraint" with skip flags for "t/r/s channels." The actual `build()` only creates a `pointConstraint` and an `orientConstraint` (translate and rotate) — there is no `scaleConstraint` and no scale skip option anywhere in the contract or the build code. Scale is not driven by this component at all currently; worth confirming with Adrian whether that's a stale comment or a dropped feature.
- Constraint cleanup on `unbuild` depends on finding the matching component network node at build time. If that lookup fails, build prints a warning ("constraints not tracked; unbuild will leak them") and proceeds anyway — the constraints get built but won't be found and deleted later.
- Exactly one joint per instance (`min_joints=1, max_joints=1`); selecting zero or more than one joint fails `can_apply` before the component can be added.
