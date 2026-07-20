---
title: Joint Aimer
summary: Places a rotatable XYZ aimer on each joint so you can preview orientation before baking; Orient All Joints commits it into rotate with jointOrient zeroed for clean UE5 export.
category: rigging
gif: ../media/joint-aimer.gif
video:
---

# Joint Aimer

## What it does
Joint Aimer builds a visible XYZ arrow curve (an "aimer") plus a hidden offset transform for each joint you pick, parented under a single `JntOrient_GRP` group node. You rotate the aimer, or set its `aimTarget` enum to a direct child joint, `Local`, or `World`, to preview exactly how the joint will orient before anything commits. Orient All Joints then bakes that aim into each joint's `rotate` channels, zeroes `jointOrient` and `rotateAxis`, and deletes every aimer, leaving plain joints behind. The aim/up axes and rotate order come from the studio's orientation convention (`+X` aim, `+Y` up, house default), which is also what the export pipeline expects.

## Quick start
1. Select the root joint of the chain you want to orient.
2. Open the `Joint Aimer` window (its shelf button or menu item) and click **Create Aimers - From Root** (this walks up to the joint's topmost parent and builds an aimer for every joint from there down).
3. Select an aimer curve in the viewport. In the Channel Box, set its `aimTarget` enum to the child joint you want it to point at (or leave it on `Local`, the default on a fresh aimer, which reproduces the joint's current aim unchanged). Rotate the aimer curve itself to dial in twist or any offset beyond a straight aim-at-child.
4. Repeat step 3 for each joint that needs a target other than the default.
5. Click **Orient All Joints** to bake the aim into `rotate`, zero `jointOrient`/`rotateAxis`, and remove all aimer geometry.

## Workflow
Joint Aimer opens from the `Joint Aimer` shelf button or menu item (the same right-click popup also lists `Skeleton IO`), which brings up the full `Joint Aimer` window.

The full window's groups, top to bottom: **AIMER SETTINGS** (Aimer Scale, 0.1-100, default 10: aimer curve size scales with each joint's `radius` attribute, so thin finger joints get a proportionally smaller arrow than a radius-3 body joint), **CREATE AIMERS** (Create Aimers - From Root), then standalone **Mirror Selected Aimers**, **Delete All Aimers** (danger-styled), and a tall primary **Orient All Joints** button, followed by a collapsed **// LOG** section.

The output is why this tool sits upstream of export: it writes clean `rotate`-only orientation with `jointOrient` and `rotateAxis` both zeroed, which is the same export contract Skeleton IO and the Rig & Animation Exporter rely on for a clean FBX round trip into Unreal (a joint carrying a nonzero `jointOrient` alongside `rotateAxis` silently world-aligns on UE/Unity import). Internally, Fabricator's Armature guide phase runs this exact same aimer engine automatically on every joint (it seeds each aimer's target from detected geometry and rebuilds it when a limb's topology changes), so a rigger building through Fabricator components rarely opens this tool directly. Joint Aimer is the tool for orienting hand-built or imported skeletons, or for manually overriding orientation Fabricator seeded.

**Mirror Selected Aimers** copies the selected aimer's `aimTarget` enum index (positionally, not by child name) and rotation to its opposite-side counterpart, reflecting rotation across the YZ plane (`rx` unchanged, `ry -> -ry + 180`, `rz -> -rz`) so the mirrored side aims the opposite direction down the bone, per the studio's UE5 mirroring convention. It resolves the counterpart joint from the source joint's name using the shared side-token rules (`l`/`r`, `lf`/`rt`, `left`/`right`, etc.).

## Gotchas
- A fresh aimer's `aimTarget` defaults to `Local`, which is a no-op aim (reproduces the joint's current orientation exactly): nothing points at a child until you set it, so Orient All Joints on untouched aimers just re-bakes the existing orientation.
- Aimer and offset node names are built from the joint's short name only, with namespace and DAG path stripped. A scene with duplicate short joint names will either collide (`Aimer already exists`) or wire the aimer network to the wrong joint.
- The `aimTarget` enum's list of child joints is captured when the aimer is created. If you add, remove, or reorder that joint's children afterward, the enum goes stale: the standalone tool has no rebuild control in its UI; only Fabricator's internal `rebuild_aimer` handles that automatically when it restructures a rig.
- Mirror Selected Aimers matches children by enum position, not by name. If the left and right side of a mirrored pair have their children ordered differently, the mirrored aimer can end up pointed at the wrong child without an error.
- Orient All Joints unparents every joint that has an aimer to world, bakes, then reparents; any other constraint or connection driving those joints during the bake will be disrupted by the temporary unparent.
- Aimer curves and the hidden Local/World reference locators have translate and scale locked and hidden by design (position comes from a pointConstraint, not manual edits), so a script or rig trying to key those channels directly will fail.
- Only direct joint children can be aim targets; locators, meshes, or other non-joint children under a joint never appear in the `aimTarget` enum.

## Troubleshooting
**"Aimer already exists for '<joint>'."** An aimer is already built for that joint. Delete it first (Delete All Aimers, or delete just that one via script) before creating a new one.

**"'<joint>' is not a joint node." / "Joint '<joint>' does not exist."** Create Aimers requires an actual joint node in the selection. Select a joint, not a control or transform, before clicking Create Aimers.

**"No aimer found for '<joint>'. Create aimers first."** Something tried to orient, classify, or apply state to a joint that has no aimer yet. Run Create Aimers on that joint (or its hierarchy) first.

**"mirror_aimers: no aimer in selection. Select one or more aimer ctrls..."** Mirror Selected Aimers needs the aimer curves themselves selected (the XYZ arrows under `JntOrient_GRP`), not the underlying joints.

**"<N> joint(s) have no aimer curve (deleted in the viewport?): ..."** on Orient All Joints. An aimer curve was hand-deleted in the viewport, leaving its offset node behind. Recreate aimers for the listed joints before orienting again; the shipped UI always runs with the strict check, so it will not silently skip them for you.

**"No aimers in scene." warning on Orient All Joints.** There is nothing to bake. Create aimers first.

**Mirror log lines like "center joint has no counterpart," "no side token in joint name," "mirror joint '<name>' not found," or "no aimer on '<name>'."** These are per-aimer skip reasons, not failures of the whole operation: they mean that particular joint is a center joint, has no left/right token in its name, has no counterpart joint in the scene, or its counterpart has no aimer yet. Fix the naming or create the missing counterpart's aimer, then mirror again.
