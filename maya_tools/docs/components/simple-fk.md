---
title: SimpleFK
summary: A single-joint FK control with an offset buffer; the basic building block for one posable joint that doesn't need a chain or IK.
category: component
gif: ../media/simple-fk.gif
video:
---

# SimpleFK

## What it builds

One FK control for one joint. Fabricator creates an offset_null/connector_null pair under `fab_nulls_grp`, then an `offset_ctrl` + `ctrl` pair (a CtrlEditor shape) parented under whatever the component's resolved parent control is. The joint is driven through a constraint chain: ctrl to connector_null to joint (parent + scale constraints, maintain offset). The control is tagged with the `fk_ctrl` role for the marking menu, Pose Studio, and selection sets, and its world matrix is exposed as the `ctrl_out` plug.

If the resolved parent turns out to be a joint rather than a control (for example, a SimpleIK bind output like `arm_l.end_out`), SimpleFK does not parent under the joint directly. It parents the offset control under `fab_controls_grp` instead and drives it with a parent/scale constraint back to that joint, so it never pollutes the joint hierarchy or gets stranded when `unbuild` deletes `rig_grp`.

## When to use it

SimpleFK is the single-joint FK component: `min_joints=1, max_joints=1`, so it can only ever be added to exactly one joint. Reach for it on a lone joint that just needs a plain FK control walked up under its parent's control: a clavicle, a jaw, an individual finger or spine joint you are not chaining. For a run of two or more joints in parent-child order, use FKChain instead (same drive-chain and naming convention, so a single joint can later be pulled out into its own SimpleFK without renaming). If the control needs space switching or IK/FK matching, that lives on AdvancedFK, not SimpleFK.

## Options

Pulled directly from `SIMPLE_FK_CONTRACT.options_schema` in `modules/simple_fk.py`:

| Option | Default | What it does |
|---|---|---|
| `ctrl_shape` | `sphere` | CtrlEditor shape used to build the visible FK control. |
| `ctrl_color` | `yellow` | Override color applied to the control's shape nodes. |
| `channels` | keyable: tx, ty, tz, rx, ry, rz | Channel-box setup. Listed channels stay unlocked and keyable; every other transform channel is locked and hidden. |
| `free_float_space` | `False` | Declared in the contract's option schema (so it will appear as a toggle in the Properties panel), but `build()` never reads it. No effect on the built rig as of this file. |
| `anim_pivot` | `False` | Same situation as `free_float_space`: declared, not consumed anywhere in `build()` or `unbuild()`. No effect on the built rig as of this file. |

## Plugs and spaces

- Input: `parent_in` (matrix, required) — the parent transform space. Resolved via `context.resolve_plug(instance.parent_plug)`; `parent_strategy` is `walk_up`, meaning the offset control parents under whatever ancestor control that plug resolves to, rather than always building flat under `fab_controls_grp` (contrast with AdvancedFK, which uses `parent_strategy='world'`).
- Output: `ctrl_out` (matrix, `space_target=True`) — the ctrl's `worldMatrix[0]`, so downstream components can wire their own `parent_in` to this control.
- `side_supported = True`. No `space_consumers` are declared on this contract, so SimpleFK does not add a `space` enum attribute to its control on its own.

## Animator features

- Marking menu (Ctrl+Alt+RMB): the universal "Common" section applies to every KS control, including this one — Zero All, Zero Translates, Zero Rotations, Zero Scale. SimpleFK's contract declares no `actions` and no `space_consumers`, so there is no IK/FK match entry and no "Switch to space" / "Match to space" entries unless something else wires a live `space` attribute onto the control (this component does not).
- Selection sets: because the component type name `SimpleFK` contains "fk", every built control lands in the `all_fk_ctrls` set, plus the side set (`all_left_ctrls` / `all_right_ctrls` / `all_center_ctrls`) and, if the instance has a `region` set, `<region>[_l|_r]_all` and `<region>[_l|_r]_fk`.
- Mirroring: `mirror_rules` tags the `fk_ctrl` role with `negate={translateX, translateY, translateZ}`. Rotation values copy verbatim between the left/right pair (FK controls live in pre-mirrored joint frames); translation values flip sign.

## Gotchas

- `free_float_space` and `anim_pivot` are visible, toggleable options in the UI today but are dead schema entries in the current code: nothing in `build()` reads them. Worth confirming with Adrian whether they're half-wired future work or should be pulled from the schema until implemented.
- If `parent_plug` resolves to a control that doesn't exist in the scene yet, the build falls back silently to `fab_controls_grp` rather than raising — the code comment calls this "defensive, validator should catch," so a real graph-ordering problem could build quietly into the wrong parent instead of failing loudly.
- Custom-edited control curve shapes persist per `ctrl_shape` value (`instance.persisted['cv_data']`, keyed by shape name). Switching `ctrl_shape` to a shape you haven't hand-edited yet will not carry over your custom CVs; it falls back to the library default for that shape. The `reset_ctrl_shapes` build option intentionally clears this and forces the library default.
- `unbuild()` only removes `parentConstraint`/`scaleConstraint` nodes that are direct children of the bind joint. Anything added outside that exact pattern will not get cleaned up before the `rig_grp` delete cascade runs.
- Strictly one joint per instance (`min_joints=1, max_joints=1`). Selecting zero, or more than one, joint will fail `can_apply` before the component can be added.
