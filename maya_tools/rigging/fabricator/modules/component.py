# Python/maya_tools/rigging/fabricator/components/component.py
"""Component contract layer — declarative metadata for KS v2 modules.

A Component is a class implementing build/unbuild logic. Its CONTRACT class
attribute is a declarative description of inputs, outputs, options, and
dependencies. The contract is consumed by:
  - Blueprint validation (joint counts, plug graph, option schemas)
  - UI form generation (option_widgets builds widgets per option_schema field)
  - Build order resolution (parent_plug edges form a DAG)
  - Phase-2 mirror table (side_supported, plug names)
  - Phase-3 space switching (provider/consumer plug declarations)
"""
__author__ = "Adrian Melian"

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Plug:
    """A named input or output on a component.

    name: short name (e.g. 'parent_in', 'ctrl_out'). Joined to the component
          id at runtime as '<component_id>.<plug_name>'.
    kind: 'matrix' (a worldMatrix-shaped output that downstream components
          can connect into their parent_in) or 'message' (for object
          identity references like 'this is my world ctrl').
    required: only meaningful on inputs. If True, the blueprint validator
              raises if no parent_plug wires this input.
    description: human-readable, surfaced in UI tooltips.
    """
    name: str
    kind: str  # 'matrix' or 'message'
    required: bool = False
    description: str = ''
    space_target: bool = False    # NEW: matrix output can serve as space provider


@dataclass(frozen=True)
class OptionField:
    """An entry in a contract's options_schema.

    type: 'shape_enum', 'color_enum', 'bool', 'int', 'float', 'string',
          'string_choices' (free-form string with dropdown suggestions
          sourced from a dotted-path function — see suggestions_source),
          'channels' (special structured editor for keyable/locked/hidden lists),
          'xyz_axes' (3-bool master+XYZ compound: stored as [bx, by, bz]).
          'finger_list' is RETIRED (Task 2.3, SPEC 2026-07-09 Limbs +
          Follower Joints §3.4) — RibbonIKArm's old 'fingers' membership
          option this type served now lives on the fab_limb node
          instead (limb_node.py's finger_roots[]/curl_excluded[]); no
          component declares this type anymore, and option_widgets.py no
          longer registers a factory for it (falls back to the generic
          disabled field).
          UI generates a widget per type; validator enforces type on blueprint
          load.
    default: default value if the blueprint omits this option.
    description: human-readable, surfaced in UI tooltips.
    choices: optional list of allowed values for enum-like types.
    range: optional (min, max) clamp for numeric types (int/float). Empty
           tuple means unclamped (uses widget defaults).
    suggestions_source: optional dotted Python path to a zero-arg function
           returning a list[str] of dropdown suggestions for 'string_choices'
           widgets. Resolved at widget-populate time (not at module load), so
           the suggestions live-update as the scene changes. Empty for other
           field types.
    """
    type: str
    default: object  # type matches `type` field
    description: str = ''
    choices: tuple = ()
    range: tuple = ()
    suggestions_source: str = ''


@dataclass(frozen=True)
class JointRole:
    """A named role for one slot in a multi-joint component's joints[] list.

    `joints[i]` corresponds to `joint_roles[i].name`. Single-joint components
    leave joint_roles=() and use joints[0] directly (existing convention).

    descendant_of: another role's name; this joint must be a descendant of
    that role's joint in the blueprint's skeleton hierarchy. '' = no
    constraint (typically the start role).
    """
    name: str
    label: str
    descendant_of: str = ''


