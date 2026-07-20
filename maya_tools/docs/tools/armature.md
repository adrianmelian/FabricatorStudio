---
title: Armature
summary: Fabricator's editable skeleton stage. Place the joints and they get auto-oriented, symmetry mirrors live, and it bakes into a clean, engine-ready skeleton when you build your Animation Rig.
category: rigging
gif: ../media/armature.gif
video:
---

# Armature

## What
The Armature Rig is Fabricator's editable skeleton stage. You place the joints and they'll get auto-oriented. Every rig starts here. You place and shape the skeleton in the Armature, Fabricator keeps it oriented and symmetrical as you work, and it bakes into a clean, engine-ready skeleton when you build your Animation Rig.

## Why
A rig is only as good as the skeleton under it. The Armature keeps that skeleton correct while you author it. Joints orient themselves as you move them, symmetry mirrors one side onto the other live, and the whole thing stays live so you can restructure at any point.

## How
1. Start with a Template, a single root joint, or bring your own skeleton.
2. Move the Armature controls around.
3. Watch the aimers move around, they represent the final orientation of that joint.
4. Use Symmetry to keep your skeleton symmetrical. Turn it off for asymmetry.
5. Duplicate and Mirror limbs as needed.

The Armature is non-destructive. Unbuild a rig and you land right back in it with nothing lost, and skinned meshes detach and rebind automatically around Armature edits, so you can restructure the skeleton even after skinning.

