---
title: Rig & Animation Exporter
summary: Exports static and skeletal meshes (FBX, or a skeletal USD delivery for Armature and Unreal) and batches of animation clips from Maya to the Engine, using a per-rig binding network to decide exactly what ships.
category: export
gif: ../media/exporter.gif
video:
---

# Rig & Animation Exporter

## What it does
The Rig & Animation Exporter is two linked tools sharing one FBX pipeline: a model exporter (window title **Rig Exporter**, code name `Rig Exporter`) for static and skeletal meshes, and a batch clip exporter (`Anim Exporter`) for animation. Both read a per-project `export_mapping.json` to resolve output folders, filename prefixes, and FBX presets from the scene's file path, and both persist their entry/clip lists on network nodes in the scene so a multi-asset scene keeps a remembered export list across sessions. Skeletal export writes an `FAB_RigBinding` contract (root joint, the full export joint chain, and the rig's nulls/controls groups) that the animation exporter later reads to know exactly which joints to bake and which rig groups to strip; a rig with no binding, or a stale one, blocks animation export rather than shipping something wrong. Animation clips export in a separate headless `mayapy` subprocess per clip, so baking and reference-import never touch or corrupt the artist's open scene.

## Quick start
1. Save the scene somewhere under a project registered in `<maya>/fabricator_project_configs/<project>/export_mapping.json` (or leave it unsaved and type/browse a directory manually into Output Dir later).
2. For a single mesh or rig: select mesh(es) for a static export, or a root joint plus its skinned mesh(es) for a skeletal export, then press **Alt+Shift+E** (or the shelf's **FBX Export** button). This dispatches straight to `export_one_button()`.
3. For a repeatable, multi-asset export list: open the Bridge toolbar's **Export** popover (glyph `EXP`), **MODEL** tab, or right-click **FBX Export** on the shelf and pick "Open Exporter UI". Click **+ Add Entry from Selection**, adjust the Prefix/Name per row, then **Export Checked** / **Export Selected** / **Export All**.
4. For animation: set the Maya playback range to the clip you want, open the **Export** popover's **ANIM** tab (or shelf **Anim Export**, or menu Export > Anim Exporter), click **+ Add Clip from Range**. The clip's Rig auto-fills from your selection or the scene's only rig binding; open the row's detail button to set it manually if needed. Then **Export Checked** / **Export Selected** / **Export All**; you'll be prompted to save if the scene is dirty, since the actual bake runs in a separate `mayapy` process against the file on disk.

## Workflow
Project configuration lives outside the repo, in `<maya folder>/fabricator_project_configs/<project>/export_mapping.json` (seeded from a packaged Unreal 5 template on first run). Each config's `path_patterns` (and `anim_path_patterns` for gameplay vs cinematic clips) are matched by regex against the scene's path relative to `project_root`, and drive the resolved output folder, the `prefixes` block (`static_mesh`, `skeletal_mesh`), `anim_prefix`, and the `fbx_presets` block (separate FBX exporter settings for `static_mesh`, `skeletal_mesh`, and `animation`).

Bridge hosts both exporters as one popover ("Export", `EXP` glyph) with **MODEL** and **ANIM** tabs, each an embedded, full copy of the standalone windows:

- **Rig Exporter** (model exporter, MODEL tab or shelf "FBX Export" right-click > "Open Exporter UI"): an Output Dir field (auto-filled from the project config, editable), a "Fix Paths" banner that appears after a Save As / scene rename, a **+ Add Entry from Selection** / **Delete Selected** / **Save Scene** row, an entries table (enabled checkbox, PREFIX, NAME, TYPE, SELECTION summary, and a "..." detail button), and **Export Checked** / **Export Selected** / **Export All** buttons. The detail dialog per entry exposes Prefix, Name, State (enabled), Dest Override (with Browse), the connected node list, "Update Selection", and "Delete Entry".
- **Anim Exporter** (anim exporter, ANIM tab or shelf "Anim Export"): the same Output Dir / Fix Paths pattern, a **+ Add Clip from Range** / **Duplicate Selected** / **Delete Selected** row, a clip table (enabled, PREFIX, RIG, CLIP NAME, START, END, detail), a gear menu with "Cleanup Stale Bindings", and **Export Checked** / **Export Selected** / **Export All**, each driving an "EXPORTING CLIP i / n" progress dialog. The per-clip detail dialog sets Clip Name, Output Kind (Gameplay/Cinematic), the assigned Rig ("Set Rig" from a live-binding dropdown, "Clear", or "Update Rig from Selection"), the frame range (with "= cur" buttons and "Use Timeline Range"), and a Dest Override.

Skeletal (SK_) export scrubs to frame 0 — the default T-pose (Adrian authors raw range-of-motion anims starting on frame 1, so frame 0 stays the rest pose), so a rig file carrying a ROM still exports its rest. On a Fabricator (KS) rig the export runs in a throwaway `mayapy` subprocess (`skeletal_pipeline` / `skeletal_export_runner`): it opens the saved scene, disconnects skins, breaks the Armature and orients the joints to the game contract, strips to joints + mesh, resets every skinCluster's bind to the final pose (`reset_all_binds_to_pose` — a full disconnect+reconnect, so a cluster left live can't ride a stale bind into the FBX as the twist-joint candy-wrapper), writes the FBX, and exits — the live scene is never touched. On a hand-built/legacy rig it does a small in-scene surgery (delete constraints + cut keys on the joint chain) inside an undo chunk and undoes it after, leaving the scene as it was. Every path writes/refreshes the rig's `FAB_RigBinding` node.

