---
title: IKArm
summary: Free-core three-joint IK/FK arm (shoulder, elbow, wrist) with SimpleIK parity plus limb-driven finger FK, a layered fist-curl master, and a basic forearm twist. The ribbon-deformation arm is the paid RibbonIKArm.
category: component
gif: ../media/ik-arm.gif
video:
---

# IKArm

## What it builds

IKArm is the free-core arm: a subclass of SimpleIK (`IKArmComponent(SimpleIKComponent)`) that runs the full SimpleIK build on a required 3-joint shoulder-elbow-wrist chain, then layers on a hand and a basic forearm twist. From SimpleIK it inherits verbatim the parallel FK, IK, and BLEND duplicate chains, one FK ctrl per joint, a pole-vector ctrl that auto-tracks the elbow, an IK wrist ctrl driving an `ikRPsolver` handle, the IK/FK switch ctrl, the space switches, and optional stretchy IK. On top of that it builds: per-finger FK ctrls for every finger discovered on the arm's limb node (metacarpals included), chain-parented under the wrist IK/FK switch ctrl; a single `fingers_ctrl` fist-curl master near the wrist whose curl-axis rotation sums into every included phalange; and a basic forearm twist where the lower twist joints rotate a fraction of the wrist bind joint's bone-axis rotation.

The hand is built through the exact same `_limb_common.build_hand_from_limb` code path the paid RibbonIKArm uses, so fingers and fist curl behave identically across the free and paid arms. What IKArm strips relative to RibbonIKArm is the ribbon: no per-bone ribbon twist segments, no antCGi roll joints, no ribbon options. This is the deliberate free/paid seam, and it is enforced in code: `ik_arm.py` must never import `_ribbon_common` or any `ribbon_*` module (guarded by `_dev/test_open_core_import_boundary.py`).

## When to use it

Use IKArm on any 3-joint shoulder-elbow-wrist chain that needs a production IK/FK arm with a working hand and honest secondary forearm rotation, but does not need ribbon-quality twist deformation. It is the right default arm for a game character where the free core is the whole rig. `can_apply` is inherited from SimpleIK unchanged: a strict 3-joint parent chain is exactly what a shoulder-elbow-wrist arm needs. Reach for the paid RibbonIKArm instead when you want per-bone ribbon twist distribution (smooth upper-arm and forearm twist with floating mid ctrls and volume preservation) rather than IKArm's single fractional-twist pass. For a leg, use IKLeg or RibbonIKLeg; for a chain with no hand, use SimpleIK directly.

## Options

Pulled from `IK_ARM_CONTRACT.options_schema` in `modules/ik_arm.py`, which spreads `SIMPLE_IK_CONTRACT.options_schema` and then overrides/adds fields:

