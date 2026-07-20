# Python/maya_tools/rigging/fabricator/nodes.py
"""Network node CRUD specific to ks/ v2.

Three node types:
  1. fab_registry — one per scene. Tracks all component instance nodes
     and the source blueprint metadata (path, name, schema_version).
  2. fab_<id> — one per component in the blueprint. Stores
     instance data: type, joints message connections, parent_plug, side,
     options (JSON), persisted (JSON), is_built flag.
  3. ksfab_guides_grp (LEGACY, name intentionally NOT renamed — see the
     "Guides group (LEGACY)" section below) — transform group with a
     marker attr; child locators are individual guide locators (one per
     joint).

Built on utils/maya/network_nodes for the underlying CRUD primitives.
"""
__author__ = "Adrian Melian"

import json
import re
import traceback

import maya.cmds as cmds

from maya_tools.utils.maya import network_nodes as nn


_REGISTRY_MARKER = 'fab_registry'
_REGISTRY_NAME   = 'fab_registry'
_COMPONENT_MARKER = 'fab_component'
_COMPONENT_PREFIX = 'fab_'
_GUIDES_GRP_MARKER = 'ksfab_guides_marker'
_GUIDES_GRP_NAME   = 'ksfab_guides_grp'
_PIVOTS_GRP_MARKER = 'fab_pivots_marker'
_PIVOTS_GRP_NAME   = 'fab_pivots_grp'


# ─── Registry node ───────────────────────────────────────────────────────────

def get_registry() -> str:
    """Return the KS v2 registry network node, or None."""
    return nn.find_one_marked_node(_REGISTRY_MARKER)


def create_registry(blueprint_name: str, blueprint_path: str = '',
                    schema_version: str = '1.0',
                    description: str = '', origin: str = '',
                    wiring: list = None) -> str:
    """Create the registry node. Raises if one already exists.

    description / origin / wiring: top-level Blueprint metadata, mirrored
    scene-side so save() can snapshot directly without consulting an
    in-memory bp object. wiring is JSON-encoded (Spec 3 reserved; current
    schema is always empty list).

    Stamps fabricator_version at creation with the CURRENT FABRICATOR_
    VERSION, rather than leaving it unset for the first successful build
    to fill in. modules._is_legacy_scene_data treats an UNSTAMPED
    registry as legacy data (conservatively correct for scenes that
    predate version stamping), but a registry created fresh today has no
    legacy data behind it — left unstamped, a never-built scene would
    misresolve a future free component that legitimately reclaims a
    retired type name (e.g. 'IKArm', reserved by
    modules._VERSION_GATED_LEGACY_TYPE_MAP) through that map the instant
    it registers, before any build ever runs. Same attr/format
    set_registry_fabricator_version writes (fabricator_version, dt=
    'string') — a later successful build re-stamps it via that function
    exactly as before; this is just the creation-time floor so "stamped"
    starts true instead of false.

    Caveat for callers loading POSSIBLY-OLD data (currently only
    fs_app.load): the Blueprint YAML format carries no fabricator_version
    of its own, so this creation-time stamp is the wrong signal for "is
    the .yaml file I'm about to spawn components from actually old" —
    fs_app.load resets the stamp to '' for the window of its
    component-spawn loop (so resolve_component_type's version-gated
    legacy map is active while old YAML types are routed through it),
    then re-stamps CURRENT the moment the loop has written resolved
    canonical types onto the scene nodes (2026-07-12 — a fresh load
    must never read as a pre-stamping rig). See that call site's own
    comment.
    """
    if get_registry():
        raise RuntimeError('A fab_registry already exists. Call delete_registry() first.')

    node = nn.create_marked_node(_REGISTRY_MARKER, _REGISTRY_NAME, marker_value='registry')
    nn.ensure_string_attr(node, 'blueprint_name',  default=blueprint_name)
    nn.ensure_string_attr(node, 'blueprint_path',  default=blueprint_path)
    nn.ensure_string_attr(node, 'schema_version', default=schema_version)
    nn.ensure_string_attr(node, 'description',    default=description)
    nn.ensure_string_attr(node, 'origin',         default=origin)
    nn.ensure_string_attr(node, 'wiring_json',    default=json.dumps(wiring or []))
    nn.ensure_message_attr(node, 'components', multi=True)
    nn.ensure_message_attr(node, 'root_joint')

    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION
    nn.ensure_string_attr(node, 'fabricator_version', default=FABRICATOR_VERSION)

    # Stamp the mirror convention from the active project's authored
    # default (t48 Phase C). The stamp is this rig's truth forever —
    # the project config is only consulted here, at creation, so a rig
    # sent to another artist mirrors identically on their machine. All
    # rig-creation paths (new, load, adopt) flow through this function.
    from maya_tools.utils.maya import orientation_convention as oc
    oc.stamp_registry(node, _default_convention_name())
    return node


def _default_convention_name() -> str:
    """The active project's authored 'orient_convention' key
    ('unreal' | 'standard'), else 'unreal'. Reads the AUTHORED tier
    only (project_config_io.load_project) — no machine bindings needed,
    so an unbound project still stamps correctly. Any failure (no
    active project, unreadable config, junk value) falls back to the
    house convention rather than blocking rig creation."""
    try:
        from maya_tools.framework import maya_startup, project_config_io
        slug = maya_startup.get_active_config() or ''
        if not slug:
            return 'unreal'
        name = (project_config_io.load_project(slug) or {}).get(
            'orient_convention', 'unreal')
        return name if name in ('unreal', 'standard') else 'unreal'
    except Exception:
        return 'unreal'


def delete_registry() -> None:
    reg = get_registry()
    if reg and cmds.objExists(reg):
        cmds.delete(reg)


def get_blueprint_name(registry: str = None) -> str:
    reg = registry or get_registry()
    return cmds.getAttr(f'{reg}.blueprint_name') if reg else ''


def get_blueprint_path(registry: str = None) -> str:
    reg = registry or get_registry()
    return cmds.getAttr(f'{reg}.blueprint_path') if reg else ''


def set_blueprint_path(path: str) -> None:
    reg = get_registry()
    if reg:
        cmds.setAttr(f'{reg}.blueprint_path', path or '', type='string')


def get_rig_label(registry: str = None) -> str:
    """Stored rig label override. Empty string means "not yet set" — the
    caller should fall back to derive_rig_label and persist the result via
    set_rig_label so subsequent reads are stable across scene save/load.
    Use `get_or_init_rig_label` for the lazy-init read pattern."""
    reg = registry or get_registry()
    if not reg:
        return ''
    if not cmds.attributeQuery('rig_label', node=reg, exists=True):
        return ''
    return cmds.getAttr(f'{reg}.rig_label') or ''


