---
title: Skinning Utilities
summary: Smooth, copy, paste, and average skin weights on selected vertices, plus combine or separate skinned meshes without losing weights.
category: skinning
gif: ../media/skinning-utilities.gif
video:
status: internal
---

> **INTERNAL (2026-07-12).** Removed from the public palette; the toolbar's Skin row carries these operations now. This page stays for internal reference; the public site excludes it.

# Skinning Utilities

## What it does
Skinning Utilities is four small, independent tools grouped in one area of Bridge for everyday weight cleanup, short of a full Skin IO save/load round trip: skinSmoosh, a neighbor-average weight smoother that runs as a native undoable Maya command (`cmds.skinSmoosh`); a vertex weight clipboard (copy weights from one vertex or averaged from several, paste onto other vertices, or average a selection in place); and Combine/Separate, which unite several skinned meshes into one or split a multi-shell mesh back apart while preserving exact per-vertex weights. All four operate on the current skinCluster found upstream of your selection via `listHistory`, so a mesh must already be bound before any of them will do anything.

## Quick start
1. Select the vertices you want to fix (faces and edges convert to vertices automatically for skinSmoosh; the others expect vertex selections).
2. To smooth a rough patch: in Bridge's Skin row, hover the **SM** (Smoosh) icon to open its popover, drag **Strength** and **Iterations**, then click **Apply Smoosh**. Clicking the **SM** icon directly (no hover) reapplies smoosh at whatever strength was last used.
3. To copy a known-good weighting: select one clean vertex and click **Copy Weights (CW)**, then select the target vertex or vertices and click **Paste Weights (PW)**.
4. To even out a patch of vertices without a reference vertex: select them and click **Average Weights (AVG)**.
5. To combine or split skinned meshes, open the **Skin (SKN)** popover in Bridge's Rig zone and use **Combine Selected** / **Separate Selected**.

## Workflow
Bridge's toolbar splits this across two spots. The Rig zone's **Skin (SKN)** popover (`SkinPopover`) holds the mesh-level operations: Save Temp Skin, Transfer Temp Skin, Disconnect/Reconnect All Skins, **Combine Selected**, **Separate Selected**, and a link to the full Skin IO window. The right-center **Skin** row holds the per-vertex tools: an Influence cluster (**+** add / **-** remove influence), the **Smoosh** popover, and a Weights cluster (**CW** copy / **PW** paste / **AVG** average). The same operations are duplicated as text entries under Bridge's menu, in a **Skinning** submenu: Skin IO, Save/Load Temp Skin, Combine Skinned Meshes, Separate Skinned Mesh, Skin Smoosh, Copy/Paste/Average Skin Weights.

Copy Weights and Paste Weights write and read a single clipboard file at `cmds.internalVar(userAppDir=True)/am_weight_clipboard.json` (per Maya user, not per-scene or per-project). A single copied vertex stores its exact weights; copying from several vertices stores the per-influence average instead. Only influences above a `1e-6` threshold are kept in the stored data, and paste rebuilds the weight vector against the target skinCluster's full influence list, filling zero for anything not in the clipboard.

Combine and Separate matter most going into export: the pipeline's bind-pose convention reserves frame 0 for the bind pose (the same convention the FBX exporter uses), and Combine explicitly jumps to frame 0 before duplicating and rebinding so the new skinCluster's bind matrices come from the joints at rest. Combine is also used internally by Skin IO's multi-mesh save path (`save_skin_from_meshes`), which combines the selected meshes behind the scenes, saves the combined weights, then deletes the combined mesh and restores your selection - so Combine's exact-weight guarantee is load-bearing for more than just the toolbar button.

Note there is a second, separate averaging implementation in the same folder, `average_skin_weights.py` (`average_skin_weights()`, `average_weights_on_current_selection()`). It is not wired to any Bridge button or menu item as of this read; the **AVG** button and the **Average Skin Weights** menu entry both call `weight_clipboard.average_weights()` instead. The standalone module is only reachable by importing it and calling it from the Script Editor.

