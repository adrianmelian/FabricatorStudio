---
title: RibbonIKLeg
summary: Paid Advanced-Ribbon four-joint IK/FK leg that inherits IKLeg's reverse foot verbatim and adds two per-bone ribbon twist segments (thigh, shin) driven by antCGi aim roll joints, plus limb-sourced twist riders. The foot is not ribboned.
category: component
gif: ../media/ribbon-ik-leg.gif
video:
---

# RibbonIKLeg

## What it builds

RibbonIKLeg is the paid Advanced Ribbon pack's leg (module 3 of 3, after RibbonSpine and RibbonIKArm). It subclasses IKLeg (`RibbonIKLegComponent(IKLegComponent)`) and inherits the entire reverse-foot rig verbatim: the SimpleIK thigh-knee-ankle IK/FK/BLEND chains, the reverse-foot pivot stack, heel and toe ctrls, the `foot_roll` attribute, and the ball FK toe-wiggle ctrl, all built on the required 4-joint thigh-knee-ankle-ball chain. On top of that it adds two per-bone ribbon twist segments, mirroring RibbonIKArm one level up: a thigh segment (thigh to knee) and a shin segment (knee to ankle), each a falloff-skinned ribbon with N floating mid ctrls and volume preservation. The foot is not ribboned.

Each segment's twisting end is driven by an antCGi aim roll joint: the thigh roll filters twist (anti-candy-wrap under hip swing), the ankle roll passes twist through (available in both IK and FK, reverse-foot yaw included). Beyond the segments' own internal ride joints, RibbonIKLeg also wires twist riders sourced live from the leg's limb node: any `twist_upper[]` member joints ride the thigh segment's surface and any `twist_lower[]` members ride the shin segment's surface, via a second uvPin per segment. The internal ride joints stay as the standalone fallback, so a leg with no limb (or empty twist multis) builds identically to the roll-joint-only version. The inherited free-leg fractional shin twist is deliberately turned off here, since the ribbon riders own the twist joints.

## When to use it

Use RibbonIKLeg when a character leg needs both a full reverse foot (heel lift, toe roll, a rockable `foot_roll`, toe wiggle) and AAA-grade ribbon twist deformation on the thigh and shin. It is the ribbon upgrade of IKLeg and inherits IKLeg's foot verbatim, so nothing about the foot changes when you move up: only the twist deformation on the two upper bones is added. `can_apply` is inherited from IKLeg unchanged: a strict, unbranched 4-joint hip-to-ball parent chain. For an arm with the same ribbon treatment use RibbonIKArm; for a spine, RibbonSpine. If you need the reverse foot but not ribbon twist, use the free IKLeg.

## Options

Pulled from `RIBBON_IK_LEG_CONTRACT.options_schema` in `modules/ribbon_ik_leg.py`, which spreads `IK_LEG_CONTRACT.options_schema` then overrides/adds the four ribbon fields (mirroring RibbonIKArm verbatim so riggers learn one ribbon vocabulary across the pack):

