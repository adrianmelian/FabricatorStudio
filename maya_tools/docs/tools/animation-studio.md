---
title: Animation Studio
summary: Saves keyframed animation clips as portable JSON addressed by component identity, so a clip loads onto any Fabricator rig, not just the one it was recorded on.
category: animation
gif: ../media/animation-studio.gif
video:
---

# Animation Studio

## What it does
Animation Studio captures keyframed animation on the active Fabricator-built rig over a chosen time range and writes it to a `<name>.anim.json` file, one file per clip. Each clip is addressed by the same cross-rig component-identity tuples Pose Studio uses (component type, side, role, ctrl role, joint index, plus a same-rig component-id fast path), so a saved clip can resolve onto any rig built from a Fabricator blueprint, not only the rig it was captured on. Only keyable attributes on Fabricator-tagged controls that already carry at least one key get captured; everything else on the rig is ignored. Saving also renders a static PNG and a hover-preview GIF from a Maya playblast, so clips are recognizable at a glance in the gallery.

## Quick start
1. Open Animation Studio from the fsAnim shelf ("Animation Studio" button, or right-click the "Pose Studio" shelf button and pick "Animation Studio" from the popup), or from Bridge's Library popover on the ANIM tab (next to POSE).
2. In the Characters panel, pick the rig to capture from (live rig bindings in the scene, or an offline rig label that already has saved clips).
3. On the Maya timeline, highlight a range on the time slider, or just set your playback range. Animation Studio uses the highlighted range if it spans more than one frame, otherwise it falls back to the playback range.
4. Click "+ Save Anim", type a clip name, confirm the category, and click Save.
5. Frame the character in the thumbnail dialog that appears; it plays back the range and writes the clip's PNG and hover GIF.
6. To play a clip back: click its card in the gallery, choose a mode and the anchor/root-motion options in the Properties panel, then click "Load Animation" (or just double-click the card to load with the current Properties settings).

## Workflow
The window is a three-panel layout: Characters (left) lists live rig bindings by namespace plus any offline rig labels that still have saved clips; the center Anims panel is a searchable gallery of clip cards with a "+ Save Anim" button; the right Properties panel shows the selected clip's source rig, time range, frame count and FPS, and carries the load controls.

Properties panel controls, by their real names:
- Mode: Place, Insert, Replace (radio buttons). Place applies the clip's curves at the current time, overwriting any existing keys at the same frames. Insert shifts every key on the rig at or after the current time forward by the clip's length, then places the clip into the gap, for splicing a clip into the middle of existing animation. Replace cuts existing keys within the clip's time window on just the (control, attribute) pairs the clip touches, then places the clip, for overwriting a section without disturbing anything the clip doesn't touch.
- "Apply start anchor" / "Apply end anchor" checkboxes: each capture injects a synthetic key at the start and end frame of the range if a real key isn't already there; unchecking either drops that anchor key on load.
- "Apply root motion": drop channels whose Fabricator ctrl role is `world_ctrl` on load, so a clip's global travel isn't reapplied.
- "Load Animation", "Recapture Thumb + GIF" (re-renders a clip's PNG/GIF without changing the clip data), and "Delete" (also removes the clip's sibling .gif/.png).

Animation Studio shares its rig-binding resolution and cross-rig control addressing with Pose Studio (same `address.py`, same active-binding lookup), and it is hosted embedded inside Bridge's Library popover alongside Pose Studio as a tabbed pair. The save/load/list/delete logic in `app.py` has no Qt dependency, so it can also be called from headless/batch scripts, not only through the window.

Clip files live per source rig under an anim library root that is resolved from the current Maya project's config (falling back to a user-app-data folder when the scene isn't inside a recognized project), so a team pointed at the same project config reads and writes the same clip locations.

## Gotchas
- Saving requires a live rig binding in the scene; if none is found you get a "Build the rig first (Fabricator -> Build Modules)" error. The underlying message is worded as a Pose Studio error internally but applies identically here.
- A highlighted time-slider range of one frame or less is treated as no selection and the tool falls back to the full playback range instead. There is no single-frame clip; use Pose Studio for that.
- Saving with a time range where start >= end raises an error rather than silently doing nothing.
- Only the `animation` category currently exists (`CATEGORIES = ('animation',)`), so the Save dialog's category dropdown has just one entry today.
- Loading a clip does not retime: if the clip's saved FPS differs from the current scene's FPS, the tool warns and loads the raw keys unchanged, which can play back at the wrong speed.
- Cross-rig loading can fail to resolve some controls: if a clip was saved with an empty component "role" and the target rig has more than one component sharing that same (type, side), the match is ambiguous and that channel is skipped rather than guessed at.
- Locked attributes and attributes that don't exist on the target control are silently skipped on load. No warning, no channel applied for that one attribute.
- Canceling the thumbnail-framing dialog after a Save deletes the just-written `.anim.json` again, so a cancelled save leaves nothing behind.
- The gallery's hover GIF is played through a QMovie that keeps its file open; on Windows a card's GIF handle must be released before its file can be deleted, and the tool does this internally on delete. If a delete still errors, it is worth retrying after closing and reopening the window.
- The hover-GIF capture needs an actual Maya viewport (a modelPanel) to playblast from; passing anything else (outliner, graph editor, script editor) fails the capture with a warning and produces no GIF or PNG.
- The GIF's camera-follow behavior tracks a root joint's translation only; if that joint has no translate keys in the saved range (root motion often lives on a controller instead), the follow camera is a visible no-op and the tool warns about it.

## Troubleshooting
**"Animation Studio: name is empty."** The Save dialog's Name field was left blank. Type a clip name before clicking Save.

**"Animation Studio: no active rig binding" / "Build the rig first (Fabricator -> Build Modules)."** No `FAB_RigBinding` exists in the scene for Save or Load to target. Build the rig with Fabricator first, or make sure the intended rig is referenced/live in the scene.

**"Animation Studio: invalid time range [...] - start must be less than end (use Pose Studio for single-frame snapshots)."** The resolved time range collapsed to zero or negative length (often because the highlighted range was a single frame). Highlight a real multi-frame range on the time slider, or set the playback range, before saving.

**"Animation Studio: source fps X != scene fps Y; loading without retiming."** The clip was recorded at a different frame rate than the current scene. The clip loads at its original frame numbers with no conversion; expect it to play faster, slower, or the wrong length until the scene FPS matches or the clip is re-saved at the current rate.

**"Animation Studio: no channels resolved to the target rig."** None of the clip's addressed controls matched anything on the currently selected rig. Confirm the correct character is selected in the Characters panel and that it was built from a Fabricator blueprint compatible with the one the clip was recorded on.

**"Animation Studio: N ctrl(s) on the target rig did not match the clip."** Some but not all of the clip's channels resolved. Usually caused by an empty component "role" that is ambiguous on the target rig, or a component present on the source rig but missing/renamed on this one. The applied-channel count in the log line shows how much of the clip actually landed.

**Deleting a clip fails with a file-in-use / permission error on the .gif.** A hover-preview GIF can still be open in memory from the gallery. Close and reopen the Animation Studio window, then delete again.

**Recorded GIF looks static or the camera doesn't move with the character.** The root joint used for camera-follow has no translate keys in the clip's frame range; the rig's root motion is likely being driven by a controller higher in the hierarchy instead of the joint itself. This affects only the preview GIF, not the saved animation data.
