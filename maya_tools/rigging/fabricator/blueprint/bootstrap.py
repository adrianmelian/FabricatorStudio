# Python/maya_tools/rigging/fabricator/blueprint/bootstrap.py
"""Bootstrap a starter Blueprint from a Skeleton IO export.

The blueprint's skeleton block is populated from the JSON; components and
origin remain empty/None. The user then opens the blueprint in the KS UI
and adds component assignments.
"""
__author__ = "Adrian Melian"

import json
from pathlib import Path

from maya_tools.rigging.fabricator.blueprint.schema import (
    Blueprint, JointSpec, CURRENT_SCHEMA_VERSION,
)


# Maya's rotateOrder enum: int -> rotate-axis-string.
_ROTATE_ORDER_LOOKUP = ('xyz', 'yzx', 'zxy', 'xzy', 'yxz', 'zyx')


def from_skeleton_json(path: str, blueprint_name: str = '') -> Blueprint:
    """Read a Skeleton IO .skeleton.json and produce a starter Blueprint.

    Args:
        path: filesystem path to the .skeleton.json file.
        blueprint_name: optional name override. Default: scene-stem of the
                        skeleton.json filename (e.g. 'Michael.skeleton.json'
                        -> 'Michael').

    Returns:
        Blueprint with skeleton_joints populated, components empty.

    Raises FileNotFoundError, json.JSONDecodeError, or RuntimeError on
    unrecognized schema.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'Skeleton JSON not found: {path}')

    with path.open('r', encoding='utf-8') as f:
        data = json.load(f)

    if not blueprint_name:
        # 'Michael.skeleton.json' -> 'Michael'
        stem = path.name
        if stem.endswith('.skeleton.json'):
            blueprint_name = stem[:-len('.skeleton.json')]
        else:
            blueprint_name = path.stem

    joints_data = data.get('joints', []) or []
    skeleton_joints = [_skeleton_io_joint_to_jointspec(jd) for jd in joints_data]

    return Blueprint(
        name             = blueprint_name,
        schema_version   = CURRENT_SCHEMA_VERSION,
        description      = '',
        origin           = None,
        skeleton_joints  = skeleton_joints,
        components       = [],
        wiring           = [],
    )


def _skeleton_io_joint_to_jointspec(jd: dict) -> JointSpec:
    """Convert one Skeleton IO joint record (version 2) to a JointSpec.

    Skeleton IO nests transform/orient/radius under jd['attrs'] and stores
    rotateOrder as an int enum (0=xyz..5=zyx); we unpack that shape here.

    World translate is extracted from jd['worldMatrix'] (16-float row-major,
    last row = translation in indices 12-14). Used by create_guides to spawn
    locators at the correct world locations — the local translate alone
    doesn't account for parent rotations + joint_orient through the chain.
    """
    name = jd.get('name', '')
    short = name.split('|')[-1].split(':')[-1]

    parent = jd.get('parent') or None
    if parent:
        parent = parent.split('|')[-1].split(':')[-1]

    attrs = jd.get('attrs', {}) or {}

    translate    = list(attrs.get('translate',   [0.0, 0.0, 0.0]))
    rotate       = list(attrs.get('rotate',      [0.0, 0.0, 0.0]))
    joint_orient = list(attrs.get('jointOrient', [0.0, 0.0, 0.0]))
    radius       = float(attrs.get('radius', jd.get('radius', 1.0)))

    ro_raw = attrs.get('rotateOrder', 0)
    if isinstance(ro_raw, int):
        rotate_order = _ROTATE_ORDER_LOOKUP[ro_raw] if 0 <= ro_raw < len(_ROTATE_ORDER_LOOKUP) else 'xyz'
    else:
        rotate_order = str(ro_raw or 'xyz').lower()

    # Skeleton IO writes a 16-float row-major worldMatrix; last row holds
    # translation. Defaults to [0,0,0] if missing (older Skeleton IO exports).
    wm = jd.get('worldMatrix') or []
    if len(wm) >= 15:
        world_translate = [float(wm[12]), float(wm[13]), float(wm[14])]
    else:
        world_translate = [0.0, 0.0, 0.0]

    return JointSpec(
        name=short,
        parent=parent,
        translate=translate,
        rotate=rotate,
        joint_orient=joint_orient,
        rotate_order=rotate_order,
        radius=radius,
        world_translate=world_translate,
    )
