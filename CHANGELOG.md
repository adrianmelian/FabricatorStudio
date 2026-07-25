# FabricatorStudio Changelog

Notable changes only - breaking API shifts, new tools, rig convention changes, major fixes.
Raw commit history lives in git. This is the human-readable layer on top.

Format: `## [date] - short title` then bullet points. Keep entries brief.

---

## [2026-07-25] - v1.1.1: template drop onto an existing skeleton

- **Loading a template into a scene that already holds its skeleton works
  again** (was: "Armature: no root joint" and the load died). The load now
  adopts the joints already in the scene, creates only what is missing, and
  stands the Armature up as usual. Partial overlaps (just the root, or part
  of the hierarchy) are covered too.

---

## [2026-07-22] - v1.1.0: Fabricator's guided tour

Fabricator opens into an empty window, and its two ways in (File > New Rig, or
a template in the Components panel) are the two least visible controls in it.
This is the fix for that first minute.

- **A guided tour of Fabricator**, once ever on your first open, and any time
  after from **File > Take the Tour**. Eight stops in two acts. *The Layout*
  names each region of the window: Armature Tools, Components, Rig Outliner,
  Properties. *First Rig Walkthrough* is hands-on and builds a rig with you:
  load a template, build it, drop back to Edit Mode, done.
- **The hands-on stops wait for you.** They advance when the template actually
  loads, when the build actually finishes, when the unbuild actually lands, and
  each one says what it is waiting for. Nothing to click past, and skipping asks
  first.
- **Cards carry clips and stills**, and every card stays fully on screen no
  matter where the panel it points at sits.
- **Armature Tools**: the skeleton toolbar is now a labelled group, and the
  Components / Rig Outliner / Properties headers pick up the same accent, so
  the window reads as four named regions instead of loose chrome.

Also in this batch, from the same pass:

- **Solo handles**: drag one joint without taking its whole subtree with it.
  Show/Hide and Reset live under Skeleton.
- **Straighten Mid Joint**: the classic knee/elbow fix, corrected on one axis
  so the deliberate forward bend survives. It picks the error axis and tells you
  which it chose.
- **Aim All at Child**: points every aimer down its chain in one pass. The
  repair for an imported skeleton whose aimers all sit on Local.
- **Export a single entry or clip** by right-clicking its row, no checkboxes or
  multi-select needed.

---

## [2026-07-21] - v1.0.1: first-install fixes

First patch after launch, from the first outside installs.

