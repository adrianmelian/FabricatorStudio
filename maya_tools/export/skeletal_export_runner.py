# Python/maya_tools/export/skeletal_export_runner.py
"""Rig (skeletal-mesh) exporter — mayapy subprocess runner.

Runs in mayapy.exe (separate process). Opens the saved scene, runs the export
surgery on the throwaway copy, writes the FBX, exits. No UI, no caller
communication beyond exit code + stdout/stderr. Single-shot per invocation.

Because the scene is a throwaway copy, NOTHING is restored — we delete the
Armature and never rebuild it, orient the joints in place, and strip whatever
must not ride into the FBX. The user's interactive Maya is untouched, so the
fragile in-scene unbuild/rebuild/undo dance of the old exporter simply
disappears (Adrian, 2026-07-13).

Pipeline order (mirrors the Build's skin discipline):
  1. disconnect ALL skins           — an edit-mode bind goes dormant BEFORE any
                                       joint moves, so it can't candy-wrap
  2. break Armature + orient joints  — the same aimer-bake + collapse the Build
                                       runs, so the FBX skeleton is the game rig
  3. strip to joints + mesh          — unparent to world, delete under-joint
                                       helpers (aimer localRefs, stray nulls)
  4. reconnect ALL skins             — rebind at the FINAL, game-oriented pose:
                                       the step no in-scene path ever did
  5. FBX export selected joints + meshes
  6. exit

Args (single JSON string on argv[1]):
    {
        "scene_path": "...",
        "joints": [...],          # export joint names (root first); re-resolved
        "meshes": [...],          # mesh / mesh-group names
        "out_path": "...",
        "fbx_preset": {...},
        "repo_python_root": "..." # added to sys.path so maya_tools imports work
    }
"""
__author__ = "Adrian Melian"

import json
import sys
import traceback


def main() -> None:
    payload = json.loads(sys.argv[1])
    sys.path.insert(0, payload['repo_python_root'])

    print('[runner] initializing Maya standalone')
    import maya.standalone
    maya.standalone.initialize(name='python')

    import maya.cmds as cmds
    cmds.loadPlugin('fbxmaya', quiet=True)

    print(f'[runner] opening scene: {payload["scene_path"]}')
    cmds.file(payload['scene_path'], open=True, force=True)

    if payload.get('format', 'fbx') == 'usd':
        cmds.loadPlugin('mayaUsdPlugin', quiet=True)

    _prepare_and_export(payload['joints'], payload['meshes'],
                        payload['out_path'], payload['fbx_preset'],
                        engine_up_axis=payload.get('engine_up_axis', 'y'),
                        format=payload.get('format', 'fbx'),
                        character_name=payload.get('character_name', ''))


# ─────────────────────────────────────────────────────────────────────────────
# The surgery (also called directly, in-process, by the tests)
# ─────────────────────────────────────────────────────────────────────────────

