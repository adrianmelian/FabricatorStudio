# maya_tools/rigging/fabricator/planar_align.py
"""Planar Align — straighten a mid joint (knee/elbow) on ONE axis while
keeping its deliberate bend on the other two.

The classic rigging fix: a leg or arm whose mid joint has picked up a
slight sideways error should be made straight when viewed head-on,
WITHOUT flattening the forward bend that's actually intentional. That
rules out a full snap onto the parent->child line (mgear/most "straighten"
tools do exactly that, and it eats the bend) — this is a single-axis
correction instead.

Math (world positions P=parent, M=mid, C=child):
  t = dot(M-P, C-P) / |C-P|^2, clamped to [0, 1] — the fraction along the
  P->C segment closest to M. The corrected M keeps its OTHER two
  components bit-identical to the input and replaces ONLY the chosen
  axis with lerp(P, C, t) on that axis.

Axis choice ('auto' default, or explicit 'x'/'y'/'z'): 'auto' picks the
axis of LEAST deviation between M and the P->C line, EXCLUDING the axis
the bone itself runs along. A knee pokes forward a lot (deliberate) and
sideways a little (the error), so least-deviation IS the error axis —
but only once the bone axis is off the table, because deviation there is
structurally zero rather than merely small. See pick_correction_axis for
the measured failure that exclusion prevents.

This reuses the SHAPE of simple_ik.switch_to_ik_match's PV-placement
math (project onto the chain line, then reason about the perpendicular
remainder) for a different purpose here — straightening one axis TOWARD
the line instead of placing a control OFF it. Implemented in plain
tuple/float math rather than om.MVector (switch_to_ik_match's choice)
so the pure-math section below has ZERO Maya imports and is
unit-testable with plain `py -3` — see the "Pure math" section marker
and maya_tools/rigging/fabricator/_dev/test_planar_align.py. Every
Maya-touching function below imports maya.cmds LOCALLY (the same
pattern maya_tools/export/skeletal_export_runner.py already uses) so
importing this module at all never requires a maya install.

What gets driven (CRITICAL — see armature.py's own module docstring):
the SOLO handle first, then the ctrl, then the bare joint. Driving the
solo handle rather than the ctrl is a correctness requirement, not a
preference: child ctrls are parented under their parent ctrl, so
writing the knee's CTRL drags the ankle and the whole subtree along
with it — the knee straightens and the lower leg slides sideways,
leaving the chain just as bent and everything below it offset. The solo
handle moves ONE joint and leaves its descendants standing, which is
exactly the counter-move a straighten needs. (Caught by Adrian in Maya,
2026-07-21; the ctrl path had looked right in every headless test
because those measured the mid joint alone.)

Ctrl vs joint (CRITICAL — see armature.py's own module docstring): the
Armature edit stage drives EVERY joint live — a pointConstraint on
authored/branch edges, an SC-IK + stretch on pure-aim edges — so writing
a joint's translate directly while a live Armature owns it gets stomped
on the next DG evaluation. So: if a live Armature exists and the mid
joint has a ctrl (naming `{jnt}_amt_CTL`; resolved via armature.
ctrl_for_joint, reused rather than re-deriving the suffix locally, per
that function's own docstring invitation), this moves the CTRL. Only a
ctrl-less joint (no live Armature at all, or a follow-ruled joint —
armature.py's Follow Rules section — which never gets a ctrl) gets its
translate written directly; that's the plain-skeleton / Joint Aimer
case.
"""
__author__ = "Adrian Melian"

# ─────────────────────────────────────────────
# Pure math — NO Maya import anywhere in this section. Every function
# takes/returns plain floats and 3-tuples so it is unit-testable with
# plain `py -3`, no mayapy and no maya.standalone.initialize() — the
# same "Maya-free" half of the house test idiom
# _dev/test_blueprint_version_stamp.py exercises for blueprint/schema.py,
# as opposed to simple_ik.py's own isolated-but-still-mayapy classmethod
# tests (_dev/test_simple_ik_pref_angle.py), which import maya.cmds at
# module level and so always need mayapy even for their "isolated" case.
# ─────────────────────────────────────────────

