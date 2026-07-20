# Fabricator Toolset

One section per tool on the FabricatorStudio toolbar strip, grouped the same way the strip groups them.

## Brand

**Bridge** (top-left brand button): opens the About menu, a plain list of About, Tutorials, Help, Go to Website, and File a Bug entries. Several of these are placeholders today (the fabricator.studio site and in-app help content aren't live yet), wired but inert until that content ships.

## Project

**Project Chooser**: picks the active project, stored in the toolbar's own prefs file. The active project drives path resolution for exports and other project-scoped tools, which project config folder gets read (`fabricator_project_configs/<name>/` under your Maya preferences folder, outside the install tree so uninstalls and updates never touch it).

**Project Setup**: opens Mindmeld, the project config manager: create, edit, duplicate, delete, and validate project configs, and Apply & Activate one to the session. A config is two shareable files per project (session settings + export mapping, the "project truth", carrying no machine paths); where that project lives on THIS machine (Source Art Root, Content Root, optional library/blueprints overrides) is a separate per-machine binding, editable here or in the Settings window. Full page: `tools/project-setup.md`.

**Renamer**: Renamer's four modes built directly into the strip as an inline widget: Hash Rename (`#`-placeholder numbering), Search/Replace, Prefix/Suffix, and Select by Name (a wildcard-pattern selection helper rather than a rename mode). Reach for it whenever you need to batch-rename selected nodes, or select nodes by name pattern, without opening a separate window.

**Scene**: Save Scene, Save Scene As, Open Scene, Load From Current Dir (browse rooted at the current scene's folder), Reload Scene (prompts if there are unsaved changes), and Open Rig <-> Source (swap between a rig file and its paired source file). The everyday file-menu actions, one popover away instead of buried in Maya's File menu.

**Export**: a tabbed popover hosting the full exporters, MODEL (static and skeletal mesh FBX export) and ANIM (animation FBX export, run via a `mayapy` subprocess so mtoa's scene-read scriptJob can't corrupt the anim bake). Use this whenever a mesh or a clip needs to leave Maya.

## Build

**Create**: spawns an object at the selection's center, or at the origin if nothing is selected. Joint comes first and uses the smart joint create (child of the last-selected joint, so repeated presses chain a joint down a limb); the rest are ordinary poly primitives (Null, Locator, Cube, Sphere, Cylinder, Plane).

**Constraints**: Parent, Point, Orient, Scale, and Aim constrain the last-selected node to the driver(s) selected before it, plus a live joint Mirror shortcut and a generic Delete Constraints that removes every constraint on the selection and severs any active mirror links in one action.

**Selection**: Select Child Joints, Select Child Meshes, Select Influencing Joints (the joints actually driving the selected mesh's skinCluster), and Select Influenced Meshes (the meshes a selected joint's weights touch). Reach for these when you need to jump from geometry to skeleton or back without hunting in the Outliner.

**Mirror**: Mirror Joints (YZ, selected joints plus descendants), Mirror Skin Weights (from the first selected mesh), and Mirror Ctrl Curves (mirror a control's CV shape to its opposite-side counterpart).

**Snap A to B**: a single icon button that snaps every selected object except the last onto the last-selected object (position only). Select the object(s) to move first and the target last; the target itself never moves. Quicker than opening the Constraints popover for a one-off placement.

## Rig

**Skeleton**: Save Temp Skeleton (export from a selected joint's root into a scratch slot), Load Temp Skeleton (rebuild from that slot), and the full Skeleton IO window for real export/import options. Skeleton IO is the underlying joint-hierarchy JSON format; the "temp" pair is a fast round-trip for iterating on a chain without naming a file.

**Skin**: Save Temp Skin / Transfer Temp Skin (closest-point transfer, tolerant matching that catches near-identical topology), Disconnect All Skins / Reconnect All Skins (detach or rebind every skinned mesh in the scene at its current joint positions, the same operations the Armature lifecycle uses under the hood), Combine Selected / Separate Selected (for multi-shell skinned meshes), and the full Skin IO window for direct save/load/transfer control.

**Insert Joints**: an expanding slider (drag right to jog the live count) that inserts joints between two selected adjacent joints. Use it to add twist or bend joints into an existing chain without rebuilding it.

**Offset Joints**: an expanding slider that offsets or spreads selected joints along an axis, live as you drag; hover or right-click for the axis (X/Y/Z) and Offset-vs-Spread mode options.

**Fabricator**: opens the full Fabricator window, the modular rig builder described in `concepts.md`. Hovering the strip button instead pops a single contextual shortcut: a green Build button when the rig is unbuilt, or an orange Unbuild button when it's built, so a routine build or unbuild doesn't require opening the full window.

**CtrlEditor**: the complete control-curve shape library embedded directly in its popover: swap a ctrl's shape, build one at the origin, edit, mirror, or combine curves, recolor, and save new shapes to the library.

## Skin

**Add / Remove Influence**: adds the selected influence transform(s) to the selected skinned mesh at weight 0 and locked (no dialog, so it never disturbs existing weights), or removes a selected influence via Maya's native command. Use this when a mesh needs a new joint layered into its skinCluster, such as a twist or corrective joint added after the mesh was already bound.

**Smoosh**: a hover panel of strength and iteration sliders over the skinSmoosh relaxation plugin; clicking the strip button itself re-applies at the last-used strength.

**Copy / Paste / Average Weights**: the weight clipboard, for copying, pasting, or averaging skin weights across selected vertices.

**Paint on Select**: a toggle that automatically enters the Paint Skin Weights tool whenever a skinned mesh (or its joint) is selected; right-click for Open Tool Settings or Enter Paint Once.

**Pose & Anim Studios**: a tabbed popover hosting the full Pose Studio and Animation Studio, cross-rig pose and clip storage with thumbnail previews, built lazily so opening one tab doesn't pay the cost of loading both.

## Settings

**Connect AI**: sits on the settings side of the strip, next to Settings itself. Starts or stops a local, loopback-only bridge that lets your own AI assistant, Claude Code, Claude Desktop, Cursor, or any MCP client, inspect your Maya scene and Fabricator rig to help you troubleshoot. It is read-only by design: the bridge issues only inspection commands and never modifies your scene.

Click Start to bring the bridge up, pick your client from the dropdown, and click Copy to grab that client's config snippet. Paste it into that client's own MCP config and your AI can call the bridge's read-only inspection tools from there. Start with Maya auto-starts the bridge when Maya launches; it is off by default.

**Settings**: left-click opens the Settings window, everything "me on this machine": the Shared Configs Root pointer (local default, or point at a studio's shared configs folder; the `FABRICATOR_PROJECT_CONFIGS` env var outranks the pointer when set), per-project machine bindings (Source Art Root + Content Root + optional pose/anim/blueprints overrides, written only to `_user/<slug>.json`, so binding works even against a read-only shared configs root), and Maya Integration toggles (load the FabricatorStudio menu, shelves, hotkeys, and the Ctrl+Alt+RMB animation marking menu at startup; on builds immediately, off applies next start). Right-click keeps the quick toolbar options: Dock mode (Top / Bottom / Floating), Customize Layout (drag tools to reorder the strip, Esc to finish), Show/Hide Tools, and Hide Toolbar (re-shown from the FabricatorStudio menu). Full page: `tools/settings.md`.
