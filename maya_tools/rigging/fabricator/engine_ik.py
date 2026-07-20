# maya_tools/rigging/fabricator/engine_ik.py
"""Build Engine IK Joints — the UE5 engine-reference joint set as a
one-button action (Adrian, 2026-07-10 design session).

Why an action and not a limb fragment: a fragment stores template-baked
joint positions, so on a custom-proportioned character the IK joints
would land at the TEMPLATE's hand/foot positions and the FollowJoint's
maintain_offset would freeze that wrong offset into the build. UE
expects engine IK bones exactly coincident with their counterparts.
Snapping at creation time reads the live scene instead — correct on any
proportions, idempotent (re-run re-snaps), and the created joints +
FollowJoint components round-trip into saved templates for free
(scene-is-truth).

Run it in Edit Mode at the end of a skeleton build, once every deform
joint is in place. Re-run any time proportions change.

Missing counterparts (non-UE-named or partial skeletons) are NOT
errors: the joint is still created (parked at its parent), the
FollowJoint still records the intended target name, and the report
lists the gap loudly. FollowJoint's build treats a set-but-missing
target as a warned no-op, so Build Rig never crashes on the gap — the
user renames or repicks in Properties later.
"""
__author__ = "Adrian Melian"

import maya.cmds as cmds

from maya_tools.rigging.fabricator import armature, nodes


# Sentinel parent meaning "the registry root joint".
ROOT = '<ROOT>'

# The UE5 Manny/Quinn engine-reference set, creation-ordered so every
# parent exists before its children. (name, parent, follow_target);
# follow_target None = the joint just sits at its parent (engine roots
# and the interaction bone live at the root, unconstrained).
ENGINE_IK_SPEC = (
    ('center_of_mass', ROOT,           'pelvis'),
    ('ik_foot_root',   ROOT,           None),
    ('ik_foot_l',      'ik_foot_root', 'foot_l'),
    ('ik_foot_r',      'ik_foot_root', 'foot_r'),
    ('ik_hand_root',   ROOT,           None),
    ('ik_hand_gun',    'ik_hand_root', 'hand_r'),
    ('ik_hand_l',      'ik_hand_gun',  'hand_l'),
    ('ik_hand_r',      'ik_hand_gun',  'hand_r'),
    ('interaction',    ROOT,           None),
)


def _find_follow_component(joint: str):
    """The FollowJoint component node owning `joint`, or None."""
    for cnode in nodes.get_all_component_nodes():
        if nodes.get_component_type(cnode) != 'FollowJoint':
            continue
        joints = nodes.get_component_joints(cnode) or []
        if joints and joints[0].split('|')[-1] == joint:
            return cnode
    return None


def build_engine_ik_joints(spec=None) -> dict:
    """Create/refresh the engine IK joint set against the live skeleton.

    Per spec entry: create the joint if absent (under its spec parent,
    ROOT resolving to the registry root joint), snap its world pos+rot
    to the counterpart (or to its parent when it has none), and — for
    followed entries — ensure a FollowJoint component targeting the
    counterpart by name. Existing joints are re-snapped, never
    duplicated; existing FollowJoint components are kept as authored.

    Returns a report dict:
      created            [joint, ...]  joints newly created
      snapped            [joint, ...]  joints (re)snapped to a live target
      parked             [joint, ...]  joints placed at their parent
      components_created [id, ...]     FollowJoint components added
      missing_targets    {joint: target}  counterparts absent from the scene
    """
    if not nodes.get_registry():
        raise RuntimeError(
            'No fab_registry in the scene. Load or start a rig first.')
    root = nodes.get_registry_root_joint()
    if not root or not cmds.objExists(root):
        raise RuntimeError(
            'Registry has no root joint — build the skeleton first.')

    report = {
        'created': [], 'snapped': [], 'parked': [],
        'components_created': [], 'missing_targets': {},
    }

    # Engagement suspended for the whole loop: each FollowJoint add
    # would otherwise trigger a FULL armature rebuild (six for this
    # spec) against a scene that is still mid-mutation — wasted work,
    # and the trigger of the fingertip-collapse bug (2026-07-14: finger
    # *_03 joints snapped onto their parent knuckle during the
    # back-to-back rebuilds; headless-repro'd, deterministic). One
    # rebuild after the loop reflects the final state instead.
    with armature.engage_suspended():
        _build_spec_entries(spec, report, root)
    if armature.armature_exists():
        armature.build_armature()

    cmds.select(clear=True)
    return report


def _build_spec_entries(spec, report: dict, root: str) -> None:
    for name, parent_key, target in (spec or ENGINE_IK_SPEC):
        parent = root if parent_key == ROOT else parent_key

        if not cmds.objExists(name):
            cmds.select(clear=True)
            made = cmds.joint(name=name)
            made = cmds.parent(made, parent)[0]
            # cmds.parent under a joint keeps world transform; zero the
            # locals so an unfollowed joint sits exactly ON its parent
            # before any snap below.
            for attr in ('translate', 'rotate', 'jointOrient'):
                cmds.setAttr(f'{name}.{attr}', 0, 0, 0)
            report['created'].append(name)

        if target and cmds.objExists(target):
            cmds.matchTransform(name, target, position=True, rotation=True,
                                scale=False)
            report['snapped'].append(name)
        elif target:
            report['missing_targets'][name] = target
            cmds.warning(
                f'Build Engine IK: counterpart {target!r} for {name!r} '
                f'not found — joint parked at its parent, follow target '
                f'recorded for later.')
        else:
            report['parked'].append(name)

        if target and _find_follow_component(name) is None:
            cid = f'{name}_follow'
            nodes.create_component_node(
                component_id=cid, component_type='FollowJoint',
                joints=[name], parent_plug='', side='md',
                options={'follow_target': target}, persisted={},
            )
            report['components_created'].append(cid)
