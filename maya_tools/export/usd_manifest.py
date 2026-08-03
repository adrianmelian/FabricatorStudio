"""USD delivery embeds — the armature_modules manifest and the v2 blueprint.

The exported USD carries two JSON strings in the stage's customLayerData
(SPEC-FABRICATOR-USD-EXPORT, the Armature lane's contract):

  armature_modules     — the WORKING payload: the rig described in Armature's
                         own Module vocabulary (kind/side/twistCount/...),
                         translated here because component knowledge lives on
                         the Maya side and Armature never learns what a
                         Fabricator component is (their boundary rule A12).
  fabricator_blueprint — provenance: the raw Fabricator blueprint, version-2
                         schema (ratified 2026-08-03), numbers in scene
                         centimetres, declared honestly.

Capture is split in two phases because the export surgery destroys aimers but
settles transforms:

  capture_early()  — BEFORE the Armature teardown (aimers alive): component
                     records, aimer states, rig label.
  build_embeds()   — AFTER the surgery, at the final bind pose: joint
                     transforms read live from the scene, mapping resolved,
                     both embeds serialized.

THE ROLE RULE (Armature's, ratified): a joint is `derived` ONLY when Armature
would regenerate it in the same place — twists sitting at the standard
fractions of their segment (i/(n+1), tolerance 1% of segment length).
Anything else is `driver`; a driver too many costs nothing, a derived joint
regenerated elsewhere moves someone's skinning.

Components with no clean module mapping export their joints LOOSE (absent from
the joints map) — Armature's spec calls loose a feature, not a failure.
"""
__author__ = "Adrian Melian"

import json
import math
import re

import maya.cmds as cmds


# Armature's FROZEN region vocabulary (v1; 'chain' joins in the v2 batch but
# the safe write for bending runs stays 'tail' until their writer flips too).
_ARMATURE_REGIONS = {'root', 'pelvis', 'spine', 'neck', 'head', 'arm', 'hand',
                     'leg', 'foot', 'tail', 'prop'}

_FS_SIDE_TO_ARMATURE = {'lf': 'L', 'rt': 'R', 'md': 'C', '': 'C'}

# Fraction tolerance for the derived-twist rule: 1% of segment length.
_TWIST_TOLERANCE = 0.01


# ─────────────────────────────────────────────────────────────────────────────
# Early capture (pre-teardown: aimers + component records still alive)
# ─────────────────────────────────────────────────────────────────────────────

def capture_early(log=print):
    """Capture everything the embeds need that the export surgery destroys.

    Returns a dict, or None when the scene has no Fabricator registry (the
    export then ships as a plain delivery USD with no embeds — the
    any-rig-to-Unreal case)."""
    from maya_tools.rigging.fabricator import nodes

    reg = nodes.get_registry()
    if not reg:
        return None

    from maya_tools.rigging.fabricator import limb_node
    from maya_tools.rigging.joint_orient import joint_orient_app as joa

    components = []
    for node in nodes.get_all_component_nodes(reg):
        comp = {
            'id':      nodes.get_component_id(node),
            'type':    nodes.get_component_type(node),
            'side':    nodes.get_component_side(node),
            'region':  nodes.get_component_region(node),
            'joints':  [j for j in nodes.get_component_joint_names(node)
                        if cmds.objExists(j)],
            'options': {},
            'fingers': [],
            'twist_upper': [],
            'twist_lower': [],
        }
        try:
            comp['options'] = nodes.get_component_options(node) or {}
        except Exception:
            pass
        # Read each list independently: a component missing one attr (an
        # importer-created IKArm has no authored finger_roots) must not
        # blind the others.
        for field, reader in (('fingers', limb_node.list_finger_roots),
                              ('twist_upper', limb_node.list_twist_upper),
                              ('twist_lower', limb_node.list_twist_lower)):
            try:
                comp[field] = [x for x in reader(node) if cmds.objExists(x)]
            except Exception:
                pass
        components.append(comp)

    root = nodes.get_registry_root_joint() or ''
    aim = {}
    if root and cmds.objExists(root):
        for j in [root] + (cmds.listRelatives(root, allDescendents=True,
                                              type='joint') or []):
            try:
                state = joa.get_aimer_state(j) or {}
            except Exception:
                state = {}
            if state.get('aim_target'):
                aim[j] = {'aim_target': state.get('aim_target', ''),
                          'aim_offset': list(state.get('aim_offset',
                                                       (0.0, 0.0, 0.0)))}

    label = ''
    try:
        label = nodes.get_rig_label() or ''
    except Exception:
        pass

    log('[usd] early capture: %d component(s), %d aimer state(s), label %r'
        % (len(components), len(aim), label))
    return {'components': components, 'aim': aim, 'rig_label': label,
            'root': root}


