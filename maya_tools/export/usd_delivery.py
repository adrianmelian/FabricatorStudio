"""USD skeletal delivery — the writer, post-pass, and refuse-don't-ship gate.

Runs inside the throwaway subprocess scene AFTER skeletal_export_runner's
surgery (skins reconnected at the final pose). Produces one USD matching the
Armature lane's SPEC-FABRICATOR-USD-EXPORT delivery dialect, which Unreal's
USD importer reads identically — one dialect, two destinations.

Pipeline (each step exists because a measurement demanded it — see the task
folder's FINDINGS in the MrMiata depot, workspace/2026-08-03_maya-usd-exporter):

  0. normalize the root frame to identity, world-preserving (Maya-authored FS
     rigs carry root world rotate [-90,0,0]; the contract requires identity in
     BOTH artifacts and the stage upAxis is the ONLY axis mechanism)
  1. re-derive every bind at the settled pose, prune zero-total-weight
     influences from the skinClusters (binding level — never the skeleton)
  2. temp-group root + meshes under a group named for the character: only a
     REAL scene ancestor becomes the SkelRoot (-rootPrim does not)
  3. mayaUSDExport to a temp file
  4. pxr post-pass: Skeleton prim renamed to `skel`, jointIndices remapped to
     Skeleton order then the mesh's own skel:joints table dropped (dropping
     without remapping silently rebinds every vertex), static SkelAnimation
     stripped, unbound materials deleted, textures copied to ./textures/ with
     relative paths, embeds written into customLayerData
  5. cross-check + read-back verification on the final bytes; only a file
     that passes every check is renamed onto out_path
"""
__author__ = "Adrian Melian"

import json
import os
import shutil

import maya.cmds as cmds

CONTRACT_VERSION = 1

_WEIGHT_TOLERANCE = 1e-4
_ROOT_TOLERANCE = 1e-3          # degrees / cm
_WORLD_PRESERVE_TOLERANCE = 0.01  # cm — the normalize step's own assert


class UsdExportError(RuntimeError):
    """A cross-check failure. The file was NOT shipped."""


