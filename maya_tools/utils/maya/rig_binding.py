# Python/utils/maya/rig_binding.py
"""FAB_RigBinding — cross-tool rig contract node.

Owned by no single tool. Written by KS (on build_rig) and the model
exporter (on SK_ export). Read by the anim exporter to discover joints
and the rig's drive-hierarchy groups for clean teardown at export time.

One FAB_RigBinding_<rig_label> per rig. Idempotent — write_rig_binding()
creates or refreshes in place.
"""
__author__ = "Adrian Melian"

import datetime as _dt
import re
from pathlib import Path

import maya.cmds as cmds

from maya_tools.utils.maya import network_nodes as nn


BINDING_MARKER = 'fab_rig_binding'
BINDING_PREFIX = 'FAB_RigBinding_'


# ─────────────────────────────────────────────────────────────────────────────
# Lookup
# ─────────────────────────────────────────────────────────────────────────────

def find_all_bindings() -> list[str]:
    """Return all FAB_RigBinding network nodes in the scene."""
    return nn.find_marked_nodes(BINDING_MARKER)


def find_live_bindings() -> list[str]:
    """Bindings whose root_joint connection still resolves to a real joint."""
    out = []
    for node in find_all_bindings():
        root = nn.get_message_target(node, 'root_joint')
        if root and cmds.objExists(root):
            out.append(node)
    return out


def find_binding(rig_label: str) -> str | None:
    """Return the binding node for a given rig_label, or None."""
    for node in find_all_bindings():
        if (cmds.getAttr(f'{node}.rig_label') or '') == rig_label:
            return node
    return None


def find_binding_for_root(root_joint: str) -> str | None:
    """Return the FAB_RigBinding whose root_joint connects to this joint, or None."""
    if not cmds.objExists(root_joint):
        return None
    target = cmds.ls(root_joint, long=True)
    if not target:
        return None
    for node in find_all_bindings():
        srcs = cmds.listConnections(f'{node}.root_joint',
                                    source=True, destination=False) or []
        if srcs and cmds.ls(srcs[0], long=True) == target:
            return node
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Read
# ─────────────────────────────────────────────────────────────────────────────

def get_rig_label(binding: str) -> str:
    return cmds.getAttr(f'{binding}.rig_label') or ''


def get_root_joint(binding: str) -> str | None:
    return nn.get_message_target(binding, 'root_joint')


def get_export_joints(binding: str) -> list[str]:
    """Return export_joints[] connections preserving slot index order."""
    indices = cmds.getAttr(f'{binding}.export_joints', multiIndices=True) or []
    out: list[str] = []
    for i in sorted(indices):
        srcs = cmds.listConnections(f'{binding}.export_joints[{i}]',
                                    source=True, destination=False) or []
        if srcs:
            out.append(srcs[0])
    return out


def get_nulls_grp(binding: str) -> str | None:
    if not cmds.attributeQuery('nulls_grp', node=binding, exists=True):
        return None
    return nn.get_message_target(binding, 'nulls_grp')


def get_controls_grp(binding: str) -> str | None:
    if not cmds.attributeQuery('controls_grp', node=binding, exists=True):
        return None
    return nn.get_message_target(binding, 'controls_grp')


def get_binding_data(binding: str) -> dict:
    """Read all binding attrs + resolve message connections."""
    return {
        'node':            binding,
        'rig_label':       get_rig_label(binding),
        'root_joint':      get_root_joint(binding) or '',
        'export_joints':   get_export_joints(binding),
        'nulls_grp':       get_nulls_grp(binding) or '',
        'controls_grp':    get_controls_grp(binding) or '',
        'last_written_at': cmds.getAttr(f'{binding}.last_written_at') or '',
    }


# ─────────────────────────────────────────────────────────────────────────────
# Write — idempotent create-or-refresh
# ─────────────────────────────────────────────────────────────────────────────