# ─────────────────────────────────────────────────────────────────────────────
# Module mapping (component records -> Armature Module vocabulary)
# ─────────────────────────────────────────────────────────────────────────────

def _module_key(base, side, taken):
    key = re.sub(r'[^a-z0-9_]', '_', base.lower()).strip('_') or 'module'
    key = re.sub(r'_(l|lf|left)$', '', key)
    key = re.sub(r'_(r|rt|right)$', '', key)
    key = re.sub(r'_?\d+$', '', key) or key
    if side == 'L':
        key += '_l'
    elif side == 'R':
        key += '_r'
    out, i = key, 2
    while out in taken:
        out = '%s_%d' % (key, i)
        i += 1
    taken.add(out)
    return out


def _label_for(key):
    words = [w for w in key.split('_') if w]
    side = ''
    if words and words[-1] in ('l', 'r'):
        side = ', left' if words[-1] == 'l' else ', right'
        words = words[:-1]
    return ' '.join(w.capitalize() for w in words) + side


def _scene_twists(segment_joint):
    """Twist-named joint children of a segment joint, ordered by distance
    from the segment start (the layout limb adoption reads too)."""
    try:
        from maya_tools.rigging.fabricator import armature
        kids = [k for k in (cmds.listRelatives(segment_joint, children=True,
                                               type='joint') or [])
                if armature.is_twist_joint(k)]
    except Exception:
        return []
    if len(kids) < 2:
        return kids
    origin = cmds.xform(segment_joint, q=True, ws=True, t=True)

    def dist(j):
        p = cmds.xform(j, q=True, ws=True, t=True)
        return sum((p[i] - origin[i]) ** 2 for i in range(3))
    return sorted(kids, key=dist)


def _scene_finger_roots(hand):
    """Joint children of the hand that are not twist joints — the digit
    chains, read from the hierarchy."""
    try:
        from maya_tools.rigging.fabricator import armature
        kids = cmds.listRelatives(hand, children=True, type='joint') or []
        return [k for k in kids if not armature.is_twist_joint(k)]
    except Exception:
        return []


def _twist_fraction_role(twist, seg_start, seg_end, index, count):
    """'derived' when the twist sits at fraction (index+1)/(count+1) of its
    segment within 1% of segment length, else 'driver' (the ratified rule)."""
    try:
        a = cmds.xform(seg_start, q=True, ws=True, t=True)
        b = cmds.xform(seg_end, q=True, ws=True, t=True)
        p = cmds.xform(twist, q=True, ws=True, t=True)
    except Exception:
        return 'driver'
    seg = [b[i] - a[i] for i in range(3)]
    seg_len = math.sqrt(sum(v * v for v in seg))
    if seg_len < 1e-6:
        return 'driver'
    rel = [p[i] - a[i] for i in range(3)]
    along = sum(rel[i] * seg[i] for i in range(3)) / seg_len
    expected = seg_len * (index + 1) / float(count + 1)
    return ('derived' if abs(along - expected) <= seg_len * _TWIST_TOLERANCE
            else 'driver')


