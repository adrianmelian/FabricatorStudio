---
title: FKAim
summary: Single-joint FK ctrl driven by a separate aim target; the eye-rig substrate, with optional multi-instance link ctrl and space switching.
category: component
gif: ../media/fk-aim.gif
video:
---

# FKAim

## What it builds
FKAim builds a single-joint FK setup: an offset/ctrl pair on the joint (the same offset_ctrl + ctrl pattern as SimpleFK) whose rotation is driven by an aimConstraint from a second control, the aim ctrl, placed a set distance in front of the joint along one of its local axes. A connector_null chain carries the FK ctrl's transform onto the joint, same as SimpleFK. If several FKAim instances share a `link_group` name, the first one to build also creates one shared "link ctrl" parented at the top of the rig next to the World ctrl; every linked instance's aim ctrl is parented under that shared link ctrl instead of under its own parent, so dragging the link ctrl moves every aim target together.

## When to use it
The module is written as the eye-rig substrate: one FKAim per eye joint, both sharing a link_group (for example `eyes`), gives a classic two-eyes-plus-shared-look-at-target setup with a single control the animator can drag to make both eyes converge. It also works solo (no link_group) for any single joint that needs to face a draggable target rather than rotate directly, such as a turret or a single camera-eye prop.

## Options
| Option | Default | What it does |
|---|---|---|
| `ctrl_shape` | `sphere` | Curve shape for the FK ctrl. |
| `aim_shape` | `crosshair` | Curve shape for the aim ctrl. |
| `link_shape` | `eye_mask` | Curve shape for the shared link ctrl. Only used when `link_group` is set. |
| `ctrl_color` | `yellow` | Override color applied to both the FK ctrl and the aim ctrl. |
| `aim_distance` | `30.0` | How far in front of the joint the aim ctrl sits, in scene units, along `aim_axis`. |
| `aim_axis` | `+z` | Which local joint axis aims at the target. Choices: `+x`, `-x`, `+y`, `-y`, `+z`, `-z`. |
| `link_group` | `''` (empty) | Shared name across FKAim instances that should be moved together by one link ctrl. Leave empty for a solo (unlinked) aim ctrl. The Properties panel offers existing group names in use elsewhere in the scene as suggestions. |
| `channels` | keyable: `tx,ty,tz,rx,ry,rz` | Channelbox setup, applied to both the FK ctrl and the aim ctrl. |

## Plugs and spaces
Inputs: `parent_in` (matrix, required) is the parent transform space the FK ctrl's offset is built under.

Outputs: `ctrl_out` (matrix, space-target) is the FK ctrl's world matrix; `aim_ctrl_out` (matrix, space-target) is the aim ctrl's world matrix. Both are valid space-provider sources for other components downstream.

FKAim's own contract declares no space_consumers on the FK ctrl or the aim ctrl themselves. Space switching only appears on the shared link ctrl, and only when a `link_group` has two or more built members: a post-build pass wires a `space` enum on the link ctrl with these entries: `world` (a held bind-pose matrix), `root` (the scene's root joint), and one entry per unique parent component feeding into the group's members (resolved to that parent's primary ctrl). Default space is `root`, falling back to `world` if no root joint is found. A solo instance that happens to have a `link_group` name set still gets a link ctrl built, but the space switch is not wired for single-member groups.

## Animator features
Every FKAim ctrl gets the universal KS marking-menu entries (Ctrl+Alt+RMB): Zero All, Zero Translates, Zero Rotations, Zero Scale, under the Common section. FKAim's contract does not add any component-specific marking-menu actions of its own.

When a link_group has two or more members, the shared link ctrl's dynamically-wired `space` enum is picked up by the marking menu's generic fallback for any ctrl carrying a live space attribute, surfacing "Switch to space: <name>" and "Match to Space: <name>" entries for each provider (world, root, each linked parent).

Mirroring: `side_supported=True`. Both the FK ctrl and the aim ctrl negate their translate channels on an L/R pose mirror swap; rotations copy verbatim, since both ctrls live in pre-mirrored joint frames (the same convention SimpleFK uses).

Canvas color is a soft violet (`#C4A0FF`), chosen to read distinctly from FK (sky blue), IK (orange), and World (yellow) components on the graph.

## Gotchas
- The target joint must already exist in the scene at build time; build() raises immediately if it doesn't.
- If FKAim's resolved parent turns out to be a joint rather than a ctrl, the FK offset (and, when solo, the aim offset) is deliberately not parented directly under it. Instead it's parented under the controls group and driven with a parentConstraint plus scaleConstraint, mirroring SimpleFK's defensive handling, to avoid polluting the joint hierarchy.
- Setting `link_group` on a single instance still creates the full shared link ctrl at build time (parented at the top of the rig), but the post-build sweep only wires the space switch once two or more built FKAim instances share that name. A solo instance with a link_group name gets the extra node with no working space switch. Reserve `link_group` for actual multi-instance shares (left/right eyes and similar).
- Ownership of the shared link ctrl (the `fab_owner` tag used by Pose Studio) is pinned to whichever linked FKAim instance builds it first, since that tag is a 1:1 message connection but the link ctrl is logically shared. This only affects Pose Studio address resolution, not the space switch itself, but removing that first-building instance's component (not just unbuilding it) while other linked members remain can leave that pointer stale.
- The aimConstraint uses a fixed world-space up vector (0, 1, 0) rather than tracking the parent's rotation. This was a deliberate change from an earlier mode that let parent-chain roll leak into the eye as unwanted Z rotation, but it carries the standard aimConstraint risk: if the aim direction ever swings close to world vertical, expect a pop or flip in the FK ctrl's orientation.
- Unbuild explicitly deletes the joint's parent/scale constraints and the FK ctrl's aimConstraint, but it never deletes the shared link ctrl itself. That only happens through the full rig group delete cascade. Unbuilding a single linked instance correctly leaves the link ctrl and sibling offsets in the scene; this can look like leftover nodes if you expect a fully clean per-instance unbuild.
