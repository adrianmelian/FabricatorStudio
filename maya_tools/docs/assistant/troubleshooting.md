# Fabricator Troubleshooting

The playbook to consult before proposing any fix. Every entry below is grounded in Fabricator's live pre-build check registry or in documented Fabricator behavior. If a problem doesn't match anything here, say so plainly rather than guessing: gather evidence (status, build checks, the last build report, a viewport screenshot), and only then propose a next step, or suggest filing a bug once a real attempt has failed.

## How the pre-build checks work

Clicking Build Rig always runs the pre-build checks first. If any issues are found, the Build Issues dialog opens automatically: one entry per detected issue, with Fix Selected / Fix All buttons for issues that carry an automatic fix, and a proceed button whose label follows the state of the list. When nothing remains, or everything remaining is a warning, that button reads **Build** and is enabled (warnings inform, they never block). Once error-level issues exist it reads **Build Anyway**: disabled while anything remains that cannot be skipped (fix those first, by button or by hand), and enabled only when everything left is explicitly skippable. Don't tell a user to press "Build Anyway" when the dialog is currently showing them "Build".

## missing_aimers

**What the user sees**: the Build Issues dialog lists "Missing N aimer(s)", or, if building headlessly, an error naming the affected joints.

**Why it happens**: a joint's aimer curve (the XYZ arrow widget from the joint-orient system) was deleted directly in the viewport. Deleting the curve leaves its offset node behind, so Fabricator can tell the aimer is gone even though the joint itself is untouched.

**Fix**: in the Build Issues dialog, select "Missing N aimer(s)" and click Fix Selected (or Fix All). This captures every live aimer's current state, wipes the whole aimer layer, and recreates one aimer per skeleton joint, restoring saved aim targets where known and otherwise detecting the joint's natural aim. Then proceed with the build (the proceed button reads Build once nothing blocking remains), or close the dialog and press Build Rig again.

**If you don't want to fix it**: this issue is skippable. Build Anyway proceeds and leaves the affected joints at their current orientation, with no aim pass applied to them.

**Give up and file a bug when**: Fix Selected doesn't make the entry disappear on the next check run, or the recreated aimers point somewhere clearly wrong that re-aiming can't correct.

## overconnected / overconnected_manual

**What the user sees**: "Duplicated joints on N module(s)" (key `overconnected`, auto-fixable), and/or "N module(s) need manual joint repair" (key `overconnected_manual`, no auto-fix).

**Why it happens**: duplicating a joint in the viewport, or using Maya's native joint mirror, carries the source joint's outgoing message connection onto the duplicate, which silently extends the owning component's joint connections. The component now looks like it owns more joints than its contract allows, which aborts the build.

**Fix (`overconnected`)**: select the entry and click Fix Selected/Fix All. Fabricator only auto-repairs when every one of the component's originally-named joints can still be matched by name among the live connections; it releases the extra, duplicated connections and leaves the duplicates as free joints in the skeleton, available to receive modules of their own. If the duplicated chain shares short names with the originals, rename the duplicates first, or the release step can't tell them apart.

**Fix (`overconnected_manual`)**: there is no automatic fix. This happens when an original joint was deleted or renamed, so which connections to keep is ambiguous. Open the Node Editor, inspect the component node's joint connections by hand, disconnect the wrong ones, then build again.

**Give up and file a bug when**: the manual entry's described joint count doesn't match what the Node Editor actually shows connected.

## orphaned_modules

**What the user sees**: "N module(s) reference deleted joints", listing each module's id, type, and the missing joint name(s).

**Why it happens**: a joint was deleted in the viewport, commonly a component's secondary joint (for example a pauldron), while the component network node that was driving it still exists and still points at the old name.

**Fix**: select the entry and click Fix Selected/Fix All. This deletes the orphaned component network node(s) only; the surviving skeleton is untouched. Re-add a module to the surviving joints afterward if the rig still needs that piece.

**Give up and file a bug when**: the same module keeps reappearing as orphaned immediately after a fix and a fresh Build Rig attempt.

## legacy_registry

**What the user sees**: "Pre-registry rig (no registry root joint)", severity warning, in scenes built before Fabricator's registry system existed, or where the registry's root-joint connection has gone stale.

**Why it happens**: the rig predates the current architecture, or something disconnected the registry's link to the scene's root joint.

**Fix**: select the entry and click Fix Selected/Fix All. This adopts the rig: it creates a registry if one is missing, connects it to the scene's root joint, derives and persists a rig label, and stamps the current Fabricator build version, all without touching modules, skins, or geometry. This is skippable: the build proceeds even unfixed, it just can't run Armature-era features (orientation-contract validation, aimer snapshots, version stamping) until adopted.

**Important**: never propose New Rig as a fix for this. New Rig deletes every Fabricator component network node; the adopt fix above is specifically the non-destructive alternative.

**Give up and file a bug when**: the adopt fix raises an error. It requires exactly one identifiable root joint in the scene; a scene with none, or several disconnected hierarchies, needs a person to decide which is the real root first.

## version_unstamped / version_mismatch

**What the user sees**: "Rig predates FS version stamping" (key `version_unstamped`), or "Built with Fabricator X, building with Y" (key `version_mismatch`). Both are warnings, never errors, and both are skippable.