| Option | Default | What it does |
|---|---|---|
| `ctrl_shape` | `sphere` | Inherited from SimpleIK. Shape for the FK ctrls on the shoulder/elbow/wrist chain. |
| `ik_ctrl_shape` | `cube` | Inherited from SimpleIK (not overridden here, unlike IKLeg's `foot`). Shape for the IK wrist ctrl. |
| `ctrl_color` | `amber` | **Overridden from SimpleIK.** IK-family color (all IKs are orange shades). Applies to the FK/IK/PV/switch ctrls, and is the default source for the fist-curl master's color. |
| `pv_shape` | `diamond` | Inherited. Shape for the pole-vector ctrl. |
| `pv_distance` | `0.5` | Inherited. PV distance as a fraction of chain length. |
| `pv_position` | (unset) | Inherited. Pole vector worldspace position, captured from the **Pole vector marker guide** (`pv` extra guide). Drag the marker to override the automatic placement — handy when an elbow's PV builds somewhere you don't want it. See SimpleIK's "Pole vector placement". |
| `switch_ctrl_shape` | `cog` | Inherited. Shape for the IK/FK switch ctrl. |
| `stretchy` | `False` | Inherited. Classic stretchy IK on the arm segment (stretch, no squash; IK mode only). |
| `channels` | keyable: tx, ty, tz, rx, ry, rz | Inherited. Applies to the FK ctrls; the IK wrist ctrl always gets full translate+rotate keyable regardless. |
| `curl_axis` | `z` (choices: `x`, `y`, `z`) | New in IKArm. Local rotate axis every included phalange curls about. Uniform across all fingers, so consistent finger-joint orientation is the rigger's job. |
| `finger_fk_shape` | `capsule_ramp` | New in IKArm. Shape for the per-finger FK ctrls, kept separate from the arm's `ctrl_shape` so fingers read at their smaller scale. |
| `fingers_ctrl_shape` | `sine_handle` | New in IKArm. Shape for the fist-curl master ctrl (`fingers_ctrl`), built at the wrist. The default `sine_handle` supplies its own +Y standoff so the ctrl pivot sits at the wrist. |
| `fingers_ctrl_color` | `''` (unset) | New in IKArm. Color for the fist-curl master. Empty follows this arm's own `ctrl_color` (its side color); set a value to override. |

## Plugs and spaces

- Input: `parent_in` (matrix, required) — inherited from SimpleIK. `parent_strategy` is `walk_up`.
- Outputs (inherited from SimpleIK): `start_out`, `mid_out`, `end_out` (BIND shoulder/elbow/wrist world matrices), `ik_ctrl_out` (wrist ctrl world matrix), `anchor_out` (cycle-free parent anchor used internally by the PV and IK ctrls).
- `space_consumers` are inherited unchanged from SimpleIK: the `pv_ctrl` gets a `space` enum and the IK wrist ctrl (`ik_end_ctrl` role) gets a `space` enum. Finger ctrls and the fist-curl master have no space-switching attribute.
- Joint roles: `start` (shoulder), `mid` (elbow, descendant of start), `end` (wrist, descendant of mid).
- `limb_features = ('fingers', 'twists')` — the arm's derived limb carries finger membership (`finger_roots[]`, `curl_excluded[]`) and twist membership (`twist_lower[]`, `twist_upper[]`) on its fab_limb node, resolved live at build time, never a stored option.

## Animator features

- IK/FK marking-menu actions on the switch ctrl (inherited from SimpleIK): **Match to IK**, **Match to FK**, and plain **Switch to IK** / **Switch to FK**. `ik_fk_blend` (0 = FK, 1 = IK) lives on the switch ctrl; FK and IK+PV ctrl visibility hard-switches at the 0.5 threshold.
- The pole-vector ctrl auto-tracks the true blend-chain elbow (SimpleIK's `aim_pv_at_mid`), and a greyed guide line from the elbow to the PV ctrl is visible in IK mode.
- Per-finger FK ctrls are chain-parented under the wrist switch ctrl, so the whole hand follows the wrist in both IK and FK. Each included phalange also reads the fist-curl master.
- The `fingers_ctrl` fist-curl master sums its local curl-axis rotation into every included phalange's offset, so a per-finger key on a single finger composes on top of the fist curl rather than overriding it. Metacarpals (curl-excluded) get their own FK ctrl but no curl input.
- Basic forearm twist: the lower twist joints rotate a fraction (t) of the wrist bind joint's bone-axis rotation, distributing pronation along the forearm.
- Selection sets: the type name `IKArm` contains "ik", so every built ctrl lands in `all_ik_ctrls` plus its side set; `default_region='arm'` auto-populates `arm[_l|_r]_all` and `arm[_l|_r]_ik`.
- Mirroring uses SimpleIK's standard convention (rotations copy verbatim, translations flip).

## Gotchas

- **Free-tier twist is deliberately basic.** Only the lower (forearm) twist joints are driven, each by a fraction of the wrist's bone-axis rotation; upper-arm twist members ride rigid in v1. This is the free/paid trade: the paid RibbonIKArm is the upgrade, with real per-bone ribbon twist distribution on both bones. If you need smooth upper-arm twist, use RibbonIKArm.
- **`curl_axis` is uniform across all fingers.** Consistent finger-joint orientation is the rigger's responsibility; if one finger is oriented to flex on a different local axis, it will curl wrong. Orient the finger joints consistently before relying on the fist master.
- **`fingers_ctrl_color` defaults to follow the group.** Empty resolves to the arm's own `ctrl_color` (its side color: lf blue, rt red, md yellow), so the fist ctrl matches the rest of the arm. An earlier fixed-red default was a bug (a blue-group left arm's fist ctrl built red). Set an explicit color only to make the fist master a distinct layer on purpose.
- **Legacy type-string reuse.** IKArm reuses the `'IKArm'` type string that the paid ribbon arm vacated when it was renamed to RibbonIKArm. Old scenes and blueprints whose component type is a pre-rename `'IKArm'` are migrated on load to RibbonIKArm (version-gated), so never assume a pre-rename `'IKArm'` is this free component.
- **Finger membership is not a stored option.** It lives on the arm's fab_limb node (`finger_roots[]`, `curl_excluded[]`) and is walked live at build, so an inserted joint auto-joins its finger on the next rebuild, and a rename or reorder is picked up transparently. If the limb node can't be resolved (a hand-rolled instance bypassing the normal create path), the hand degrades to zero fingers and the `fingers_ctrl` still builds inert.
- **Curl and twist DG nodes live outside `rig_grp`.** The fist-curl sum nodes and the forearm-twist scalar nodes have no DAG parent, so they are swept explicitly from the component node's buckets at unbuild rather than cascading with `rig_grp`; the sweep is unconditional and only needs the component node.
