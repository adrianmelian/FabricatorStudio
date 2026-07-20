# Python/maya_tools/rigging/fabricator/ui/scene_canvas_model.py
"""Scene → canvas data-layer projection (V1 read-only).

Walks the Maya scene's joint hierarchy and reads fab_* metadata to
produce an immutable tree consumable by CanvasPanel. Pure data; no Qt.

The canvas projects the scene state directly. Scene IS truth — see
docs/superpowers/specs/2026-05-10-ks-scene-is-truth-design.md.

V1 makes no visual distinction between KS-managed joints and external
ones (manual outliner creation, Skeleton IO import). The user-discipline
rule "use Rig Maker for structural edits" carries that distinction
operationally; V2 reintroduces it visually alongside an auto-promote /
right-click Promote action.
"""
__author__ = "Adrian Melian"

from dataclasses import dataclass, field
from typing import List

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes
from maya_tools.rigging.fabricator.ui.limb_grouping import LimbRecord


@dataclass(frozen=True)
class CanvasNode:
    """Immutable tree node — one per joint in the scene.

    Component info is denormalized onto the node. Joints with no
    component get component_id='', component_type=''.
    """
    joint_name: str
    component_id: str = ''      # '' if no component owns this joint as joints[0]
    component_type: str = ''    # '' if no component
    side: str = ''              # 'l' / 'r' / 'c' / 'md' / '' — read from component
    children: tuple = field(default_factory=tuple)  # tuple[CanvasNode, ...]


def read_scene_canvas() -> List[CanvasNode]:
    """Walk the scene's joint hierarchy + read KS metadata.

    Returns the list of root CanvasNodes (joints with no joint parent).
    Empty list if no joints in scene.
    """
    all_joints = cmds.ls(type='joint', long=False) or []
    if not all_joints:
        return []

    # joint_name -> component info
    joint_to_component = _index_components_by_joint()

    # Walk parents to build child mapping. cmds.listRelatives with parent=True
    # returns long path or short name depending on context — we use short names
    # consistently and rely on Maya's unique-short-name guarantee within the
    # rig file (validated by find_duplicate_transforms before build).
    children_of: dict = {}
    parent_of: dict = {}
    for j in all_joints:
        par_list = cmds.listRelatives(j, parent=True, type='joint') or []
        par = par_list[0] if par_list else None
        parent_of[j] = par
        children_of.setdefault(par, []).append(j)

    roots = [j for j in all_joints if parent_of.get(j) is None]
    return [_build_node(r, children_of, joint_to_component) for r in sorted(roots)]


def read_limb_records() -> List[LimbRecord]:
    """Every `fab_limb` node in the scene, projected to the plain
    `LimbRecord` triple `ui.limb_grouping.build_render_tree` consumes
    (SPEC 2026-07-09 Limbs + Follower Joints §3.5 — canvas collapse).

    `top_joint` is short-named (`split('|')[-1].split(':')[-1]`) to
    match `CanvasNode.joint_name`'s own convention — `build_render_tree`
    matches purely by string equality, so a mismatched naming
    convention here would silently produce zero collapses.

    A limb node whose `top_joint` connection has gone stale (deleted,
    or never resolved) is skipped outright — degrade like every other
    accessor in this module and `limb_node.py` itself does on a stale
    reference, never raise; a limb with no live top_joint has nothing
    to anchor a render decision to anyway.
    """
    from maya_tools.rigging.fabricator import limb_node as ln

    out = []
    for node in ln.all_limb_nodes():
        top = ln.get_top_joint(node)
        if not top or not cmds.objExists(top):
            continue
        top_short = top.split('|')[-1].split(':')[-1]
        out.append(LimbRecord(
            top_joint=top_short,
            limb_type=ln.get_limb_type(node),
            is_implicit=ln.is_implicit(node),
        ))
    return out


def read_limb_member_tints() -> dict:
    """joint -> component_type for every LIMB MEMBER joint (KS #8):
    finger chains and dialed twist joints live on the fab_limb node's
    message multis, not in any component's joints[], so the canvas tint
    map never covered them — an RibbonIKArm's fingers and twists rendered
    untinted next to its colored shoulder/elbow/wrist.

    The tint type is the limb's PRIMARY component's type (the member
    component anchored at the limb's own top_joint, falling back to the
    limb's first member component). The caller merges this map with
    setdefault so any joint a component directly owns (e.g. a weapon
    joint's AdvancedFK) keeps its OWN component tint, never the limb's.
    """
    from maya_tools.rigging.fabricator import limb_node as ln

    def _short(n):
        return n.split('|')[-1].split(':')[-1]

    out = {}
    for limb in ln.all_limb_nodes():
        top = ln.get_top_joint(limb)
        top_short = _short(top) if top else ''
        ltype = ''
        comps = ln.list_components(limb)
        for cnode in comps:
            cj = nodes.get_component_joints(cnode) or []
            if cj and _short(cj[0]) == top_short:
                ltype = nodes.get_component_type(cnode)
                break
        if not ltype and comps:
            ltype = nodes.get_component_type(comps[0])
        if not ltype:
            continue

        members = []
        for fr in ln.list_finger_roots(limb):
            if not cmds.objExists(fr):
                continue
            members.append(fr)
            members.extend(cmds.listRelatives(
                fr, allDescendents=True, type='joint') or [])
        members.extend(ln.list_twist_upper(limb))
        members.extend(ln.list_twist_lower(limb))
        for j in members:
            out.setdefault(_short(j), ltype)
    return out


def _index_components_by_joint() -> dict:
    """Walk every fab_<id> component node, map its primary joint
    (joints[0]) to a {id, type, side} dict.

    Joints not owned as joints[0] aren't keyed (a joint can be in a
    component's joints list at index >0 — a chain's interior joint —
    but the canvas tags it as belonging to the component that owns it
    primarily).
    """
    out = {}
    for cnode in nodes.get_all_component_nodes():
        cid = nodes.get_component_id(cnode)
        ctype = nodes.get_component_type(cnode)
        cjoints = nodes.get_component_joints(cnode)
        if not cjoints:
            continue
        primary = cjoints[0]
        side = ''
        if cmds.attributeQuery('side', node=cnode, exists=True):
            side = cmds.getAttr(f'{cnode}.side') or ''
        out[primary] = {'id': cid, 'type': ctype, 'side': side}
    return out


def _build_node(joint_name: str, children_of: dict, joint_to_component: dict) -> CanvasNode:
    """Recursive — build a CanvasNode for joint_name + its descendants."""
    comp = joint_to_component.get(joint_name, {})
    children = tuple(
        _build_node(c, children_of, joint_to_component)
        for c in sorted(children_of.get(joint_name, []))
    )
    return CanvasNode(
        joint_name=joint_name,
        component_id=comp.get('id', ''),
        component_type=comp.get('type', ''),
        side=comp.get('side', ''),
        children=children,
    )
