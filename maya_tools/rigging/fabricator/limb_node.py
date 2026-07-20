# Python/maya_tools/rigging/fabricator/limb_node.py
"""`fab_limb` network node CRUD — SPEC 2026-07-09 Limbs + Follower
Joints, section 3.2.

One `fab_limb` network node per limb. Registry-connected exactly like a
`fab_component` node (see `nodes.py`), and it owns membership the same
way follow_rules.py owns a follow rule: by MESSAGE connections only —
never name strings. Renaming the top joint, a finger root, or a twist
joint never breaks anything here; every accessor resolves through live
connections.

Connections on the node:
    top_joint (1)        — the limb's root joint (e.g. shoulder_l).
    registry (1)          — back-link to the fab_registry, mirroring
                             fab_component's own `registry` slot.
    components[]           — member fab_component node(s).
    finger_roots[]         — ORDERED. Order is authoring (append) order —
                             see `_ordered_list` for how that's preserved
                             across an add/remove-middle without a stored
                             index or name.
    twist_upper[] / twist_lower[] / curl_excluded[] — joints. Unordered.

Attrs:
    limb_type   (string) — display identity (e.g. 'Arm_RibbonIK'),
                            editable; NOT used for any lookup.
    implicit    (bool)   — True for a limb node auto-created by a
                            standalone membership-carrying component add
                            (SPEC 3.4). Never used for lookup either.
    color       (string) — SPEC 3.6 (Task 4.2b). Empty string = auto
                            (unset sentinel — same empty-string-means-
                            "follow the group" idiom ribbon_ik_arm.py's
                            'fingers_ctrl_color' OptionField uses one
                            level down, at the single-component-instance
                            scale; this is the limb-wide equivalent).
                            get_limb_color() resolves the empty case to a
                            side-aware default (side_tokens.side_to_ctrl_
                            color off the limb's own top_joint) — see that
                            function's docstring. cascade_color_to_
                            component() is what actually pushes an
                            EXPLICIT (non-empty) color onto member
                            components' own 'ctrl_color' option, at the
                            choke points that already assign it
                            (nodes._maybe_create_implicit_limb,
                            limbs/builder._link_fragment_components_to_
                            limb) — never at Component.build() time.

Registry connection: a `fab_limb` node requires a live `fab_registry`
to exist (mirrors `nodes.create_component_node`'s own requirement) — the
registry gains a lazily-added `limbs` message-multi the first time a limb
node is created; `nodes.create_registry`'s schema is never touched, so
every pre-P2 scene upgrades transparently the moment its first limb node
is authored.

`find_limb_for_joint` is the one non-direct lookup: it walks UP the joint
hierarchy (self first, then each joint ancestor) and returns the FIRST
(nearest) limb node whose top_joint matches. Nested limbs are out of
scope for this repo (SPEC 5), but the primitive itself must still behave
deterministically if a joint ever sits under two limbs' top_joints (e.g.
during an in-progress author flow) — nearest-wins is the documented
choice: the innermost/most-specific limb always resolves first, mirroring
how a nested scope shadows an outer one in ordinary name resolution.
"""
__author__ = "Adrian Melian"

import re

import maya.cmds as cmds

from maya_tools.utils.maya import network_nodes as nn
from maya_tools.utils.maya import side_tokens
from maya_tools.rigging.fabricator import nodes as ks_nodes




_MARKER = 'fab_limb'
_MARKER_VALUE = 'limb'
_NODE_PREFIX = 'fab_limb_'

_COMPONENTS_ATTR = 'components'
_FINGER_ROOTS_ATTR = 'finger_roots'          # ORDERED
_TWIST_UPPER_ATTR = 'twist_upper'
_TWIST_LOWER_ATTR = 'twist_lower'
_CURL_EXCLUDED_ATTR = 'curl_excluded'

_ALL_MULTIS = (
    _COMPONENTS_ATTR, _FINGER_ROOTS_ATTR, _TWIST_UPPER_ATTR,
    _TWIST_LOWER_ATTR, _CURL_EXCLUDED_ATTR,
)


# ─────────────────────────────────────────────
# Name helpers (cosmetic only — node NAME is never load-bearing; every
# lookup below resolves through message connections)
# ─────────────────────────────────────────────

def _short(name: str) -> str:
    return name.split('|')[-1].split(':')[-1]


def _safe(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9_]', '_', name).strip('_') or 'unnamed'


# ─────────────────────────────────────────────
# Create / find / get / delete
# ─────────────────────────────────────────────

