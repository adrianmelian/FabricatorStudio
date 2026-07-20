---
title: Skin IO
summary: Saves skin weights to a portable JSON file and loads them back, in place on identical topology or transferred by closest point onto a rebuilt mesh.
category: skinning
gif: ../media/skin-io.gif
video:
---

# Skin IO

## What it does
Skin IO saves a mesh's skin weights, vertex positions, topology, and influence list to a JSON file, then loads that JSON back onto a mesh either in Direct mode (same vertex count, 1:1 write) or Transfer mode (rebuilds the saved mesh in-scene at its original world-space points and uses Maya's `copySkinWeights` to project weights onto a different mesh by closest point, ray cast, or closest component). It handles one mesh or several at once: saving multiple meshes combines them internally, saves the combined result, then deletes the temporary combine so the originals are left bound and untouched. A load can also be scoped to a specific set of selected vertices instead of the whole mesh, for patching a problem area without redoing everything. It ships both as quick popover actions on a fixed "temp skin" file and as a full window ("Skin IO") with independent Save and Load tabs for the complete option set.

## Quick start
1. On the Bridge toolbar, open the Skin popover (glyph SKN) and click "Skin IO Window..." (or run the Skin IO entry from the Maya menu) to open the Skin IO window.
2. On the SAVE tab, select your skinned mesh in the viewport and click Use Selected to fill the Mesh field (select several meshes to save them together).
3. Confirm or edit the Output File path — it defaults to `Data/<mesh-name>_skin.json` next to the current scene — then click Save Skin Weights.
4. Switch to the LOAD tab, select the target mesh, click Use Selected, and set Skin File to the JSON you just saved.
5. Pick a Mode: "Direct (same topology)" for a like-for-like reload onto the same mesh, or "Transfer (closest point)" if the mesh has changed. Click Load Skin Weights.

## Workflow
Skin IO's main job in the Maya-to-Unreal pipeline is carrying weights across a mesh rebuild. When a model gets retopologized, gets new UVs, or picks up extra loops, the rig does not need to be reskinned from scratch: save the old mesh's weights, then Transfer-load them onto the new mesh. This mirrors what Skeleton IO does for joint hierarchies, and both feed the same "portable JSON" habit that keeps a model update routine instead of an emergency, ahead of the Rig & Animation Exporter shipping FBX to Unreal.

Two entry points share the same underlying engine (`skin_io_app`):
- **Bridge Skin popover** (glyph SKN, `SkinPopover`): "Save Temp Skin" and "Transfer Temp Skin" write to and read from a single fixed file (`am_skin_temp.json` in the Maya user app directory) for a fast copy-paste-style workflow, plus a "Skin IO Window..." button that opens the full window. The same popover also carries Disconnect/Reconnect All Skins and Combine/Separate Selected, which are separate tools (`combine_skinned.py`) rather than part of Skin IO itself.
- **Skin IO window** (also reachable from the Maya main menu as "Skin IO", "Save Temp Skin", "Load Temp Skin - Direct", and "Load Temp Skin - Transfer"): a tabbed dialog.
  - **SAVE tab**: Mesh field + Use Selected, Output File field + Browse..., Save Skin Weights.
  - **LOAD tab**: Target Mesh field + Use Selected, Skin File field + Browse..., a Mode combo ("Transfer (closest point)" / "Direct (same topology)"), a Transfer Options group (Surface Association: closestPoint / rayCast / closestComponent; Influence Match 1° and 2°: name / label / closestJoint / oneToOne), a Create missing influences checkbox, a scope label ("// scope: whole mesh" or "// scope: N vert(s) on <mesh>"), and Load Skin Weights.
  - A collapsible LOG section at the bottom of the window reports each save/load result.

Vertex-scoped loads: selecting vertices (instead of a whole mesh) before clicking Use Selected on the LOAD tab scopes the next load to just those verts, on a single mesh only. Manually editing the Target Mesh field clears that scope back to whole-mesh.

