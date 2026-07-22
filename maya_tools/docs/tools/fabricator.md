---
title: Fabricator
summary: Modular networked Maya rigging tool, assemble rig components onto a skeleton, then build and edit your rig until it is animation ready.
category: rigging
gif: ../media/fabricator.gif
video:
---

# Fabricator

## What it does
Fabricator is FabricatorStudio's rigging tool inside Maya: you assemble an animation rig out of small reusable components (SimpleFK, AdvancedFK, FKChain, FKAim, SimpleIK, IKLeg, QuadLeg, IKArm, SplineFK, Ribbon, RibbonSpine, FollowJoint, and the always-present World) wired onto a skeleton, then click Build Rig to bake them into controls and constraints. Rig state lives in the Maya scene itself as network nodes joined by message connections (a `fab_registry` plus one `fab_<id>` node per component), not as name-string lookups or a project file, so renaming joints or controls doesn't break anything. One button (Build Rig / Edit Rig) toggles the whole rig between an editable Edit Mode and a built Animation Mode, and unbuilding captures each component's live state so rebuilding restores it.

## Quick start
0. **First time here? Take the tour.** The first time you open Fabricator it offers a guided walkthrough, and you can start it again at any point from **File > Take the Tour** (the folder icon in the brand bar). It runs in two parts: *The Layout* names the four regions of the window (Armature Tools, Components, Rig Outliner, Properties), and *First Rig Walkthrough* builds a rig with you: load a template, build it, drop back to Edit Mode. The hands-on stops wait for you to actually do the thing rather than clicking through, and each one says what it is waiting for. Skippable at any point, and it asks before abandoning a run.
1. Launch Fabricator (its entry point is `fs_window.FSWindow.show_window()`, which opens either a floating frameless window or, if a Fabricator dock is already open, docks into a Maya workspaceControl instead; right-click the brand bar to switch between the two).
2. File > New Rig: adopts the current scene's skeleton (or seeds a single root joint at the origin in an empty scene), prompts for a rig name, and drops a World component on the root automatically.
3. In the Canvas panel, select a joint (or a chain of joints), then double-click a component in the Palette panel (or drag it in) to attach it, e.g. SimpleFK on a spine joint, IKLeg on a leg chain.
4. Use the Skeleton Helpers Bar's "Aim Joints at Aimers" at least once before building: Fabricator's editable Armature stage drives joint orientation with live aim constraints, and Build Rig needs that orientation baked (jointOrient/rotateAxis zeroed) to pass its export-orientation check.
5. Click Build Rig. If pre-build checks find anything, a Build Issues dialog lists each one with a Fix button (or Build Anyway for skippable ones); once clean, the rig builds and the button relabels to Edit Rig.