## Gotchas
- Copy/Paste/Average Weights only ever process the **first mesh** found in your selection if the selected vertices span more than one mesh - verts on any other mesh are silently dropped, with no warning (documented in `weight_clipboard._dag_and_comp`'s own docstring). skinSmoosh does not have this limitation: it groups selected vertices by their owning shape and smooths each mesh's skinCluster independently in one command call.
- Paste Weights does not add missing influences for you. If the target skinCluster is missing an influence the clipboard expects, it selects the matching joint(s) by name in the scene, prints a `cmds.warning`, and applies nothing at all until you add them (via Skin > Edit Influences > Add Influence, or Bridge's own **+** Add Influence button, which adds at weight 0 and locked with no dialog) and paste again.
- skinSmoosh is a pure-Python MPx plugin that must be `loadPlugin`-ed once before `cmds.skinSmoosh()` will resolve. Driving it from Bridge's Smoosh popover/icon or the menu's Skin Smoosh entry loads it automatically; calling `cmds.skinSmoosh()` cold from the Script Editor without ever going through one of those paths will not.
- skinSmoosh's per-vertex smoothing is a Jacobi-style iterative blend toward the CURRENT neighbor average, re-snapshotted after every pass. One iteration on an already-smooth vertex barely moves it; more iterations are needed for a stronger effect, not just a higher strength value.
- Combine Skinned Meshes requires every selected mesh to already have a skinCluster; unlike skinSmoosh's per-mesh skip-and-continue, Combine aborts the entire operation if even one selected mesh is unbound.
- Combine assumes frame 0 is the bind pose and jumps there before rebinding. Combining while frame 0 is not actually the rig's rest pose in a given scene will bake incorrect bind matrices into the new skinCluster.
- Separate Skinned Mesh unbinds the ORIGINAL mesh as part of the process (to snap it to bind pose so vertex positions can be matched back to their original weights). The original transform is only kept if `polySeparate` happens to hand it back as one of the resulting pieces; otherwise it is deleted outright. There is no "keep the original intact" option.
- Separate does not support meshes with coincident vertices - two vertices sharing the exact same world-space position will collide in the position-matching step, and the second one gets zero weights instead of the correct value (a documented limitation in the code, not something the tool detects or warns about).
- Weight_clipboard.py and combine_skinned.py raise plain `ValueError`/`RuntimeError`, not the toolbar's own `ToolbarUserError`. Run from a Bridge button, these show up as a `[FS toolbar] <label>: <message>` warning plus a full traceback in the Script Editor, not the softer in-viewport heads-up the toolbar uses for expected, user-fixable mistakes elsewhere. The message text after the prefix is still the actionable part.

## Troubleshooting
**"Select one or more mesh vertices (or convert-able components)."** skinSmoosh: nothing usable was selected, or your selection didn't convert to any vertices. Select vertices, faces, or edges on a skinned mesh and try again.

**"No skinCluster on <mesh>. Skipping."** skinSmoosh: the mesh owning some of your selected vertices has no skinCluster upstream. That mesh is skipped; any other selected, skinned mesh is still processed.

**"skinSmoosh: No change on <mesh> (neighbors identical or fully locked)."** Not an error - the smoosh ran, but the neighbor weights were already identical to the selection's (or the influences involved are locked), so nothing moved.

**"Nothing selected. Select at least one mesh vertex."** Copy/Paste/Average Weights: run the command with nothing selected. Select vertices first.

**"Selection contains no mesh vertices."** Your selection didn't convert to any mesh vertices (e.g. you had a non-mesh object selected).

**"No skinCluster found on '<mesh>'."** Copy/Paste/Average Weights: the target mesh has no skinCluster in its history. Bind it first.

**"No weight clipboard at '<path>'. Run Record Weights first."** Paste Weights: nothing has been copied yet in this Maya session/user profile. Run Copy Weights on a source vertex first.

**"paste_weights: N influence(s) not bound to target mesh: [...]. Missing joint(s) selected - go to bind pose and run Skin > Edit Influences > Add Influence."** The target skinCluster is missing one or more influences the clipboard needs. The matching joints are selected for you; add them as influences (native menu or Bridge's **+** button), then paste again.

**"Clipboard weights sum to zero - clipboard may be corrupt."** Paste Weights: the stored JSON has no usable weight data. Re-run Copy Weights from a valid source vertex.

**"Select at least two skinned meshes to combine."** Combine Skinned Meshes: fewer than two skinned mesh transforms were selected.

**"No skinCluster on '<mesh>'."** Combine or Separate: one of the selected meshes isn't bound. Bind it, or remove it from the selection.

**"Select exactly one skinned mesh to separate."** Separate Skinned Mesh: the selection wasn't exactly one skinned mesh transform.

**"'<mesh>' has only 1 shell - nothing to separate."** The mesh has a single connected shell; Separate needs at least two shells to produce more than one piece.

**A separated piece has zero weights everywhere.** Likely coincident vertices in the source mesh - two verts at the same world position confuse the position-based match-back, and the losing vertex gets a zero weight row instead of its real one.
