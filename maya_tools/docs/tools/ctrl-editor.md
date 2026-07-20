---
title: CtrlEditor
summary: Build, swap, recolor, and mirror NURBS control-curve shapes from a shared library; the same shapes drive every Fabricator component's ctrl-shape options.
category: rigging
gif: ../media/curve-o-matic.gif
video:
---

# CtrlEditor

## What it does

CtrlEditor is the control-curve shape library for rigging: a shape list backed by JSON
files (degree, CV positions, and knot/periodic data for each NURBS curve), plus a set of
operations for using them: build a new ctrl curve from a library shape, swap an existing
ctrl's shape in place without breaking its connections, mirror a side ctrl's curve onto its
opposite, combine several curve transforms into one multi-shape ctrl, recolor ctrl curves
from a shared palette, and save your own curve(s) back into the library under a new name.

It is also the shape source for Fabricator itself: every component's shape-related build
option (ctrl_shape, ik_ctrl_shape, pv_shape, switch_ctrl_shape, and similar) is a dropdown
populated straight from CtrlEditor's library, and component build code calls into it
directly to construct each ctrl curve. Add a shape to the library and it shows up as a
choice on every component that has a shape option.

## Quick start

1. Open CtrlEditor from any of: the Bridge toolbar's COM button (click to pop the full
   tool out inline), the `CtrlEditor` shelf button, or the Rigging menu's `CtrlEditor`
   entry (the shelf and menu commands open it as a standalone window).
2. In the shape list, click a shape (for example `circle`, `cube`, or `sphere`).
3. Click **Build at Origin** to create a new ctrl curve named `<shape>_ctrl` at the world
   origin. To restyle something that already exists instead: select one or more curve
   ctrls in the viewport first, then click **Swap** to replace their shapes with the one
   selected in the list. Swap preserves the ctrl's transform and every incoming/outgoing
   connection (constraints, message links, keyframes, anim layers).
4. Optional: with curve ctrl(s) selected, pick a color from the **Color** dropdown and
   click **Apply Color** to set the override color on their shapes.

## Workflow

CtrlEditor shows up in four places, all backed by the same shape library:

- **Bridge toolbar**: a popover button (glyph `COM`, click to open) hosts the full tool
  inline, shape list through the Save button.
- **Shelf / Rigging menu**: `CtrlEditor` opens it as a standalone floating window.
- **Fabricator's Animation-Mode Tools panel**: the panel that replaces the Properties
  panel once a rig is built hosts a trimmed instance, just the shape list and **Swap**, for
  quick ctrl restyling during animation without leaving the panel.

Buttons and what they do, by their on-screen names:

- **Swap**: replaces the nurbsCurve shape node(s) on each selected ctrl with the chosen
  library shape. Transform identity and all connections are untouched.
- **Build at Origin**: builds the selected library shape as a new curve at world origin,
  named `<shape>_ctrl`. It does not snap to the current viewport selection (a
  selection-snapping build exists in the underlying API but is not wired to this button).
- **Edit**: selects the CVs of every nurbsCurve shape under the selected ctrl(s), limited
  to those ctrls (child ctrls are not pulled in).
- **Mirror**: replaces the opposite-side ctrl's curve with a YZ world-mirrored copy of the
  selected ctrl's curve. Requires the selected ctrl's name to carry a recognized side token
  (L/R, lf/rt, left/right); flips only the first matching token. The mirror math goes
  through each ctrl's world matrix rather than a naive local-axis flip, so ctrls with
  mirror-aimed local frames (clavicles, fingers) come back right-side up instead of rotated
  180 degrees around the aim axis. For a Fabricator ctrl (one carrying a `fab_owner`
  connection), the mirrored CVs are also written to the owning component's persisted data,
  so the change survives an unbuild/rebuild.
- **Combine**: merges 2+ selected curve transforms into one. The last-selected transform is
  the target; every other transform has its world TRS frozen into its CVs, its shape nodes
  re-parented under the target, and is then deleted. Used to build multi-shape ctrls (the
  shipped `sphere`, `sphere_arrow`, `cog`, and `arrow_double` shapes are built this way).
- **Color dropdown + Apply Color**: sets the override RGB color on the selected curve(s)'
  shapes from an 8-name palette shared with Fabricator (yellow, blue, red, green, orange,
  white, cyan, pink). For a Fabricator ctrl, the owning component's `ctrl_color` option is
  also updated, so the color survives a rebuild.
