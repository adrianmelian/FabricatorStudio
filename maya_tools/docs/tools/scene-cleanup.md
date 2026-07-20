---
title: Scene Cleanup
summary: Flags duplicate short names and mesh artifacts (non-manifold, lamina faces) across a scene, and can auto-fix the mesh issues; the same checks Fabricator runs before every build.
category: framework
gif: ../media/scene-cleanup.gif
video:
---

# Scene Cleanup

## What it does
Scene Cleanup is a shared validator module rather than a standalone tool window. It covers two checks: name-collision detection (`find_duplicate_short_names` / `find_duplicate_transforms`), which flags any two transforms in the scene that share a short name, and mesh-artifact detection and repair (`select_mesh_artifacts` / `fix_mesh_artifacts`), which finds non-manifold edges, non-manifold vertices, and lamina faces via `polyInfo`, and can auto-fix those plus zero-area faces, invalid components, and shared UV shells via Maya's `polyCleanupArgList`. Today it is reached through the legacy Scene Cleanup shelf button's right-click popover on the `fsModel` and `fsAnim` shelves, and it also runs automatically inside Fabricator's pre-build validation and Connect Your AI's read-only scene report. There is no dedicated Scene Cleanup window and it is not currently on the Bridge toolbar; a full UI with per-asset configs is a planned future addition, per the module's own docstrings.

## Quick start
1. Open the `fsModel` or `fsAnim` shelf tab (both load at Maya startup) and find the **Scene Cleanup** button.
2. Left-clicking the button directly does nothing useful yet: it just warns "AM Scene Cleanup Tool not yet implemented." The real checks live in the right-click popover.
3. To check mesh topology: select one or more meshes, right-click the Scene Cleanup button, and choose **Select Mesh Artifacts (Non-Manifold / Lamina / Zero-Area)**. Your selection is replaced with the problem components found; press F to frame them in the viewport.
4. To fix what you just found: with the same mesh(es) still selected, right-click again and choose **Fix Mesh Artifacts (Non-Manifold / Lamina / Zero-Area)**. This rewrites topology and deletes construction history, but the whole pass is wrapped in one undo chunk, so a single Ctrl+Z reverts it.
5. Duplicate short names have no shelf button of their own: they are checked automatically the next time you build a rig in Fabricator, or you can run `scene_cleanup_app.find_duplicate_transforms()` yourself from the Script Editor.

## Workflow
Scene Cleanup follows a "buffet" pattern documented in the FabricatorStudio repo: tools that need a specific check call the relevant helper function directly out of `scene_cleanup_app.py` rather than routing through one shared UI. The two current consumers are Fabricator's build step and Connect Your AI's scene report:

- **Fabricator's pre-build gate.** When you click Build Modules, Fabricator first runs its own 12-rule blueprint validator, then calls `find_duplicate_transforms()` as a separate scene-wide gate (this check needs a live Maya scene, so it cannot run at the pure-Python blueprint-validation stage). If it finds collisions, the build stops with "Rig cannot be built - fix these issues first," listing every duplicate short name and the full DAG path of each node that shares it.
- **Connect Your AI's scene report.** The bridge's `scene_report` op calls `find_duplicate_transforms()` and `select_mesh_artifacts()` as read-only diagnostics your connected AI client can request when troubleshooting a scene. Results are capped (200 mesh transforms scanned for artifacts, duplicate-name entries capped in the report) so a badly-imported scene with hundreds of collisions doesn't blow past response size limits. The op saves and restores your live selection around the call, since `select_mesh_artifacts` normally leaves the problem components selected.

Because both consumers call the underlying functions directly, there's no single "Scene Cleanup panel" to open for a full scene audit today; the shelf popover only exposes the mesh-artifact half.

## Gotchas
- The Scene Cleanup shelf button's main click is a stub. It only warns that the tool isn't implemented; you have to right-click for the two working commands.
- **Fix Mesh Artifacts is destructive.** It rewrites mesh topology (non-manifold cleanup, lamina removal, zero-area face and invalid-component cleanup, shared-UV splitting) and deletes construction history on every mesh it touches. It's one undo chunk, but there's no dry run beyond running Select first.
- **Select Mesh Artifacts replaces your selection with no restore.** Unlike most FabricatorStudio tools, it does not save and reapply your prior selection afterward; that's by design, so you can immediately inspect the flagged components; press F to frame them.
- Select's diagnostic scope is narrower than Fix's repair scope: Select only reports non-manifold edges/verts and lamina faces (the things `polyInfo` can query cleanly). It cannot detect zero-area faces, invalid components, or shared-UV issues, even though Fix silently repairs all of those too. If you want to preview what Fix would touch beyond what Select shows, duplicate the mesh first and run Fix on the duplicate.
- An earlier version of Select tried to reuse `polyCleanupArgList` with its `selectOnly` flag to preview problems non-destructively, but Maya 2025's `polyCleanupArgList` ran the destructive cleanup regardless of that flag, collapsing meshes to a single triangle. The shipped Select path uses `polyInfo` instead specifically to avoid this; the fix is noted directly in the code as the reason the two functions diverge in scope.
- `find_duplicate_short_names` expands every input name via `cmds.ls(node, long=True)` before comparing. Passing a short name like `rig_grp` matches and reports every node in the scene that shares that short name, not just one; pass a full DAG path if you only want to check a specific node.
- `find_duplicate_transforms` walks every `transform` node in the scene, which implicitly covers joints (joints are transforms in Maya's type hierarchy) but does not check shape nodes directly.

## Troubleshooting
**"AM Scene Cleanup Tool not yet implemented" warning.** Expected behavior from left-clicking the Scene Cleanup shelf button. Right-click it instead for Select Mesh Artifacts / Fix Mesh Artifacts.

**`RuntimeError: fix_mesh_artifacts: no mesh in selection. Select the mesh(es) you want to clean up.`** Nothing was selected, or the selection contained no mesh transforms, when you ran Fix Mesh Artifacts. Select the mesh(es) first.

**`RuntimeError: select_mesh_artifacts: no mesh in selection. Select the mesh(es) you want to inspect.`** Same cause as above, raised from the Select path instead.

**"Rig cannot be built - fix these issues first: Duplicate short name '...' (N matches): ..."** Fabricator's build-time scene gate found two or more transforms sharing a short name. Rename the listed nodes (the error lists every full DAG path that collides) to unique names, then rebuild.

**Nothing is selected after Select Mesh Artifacts, and there's no warning.** The scanned mesh(es) are clean of non-manifold edges, non-manifold vertices, and lamina faces. Select Mesh Artifacts clears your selection when it finds no problem components, rather than leaving your prior selection in place.

**A mesh collapses to a single triangle when you only meant to inspect it.** If you see this, you are likely running a stale cached copy of the module from before the Maya 2025 `polyCleanupArgList selectOnly` bug was worked around. Reload `scene_cleanup_app` (the shelf popover commands already force an `importlib.reload`) and confirm you're calling `select_mesh_artifacts`, not `fix_mesh_artifacts`.
