# Fabricator Glossary

Tight, one-entry-per-term definitions in Fabricator's own vocabulary. See `concepts.md` for how these fit together.

**component / module**: an instance of a rigging building block (SimpleFK, SimpleIK, IKLeg, FKChain, SplineFK, Ribbon, World, and so on), each a Python class declaring its joints, options, and plugs. "Component" is the internal, technical term; "module" is the same thing in user-facing text (Build Modules, the Build Issues dialog's own wording). They are interchangeable.

**component_id**: the unique string identifying one component instance within a rig (for example `lf_arm_ik`). Auto-derived from its joints when not explicitly authored; must be unique across the blueprint.

**plug / parent_plug**: a named output a component exposes for others to attach to, addressed as `<component_id>.<plug_name>` (for example `world.ctrl_out`). `parent_plug` is the string on a component instance naming which upstream plug it attaches to; components are built in the order these references imply, so a parent's plug is always ready before a child tries to resolve it.

**registry**: the single network node, one per scene, holding top-level blueprint metadata (name, source path, schema version, description, origin) and a connection to every component network node in the rig, plus the connection to the rig's root joint.

**blueprint**: the `.blueprint.yaml` file that seeds a rig, a skeleton block (joints with local transforms) plus a components block. It is a starting recipe only; once loaded, the scene is authoritative until an explicit Save Template snapshots it back out.

**Save Template**: the explicit action that snapshots the live scene (registry attrs, component network nodes, skeleton joints) back into a fresh blueprint YAML. The only path from scene to file; it refuses to run while the rig is built, because a built rig's joints can be posed and saving them as bind would corrupt the skeleton on reload. Unbuild (Edit Rig) first.

**Armature**: the rigger-facing editable stage. A translate-only ctrl cube on every joint except the root, with single-chain IK on pure-aim edges so a dragged ctrl keeps its child aimed correctly, standing between skeleton creation and Build Rig, and again after Edit Rig. It is not the animation rig; Build Rig tears it down before building on the bind skeleton.

**aimer**: the per-joint orientation widget (an XYZ arrow curve plus an offset node), the actual owner of twist and authored orientation. Its state is what gets baked into the joint's orientation at build time, and is what the missing-aimers pre-build check looks for.

**guide**: an overloaded word in Fabricator. The old transient per-joint guide-locator layer that used to sit between "Create Guides" and "Build Skeleton" was retired; loading a blueprint creates real joints directly now, and only legacy-scene cleanup code still references that layer. The word survives in a second, current sense: an "extra guide" is a component-declared pivot locator (for example IKLeg's heel and toe-tip pivots), spawned alongside the skeleton and tagged so it can be captured back into the owning component's options at Save Template or Build Rig time.

**binding (FAB_RigBinding)**: the cross-tool contract node, one per rig, written by Build Rig (and by skeletal-mesh export on its own pass) and read by the animation exporter and Pose Studio. Stores the rig label, root joint, export joint list, and the nulls/controls groups, all as live connections rather than stored names.

**rig label**: the human-readable name stamped on the registry and used to name a rig's top group and its rig binding node. Derived once (namespace, then scene filename, then the root joint's name) and persisted so repeated reads stay stable across save and load.

**scene-is-truth**: the foundational rule that the Maya scene, plus its network nodes, not any in-memory blueprint object, is authoritative from the moment a blueprint finishes loading. There is no dirty flag and no unsaved-model state to reconcile; Save Template is the only path from scene back to YAML.

**edit stage**: the state a rig is in whenever it is not built (`skeleton` mode, with the Armature standing), where rig-structure edits (adding or removing modules, repositioning joints, adjusting options) are legal. Building locks structure edits away until Edit Rig returns to this stage.

**New Rig**: the start-over teardown, not a routine edit. Deletes `rig_grp`, the Armature, all aimers, every Fabricator network node (registry and components), every `FAB_RigBinding` node, and the `_Joints` / `_Geo` display layers. Joints, geometry, and skin weights survive. Because it removes the export contract along with the authored modules, never propose it where Edit Rig (or the registry-adopt fix) would do.

**skinCluster influence**: a joint or transform driving deformation on a bound mesh through its skinCluster. The toolbar's Add Influence adds a new influence at weight 0, locked, layering it onto an existing bind without disturbing current weights; Remove Influence takes one back out.

**build report**: the single snapshot of the last Build Rig or Edit Rig attempt, stamped on the registry. Keep-last-only by design: every action overwrites it, so it never grows the scene. Written even on failure, since a failed build leaves no rig behind to inspect any other way.
