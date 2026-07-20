# Python/maya_tools/rigging/fabricator/follow_rules.py
"""Follower-joint primitive — armature-level (SPEC 2026-07-09 Limbs +
Follower Joints, section 3.1). Any joint may carry ONE follow rule:

  distribute(A, B, t) — position = lerp(A.worldPos, B.worldPos, t).
                         Twist joints: t=1/3, 2/3 along elbow->wrist.
                         Orientation stays the aimers' job (twist joints get it
                         via the aimer's "Parent" target + 180 RZ, joint_orient).
  match(X)            — position AND orientation snap to X.
                         IK hand joints: match(wrist).

Persistence model (mirrors joint_orient_app.py's aimer state + the CRUD
primitives in utils/maya/network_nodes.py and fabricator/nodes.py): each
ruled joint gets its OWN small `network` node, marked with the
`fab_follow_rule` attr so it's discoverable via
`network_nodes.find_marked_nodes`. The node is wired to the rest of the
scene by MESSAGE connections only — never name strings:

    joint.message  -> rule_node.owner_joint   (single)
    A/B or X.message -> rule_node.targets[i]  (multi, explicit index —
                                                order is the contract:
                                                targets[0]=A, targets[1]=B)

Because both ends are message connections, renaming the ruled joint OR
any of its targets never breaks the rule — `get_follow_rule` and
`resolve_position`/`resolve_orientation` always resolve through the live
connections, never through a stored name. `_find_rule_node` locates a
joint's rule node the same way: by walking the joint's own OUTGOING
message connections for one landing on `owner_joint` of a
`fab_follow_rule`-marked node, not by guessing a node name from the
joint's current name.

Scope: this module is a PURE function of live scene state. It has no
callbacks, no scriptJobs, and does not drive the built rig — it only
evaluates ARMATURE/EDIT-mode positions (SPEC 3.1). The armature's live
"move a ctrl, followers update" hook (Task 1.2,
maya_tools/rigging/fabricator/armature.py) calls `evaluate()` on its
own update path; this module doesn't schedule anything itself.

`add_change_listener`/`_notify_change` are the one exception, and a
narrow one: a plain synchronous notification (not scene-observation)
so that hook can invalidate its own cached ruled-joint set exactly
when set_follow_rule/clear_follow_rule change something, instead of
re-scanning the scene on every drag tick to notice.
"""
__author__ = "Adrian Melian"

import traceback

import maya.cmds as cmds

from maya_tools.utils.maya import network_nodes as nn


_MARKER = 'fab_follow_rule'
_MARKER_VALUE = 'follow_rule'
_NODE_SUFFIX = '_follow_rule'

_KINDS = ('distribute', 'match')

# Change notification — NOT scene-observation by this module (it still
# has no callbacks/scriptJobs of its own, per the module docstring): a
# plain synchronous notification point so a caller that DOES own a
# live-update path (the armature hook, Task 1.2) can invalidate its own
# cached ruled-joint set exactly when a rule is created/cleared, rather
# than re-scanning the scene on every drag tick to notice the change.
_LISTENERS: list = []


def add_change_listener(fn) -> None:
    """Register fn(joint: str) to be called whenever set_follow_rule or
    clear_follow_rule changes joint's rule (the joint that was just
    ruled/cleared — NOT necessarily still ruled by the time fn runs;
    call get_follow_rule(joint) to check). Idempotent — re-adding the
    same callable is a no-op."""
    if fn not in _LISTENERS:
        _LISTENERS.append(fn)


def remove_change_listener(fn) -> None:
    """No-op if fn was never registered."""
    if fn in _LISTENERS:
        _LISTENERS.remove(fn)


def _notify_change(joint: str) -> None:
    for fn in list(_LISTENERS):
        try:
            fn(joint)
        except Exception:
            # A listener hiccup must never break a rule edit, but it
            # must still leave a diagnostic trail (every sibling
            # except-block added alongside this hook does the same —
            # see armature.py's _follow_cache_dirty/_follow_on_ctrl_
            # attr_changed/_follow_watch_ctrls).
            traceback.print_exc()


# ─────────────────────────────────────────────
# Name helpers
# ─────────────────────────────────────────────

def _short(name: str) -> str:
    return name.split('|')[-1].split(':')[-1]


# ─────────────────────────────────────────────
# Rule-node lookup (rename-proof: via message connections, never names)
# ─────────────────────────────────────────────

