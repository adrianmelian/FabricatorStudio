"""AutoSkin Maya apply-layer tests. Run:

  fast (no GPU):
    "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" \
        maya_tools/skinning/autoskin/_dev/test_autoskin_app_maya.py

  + the live end-to-end (needs the backend + GPU, ~2 min per bind):
    ... test_autoskin_app_maya.py --live

The fast tests cover the logic a live run would only exercise by accident:
selection rules, the vertex probe, the API weight write and its lock
handling, and the Generate-Joints rebuild. The --live run is the checkpoint:
a real character, skinned by one call, in a real Maya session, with undo.
"""
import contextlib
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

import maya.standalone
maya.standalone.initialize(name='python')

import maya.cmds as cmds

from maya_tools.skinning.autoskin import autoskin_app as app

REGGIE = ('d:/Documents/MrMiata/workspace/2026-07-12_skintoken-research/'
          'phase0/reggie_v2_proxy.fbx')

LIVE = '--live' in sys.argv
FAILURES = []

# Batch Maya starts with the undo queue off, so an undo test would pass
# vacuously. Turn it on before anything runs.
cmds.undoInfo(stateWithoutFlush=True)
cmds.undoInfo(state=True, infinity=True)


def check(name, fn):
    try:
        fn()
        print(f'  ok: {name}')
    except Exception as exc:
        import traceback
        FAILURES.append(f'{name}: {exc!r}')
        print(f'FAIL: {name}: {exc!r}')
        traceback.print_exc()


_REAL_HEALTH = app.backend.health


@contextlib.contextmanager
def fake_health(ready=True, reason=''):
    """Patch backend.health and ALWAYS put it back.

    A monkeypatch that leaks poisons every test after it — this one once
    convinced the live checkpoint that a 5070 Ti was 'No NVIDIA GPU'.
    """
    app.backend.health = lambda: {'ready': ready, 'reason': reason}
    try:
        yield
    finally:
        app.backend.health = _REAL_HEALTH


def _cube_and_joints(prefix='t'):
    """A skinnable scene: 2 joints + a cube, nothing bound."""
    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(name=f'{prefix}_root', p=(0, 0, 0))
    tip = cmds.joint(name=f'{prefix}_tip', p=(0, 5, 0))
    cmds.select(clear=True)
    mesh = cmds.polyCube(name=f'{prefix}_mesh', h=6)[0]
    return [root, tip], mesh


# ── selection rules ──────────────────────────────────────────────────────────

def test_split_selection_sorts_joints_from_meshes():
    joints, mesh = _cube_and_joints()
    j, m = app.split_selection(joints + [mesh])
    assert j == joints, j
    assert m == [mesh], m


def test_validate_demands_joints_unless_generating():
    joints, mesh = _cube_and_joints()
    with fake_health():
        try:
            app.validate([mesh])
        except app.AutoSkinError as exc:
            assert 'bind joints' in str(exc), exc
        else:
            raise AssertionError('accepted a mesh with no joints')


def test_validate_rejects_joints_when_generating():
    """Generate Joints builds a NEW skeleton — selecting joints as well is
    a contradiction, and silently ignoring them would be worse."""
    joints, mesh = _cube_and_joints()
    with fake_health():
        try:
            app.validate(joints + [mesh], generate_joints=True)
        except app.AutoSkinError as exc:
            assert 'only the' in str(exc), exc
        else:
            raise AssertionError('accepted joints in generate-joints mode')


def test_validate_refuses_on_an_unhealthy_backend():
    joints, mesh = _cube_and_joints()
    with fake_health(ready=False, reason='No NVIDIA GPU.'):
        try:
            app.validate(joints + [mesh])
        except app.AutoSkinError as exc:
            assert 'NVIDIA' in str(exc)
        else:
            raise AssertionError('ran against an unhealthy backend')
    assert app.backend.health is _REAL_HEALTH, 'health patch leaked'


def test_validate_refuses_a_mesh_away_from_joints():
    """Hand the engine a mesh that does not sit on its skeleton and it
    does not fail — it prunes joints it cannot explain and hands back a
    mangled skeleton (80 joints came back as 78, live). Refuse early."""
    joints, mesh = _cube_and_joints()
    cmds.move(500, 0, 0, mesh, relative=True)
    with fake_health():
        try:
            app.validate(joints + [mesh])
        except app.AutoSkinError as exc:
            assert 'does not overlap' in str(exc), exc
        else:
            raise AssertionError('accepted a mesh nowhere near the skeleton')


