# Fabricator Concepts

Fabricator's mental model: what the scene actually holds, what a blueprint is for, and what the three build modes mean for what you can and cannot do right now.

## The scene is the truth, not the blueprint

A Fabricator blueprint (a `.blueprint.yaml` file) is a starting recipe, not a live document. Loading one reads the YAML once, uses it to spawn a registry node, one network node per component, the real skeleton joints, and the Armature, then discards the in-memory copy. From that moment forward the Maya scene, plus its network nodes, is the only source of truth. There is no hidden in-memory model, no dirty flag, no "save before this action" prompt. Maya's own scene-modified state covers `.ma` durability.

The blueprint YAML only comes back into the picture when the user explicitly runs Save Template: that snapshots the live scene (registry attrs, component network nodes, skeleton joints) back into a fresh YAML file. Renaming that file is the only way to rename a blueprint; the filename stem is read back as the canonical name on every load.

One consequence worth knowing: Save Template refuses to run while any component is built (`modules_built` mode). A built rig's joints can be posed, and saving would snapshot posed transforms as if they were bind, corrupting the skeleton on reload. Unbuild (Edit Rig) first, then save.

## The registry and the component network nodes

Everything Fabricator tracks in the scene lives on two kinds of network node, connected by `message` attributes rather than string names, so renaming a joint or a ctrl never breaks the bookkeeping:

- One `fab_registry` node per scene. Holds the blueprint name, its source path, schema version, description, origin, a `components` message-multi (every component node in the rig), and a `root_joint` message connection.
- One `fab_<component_id>` node per component instance. Holds `component_type`, a `joints` message-multi (which real joints this component drives), `parent_plug`, `side`, `options_json` (the component's authored options), `persisted_json` (state captured at the last Unbuild, for round-tripping tweaks like CV shapes), and an `is_built` flag.

Because these are message connections, not stored strings, Fabricator can always find "the component driving this joint" or "every component in this rig" even after a rename. The same rename-resilient pattern shows up in `FAB_RigBinding`, the cross-tool export contract (see the glossary).

## Three modes, and how Fabricator tells them apart

`detect_mode()` inspects the scene and returns exactly one of:

- `empty`: no registry, or a registry with no live joints under any of its components.
- `skeleton`: the registry exists and at least one of its components has a real joint connected, but nothing is built.
- `modules_built`: at least one component has `is_built = True`, and `rig_grp` exists.

Detection checks built-state first, then skeleton, then falls back to empty. It also self-heals: if components claim `is_built = True` but `rig_grp` has been deleted out from under them (a manual delete, or some other tool), detection resets every stale `is_built` flag to `False` and falls through to a lower mode, instead of leaving the UI stuck thinking a rig is built that no longer exists. This is why "the button says Edit Rig but there's no rig in the scene" resolves itself on the next refresh rather than needing a manual flag clear.

## Build Skeleton and Build Modules are two different things, even though there's one button

Historically these were separate phases: Create Guides, then Build Skeleton, then Build Modules. The transient guide-locator layer was retired; loading a blueprint now creates the real skeleton joints directly and stands up the Armature in the same step, so what used to be "Build Skeleton" now happens automatically at load time, before the user clicks anything.

What remains as an explicit action is one button: labeled **Build Rig** in `skeleton` mode, and **Edit Rig** in `modules_built` mode.

- **Build Rig** validates the blueprint, builds every component in parent-first order, wires space switches, organizes the scene, stamps the current Fabricator build version onto the registry, and locks the rig into `modules_built`.
- **Edit Rig** captures each component's current state back onto its network node, deletes `rig_grp` (and everything parented under it), restores the skeleton's bind pose, and stands the Armature back up so the rig is editable again.

Neither of these tears down the authored components. That distinction matters against **New Rig**, a separate, more destructive action: it deletes `rig_grp`, the Armature, all aimers, every Fabricator network node (registry and components), every `FAB_RigBinding` node (the export contract, so animation export and Pose Studio lose their handle on this rig until the next build recreates it), and the `_Joints` / `_Geo` display layers, leaving only the bare skeleton, geometry, and any skin behind. New Rig is for starting over, not for routine editing. Never propose it as a fix for something Edit Rig would solve.

## The Armature: the editable stage

Between skeleton creation and Build Rig (and again after Edit Rig), the scene carries the Armature: a translate-only control cube on every joint except the root, wired with lightweight single-chain IK on joint-to-child edges that are pure aim targets, so dragging a ctrl keeps the child aimed correctly by construction. The Armature is not the animation rig; it is rigger-facing scaffolding for positioning joints. Underneath it, the aimer system (an XYZ arrow curve per joint) owns twist and authored orientation, and is what actually gets baked into `jointOrient` when a build runs.

Build Rig always tears the Armature down first (bakes aimer orientation into the joints, deletes the aimers and Armature ctrls) before building the animation rig on top of the bind skeleton. Edit Rig restores the aimers from a snapshot captured at build time and rebuilds the Armature, so the rigger lands back in the same editable state they left.

## What locks down once the rig is built

In `modules_built` mode, the Fabricator window's editing surfaces collapse into a stripped cockpit view: the Palette panel, Properties option widgets, the Mirror panel (Mirror Limb / Mirror Joints / Mirror Module), and the Symmetry toggle hide; the main splitter (palette, canvas, properties) goes dark, leaving only the rig name (read-only), the Edit Rig button, and the build log. Rig-structure edits are not available while a rig is built, by design. The only way back is **Edit Rig**, which is the expected escape hatch, not a workaround.
