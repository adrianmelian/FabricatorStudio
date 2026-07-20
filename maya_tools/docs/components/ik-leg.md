---
title: IKLeg
summary: Four-joint IK/FK leg with reverse foot: heel, toe, and foot-roll pivots on a SimpleIK thigh-knee-ankle chain, plus a separate FK ball ctrl for toe wiggle.
category: component
gif: ../media/ik-leg.gif
video:
---

# IKLeg

## What it builds

IKLeg is a subclass of SimpleIK (`IKLegComponent(SimpleIKComponent)`) that runs the base SimpleIK build on the first three joints of a required 4-joint chain (thigh, knee, ankle), then extends the result with a reverse-foot rig. The first three joints get the full SimpleIK treatment: parallel FK, IK, and BLEND duplicate joint chains, an FK ctrl per joint, a diamond pole-vector ctrl, and a foot ctrl (`<ankle>_IK_ctrl`) driving an `ikRPsolver` handle. IKLeg then repositions that foot ctrl to ground level at the ankle's XZ position (identity rotation, so the default `foot`-shaped curve sits flat with the ankle above it), appends two synthetic, unskinned IK-only joints (`ball_ik`, `toe_tip_ik`) onto the IK chain, and builds a 4-transform pivot stack under the foot ctrl: `heel_pivot` to `toe_tip_pivot` to two sibling pivots, `foot_roll_pivot` and `ankle_aim_pivot`. The main IK handle is rerouted to live under `foot_roll_pivot`; two additional `ikSCsolver` handles (ankle-to-ball, ball-to-toe-tip) live under `ankle_aim_pivot` and keep the ball and toe planted while `foot_roll_pivot` lifts the leg.

Two sphere ctrls (heel, toe) drive the heel and toe-tip pivots directly via `parentConstraint(mo=True)`; the toe ctrl is parented under the heel ctrl in the DAG. A `foot_roll` scalar attribute on the foot ctrl drives `foot_roll_pivot`'s rotation through a `reverse` node. The fourth joint (ball) does not get its own FK/IK/BLEND triple chain — it gets one FK ctrl (`ball_ctrl`, circle shape by default) whose offset buffer sits under `fab_controls_grp` and is blended by a parentConstraint between `ankle_aim_pivot` (IK) and the last super-FK ctrl (FK), weighted by the same `ik_fk_blend` attribute the rest of the leg uses. That ctrl drives the ball joint through a standard connector-null constraint pair. A `ball_out` matrix plug is registered on top of SimpleIK's existing outputs.

## When to use it

Use IKLeg on any 4-joint parent-child chain that ends in hip, knee, ankle, ball, where the character needs a floor-contact foot: a game-character leg that needs heel lift, toe roll, and a rockable foot_roll attribute, not just a raw 3-joint IK/FK blend. `can_apply` enforces this: it requires exactly a 4-joint chain, sorted by hierarchy depth, where each of joints 2-4 is the direct child of the joint before it; branching skeletons that don't form a clean parent chain are rejected with a specific "is not a direct child of" message. For a 3-joint limb with no foot (an arm, a tail segment, anything without ground contact), use SimpleIK directly instead — IKLeg's entire reverse-foot stack assumes a 4th ball joint and heel/toe-tip guide positions that a non-foot chain has no use for.

## Options

Pulled from `IK_LEG_CONTRACT.options_schema` in `modules/ik_leg.py`, which spreads `SIMPLE_IK_CONTRACT.options_schema` and then overrides/adds fields:

| Option | Default | What it does |
|---|---|---|
| `ctrl_shape` | `sphere` | Inherited from SimpleIK. Shape for the FK ctrls on the thigh/knee/ankle chain. |
| `ik_ctrl_shape` | `foot` | **Overridden from SimpleIK's `cube` default.** Shape for the IK foot ctrl. Its curve origin must sit at the ankle in XZ — `build()` positions the ctrl at `(ankle.x, 0, ankle.z)` and mirrors the CVs across X on right-side legs. |
| `ctrl_color` | `yellow` | Inherited. Override color applied to most ctrl shape nodes (not the ball ctrl — see Gotchas). |
| `pv_shape` | `diamond` | Inherited. Shape for the pole-vector ctrl. |
| `pv_distance` | `0.5` | Inherited. PV distance as a fraction of chain length. |
| `switch_ctrl_shape` | `cog` | Inherited. Shape for the master IK/FK switch ctrl. |
| `stretchy` | `False` | Inherited. Classic stretchy IK on the thigh/knee/ankle segment only (stretch, no squash; IK mode only). On legs this is measured against the rerouted IK handle rather than the foot ctrl, deliberately, since the foot ctrl sits at floor level and would otherwise read a false rest-length overshoot. |
| `channels` | keyable: tx, ty, tz, rx, ry, rz | Inherited. Applies to the FK ctrls and to the heel/toe ctrls. The foot ctrl always gets full translate+rotate keyable regardless of this option (SimpleIK forces it); the ball ctrl is not touched by this option at all. |
| `ball_ctrl_shape` | `circle` | New in IKLeg. Shape for the toe-wiggle ball FK ctrl. |
| `foot_roll_axis` | `z` (choices: `x`, `y`, `z`) | New in IKLeg. Which local axis of the pivot stack the `foot_roll` attribute drives. The correct axis depends on the scene's up axis and the skeleton's joint-orientation convention — if `foot_roll` yaws or rolls instead of pitching, change this. |
| `heel_position` | `None` (vector3) | New in IKLeg. Heel pivot worldspace position. Normally set by dragging the heel extra guide during guide/skeleton phase; falls back to `resolve_extra_guide_default` if unset. |
| `toe_tip_position` | `None` (vector3) | New in IKLeg. Toe-tip pivot worldspace position, same guide-driven mechanism as `heel_position`. |