def _prepare_and_export(joints: list, meshes: list, out_path: str,
                        fbx_preset: dict, engine_up_axis: str = 'y',
                        format: str = 'fbx',
                        character_name: str = '') -> None:
    """Disconnect skins, break the Armature + orient joints, strip to
    joints + mesh, reconnect skins at the final pose, write out. No restore —
    the caller's scene is a throwaway copy.

    engine_up_axis='z' folds the Z-up engine (Unreal) conversion into the
    root joint's frame after the strip and BEFORE the bind reset, so the
    rebind derives every bindPreMatrix at the converted pose. FBX only:
    a USD stage's upAxis metadata is the single axis mechanism (the ruled
    Armature contract) and baking -90 on top of it composes to -180.

    format='usd' writes the Armature/Unreal delivery USD via usd_delivery
    instead of FBX. Embed data (component records, aimer states) is captured
    BEFORE the Armature teardown destroys the aimers."""
    import maya.cmds as cmds
    import maya.mel as mel
    from maya_tools.skinning import skin_connect_app
    from maya_tools.export import export_core

    # 0. Scrub to frame 0 — the default T-pose. Adrian authors raw
    #    range-of-motion anims starting on frame 1, so frame 0 stays the rest
    #    pose; if a rig file happens to carry a ROM, exporting from frame 0
    #    captures the T-pose rather than a mid-animation frame. A pragmatic
    #    default, not a hard rule.
    cmds.currentTime(0)

    # 0.5 Delete every animCurve driving a joint — a live curve overwrites
    #     the orient/collapse setAttr writes on the next time evaluation
    #     (the FBX write's own scene eval is one), snapping the keyed joint
    #     back to its stale value while its unkeyed children keep their
    #     re-expressed locals. Verified 2026-07-14: a rotate-keyed thigh_l
    #     exported with the calf swung 90 deg out of the hip. The legacy
    #     in-scene path always cut keys (_cut_keys_on_chain); this path must
    #     too. Connection-based (not cutKey/keyframe queries) because the
    #     Armature locks joint channels and both go blind on locked
    #     channels; node deletion works regardless and leaves the frame-0
    #     evaluated values in place. A skeletal-mesh FBX is a rest-pose
    #     asset and carries no curves anyway. Throwaway scene: no restore.
    curves = _joint_anim_curves()
    if curves:
        print(f'[runner] deleting {len(curves)} anim curve(s) on joints')
        cmds.delete(curves)

    # 1. Disconnect every skin. A mesh the user bound in edit mode is live;
    #    detaching it here means the orient below can't drag it into a
    #    candy-wrap. Unconditional — already-dormant clusters are a no-op.
    detached = skin_connect_app.disconnect_all_skins()
    print(f'[runner] disconnected {len(detached)} skinned mesh(es)')

    # 2. Break the Armature + orient the joints to the game contract (the same
    #    aimer bake + jointOrient collapse the Build runs). The USD embeds
    #    need the aimers and component records ALIVE, so their capture is
    #    threaded between the unbuild and the teardown.
    early = _orient_to_game_contract(
        capture_for_usd=(format == 'usd'))

    joints = _resolve(joints) or _registry_root_as_list()
    if not joints:
        raise RuntimeError('[runner] no export joints resolved after orient.')
    meshes = _resolve(meshes)
    root = joints[0]

    # 3. Strip to joints + mesh: unparent the root and the mesh group(s) to
    #    world so the FBX hierarchy starts at the root joint, then delete the
    #    non-joint helpers that live UNDER the joints (aimer localRef locators,
    #    stray nulls) — FBXExport -s would otherwise carry them in as bogus
    #    bones. Throwaway scene, so plain delete, no restore.
    root = _unparent_to_world(root)
    meshes = [_unparent_to_world(m) for m in meshes]
    helpers = export_core.non_joint_helpers_under(_resolve(joints))
    if helpers:
        print(f'[runner] stripping {len(helpers)} helper(s) under the joints')
        cmds.delete(helpers)

    # 3.5 Z-up engine target (Unreal): fold the axis conversion into the
    #     root joint's frame — root world orientation becomes RX(-90), the
    #     children's locals re-express, the visible pose doesn't move. The
    #     engine's Y-up import conversion then lands the root bone at
    #     identity, matching engine-authored skeletons (SKM_Manny) so
    #     engine animations play unpitched. Before step 4 on purpose: the
    #     bind reset below derives every bindPreMatrix at this final pose.
    if engine_up_axis == 'z' and format != 'usd':
        print('[runner] engine up axis = z — folding -90 deg world-X into '
              'the root frame')
        export_core.orient_root_for_z_up_engine(root)

    # 4. Reset every skin's bind to the FINAL, settled, game-oriented pose.
    #    Force-eval the skeleton so the joints are settled, then a FULL
    #    disconnect+reconnect (reset_all_binds_to_pose). A plain reconnect skips
    #    a cluster left live and rides its stale bind into the FBX — the twist-
    #    joint candy-wrapper. This re-derives every bindPreMatrix; the logged
    #    worst delta is the export's own self-check (0 == candy-wrapper-free).
    for _j in cmds.ls(type='joint', long=True) or []:
        cmds.getAttr(f'{_j}.worldMatrix[0]')
    refreshed = skin_connect_app.reset_all_binds_to_pose()
    worst = _worst_bind_delta()
    print(f'[runner] reset bind on {len(refreshed)} skinned mesh(es); '
          f'worst bindPreMatrix delta = {worst:.4f}')
    if worst > 0.01:
        cmds.warning(f'[runner] bind still inconsistent after reset (worst '
                     f'delta {worst:.4f}) — the FBX may candy-wrap.')

    # 5. Write out: FBX (selected export) or the USD delivery.
    joints = _resolve(joints)
    meshes = _resolve(meshes)
    if format == 'usd':
        from maya_tools.export import usd_delivery
        usd_delivery.export_delivery(joints[0], meshes, out_path,
                                     character_name=character_name,
                                     early=early, log=print)
    else:
        export_core.apply_fbx_preset(fbx_preset)
        cmds.select(joints + meshes, replace=True)
        mel.eval('FBXExport -f "{}" -s;'.format(out_path.replace('\\', '/')))
    print(f'[runner] exported: {out_path}')