def _map_components(early, log=print):
    """component records -> (modules dict, joints dict) in Armature's Module
    vocabulary. Unmappable components leave their joints loose."""
    modules, joints_map = {}, {}
    taken = set()

    def add_joint(j, module_key, role, region):
        joints_map[j] = {'module': module_key, 'role': role,
                         'region': region if region in _ARMATURE_REGIONS
                         else 'tail'}

    def add_twists(comp, module_key, chain):
        pairs = (('twist_upper', 0), ('twist_lower', 1))
        count = 0
        for attr, seg in pairs:
            # Authored lists first; an importer-created limb adopts twists
            # by hierarchy, so fall back to the segment joint's twist
            # children (same reasoning as the finger fallback).
            twists = comp[attr]
            if not twists and len(chain) > seg:
                twists = _scene_twists(chain[seg])
            if not twists or len(chain) < seg + 2:
                continue
            count = max(count, len(twists))
            for i, t in enumerate(twists):
                role = _twist_fraction_role(t, chain[seg], chain[seg + 1],
                                            i, len(twists))
                add_joint(t, module_key, role, joints_map.get(
                    chain[seg], {}).get('region', 'arm'))
        return count

    for comp in early['components']:
        ctype = comp['type']
        side = _FS_SIDE_TO_ARMATURE.get(comp['side'], 'C')
        joints = comp['joints']
        if not joints:
            continue

        if ctype == 'World':
            continue  # the root exports loose, per the spec

        if ctype in ('IKArm', 'RibbonIKArm') and len(joints) >= 3:
            key = _module_key('arm', side, taken)
            modules[key] = {'key': key, 'label': _label_for(key),
                            'side': side, 'kind': 'limb',
                            'twistCount': 0, 'jointCount': 3,
                            'editMiddles': False}
            add_joint(joints[0], key, 'driver', 'arm')
            add_joint(joints[1], key, 'driver', 'arm')
            hand = joints[2]
            # finger_roots is authored on hand-assembled limbs; an IKArm
            # created by the Armature importer adopts fingers by HIERARCHY
            # (limb_features), so fall back to the scene: joint children of
            # the hand that are not twists ARE the digit chains.
            fingers = comp['fingers'] or _scene_finger_roots(hand)
            hkey = _module_key('hand', side, taken)
            modules[hkey] = {'key': hkey, 'label': _label_for(hkey),
                             'side': side, 'kind': 'hand',
                             'twistCount': 0, 'jointCount': 1,
                             'editMiddles': False,
                             'digitCount': len(fingers)}
            add_joint(hand, hkey, 'driver', 'hand')
            for f in fingers:
                for d in [f] + (cmds.listRelatives(
                        f, allDescendents=True, type='joint') or []):
                    add_joint(d, hkey, 'driver', 'hand')
            modules[key]['twistCount'] = add_twists(comp, key, joints[:3])
            continue

        if ctype in ('IKLeg', 'RibbonIKLeg', 'QuadLeg') and len(joints) >= 3:
            key = _module_key('leg', side, taken)
            modules[key] = {'key': key, 'label': _label_for(key),
                            'side': side, 'kind': 'limb',
                            'twistCount': 0, 'jointCount': len(joints),
                            'editMiddles': False}
            for i, j in enumerate(joints):
                add_joint(j, key, 'driver', 'leg' if i < 2 else 'foot')
            modules[key]['twistCount'] = add_twists(comp, key, joints)
            opts = comp.get('options') or {}
            pivots = {}
            if opts.get('heel_position'):
                pivots['heel'] = list(opts['heel_position'])
            if opts.get('toe_tip_position'):
                pivots['toe_tip'] = list(opts['toe_tip_position'])
            if pivots:
                modules[key]['_foot_pivots'] = pivots
            continue

        if ctype in ('SimpleFK', 'AdvancedFK', 'RibbonSpine'):
            region = comp['region'] or ''
            first = joints[0].lower()
            pelvis_headed = 'pelvis' in first
            if region == 'spine' or pelvis_headed:
                key = _module_key('spine', 'C', taken)
                mod_region = 'spine'
            elif region == 'neck' or 'neck' in first:
                key = _module_key('neck', 'C', taken)
                mod_region = 'neck'
            else:
                key = _module_key(joints[0], side, taken)
                mod_region = region if region in _ARMATURE_REGIONS else 'tail'
            mod = {'key': key, 'label': _label_for(key), 'side': side
                   if mod_region not in ('spine', 'neck') else 'C',
                   'kind': 'chain', 'twistCount': 0,
                   'jointCount': len(joints), 'editMiddles': True,
                   'region': mod_region}
            for j in joints:
                jl = j.lower()
                if 'pelvis' in jl:
                    add_joint(j, key, 'driver', 'pelvis')
                elif mod_region == 'neck' and 'head' in jl:
                    add_joint(j, key, 'driver', 'head')
                else:
                    add_joint(j, key, 'driver', mod_region)
            if mod_region == 'neck' and 'head' in joints[-1].lower():
                mod['cap'] = {'base': joints[-1], 'region': 'head'}
                mod['jointCount'] = len(joints) - 1
            modules[key] = mod
            continue

        log('[usd] component %r (%s): no module mapping — joints export loose'
            % (comp['id'], ctype))

    return modules, joints_map


