---
title: RibbonSpine
summary: A COG-driven spine with hip and chest end controls, floating mid aimers, and a falloff-skinned ribbon that gives free twist distribution plus twist/sine/jiggle/volume dials.
category: component
gif: ../media/ribbon-spine.gif
video:
---

# RibbonSpine

## What it builds
RibbonSpine builds a whole-system COG control (parented under whatever this component's own parent plug resolves to), with a hip control and a chest control underneath it, plus N floating "mid" controls in between. Those controls drive a small set of control joints, which skin a NURBS ribbon surface running root to tip. The surface uses a falloff skin (few control joints, smooth dropoff between them) rather than one control per row, which is what gives the spine its free hip-to-chest twist interpolation. The original bind joints then ride that surface through a uvPin, so the rig has continuous arc-length stretch built in rather than a fixed per-joint FK chain. A settings/board control on the chest carries dial attributes for twist, sine, jiggle, and volume, layered onto the surface as sculpt deformers.

## When to use it
Use it for a spine (or any other 3+ joint chain the studio treats as a spine-like span) where you want organic secondary motion, a single whole-body mover, and non-uniform twist distribution between two end controls, without hand-placing an FK control per joint. It is a good fit whenever the brief calls for sculpted overshoot (sine), jiggle, or volume preservation along a stretchy ribbon, and where downstream components (arms, neck, legs) need a stable hip or chest space to parent into. It is not a mirrored per-side limb component: `side_supported=False` in its contract, so it is a center-line-only build.

## Options
Pulled directly from `RIBBON_SPINE_CONTRACT.options_schema` in `modules/ribbon_spine.py`:

| Option | Default | What it does |
|---|---|---|
| `mid_ctrl_count` | `1` (range 1-8) | Number of floating mid controls between hip and chest. A build option: change it in Edit Mode and rebuild to take effect. |
| `ribbon_width` | `0.0` (range 0.0-1000.0) | Ribbon cross-width. `0.0` means auto: the build resolves a width from the joint chain's arc length (10% of total length, or 25% of the shortest segment, whichever is larger) and persists that resolved value so later rebuilds match instead of re-measuring a posed scene. |
| `cog_ctrl_shape` | `cog_ctrl` | CtrlEditor shape used for the COG control. |
| `ctrl_shape` | `hip_ctrl` | CtrlEditor shape used for the hip control. |
| `chest_ctrl_shape` | `cube_open` | CtrlEditor shape used for the chest control. |
| `mid_ctrl_shape` | `sphere` | CtrlEditor shape used for the mid controls. |
| `ctrl_color` | `yellow` | Color applied to COG, hip, chest, and mid controls. |
| `channels` | `{'keyable': ['tx','ty','tz','rx','ry','rz']}` | Which TRS channels stay keyable on the hip/chest/mid controls (scale is locked separately on those controls regardless of this setting). |

## Plugs and spaces
Contract inputs and outputs, from `RIBBON_SPINE_CONTRACT`:

- Input `parent_in` (matrix, required): the parent transform space this component's controls build under. The COG control is parented here; hip, chest, and mid controls then parent under the COG, not directly under this plug.
- Output `start_out` (matrix, space-target): the hip control's world matrix (`hip_ctrl.worldMatrix[0]`).
- Output `tip_out` (matrix, space-target): the chest control's world matrix (`chest_ctrl.worldMatrix[0]`), described in code as the plug a neck or arm component should parent to.

Joint roles declared: `start` = "Hips (root of the spine chain)", `end` = "Chest (tip of the spine chain)". The contract does not declare any `space_consumers`, so RibbonSpine's own controls do not get a built-in "space" switch enum from this contract; it is a space *provider* for downstream components via `start_out`/`tip_out`, not a space *consumer* itself.

## Animator features
- COG control: a whole-rig mover with scale and visibility locked, translate/rotate keyable, positioned at the root bind joint (pelvis).
- Hip, chest, and mid controls are tagged with roles (`spine_hip_ctrl`, `spine_chest_ctrl`, `spine_mid_ctrl`, `spine_cog_ctrl`, `spine_settings_ctrl`) that feed rig-native selection sets, Pose Studio's per-control identity, and the anim mirror tool. RibbonSpine's contract declares no `actions`, so it does not add its own marking-menu entries (no IK/FK match action applies here; this is not an IK/FK component).
- Mid controls are "always-on aimers": their offset buffer is point-constrained between the hip and chest controls (weighted by its parametric position) and aim-constrained toward the chest. They follow hip/chest movement automatically; the animator's keyable TRS channels on a mid control are an offset on top of that constrained rest position, not a free, independent placement.
- The dial board lives on the COG control (there is no separate settings control; `cog_ctrl` carries the board attrs under the `spine_settings_ctrl` role): keyable dial attributes `enable`, `twist_root`, `twist_tip`, `sine_amplitude`, `sine_wavelength`, `sine_orientation`, `jiggle_amount`, `jiggle_stiffness`, `jiggle_weight`, `volume`. These values are captured on unbuild and restored on rebuild.
- No mirror rules are declared (`mirror_rules=()`) and `side_supported=False`, consistent with this being a center-line-only component.

## Gotchas
- Build raises `RuntimeError` immediately if any bind joint in the component's joint list does not exist in the scene.
- COG naming collision: the COG control wants the bare name `cog_ctrl`. If that name is already taken in the scene, the build falls back to `<chain>_spine_cog_ctrl` and prints a Maya warning. The resolved name is persisted (`cog_ctrl_name`) so a later unbuild finds the right node even after the fallback.
- `mid_ctrl_count` and `ribbon_width` are silently clamped/resolved rather than erroring: mid count clamps into 1-8, and a `ribbon_width` of `0.0` is replaced by an auto-computed value that then gets persisted.
- The uvPin ride that drives the bind joints has hard preconditions, checked before any node is created: every bind joint needs `rotateAxis == (0,0,0)` and unit bind scale `(1,1,1)`, or the build raises `RuntimeError` naming the offending joint.
- Volume (arc-length compensation) depends on the ride's per-joint decompose nodes already existing; if volume is ever invoked out of order relative to the ride, it raises `RuntimeError` rather than silently doing nothing.
- The two end bind joints (hip and chest) have their rotation locked rigidly to their end control joints rather than left to read the ribbon surface's tangent frame directly. This is a deliberate fix for a measured drift (about 1.47 degrees on a test rig) that otherwise arcs anything parented under the hip or chest end, such as legs off the hip or a neck/clavicle off the chest, when that end control rotates. Interior joints keep the free ribbon-driven twist; this lock only applies to the two ends.
- Global rig scale is deliberately not wired directly into this component's internal setup group; scale flows through the control chain and skin instead. The code notes that adding a direct scale wire there compounds scale rather than applying it once.
- Unbuild's cleanup disconnects every incoming connection on each bind joint's translate/rotate/scale channels before deleting the component's nodes; any connection added onto those channels from outside the component will also be swept.