- **AutoSkin backend installs without dev tooling** (#22): the engine installer
  used to demand `uv` and `git` already be on the machine and hard-fail
  otherwise, which broke on a fresh artist box. It now resolves each tool as
  private-copy-first, then PATH, then a pinned, hash-verified download, so a
  clean machine installs with nothing pre-set-up.
- **AutoSkin uninstall cleans up fully on Windows** (#23): uninstalling the
  backend left its `repo/` folder (about 1.6 GB of model checkpoints) behind,
  because git marks its files read-only. Uninstall now clears the read-only bit
  and removes everything it owns.
- **Project bindings live in Project Setup now**: the per-project binding
  settings and the machine-wide Shared Configs pointer moved out of Settings and
  into Project Setup, next to the project list they belong with. Settings keeps
  only the truly per-machine concerns.

---

## [2026-07-19] - Installer choice + the first-welcome tour

- **Installer**: choose the Install Directory (defaults to Maya's own scripts
  folder, correct on machines with a redirected Documents folder), or run the
  toolset directly from a shared/network folder with no copying. The
  uninstaller understands both: a shared install only unwires this machine
  and never deletes shared files.
- **First launch**: a four-step welcome tour replaces the two first-run
  popups. It starts right after install, walks the FabricatorStudio
  button, the toolbar Settings, and the hover cards, and exits on the
  Project Setup button. One-time, skippable at every step.
- **Install layout**: everything now lands in one `fabricator_studio/`
  folder inside the chosen Install Directory (was two loose folders), and
  the repo `Icons/` folder is renamed `icons/`. Uninstalling a nested
  install removes that one folder cleanly.
- **DemBones split out**: the DemBones Bake tool and its pip-dependency
  step leave the main package and will ship as a separate add-on (same
  model as AutoSkin). The skin menu shows the entry only when the add-on
  is present.
- **One card language**: the walkthrough's coach card (plasma-bordered,
  rounded, bold title over dim body) is now the standard for hover
  annotation cards, the rig-built / exported toasts, and a new
  ProgressCard window for long operations.
- **Ship manifest**: `ship_manifest.json` is the reviewable ledger of
  what the installer package includes; the packaging script refuses to
  build if a directory exists that the ledger has not ruled.

---

## [2026-07-11] - The road to v1 (May 8 → Jul 11 catch-up)

Two months of shipped work, summarized; per-feature detail lives in git and the specs.

- **Armature skeleton builder** replaces the transient guide layer: live aim-constraint aimers, limb fragments, one **Build Rig / Edit Rig** button (guides + `MODE_GUIDES` removed 2026-07-04).
- **Ribbon family**: `_ribbon_common` substrate (uvPin ride, falloff skin, sculpt board, jiggle, composed volume), **RibbonSpine** (hip/chest/N-mids, COG dial board), generic **Ribbon** dial board; AdvancedRibbon retired (legacy blueprints map to Ribbon).
- **Open-core limb split**: **RibbonIKArm** (fka IKArm; roll-driven twist, owned fingers, layered fist curl), **RibbonIKLeg** (reverse-foot + thigh/shin ribbons + roll joints), free **IKArm** (ribbon-stripped basic arm) - `_limb_common` (free) / `_ribbon_common` (paid) seam with an import-boundary test.
- **Limb Units + Follower Joints**: `ksfab_limb` network node (message-connected membership), follower-joint primitive (distribute/match rules, live armature eval), finger + twist dials with skinning guards, canvas limb collapse, side-aware limb color, Save Limb round-trip; twist-joint **adoption** at component add (UE5.8 Manny compatible); **Build Engine IK Joints** action (UE engine-reference set).
- **Reggie + Connect Your AI**: in-Maya AI TA chat panel (BYO Anthropic key, read-only law) and the `fabricator-mcp` MCP server over a read-only loopback bridge; assistant context pack in `maya_tools/docs/assistant/`.
- **Project Setup** (Mindmeld manager): create/edit/duplicate/delete project configs from engine templates (`_unreal5` vetted; `_unity`/`_godot`/`_generic` experimental), validation-gated saves, scaffold checklist.
- **DevBot toolbar** + AnimBot-style **hover annotations** (docs front-matter → in-app cards) across toolbar/palette/properties; user docs at `maya_tools/docs/{tools,components}`.
- **Exporters**: model exporter renamed **Rig Exporter**; skeletal export is rig-layer-aware (armature / built-modules / bare joints); structural prebuild checks wired into Build Issues + AI scan.
- **ML auto-skin suite**: ksAutoSkin (Bind / DeltaMush / DemBones-Bake), BBW libigl solver (+pymeshfix repair), opt-in dependency installer.
- **Fixes of note**: straight-chain IK preferredAngle seeding, Build Rig GUI lockup (watch-suspended build/unbuild), template-load stall, armature FBX export path.
- **Cleanup**: legacy v1 rigger removed (superseded by Fabricator); dead modules pruned.

---

## [2026-05-08] - KS v2 Spec 1.5 (SimpleIK + Space Switching + Marking Menu)

`SimpleIKComponent` ships as a generic 3-joint IK/FK chain - substrate for the IK family (Spec 1.6 IK Arm + IK Leg will inherit). Three-chain build (bind/FK/IK + BLEND), parent+scale-constraint blending into BLEND with weights driven by `ik_fk_blend` (FK/IK weight + reverse), bind chain follows BLEND with `mo=True`. Per-arm `<start>_ik_grp` outliner container (v1 pattern). IK end + PV ctrls world-anchored under that container; chain anchor parent-constrained from `parent_ctrl` so the IK chain follows the rig's parent (e.g. clavicle).

**Magic PV** (auto-tracking polevector): DG node graph (decompose × 2, plusMinusAverage × 3, multiplyDivide × 3, vectorProduct × 2, composeMatrix) reads `offset_ctrl.worldMatrix` + IK end ctrl, computes midpoint + perpendicular projection scaled by bind limb length × `pv_distance`. Uses bind-pose limb length (not live) so the PV stays at constant perpendicular distance as the chain folds - avoids singular-pose collapse. Single-bind-perp limitation: PV can flick when live limb_axis aligns with bind_perp (e.g. IK hand crosses the shoulder plane). Animator escape: marking-menu → world space.

**Space switching infrastructure**: `Plug.space_target`, `SpaceConsumer`, `Contract.space_consumers` schema additions; `_collect_space_providers` + `build_space_switch_dg` in `fs_app.build_modules`. `wtAddMatrix` weighted sum + per-target `multMatrix` static-offset compensation (row-vector math: `static_offset = ctrl_bind × inv(provider_bind)`; `matrixSum = static_offset × provider`). World-anchored ctrls store `ks_v2_bind_matrix` for cycle-free 'world' provider. Per-component-network-node `space_switch_nodes[]` tracking; symmetric unbuild cleanup. Animator-friendly enum labels via `ks_v2_display_name` attr (e.g. `clavicle_ctrl` instead of `<id>.anchor_out`). SimpleIK exposes `anchor_out` (cycle-free, follows clav) for internal consumers; `start_out`/`mid_out`/`end_out` (bind joint matrices) remain for external use.

**Animation marking menu**: `Ctrl+Alt+RMB` on viewPanes (matches v1 `anim_utils` pattern). Persistent `popupMenu` registered on FSWindow open; `postMenuCommand` rebuilds items each invocation from selected ctrl's `Contract.actions` + dynamically-synthesized space-switch entries. Handlers in `maya_tools/rigging/ks_rig_maker/actions/` resolved via dotted-path strings + `#kwargs` query-string suffix (frozen `Contract` can't store callable refs; reload-safe). SimpleIK ships four mode actions (Switch to IK/FK with and without match) on the IKFK switch ctrl.

