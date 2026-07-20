# Python/maya_tools/export/anim_export_runner.py
"""Anim exporter — mayapy subprocess runner.

Runs in mayapy.exe (separate process). Loads the scene, runs the export
pipeline, exits. No UI, no caller communication beyond exit code +
stdout/stderr. Single-shot per invocation.

Pipeline order (matches the artist's verified manual workflow):
  1. Bake R+T+S on binding joints (constraints active, refs intact, namespaces intact)
  2. Import all references
  3. Unbuild rig (KS unbuild_rig() OR delete *_Nulls_grp / *_Controls_grp)
  4. FBX export selected joints
  5. Exit

Args (single JSON string on argv[1]):
    {
        "scene_path": "...",
        "clip_data": {...},
        "out_path": "...",
        "config": {...},
        "repo_python_root": "..."  # added to sys.path so maya_tools.export imports work
    }
"""
__author__ = "Adrian Melian"

import json
import sys
import traceback


def main() -> None:
    payload = json.loads(sys.argv[1])
    sys.path.insert(0, payload['repo_python_root'])

    scene_path = payload['scene_path']
    clip_data = payload['clip_data']
    out_path = payload['out_path']
    config = payload['config']

    # Initialize Maya standalone before importing maya.cmds
    print('[runner] initializing Maya standalone')
    import maya.standalone
    maya.standalone.initialize(name='python')

    import maya.cmds as cmds
    cmds.loadPlugin('fbxmaya', quiet=True)

    print(f'[runner] opening scene: {scene_path}')
    cmds.file(scene_path, open=True, force=True)

    from maya_tools.export import export_core
    from maya_tools.utils.maya import rig_binding

    # Resolve binding joints (with namespace prefixes, pre-import)
    pre_import_joints: list[str] = []
    for binding in clip_data['rigs']:
        if not cmds.objExists(binding):
            raise RuntimeError(f'Binding node missing in scene: {binding}')
        bdata = rig_binding.get_binding_data(binding)
        for j in bdata['export_joints']:
            if cmds.objExists(j):
                pre_import_joints.append(j)
            else:
                cmds.warning(f'[runner] joint missing pre-import: {j}')

    if not pre_import_joints:
        raise RuntimeError('No joints resolved from binding.')

    start = int(clip_data['frame_start'])
    end = int(clip_data['frame_end'])

    # Step 0: Transfer root motion to world ctrls if any rig has it enabled.
    # Runs BEFORE the joint bake so the bake captures world-ctrl-driven motion
    # on the root joint and decomposes hip-and-below into local-residual.
    print('[runner] checking root motion settings')
    _transfer_root_motion_if_enabled(clip_data, start, end)

    # Step 1: Bake R+T+S on all binding joints (refs + namespaces intact).
    # disableImplicitControl=True severs constraint outputs from joint plugs
    # so post-bake the joints are driven only by their anim curves.
    print(f'[runner] baking {len(pre_import_joints)} joints over frames {start}-{end}')
    cmds.bakeResults(
        pre_import_joints,
        simulation=True,
        time=(start, end),
        sampleBy=1,
        oversamplingRate=1,
        disableImplicitControl=True,
        preserveOutsideKeys=True,
        sparseAnimCurveBake=False,
        removeBakedAttributeFromLayer=False,
        removeBakedAnimFromLayer=False,
        bakeOnOverrideLayer=False,
        minimizeRotation=True,
        controlPoints=False,
        shape=True,
    )

    # Step 2: Import all references — rigs become local nodes
    print('[runner] importing all references')
    _import_all_references()

    # Step 3: Re-resolve joints (still namespaced, but namespace is now a
    # local prefix rather than a reference scope)
    joints_to_export = _resolve_joints_post_import(pre_import_joints)
    if not joints_to_export:
        raise RuntimeError('No joints resolved after reference import.')

    # Step 4: Unbuild the rig — delete the nulls_grp and controls_grp captured
    # on each rig binding. The bake above already severed constraint outputs
    # from joint plugs (disableImplicitControl=True), so deleting the groups
    # just cleans up the now-redundant constraint nodes.
    print('[runner] deleting rig groups')
    _delete_rig_groups(clip_data)

    # Step 5: Unparent each rig's root joint to world so the FBX skeleton
    # starts at the root joint — without this, the rig's top group (e.g.
    # 'michael' for Michael's rig) becomes an unwanted extra parent node
    # in the exported FBX.
    print('[runner] unparenting root joints to world')
    _unparent_roots_to_world(clip_data)

    # Long paths shifted after unparent — re-resolve before select/export.
    joints_to_export = _resolve_joints_post_import(pre_import_joints)
    if not joints_to_export:
        raise RuntimeError('No joints resolved after unparenting roots.')

    # Strip helper transforms living under the export joints (aimer
    # localRef locators, stray nulls) — FBXExport -s takes each selected
    # joint's whole subtree, the same leak the skeletal exporter strips.
    # This scene is a throwaway subprocess copy (never saved), so plain
    # deletion needs no restore.
    helpers = export_core.non_joint_helpers_under(joints_to_export)
    if helpers:
        print(f'[runner] stripping {len(helpers)} helper node(s) under export joints')
        cmds.delete(helpers)

    # Step 5.5: Z-up engine target (Unreal) — fold the axis conversion into
    # each rig's root joint frame ON EVERY FRAME of the clip (the joints are
    # baked, so the root and its direct children are re-keyed; the visible
    # animation doesn't move). This must match how the SK_ mesh was exported
    # (same engine_up_axis) or the clip plays rotated on the skeleton.
    # anim_app resolved the effective value (UI override > project config).
    engine_axis = clip_data.get('engine_up_axis', 'y')
    if engine_axis == 'z':
        print('[runner] engine up axis = z — folding -90 deg world-X into '
              'root frames across the clip')
        _orient_roots_for_z_up(clip_data, start, end)
        # Re-resolve: xform/keying doesn't change paths, but stay consistent
        # with the post-surgery discipline above.
        joints_to_export = _resolve_joints_post_import(pre_import_joints)

    # Step 6: FBX export selected joints
    print('[runner] applying FBX preset')
    # config is None for out-of-project scenes — fbx_preset falls back to
    # the built-in defaults.
    export_core.apply_fbx_preset(export_core.fbx_preset(config, 'animation'))

    print(f'[runner] exporting FBX: {out_path}')
    cmds.select(joints_to_export, replace=True)
    import maya.mel as mel
    mel.eval('FBXExport -f "{}" -s;'.format(out_path.replace('\\', '/')))

    print(f'[runner] exported: {out_path}')


