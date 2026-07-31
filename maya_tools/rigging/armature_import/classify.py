"""Turn an Armature blueprint's joint graph into Fabricator component assignments.

WHY THIS CLASSIFIES ON THE JOINT GRAPH AND NOT ON MODULES
---------------------------------------------------------
Two measured facts kill the obvious "one module -> one component" mapping:

1. Module NAMES carry no type. They are auto-suffixed UI labels. Pupper's modules are
   `pivot_l, pivot_l_2, pivot_l_3, hindleg_l, leg_l, ...` and the Squid's are
   `tail, tail_2, tail_2_2, tail_2_2_2, ...`. Anything switching on the name works on
   Samurai and falls apart on every other character. (We already ship one bug of exactly
   this shape: exportActions.ts gates foot-pivot injection on names starting with 'leg',
   so Pupper's `hindleg_l` never gets pivots.)

2. Components do not align to modules anyway. On Samurai the arm chain's `hand_l` lives
   in module `hand_l` while `upperarm_l`/`lowerarm_l` live in module `arm_l`, so a single
   IKArm spans two Armature modules.

So: classify on region + role + chain shape. Modules are consulted for exactly one
thing, locating `foot_pivots` for a foot joint.

THE FALLBACK IS TOTAL. Rule 9 gives every unclassified driver joint a SimpleFK, which is
why the Squid's 30 tentacle joints import green instead of refusing. An importer that
refuses a character because it does not recognise its anatomy is useless for the exact
audience this tool exists for.

No maya.cmds import — this is pure data and tests without a Maya session.
"""
__author__ = "Adrian Melian"

# Blueprint side token -> Fabricator ComponentSpec side.
SIDE_MAP = {'L': 'lf', 'R': 'rt', 'C': 'md'}

# Fabricator ctrl colour by side, matching both shipped biped templates.
SIDE_COLOR = {'lf': 'blue', 'rt': 'red', 'md': 'yellow'}

# Regions FS's ComponentSpec.region understands. Armature's `tail` and `prop` have no
# equivalent and pass as '' rather than inventing a value the pose library cannot use.
FS_REGIONS = {'arm', 'leg', 'spine', 'head', 'face', 'hand', 'foot', 'neck'}

# Component types by tier. Advanced is offered only when all three are registered.
RIBBON_TYPES = ('RibbonSpine', 'RibbonIKArm', 'RibbonIKLeg')

# Exact joint counts the leg contracts declare. Dispatch is exact, not heuristic:
# IKLeg is min_joints=max_joints=4, QuadLeg is 5.
IKLEG_CHAIN = 4
QUADLEG_CHAIN = 5

# RibbonSpine declares min_joints=3; shorter chains stay FK even in Advanced.
RIBBON_SPINE_MIN = 3


class Joint(object):
    """One blueprint joint plus its resolved children, for graph walking."""

    __slots__ = ('name', 'parent', 'region', 'side', 'role', 'aim_target',
                 'module', 'children')

    def __init__(self, record):
        self.name = record.get('name') or ''
        self.parent = record.get('parent') or None
        self.region = record.get('region') or ''
        self.side = record.get('side') or 'C'
        self.role = record.get('role') or 'driver'
        self.aim_target = record.get('aim_target') or ''
        self.module = record.get('module') or None
        self.children = []

    @property
    def is_driver(self):
        return self.role != 'derived'

    def __repr__(self):
        return '<Joint %s %s/%s>' % (self.name, self.region, self.role)


def build_graph(joint_records):
    """Return (by_name, roots) with children resolved, blueprint order preserved."""
    by_name = {}
    order = []
    for rec in joint_records:
        j = Joint(rec)
        if not j.name:
            continue
        by_name[j.name] = j
        order.append(j)

    roots = []
    for j in order:
        if j.parent and j.parent in by_name:
            by_name[j.parent].children.append(j)
        else:
            roots.append(j)
    return by_name, roots


def driver_children(joint):
    """Children that are authored joints, i.e. skipping twists and other derived ones."""
    return [c for c in joint.children if c.is_driver]


def _linear_chain(start, max_len=8):
    """Walk down single-driver-child links from `start`, inclusive.

    Stops at a branch (more than one driver child) or at the end of the chain. Used to
    measure limb length so IKLeg (4) and QuadLeg (5) dispatch exactly.
    """
    chain = [start]
    node = start
    while len(chain) < max_len:
        kids = driver_children(node)
        if len(kids) != 1:
            break
        node = kids[0]
        chain.append(node)
    return chain


def _descendants(joint):
    """Every joint below `joint`, driver and derived alike."""
    out = []
    stack = list(joint.children)
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(node.children)
    return out


def _region_chain(start, regions, max_len=8):
    """Like _linear_chain but only walks while the child is in `regions`."""
    chain = [start]
    node = start
    while len(chain) < max_len:
        kids = [c for c in driver_children(node) if c.region in regions]
        if len(kids) != 1:
            break
        node = kids[0]
        chain.append(node)
    return chain


def fs_region(region):
    return region if region in FS_REGIONS else ''


