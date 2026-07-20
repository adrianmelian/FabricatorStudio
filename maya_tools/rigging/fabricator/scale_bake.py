# maya_tools/rigging/fabricator/scale_bake.py
"""Uniform-scale-and-bake for a Fabricator rig (Edit Mode only).

Scales the whole rig by a uniform factor, then bakes that scale off the
asset so the rig ends at scale 1 but physically larger. Pieces together
existing ingredients (Skin IO transfer + makeIdentity scale freeze) in the
correct order, with the one non-obvious safeguard: segmentScaleCompensate
must be OFF while the root joint carries the scale, or child joints
compensate and the scale isn't uniform.

Sequence (all under one undo chunk):
  1. Validate: Edit Mode (rig not built), scale > 0, root joint found.
  2. Seek to frame 0 (the default T-pose); restore the frame after.
  3. Record + disable segmentScaleCompensate on every rig joint.
  4. root_joint.scale = (S, S, S)  -> skeleton + skinned geo scale
     uniformly (root sits at the world origin, so this is an
     origin-pivot scale).
  5. Per skinned mesh: Skin IO save -> temp .json (captures the scaled
     world points + weights).
  6. Per skinned mesh: delete construction history -> removes the
     skinCluster and bakes the scaled deformed geo as the new base shape.
  7. makeIdentity(apply=True, scale=True) on the root joint (bakes S into
     the chain, scales back to 1, world positions preserved) + on each
     baked mesh transform (safety; the geo transform is already identity).
  8. Per skinned mesh: Skin IO load mode='transfer' -> rebinds at the
     scaled positions against the now-scale-1 joints.
  9. Restore segmentScaleCompensate + frame (inside the chunk) and clean
     up temp files (finally).

Non-skinned geo is out of scope — it doesn't follow a joint scale.
"""
__author__ = "Adrian Melian"

import shutil
import tempfile
from pathlib import Path

import maya.cmds as cmds

from maya_tools.framework.decorators import undo_chunk
from maya_tools.rigging.fabricator import nodes
from maya_tools.skinning.skin_io import skin_io_app as skin_io


def _topmost_joint(joint: str) -> str:
    """Walk up the joint-parent chain to the topmost joint ancestor."""
    cur = joint
    while True:
        parents = cmds.listRelatives(cur, parent=True, type='joint') or []
        if not parents:
            return cur
        cur = parents[0]


def _find_root_joint() -> str:
    """Resolve the rig's root joint with fallbacks, so the tool works on
    rigs whose registry root_joint message was never wired (skeletons
    built before that wiring, or via another path).

      1. The registry's explicit root_joint message (preferred).
      2. Walk up from any registered component joint to its topmost joint.
      3. Last resort: the single scene joint with no joint parent.

    Returns '' if none can be resolved.
    """
    root = nodes.get_registry_root_joint()
    if root and cmds.objExists(root):
        return root
    for cnode in nodes.get_all_component_nodes():
        for j in (nodes.get_component_joints(cnode) or []):
            if cmds.objExists(j):
                return _topmost_joint(j)
    roots = [j for j in (cmds.ls(type='joint', long=False) or [])
             if not (cmds.listRelatives(j, parent=True, type='joint') or [])]
    return roots[0] if len(roots) == 1 else ''


def _rig_joints(root_joint: str) -> list:
    """Root joint + every descendant joint (the rig skeleton)."""
    descendants = cmds.listRelatives(root_joint, allDescendents=True,
                                     type='joint', fullPath=False) or []
    return [root_joint] + list(descendants)


def _rig_skinned_meshes(rig_joints: list) -> list:
    """Mesh transforms whose skinCluster influences include any rig joint
    — exactly the meshes that deform when the root joint scales.

    Returns sorted unique transform names. Influences are matched by short
    name so namespaced rigs resolve.
    """
    rig_short = {j.split('|')[-1].split(':')[-1] for j in rig_joints}
    out = set()
    for sc in cmds.ls(type='skinCluster') or []:
        infls = cmds.skinCluster(sc, query=True, influence=True) or []
        infl_short = {i.split('|')[-1].split(':')[-1] for i in infls}
        if not (rig_short & infl_short):
            continue
        for geo in cmds.skinCluster(sc, query=True, geometry=True) or []:
            if cmds.nodeType(geo) == 'transform':
                out.add(geo)
            else:
                parents = cmds.listRelatives(geo, parent=True,
                                             fullPath=False) or []
                if parents:
                    out.add(parents[0])
    return sorted(out)


