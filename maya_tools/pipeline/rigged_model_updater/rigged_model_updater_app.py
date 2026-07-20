# maya_tools/pipeline/rigged_model_updater/rigged_model_updater_app.py
"""Rigged Model Updater — bidirectional sync between source and rig .ma files.

Detects direction from the active scene's parent folder:
- `<character>/source/<name>.ma` → push to `<character>/rig/<name>.ma`
- `<character>/rig/<name>.ma` → push to `<character>/source/<name>.ma`

Composes existing tools (skin_io, Fabricator unbuild/rebuild, cmds.file) —
no new low-level code, just orchestration.

Spec: docs/superpowers/specs/2026-05-12-rigged-model-updater-design.md
"""
__author__ = "Adrian Melian"

import shutil
from pathlib import Path

import maya.cmds as cmds
import maya.api.OpenMaya as om


# Direction sentinels.
_DIR_SOURCE_TO_RIG = 'source_to_rig'
_DIR_RIG_TO_SOURCE = 'rig_to_source'


def detect_direction() -> tuple:
    """Return (direction, source_path, partner_path) for the active scene.

    Direction is _DIR_SOURCE_TO_RIG when the active scene lives in a
    `<character>/source/` folder, _DIR_RIG_TO_SOURCE for a `<character>/rig/`
    folder. Partner path swaps `source` ↔ `rig` and reuses the active
    scene's filename — `michael.ma ↔ michael.ma`.

    Raises RuntimeError when the active scene isn't in a `source/` or
    `rig/` folder, or when it isn't saved to disk at all.
    """
    scene_path = cmds.file(query=True, sceneName=True) or ''
    if not scene_path:
        raise RuntimeError(
            'Rigged Model Updater: active scene is unsaved. Save into a '
            '<character>/source/ or <character>/rig/ folder first.'
        )
    p = Path(scene_path)
    parent_name = p.parent.name.lower()
    if parent_name == 'source':
        partner = p.parent.parent / 'rig' / p.name
        return (_DIR_SOURCE_TO_RIG, str(p), str(partner))
    if parent_name == 'rig':
        partner = p.parent.parent / 'source' / p.name
        return (_DIR_RIG_TO_SOURCE, str(p), str(partner))
    raise RuntimeError(
        f'Rigged Model Updater: active scene parent folder is {parent_name!r}, '
        f'expected "source" or "rig". Path: {scene_path}'
    )


# ─── Source-side helpers ────────────────────────────────────────────────────


def _meshes_under_geo_grp() -> list:
    """Return all mesh transforms (parents of nurbsCurve/mesh shapes) that
    live under `geo_grp`. Empty list when geo_grp doesn't exist."""
    if not cmds.objExists('geo_grp'):
        return []
    descendants = cmds.listRelatives('geo_grp', allDescendents=True,
                                      type='transform', fullPath=True) or []
    meshes = []
    for t in descendants:
        shapes = cmds.listRelatives(t, shapes=True, type='mesh',
                                     fullPath=True) or []
        if shapes:
            meshes.append(t)
    return meshes


def _validate_source_geo() -> None:
    """Raise RuntimeError when the active source scene lacks a `geo_grp`
    at world root with at least one mesh descendant."""
    if not cmds.objExists('geo_grp'):
        raise RuntimeError(
            'Rigged Model Updater: source scene has no `geo_grp` at world '
            'root. Place all source meshes under a top-level group named '
            'geo_grp and try again.'
        )
    parents = cmds.listRelatives('geo_grp', parent=True) or []
    if parents:
        raise RuntimeError(
            f'Rigged Model Updater: `geo_grp` is not at world root '
            f'(parent: {parents[0]!r}). Unparent it and try again.'
        )
    if not _meshes_under_geo_grp():
        raise RuntimeError(
            'Rigged Model Updater: `geo_grp` has no mesh descendants. '
            'Nothing to export.'
        )


