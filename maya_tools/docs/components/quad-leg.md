---
title: IK Quad
summary: Builds a 5-joint quadruped leg (hip to toe) with IK/FK blend, a hock FK control layered on the IK solve, and a PV control. Held from the public palette for v1.
category: component
gif: ../media/quad-leg.gif
video:
---

# IK Quad

**Status: held from the public component palette for v1.** QuadLeg builds and functions in the tool today, but it is not on the public component list yet; the internal release checklist calls it out as pending until its unbuild path ships. Document it as what it builds and how it behaves, but treat it as internal/in-progress rather than a shipped, supported component until Adrian confirms otherwise (see the accuracy note on this point below).

## What it builds

A Schleifer-style quadruped leg from a 5-joint bind chain: upper_leg, knee, ankle, toe, toe_end. Under the hood it duplicates that chain twice, an IK target chain that hosts three rotate-plane IK handles (upper_leg to ankle, ankle to toe, toe to toe_end) segmenting the bend, and an FK chain (upper_leg, knee, ankle, toe) with one FK control per joint. A blend chain sits between them and drives the bind joints, weighted by an `ik_fk_blend` attribute. On top of the IK solve sits a hock FK control: an invisible pivot transform parented under the foot control, pivoted at the toe joint's position, so rotating the hock control swings the upper-to-ankle IK handle around the toe like a real hock joint. A pole vector control in front of the knee (with a greyed debug line back to the knee) drives knee direction, and a foot IK control at the toe_end position carries a `foot_roll` attribute for toe-tip lift.

The original spec called for an `ikSpringSolver`-driven 4-joint driver chain to give natural multi-joint curl (the true Schleifer recipe). That was dropped: Maya 2025's `ikSpringSolver` has a confirmed bug where `cmds.ikHandle(sol='ikSpringSolver')` locks the chain straight at rest regardless of joint state, and the usual ikRPsolver-swap workaround didn't fix it either. The three RP handles plus the hock control now carry the bend instead of an automatic spring distribution — see Gotchas.

## When to use it

Quadruped fore or hind legs (or any analogous 5-segment limb) where you want IK/FK blending, a marking-menu mode switch, and a manual hock control layered on top of the IK solve rather than relying purely on a pole vector. It is not a drop-in biped leg component; the joint chain and control set (hock control, three-segment RP setup) are quadruped-specific.

## Options

| Option | Default | What it does |
|---|---|---|
| `ctrl_shape` | `sphere` | FK control shape for the upper_leg, knee, and ankle FK controls. |
| `toe_fk_shape` | `sphere` | Toe FK control shape. |
| `ik_ctrl_shape` | `foot` | Foot (IK end) control shape. |
| `pv_shape` | `diamond` | Pole vector control shape. |
| `hock_ctrl_shape` | `sphere` | Hock FK control shape. |
| `switch_ctrl_shape` | `cog` | IK/FK switch control shape. |
| `ctrl_color` | `yellow` | Control color. Side detection overrides this at build time (blue on `lf`, red on `rt`, yellow on `md`) unless you set `ctrl_color` to something other than `yellow` explicitly, in which case your value wins. |
| `stretchy` | `False` | Meant to add classic stretchy IK (distance-based scale) on the spring-driven chain. In the current build path there is no live spring chain to attach it to, so today this option has no visible effect. See Gotchas. |

## Plugs and spaces

**Input:** `parent_in` (matrix, required) — the parent transform space, typically a cog or hip control.

**Outputs** (all matrix, all marked as space-provider targets):
- `start_out` — bind upper_leg joint world matrix
- `mid_out` — bind knee joint world matrix
- `ankle_out` — bind ankle joint world matrix
- `toe_out` — bind toe joint world matrix
- `end_out` — bind toe_end joint world matrix
- `foot_ctrl_out` — foot (IK end) control world matrix
- `anchor_out` — a cycle-free anchor for internal consumers (the foot and PV controls), following the resolved parent control without depending on the IK solve

**Space switching is off.** The contract's `space_consumers` is deliberately empty. A `root`-provider wiring was tried (feeding the foot and PV controls' offset parent matrix through the root joint's world matrix) but it created a dependency cycle: bind is constrained to the blend chain, blend to the IK target chain, and the target chain's rotation depends on the pole vector, which depended on an offset-parent-matrix wired back through the root provider. That cycle left the foot control resting in the wrong world position and caused flips when it was moved. QuadLeg ships without space switching so the rig is functional; re-enabling it needs either dropping the `root` provider (keeping only `world`/`parent`) or making the bind-drive constraints orientation-only. Auto pole-vector placement was deferred for the same cycle reason.

## Animator features