AXES = ('x', 'y', 'z')
_AXIS_INDEX = {'x': 0, 'y': 1, 'z': 2}

# Minimum |C - P| (Maya scene units) to treat the parent->child segment
# as non-degenerate. Below this there is no line to project onto.
_DEGENERATE_LEN_EPS = 1e-6


def lerp3(p: tuple, c: tuple, t: float) -> tuple:
    """Linear interpolation from 3-tuple `p` to 3-tuple `c` at `t`."""
    return tuple(p[i] + t * (c[i] - p[i]) for i in range(3))


def solve_projection_param(p: tuple, m: tuple, c: tuple) -> float:
    """t = dot(M-P, C-P) / |C-P|^2, clamped to [0, 1] — how far along the
    P->C segment the closest point to M sits.

    Raises ValueError if |C-P| is degenerate (parent and child
    coincide) — there is no line to project onto, and dividing by
    |C-P|^2 would blow up."""
    seg = tuple(c[i] - p[i] for i in range(3))
    seg_len_sq = sum(s * s for s in seg)
    if seg_len_sq < _DEGENERATE_LEN_EPS ** 2:
        raise ValueError(
            f"parent and child are coincident (|C-P|={seg_len_sq ** 0.5:.6g} "
            f"< {_DEGENERATE_LEN_EPS:.1e}) -- cannot project the mid joint "
            f"onto a zero-length parent-child line")
    to_m = tuple(m[i] - p[i] for i in range(3))
    dot = sum(to_m[i] * seg[i] for i in range(3))
    t = dot / seg_len_sq
    return max(0.0, min(1.0, t))


def axis_deviations(p: tuple, m: tuple, c: tuple, t: float) -> dict:
    """Per-axis |M - lerp(P, C, t)| — how far M sits from the straight
    parent->child line, measured independently on each world axis.

    Among the two axes the bone does NOT run along, the one with the
    SMALLEST deviation is the error axis: a knee's forward bend is a
    large, deliberate deviation on one axis; a sideways rig mistake is a
    small deviation on another. The bone axis itself always reads ~0 and
    must be excluded — see pick_correction_axis."""
    proj = lerp3(p, c, t)
    return {axis: abs(m[i] - proj[i]) for i, axis in enumerate(AXES)}


def bone_axis(p: tuple, c: tuple) -> str:
    """The world axis the parent->child segment most runs along."""
    seg = tuple(abs(c[i] - p[i]) for i in range(3))
    return max(AXES, key=lambda a: seg[_AXIS_INDEX[a]])


def pick_correction_axis(p: tuple, c: tuple, deviations: dict) -> str:
    """axis='auto': the error axis — least deviation, EXCLUDING the bone
    axis.

    Excluding the bone axis is not a refinement, it is required for
    correctness. Deviation on the axis the P->C segment runs along is
    structurally ~0, not incidentally small: for a segment along a world
    axis, t solves so that lerp(P, C, t) reproduces M's component on that
    axis exactly. So a plain least-deviation min() over all three axes
    ALWAYS selects the bone axis, and correcting along it is a guaranteed
    no-op — the tool silently does nothing on the single most common
    input there is, a leg or arm whose bone runs down a world axis.
    Measured: hip(10,90,0) knee(12,50,15) ankle(10,10,0) gives
    deviations x=2.0 y=0.0 z=15.0, so min() picks y and the 2.0 sideways
    error survives untouched.

    Among the two remaining axes, least deviation wins: a knee's forward
    poke is the large, deliberate one; the rig error is the small one.
    Ties favor x, then y, then z (AXES order via min()'s leftmost-wins
    rule) — deterministic, never a coin flip."""
    bone = bone_axis(p, c)
    return min((a for a in AXES if a != bone), key=lambda a: deviations[a])