@dataclass(frozen=True)
class SpaceConsumer:
    """A control that wants space switching.

    ctrl_role: fab_role tag value of the ctrl to wire ('pv_ctrl',
               'ik_end_ctrl', 'advfk_ctrl', ...).
    attr_name: enum attribute name on the Maya ctrl. 'space' by
               convention.
    defaults: ordered tuple of provider names included as the FIRST
              enum slots, always. Recognized 'magic' names handled by
              the build-time resolver: 'world', 'root', 'auto',
              'parent'. Otherwise a '<id>.<plug>' reference (template
              substitution at build) or a raw provider name registered
              via _collect_space_providers.
    default: provider name to use as the initial enum value.

    Per-instance user additions live on the component network node as
    a `spaces_<ctrl_role>` message-multi of joint connections. Build
    appends them AFTER the defaults in enum order.
    """
    ctrl_role: str
    attr_name: str = 'space'
    defaults: tuple = ()
    default: str = 'world'


@dataclass(frozen=True)
class Action:
    """Marking-menu entry. Handler is a dotted-path string (resolved at
    trigger time) so reload doesn't break references — Contract is frozen
    and storing live function refs would lock them at module-import time.

    label: visible text in the marking menu.
    ctrl_role: fab_role tag value the action applies to (e.g. 'pv_ctrl',
               'master_switch_ctrl'). Marking menu filters by this.
    handler: dotted Python path 'pkg.module.func'. Called as
             handler(component_id, ctrl_name, **kwargs) at trigger time.
    section: optional grouping label for the menu (e.g. 'Match', 'Switch').
    """
    label: str
    ctrl_role: str
    handler: str
    section: str = ''


@dataclass(frozen=True)
class MirrorRule:
    """Per-ctrl-role rule for the L↔R pair mirror swap.

    A pose mirror swaps an L ctrl's TRS values to its R counterpart and
    vice versa. Channels named in `negate` flip sign during the swap;
    the rest copy verbatim. Remaining channels (and any role not
    declared in a contract's mirror_rules) default to no-flip.

    Channels are the 9 standard TRS plug names:
        translateX, translateY, translateZ,
        rotateX, rotateY, rotateZ,
        scaleX, scaleY, scaleZ.

    Why per-role and not per-component-uniform: components like SimpleIK
    have ctrls in different local-frame conventions. fk_ctrls live in
    pre-mirrored joint frames (rotations don't flip on swap, translates
    do), but the ik_end_ctrl on an IKLeg is rebound to a world-axis
    foot frame at build time (translateX/rotateY/rotateZ flip — the
    standard YZ-plane reflection). Each role gets its own rule.

    ctrl_role: fab_role tag value the rule applies to.
    negate:    frozenset of TRS channel names that flip sign.
    """
    ctrl_role: str
    negate: frozenset


@dataclass(frozen=True)
class ExtraGuide:
    """A non-joint guide locator for components that need user-authored
    worldspace positions beyond the skeleton (heel + toe-tip pivots for
    IKLeg, future custom space anchors).

    Spawned during create_guides; positions captured into options as
    Vector3 fields during build_skeleton (option key is f'{name}_position').

    name: short identifier ('heel', 'toe_tip'). Combined with the component
          id at runtime as f'{component_id}_{name}_extra_guide'.
    display_name: Properties-panel field label ('Heel pivot').
    description: tooltip on the Vector3 field.
    shape: Curve-O-Matic library shape name (defaults to 'locator').
    color: Maya colour index used as fallback when side_aware=False or side
           detection fails. Defaults to 17 (yellow centre); see side_tokens.SIDE_COLORS
           for the project palette.
    side_aware: if True, auto-color via side_tokens.detect_side on the component's
                first joint (matches joint guide coloring).
    parent: name of another ExtraGuide in the same contract to DAG-parent this
            guide's locator under (IKLeg's toe_tip rides the heel). Empty = parent
            directly under fab_pivots_grp. The named guide must be declared BEFORE
            this one in extra_guides — spawn order follows declaration order.
    """
    name: str
    display_name: str
    description: str = ''
    shape: str = 'locator'
    color: int = 17
    side_aware: bool = True
    parent: str = ''