def set_rig_label(label: str) -> None:
    """Persist the rig label on the registry. Idempotent on the attr
    (ensure_string_attr no-ops when it already exists)."""
    reg = get_registry()
    if not reg:
        return
    nn.ensure_string_attr(reg, 'rig_label', default='')
    cmds.setAttr(f'{reg}.rig_label', label or '', type='string')


def get_or_init_rig_label() -> str:
    """Return the stored rig label, deriving + persisting one on first
    read so the auto-named state happens exactly once per scene.

    Derivation walks the standard `rig_binding.derive_rig_label` chain
    (namespace > scene stem > joint basename) against the first root
    joint. Returns '' if neither a registry nor a root joint exists.
    """
    reg = get_registry()
    if not reg:
        return ''
    stored = get_rig_label(reg)
    if stored:
        return stored
    # Lazy-init: derive from the root joint, then persist back.
    from maya_tools.utils.maya import rig_binding
    all_joints = cmds.ls(type='joint', long=False) or []
    roots = [j for j in all_joints
             if not (cmds.listRelatives(j, parent=True, type='joint') or [])]
    if not roots:
        return ''
    derived = rig_binding.derive_rig_label(roots[0]) or ''
    if derived:
        set_rig_label(derived)
    return derived


def get_blueprint_description(registry: str = None) -> str:
    reg = registry or get_registry()
    if not reg:
        return ''
    if not cmds.attributeQuery('description', node=reg, exists=True):
        return ''
    return cmds.getAttr(f'{reg}.description') or ''


def set_blueprint_description(description: str) -> None:
    reg = get_registry()
    if not reg:
        return
    nn.ensure_string_attr(reg, 'description', default='')
    cmds.setAttr(f'{reg}.description', description or '', type='string')


def get_blueprint_origin(registry: str = None) -> str:
    """Origin string (typically the parent blueprint identifier; free-form).
    Stored as a string to round-trip whatever YAML format the bp uses
    (dict / string / None) by JSON-encoding non-string values upstream."""
    reg = registry or get_registry()
    if not reg:
        return ''
    if not cmds.attributeQuery('origin', node=reg, exists=True):
        return ''
    return cmds.getAttr(f'{reg}.origin') or ''


def set_blueprint_origin(origin: str) -> None:
    reg = get_registry()
    if not reg:
        return
    nn.ensure_string_attr(reg, 'origin', default='')
    cmds.setAttr(f'{reg}.origin', origin or '', type='string')


def get_blueprint_wiring(registry: str = None) -> list:
    reg = registry or get_registry()
    if not reg:
        return []
    if not cmds.attributeQuery('wiring_json', node=reg, exists=True):
        return []
    raw = cmds.getAttr(f'{reg}.wiring_json') or '[]'
    return json.loads(raw)


def set_blueprint_wiring(wiring: list) -> None:
    reg = get_registry()
    if not reg:
        return
    nn.ensure_string_attr(reg, 'wiring_json', default='[]')
    cmds.setAttr(f'{reg}.wiring_json', json.dumps(wiring or []), type='string')


def get_registry_root_joint(registry: str = None) -> str:
    """Return the joint connected to the registry's root_joint message,
    or '' if nothing is connected. Used by _snapshot_skeleton_from_scene
    to scope the joint walk to KS-owned joints only."""
    reg = registry or get_registry()
    if not reg:
        return ''
    if not cmds.attributeQuery('root_joint', node=reg, exists=True):
        return ''
    targets = nn.get_message_targets(reg, 'root_joint')
    return targets[0] if targets else ''


def set_registry_root_joint(joint_name: str) -> None:
    """Connect the registry's root_joint message attr to the given joint.
    Replaces any prior connection. No-op if no registry exists.

    Called at build_skeleton time once the root joint has been spawned.
    """
    reg = get_registry()
    if not reg or not cmds.objExists(joint_name):
        return
    nn.ensure_message_attr(reg, 'root_joint')
    # Disconnect any existing source before reconnecting.
    existing = cmds.listConnections(f'{reg}.root_joint',
                                    source=True, destination=False,
                                    plugs=True) or []
    for src in existing:
        try:
            cmds.disconnectAttr(src, f'{reg}.root_joint')
        except Exception:
            pass
    nn.connect_message(joint_name, reg, 'root_joint')


def get_registry_fabricator_version(registry: str = None) -> str:
    """The FS build version stamped at the last successful Build
    Modules, or '' (pre-stamping rig / no registry)."""
    reg = registry or get_registry()
    if not reg:
        return ''
    if not cmds.attributeQuery('fabricator_version', node=reg,
                               exists=True):
        return ''
    return cmds.getAttr(f'{reg}.fabricator_version') or ''


def set_registry_fabricator_version(version: str) -> None:
    """Stamp the registry with an FS build version (lazily adds the
    attr). No-op without a registry."""
    reg = get_registry()
    if not reg:
        return
    if not cmds.attributeQuery('fabricator_version', node=reg,
                               exists=True):
        cmds.addAttr(reg, ln='fabricator_version', dt='string')
    cmds.setAttr(f'{reg}.fabricator_version', version, type='string')


def find_component_node_by_id(component_id: str) -> str:
    """Look up the network node for a given component id. Returns the node
    name or '' if not found. Walks every fab_component node in the scene."""
    for cnode in get_all_component_nodes():
        if get_component_id(cnode) == component_id:
            return cnode
    return ''


def find_component_for_joint(joint_name: str) -> str:
    """Return the Fabricator component node owning this joint, or ''
    if the joint isn't part of any Fabricator component.

    Walks joint.message outbound connections looking for a target on
    a fab_component-marked node's joints[] multi.
    """
    if not cmds.objExists(joint_name):
        return ''
    connections = cmds.listConnections(
        f'{joint_name}.message', source=False, destination=True, plugs=True,
    ) or []
    for plug in connections:
        node = plug.split('.')[0]
        if cmds.attributeQuery(_COMPONENT_MARKER, node=node, exists=True):
            return node
    return ''


# ─── Component instance nodes ────────────────────────────────────────────────