def _find_rule_node(joint: str) -> str | None:
    """Return the follow-rule network node connected to joint, or None.

    Walks joint.message's OUTGOING connections (Maya preserves these
    across a rename of either end) looking for one landing on a
    `fab_follow_rule`-marked node's `owner_joint` attr.
    """
    if not cmds.objExists(joint):
        return None
    conns = cmds.listConnections(f'{joint}.message', source=False,
                                 destination=True, plugs=True) or []
    for plug in conns:
        if not plug.endswith('.owner_joint'):
            continue
        node = plug.rsplit('.', 1)[0]
        if cmds.attributeQuery(_MARKER, node=node, exists=True):
            return node
    return None


def _get_ordered_targets(node: str) -> dict:
    """Return targets[] sources keyed by explicit index (0, 1, ...).

    Order is contract-significant for 'distribute' (index 0=A, index
    1=B) so this reads multi indices directly rather than relying on
    listConnections' incidental ordering. An index whose connection has
    gone (e.g. its target joint was deleted — Maya leaves the multi
    index in place and just drops the connection) maps to None rather
    than being silently dropped from the result. See
    _resolve_targets_or_raise, the only caller, for how that becomes a
    clear error instead of a downstream unpack/index crash.
    """
    if not cmds.attributeQuery('targets', node=node, exists=True):
        return {}
    indices = cmds.getAttr(f'{node}.targets', multiIndices=True) or []
    out = {}
    for i in indices:
        srcs = cmds.listConnections(f'{node}.targets[{i}]', source=True,
                                    destination=False) or []
        out[i] = srcs[0] if srcs else None
    return out


_TARGET_LABELS = {'distribute': ('A', 'B'), 'match': ('X',)}


def _resolve_targets_or_raise(joint: str, node: str, kind: str) -> list:
    """Read node's targets[] in order, validated against `kind`'s
    expected count.

    A deleted target joint doesn't remove its multi index — it just
    breaks the connection — so an ordinary scene edit (deleting a
    joint that happens to be a follow-rule target) must not silently
    shrink the resolved list into a cryptic unpack/index error further
    downstream. Raises a clear RuntimeError naming the ruled joint, the
    rule kind, and which target (by its documented label — A/B for
    'distribute', X for 'match') is missing.
    """
    labels = _TARGET_LABELS.get(kind, ())
    resolved = _get_ordered_targets(node)
    for i, label in enumerate(labels):
        if resolved.get(i) is None:
            raise RuntimeError(
                f"follow rule on '{joint}': {kind} target {label} no "
                f"longer exists.")
    return [resolved[i] for i in range(len(labels))]


# ─────────────────────────────────────────────
# Public API — CRUD
# ─────────────────────────────────────────────

def set_follow_rule(joint: str, kind: str, targets, t: float = None,
                    linked: bool = False) -> str:
    """Create (or replace) joint's follow rule.

    Args:
        joint: the follower joint.
        kind: 'distribute' or 'match'.
        targets: (A, B) for 'distribute'; (X,) for 'match'.
        t: 0-1, clamped. 'distribute' only; defaults to 0.5 when omitted.
           Ignored (stored as None) for 'match'.
        linked: reserved for Task 1.3 (FollowJoint-derived rules show as
            "linked" in the Follow block). Defaults False; just persisted
            here, not otherwise consumed by this module.

    Re-setting replaces the rule wholesale (old rule node deleted first,
    so no stale multi-index/target ever survives a re-author).

    Raises:
        RuntimeError: joint or a target doesn't exist.
        ValueError: kind isn't 'distribute'/'match', or targets' length
            doesn't match what that kind requires.

    Returns:
        The created rule network node's name.
    """
    if not cmds.objExists(joint):
        raise RuntimeError(f"set_follow_rule: joint '{joint}' does not exist.")
    if kind not in _KINDS:
        raise ValueError(
            f"set_follow_rule: kind must be one of {_KINDS}, got {kind!r}.")

    targets = tuple(targets)
    if kind == 'distribute' and len(targets) != 2:
        raise ValueError(
            f"set_follow_rule: 'distribute' requires targets=(A, B) "
            f"(2 targets), got {len(targets)}.")
    if kind == 'match' and len(targets) != 1:
        raise ValueError(
            f"set_follow_rule: 'match' requires targets=(X,) "
            f"(1 target), got {len(targets)}.")

    missing = [tg for tg in targets if not cmds.objExists(tg)]
    if missing:
        raise RuntimeError(
            f"set_follow_rule: target(s) do not exist: {missing}.")

    if kind == 'distribute':
        t_value = 0.5 if t is None else max(0.0, min(1.0, float(t)))
    else:
        t_value = None

    # Replace: clean slate. Validation above already ran, so a bad call
    # never destroys a previously-valid rule.
    clear_follow_rule(joint)

    node = nn.create_marked_node(
        _MARKER, f'{_short(joint)}{_NODE_SUFFIX}', marker_value=_MARKER_VALUE)
    nn.ensure_string_attr(node, 'kind', default=kind)

    nn.ensure_message_attr(node, 'owner_joint')
    nn.connect_message(joint, node, 'owner_joint')

    nn.ensure_message_attr(node, 'targets', multi=True)
    for i, tg in enumerate(targets):
        cmds.connectAttr(f'{tg}.message', f'{node}.targets[{i}]')

    cmds.addAttr(node, longName='t', attributeType='double',
                 defaultValue=(t_value if t_value is not None else 0.5))
    if t_value is not None:
        cmds.setAttr(f'{node}.t', t_value)

    nn.ensure_bool_attr(node, 'linked', default=linked)

    _notify_change(joint)
    return node


