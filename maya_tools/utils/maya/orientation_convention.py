# maya_tools/utils/maya/orientation_convention.py
# Studio orientation convention — the single source of truth for aim/up
# axes across the Armature, aimers, mirror systems, and export.
#
# House convention (the free-tool default, UE-ready):
#   * +X aims down the bone.
#   * +Y is the up axis.
#   * Twist = rotation about the aim axis.
#   * 'xyz' rotate order (aim axis first).
#   * A mirror REVERSES the aim on the far side (-X down the mirrored
#     bone), so both sides animate symmetrically from the same local
#     rotate values. Maya's `mirrorJoint -mirrorBehavior 1`.
#   * Export contract: rotate carries orientation; jointOrient = 0;
#     rotateAxis = 0.
#
# This module owns the mirror convention outright: the plane, the
# negated axis, and the aimer flip. Nothing else may hardcode them
# (2026-07-19 — the same flip literal had drifted into three call sites
# and the same bug had to be fixed in all three).
#
# Named mirror conventions (t48, 2026-07-19):
#   'unreal'   — Mirrored Behavior: the far side aims -X down the bone,
#                both sides animate symmetrically from identical local
#                rotate values (`mirrorJoint -mirrorBehavior 1`).
#   'standard' — Mirrored Orientation: +X down the bone on BOTH sides,
#                +Y matching (Adrian's ruling); the common convention
#                outside Unreal.
# The general mirror maps below are MEASURED, not derived — probes 4-7
# (MrMiata workspace/2026-07-19_mirror-limb-aimer-flip/, raw outputs
# kept). Three separate derivation attempts were each refuted by
# measurement; treat these tables as measurement-only territory.
#
# Per-rig override:
#   A rig may carry a JSON override on the Fabricator registry node
#   (attr `fab_orient_convention`), e.g. {"convention": "standard"}.
#   The stamp is the truth for that rig forever (portability); the
#   project config only sets the default stamped onto NEW rigs.
#   Non-X aim axes remain engagement work. Code paths must query this
#   module rather than hardcoding axes or flips.
__author__ = "Adrian Melian"

import json
from dataclasses import dataclass

import maya.cmds as cmds

# ─────────────────────────────────────────────
# Axis primitives
# ─────────────────────────────────────────────

_AXIS_VECTORS = {
    '+x': (1.0, 0.0, 0.0), '-x': (-1.0, 0.0, 0.0),
    '+y': (0.0, 1.0, 0.0), '-y': (0.0, -1.0, 0.0),
    '+z': (0.0, 0.0, 1.0), '-z': (0.0, 0.0, -1.0),
}

# Aim axis letter → rotate order with the aim (twist) axis first.
# Cyclic pairings keep handedness consistent (research-verified rule:
# the aim axis must lead the rotate order or animators fight gimbal).
_ROTATE_ORDER_FOR_AIM = {'x': 'xyz', 'y': 'yzx', 'z': 'zxy'}

_ROTATE_ORDER_INDEX = {'xyz': 0, 'yzx': 1, 'zxy': 2,
                       'xzy': 3, 'yxz': 4, 'zyx': 5}


def axis_vector(axis: str) -> tuple:
    """'+x' → (1,0,0). Raises KeyError on junk input."""
    return _AXIS_VECTORS[axis.lower()]


def axis_letter(axis: str) -> str:
    """'+x' → 'x'."""
    return axis.lower().lstrip('+-')


# ─────────────────────────────────────────────
# Convention
# ─────────────────────────────────────────────

_CONVENTION_NAMES = ('unreal', 'standard')