def create_limb_node(limb_type: str, top_joint: str, implicit: bool = False) -> str:
    """Create a `fab_limb` node, registry-connected, message-connected
    to `top_joint`.

    Raises:
        RuntimeError: `top_joint` doesn't exist, `top_joint` already owns
            a limb node, or no `fab_registry` exists in the scene.
    """
    if not cmds.objExists(top_joint):
        raise RuntimeError(
            f"create_limb_node: top_joint '{top_joint}' does not exist.")
    if get_limb_node(top_joint) is not None:
        raise RuntimeError(
            f"create_limb_node: '{top_joint}' already owns a limb node.")

    reg = ks_nodes.get_registry()
    if not reg:
        raise RuntimeError(
            'create_limb_node: no fab_registry. Call '
            'nodes.create_registry() first.')

    node_name = f'{_NODE_PREFIX}{_safe(_short(top_joint))}'
    node = nn.create_marked_node(_MARKER, node_name, marker_value=_MARKER_VALUE)

    nn.ensure_string_attr(node, 'limb_type', default=limb_type or '')
    nn.ensure_bool_attr(node, 'implicit', default=bool(implicit))
    nn.ensure_string_attr(node, 'color', default='')

    nn.ensure_message_attr(node, 'top_joint')
    nn.connect_message(top_joint, node, 'top_joint')

    for attr in _ALL_MULTIS:
        nn.ensure_message_attr(node, attr, multi=True)

    nn.ensure_message_attr(node, 'registry')
    nn.ensure_message_attr(reg, 'limbs', multi=True)
    nn.connect_message(reg, node, 'registry')
    nn.connect_message_multi(node, reg, 'limbs')

    return node


def get_limb_node(top_joint: str) -> str | None:
    """Direct lookup: the limb node whose top_joint == top_joint. No
    walk — use `find_limb_for_joint` for the ancestor-walking resolver."""
    if not cmds.objExists(top_joint):
        return None
    conns = cmds.listConnections(f'{top_joint}.message', source=False,
                                 destination=True, plugs=True) or []
    for plug in conns:
        if not plug.endswith('.top_joint'):
            continue
        node = plug.rsplit('.', 1)[0]
        if cmds.attributeQuery(_MARKER, node=node, exists=True):
            return node
    return None


def get_top_joint(node: str) -> str | None:
    """Direct read of the `top_joint` message slot — the one connection
    `get_limb_node` only ever resolves in REVERSE (joint -> node). None
    if `node` doesn't exist or the connection is somehow missing (should
    not happen for any node `create_limb_node` produced)."""
    if not cmds.objExists(node):
        return None
    return nn.get_message_target(node, 'top_joint')


def all_limb_nodes() -> list:
    """Every `fab_limb`-marked node in the scene, regardless of
    registry wiring health."""
    return nn.find_marked_nodes(_MARKER) or []


def delete_limb_node(node: str) -> None:
    """Delete the limb node itself. Only ever removes the network node
    and, with it, every connection it carried — the joints and component
    nodes it referenced are never touched. The registry's `limbs` multi
    is left clean: Maya drops the dangling connection on delete, and
    `get_message_targets`/`_ordered_list` only ever report CONNECTED
    sources, so a deleted limb node simply stops appearing anywhere.

    Registry cascade-delete guard (2026-07-10, P4.2a — surfaced by the
    nodes.delete_component_node implicit-limb lifecycle cleanup, the
    first caller that can reach "this is the LAST limb, and the LAST
    component, connected to the registry"): a plain `cmds.delete(node)`
    on a node that still holds LIVE message connections to another
    plain 'network' node — here, the registry — can cascade into
    deleting that OTHER node too, when this delete would leave it with
    no remaining live message connection. Confirmed empirically: with
    no other component/limb left connected, deleting the scene's last
    limb node this way silently took `fab_registry` down with it.
    Plain `disconnectAttr` calls do NOT trigger the same cascade (proven
    the same way), so both registry links — `node.registry` (incoming
    from `registry.message`) and `node.message` (outgoing to
    `registry.limbs[i]`) — are severed explicitly FIRST, fully isolating
    `node` from the registry before the actual delete runs. Best-effort
    (each disconnect is independently guarded): a stale/already-broken
    connection must never block the delete itself."""
    if not node or not cmds.objExists(node):
        return

    if cmds.attributeQuery('registry', node=node, exists=True):
        reg_plug = f'{node}.registry'
        for s in (cmds.listConnections(reg_plug, source=True,
                                       destination=False, plugs=True) or []):
            try:
                cmds.disconnectAttr(s, reg_plug)
            except Exception:
                pass
    for d in (cmds.listConnections(f'{node}.message', source=False,
                                   destination=True, plugs=True) or []):
        try:
            cmds.disconnectAttr(f'{node}.message', d)
        except Exception:
            pass

    cmds.delete(node)


def find_limb_for_joint(joint: str) -> str | None:
    """Resolve the limb node owning `joint` by walking UP the hierarchy:
    `joint` itself first, then each joint ancestor in turn, returning the
    FIRST (nearest) node whose top_joint the walk lands on. None if the
    walk reaches the top of the joint chain without a match.

    Rename-proof: every step is a live message-connection lookup
    (`get_limb_node`) or a live `listRelatives` hierarchy query — no
    joint name is ever reconstructed or stored.

    Nested-limb semantics: if `joint` sits under TWO limbs' top_joints
    (one nested inside the other's subtree), the walk finds the nearer
    one first and returns it — see the module docstring for why nearest-
    wins is the chosen, documented behavior even though authoring nested
    limbs is out of scope (SPEC 5).
    """
    if not cmds.objExists(joint):
        return None
    current = joint
    while current:
        node = get_limb_node(current)
        if node is not None:
            return node
        parents = cmds.listRelatives(current, parent=True, type='joint',
                                     fullPath=True) or []
        current = parents[0] if parents else None
    return None


