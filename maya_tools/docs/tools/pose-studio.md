---
title: Pose Studio
summary: Save and load full-rig poses that are portable across any Fabricator rig, addressed by component identity rather than node names.
category: animation
gif: ../media/pose-studio.gif
video:
---

# Pose Studio

## What it does
Pose Studio captures a Fabricator rig's full pose (translate/rotate/scale and custom keyable attributes on every tagged control) and writes it to a JSON file with a thumbnail next to it. Poses are identified by component address (component type, side, role, control role, and joint index) instead of control names, so a pose saved on one character can be loaded onto a different rig built from Fabricator as long as the component contracts match. Loading supports partial blending toward the saved pose, applying to only the controls currently selected in the viewport, and keeping the character's World control planted so the load doesn't relocate the rig in the scene.

## Quick start
1. Build the rig with Fabricator first if you haven't (an `FAB_RigBinding` must exist in the scene; Pose Studio refuses to save or load without one).
2. Open Pose Studio from the Bridge toolbar's Library button (glyph LIB) - it lands on the POSE tab - or expand it to the standalone window from that popover's drag bar.
3. In the Characters panel (left), pick the character from the combo. If the scene has more than one live rig, select a control that belongs to the rig you want first, or pick it explicitly from the combo.
4. Pose the rig, then click **+ Save Pose** in the center panel, type a name, and use the framing dialog's embedded viewport (tumble/dolly/pan, or **Frame All** / **Frame Selected**) to compose the thumbnail, then **Capture Thumbnail**.
5. To reapply later, select the pose card in the grid and click **Apply** in the Properties panel (right), or just double-click the card.

## Workflow
The window is a 3-panel layout:
- **Characters (left)**: a combo of live rig bindings (named by namespace, or by rig label if unnamed) plus offline rig labels that only have saved poses on disk. Under it, a Selection Sets list drawn from the rig's baked selection sets (rigs built before the sets bake fall back to runtime-derived region/side sets). Clicking a set selects its controls in the viewport - sets are selection helpers, not silent filters. The **+ Add Selection Set** button is a stub in this version; use the **Sets...** button in the title bar to create and delete user sets from the current viewport selection instead.
- **Poses (center)**: an icon grid of pose cards with thumbnails for the active character only, a search box that filters by pose name substring, and **+ Save Pose**.
- **Properties (right)**: the selected pose's name and source rig, a Blend spinbox (0-1, 1.0 = absolute set), a **Move World** checkbox (off by default, so the World control's saved position is skipped and the character stays where it is in the scene), **Apply** (whole pose), **Apply to Selected Controls** (masks the pose to whatever is selected in the viewport - click a Selection Set first to build that selection), **Recapture Thumbnail**, and **Delete**.

Right-clicking the Character combo offers **Recapture all thumbnails**, which applies every saved pose for that rig in turn, captures a fresh thumbnail, and restores the rig to its pre-recapture pose afterward.

Cross-rig loading (loading a pose saved on one character onto a different one) resolves the same way as same-rig loading: by component address, not by control name, so it fits the pipeline's "build once with Fabricator, share poses across characters" workflow. For IK end controls specifically, the saved pose also carries an FK-equivalent fallback (the IK chain's duplicate-joint rotations) for use when the two rigs' proportions don't match well enough for a straight world-space paste.

## Gotchas
- Saving or applying with no `FAB_RigBinding` in the scene fails outright - build the rig with Fabricator (Build Modules) first.
- With multiple rigs in the scene, Pose Studio resolves the "active" one from your current viewport selection. If nothing you've selected belongs to any rig, save/apply raises rather than guessing; select a control on the rig you mean, or pick it from the Characters combo.
- Only channels that are unconnected or driven solely by animCurve nodes are captured and applied. Channels driven by constraints, expressions, or multMatrix outputs are skipped silently on both save and load - the pose can't override a value that's computed from elsewhere.
- Locked attributes are skipped on both capture and apply.
- Enum-typed custom attributes are only handled for one specific attribute name, `space` - captured and restored by its display label (not its raw index) so a load onto a rig with different enum ordering still resolves correctly. Other enum attributes are captured as raw values.
- Compound custom attributes are skipped entirely in this version.
- The World control (`fab_role == world_ctrl`) is excluded from a normal Apply by default; check **Move World** if you actually want the saved world-space position to apply. **Apply to Selected Controls** is the one exception - if you explicitly selected the World control in the viewport, it applies regardless of the Move World checkbox.
- Cross-rig address resolution can be ambiguous: if a pose's saved role is empty and the target rig has more than one component sharing the same (type, side), that control is skipped rather than guessed onto the wrong component.
- Category is fixed to `animation` in this version - the schema has `bind`/`rest`/`a-pose` slots reserved for later, but the UI doesn't expose choosing them yet.
- Loading a pose file whose schema version doesn't match the current one still loads (best-effort) but logs a warning.
- Thumbnail capture is best-effort on Save - a capture failure logs a warning but does not block the pose from being written.
- Mirrored pose loading ("load mirrored" / mirror in place) is not implemented in this version.

## Troubleshooting
**"Pose Studio: no FAB_RigBinding found in scene."** - No Fabricator rig is built in this scene. Build the rig first (Fabricator to Build Modules), then reopen Pose Studio.

**"Pose Studio: scene has N rigs; select a ctrl on the rig you want to operate on."** - More than one rig is live and nothing in your current viewport selection identifies which one to target. Select a control on the intended rig, or pick it explicitly from the Characters combo.

**"No live rig selected."** on Save or Apply - The Characters panel is showing an offline rig label (poses on disk but no live binding in the scene) rather than a live character. Select a live character row, or select a control on the rig in the viewport - the panel auto-switches to whichever live rig owns your selection.

**Some controls report as skipped after Apply ("N ctrl(s) on the target rig didn't match the pose")** - Usually a cross-rig load where the target rig doesn't have a matching component (type, side, role), or the pose's role tag is empty and ambiguous against multiple components on the target. Check the component's role tag, or accept that pose data for components absent on the target rig won't apply.

**Selection Set click reports "no matching ctrls on the active rig"** - The set is empty for this rig, or the rig predates the baked-selection-sets upgrade and the runtime-derived fallback for that region/kind combination has nothing built. Rebuild the rig to pick up baked sets, or check the region actually has the component kind (FK/IK) you expect.

**"Pose Studio: could not embed Maya viewport for thumbnail framing"** - The framing dialog couldn't find its embedded viewport widget (`MQtUtil.findControl` returned nothing). Close and reopen the Save Pose flow; if it persists, restart Maya.

**"Pose Studio: playblast produced no image."** during thumbnail capture - No active/visible modelPanel was available to capture from. Make sure a 3D viewport panel is open and focused, then retry Save or Recapture Thumbnail.

**Recapture all thumbnails seems slow or leaves a stray pose** - It works by applying every pose in sequence, capturing, then restoring your original pose from an internal snapshot; on a large library this takes a while. If Maya is interrupted mid-run, check for and delete a leftover `__pre_recapture_snapshot__` pose file in that rig's pose folder.