def write_rig_binding(rig_label: str, root_joint: str, export_joints: list[str],
                      nulls_grp: str | None, controls_grp: str | None) -> str:
    """Create or refresh FAB_RigBinding_<rig_label>. Idempotent.

    Updates rig_label, export_joints[], nulls_grp/controls_grp connections,
    and last_written_at on every call. Returns the binding node name.

    For coordination between writers (KS on build, SK_ on export): both call
    this with the same rig_label; whoever runs last wins on overlap. This is
    the SK-export-wins-on-overlap policy from the spec.
    """
    if not cmds.objExists(root_joint):
        raise RuntimeError(f'write_rig_binding: root joint does not exist: {root_joint}')

    node = _get_or_create_binding_node(root_joint, rig_label)

    cmds.setAttr(f'{node}.rig_label', rig_label, type='string')
    nn.connect_message(root_joint, node, 'root_joint')
    _set_export_joints(node, export_joints)
    _set_groups(node, nulls_grp, controls_grp)
    cmds.setAttr(f'{node}.last_written_at',
                 _dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                 type='string')
    return node


def cleanup_stale_bindings() -> list[str]:
    """Delete every FAB_RigBinding whose root_joint is broken/missing.
    Returns the names of deleted bindings.
    """
    deleted = []
    for node in find_all_bindings():
        srcs = cmds.listConnections(f'{node}.root_joint',
                                    source=True, destination=False) or []
        if not srcs or not cmds.objExists(srcs[0]):
            try:
                cmds.delete(node)
                deleted.append(node)
            except Exception as e:
                cmds.warning(f'Could not delete stale binding {node}: {e}')
    return deleted


def derive_rig_label(root_joint: str) -> str:
    """Derive the binding label from a root joint's namespace, with scene-stem
    fallback when no namespace exists (e.g. when running in the rig file directly).

    Examples:
        derive_rig_label('CapsuleMichael:root')           -> 'CapsuleMichael'
        derive_rig_label('a:b:foot_jnt')                  -> 'b' (deepest namespace component)
        derive_rig_label('root')   in CapsuleMichael.ma   -> 'CapsuleMichael' (scene stem)
        derive_rig_label('root')   when scene unsaved     -> 'root' (last-resort basename)
    """
    short = root_joint.split('|')[-1]
    if ':' in short:
        ns = short.rsplit(':', 1)[0]
        parts = ns.lstrip(':').split(':')
        if parts and parts[-1]:
            return parts[-1]

    scene = cmds.file(q=True, sn=True) or ''
    if scene:
        return Path(scene).stem

    return short


# ── private ──────────────────────────────────────────────────────────────────

def _safe_node_name(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9_]', '_', name).strip('_') or 'binding'


def _get_or_create_binding_node(root_joint: str, rig_label: str) -> str:
    """Find the binding for root_joint, creating it if absent. Idempotent."""
    existing = find_binding_for_root(root_joint)
    if existing:
        return existing

    base = BINDING_PREFIX + _safe_node_name(rig_label)
    name = base
    i = 2
    while cmds.objExists(name):
        name = f'{base}_{i}'
        i += 1

    node = nn.create_marked_node(BINDING_MARKER, name, marker_value='binding')
    nn.ensure_string_attr(node, 'rig_label', default=rig_label)
    nn.ensure_message_attr(node, 'root_joint')
    nn.ensure_message_attr(node, 'export_joints', multi=True)
    nn.ensure_message_attr(node, 'nulls_grp')
    nn.ensure_message_attr(node, 'controls_grp')
    nn.ensure_string_attr(node, 'last_written_at', default='')
    return node


def _set_export_joints(node: str, joints: list[str]) -> None:
    nn.replace_message_multi(node, 'export_joints', joints)


def _set_groups(node: str, nulls_grp: str | None, controls_grp: str | None) -> None:
    """Connect nulls_grp / controls_grp to the named transforms (or clear if None)."""
    for attr_name, target in (('nulls_grp', nulls_grp),
                              ('controls_grp', controls_grp)):
        plug = f'{node}.{attr_name}'
        srcs = cmds.listConnections(plug, source=True, destination=False,
                                    plugs=True) or []
        for src in srcs:
            try:
                cmds.disconnectAttr(src, plug)
            except Exception:
                pass
        if target and cmds.objExists(target):
            cmds.connectAttr(f'{target}.message', plug, force=True)