def create_component_node(component_id: str, component_type: str, joints: list,
                          parent_plug: str, side: str,
                          options: dict, persisted: dict,
                          role: str = '', region: str = '',
                          joint_names: list = None) -> str:
    """Create a network node for a component instance, link to registry,
    connect to its joints by message.

    role / region: cross-rig pose-library identifiers (Spec 5). Empty
    strings are valid — pose library skips components with empty
    identifiers when populating cross-rig sets.

    joint_names: canonical list of joint name strings. Stored as a
    string-array attr regardless of whether the joints exist yet in the
    scene. build_skeleton reads this to reconnect joints[] message-multi
    after spawning joints (fixes template-load-then-Build-Skeleton).
    Falls back to joints when not supplied.
    """
    reg = get_registry()
    if not reg:
        raise RuntimeError('No fab_registry. Call create_registry() first.')

    safe = _safe_node_name(component_id)
    name = f'{_COMPONENT_PREFIX}{safe}'
    if cmds.objExists(name):
        raise RuntimeError(f'Component node {name!r} already exists.')

    node = nn.create_marked_node(_COMPONENT_MARKER, name, marker_value=component_type)

    nn.ensure_string_attr(node, 'component_id',   default=component_id)
    nn.ensure_string_attr(node, 'component_type', default=component_type)
    nn.ensure_string_attr(node, 'parent_plug',    default=parent_plug)
    nn.ensure_string_attr(node, 'side',           default=side)
    nn.ensure_string_attr(node, 'role',           default=role)
    nn.ensure_string_attr(node, 'region',         default=region)
    nn.ensure_string_attr(node, 'options_json',   default=json.dumps(options))
    nn.ensure_string_attr(node, 'persisted_json', default=json.dumps(persisted))
    nn.ensure_bool_attr(node, 'is_built', default=False)

    # joint_names string-array — canonical mirror of the joints[] message-multi.
    # Survives joint-not-existing-yet (e.g. components created at template-load
    # before Build Skeleton runs). build_skeleton reconciles the message-multi
    # against this list after spawning joints.
    canonical_names = list(joint_names) if joint_names is not None else list(joints)
    cmds.addAttr(node, ln='joint_names', dt='stringArray')
    cmds.setAttr(f'{node}.joint_names', len(canonical_names), *canonical_names,
                 type='stringArray')

    # Joints: message-multi inbound from each joint
    nn.ensure_message_attr(node, 'joints', multi=True)
    for j in joints:
        if cmds.objExists(j):
            nn.connect_message_multi(j, node, 'joints')

    # Spaces: one message-multi per ctrl_role declared in the component's
    # Contract.space_consumers. Each multi holds the user-added space
    # targets for that ctrl. Defaults (world/parent/etc.) live in the
    # contract; user additions append after via the Properties Spaces UX.
    # Look up the class via a deferred import — nodes.py is imported by
    # the modules package at startup, so a top-level import would create
    # a cycle.
    from maya_tools.rigging.fabricator.modules import get_component_class
    try:
        cls = get_component_class(component_type)
    except KeyError:
        cls = None
    if cls is not None:
        for consumer in cls.CONTRACT.space_consumers:
            nn.ensure_message_attr(
                node, f'spaces_{consumer.ctrl_role}', multi=True,
            )

    # Registry back-link + register in registry's components multi
    nn.ensure_message_attr(node, 'registry')
    nn.connect_message(reg, node, 'registry')
    nn.connect_message_multi(node, reg, 'components')

    # Slot for the component's owned top-of-rig group (populated at build time)
    nn.ensure_message_attr(node, 'top_grp')
    # Slot for matrix-blend DG nodes that drive bind joints — populated by
    # components like SimpleIK at build time, walked by unbuild() to delete
    # the DG nodes (they're not under rig_grp, so the cascade leaves them
    # dangling otherwise).
    nn.ensure_message_attr(node, 'bind_blend_nodes', multi=True)
    nn.ensure_message_attr(node, 'space_switch_nodes', multi=True)

    # Derived Limbs (spec 2026-07-11): limb identity + membership are
    # derived for any component class with CONTRACT.limb_features. Runs
    # BEFORE the on_added hook dispatch below so a class's own on_added
    # (should one ever need it) can already see its limb connection.
    # Same choke point as on_added — every caller (UI add path, mayapy
    # tests, template load, mirror, branch_ops) funnels through
    # create_component_node, so this reaches all of them too.
    derive_limb(node)

    # Component-class lifecycle hook — fires AFTER joints[]/registry are
    # fully wired, so a hook implementation (e.g. FollowJointComponent.
    # on_added seeding a linked follow rule) can read this node's own
    # state. Every caller (UI add path, mayapy tests, template load,
    # mirror, branch_ops) funnels through this one function, so hooking
    # here — not in any individual caller — is the single choke point
    # that reaches all of them.
    _dispatch_component_hook(node, 'on_added')

    return node


def get_all_component_nodes(registry: str = None) -> list:
    reg = registry or get_registry()
    if not reg:
        return []
    return nn.get_message_targets(reg, 'components')


def find_all_component_nodes_by_marker() -> list:
    """Return every node carrying the fab_component marker, regardless
    of whether it's still wired into the registry.

    `get_all_component_nodes()` walks the registry's `components` message
    multi — fast and the right answer when the rig is healthy, but it
    silently misses component nodes that lost their registry connection
    (orphan state after a broken / partial cleanup, or when the registry
    was deleted first). Use this scan in destructive teardown paths
    (new_rig) so leftover fab_<id> nodes don't collide on the next
    template load."""
    return nn.find_marked_nodes(_COMPONENT_MARKER) or []


def find_all_registry_nodes() -> list:
    """All fab_registry-marked nodes. Normally one; this returns every
    match so teardown can clean up stray registries from a corrupted
    scene state."""
    return nn.find_marked_nodes(_REGISTRY_MARKER) or []


def cleanup_orphan_components() -> int:
    """Delete component network nodes whose joints[] multi is empty in
    a scene that has at least one other live component.

    Maya cleans the joints[] message-multi connections on joint delete,
    but the component network node persists as an orphan. After the
    artist deletes joints (e.g. via outliner), the component is
    invisible in the canvas (which filters by live joints) but still
    rebuilt by Build Modules.

    Detection: walk every fab_component; if its joints[] multi has
    zero targets, it's a candidate. But naive deletion would also nuke
    legitimate pre-build state (e.g. just-loaded template before Build
    Skeleton's reconciliation runs, where every component has joints[]
    empty because the joints don't exist yet). Pre-flight gate: only
    sweep if at least one component has a populated joints[]. That
    state can only exist post-Build-Skeleton (reconciliation has run),
    so empty joints[] in that context is genuinely an orphan and not
    a pre-build artifact.

    Important: `joint_names` does NOT reliably indicate orphan-ness.
    After joint delete + Mirror Joints, the names list points to the
    newly-recreated joints, but the message-multi connections were
    never re-established. joints[] is the authoritative signal.

    Returns the number of orphan components deleted.
    """
    all_components = get_all_component_nodes()
    # Pre-flight: detect pre-build state and bail to avoid destructive
    # sweep when no component has reconciled yet.
    has_live = any(get_component_joints(c) for c in all_components)
    if not has_live:
        return 0
    deleted = 0
    for cnode in all_components:
        if get_component_joints(cnode):
            continue  # has live message connections — not orphan
        if not get_component_joint_names(cnode):
            continue  # no canonical names — indeterminate, keep
        cmds.delete(cnode)
        deleted += 1
    return deleted


def get_component_id(node: str) -> str:
    return cmds.getAttr(f'{node}.component_id') or ''


def get_component_type(node: str) -> str:
    return cmds.getAttr(f'{node}.component_type') or ''


