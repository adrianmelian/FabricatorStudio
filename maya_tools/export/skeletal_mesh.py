"""Skeletal mesh export — KS-aware.

For KS rigs (a fab_registry in the scene): the export runs in a THROWAWAY
mayapy subprocess (skeletal_pipeline / skeletal_export_runner). The subprocess
opens the saved scene, disconnects skins, breaks the Armature and orients the
joints to the game contract, strips to joints + mesh, reconnects the skins at
the final pose, and writes the FBX — then exits. The user's interactive Maya is
never touched, so nothing is restored: the fragile in-scene unbuild/rebuild/undo
dance (and the delete-then-recreate-Armature step) simply disappears. Requires
the scene saved; the KS path force-saves first (Adrian, 2026-07-13: saving on
export is good hygiene).

For non-KS rigs (legacy / hand-built, no registry): the small in-scene surgery
stays — wrapped in an undo chunk and undone after export, leaving the artist's
scene exactly as it was. Those rigs have no Armature/orient lifecycle and don't
hit the ribbon candy-wrapper the subprocess path was built to cure.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds
import maya.mel as mel

from maya_tools.export import export_core


def export_skeletal(joints: list[str], meshes: list[str],
                    out_path: str, fbx_preset: dict,
                    engine_up_axis: str = 'y',
                    format: str = 'fbx',
                    character_name: str = '') -> str:
    """Export joints + skinned meshes to a single FBX or delivery USD.

    KS rigs go through the throwaway subprocess (never touches the live scene);
    non-KS rigs use the small in-scene, undo-restored path. The rig is exported
    at its current (rest) state — no frame seeking.

    engine_up_axis: 'y' (Maya-native, no conversion) or 'z' (Z-up engines,
    e.g. Unreal) — 'z' folds a -90° world-X rotation into the root joint's
    frame so the engine's import lands the root bone at identity (see
    export_core.orient_root_for_z_up_engine). Resolve via
    export_core.engine_up_axis(config, override) — callers pass the
    resolved value, not the raw config. FBX only: the USD stage's upAxis
    metadata is the single axis mechanism.

    format='usd' writes the Armature/Unreal delivery USD. ALWAYS routed
    through the subprocess path, registry or not — the runner no-ops the
    orient for registry-less scenes and the USD post-pass needs the pxr
    environment the subprocess guarantees."""
    _write_rig_binding(joints, meshes)
    if not joints:
        raise RuntimeError("export_skeletal called with no joints.")
    if not meshes:
        raise RuntimeError("export_skeletal called with no meshes.")

    missing = [n for n in joints + meshes if not cmds.objExists(n)]
    if missing:
        raise RuntimeError(f"Skeletal export: missing nodes: {', '.join(missing)}")

    if format == 'usd' or _ks_rig_present():
        _export_ks_subprocess(joints, meshes, out_path, fbx_preset,
                              engine_up_axis, format=format,
                              character_name=character_name)
    else:
        _export_legacy_inscene(joints, meshes, out_path, fbx_preset,
                               engine_up_axis)

    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# KS path
# ─────────────────────────────────────────────────────────────────────────────

def _ks_rig_present() -> bool:
    """True if a v2 fab_registry network node exists in the scene."""
    try:
        from maya_tools.rigging.fabricator import nodes
        return bool(nodes.get_registry())
    except Exception:
        return False


def _export_ks_subprocess(joints, meshes, out_path, fbx_preset,
                          engine_up_axis='y', format='fbx',
                          character_name=''):
    """KS rig: export in a throwaway mayapy subprocess.

    The subprocess opens the SAVED scene, disconnects skins, breaks the
    Armature and orients the joints to the game contract, strips to joints +
    mesh, reconnects the skins at the final pose, and writes the FBX — then
    exits (skeletal_pipeline / skeletal_export_runner). The live scene is never
    touched, so none of it needs restoring: no unbuild/rebuild, no undo, no
    delete-then-recreate the Armature.

    Requires the scene saved; force-saves first so the subprocess sees the
    current state (including the rig binding just written by export_skeletal).
    Adrian, 2026-07-13: saving on export is good hygiene — force it."""
    scene = cmds.file(q=True, sn=True)
    if not scene:
        raise RuntimeError(
            "Save the scene before exporting a rig — the export runs in a "
            "subprocess against the saved file.")
    if cmds.file(q=True, modified=True):
        cmds.file(save=True)

    from maya_tools.export import skeletal_pipeline
    skeletal_pipeline.run_export(joints, meshes, out_path, fbx_preset,
                                 engine_up_axis=engine_up_axis,
                                 format=format,
                                 character_name=character_name)


def _export_legacy_inscene(joints, meshes, out_path, fbx_preset,
                           engine_up_axis='y'):
    """Non-KS (hand-built) rig: small in-scene surgery, undone after export so
    the artist's scene is left exactly as it was; selection + current frame
    restored. Scrubs to frame 0 (the default T-pose) for the writeout, so a rig
    file that carries a range-of-motion anim still exports its rest pose. (KS
    rigs go through the subprocess pipeline instead — see _export_ks_subprocess.)"""
    selection_backup = cmds.ls(sl=True) or []
    time_backup = cmds.currentTime(q=True)
    try:
        cmds.currentTime(0)   # the default T-pose (raw ROM anims start on f1)
        _export_legacy(joints, meshes, out_path, fbx_preset, engine_up_axis)
    finally:
        cmds.currentTime(time_backup)
        if selection_backup:
            try:
                cmds.select(selection_backup, replace=True)
            except Exception:
                cmds.select(clear=True)
        else:
            cmds.select(clear=True)


def _undo_recording_on() -> bool:
    """Force undo recording ON if the user has it globally disabled, so
    the strip's deletions are guaranteed reversible. With undo off,
    openChunk/undo are silent NO-OPS — the deletes would be permanent and
    cmds.undo() would not even raise. stateWithoutFlush preserves the
    user's existing queue. Returns whether undo was already on."""
    was_on = cmds.undoInfo(q=True, state=True)
    if not was_on:
        cmds.undoInfo(stateWithoutFlush=True)
    return was_on