# ─────────────────────────────────────────────────────────────────────────────
# Internals (lazy maya.cmds imports — only safe after standalone.initialize)
# ─────────────────────────────────────────────────────────────────────────────

def _import_all_references() -> None:
    """Import every reference, retrying until none remain. Failed imports
    are tracked so we don't loop forever on a permanently-broken ref."""
    import maya.cmds as cmds
    seen_failed: set = set()
    while True:
        refs = cmds.ls(type='reference') or []
        importable = []
        for ref in refs:
            try:
                if cmds.referenceQuery(ref, isLoaded=True):
                    fn = cmds.referenceQuery(ref, filename=True)
                    if fn not in seen_failed:
                        importable.append(fn)
            except Exception:
                pass
        if not importable:
            break
        for ref_file in importable:
            try:
                cmds.file(ref_file, importReference=True)
            except Exception as exc:
                cmds.warning(f'[runner] could not import reference {ref_file}: {exc}')
                seen_failed.add(ref_file)


def _resolve_joints_post_import(pre_import_joints: list) -> list:
    """Joints retain their names (with namespace prefix) after import — the
    namespace becomes a local-name prefix rather than a reference scope.
    Look them up in the current scene to get fresh long paths."""
    import maya.cmds as cmds
    out = []
    seen = set()
    for j in pre_import_joints:
        if cmds.objExists(j):
            long_name = cmds.ls(j, long=True)
            if long_name and long_name[0] not in seen:
                seen.add(long_name[0])
                out.append(long_name[0])
            continue
        # Fallback: try short-name match if the namespace got dropped
        short = j.split('|')[-1].split(':')[-1]
        matches = cmds.ls(short, type='joint', long=True) or []
        for m in matches:
            if m not in seen:
                seen.add(m)
                out.append(m)
    return out


