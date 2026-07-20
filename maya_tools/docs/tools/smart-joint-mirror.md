---
title: Smart Joint Mirror
summary: Mirrors a selected joint chain across the YZ plane with a live link while you pose the source side, then freezes the result permanently on disconnect.
category: rigging
gif: ../media/smart-joint-mirror.gif
video:
status: deprecated
---

> **DEPRECATED (2026-07-12).** Replaced by the Armature system's Skeleton Symmetry (live, always-on mirroring inside Edit Mode — see the Armature doc). This page stays for internal reference; the public site excludes it.

# Smart Joint Mirror

## What it does
Smart Joint Mirror takes the joints you select, plus every descendant below them, and builds a mirrored counterpart chain across the YZ plane (X negated), matching position and orientation. Rather than a one-shot copy, it wires a live dependency-graph network (a pair of `multMatrix` nodes sandwiching the source joint's world matrix between a static mirror matrix, feeding a `decomposeMatrix` into the mirrored joint's translate/rotate) so edits to the source side keep updating the mirrored side in real time. Names flip automatically using the studio's shared side-token convention (`L_`/`R_`, `_l`/`_r`, `lf_`/`rt_`, `left`/`right`); a joint with no recognized side token, or whose mirrored name already exists in the scene, is rejected before anything is built. Disconnecting freezes the mirrored chain's current pose as static translate/rotate values and deletes the live network nodes, turning it back into a normal, independent joint chain.

