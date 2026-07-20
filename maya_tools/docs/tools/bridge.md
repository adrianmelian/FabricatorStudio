---
title: Bridge (Toolbar)
summary: A dockable Maya strip that puts FabricatorStudio's scene, build, rigging, skinning, and export tools two clicks away, with a read-only AI bridge panel built in.
category: framework
gif: ../media/bridge.gif
video:
---

# Bridge

## What it does
Bridge is the FabricatorStudio framework toolbar: a single strip that docks to the top or bottom of Maya's viewport (or floats) and exposes the everyday tools as compact icon buttons, with the bigger tools opening as full-size popover panels instead of separate windows. It covers scene I/O, object creation, constraints, selection helpers, mirroring, skeleton and skin I/O, the inline renamer, and the Fabricator and CtrlEditor tools, plus tabbed exporters and pose/animation libraries. A Settings panel controls dock placement, drag-to-reorder layout, and per-tool show/hide, and a Connect AI panel starts a read-only MCP bridge so an external AI client can inspect the scene.

## Quick start
1. From Maya's FabricatorStudio menu, show the toolbar (the same entry hides it again once it's up). On first run it docks along the bottom of the viewport; if that placement isn't available it falls back to a floating window.
2. Set your active project with the PRJ chooser at the left edge of the strip.
3. Hover the CR (Create) button and click **Joint** for a quick, grounded first action: it creates a joint as a child of your last-selected joint (or at the origin if nothing is selected). Every strip action runs inside one undo chunk, so Ctrl+Z cleanly reverses it.
4. Open the SCN (Scene) popover and use **Save Scene** to save.
5. Open the SET (gear) popover to move the strip (Dock: Top / Bottom / Floating), drag-reorder tools (Customize Layout), or hide ones you don't use (Show / Hide Tools).

## Workflow
The strip is laid out in zones: a brandmark button (Bridge/About) on the left wall, a Project group (project chooser, inline renamer, Scene, Export), a Build group (Create, Constraints, Selection, Mirror, Snap A→B), a Rig group (Skeleton, Skin, the Insert Joints and Offset/Spread sliders, Fabricator, CtrlEditor), a Skin group (add/remove influence, Smoosh, copy/paste/average weights, paint-on-select toggle, Library), and a Settings group (Connect AI, Settings) on the right wall.

Two trigger behaviors matter when using it: most option panels (Create, Constraints, Selection, Mirror, Skeleton, Skin, Scene) open on **hover** (about 350ms); the panels that are full GUI tools in their own right - the inline Renamer's mode menu, Export, CtrlEditor, Pose & Anim Studios, and Fabricator's own popover - open on **click** only, so they don't pop up uninvited. A hover-opened panel is provisional and closes if you click outside it; it becomes committed (stays open until you hit its own close button) once you interact with it directly - focusing a field, dragging it, or pressing its owner button to open it.

This is where the toolbar meets the rest of the Maya-to-Unreal pipeline:
- **Scene (SCN)**: Save/Save As/Open, Load From Current Dir, Reload Scene, and Open Rig↔Source to jump between a rig scene and its source sibling file.
- **Export (EXP)**: a tabbed popover hosting the full MODEL exporter and ANIM exporter tools (click-trigger) - the handoff point toward the engine side.
- **Fabricator (FAB)**: hovering (when a rig is present) shows a single Build/Unbuild button that flips after each press; the button itself only lights up, and the popover only appears at all, when the scene has a live, local (non-referenced) rig network. Clicking the button opens the full Fabricator tool window.
- **Skeleton (SKL) / Skin (SKN)**: quick Save/Load to a temp slot, plus a link into the full Skeleton IO / Skin IO windows for the complete options.
- **CtrlEditor (COM)**: the entire CtrlEditor tool embedded in the popover, not a sampler - shape list, build, edit/mirror/combine, color, save.
- **Insert Joints (INS) / Offset-Spread (OFF)**: expanding sliders you drag to jog a live value; Offset also has an options panel (hover or right-click) for axis (X/Y/Z) and Offset-vs-Spread mode.
- **Connect AI**: Start/Stop a local MCP bridge, an autostart-with-Maya checkbox, and a per-client (Claude Code / Claude Desktop / Cursor) config snippet you copy into your AI client's config. The panel is explicitly labeled read-only: the bridge lets a connected AI see the scene, never edit it.

## Gotchas
- Left/right side docking is retired. Any stale left/right dock-mode preference is silently normalized to bottom on the next launch - if the toolbar was docked to a side on an older build, it will come back on the bottom instead.
- Drag-to-reorder (Customize Layout) only reorders items **within their own group**; you cannot drag a tool into a different zone.
- Hiding the toolbar turns off its scene-change listeners entirely (zero background cost while hidden); anything that depends on live scene state, like the Fabricator lit indicator, only resyncs when you show the toolbar again.
- The Fabricator popover's Build/Unbuild button only appears for a live, local rig network - it is deliberately suppressed for a non-Fabricator scene or a referenced (animation) scene, so don't expect it to show up there.
- Popover content (Library, CtrlEditor, and similar) is rebuilt fresh every time you open it - there is no caching, by design, so if you swap in your own thumbnails or assets on disk, restarting the tool (not just re-opening the popover) is what makes them visible if a stale in-memory instance is holding old state.
- A manually-started AI bridge (Start pressed, autostart unchecked) is meant to survive an internal toolbar relaunch, such as switching dock modes - it should not die just because you moved the toolbar.

## Troubleshooting
**Toolbar doesn't come back after restarting Maya.** It only auto-restores if it was left visible when Maya last closed. If you hid it (Settings → Hide Toolbar, or the FabricatorStudio menu toggle), it stays hidden until you show it again from the FabricatorStudio menu.

**Toolbar opens floating instead of docked top/bottom.** The dock attempt couldn't find Maya's expected central layout (it locates the dock point by walking up from the playback slider) and fell back to floating, with a warning in the Script Editor. Try Settings → Dock: Top or Dock: Bottom again; if your Maya layout is nonstandard it may keep falling back.

**A button or tool is missing from the strip.** A manifest entry that fails to resolve (for example, a broken command path) is dropped from the strip with a Script Editor warning naming the item's id - check there first. Otherwise check Settings → Show / Hide Tools in case it was unchecked.

**Hovering over Export, Library, CtrlEditor, the Renamer's mode menu, or Fabricator's popover does nothing.** These are click-trigger by design (they're full tools, not quick option panels); click the button instead of hovering.

**A popover closed as soon as I looked away.** Hover-opened panels are provisional and close on an outside click. Click the toolbar button directly (rather than hovering), or interact with a field/drag the panel, to commit it so it stays open until you close it explicitly.

**AI bridge won't start ("port may be in use").** The bridge start failed; another process may already be bound to that port. Check the Connect AI popover's status pill (hover it for the raw error) and try Stop, then Start again, or a different port via your client config.

**Reordering only moves a tool a short distance, not to a different group.** That's expected in the current version - Customize Layout is within-group only.

**All my toolbar settings (dock mode, layout, hidden tools) reset to defaults.** The prefs file (`fabricator_toolbar_prefs.json`, in the shared Maya prefs folder) failed to parse - a torn write or a hand-edit broke its JSON - and the loader fell back to defaults rather than raising. Check that file if this happens repeatedly.
