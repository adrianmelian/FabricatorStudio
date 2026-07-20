---
title: World
summary: Builds the rig's root world control and joint pair; the first component every Fabricator rig needs, since nothing else has anywhere to attach until it exists.
category: component
gif: ../media/world.gif
video:
---

# World

## What it builds
World builds a single joint (the rig root) driven through Fabricator's standard null-pair chain: an `offset_ctrl` + `ctrl` pair under `fab_controls_grp`, a `offset_null` + `connector_null` pair under `fab_nulls_grp`, and a parent/scale constraint chain (ctrl to connector_null to joint, both with `maintainOffset=True`). The ctrl is positioned at the root joint but its rotation is left at world identity rather than matched to the joint's raw orientation, so the world ctrl's axes align to world space even when the root joint itself is rotated (for example a UE5-convention root at rotate -90). It has no parent plug of its own and exposes two outputs for everything downstream: `ctrl_out` (the ctrl's worldMatrix, animator-displaced) and `joint_out` (the root joint's worldMatrix, the baked result after constraints).

## When to use it
World must be the first component in any blueprint. It has no parent plug (`parent_strategy='world'`; the contract note in code says the field isn't actually consulted since World simply has nothing to walk up to), so it's the anchor everything else attaches downstream of. Use it exactly once per rig as the single worldspace root; every other component's `parent_plug` traces back to `world.ctrl_out` eventually.

## Options

| Option | Default | What it does |
|---|---|---|
| `ctrl_shape` | `circle_arrow` | CtrlEditor shape used to build the world ctrl. |
| `ctrl_color` | `yellow` | Override color applied to the ctrl's shape nodes. |
| `channels` | `{'keyable': ['tx','ty','tz','rx','ry','rz']}` | Channelbox setup: whitelisted attrs stay unlocked/keyable; everything else (including scale) is locked, non-keyable, and hidden from the channel box. |

Note: the contract's stated default for `ctrl_shape` is `circle_arrow`, but the `build()` code itself falls back to `circle` if the option key is missing from the instance's options dict (`opts.get('ctrl_shape', 'circle')`). Which value actually wins depends on how blueprint loading populates `instance.options` from the contract defaults, which wasn't traced in these two files — see accuracy_flags.

`side_supported=False` — World is always center; it is not mirrored and has no left/right counterpart.

## Plugs and spaces
- No parent plug. World is the root of the component graph.
- Output `ctrl_out` (matrix, `space_target=True`): the world ctrl's `worldMatrix[0]`. This is the plug most other components wire their `parent_plug` to.
- Output `joint_out` (matrix, `space_target=False`): the root joint's `worldMatrix[0]`, the baked/animated result downstream of the constraint chain.
- World has no `space_consumers` of its own (its ctrl doesn't carry a space-switch enum). It does participate in the rig-wide space-switch system as a provider: the system-level `'root'` space name resolves to the rig's root joint worldMatrix (in practice, this component's joint, since the blueprint's parentless skeleton joint is what `_collect_space_providers` treats as root). The system-level `'world'` space name, by contrast, resolves to a static identity/bind-pose sentinel, not a live follow of this component's ctrl. A consumer that wants to actually track the live world ctrl needs to target the dotted plug reference (`<world_id>.ctrl_out`), not the `'world'` magic name.
- Canvas color: `#FFC857` (plasma yellow), matching the ctrl's default color.

## Animator features
- The World contract declares no marking-menu `Action`s and no `SpaceConsumer` entries, so the world ctrl itself has no IK/FK match or space-switch enum wired onto it.
- `build()` tags the ctrl with role `world_ctrl` via `nodes.tag_ctrl`. Other systems look the ctrl up by that role rather than by name: the animation exporter's root-motion transfer helper (`find_world_ctrl` / `transfer_root_motion` in `anim_root_motion.py`) finds the rig's world ctrl this way to bake root motion onto it before export.
- Channelbox exposes translate + rotate as keyable by default (scale is locked and hidden), per the `channels` option.

## Gotchas
- World must be added before anything else — it has no parent plug, so a blueprint with any other component but no World has nothing to attach the rig to.
- Treat it as a singleton: the contract doesn't hard-block a second World instance, but `side_supported=False` and the `world_ctrl` role tag both assume exactly one. A second instance would tag a second ctrl with the same role, and role-based lookups like `find_world_ctrl` return the first match found — a second World would silently make root-motion export and similar lookups ambiguous.
- `unbuild()` explicitly deletes the `parentConstraint`/`scaleConstraint` nodes that live as children of the joint before the rest of the rig is torn down. Those constraints sit on the joint, not under `rig_grp`, so if any future edit path deletes `rig_grp` directly without going through `unbuild()`, they're left dangling on the joint.
- Don't confuse the `'world'` space-switch option (a fixed identity/bind-pose reference) with this component's live `ctrl_out`. Selecting "World" as a space on some other control's space-switch enum does not make it track this ctrl's animation.
- The ctrl's rotation starts at world identity regardless of the root joint's own orientation — expected behavior (see What it builds), not a bug, but worth knowing before assuming the ctrl's local axes match the joint's.