# The measured general mirror maps, per (name, frame_reverses), as
# per-channel affine terms (scale, offset): dst = scale * src + offset.
# Probe 6 solved them in the production lifecycle; probe 7 confirmed
# them AS CHANNEL FORMULAS at two offset sets including a mid-chain
# authored offset (every row: frame err 0.000000, invariant exact).
#
#   unreal   reversing : (rx - 180, -ry, 180 - rz)
#   unreal   Local/World: verbatim
#   standard reversing : (180 - rx, ry, -rz)
#   standard Local/World: (-rx, -ry, rz + 180)
#
# The unreal-reversing map REPLACES the shipped (rx, -ry + 180, -rz),
# which is rotation-identical at ZERO offset (every case validated
# before 2026-07-19) but measurably wrong for any authored offset —
# baked mirrored locals violated the equal-locals invariant (FS #18,
# frame err 0.67 at offset (10,20,30)). At zero offset the new map
# writes (-180, 0, 180) where the old wrote (0, 180, 0): different
# channel values, same rotation, identical baked joints.
_MIRROR_TERMS = {
    ('unreal',   True):  ((1.0, -180.0), (-1.0, 0.0),  (-1.0, 180.0)),
    ('unreal',   False): ((1.0, 0.0),    (1.0, 0.0),   (1.0, 0.0)),
    ('standard', True):  ((-1.0, 180.0), (1.0, 0.0),   (-1.0, 0.0)),
    ('standard', False): ((-1.0, 0.0),   (-1.0, 0.0),  (1.0, 180.0)),
}


