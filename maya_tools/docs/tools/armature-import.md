---
title: Armature Import
summary: Turn an Armature USD export into a Fabricator rig. The mesh, skeleton and skin weights come in from the file, the blueprint is built and loaded, and Build Rig is the next click. Import into an empty scene.
category: rigging
gif:
video:
---

# Armature Import

## What it does
Armature Import reads a USD export from Armature, the standalone rigging and skinning app, and stands up a Fabricator rig around it. The mesh, the skeleton and the skin weights come straight from the file; nothing is rebuilt and no weights are transferred. The tool then writes a Fabricator blueprint describing those joints, assigns components (World, FK, IK arms and legs), applies the authored aimers, and loads the blueprint so the scene is ready for Build Rig.

One import gives you the character standing in the scene, skinned, named, with modules assigned. Build Rig from there as you would with any Fabricator blueprint.

## Quick start
1. Open an empty scene.
2. Press the Armature button on the toolbar (or run the window from the Script Editor).
3. Browse to the `.usd` or `.usdc` file Armature exported.
4. Pick **Simple** or **Advanced** components.
5. Press **Import Rig**, then run **Build Rig** in Fabricator.

The blueprint is written beside the USD as `<Name>.blueprint.yaml`, so the import is repeatable from the file alone.

## Options
- **Simple** - FK spine and neck, IK arms and legs. Ships with every install.
- **Advanced** - ribbon spine, neck, arms and legs. Available when the Advanced Ribbon Modules pack is installed; otherwise the option is disabled and says why. Picking Advanced without the full pack quietly present falls back to the free set, with a note in the log.

## Known limits
- **Armature exports only.** The rig comes from a blueprint embedded in the USD by Armature. A USD from Blender, Tripo, or any other tool carries no blueprint and is refused by name. This is permanent and by design; without the blueprint there are no aimers, modules or regions to build from.
- **Import into an empty scene.** The import joins the blueprint to the scene by joint name. If a name already exists in the scene, the whole import stops before touching anything, and the message names the collision. Importing a second character into a populated scene is refused rather than half-done.
- **`.usdz` is not supported.** The zip container never carries the rig blueprint. Export `.usd` or `.usdc` from Armature.
- **Foot bank pivots are not used.** Armature exports four foot pivots per leg (heel, toe, inner bank, outer bank). IKLeg builds its reverse foot from heel and toe; the bank pair is carried in the file but unused, and the log says so per foot. Banking still works in the built rig through the heel ctrl's side axis.
- **Unskinned chains import as-is.** A chain that was added in Armature but not yet skinned arrives bound with no weights, deforming nothing. That is a normal work-in-progress state; the log notes it and the import carries on.
- **The rig is named after the character.** The export's own name fills the Rig Name field. Rename before building if you want something else.

## Troubleshooting
Every refusal names its reason. The ones you are most likely to meet:

**"This USD carries no Armature rig blueprint."** The file was exported before Armature embedded the blueprint, or it is not an Armature export at all. Re-export from a current Armature.

**"These names already exist in the scene."** The scene is not empty. Open a new scene, or rename the clashing nodes first.

**"This file was written by a newer Armature."** The export's blueprint format is newer than this FabricatorStudio build understands. Update FabricatorStudio.

**"The blueprint names N joint(s) the USD skeleton does not contain."** The file's two halves disagree, which means it is damaged. Re-export from Armature; if it recurs, file a bug with the export attached.

**The Advanced option is greyed out.** The Advanced Ribbon Modules pack is not installed in this build. Simple imports everything; the component set is the only difference.