def corrected_mid_position(p: tuple, m: tuple, c: tuple, t: float,
                            axis: str) -> tuple:
    """M with ONLY `axis` replaced by lerp(P, C, t) on that axis. The
    other two components are returned bit-identical to `m` — this is
    the property that keeps a knee's deliberate forward bend intact
    while erasing only the sideways error (verified by
    _dev/test_planar_align.py's key property test)."""
    if axis not in _AXIS_INDEX:
        raise ValueError(f"axis must be one of {AXES}, got {axis!r}")
    proj = lerp3(p, c, t)
    idx = _AXIS_INDEX[axis]
    out = list(m)
    out[idx] = proj[idx]
    return tuple(out)


# ─────────────────────────────────────────────
# Maya resolution + mutation. Local `import maya...` inside every
# function below — never at module level — so the pure-math section
# above stays importable with no maya module on sys.path at all.
# ─────────────────────────────────────────────

_CTRL_SUFFIX = '_amt_CTL'   # armature.py's ctrl naming contract
_SOLO_SUFFIX = '_amtSolo'   # ... and its solo handle


def _resolve_ctrl_to_joint(node: str) -> str:
    """Resolve an Armature ctrl selection back to the joint it drives.

    Ctrl naming is armature.py's own contract: `{joint}_amt_CTL` (the
    forward direction of this mapping is armature.ctrl_for_joint). This
    is the reverse, which armature.py exposes no helper for. Ctrls and
    their joints live in different parts of the DAG (armature.py's
    docstring: "Ctrl hierarchy mirrors the joint hierarchy", not the
    joint hierarchy itself) so the ctrl's own long path is never the
    joint's — strip the suffix off the SHORT name only, then confirm a
    joint by that name actually exists before trusting it. Anything that
    isn't a `_amt_CTL`-suffixed transform passes through unchanged
    (assumed to already be a joint; _resolve_chain validates that)."""
    import maya.cmds as cmds
    short = node.rsplit('|', 1)[-1]
    # Solo handle included: during a marking-menu swap the cage is the
    # only visible manipulator on that joint, so it is what the rigger
    # has selected when they reach for this tool.
    for suffix in (_CTRL_SUFFIX, _SOLO_SUFFIX):
        if short.endswith(suffix):
            candidate = short[:-len(suffix)]
            if (cmds.objExists(candidate)
                    and cmds.nodeType(candidate) == 'joint'):
                return candidate
    return node


def _resolve_selection_nodes(mid) -> list:
    """Normalize `mid` (None, a single node string, or a list/tuple of
    node strings) into a raw selection list of 1 or 3 entries. Ctrl->
    joint resolution happens one level up in _resolve_chain, so error
    messages here can quote exactly what the caller selected."""
    import maya.cmds as cmds
    if mid is None:
        sel = cmds.ls(selection=True, long=True) or []
    elif isinstance(mid, str):
        sel = [mid]
    else:
        sel = list(mid)

    if not sel:
        raise RuntimeError(
            "align_mid_joint: nothing selected and no `mid` argument "
            "given -- select the mid joint (or its Armature ctrl), or "
            "the parent/mid/child trio, then run again.")
    if len(sel) not in (1, 3):
        raise RuntimeError(
            f"align_mid_joint: expected 1 node (mid joint or its "
            f"Armature ctrl) or 3 nodes (parent, mid, child) selected; "
            f"got {len(sel)}: {sel}")
    return sel