**UX upgrades**: `Component.can_apply(joints, blueprint)` per-component validation (palette enable/disable, drop handler, validator); joint-role aware properties dropdowns (descendancy filtering); single-select + first-child auto-fill on Add for multi-joint components; lowercase joint suffix convention (`_fk`/`_ik`/`_blend`).

35 tasks across 4 phases (1.5a SimpleIK core / 1.5b spaces / 1.5c magic_pv / 1.5d marking menu).

---

## [2026-05-07] - KS v2 substrate follow-up: bug fixes, UX pass, prop rig verified

Same-week follow-up to the substrate landing - closes out everything found while building and stress-testing real rigs (prop + Michael bare-bones).

**Fixes shaken out by hands-on use:**
- `fs_app.load` now tears down + recreates the registry every load. Previously skipped re-creating component nodes on subsequent loads (existing-id check), which left joint→component message connections empty when the original load ran before joints existed. Loading the prop blueprint into a fresh scene with `root` joint already present now wires up correctly.
- Restored full v1-equivalent null-pair architecture in WorldComponent + SimpleFKComponent: `ks_nulls_grp/<jnt>_offset_null/<jnt>_connector_null` + `ks_controls_grp/<jnt>_offset_ctrl/<jnt>_ctrl` parented properly under `rig_grp`. Drive chain `ctrl → connector_null → joint`. Both `unbuild()` methods explicitly delete joint constraints so the rig_grp delete cascade leaves the joint clean.
- `build_skeleton` reconciles joint→component message connections after creating fresh joints (the wipe-and-recreate severs them otherwise).
- `create_guides` spawns locators at correct world positions (was using local translate as if it were world). Guides now hierarchically parented matching the joint tree - repositioning a parent locator drags children, matching artist expectations.
- New `JointSpec.world_translate` field; bootstrap reads from Skeleton IO's `worldMatrix[12:15]`. YAML emits the field only when non-zero (keeps hand-authored single-joint props clean).
- Canvas selection now survives `refresh_all` cycles. `set_blueprint` computes a structure signature; if it matches the prior call, just refreshes labels (no tree rebuild). When a rebuild IS needed, captures + restores selection by joint name. Re-emits selection signals after rebuild so the properties panel picks up component-add/-remove on the selected row.
- `build_modules` auto-resolves blank `parent_plug` values by walking up the joint hierarchy from each instance's primary joint until it finds an ancestor with a component. With World auto-attached on root, every component is reachable. User overrides are preserved.

**UX:**
- World auto-attaches to the root joint at load (idempotent). World hidden from the user-facing palette + right-click submenu - it's structural, not user-managed.
- Build Rig / Unbuild Rig button moved out of the BlueprintPanel, now lives below the splitter - visually adjacent to the canvas/properties workflow that drives it. State-aware (primary blue when ready, danger ember when built).
- Single-joint contracts (max_joints=1) accept multi-selection: select N joints, drag SimpleFK from palette → N component instances, one per joint. Multi-joint contracts (e.g. 3-joint IK chains in Spec 1.5) still require exact joint count selection.
- Per-component color identity: `Contract.color` field (hex). World = `#FFC857` (plasma yellow), SimpleFK = `#7AC4FF` (cyan). Renders as a leading swatch in the palette + as the joint row's foreground tint in the canvas.
- Properties panel scene-rehydrates on FSWindow open (if a `ks_v2_registry` exists in the scene, rebuild `_CURRENT_BLUEPRINT` from disk-skeleton + scene-components). User picks up where they left off after Maya restart / module reload.
- Dropdown selection now syncs to the loaded blueprint's path on every refresh - was floating on whatever section header Qt defaulted to.
- Properties refresh immediately when adding a component to the currently-selected canvas row (was requiring a click-away-and-back).
- Joint properties trimmed: only name (RO), rotate_order, radius, Sync from Scene. Translate / rotate / joint_orient / parent removed - best edited via viewport + channel box; parent visible in canvas tree.
- All numeric spinboxes + combo boxes (`_NoWheelDoubleSpinBox` / `_NoWheelSpinBox` / `_NoWheelComboBox`) ignore mouse-wheel events. Hovering and scrolling no longer accidentally changes values; the wheel propagates to the parent QScrollArea.
- Save-before-build prompt removed (build works on scene state, not disk YAML - prompt was misleading). Save-before-close prompt removed (Maya scene save handles per-character durability). Reload-when-dirty warning kept - that's the one path that actually discards in-scene state.
- `prop.blueprint.yaml` ships in `blueprints/templates/` - single root joint at origin with World; minimum-viable rig for smoke-testing the whole pipeline.

**Architectural reframe (docs):** the "disk-first storage authority" framing in the original spec was incomplete. Corrected: blueprint disk YAML is authoritative for the BLUEPRINT (the recipe - `base_biped`, `base_quadruped`, `base_prop`); Maya scene `.ma`/`.mb` is authoritative for the RIG (the baked per-character instance - `Michael.ma`). KS Save = explicit "promote rig to template" action, rare. Most per-character work never saves back to YAML. Spec Section 3 + Decision 1/9, CLAUDE.md, this changelog updated to match.