## Gotchas
- Direct mode's compatibility check only compares vertex count, not vertex order. Two meshes with the same vertex count but a different build/export order will load without an error, but onto the wrong vertices — use Transfer whenever the mesh has actually been rebuilt, not just when you've confirmed the count matches.
- Saving multiple meshes combines them internally (`combine_skinned_meshes`) into a temporary mesh, saves it, then deletes it; the code deletes it in a `finally` block, but the temp mesh briefly exists mid-save.
- Direct mode is rejected outright for more than one target mesh ("use Transfer for multi-mesh loads"), and a vertex-scoped load is rejected for more than one target mesh too.
- Influence matching falls back from exact full-path to basename (namespace-tolerant) when a saved joint's full path isn't found; if the scene has two joints sharing the same short name, only the first one encountered is used, silently.
- Vertex-scoped Transfer mode still runs Maya's `copySkinWeights` across the entire mesh under the hood (there is no native partial mode), then restores the untouched vertices from a snapshot taken beforehand. That restore assumes `copySkinWeights` never adds or reorders influences on the destination skinCluster — true in practice, per an explicit comment in the code, but not something Maya's API guarantees.
- "Create missing influences" is off by default. With it off, a load whose saved joints are missing from the target scene just skips those influences with no warning. With it on, missing joints are recreated at the saved world matrix as placeholders — a decomposed TRS approximation, not a verified bind-accurate joint.
- Only mesh-type shapes are recognized when resolving a name typed or selected into a Mesh/Target Mesh field; a transform with no mesh shape (or a non-mesh shape) resolves to nothing and the action fails.
- Reconstructing the saved mesh for Transfer mode, and reading mesh topology for Save, both require the `maya.api.OpenMaya` module; the code has an explicit fallback path that raises rather than silently degrading if that API is unavailable.

## Troubleshooting
**"No mesh shape found on: `<name>`"** — the Mesh or Target Mesh field doesn't resolve to a mesh transform or shape. Check for typos, or that the field actually names a polygon mesh (not a group, locator, or NURBS surface).

**"No skinCluster found for: `<shape>`"** (on Save) — the mesh isn't bound yet. Skin it first, then save.

**"N mesh(es) have no skinCluster — see Script Editor for the list, selection updated to highlight them."** (multi-mesh Save) — one or more of the selected meshes has no skinCluster; the tool selects the offenders for you so you can bind or remove them before retrying.

**"Direct mode requires identical vertex count/order. Saved: X Target: Y"** — the target's topology doesn't match the saved mesh. Switch Mode to Transfer instead.

**"vert_indices is empty — nothing to load."** — a vertex-scoped load fired with no vertices captured. Reselect vertices on the mesh and click Use Selected again on the LOAD tab.

**"Direct (subset) mode: N vertex index/indices out of range (saved=X, target=Y)"** — the selected vertex numbers don't exist in the saved file, the target mesh, or both. Recheck the selection or use Transfer mode instead.

**"Transfer (subset) mode: N vert index/indices out of range on target (Y verts)"** — same issue as above, encountered during a vertex-scoped Transfer load.

**"No matching influences found to bind the target/reconstructed source/destination mesh."** — none of the saved joint names exist anywhere in the current scene. Either rename the scene joints to match, or check Create missing influences to have the tool recreate them at their saved position.

**"Direct mode requires a single target — use Transfer for multi-mesh loads."** — Direct was chosen with more than one target mesh selected. Switch to Transfer, or load one target at a time.

**"Vert-scoped load is single-mesh only — select verts on one mesh."** — a vertex-scoped load was attempted with more than one target mesh in the field.

**"Vert selection spans N meshes — select verts on a single mesh."** — the LOAD tab's Use Selected found vertex selections belonging to more than one mesh. Select vertices on one mesh only.

**"Mesh and output path are required." / "Target mesh and skin file are required."** — one of the required fields on the SAVE or LOAD tab is empty.

**"Select at least one mesh first." / "Select at least one mesh (or vertices on a mesh) first."** — Use Selected was clicked with nothing (or nothing valid) selected in the viewport.

**"OpenMaya API required for topology export. Please use Maya 2016+ with maya.api.OpenMaya."** — the `maya.api.OpenMaya` module couldn't be imported. This should not happen on a normal Bridge-supported Maya install; if it does, the Maya Python environment is missing the API module.