def set_component_type(node: str, component_type: str) -> None:
    """Rewrite a component node's stored type string in place. Used to
    self-heal a legacy/version-gated type alias (e.g. 'IKArm' ->
    'RibbonIKArm', modules._VERSION_GATED_LEGACY_TYPE_MAP) onto its
    resolved successor after a successful build, so the mapping's own
    promise — "stops applying once this rig is rebuilt" — holds: without
    this, the registry's fabricator_version stamp (written at the end of
    every successful build) flips scene-wide to "current" while the node's
    type string is still the retired one, so any read that happens after
    that same build (e.g. an immediate Unbuild) would fail to resolve it."""
    cmds.setAttr(f'{node}.component_type', component_type, type='string')


def get_component_joints(node: str) -> list:
    return nn.get_message_targets(node, 'joints')


def get_component_joint_names(node: str) -> list:
    """Return the string-array of canonical joint names for this component.
    Survives joint-not-existing-yet (e.g. at template-load time). Empty
    list if the attribute doesn't exist (pre-fix scenes).
    """
    if not cmds.attributeQuery('joint_names', node=node, exists=True):
        return []
    raw = cmds.getAttr(f'{node}.joint_names') or []
    return list(raw)


def set_component_joint_names(node: str, names: list) -> None:
    """Replace the joint_names string-array on a component network node.
    Idempotent: creates the attr if it doesn't exist (pre-fix scenes
    transparently upgrade on first write).
    """
    names = list(names)
    if not cmds.attributeQuery('joint_names', node=node, exists=True):
        cmds.addAttr(node, ln='joint_names', dt='stringArray')
    cmds.setAttr(f'{node}.joint_names', len(names), *names, type='stringArray')


def get_ctrl_space_names(node: str, ctrl_role: str) -> list:
    """Return the list of joint short names connected to this component's
    spaces_<ctrl_role> message-multi. Used by build_modules to assemble
    the per-instance user additions for that ctrl. Empty list if the
    attr doesn't exist (component contract has no consumer for this
    ctrl_role, or pre-pivot scene).
    """
    attr = f'spaces_{ctrl_role}'
    if not cmds.attributeQuery(attr, node=node, exists=True):
        return []
    targets = nn.get_message_targets(node, attr)
    # listConnections returns short names by default; defensively
    # normalize on '|' (long-path segments) and ':' (namespace prefixes)
    # in case Maya disambiguated to a partial path.
    return [t.split('|')[-1].split(':')[-1] for t in targets if cmds.objExists(t)]


def add_ctrl_space(node: str, ctrl_role: str, joint_name: str) -> bool:
    """Connect joint_name to the component's spaces_<ctrl_role> message-
    multi. Returns True on success, False if the joint doesn't exist or
    isn't a joint. nn.connect_message_multi handles identity-based
    idempotency.

    Spec Section 2: spaces are joint-only by design. Widen the nodeType
    gate here if future components need transform-typed space providers.
    """
    if not cmds.objExists(joint_name):
        return False
    if cmds.nodeType(joint_name) != 'joint':
        return False
    attr = f'spaces_{ctrl_role}'
    nn.ensure_message_attr(node, attr, multi=True)
    nn.connect_message_multi(joint_name, node, attr)
    return True


def remove_ctrl_space(node: str, ctrl_role: str, joint_name: str) -> None:
    """Disconnect joint_name from the component's spaces_<ctrl_role>
    message-multi. No-op if attr doesn't exist or joint isn't connected.

    Note: if the scene has duplicate short names (blocked by
    find_duplicate_transforms in validate_rig before Build Modules),
    this removes ALL matches. Acceptable — duplicate short names are
    a build-time hard error, not a steady state we need to preserve.
    """
    attr = f'spaces_{ctrl_role}'
    if not cmds.attributeQuery(attr, node=node, exists=True):
        return
    short = joint_name.split('|')[-1].split(':')[-1]
    targets = nn.get_message_targets(node, attr)
    remaining = [
        t for t in targets
        if t.split('|')[-1].split(':')[-1] != short
    ]
    if len(remaining) == len(targets):
        return  # not connected; nothing to remove
    nn.replace_message_multi(node, attr, remaining)


def get_component_parent_plug(node: str) -> str:
    return cmds.getAttr(f'{node}.parent_plug') or ''


def get_component_side(node: str) -> str:
    return cmds.getAttr(f'{node}.side') or 'md'


def get_component_role(node: str) -> str:
    """Cross-rig pose-library role string. '' if not set."""
    if not cmds.attributeQuery('role', node=node, exists=True):
        return ''
    return cmds.getAttr(f'{node}.role') or ''


def set_component_role(node: str, role: str) -> None:
    nn.ensure_string_attr(node, 'role', default='')
    cmds.setAttr(f'{node}.role', role or '', type='string')


def get_component_region(node: str) -> str:
    """Anatomical region tag for pose-library auto-sets. '' if not set."""
    if not cmds.attributeQuery('region', node=node, exists=True):
        return ''
    return cmds.getAttr(f'{node}.region') or ''


def set_component_region(node: str, region: str) -> None:
    nn.ensure_string_attr(node, 'region', default='')
    cmds.setAttr(f'{node}.region', region or '', type='string')


def get_component_ik_chain_joints(node: str) -> list:
    """IK chain duplicate joints (3-joint chain for SimpleIK; 5-joint
    extended chain for IKLeg). Empty list for non-IK components.
    Used by the pose library to capture FK-equivalent rotations on IK
    ctrls at save time."""
    if not cmds.attributeQuery('ik_chain_joints', node=node, exists=True):
        return []
    return nn.get_message_targets(node, 'ik_chain_joints')


def get_component_fk_chain_joints(node: str) -> list:
    """FK chain duplicate joints. Empty list for non-IK components.
    Mirror of get_component_ik_chain_joints — pose library reads this
    when retargeting an IK pose into FK-equivalent rotations."""
    if not cmds.attributeQuery('fk_chain_joints', node=node, exists=True):
        return []
    return nn.get_message_targets(node, 'fk_chain_joints')


def get_component_options(node: str) -> dict:
    raw = cmds.getAttr(f'{node}.options_json') or '{}'
    return json.loads(raw)


def set_component_options(node: str, options: dict) -> None:
    old_options = get_component_options(node)
    cmds.setAttr(f'{node}.options_json', json.dumps(options), type='string')
    if old_options != options:
        _dispatch_component_hook(node, 'on_options_changed',
                                 old_options, options)


def get_component_persisted(node: str) -> dict:
    raw = cmds.getAttr(f'{node}.persisted_json') or '{}'
    return json.loads(raw)


def set_component_persisted(node: str, persisted: dict) -> None:
    cmds.setAttr(f'{node}.persisted_json', json.dumps(persisted), type='string')


def is_built(node: str) -> bool:
    return bool(cmds.getAttr(f'{node}.is_built'))