def _capture_source_material_names() -> set:
    """Return the set of material node names assigned to meshes under
    `geo_grp` via shadingEngine.surfaceShader. Used by the rig-side
    verification step to confirm no `1`-suffix renames slipped in."""
    names = set()
    for mesh in _meshes_under_geo_grp():
        shapes = cmds.listRelatives(mesh, shapes=True, type='mesh',
                                     fullPath=True) or []
        for shape in shapes:
            sgs = cmds.listConnections(shape, type='shadingEngine') or []
            for sg in sgs:
                mats = cmds.listConnections(f'{sg}.surfaceShader',
                                             source=True, destination=False) or []
                for mat in mats:
                    names.add(mat.split('|')[-1])
    return names


def _export_geo_intermediate(temp_path: str) -> None:
    """Select `geo_grp` and exportSelected to Maya ASCII at temp_path.

    Flags strip skinClusters (constructionHistory=False), anim/constraints/
    expressions, and prevent the export from following references. Materials
    + SG assignments DO come along (no shader=False) — that's how the source
    pushes its assignments to the rig.
    """
    cmds.select('geo_grp', replace=True)
    cmds.file(
        temp_path,
        exportSelected=True,
        type='mayaAscii',
        constructionHistory=False,
        channels=False,
        constraints=False,
        expressions=False,
        preserveReferences=False,
        force=True,
    )


# ─── Rig-side destructive helpers ───────────────────────────────────────────


def _save_weights(meshes: list, weights_path: str) -> tuple:
    """Save skin weights for `meshes` to `weights_path`. Returns
    (skinned, unskinned) lists. Wraps skin_io's RuntimeError on unskinned
    meshes so the flow can continue with the skinned subset."""
    from maya_tools.skinning.skin_io import skin_io_app

    if not meshes:
        return ([], [])
    # skin_io.save_skin_from_meshes raises RuntimeError listing all unskinned
    # meshes; we want to partition + continue with skinned ones.
    skinned = []
    unskinned = []
    io = skin_io_app.JsonSkinIO()
    for m in meshes:
        shape = io._get_mesh_shape(m)
        if shape and io._find_skin_cluster(shape):
            skinned.append(m)
        else:
            unskinned.append(m)
    if skinned:
        skin_io_app.save_skin_from_meshes(skinned, weights_path)
    return (skinned, unskinned)


def _delete_geo_grp() -> None:
    """Delete the rig's `geo_grp` and its descendants. No-op when absent."""
    if cmds.objExists('geo_grp'):
        cmds.delete('geo_grp')


def _scrub_unused_materials() -> None:
    """Run Maya's MLdeleteUnused MEL command to delete shading networks
    that no longer reference any geometry. Run after `_delete_geo_grp` so
    the rig's now-orphaned materials are cleared before the source import
    lands — that's what prevents the `M_Michael_Gold1` suffix on collision."""
    import maya.mel as mel
    mel.eval('MLdeleteUnused;')


# ─── Rig-side constructive helpers ──────────────────────────────────────────


def _import_geo_intermediate(temp_path: str) -> list:
    """Import the source-exported .ma into the current rig. Returns the
    list of new top-level transforms (geo_grp + anything else that came
    along, though `exportSelected` should have stripped extras)."""
    return cmds.file(
        temp_path,
        i=True,                       # import
        type='mayaAscii',
        namespace=':',                # root namespace
        mergeNamespacesOnClash=False,
        ignoreVersion=True,
        returnNewNodes=True,
    ) or []


def _verify_material_names(expected: set) -> list:
    """Check that every captured source material name landed in the rig
    with its original name (no `1`/`2` suffix from collision). Returns a
    list of human-readable issues; empty list = clean."""
    issues = []
    for name in sorted(expected):
        if not cmds.objExists(name):
            # Look for suffix variants that indicate a collision rename.
            variants = [n for n in (cmds.ls(f'{name}*') or [])
                        if n != name and n.startswith(name)]
            if variants:
                issues.append(
                    f'{name!r}: missing, collision variants present: '
                    f'{variants}'
                )
            else:
                issues.append(f'{name!r}: missing post-import')
    return issues


