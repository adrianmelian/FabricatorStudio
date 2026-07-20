---
title: RibbonIKArm
summary: Paid Advanced-Ribbon three-joint IK/FK arm (shoulder, elbow, wrist) with SimpleIK parity plus two per-bone ribbon twist segments driven by antCGi aim roll joints, floating mid ctrls, volume preservation, and an owned finger/fist-curl rig.
category: component
gif: ../media/ribbon-ik-arm.gif
video:
---

# RibbonIKArm

## What it builds

RibbonIKArm is the paid Advanced Ribbon pack's arm: a subclass of SimpleIK (`RibbonIKArmComponent(SimpleIKComponent)`) that runs the full SimpleIK build on a 3-joint shoulder-elbow-wrist chain, then adds ribbon twist deformation and a hand. On top of SimpleIK's FK/IK/BLEND chains, IK/FK switch, auto-tracked pole vector, spaces, and stretchy IK, it builds two per-bone ribbon segments: an upper-arm segment (shoulder to elbow) and a forearm segment (elbow to wrist). Each segment is a mini-RibbonSpine: driven end controls, N floating mid ctrls, and a falloff-skinned NURBS ribbon that distributes twist smoothly between the ends, with composed volume preservation. There is no sine or jiggle here (those are RibbonSpine's).

Each segment's twisting end is driven by one antCGi-inspired aim+IK roll joint per bone (roll-method, not quaternion swing-twist). The shoulder roll drives the upper segment's shoulder end; the forearm/wrist roll drives the forearm segment's wrist end, so pronation twist stays available in both IK and FK. The shared elbow end of each segment keeps the direct elbow-bind drive. Every mid ctrl's up-vector also reads its segment's roll joint, so twist reaches the interior falloff-skinned rows, not just the boundary. Finally it builds the same hand as the free IKArm through the shared `_limb_common.build_hand_from_limb` path: per-finger FK ctrls (metacarpals included) chain-parented under the wrist switch ctrl, plus a layered `fingers_ctrl` fist-curl master.

## When to use it

Use RibbonIKArm when you want AAA-grade forearm and upper-arm twist deformation on a 3-joint arm: smooth, art-directable twist distribution with floating mid ctrls and volume preservation, rather than the free IKArm's single fractional-twist pass. It is the upgrade path from IKArm and shares its finger and fist-curl rig verbatim, so moving up costs no relearning of the hand. `can_apply` is inherited from SimpleIK unchanged (a strict 3-joint parent chain). For a leg with the same ribbon treatment, use RibbonIKLeg; for a spine, RibbonSpine. If ribbon deformation is not needed, the free IKArm covers the same IK/FK/finger feature set without the ribbon layer.

## Options

Pulled from `RIBBON_IK_ARM_CONTRACT.options_schema` in `modules/ribbon_ik_arm.py`, which spreads `SIMPLE_IK_CONTRACT.options_schema` then overrides/adds fields:

| Option | Default | What it does |
|---|---|---|
| `ctrl_shape` | `sphere` | Inherited from SimpleIK. Shape for the FK ctrls. |
| `ik_ctrl_shape` | `cube` | Inherited. Shape for the IK wrist ctrl. |
| `ctrl_color` | `mint` | **Overridden.** Ribbon-family color (all ribbons are green shades). Also the default source for the ribbon mid ctrls and the fist master. |
| `pv_shape` | `diamond` | Inherited. Pole-vector ctrl shape. |
| `pv_distance` | `0.5` | Inherited. PV distance as a fraction of chain length. |
| `switch_ctrl_shape` | `cog` | Inherited. IK/FK switch ctrl shape. |
| `stretchy` | `False` | Inherited. Classic stretchy IK on the arm segment. |
| `channels` | keyable: tx, ty, tz, rx, ry, rz | Inherited. Applies to the FK ctrls. |
| `mid_ctrl_count` | `1` (range 1-8) | New. Floating mid controls per ribbon segment (upper-arm and forearm each get this many). Build option: change in Edit Mode and rebuild. |
| `ribbon_width` | `0.0` (range 0.0-1000.0) | New. Ribbon cross-width for both segments. `0.0` = auto (25% of the bone's length, since each segment spans exactly one bone). Resolved value is persisted so rebuilds match. |
| `ribbon_mid_ctrl_shape` | `sphere` | New. Shape for the floating ribbon mid ctrls. |
| `ribbon_ctrl_color` | `''` (unset) | New. Color for the ribbon mid ctrls. Empty follows the arm's `ctrl_color` (side color); set only to make the ribbon layer a distinct color. |
| `curl_axis` | `z` (choices: `x`, `y`, `z`) | New. Local rotate axis every included phalange curls about. Uniform across all fingers. |
| `finger_fk_shape` | `capsule_ramp` | New. Shape for the per-finger FK ctrls. |
| `fingers_ctrl_shape` | `sine_handle` | New. Shape for the fist-curl master, built near the wrist; the default supplies its own +Y standoff. |
| `fingers_ctrl_color` | `''` (unset) | New. Color for the fist master. Empty follows the arm's `ctrl_color`; set to override. |

## Plugs and spaces

- Input: `parent_in` (matrix, required) — inherited from SimpleIK. `parent_strategy` is `walk_up`.
- Outputs (inherited from SimpleIK): `start_out`, `mid_out`, `end_out` (BIND shoulder/elbow/wrist world matrices), `ik_ctrl_out` (wrist ctrl world matrix), `anchor_out` (cycle-free parent anchor).
- `space_consumers` inherited from SimpleIK: the `pv_ctrl` and the IK wrist ctrl (`ik_end_ctrl` role) each get a `space` enum. Ribbon mid ctrls, finger ctrls, and the fist master have no space switch.
- Joint roles: `start` (shoulder), `mid` (elbow, descendant of start), `end` (wrist, descendant of mid). `limb_features = ('fingers', 'twists')`.
- Both ribbon segments share one global-scale reference: SimpleIK's `anchor_out` (the offset ctrl's world matrix, the same node the arm's parent-scale chain flows through), so global rig scale is applied once, never double-read as stretch.

## Animator features

- Full SimpleIK IK/FK: switch ctrl with `ik_fk_blend`, **Match to IK** / **Match to FK** / **Switch** marking-menu actions, hard visibility switch at 0.5, and an auto-tracked pole vector with a guide line in IK mode.
- Smooth per-bone twist: the upper-arm and forearm ribbon segments distribute twist between their ends via falloff skinning. Shoulder twist is filtered (a plain bend does not twist the shoulder); forearm/wrist twist passes through, so pronation is available in both IK and FK.
- Floating ribbon mid ctrls (`mid_ctrl_count` per segment) let the animator shape the ribbon between the driven ends; they ride the ribbon surface.
- Volume preservation and twist dials live on each segment's ribbon settings board (twist root/tip, volume, and related dials); values are captured on unbuild and restored on rebuild. No sine or jiggle here (that is RibbonSpine).
- The full hand: per-finger FK ctrls follow the wrist in both IK and FK, and the `fingers_ctrl` fist-curl master sums into every included phalange so a per-finger key composes on top. Metacarpals (curl-excluded) get an FK ctrl but no curl input.
- Selection sets: the type name `RibbonIKArm` contains "ik", so ctrls land in `all_ik_ctrls` plus their side set; `default_region='arm'` populates `arm[_l|_r]_all` and `arm[_l|_r]_ik`.

## Gotchas

- **Twist filtering vs pass-through is by design.** The shoulder roll filters twist (anti-candy-wrap: a plain shoulder bend must not induce twist), while the wrist roll passes twist through (pronation must survive into both IK and FK). The mechanism is the roll-aim locator's world-position relationship to its reference joint (a translate-only point constraint filters; a rigid DAG parent passes through), not any joint's rotation channel.
- **Roll joints and their locators are hidden from the viewport on purpose.** They are rig internals that drive the twisting ends; do not unhide or key them.
- **`ribbon_width = 0.0` auto-resolves to 25% of the bone length** (each segment spans exactly one bone, so RibbonSpine's general max(10% of total, 25% of shortest) formula collapses to 25% here). The resolved width is persisted, so later rebuilds match instead of re-measuring a possibly posed or stretched scene.
- **`mid_ctrl_count` and `ribbon_width` are build options.** They only take effect on a rebuild in Edit Mode, not live.
- **`ribbon_ctrl_color` and `fingers_ctrl_color` default to follow the group.** Empty resolves to the arm's own `ctrl_color` (its side color), so the ribbon and fist layers match the rest of the arm. Set an explicit value only to make a layer read as distinct.
- **Legacy type-string history.** RibbonIKArm was formerly the type `'IKArm'`; that string was vacated for the new free IKArm, and pre-rename `'IKArm'` scene/blueprint data is migrated on load to RibbonIKArm (version-gated). A current `'IKArm'` is the free arm, not this one.
- **Both segments share one set of DG buckets on the component node,** and unbuild sweeps them unconditionally (it only needs the component node, not a clean 3-joint unpack). An instance whose joint list has drifted from exactly 3 still gets its full ribbon node-set (skin clusters, uvPin rides, twist boards, volume chains) swept instead of leaking as permanent zombie DG nodes; only the by-name CV/board capture is guarded behind a clean shoulder/elbow/wrist unpack.
- **`curl_axis` is uniform across all fingers** — orient the finger joints consistently or a finger curls on the wrong axis.