**USD format** (Export Options > Format > "USD (Armature, Unreal)"): skeletal entries write one `SK_<name>.usd` UsdSkel delivery file instead of an FBX — mesh, skeleton, weights (top-4, normalized), and UsdPreviewSurface materials with textures copied to a `textures/` folder beside the file. Skinning is optional for USD: a model plus skeleton with no skinCluster exports cleanly (with a note in the log) so the character can be skinned in Armature — the FBX path still requires a bound mesh. One file serves both destinations: Armature ingests it (a Fabricator rig also embeds its module layout and blueprint in the file, so the rig arrives in Armature as named modules with working steppers, not a flat pile of joints), and Unreal's USD importer reads the same file. Static entries always export FBX regardless of the option. The Engine Up Axis option does not apply to USD: the stage carries `upAxis` metadata and consumers convert on import, so the root bone ships at identity always. USD export always runs in the throwaway `mayapy` subprocess (even for non-Fabricator rigs) and verifies its own output before shipping it — the skeleton, weights, embeds and root frame are re-read from the written bytes, and a file failing any check is deleted, never shipped. A rig whose root frame is not identity (Fabricator-native rigs carry a -90 X on the root) is normalized world-preservingly during export; the character does not move.

Animation export runs each clip through a `mayapy` subprocess (`anim_export_runner.py`) in this fixed order: transfer root motion if enabled (currently a no-op, see Gotchas) -> bake R+T+S on the binding's joints -> import all scene references -> delete the rig's `nulls_grp`/`controls_grp` -> unparent the root joint to world -> FBX-export the resolved joints. The animation FBX preset (`fbx_presets.animation` in the project config, or the built-in default when the scene is outside any project) is applied inside that subprocess.

A project config is never required to export. Out-of-project scenes (a Desktop test file, a scratch rig) export through the Output Dir field: type or Browse a folder once and it persists with the scene, so every later export from that scene reuses it. Both exporters resolve the destination in the same order: the Output Dir field for this run, then a per-entry/per-clip Dest Override, then the scene's saved Output Dir, then the project config's auto path. Without a config, FBX settings come from built-in game-ready defaults (Y-up) and prefixes fall back to SM_/SK_.

Hotkey: **Alt+Shift+E**, `export_one_button`: "FBX export selected (skeletal if joint selected, else static)". Shelf: **FBX Export** (click = one-button export; right-click popup has "Export Selection (one-button)" and "Open Exporter UI") and **Anim Export** (click opens `Anim Exporter`; right-click popup has "Open Animation Exporter" and "Export All Animation Clips"). The Maya "Export" menu mirrors the same four entries: "Export Selection (one-button FBX)", "FBX Exporter UI", "Anim Exporter", "Export All Animation Clips".

