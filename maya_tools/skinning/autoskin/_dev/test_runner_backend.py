"""AutoSkin backend math tests — RUN IN THE INFERENCE VENV (needs numpy/scipy):

  %LOCALAPPDATA%/FabricatorStudio/autoskin/venv/Scripts/python.exe \
      maya_tools/skinning/autoskin/_dev/test_runner_backend.py

The numerical core of the harvest: Umeyama alignment and position-keyed
bone mapping. These are the two places a silent error would not crash
anything — it would just produce a subtly wrong skin, which is far worse.
So they get exact, synthetic ground truth: build a transform, apply it,
and demand it back.

The bpy-dependent stages are proven end-to-end against a real character
instead (B1 checkpoint), where a fake scene would prove nothing.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from maya_tools.skinning.autoskin import runner_backend as rb

FAILURES = []


PASSED = []


def check(name, fn):
    try:
        fn()
        PASSED.append(name)
        print(f'  ok: {name}')
    except Exception as exc:
        import traceback
        FAILURES.append(f'{name}: {exc!r}')
        print(f'FAIL: {name}: {exc!r}')
        traceback.print_exc()


def _rot(ax, ay, az):
    cx, sx = np.cos(ax), np.sin(ax)
    cy, sy = np.cos(ay), np.sin(ay)
    cz, sz = np.cos(az), np.sin(az)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def test_umeyama_recovers_a_known_similarity():
    """Build scale+rotation+translation, apply it, demand it back."""
    rng = np.random.default_rng(7)
    src = rng.normal(size=(60, 3))
    s_true, R_true = 2.5, _rot(0.3, -0.7, 1.1)
    t_true = np.array([4.0, -2.0, 9.0])
    dst = (s_true * (R_true @ src.T)).T + t_true

    s, R, t = rb.umeyama(src, dst)
    assert abs(s - s_true) < 1e-9, s
    assert np.allclose(R, R_true, atol=1e-9)
    assert np.allclose(t, t_true, atol=1e-9)
    assert np.allclose(rb.apply_similarity(src, s, R, t), dst, atol=1e-9)


def test_umeyama_is_identity_on_identical_clouds():
    pts = np.random.default_rng(1).normal(size=(40, 3))
    s, R, t = rb.umeyama(pts, pts)
    assert abs(s - 1.0) < 1e-9
    assert np.allclose(R, np.eye(3), atol=1e-9)
    assert np.allclose(t, 0.0, atol=1e-9)


def test_umeyama_never_returns_a_reflection():
    """A mirrored fit would flip the character's skin left-for-right. The
    SVD sign correction must keep det(R) = +1."""
    rng = np.random.default_rng(3)
    src = rng.normal(size=(50, 3))
    dst = src.copy()
    dst[:, 0] *= -1.0                       # a pure reflection
    _, R, _ = rb.umeyama(src, dst)
    assert np.linalg.det(R) > 0, f'reflection returned: det={np.linalg.det(R)}'


def test_bone_map_is_a_bijection_across_a_scale_gap():
    """The engine hands back bone_0..bone_N in ITS scale. Mapping must
    still land 1:1 on our joints — the eval got 80/80, 0 collisions."""
    rng = np.random.default_rng(11)
    user_pos = rng.normal(size=(80, 3)) * 10.0
    user_names = [f'joint_{i}' for i in range(80)]
    # Same skeleton, different scale/offset, generic names, shuffled order.
    order = rng.permutation(80)
    pred_pos = user_pos[order] * 0.013 + np.array([100.0, -50.0, 7.0])
    pred_names = [f'bone_{i}' for i in range(80)]

    mapping = rb.map_predicted_bones(pred_names, pred_pos, user_names, user_pos)

    assert len(mapping) == 80
    assert len(set(mapping.values())) == 80, 'collisions — not a bijection'
    for i, o in enumerate(order):
        assert mapping[f'bone_{i}'] == f'joint_{o}', (i, o, mapping[f'bone_{i}'])


def test_max_influences_is_four():
    """Engines take four. If this ever drifts, weights silently exceed
    what the exporter can carry."""
    assert rb.MAX_INFLUENCES == 4


# ── the vertex-index contract ───────────────────────────────────────────────
#
# fit_to_maya is the safety net under the whole data-only architecture: the
# weights go home as vertex INDICES, and this is the thing that earns the
# right to send them. It gets a real bpy mesh and a real probe file.

import tempfile

import bpy


def _bpy_cube_and_probe(similarity, scramble=False):
    """A bpy mesh, plus a probe file holding 'Maya's' view of the same mesh:
    the same vertices, moved by a known similarity. Optionally scrambled, to
    stand in for a round-trip that reordered them."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3)   # 642 verts
    obj = bpy.context.active_object

    Q = rb._mesh_world_verts(obj)
    s, R, t = similarity
    P = rb.apply_similarity(Q, s, R, t)
    if scramble:
        P = P[np.random.default_rng(0).permutation(len(P))]

    path = Path(tempfile.mkdtemp()) / 'probe.f32'
    path.write_bytes(P.astype('<f4').tobytes())
    return obj, path