**Verified end-to-end:** prop rig (single root + World) builds, drives, unbuilds, rebuilds, captures CV state across cycles. Michael bare-bones bootstrap + load + create-guides + build-skeleton verified visually. Full Path B with components is the next step before merging to master.

---

## [2026-05-06] - KS v2 substrate (blueprint-driven, two-tier storage authority, new authoring UI)

Spec 1 of the KS v2 series shipped. New `Python/maya_tools/rigging/ks_rig_maker/` namespace develops parallel to v1 (`kinematic_solutions/`); v1 stays in place during transition and gets deleted in a single cleanup commit once Michael's spine is verified end-to-end on v2. 30 commits on branch `ks-v2-substrate`. Substrate verified via prop rig (single-joint World) end-to-end through the new GUI. Spec at `docs/superpowers/specs/2026-05-05-ks-v2-contracts-and-blueprints-design.md`; plan at `docs/superpowers/plans/2026-05-05-ks-v2-contracts-and-blueprints.md`. Designer review integrated before any UI code (`docs/superpowers/specs/2026-05-05-ks-v2-ui-review-notes.md` + `2026-05-06-ks-v2-ui-review-response.md`).

**Architectural shift: two-tier storage authority.** v1 stored rig definition in network nodes only; v2 splits authority between two artifacts. The **blueprint** (recipe) lives on disk as YAML - read-mostly templates that provide cross-character consistency (`base_biped` etc.). The **rig** (baked instance) lives in the Maya scene, persisted by the regular `.ma`/`.mb` save - per-character durable state. Loading a blueprint spawns the registry + component nodes; the artist edits/builds in the scene; Maya scene save preserves it. KS Save walks the network nodes and writes a YAML (permissive: script-editor edits get persisted), but Save is a deliberate "promote rig to template" action - rare. Most per-character work never saves back to YAML. FSWindow rehydrates from the scene's `ks_v2_registry` on open (skeleton from disk, components walked from network nodes); the dirty flag is informational only and gates only Reload (the one path that discards in-scene state).

**New: component contracts.** Declarative metadata (`Plug`, `OptionField`, `Contract`, `Component` ABC) drives validation, build order, and UI form generation. Components set `CONTRACT` as a class attr; auto-discovery via pkgutil picks them up. Spec 1 ports `World` and `SimpleFK` to the contract form; Spec 1.5+ adds FK Chain / IK Arm / IK Leg / Reverse Foot / Twist / Eyeball.

**New: three-phase mGear-style workflow** - `Create Guides` → `Build Skeleton` → `Build Modules`. State-aware phase buttons cycle Forge variants (`primary`/`default`/`danger`/disabled) per scene state. `Unbuild Modules` flips destructively into the Build Modules slot when modules are built.

**New: blueprint format + IO.** `<name>.blueprint.yaml` with `name/schema_version/description/origin/skeleton/components/wiring`. PyYAML 6.0.1 vendored at `Python/_vendor/yaml/` (pure-Python; userSetup adds it to sys.path). `read_yaml` / `write_yaml` round-trip preserves all fields including optional origin metadata and per-component persisted blocks. `Blueprint.from_skeleton_json` bootstraps a starter blueprint from a Skeleton IO export (reads from the nested `attrs` block; converts `rotateOrder` int enum to string).

**New: 12-rule pure-Python validator.** `validate_blueprint(bp) -> list[str]` checks schema version, joint hierarchy (parent refs / cycles / uniqueness), component types / joint counts / joint refs / option choices, parent_plug graph (unique outputs + DAG, malformed-reference handling). Scene-wide unique-name validation runs separately at build time via `find_duplicate_transforms`.

**New: plug system + BuildContext.** `<component_id>.<plug_name>` references in the blueprint resolve at build time. Components register output plugs as Maya plug strings (e.g. `'root_ctrl.worldMatrix[0]'`); downstream components query the BuildContext to wire their inputs. Topo-sort by parent_plug edges ensures registration happens before resolution.

**New: 4-panel authoring UI** - `FSWindow` with brand bar, top BlueprintPanel (dropdown sectioned by source + Save/Save As/Reload via forge_style.button + 3 phase buttons + caps_label origin badge), middle splitter (PalettePanel left, CanvasPanel center with QTreeWidget + drop-target for palette drags + right-click context menu, PropertiesPanel right with stacked Joint+Component sections wrapped in QScrollArea), LoggerWidget in CollapsibleSection (expanded by default), status bar at bottom. Splitter state + window size persist via optionVar.

**New: component port (v1-equivalent drive chain).** Both World and SimpleFK build the v1 null-pair architecture: `ks_nulls_grp/<jnt>_offset_null/<jnt>_connector_null` + `ks_controls_grp/<jnt>_offset_ctrl/<jnt>_ctrl`, with `ctrl → parentConstraint(mo) + scaleConstraint(mo) → connector_null → parentConstraint(mo) + scaleConstraint(mo) → joint`. World parents offset_ctrl directly under `ks_controls_grp` (no parent_plug); SimpleFK parents under the parent's ctrl (resolved via parent_plug, falls back to ks_controls_grp if absent). Both `unbuild()` methods explicitly delete the joint's parentConstraint+scaleConstraint children before returning - those constraints live as joint children, NOT under rig_grp, so the rig_grp delete cascade leaves them dangling otherwise.