# ─────────────────────────────────────────────
# limb_type / implicit / color
# ─────────────────────────────────────────────

def get_limb_type(node: str) -> str:
    if not cmds.attributeQuery('limb_type', node=node, exists=True):
        return ''
    return cmds.getAttr(f'{node}.limb_type') or ''


def set_limb_type(node: str, s: str) -> None:
    nn.ensure_string_attr(node, 'limb_type', default='')
    cmds.setAttr(f'{node}.limb_type', s or '', type='string')


def limb_has_feature(node: str, feature: str) -> bool:
    """True when any member component's contract declares `feature`
    ('fingers' | 'twists') in limb_features (Derived Limbs, spec
    2026-07-11 §1). The UI gates its blocks per feature through this —
    a leg limb never shows a fingers block. Best-effort False on any
    resolution failure."""
    from maya_tools.rigging.fabricator.modules import get_component_class
    for cnode in list_components(node):
        try:
            cls = get_component_class(ks_nodes.get_component_type(cnode))
        except Exception:
            continue
        if feature in (getattr(cls.CONTRACT, 'limb_features', ()) or ()):
            return True
    return False


def limb_display_label(limb_type: str) -> str:
    """Human label for a limb_type string (Derived Limbs, spec
    2026-07-11 §6): a derived limb's limb_type is its primary
    component's TYPE string (e.g. 'RibbonIKArm') — render the contract's
    display_name ('Ribbon IK Arm'). A legacy fragment-named limb_type
    ('Advanced_Arm') doesn't resolve to a component class and passes
    through verbatim. Shared by the canvas limb-unit labels and the
    Properties LIMB block."""
    if not limb_type:
        return ''
    try:
        from maya_tools.rigging.fabricator.modules import get_component_class
        return get_component_class(limb_type).CONTRACT.display_name
    except Exception:
        return limb_type


def is_implicit(node: str) -> bool:
    if not cmds.attributeQuery('implicit', node=node, exists=True):
        return False
    return bool(cmds.getAttr(f'{node}.implicit'))


def get_limb_color_raw(node: str) -> str:
    """The literal `color` attr value — '' means unset (auto, side-
    aware). Distinct from `get_limb_color`, below, which resolves the
    empty case to a real color name; this is what a caller needs to
    know "was this limb EXPLICITLY colored" (e.g.
    `cascade_color_to_component`'s own no-op gate, and the Properties
    color field's initial widget state — SPEC 3.6 Task 4.2b)."""
    if not cmds.objExists(node) or not cmds.attributeQuery('color', node=node, exists=True):
        return ''
    return cmds.getAttr(f'{node}.color') or ''


def get_limb_color(node: str) -> str:
    """Resolved limb color — the SAME empty-sentinel-falls-through-to-
    the-group-source idiom `ribbon_ik_arm.py`'s 'fingers_ctrl_color'
    OptionField uses at the single-component scale (see that field's
    own docstring), one level up: an explicit `color` attr always wins;
    an unset one resolves to a side-aware default (lf -> blue, rt ->
    red, md -> yellow) — a display-only tint fallback. (Component
    ctrl_color seeding moved to per-TYPE contract defaults 2026-07-12;
    this limb-tint fallback deliberately keeps the side read so limb
    groupings still scan by side on the canvas.)

    Side is detected off the limb's own `top_joint` name (same
    `side_tokens.detect_side` call `_auto_detect_component_meta` makes
    on a joint's short name); 'md' (and so 'yellow') for a limb whose
    top_joint no longer resolves — degrade, never raise, matching every
    other accessor in this module."""
    explicit = get_limb_color_raw(node)
    if explicit:
        return explicit
    top = get_top_joint(node)
    side = side_tokens.detect_side(_short(top)) if top else 'md'
    return side_tokens.side_to_ctrl_color(side)


def set_limb_color(node: str, s: str) -> None:
    nn.ensure_string_attr(node, 'color', default='')
    cmds.setAttr(f'{node}.color', s or '', type='string')