def set_built(node: str, state: bool) -> None:
    cmds.setAttr(f'{node}.is_built', bool(state))


def delete_component_node(node: str) -> None:
    if cmds.objExists(node):
        # Fires BEFORE delete — the hook (e.g. FollowJointComponent.
        # on_removed clearing a linked follow rule) still needs the
        # node's joints[]/options readable.
        _dispatch_component_hook(node, 'on_removed')

        # SPEC 2026-07-09 Limbs + Follower Joints §3.4 lifecycle ruling
        # (BasicIKArm thread's live-validation find, ruling adopted):
        # resolve `node`'s limb BEFORE it's deleted — find_limb_for_joint
        # needs its joints[] connections still live — so
        # _cleanup_orphaned_implicit_limb (below) can tell, AFTER the
        # delete, whether an IMPLICIT limb just lost its last component
        # and must evaporate with it. Explicit (fragment-dropped) limbs
        # are never touched here — see that function's own docstring.
        limb = _resolve_component_limb_for_cleanup(node)

        cmds.delete(node)

        if limb is not None:
            _cleanup_orphaned_implicit_limb(limb)


def _resolve_component_limb_for_cleanup(node: str) -> str | None:
    """Best-effort resolve of `node`'s fab_limb, read BEFORE `node` is
    deleted — `limb_node.find_limb_for_joint`'s ancestor walk needs
    `node`'s joints[] connections (specifically joints[0], the same
    "top joint" convention `_maybe_create_implicit_limb` uses) still
    live to land on the right node. Companion half of the delete-time
    lifecycle cleanup `_cleanup_orphaned_implicit_limb` performs AFTER
    the delete — split in two because the walk needs the node ALIVE and
    the "is it now orphaned" check needs the node GONE (Maya has
    already dropped its dangling connection off the limb's components[]
    multi by then).

    Never raises: a resolution failure here must not block the delete
    already in progress in `delete_component_node` — it just means the
    limb-cleanup half of that call is skipped this one time (the limb,
    if any, is left exactly as it would be if this whole feature didn't
    exist), same discipline as every other best-effort step in this
    module (see `_maybe_create_implicit_limb`'s own class-resolution
    guard)."""
    try:
        from maya_tools.rigging.fabricator import limb_node as ln
        joints = get_component_joints(node)
        if not joints:
            return None
        top_joint = joints[0]
        if not cmds.objExists(top_joint):
            return None
        return ln.find_limb_for_joint(top_joint)
    except Exception:
        cmds.warning(
            f'[Fabricator] {node}: could not resolve its limb before '
            f'delete; implicit-limb cleanup will be skipped for this '
            f'delete. See the Script Editor for the traceback.')
        traceback.print_exc()
        return None


def _cleanup_orphaned_implicit_limb(limb: str) -> None:
    """SPEC 2026-07-09 Limbs + Follower Joints §3.4 lifecycle ruling
    (BasicIKArm thread's live-validation find, ruling adopted): implicit
    limbs are lifecycle-bound to their components; explicit (fragment-
    dropped, implicit=False) limbs are authored entities that survive
    component churn unchanged.

    Called by `delete_component_node` AFTER the triggering component
    node has already been deleted — Maya has already dropped that
    node's dangling connection off `limb`'s components[] multi by the
    time this runs (the same "delete drops the connection" behavior
    `limb_node.delete_limb_node`'s own docstring documents for the
    registry's limbs[] multi), so `limb_node.list_components(limb)`
    here already reflects the post-delete membership.

    If `limb` is implicit AND now has zero live components, its
    membership evaporates with it: `limb_node.delete_limb_node(limb)`
    takes finger_roots[]/twist_upper[]/twist_lower[]/curl_excluded[]
    down with it too (they're connections ON the limb node itself, not
    on the component). This is what closes the orphan-resurrection
    hole: a deleted implicit limb can never again be found by a later
    `find_limb_for_joint(top_joint)` walk, so a component re-added on
    the same joints gets a genuinely FRESH implicit limb (fresh
    discovery only) instead of reconnecting into the old node's stale,
    possibly dial-edited membership.

    An EXPLICIT limb (implicit=False) is never deleted here regardless
    of its components[] count — it's an authored entity (a fragment
    drop), not a byproduct of any one component's existence, so it
    survives component churn unchanged, exactly per the ruling above.

    Never raises into the delete path that has already committed —
    warn+continue on any resolution failure, same discipline as
    `_maybe_create_implicit_limb`."""
    try:
        from maya_tools.rigging.fabricator import limb_node as ln
        if not cmds.objExists(limb):
            return
        if ln.is_implicit(limb) and not ln.list_components(limb):
            ln.delete_limb_node(limb)
    except Exception:
        cmds.warning(
            f'[Fabricator] {limb}: implicit-limb cleanup failed after a '
            f'component delete; the delete itself already succeeded, '
            f'but this limb may now be an empty orphan. See the Script '
            f'Editor for the traceback.')
        traceback.print_exc()