def export_delivery(root, meshes, out_path, character_name='', early=None,
                    log=print):
    """Write the delivery USD. Returns the path written. Raises UsdExportError
    (nothing shipped) when any check fails."""
    root = _long(root)
    if not root:
        raise UsdExportError('USD export: root joint not found in scene.')

    name = _usd_identifier(character_name
                           or (early or {}).get('rig_label')
                           or _scene_stem() or 'Character')

    _normalize_root_frame(root, log=log)

    pruned = _reset_binds_and_prune(log=log)

    manifest_json = blueprint_json = ''
    if early:
        from maya_tools.export import usd_manifest
        manifest_json, blueprint_json, _bp = usd_manifest.build_embeds(
            early, root, pruned_influences=pruned, log=log)
    else:
        log('[usd] no Fabricator registry — plain delivery USD, no embeds')

    tmp_path = out_path + '.tmp.usd'
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    grp = cmds.group(_roots_of([root] + list(meshes)), name=name, world=True)
    try:
        cmds.mayaUSDExport(file=tmp_path, exportRoots=['|' + grp],
                           exportSkels='auto', exportSkin='auto',
                           shadingMode='useRegistry',
                           convertMaterialsTo=['UsdPreviewSurface'],
                           materialsScopeName='_materials',
                           defaultUSDFormat='usdc')
    finally:
        kids = cmds.listRelatives(grp, children=True, fullPath=True) or []
        if kids:
            cmds.parent(kids, world=True)
        cmds.delete(grp)
    log('[usd] wrote %s (%d bytes), running post-pass'
        % (tmp_path, os.path.getsize(tmp_path)))

    try:
        _post_pass(tmp_path, name, manifest_json, blueprint_json, out_path,
                   log=log)
        _verify(tmp_path, name, blueprint_json, log=log)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    if os.path.exists(out_path):
        os.remove(out_path)
    os.replace(tmp_path, out_path)
    log('[usd] verified and shipped: %s' % out_path)
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Scene-side steps
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_root_frame(root, log=print):
    """Zero the root joint's world orientation, world-preserving.

    Children's world matrices are captured, the root's rotate/jointOrient are
    zeroed, the children are restored by world matrix (their locals recompute
    against the new parent frame). Asserts world positions are unchanged —
    the frame changes, the world must not (the bake lesson: assert world,
    never local)."""
    rot = cmds.xform(root, q=True, ws=True, ro=True)
    pos = cmds.xform(root, q=True, ws=True, t=True)
    if max(abs(v) for v in rot) <= _ROOT_TOLERANCE and \
       max(abs(v) for v in pos) <= _ROOT_TOLERANCE:
        return

    if max(abs(v) for v in pos) > _ROOT_TOLERANCE:
        raise UsdExportError(
            'USD export: the root joint sits at [%.3f, %.3f, %.3f], not the '
            'origin. Move the rig to the origin and re-export — the contract '
            'requires the root at identity at the origin.' % tuple(pos))

    log('[usd] root frame is [%.2f, %.2f, %.2f] — normalizing to identity '
        '(world-preserving)' % tuple(rot))

    probes = cmds.listRelatives(root, allDescendents=True, type='joint',
                                fullPath=True) or []
    before = {p: cmds.xform(p, q=True, ws=True, t=True) for p in probes}

    kids = cmds.listRelatives(root, children=True, type='joint',
                              fullPath=True) or []
    kid_world = {k: cmds.xform(k, q=True, ws=True, m=True) for k in kids}

    for attr in ('jointOrient', 'rotate'):
        try:
            cmds.setAttr('%s.%s' % (root, attr), 0, 0, 0)
        except Exception as exc:
            raise UsdExportError(
                'USD export: could not zero the root frame (%s.%s): %s'
                % (root, attr, exc))
    for k, m in kid_world.items():
        cmds.xform(k, ws=True, m=m)

    worst, worst_j = 0.0, ''
    for p in probes:
        after = cmds.xform(p, q=True, ws=True, t=True)
        d = max(abs(a - b) for a, b in zip(after, before[p]))
        if d > worst:
            worst, worst_j = d, p
    if worst > _WORLD_PRESERVE_TOLERANCE:
        raise UsdExportError(
            'USD export: root normalization moved %s by %.4f cm in world '
            'space — refusing to ship a moved rig.' % (worst_j, worst))
    log('[usd] root normalized; worst world drift %.5f cm' % worst)


def _reset_binds_and_prune(log=print):
    """Re-derive every bindPreMatrix at the current settled pose, then prune
    zero-total-weight influences from each skinCluster (the ratified v2
    binding-level prune: the influence leaves the cluster, never the
    skeleton). Returns the pruned joint short names."""
    from maya_tools.skinning import skin_connect_app
    for j in cmds.ls(type='joint', long=True) or []:
        cmds.getAttr(j + '.worldMatrix[0]')
    skin_connect_app.reset_all_binds_to_pose()

    # Delete every dagPose node: mayaUSDExport reads bindTransforms from the
    # bindPose when one exists, and that node still remembers the
    # pre-normalization frame (a root normalized to identity would export a
    # stale non-identity bind). With no dagPose the exporter derives bind
    # from the current pose, which reset_all_binds_to_pose just made the
    # truth. Throwaway scene: plain delete.
    poses = cmds.ls(type='dagPose') or []
    if poses:
        cmds.delete(poses)
        log('[usd] deleted %d dagPose node(s) (bind derives from the '
            'settled pose)' % len(poses))

    # The joint's OWN .bindPose attr survives the dagPose deletion and
    # mayaUSDExport reads it for bindTransforms — measured: a root
    # normalized to identity still exported bind R(-90) from this attr.
    # Stamp every joint's .bindPose with its settled world matrix so the
    # one bind truth is the pose the skins were just reset to.
    stamped = 0
    for j in cmds.ls(type='joint', long=True) or []:
        if not cmds.attributeQuery('bindPose', node=j, exists=True):
            continue
        try:
            world = cmds.getAttr(j + '.worldMatrix[0]')
            cmds.setAttr(j + '.bindPose', world, type='matrix')
            stamped += 1
        except Exception:
            pass
    if stamped:
        log('[usd] stamped .bindPose on %d joint(s) at the settled pose'
            % stamped)

    pruned = []
    for sc in cmds.ls(type='skinCluster') or []:
        influences = cmds.skinCluster(sc, q=True, influence=True) or []
        try:
            weighted = set(cmds.skinCluster(sc, q=True,
                                            weightedInfluence=True) or [])
        except Exception:
            continue
        for inf in influences:
            if inf in weighted:
                continue
            try:
                cmds.skinCluster(sc, edit=True, removeInfluence=inf)
                pruned.append(inf.split('|')[-1])
            except Exception as exc:
                log('[usd] could not prune influence %s from %s: %s'
                    % (inf, sc, exc))
    if pruned:
        log('[usd] pruned %d zero-weight influence(s) from the binding '
            '(skeleton untouched): %s' % (len(pruned), ', '.join(pruned)))
    return pruned