**New: build-skeleton joint reconcile.** `build_skeleton` wipes + recreates joints when the skeleton already exists; the wipe severs joint→component message connections. New `_reconcile_component_joints(bp)` helper walks the blueprint's components and re-establishes message connections on the freshly-created joints. Without this, Build Modules stayed disabled after a Rebuild Skeleton until a full blueprint reload.

**New: shared CV / channel state capture.** Components' `unbuild()` returns a `persisted` dict (`starting_shape`, `cv_data`, `channels`, `enum_orders`) which the orchestrator writes to the network node before the rig_grp delete. Subsequent Build Modules calls restore CV deltas via `curve_o_matic_app.serialize_shape` / `deserialize_shape_to`.

**New: schema migrations registry skeleton.** `blueprint/migrations/__init__.py` with `@register('1.0', '1.1')` decorator pattern. Empty for Spec 1; future schema bumps add migration fns there.

**New: prop blueprint template.** `blueprints/templates/prop.blueprint.yaml` ships as the minimum-viable rig - single root joint at origin with a World component. Useful as a starting point for any one-master-control prop and as a smoke-test artifact.

**Forge design system integration.** All KS v2 UI conforms: brand bar (`display_label` + `caps_label` + `horizontal_rule`), `forge_style.button(text, kind)` for every button (no raw `QPushButton`), `forge_style.field_label` for form labels, status bar with `forge_style.pill('FORGE', 'idle')` and `caps_label` segments. Phase button variants cycle via `forge_style.tag(btn, kind)` on every mode change.

**Designer review integration (pre-code).** Five blockers caught + folded into Section 3 of the spec before any UI code shipped: brand bar consistency, phase-button visual primacy, auto-save debouncing (`editingFinished` for QLineEdit, `valueChanged` immediate for combos/spinboxes/checkboxes, debounced 300ms for Vector3 spinboxes), click-target ambiguity on annotated rows (resolved as Option 2 - stacked properties), `ChannelsField` 3-column K/L/H exclusive radio grid sketched. Plus status bar, save-on-close prompt, mode detection on `WindowActivate`, splitter persistence via optionVar, keyboard shortcuts (Ctrl+S/Shift+S/R, Esc, Delete; Ctrl+B deliberately omitted - too easy to misfire).

**Known gaps / follow-ups (carried in CLAUDE.md Open Items):**
- "New From Scene" UI button - Save As gated on loaded blueprint; first blueprint requires templates folder or `bootstrap_from_skeleton`.
- Forge → Mindmeld rename - design docs renamed, code imports still say `forge`.
- Reload-with-dirty-buffer prompt - Reload silently discards in-memory edits.

---

## [2026-05-05] - KS foundation tightening + cross-tool unification

Multi-week refactor closing out KS Phase 1 cleanly and unifying the network-node + rig-binding patterns shared across KS, model exporter, and anim exporter. 23 commits on branch `ks-foundation-tightening`. Empirically verified end-to-end on a real rig.

**New: `utils/maya/network_nodes.py`** - shared CRUD primitives for the network-node persistence pattern. 12 helpers (`create_marked_node`, `find_marked_nodes`, `find_one_marked_node`, `ensure_message_attr`/`ensure_string_attr`/`ensure_bool_attr`, `connect_message`/`connect_message_multi`/`replace_message_multi`/`disconnect_message`, `get_message_target`/`get_message_targets`). Idempotent throughout. KS `ks_registry.py`, model exporter `export_core.py`, anim exporter `anim_core.py` all rebase onto these - ~150 LOC of triplicated boilerplate consolidated.

**New: `utils/maya/rig_binding.py`** - `AM_RigBinding` cross-tool contract relocated out of `maya_tools/export/export_core.py`. Owned by no single tool; both KS and model exporter import from `utils.maya.rig_binding` directly. All 14 caller sites across `anim_core`, `anim_app`, `anim_ui`, `anim_export_runner`, `skeletal_mesh` migrated. Function rename map: `get_rig_binding_data → get_binding_data`, `list_rig_bindings → find_all_bindings`, `list_live_rig_bindings → find_live_bindings`, `find_rig_binding_for_root → find_binding_for_root`. Behavior unchanged.

**New: `maya_tools/scene_cleanup/`** - module seeded for the future Scene Cleanup tool. Headless API only for now (full UI is a "coming soon" stub on AMModel/AMRig shelves). `find_duplicate_short_names(nodes)` is the building block; `find_duplicate_transforms()` walks the entire scene. Future entries add `find_orphaned_constraints`, `find_unfrozen_transforms`, etc. Per-asset configs will pick which buffet items run for each asset type.

**New: `KS writes AM_RigBinding on build_rig()`** - anim export against a freshly-built KS rig works without requiring a prior SK_ export. SK-export-wins-on-overlap on shared `export_joints[]`.