def derive_limb(node: str) -> None:
    """Derived Limbs (spec 2026-07-11): derive `node`'s limb identity +
    membership from the scene. Limbs are never serialized and never
    hand-edited — this function IS the source of limb state, re-run at
    every seam that creates or reshapes components (create_component_
    node, the blueprint-load pass, build preflight, mirror, fragment
    drop). Safe to call as many times as needed.

    Opt-in is CONTRACT.limb_features, derived PER FEATURE:
      'fingers': clear + re-run wrist finger discovery off joints[-1]
                 (curl_excluded re-seeded via discover_fingers's
                 metacarpal heuristic). Fingers are authored as JOINTS
                 in the Armature phase; membership is always a fresh
                 read of the subtree.
      'twists' : clear + re-run convention twist adoption. Dial-created
                 twist joints follow the same convention
                 (limb_set_twist_count), so they re-detect — the
                 refresh is idempotent.
    No features => no limb node at all (a spine is not a limb).

    Limb resolution is REGION-BOUNDED (see _resolve_limb_region_bounded
    — kills the ancestor-capture bug class: a mirrored arm must never
    join a pelvis-anchored limb). A miss creates a fresh implicit limb
    anchored at this component's own top joint, limb_type = the
    component's type string.

    Best-effort throughout, like every other on_added-adjacent step in
    this module: a derivation failure must never block the operation
    that has already otherwise succeeded.
    """
    from maya_tools.rigging.fabricator.modules import get_component_class

    try:
        ctype = get_component_type(node)
        cls = get_component_class(ctype)
    except Exception:
        # A class-resolution failure must never block the operation, so
        # this swallows and returns — but loudly, so a corrupt
        # component_type attr never masquerades as the ordinary
        # "no limb_features" early-out below.
        ctype_str = locals().get('ctype', '?')
        cmds.warning(
            f'[Fabricator] {node}: could not resolve a component class '
            f'for limb derivation (component_type={ctype_str!r}); '
            f'skipping. See the Script Editor for the traceback.')
        traceback.print_exc()
        return
    features = getattr(cls.CONTRACT, 'limb_features', ()) or ()
    if not features:
        return

    try:
        from maya_tools.rigging.fabricator import limb_node as ln
        from maya_tools.rigging.fabricator.modules import _limb_common as hc

        joints = get_component_joints(node)
        if not joints:
            return
        top_joint = joints[0]
        if not cmds.objExists(top_joint):
            return

        # Instance region, falling back to the contract default so a
        # fragment/mirror-created component with an empty region attr
        # still region-matches (2026-07-11 review finding).
        region = (get_component_region(node)
                  or getattr(cls.CONTRACT, 'default_region', '') or '')
        limb = _resolve_limb_region_bounded(node, top_joint, region)
        created_fresh = limb is None
        if created_fresh:
            limb = ln.create_limb_node(ctype, top_joint, implicit=True)
        ln.add_component(limb, node)
        # A limb resolved (or freshly created) here may already carry an
        # EXPLICIT color; cascade it onto `node`'s own 'ctrl_color'
        # unless that option was already given a genuine explicit
        # override. See `limb_node.cascade_color_to_component`.
        ln.cascade_color_to_component(limb, node)

        if 'fingers' in features:
            # Membership is derived state: clear, then re-discover.
            # Deleted finger joints drop out (their connections died
            # with them), reparented ones drop out here, and new joints
            # under the wrist join — no hand-repair path needed.
            for j in list(ln.list_finger_roots(limb)):
                ln.remove_finger_root(limb, j)
            for j in list(ln.list_curl_excluded(limb)):
                ln.remove_curl_excluded(limb, j)
            wrist = joints[-1]
            if cmds.objExists(wrist):
                bp_joints = _live_joint_subtree_specs(wrist)
                from types import SimpleNamespace
                bp = SimpleNamespace(skeleton_joints=bp_joints)
                for finger in hc.discover_fingers(wrist, bp):
                    root = finger.get('root_joint')
                    if root and cmds.objExists(root):
                        ln.add_finger_root(limb, root)
                        for excl in finger.get('curl_excluded') or []:
                            if cmds.objExists(excl):
                                ln.add_curl_excluded(limb, excl)

        if 'twists' in features:
            # Same clear + re-detect discipline. Pre-authored twist
            # joints (UE5.8 Manny convention — 2 per segment, parented
            # to the segment's top joint exactly like
            # limb_set_twist_count spawns them) re-adopt every pass; see
            # limb_node.adopt_existing_twists for the conservative
            # detection contract.
            for j in list(ln.list_twist_upper(limb)):
                ln.remove_twist_upper(limb, j)
            for j in list(ln.list_twist_lower(limb)):
                ln.remove_twist_lower(limb, j)
            adopted = ln.adopt_existing_twists(limb)
            n_adopted = len(adopted['upper']) + len(adopted['lower'])
            if n_adopted and created_fresh:
                # Warn only on a FRESH limb (a real add / first load) —
                # the re-derive seams (build preflight) would otherwise
                # repeat one warning per component per build.
                cmds.warning(
                    f'[Fabricator] {get_component_id(node)}: adopted '
                    f'{n_adopted} existing twist joint(s) — upper: '
                    f'{adopted["upper"] or "none"}, lower: '
                    f'{adopted["lower"] or "none"}.')
    except Exception:
        cmds.warning(
            f'[Fabricator] {node}: limb derivation failed partway '
            f'through; the operation still succeeds, but this '
            f'component\'s limb membership may be incomplete. See the '
            f'Script Editor for the traceback.')
        traceback.print_exc()


def _resolve_limb_region_bounded(node: str, top_joint: str,
                                 region: str = '') -> str | None:
    """The Derived Limbs adoption rule (spec 2026-07-11 §3). Walking up
    from `top_joint` to the NEAREST limb-anchored ancestor
    (limb_node.find_limb_for_joint), adopt that limb only when:

      - its anchor IS our own top joint (sibling components sharing a
        chain share one limb identity), or
      - its region (first non-empty member-component region) matches
        `region` (the component's instance region, contract-default
        fallback applied by the caller) — an arm joins an arm-limb up
        its chain, but a leg can never be captured by a spine/pelvis
        limb.

    Any mismatch returns None and the caller creates a fresh limb at
    the component's own top joint: the safe default is an extra limb
    node, never cross-appendage corruption.
    """
    from maya_tools.rigging.fabricator import limb_node as ln

    limb = ln.find_limb_for_joint(top_joint)
    if limb is None:
        return None

    def _short(n: str) -> str:
        return n.split('|')[-1].split(':')[-1]

    anchor = ln.get_top_joint(limb) or ''
    if anchor and _short(anchor) == _short(top_joint):
        return limb
    limb_region = _limb_region(limb)
    my_region = region or get_component_region(node)
    if limb_region and my_region and limb_region == my_region:
        return limb
    return None


def _limb_region(limb: str) -> str:
    """A limb's region = the first non-empty `region` among its member
    components (components persist region; arm/leg contracts carry
    default_region). '' when no member declares one."""
    from maya_tools.rigging.fabricator import limb_node as ln
    for cnode in ln.list_components(limb):
        try:
            region = get_component_region(cnode)
        except Exception:
            continue
        if region:
            return region
    return ''


def _live_joint_subtree_specs(root_joint: str) -> list:
    """Duck-typed (name, parent) records for every joint BELOW
    `root_joint` (root itself excluded), walked live off the scene DAG —
    exactly the shape `_limb_common.discover_fingers`'s
    `blueprint.skeleton_joints` duck-type needs. Used only by
    `_maybe_create_implicit_limb`'s wrist-child finger discovery, which
    has no saved Blueprint to read (a standalone component add, not a
    limb-fragment load)."""
    from types import SimpleNamespace
    out = []

    def _walk(j, parent_name):
        out.append(SimpleNamespace(name=j, parent=parent_name))
        for child in cmds.listRelatives(j, children=True, type='joint') or []:
            _walk(child, j)

    for child in cmds.listRelatives(root_joint, children=True, type='joint') or []:
        _walk(child, root_joint)
    return out


def _dispatch_component_hook(node: str, hook_name: str, *args) -> None:
    """Call `node`'s component-class hook_name(node, *args), if the
    class defines one beyond Component's own no-op default.

    Shared by create_component_node ('on_added'), delete_component_node
    ('on_removed'), and set_component_options ('on_options_changed') —
    see Component.on_added/on_removed/on_options_changed (modules/
    component.py) for the hook contract. Never raises: a hook failure
    must never block the CRUD operation that triggered it, but it must
    still leave a diagnostic trail (same discipline as follow_rules.
    _notify_change's listener guard).
    """
    from maya_tools.rigging.fabricator.modules import get_component_class
    try:
        ctype = get_component_type(node)
        cls = get_component_class(ctype)
    except Exception:
        return
    hook = getattr(cls, hook_name, None)
    if hook is None:
        return
    try:
        hook(node, *args)
    except Exception:
        traceback.print_exc()