def _roots_of(nodes):
    """Top-level ancestors of the given nodes, deduped, existing only."""
    out, seen = [], set()
    for n in nodes:
        n = _long(n)
        if not n:
            continue
        top = '|' + n.split('|')[1]
        if top not in seen:
            seen.add(top)
            out.append(top)
    return out


def _long(node):
    found = cmds.ls(node, long=True) or cmds.ls(
        str(node).split('|')[-1].split(':')[-1], long=True) or []
    return found[0] if found else ''


def _scene_stem():
    scene = cmds.file(q=True, sn=True) or ''
    return os.path.splitext(os.path.basename(scene))[0] if scene else ''


def _usd_identifier(name):
    import re
    clean = re.sub(r'[^A-Za-z0-9_]', '_', str(name)).strip('_') or 'Character'
    return clean if not clean[0].isdigit() else '_' + clean


# ─────────────────────────────────────────────────────────────────────────────
# Post-pass (pxr, on the written temp file)
# ─────────────────────────────────────────────────────────────────────────────

def _post_pass(path, name, manifest_json, blueprint_json, final_out_path,
               log=print):
    from pxr import Usd, UsdShade, Sdf

    stage = Usd.Stage.Open(path)
    root_prim = '/' + name

    skel_paths = [str(p.GetPath()) for p in stage.Traverse()
                  if p.GetTypeName() == 'Skeleton']
    if len(skel_paths) != 1:
        raise UsdExportError(
            'USD export: expected exactly one Skeleton prim, found %d (%s).'
            % (len(skel_paths), ', '.join(skel_paths) or 'none'))
    skel_path = skel_paths[0]

    # Strip the static SkelAnimation (zero time samples, rest-pose duplicate)
    # AND the skeleton's animationSource rel, which would dangle after.
    for p in list(stage.Traverse()):
        if p.GetTypeName() == 'SkelAnimation':
            stage.RemovePrim(p.GetPath())
    skel_prim_obj = stage.GetPrimAtPath(skel_path)
    if skel_prim_obj and skel_prim_obj.HasProperty('skel:animationSource'):
        skel_prim_obj.RemoveProperty('skel:animationSource')

    # Remap each mesh's jointIndices to Skeleton order, drop skel:joints.
    _remap_bindings_to_skeleton_order(stage, skel_path, log=log)

    # Delete materials nothing binds (the control-orb leak).
    _delete_unbound_materials(stage, log=log)

    # Copy textures beside the FINAL destination, rewrite paths relative.
    _localize_textures(stage, os.path.dirname(os.path.abspath(final_out_path)),
                       log=log)

    stage.GetRootLayer().Save()
    del stage

    # Rename the Skeleton prim to `skel` (contract path /<Name>/skel) with a
    # layer-level namespace edit, then retarget every skel:skeleton rel.
    new_skel = root_prim + '/skel'
    if skel_path != new_skel:
        layer = Sdf.Layer.FindOrOpen(path)
        edit = Sdf.BatchNamespaceEdit()
        edit.Add(skel_path, new_skel)
        if not layer.Apply(edit):
            raise UsdExportError(
                'USD export: could not rename Skeleton prim %s -> %s.'
                % (skel_path, new_skel))
        layer.Save()

        stage = Usd.Stage.Open(path)
        from pxr import UsdSkel
        for p in stage.Traverse():
            if p.GetTypeName() != 'Mesh':
                continue
            rel = UsdSkel.BindingAPI(p).GetSkeletonRel()
            if rel and rel.GetTargets():
                rel.SetTargets([new_skel])
        stage.GetRootLayer().Save()
        del stage

    # customLayerData: the contract key + the embeds, then read back.
    layer = Sdf.Layer.FindOrOpen(path)
    data = dict(layer.customLayerData or {})
    data['fabricator_usd_contract'] = CONTRACT_VERSION
    if manifest_json:
        data['armature_modules'] = manifest_json
    if blueprint_json:
        data['fabricator_blueprint'] = blueprint_json
    layer.customLayerData = data
    layer.Save()

    check = dict(Sdf.Layer.FindOrOpen(path).customLayerData or {})
    for key, want in data.items():
        if check.get(key) != want:
            raise UsdExportError(
                'USD export: customLayerData read-back mismatch on %r.' % key)
    log('[usd] post-pass complete: skel prim, bindings remapped, embeds in')