def cascade_color_to_component(node: str, component_node: str) -> None:
    """Apply limb `node`'s EXPLICIT color to `component_node`'s own
    'ctrl_color' option — SPEC 2026-07-09 Limbs + Follower Joints §3.6
    (Task 4.2b), "a dropped limb's member components resolve their ctrl
    colors from the LIMB's color... unless a component explicitly
    overrides."

    Deliberately NOT a build-time resolution (unlike 'fingers_ctrl_
    color', which resolves within a single component instance inside
    Component.build() because there is no other choke point for an
    option that only ever affects that same instance's own ctrl):
    this cascades ACROSS a limb's member components, so it runs at the
    SAME choke points that already assign a component's 'ctrl_color'
    in the first place — `nodes._maybe_create_implicit_limb` (standalone
    add / fragment-dropped components that opt into
    `Component.creates_implicit_limb`) and `limbs.builder._link_
    fragment_components_to_limb` (every fragment-dropped component,
    opted-in or not). Call this right after connecting `component_node`
    into `node` at either choke point — never from `Component.build()`.

    No-op (component's own 'ctrl_color' left exactly as assigned) when:
      - `node` carries no EXPLICIT color (`get_limb_color_raw(node)` ==
        '') — the limb's own resolved default is ALREADY the same
        side-aware value `_auto_detect_component_meta`/the fragment's
        own saved side already gave this component, so there is
        nothing to override.
      - `component_node`'s options dict has no 'ctrl_color' key at all
        (its contract doesn't declare the option).
      - `component_node`'s CURRENT 'ctrl_color' differs from its
        side-derived auto default (lf=blue / rt=red / md=yellow — the
        same value `_auto_detect_component_meta` seeds at add time;
        side seeding RESTORED 2026-07-14, retiring the 2026-07-12
        contract-family-default comparison) — that mismatch is what
        "explicitly overridden" MEANS here: a value a rigger (or a
        fragment author) deliberately set to something other than the
        side default. That always wins over the limb, per SPEC 3.6.

    Idempotent / safe to call more than once on the same pair: once a
    component's 'ctrl_color' has been cascaded to the limb's color, it
    generally no longer equals its own side-auto-default, so a second
    call correctly no-ops (unless the limb color happens to BE that
    default, in which case re-applying it is a harmless no-change
    write)."""
    explicit_color = get_limb_color_raw(node)
    if not explicit_color:
        return
    if not component_node or not cmds.objExists(component_node):
        return
    options = ks_nodes.get_component_options(component_node)
    if 'ctrl_color' not in options:
        return
    # "Explicitly overridden" = differs from the side-derived auto
    # default (lf=blue / rt=red / md=yellow) that
    # _auto_detect_component_meta seeds at add time (side seeding
    # RESTORED 2026-07-14 — the 2026-07-12 contract-family-default
    # comparison retired with family-color seeding).
    auto_default = side_tokens.side_to_ctrl_color(
        ks_nodes.get_component_side(component_node))
    if options.get('ctrl_color') != auto_default:
        return  # genuine explicit per-component override — always wins
    if options.get('ctrl_color') == explicit_color:
        return  # already the limb's color — no-op
    options['ctrl_color'] = explicit_color
    ks_nodes.set_component_options(component_node, options)


# ─────────────────────────────────────────────
# Generic message-multi CRUD
#
# Shared by every connection accessor below (components, finger_roots,
# twist_upper, twist_lower, curl_excluded). `_ordered_list` reads the
# multi's live indices in ASCENDING order. Ascending-index order IS
# authoring order ONLY because `_add_to_multi` deliberately connects
# each new entry at an explicit index of `max(existing indices) + 1`
# rather than via `connectAttr(..., nextAvailable=True)`
# (`nn.connect_message_multi`). `nextAvailable` fills the LOWEST
# currently-unused index, and `_remove_from_multi`'s
# `cmds.removeMultiInstance(plug, b=True)` genuinely vacates whatever
# index it clears — so after a remove-middle, the freed (now-lowest)
# index would otherwise be the next one `nextAvailable` hands out,
# reordering the very next add into the middle instead of appending it
# after the survivors. Always deriving the next index from the current
# max survives that: removing a middle entry only clears that one
# index (never renumbers the rest — see `_remove_from_multi`), so an
# add/remove-middle/add sequence reads back in the order each surviving
# entry was first authored, and the new entry always lands after all of
# them. This is why `finger_roots` (the one ORDERED slot, per SPEC 3.2)
# needs no bespoke storage — the same generic multi machinery both the
# ordered and unordered slots share preserves authoring order for free.
# ─────────────────────────────────────────────

def _ordered_list(node: str, attr: str) -> list:
    # objExists FIRST: cmds.attributeQuery raises a raw TypeError ("No
    # object matches name") on a node that doesn't exist at all, unlike
    # objExists itself, which just answers False — a caller holding a
    # stale `node` reference (e.g. across a scene wipe) must see this
    # accessor degrade to "nothing here" like every other read in this
    # module (get_top_joint, get_limb_node), never crash with a raw
    # Maya TypeError. See test_limb_node_stale_node_reference_accessors_
    # degrade_gracefully.
    if not cmds.objExists(node) or not cmds.attributeQuery(attr, node=node, exists=True):
        return []
    indices = sorted(cmds.getAttr(f'{node}.{attr}', multiIndices=True) or [])
    out = []
    for i in indices:
        srcs = cmds.listConnections(f'{node}.{attr}[{i}]', source=True,
                                    destination=False) or []
        if srcs:
            out.append(srcs[0])
    return out


def _add_to_multi(node: str, attr: str, target: str, *,
                  require_joint: bool = False,
                  require_component: bool = False) -> bool:
    """Connect `target` to `node.attr`'s next slot, appending AFTER the
    highest surviving index rather than trusting `nextAvailable` (see
    the section comment above for why that distinction matters for the
    ORDERED `finger_roots` contract).

    `require_joint`/`require_component` gate the target's type the same
    way `nodes.add_ctrl_space` gates joint-only spaces: an invalid
    target is rejected (returns False) rather than silently wired in.
    """
    if not target or not cmds.objExists(target):
        return False
    if require_joint and cmds.nodeType(target) != 'joint':
        return False
    if require_component and not cmds.attributeQuery(
            ks_nodes._COMPONENT_MARKER, node=target, exists=True):
        return False
    if target in _ordered_list(node, attr):
        return False
    nn.ensure_message_attr(node, attr, multi=True)
    indices = cmds.getAttr(f'{node}.{attr}', multiIndices=True) or []
    next_index = (max(indices) + 1) if indices else 0
    cmds.connectAttr(f'{target}.message', f'{node}.{attr}[{next_index}]')
    return True


