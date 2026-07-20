---
title: AdvancedFK
summary: A single world-anchored FK control with built-in space switching between its skeleton parent, world, and any joint you add in Properties.
category: component
gif: ../media/advanced-fk.gif
video:
---

# AdvancedFK

## What it builds

One control for one joint (`min_joints=1, max_joints=1`). Fabricator builds the same offset_null/connector_null pair under `fab_nulls_grp` used by SimpleFK, then a single ctrl (a CtrlEditor shape, default `sphere`) parented directly under `fab_controls_grp` rather than under any ancestor control — there is no `offset_ctrl` buffer node. The ctrl is snapped to the joint's position and rotation, its world matrix at that moment is stamped as `fab_bind_matrix`, its local TRS is zeroed, and `offsetParentMatrix` is set to the bind matrix so it sits on the joint. A parentConstraint + scaleConstraint chain (ctrl to connector_null to joint) drives the joint. The ctrl is tagged with the `advfk_ctrl` role and its world matrix is exposed as `ctrl_out`.

The static `offsetParentMatrix` set during `build()` is only a placeholder. A separate post-build pass in `fs_app.build_modules` reads the component's declared space consumer and any user-added joints, then replaces that placeholder with a `wtAddMatrix` network feeding `ctrl.offsetParentMatrix` — that pass is what actually creates the `space` enum attribute and the switching logic.

## When to use it

Reach for AdvancedFK anywhere a single control needs to move between different worldspace parents during animation rather than staying rigidly attached to one ancestor: props, weapons, an end-of-chain accessory, anything that should be able to sit in a hand, then in world, then on a different joint later in the same shot. It is the space-switching counterpart to SimpleFK — same drive chain, but `parent_strategy='world'` and a declared `space_consumers` entry instead of a required `parent_in` input. If the control never needs to switch spaces, SimpleFK is the plainer, cheaper choice.

## Options

Pulled directly from `ADVANCED_FK_CONTRACT.options_schema` in `modules/advanced_fk.py`:

| Option | Default | What it does |
|---|---|---|
| `ctrl_shape` | `sphere` | CtrlEditor shape used to build the ctrl. |
| `ctrl_color` | `yellow` | Override color applied to the ctrl. |
| `channels` | keyable: tx, ty, tz, rx, ry, rz | Channel Box setup. AdvancedFK defaults to keyable translate AND rotate (not rotate-only, as some FK components do) because a space-switched control is typically a positionable accessory that needs to move, not just orient. |

## Plugs and spaces

- Inputs: none declared (`inputs=()`). `parent_strategy='world'` means the ctrl's own DAG parent is always `fab_controls_grp`; it never inherits transforms from an ancestor control the way SimpleFK does, even if the blueprint wires a `parent_plug` to this instance for build-order purposes.
- Output: `ctrl_out` (matrix, `space_target=True`) — the ctrl's `worldMatrix[0]`, registered as `<component_id>.ctrl_out`. Other AdvancedFK instances (or any component reading space providers) can target this one by name.
- Space consumer: the `advfk_ctrl` role gets a `space` enum attribute. Contract defaults are `('parent', 'world')` with `default='parent'`:
  - `parent` resolves to the joint's own skeleton-parent joint's `worldMatrix[0]` — not the blueprint's `parent_plug` wiring. If the joint has no joint parent, it collapses to `world`.
  - `world` holds the ctrl at its bind matrix (identity offset, no drift).
  - Per the contract's own description, the rigger adds more named joints as space targets per-instance in the Properties panel's Spaces section; those are appended after the two defaults in enum order.

## Animator features

- Marking menu (Ctrl+Alt+RMB on the ctrl): the universal Common section (Zero All, Zero Translates, Zero Rotations, Zero Scale), plus one "Match to Space: `<name>`" and one "Switch to space: `<name>`" entry synthesized per live enum value — these come from the contract's `space_consumers`, not a hand-authored `Action`. Match to Space captures the ctrl's current world matrix, flips the enum, then rewrites the local transform so the ctrl does not visually jump; Switch to space just flips the enum and lets the ctrl land wherever the new space puts it.
- No `Contract.actions` are declared for AdvancedFK — it has no IK counterpart, so there is no IK/FK match entry, only the Common set plus the synthesized space actions above.
- Mirroring: `mirror_rules` tags `advfk_ctrl` with `negate={translateX, translateY, translateZ}`. Rotation values copy verbatim between the left/right pair; translation values flip sign, same convention as SimpleFK.
- Selection sets: because the type name `AdvancedFK` contains "fk", every built ctrl lands in `all_fk_ctrls`, plus the side set (`all_left_ctrls` / `all_right_ctrls` / `all_center_ctrls`) and, if the instance has a `region` set, `<region>[_l|_r]_all` and `<region>[_l|_r]_fk`. `default_region` is empty on this contract, so region membership depends entirely on what the rigger sets on the instance.

## Gotchas

- Strictly one joint per instance (`min_joints=1, max_joints=1`); selecting zero or more than one joint fails `can_apply` before the component can be added.
- `build()` raises `RuntimeError` if `instance.joints[0]` doesn't exist in the scene at build time.
- The `space` enum and its `wtAddMatrix` switching network only exist after the full Build Modules pass runs. Immediately after `AdvancedFKComponent.build()` alone, the ctrl only has a static `offsetParentMatrix` at bind and no space attribute yet.
- The default `parent` space follows the joint's own skeleton-parent joint, not whatever the blueprint's `parent_plug` is wired to. Because the ctrl's own parenting (`fab_controls_grp`) and its default space provider are resolved independently, they can point at different things if a blueprint wires this instance's `parent_plug` to something other than its joint's actual joint-parent.
- Ctrl name is derived from the joint's short name (namespace and DAG path stripped) as `<short>_ctrl`; two components whose joints share a short name will collide on that name.
- Custom-sculpted ctrl CVs persist keyed by `ctrl_shape` name (`instance.persisted['cv_data']`). Switching `ctrl_shape` between builds means the previous shape's CVs are not lost but also do not apply to the new shape — same behavior as SimpleFK. The `reset_ctrl_shapes` build option suppresses restoring persisted CVs entirely.
- `unbuild()` only deletes `parentConstraint`/`scaleConstraint` nodes that are direct children of the joint; anything added by hand outside that pattern is not cleaned up before the `rig_grp` delete cascade runs.