def _remap_bindings_to_skeleton_order(stage, skel_path, log=print):
    from pxr import UsdSkel, Vt

    sk = UsdSkel.Skeleton(stage.GetPrimAtPath(skel_path))
    sk_joints = list(sk.GetJointsAttr().Get() or [])
    sk_index = {j: i for i, j in enumerate(sk_joints)}

    for p in stage.Traverse():
        if p.GetTypeName() != 'Mesh':
            continue
        b = UsdSkel.BindingAPI(p)
        mesh_joints_attr = b.GetJointsAttr()
        mesh_joints = list(mesh_joints_attr.Get() or []) \
            if mesh_joints_attr else []
        if not mesh_joints:
            continue
        missing = [j for j in mesh_joints if j not in sk_index]
        if missing:
            raise UsdExportError(
                'USD export: mesh %s binds joints missing from the skeleton: '
                '%s' % (p.GetPath(), ', '.join(missing[:5])))
        remap = [sk_index[j] for j in mesh_joints]

        ji_attr = p.GetAttribute('primvars:skel:jointIndices')
        jw_attr = p.GetAttribute('primvars:skel:jointWeights')
        indices = list(ji_attr.Get() or [])
        weights = list(jw_attr.Get() or [])
        new_indices = [remap[idx] if w != 0.0 else 0
                       for idx, w in zip(indices, weights)]
        ji_attr.Set(Vt.IntArray(new_indices))
        p.RemoveProperty('skel:joints')
        log('[usd] %s: %d indices remapped to skeleton order, '
            'skel:joints dropped' % (p.GetPath(), len(new_indices)))


def _delete_unbound_materials(stage, log=print):
    from pxr import UsdShade
    bound = set()
    for p in stage.Traverse():
        rel = p.GetRelationship('material:binding')
        if rel:
            bound.update(str(t) for t in rel.GetTargets())
        for sub in p.GetChildren():
            srel = sub.GetRelationship('material:binding')
            if srel:
                bound.update(str(t) for t in srel.GetTargets())
    doomed = [p.GetPath() for p in stage.Traverse()
              if p.GetTypeName() == 'Material'
              and str(p.GetPath()) not in bound]
    for path in doomed:
        stage.RemovePrim(path)
    if doomed:
        log('[usd] deleted %d unbound material(s): %s'
            % (len(doomed), ', '.join(str(d).rsplit('/', 1)[-1]
                                      for d in doomed)))


