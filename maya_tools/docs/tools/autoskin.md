---
title: AutoSkin
summary: One-button ML skinning. Select your bind joints and mesh, press Bind Skin, and a local GPU engine predicts the skin weights and writes them straight onto a fresh skinCluster. Optionally generates the skeleton too. One Ctrl+Z restores everything.
category: skinning
gif: ../media/autoskin.gif
video:
---

# AutoSkin

## What it does
AutoSkin is FabricatorStudio's one-button ML skinning tool. Select your bind joints and your mesh, press Bind Skin, and a local GPU engine predicts the skin weights and writes them straight onto a fresh skinCluster. Tick Generate Joints and it builds the skeleton for you as well, from the mesh alone. The tool is deliberately one button and one checkbox; everything else in the window exists to report honestly on a run that takes about two minutes. The whole operation is a single undo chunk, so one Ctrl+Z puts the scene back exactly as it was, including a rebind's original skin, influences, weights, and locks.

The weights come home as data, not as a rig: the engine returns numbers keyed by vertex index and bone name, and AutoSkin writes them onto the skinCluster through the Maya API (about 100x faster than a command bind, and with no proxy-FBX import dialog). Your own geometry is never selected, moved, combined, or edited during a run - AutoSkin works on a duplicate it deletes afterward.

## Quick start
1. On first run, install the engine. AutoSkin gates itself until the local ML backend and a working GPU are ready, and the window names what is missing.
2. Select your bind joints and your mesh, the same way you would smooth-bind, in any order.
3. Press **Bind Skin**. The stage label ticks through Exporting mesh, Generating weights, Matching to your skeleton, and Applying weights; **Cancel** stays live the whole time.
4. To skin a mesh that has no skeleton yet, tick **Generate Joints**, select only the mesh, and press Bind Skin: AutoSkin builds a skeleton and skins to it in one pass.

## How it works
AutoSkin runs a five-step pipeline with the GPU engine isolated in a subprocess:
1. **Validate** - checks the selection, GPU and backend health, that the mesh sits on the skeleton, that bind-joint names are unique, and that a rebind target is at its bind pose. It fails early and names the fix.
2. **Export** - duplicates the mesh, deletes construction history, and writes a temporary FBX with the joints, plus a probe of Maya's own vertex positions. Your nodes are untouched.
3. **Generate** - the engine predicts weights (and, in Generate Joints mode, a skeleton) on the GPU, roughly two minutes.
4. **Match** - the backend re-proves, on your actual mesh, that the FBX round-trip preserved vertex order, and recovers the Blender-to-Maya transform from the probe. It refuses to return weights it cannot vouch for.
5. **Apply** - AutoSkin creates a fresh skinCluster (max 4 influences, normalized) and writes the weights through the API, then deletes the temp files.

Locked influences are honored: a locked influence means "I weighted this by hand," so its weights are read before the rebind and carried across untouched, with the new prediction normalized into whatever weight is left over. Multiple meshes are bound one at a time (one engine run each); overlapping meshes are always separated so their weights cannot bleed. Generated joints arrive in Maya's coordinates with Maya's own joint orientation - positions and parentage only.

## Options
- **Bind Skin** - runs the whole gesture on the current selection.
- **Generate Joints** (checkbox) - build a new skeleton from the mesh instead of using selected joints. Select only the mesh; one mesh at a time.
- **Cancel** - stops a running generation cleanly. The window stays responsive throughout (the ~2-minute engine runs in a subprocess; Maya never freezes).

## Gotchas
- AutoSkin needs a working local GPU and the engine installed. The window stays gated until the backend reports ready, and it names what is missing.
- A run takes about two minutes per mesh on the GPU. Multi-mesh selections loop, so time scales with mesh count.
- Rebinding a posed rig is refused: AutoSkin reads the rest shape and a rebind would bake the current pose in as the new bind pose. Put the rig back at bind pose first.
- The mesh must sit on the skeleton. A mesh offset from its joints is rejected, because the engine would otherwise prune joints it cannot explain and return a mangled skeleton (an 80-joint character came back as 78 in testing).
- Bind joints must have unique leaf names; the engine keys weights by bone name, so two joints called "wrist" are ambiguous.
- Generate Joints builds one skeleton for one mesh, with no joints selected. Maya may rename a generated joint to keep names unique; AutoSkin remaps the weights to follow and warns.
- One Ctrl+Z restores a bind or rebind exactly, but only because AutoSkin always writes into a skinCluster it created this run. The API weight write itself is not on Maya's undo queue; this is what makes it safe.
- Vertices the engine had nothing to say about keep the closest-joint weights of the initial bind (normalized), rather than collapsing to the origin. AutoSkin warns with a count.

## Troubleshooting
**The Bind Skin button is disabled / "AutoSkin isn't ready."** The backend health check failed: the ML engine is not installed, or a GPU is not available. The window states the specific reason; run the install step.

**"... does not overlap the selected joints."** The mesh and skeleton are in different places. Check you selected the right skeleton and that the mesh is not offset from it.

**"... is already skinned and its skeleton is not at the bind pose."** Put the rig back at bind pose before regenerating the weights.

**"These bind joints share a name."** Rename the duplicated joints so each has a unique leaf name.

**Some vertices look stuck at a rough closest-joint weight.** Those vertices got no generated weight and kept the fallback bind; AutoSkin warns with a count. Paint them in, or check the mesh in that area.

**It renamed my generated joints.** Maya would not grant a name that already existed, so AutoSkin remapped the weights to the names Maya gave and warned. The skin is intact.
