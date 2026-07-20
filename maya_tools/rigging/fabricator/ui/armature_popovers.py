# maya_tools/rigging/fabricator/ui/armature_popovers.py
"""Right-click menu rows for the Fabricator skeleton toolbar (Adrian,
2026-07-19: the hover popover panels traded for plain right-click menus,
matching the Bridge strip's MenuButton grammar).

Each factory returns [(label, tooltip, handler), ...] rows wired to the
SkeletonHelpersBar's signals — the bar stays the single signal source
and FSWindow stays the single handler home. Factories are called FRESH
on every open by MenuButton, so rows can never go stale.
"""
__author__ = "Adrian Melian"


def joints_rows(bar):
    return [
        ('Add Joint',
         'Child of the last-selected joint (presses chain); free '
         'joint at origin when nothing is selected.',
         bar.add_joint_requested.emit),
        ('Insert Joints Between',
         'Select 2 joints (one a descendant of the other), then '
         'insert N joints evenly between them.',
         bar.insert_between_requested.emit),
        ('Build Engine IK',
         'Create the UE5 engine-reference joints (ik_foot_*, '
         'ik_hand_*, center_of_mass, interaction), snapped to '
         'their counterparts with FollowJoint components. Run at '
         'the end of a skeleton build; re-run to re-snap.',
         bar.build_engine_ik_requested.emit),
    ]


def aimers_rows(bar):
    return [
        ('Aim Joints at Aimers',
         'Bake every joint\'s orientation to its aimer, then '
         'rebuild the Armature (aimers restored).',
         bar.aim_joints_requested.emit),
        ('Mirror Selected Aimers',
         'Copy the selected aimer(s) onto their opposite-side '
         'counterparts (name flip via side tokens). One-shot, and '
         'it does NOT re-orient the joints — run Aim Joints at '
         'Aimers after, or build.',
         bar.aimers_mirror_selected_requested.emit),
        ('Rebuild All Aimers',
         'Recreate every aimer with its state preserved — fixes '
         'stale child enums after topology edits.',
         bar.aimers_rebuild_all_requested.emit),
        ('Reset All Aimers',
         'Delete every aimer and re-seed from geometric detection '
         '(authored Local/World targets and twists are lost — '
         'confirms first).',
         bar.aimers_reset_all_requested.emit),
        None,
        ('Show / Hide Aimers',
         'Toggle the _aimers display layer.',
         bar.aimers_visibility_toggle_requested.emit),
    ]


def mirror_rows(bar):
    return [
        ('Mirror Limb',
         'Smart mirror the whole selected branch to the other '
         'side: joints missing there → joints + every module down '
         'the chain; joints already there → modules only.',
         bar.mirror_limb_requested.emit),
        ('Mirror Joints',
         'Mirror only the selected branch\'s joints to the other '
         'side (one-shot, name flip via side tokens).',
         bar.mirror_joints_requested.emit),
        ('Mirror Module',
         'Mirror ONLY the module on the selected joint (e.g. the '
         'clavicle FK without the shoulder IK below it). Far-side '
         'joints must already exist.',
         bar.mirror_module_requested.emit),
    ]


def duplicate_rows(bar):
    return [
        ('Duplicate Limb',
         'Duplicate the selected armature branch (joints + '
         'modules) with a smart region rename of the WHOLE '
         'subtree — no colliding names.',
         bar.duplicate_limb_requested.emit),
        ('Duplicate Joints',
         'Duplicate only the branch\'s joints, same smart region '
         'rename.',
         bar.duplicate_joints_requested.emit),
    ]