# ─────────────────────────────────────────────────────────────────────────────
# Late build (post-surgery, scene at the final bind pose)
# ─────────────────────────────────────────────────────────────────────────────

def build_embeds(early, root, pruned_influences=(), log=print):
    """Serialize both embeds from the settled scene. Returns
    (manifest_json, blueprint_json, blueprint_dict)."""
    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION

    modules, joints_map = _map_components(early, log=log)

    foot_pivots = {key: mod.pop('_foot_pivots')
                   for key, mod in modules.items() if '_foot_pivots' in mod}

    manifest = {
        'version': 1,
        'modules': modules,
        'joints': {j: dict(rec) for j, rec in joints_map.items()
                   if cmds.objExists(j)},
    }

    ordered = _walk_joints(root)

    # A module is unweighted when NO member joint carries weight anywhere —
    # the ratified scarf rule (INFO, never a block). This covers both pruned
    # zero-weight influences AND joints that were never influences at all.
    weighted = set()
    for sc in cmds.ls(type='skinCluster') or []:
        try:
            for inf in cmds.skinCluster(sc, q=True,
                                        weightedInfluence=True) or []:
                weighted.add(inf.split('|')[-1])
        except Exception:
            pass
    members = {}
    for j, rec in joints_map.items():
        members.setdefault(rec['module'], []).append(j)
    unweighted = sorted(key for key, js in members.items()
                        if not any(j in weighted for j in js))

    joints = []
    for path in ordered:
        short = path.split('|')[-1]
        parent = (cmds.listRelatives(path, parent=True, type='joint') or [None])[0]
        rec = joints_map.get(short, {})
        aim = early['aim'].get(short, {})
        spec = {
            'name': short,
            'parent': parent.split('|')[-1] if parent else None,
            'translate': list(cmds.getAttr(path + '.translate')[0]),
            'rotate': list(cmds.getAttr(path + '.rotate')[0]),
            'joint_orient': list(cmds.getAttr(path + '.jointOrient')[0]),
            'rotate_order': ('xyz', 'yzx', 'zxy', 'xzy', 'yxz', 'zyx')[
                cmds.getAttr(path + '.rotateOrder')],
            'radius': cmds.getAttr(path + '.radius'),
            'aim_target': aim.get('aim_target', ''),
            'aim_offset': aim.get('aim_offset', [0.0, 0.0, 0.0]),
            'side': (_module_side(modules, rec['module'])
                     if rec.get('module') in modules else 'md'),
            'region': rec.get('region', 'root' if parent is None else ''),
            'role': rec.get('role', 'driver'),
        }
        if parent is None:
            spec['world_rotate'] = list(
                cmds.xform(path, q=True, ws=True, ro=True))
        joints.append(spec)

    bp_modules = []
    for key, mod in modules.items():
        entry = {'name': key, 'role': 'module', 'kind': mod['kind'],
                 'side': mod['side'], 'twist': mod.get('twistCount', 0)}
        if key in foot_pivots:
            entry['foot_pivots'] = foot_pivots[key]
        bp_modules.append(entry)

    blueprint = {
        'version': 2,
        'units': 'cm',
        'up_axis': 'Y',
        'engine_joints': [],
        'fabricator_version': FABRICATOR_VERSION,
        'unweighted_modules': unweighted,
        'joints': joints,
        'modules': bp_modules,
    }

    log('[usd] embeds built: %d module(s), %d mapped joint(s), %d loose, '
        '%d unweighted module(s)'
        % (len(modules), len(manifest['joints']),
           len(ordered) - len(manifest['joints']), len(unweighted)))
    return (json.dumps(manifest), json.dumps(blueprint), blueprint)


def _module_side(modules, key):
    side = modules.get(key, {}).get('side', 'C')
    return {'L': 'lf', 'R': 'rt', 'C': 'md'}[side]


def _walk_joints(root):
    """Root-first, parent-before-child, stable order."""
    ordered, stack = [], [root]
    while stack:
        j = stack.pop(0)
        ordered.append(j)
        kids = cmds.listRelatives(j, children=True, type='joint',
                                  fullPath=True) or []
        stack = sorted(kids) + stack
    return ordered