def test_validate_refuses_duplicate_joint_names():
    """The engine hands weights back keyed by bone NAME. Two joints called
    the same thing make that ambiguous, and guessing would mis-weight the
    character silently."""
    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    a = cmds.ls(cmds.joint(name='wrist', p=(0, 0, 0)), long=True)[0]
    cmds.select(clear=True)
    grp = cmds.group(empty=True, name='other')
    cmds.select(grp, replace=True)
    # Same LEAF name, different DAG path -- which is exactly the ambiguity the
    # engine's name-keyed weights cannot survive. (Long names throughout here:
    # once two nodes are called 'wrist', the short name means nothing.)
    b = cmds.ls(cmds.joint(name='wrist', p=(0, 5, 0)), long=True)[0]
    cmds.select(clear=True)
    mesh = cmds.polyCube(name='m', h=6)[0]
    with fake_health():
        try:
            app.validate([a, b, mesh])
        except app.AutoSkinError as exc:
            assert 'share a name' in str(exc), exc
        else:
            raise AssertionError('accepted two joints with the same name')


def test_validate_refuses_a_rebind_on_a_posed_rig():
    """A rebind unbinds and re-creates the skinCluster (the price of a fast,
    undoable weight write). On a posed rig that would bake the POSE in as the
    new bind pose — and the engine would have been reading a posed character
    anyway. Stop and say so."""
    joints, mesh = _cube_and_joints()
    cmds.select(joints + [mesh], replace=True)
    cmds.skinCluster(toSelectedBones=True)
    cmds.select(clear=True)

    with fake_health():
        app.validate(joints + [mesh])              # at bind pose: fine

        cmds.rotate(35, 0, 0, joints[0])           # now pose it
        try:
            app.validate(joints + [mesh])
        except app.AutoSkinError as exc:
            assert 'bind pose' in str(exc), exc
        else:
            raise AssertionError('accepted a rebind on a posed rig')


# ── the vertex probe: what makes index-keyed weights legitimate ─────────────

def test_vertex_probe_records_mayas_world_positions_in_mayas_order():
    """The probe is how the backend re-proves, on the artist's own mesh, that
    bpy's vertex i is Maya's vertex i. If the probe itself were wrong, the
    proof would be worthless — so check it against Maya."""
    _, mesh = _cube_and_joints()
    cmds.move(3, 4, 5, mesh, relative=True)         # off origin, so it matters

    out = Path(cmds.internalVar(userTmpDir=True)) / 'probe_test.f32'
    n = app.write_vertex_probe(mesh, out)

    raw = out.read_bytes()
    assert n == 8, n                                # a cube
    assert len(raw) == n * 3 * 4, len(raw)          # 3 float32 per vertex
    got = struct.unpack('<' + 'f' * (n * 3), raw)

    for vi in range(n):
        want = cmds.pointPosition(f'{mesh}.vtx[{vi}]', world=True)
        have = got[vi * 3: vi * 3 + 3]
        assert all(abs(a - b) < 1e-4 for a, b in zip(want, have)), \
            (vi, want, have)
    out.unlink()


# ── the API weight write ────────────────────────────────────────────────────

def _bound_cube():
    joints, mesh = _cube_and_joints()
    skin = app.create_skin_cluster(mesh, joints)
    return joints, mesh, skin


def test_apply_weights_writes_exactly_what_it_is_given():
    joints, mesh, skin = _bound_cube()
    root, tip = joints
    # Bottom half to root, top half to tip. Cube vertex order is Maya's.
    weights = {'t_root': {str(v): 1.0 for v in range(4)},
               't_tip': {str(v): 1.0 for v in range(4, 8)}}

    app.apply_weights(mesh, skin, weights, 8)

    for v in range(8):
        vals = dict(zip(cmds.skinCluster(skin, q=True, influence=True),
                        cmds.skinPercent(skin, f'{mesh}.vtx[{v}]', q=True,
                                         value=True)))
        want = root if v < 4 else tip
        assert abs(vals[want] - 1.0) < 1e-4, (v, vals)
        assert abs(sum(vals.values()) - 1.0) < 1e-4, (v, vals)