def _delete_rig_groups(clip_data: dict) -> None:
    """Delete the nulls_grp and controls_grp captured on each rig binding.

    Single source of truth — the binding stored these node references at
    SK_-export time. After namespaces and references are imported, the
    message connections still resolve to the (now-local) nodes.
    """
    import maya.cmds as cmds
    from maya_tools.utils.maya import rig_binding

    deleted: list = []
    for binding in clip_data['rigs']:
        if not cmds.objExists(binding):
            continue
        bdata = rig_binding.get_binding_data(binding)
        for key in ('nulls_grp', 'controls_grp'):
            grp = bdata.get(key) or ''
            if grp and cmds.objExists(grp):
                try:
                    cmds.delete(grp)
                    deleted.append(grp)
                except Exception as exc:
                    cmds.warning(f'[runner] failed to delete {grp}: {exc}')
            elif not grp:
                cmds.warning(
                    f'[runner] binding {binding!r} has no {key!r} connection — '
                    f'rig may be incomplete in FBX. Re-run SK_ export to populate.'
                )
    if deleted:
        print(f'[runner] deleted rig groups: {deleted}')


def _orient_roots_for_z_up(clip_data: dict, start: int, end: int) -> None:
    """Fold the Z-up engine conversion into each rig's root joint across
    the baked clip (export_core.orient_root_for_z_up_engine with a frame
    range). Runs AFTER the roots are unparented to world.

    The conversion is target-based and needs the root's REST orientation —
    a posed clip can't supply it, so it comes from the Fabricator
    registry's bind-pose snapshot (world_m, captured at build). Legacy
    scenes without the snapshot fall back to sampling the clip's first
    frame with a warning (correct unless the clip starts posed off-rest)."""
    import maya.cmds as cmds
    from maya_tools.export import export_core
    from maya_tools.utils.maya import rig_binding

    for binding in clip_data['rigs']:
        if not cmds.objExists(binding):
            continue
        root = rig_binding.get_root_joint(binding)
        if not root or not cmds.objExists(root):
            cmds.warning(f'[runner] no root joint on binding {binding!r} — '
                         f'skipping Z-up conversion for this rig')
            continue
        root = (cmds.ls(root, long=True) or [root])[0]
        rest_m = _root_rest_world_matrix(root)
        if rest_m is None:
            cmds.warning(
                f'[runner] no bind-pose snapshot for {root} — deriving the '
                f'rest frame from frame {start} (wrong if the clip starts '
                f'posed off-rest; rebuild the rig to refresh the snapshot)')
            cmds.currentTime(start)
            rest_m = cmds.xform(root, q=True, ws=True, matrix=True)
        export_core.orient_root_for_z_up_engine(root, frame_range=(start, end),
                                                rest_world_matrix=rest_m)
        print(f'[runner] Z-up conversion applied to {root} '
              f'over frames {start}-{end}')


def _root_rest_world_matrix(root: str):
    """The root's bind-rest world matrix from the Fabricator registry's
    bind_pose_json (the 'world_m' field captured at build, 2026-07-14), or
    None when no registry/snapshot/field exists (legacy scene)."""
    import json as _json
    import maya.cmds as cmds
    try:
        from maya_tools.rigging.fabricator import nodes as fab_nodes
        reg = fab_nodes.get_registry()
    except Exception:
        reg = None
    if not reg or not cmds.attributeQuery('bind_pose_json', node=reg,
                                          exists=True):
        return None
    try:
        pose = _json.loads(cmds.getAttr(f'{reg}.bind_pose_json') or '{}')
    except Exception:
        return None
    short = root.split('|')[-1].split(':')[-1]
    for j, vals in pose.items():
        if j.split('|')[-1].split(':')[-1] == short and 'world_m' in vals:
            return list(vals['world_m'])
    return None