def component_id(joint_name, comp_type):
    return '%s_%s' % (joint_name, _snake(comp_type))


def _snake(text):
    out = []
    for i, ch in enumerate(text):
        if ch.isupper() and i and not text[i - 1].isupper():
            out.append('_')
        out.append(ch.lower())
    return ''.join(out)


def foot_pivots_for(joint, modules_by_name, scale, axis_convert=None):
    """heel/toe_tip options for a foot joint, in Maya units.

    Blueprint pivots are metres while the stage geometry is centimetres, so `scale`
    (x100 on every current export) is mandatory, not cosmetic. inner_bank/outer_bank are
    read and DROPPED: IKLeg's options_schema has no bank fields — bank is rotateX on the
    heel pivot — and rig modules are not being changed (Adrian, 2026-07-31). The caller
    logs the drop rather than letting it be silent.

    Returns (options_dict, dropped_names).
    """
    mod = modules_by_name.get(joint.module) if joint.module else None
    if not mod:
        return {}, []

    pivots = mod.get('foot_pivots') or {}
    if not pivots:
        return {}, []

    def convert(vec):
        v = [float(vec[0]) * scale, float(vec[1]) * scale, float(vec[2]) * scale]
        return axis_convert(v) if axis_convert else v

    opts = {}
    if isinstance(pivots.get('heel'), (list, tuple)):
        opts['heel_position'] = convert(pivots['heel'])
    if isinstance(pivots.get('toe_tip'), (list, tuple)):
        opts['toe_tip_position'] = convert(pivots['toe_tip'])

    dropped = [k for k in ('inner_bank', 'outer_bank') if k in pivots]
    return opts, dropped


class Classification(object):
    """Result of a classification pass."""

    def __init__(self):
        self.components = []          # list[dict] shaped like ComponentSpec
        self.notes = []               # human-readable, surfaced in the log
        self.owner_of = {}            # joint name -> component id that owns it
        self.adopted_by = {}          # joint name -> component id that adopts it

    def add(self, comp, member_names):
        self.components.append(comp)
        for n in member_names:
            self.owner_of[n] = comp['id']

    def adopt(self, comp_id, joint_names):
        """Record joints a limb component absorbs without listing as members.

        IKArm declares limb_features=('fingers','twists'), so it discovers the finger
        chains under the wrist itself. Those joints are real driver joints, but they
        must NOT get components of their own — a SimpleFK per finger joint would fight
        the limb for control of the same transforms.
        """
        for n in joint_names:
            self.adopted_by[n] = comp_id

    def by_type(self):
        counts = {}
        for c in self.components:
            counts[c['type']] = counts.get(c['type'], 0) + 1
        return counts