def _find_rig_top_group() -> str:
    """Resolve the rig's top group by walking up from a root joint via
    rig_binding's live bindings. Returns '' when no skeleton exists yet
    (skeleton-only pre-build, or fresh empty rig file). Resolving via
    parent-of-root-joint sidesteps the legacy-naming case where the top
    group is named differently from the current scene file (e.g.,
    `biped_male` on a rig built before the scene-name-derived top group
    landed)."""
    from maya_tools.utils.maya import rig_binding
    bindings = rig_binding.find_live_bindings() or []
    for b in bindings:
        root = rig_binding.get_root_joint(b)
        if root and cmds.objExists(root):
            parents = cmds.listRelatives(root, parent=True, fullPath=False) or []
            if parents:
                return parents[0]
    return ''


def _reparent_geo_grp_to_top() -> str:
    """Parent `geo_grp` under the rig's top group, if one exists. Returns
    the top group name on success, '' when no top group exists (geo_grp
    is left at world root for `build_modules` to adopt later)."""
    if not cmds.objExists('geo_grp'):
        return ''
    top = _find_rig_top_group()
    if not top:
        return ''
    current_parent = cmds.listRelatives('geo_grp', parent=True) or []
    if current_parent and current_parent[0] == top:
        return top  # already correctly parented
    cmds.parent('geo_grp', top)
    return top


def _load_weights(meshes: list, weights_path: str) -> None:
    """Load saved weights onto `meshes` via skin_io transfer mode.
    Transfer tolerates vert-count changes (re-topo'd source meshes still
    get plausible weights). No-op when no meshes."""
    from maya_tools.skinning.skin_io import skin_io_app
    if not meshes:
        return
    skin_io_app.load_skin_to_meshes(meshes, weights_path, mode='transfer')


def _confirm_proceed_with_unsaved_changes(direction_label: str) -> bool:
    """Prompt the user when the active scene has unsaved changes.

    Save / Don't Save / Cancel:
    - Save commits the current scene before the flow runs.
    - Don't Save proceeds, losing unsaved changes.
    - Cancel aborts the flow.

    Returns True to proceed, False to abort. This is the ONLY place the
    tool writes to disk (via the user's Save button); every other
    destructive op stays in-memory so a botched run can be rolled back
    via File > Revert.
    """
    if not cmds.file(query=True, modified=True):
        return True
    result = cmds.confirmDialog(
        title=f'Rigged Model Updater — {direction_label}',
        message=(
            'The current scene has unsaved changes.\n\n'
            'Any unsaved work will be lost when this tool runs.\n\n'
            'Save before continuing?'
        ),
        button=['Save', "Don't Save", 'Cancel'],
        defaultButton='Save',
        cancelButton='Cancel',
        dismissString='Cancel',
        icon='warning',
    )
    if result == 'Cancel':
        om.MGlobal.displayInfo('[ModelUpdater] Cancelled by user.')
        return False
    if result == 'Save':
        cmds.file(save=True)
    return True


# ─── Source → Rig flow ──────────────────────────────────────────────────────


