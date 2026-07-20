# maya_tools/animation/pose_library/address.py
"""Component-address tuples for cross-rig pose portability.

Address shape: (component_type, side, role, ctrl_role, joint_index)

The first three identify the component on any rig that shares KS
component types + sides (cross-rig identity from Fabricator's contract).
The last two identify the specific ctrl within that component.

Adapters:
- ctrl_to_address(ctrl)         — read tags from a scene ctrl
- address_to_ctrl(addr, binding) — resolve an address back to a ctrl
                                    name on the rig owning `binding`.
"""
__author__ = "Adrian Melian"

from dataclasses import dataclass

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes as fab_nodes
from maya_tools.utils.maya import rig_binding


@dataclass(frozen=True)
class Address:
    """A cross-rig-portable identifier for a single ctrl.

    Two resolution modes:
    - **Same-rig fast path** via `component_id` (the per-scene unique ID
      Fabricator stamps on each component network node). When the pose was
      saved on this same rig — or any rig that hasn't had component IDs
      regenerated — this resolves unambiguously even when multiple
      components share `(component_type, side, role)`.
    - **Cross-rig fallback** via `(component_type, side, role)`. Component
      IDs differ between rigs, so the resolver drops to this tuple. Role
      is author-set; when multiple components on the target rig share
      `(type, side)` and role is empty, this tuple is ambiguous.
    """
    component_type: str   # 'World' | 'SimpleFK' | 'SimpleIK' | 'IKLeg' | ...
    side: str             # 'md' | 'lf' | 'rt'
    role: str             # author-set; '' is allowed for same-rig only
    ctrl_role: str        # fab_role tag value
    joint_index: int      # fab_joint_index; -1 = no-1:1-joint ctrl
    component_id: str = ''  # Fabricator fab_id; same-rig disambiguator

    def as_tuple(self) -> tuple:
        return (self.component_type, self.side, self.role,
                self.ctrl_role, self.joint_index)

    def to_dict(self) -> dict:
        out = {
            'component_type': self.component_type,
            'side': self.side,
            'role': self.role,
            'ctrl_role': self.ctrl_role,
            'joint_index': self.joint_index,
        }
        # Only emit component_id when set so JSON stays tidy for poses
        # saved before this field existed; absent → cross-rig fallback.
        if self.component_id:
            out['component_id'] = self.component_id
        return out

    @classmethod
    def from_dict(cls, d: dict) -> 'Address':
        return cls(
            component_type=d.get('component_type', ''),
            side=d.get('side', 'md'),
            role=d.get('role', ''),
            ctrl_role=d.get('ctrl_role', ''),
            joint_index=int(d.get('joint_index', -1)),
            component_id=d.get('component_id', ''),
        )


def ctrl_to_address(ctrl: str) -> Address | None:
    """Read the fab_* tags off a ctrl and return its cross-rig address.
    Returns None if the ctrl isn't a Fabricator-tagged ctrl.

    Belt-and-braces: resolves each tag via fab_nodes.resolve_ctrl_tag_attr
    (fab_* preferred, ksfab_* fallback for a rig not yet rebuilt/migrated
    past the 2026-07 rename — remove after v1).
    """
    if not cmds.objExists(ctrl):
        return None
    role_attr = fab_nodes.resolve_ctrl_tag_attr(ctrl, 'fab_role')
    if role_attr is None:
        return None
    ctrl_role = cmds.getAttr(f'{ctrl}.{role_attr}') or ''
    joint_index_attr = fab_nodes.resolve_ctrl_tag_attr(ctrl, 'fab_joint_index')
    joint_index = cmds.getAttr(f'{ctrl}.{joint_index_attr}') if joint_index_attr else -1

    # Ctrls tagged with a role but no owner connection (e.g. an
    # older-build FKAim link_ctrl from before the tag_ctrl wire was
    # added) can't be cross-rig addressed. Skip rather than raise.
    owner_attr = fab_nodes.resolve_ctrl_tag_attr(ctrl, 'fab_owner')
    if owner_attr is None:
        return None
    owner_targets = cmds.listConnections(f'{ctrl}.{owner_attr}',
                                          source=True, destination=False) or []
    if not owner_targets:
        return None
    cnode = owner_targets[0]

    return Address(
        component_type=fab_nodes.get_component_type(cnode),
        side=fab_nodes.get_component_side(cnode),
        role=fab_nodes.get_component_role(cnode),
        ctrl_role=ctrl_role,
        joint_index=int(joint_index),
        component_id=fab_nodes.get_component_id(cnode),
    )