# ─── Guides group (LEGACY) ───────────────────────────────────────────────────
# The transient guide layer was removed 2026-07-04 (the Armature is the
# editable stage). Nothing creates ksfab_guides_grp anymore; get/delete
# remain only so old scenes saved with a stale guides group clean up.

def get_guides_grp() -> str:
    """LEGACY: return a stale ksfab_guides_grp from an old scene, or None."""
    if not cmds.objExists(_GUIDES_GRP_NAME):
        return None
    if not cmds.attributeQuery(_GUIDES_GRP_MARKER, node=_GUIDES_GRP_NAME, exists=True):
        return None
    return _GUIDES_GRP_NAME


def delete_guides_grp() -> None:
    """LEGACY: delete a stale guides group left by a pre-Armature scene."""
    grp = get_guides_grp()
    if grp:
        cmds.delete(grp)


# ─── Pivots group (transient editing tools for component extra_guides) ───────
# Same lifecycle pattern as ksfab_guides_grp. Pivot locators only exist while
# the user is actively positioning them — between Build Skeleton and Build
# Modules, and again after Unbuild Modules.

def get_pivots_grp() -> str:
    """Return the fab_pivots_grp node, or None if not present."""
    if not cmds.objExists(_PIVOTS_GRP_NAME):
        return None
    if not cmds.attributeQuery(_PIVOTS_GRP_MARKER, node=_PIVOTS_GRP_NAME, exists=True):
        return None
    return _PIVOTS_GRP_NAME


def ensure_pivots_grp() -> str:
    """Create or reuse the fab_pivots_grp transform with marker attr."""
    if cmds.objExists(_PIVOTS_GRP_NAME):
        existing = get_pivots_grp()
        if existing:
            return existing
        raise RuntimeError(
            f"Node {_PIVOTS_GRP_NAME!r} exists but isn't a ks v2 pivots group. "
            f"Rename or delete it before continuing."
        )
    grp = cmds.createNode('transform', name=_PIVOTS_GRP_NAME)
    cmds.addAttr(grp, ln=_PIVOTS_GRP_MARKER, dt='string')
    cmds.setAttr(f'{grp}.{_PIVOTS_GRP_MARKER}', 'pivots', type='string')
    cmds.setAttr(f'{grp}.inheritsTransform', False)
    return grp


def delete_pivots_grp() -> None:
    grp = get_pivots_grp()
    if grp and cmds.objExists(grp):
        cmds.delete(grp)


# ─── Rig containers + null-pair builder ──────────────────────────────────────
# Components call ensure_*_grp idempotently; the first component creates the
# containers, subsequent components find and parent into them.
# _organize_scene reparents rig_grp under the top group.

def ensure_rig_grp() -> str:
    """Create-or-find the rig_grp transform. inheritsTransform=True so it
    follows the top group.
    """
    if not cmds.objExists('rig_grp'):
        cmds.createNode('transform', name='rig_grp')
    cmds.setAttr('rig_grp.inheritsTransform', True)
    return 'rig_grp'


def ensure_controls_grp() -> str:
    """fab_controls_grp under rig_grp. inheritsTransform=False so ctrls live in
    pure worldspace (driven by their offset buffers, not by parent transform).
    """
    rig = ensure_rig_grp()
    if not cmds.objExists('fab_controls_grp'):
        cmds.createNode('transform', name='fab_controls_grp')
    cmds.setAttr('fab_controls_grp.inheritsTransform', False)
    parent = cmds.listRelatives('fab_controls_grp', parent=True) or []
    if not parent or parent[0] != rig:
        cmds.parent('fab_controls_grp', rig)
    return 'fab_controls_grp'


def ensure_nulls_grp() -> str:
    """fab_nulls_grp under rig_grp. inheritsTransform=False — the constraint-
    chain anchor for every component's null pair. Visibility=0 so the null
    hierarchy doesn't clutter the viewport (animators interact with ctrls,
    not nulls).
    """
    rig = ensure_rig_grp()
    if not cmds.objExists('fab_nulls_grp'):
        cmds.createNode('transform', name='fab_nulls_grp')
    cmds.setAttr('fab_nulls_grp.inheritsTransform', False)
    cmds.setAttr('fab_nulls_grp.visibility', False)
    parent = cmds.listRelatives('fab_nulls_grp', parent=True) or []
    if not parent or parent[0] != rig:
        cmds.parent('fab_nulls_grp', rig)
    return 'fab_nulls_grp'


def build_null_pair(joint: str) -> tuple:
    """Create offset_null + connector_null for a joint, parented flat under
    fab_nulls_grp. Both snapped to joint world transform.

    Returns (offset_null, connector_null).

    Convention (carries forward from v1):
      - offset_null: static at bind position, dual-purpose anchor for both
        the constraint chain AND future worldspace space-switching targets.
      - connector_null: child of offset_null. The thing the joint is
        constrained to. Driven by the component's ctrl via parent/scale
        constraint.
    """
    short = joint.split('|')[-1].split(':')[-1]
    nulls_grp = ensure_nulls_grp()

    offset_null = cmds.createNode('transform', name=f'{short}_offset_null')
    cmds.matchTransform(offset_null, joint, position=True, rotation=True, scale=False)
    cmds.parent(offset_null, nulls_grp)
    # Re-apply world xform after reparent (parent has inheritsTransform=False).
    cmds.matchTransform(offset_null, joint, position=True, rotation=True, scale=False)

    connector_null = cmds.createNode(
        'transform', name=f'{short}_connector_null', parent=offset_null)
    for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
        cmds.setAttr(f'{connector_null}.{attr}', 0)
    for attr in ('sx', 'sy', 'sz'):
        cmds.setAttr(f'{connector_null}.{attr}', 1)

    hide_internal(offset_null)
    hide_internal(connector_null)

    return offset_null, connector_null


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _safe_node_name(name: str) -> str:
    """Strip characters not valid in a Maya node name."""
    return re.sub(r'[^A-Za-z0-9_]', '_', name).strip('_') or 'unnamed'