# ─────────────────────────────────────────────────────────────────────────────
# Legacy / hand-built rig path
# ─────────────────────────────────────────────────────────────────────────────

def _export_legacy(joints, meshes, out_path, fbx_preset, engine_up_axis='y'):
    """Mutate scene, export, then close chunk and undo to restore.

    The chunk MUST be closed before cmds.undo() — otherwise undo only pops the
    latest operation in the chunk instead of the whole chunk.
    """
    undo_was_on = _undo_recording_on()
    cmds.undoInfo(openChunk=True, chunkName="Export Skeletal (legacy)")
    try:
        root = joints[0]
        skeleton_chain = _gather_chain(root)
        _delete_constraints_on_chain(skeleton_chain)
        _cut_keys_on_chain(skeleton_chain)
        # Aimers are ours even on hand-built rigs (Joint Orient runs
        # without a fab_registry): their per-joint localRef locators must
        # not ride into the FBX. ONLY aimer refs are stripped here — a
        # hand-built skeleton's own under-joint locators (sockets etc.)
        # are the artist's, not ours to remove.
        aimer_refs = [t for t in export_core.non_joint_helpers_under(skeleton_chain)
                      if t.split('|')[-1].endswith('_localRef_LOC')]
        if aimer_refs:
            cmds.delete(aimer_refs)
        _unparent_to_world(root)
        if engine_up_axis == 'z':
            # The conversion MOVES the root's frame, so live skins must go
            # dormant around it and re-bind at the converted pose — the
            # same bracket the subprocess runner uses. All inside the undo
            # chunk, so the artist's scene restores untouched.
            from maya_tools.skinning import skin_connect_app
            skin_connect_app.disconnect_all_skins()
            export_core.orient_root_for_z_up_engine(root)
            skin_connect_app.reset_all_binds_to_pose()
        _fbx_export_selected([root] + meshes, out_path, fbx_preset)
    finally:
        cmds.undoInfo(closeChunk=True)
        try:
            cmds.undo()
        except Exception:
            cmds.warning("[Exporter] Failed to undo skeletal export surgery; scene may need manual reset.")
        if not undo_was_on:
            cmds.undoInfo(stateWithoutFlush=False)