| Option | Default | What it does |
|---|---|---|
| `ctrl_shape` | `sphere` | Inherited from IKLeg/SimpleIK. Shape for the FK ctrls on the thigh/knee/ankle chain. |
| `ik_ctrl_shape` | `foot` | Inherited from IKLeg. Shape for the IK foot ctrl (its curve origin sits at the ankle in XZ). |
| `ctrl_color` | `green` | **Overridden.** Ribbon-family color. Applies to the FK/IK/PV/switch and heel/toe ctrls, and is the default source for the ribbon mid ctrls. |
| `pv_shape` | `diamond` | Inherited. Pole-vector ctrl shape. |
| `pv_distance` | `0.5` | Inherited. PV distance as a fraction of chain length. |
| `switch_ctrl_shape` | `cog` | Inherited. IK/FK switch ctrl shape. |
| `stretchy` | `False` | Inherited. Classic stretchy IK on the thigh/knee/ankle segment. |
| `channels` | keyable: tx, ty, tz, rx, ry, rz | Inherited. Applies to the FK and heel/toe ctrls. |
| `ball_ctrl_shape` | `circle` | Inherited from IKLeg. Shape for the toe-wiggle ball FK ctrl. |
| `foot_roll_axis` | `z` (choices: `x`, `y`, `z`) | Inherited from IKLeg. Which local axis of the pivot stack `foot_roll` drives; depends on up axis and joint orientation. |
| `heel_position` | `None` (vector3) | Inherited from IKLeg. Heel pivot worldspace position, normally set by the heel guide. |
| `toe_tip_position` | `None` (vector3) | Inherited from IKLeg. Toe-tip pivot worldspace position, from the toe-tip guide. |
| `mid_ctrl_count` | `1` (range 1-8) | New. Floating mid controls per ribbon segment (thigh and shin each get this many). Build option: change in Edit Mode and rebuild. |
| `ribbon_width` | `0.0` (range 0.0-1000.0) | New. Ribbon cross-width for both segments. `0.0` = auto (25% of the bone's length; each segment spans one bone). Resolved value persisted so rebuilds match. |
| `ribbon_mid_ctrl_shape` | `sphere` | New. Shape for the floating ribbon mid ctrls. |
| `ribbon_ctrl_color` | `''` (unset) | New. Color for the ribbon mid ctrls. Empty follows the leg's `ctrl_color` (side color); set only for a distinct ribbon layer. |

## Plugs and spaces

- Input: `parent_in` (matrix, required) — inherited from SimpleIK via IKLeg. `parent_strategy` is `walk_up`.
- Outputs (inherited from IKLeg): `start_out`, `mid_out`, `end_out` (BIND thigh/knee/ankle world matrices), `ik_ctrl_out` (foot ctrl world matrix), `anchor_out` (cycle-free anchor), and `ball_out` (BIND ball joint world matrix, `space_target`) so downstream can parent to the ball.
- `space_consumers` inherited: the `pv_ctrl` and the foot ctrl (`ik_end_ctrl` role) each get a `space` enum. Heel, toe, ball, and ribbon mid ctrls have no space switch.
- `extra_guides`: `heel` and `toe_tip` (inherited from IKLeg), whose captured positions feed `heel_position` / `toe_tip_position`.
- Joint roles: `start` (hip), `mid` (knee), `end` (ankle), `ball`. `limb_features = ('twists',)` — legs twist, never finger.
- Both ribbon segments share one global-scale reference: SimpleIK's `anchor_out` (registered under this instance by the inherited SimpleIK build), so global rig scale is applied once, never double-read as stretch.

## Animator features

- The complete IKLeg reverse foot, unchanged: IK/FK switch with **Match to IK** / **Match to FK** / **Switch** marking-menu actions, the `foot_roll` scalar that lifts the leg while the ball and toe stay planted, directly manipulable heel and toe ctrls, and the ball FK ctrl for toe wiggle (available in both IK and FK).
- Smooth per-bone twist on the thigh and shin ribbon segments (falloff-skinned distribution). Thigh twist is filtered (hip swing does not candy-wrap the upper thigh); ankle twist passes through and is available in both IK and FK.
- Floating ribbon mid ctrls (`mid_ctrl_count` per segment) shape the ribbon between the driven ends.
- Volume preservation and twist dials on each segment's ribbon settings board; captured on unbuild, restored on rebuild. No sine or jiggle (that is RibbonSpine).
- Twist riders: any `twist_upper[]` member joints on the leg's limb node ride the thigh surface, any `twist_lower[]` members ride the shin surface, so extra twist joints deform with the ribbon rather than needing their own driver.
- Selection sets: the type name `RibbonIKLeg` contains "ik", so ctrls land in `all_ik_ctrls` plus their side set; `default_region='leg'` populates `leg[_l|_r]_all` and `leg[_l|_r]_ik`.

## Gotchas

- **The foot is not ribboned.** The entire reverse-foot stack (pivots, `foot_roll`, heel/toe/ball ctrls) is pure IKLeg; ribbon twist covers only the thigh and shin. Every IKLeg foot gotcha still applies here, in particular: `foot_roll_axis` has no universally correct value (a wrong pick yaws or rolls instead of pitching, with no build error); at rest the driven `foot_roll_pivot` reads 1 degree, not 0, by legacy-parity design; and **Match to FK** does not snap the ball ctrl.
- **Twist filtering vs pass-through is by design,** the same mechanism as RibbonIKArm: the thigh roll-aim locator is translate-only (filters twist), the ankle roll-aim locator is rigidly parented to the ankle bind joint (passes twist through, reverse-foot yaw included).
- **The inherited free-leg fractional shin twist is off here** (`free_fractional_lower_twist = False`). The ribbon riders own the twist joints; leaving the free fractional twist on would put two drivers on one channel and fight the already-driven guard.
- **Twist rider membership is read live from the limb node** (`twist_upper[]` to the thigh segment, `twist_lower[]` to the shin), never persisted, so a rename or re-add is picked up on the next build. A leg with no limb or empty twist multis builds identically to the roll-joint-only version. The pinned twist joints themselves are never deleted at unbuild (only the pin infrastructure is swept), so they can safely carry skin weights.
- **`ribbon_width = 0.0` auto-resolves to 25% of the bone length** (each segment spans one bone) and the resolved value is persisted so rebuilds match instead of re-measuring a posed scene.
- **`mid_ctrl_count` and `ribbon_width` are build options** — they take effect only on an Edit-Mode rebuild.
- **`ribbon_ctrl_color` defaults to follow the group** (the leg's `ctrl_color` / side color); set it only to make the ribbon mid ctrls a distinct layer.
- **Both segments share one set of DG buckets on the component node,** swept unconditionally at unbuild (needs only the component node). A joint list that has drifted from exactly 4 still gets its full ribbon node-set swept instead of leaking zombie skin clusters; only the by-name CV/board capture is guarded behind a clean thigh/knee/ankle/ball unpack.