@dataclass(frozen=True)
class Convention:
    """Immutable orientation convention. Query, never hardcode."""
    aim_axis: str = '+x'   # points down the bone
    up_axis: str = '+y'    # secondary axis

    # Named mirror convention:
    #
    # 'unreal' (house / UE) — Mirrored Behavior: the mirrored side aims
    # -X down the bone, and both sides animate symmetrically from the
    # same local rotate values. `cmds.mirrorJoint -mirrorBehavior 1`.
    #
    # 'standard' — Mirrored Orientation: +X runs down the bone on BOTH
    # sides and +Y points the same way (Adrian's ruling 2026-07-19), at
    # the cost of symmetric local-rotation behavior. Maps measured by
    # probes 4-7; the mirrored-locals invariant is (-rx, -ry, +rz) of
    # source on every non-root joint.
    name: str = 'unreal'

    def __post_init__(self):
        if self.aim_axis.lower() not in _AXIS_VECTORS:
            raise ValueError(f'bad aim_axis {self.aim_axis!r}')
        if self.up_axis.lower() not in _AXIS_VECTORS:
            raise ValueError(f'bad up_axis {self.up_axis!r}')
        if axis_letter(self.aim_axis) == axis_letter(self.up_axis):
            raise ValueError(
                f'aim and up axes must differ: {self.aim_axis!r} / '
                f'{self.up_axis!r}')
        if self.name not in _CONVENTION_NAMES:
            raise ValueError(
                f'bad convention name {self.name!r}: expected one of '
                f'{_CONVENTION_NAMES}')

    # -- identity ------------------------------------------------
    @property
    def mirror_flips_aim(self) -> bool:
        """Does a mirror REVERSE the aim on the far side? Derived from
        the name (retired as a stored field 2026-07-19)."""
        return self.name == 'unreal'

    # -- vectors -------------------------------------------------
    @property
    def aim_vector(self) -> tuple:
        return axis_vector(self.aim_axis)

    @property
    def up_vector(self) -> tuple:
        return axis_vector(self.up_axis)

    # -- twist ---------------------------------------------------
    @property
    def twist_axis(self) -> str:
        """Letter of the twist axis (== aim axis). Rotation about this
        axis on an aimer is 'twist' and keeps a pure aim-at-child; the
        other two rotation axes are aim-breaking offsets."""
        return axis_letter(self.aim_axis)

    @property
    def offset_axes(self) -> tuple:
        """The two rotation axes that break a pure aim when non-zero."""
        return tuple(a for a in 'xyz' if a != self.twist_axis)

    # -- channels ------------------------------------------------
    @property
    def stretch_channel(self) -> str:
        """Translate channel that carries bone length ('tx' in house)."""
        return 't' + axis_letter(self.aim_axis)

    @property
    def rotate_order(self) -> str:
        return _ROTATE_ORDER_FOR_AIM[axis_letter(self.aim_axis)]

    @property
    def rotate_order_index(self) -> int:
        return _ROTATE_ORDER_INDEX[self.rotate_order]

    # -- mirror --------------------------------------------------
    @property
    def mirror_plane(self) -> str:
        """The plane a mirror reflects across. A character's symmetry
        plane, not an engine axis, so this is YZ everywhere in v1.
        (Moved here from side_tokens 2026-07-19 — the mirror convention
        gets one home.)"""
        return 'YZ'

    @property
    def mirror_axis(self) -> str:
        """The axis negated by mirror_plane."""
        return 'x'

    def mirror_channel_terms(self, frame_reverses: bool = True) -> tuple:
        """Per-channel affine terms of the mirror map: ((sx, ox),
        (sy, oy), (sz, oz)) with dst = scale * src + offset per
        channel. This is the DG-wirable form armature_mirror's live
        Symmetry network consumes (scalar mult + add nodes); probe 7
        proved the maps hold as channel formulas, so scalar wiring is
        sufficient.

        frame_reverses: True for a child- or Parent-aimed offset node
        (frame flips with the skeleton), False for Local / World.
        Non-X aim conventions raise — engagement-validated work.
        """
        if self.twist_axis != 'x':
            raise NotImplementedError(
                f'mirror terms for aim axis {self.aim_axis!r} are '
                f'engagement-validated work; house convention is +x.')
        return _MIRROR_TERMS[(self.name, bool(frame_reverses))]

    def mirror_frame(self, axes) -> list:
        """The convention's WORLD-frame mirror map: given a frame's
        world axes ((X), (Y), (Z)), return the axes the mirrored
        counterpart must land. R(v) = YZ-plane reflection (-vx, vy, vz).

          unreal   : (-R(X), -R(Y), -R(Z)) — the behavior frame; equal
                     locals chain-wide (probe 4/6).
          standard : ( R(X),  R(Y), -R(Z)) — +X down both bones, +Y
                     matching, Z flipped to stay right-handed.

        Frame-EXACT regardless of the counterpart's current state —
        unlike the channel maps, which assume the destination aim frame
        sits in the corresponding lifecycle state (probe 5's lesson).
        Used by cross-convention conversion (template load), where the
        destination joints were baked under a DIFFERENT convention and
        the channel maps' assumption does not hold.
        """
        def _r(v):
            return (-v[0], v[1], v[2])
        x, y, z = axes
        if self.name == 'standard':
            return [_r(x), _r(y), tuple(-c for c in _r(z))]
        return [tuple(-c for c in _r(x)), tuple(-c for c in _r(y)),
                tuple(-c for c in _r(z))]

    def mirror_aimer_rotation(self, rx: float, ry: float, rz: float,
                              frame_reverses: bool = True) -> tuple:
        """Mirror an aimer's local rotation across the mirror plane.

        frame_reverses: True for an aimer whose frame flips with the
        skeleton — a child-aimed or Parent-aimed offset node, which
        points down the bone and therefore reverses on the far side.
        False for Local (whose frame IS the joint, already
        behavior-mirrored) and World (a fixed world-identity frame).
        Forcing a reversing flip onto a Local frame does not even stay
        in the channel: it lands in the joint at the next orientation
        bake and un-flips every childless tip (2026-07-19 — the Mirror
        Limb tip bug, c2ba923).

        The maps are the MEASURED general maps in _MIRROR_TERMS
        (probes 6-7; the unreal-reversing map is the FS #18 fix —
        rotation-identical to the old (rx, -ry+180, -rz) at zero
        offset, correct where the old map was not for authored
        offsets). Returns UNWRAPPED values; callers that want a
        canonical range wrap the result themselves (the existing sites
        disagree on this and Stage 1 preserves both). Non-X aim
        conventions raise rather than silently producing wrong flips.
        """
        terms = self.mirror_channel_terms(frame_reverses)
        return tuple(s * v + o
                     for (s, o), v in zip(terms, (rx, ry, rz)))