def _remove_from_multi(node: str, attr: str, target: str) -> None:
    # Same objExists-first guard as _ordered_list above, same reason.
    if (not target or not cmds.objExists(node)
            or not cmds.attributeQuery(attr, node=node, exists=True)):
        return
    indices = cmds.getAttr(f'{node}.{attr}', multiIndices=True) or []
    for i in indices:
        plug = f'{node}.{attr}[{i}]'
        srcs = cmds.listConnections(plug, source=True, destination=False) or []
        if target in srcs:
            for s in srcs:
                try:
                    cmds.disconnectAttr(f'{s}.message', plug)
                except Exception:
                    pass
            try:
                cmds.removeMultiInstance(plug, b=True)
            except Exception:
                pass


# ─── components ───

def add_component(node: str, component_node: str) -> bool:
    return _add_to_multi(node, _COMPONENTS_ATTR, component_node,
                         require_component=True)


def remove_component(node: str, component_node: str) -> None:
    _remove_from_multi(node, _COMPONENTS_ATTR, component_node)


def list_components(node: str) -> list:
    return _ordered_list(node, _COMPONENTS_ATTR)


# ─── finger_roots (ORDERED — authoring order) ───

def add_finger_root(node: str, joint: str) -> bool:
    return _add_to_multi(node, _FINGER_ROOTS_ATTR, joint, require_joint=True)


def remove_finger_root(node: str, joint: str) -> None:
    _remove_from_multi(node, _FINGER_ROOTS_ATTR, joint)


def list_finger_roots(node: str) -> list:
    """Ordered by authoring (append) order — see the module-level note
    on `_ordered_list` for how add/remove-middle preserves it."""
    return _ordered_list(node, _FINGER_ROOTS_ATTR)


# ─── twist_upper ───

def add_twist_upper(node: str, joint: str) -> bool:
    return _add_to_multi(node, _TWIST_UPPER_ATTR, joint, require_joint=True)


def remove_twist_upper(node: str, joint: str) -> None:
    _remove_from_multi(node, _TWIST_UPPER_ATTR, joint)


def list_twist_upper(node: str) -> list:
    return _ordered_list(node, _TWIST_UPPER_ATTR)


# ─── twist_lower ───

def add_twist_lower(node: str, joint: str) -> bool:
    return _add_to_multi(node, _TWIST_LOWER_ATTR, joint, require_joint=True)


def adopt_existing_twists(node: str) -> dict:
    """Adopt pre-authored twist joints onto `node`'s twist multis
    (Adrian, 2026-07-10 — the UE5.8 Manny ships 2 twists per segment in
    exactly the layout limb_set_twist_count produces: siblings parented
    to the segment's top joint, never chained).

    Detection per segment, deliberately conservative (a wrong adoption
    puts a stranger's skinned joint under the dial's respace/guard):
      - a JOINT child of the segment's top joint,
      - name matches the twist-token pattern ('twist' bounded by _ /
        digit / string edge, case-insensitive — upperarm_twist_01_l,
        calf_twist_02_l, twist_00; NOT 'twisted_villain_helper'),
      - childless (the main-chain continuation always has children),
      - owned by NO component (structural guard — a bare wrist on a
        chain whose PREFIX contains 'twist' is a component member and
        must never be adopted; caught live by the limb-units dial
        suite, 2026-07-10),
      - not already registered on either multi.

    Each adoptee gets a 'distribute' follow rule between the segment's
    own bone ends with t computed from its ACTUAL position (projection
    along the bone, clamped) — adoption NEVER repositions a joint,
    unlike the dial's deliberate even respace: adopted twists are
    usually skinned. A joint that already carries a follow rule keeps
    it untouched (membership is still registered). Registration is
    t-ordered so list_twist_* reads root-to-end.

    Idempotent and best-effort: unresolvable segments are skipped;
    callers (nodes._maybe_create_implicit_limb) wrap the whole call in
    their own never-block-the-add guard regardless.

    Returns {'upper': [adopted...], 'lower': [adopted...]}.
    """
    import re
    from maya_tools.rigging.fabricator import follow_rules as fr

    # 'twist' as a bounded token: preceded by start/_, followed by
    # _/digit/end — 'lowerarm_twist_01_l' and 'twist_00' match,
    # 'twisted_villain_arm' does not.
    twist_token = re.compile(r'(?:^|_)twist(?:_|\d|$)', re.IGNORECASE)

    # Structural guard: any joint ANY component owns is off-limits —
    # chain joints (a childless bare wrist under the elbow), weapon
    # AdvancedFK joints, FollowJoint targets alike.
    component_owned = set()
    for cn in ks_nodes.get_all_component_nodes():
        for j in ks_nodes.get_component_joints(cn) or []:
            component_owned.add(j.split('|')[-1].split(':')[-1])

    out = {'upper': [], 'lower': []}
    already = set(list_twist_upper(node)) | set(list_twist_lower(node))
    for segment, add_fn in (('upper', add_twist_upper),
                            ('lower', add_twist_lower)):
        seg = _resolve_twist_segment(node, segment)
        if not seg:
            continue
        seg_top, seg_end = seg
        tp = cmds.xform(seg_top, q=True, ws=True, t=True)
        ep = cmds.xform(seg_end, q=True, ws=True, t=True)
        bone = [ep[i] - tp[i] for i in range(3)]
        bone_len2 = sum(c * c for c in bone) or 1.0

        scored = []
        for kid in cmds.listRelatives(seg_top, children=True,
                                      type='joint') or []:
            short = kid.split('|')[-1].split(':')[-1]
            if short in already or short in component_owned:
                continue
            if not twist_token.search(short):
                continue
            if cmds.listRelatives(kid, children=True, type='joint'):
                continue
            jp = cmds.xform(kid, q=True, ws=True, t=True)
            t = sum((jp[i] - tp[i]) * bone[i] for i in range(3)) / bone_len2
            scored.append((max(0.0, min(1.0, t)), short))

        for t, short in sorted(scored):
            if not add_fn(node, short):
                continue
            already.add(short)
            if fr.get_follow_rule(short) is None:
                fr.set_follow_rule(short, 'distribute',
                                   [seg_top, seg_end], t=t)
            out[segment].append(short)
    return out


