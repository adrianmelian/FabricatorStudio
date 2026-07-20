# Fabricator FAQ

The questions riggers most commonly arrive with. Each answer is grounded in how Fabricator actually works, and every one still runs through the same read-only, evidence-first contract: recognize the question, then diagnose the user's live scene rather than answering from the question text alone. Where a problem is a general Maya or FBX behavior rather than a Fabricator choice, say so plainly.

## "Will my rig break when I update Maya?"

Fabricator ships no compiled plugins of its own, so there is nothing to recompile per Maya release, and no native node that fails to load on a new version. It is plain `maya.cmds` plus OpenMaya API 2.0 with PySide6, and does not depend on PyMEL. That removes the most common class of "the tool stopped loading after a Maya update" breakage. It does not make Fabricator immune to every Maya change, and it does not mean any given Maya version is supported: check the actual supported versions rather than promising one. A skinning convenience can optionally wrap a third-party Maya plugin the user installed separately; that is the user's own plugin, not part of Fabricator's rig code.

## "My character looks distorted or sheared when I export to a game engine"

Frame this accurately first: shear on joints is a general Maya and FBX limitation, not a Fabricator defect. FBX cannot carry shear and no game engine supports it, so any rig that puts non-uniform scale onto a bind joint (squash-and-stretch, a scaled body control) can produce a mismatch between Maya and the engine once animation is baked.

What is true of Fabricator: its stretchy IK is stretch-only (no squash), and it manages `segmentScaleCompensate` deliberately rather than leaving it to chance, which reduces the usual ways non-uniform scale reaches bind joints. Do not promise a shear-free export as a guarantee. Diagnose instead: use `get_rig_binding` to see the exact export joint list, and have the user check whether any bind joint in that list is carrying non-uniform scale or shear at the frames they are exporting. The general fix is to keep bind joints on uniform scale.

## "My joints are oriented wrong"

Joint orientation is the most common rigging pain, and it is squarely Fabricator's territory. Orientation is owned by the aimer system (an XYZ arrow curve per joint), which is what gets baked into each joint's `jointOrient` when the rig builds. The editable Armature stage is where orientation is set before Build Rig.

To diagnose: `run_build_checks` flags missing aimers (a deleted aimer curve is a common cause), `get_scene_summary` reports the scene up-axis (a Y-up versus Z-up mismatch is a frequent source of engine-orientation trouble), and the joints themselves can be inspected with `get_node_details`. Fix orientation at the aimer/Armature stage and rebuild, rather than hand-editing a built rig.

## "How do I match or switch between IK and FK?"

IK/FK matching is built into the IK components themselves (SimpleIK, IKLeg, QuadLeg), driven by an `ik_fk_blend` attribute with a match action that snaps the controls to the current pose before flipping the blend. There is no separate picker application to keep version-matched with the rig, and because Fabricator's bookkeeping is connection-based, renaming a joint or control does not break it.

To diagnose a match that misbehaves: `get_component_details` on the IK component shows its controls and chains; check the `ik_fk_blend` attribute and the component's built state, and diagnose from there.

## "How do I transfer, mirror, or fix skin weights?"

Fabricator's Skin tools cover this: Save/Transfer Temp Skin (a closest-point transfer with tolerant matching for near-identical topology), Mirror Skin Weights, Copy/Paste/Average Weights, Add/Remove Influence (adds a new influence at weight 0 and locked, so it never disturbs existing weights), Smoosh (weight relaxation), and the full Skin IO window for save/load/transfer. Which tool fits depends on the situation, so narrow the actual problem first.

Remember the contract: propose the tool or a `maya.cmds` script the user runs; never perform the skinning yourself. `get_rig_binding` and `get_scene_report` help confirm what is bound and where before proposing anything.

## "My rig plays back slowly"

Attribute this correctly: rig playback speed is dominated by general Maya evaluation behavior (evaluation mode, viewport settings, scene and constraint complexity), not by any one auto-rigger. Fabricator ships no compiled evaluation nodes of its own that would add fixed per-rig overhead, but do not claim its rigs are fast without a benchmark on the actual scene.

To diagnose: check whether the scene is on Parallel evaluation, what the viewport is drawing, and how heavy the rig and its constraints are. Treat a slow rig as a performance question to investigate, not a verdict on the tool.

## "I installed the toolset but the toolbar or a tool isn't showing"

Install and menu-loading friction is common to any Maya toolset. For Fabricator, the toolbar can be re-shown from the FabricatorStudio menu (Settings has a Hide Toolbar action, and Show/Hide Tools controls which tools appear at all). If the toolbar or a specific tool is missing, check those first, before assuming a deeper problem.

## After the question is narrowed

These are starting questions, not diagnoses. Once you have recognized which one the user is really asking, drop back into the normal workflow: ask for the one-line description and repro steps, get them to the pre-error state, scan the live scene, then propose the smallest documented fix (from `troubleshooting.md` or a Script Editor script), and offer `report_bug` only after a real attempt has failed.