def test_fit_to_maya_recovers_the_transform_when_the_order_survives():
    want = (100.0, _rot(np.pi / 2, 0.0, 0.0), np.array([3.0, -2.0, 7.0]))
    obj, probe = _bpy_cube_and_probe(want)

    (s, R, t), fid = rb.fit_to_maya(obj, probe)

    assert abs(s - want[0]) < 1e-6, s
    assert np.allclose(R, want[1], atol=1e-6), R
    assert np.allclose(t, want[2], atol=1e-4), t
    assert fid['residual'] < 1e-6, fid


def test_fit_to_maya_refuses_a_scrambled_vertex_order():
    """THE failure this exists to catch. A reordered mesh must stop the job,
    not quietly produce a scrambled skin."""
    obj, probe = _bpy_cube_and_probe(
        (100.0, _rot(np.pi / 2, 0.0, 0.0), np.array([3.0, -2.0, 7.0])),
        scramble=True)
    try:
        rb.fit_to_maya(obj, probe)
    except RuntimeError as exc:
        assert 'vertex order' in str(exc), exc
    else:
        raise AssertionError('accepted a scrambled vertex order')


def test_fit_to_maya_refuses_a_vertex_count_mismatch():
    obj, probe = _bpy_cube_and_probe((1.0, np.eye(3), np.zeros(3)))
    probe.write_bytes(probe.read_bytes()[:-12])     # drop a vertex
    try:
        rb.fit_to_maya(obj, probe)
    except RuntimeError as exc:
        assert 'vertices' in str(exc), exc
    else:
        raise AssertionError('accepted a probe of the wrong size')


def _pred_and_user(rotation, scale, offset=(1.7, -0.9, 2.4)):
    """A 'user' mesh, and the 'engine's' version of it: the same geometry moved
    by a known rotation + uniform scale, with an armature inside it. Recovering
    that transform is the entire job of adopt_predicted_skeleton."""
    bpy.ops.wm.read_factory_settings(use_empty=True)

    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.3, depth=2.0)
    user = bpy.context.active_object
    user.name = 'user_mesh'

    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.3, depth=2.0)
    pred = bpy.context.active_object
    pred.name = 'pred_mesh'

    bpy.ops.object.armature_add()
    arm = bpy.context.active_object
    arm.name = 'pred_arm'

    # Where the armature sits BEFORE the engine's frame is applied — this is
    # what adopt_predicted_skeleton has to give us back.
    home = rb._bone_positions(arm)[1].copy()

    # Move the engine's mesh AND its armature into the engine's canonical frame.
    # The OFFSET is load-bearing in this fixture: Blender's default armature has
    # one bone whose head is at the ORIGIN, and the origin is a fixed point of
    # any rotation+scale. Without a translation the armature would "return home"
    # for free and the test would pass whatever the code did.
    from mathutils import Matrix
    R, s, o = rotation, scale, offset
    M = Matrix(((s * R[0][0], s * R[0][1], s * R[0][2], o[0]),
                (s * R[1][0], s * R[1][1], s * R[1][2], o[1]),
                (s * R[2][0], s * R[2][1], s * R[2][2], o[2]),
                (0.0, 0.0, 0.0, 1.0)))
    pred.matrix_world = M @ pred.matrix_world
    arm.matrix_world = M @ arm.matrix_world

    # The engine's mesh needs a vertex group or _skinned_mesh would reject it;
    # adopt_predicted_skeleton does not care, but keep the fixture honest.
    pred.vertex_groups.new(name='Bone')
    return pred, arm, user, home