def remove_twist_lower(node: str, joint: str) -> None:
    _remove_from_multi(node, _TWIST_LOWER_ATTR, joint)


def list_twist_lower(node: str) -> list:
    return _ordered_list(node, _TWIST_LOWER_ATTR)


# ─── curl_excluded ───

def add_curl_excluded(node: str, joint: str) -> bool:
    return _add_to_multi(node, _CURL_EXCLUDED_ATTR, joint, require_joint=True)


def remove_curl_excluded(node: str, joint: str) -> None:
    _remove_from_multi(node, _CURL_EXCLUDED_ATTR, joint)


def list_curl_excluded(node: str) -> list:
    return _ordered_list(node, _CURL_EXCLUDED_ATTR)


# ─────────────────────────────────────────────────────────────────────────
# (Derived Limbs, spec 2026-07-11: the Fingers dial —
# _resolve_limb_wrist / limb_can_add_finger / _uniquify_finger_fragment /
# limb_add_finger / limb_remove_finger — and templates/finger.limb.yaml
# are retired. Fingers are authored as JOINTS in the Armature phase;
# nodes.derive_limb re-discovers them at every seam. The Twist dial
# below stays: it authors real joints, which adoption then re-finds.)
# ─────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────
# Task 3.2 (SPEC 2026-07-09 Limbs + Follower Joints §3.3): the Twist
# dial — limb_set_twist_count. The remaining half of the LIMB section
# (the Fingers dial, Task 3.1, is above). A twist joint is a pure
# follow_rules primitive — SPEC 3.1's own worked example is "Twist
# joints: t=1/3, 2/3 along elbow->wrist" — and, unlike a finger,
# NEVER gets an Armature ctrl of its own: armature.py's ctrl loop
# skips every follow-ruled joint outright (position — and, for
# 'match', orientation — is entirely rule-driven). So this dial
# manages joints + follow rules + limb-node membership directly; it
# has no apply_limb_fragment/Armature-rebuild detour to reuse the way
# the Fingers dial does for its own, ctrl-bearing chain.
# ─────────────────────────────────────────────────────────────────────────