def _unparent_roots_to_world(clip_data: dict) -> None:
    """Unparent each rig's root joint to world. After bake the joints are
    anim-curve-driven, so reparenting doesn't disturb the animation as long
    as the rig's top group is at world identity (which is the KS convention).
    """
    import maya.cmds as cmds
    from maya_tools.utils.maya import rig_binding

    moved: list = []
    for binding in clip_data['rigs']:
        if not cmds.objExists(binding):
            continue
        root = rig_binding.get_root_joint(binding)
        if not root or not cmds.objExists(root):
            continue
        parent = cmds.listRelatives(root, parent=True, fullPath=True) or []
        if not parent:
            continue  # already at world
        try:
            cmds.parent(root, world=True)
            moved.append(root)
        except Exception as exc:
            cmds.warning(f'[runner] failed to unparent root {root!r}: {exc}')
    if moved:
        print(f'[runner] unparented roots: {moved}')


# Master gate for the root motion transfer step. Disabled while the
# subprocess-side compensation math is still flaky for FK ctrls with bind
# rotation in the parent chain (Phase 2 debugging never fully resolved
# this). Workflow for now: animate the world ctrl directly, OR run
# anim_root_motion.transfer_root_motion() manually from the script editor
# before saving + exporting. Flip back to True to re-enable the per-clip
# pipeline.
_ROOT_MOTION_TRANSFER_ENABLED = False


def _transfer_root_motion_if_enabled(clip_data: dict, start: int, end: int) -> None:
    """Per-rig root motion transfer. No-op if the master gate is off OR the
    clip has root_motion_enabled=False, OR no source ctrls connected.

    For each rig with a source ctrl assigned, compensates the source ctrl AND
    every IK end ctrl under the binding's controls_grp — both are DAG
    descendants of root_ctrl and would otherwise get dragged forward by the
    world ctrl, producing double-displacement (source) or stretched IK chains
    (feet/hands).

    Missing source ctrls or missing world ctrls produce warnings but don't
    block export — validation already gates this, so reaching this point with
    bad state means the scene changed mid-export (rare). Warn and skip.
    """
    if not _ROOT_MOTION_TRANSFER_ENABLED:
        print('[runner] root motion transfer disabled at module gate — skipping')
        return
    if not clip_data.get('root_motion_enabled'):
        return

    import maya.cmds as cmds
    from maya_tools.export import anim_root_motion

    # clip_data comes through json.dumps/loads from anim_pipeline, which
    # stringifies the int keys of root_motion_sources. Coerce back to int so
    # the rig_index lookup below resolves.
    sources_raw = clip_data.get('root_motion_sources') or {}
    sources = {int(k): v for k, v in sources_raw.items()}
    axes = clip_data.get('root_motion_axes') or 'xz'

    for rig_index, binding in enumerate(clip_data['rigs']):
        source_ctrl = sources.get(rig_index)
        if not source_ctrl:
            continue
        if not cmds.objExists(source_ctrl):
            cmds.warning(f'[runner] root motion source missing: {source_ctrl!r}; skipping')
            continue
        world_ctrl = anim_root_motion.find_world_ctrl(binding)
        if not world_ctrl:
            cmds.warning(
                f'[runner] no world ctrl under binding {binding!r}; '
                f'skipping root motion transfer for this rig'
            )
            continue
        ik_feet = anim_root_motion.find_ik_end_ctrls(binding)
        print(
            f'[runner] root motion: {source_ctrl} -> {world_ctrl} '
            f'(axes={axes}, extra_compensate={len(ik_feet)} IK end ctrls)'
        )
        anim_root_motion.transfer_root_motion(
            world_ctrl, source_ctrl, start, end,
            axes=axes, extra_compensate=ik_feet,
        )


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0)