**New: `KS organizes the scene on build_rig()`** - `organize_scene()` (also exposed publicly for manual re-runs) builds the deliverable scene structure under a top group named after the scene filename (via `derive_rig_label`):
```
<filename>           inheritsTransform=True
├── <root_joint>
├── geo_grp          inheritsTransform=False (skinned-to-KS-joint meshes)
└── rig_grp          inheritsTransform=True
    ├── ks_controls_grp
    └── ks_nulls_grp
```
Idempotent; respects user-authored mesh hierarchy. Defensive `_grp` suffix when the top-group name collides with a non-transform node.

**New: `validate_rig()` runs as the first step of `build_rig()`** - KS owns the rig-file-cleanliness rule because nothing else does. Calls `scene_cleanup_app.find_duplicate_transforms()` and surfaces every duplicate short name in the scene. On any duplicates, raises `Rig cannot be built - fix these issues first:\n\n• ...` listing each conflict with full DAG paths. The rig file shipped to animation must have unique short names everywhere so references (`Hero:root` etc.) resolve unambiguously.

**KS internals tightened:**
- **A1 + A2** - `WorldModule` no longer inherits from `SimpleFKModule`; both peer subclasses of `KSModule`. `build_fk_ctrl()` and `unbuild_fk_ctrl()` extracted to `base_module.py` as free functions; `_lock_visibility` and `_auto_scale_ctrl` moved alongside.
- **A3** - `build_rig` and `unbuild_rig` no longer swallow per-module exceptions. Half-built rigs with inconsistent `is_built` flags are worse than loud failures. `remove_module` and `rebuild_module` keep their per-module try/except (intentional caller-side recovery).
- **A4 + A5** - `_resolve_ctrl_parent` walks UP the joint chain to find the nearest ancestor with a built module's ctrl (not just the immediate parent). Required for biped rigs with un-controlled intermediate joints (clavicle, twist segments, etc.). New `KSModule.PARENT_STRATEGY` class attr (`'walk_up'` | `'world'` | `'custom'`) - Phase 2 IK modules use `'world'` for worldspace anchoring; reverse foot uses `'custom'`.
- **B4** - Multi-shape control CVs survive unbuild/rebuild. KS `store_cv_data` / `apply_cv_data` route through new `curve_o_matic_app.serialize_shape` / `deserialize_shape_to` (multi-shape correct via `MFnNurbsCurve`). `apply_cv_data` self-heals legacy flat-list format on first build after upgrade.
- **B5** - `mirror_module` reads every dynamic non-message attr from the source and passes through to the new side's `create_node`. `spaces_json`, `cv_data_json`, `free_float_space`, `anim_pivot`, all module-specific extras carry. Default color consolidated.
- **B6** - `_swap_side` replaces all occurrences of the matched pattern (was capped to first occurrence).
- **C7** - `KSBaseOptionsWidget` accepts a `logger` constructor arg and routes status through the parent window's `LoggerWidget`. The `inViewMessage` fade (errors disappeared in 800ms) is gone.
- **C9 + auto-save** - All KS scene-node names are lowercase snake_case (`ks_registry`, `ks_<jnt>_<module_type>`, `ks_controls_grp`, `ks_nulls_grp`). Apply Changes button removed from options widget; every field change auto-saves to the module node and triggers `rebuild_module` if built. `_loading` flag suppresses spurious saves during initial widget population.
- **`create_module_node` bool fix** - bool extras now go to `at='bool'` attrs explicitly instead of being JSON-serialized into strings (fixes type demotion on mirror).

**Curve-O-Matic gains:**
- `swap_shape(name, ctrl)` - replaces a ctrl's NURBS shapes in-place, preserving transform identity + connections.
- `serialize_shape(transform) -> dict` and `deserialize_shape_to(transform, data)` - single source of truth for NURBS-shape persistence project-wide. Multi-shape correct via `MFnNurbsCurve`. Internal `_capture_override_color` / `_apply_override_color` factor the colour-attr round-trip.
- Torn-state safety: both `swap_shape` and `deserialize_shape_to` reparent new shapes onto the target BEFORE deleting old ones. If `cmds.parent` raises, target carries both shape sets briefly but never zero - no destructive failure.

**Documentation:** spec at `docs/superpowers/specs/2026-05-05-ks-foundation-tightening-design.md`, plan at `docs/superpowers/plans/2026-05-05-ks-foundation-tightening.md`.

**Migration impact for existing rigs:** uppercase `KS_*` nodes don't auto-migrate - torn down and rebuilt on first Build. CV data is self-healing on first unbuild→build cycle (legacy flat-list format gets overwritten with the new schema).

---

## [2026-04-18] - Initial scaffold

- Repo structure documented in CLAUDE.md
- Changelog scaffolded

## [2026-04-18] - Skeleton tools + Maya menu system

**New: Skeleton Saver** (`maya_tools/rigging/skeleton_saver/`)
- `skeleton_saver_app.py` - `save_skeleton()`, `load_skeleton()`, `get_skeleton_data()`. Serializes full joint hierarchy to JSON: world-space translate, local rotate, jointOrient, rotateOrder, scale, preferredAngle, radius, segmentScaleCompensate, override color, joint labels.
- `skeleton_saver_ui.py` - Save UI. Reads root from scene selection. Type combo auto-populates from `maya_tools/rigging/skeletons/` subdirs; "Add new type…" creates a new subfolder on demand.
- `skeleton_loader_ui.py` - Load UI. Browses the skeleton library by type/name and rebuilds the skeleton in-scene.
- Library root: `maya_tools/rigging/skeletons/<type>/<name>.json`
- First saved skeleton: `skeletons/characters/ue5_base.json` (UE5 Mannequin, 23 joints)

