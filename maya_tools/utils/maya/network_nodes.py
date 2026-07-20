# Python/utils/maya/network_nodes.py
"""Shared network-node CRUD helpers.

Pure Maya utility - no rigging, exporter, or domain knowledge. Used by
Fabricator (nodes.py), model exporter (export_core.py), anim exporter
(anim_core.py), and the cross-tool rig-binding helper (utils/maya/rig_binding.py).

Marker attr names (e.g. 'fab_registry', 'fab_exporter_registry') stay in
their owning tool - these helpers operate on whatever marker name the
caller passes.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds


# Node creation + lookup


def create_marked_node(marker_attr: str, node_name: str,
                       marker_value: str = '') -> str:
    """Create a network node tagged with a string marker attr.

    Marker attr identifies the node's type/owner. Look up via find_marked_nodes()
    or find_one_marked_node().

    Returns the created node name.
    """
    node = cmds.createNode('network', name=node_name)
    cmds.addAttr(node, ln=marker_attr, dt='string')
    cmds.setAttr(f'{node}.{marker_attr}', marker_value or marker_attr, type='string')
    return node


def find_marked_nodes(marker_attr: str) -> list[str]:
    """Return all network nodes carrying this marker attr."""
    out = []
    for node in (cmds.ls(type='network') or []):
        if cmds.attributeQuery(marker_attr, node=node, exists=True):
            out.append(node)
    return out


def find_one_marked_node(marker_attr: str) -> str | None:
    """Return the first network node carrying this marker, or None."""
    nodes = find_marked_nodes(marker_attr)
    return nodes[0] if nodes else None


# Attr management (idempotent - add only if missing)


def ensure_message_attr(node: str, attr: str, multi: bool = False) -> None:
    """Add a message attr if it doesn't exist."""
    if not cmds.attributeQuery(attr, node=node, exists=True):
        if multi:
            cmds.addAttr(node, ln=attr, at='message', multi=True, indexMatters=False)
        else:
            cmds.addAttr(node, ln=attr, at='message')


def ensure_string_attr(node: str, attr: str, default: str = '') -> None:
    """Add a string attr if it doesn't exist; set default value when creating."""
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, ln=attr, dt='string')
        cmds.setAttr(f'{node}.{attr}', default, type='string')


def ensure_bool_attr(node: str, attr: str, default: bool = False) -> None:
    """Add a bool attr if it doesn't exist; set default value when creating."""
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, ln=attr, at='bool')
        cmds.setAttr(f'{node}.{attr}', bool(default))


# Message connections


def connect_message(src: str, dst_node: str, dst_attr: str) -> None:
    """Idempotently connect src.message -> dst_node.dst_attr (single message slot).

    No-op if the connection already exists. Replaces any prior connection on dst.
    """
    src_plug = f'{src}.message'
    dst_plug = f'{dst_node}.{dst_attr}'
    if cmds.isConnected(src_plug, dst_plug):
        return
    # Disconnect any existing source on dst before reconnecting
    existing = cmds.listConnections(dst_plug, source=True, destination=False,
                                    plugs=True) or []
    for s in existing:
        try:
            cmds.disconnectAttr(s, dst_plug)
        except Exception:
            pass
    cmds.connectAttr(src_plug, dst_plug)


def connect_message_multi(src: str, dst_node: str, dst_attr: str) -> None:
    """Idempotently append src.message -> dst_node.dst_attr (multi message slot,
    next available index).

    No-op if src is already connected to any index of the multi. Auto-creates
    the multi attr on dst_node if it doesn't exist (callers don't need to
    pre-declare).
    """
    ensure_message_attr(dst_node, dst_attr, multi=True)
    src_plug = f'{src}.message'
    indices = cmds.getAttr(f'{dst_node}.{dst_attr}', multiIndices=True) or []
    for i in indices:
        plug = f'{dst_node}.{dst_attr}[{i}]'
        srcs = cmds.listConnections(plug, source=True, destination=False,
                                    plugs=True) or []
        if src_plug in srcs:
            return
    cmds.connectAttr(src_plug, f'{dst_node}.{dst_attr}', nextAvailable=True)


def replace_message_multi(dst_node: str, dst_attr: str, sources: list[str]) -> None:
    """Replace all connections on dst_node.dst_attr with `sources` (skip non-existent).

    Disconnects everything first, removes the multi indices, then reconnects in
    the given order. Used by entry-set-nodes patterns across all three tools.
    Auto-creates the multi attr on dst_node if it doesn't exist.
    """
    ensure_message_attr(dst_node, dst_attr, multi=True)
    indices = cmds.getAttr(f'{dst_node}.{dst_attr}', multiIndices=True) or []
    for i in indices:
        plug = f'{dst_node}.{dst_attr}[{i}]'
        srcs = cmds.listConnections(plug, source=True, destination=False,
                                    plugs=True) or []
        for s in srcs:
            try:
                cmds.disconnectAttr(s, plug)
            except Exception:
                pass
        try:
            cmds.removeMultiInstance(plug, b=True)
        except Exception:
            pass
    for s in sources:
        if s and cmds.objExists(s):
            cmds.connectAttr(f'{s}.message', f'{dst_node}.{dst_attr}',
                             nextAvailable=True)


def disconnect_message(src: str, dst_node: str, dst_attr: str) -> None:
    """Idempotently disconnect src.message <- dst_node.dst_attr. No-op if absent."""
    src_plug = f'{src}.message'
    dst_plug = f'{dst_node}.{dst_attr}'
    if cmds.isConnected(src_plug, dst_plug):
        cmds.disconnectAttr(src_plug, dst_plug)


def get_message_target(node: str, attr: str) -> str | None:
    """Return the source node connected to node.attr, or None.

    Use for single-message slots. If multiple are connected (shouldn't happen
    on a single-slot attr), returns the first.
    """
    if not cmds.attributeQuery(attr, node=node, exists=True):
        return None
    conns = cmds.listConnections(f'{node}.{attr}', source=True, destination=False) or []
    return conns[0] if conns else None


def get_message_targets(node: str, attr: str) -> list[str]:
    """Return all source nodes connected to node.attr.

    Use for multi-message slots; returns deduplicated, order-stable list.
    For ordered-by-index access, iterate the multi indices manually.

    Missing attr → [] (never raises): message attrs are created lazily
    by the features that use them, so nodes from before a feature
    existed legitimately lack its attr — a reader throwing on that
    aborted whole unbuild captures and wiped persisted state
    (2026-07-05, pin_nodes on a pre-pin SimpleIK node).
    """
    if not cmds.attributeQuery(attr, node=node, exists=True):
        return []
    conns = cmds.listConnections(f'{node}.{attr}', source=True, destination=False) or []
    return list(dict.fromkeys(conns))
