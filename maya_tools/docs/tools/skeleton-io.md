---
title: Skeleton IO
summary: Save a joint hierarchy to portable JSON and rebuild it in any scene, with options to strip scale offsets and control how rotation splits between rotate and joint orient.
category: rigging
gif: ../media/skeleton-io.gif
video:
---

# Skeleton IO

## What it does
Skeleton IO exports the joint chain under a chosen root to a JSON file, and rebuilds that same hierarchy from JSON later, in any scene. Export captures each joint's world matrix plus its transform, orient, rotate order, draw and display settings, rotation limits, channel locks, and (optionally) custom attributes. Because positions are read and reapplied through world matrices, the rebuild is accurate regardless of which rotate order the original joints used, and any upstream scale can be stripped out on import so the rebuilt chain comes back clean. On import you also choose whether local rotation gets consolidated into joint orient, or left split the way it was exported.

## Quick start
1. Select the joint that is the root of the hierarchy you want to save (or type its name directly).
2. Open the tool: Bridge toolbar, Rig zone, Skeleton popover, "Skeleton IO Window..." (or Maya's Skeleton menu, Skeleton IO; or the fsAnim shelf tab, Skeleton IO).
3. On the EXPORT tab, click "Use Selected" to fill Root Joint from your selection, confirm the Output File path (it defaults to a Data folder next to the current scene once the scene is saved), and click "Export Skeleton".
4. To rebuild it, switch to the IMPORT tab (or open a fresh scene), Browse to the .json you just saved, and click "Import Skeleton". Leave "Remove scale offset" and "Zero joint scales" checked unless you have a specific reason to keep scale on the joints.

## Workflow
The tool lives in the Bridge toolbar's Rig zone under the Skeleton popover (glyph SKL), which offers two quick one-click actions plus the full window:
- "Save Temp Skeleton" exports from the selected joint's root to a fixed temp file in the Maya user app directory (`am_skeleton_temp.json`, not per-scene, not per-project).
- "Load Temp Skeleton" rebuilds from that same temp file.
- "Skeleton IO Window..." opens the full `Skeleton IO` window with explicit paths.

The same window is also reachable from Maya's own Skeleton menu (Skeleton IO) and from the fsAnim shelf tab.

The window is a two-tab dialog:
- EXPORT tab: Root Joint field with a "Use Selected" button, Output File field with Browse, an "Include user attributes" checkbox (checked by default), and the Export Skeleton button.
- IMPORT tab: Skeleton File field with Browse, an optional Namespace field, three checkboxes ("Remove scale offset", "Zero joint scales", both checked by default, and "Bake to joint orient (rotate → 0)", unchecked by default), and the Import Skeleton button.
- A collapsible LOG section at the bottom of both tabs reports success and error messages.

In the Maya-to-Unreal pipeline, Skeleton IO is a portability and recovery layer for the joint hierarchy itself, independent of skinning, rigging, or export. Save a chain before a risky edit, hand a skeleton to another artist or scene, or move a rig's joint positions onto an updated mesh without redoing placement by hand. Internally, the same version-2 JSON format is also read by Fabricator's blueprint bootstrap code (`bootstrap.from_skeleton_json`, called from `fs_app.bootstrap_from_skeleton`) to seed a starter Blueprint's skeleton block from a saved hierarchy. As read, that entry point is not yet wired to a button in the Fabricator window, so treat it as an internal integration point rather than a shipped user workflow (flagged below for Adrian to confirm).

## Gotchas
- Joint names must be unique within the exported hierarchy; the exporter and importer both look joints up by short name, and duplicates are not handled.
- Export requires an actual existing joint node as the root; anything else raises `Root must be an existing joint: '<name>'`.
- `preferredAngle` is written on export but is never set back on import, it is read into the JSON but not applied to the rebuilt joints, so IK preferred angles do not round-trip automatically.
- User attribute values round-trip (numeric, bool, enum, string, double3-style compounds), but connections, expressions, and driven keys on those attributes do not; only the static value is restored.
- The Namespace field on import is a plain string prefix concatenated onto each joint name (`createNode(name=namespace + jointName)`), not a real Maya namespace. Include the colon yourself (e.g. `charA:`) if you want colon-style namespacing.
- "Remove scale offset" (default on) strips scale/shear from every joint's world matrix on import; if the source hierarchy actually relied on non-uniform joint scale, that information is discarded unless you uncheck it.
- "Bake to joint orient" does the opposite of what its neighboring checkboxes suggest at a glance: turning it on moves local rotation into joint orient and zeroes rotate; leaving it off (the default) keeps whatever split between rotate and joint orient the source scene had.
- Import does not check the JSON's `"version"` field or otherwise validate the file. A hand-edited or unrelated JSON missing a top-level `"joints"` array fails with a raw `'joints'` KeyError rather than a clear message.
- The toolbar's "Save Temp Skeleton" / "Load Temp Skeleton" pair writes to one fixed file in the Maya user app directory. It is not per-scene or per-project, so saving from one scene and loading in another (or a teammate's machine sharing the same user profile) can load stale or unrelated data.

## Troubleshooting
**"Root must be an existing joint: '\<name\>'"** - The Root Joint field names something that either does not exist in the scene or is not a joint node. Select the actual joint and click "Use Selected", or fix the typed name.

**"Root joint and output path are required."** - Export Skeleton was clicked with an empty Root Joint or Output File field. Fill both; use Browse to pick a path or Use Selected to grab the root from the current selection.

**"Select a joint first."** - "Use Selected" was clicked with nothing selected, or the selection was not a joint.

**"File not found: \<path\>"** - Import Skeleton pointed at a path that does not exist on disk. Browse to a valid `.json` file.

**"Select a skeleton file first."** - Import Skeleton was clicked with an empty Skeleton File field.

**Bare `'joints'` error in the log** - The chosen file is not a Skeleton IO export (missing the top-level `joints` array), for example a JSON file from an unrelated tool. Point the Skeleton File field at a file actually produced by Skeleton IO's Export.

**"No temp skeleton found - run Save Temp first"** - "Load Temp Skeleton" was clicked in the Bridge Skeleton popover before "Save Temp Skeleton" had ever been run (the temp file does not exist yet).

**Imported joints look squashed, stretched, or carry unexpected scale values** - "Remove scale offset" was unchecked and the source hierarchy carried scale on its joints. Re-import with it checked (the default) to bake scale out of the world matrix.

**Rotation looks "wrong" or split differently than the source after import** - The "Bake to joint orient" checkbox changes how much of the local orientation lives in rotate versus joint orient. Toggle it to match what downstream tooling (skinning, animation, exporter) expects for that rig.

**Custom attribute values came back but connections, expressions, or driven keys did not** - Expected. Skeleton IO restores static values on user attributes only, never incoming connections.