def _resolve_chain(mid) -> tuple:
    """Resolve `mid` into (parent_joint, mid_joint, child_joint).

    ONE node: the mid joint (or its ctrl, resolved via
    _resolve_ctrl_to_joint) — parent and child are INFERRED from the
    joint's one parent joint and one child joint. Zero or 2+ children is
    a hard error (never a guess): the caller must disambiguate by
    selecting the trio explicitly instead.

    THREE nodes: sorted by DAG depth into parent/mid/child (same
    depth-sort idiom actions/simple_ik.py's _resolve_simple_ik_ctrls
    uses for its fk_ctrls list), then verified to actually form that
    chain — mid's parent must be the resolved parent, child's parent
    must be the resolved mid. A same-depth or unrelated trio raises
    rather than silently computing garbage from a wrong order."""
    import maya.cmds as cmds

    raw = _resolve_selection_nodes(mid)
    resolved = [_resolve_ctrl_to_joint(n) for n in raw]

    if len(resolved) == 1:
        mid_jnt = resolved[0]
        if not cmds.objExists(mid_jnt) or cmds.nodeType(mid_jnt) != 'joint':
            raise RuntimeError(
                f"align_mid_joint: {raw[0]!r} does not resolve to a "
                f"joint (not a joint, and not an Armature ctrl either -- "
                f"ctrl names are `{{joint}}{_CTRL_SUFFIX}`)")
        parents = cmds.listRelatives(mid_jnt, parent=True, type='joint') or []
        children = cmds.listRelatives(mid_jnt, children=True, type='joint') or []
        if not parents:
            raise RuntimeError(
                f"align_mid_joint: {mid_jnt!r} has no parent joint -- "
                f"select parent, mid and child explicitly (3-node form) "
                f"instead of relying on single-node inference.")
        if not children:
            raise RuntimeError(
                f"align_mid_joint: {mid_jnt!r} has no child joint -- "
                f"nothing to straighten against.")
        if len(children) > 1:
            # A knee on a UE-style leg has the ankle AND its calf
            # twists as children, which used to make the one-node form
            # unusable on exactly the joint it was built for. Same
            # rule aim_all_at_child uses (deepest subtree, longest
            # bone tiebreak), imported rather than re-implemented.
            from maya_tools.rigging.joint_orient import joint_orient_app as joa
            chain_child = joa.chain_continuation_child(mid_jnt)
            if not chain_child:
                raise RuntimeError(
                    f"align_mid_joint: {mid_jnt!r} has {len(children)} "
                    f"child joints ({', '.join(children)}) with no unique "
                    f"chain continuation (equal subtree depth AND bone "
                    f"length). Select parent, mid and child explicitly "
                    f"(3-node form).")
            return parents[0], mid_jnt, chain_child
        return parents[0], mid_jnt, children[0]

    # len(resolved) == 3 (the only other case _resolve_selection_nodes allows)
    for raw_n, n in zip(raw, resolved):
        if not cmds.objExists(n) or cmds.nodeType(n) != 'joint':
            raise RuntimeError(
                f"align_mid_joint: {raw_n!r} does not resolve to a joint")

    ordered = sorted(
        resolved, key=lambda n: len(cmds.ls(n, long=True)[0].split('|')))
    parent_jnt, mid_jnt, child_jnt = ordered

    mid_parents = cmds.listRelatives(mid_jnt, parent=True, type='joint') or []
    mid_actual_parent = mid_parents[0] if mid_parents else None
    if mid_actual_parent != parent_jnt:
        raise RuntimeError(
            f"align_mid_joint: the 3 selected joints don't form a "
            f"parent->mid->child chain -- {mid_jnt!r}'s parent is "
            f"{mid_actual_parent!r}, not {parent_jnt!r} (DAG-depth sort "
            f"picked a wrong order because the selection isn't a "
            f"straight chain)")

    child_parents = cmds.listRelatives(child_jnt, parent=True, type='joint') or []
    child_actual_parent = child_parents[0] if child_parents else None
    if child_actual_parent != mid_jnt:
        raise RuntimeError(
            f"align_mid_joint: the 3 selected joints don't form a "
            f"parent->mid->child chain -- {child_jnt!r}'s parent is "
            f"{child_actual_parent!r}, not {mid_jnt!r}")

    return parent_jnt, mid_jnt, child_jnt