def _resolve_twist_segment(node: str, segment: str):
    """Resolve (seg_top, seg_end) — the two joints bounding `segment`'s
    bone on limb `node`. 'upper' spans top_joint -> the mid joint (e.g.
    shoulder -> elbow / hip -> knee); 'lower' spans mid -> the end of the
    SECOND bone (elbow -> wrist / knee -> ankle) — never further down the
    chain (a 4-joint leg's ball/toe is not part of the shin's twist span).

    Reuses the SAME component-joints convention `_resolve_limb_wrist`
    does: the order-guaranteed joint_names string array
    (`nodes.get_component_joint_names` — NOT the live joints[]
    message-multi, which disclaims index-order guarantees; see
    `_resolve_limb_wrist`'s own docstring for why), read as
    joints[0]=top / joints[1]=mid / joints[-1]=end off the limb's first
    connected component that resolves a live 3+-joint chain. Falls
    back to the live joints[] multi (`get_component_joints`) the same
    way `_resolve_limb_wrist` does, for a pre-fix scene with no
    joint_names array yet.

    Componentless-limb fallback: walks a linear chain down from the
    limb's own top_joint (top -> its one child -> that child's one
    child) — the same "bare skeleton, no component yet" shape
    `_resolve_limb_wrist` falls back to top_joint itself for, except a
    twist BONE needs two endpoints, not one attach point, so this walks
    one/two hops further down the joint hierarchy instead. Twist
    joints ALREADY registered on this limb (twist_upper[]/
    twist_lower[]) are excluded from the child candidates at each hop
    — they are themselves SIBLINGS of the main-chain continuation
    joint (per limb_set_twist_count's own "parented to it, NOT
    chained" contract), so without this exclusion, resolving 'lower'
    AFTER an 'upper' twist already exists would see top_joint's two
    children (the real mid joint AND the upper twist) and bail as
    "branching" — a false ambiguity, not a genuine fork in the
    skeleton. A branching (more than one NON-twist child at either
    hop) or too-short chain resolves to None rather than guessing —
    same "don't guess, return None" contract `_resolve_limb_wrist`
    documents for its own no-candidate case.

    Returns (seg_top, seg_end), or None when nothing resolves —
    `limb_set_twist_count` turns a None here into a clear RuntimeError
    naming the limb and segment.
    """
    for cnode in list_components(node):
        names = ks_nodes.get_component_joint_names(cnode)
        if len(names) < 3:
            names = ks_nodes.get_component_joints(cnode)
        if len(names) >= 3:
            # names[2], NOT names[-1]: the twist segments are always the
            # chain's first two BONES (top->mid, mid->end-of-second-bone).
            # Identical on a 3-joint arm (names[2] == wrist == names[-1]),
            # but on a 4-joint leg (hip/knee/ankle/ball) names[-1] is the
            # BALL — and a foot is not a shin (RibbonIKLeg finding,
            # 2026-07-10): 'lower' must span knee->ankle, never knee->ball.
            top, mid, end = names[0], names[1], names[2]
            if (cmds.objExists(top) and cmds.objExists(mid)
                    and cmds.objExists(end)):
                return (top, mid) if segment == 'upper' else (mid, end)

    existing_twists = set(list_twist_upper(node)) | set(list_twist_lower(node))

    top_joint = get_top_joint(node)
    if not top_joint or not cmds.objExists(top_joint):
        return None
    kids = [k for k in (cmds.listRelatives(top_joint, children=True, type='joint') or [])
           if k not in existing_twists]
    if len(kids) != 1:
        return None
    mid = kids[0]
    grandkids = [k for k in (cmds.listRelatives(mid, children=True, type='joint') or [])
                if k not in existing_twists]
    if len(grandkids) != 1:
        return None
    end = grandkids[0]
    return (top_joint, mid) if segment == 'upper' else (mid, end)


def _new_twist_joint_name(seg_top: str, segment: str) -> str:
    """A fresh, scene-unique name for a new twist joint on `segment`.
    Cosmetic only (rename-proof by construction, same as every other
    name in this file) — a plain incrementing numeric suffix, Maya's
    own auto-uniquify convention, off `{short(seg_top)}_twist_{segment}`.
    """
    base = f'{_short(seg_top)}_twist_{segment}'
    k = 1
    name = f'{base}_{k:02d}'
    while cmds.objExists(name):
        k += 1
        name = f'{base}_{k:02d}'
    return name


def _twist_skinned_guard(limb: str, segment: str, joints: list) -> None:
    """Raise naming every skinned joint in `joints`, or no-op if none
    are. Checked against EVERY joint currently in the segment — not
    just the ones about to be added/removed — because both add and
    remove ultimately re-space every SURVIVING joint's `t` too (see
    limb_set_twist_count's own docstring), and a skinned joint's
    weights are sacred: never silently re-positioned, same contract
    limb_remove_finger's own skinning guard documents."""
    skinned = [j for j in joints
              if cmds.objExists(j) and cmds.listConnections(j, type='skinCluster')]
    if skinned:
        plural = len(skinned) != 1
        raise RuntimeError(
            f'limb_set_twist_count: cannot change the {segment!r} twist '
            f'count on {limb!r} — {skinned!r} '
            f'{"carry" if plural else "carries"} skinCluster weights and '
            f'would be re-spaced or orphaned. Detach/unbind skin from '
            f'{"these joints" if plural else "this joint"} first — no '
            f'weight migration happens automatically (SPEC 5). Nothing '
            f'was changed.'
        )


def _respace_twists(joints: list, seg_top: str, seg_end: str, n: int) -> None:
    """Set/refresh each of `joints`' distribute follow rule to an even
    k/(n+1) fraction along seg_top -> seg_end — joints[0] (authoring
    order) gets t=1/(n+1), joints[1] gets 2/(n+1), etc. — then
    evaluate() them once so the new fractions land in their live
    positions immediately: "re-spacing" is a visible, synchronous
    effect of this call, not something deferred to the next Armature
    ctrl drag. (That live per-ctrl-drag path — SPEC 3.1 Task 1.2 — is
    what KEEPS these joints correctly positioned on every LATER drag
    of the segment's ctrls; it isn't needed to make THIS call's own
    re-space observable, and armature_exists() may even be False here,
    e.g. a twist count set before the Armature has ever been built
    this session — set_follow_rule/evaluate() both work fine with no
    live Armature at all, per follow_rules.py's own "pure function of
    scene state" contract.)"""
    from maya_tools.rigging.fabricator import follow_rules as fr
    for k, j in enumerate(joints, start=1):
        fr.set_follow_rule(j, 'distribute', (seg_top, seg_end), t=k / (n + 1))
    fr.evaluate(list(joints))