def test_apply_weights_refuses_a_vertex_count_mismatch():
    """The one failure that would scramble a skin silently. It must be loud."""
    joints, mesh, skin = _bound_cube()
    try:
        app.apply_weights(mesh, skin, {'t_root': {'0': 1.0}}, 999)
    except app.AutoSkinError as exc:
        assert 'Refusing' in str(exc), exc
    else:
        raise AssertionError('applied weights generated for a different mesh')


def test_apply_weights_honors_a_locked_influence():
    """A lock means 'I weighted this by hand, leave it alone'. Its weights are
    carried across the rebind untouched and the prediction is normalized into
    what is left over — not written over the top."""
    joints, mesh, skin = _bound_cube()
    root, tip = joints

    # The artist pins vertex 0 at 25% root by hand, and locks root.
    cmds.skinPercent(skin, f'{mesh}.vtx[0]',
                     transformValue=[(root, 0.25), (tip, 0.75)])
    cmds.setAttr(f'{root}.liw', 1)
    preserved = app.preserve_locked(mesh, skin)
    assert list(preserved) == [cmds.ls(root, long=True)[0]], preserved

    # A real engine weights EVERY joint, locked or not: it does not know about
    # locks. Here it wants vertex 0 to be 90% root / 10% tip. It may not have
    # root — that is the artist's, pinned at 0.25 — so the prediction has to be
    # normalized into the 0.75 that is left, landing entirely on tip.
    app.apply_weights(mesh, skin,
                      {'t_root': {'0': 0.9}, 't_tip': {'0': 0.1}}, 8, preserved)

    infl = cmds.skinCluster(skin, q=True, influence=True)
    vals = dict(zip(infl, cmds.skinPercent(skin, f'{mesh}.vtx[0]', q=True,
                                           value=True)))
    assert abs(vals[root] - 0.25) < 1e-4, f"the artist's lock was overwritten: {vals}"
    assert abs(vals[tip] - 0.75) < 1e-4, f'the prediction was not renormalized into the remainder: {vals}'
    assert abs(sum(vals.values()) - 1.0) < 1e-4, vals


def _gapped_cluster():
    """The artist's cluster, not a freshly-bound one: six influences, then one
    removed from the middle, so the LOGICAL matrix indices ([0,1,3,4,5]) no
    longer match the PHYSICAL ones ([0..4]). Every AutoSkin test used to build
    a fresh cluster, where the two agree — which is exactly why the index bug
    survived all of them."""
    cmds.file(new=True, force=True)
    js = []
    for i in range(6):
        cmds.select(clear=True)
        js.append(cmds.joint(name=f'j{i}', p=(i * 2.0, 0, 0)))
    cmds.select(clear=True)
    mesh = cmds.polyPlane(name='m', w=10, h=2, sx=5, sy=1)[0]
    cmds.select(js + [mesh], replace=True)
    skin = cmds.skinCluster(toSelectedBones=True, maximumInfluences=6,
                            obeyMaxInfluences=False)[0]
    cmds.select(clear=True)
    cmds.skinPercent(skin, f'{mesh}.vtx[0]',
                     transformValue=[('j0', .10), ('j1', .20), ('j2', .0),
                                     ('j3', .30), ('j4', .15), ('j5', .25)])
    cmds.skinCluster(skin, edit=True, removeInfluence='j2')
    return js, mesh, skin


def test_preserve_locked_reads_the_right_influence_on_a_gapped_cluster():
    """REGRESSION (blocker, 2026-07-13). MFnSkinCluster.getWeights indexes
    influences PHYSICALLY; indexForInfluenceObject returns a LOGICAL index. On
    the artist's cluster (any removeInfluence leaves a gap) those differ, and
    preserve_locked silently returned ANOTHER JOINT'S weights — measured: asked
    for j3's 0.300, got j4's 0.150 — corrupting the very weights the lock
    existed to protect."""
    _, mesh, skin = _gapped_cluster()
    cmds.setAttr('j3.liw', 1)

    got = app.preserve_locked(mesh, skin)
    key = [k for k in got if app._leaf(k) == 'j3']
    assert key, f'j3 not preserved: {list(got)}'
    assert abs(got[key[0]][0] - 0.30) < 1e-5, (
        f"read {got[key[0]][0]:.3f} for j3 on vertex 0, expected 0.300 "
        f"(0.150 means it read j4 — the logical/physical index bug is back)")