def address_to_ctrl(address: Address, binding: str) -> str | None:
    """Resolve an address to a ctrl name on the rig owning `binding`.

    Two-phase lookup:

    1. **Same-rig** — when `address.component_id` is populated AND a
       network node with that ID exists on the rig, use it as the sole
       candidate. Survives ambiguous `(type, side, role)` tuples (e.g.
       three FKChain md components with empty role on the same rig).
    2. **Cross-rig fallback** — `(component_type, side, role)` tuple
       match. When role is empty and multiple candidates share
       `(type, side)`, the tuple is genuinely ambiguous and resolution
       fails (returns None). This is the historical behavior; the
       component_id phase exists specifically to unblock it for the
       overwhelmingly-common same-rig case.

    Both phases are SCOPED to component nodes that actually belong to the
    rig owning `binding` (the owners of ctrls under its controls_grp). This
    matters when multiple KS rigs share a scene: component IDs and
    (type, side, role) tuples collide across rigs built from the same
    blueprint, so an unscoped match would pick the wrong rig's component
    and resolve to nothing under this binding's controls_grp.

    Then walks descendants of `binding.controls_grp` for a ctrl whose
    `fab_owner` is one of the candidates AND whose `(fab_role,
    fab_joint_index)` match.

    Returns the ctrl name, or None when nothing matches / address is
    ambiguous in the cross-rig fallback.
    """
    controls_grp = rig_binding.get_controls_grp(binding)
    if not controls_grp or not cmds.objExists(controls_grp):
        return None

    # Walk this rig's controls once. Long paths so attributeQuery resolves
    # on referenced rigs; type='transform' so shapes (no fab_* tags) drop
    # out. The walk both scopes the candidate component nodes to THIS rig
    # and supplies the ctrl list for the final match — and caches each
    # ctrl's owner so we don't re-query connections in the match loop.
    all_descendants = cmds.listRelatives(
        controls_grp, allDescendents=True, fullPath=True,
        type='transform',
    ) or []
    ctrl_owner = {}
    rig_owner_nodes = set()
    for ctrl in all_descendants:
        # Belt-and-braces: fab_owner preferred, ksfab_owner fallback for
        # a not-yet-rebuilt/migrated rig — remove after v1.
        owner_attr = fab_nodes.resolve_ctrl_tag_attr(ctrl, 'fab_owner')
        if owner_attr is None:
            continue
        owners = cmds.listConnections(f'{ctrl}.{owner_attr}',
                                       source=True, destination=False) or []
        if owners:
            ctrl_owner[ctrl] = owners[0]
            rig_owner_nodes.add(owners[0])

    candidates = []

    # Phase 1: same-rig fast path via component_id (scoped to this rig).
    if address.component_id:
        for cnode in rig_owner_nodes:
            if fab_nodes.get_component_id(cnode) == address.component_id:
                candidates.append(cnode)
                break

    # Phase 2: cross-rig fallback by (component_type, side, role) (scoped).
    if not candidates:
        for cnode in rig_owner_nodes:
            if fab_nodes.get_component_type(cnode) != address.component_type:
                continue
            if fab_nodes.get_component_side(cnode) != address.side:
                continue
            cnode_role = fab_nodes.get_component_role(cnode)
            if address.role and cnode_role != address.role:
                continue
            candidates.append(cnode)
        if not candidates:
            return None
        if len(candidates) > 1 and not address.role:
            # Ambiguous — role required to disambiguate cross-rig.
            return None

    candidate_set = set(candidates)
    for ctrl in all_descendants:
        if ctrl_owner.get(ctrl) not in candidate_set:
            continue
        role_attr = fab_nodes.resolve_ctrl_tag_attr(ctrl, 'fab_role')
        if role_attr is None:
            continue
        if cmds.getAttr(f'{ctrl}.{role_attr}') != address.ctrl_role:
            continue
        joint_index_attr = fab_nodes.resolve_ctrl_tag_attr(ctrl, 'fab_joint_index')
        if joint_index_attr is not None:
            if cmds.getAttr(f'{ctrl}.{joint_index_attr}') != address.joint_index:
                continue
        return ctrl
    return None
