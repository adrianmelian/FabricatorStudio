# Python/maya_tools/rigging/fabricator/blueprint/io.py
"""Blueprint YAML serialization + parsing.

Round-trip invariant: read_yaml(write_yaml(bp)) == bp (for any well-formed
Blueprint). This is the disk persistence layer — Save writes, Load reads.
"""
__author__ = "Adrian Melian"

from pathlib import Path

import yaml

from maya_tools.rigging.fabricator.blueprint.schema import (
    Blueprint, JointSpec, ComponentSpec, OriginInfo,
    CURRENT_SCHEMA_VERSION,
)


def read_yaml(path: str) -> Blueprint:
    """Parse a .blueprint.yaml file and return a Blueprint dataclass.

    Raises FileNotFoundError if path is missing, yaml.YAMLError on malformed
    YAML, or RuntimeError on schema_version we can't migrate from.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'Blueprint not found: {path}')

    with path.open('r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}

    # Schema version check + migration hook (none needed yet for 1.0)
    version = data.get('schema_version', '0.0')
    if version != CURRENT_SCHEMA_VERSION:
        # Future: dispatch to migrations registry. For now, refuse.
        raise RuntimeError(
            f'Blueprint schema_version {version!r} is not supported by this '
            f'build (current: {CURRENT_SCHEMA_VERSION!r}). No migration available.'
        )

    # Origin block (optional)
    origin = None
    if 'origin' in data and data['origin']:
        o = data['origin']
        origin = OriginInfo(
            blueprint  = o.get('blueprint', ''),
            path       = o.get('path', ''),
            version    = o.get('version', ''),
            cloned_at  = o.get('cloned_at', ''),
        )

    # Skeleton block
    sk = data.get('skeleton', {}) or {}
    joints_data = sk.get('joints', []) or []
    skeleton_joints = [_joint_from_dict(jd) for jd in joints_data]

    # Components block
    comps_data = data.get('components', []) or []
    components = [_component_from_dict(cd) for cd in comps_data]

    # Wiring block (Spec 1: empty)
    wiring = data.get('wiring', []) or []

    return Blueprint(
        name             = data.get('name', ''),
        schema_version   = version,
        description      = data.get('description', ''),
        origin           = origin,
        skeleton_joints  = skeleton_joints,
        components       = components,
        wiring           = wiring,
        orient_convention = data.get('orient_convention', ''),
        fabricator_version = data.get('fabricator_version', ''),
    )


def write_yaml(blueprint: Blueprint, path: str) -> None:
    """Serialize a Blueprint to a .blueprint.yaml file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = _blueprint_to_dict(blueprint)

    with path.open('w', encoding='utf-8') as f:
        yaml.safe_dump(
            data, f,
            default_flow_style=False,
            sort_keys=False,
            indent=2,
            allow_unicode=True,
        )


# ── private — dict <-> dataclass conversions ──────────────────────────────────

def _joint_from_dict(d: dict) -> JointSpec:
    return JointSpec(
        name            = d['name'],
        parent          = d.get('parent'),
        translate       = list(d.get('translate', [0.0, 0.0, 0.0])),
        rotate          = list(d.get('rotate', [0.0, 0.0, 0.0])),
        joint_orient    = list(d.get('joint_orient', [0.0, 0.0, 0.0])),
        rotate_order    = d.get('rotate_order', 'xyz'),
        radius          = float(d.get('radius', 1.0)),
        world_translate = list(d.get('world_translate', [0.0, 0.0, 0.0])),
        aim_target      = d.get('aim_target', ''),
        aim_offset      = list(d.get('aim_offset', [0.0, 0.0, 0.0])),
        world_rotate    = (list(d['world_rotate'])
                           if d.get('world_rotate') is not None else None),
    )


def _joint_to_dict(j: JointSpec) -> dict:
    out = {
        'name':         j.name,
        'parent':       j.parent,
        'translate':    list(j.translate),
        'rotate':       list(j.rotate),
        'joint_orient': list(j.joint_orient),
        'rotate_order': j.rotate_order,
        'radius':       j.radius,
    }
    # world_translate only emitted when non-default — keeps hand-authored
    # blueprints (single-joint props etc.) clean. Bootstrap fills it.
    if any(abs(v) > 1e-9 for v in (j.world_translate or [])):
        out['world_translate'] = list(j.world_translate)
    # Aimer state only emitted when set — legacy blueprints round-trip
    # without churn.
    if j.aim_target:
        out['aim_target'] = j.aim_target
    if any(abs(v) > 1e-9 for v in (j.aim_offset or [])):
        out['aim_offset'] = list(j.aim_offset)
    # world_rotate only emitted when captured (root joints, 2026-07-14) —
    # legacy blueprints round-trip without churn.
    if getattr(j, 'world_rotate', None) is not None:
        out['world_rotate'] = list(j.world_rotate)
    return out


def _component_from_dict(d: dict) -> ComponentSpec:
    return ComponentSpec(
        type         = d['type'],
        joints       = list(d.get('joints', [])),
        id           = d.get('id'),
        parent_plug  = d.get('parent_plug', ''),
        side         = d.get('side', 'md'),
        role         = d.get('role', ''),
        region       = d.get('region', ''),
        options      = dict(d.get('options', {}) or {}),
        persisted    = dict(d.get('persisted', {}) or {}),
    )


def _component_to_dict(c: ComponentSpec) -> dict:
    out = {
        'id':          c.id,
        'type':        c.type,
        'joints':      list(c.joints),
        'side':        c.side,
        'options':     dict(c.options),
    }
    # Cross-rig pose-library identifiers — only emit when set so legacy
    # blueprints round-trip without churn.
    if c.role:
        out['role'] = c.role
    if c.region:
        out['region'] = c.region
    if c.parent_plug:
        out['parent_plug'] = c.parent_plug
    if c.persisted:
        out['persisted'] = dict(c.persisted)
    return out


def _origin_to_dict(o: OriginInfo) -> dict:
    return {
        'blueprint':  o.blueprint,
        'path':       o.path,
        'version':    o.version,
        'cloned_at':  o.cloned_at,
    }


def _blueprint_to_dict(bp: Blueprint) -> dict:
    out = {
        'name':           bp.name,
        'schema_version': bp.schema_version,
    }
    # File stamp only emitted when set — pre-stamping blueprints
    # round-trip without churn ('' stays absent, reads back '').
    if bp.fabricator_version:
        out['fabricator_version'] = bp.fabricator_version
    if bp.orient_convention:
        out['orient_convention'] = bp.orient_convention
    if bp.description:
        out['description'] = bp.description
    if bp.origin is not None:
        out['origin'] = _origin_to_dict(bp.origin)
    out['skeleton'] = {
        'joints': [_joint_to_dict(j) for j in bp.skeleton_joints],
    }
    out['components'] = [_component_to_dict(c) for c in bp.components]
    out['wiring'] = list(bp.wiring)
    return out