def scale_and_bake(scale: float) -> dict:
    """Uniformly scale the active Fabricator rig by `scale` and bake the
    scale off the asset — skeleton + skinned geo end at scale 1, physically
    larger. Edit Mode only.

    Returns a summary dict {scale, root_joint, meshes, joints_scaled}.
    Raises RuntimeError on validation failure (rig built, no root joint,
    bad scale).
    """
    if scale is None or scale != scale or scale in (float('inf'), float('-inf')):
        raise RuntimeError(f'Scale factor must be a finite number (got {scale!r}).')
    if scale <= 0:
        raise RuntimeError(f'Scale factor must be > 0 (got {scale!r}).')
    if abs(scale - 1.0) < 1e-9:
        raise RuntimeError('Scale factor of 1.0 is a no-op.')

    # Edit-Mode guard (app-layer, no UI import): a built rig owns rig_grp.
    if cmds.objExists('rig_grp'):
        raise RuntimeError(
            'Scale Rig runs in Edit Mode only — the rig is built (rig_grp '
            'present). Unbuild modules (Edit Rig) first.')

    root_joint = _find_root_joint()
    if not root_joint or not cmds.objExists(root_joint):
        raise RuntimeError(
            'No rig root joint found — the registry has no root_joint set, '
            'no component joints to walk up from, and the scene has no '
            'single unambiguous root joint. Build a skeleton first.')

    rig_joints = [j for j in _rig_joints(root_joint) if cmds.objExists(j)]
    meshes = _rig_skinned_meshes(rig_joints)

    tmp_dir = Path(tempfile.mkdtemp(prefix='ks_scale_bake_'))
    prev_frame = cmds.currentTime(query=True)
    ssc_state = {}  # joint -> original segmentScaleCompensate value

    try:
        with undo_chunk(f'Scale Rig x{scale}'):
            # Operate at frame 0 (the default T-pose).
            cmds.currentTime(0, edit=True)
            try:
                # SSC off so the root scale propagates uniformly down the
                # chain instead of being compensated away by the children.
                for j in rig_joints:
                    if cmds.attributeQuery('segmentScaleCompensate',
                                           node=j, exists=True):
                        ssc_state[j] = cmds.getAttr(
                            f'{j}.segmentScaleCompensate')
                        cmds.setAttr(f'{j}.segmentScaleCompensate', 0)

                # Scale the root joint — pivots at the world-origin root.
                cmds.setAttr(f'{root_joint}.scale', scale, scale, scale)

                # Per-mesh skin save (captures scaled world points + weights).
                mesh_files = {}
                for mesh in meshes:
                    short = mesh.split('|')[-1].split(':')[-1]
                    path = tmp_dir / f'{short}.json'
                    skin_io.save_skin(mesh, str(path))
                    mesh_files[mesh] = str(path)

                # Bake the scaled deformation: deleting construction history
                # removes the skinCluster and freezes the scaled deformed
                # geo as the new base shape.
                for mesh in meshes:
                    if cmds.objExists(mesh):
                        cmds.delete(mesh, constructionHistory=True)

                # Freeze the scale into the skeleton — world positions
                # preserved, scales back to 1.
                cmds.makeIdentity(root_joint, apply=True,
                                  translate=False, rotate=False, scale=True,
                                  normal=0)
                for mesh in meshes:
                    if cmds.objExists(mesh):
                        cmds.makeIdentity(mesh, apply=True,
                                          translate=False, rotate=False,
                                          scale=True, normal=0)

                # Rebind each mesh via transfer at the scaled positions.
                for mesh, path in mesh_files.items():
                    if cmds.objExists(mesh):
                        skin_io.load_skin(mesh, path, mode='transfer')
            finally:
                # Restore SSC + frame inside the chunk so one Ctrl+Z reverts
                # the whole operation and the scene is never left with SSC
                # globally off. At scale 1 (post-freeze) SSC is geometrically
                # irrelevant — this just honors the original per-joint state.
                ssc_failed = []
                for j, val in ssc_state.items():
                    if cmds.objExists(j):
                        try:
                            cmds.setAttr(f'{j}.segmentScaleCompensate', val)
                        except Exception:
                            ssc_failed.append(j)
                if ssc_failed:
                    cmds.warning(
                        f'Scale Rig: failed to restore segmentScaleCompensate '
                        f'on {len(ssc_failed)} joint(s): {ssc_failed[:5]}')
                try:
                    cmds.currentTime(prev_frame, edit=True)
                except Exception:
                    cmds.warning(
                        f'Scale Rig: failed to restore the timeline to frame '
                        f'{prev_frame}.')
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {
        'scale': scale,
        'root_joint': root_joint,
        'meshes': meshes,
        'joints_scaled': len(rig_joints),
    }