def test_adopt_recovers_a_rotated_rescaled_engine_frame():
    """THE Generate Joints bug (2026-07-13). The engine canonicalizes what it is
    given, so it can hand the rig back with the AXES PERMUTED — measured: the
    same character in two orientations came back with Y and Z swapped. The old
    code ran a similarity ICP to find that transform and collapsed a 50-joint
    skeleton into an 18-unit blob inside a 97-unit character."""
    # A PROPER axis-permuting rotation: 90 degrees about X, which swaps Y and Z
    # exactly as the engine did to Adrian's character.
    rot_x90 = [[1.0, 0.0, 0.0],
               [0.0, 0.0, -1.0],
               [0.0, 1.0, 0.0]]
    pred, arm, user, home = _pred_and_user(rot_x90, 2.063)

    displaced = rb._bone_positions(arm)[1]
    assert not np.allclose(displaced, home, atol=1e-3), \
        'fixture is broken: the armature was never displaced'

    rb.adopt_predicted_skeleton(pred, arm, user)
    after = rb._bone_positions(arm)[1]

    assert np.allclose(after, home, atol=1e-3), \
        f'the armature did not come home: {after} vs {home}'
    # The engine's mesh IS the user's mesh: aligned, they must coincide.
    d = np.abs(rb._mesh_world_verts(pred) - rb._mesh_world_verts(user)).max()
    assert d < 1e-3, f'the engine mesh did not land on the user mesh (off by {d})'


def test_adopt_refuses_rather_than_collapse():
    """A mesh the engine's output genuinely does not match must STOP the job. The
    old ICP would happily shrink it to a dot and report a clean residual."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.3, depth=2.0)
    user = bpy.context.active_object
    bpy.ops.mesh.primitive_monkey_add()          # a completely different shape
    pred = bpy.context.active_object
    pred.vertex_groups.new(name='Bone')
    bpy.ops.object.armature_add()
    arm = bpy.context.active_object

    try:
        rb.adopt_predicted_skeleton(pred, arm, user)
    except RuntimeError as exc:
        assert 'not' in str(exc).lower(), exc
    else:
        raise AssertionError('aligned a mesh that does not match, instead of '
                             'refusing')


check('umeyama recovers a known similarity', test_umeyama_recovers_a_known_similarity)
check('umeyama is identity on identical clouds', test_umeyama_is_identity_on_identical_clouds)
check('umeyama never returns a reflection', test_umeyama_never_returns_a_reflection)
check('fit_to_maya recovers the transform when the order survives',
      test_fit_to_maya_recovers_the_transform_when_the_order_survives)
check('fit_to_maya REFUSES a scrambled vertex order',
      test_fit_to_maya_refuses_a_scrambled_vertex_order)
check('fit_to_maya refuses a vertex-count mismatch',
      test_fit_to_maya_refuses_a_vertex_count_mismatch)
check('bone map is a bijection across a scale gap',
      test_bone_map_is_a_bijection_across_a_scale_gap)
check('max influences is four', test_max_influences_is_four)
check('adopt_predicted_skeleton recovers a ROTATED, rescaled engine frame',
      test_adopt_recovers_a_rotated_rescaled_engine_frame)
check('adopt_predicted_skeleton REFUSES rather than collapse',
      test_adopt_refuses_rather_than_collapse)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S)')
    sys.exit(1)
print(f'ALL PASS ({len(PASSED)})')