@dataclass(frozen=True)
class Contract:
    """Declarative metadata for a Component class.

    Set on Component subclasses as `CONTRACT = Contract(...)`. The contract
    is the source of truth for everything *about* a component — what it
    consumes, what it produces, what options it accepts, what plugs other
    components can wire to it. Build/unbuild logic lives on the class as
    methods; the contract is the data.
    """
    type: str                                                 # e.g. 'World', 'SimpleFK'
    display_name: str                                         # e.g. 'World', 'Simple FK'
    description: str                                          # one-paragraph
    min_joints: int                                           # inclusive
    max_joints: int                                           # inclusive; -1 = unlimited
    parent_strategy: str = 'walk_up'                          # 'walk_up' | 'world' | 'custom'
    inputs: tuple = ()                                        # tuple of Plug
    outputs: tuple = ()                                       # tuple of Plug
    options_schema: dict = field(default_factory=dict)        # {field_name: OptionField}
    side_supported: bool = True                               # can this be mirrored?
    joint_roles: tuple = ()                                   # tuple of JointRole. Empty = single-joint.
    color: str = ''                                           # hex (e.g. '#7AC4FF'); palette swatch + canvas tint. '' = neutral fallback.
    space_consumers: tuple = ()                               # tuple of SpaceConsumer
    actions: tuple = ()                                       # tuple of Action
    extra_guides: tuple = ()                                  # tuple of ExtraGuide
    mirror_rules: tuple = ()                                  # tuple of MirrorRule; per-ctrl-role pair-mirror sign flips
    default_region: str = ''                                  # 'arm' | 'leg' | ...; default region for component type. Instance.region overrides if set.
    # Derived Limbs (spec 2026-07-11): which limb abilities this
    # component type has. Subset of ('fingers', 'twists'). Non-empty =>
    # instances get a fab_limb home, derived (never serialized or
    # hand-edited) at every seam via nodes.derive_limb: 'fingers' runs
    # wrist finger discovery + the metacarpal curl heuristic; 'twists'
    # runs convention twist adoption and shows the Twist dial. Empty =>
    # no limb node, no limb UI (a spine is not a limb).
    limb_features: tuple = ()                                 # subset of ('fingers', 'twists')


