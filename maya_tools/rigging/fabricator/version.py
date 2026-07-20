# maya_tools/rigging/fabricator/version.py
"""FS build version — the Fabricator pipeline's version stamp
(Adrian, 2026-07-05: "we should maybe start stamping rigs with fs
build versions").

Build Modules stamps FABRICATOR_VERSION onto the registry
(fabricator_version string attr) at the end of every successful
build. The pre-build checks compare a rig's stamp against the
current constant and raise a YELLOW warning on mismatch — never an
error; version drift is information, not a build break.

Bump discipline: raise this whenever a build-relevant contract
changes — registry schema, component network attrs, the export
orientation contract, baked selection sets, persisted-state shape.
Cosmetic/UI work does not bump. History:

  1.0.0  2026-07-05  Versioning baseline: registry era + Armature
                     lifecycle + baked per-rig selection sets.
  1.1.0  2026-07-09  IKArm renamed to RibbonIKArm (commercial seam split,
                     mechanical rename) to reserve 'IKArm' for a future
                     free basic-arm component. modules/__init__.py's
                     _VERSION_GATED_LEGACY_TYPE_MAP maps old
                     component_type='IKArm' data to RibbonIKArm ONLY when
                     the loading scene's stamped registry
                     fabricator_version predates this bump (or carries no
                     stamp at all, i.e. pre-versioning-era data) — see
                     that module's resolve_component_type for the gate.
  1.2.0  2026-07-09  Limbs + Follower Joints Task 2.3: RibbonIKArm's
                     'fingers' name-string option is retired — finger/
                     twist membership lives ONLY on the component's
                     fab_limb network node from here on (finger_roots[]/
                     curl_excluded[] message multis, resolved via
                     limb_node.find_limb_for_joint and walked live at
                     build time). fs_app._migrate_legacy_finger_membership
                     folds a pre-1.2.0 blueprint/scene's stored 'fingers'
                     option into the limb node ONE TIME on load (gated on
                     modules.is_legacy_scene_data('1.2.0'), the same
                     stamped-registry mechanism the 1.1.0 rename above
                     uses), then drops the option from stored options.
  1.3.0  2026-07-11  KinematicSolutions -> FabricatorStudio rebrand sweep
                     (S2): every scene-persisted identifier this package
                     creates or queries moves from the ksfab_ / ks_*_grp /
                     AM_ prefixes to fab_ / FAB_ — registry + component +
                     limb + pivots-grp marker attrs and node names, ctrl
                     tag attrs (role/owner/joint_index/bind_matrix), the
                     controls/nulls DAG groups, the baked selection-sets
                     master set, and the AM_RigBinding / AM_ExporterRegistry
                     / AM_AnimRegistry export contracts. See
                     fab_migrate_scene.migrate_scene for the upgrade path.
                     NOT hooked into this module's own version-gate
                     machinery (is_legacy_scene_data / resolve_component_
                     type): that gate reads the stamp OFF the registry via
                     nodes.get_registry(), which itself now looks for the
                     NEW fab_registry marker — on a not-yet-migrated scene
                     that lookup finds nothing at all, so the gate can
                     never fire to trigger its own prerequisite. The
                     standalone migrate_scene() is the contract; run it
                     once per old scene before anything else in this
                     package touches it (a future opt-in "detect old
                     marker on scene open" hook is a reasonable follow-up,
                     deliberately not added here — silently mutating node
                     names/attrs on file-open needs its own review, unlike
                     this bump's narrower JSON-option precedent).
  1.4.0  2026-07-20  Blueprint YAML gains a top-level fabricator_version
                     stamp (the build that WROTE the file; absent on older
                     files, read back as ''). fs_app.load() now holds the
                     registry stamp at the FILE's version for the raw-YAML
                     type-resolution window (spawn loop + pivot sync)
                     instead of blanking it unconditionally — the blank
                     made _is_legacy_scene_data return legacy on EVERY
                     load, so the free IKArm (which reclaimed the vacated
                     'IKArm' type string, 1.1.0 above) misloaded as
                     RibbonIKArm on each template round-trip (ribbon-arms
                     bug, 2026-07-19). Files saved after the free IKArm
                     landed but before this stamp existed are unstamped
                     and still read as legacy — one re-save under 1.4.0+
                     cures them. Same-day fragment parity: .limb.yaml
                     carries the same stamp (save_limb writes it;
                     apply_limb_fragment resolves component types against
                     the FILE's stamp via resolve_component_type's
                     data_version param — a fragment drops into a LIVE
                     scene whose registry stamp is current, so the scene
                     gate can't date the file; an unstamped fragment's
                     'IKArm' is ribbon-era and now migrates on drop).
"""
__author__ = "Adrian Melian"

FABRICATOR_VERSION = '1.4.0'
