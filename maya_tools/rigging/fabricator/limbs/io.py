# maya_tools/rigging/fabricator/limbs/io.py
"""Limb fragment YAML reader/writer. Parallel to blueprint.io but the
top-level container is LimbFragment, not Blueprint.

write_yaml dumps fragment dataclass -> dict -> YAML.
read_yaml parses YAML -> LimbFragment, validating kind + schema_version.
"""
__author__ = "Adrian Melian"

from dataclasses import asdict
from pathlib import Path

import yaml

from maya_tools.rigging.fabricator.blueprint.schema import (
    JointSpec, ComponentSpec,
)
from maya_tools.rigging.fabricator.limbs.schema import (
    LimbFragment, ExternalAnchor, EXTERNAL_PLACEHOLDER,
)


def write_yaml(fragment: LimbFragment, path) -> None:
    """Serialize fragment to YAML at `path`.

    save_limb in fs_app handles parent-dir creation; this function
    assumes the directory exists and just writes the file.
    """
    payload = _fragment_to_dict(fragment)
    Path(path).write_text(
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
        encoding='utf-8',
    )


def _fragment_to_dict(fragment: LimbFragment) -> dict:
    payload = {
        'kind': 'limb_fragment',
        'schema_version': fragment.schema_version,
        'name': fragment.name,
        'description': fragment.description,
    }
    # File stamp only emitted when set — pre-stamping fragments
    # round-trip without churn ('' stays absent, reads back '').
    if fragment.fabricator_version:
        payload['fabricator_version'] = fragment.fabricator_version
    payload.update({
        'external_anchor': asdict(fragment.external_anchor),
        'skeleton': {
            'joints': [
                _joint_to_dict(j) for j in fragment.skeleton_joints
            ],
        },
        'components': [
            {
                'id': c.id,
                'type': c.type,
                'joints': list(c.joints),
                'side': c.side,
                'role': c.role,
                'region': c.region,
                'parent_plug': c.parent_plug,
                'options': dict(c.options),
                'persisted': dict(c.persisted),
            }
            for c in fragment.components
        ],
    })
    # (Derived Limbs, spec 2026-07-11: the limb: block is never written
    # anymore — limb identity/membership re-derives on every drop. The
    # reader silently ignores a limb: key in older files.)
    # Authored Armature-ctrl local transforms (LimbFragment.armature_ctrls
    # docstring). Omit-when-empty convention — a fragment saved without
    # an Armature standing round-trips byte-for-byte with no key churn.
    if fragment.armature_ctrls:
        payload['armature_ctrls'] = {
            j: {'translate': list(t.get('translate') or []),
                'rotate': list(t.get('rotate') or [])}
            for j, t in fragment.armature_ctrls.items()
        }
    return payload


def _joint_to_dict(j: JointSpec) -> dict:
    out = {
        'name': j.name,
        'parent': j.parent,
        'translate': list(j.translate),
        'rotate': list(j.rotate),
        'joint_orient': list(j.joint_orient),
        'rotate_order': j.rotate_order,
        'radius': j.radius,
    }
    # Aimer state (Armature, spec 2026-07-04 §7) — only emitted when
    # set so legacy limb YAMLs round-trip without churn.
    if j.aim_target:
        out['aim_target'] = j.aim_target
    if any(abs(v) > 1e-9 for v in (j.aim_offset or [])):
        out['aim_offset'] = list(j.aim_offset)
    return out


def read_yaml(path) -> LimbFragment:
    """Parse a .limb.yaml file. Validates kind == 'limb_fragment' and
    schema_version compatibility. Raises RuntimeError on schema errors."""
    raw = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise RuntimeError(f'Limb YAML is not a mapping: {path}')

    kind = raw.get('kind')
    if kind != 'limb_fragment':
        raise RuntimeError(
            f"Limb YAML kind is {kind!r}, expected 'limb_fragment'. "
            f"Path: {path}"
        )

    schema_version = raw.get('schema_version', '')
    if schema_version != '1.0':
        raise RuntimeError(
            f"Limb schema version {schema_version!r} unsupported "
            f"(expected '1.0'). Path: {path}"
        )

    anchor_raw = raw.get('external_anchor') or {}
    anchor = ExternalAnchor(plug_kind=anchor_raw.get('plug_kind', 'matrix'))

    skel_block = raw.get('skeleton') or {}
    joints_raw = skel_block.get('joints') or []
    skel_joints = [
        JointSpec(
            name=j['name'],
            parent=j.get('parent'),
            translate=list(j.get('translate', [0, 0, 0])),
            rotate=list(j.get('rotate', [0, 0, 0])),
            joint_orient=list(j.get('joint_orient', [0, 0, 0])),
            rotate_order=j.get('rotate_order', 'xyz'),
            radius=float(j.get('radius', 1.0)),
            aim_target=j.get('aim_target', ''),
            aim_offset=list(j.get('aim_offset', [0, 0, 0])),
        )
        for j in joints_raw
    ]

    # Sanity check: well-formed limb YAMLs always have exactly one root
    # joint marked with the EXTERNAL anchor placeholder. A YAML that's been
    # hand-edited or saved-from-elsewhere without that marker can't be
    # parented under a host joint at load time, so fail loudly here rather
    # than silently parenting the limb's root to scene root.
    if skel_joints and not any(j.parent == EXTERNAL_PLACEHOLDER for j in skel_joints):
        raise RuntimeError(
            f"Limb YAML has no '{EXTERNAL_PLACEHOLDER}' root joint anchor. "
            f"Re-save the limb from a built rig. Path: {path}"
        )

    comps_raw = raw.get('components') or []
    components = [
        ComponentSpec(
            type=c['type'],
            joints=list(c['joints']),
            id=c.get('id'),
            parent_plug=c.get('parent_plug', ''),
            side=c.get('side', 'md'),
            role=c.get('role', ''),
            region=c.get('region', ''),
            options=dict(c.get('options') or {}),
            persisted=dict(c.get('persisted') or {}),
        )
        for c in comps_raw
    ]

    # A legacy 'limb:' block (pre-Derived-Limbs fragments) is silently
    # ignored — membership re-derives at drop time.
    return LimbFragment(
        name=raw.get('name', ''),
        schema_version=schema_version,
        description=raw.get('description', ''),
        fabricator_version=raw.get('fabricator_version', ''),
        external_anchor=anchor,
        skeleton_joints=skel_joints,
        components=components,
        armature_ctrls=dict(raw.get('armature_ctrls') or {}),
    )