## Plugs and spaces

- Input: `parent_in` (matrix, required) — inherited from SimpleIK. `parent_strategy` is `walk_up`.
- Outputs (inherited from SimpleIK): `start_out`, `mid_out`, `end_out` (BIND thigh/knee/ankle world matrices), `ik_ctrl_out` (foot ctrl world matrix), `anchor_out` (cycle-free parent anchor used internally by the PV and foot ctrls).
- Output (new in IKLeg): `ball_out` (matrix, `space_target=True`) — BIND ball joint world matrix, so downstream components can wire a `parent_in` to the ball.
- `space_consumers` are inherited unchanged from SimpleIK: `pv_ctrl` gets a `space` enum (defaults `auto`, `world`, `<id>.ik_ctrl_out`, `<id>.anchor_out`; default `auto`) and the foot ctrl (`ik_end_ctrl` role) gets a `space` enum (defaults `root`, `world`, `<id>.anchor_out`; default `root`). Heel, toe, and ball ctrls have no space-switching attribute.
- `extra_guides`: `heel` and `toe_tip`, both locator-shaped, side-aware. Their captured positions feed the `heel_position` / `toe_tip_position` options above.
- Joint roles: `start` (hip), `mid` (knee, descendant of start), `end` (ankle, descendant of mid), `ball` (descendant of end).

## Animator features

- IK/FK marking-menu actions on the master switch ctrl (inherited from SimpleIK, section "Mode"): **Match to IK** (snaps the foot ctrl and PV ctrl to the current bind pose using the thigh/knee/ankle joints, then sets `ik_fk_blend` to 1), **Match to FK** (snaps the FK ctrls to the bind joints, then sets `ik_fk_blend` to 0), and plain **Switch to IK** / **Switch to FK** (flip the blend with no matching — the pose can pop).
- `ik_fk_blend` (0 = FK, 1 = IK) lives on the master switch ctrl. FK ctrl visibility and IK+PV ctrl visibility are hard-switched at the 0.5 threshold via condition nodes (no half-visible ctrls mid-transition); the heel and toe ctrls are wired to the same IK-mode visibility condition, so they disappear in FK mode along with the foot and PV ctrls.
- `foot_roll` is a single scalar attribute on the foot ctrl (min 0, no max) that rotates `foot_roll_pivot` around the rigger-chosen axis, lifting the leg while the sub-IK handles under the sibling `ankle_aim_pivot` keep the ball and toe planted.
- Heel and toe ctrls (spheres) are directly manipulable in the viewport: heel ctrl rotation drives heel roll/bank/twist, toe ctrl rotation drives toe roll.
- The ball FK ctrl (toe wiggle) stays available in both IK and FK mode since it is blended internally by `ik_fk_blend` rather than gated by the same visibility condition as the rest of the IK ctrls.
- A greyed pole-vector guide line (mid joint to PV ctrl, inherited from SimpleIK) is visible in IK mode to help locate the PV ctrl.
- Selection sets: the component type name `IKLeg` contains "ik", so every built ctrl lands in `all_ik_ctrls` plus its side set. `default_region='leg'` auto-populates `leg[_l|_r]_all` and `leg[_l|_r]_ik` sets.
- Mirroring: FK and PV ctrls use SimpleIK's standard convention (rotations copy verbatim, translations flip). The foot, heel, toe, and ball ctrls use an IKLeg-specific override tied to the ground-level foot frame: `translateX`, `rotateY`, and `rotateZ` flip; everything else copies verbatim.

## Gotchas

- `foot_roll_axis` has no universally correct value — it depends on the scene's up axis and the skeleton's joint-orientation convention. Picking the wrong axis produces a visible yaw or roll instead of a heel-lift pitch, not a build error, so it is easy to miss until you scrub the attribute.
- At rest (`foot_roll = 0`), `foot_roll_pivot`'s driven rotate channel reads `1` degree, not `0` — a deliberately preserved legacy-parity quirk in the reverse-node math, captured into the bind pose at frame 0. This is expected, not a broken pivot.
- The **Match to FK** marking-menu action only snaps the three inherited super-FK ctrls (thigh/knee/ankle); it does not touch the ball ctrl. The toe/ball pose is not part of the IK-to-FK match round-trip.
- Reverse-foot pivots need heel and toe-tip guide positions that are not vertically co-located; if they collapse to the same horizontal point, the foot-frame computation falls back to a default forward vector, which can make the `foot_roll_axis` choice rotate the wrong way.
- Reverse-foot scaffolding (pivots, sub-IK handles, the synthetic `ball_ik`/`toe_tip_ik` joints, heel/toe/ball ctrls, the `foot_roll` reverse node) is only tracked for cleanup if a component network node is found for the instance at build time. If that lookup fails, `build()` emits a `cmds.warning` and `unbuild()` will leak those nodes instead of deleting them.
- `can_apply` requires a strict, unbranched 4-joint parent-to-child chain in exactly hip-to-ball order; a skeleton where an intervening joint breaks that chain fails with a specific "is not a direct child of" message rather than a generic joint-count error.
- The foot ctrl's CVs are only mirrored to the right side on the very first build (only when no persisted CV data exists yet for that ctrl name). Rebuilds always restore the previously saved CV layout instead of re-mirroring, so hand-edited curve tweaks on one side survive rebuilds without being clobbered.