def align_mid_joint(mid=None, axis: str = 'auto') -> dict:
    """Straighten a mid joint (knee/elbow) on ONE axis, keeping its
    deliberate bend on the other two. Headless-callable, no UI imports.

    Args:
        mid: None (read current selection), a single node name (the mid
            joint or its Armature ctrl), or a list/tuple of 3 node names
            (parent, mid, child in any order). See _resolve_chain.
        axis: 'auto' (default — least-deviation axis, see module
            docstring) or an explicit 'x' / 'y' / 'z'.

    Wrapped in undo_chunk (one undo step). Always logs (cmds.warning)
    the chosen axis and all three deviation magnitudes, auto or
    explicit, so an auto pick is never silent.

    Returns a dict: parent/mid/child (resolved joint names), axis
    (chosen), deviations (per-axis dict), delta (3-tuple applied to M),
    t (clamped projection param), drove ('ctrl' or 'joint'), and
    driven_node (the actual node whose translate was written)."""
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    from maya_tools.framework.decorators import undo_chunk

    axis_arg = axis.lower() if isinstance(axis, str) else axis
    if axis_arg != 'auto' and axis_arg not in _AXIS_INDEX:
        raise RuntimeError(
            f"align_mid_joint: axis must be 'auto', 'x', 'y' or 'z'; "
            f"got {axis!r}")

    with undo_chunk('Straighten Mid Joint'):
        parent_jnt, mid_jnt, child_jnt = _resolve_chain(mid)

        p = tuple(cmds.xform(parent_jnt, q=True, ws=True, t=True))
        m = tuple(cmds.xform(mid_jnt, q=True, ws=True, t=True))
        c = tuple(cmds.xform(child_jnt, q=True, ws=True, t=True))

        try:
            t = solve_projection_param(p, m, c)
        except ValueError as exc:
            raise RuntimeError(f"align_mid_joint: {exc}") from exc

        deviations = axis_deviations(p, m, c, t)
        if axis_arg == 'auto':
            chosen_axis = pick_correction_axis(p, c, deviations)
            mode = 'auto-picked'
        else:
            chosen_axis = axis_arg
            mode = 'explicit'

        corrected = corrected_mid_position(p, m, c, t, chosen_axis)
        delta = tuple(corrected[i] - m[i] for i in range(3))

        # displayInfo, not cmds.warning: this is the routine success
        # trace of an operation the rigger just asked for. A warning on
        # every successful run trains people to ignore warnings.
        om.MGlobal.displayInfo(
            f"align_mid_joint: straightening {mid_jnt!r} on axis "
            f"{chosen_axis!r} ({mode}, bone axis {bone_axis(p, c)!r} "
            f"excluded) -- deviations x={deviations['x']:.4f} "
            f"y={deviations['y']:.4f} z={deviations['z']:.4f}")

        # Drive the SOLO handle when there is one — NOT the ctrl (fixed
        # 2026-07-21 after Adrian caught it in Maya). Child ctrls are
        # parented under their parent ctrl, so writing the knee's CTRL
        # drags the ankle and everything below it rigidly along: the
        # knee straightens and the whole lower leg slides sideways with
        # it, which reads as "the mid joint moved but nothing counter-
        # moved". The solo handle is precisely the manipulator that
        # moves one joint and leaves its subtree standing, so the ankle
        # holds station and the bones re-aim around the corrected knee.
        # Ctrl is the fallback for a pre-solo Armature; the bare joint
        # for no Armature at all (plain skeleton / Joint Aimer case).
        # Lazy import, same reason joint_orient_app._resolve_convention
        # lazily imports fabricator (avoids a load-time circular import).
        from maya_tools.rigging.fabricator import armature as amt
        solo = amt.solo_handle_for_joint(mid_jnt)
        ctrl = amt.ctrl_for_joint(mid_jnt)
        if solo:
            cmds.xform(solo, ws=True, t=corrected)
            drove, driven_node = 'solo', solo
        elif ctrl:
            cmds.xform(ctrl, ws=True, t=corrected)
            drove, driven_node = 'ctrl', ctrl
        else:
            cmds.xform(mid_jnt, ws=True, t=corrected)
            drove, driven_node = 'joint', mid_jnt

    return {
        'parent': parent_jnt,
        'mid': mid_jnt,
        'child': child_jnt,
        'axis': chosen_axis,
        'deviations': deviations,
        'delta': delta,
        't': t,
        'drove': drove,
        'driven_node': driven_node,
    }
