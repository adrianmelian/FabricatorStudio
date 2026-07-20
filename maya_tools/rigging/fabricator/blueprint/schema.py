"""Blueprint schema — in-memory representation of a .blueprint.yaml.

The Blueprint dataclass is the single canonical Python object loaded from
disk YAML or constructed by bootstrap. The UI mutates a Blueprint instance
in-place during a session; Save serializes it back to YAML.
"""
__author__ = "Adrian Melian"

from dataclasses import dataclass, field


CURRENT_SCHEMA_VERSION = '1.0'


@dataclass
class JointSpec:
    """One joint in a blueprint's skeleton block.

    Most fields are LOCAL transforms — used to recreate joints in the
    parent chain when no guides exist:
      translate / rotate / joint_orient / rotate_order / radius

    world_translate is the joint's WORLD position at bootstrap time. Used by
    create_guides to spawn locators at the correct world locations (the local
    translate alone won't account for parent rotations + joint_orient through
    the chain). Optional — defaults to [0,0,0] for hand-authored blueprints.
    Bootstrap fills it from Skeleton IO's worldMatrix.

    aim_target / aim_offset persist the joint's aimer state (Armature,
    spec 2026-07-04 §7): aim_target is a child joint name, 'Local',
    'World', or '' (unset — creation default); aim_offset is the aimer
    ctrl's local rotation. Restoring a limb recreates aimers in the
    exact saved state.

    world_rotate (2026-07-14) is the WORLD rotation of a parentless
    (root) joint at capture time. A root captured under a rotated
    non-joint group (a UE import's -90 axis node) records parent=None
    with GROUP-RELATIVE locals — recreating from locals alone silently
    dropped the group's rotation and the template came back sideways.
    None = not captured (legacy blueprint): reload keeps the old
    locals-only behavior.
    """
    name: str
    parent: str = None             # None = world root
    translate: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotate: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    joint_orient: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotate_order: str = 'xyz'
    radius: float = 1.0
    world_translate: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    aim_target: str = ''           # child name | 'Local' | 'World' | '' unset
    aim_offset: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    world_rotate: list = None      # root joints only; None = legacy/not captured


@dataclass
class ComponentSpec:
    """Data form of a component entry from the blueprint YAML.

    Distinct from ComponentInstance (in components/component.py) — this is
    the 'as it sits on disk' form. Build pipeline converts to
    ComponentInstance with id default-derivation applied.

    role / region: optional cross-rig-portable semantic identifiers.
        - role: specific role within the rig ('shoulder', 'spine_03', etc.).
        - region: anatomical region ('arm', 'leg', 'spine', 'head', 'face',
                  'hand', 'foot', '').
        Both are author-set and survive blueprint edits. The pose library
        (Spec 5) uses (component_type, side, role) as the cross-rig identity
        for user-authored sets, and (component_type, side, region) for
        region-derived auto-sets. Empty strings are valid for components
        that don't fit either taxonomy yet — pose library skips them.
    """
    type: str
    joints: list                                  # list[str]
    id: str = None                                # None = auto-derive at load
    parent_plug: str = ''                         # '' for root (World)
    side: str = 'md'                              # 'md' | 'lf' | 'rt'
    role: str = ''                                # cross-rig-portable specific role
    region: str = ''                              # 'arm' | 'leg' | 'spine' | 'head' | ...
    options: dict = field(default_factory=dict)
    persisted: dict = field(default_factory=dict)


@dataclass
class OriginInfo:
    """Optional fork/origin metadata. None for from-scratch and template blueprints."""
    blueprint: str = ''                           # source slug name
    path: str = ''                                # source path at fork time
    version: str = ''                             # source's schema_version at fork time
    cloned_at: str = ''                           # ISO timestamp


@dataclass
class Blueprint:
    """Top-level blueprint object. Mirrors the YAML schema.

    Fields:
      name: human-readable. Used for window titles, top-group naming.
      schema_version: e.g. '1.0'. Migration anchor.
      description: optional, free-form.
      origin: optional OriginInfo if forked from another blueprint.
      skeleton_joints: list[JointSpec] — flat list, parent refs by name.
      components: list[ComponentSpec] — components block.
      wiring: list of explicit cross-component plug connections (Spec 1: empty).
      orient_convention: the mirror convention the skeleton was BAKED
        under at save time ('unreal' | 'standard'; '' = legacy blueprint,
        treated as unreal). Loading under a rig stamped with a different
        convention auto-converts the mirrored-side aimers (t48,
        2026-07-19) — without this field the load cannot know what it is
        converting FROM.
      fabricator_version: the FS build (version.py FABRICATOR_VERSION)
        that WROTE this file. '' = saved before file stamping existed —
        load treats such a file as pre-versioning-era data, so the
        version-gated legacy TYPE map applies to it (the 'IKArm' ->
        'RibbonIKArm' rename). Distinct from schema_version (the YAML
        format's own migration anchor): this field dates the DATA, and is
        the signal fs_app.load() gates the legacy-map window on — without
        it every load read as legacy and a current-era free IKArm
        misloaded as RibbonIKArm (ribbon-arms bug, 2026-07-19).
    """
    name: str
    schema_version: str = CURRENT_SCHEMA_VERSION
    description: str = ''
    origin: OriginInfo = None
    skeleton_joints: list = field(default_factory=list)       # list[JointSpec]
    components: list = field(default_factory=list)            # list[ComponentSpec]
    wiring: list = field(default_factory=list)                # reserved for Spec 3
    orient_convention: str = ''    # '' = legacy, treated as 'unreal'
    fabricator_version: str = ''   # '' = pre-stamping file (legacy data)
