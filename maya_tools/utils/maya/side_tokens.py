"""Cross-tool side-token detection and flipping.

Shared by Fabricator (guide-coloring, component naming, module mirror),
smart joint mirror (shelf utility), and future Pose Studio Pass B
(paste-mirrored). Single source of truth — owned by no single tool, per
the Cross-Tool Conventions in CLAUDE.md.
"""
__author__ = "Adrian Melian"

import re


_LEFT_TOKENS  = frozenset({'l', 'lf', 'lt', 'left'})
_RIGHT_TOKENS = frozenset({'r', 'rt', 'right'})

# Canonical flip map. When flipping, prefer the canonical opposite.
# Asymmetric: 'lt' has only 'rt' as the right-side candidate.
_FLIP_MAP = {
    'l': 'r',     'r': 'l',
    'L': 'R',     'R': 'L',
    'lf': 'rt',   'rt': 'lf',
    'Lf': 'Rt',   'Rt': 'Lf',
    'LF': 'RT',   'RT': 'LF',
    'lt': 'rt',
    'Lt': 'Rt',
    'LT': 'RT',
    'left': 'right',   'right': 'left',
    'Left': 'Right',   'Right': 'Left',
    'LEFT': 'RIGHT',   'RIGHT': 'LEFT',
}

# Maya color-index convention (project-wide).
SIDE_COLORS = {
    'lf': 6,    # blue
    'rt': 13,   # red
    'md': 17,   # yellow
}

# Ctrl color palette (RGB floats, 0..1). Single source of truth for the
# named colors that show up in the Properties ctrl_color enum and in
# build-time joint coloring. Add a new color here once and every site
# that uses it picks it up.
#
# Component color language (Adrian, 2026-07-12). Colors encode the
# component FAMILY, never the side:
#   FK      = blues   (SimpleFK ice_blue lightest -> AdvancedFK sky_blue
#                      -> FKAim blue)
#   IK      = oranges (SimpleIK peach -> IKArm amber -> IKLeg orange ->
#                      IKQuad rust)
#   Ribbons = greens  (RibbonIKArm mint -> RibbonIKLeg green ->
#                      RibbonSpine forest)
#   World   = yellow
# The remaining entries (pink, red, white, cyan) carry no family meaning
# and stay as free picks in the Properties ctrl_color enum.
CTRL_COLOR_RGB = {
    'ice_blue': (0.78, 0.85, 0.95),
    'sky_blue': (0.45, 0.65, 1.0),
    'blue':     (0.15, 0.35, 1.0),
    'peach':    (1.0,  0.85, 0.6),
    'amber':    (1.0,  0.72, 0.3),
    'orange':   (1.0,  0.5,  0.0),
    'rust':     (0.85, 0.33, 0.05),
    'mint':     (0.5,  0.9,  0.6),
    'green':    (0.1,  0.8,  0.1),
    'forest':   (0.0,  0.55, 0.2),
    'yellow':   (1.0,  0.9,  0.0),
    'pink':     (1.0,  0.4,  0.7),
    'red':      (1.0,  0.1,  0.1),
    'white':    (1.0,  1.0,  1.0),
    'cyan':     (0.0,  0.9,  1.0),
}

# Closest match in Maya's 0..31 override-color index palette. Used as a
# fallback alongside the RGB override on joints — some viewport draw
# paths use the index even when overrideRGBColors is True.
CTRL_COLOR_INDEX = {
    'ice_blue': 16,
    'sky_blue': 18,
    'blue':      6,
    'peach':    21,
    'amber':    21,
    'orange':   12,
    'rust':      4,
    'mint':     19,
    'green':    14,
    'forest':    7,
    'yellow':   17,
    'pink':     20,
    'red':      13,
    'white':    16,
    'cyan':     18,
}

# Mirror plane / axis MOVED 2026-07-19 to
# utils.maya.orientation_convention (Convention.mirror_plane /
# .mirror_axis), which owns the mirror convention outright. They were
# declared here and never read; this module stays naming-only.


def detect_side(name: str) -> str:
    """Return 'lf' / 'rt' / 'md' for a name. Center is default.

    Splits on '_', ':', and '|' to support Manny's _l/_r suffix, classic
    lf_/rt_ prefix, _left_/_right_ tokens, and namespaced names.
    """
    for tok in re.split(r'[_:|]', name):
        t = tok.lower()
        if t in _LEFT_TOKENS:
            return 'lf'
        if t in _RIGHT_TOKENS:
            return 'rt'
    return 'md'


def flip_side_token(name: str) -> str | None:
    """Return the name with its first side token flipped to the opposite
    side. Returns None if no side token is found (caller decides whether
    that's an error). Preserves the original casing of the matched token.

    Splits on '_', ':', '|' (separators kept in the output). Flips the
    first matching token only — later side tokens stay put. This matches
    the artist intuition that `L_arm_lt_finger` mirrors to `R_arm_lt_finger`
    (only the first L is the side; subsequent 'lt' might be 'left' but
    is more likely a finger-tip or alternate token).
    """
    tokens = re.split(r'([_:|])', name)  # keep separators
    for i, tok in enumerate(tokens):
        if tok in _FLIP_MAP:
            tokens[i] = _FLIP_MAP[tok]
            return ''.join(tokens)
    return None