def tag_ctrl(ctrl: str, role: str, component_node: str = '',
             joint_index: int = -1) -> None:
    """Tag a ctrl with KS metadata so the marking menu + pose library +
    anim mirror + other tools can identify it.

    role: short string ('fk_ctrl', 'ik_end_ctrl', 'pv_ctrl',
          'master_switch_ctrl', 'heel_ctrl', etc.). Used by the marking
          menu to filter actions and by the pose library as part of the
          per-ctrl identity tuple.
    component_node: if non-empty, connects ctrl.fab_owner to
                    component_node.message. Pose library walks this
                    backward to find which component owns each ctrl.
    joint_index: which entry in instance.joints[] this ctrl primarily
                 drives. -1 for ctrls that don't map 1:1 to a joint slot
                 (e.g. switch ctrl, foot pivot ctrls). Pose library uses
                 this to disambiguate chain ctrls that share role
                 (shoulder/elbow/wrist all 'fk_ctrl').

    Also captures fab_bind_matrix — ctrl's worldMatrix at tag time.
    Required by the anim mirror algorithm; bind frames on UE5-style rigs
    are NOT YZ-symmetric (R bone X = -L bone X but encoded as ry(180°)
    rather than S-sandwich), so mirror needs an explicit bind reference
    to compute world-space pose offsets. Call tag_ctrl AFTER positioning
    the ctrl at bind (matchTransform / TRS-zero / OPM-set complete).
    Components that subsequently shift the ctrl (IKLeg rewriting the foot
    OPM to a foot-frame matrix) re-stamp fab_bind_matrix afterwards.
    """
    if not cmds.attributeQuery('fab_role', node=ctrl, exists=True):
        cmds.addAttr(ctrl, ln='fab_role', dt='string')
    cmds.setAttr(f'{ctrl}.fab_role', role, type='string')

    if not cmds.attributeQuery('fab_joint_index', node=ctrl, exists=True):
        cmds.addAttr(ctrl, ln='fab_joint_index', at='long', dv=-1)
    cmds.setAttr(f'{ctrl}.fab_joint_index', joint_index)

    if not cmds.attributeQuery('fab_bind_matrix', node=ctrl, exists=True):
        cmds.addAttr(ctrl, ln='fab_bind_matrix', dt='matrix')
    bind_wm = cmds.xform(ctrl, q=True, ws=True, matrix=True)
    cmds.setAttr(f'{ctrl}.fab_bind_matrix', *bind_wm, type='matrix')

    if component_node:
        if not cmds.attributeQuery('fab_owner', node=ctrl, exists=True):
            cmds.addAttr(ctrl, ln='fab_owner', at='message')
        try:
            cmds.connectAttr(f'{component_node}.message',
                             f'{ctrl}.fab_owner', force=True)
        except Exception:
            pass


def resolve_ctrl_tag_attr(ctrl: str, canonical_attr: str) -> str | None:
    """Belt-and-braces name resolution for a ctrl tag attr written by
    `tag_ctrl` (`fab_role` / `fab_owner` / `fab_joint_index` /
    `fab_bind_matrix`): prefers the current `fab_*` name, falling back
    to the pre-rebrand `ksfab_*` name (2026-07 KinematicSolutions ->
    FabricatorStudio sweep) so a ctrl on a rig that hasn't been rebuilt
    or run through `fab_migrate_scene.migrate_scene` yet still resolves.

    `tag_ctrl` itself writes `fab_*` ONLY — this is a READ-time fallback
    for the three hot, cheap identity lookups that need it (pose library
    address resolution, the animation marking menu, selection_sets.
    _tagged_ctrls). Remove after v1, once every shipped rig has been
    rebuilt or migrated.

    Returns the attr name that actually exists on `ctrl` (never a
    made-up name), or None if neither is present.
    """
    if cmds.attributeQuery(canonical_attr, node=ctrl, exists=True):
        return canonical_attr
    legacy_attr = 'ks' + canonical_attr  # fab_role -> ksfab_role, etc.
    if cmds.attributeQuery(legacy_attr, node=ctrl, exists=True):
        return legacy_attr
    return None


def hide_internal(node: str) -> None:
    """Hide internal scaffolding from animator-facing surfaces.

    Use on chain joint duplicates, IK handles, pivot transforms, anchors,
    null pairs — anything that's NOT a ctrl or bind joint.

    - Marks TRS + visibility non-keyable, removed from channel box.

    Internals stay VISIBLE in the Outliner on purpose: hiding them there
    makes rig debugging miserable. Channel-box cleanup is enough to keep
    animators from selecting/keying these by accident.

    Does NOT lock TRS — most internals are constraint-driven and locking
    can interfere with DG evaluation. Hide-only is enough to keep animators
    from selecting or keying these by accident.
    """
    if not cmds.objExists(node):
        return
    for attr in ('translate', 'rotate', 'scale'):
        for axis in ('X', 'Y', 'Z'):
            try:
                cmds.setAttr(f'{node}.{attr}{axis}',
                             keyable=False, channelBox=False)
            except Exception:
                pass
    try:
        cmds.setAttr(f'{node}.visibility',
                     keyable=False, channelBox=False)
    except Exception:
        pass


def mirror_ctrl_cvs_x(ctrl: str) -> None:
    """Mirror a ctrl's CVs along the X axis (negate CV.x). Used for
    side-aware control shapes — author the curve for the left side, then
    call this on the right-side build to produce a mirrored shape without
    needing a separately-authored right-side curve.

    Operates on CVs in object space. Idempotent at the call level — calling
    twice mirrors back to the original. Components should call once based
    on instance.side.
    """
    if not cmds.objExists(ctrl):
        return
    import maya.api.OpenMaya as om
    for shape in cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []:
        if cmds.nodeType(shape) != 'nurbsCurve':
            continue
        try:
            sel = om.MSelectionList()
            sel.add(shape)
            fn = om.MFnNurbsCurve(sel.getDagPath(0))
            points = fn.cvPositions()
            mirrored = om.MPointArray([
                om.MPoint(-p.x, p.y, p.z) for p in points
            ])
            fn.setCVPositions(mirrored)
            fn.updateCurve()
        except Exception:
            pass


def scale_ctrl_to_joint_radius(ctrl: str, joint: str,
                                multiplier: float = 1.0) -> None:
    """Scale ctrl's CVs by joint.radius * multiplier. Used on first build
    (when no persisted CV data exists) to size ctrls to roughly match the
    bone they're driving.

    Operates on CVs in object space, so the transform identity is preserved
    (no scale baked into the transform's TRS). Idempotent at the CV level —
    each call rescales from current CV positions, so call once per ctrl
    after build_shape and BEFORE any persisted CV data is applied.
    """
    if not cmds.objExists(ctrl) or not cmds.objExists(joint):
        return
    if not cmds.attributeQuery('radius', node=joint, exists=True):
        return
    radius = cmds.getAttr(f'{joint}.radius')
    if radius <= 0:
        return
    scale = radius * multiplier
    import maya.api.OpenMaya as om
    for shape in cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []:
        if cmds.nodeType(shape) != 'nurbsCurve':
            continue
        try:
            sel = om.MSelectionList()
            sel.add(shape)
            fn = om.MFnNurbsCurve(sel.getDagPath(0))
            points = fn.cvPositions()
            scaled = om.MPointArray([
                om.MPoint(p.x * scale, p.y * scale, p.z * scale)
                for p in points
            ])
            fn.setCVPositions(scaled)
            fn.updateCurve()
        except Exception:
            pass