def _localize_textures(stage, out_dir, log=print):
    from pxr import UsdShade
    tex_dir = os.path.join(out_dir, 'textures')
    for p in stage.Traverse():
        if p.GetTypeName() != 'Shader':
            continue
        sh = UsdShade.Shader(p)
        if sh.GetIdAttr().Get() != 'UsdUVTexture':
            continue
        inp = sh.GetInput('file')
        if not inp:
            continue
        asset = inp.Get()
        src = asset.resolvedPath or asset.path if asset else ''
        if not src or not os.path.isfile(src):
            continue
        os.makedirs(tex_dir, exist_ok=True)
        base = os.path.basename(src)
        dst = os.path.join(tex_dir, base)
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy2(src, dst)
        inp.Set('./textures/' + base)
        log('[usd] texture localized: %s' % base)


# ─────────────────────────────────────────────────────────────────────────────
# The gate: cross-check + read-back on the final bytes
# ─────────────────────────────────────────────────────────────────────────────

def _verify(path, name, blueprint_json, log=print):
    """Every check the contract and the ratified v2 batch demand. Raises
    UsdExportError naming the difference; a failing file is never shipped."""
    from pxr import Usd, UsdGeom, UsdSkel, Gf

    stage = Usd.Stage.Open(path)
    layer = stage.GetRootLayer()
    problems = []

    if str(layer.defaultPrim) != name:
        problems.append('defaultPrim is %r, expected %r'
                        % (str(layer.defaultPrim), name))
    if UsdGeom.GetStageUpAxis(stage) != 'Y':
        problems.append('stage upAxis is not Y')
    if abs(UsdGeom.GetStageMetersPerUnit(stage) - 0.01) > 1e-9:
        problems.append('metersPerUnit is not 0.01')

    skels = [p for p in stage.Traverse() if p.GetTypeName() == 'Skeleton']
    if len(skels) != 1:
        problems.append('expected one Skeleton prim, found %d' % len(skels))
        raise UsdExportError('USD export failed verification: '
                             + '; '.join(problems))
    skel_prim = skels[0]
    if str(skel_prim.GetPath()) != '/%s/skel' % name:
        problems.append('Skeleton prim is %s, expected /%s/skel'
                        % (skel_prim.GetPath(), name))

    sk = UsdSkel.Skeleton(skel_prim)
    joints = list(sk.GetJointsAttr().Get() or [])
    rest = sk.GetRestTransformsAttr().Get() or []
    bind = sk.GetBindTransformsAttr().Get() or []
    if not joints:
        problems.append('skeleton has no joints')
    if len(rest) != len(joints) or len(bind) != len(joints):
        problems.append('rest/bind transform counts do not match joints')

    shorts = [j.split('/')[-1] for j in joints]
    dupes = sorted({s for s in shorts if shorts.count(s) > 1})
    if dupes:
        problems.append('duplicate joint names: %s' % ', '.join(dupes[:5]))

    for label, xf in (('rest', rest[0] if rest else None),
                      ('bind', bind[0] if bind else None)):
        if xf is None:
            continue
        t = Gf.Transform(Gf.Matrix4d(xf))
        tr = t.GetTranslation()
        rq = t.GetRotation().GetQuaternion()
        ident = abs(abs(rq.GetReal()) - 1.0) < 1e-5
        if not ident or max(abs(v) for v in tr) > _ROOT_TOLERANCE:
            problems.append('root %s transform is not identity at origin'
                            % label)

    skel_root = UsdSkel.Root(stage.GetPrimAtPath('/' + name))
    cache = UsdSkel.Cache()
    cache.Populate(skel_root, Usd.TraverseInstanceProxies())
    bindings = cache.ComputeSkelBindings(skel_root,
                                         Usd.TraverseInstanceProxies())
    n_targets = sum(len(b.GetSkinningTargets()) for b in bindings)

    meshes = [p for p in stage.Traverse() if p.GetTypeName() == 'Mesh']
    if not meshes:
        problems.append('no Mesh prims in the export')

    # A mesh is either FULLY skinned (every check below) or carries no
    # binding at all — the deliberate model-plus-skeleton delivery, skinned
    # in Armature (Jarrod's workflow, 2026-08-04). Anything in between is a
    # damaged file.
    skinned_meshes = [p for p in meshes
                     if p.GetAttribute('primvars:skel:jointIndices')
                     and p.GetAttribute('primvars:skel:jointIndices').Get()
                     is not None]
    unskinned = len(meshes) - len(skinned_meshes)
    if n_targets < len(skinned_meshes):
        problems.append('ComputeSkelBindings resolves %d of %d skinned '
                        'mesh(es)' % (n_targets, len(skinned_meshes)))

    from pxr import UsdGeom as _ug
    for p in skinned_meshes:
        mesh = _ug.Mesh(p)
        pts = mesh.GetPointsAttr().Get() or []
        ji = p.GetAttribute('primvars:skel:jointIndices')
        jw = p.GetAttribute('primvars:skel:jointWeights')
        if not (jw and jw.Get() is not None):
            problems.append('%s: jointIndices without jointWeights'
                            % p.GetPath())
            continue
        es = ji.GetMetadata('elementSize') or 0
        idx = ji.Get()
        wts = jw.Get()
        if len(idx) != len(pts) * es:
            problems.append('%s: jointIndices length %d != verts %d x %d'
                            % (p.GetPath(), len(idx), len(pts), es))
            continue
        nj = len(joints)
        if idx and (min(idx) < 0 or max(idx) >= nj):
            problems.append('%s: joint index out of skeleton range'
                            % p.GetPath())
        bad = 0
        for v in range(len(pts)):
            s = sum(wts[v * es:(v + 1) * es])
            if abs(s - 1.0) > _WEIGHT_TOLERANCE:
                bad += 1
        if bad:
            problems.append('%s: %d vertex weight sums off by more than %g'
                            % (p.GetPath(), bad, _WEIGHT_TOLERANCE))
        if p.GetAttribute('primvars:skel:joints') or \
           UsdSkel.BindingAPI(p).GetJointsAttr().Get():
            problems.append('%s: skel:joints still present on the mesh'
                            % p.GetPath())

    # Machinery leak sweep.
    leaks = [str(p.GetPath()) for p in stage.Traverse()
             if any(k in p.GetName().lower() for k in
                    ('fab_', 'ikhandle', '_pivot', 'aimer', 'jntorient'))]
    if leaks:
        problems.append('FS machinery leaked into the file: %s'
                        % ', '.join(leaks[:5]))

    # Blueprint vs skeleton agreement (Fabricator exports only).
    if blueprint_json:
        bp = json.loads(blueprint_json)
        bp_names = [j['name'] for j in bp.get('joints') or []]
        bp_dupes = sorted({n for n in bp_names if bp_names.count(n) > 1})
        if bp_dupes:
            problems.append('blueprint duplicate joints: %s'
                            % ', '.join(bp_dupes[:5]))
        engine = set(bp.get('engine_joints') or [])
        bone_set = set(shorts)
        missing = [n for n in bp_names if n not in bone_set]
        extra = [s for s in shorts if s not in set(bp_names) | engine]
        if missing:
            problems.append('blueprint joints missing from the skeleton: %s'
                            % ', '.join(missing[:5]))
        if extra:
            problems.append('skeleton bones not in blueprint or '
                            'engine_joints: %s' % ', '.join(extra[:5]))
        if bp.get('up_axis') and bp['up_axis'] != str(
                UsdGeom.GetStageUpAxis(stage)):
            problems.append('blueprint up_axis %r disagrees with stage upAxis'
                            % bp['up_axis'])
        emb = dict(layer.customLayerData or {})
        if emb.get('fabricator_blueprint') != blueprint_json:
            problems.append('embedded blueprint does not match what was built')
        if 'armature_modules' not in emb:
            problems.append('armature_modules embed missing')

    if problems:
        raise UsdExportError(
            'USD export failed verification (nothing shipped):\n  - '
            + '\n  - '.join(problems))
    skin_note = ('' if not unskinned else
                 ', %d unskinned (bind in Armature)' % unskinned)
    log('[usd] verification: %d joints, %d mesh(es)%s, all checks green'
        % (len(joints), len(meshes), skin_note))