## Workflow
- Two-window design: the same window class floats (frameless, remembers window size and splitter layout) or docks into a Bridge-hosted workspaceControl; right-click the brand bar's drag area for "Dock Fabricator" / "Undock Fabricator."
- Edit Mode (status bar shows "MODE: Edit") shows the three-panel splitter: Palette (component list plus a Templates section, factory-shipped and project blueprints, load by drag or double-click), Canvas (a tree of joints and their attached components, inline rename via double-click/F2, MMB drag-drop to reparent a branch, right-click for Mirror Limb / Duplicate Limb / Delete Limb on the clicked branch), and Properties (per-joint and per-component option editing, shown for whichever is selected).
- Animation Mode (status bar shows "MODE: Animation") is a stripped cockpit: rig name, the Edit Rig button, and the log only; the whole splitter (Palette/Canvas/Properties) hides.
- Skeleton Helpers Bar (above the rig-name row) is the Armature toolbox: Add Joint, Insert Joints Between, Aim Joints at Aimers, Rebuild All Aimers, Reset All Aimers, an aimer visibility (Show/Hide) toggle, Mirror Limb / Mirror Joints / Mirror Module, Duplicate Limb / Duplicate Joints, and a Live Mirror ("Symmetry") toggle that drives the opposite side live while posing.
- Armature Skeleton Symmetry is that Symmetry toggle: a live, selection-based mirror across the YZ plane for Armature ctrls and aimers while you edit the skeleton, grab either side and the opposite side follows, no rewiring needed to change which side drives. It replaced the retired Smart Joint Mirror tool's one-shot, freeze-on-disconnect workflow. It survives a build/unbuild cycle through the same preservation sandwich Mirror Limb, Mirror Joints, Duplicate, and Reparent already use: if it was on going in, it re-enables against the freshly rebuilt Armature ctrls once the op finishes, instead of leaving the toggle reading on with a dead network underneath. See the Armature doc for the full mechanism, including which Armature rebuilds (Aim Joints at Aimers, a limb drop, Unbuild) still turn it off and need a re-toggle.
- File menu (folder icon in the brand row): New Rig (Ctrl+N), Save Template (Ctrl+S, snapshots the current rig to `<name>.blueprint.yaml` in the active project's blueprint library), and Delete Rig (wipes rig_grp, guides/pivots groups, every Fabricator network node and FAB_RigBinding node; skeleton, geo, and skin clusters are preserved).
- Build Options (collapsible, collapsed by default) currently exposes one flag: "Reset Control Shapes," which rebuilds controls from the library default shape instead of restoring persisted CV edits; it auto-unchecks after each build.
- Build Rig runs a pre-build check pass (`build_checks.py`) first: duplicated joint connections from viewport duplication/mirroring, orphaned modules pointing at deleted joints, missing aimers, pre-registry ("legacy") rigs, and Fabricator-version drift since the rig was last built. Fixable issues get a one-click Fix; ambiguous ones (e.g. can't tell which duplicate joint is the original) block the build until resolved by hand.
- On a clean build, Fabricator bakes orientation from the aimers, validates the export orientation contract (rotate carries orient, jointOrient/rotateAxis zeroed, the UE5-style convention FollowJoint and IKLeg's axis handling also target), builds each component in dependency order, wires space switching, bakes per-rig selection sets as ordinary Maya objectSets under `fab_selection_sets` (read by Pose Studio instead of re-deriving from scene walks), organizes the scene, locks `*_offset`/`*_grp` transform channels, colors joints and outliner entries by component side, and stamps the rig with the current Fabricator version.
- Edit Rig (the same button, relabeled once built) unbuilds: captures each component's live state onto its network node, deletes `rig_grp`, restores the skeleton to the bind pose captured at build time, and rebuilds the Armature (aimers restored from a registry-side snapshot), landing you back in Edit Mode with nothing lost.
- FollowJoint exists specifically to wire UE5 engine reference joints (`ik_foot_*`, `ik_hand_*`, `ik_hand_gun`) so the FBX animation export carries them as keyed engine refs; it has no control of its own, just a parent/scale constraint driven by the Properties panel's Follow Target dropdown.
- Fabricator sits between Skeleton IO (imports/exports the joint hierarchy as portable JSON in the same UE5 orientation convention Fabricator's export-contract check enforces) and Skin IO in the pipeline; `bootstrap_from_skeleton()` can seed a starter blueprint directly from a Skeleton IO JSON export.

## Gotchas
- Save Template requires Edit Mode. Saving while any component is built raises `RuntimeError: Save Template: rig is built. Unbuild (Edit Rig) before saving a template.` A built rig's captured bind-pose snapshot is only a correct world-position stand-in for the root joint; saving it as a template would reload into a broken child-joint hierarchy.
- Save Limb is not offered on the rig root, by design. The root is the World component's own first joint, so saving from it would sweep every limb in the rig plus World into one "fragment" - that is a template, not a limb. Use File > Save Template for the whole rig, and right-click a limb's own first joint (clavicle, thigh, pelvis) to save just that limb. Right-clicking a limb's first joint saves that joint and its own subtree only; sibling branches hanging off the same parent are never included.
- Duplicating joints in the viewport (or using Maya's own mirror-joint) silently extends the source component's joint connections onto the duplicate, because Maya's duplicate/mirror propagates outgoing `.message` connections. The pre-build check reports this as "Duplicated joints on N module(s)" and can auto-repair only when every original joint name is still resolvable; if an original was also renamed or deleted, it's flagged for manual fix in the Node Editor.
- Deleting a joint in the viewport that a component depends on leaves an orphaned module. Build Rig blocks with "N module(s) reference deleted joints" until the module is removed or the joint restored.
- Deleting an aimer curve in the viewport (rather than through Fabricator) leaves its offset node behind with no visible aim arrow. Build Rig reports "Missing N aimer(s)"; the fix recreates them from saved state, falling back to geometric detection of the joint's current aim direction.
- Mirror Module/Mirror Limb do not mirror per-instance space-switch targets by design (Adrian's call, 2026-07-05); the mirrored component only inherits the contract's default spaces. Re-add custom space targets in Properties after mirroring.
- A pre-registry ("legacy") rig, meaning no Fabricator registry, or a registry whose root joint no longer resolves, can't use Armature-era features (orientation-contract validation, aimer snapshots, version stamping). Never use File > New Rig to fix this; it deletes the authored modules. The pre-build check's "Adopt registry" fix wires up the registry without touching modules instead.
- Fabricator-version drift between when a rig was last built and the currently installed Fabricator is a warning only, never a blocker; a successful build re-stamps the rig at the current version.
- The rig-name field is read-only by default (click Edit to unlock it for one rename) and can't be renamed at all once the rig is built; unbuild first.

## Troubleshooting
**Build Rig button is disabled.** No Fabricator registry is loaded in the scene, or the current mode doesn't allow it. Run File > New Rig or load a template from the Palette's Templates section first.

**"No fab_registry in the scene. Load a blueprint first."** `build_modules()` (and several other operations) require a live registry node. Run File > New Rig or load a blueprint before building.

**"Rig cannot be built, blueprint issues: ..."** The scene-snapshot blueprint failed structural validation (e.g. a component references a joint that isn't in the skeleton block). Fix the listed issues and rebuild.

**"Rig cannot be built, fix these issues first: Duplicate short name ..."** Two or more transforms in the scene share the same short name, which breaks anything that resolves nodes by name. Rename the duplicates and rebuild.

**"Rig cannot be built, export orientation contract violations (rotate carries orient, jointOrient=0, rotateAxis=0)."** One or more joints still carry non-zero `jointOrient`/`rotateAxis` after the pre-build bake, usually because an aimer was never run or was deleted. Run "Aim Joints at Aimers," then build again.

**"N joint(s) have no aimer curve (deleted in the viewport?)" (`MissingAimersError`).** Run "Aim Joints at Aimers" or "Rebuild All Aimers" to recreate them, or choose "Build Anyway" in the Build Issues dialog to skip them and keep their current orientation.

**Mirror Module/Mirror Limb fails with "joint ... has no side token; cannot derive R counterpart."** The joint name doesn't carry a side token Fabricator's side-token flipper recognizes. Rename with a proper side token, or hand-author the opposite-side component.

**Mirror Module/Mirror Limb fails with "R joint ... does not exist."** The mirrored-side joint hasn't been created yet. Run Mirror Joints first, then Mirror Module/Mirror Limb.

**Mirror Module/Mirror Limb fails with "component id ... already exists."** A component already occupies the target R-side joints. Remove it first, unless its joints were deleted, in which case Fabricator auto-cleans the orphan and re-mirrors.

**Save Template fails with "rig is built. Unbuild (Edit Rig) before saving a template."** Click Edit Rig to unbuild, then Save Template.

**Save Limb fails with "these components extend outside the selected subtree."** A component under the anchor joint owns a joint outside that anchor's descendant subtree, so the limb can't be cleanly extracted. Restructure so every component under the anchor stays inside the subtree being saved.