def classify(source, advanced=False, available_types=None, axis_convert=None):
    """Produce component assignments for an ArmatureSource.

    Args:
        source:          blueprint_source.ArmatureSource.
        advanced:        use the ribbon component set where it applies.
        available_types: set of registered component type names. When given, a chosen
                         type not in it degrades to its free equivalent with a note,
                         so a user who picks Advanced without the pack still imports.
        axis_convert:    optional callable applied to blueprint-sourced world
                         coordinates (foot pivots) when the stage is Z-up.

    Returns Classification.
    """
    by_name, roots = build_graph(source.joints)
    modules_by_name = source.module_by_name()
    scale = source.blueprint_scale
    result = Classification()

    def type_ok(name):
        return available_types is None or name in available_types

    def pick(preferred, fallback):
        """Ribbon type if we're in Advanced AND it's actually installed."""
        if advanced and preferred:
            if type_ok(preferred):
                return preferred
            result.notes.append(
                '%s is not installed (Advanced Ribbon Modules), using %s'
                % (preferred, fallback))
        return fallback

    handled = set()

    def emit(joint, comp_type, members, region=None, options=None):
        side = SIDE_MAP.get(joint.side, 'md')
        opts = {'ctrl_color': SIDE_COLOR[side]}
        if options:
            opts.update(options)
        comp = {
            'id': component_id(joint.name, comp_type),
            'type': comp_type,
            'joints': [m.name for m in members],
            'side': side,
            'region': fs_region(region if region is not None else joint.region),
            'options': opts,
        }
        result.add(comp, [m.name for m in members])
        for m in members:
            handled.add(m.name)
        return comp

    # ── Rule 1: the root joint gets World ────────────────────────────────────
    for root in roots:
        if root.region == 'root' or root.parent is None:
            emit(root, 'World', [root], region='spine')

    # Walk the whole graph depth-first in blueprint order so parents are classified
    # before their children and parent_plug resolution below always finds an owner.
    def walk(joint):
        _classify_joint(joint)
        for child in joint.children:
            walk(child)

    def _classify_joint(joint):
        if joint.name in handled or not joint.is_driver:
            return

        region = joint.region

        # ── Rule 2: pelvis ───────────────────────────────────────────────────
        if region == 'pelvis':
            emit(joint, 'SimpleFK', [joint], region='spine')
            return

        # ── Rule 3: spine chain ──────────────────────────────────────────────
        if region == 'spine':
            chain = _region_chain(joint, {'spine'})
            comp_type = pick('RibbonSpine' if len(chain) >= RIBBON_SPINE_MIN else None,
                             'SimpleFK')
            if comp_type == 'RibbonSpine':
                emit(joint, comp_type, chain, region='spine')
            else:
                emit(joint, 'SimpleFK', [joint], region='spine')
            return

        # ── Rule 4: neck chain (+ head) ──────────────────────────────────────
        if region == 'neck':
            chain = _region_chain(joint, {'neck', 'head'})
            comp_type = pick('RibbonSpine' if len(chain) >= RIBBON_SPINE_MIN else None,
                             'SimpleFK')
            if comp_type == 'RibbonSpine':
                emit(joint, comp_type, chain, region='neck')
            else:
                emit(joint, 'SimpleFK', [joint], region='neck')
            return

        if region == 'head':
            emit(joint, 'SimpleFK', [joint], region='head')
            return

        # ── Rules 5 + 6: arm ─────────────────────────────────────────────────
        if region == 'arm':
            parent = by_name.get(joint.parent) if joint.parent else None
            parent_region = parent.region if parent else ''

            # The clavicle is the arm-region joint hanging off the spine.
            if parent_region in ('spine', 'neck', 'pelvis', 'root'):
                emit(joint, 'SimpleFK', [joint], region='arm')
                return

            # upperarm -> lowerarm -> hand. The hand is region 'hand', so walk the
            # arm chain and accept a hand-region tip.
            chain = _region_chain(joint, {'arm', 'hand'}, max_len=3)
            if len(chain) == 3 and chain[-1].region == 'hand':
                comp_type = pick('RibbonIKArm', 'IKArm')
                comp = emit(joint, comp_type, chain, region='arm')
                # The wrist's whole subtree is the fingers, and the arm's
                # limb_features=('fingers','twists') adopts them by hierarchy. Mark
                # them handled so the total fallback below does not hand each finger
                # joint its own SimpleFK and fight the limb for the same transforms.
                fingers = _descendants(chain[-1])
                result.adopt(comp['id'], [f.name for f in fingers])
                for f in fingers:
                    handled.add(f.name)
                return

            emit(joint, 'SimpleFK', [joint], region='arm')
            return

        # ── Rules 7 + 8: leg ─────────────────────────────────────────────────
        if region == 'leg':
            chain = _linear_chain(joint, max_len=QUADLEG_CHAIN)
            ends_in_foot = chain[-1].region == 'foot'

            if len(chain) == QUADLEG_CHAIN and ends_in_foot and type_ok('QuadLeg'):
                emit(joint, 'QuadLeg', chain, region='leg')
                return

            if len(chain) >= IKLEG_CHAIN and chain[IKLEG_CHAIN - 1].region == 'foot':
                members = chain[:IKLEG_CHAIN]
                comp_type = pick('RibbonIKLeg', 'IKLeg')
                foot = members[2]
                opts, dropped = foot_pivots_for(foot, modules_by_name, scale,
                                                axis_convert)
                if dropped:
                    result.notes.append(
                        '%s: bank pivots (%s) dropped, IKLeg has no bank options'
                        % (foot.name, ', '.join(dropped)))
                if not opts:
                    result.notes.append(
                        '%s: no foot pivots in the export, IKLeg will use its guide '
                        'defaults' % foot.name)
                emit(joint, comp_type, members, region='leg', options=opts)
                return

            emit(joint, 'SimpleFK', [joint], region='leg')
            return

        # ── Rule 9: total fallback ───────────────────────────────────────────
        emit(joint, 'SimpleFK', [joint], region=region)

    for root in roots:
        walk(root)

    _resolve_parent_plugs(result, by_name)
    return result


# Output plug to use when parenting UNDER a component of a given type.
_PARENT_PLUG_BY_TYPE = {
    'RibbonSpine': 'tip_out',
    'World': 'ctrl_out',
}


def _resolve_parent_plugs(result, by_name):
    """Wire each component's parent_plug from the joint hierarchy.

    A component parents under whichever component owns the nearest ancestor joint that
    is not one of its own members. Mirrors what both shipped biped templates do.
    """
    comp_by_id = {c['id']: c for c in result.components}

    for comp in result.components:
        members = set(comp['joints'])
        first = by_name.get(comp['joints'][0])
        if first is None:
            continue

        ancestor = by_name.get(first.parent) if first.parent else None
        while ancestor is not None and ancestor.name in members:
            ancestor = by_name.get(ancestor.parent) if ancestor.parent else None

        owner_id = None
        while ancestor is not None:
            owner_id = result.owner_of.get(ancestor.name)
            if owner_id and owner_id != comp['id']:
                break
            owner_id = None
            ancestor = by_name.get(ancestor.parent) if ancestor.parent else None

        if not owner_id:
            continue

        owner = comp_by_id.get(owner_id)
        plug = _PARENT_PLUG_BY_TYPE.get(owner['type'] if owner else '', 'ctrl_out')
        comp['parent_plug'] = '%s.%s' % (owner_id, plug)
