---
title: SimpleIK
summary: Three-joint IK/FK chain with a pose-preserving switch, auto pole vector, and an elbow/knee pin; the base component for any bend-joint limb.
category: component
gif: ../media/simple-ik.gif
video:
---

# SimpleIK

## What it builds

SimpleIK takes a 3-joint bind chain (start/mid/end, e.g. shoulder/elbow/wrist or hip/knee/ankle) and duplicates it into three parallel joint chains under a hidden `<start>_setup_grp`: an FK chain, an IK chain (driven by an `ikRPsolver` IK handle), and a BLEND chain. The BLEND chain is parent- and scale-constrained from both the FK and IK chains, weighted by an `ik_fk_blend` attribute (quaternion/shortest-path interpolation, so aimed joints don't wrap-around twist during the blend); the real bind joints are then constrained from the BLEND chain, `mo=True`. Controls are: one FK control per joint (each in its own offset buffer, chained parent to child), an IK end control (wrist/foot) that drives the IK handle's position and the IK end joint's orientation, a pole vector (PV) control that drives the poleVectorConstraint, and a dedicated IK/FK switch control that carries the `ik_fk_blend` attribute and sits just off the end joint. All four live under a per-limb `<start>_ik_grp` outliner container. A template guide line runs from the mid bind joint to the PV control, visible only in IK mode, so the PV is easy to find in the viewport.

## When to use it

The module header calls this the "substrate for the IK family" — the intended base for the IK Arm and IK Leg specializations, which build on SimpleIK's chain/switch pattern and add their own specifics (clavicle + hand for the arm, reverse-foot pivot stack for the leg). Used on its own, SimpleIK is the right call for any generic 3-joint bend chain that needs an IK/FK switch with pole-vector control and doesn't need those limb-specific extras: `can_apply` enforces exactly 3 joints (`min_joints=3, max_joints=3`) that form a direct parent chain in the blueprint's skeleton — selection order doesn't matter (joints are sorted by hierarchy depth first), but each joint must be a direct child of the previous one, so a branched skeleton (e.g. an upper-arm joint with two children) will be rejected with an explicit reason rather than silently picking the wrong branch.

## Options

Pulled directly from `SIMPLE_IK_CONTRACT.options_schema` in `modules/simple_ik.py`:

| Option | Default | What it does |
|---|---|---|
| `ctrl_shape` | `sphere` | CtrlEditor shape used for the FK chain controls. |
| `ik_ctrl_shape` | `cube` | CtrlEditor shape used for the IK end (hand/foot) control. |
| `ctrl_color` | `yellow` | Override color applied to all of this component's control shapes. |
| `pv_shape` | `diamond` | CtrlEditor shape used for the pole vector control. |
| `pv_distance` | `0.5` | PV placement distance, expressed as a fraction of total chain length, both at initial build and in the live auto-PV graph. |
| `switch_ctrl_shape` | `cog` | CtrlEditor shape used for the IK/FK switch control. |
| `stretchy` | `False` | Classic stretchy IK: the chain extends past rest length once the IK end control is pulled beyond it. Stretch only, no squash. IK mode only — FK pose is unaffected. |
| `channels` | keyable: tx, ty, tz, rx, ry, rz | Channel-box setup applied to the FK controls (the IK end control always gets full translate + rotate regardless of this setting — see Gotchas). |

Two more animatable attributes are added at build time but are not part of `options_schema` (they're per-instance keyable attributes on controls, not build-time options): `ik_fk_blend` (float, 0-1, default 1.0) on the switch control, and `pin` (float, 0-1, default 0.0) on the PV control. See Animator features.

## Plugs and spaces

- Input: `parent_in` (matrix, required) — parent transform space, e.g. a clavicle control feeding an arm. `parent_strategy` is `walk_up`.
- Outputs: `start_out`, `mid_out`, `end_out` (matrix, `space_target=True`) — the three BIND joints' world matrices; `ik_ctrl_out` (matrix, `space_target=True`) — the IK end control's world matrix; `anchor_out` (matrix, `space_target=True`) — a cycle-free anchor (the `offset_ctrl`'s world matrix) used so the PV and IK end controls can reference "my parent" without depending on the IK solve itself and creating a feedback loop.
- A fifth, build-time-only output, `auto`, is registered directly by `build()` (not declared in the contract's `outputs` tuple) — the "magic PV" node graph's composed matrix, exposed to animators as the `magic_pv` space choice (see below).
- Space consumers (auto-generated "Switch to space" / "Match to space" marking-menu entries, `space` attribute):
  - `pv_ctrl`: choices `auto` (magic_pv), `world`, `<id>.ik_ctrl_out`, `<id>.anchor_out` — defaults to `auto`.
  - `ik_end_ctrl`: choices `root`, `world`, `<id>.anchor_out` — defaults to `root`.
- `side_supported = True`.

## Animator features

- Marking menu, `master_switch_ctrl` role, section "Mode": **Match to IK** (snaps the IK end + PV controls to the current bind pose, then flips `ik_fk_blend` to 1 — pose holds through the switch), **Match to FK** (snaps each FK control to its bind joint, then flips `ik_fk_blend` to 0 — pose holds), **Switch to IK** / **Switch to FK** (just flips the blend value with no pose match — the visual pose can pop).
- Marking menu, `pv_ctrl` and `ik_end_ctrl` roles: "Switch to space" / "Match to space" entries are synthesized automatically because both roles are declared as space consumers (see Plugs and spaces for the enum choices).
- Every tagged control also carries the universal "Common" section: Zero All, Zero Translates, Zero Rotations, Zero Scale.
- `ik_fk_blend` (keyable, 0-1) lives on the switch control and drives everything: BLEND-chain constraint weights, and a hard-edged (no half-visible states, threshold at 0.5) visibility swap between the FK controls and the IK end + PV controls.
- `pin` (keyable, 0-1) lives on the PV control — a Schleifer-style elbow/knee pin. At `pin=0` the mid/end joints sit at rest length (or the stretchy-computed length, if `stretchy` is on); as `pin` rises toward 1, the joints blend toward the raw signed distance from shoulder-to-PV and PV-to-wrist, so the elbow/knee locks exactly under the PV control. This attribute exists regardless of the `stretchy` option.
- The auto pole vector ("magic_pv" in the space enum) tracks the limb's live bend plane so the PV control doesn't need to be re-keyed every pose; it's on by default (`pv_ctrl.space = auto`).
- Selection sets: `SimpleIK` contains "ik", so every control this component builds lands in `all_ik_ctrls`, plus the appropriate side set (`all_left_ctrls` / `all_right_ctrls` / `all_center_ctrls`) and, if the instance has a `region` set, `<region>[_l|_r]_all` and `<region>[_l|_r]_ik`.
- Mirroring: `fk_ctrl` and `ik_end_ctrl` both copy rotation verbatim and flip translation (X/Y/Z) across the L/R pair; `pv_ctrl` mirrors the same way (it's placed in world space via perpendicular projection); `master_switch_ctrl` has an empty negate set, so no TRS channel is flipped — only `ik_fk_blend`, a custom attribute swapped verbatim by the mirror dispatcher, actually needs to carry over.

## Gotchas

- Near-straight chains can lock the IK solver dead-straight at build. The IK duplicate chain is baked to `rotate=0` (required so the IK chain reproduces bind world rotations exactly and the IK/FK switch doesn't introduce a twist), which means `ikHandle`'s auto `setPreferredAngles` has nothing to seed a bend hint from — it always writes `(0, 0, 0)`. This is harmless on a normally-bent arm (the mid joint's own bind position already gives the RP solver an unambiguous bend plane), but on a straight-legged T-pose there's no bend deviation to read a plane from and the solver can lock dead-straight. Fix, implemented in `SimpleIKComponent._compute_straight_chain_bend_hint` (computed right after the whole-chain `makeIdentity` and before `cmds.ikHandle` — while every joint in the chain is still guaranteed `rotate=0` — with the actual `preferredAngle` write deferred until after `ikHandle` + the `poleVectorConstraint` are wired, so it's the last word over `ikHandle`'s own auto-`setPreferredAngles` pass): measure the angle between the start→mid and mid→end segments; below a small epsilon (2°, chosen well above the float noise a duplicate/matchTransform/makeIdentity round-trip already introduces and well below a normal working bend) the chain counts as straight, and the mid joint's preferredAngle is re-stamped from the resolved PV ctrl position — project the PV's offset from the mid joint onto the plane perpendicular to the chain axis, cross with the chain axis to get the world bend axis, and write a small signed hint (5°) onto whichever of the mid joint's own local rotation axes is most aligned with it. At or above the epsilon, the chain already has a working bend plane from its own geometry and preferredAngle is left exactly as auto-`setPreferredAngles` wrote it — a genuinely bent chain's existing preferred angle is never overwritten. Both IKArm and IKLeg inherit this from the shared `SimpleIKComponent.build` path (the classmethod is overridable per-subclass if a future limb needs different mid-joint axis conventions, but neither overrides it today).
- The auto-PV ("magic_pv") graph has a real singularity: when the live limb axis lines up with the bind-pose perpendicular direction (e.g. the IK hand traces a path that crosses the shoulder's bend plane), the graph's cross-product math amplifies noise and the PV can swing or flicker at those poses. The documented escape hatch is to switch the PV's space to `world`, pose it by hand, and key it.
- `stretchy` only affects IK mode; switching to FK does not carry a stretched pose's length change — the FK chain's own controls are unlocked on translate by default, so a stretched pose can be re-matched by hand via Match to FK, but it isn't automatic.
- `pin` is always present on the PV control even with `stretchy=False`. That means a nominally rigid IK chain can still be pulled off rest length by an animator raising `pin` above 0 — it isn't gated behind the `stretchy` option.
- `can_apply`'s chain-order check is strict: all 3 selected joints must form a direct parent chain after depth-sorting. Selecting 3 joints that don't walk a single unbroken lineage (skipping a joint, or picking across a branch) fails with a specific message rather than silently building a wrong chain.
- Stretch/pin wiring is skipped entirely if the chain's rest length rounds to (near) zero (`rest_total <= 1e-6`) — a degenerate zero-length joint chain gets no stretch or pin attributes at all.
- If the component's network node can't be resolved during `build()` (a `cmds.warning` fires when this happens), the IK/FK blend's `reverse` node isn't tracked for cleanup, and a later `unbuild()` will leak it rather than deleting it.
- Custom-edited control curve shapes persist per `ctrl_shape` / `ik_ctrl_shape` / `pv_shape` / `switch_ctrl_shape` value (`instance.persisted['cv_data']`, keyed by control name). Switching a shape option to a shape you haven't hand-edited yet falls back to the library default rather than carrying your edits over. The `reset_ctrl_shapes` build option clears all of this and forces every control back to its library default shape.