def _orient_to_game_contract(capture_for_usd: bool = False):
    """Delete the Armature and bake the joints to the export orientation
    contract, exactly as the Build's teardown does. A modules-built scene is
    unbuilt to the edit state first (so the aimers exist to bake). A non-KS
    scene (no registry) is exported as-is.

    capture_for_usd=True snapshots the USD embed data (component records +
    aimer states) at the edit state, after the unbuild and BEFORE the teardown
    deletes the aimers. Returns that capture (or None)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import armature, fs_app, nodes

    if not nodes.get_registry():
        print('[runner] no fab registry — exporting joints as-is (no orient)')
        return None

    if cmds.objExists('rig_grp') and not armature.armature_exists():
        print('[runner] modules built — unbuilding to the edit state first')
        fs_app.unbuild_modules()

    early = None
    if capture_for_usd:
        from maya_tools.export import usd_manifest
        early = usd_manifest.capture_early(log=print)

    print('[runner] deleting Armature + orienting joints to the game contract')
    # force_missing_aimers: a hand-deleted aimer must not abort an export;
    # that joint keeps its current orientation. The throwaway scene means we
    # never rebuild the Armature after.
    fs_app._armature_teardown_for_build(force_missing_aimers=True)
    return early


# ─────────────────────────────────────────────────────────────────────────────
# Internals (lazy maya.cmds imports — only safe after standalone.initialize)
# ─────────────────────────────────────────────────────────────────────────────

def _joint_anim_curves() -> list:
    """Every animCurve feeding any joint, found by connection (direct or
    through one unitConversion hop) so locked/non-keyable channels — the
    Armature locks joint channels — are covered too."""
    import maya.cmds as cmds
    joints = cmds.ls(type='joint', long=True) or []
    if not joints:
        return []
    curves = set(cmds.listConnections(joints, source=True, destination=False,
                                      type='animCurve') or [])
    for uc in cmds.listConnections(joints, source=True, destination=False,
                                   type='unitConversion') or []:
        curves.update(cmds.listConnections(uc, source=True, destination=False,
                                           type='animCurve') or [])
    return sorted(curves)


def _resolve(names: list) -> list:
    """Long paths for `names` in the current scene. Tries the given name, then
    a short-name (namespace-stripped) fallback — long paths shift under the
    unparent/strip surgery, so callers re-resolve between steps."""
    import maya.cmds as cmds
    out, seen = [], set()
    for n in names:
        found = (cmds.ls(n, long=True)
                 or cmds.ls(n.split('|')[-1].split(':')[-1], long=True)
                 or [])
        for f in found:
            if f not in seen:
                seen.add(f)
                out.append(f)
    return out


def _registry_root_as_list() -> list:
    """The registry root joint as a one-element long-path list, or []. Fallback
    when the payload's joint names don't resolve (renamed under surgery)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    root = nodes.get_registry_root_joint()
    return cmds.ls(root, long=True) if root and cmds.objExists(root) else []


def _unparent_to_world(node: str) -> str:
    """Unparent `node` to world, returning its new long path (cmds.parent
    returns the new path — the local var would otherwise hold a stale one)."""
    import maya.cmds as cmds
    if node and cmds.objExists(node) and cmds.listRelatives(node, parent=True):
        res = cmds.parent(node, world=True)
        if res:
            return res[0]
    return node


def _worst_bind_delta() -> float:
    """Largest bindPreMatrix-vs-joint-worldInverse element delta across every
    skinCluster. 0 means every influence's bind matches its joint's current
    pose — a candy-wrapper-free export. The runner logs this as its self-check."""
    import maya.cmds as cmds
    worst = 0.0
    for sc in cmds.ls(type='skinCluster') or []:
        conns = cmds.listConnections(f'{sc}.matrix', connections=True,
                                     plugs=True, source=True,
                                     destination=False) or []
        for k in range(0, len(conns), 2):
            j = conns[k + 1].split('.')[0]
            if not cmds.objExists(j):
                continue
            idx = int(conns[k][conns[k].index('[') + 1:conns[k].index(']')])
            bpm = cmds.getAttr(f'{sc}.bindPreMatrix[{idx}]')
            wim = cmds.getAttr(f'{j}.worldInverseMatrix[0]')
            worst = max(worst, max(abs(a - b) for a, b in zip(bpm, wim)))
    return worst


if __name__ == '__main__':
    # os._exit, not sys.exit: this is a single-shot throwaway process whose
    # only outputs are the exit code and the file on disk. Normal interpreter
    # finalization can crash AFTER the work is done (field-crashed 2026-08-03:
    # "PyThreadState_Get ... runtime state: finalizing" with pxr stages alive
    # at teardown), turning a successful export into returncode!=0. Flush,
    # then leave without finalizing.
    import os as _os
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        _os._exit(1)
    sys.stdout.flush()
    sys.stderr.flush()
    _os._exit(0)