def _run_source_to_rig(source_path: str, rig_path: str) -> None:
    """Push source geo + materials into the rig file, preserving rig
    structure (skeleton + Fabricator nodes) and rebinding skin weights
    via transfer mode. Restores the built state if the rig was built
    before the push.

    Pre-requisites: caller has already validated that the active scene
    IS source_path (we don't re-open source here)."""
    import tempfile
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator.ui import state as ks_state

    om.MGlobal.displayInfo(
        f'[ModelUpdater] source→rig: {source_path} → {rig_path}'
    )

    if not _confirm_proceed_with_unsaved_changes('source → rig'):
        return

    # 1. Validate the active (source) scene.
    _validate_source_geo()

    # 2. Capture source material names for the post-import verify.
    source_material_names = _capture_source_material_names()

    # 3. Export geo intermediate to temp dir.
    tmp_dir = tempfile.mkdtemp(prefix='ks_modelupdate_')
    try:
        temp_geo = str(Path(tmp_dir) / 'geo_intermediate.ma')
        temp_weights = str(Path(tmp_dir) / 'weights.json')
        _export_geo_intermediate(temp_geo)
        om.MGlobal.displayInfo(f'[ModelUpdater] Exported geo: {temp_geo}')

        # 4. Open the rig (or set up a fresh in-memory scene targeting rig_path).
        rig_exists = Path(rig_path).is_file()
        if rig_exists:
            cmds.file(rig_path, open=True, force=True)
        else:
            cmds.file(new=True, force=True)
            Path(rig_path).parent.mkdir(parents=True, exist_ok=True)
            cmds.file(rename=rig_path)
            # No save — user reviews and saves manually at the end.

        # 5. Detect built state, cache.
        was_built = (ks_state.detect_mode() == ks_state.MODE_MODULES_BUILT)

        # 6. Unbuild if built.
        if was_built:
            fs_app.unbuild_modules()
            om.MGlobal.displayInfo('[ModelUpdater] Unbuilt rig for update.')

        # 7. Save skin weights from rig's existing geo_grp meshes.
        rig_meshes_before = _meshes_under_geo_grp()
        skinned, unskinned = _save_weights(rig_meshes_before, temp_weights)
        if unskinned:
            om.MGlobal.displayWarning(
                f'[ModelUpdater] {len(unskinned)} unskinned mesh(es) in rig '
                f'pre-update — weights not saved for: '
                f'{[m.split("|")[-1] for m in unskinned]}'
            )

        # 8. Delete rig's geo_grp.
        _delete_geo_grp()

        # 9. Scrub orphaned materials — clears name collisions BEFORE import.
        _scrub_unused_materials()

        # 10. Import source intermediate.
        _import_geo_intermediate(temp_geo)
        om.MGlobal.displayInfo('[ModelUpdater] Imported source geo.')

        # 11. Verify material names held up.
        issues = _verify_material_names(source_material_names)
        if issues:
            om.MGlobal.displayWarning(
                f'[ModelUpdater] {len(issues)} material name issue(s) after '
                f'import — check before shipping:'
            )
            for line in issues:
                om.MGlobal.displayWarning(f'    {line}')

        # 12. Reparent geo_grp into the rig's top group, if one exists.
        top = _reparent_geo_grp_to_top()
        if top:
            om.MGlobal.displayInfo(
                f'[ModelUpdater] Reparented geo_grp under {top!r}.'
            )

        # 13. Load weights onto the imported meshes (transfer mode).
        rig_meshes_after = _meshes_under_geo_grp()
        # Only load for meshes whose short name was in the saved set.
        saved_short_names = {m.split('|')[-1] for m in skinned}
        load_targets = [m for m in rig_meshes_after
                        if m.split('|')[-1] in saved_short_names]
        if load_targets:
            _load_weights(load_targets, temp_weights)
            om.MGlobal.displayInfo(
                f'[ModelUpdater] Loaded weights onto '
                f'{len(load_targets)} mesh(es).'
            )

        # 14. Defensive second scrub for any shaders that hitched a ride.
        _scrub_unused_materials()

        # 15. Rebuild if it was built coming in.
        if was_built:
            fs_app.build_modules()
            om.MGlobal.displayInfo('[ModelUpdater] Rebuilt rig.')

        # 16. Done — user reviews and saves manually.
        om.MGlobal.displayInfo(
            f'[ModelUpdater] Update applied to {rig_path} '
            f'— review and save manually. '
            f'({len(rig_meshes_after)} mesh(es), '
            f'{len(load_targets)} weight set(s), '
            f'{len(issues)} material issue(s))'
        )
    finally:
        # Always clean the temp dir, even on exception.
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ─── Rig → Source flow ──────────────────────────────────────────────────────


def _delete_history_on_geo_meshes() -> None:
    """Delete construction history on every mesh under `geo_grp`. Nukes
    skinClusters + tweak nodes — source files have unskinned meshes by
    convention."""
    for mesh in _meshes_under_geo_grp():
        try:
            cmds.delete(mesh, constructionHistory=True)
        except Exception as exc:
            cmds.warning(
                f'[ModelUpdater] Delete history failed on {mesh!r}: {exc}. '
                f'Continuing.'
            )