def limb_set_twist_count(limb: str, segment: str, n: int) -> None:
    """Set the number of twist joints on `segment` ('upper' or 'lower')
    of `limb` to exactly `n` (SPEC §3.3 Twist dial).

    ADD (n > current): spawns (n - current) new joints as SIBLINGS
    directly under the segment's TOP joint — never chained to each
    other, always re-parented back to seg_top before the next one is
    created — each wired into the right twist_upper[]/twist_lower[]
    multi and given a fresh 'distribute' follow rule between the
    segment's own (seg_top, seg_end) bone. EVERY twist joint in the
    segment — existing survivors AND the new ones — is then re-spaced
    to an even k/(n+1) fraction (see _respace_twists) and the new
    joints join the `_Joints` reference-display layer, same as any
    other armature joint (limbs/builder.apply_limb_fragment's own tail
    makes the identical add_joints_to_reference_layer call for a fresh
    limb drop).

    REMOVE (n < current): removes from the END of the segment's
    authored list (list_twist_upper/list_twist_lower's own authoring-
    order contract — see _ordered_list's module-level note), and for
    each doomed joint: disconnects it from the multi, clears its
    follow rule, deletes its aimer (delete_aimer-BEFORE-delete — the
    same order limb_remove_finger established, for the identical
    stale-aimer-DG-fragment reason documented on that function), then
    deletes the joint itself. Survivors are re-spaced to the new
    k/(n+1) fractions.

    SKINNING GUARD (both directions): checked FIRST, against every
    joint CURRENTLY in the segment, BEFORE any joint is created,
    deleted, or re-spaced — because both directions ultimately
    re-space every survivor's `t`. Raises RuntimeError naming every
    skinned joint found; mutates nothing when it raises (no spawn, no
    delete, no rule/position change) — a same-count call (n == current)
    is a pure no-op and skips this check entirely, since nothing would
    change either way.

    Edit-Mode-gated exactly like limb_add_finger/limb_remove_finger.

    Raises:
        ValueError: segment isn't 'upper'/'lower', or n < 0.
        RuntimeError: limb doesn't exist, not in Edit Mode, the
            segment's bone can't be resolved, or the skinning guard
            trips.
    """
    if segment not in ('upper', 'lower'):
        raise ValueError(
            f"limb_set_twist_count: segment must be 'upper' or 'lower', "
            f"got {segment!r}.")
    n = int(n)
    if n < 0:
        raise ValueError(f'limb_set_twist_count: n must be >= 0, got {n}.')

    if not cmds.objExists(limb):
        raise RuntimeError(f'limb_set_twist_count: limb node not found: {limb!r}')

    from maya_tools.rigging.fabricator.ui import state as fab_state
    mode = fab_state.detect_mode()
    if mode != fab_state.MODE_SKELETON:
        raise RuntimeError(
            f'Twist count requires Edit Mode (skeleton) — current mode '
            f'is {mode!r}. Unbuild modules first, adjust twists, then '
            f'Build Modules to materialize the rig without them.'
        )

    bone = _resolve_twist_segment(limb, segment)
    if bone is None:
        raise RuntimeError(
            f'limb_set_twist_count: could not resolve the {segment!r} '
            f'bone for limb {limb!r} — no connected component with a '
            f'live 3-joint chain, and no resolvable componentless '
            f'fallback (a linear top -> mid -> end joint chain).'
        )
    seg_top, seg_end = bone

    list_fn = list_twist_upper if segment == 'upper' else list_twist_lower
    add_fn = add_twist_upper if segment == 'upper' else add_twist_lower
    remove_fn = remove_twist_upper if segment == 'upper' else remove_twist_lower

    current = [j for j in list_fn(limb) if cmds.objExists(j)]
    if n == len(current):
        return

    _twist_skinned_guard(limb, segment, current)

    if n > len(current):
        top_pos = cmds.xform(seg_top, q=True, ws=True, t=True)
        new_joints = []
        for _ in range(n - len(current)):
            name = _new_twist_joint_name(seg_top, segment)
            cmds.select(seg_top, replace=True)
            j = cmds.joint(name=name, position=top_pos)
            cmds.select(clear=True)
            add_fn(limb, j)
            new_joints.append(j)

        _respace_twists(current + new_joints, seg_top, seg_end, n)

        from maya_tools.rigging.fabricator import fs_app
        fs_app.add_joints_to_reference_layer(new_joints)
    else:
        to_remove = len(current) - n
        doomed = current[-to_remove:]
        survivors = current[:len(current) - to_remove]

        from maya_tools.rigging.joint_orient import joint_orient_app as joa
        from maya_tools.rigging.fabricator import follow_rules as fr
        for j in doomed:
            remove_fn(limb, j)
            fr.clear_follow_rule(j)
            joa.delete_aimer(j)
        live_doomed = [j for j in doomed if cmds.objExists(j)]
        if live_doomed:
            cmds.delete(live_doomed)

        if survivors:
            _respace_twists(survivors, seg_top, seg_end, n)