def _gather_chain(root: str) -> list[str]:
    chain = [root] + (cmds.listRelatives(root, allDescendents=True, type='joint', fullPath=True) or [])
    return list(dict.fromkeys(chain))


def _delete_constraints_on_chain(joints: list[str]) -> None:
    constraints = []
    for j in joints:
        children = cmds.listRelatives(j, children=True, type='constraint', fullPath=True) or []
        constraints.extend(children)
    if constraints:
        cmds.delete(constraints)


def _cut_keys_on_chain(joints: list[str]) -> None:
    for j in joints:
        try:
            cmds.cutKey(j, clear=True)
        except Exception:
            pass


def _unparent_to_world(root: str) -> None:
    if cmds.listRelatives(root, parent=True):
        cmds.parent(root, world=True)


# ─────────────────────────────────────────────────────────────────────────────
# Shared FBX writeout
# ─────────────────────────────────────────────────────────────────────────────

def _fbx_export_selected(nodes: list[str], out_path: str, fbx_preset: dict) -> None:
    export_core.apply_fbx_preset(fbx_preset)
    cmds.select(nodes, replace=True)
    mel.eval('FBXExport -f "{}" -s;'.format(out_path.replace('\\', '/')))


# ─────────────────────────────────────────────────────────────────────────────
# Rig binding writeup (read by anim exporter)
# ─────────────────────────────────────────────────────────────────────────────

def _write_rig_binding(joints: list[str], meshes: list[str]) -> str:
    """Capture the full joint hierarchy + the rig's null/control groups onto
    FAB_RigBinding. Idempotent — refreshes an existing binding in place.

    Returns the binding node name.
    """
    if not joints:
        raise RuntimeError('_write_rig_binding: no joints provided')
    if not meshes:
        raise RuntimeError('_write_rig_binding: no meshes provided')

    root = joints[0]
    if not cmds.objExists(root):
        raise RuntimeError(f'_write_rig_binding: root joint does not exist: {root}')

    # Full joint hierarchy from root down — same skeleton UE5 sees in SK_
    descendants = cmds.listRelatives(root, allDescendents=True, type='joint',
                                     fullPath=True) or []
    chain: list[str] = []
    seen: set[str] = set()
    for j in [root] + descendants:
        long_name = cmds.ls(j, long=True)
        key = long_name[0] if long_name else j
        if key not in seen:
            seen.add(key)
            chain.append(j)

    # Resolve the rig's drive-hierarchy groups within the root joint's namespace.
    # Matches lowercase fab_nulls_grp / fab_controls_grp (current convention) and
    # also the legacy *_Nulls_grp / *_Controls_grp suffix patterns for any
    # hand-built rigs still using mixed-case naming.
    short_root = root.split('|')[-1]
    namespace  = short_root.rsplit(':', 1)[0] if ':' in short_root else ''
    nulls_grp    = (_find_rig_group(namespace, 'nulls_grp')
                    or _find_rig_group(namespace, '_Nulls_grp'))
    controls_grp = (_find_rig_group(namespace, 'controls_grp')
                    or _find_rig_group(namespace, '_Controls_grp'))

    from maya_tools.utils.maya import rig_binding
    return rig_binding.write_rig_binding(
        rig_label     = rig_binding.derive_rig_label(root),
        root_joint    = root,
        export_joints = chain,
        nulls_grp     = nulls_grp,
        controls_grp  = controls_grp,
    )


def _find_rig_group(namespace: str, suffix: str) -> str | None:
    """Find a transform whose name ends in suffix, scoped to the rig's namespace.
    Returns None if no match. If multiple match, returns the first.
    """
    if namespace:
        pattern = f'{namespace}:*{suffix}'
    else:
        pattern = f'*{suffix}'
    matches = cmds.ls(pattern, long=True, type='transform') or []
    return matches[0] if matches else None
