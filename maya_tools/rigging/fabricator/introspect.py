# Python/maya_tools/rigging/fabricator/introspect.py
"""Fabricator's public read-only state contract.

This module is the stable public API for out-of-process consumers (an AI
bridge calls it next task). It OWNS the right to call Fabricator's private
snapshot internals (fs_app._snapshot_blueprint_from_scene,
blueprint.io._blueprint_to_dict) — external consumers import introspect,
never the underscore-prefixed functions directly.

Every function here is a pure read: zero mutations of any kind, of the
scene or of Fabricator's network nodes.

Divergence from ui.state.detect_mode: that function legitimately
self-heals stale is_built flags (a real setAttr) when it finds built
components but no rig_grp — the right thing for the interactive UI to
do. introspect NEVER self-heals; _detect_mode_readonly() below mirrors
detect_mode's decision logic without the write, and fabricator_status()
surfaces the condition via stale_built_flags instead of fixing it.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes
from maya_tools.rigging.fabricator.ui import state as ui_state
from maya_tools.utils.maya import rig_binding


class NoRegistryError(RuntimeError):
    """Raised when the scene has no Fabricator registry."""


def _detect_mode_readonly() -> tuple:
    """Mirror ui.state.detect_mode()'s decision logic with zero writes.

    Returns (mode, stale_built_flags). stale_built_flags is True when
    components carry is_built=True but rig_grp is missing — the exact
    condition detect_mode() resets via nodes.set_built(cn, False) before
    falling through. introspect reports the condition instead of fixing
    it, so a pure status read never mutates the scene.
    """
    built_nodes = [cn for cn in nodes.get_all_component_nodes()
                   if nodes.is_built(cn)]
    stale = False
    if built_nodes:
        if cmds.objExists('rig_grp'):
            return ui_state.MODE_MODULES_BUILT, False
        # Same condition detect_mode() heals with a write; here we only
        # note it and fall through to skeleton/empty detection below.
        stale = True

    reg = nodes.get_registry()
    if reg:
        for cnode in nodes.get_all_component_nodes():
            for j in nodes.get_component_joints(cnode):
                if cmds.objExists(j):
                    return ui_state.MODE_SKELETON, stale

    return ui_state.MODE_EMPTY, stale


def fabricator_status() -> dict:
    """Cheap overview of the current scene. Never raises, even on an
    empty scene.

    Keys: mode ('empty'|'skeleton'|'modules_built'), has_registry (bool),
    blueprint_name, rig_label, fabricator_version (all '' when there is
    no registry), stale_built_flags (bool — True means is_built flags are
    stale and opening the Fabricator UI will self-heal them; introspect
    itself never does).
    """
    reg = nodes.get_registry()
    mode, stale = _detect_mode_readonly()
    return {
        'mode':               mode,
        'has_registry':       bool(reg),
        'blueprint_name':     nodes.get_blueprint_name(reg) if reg else '',
        'rig_label':          nodes.get_rig_label(reg) if reg else '',
        'fabricator_version': nodes.get_registry_fabricator_version(reg) if reg else '',
        'stale_built_flags':  stale,
    }


def rig_snapshot() -> dict:
    """Full rig description: the current scene's Blueprint (as a dict)
    plus a components_live list carrying live network-node state.

    Raises NoRegistryError when the scene has no Fabricator registry.
    """
    reg = nodes.get_registry()
    if not reg:
        raise NoRegistryError('No Fabricator registry in scene.')

    # Deferred: fs_app eagerly imports the whole tool family (a heavy
    # chain) — paying that cost is only acceptable here, in the one
    # function that actually needs it, not at introspect import time
    # (fabricator_status is documented as the cheap call). Also sidesteps
    # any future import-cycle risk, matching nodes.py's own deferred
    # modules import (nodes.py:344-345).
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator.blueprint import io as blueprint_io

    bp = fs_app._snapshot_blueprint_from_scene()
    data = blueprint_io._blueprint_to_dict(bp)

    components_live = []
    for cnode in nodes.get_all_component_nodes(reg):
        components_live.append({
            'id':          nodes.get_component_id(cnode),
            'type':        nodes.get_component_type(cnode),
            'side':        nodes.get_component_side(cnode),
            'role':        nodes.get_component_role(cnode),
            'region':      nodes.get_component_region(cnode),
            'built':       nodes.is_built(cnode),
            'parent_plug': nodes.get_component_parent_plug(cnode),
        })
    data['components_live'] = components_live

    return data


def component_details(component_id: str) -> dict:
    """Everything about one component.

    Raises NoRegistryError when the scene has no Fabricator registry;
    raises KeyError when component_id doesn't match any component.
    """
    reg = nodes.get_registry()
    if not reg:
        raise NoRegistryError('No Fabricator registry in scene.')

    cnode = nodes.find_component_node_by_id(component_id)
    if not cnode:
        raise KeyError(f'No component with id {component_id!r}.')

    return {
        'id':           nodes.get_component_id(cnode),
        'node':         cnode,
        'type':         nodes.get_component_type(cnode),
        'side':         nodes.get_component_side(cnode),
        'role':         nodes.get_component_role(cnode),
        'region':       nodes.get_component_region(cnode),
        'built':        nodes.is_built(cnode),
        'parent_plug':  nodes.get_component_parent_plug(cnode),
        'joints':       nodes.get_component_joints(cnode),
        'joint_names':  nodes.get_component_joint_names(cnode),
        'options':      nodes.get_component_options(cnode),
        'persisted':    nodes.get_component_persisted(cnode),
    }


def binding_summaries() -> list:
    """Summaries of every FAB_RigBinding node in the scene."""
    return [rig_binding.get_binding_data(b) for b in rig_binding.find_all_bindings()]


def validate_live_blueprint() -> list:
    """Run the structural blueprint validator against the live scene.

    Returns the validator's message list (empty = clean). Raises
    NoRegistryError when the scene has no registry.
    """
    reg = nodes.get_registry()
    if not reg:
        raise NoRegistryError('No Fabricator registry in scene.')

    # Deferred: fs_app eagerly imports the whole tool family (a heavy
    # chain) — paying that cost is only acceptable here, in the one
    # function that actually needs it, not at introspect import time
    # (fabricator_status is documented as the cheap call). Also sidesteps
    # any future import-cycle risk, matching nodes.py's own deferred
    # modules import (nodes.py:344-345).
    from maya_tools.rigging.fabricator import fs_app
    from maya_tools.rigging.fabricator.blueprint import builder

    bp = fs_app._snapshot_blueprint_from_scene()
    return builder.validate_blueprint(bp)
