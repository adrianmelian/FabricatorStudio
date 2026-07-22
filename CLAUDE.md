# CLAUDE.md — AM Maya Tools
**Owner:** Adrian Melian | **Role:** Technical Artist | **Engine:** Maya 2025 | **Language:** Python 3.11

This file is the persistent brain for the Maya Tools repo. Claude Code reads it automatically each session. For deep dives on any system, see [Where to find more](#where-to-find-more) below.

---

## Owner Background

14 years game industry TA. Lead TA at Sledgehammer Games (Call of Duty). Expert in Maya, Python, PyQt, PyTorch, rigging, skinning, ML skinning research, cloth sim, Unreal Engine, Unity.

**Collaboration rules:**
- Work peer-to-peer. Do not explain Maya, Python, or rigging basics.
- No preamble. No affirmations. Get to the point.
- Brutal honesty. If there's a better approach, say so.
- Production quality only — no toy scripts.

**Shell / Python invocation (Windows):**
- Always use `py -3 -c` to run Python from bash. `python` and `python3` are not on PATH and will fail.

---

## Where to find more

CLAUDE.md is the high-level brain. For deep detail, query these on demand:

| For… | Look at… |
|---|---|
| Fabricator architecture (full) | `docs/superpowers/specs/2026-05-10-ks-scene-is-truth-design.md`, `2026-05-05-ks-v2-contracts-and-blueprints-design.md`, `2026-05-07-ks-v2-component-library-expansion-design.md` |
| Writing a new Fabricator component | `maya_tools/rigging/fabricator/COMPONENT_AUTHORING.md` |
| Active design specs (every sprint) | `docs/superpowers/specs/YYYY-MM-DD-*.md` |
| Implementation plans (executed sprints) | `docs/superpowers/plans/YYYY-MM-DD-*.md` |
| Fabricator follow-up backlog | `maya_tools/rigging/fabricator/IDEAS.md` |
| Tool internals / API | The tool's `*_app.py` docstring + adjacent `*_ui.py` |
| User-facing tool/component docs | `maya_tools/docs/tools/*.md`, `maya_tools/docs/components/*.md`, `maya_tools/docs/assistant/*.md` |
| Cross-tool contracts | `maya_tools/utils/maya/{network_nodes,rig_binding,side_tokens}.py` |
| History | `git log` (no archeology in this file — deleted systems live in commit history) |

---

## Repo Structure

```
FabricatorStudio/                        ← repo root
├── maya_tools/                          ← Maya-specific tools
│   ├── animation/                       — Pose Studio + Animation Studio (cross-rig via component addresses), anim helpers (mirror pose/controls, reverse keys), snap; pose_ghost.py is unwired legacy
│   │   └── pose_library/                — v1 package: app / ui / sets / thumbnails / framing_dialog / address
│   ├── export/                          — FBX exporter (static + skeletal + anim, subprocess for anim)
│   ├── framework/                       — System layer
│   │   ├── menu/                        — JSON-driven menu builder (fs_menu.py + fs_menu.json)
│   │   ├── shelves/                     — JSON-driven shelf builder (fs_shelf.py + shelf_data/*.json)
│   │   ├── hotkeys/                     — JSON-driven hotkey builder (FabricatorStudio hotkey set)
│   │   ├── renamer_tools/               — Renamer (Hash / Find-Replace / Prefix-Suffix / Renumber)
│   │   ├── scene_cleanup/               — Validator buffet (duplicate-short-names, duplicate-transforms, …)
│   │   ├── project_configs/             — Per-project folders (archangel/, …) with session.json + export_mapping.json; engine templates (_unreal5/_unity/_godot/_generic)
│   │   ├── project_setup_app.py/_ui.py  — Mindmeld Project Setup manager (+ project_config_io/paths, config_generator, config_validation, project_scaffold)
│   │   ├── toolbar/                     — Bridge toolbar strip (toolbar_app/_ui/_manifest + toolbar.json) + ai_bridge/ (read-only AI bridge) + reggie/ (AI TA chat panel)
│   │   ├── annotations.py               — hover-annotation content loader (docs front-matter → popovers)
│   │   ├── tour_engine.py               — guided-tour runner (Step list + late anchor resolver)
│   │   ├── first_run.py                 — the post-install welcome tour
│   │   ├── installer/                   — ML deps installer (pinned numpy/scipy/libigl into mayapy user site)
│   │   ├── utilities.py                 — Viewport toggles, snap, joint helpers, weight clipboard
│   │   ├── decorators.py                — undo_chunk + decorators
│   │   └── maya_startup.py / scene_loader.py
│   ├── rigging/
│   │   ├── joint_orient/                — Joint Aimer (aimer-driven orient, UE5 convention)
│   │   ├── curve_o_matic/               — Control curve library (JSON-driven, multi-shape correct)
│   │   └── fabricator/                  — KS v2 — blueprint-driven modular rigging (see Active Systems)
│   ├── skeleton/
│   │   ├── skeleton_io/                 — Joint hierarchy save/load via world-matrix decomp
│   │   └── joint_mirror/                — Smart Joint Mirror (live DG, permanent disconnect)
│   ├── skinning/
│   │   ├── skin_io/                     — Save/load skin weights to JSON (Direct + Transfer modes)
│   │   ├── skinSmoosh.py                — MPxCommand plugin (neighbor-average blending)
│   │   ├── combine_skinned.py / average_skin_weights.py / weight_clipboard.py / influence_cleanup.py
│   │   ├── auto_skin/ bbw_skin/ dem_bones_bake/ unirig_export/   — ML auto-skin suite (Auto Skin UI, BBW + DemBones solvers via mayapy, ComfyUI feed)
│   ├── utils/
│   │   ├── maya/                        — gui.py, network_nodes.py (shared CRUD), rig_binding.py (FAB_RigBinding), side_tokens.py, orientation_convention.py (aim/up axis truth)
│   │   └── qt/
│   │       ├── widgets/                 — CollapsibleSection, LoggerWidget
│   │       ├── hover_annotation.py      — hover-popover renderer (annotation system)
│   │       └── mindmeld/                — Mindmeld design system (mindmeld_style + .qss); FabricatorStudio/site industrial-terminal UI, distinct from Archangel's cartoony look
│   ├── pipeline/rigged_model_updater/   — source⇄rig .ma sync (skin_io + Fabricator unbuild/rebuild)
│   ├── scene_batch/                     — mayapy batch script runner across scenes
│   ├── skills/fabricator-assistant/     — portable AI-assistant skill (served via get_skill)
│   └── docs/                            — user-facing docs: tools/, components/, assistant/, media/ (repo-root docs/ is gitignored; this one commits)
├── fabricator-mcp/                      — stdio MCP server for Connect Your AI (bridge_client + FastMCP tools)
├── Icons/                               — Shelf + tool icons (flat; PSD sources under Icons/FSShelf/)
└── docs/                                — superpowers specs/plans (gitignored-internal)
```

**Maya startup:** `D:/Documents/maya/scripts/userSetup.py` adds repo root + `_vendor` to `sys.path`, then four deferred calls: `maya_startup.run()` → menu build → shelf build → hotkey build.

---

## Active Systems

### Fabricator (`maya_tools/rigging/fabricator/`)
Blueprint-driven modular rigging. **Active system for new work.** The editable stage is the **Armature** (`armature.py` + `armature_mirror.py` + `armature_watch.py`): a live skeleton-builder where aimers drive joint orientation via aim constraints and limb fragments drop in pre-wired. One **Build Rig / Edit Rig** button toggles the whole rig between Edit Mode and Animation Mode. The old transient guide layer (`create_guides`, `ksfab_guides_grp`, `MODE_GUIDES`) was removed 2026-07-04; the mGear-style three-phase flow (Create Guides → Build Skeleton → Build Modules) is history.

**Scene-is-truth (foundational pillar).** The Maya scene + network nodes are the only source of truth. Blueprint YAML is a starting recipe; once loaded it's irrelevant until the user explicitly Saves Template (snapshot of scene → fresh YAML). No in-memory blueprint, no dirty marker, no save-before-* prompts. Maya's native scene-modified state handles per-`.ma` durability. Full rationale: `docs/superpowers/specs/2026-05-10-ks-scene-is-truth-design.md`.

**Edit Mode pillar:** all rig-structure mutations gated on pre-build state. Animation Mode disables editing affordances. Editing UI (Palette, Properties option widgets, Mirror Modules) hidden/disabled when `MODE_MODULES_BUILT`. Unbuild ("Edit Rig") is the explicit escape hatch.

**Architecture:**
- **Component contracts** (`modules/component.py`): `Plug`, `OptionField`, `Contract`, `Component(ABC)` with `build(instance, context) / unbuild(instance) -> dict`. Subclasses set `CONTRACT` class attr. Auto-discovered by `modules/__init__.py` (pkgutil).
- **Plug system** — `<component_id>.<plug_name>` references resolve via `BuildContext` after topo-sort by `parent_plug` edges.
- **Network nodes** (built on `utils/maya/network_nodes`): `fab_registry` (1/scene) + `fab_<component_id>` (1/component, holds options_json + persisted_json + is_built + joints message-multi + space multis).
- **Drive chain:** `ctrl → parentConstraint(mo) + scaleConstraint(mo) → connector_null → parentConstraint(mo) + scaleConstraint(mo) → joint`. Constraints are children of the joint, NOT under rig_grp, so `unbuild()` must delete them explicitly.
- **Scene structure after Build Modules:**
  ```
  <rig_label>                     # top group (derived from binding/scene)
  ├── <root_joint>                # joint hierarchy
  ├── geo_grp                     # inheritsTransform=False
  └── rig_grp
      ├── fab_controls_grp        # inheritsTransform=False
      └── fab_nulls_grp           # inheritsTransform=False
  ```
- **Ctrl tagging** (mandatory for marking-menu + Pose Studio): `fab_role` (string), `fab_owner` (message → component node), `fab_joint_index` (int). Written at build time via `_tag_ctrl()`.

**Key files:** `fs_app.py` (orchestration), `nodes.py` (network-node CRUD + rig containers), `plugs.py` (BuildContext), `blueprint/io.py`, `blueprint/builder.py` (12-rule validator), `modules/component.py` + `modules/*.py` (one file per component class) + `modules/_chain_common.py` (shared FK-cascade substrate). Note: component files live in `modules/`, not `components/`.

**UI** (`ui/`): `FSWindow` (3-panel splitter: PalettePanel / CanvasPanel / PropertiesPanel; floats frameless or docks into a Bridge workspaceControl) + `SkeletonHelpersBar` (the Armature toolbox: Add Joint, Insert Joints Between, Aim Joints at Aimers, aimer rebuild/reset/visibility, Mirror Limb/Joints/Module, Duplicate Limb/Joints, Live Mirror "Symmetry" toggle). Mindmeld-styled. Mode-aware: `MODE_EMPTY / SKELETON / MODULES_BUILT` via `state.detect_mode()` (`MODE_GUIDES` removed 2026-07-04).

**Shipped components (13):** World, SimpleFK, FKChain, FKAim, AdvancedFK (FK with space-switching), SimpleIK, IKLeg, QuadLeg, IKArm (ribbon IK arm: roll-driven twist, owned skeleton-only fingers, layered fist curl), SplineFK (degree-3 curve + pointOnCurve ride, up-curve), Ribbon (1-wide NURBS surface + uvPin ride, dial board, flip-proof under hard writhing), RibbonSpine (hip/chest/N-mids, always-on aimers, COG ctrl carrying the dial board), FollowJoint. AdvancedRibbon was RETIRED 2026-07-06 (`c24e564`); legacy blueprints map to Ribbon with a warning. SplineFK + Ribbon share the FK-cascade substrate in `modules/_chain_common.py`; the ribbon family shares `modules/_ribbon_common.py`. Templates in `templates/*.blueprint.yaml`.

**Cross-rig identity tuple:** `(component_type, side, role)` on the component network node; `(component_type, side, role, fab_role, fab_joint_index)` per ctrl. Consumed by Pose Studio and (future) mirror-paste.

### Other active tools (one-line each)

- **Curve-O-Matic** (`rigging/curve_o_matic/`) — Control curve library. JSON shapes in `curve_data/` (30 as of 2026-07-09; `get_all_shapes()` globs the folder — don't hardcode the count). Project-wide single source of truth for NURBS-shape persistence (`serialize_shape` / `deserialize_shape_to`, multi-shape correct via `MFnNurbsCurve`).
- **Skeleton IO** (`skeleton/skeleton_io/`) — Joint hierarchy save/load to JSON. World-matrix decomp; handles any rotate order. UE5 convention (rotation in `rotate`, jointOrient=0).
- **Joint Orient** (`rigging/joint_orient/`) — Aimer-driven orient workflow. XYZ aimers per joint, enum `aimTarget` dispatched via pure DG (no scriptJobs).
- **Smart Joint Mirror** (`skeleton/joint_mirror/`) — Live DG mirror across YZ plane. Position + orientation. Permanent disconnect (re-stamps R TRS so chain doesn't collapse). Defensive `is_built` check on owning Fabricator component.
- **Skin IO** (`skinning/skin_io/`) — Save/load skin weights to JSON via API 2.0. Single + multi-mesh (`save_skin_from_meshes` / `load_skin_to_meshes`). Modes: Direct (1:1 by vert) + Transfer (rebuilt source mesh → `copySkinWeights`).
- **Skinning utils** (`skinning/`) — `skinSmoosh.py` (MPxCommand plugin), `combine_skinned.py`, `average_skin_weights.py`, `weight_clipboard.py`, `influence_cleanup.py`. Convention: camelCase filename = Maya plugin; snake_case = Python utility.
- **ML auto-skin suite** (`skinning/auto_skin/`, `bbw_skin/`, `dem_bones_bake/`, `unirig_export/`) — Auto Skin: unified Bind / DeltaMush / DemBones-Bake pipeline (tabbed Mindmeld UI, shelf-wired), BBW libigl solver + DemBones weight solver via mayapy subprocess, ComfyUI-node feed for ML auto-rigging. Pinned deps installed on first use via `framework/installer/` (ml_deps app/UI; never auto-runs).
- **Bridge toolbar** (`framework/toolbar/`) — the primary UI surface: a docked strip hosting Fabricator, Reggie, exporters, Curve-O-Matic, library popovers, skin tools, and settings. toolbar_app/toolbar_ui/toolbar_manifest (JSON-driven, `doc` keys feed hover annotations). Also owns the AI bridge lifecycle (`toolbar/ai_bridge/`).
- **Reggie + Connect Your AI** (`framework/toolbar/reggie/`, `toolbar/ai_bridge/`, `fabricator-mcp/`) — in-Maya AI TA chat panel (Anthropic key via env, read-only law) and the BYO-LLM MCP layer: loopback NDJSON bridge exposing a read-only op registry, consumed by the `fabricator-mcp` stdio server (not yet on PyPI) and Reggie alike. Context pack in `maya_tools/docs/assistant/`; portable skill in `maya_tools/skills/fabricator-assistant/`.
- **Project Setup** (`framework/project_setup_app.py` + `project_setup_ui.py` + `project_config_io/paths.py`, `config_generator.py`, `config_validation.py`, `project_scaffold.py`) — the Mindmeld project-config manager: create/edit/duplicate/delete project configs from engine templates (`_unreal5`, `_unity`, `_godot`, `_generic`), validation panel, scaffold checklist, apply-and-activate. Wired into the FabricatorStudio menu, the fsModel/fsAnim shelves, and the Bridge toolbar's brandmark group.
- **Scene Batch** (`scene_batch/`) — mayapy-subprocess batch script runner across many scenes (cancel-responsive, state-persisting), shelf-wired on fsAnim + fsModel.
- **Rigged Model Updater** (`pipeline/rigged_model_updater/`) — bidirectional source⇄rig `.ma` sync composing skin_io + Fabricator unbuild/rebuild; wired in the Rigging menu + fsAnim shelf.
- **Hover annotations** (`framework/annotations.py` + `utils/qt/hover_annotation.py`) — AnimBot-style hover popovers (title + text + media) across toolbar/palette/properties; content resolves from `maya_tools/docs/tools/<slug>.md` front-matter (`doc` keys) or the widget's own tooltip. Media resolves `.gif`, `.webp`, then `.png` — stills are first-class, not a fallback.
- **Guided tours** (`framework/tour_engine.py` + `utils/qt/coach_card.py` + per-tour step lists) — steps are DATA: a `Step` list plus an anchor resolver, walked one `CoachCard` at a time. Two tours ship: the install welcome (`framework/first_run.py`, still hand-chained) and Fabricator's (`rigging/fabricator/ui/fabricator_tour.py`). Seen-flags live in toolbar prefs under `onboarding`, shared by both. **Anchors MUST resolve late** via a callable (`FSWindow.widget_for`), never captured: Build and Unbuild each call `FSWindow._force_reopen()`, which destroys and rebuilds the whole window mid-tour. Tour media lives in `maya_tools/docs/media/tour/`.
- **Card widgets** (`utils/qt/coach_card.py`, `confirm_card.py`) — the shared floating-card frame (`mindmeld_style.coach_frame_qss`). `CoachCard` is the tour/coachmark pointer: 4-sided notch, always-on-screen placement, clip-or-still media well, act eyebrow. `confirm_card.confirm()` is the Mindmeld yes/no, replacing raw `QMessageBox` in new code (the recommended button takes plasma, not whichever answer is affirmative).
- **Renamer** (`framework/renamer_tools/`) — Tabbed: Hash (`#` placeholder), Find/Replace, Prefix/Suffix, Renumber.
- **Scene Cleanup** (`framework/scene_cleanup/`) — Buffet of validators (`find_duplicate_short_names`, `find_duplicate_transforms`). Tools call relevant helpers; future UI surfaces with per-asset configs. Fabricator's `validate_rig()` is the first consumer.
- **Exporter** (`export/exporter_*` + `export_core.py` + `skeletal_pipeline.py` / `skeletal_export_runner.py`) — FBX exporter for static + skeletal mesh. One-button (Alt+Shift+E) + multi-entry UI with network-node persistence (`FAB_ExporterRegistry`). KS skeletal export runs in a throwaway `mayapy` subprocess (force-save → disconnect skins → break Armature + orient joints to the game contract → strip to joints+mesh → reconnect skins → FBX), same non-destructive pattern as the anim exporter; non-KS rigs use a small in-scene undo-restored path.
- **Anim Exporter** (`export/anim_*`) — Animation FBX export via `mayapy` subprocess (sidesteps mtoa's `PostSceneRead` scriptJob corruption). Pipeline order is load-bearing: bake R+T+S with refs+constraints intact → import refs → delete `nulls_grp` + `controls_grp` (from `FAB_RigBinding`) → FBX export joints.
- **Pose Studio** (`animation/pose_library/`) — v1: full-rig pose save/load, cross-rig portable (via component-address tuples), interactive thumbnail framing dialog (embedded Maya viewport), search bar + user-authored sets. See `docs/superpowers/specs/2026-05-07-ks-v2-pose-library-design.md`.
- **Menu / Shelf / Hotkey builders** (`framework/{menu,shelves,hotkeys}/`) — All JSON-driven. Drop a JSON, get a UI surface. Hotkey gotcha: Python commands must be wrapped in MEL `python("...")` with `sourceType="mel"` (sourceType="python" is unreliable).
- **Project config** (`framework/project_configs/<project>/`) — One folder per project. `session.json` (Maya session settings) + `export_mapping.json` (path patterns, FBX presets, `pose_library_root`, `anim_path_patterns`). Discovery via `export_core.load_project_config(scene_path)` regex-matching the current scene.
- **utils** (`utils/`) — `qt/widgets/` (CollapsibleSection, LoggerWidget) + `qt/mindmeld/` (design system) + `qt/hover_annotation.py` + `maya/{gui,network_nodes,rig_binding,side_tokens,orientation_convention,progress,skin_helpers,skin_subprocess,live_tool}.py`. `orientation_convention.py` is the single source of truth for aim/up axes — read it before touching joint orientation. `scene_utils.py` has no live callers (kept for API surface). New tools should adopt `LoggerWidget` in a `CollapsibleSection`.

---

## Cross-Tool Conventions

Architectural patterns load-bearing for all new tools. Follow them — they're the reason different tools compose cleanly.

**1. Network-node persistence for rig-adjacent per-scene data.**
For state that (a) survives `.ma` copy-paste, (b) tracks specific DAG nodes, (c) is inspectable in the Outliner: one **registry** node per tool + one **per-thing** node per entity, connected via `message` multi-attrs. Never store node names as strings — Maya tracks connections by node identity (rename-resilient). CRUD goes through `utils/maya/network_nodes.py`. Reference: `fabricator/nodes.py`, `export/export_core.py`, `export/anim_core.py`.

**2. Project mapping config for path resolution.**
Tools that derive output paths read `project_configs/<project>/export_mapping.json`. Extend the schema (`pose_library_root`, `anim_path_patterns`, …) rather than introducing parallel discovery. Reference: `export_core.load_project_config()` + `get_pose_library_root()`.

**3. Cross-tool contracts go in `utils/maya/`, not in any owning tool.**
Shared contracts live in `utils/maya/<name>.py`; every tool imports from there. Reference: `rig_binding.py` (FAB_RigBinding, written by Fabricator + SK_ export, read by anim export), `side_tokens.py` (`detect_side`, `flip_side_token`, `SIDE_COLORS`, used by Fabricator, joint mirror, future Pose Studio paste-mirrored).

**4. Scene cleanup as a buffet.**
Validation helpers in `framework/scene_cleanup/scene_cleanup_app.py` as independently-callable functions. Tools that need validation call the relevant helpers themselves; future UI surfaces the same buffet with per-asset configs.

**5. FAB_RigBinding (cross-tool rig contract).**
`FAB_RigBinding_<rig_label>` network nodes — one per rig. **Writers:** Fabricator on `build_modules`, skeletal_mesh export on SK_ export (SK-export-wins-on-overlap on `export_joints[]`). **Readers:** anim export, Pose Studio. Stores `rig_label`, `root_joint`, `export_joints[]`, `nulls_grp`, `controls_grp`. The two group connections let the anim runner delete the rig's drive hierarchy by following message connections — rename-resilient. No fallback at any layer: if a clip references a rig with no binding, anim export blocks at validation.

**6. No Maya bind-pose nodes; frame 0 is the default T-pose.**
No `dagPose` / `bindPose` nodes anywhere — Adrian explicitly avoids them; a skinCluster's own `bindPreMatrix` is the bind. Frame 0 is the default rest/T-pose by convenience, not by rule: Adrian authors raw range-of-motion anims starting on frame 1, so frame 0 stays clean. Tools that need a rest pose (exporter, scale bake, combine, BBW, DemBones) scrub to frame 0 as a pragmatic default in case a rig file carries a ROM — there is no mandate to key frame 0 as bind, and nothing depends on a "frames 0–1 reserved" rule.

---

## Code Conventions

- **Maya:** 2025.3.2, Python 3.11, PySide6 (shiboken6). PySide2 is non-functional in Maya 2025 — do not use it.
- **API:** `maya.api.OpenMaya` (API 2.0) preferred for performance-critical ops; `maya.cmds` for high-level. No PyMEL in new code.
- **UI/function separation — mandatory:** Every tool has two files: `tool_app.py` (functions, zero UI imports, headless-callable) + `tool_ui.py` (UI class, imports app). Functions must be callable without instantiating the UI.
- **UI pattern:** `create_layout()` → `connect_signals()` → `populate()` → `initialize()`. Separate `@classmethod show_window()` with the project `_win` global pattern (close + deleteLater + recreate).
- **Undo:** Use `undo_chunk` context manager from `framework/decorators.py`. Wrap all multi-step operations.
- **Style:** Python 3, f-strings, type hints on public functions. No `reload()`. No `print` without parentheses.
- **Error handling:** `raise RuntimeError(...)` for hard failures, `cmds.warning(...)` for soft. App-layer raises; UI layer catches and calls `_handle_error(e)`.
- **UI error reporting — design pillar:** Every UI class has `_handle_error(self, e, brief=None)` that calls `traceback.print_exc()` then `_set_status(brief or str(e), ok=False)`. Never swallow exceptions silently.
- Ask about joint naming, orientation, or hierarchy conventions before assuming.

---

## Fabricator — Gotchas for component authors

Non-obvious failure modes from production work. Read before authoring a new component or touching IK/FK chain code. Full authoring guide: `maya_tools/rigging/fabricator/COMPONENT_AUTHORING.md`.

**1. `cmds.duplicate` propagates outgoing message connections.** When you duplicate a joint whose `.message` is connected into a multi-attr (e.g. `fab_<id>.joints[N]`), the duplicate auto-extends that multi at the next available index. Fix: disconnect the duplicate's outgoing `.message` immediately after `cmds.duplicate`. Canonical pattern: `_create_chain_duplicate` in `simple_ik.py`.

**2. IK/FK chain duplicates need `cmds.makeIdentity(rotate=True)` for a clean rest pose.** UE5 convention has rotation in `rotate` with `jointOrient=0`. If duplicate has `rotate ≠ 0` at bind, the IK solver picks a different `rotate` that achieves the same world *position* but different *rotation* — 90° twist mismatch on aimed joints. Bake rotation into jointOrient so the chain has `rotate=0` at rest.

**3. `setPreferredAngles` MUST be called BEFORE `makeIdentity`.** `setPreferredAngles=True` reads current `rotate` and bakes it into `preferredAngle`. After `makeIdentity(rotate=True)` zeros rotate, that read returns 0 and the bend hint is gone — IK solver can't tell which way to flex, longer chains lock straight.

**4. BLEND parentConstraint must use `interpType=2` (Shortest / quaternion).** Default `interpType=1` (Average) Euler-blends weight-wise — produces wrap-around twist when IK and FK chains have different Euler representations of the same world rotation.

**5. Animation match handlers must use `matchTransform`, not raw `rotate` copy.** Reading `bind.rotate` and writing onto a nested FK ctrl double-applies the bone orientation (the ctrl's parent carries bind world rotation already). Use `cmds.matchTransform(fk_ctrl, bind_jnt, position=False, rotation=True)` and apply down the chain (parent first).

**6. `parentConstraint(blend, bind, mo=True)` captures the blend chain's *current* pose at creation.** If the IK chain doesn't reproduce bind exactly when the constraint is created, the banked offset is non-identity → FK mode shows wrong pose, IK is correct. Fix the IK chain (gotchas 2 + 3) so blend = bind at constraint creation.

**7. Components are auto-discovered via `Component.CONTRACT` class attr.** Set CONTRACT as a class attribute (not in `__init__`); otherwise auto-discovery silently skips your component.

**8. `fab_role` + `fab_owner` tags are mandatory for marking-menu integration AND Pose Studio cross-rig identity.** Tag every ctrl at build time via `_tag_ctrl(ctrl_name, role)` (see `simple_ik.py`). Without the tags, marking menu has no actions; without `fab_owner` connected to the component network node, action handlers can't find their ctrls.

**9. Output plugs must be registered before downstream consumers.** Topo-sort by `parent_plug` edges ensures registration happens first — but if you call `Component.build()` directly (tests, scripts) you must order parents-before-children manually, or `resolve_plug` raises.

**10. `cmds.duplicate(name='X')[0]`, never just `cmds.duplicate(name='X')`.** Returns a list. Maya appends a numeric suffix if `X` exists; capture the actual returned name.

---

## Open Items

**Fabricator — roadmap:**

| Spec | Title | Status |
|---|---|---|
| 1     | Contracts + Blueprints + UI substrate | Shipped (May 2026) |
| 1.5   | SimpleIK + Spaces + Marking Menu + Magic PV | Shipped (May 2026) |
| 1.6   | Eyeball component | Future (low priority) |
| 2     | Mirror system (joint mirror + module mirror + side_tokens) | Shipped (2026-05-11) |
| —     | Unified Spaces — collapse SimpleIK static + AdvancedFK per-instance into one path | Shipped (2026-05-11) |
| 5     | Pose Studio v1 — full-rig save/load, cross-rig, framing dialog | Shipped (2026-05-12) |
| 5b    | Pose Studio v2 — Load Mirrored + Mirror In Place, region-scoped mirror, multi-source mixing, additive, user-defined categories | Future |
| —     | Authoring Overhaul → shipped as the **Armature skeleton builder** (guides removed, live aimers, limb fragments, Build Rig button) | Shipped (2026-07-04) |
| —     | Ribbon limbs: `_ribbon_common` substrate + RibbonSpine + Ribbon dial board (AdvancedRibbon retired) | Shipped (2026-07-06) |
| —     | Fabricator MCP Phase 1: read-only ai_bridge + `fabricator-mcp` server + assistant context pack | Shipped (2026-07-06; PyPI publish pending) |
| —     | Reggie chat panel (in-Maya AI TA, Anthropic BYO-key, read-only law) | Shipped (2026-07-07) |
| —     | Project Setup tool (Mindmeld config manager + engine templates) | Built (2026-07-07; entry-point wiring + QA pending) |
| —     | Structural prebuild checks (misparented ctrls, renamed joints → Build Issues + AI scan) | Shipped (2026-07-07) |
| —     | IKArm: ribbon IK arm w/ fingers + fist curl + anti-candy-wrap roll | Shipped (2026-07-08) |
| —     | Limb Units + Follower Joints (fab_limb node, follow rules, limb dials, canvas collapse) | IN FLIGHT (2026-07-09, active build) |

**Known follow-ups** (not blockers): tracked in `maya_tools/rigging/fabricator/IDEAS.md`. Notable items: Magic PV singularity (animator escape via world-space), Properties enum auto-save bug, `Component.address()` classmethod, extra-guide capture race.

**Repo housekeeping:**
- User-facing docs: `README.md` at root (drag-drop install quickstart) + `maya_tools/docs/{tools,components,assistant}/` (rendered in-app via hover annotations; repo-root `docs/` is gitignored, `maya_tools/docs/` commits normally).
- MEL folder is empty.
- `maya_tools/skeleton/skeleton_io.py` — superseded by `skeleton_io/skeleton_io_app.py`. Kept unused.
- Git remote: https://github.com/adrianmelian/FabricatorStudio.git (main).

---

*Update confirmed decisions in place. Mark open items resolved when addressed. Deep history lives in `git log`, not here.*