## Quick start
1. Select the root joint of the side you want to mirror from. The tool automatically pulls in every descendant joint below it, so you only need to select the top of the chain.
2. Make sure every joint in that chain has a recognized side token in its name (e.g. `L_arm`, `arm_l`, `lf_arm`, `left_arm`). A joint without one, or a mirrored name that's already taken in the scene, stops the whole operation before any node is created.
3. In the Bridge toolbar's Build group, open the Mirror popover and click **Mirror Joints (YZ)** (or open the Constraints popover and click **Mirror** instead, which runs the identical command).
4. Pose or edit the source side; the mirrored side updates live as a result of the DG connections.
5. When the mirrored pose is the one you want to keep, click **Delete Constraints** in the Constraints popover (or run **Disconnect Mirror Constraint** from the Skeleton menu or the legacy shelf's right-click popup) to freeze the mirrored joints and remove the live link.

## Workflow
Smart Joint Mirror is reachable from four places, all calling the same two functions in `joint_mirror_app.py` (`mirror_joints`, `disconnect_mirror`): the Bridge toolbar's Mirror popover (**Mirror Joints (YZ)**) and Constraints popover (**Mirror**, plus **Delete Constraints**, which deletes real constraints on the selection AND severs any live mirror network on it); the classic Maya **Skeleton** menu (**Mirror Joints** / **Disconnect Mirror Constraint**); and the legacy shelf's **Mirror Joints** button with a right-click **Disconnect Mirror Constraint** entry.

Run from the Bridge toolbar, the operation is wrapped in a single named undo chunk, and a failure (bad selection, missing side token, name collision, built Fabricator component) surfaces as a `[FS toolbar] Mirror Joints: ...` warning line in the Script Editor with a full traceback, not the toolbar's softer in-viewport message, because the tool raises a plain `RuntimeError` rather than the toolbar's own `ToolbarUserError`. Run from the Skeleton menu or the shelf, the call goes straight to `joint_mirror_app.mirror_joints()` / `disconnect_mirror()` with no undo-chunk wrapper of its own, so a failure partway through is not grouped into one undo step the way a toolbar-driven run is.

Fabricator (the flagship rigging tool) treats Smart Joint Mirror as a guardrail rather than something you drive directly mid-build: `mirror_joints` refuses outright on any joint already owned by a built Fabricator component (checked via `is_built` on the joint's owning component node, with the fix being "unbuild the rig (Edit Rig) first"), and Fabricator's Build Modules step automatically calls `disconnect_mirror` on every joint owned by a component before it bakes, because the mirror's live DG connections would otherwise fight the module build's own constraint chain.

Two additional, scene-wide functions live in the same module: `mirror_constrain_pairs()` and `break_all_mirror_constraints()`. They wire or break a live mirror across every existing left/right joint pair in the scene at once, built for skeletons that already have both sides present (an imported UE5 mannequin, for example) using a hidden ghost transform plus `maintainOffset` constraints so the right side keeps its existing bind orientation. As of this read, neither is wired to a button in Bridge, the Skeleton menu, or the shelf; they run from the Script Editor only.

## Gotchas
- The mirror plane is hardcoded to YZ (negates X) for this version. There is no option to mirror across a different plane.
- Only translate and rotate are mirrored; joint scale is not touched, so non-uniform scale on the source side will not carry across.
- The mirrored chain is created fresh, never repositioned from an existing joint. If a joint with the flipped name already exists, Mirror Joints refuses outright rather than reusing or overwriting it.
- Mirroring a mid-chain joint without first mirroring its parent reproduces Maya's own stock-mirror "mistake": the new mirrored joint parents as a sibling under the original's parent instead of under a mirrored parent, because there is no flipped parent yet to attach to.
- Disconnect is permanent by design; there is no relink or reconnect call. Re-mirroring after a disconnect is a fresh Mirror Joints run, and it refuses if the mirrored joint (now static) still exists in the scene.
- Only the first side token found in a name gets flipped (the name is split on `_`, `:`, `|` and the first match wins), matching the intent that e.g. `L_arm_lt_finger` mirrors to `R_arm_lt_finger`. The `lt` token is asymmetric: it flips to `rt`, but `rt` flips back to `lf`, not `lt` — a chain built with `lt`/`rt` naming will not round-trip back to its original tokens.
- `jointOrient` on the mirrored joints is zeroed by design (the live network folds orientation into `rotate` instead), so it reads 0 in the Channel Box for as long as the mirror is connected.
- Mirroring is refused on any joint already owned by a built Fabricator component; unbuild the rig via Edit Rig first, or mirror a different joint.
- Running from the classic Skeleton menu or the shelf (as opposed to the Bridge toolbar) does not group the operation into a single undo chunk, so an interrupted or partially failed run may take more than one Undo to fully unwind.

## Troubleshooting
**"Mirror Joints: select at least one joint."** Nothing valid was selected. Select a joint (or a chain root) and try again.

**"Mirror Joints: selection contains no joints."** The collected hierarchy under your selection contains no joint-type nodes.

**"Mirror Joints: '<name>' has no recognized side token. Rename to include a side token (e.g. L_arm, arm_l, lf_arm, left_arm) before mirroring."** Rename the joint, and any other un-tokened joint in the same chain, to carry a recognized side token, then retry.

**"Mirror Joints: cannot flip side token in '<name>'."** A side token was detected but the flip itself failed. Check the name for anything unusual around the `_`, `:`, or `|` separators.

**"Mirror Joints: R joint '<name>' already exists. Delete it first, or use Disconnect Mirror if you want to refresh the link."** The mirrored name is already taken in the scene. Delete the existing joint, or run Disconnect Mirror Constraint / Delete Constraints on the current pair first if you want to rebuild the live link from scratch.

**"Mirror Joints: joint '<name>' belongs to a built Fabricator component. Unbuild the rig (Edit Rig) first, or mirror a different joint."** Fabricator has already built controls on top of this joint. Unbuild via Edit Rig, then mirror.

**Mirrored chain appears to snap toward the origin right after Delete Constraints / Disconnect Mirror.** Should not happen in normal use: the tool explicitly captures each mirrored joint's live translate/rotate and re-stamps those values as static immediately after deleting the mirror network, specifically to prevent this collapse. If you see it anyway, a joint was likely deleted, renamed, or otherwise altered in the moment between the network's deletion and the re-stamp.

**An error shows as a full traceback in the Script Editor instead of a quiet heads-up message.** Expected when running from the Bridge toolbar: Smart Joint Mirror's checks raise a plain `RuntimeError`, not the toolbar's own `ToolbarUserError`, so the command runner logs a `[FS toolbar] Mirror Joints: ...` warning plus a traceback rather than the softer in-viewport message used for user-fixable errors elsewhere in the toolbar. The message text after the prefix is still the actionable one; read it in the Script Editor.