def flip_all_side_tokens(name: str) -> str | None:
    """Return the name with EVERY side token flipped to its opposite.
    Returns None if no side token is found. Preserves casing.

    For names concatenated from MULTIPLE sided joint names — ribbon
    segment ctrls like 'upperarm_l_lowerarm_l_ribbon_mid_00_ctrl', whose
    opposite is 'upperarm_r_lowerarm_r_ribbon_mid_00_ctrl' because the
    mirror flips every joint name individually. flip_side_token's
    first-token-only rule would leave the trailing tokens on the wrong
    side and the lookup would miss. Use flip_side_token for
    single-entity names where later tokens may be coincidental.
    """
    tokens = re.split(r'([_:|])', name)  # keep separators
    hit = False
    for i, tok in enumerate(tokens):
        if tok in _FLIP_MAP:
            tokens[i] = _FLIP_MAP[tok]
            hit = True
    return ''.join(tokens) if hit else None


def flip_side_color(side: str) -> str:
    """Return the side that's the mirror of `side`. 'lf' <-> 'rt';
    'md' returns 'md'. Used by Fabricator module mirror to flip the
    ctrl_color option.
    """
    return {'lf': 'rt', 'rt': 'lf', 'md': 'md'}.get(side, 'md')


# ---------------------------------------------------------------------------
# Region detection — anatomical-region tag for the pose library + grouping.
# Matches the Component CONTRACT.default_region values: 'arm', 'leg',
# 'spine', 'neck', 'head', 'face'. First match wins; no match returns ''.
# ---------------------------------------------------------------------------

# Token → region map. Tokens are matched case-insensitively against
# underscore/colon/pipe-split segments of the joint name. Order matters
# only for ambiguous tokens (none currently); kept as a flat dict.
_REGION_TOKENS = {
    # arm chain + hand + fingers + shoulder-region accessories
    'arm':       'arm',
    'upperarm':  'arm',
    'lowerarm':  'arm',
    'forearm':   'arm',
    'hand':      'arm',
    'wrist':     'arm',
    'clavicle':  'arm',
    'shoulder':  'arm',
    'pauldron':  'arm',
    'thumb':     'arm',
    'index':     'arm',
    'middle':    'arm',
    'ring':      'arm',
    'pinky':     'arm',
    'finger':    'arm',
    # leg chain + foot
    'leg':       'leg',
    'thigh':     'leg',
    'calf':      'leg',
    'shin':      'leg',
    'knee':      'leg',
    'foot':      'leg',
    'ankle':     'leg',
    'toe':       'leg',
    'ball':      'leg',
    'heel':      'leg',
    # spine column + root
    'spine':     'spine',
    'pelvis':    'spine',
    'cog':       'spine',
    'root':      'spine',
    'chest':     'spine',
    'torso':     'spine',
    # neck
    'neck':      'neck',
    # head + facial
    'head':      'head',
    'jaw':       'head',
    'eye':       'head',
    'mouth':     'head',
    'tongue':    'head',
}


def detect_region(name: str) -> str:
    """Return an anatomical-region tag ('arm' | 'leg' | 'spine' | 'neck' |
    'head') for a joint name, or '' if no known token matches.

    Splits the same way as detect_side ('_', ':', '|'). Also matches
    multi-segment compounds like 'center_of_mass' by checking each
    underscore-split fragment — 'center_of_mass' yields 'spine' because
    of the 'cog' / 'mass' associations are loose, but 'pelvis' tokens
    elsewhere in the chain dominate; just check whole-token match. The
    caller can override the result by setting region explicitly.
    """
    for tok in re.split(r'[_:|]', name):
        match = _REGION_TOKENS.get(tok.lower())
        if match:
            return match
    return ''


# ---------------------------------------------------------------------------
# Side → ctrl color (palette name) — defaults for auto-fill on add.
# Maps the side string to the closest matching name in Fabricator's ctrl
# color palette (world.py:_COLOR_MAP). Used by _add_component to seed the
# new component's options dict so an L joint gets a blue ctrl by default,
# R gets red, center gets yellow — same convention as SIDE_COLORS (which
# uses Maya's index colors) but expressed as palette names.
# ---------------------------------------------------------------------------

_SIDE_TO_CTRL_COLOR = {
    'lf': 'blue',
    'rt': 'red',
    'md': 'yellow',
}


def side_to_ctrl_color(side: str) -> str:
    """Map a side string ('lf' | 'rt' | 'md') to a ctrl_color palette name.
    Returns 'yellow' for unrecognised side, matching the default.
    """
    return _SIDE_TO_CTRL_COLOR.get(side, 'yellow')


def mirror_point(xyz) -> tuple:
    """Apply the YZ-plane mirror to a 3D point. Negates X."""
    return (-xyz[0], xyz[1], xyz[2])