def test_apply_weights_uses_physical_indices():
    """The other half of the same bug. setWeights ALSO indexes physically: hand
    it logical indices on a gapped cluster and it raises kInvalidParameter."""
    _, mesh, skin = _gapped_cluster()
    app.apply_weights(mesh, skin, {'j3': {'0': 1.0}}, 12)

    infl = cmds.skinCluster(skin, q=True, influence=True)
    vals = dict(zip(infl, cmds.skinPercent(skin, f'{mesh}.vtx[0]', q=True,
                                           value=True)))
    assert abs(vals['j3'] - 1.0) < 1e-4, f'weight landed on the wrong joint: {vals}'


def test_apply_weights_refuses_when_nothing_lands():
    """Zero generated weights is not success. Silently leaving the artist with a
    plain closest-distance bind, reported as an ML skin, is the worst outcome
    available."""
    joints, mesh, skin = _bound_cube()
    try:
        app.apply_weights(mesh, skin, {'not_a_joint': {'0': 1.0}}, 8)
    except app.AutoSkinError as exc:
        assert 'no usable weights' in str(exc), exc
    else:
        raise AssertionError('reported success having applied nothing')


def test_validate_refuses_generate_joints_on_multiple_meshes():
    """Generate Joints exports every selected mesh but probes only the first —
    a guaranteed vertex-count mismatch that used to surface AFTER the ~2-minute
    GPU run, having wasted it."""
    _, mesh = _cube_and_joints()
    second = cmds.polyCube(name='second', h=6)[0]
    with fake_health():
        try:
            app.validate([mesh, second], generate_joints=True)
        except app.AutoSkinError as exc:
            assert 'one skeleton for one mesh' in str(exc), exc
        else:
            raise AssertionError('accepted Generate Joints on two meshes')


def test_rebuild_joints_reports_mayas_renames():
    """Maya does not promise the name you asked for. The engine's weights are
    keyed by ITS names, so a rename that is not reported drops every weight for
    that joint on the floor."""
    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    cmds.joint(name='bone_0', p=(9, 9, 9))        # the name is already taken
    cmds.select(clear=True)

    specs = [{'name': 'bone_0', 'position': [0.0, 0.0, 0.0], 'parent': None}]
    made, renames = app.rebuild_joints(specs)

    assert renames['bone_0'] == app._leaf(made[0]), renames
    assert renames['bone_0'] != 'bone_0', (
        'fixture is wrong: Maya did not actually rename anything')


def test_apply_weights_leaves_unpredicted_vertices_normalized():
    """A vertex the engine said nothing about must keep the closest-joint bind,
    not collapse to zero weights (which would drag it to the origin)."""
    joints, mesh, skin = _bound_cube()
    app.apply_weights(mesh, skin, {'t_root': {'0': 1.0}}, 8)   # vertex 0 only

    for v in range(8):
        w = cmds.skinPercent(skin, f'{mesh}.vtx[{v}]', q=True, value=True)
        assert abs(sum(w) - 1.0) < 1e-3, (v, w)


# ── Generate Joints rebuild ─────────────────────────────────────────────────

def test_rebuild_joints_builds_positions_and_hierarchy_from_specs():
    """The backend sends joints as data, already in Maya's world space. bpy's
    bone ORIENTATIONS are not inherited — Maya builds its own."""
    cmds.file(new=True, force=True)
    specs = [
        {'name': 'bone_0', 'position': [0.0, 0.0, 0.0], 'parent': None},
        {'name': 'bone_1', 'position': [1.0, 5.0, 0.0], 'parent': 'bone_0'},
        {'name': 'bone_2', 'position': [2.0, 9.0, 1.0], 'parent': 'bone_1'},
    ]
    made, renames = app.rebuild_joints(specs)

    assert len(made) == 3, made
    assert renames == {n: n for n in ('bone_0', 'bone_1', 'bone_2')}, renames
    for j, spec in zip(made, specs):
        got = cmds.xform(j, q=True, ws=True, t=True)
        assert all(abs(g - x) < 1e-4 for g, x in zip(got, spec['position'])), \
            (j, got, spec)
    p1 = (cmds.listRelatives(made[1], parent=True) or [None])[0]
    p2 = (cmds.listRelatives(made[2], parent=True) or [None])[0]
    assert p1 == made[0].split('|')[-1], p1
    assert p2 == made[1].split('|')[-1], p2