- **Save**: prompts for a name, then saves the selected transform(s)' curve data into
  `curve_data/<name>.json`, overwriting if that name already exists. Selecting multiple
  transforms bakes each one's world matrix into its CVs and combines them into a single
  new library entry (so three arrow curves arranged as an XYZ aimer save as one composed
  shape). This button is styled with Mindmeld's orange/ember accent to stand out from the
  other actions.
- Right-click a shape in the list for **Rename...** and **Delete** (delete asks for
  confirmation and cannot be undone; it removes the JSON file from disk).

Selecting a Fabricator rig ctrl in the viewport auto-highlights the matching shape in the
list, read from the ctrl's owning component (via its `fab_owner` connection and the
component's shape option), so you can see which library shape a given ctrl currently uses
without opening its options.

The shapes here are Maya-side control-curve display only. They are not part of the joint
hierarchy that ships to Unreal.

## Gotchas

- **Build at Origin ignores viewport selection.** It always places the new ctrl at world
  origin; there is no button for "build at selection" even though the headless API has a
  `build_shape_at_selection` function for scripts.
- **Delete is permanent.** It removes the shape's JSON file from `curve_data/` with no undo;
  the confirmation dialog says so explicitly.
- **Save silently overwrites.** Saving under a name that already exists in the library
  replaces that file with no warning beyond the normal save confirmation.
- **Combine deletes its sources.** Every transform except the last-selected one is deleted
  once its shape nodes are moved to the target; this cannot be undone as a single Maya undo
  step across all the intermediate operations if something goes wrong mid-way.
- **Mirror needs both a side token and an existing opposite ctrl.** Center ctrls (no side
  token) are rejected outright, and if the flipped-name ctrl does not exist yet in the scene,
  Mirror raises rather than creating it.
- **Multi-shape library entries exist.** Some shapes (`sphere`, `sphere_arrow`, `cog`,
  `arrow_double`) are more than one nurbsCurve shape node under a single transform; Swap and
  Build handle this correctly, but hand-editing a saved JSON file should preserve the
  `shapes` list structure.
- **The embedded views are intentionally trimmed.** Inside Fabricator's Animation-Mode Tools
  panel, only the shape list and Swap are shown (Build at Origin, Save, and the log are
  hidden); the Bridge toolbar popover shows everything except the log.

## Troubleshooting

**"Shape not found: '<name>'"** when building, swapping, or deleting: no `<name>.json`
exists in the shape library. It may have been renamed or deleted; check the current shape
list for the name that's actually there.

**"Control not found: '<ctrl>'"** on Swap: the target ctrl name no longer exists in the
scene (renamed or deleted after selection).

**"Select a shape first."** on Build at Origin or Swap: no item is highlighted in the shape
list.

**"Select one or more controls (transforms with nurbsCurve shapes) in Maya."** on Swap, or
**"Select one or more control curves (transforms with nurbsCurve shapes) in the viewport
first."** on Apply Color: nothing is selected, or the selection contains no transforms with
a nurbsCurve shape.

**"Edit Curve: selection has no nurbsCurve shapes."**: the selected transform(s) do not
carry any NURBS curve shapes to select CVs on.

**"Select one or more transforms with nurbsCurve shapes."** on Save: nothing useful was
selected when Save was clicked.

**"No nurbsCurve shapes found under <transform>."**: Save (or any internal serialize call)
was pointed at a transform, such as a joint or mesh, that has no curve shape under it.

**Combine does nothing / errors with "need 2+ transforms" or "Select 2+ transforms..."**:
fewer than two curve transforms were selected when Combine was clicked.

**Combine errors with "<transform> has no nurbsCurve shapes"**: one of the selected
transforms in a Combine has no curve shape to merge into the target.

**Mirror errors with "...has no recognized side token (L/R, lf/rt, left/right)."**: the
selected ctrl's name does not carry a side token CtrlEditor recognizes, so it cannot
determine which ctrl is the mirror target. Rename with a side token, or skip Mirror for
center ctrls (they have no opposite side).

**Mirror errors with "target ctrl <ctrl> does not exist. Mirror the joints + components
first, or hand-create the opposite ctrl."**: the opposite-side ctrl is not in the scene yet.

**Mirror succeeds but the shape reverts after an unbuild/rebuild**: a Maya warning
("persisted CV write failed for <ctrl>...") means the mirrored curve could not be written
back to the owning component's persisted data. The visual change in the current scene is
still correct; it just will not survive an unbuild/rebuild until the underlying write
issue is resolved.

**"Rename Shape" fails with "Shape already exists: '<new name>'"**: the new name is
already used by another shape in the library; pick a different name.