## Gotchas
- Skeletal (KS) and animation export both run in a `mayapy` subprocess that opens the on-disk file, so both require the scene saved: the KS exporter force-saves before spawning (Adrian: saving on export is good hygiene), the anim exporter prompts Save/Cancel. Static export and the legacy (non-KS) skeletal path still run in-memory and only warn on unsaved changes.
- A Fabricator rig with no `fab_registry` node blocks skeletal export with "Open the KS window and load the blueprint, then retry the export"; this can happen if the scene has rig geometry/joints but the Fabricator blueprint was never (re)loaded this session.
- Legacy/non-Fabricator skeletal export mutates the scene (deletes constraints, cuts keys, unparents the root) and relies on a single `cmds.undo()` after a closed undo chunk to restore it. If that undo call fails, the tool only logs a warning ("scene may need manual reset") and the scene is left altered.
- Animation clip export blocks if the clip has no rig assigned, if the assigned `FAB_RigBinding` has an empty `export_joints[]` ("Re-run SK_ export"), or if any bound joint is missing from the scene; all three mean the binding is stale or was never written, and re-running the skeletal export on that rig refreshes it.
- Root motion (`root_motion_enabled` / `root_motion_axes` / per-rig source ctrl) exists in the clip data model and is checked by validation, but there is no control for it anywhere in the `Anim Exporter` clip detail dialog, and the runner's actual transfer step is hard-disabled by a code-level flag (`_ROOT_MOTION_TRANSFER_ENABLED = False`) pending a fix to its FK/bind-rotation compensation math. Until that changes, root motion has to be authored by hand (animate the world ctrl directly) or triggered manually from the Script Editor via `anim_root_motion.transfer_root_motion()`.
- Two enabled clips that would resolve to the same output filename block export with a filename-collision error rather than silently overwriting one another.
- Exporter entries created before the Prefix field existed load with a blank prefix; there is no auto-migration, so the artist has to type the prefix into that row once.
- "Fix Paths" (shown when the scene has been Saved As / copied under a new name) only renames entries/clips and clears destination overrides. It does not repair broken node or joint connections; those still show in orange/red with "(missing)" and need "Update Selection" / "Update Rig from Selection" in the detail dialog.
- `mayapy.exe` is located by checking `MAYA_LOCATION`, then the folder of the current Python interpreter, then a hardcoded fallback path for Maya 2025. A non-standard Maya install without `MAYA_LOCATION` set can fail to find it.
- Each clip's export subprocess has a fixed 10-minute timeout; an unusually heavy bake or rig can exceed it and fail the whole clip.

## Troubleshooting
**"Nothing selected."** - The one-button hotkey/shelf button was pressed with an empty selection. Select mesh(es) for a static export, or a root joint plus its skinned mesh(es) for a skeletal export.

**"Scene must be saved before exporting."** - Save the scene first (Save Scene in Rig Exporter, or File > Save); the exporter needs a saved path to resolve the project config and destination folder.

**"Scene is outside any project and has no saved Output Dir."** - A project is only the auto-path convenience; exports work without one. Type or Browse a directory into Output Dir (it saves with the scene and is reused on every export from it), or register the project in Project Setup for automatic paths. Out-of-project exports use built-in default FBX presets (Y-up, game-ready settings) and the default SM_/SK_ prefixes.

**"Skeletal export needs at least one mesh selected alongside the joint(s)."** - Add the bound mesh(es) to the selection before exporting a joint through the one-button path.

**"Skeletal export: no mesh in selection has a skinCluster bound."** - The selected mesh has no skinCluster in its history (FBX only). Bind it first, export it as a static mesh, or switch Export Options > Format to USD — the USD path exports model + skeleton without skinning so you can bind in Armature.

**"Fabricator rig in scene but no fab_registry found."** - Open the Fabricator window and load the blueprint so the registry node exists in the scene, then re-run the export.

**Entry or clip row shown in orange/red with "(missing)".** - The connected node or joint no longer exists (renamed or deleted outside the tool). Open the row's detail dialog and use "Update Selection" (model exporter) or "Update Rig from Selection" (anim exporter) to repoint it.

**"Entries/Clips reference '\<old\>' but scene is '\<new\>'" banner.** - The scene was Save As'd or copied under a new name. Click "Fix Paths" to rename entries/clips to the new stem and clear stale destination overrides, then save.

**"...has empty export_joints[]. Re-run SK_ export."** - The rig's `FAB_RigBinding` contract is stale or was never written. Run the skeletal (SK_) export on that rig once to refresh it, then retry the animation export.

**"...rig binding nodes deleted."** - The `FAB_RigBinding` node itself was deleted from the scene. Re-run the skeletal export to recreate it, or reassign the clip to a live rig via "Update Rig from Selection".

**"mayapy export failed (returncode=...)" with STDOUT/STDERR in the message.** - Read the `[runner]` lines in the error for the failing pipeline step (bake, reference import, group deletion, FBX write). The interactive scene itself is untouched; only the subprocess failed.

**"mayapy reported success but the export is missing."** - The subprocess exited cleanly but the target file never appeared on disk. Check that the Output Dir's drive exists and is writable.

**"USD export failed verification (nothing shipped): ..."** - The written file failed one of the export's own checks (each listed by name in the message: weights, joint names, root frame, embeds). The file was deleted rather than shipped. Fix the named problem — most commonly duplicate joint short names across branches (rename one) — and re-export.

**"USD export: the root joint sits at [...], not the origin."** - The contract requires the root joint at the world origin. Move the rig to the origin (or zero the root's translation) and re-export.

**A clip's export prompt is answered "Cancel".** - That clip (and any queued after it in the same batch) is skipped and logged as cancelled, not treated as a failure.