# ── LIVE: the checkpoint ────────────────────────────────────────────────────

def _load_reggie_unbound():
    """Reggie as the artist has him: skeleton + mesh, NOT yet skinned."""
    cmds.file(new=True, force=True)
    cmds.file(REGGIE, i=True, type='FBX', ignoreVersion=True,
              mergeNamespacesOnClash=False, namespace=':')
    meshes = [cmds.listRelatives(s, parent=True, fullPath=True)[0]
              for s in cmds.ls(type='mesh', noIntermediate=True, long=True)]
    joints = cmds.ls(type='joint', long=True)
    for m in meshes:
        sc = app._skin_cluster_of(m)
        if sc:
            cmds.skinCluster(sc, edit=True, unbind=True)
    assert joints and meshes, (len(joints), len(meshes))
    return joints, meshes


def test_live_bind_skin_skins_reggie():
    joints, meshes = _load_reggie_unbound()
    mesh = meshes[0]
    assert not app._skin_cluster_of(mesh), 'fixture is already skinned'

    before_joints = len(cmds.ls(type='joint'))
    before_meshes = len(cmds.ls(type='mesh', noIntermediate=True))
    before_ns = set(cmds.namespaceInfo(listOnlyNamespaces=True) or [])

    r = app.bind_skin(selection=joints + [mesh])

    sc = app._skin_cluster_of(mesh)
    assert sc, 'no skinCluster after bind_skin'
    infl = cmds.skinCluster(sc, q=True, influence=True)
    assert len(infl) >= 70, f'only {len(infl)} influences'

    n = cmds.polyEvaluate(mesh, vertex=True)
    for vi in (0, n // 3, n // 2, n - 1):
        w = cmds.skinPercent(sc, f'{mesh}.vtx[{vi}]', q=True, value=True)
        assert abs(sum(w) - 1.0) < 1e-3, f'vtx {vi} sums to {sum(w)}'
        assert max(w) > 0.1, f'vtx {vi} has no meaningful influence'

    # NOTHING may be added to the scene. The old proxy-FBX path had to defend
    # against an imported rig leaking in; this path never imports one, and
    # that is exactly what is being asserted.
    assert len(cmds.ls(type='joint')) == before_joints, 'joints leaked'
    assert len(cmds.ls(type='mesh', noIntermediate=True)) == before_meshes, \
        'a mesh leaked into the scene'
    assert set(cmds.namespaceInfo(listOnlyNamespaces=True) or []) == before_ns, \
        'a namespace leaked into the scene'

    # The backend must have PROVED the vertex order on this mesh, not assumed it.
    fid = r.get('fidelity') or {}
    assert fid.get('residual') is not None, 'no fidelity check was reported'
    assert fid['residual'] < 1e-4, f'vertex-order residual {fid["residual"]}'

    print(f'     [live] skinned {r["verts"]} verts across {len(infl)} '
          f'influences; vertex order verified to '
          f'{100 * fid["residual"]:.6f}%; scene clean')


def test_live_two_meshes_both_get_skinned():
    joints, meshes = _load_reggie_unbound()
    mesh = meshes[0]
    second = cmds.duplicate(mesh, name='reggie_second')[0]

    app.bind_skin(selection=joints + [mesh, second])

    for m in (mesh, second):
        sc = app._skin_cluster_of(m)
        assert sc, f'{m} not skinned'
        w = cmds.skinPercent(sc, f'{m}.vtx[0]', q=True, value=True)
        assert abs(sum(w) - 1.0) < 1e-3, (m, sum(w))
    print('     [live] both meshes skinned, one generation each')


def test_live_undo_restores_the_scene():
    """The whole reason bind_skin only ever writes into a skinCluster it just
    created: the API weight write is invisible to Ctrl+Z, so undo has to take
    the NODE with it."""
    joints, meshes = _load_reggie_unbound()
    mesh = meshes[0]
    assert not app._skin_cluster_of(mesh)

    app.bind_skin(selection=joints + [mesh])
    assert app._skin_cluster_of(mesh), 'fixture: bind did not skin'

    cmds.undo()

    assert not app._skin_cluster_of(mesh), 'undo left the skinCluster behind'
    print('     [live] one undo removed the skin cleanly')


def test_live_undo_restores_an_earlier_skin_on_rebind():
    """The harder undo: AutoSkin over an EXISTING skin. Undo must put the old
    skinCluster back with its old weights, not merely remove the new one."""
    joints, meshes = _load_reggie_unbound()
    mesh = meshes[0]

    cmds.select(joints + [mesh], replace=True)
    old = cmds.skinCluster(toSelectedBones=True)[0]
    cmds.select(clear=True)
    before = cmds.skinPercent(old, f'{mesh}.vtx[100]', q=True, value=True)

    app.bind_skin(selection=joints + [mesh])
    after = app._skin_cluster_of(mesh)
    mid = cmds.skinPercent(after, f'{mesh}.vtx[100]', q=True, value=True)
    assert max(abs(a - b) for a, b in zip(before, mid)) > 1e-3, \
        'the rebind changed nothing — this test proves nothing'

    cmds.undo()

    sc = app._skin_cluster_of(mesh)
    assert sc, 'undo left the mesh unskinned'
    back = cmds.skinPercent(sc, f'{mesh}.vtx[100]', q=True, value=True)
    assert max(abs(a - b) for a, b in zip(before, back)) < 1e-5, \
        'undo did not restore the original weights'
    print('     [live] one undo restored the previous skin, exactly')


check('split_selection sorts joints from meshes', test_split_selection_sorts_joints_from_meshes)
check('validate demands joints unless generating', test_validate_demands_joints_unless_generating)
check('validate rejects joints when generating', test_validate_rejects_joints_when_generating)
check('validate refuses on an unhealthy backend', test_validate_refuses_on_an_unhealthy_backend)
check('validate refuses a mesh away from the joints',
      test_validate_refuses_a_mesh_away_from_joints)
check('validate refuses duplicate joint names', test_validate_refuses_duplicate_joint_names)
check('validate refuses a rebind on a posed rig',
      test_validate_refuses_a_rebind_on_a_posed_rig)
check('vertex probe records Maya world positions in Maya order',
      test_vertex_probe_records_mayas_world_positions_in_mayas_order)
check('apply_weights writes exactly what it is given',
      test_apply_weights_writes_exactly_what_it_is_given)
check('apply_weights refuses a vertex-count mismatch',
      test_apply_weights_refuses_a_vertex_count_mismatch)
check('apply_weights honors a locked influence',
      test_apply_weights_honors_a_locked_influence)
check('apply_weights leaves unpredicted vertices normalized',
      test_apply_weights_leaves_unpredicted_vertices_normalized)
check('BLOCKER preserve_locked reads the right influence on a GAPPED cluster',
      test_preserve_locked_reads_the_right_influence_on_a_gapped_cluster)
check('apply_weights uses PHYSICAL influence indices',
      test_apply_weights_uses_physical_indices)
check('apply_weights refuses when nothing lands',
      test_apply_weights_refuses_when_nothing_lands)
check('validate refuses Generate Joints on multiple meshes',
      test_validate_refuses_generate_joints_on_multiple_meshes)
check('rebuild_joints reports Maya renames', test_rebuild_joints_reports_mayas_renames)
check('rebuild_joints builds positions + hierarchy from specs',
      test_rebuild_joints_builds_positions_and_hierarchy_from_specs)

if LIVE:
    print('\n  --- LIVE (backend + GPU) ---')
    check('LIVE bind_skin skins Reggie', test_live_bind_skin_skins_reggie)
    check('LIVE two meshes both get skinned', test_live_two_meshes_both_get_skinned)
    check('LIVE undo restores the scene', test_live_undo_restores_the_scene)
    check('LIVE undo restores an earlier skin on rebind',
          test_live_undo_restores_an_earlier_skin_on_rebind)
else:
    print('\n  (live tests skipped — pass --live to run the checkpoint)')

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print('ALL PASS')