**Why it happens**: the registry's Fabricator version stamp either doesn't exist yet (a rig built before version stamping shipped) or doesn't match the currently-installed Fabricator's version.

**Fix**: none needed. A successful Build Rig re-stamps the registry with the current version and the notice retires on its own. This is only worth a second look if the release notes for the version gap call out an actual build-relevant contract change.

## misparented_ctrls

**What the user sees**: the Build Issues dialog lists "N control(s) parented outside the rig", naming each control, where it currently sits, and where it belongs. Reggie sees the same entry in his scene scan.

**Why it happens**: a Fabricator-owned control (an armature ctrl like `upperarm_l_amt_CTL`, or a tagged rig control) was reparented by hand, usually an accidental middle-drag in the Outliner. Builds relying on the expected hierarchy then fail downstream with misleading errors (locked channels, constraint failures), or worse, succeed while the rig is silently broken.

**Fix**: no automatic fix; this is deliberate hands-on repair. Undo back to before the reparent if it just happened. Otherwise re-parent the control back under the parent the issue names (armature ctrls belong under `fab_armature_grp`, following the joint hierarchy; rig controls under `fab_controls_grp`). Then Build again.

**If you don't want to fix it**: skippable, but do not skip it. Building over a mis-parented control is exactly the "passes but broken" trap.

**Give up and file a bug when**: the control is parented where the issue says it belongs and the entry still appears on the next check run.

## renamed_component_joints

**What the user sees**: "N joint(s) renamed since the component recorded them", listing each component with recorded and current names. A warning: it informs, never blocks.

**Why it happens**: a joint wired to a component was renamed after capture. Message connections keep the rig working, but exports, bindings, and rebuilds that rely on recorded names can break later.

**Fix**: rename the joint back, or if the new name is intentional, rebuild the component so it re-records.

**Give up and file a bug when**: the names shown match exactly and the warning still appears.

## Build fails with a traceback that isn't one of the above

Run the pre-build checks and read the last build report before proposing anything:

1. Run the pre-build checks (or open the Build Issues dialog) to see if a listed issue already explains it.
2. Read the last build report for the most recent Build Rig or Edit Rig attempt: it records the outcome, per-component status, and, on failure, the tail of the actual error, even for a build that produced no rig to inspect. This is the best evidence available when nothing is left in the scene to look at.
3. If the traceback mentions a joint "not in the skeleton block", or a specific structural rule, it is the blueprint's structural validator: schema version mismatch, a joint-parent cycle, duplicate joint names, an unregistered component type, a joint count outside the component's contract bounds, a component referencing a joint missing from the skeleton, two components claiming the same primary joint, an unresolved parent-plug reference, a parent-plug cycle, a bad option value, or a duplicate component id. Match the message text to the rule and fix the authored data it complains about (rename, reconnect, or remove the offending component or joint).
4. If the traceback mentions a duplicate short name, that is the separate scene-wide unique-name check, run at build time because it needs a live scene. Rename the reported duplicates.

**Give up and file a bug when**: the build checks are clean, the structural validator finds nothing, there are no duplicate names, and the traceback still doesn't match anything above.

## rig_grp was deleted manually

Deleting `rig_grp` by hand, instead of pressing Edit Rig, leaves every component's built flag stale (still marked built) with nothing left for it to point at. This is not a stuck state: mode detection self-heals on its next read, resetting every stale built flag and falling through to `skeleton` mode (or `empty` if no skeleton joints survive either). Re-open or refresh the Fabricator window and the Build Rig button should read correctly again. If it doesn't, that is worth a bug report; the self-heal is meant to be silent and automatic.

## Scene opened from an older Fabricator version

Covered by `version_unstamped` / `version_mismatch` above: this is informational only, by explicit design (yellow, never red). A rig doesn't need "fixing" for this by itself. Only chase it further if the version gap's release notes describe a build-relevant contract change (registry schema, component network attributes, the export orientation contract, baked selection sets, or persisted-state shape).

## Export / binding problems

Fabricator writes a rig binding node at the end of every successful build (skeletal-mesh export can also write or refresh one). It is the cross-tool contract the animation exporter and Pose Studio read to find a rig's joints and its drive-hierarchy groups: rig label, root joint, the export joint list, and the nulls/controls groups. If a clip or export references a rig with no binding, export blocks at validation rather than guessing.

If export is failing or behaving strangely:

1. Confirm the rig actually has a binding for its rig label. No result means the rig was never built with Build Rig (a hand-assembled or partially-built scene won't have one), or the binding was deleted. Note that New Rig deletes every rig binding node in the scene along with the Fabricator network nodes, so a missing binding on a rig that used to export cleanly often means someone ran New Rig since the last build.
2. Check that the binding's root joint still resolves to a real joint, and that its nulls/controls group connections still point at live groups. Edit Rig deletes `rig_grp` (and the groups under it); a fresh Build Rig recreates the binding along with everything else.
3. Remember writers can overlap: both Build Rig and a skeletal-mesh export can write the same binding node, and whichever ran most recently wins on any overlapping field. If the export joint list looks wrong, check whether an export ran more recently than expected.

**Give up and file a bug when**: the binding exists, its groups resolve, and the export still fails for a reason not covered above.