class Component(ABC):
    """Abstract base class for all KS v2 components.

    Subclasses set CONTRACT as a class attribute (instance of Contract above)
    and implement build/unbuild as classmethods. Components are stateless;
    per-instance data (joint references, options, persisted state) lives on
    the ComponentInstance dataclass and the corresponding network node.
    """

    CONTRACT: Contract = None  # subclass MUST override
    # (The old creates_implicit_limb class bool is retired — Derived
    # Limbs spec 2026-07-11. Limb opt-in is CONTRACT.limb_features.)

    @classmethod
    @abstractmethod
    def build(cls, instance, context) -> None:
        """Build the rig nodes for this component instance.

        Args:
            instance: a ComponentInstance from the loaded blueprint.
            context: a BuildContext (see ks/plugs.py); call
                     context.resolve_plug(instance.parent_plug) to get the
                     actual Maya plug name to wire into.
        """

    @classmethod
    @abstractmethod
    def unbuild(cls, instance) -> dict:
        """Capture this instance's runtime state from the live scene.

        Returns a dict matching the 'persisted' block schema:
            {
                'starting_shape': str,
                'cv_data': {shape_name: [[x, y, z], ...]},
                'enum_orders': {attr_name: [option1, option2, ...]},
            }
        Caller (build_modules orchestrator) writes this to the instance's
        network node, then deletes rig_grp.
        """

    @classmethod
    def default_id(cls, joints: list) -> str:
        """Default component id when blueprint doesn't specify one.

        Pattern: '<first_joint_short>_<type_snake>'. E.g. SimpleFK on
        'lf_elbow' -> 'lf_elbow_simple_fk'. Subclasses can override.
        """
        if not joints:
            return cls.CONTRACT.type.lower()
        first = joints[0].split('|')[-1].split(':')[-1]
        # SimpleFK -> simple_fk: insert '_' only when a lowercase char is followed
        # by an uppercase char (transitions like 'e'->'F'), keeping acronym runs
        # like 'FK' together as one lowercase token.
        snake = ''
        t = cls.CONTRACT.type
        for i, ch in enumerate(t):
            if i > 0 and ch.isupper() and t[i - 1].islower():
                snake += '_'
            snake += ch.lower()
        return f'{first}_{snake}'

    @classmethod
    def can_apply(cls, joints: list, blueprint) -> tuple:
        """Pre-add validation. Return (ok: bool, reason: str).

        Default checks joint count against contract bounds. Subclasses
        override for component-specific checks (e.g. SimpleIK requires
        a 3-joint chain reachable from the start joint).

        Called by:
        - PalettePanel for enable/disable + tooltip ('Disabled — <reason>').
        - _add_component (UI handler) before mutating the blueprint.
        - validate_blueprint as a final gate before Build Rig.
        """
        c = cls.CONTRACT
        n = len(joints)
        if n < c.min_joints:
            return False, f'Requires at least {c.min_joints} joint(s); selected {n}.'
        if c.max_joints != -1 and n > c.max_joints:
            return False, f'Requires at most {c.max_joints} joint(s); selected {n}.'
        return True, ''

    @classmethod
    def default_options_for_create(cls, joints: list, blueprint) -> dict:
        """Extra default option values to seed a freshly-ADDED instance,
        beyond each OptionField's own static `default` (e.g. RibbonIKArm's
        auto-discovered `fingers` membership — PLAN.md 2026-07-08 Task
        4.2: "Hook discovery into the create/add path so the option is
        populated when the RibbonIKArm is placed").

        Called ONCE, only at ADD time (fs_app._default_options_for_create,
        invoked from the UI's component-add path) — NEVER at template
        load, where persisted options already reflect prior edits/
        rebuilds and must not be silently re-derived.

        Default: no extra options. Subclasses override for create-time
        auto-population. Return value is merged into the options dict
        the UI is about to pass to nodes.create_component_node — merge
        semantics (last-write-wins on key collision) are the caller's
        responsibility, not this hook's.

        Args:
            joints: the RESOLVED joints[] this instance is about to be
                    created with (post _resolve_initial_joints — role
                    order, e.g. [shoulder, elbow, wrist] for RibbonIKArm).
            blueprint: a transient scene snapshot (same object
                       _resolve_initial_joints already receives) — read
                       blueprint.skeleton_joints for descendant-chain
                       walks. May be None (empty/no-registry scene);
                       implementations must handle that without raising.

        Callers must treat a raising implementation as "no extra
        options" (catch + degrade) — a discovery hook must never block
        a component add.
        """
        return {}

    @classmethod
    def on_added(cls, node: str) -> None:
        """Optional lifecycle hook: scene-mutation side effects AFTER a
        component's network node is fully wired (joints[] connected,
        registry-linked) — called once by nodes.create_component_node,
        right before it returns `node`.

        Unlike default_options_for_create (which only seeds OPTION
        VALUES before the node exists), this hook can read/write other
        scene state through `node` itself (nodes.get_component_joints,
        nodes.get_component_options, etc.) — e.g. FollowJointComponent
        seeding a linked follow rule on its own joint (SPEC 2026-07-09
        Limbs + Follower Joints §3.1, rule source (c)).

        Default: no-op. nodes.py wraps every call in try/except — a
        failing hook must never block a component add.
        """
        pass

    @classmethod
    def on_removed(cls, node: str) -> None:
        """Optional lifecycle hook: scene-mutation cleanup called right
        BEFORE `node` is deleted (nodes.delete_component_node) — the
        node and its connections (joints[], options, etc.) are still
        fully readable at call time.

        Default: no-op. nodes.py wraps every call in try/except — a
        failing hook must never block a component removal.
        """
        pass

    @classmethod
    def on_options_changed(cls, node: str, old_options: dict,
                           new_options: dict) -> None:
        """Optional lifecycle hook: called after nodes.set_component_options
        writes `new_options` to `node`'s options_json (the write has
        already happened by the time this runs).

        Default: no-op. nodes.py wraps every call in try/except — a
        failing hook must never block an option edit.
        """
        pass

    @classmethod
    def resolve_extra_guide_default(cls, guide_name: str,
                                     joints: list, blueprint) -> tuple:
        """Default worldspace position for one of this component's extra
        guides, derived from the joint chain + blueprint.

        Subclasses with non-empty CONTRACT.extra_guides MUST override this
        for each declared guide_name.

        Args:
            guide_name: matches an ExtraGuide.name in CONTRACT.extra_guides.
            joints: the component instance's joints[] (list of joint name strings).
            blueprint: the loaded Blueprint object — read joint world positions
                       from blueprint.skeleton_joints[i].world_translate
                       (joints aren't built yet at create_guides time).

        Returns: (x, y, z) worldspace float tuple.
        """
        raise NotImplementedError(
            f'{cls.__name__} declares extra_guides but does not implement '
            f'resolve_extra_guide_default for guide_name={guide_name!r}.'
        )

    @classmethod
    def output_for_owned_joint(cls, joint_index: int, joint_count: int) -> str | None:
        """Best parent-space output plug name for a child hanging off this
        component's joint at `joint_index` (0-based position within this
        component's joints[] list of length `joint_count`).

        Used by the UI's add-time auto-wire logic (fs_window's
        _auto_wire_parent_plug) to pick the output that actually sits at
        the parent joint's position, instead of blindly defaulting to
        CONTRACT.outputs[0] -- a child hung off an INTERIOR joint of a
        multi-joint chain (e.g. a clavicle on a RibbonSpine's spine04)
        needs THAT joint's own space output, not the chain start.

        Preference order:
          - interior joint (0 < joint_index < joint_count - 1):
                f'joint_out_{joint_index}' if declared as a space_target
                output on CONTRACT; else None.
          - first joint (joint_index == 0):
                'start_out' if declared; else f'joint_out_0' if declared;
                else None.
          - last joint (joint_index == joint_count - 1):
                'tip_out' if declared; else 'end_out' if declared; else
                f'joint_out_{joint_index}' if declared; else None.
          - anything else (out of range): None.

        Returns None when this component's contract declares no matching
        positional output -- callers should fall back to their own
        default (e.g. CONTRACT.outputs[0]).
        """
        def _declared(name: str) -> bool:
            return any(o.name == name and o.space_target
                       for o in cls.CONTRACT.outputs)

        if 0 < joint_index < joint_count - 1:
            candidate = f'joint_out_{joint_index}'
            return candidate if _declared(candidate) else None
        elif joint_index == 0:
            if _declared('start_out'):
                return 'start_out'
            candidate = 'joint_out_0'
            return candidate if _declared(candidate) else None
        elif joint_index == joint_count - 1:
            if _declared('tip_out'):
                return 'tip_out'
            if _declared('end_out'):
                return 'end_out'
            candidate = f'joint_out_{joint_index}'
            return candidate if _declared(candidate) else None
        else:
            return None


@dataclass
class ComponentInstance:
    """A single component entry from a blueprint, in-memory representation.

    Mirrors the YAML 'components[]' record. Mutable so the UI can edit it.

    role / region: cross-rig-portable identifiers for the pose library
    (Spec 5). See ComponentSpec for full description. Both default
    to empty string; pose library uses (type, side, role) and
    (type, side, region) as cross-rig identity tuples.
    """
    id: str
    type: str
    joints: list                                  # list[str]
    parent_plug: str = ''                         # e.g. 'world.ctrl_out'; '' for root
    side: str = 'md'                              # 'md' | 'lf' | 'rt'
    role: str = ''                                # cross-rig semantic role
    region: str = ''                              # anatomical region tag
    options: dict = field(default_factory=dict)
    persisted: dict = field(default_factory=dict)