def get_follow_rule(joint: str) -> dict | None:
    """Return joint's follow rule, or None if it has none.

    Returns:
        {'kind': 'distribute' | 'match',
         'targets': [<current node names>, ...],  # resolved live, rename-proof
         't': float in [0,1] ('distribute') or None ('match'),
         'linked': bool}

    Raises:
        RuntimeError: the rule exists but one of its targets has been
            deleted (see _resolve_targets_or_raise).
    """
    node = _find_rule_node(joint)
    if node is None:
        return None

    kind = cmds.getAttr(f'{node}.kind')
    targets = _resolve_targets_or_raise(joint, node, kind)
    t = None
    if kind == 'distribute' and cmds.attributeQuery('t', node=node, exists=True):
        t = cmds.getAttr(f'{node}.t')
    linked = bool(cmds.getAttr(f'{node}.linked')) \
        if cmds.attributeQuery('linked', node=node, exists=True) else False

    return {'kind': kind, 'targets': targets, 't': t, 'linked': linked}


def rule_linked_flag(joint: str) -> bool | None:
    """Return joint's follow-rule 'linked' flag WITHOUT resolving
    targets, or None if joint carries no rule node at all.

    Unlike get_follow_rule, this never raises: a rule whose target(s)
    have since been deleted still has a rule node and a readable
    'linked' attr, so it is reported here as True/False rather than
    being indistinguishable from "no rule exists". Callers that only
    need to know "is there already an AUTHORED rule here" (e.g.
    FollowJoint's never-clobber guard, SPEC 3.1) must use this instead
    of catching get_follow_rule's RuntimeError, since a broken-target
    rule and "no rule" are NOT the same thing — see get_follow_rule's
    docstring for why it raises in that case.
    """
    node = _find_rule_node(joint)
    if node is None:
        return None
    if not cmds.attributeQuery('linked', node=node, exists=True):
        return False
    return bool(cmds.getAttr(f'{node}.linked'))


def clear_follow_rule(joint: str) -> None:
    """Delete joint's follow-rule node (and, with it, every connection
    it carried). No-op if joint has no rule."""
    node = _find_rule_node(joint)
    if node and cmds.objExists(node):
        cmds.delete(node)
        _notify_change(joint)


# ─────────────────────────────────────────────
# Public API — pure resolution
# ─────────────────────────────────────────────

def _resolve_position_from_rule(joint: str, rule: dict) -> tuple:
    """Position math for an ALREADY-FETCHED rule dict — makes no
    get_follow_rule() call of its own. Shared by the public
    resolve_position() (which fetches the rule itself) and by
    evaluate(), which fetches each candidate joint's rule exactly once
    per call and reuses it here (SPEC 3.1 perf: this feeds the
    armature live-update hook, Task 1.2)."""
    if rule['kind'] == 'distribute':
        a, b = rule['targets']
        pa = cmds.xform(a, q=True, ws=True, t=True)
        pb = cmds.xform(b, q=True, ws=True, t=True)
        t = rule['t']
        return tuple(pa[i] + (pb[i] - pa[i]) * t for i in range(3))

    # match
    x = rule['targets'][0]
    return tuple(cmds.xform(x, q=True, ws=True, t=True))


def resolve_position(joint: str) -> tuple:
    """Return joint's ruled world position (x, y, z).

    distribute: lerp(A.worldPos, B.worldPos, t).
    match: X's world position.

    Raises:
        RuntimeError: joint has no follow rule.
    """
    rule = get_follow_rule(joint)
    if rule is None:
        raise RuntimeError(f"resolve_position: '{joint}' has no follow rule.")
    return _resolve_position_from_rule(joint, rule)


