# maya_tools/animation/anim_library/address.py
"""Re-export pose_library.address so anim_library shares cross-rig identity.

Both libraries use the same Fabricator address tuples — a clip and a
pose are identified by the same (component_type, side, role, ctrl_role,
joint_index, component_id) shape. If we ever fix a bug in
address_to_ctrl, both libraries benefit automatically.
"""
from maya_tools.animation.pose_library.address import (  # noqa: F401
    Address,
    address_to_ctrl,
    ctrl_to_address,
)

__all__ = ('Address', 'address_to_ctrl', 'ctrl_to_address')