**New: UE5 Skeleton Generator** (`maya_tools/rigging/ue5_skeleton_generator/`)
- `ue5_skel_gen_app.py` - Hardcoded UE5 Mannequin joint data (23 joints). Creates orange template joints at default positions, live mirror mode via multiplyDivide nodes (L↔R, X-negation), builds final oriented skeleton from template positions.
- `ue5_skel_gen_ui.py` - Template/mirror/build UI with state-driven button enable/disable.

**New: Maya menu system** (`maya_tools/`)
- `am_menu.py` - JSON-driven menu builder. `build_menu()` / `rebuild_menu()`. Supports items, submenus, dividers - arbitrarily nested.
- `am_menu.json` - Menu definition. Currently: Skeleton submenu (Saver, Loader) + Reload Menu item.
- `userSetup.py` (`D:/Documents/maya/scripts/`) - Calls `build_menu()` via `evalDeferred` on Maya startup.

**Updated: `utils/gui.py`**
- Ported from PySide2/shiboken2 to PySide6/shiboken6. `get_maya_window()` now uses `int(ptr)` for Maya 2025 SwigPyObject compatibility. All 7 tool UIs that import this are fixed.

## [2026-04-18] - Skeleton fitting pipeline foundation

**New: `maya_tools/rigging/skeleton_fitting/`**
- `bake_fit_metadata.py` - One-time script that classifies all 161 joints in a skeleton JSON into four fit categories: `guide` (artist-placed, ~45 joints), `interpolated` (lerped between two guides, ~35 joints - twist joints and finger phalanges), `ik` (snapped to FK counterpart post-build, 11 joints), `offset` (world-space delta from parent, ~70 corrective/deformation joints). Computes and writes `derive_from`/`derive_weight` for interpolated joints and `local_offset` for offset joints from default positions. No Maya dependency - pure JSON. Supports `dry_run=True`.
- `bake_fit_metadata.py` integrated into Skeleton Saver UI via "Bake fit metadata after save" checkbox (default on) in a collapsible Advanced Options section. Re-saving a skeleton automatically re-bakes metadata.
- `docs/skeleton_fitting_tool_design.md` - Full design doc covering problem statement, joint category system, guide map (all 161 UE5 joints), guide representation decision (locators + colour overrides), mirror mode, fit file persistence, UI mockup, module structure, phase plan. All five open questions resolved.

**Key design decision - spine guides:**
- spine_02, spine_03, spine_04 are **guides** (not interpolated). The real UE5 spine has a lumbar-thoracic curve (Z peaks at spine_03 = 4.25, drops to 0.53 at spine_05). Linear interpolation between spine_01 and spine_05 is wrong - all five spine joints require artist placement.

**Refactor: utils restructured**
- `utils/maya/gui.py` - `get_maya_window()` (moved from `utils/gui.py`)
- `utils/maya/progress.py` - progress window helpers, modernised API (`set_max` replaces `max_progress`, keyword args throughout)
- `utils/maya/scene_utils.py` - FBX export settings, namespace removal utilities (moved from `utils/scene_utils.py`)
- `utils/qt/widgets.py` - `CollapsibleSection` reusable PySide6 widget
- All 21 import sites across the codebase updated to new paths. Old flat files left as redirect stubs.

## [2026-04-18] - Skeleton Fitting Tool v1 + repo cleanup

**New: Skeleton Fitting Tool** (`maya_tools/skeleton/skeleton_fitting/`)
- `skeleton_fitting_app.py` - headless API: `create_guide_rig(json_path)`, `delete_guide_rig()`, `guide_rig_exists()`, `mirror_active()`, `get_template_path()`, `save_fit(path)`, `load_fit(path)`, `enable_mirror(side)`, `disable_mirror()`, `build_skeleton(delete_guides, save_path)`.
- `skeleton_fitting_ui.py` - four group sections: Template (type+skeleton combos, Create/Delete Guides), Fit File (Save/Load), Mirror Mode (L→R / R→L radios, Apply/Remove buttons), Build (delete guides checkbox, save fitted checkbox + name field, BUILD button). Full state-driven button enable/disable.
- Guide rig: 45 coloured locators (yellow=centre, blue=left, red=right) at UE5 default positions, flat under `SkelFit_Guide_GRP`. Scale varies by role (root large, limb medium, finger small).
- Mirror: `multiplyDivide` node negates X translate from source side to target side. Target locators locked while active.
- Build: four-pass position derivation - guides (read locators) → interpolated (lerp) → corrective (world offset) → IK (snap). Four passes required because BFS JSON order does not match inter-category dependency order.
- Fit persistence: `.fit.json` sidecar stores guide world positions + source skeleton path.