def _strip_rig_artifacts() -> None:
    """Delete every Fabricator artifact so the saved file is a clean
    modeling scene. Order is deliberate — descendants first, parents
    later, so we don't get `node already deleted` warnings."""
    from maya_tools.rigging.fabricator import nodes as fab_nodes
    from maya_tools.utils.maya import rig_binding

    # 1. Root joint hierarchy. Resolve via live bindings (handles renames).
    for b in (rig_binding.find_live_bindings() or []):
        root = rig_binding.get_root_joint(b)
        if root and cmds.objExists(root):
            cmds.delete(root)

    # 2. Top group (if any survived — should be empty after geo_grp moved
    #    + skeleton deleted, but defensive in case the user authored extras
    #    under it that aren't ours to delete; only nuke when empty).
    #    The top group has no canonical name; walk world-root transforms
    #    looking for one that has zero children AND isn't geo_grp.
    for top in (cmds.ls(assemblies=True, transforms=True) or []):
        if top == 'geo_grp':
            continue
        if cmds.listRelatives(top, children=True) or []:
            continue
        # Skip Maya defaults (perspShape parents, etc.) by checking it has
        # no children — empty user transforms are fair game.
        if top in ('persp', 'top', 'front', 'side'):
            continue
        cmds.delete(top)

    # 3. All fab_* network nodes.
    for cnode in list(fab_nodes.get_all_component_nodes()):
        try:
            fab_nodes.delete_component_node(cnode)
        except Exception:
            pass
    try:
        fab_nodes.delete_registry()
    except Exception:
        pass
    try:
        fab_nodes.delete_guides_grp()
    except Exception:
        pass
    try:
        fab_nodes.delete_pivots_grp()
    except Exception:
        pass

    # 4. FAB_RigBinding_* network nodes.
    for n in (cmds.ls('FAB_RigBinding_*', type='network') or []):
        if cmds.objExists(n):
            cmds.delete(n)

    # 5. _Joints / _Geo display layers.
    for layer in ('_Joints', '_Geo'):
        if cmds.objExists(layer):
            cmds.delete(layer)


def _run_rig_to_source(rig_path: str, source_path: str) -> None:
    """Strip everything Fabricator-related and save the clean unskinned
    geo as the source modeling file. Pre-requisites: caller has already
    validated that the active scene IS rig_path."""
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator.ui import state as ks_state

    om.MGlobal.displayInfo(
        f'[ModelUpdater] rig→source: {rig_path} → {source_path}'
    )

    if not _confirm_proceed_with_unsaved_changes('rig → source'):
        return

    # 1. Unbuild if built.
    if ks_state.detect_mode() == ks_state.MODE_MODULES_BUILT:
        fs_app.unbuild_modules()
        om.MGlobal.displayInfo('[ModelUpdater] Unbuilt rig.')

    # 2. Move geo_grp to world root.
    if cmds.objExists('geo_grp'):
        parents = cmds.listRelatives('geo_grp', parent=True) or []
        if parents:
            cmds.parent('geo_grp', world=True)

    # 3. Delete construction history on every mesh (nukes skinClusters).
    _delete_history_on_geo_meshes()

    # 4. Strip rig artifacts.
    _strip_rig_artifacts()

    # 5. Rename target so the user's next Save lands at source_path — no save here.
    Path(source_path).parent.mkdir(parents=True, exist_ok=True)
    cmds.file(rename=source_path)
    om.MGlobal.displayInfo(
        f'[ModelUpdater] Update applied to {source_path} '
        f'— review and save manually.'
    )


def update_rigged_model() -> None:
    """Public entry point. Detects direction from the active scene path
    and dispatches to the source→rig or rig→source flow."""
    direction, source_path, partner_path = detect_direction()
    if direction == _DIR_SOURCE_TO_RIG:
        _run_source_to_rig(source_path, partner_path)
    elif direction == _DIR_RIG_TO_SOURCE:
        _run_rig_to_source(source_path, partner_path)
    else:
        raise RuntimeError(
            f'Rigged Model Updater: unexpected direction {direction!r}.'
        )