HOUSE = Convention()

# Registry attr that carries the per-rig convention stamp (JSON, e.g.
# {"convention": "standard"}; may also carry engagement-tier axis
# overrides {"aim_axis": "+z", ...}). Stamped at rig creation from the
# project config default (t48 Phase C); the stamp is the rig's truth
# forever — a rig mirrors the same way on every machine regardless of
# local project settings.
_OVERRIDE_ATTR = 'fab_orient_convention'
CONVENTION_ATTR = _OVERRIDE_ATTR


def stamp_registry(registry_node: str, name: str) -> None:
    """Write the convention stamp onto a Fabricator registry node.

    name: 'unreal' | 'standard' (validated — a typo must fail loud at
    stamp time, not fall back silently at resolve time). Creates the
    string attr when absent. Stamping is EXPLICIT even for the house
    convention: a stamped rig stays self-describing if the house
    default ever changes.
    """
    if name not in _CONVENTION_NAMES:
        raise ValueError(
            f'bad convention name {name!r}: expected one of '
            f'{_CONVENTION_NAMES}')
    if not cmds.objExists(registry_node):
        raise RuntimeError(
            f'stamp_registry: no such node {registry_node!r}')
    if not cmds.attributeQuery(_OVERRIDE_ATTR, node=registry_node,
                               exists=True):
        cmds.addAttr(registry_node, longName=_OVERRIDE_ATTR,
                     dataType='string')
    # Merge, never clobber: the attr may also carry engagement-tier
    # axis overrides; only the convention key is ours to write.
    raw = cmds.getAttr(f'{registry_node}.{_OVERRIDE_ATTR}') or ''
    try:
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
    except ValueError:
        data = {}
    data['convention'] = name
    cmds.setAttr(f'{registry_node}.{_OVERRIDE_ATTR}',
                 json.dumps(data), type='string')


# ─────────────────────────────────────────────
# Resolution
# ─────────────────────────────────────────────

def from_json(raw: str) -> Convention:
    """Build a Convention from an override JSON string. Unknown keys
    are ignored; missing keys fall back to house values.

    The named key is 'convention' ('unreal' | 'standard'). The retired
    boolean 'mirror_flips_aim' is honored as a legacy spelling when no
    name is present (False meant +X down both bones, i.e. standard) —
    it was never written by any UI, but honoring it is free."""
    data = json.loads(raw)
    name = data.get('convention')
    if name is None:
        legacy = data.get('mirror_flips_aim')
        name = ('standard' if legacy is False else HOUSE.name)
    return Convention(
        aim_axis=data.get('aim_axis', HOUSE.aim_axis),
        up_axis=data.get('up_axis', HOUSE.up_axis),
        name=name,
    )


def resolve(registry_node: str = None) -> Convention:
    """Return the active convention.

    With a registry node given (Fabricator's fab_registry), an
    override JSON on `fab_orient_convention` wins; anything absent,
    empty, or malformed falls back to HOUSE with a warning (a broken
    override must never silently change a rig's axes).
    """
    if not registry_node or not cmds.objExists(registry_node):
        return HOUSE
    if not cmds.attributeQuery(_OVERRIDE_ATTR, node=registry_node,
                               exists=True):
        return HOUSE
    raw = cmds.getAttr(f'{registry_node}.{_OVERRIDE_ATTR}') or ''
    if not raw.strip():
        return HOUSE
    try:
        return from_json(raw)
    except Exception as exc:
        cmds.warning(
            f'orientation_convention: bad override on {registry_node} '
            f'({exc}); using house convention.')
        return HOUSE