**Renamed: fit category `offset` → `corrective`**
- `bake_fit_metadata.py` updated throughout. `_apply_offset` → `_apply_corrective`. All 70 corrective joints in `ue5_base.json` re-baked with correct category name.
- Rationale: "offset" is ambiguous and reserved for future use. "corrective" matches industry naming. "secondary" was rejected (implies secondary motion / physics-driven joints).

**Deleted: `maya_tools/rigging/ue5_skeleton_generator/`**
- 23-joint hardcoded generator fully superseded by `maya_tools/skeleton/skeletons/characters/ue5_base.json` (161 joints, baked fit metadata). Workflow is now: Skeleton Saver → bake → Fitting Tool.

**Reorganised: `maya_tools/skeleton/` extracted from `maya_tools/rigging/`**
- `maya_tools/rigging/skeleton_fitting/` → `maya_tools/skeleton/skeleton_fitting/`
- `maya_tools/rigging/skeleton_saver/` → `maya_tools/skeleton/skeleton_saver/`
- `maya_tools/rigging/skeletons/` → `maya_tools/skeleton/skeletons/`
- All imports, hardcoded `SKELETONS_ROOT` paths, and `am_menu.json` commands updated. `maya_tools/rigging/` now contains only rigger and rig_modules.

**Refactored: `utils/qt/widgets/`**
- `widgets.py` promoted to a package. `CollapsibleSection` moved to `utils/qt/widgets/collapsible_section.py`. `__init__.py` re-exports it. Old `widgets.py` deleted.

**Design pillar: verbose UI error reporting**
- All UI exception handlers now call `_handle_error(e)` → `traceback.print_exc()` to Script Editor + brief message in status bar. Every UI file imports `traceback`. App-layer raises, UI layer catches and reports.

## [2026-04-19] - Shelf builder, Save Shelf, Maya startup & project config system

**New: JSON-driven shelf builder** (`maya_tools/shelves/`)
- `am_shelf.py` - `build_shelves()` scans `shelf_data/` and rebuilds each shelf fresh on startup. Supports `button`, `separator`, `save_button` types. Buttons support `popup` array for right-click menus. `noDefaultPopup=True` required for custom RMB menus to work.
- `save_shelf_to_json(shelf_name)` - reads live Maya shelf state back to JSON. Skips the save button (identified by command string), strips trailing separators, re-appends `save_button` sentinel. Artists can edit shelves in Maya's Shelf Editor and persist with one click.
- `_normalise_image()` - inverse of `_resolve_image`; round-trips absolute Icons/ paths back to bare filenames using `os.path.normcase` for Windows path variance.
- `shelf_data/AMTools.json` - first shelf: Skeleton Tools button (LMB opens Saver, RMB popup has Saver / Loader / Fitting Tool) + save_button sentinel.

**New: Maya startup system** (`maya_tools/maya_startup.py`)
- `run(config_name)` - applies all session settings from a project config JSON. Entry point called by `userSetup.py` via `evalDeferred`.
- `apply_units()`, `apply_camera_clips()`, `apply_grid()`, `apply_misc()` - standalone helpers, callable individually.
- `list_configs()` / `load_config()` - enumerate and load JSONs from `project_configs/`.
- `get_active_config()` / `set_active_config()` - persist last-used config via Maya `optionVar`.

**New: Project config system** (`maya_tools/project_configs/`)
- `unreal5.json` - UE5 session config: linear `cm`, angular `deg`, Y-up, 30 fps (ntsc), undo depth 200, camera clips near `1.0` / far `1 000 000` cm, grid 1200 size / 100 spacing / 10 divisions (24m working area, 1m lines, 10cm sub-grid).
- Drop a new JSON in `project_configs/` to add a config - no Python changes needed.

**New: Project Setup UI** (`maya_tools/project_startup_ui.py`)
- Minimal dialog: QComboBox auto-populated from `project_configs/` (display name from JSON `"name"` field), Apply button, status label.
- Restores last-used config from `optionVar` on open.
- Accessible via AMTools menu → Project Setup.

**Updated: `userSetup.py`**
- Top-level auto-imports: `cmds`, `om`, `mel`, `importlib`, `os`, `sys`, `json` - land in `__main__` so artists don't need boilerplate in Script Editor.
- Three `evalDeferred(lowestPriority=True)` calls: `_run_startup` → `_build_am_menu` → `_build_am_shelves`.

**Updated: `am_menu.json`**
- Project Setup entry added above the Reload Menu divider.

## [2026-04-19] - Fix project settings resetting on new scene

**Fixed: `maya_startup.py` - settings now survive `File > New Scene`**
- Grid (`cmds.grid()`) and camera clip planes are scene-specific; `File > New Scene` recreates camera nodes and resets viewport state, wiping startup settings.
- Added `_register_new_scene_callback()` - registers `MSceneMessage.kAfterNew` once per Maya session via Maya API 2.0 (`om.MSceneMessage.addCallback`). Re-applies units, camera clips, and grid after every new scene.
- Module-level `_new_scene_callback_id` guard prevents duplicate registration if `run()` is called again from the Project Setup UI.
- `kAfterOpen` intentionally not registered - opening an existing scene with different settings should not be silently overridden.