## Workflow
- **Ctrl look and hierarchy**: every Armature ctrl is an x-ray ball that draws through the character mesh, colored by side (blue left, red right, kind color center) and by the joint's own aim intent (yellow when it aims at a child, cyan when it is Local, World, or a leaf); the root is the one exception, a circle-four-arrow. The ctrl tree mirrors the joint tree one to one, so dragging a parent ctrl carries its whole branch. Translate places the joint; rotate stays free for FK-style rough-in, the aimers own the final orientation bake.
- **Two edge kinds**: a joint whose parent's aimer targets it as a child gets a single-chain IK edge (parent to child, parented under the child's own ctrl) plus a stretch network so the child rides exactly onto wherever the ctrl sits, keeping the parent aimed at it as you drag. Everything else, branch joints, leaf joints, an authored Local/World aim, gets a plain point constraint instead. A child placed geometrically off the parent's aim axis (an authored deviation) demotes to the point-constraint form too, since the IK/stretch math assumes the child sits on axis.
- **The root ctrl**: the root joint wears a circle-four-arrow ctrl, sized well clear of the placement orbs. Every other ctrl parents under it, so dragging it moves the whole armature as one piece, and it is what draws the guide line down to the first joint. (Restored 2026-07-18; earlier builds gave the root no ctrl and parented its children's ctrls straight to the armature group.)
- **No ctrl by design**: any follow-ruled joint (twists and similar helpers driven by `follow_rules`) never gets an Armature ctrl. Its translate stays unlocked instead, so the live follow-rule evaluator can write straight to it the moment any ctrl moves, a distribute rule's rotate stays whatever its own aimer already baked; a match rule's rotate is rule-driven too.
- **Skeleton Helpers Bar**, above the rig-name row and hidden once the rig is built, is the Armature toolbox: four hover-to-open groups plus the Symmetry toggle. Skeleton (Add Joint, Insert Joints Between, Build Engine IK for the UE5 `ik_foot_*` / `ik_hand_*` / `center_of_mass` reference set), Aimers (Aim Joints at Aimers, Rebuild All Aimers, Reset All Aimers, Show/Hide Aimers), Mirror (Mirror Limb, Mirror Joints, Mirror Module, see Armature Skeleton Symmetry below), and Duplicate (Duplicate Limb, Duplicate Joints, both a smart whole-subtree rename so nothing collides).
- **Live aimers** (the Joint Aimer engine) give every joint a rotatable XYZ arrow, or an `aimTarget` enum you can point at a child, Local, or World, before anything commits. The Armature seeds aimers automatically from geometric detection the moment a joint exists, and every Armature rebuild re-runs the same bake-and-restore so aimer state, including any authored offset, survives. Aim Joints at Aimers is what actually commits that state into clean, export-ready orientation.
- **Armature Skeleton Symmetry**: the Symmetry toggle in the Skeleton Helpers Bar turns on a live, selection-based mirror across the YZ plane for both ctrls and aimers, replacing the retired Smart Joint Mirror tool's one-shot, freeze-on-disconnect workflow. Grab either side's ctrl or aimer and the opposite side follows in real time; selecting the other side's ctrl or aimer flips which side drives which, no rewiring needed. A branch Mirror, Duplicate, or Reparent op preserves an active Symmetry toggle across its own internal teardown-and-rebuild, the live network re-enables against the freshly rebuilt ctrls when it finishes; other Armature rebuilds (Aim Joints at Aimers, dropping a limb, Unbuild) still turn it off, and Symmetry needs a re-toggle to resume after those. Mirror Joints / Mirror Limb / Mirror Module, by contrast, are one-shot: they snap a whole branch's joints (and, for Limb/Module, its components) onto the other side once, with no live link afterward, for once you are done posing symmetrically and just need the far side's topology to match.
- **Non-destructive by construction**: Build Rig captures the Armature's bind pose and aimer state before tearing it down for the animation rig; Edit Rig (Unbuild) restores the bind pose and rebuilds the Armature from that capture, landing you back exactly where you left off. Skinned meshes detach automatically before an Armature edit and reconnect once Build Rig finishes, so restructuring the skeleton, adding a joint, reparenting a branch, after skinning does not require a manual re-skin.

## Gotchas
- Dragging a joint directly does nothing; translate and rotate are locked on every Armature-wrapped joint. Select its ctrl instead (or, for a follow-ruled joint, edit the rule, there is no ctrl to grab).
- A child placed off the parent's aim axis on purpose gets a plain point constraint instead of the single-chain IK/stretch edge; placement is preserved, but the ctrl behaves differently than a same-shaped on-axis sibling.
- Symmetry turns itself off after any Armature rebuild that is not a branch Mirror, Duplicate, or Reparent, Aim Joints at Aimers, a limb drop, and Unbuild all drop it. That is expected; re-toggle Symmetry to resume.
- Symmetry pairs joints by side token; a joint with no recognized side token, or whose counterpart ctrl does not exist yet, is silently skipped, and enabling it with no pairs at all raises "no left/right Armature ctrl pairs in the scene."
- Reset All Aimers deletes every aimer and re-seeds from geometric detection, wiping any authored Local/World target or twist offset; it confirms first, but there is no partial-undo once you agree.
- Deleting an Armature ctrl in the viewport (rather than through Fabricator) is read as deleting that joint's whole branch, the joint, its descendants, and every module on them go with it.

## Troubleshooting
**A joint will not move when I drag it.** You grabbed the joint, not its ctrl; joints are locked and unselectable by design. Select the `_amt_CTL` parented over it instead, or, if it is a follow-ruled joint, edit the rule driving it.

**"Armature: no root joint. Pass root= or load a rig with a registry root_joint."** There is no Fabricator registry in the scene, or its root joint no longer resolves. Run File > New Rig, or adopt the registry via the pre-build check's fix.

**"Rig cannot be built, export orientation contract violations (rotate carries orient, jointOrient=0, rotateAxis=0)."** A joint still carries non-zero `jointOrient` or `rotateAxis`. Run Aim Joints at Aimers, then Build Rig again.

**"Live Mirror: no left/right Armature ctrl pairs in the scene."** Symmetry needs at least one recognized left/right ctrl pair. Build joints with side-tokened names first (e.g. `_l`/`_r`, `lf_`/`rt_`), or mirror the first side's joints across before enabling it.

**Symmetry reads on but the far side stopped following.** Expected after Aim Joints at Aimers, a limb drop, or Unbuild, those rebuild the Armature without preserving the toggle. Turn Symmetry off and back on to re-derive pairs from the live scene.

**"N joint(s) have no aimer curve (deleted in the viewport?)."** An aimer curve was hand-deleted rather than removed through Fabricator. Run Rebuild All Aimers, or Build Anyway in the Build Issues dialog to skip them and keep their current orientation.