- **`ik_fk_blend`** float attribute (0 to 1) on the master switch control. The switch control follows the blend chain's ankle joint (with a side-aware offset so it isn't sitting on top of the joint), so it stays selectable and tracks the leg in both IK and FK.
- **Marking-menu actions** on the master switch control, "Mode" section:
  - **Switch to IK** — sets `ik_fk_blend` to 1. The pose may pop.
  - **Switch to FK** — sets `ik_fk_blend` to 0. The pose may pop.
  - **Match to FK** — snaps each of the 4 FK controls to its bind joint (parent-first down the chain), then sets `ik_fk_blend` to 0. This is the pop-free way to drop into FK.
  - **Match to IK is intentionally not implemented.** The hock control drives its pivot offset through a live `connectAttr`, not a constraint with an invertible bind target, so reproducing the current bind pose on an FK-to-IK match would need a genuine inverse-rotation solve rather than a mechanical pole-vector-projection match (the pattern used on SimpleIK/IKLeg). Left out rather than guessed at.
- **`foot_roll`** float attribute (-10 to 10) on the foot control. Maps to -90 to +90 degrees of rotation on a group inserted between the foot control and the toe IK handle, lifting the toe tip. The rotation axis (rotateX) is a best guess for a UE5, X-down-chain joint orientation; if `foot_roll` yaws or twists instead of pitching on a given rig, that axis needs to change.
- **IK/FK visibility crossfade:** the foot, PV, and hock controls (plus the PV debug line) become visible as `ik_fk_blend` rises above 0; the FK controls become visible as it drops below 1. It is a direct attribute-to-visibility connection, not a hard 0/1 cutoff.
- **Side-aware coloring:** control color is auto-detected from the first joint's side token (blue on `lf`, red on `rt`, yellow on `md`), independent of the `ctrl_color` option default.
- Marked `side_supported=True` for mirroring, but the contract's `mirror_rules` table is empty for QuadLeg — per-role mirror sign rules are called out as a separate follow-up, not yet wired.

## Gotchas

- **No spring solver, no automatic multi-joint curl.** The spec's driver chain and `ikSpringSolver` were dropped after a confirmed Maya 2025 bug (the solver locks the chain straight at rest no matter the joint state). Bend now comes from the three RP handles plus the hock and PV controls, not an automatic weighted distribution across the chain.
- **`stretchy` option is currently inert.** The option exists in the schema and the stretch math is written, but it's gated on a spring handle that no longer exists in the build (`instance._spring_handle` is always `None` now). Turning it on today does nothing; a rework to drive the target chain's translate off the foot control's distance is noted in the code as future work, not done.
- **Match to IK is unimplemented** for the reason above (hock control isn't an invertible constraint). Switching modes without using Match to FK first can pop the pose.
- **Bind joints need rotation baked into `rotate`, not `jointOrient`.** The build stamps each IK-chain joint's `preferredAngle` from the bind joints' `rotate` values (twice: before and after handle creation) specifically because the RP solvers otherwise settle on the straight-chain solution at rest. This assumes joints authored with the "rotate holds orientation, jointOrient zero" convention (Joint Aimer's output convention); joints with orientation baked into jointOrient and zero rotate at bind won't seed the bend hint correctly.
- **Every IK/FK blend constraint uses `interpType=2`** (quaternion/shortest) deliberately, to avoid Euler wrap-around twist when blending between the IK and FK representations. Called out explicitly in the code as "Gotcha 4" — a reminder that this is a known trap, not an arbitrary setting.
- **`can_apply` requires a true 5-joint parent chain.** Selection order doesn't matter (joints are depth-sorted), but each of the 5 must be a direct parent-child link (upper_leg to knee to ankle to toe to toe_end); a chain with a gap or branch fails validation with a specific error naming the offending joint.
- **Build still loads the `ikSpringSolver` plugin** and creates a live solver node even though the current build path no longer uses it for anything. If that plugin fails to load in a given Maya session, `build()` raises, even though the spring solver isn't actually part of the shipped rig anymore.
- **`unbuild()` silently no-ops on a malformed instance.** If the component instance doesn't resolve to exactly 5 joints, `unbuild()` returns an empty dict rather than raising, which means a broken joint-message-connection state can fail quietly instead of with a clear error.

---
*Accuracy note: this draft's opening status line follows the brief's instruction that QuadLeg is held pending its unbuild path. Reading the current `unbuild()` in `modules/quad_leg.py`, it is not a bare stub — it captures control shape/attribute state, deletes the bind-joint constraints, and cleans up tracked DG nodes. Adrian should confirm whether the "held" reason is really an incomplete unbuild, or something else (Match-to-IK missing, the dropped spring solver, space switching disabled) before this goes out publicly.*