def _resolve_orientation_from_rule(joint: str, rule: dict):
    """Orientation math for an ALREADY-FETCHED rule dict — makes no
    get_follow_rule() call of its own. Shared with evaluate(); see
    _resolve_position_from_rule for why."""
    if rule['kind'] == 'distribute':
        return None

    x = rule['targets'][0]
    mat = list(cmds.xform(x, q=True, ws=True, matrix=True))
    mat[12] = mat[13] = mat[14] = 0.0
    return tuple(mat)


def resolve_orientation(joint: str):
    """Return joint's ruled world ORIENTATION as a 16-float row-major
    world matrix (rotation only — translation zeroed), matching
    cmds.xform(..., matrix=True)'s layout so callers can drop a
    resolved position straight into indices [12, 13, 14].

    match: X's world rotation.
    distribute: None — distribute orientation stays the aimers' job
        (SPEC 3.1). A childless twist joint's aimer is pointed at the segment
        end via the aimer's own "Parent" target + a 180 RZ (joint_orient), not
        here.

    Raises:
        RuntimeError: joint has no follow rule.
    """
    rule = get_follow_rule(joint)
    if rule is None:
        raise RuntimeError(f"resolve_orientation: '{joint}' has no follow rule.")
    return _resolve_orientation_from_rule(joint, rule)


# ─────────────────────────────────────────────
# Public API — evaluate
# ─────────────────────────────────────────────

def _all_ruled_joints() -> list:
    out = []
    for node in nn.find_marked_nodes(_MARKER):
        j = nn.get_message_target(node, 'owner_joint')
        if j:
            out.append(j)
    return out


def ruled_joints() -> list:
    """Public alias for _all_ruled_joints() — every joint in the scene
    currently carrying a follow rule, in no particular order. This is a
    full-scene lookup (cmds.ls(type='network') under the hood) and is
    meant for RARE callers that rebuild a cache on a change-listener
    event (see add_change_listener), never for a per-tick hot path —
    evaluate() itself never calls this."""
    return _all_ruled_joints()


def _sort_parents_first(joints: list) -> list:
    """Stable sort: a ruled joint sorts before any of its ruled
    descendants, regardless of how many unruled joints separate them in
    the Maya hierarchy. Ties (e.g. unrelated chains) keep their
    incoming relative order (Python sort is stable).

    Sort key is the joint's full-DAG-path pipe count (a single
    cmds.ls(..., long=True) call), not a per-hop cmds.listRelatives
    walk to the root: a child's full path always has exactly one more
    '|' than its direct parent's, so this is a correct, O(1)-per-joint
    proxy for hierarchy depth — no uncached, unbounded-cost walk for
    every joint sorted."""
    def _depth(j):
        full = cmds.ls(j, long=True)
        return full[0].count('|') if full else 0

    return sorted(joints, key=_depth)


def evaluate(joints: list = None) -> list:
    """Apply every ruled joint's resolved position (+ orientation, for
    'match') to the live scene. Pure function of current scene state —
    no callbacks/scriptJobs; the armature hook (Task 1.2) is expected to
    call this on its own update path.

    Args:
        joints: subset to evaluate, or None for every ruled joint in the
            scene. Any entry with no follow rule is skipped silently
            (lets a caller pass a broader selection without pre-filtering).

    Returns:
        The joints actually evaluated, in the order they were applied
        (parents before children, per the docstring above).
    """
    candidates = list(joints) if joints is not None else _all_ruled_joints()

    # Resolve each candidate's rule exactly ONCE per call (perf hot
    # path — this feeds the armature live-update hook, Task 1.2).
    # Ordering, position, and orientation all reuse this same fetch via
    # _resolve_position_from_rule/_resolve_orientation_from_rule rather
    # than re-calling get_follow_rule() for each.
    rules = {}
    for j in candidates:
        rule = get_follow_rule(j)
        if rule is not None:
            rules[j] = rule

    ordered = _sort_parents_first(list(rules.keys()))

    for j in ordered:
        rule = rules[j]
        pos = _resolve_position_from_rule(j, rule)
        if rule['kind'] == 'match':
            mat = list(_resolve_orientation_from_rule(j, rule))
            mat[12], mat[13], mat[14] = pos
            cmds.xform(j, ws=True, matrix=mat)
        else:
            cmds.xform(j, ws=True, t=pos)

    return ordered
