# skeleton_io_app.py
# Skeleton IO — export/import Maya joint hierarchies to/from JSON (scale-free rebuild).
__author__ = "Adrian Melian"

"""
Usage:
    import maya_tools.skeleton.skeleton_io.skeleton_io_app as sio

    sio.export_skeleton("root", r"C:/temp/skeleton.json")

    sio.import_skeleton(r"C:/temp/skeleton.json",
                        namespace="charA_",
                        remove_scale_offset=True,
                        zero_joint_scales=True,
                        bake_to_joint_orient=False)

Notes:
- Assumes unique joint names within the exported hierarchy.
- Scale-free: any upstream scale used to resize the rig is baked into positions/orients.
- User attributes: numeric/bool/enum/string/double3 are restored (no connections).
"""

import json
import os

import maya.cmds as cmds
from maya.api import OpenMaya as om


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _short(n):
    return n.split('|')[-1]


def _exists(n):
    return cmds.objExists(n)


def _get_joint_chain(root):
    if (not _exists(root)) or cmds.nodeType(root) != 'joint':
        raise ValueError(f"Root must be an existing joint: '{root}'")
    desc = cmds.listRelatives(root, ad=True, type='joint') or []
    return [root] + list(reversed(desc))  # top-down


def _wm(node):
    return cmds.xform(node, q=True, ws=True, m=True)


def _set_wm(node, m16):
    cmds.xform(node, ws=True, m=m16)


def _m_no_scale(m16):
    m  = om.MMatrix(m16)
    tm = om.MTransformationMatrix(m)
    q  = tm.rotation(asQuaternion=True)
    t  = tm.translation(om.MSpace.kWorld)
    clean = om.MTransformationMatrix()
    clean.setRotation(q)
    clean.setTranslation(t, om.MSpace.kWorld)
    M = clean.asMatrix()
    return [M[i] for i in range(16)]


def _parent_safe(child, parent):
    try:
        cmds.parent(child, parent) if parent else None
    except RuntimeError:
        pass


def _q_from_m(m16):
    m  = om.MMatrix(m16)
    tm = om.MTransformationMatrix(m)
    return tm.rotation(asQuaternion=True)


def _deg(rad):
    return rad * 180.0 / 3.141592653589793


def _local_rot_quat(parent_wm, child_wm):
    pq = _q_from_m(parent_wm) if parent_wm else om.MQuaternion()
    cq = _q_from_m(child_wm)
    return pq.conjugate() * cq


def _list_user_attrs(node):
    return cmds.listAttr(node, ud=True) or []


def _attr_type(node, a):
    try:
        return cmds.getAttr(f'{node}.{a}', type=True)
    except Exception:
        return None


def _get_attr_value(node, a, atype):
    if atype in ('double3', 'float3', 'long3', 'short3'):
        try:
            return list(cmds.getAttr(f'{node}.{a}')[0])
        except Exception:
            return None
    elif atype in ('double', 'float', 'long', 'short', 'bool', 'enum'):
        try:
            return cmds.getAttr(f'{node}.{a}')
        except Exception:
            return None
    elif atype == 'string':
        try:
            return cmds.getAttr(f'{node}.{a}')
        except Exception:
            return None
    return None


def _enum_names(node, a):
    try:
        if cmds.attributeQuery(a, n=node, exists=True) and cmds.addAttr(f'{node}.{a}', q=True, at=True) == 'enum':
            return cmds.attributeQuery(a, n=node, listEnum=True) or []
    except Exception:
        pass
    return []


def _locks(node):
    out = {}
    for ch in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz', 'v'):
        try:
            out[ch] = bool(cmds.getAttr(f'{node}.{ch}', l=True))
        except Exception:
            pass
    return out


def _limits(node):
    data = {}
    for axis in ('x', 'y', 'z'):
        for prefix, label in (('t', 'translate'), ('r', 'rotate')):
            key = f'{prefix}{axis}'
            try:
                en = cmds.getAttr(f'{node}.{label}{axis.upper()}LimitEnable')[0]
                vmin = cmds.getAttr(f'{node}.{label}{axis.upper()}LimitMin')
                vmax = cmds.getAttr(f'{node}.{label}{axis.upper()}LimitMax')
                data[key] = {
                    'enableMin': bool(en[0]), 'enableMax': bool(en[1]),
                    'min': float(vmin), 'max': float(vmax),
                }
            except Exception:
                pass
    return data


def _safe_set(node, plug, val):
    try:
        cmds.setAttr(f'{node}.{plug}', val)
    except Exception:
        pass


def _by_name(joints_data, name):
    for jd in joints_data:
        if jd['name'] == name:
            return jd
    raise KeyError(name)


def _topo_sort(name_to_parent):
    remaining = set(name_to_parent.keys())
    result    = []
    while remaining:
        progressed = False
        for n in list(remaining):
            if name_to_parent[n] is None or name_to_parent[n] in result:
                result.append(n)
                remaining.remove(n)
                progressed = True
        if not progressed:
            result.extend(list(remaining))
            break
    return result


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def export_skeleton(root_joint: str, json_path: str, include_user_attrs: bool = True) -> None:
    """Serialize the joint hierarchy rooted at root_joint to a JSON file.

    Args:
        root_joint:        Name of the root joint in the scene.
        json_path:         Absolute path to write the .json file.
        include_user_attrs: If True, custom user attributes are included.

    Raises:
        ValueError: If root_joint is not a joint node.
    """
    joints = _get_joint_chain(root_joint)

    pmap = {}
    for j in joints:
        p = cmds.listRelatives(j, p=True, type='joint') or [None]
        pmap[_short(j)] = _short(p[0]) if p[0] else None

    data = {'version': 2, 'root': _short(root_joint), 'joints': []}

    for j in joints:
        sj = _short(j)
        attrs = {
            'rotateOrder':             int(cmds.getAttr(f'{j}.rotateOrder')),
            'segmentScaleCompensate':  bool(cmds.getAttr(f'{j}.segmentScaleCompensate')),
            'radius':                  float(cmds.getAttr(f'{j}.radius')),
            'drawStyle':               int(cmds.getAttr(f'{j}.drawStyle')) if cmds.attributeQuery('drawStyle', n=j, exists=True) else 0,
            'jointOrient':             list(cmds.getAttr(f'{j}.jointOrient')[0]),
            'rotate':                  list(cmds.getAttr(f'{j}.rotate')[0]),
            'translate':               list(cmds.getAttr(f'{j}.translate')[0]),
            'preferredAngle':          list(cmds.getAttr(f'{j}.preferredAngle')[0]),
            'side':                    int(cmds.getAttr(f'{j}.side')),
            'type':                    int(cmds.getAttr(f'{j}.type')),
            'otherType':               cmds.getAttr(f'{j}.otherType') or '',
            'displayLocalAxis':        bool(cmds.getAttr(f'{j}.displayLocalAxis')),
            'locks':                   _locks(j),
            'limits':                  _limits(j),
        }

        uattrs = []
        if include_user_attrs:
            for ua in _list_user_attrs(j):
                atype = _attr_type(j, ua)
                if not atype:
                    continue
                val = _get_attr_value(j, ua, atype)
                if val is None:
                    continue
                entry = {
                    'name': ua, 'type': atype, 'value': val,
                    'keyable':     bool(cmds.getAttr(f'{j}.{ua}', k=True)),
                    'channelBox':  bool(cmds.getAttr(f'{j}.{ua}', cb=True)),
                    'locked':      bool(cmds.getAttr(f'{j}.{ua}', l=True)),
                }
                if atype == 'enum':
                    entry['enumNames'] = _enum_names(j, ua)
                for flag, key in (('min','min'), ('max','max'), ('softMin','softMin'), ('softMax','softMax')):
                    try:
                        v = cmds.addAttr(f'{j}.{ua}', q=True, **{flag: True})
                        if v is not None:
                            entry[key] = v
                    except Exception:
                        pass
                uattrs.append(entry)

        data['joints'].append({
            'name':        sj,
            'parent':      pmap[sj],
            'worldMatrix': _wm(j),
            'attrs':       attrs,
            'userAttrs':   uattrs,
        })

    folder = os.path.dirname(json_path)
    if folder and not os.path.exists(folder):
        os.makedirs(folder)
    with open(json_path, 'w') as fh:
        json.dump(data, fh, indent=2)

    cmds.warning(f'[skeleton_io] Exported {len(joints)} joints → {json_path}')


def import_skeleton(
    json_path: str,
    namespace: str = '',
    remove_scale_offset: bool = True,
    zero_joint_scales: bool = True,
    bake_to_joint_orient: bool = False,
) -> list:
    """Rebuild a joint hierarchy from a JSON file.

    Args:
        json_path:            Absolute path to the .json file.
        namespace:            Optional prefix applied to all joint names.
        remove_scale_offset:  Strip scale from world matrices on import.
        zero_joint_scales:    Set joint scale to (1,1,1) on all joints.
        bake_to_joint_orient: Absorb local rotation into jointOrient (rotate → 0).

    Returns:
        Ordered list of created joint names (top-down).

    Raises:
        IOError:    If the file does not exist.
        RuntimeError: If root_joint is invalid.
    """
    if not os.path.exists(json_path):
        raise IOError(f'File not found: {json_path}')
    with open(json_path, 'r') as fh:
        data = json.load(fh)

    joints_data    = data['joints']
    name_to_parent = {j['name']: j['parent'] for j in joints_data}
    order          = _topo_sort(name_to_parent)

    created = {}
    for name in order:
        jd = _by_name(joints_data, name)
        j  = cmds.createNode('joint', name=namespace + jd['name'])
        created[name] = j

        par = jd['parent']
        _parent_safe(j, created[par] if par else None)

        a = jd['attrs']
        _safe_set(j, 'rotateOrder',            a.get('rotateOrder', 0))
        _safe_set(j, 'segmentScaleCompensate', a.get('segmentScaleCompensate', True))
        _safe_set(j, 'radius',                 a.get('radius', 0.5))
        if 'drawStyle' in a:
            _safe_set(j, 'drawStyle', a['drawStyle'])
        _safe_set(j, 'side', a.get('side', 0))
        _safe_set(j, 'type', a.get('type', 0))
        if a.get('otherType', ''):
            try:
                cmds.setAttr(f'{j}.otherType', a['otherType'], type='string')
            except Exception:
                pass
        _safe_set(j, 'displayLocalAxis', a.get('displayLocalAxis', False))

        cmds.setAttr(f'{j}.translate',   *a.get('translate',   [0, 0, 0]), type='double3')
        cmds.setAttr(f'{j}.rotate',      *a.get('rotate',      [0, 0, 0]), type='double3')
        cmds.setAttr(f'{j}.jointOrient', *a.get('jointOrient', [0, 0, 0]), type='double3')

    # Apply world placement
    for name in order:
        jd  = _by_name(joints_data, name)
        j   = created[name]
        wm  = jd['worldMatrix']
        if remove_scale_offset:
            wm = _m_no_scale(wm)
        _set_wm(j, wm)
        if zero_joint_scales:
            for ax in 'XYZ':
                _safe_set(j, f'scale{ax}', 1.0)

    if bake_to_joint_orient:
        _bake_world_rot_into_joint_orient(created, joints_data, order)

    # Limits, locks, user attrs — applied after transforms
    for name in order:
        jd    = _by_name(joints_data, name)
        j     = created[name]
        lim   = jd['attrs'].get('limits', {})
        _restore_limits(j, lim)
        locks = jd['attrs'].get('locks', {})
        for ch, state in locks.items():
            try:
                cmds.setAttr(f'{j}.{ch}', l=bool(state))
            except Exception:
                pass
        for ua in jd.get('userAttrs', []):
            _restore_user_attr(j, ua)

    cmds.warning(f'[skeleton_io] Imported {len(order)} joints from {json_path}')
    return [created[n] for n in order]


# ─────────────────────────────────────────────
# Private import helpers
# ─────────────────────────────────────────────

def _bake_world_rot_into_joint_orient(created_map, joints_data, order):
    """Absorb local rotation into jointOrient so rotate channels are zeroed."""
    jd_by    = {jd['name']: jd for jd in joints_data}
    wm_cache = {name: jd_by[name]['worldMatrix'] for name in order}

    for name in order:
        j      = created_map[name]
        parent = jd_by[name]['parent']
        pwm    = wm_cache[parent] if parent else None
        lq     = _local_rot_quat(pwm, wm_cache[name])

        ro  = int(cmds.getAttr(f'{j}.rotateOrder'))
        e   = om.MEulerRotation(lq)
        e.reorder(ro)
        jo_deg = [_deg(e.x), _deg(e.y), _deg(e.z)]

        try:
            cmds.setAttr(f'{j}.jointOrient', *jo_deg, type='double3')
            cmds.setAttr(f'{j}.rotate', 0, 0, 0, type='double3')
        except Exception:
            pass


def _restore_user_attr(node, ua):
    name  = ua['name']
    atype = ua['type']
    val   = ua['value']

    try:
        if _exists(f'{node}.{name}'):
            cmds.deleteAttr(f'{node}.{name}')
    except Exception:
        pass

    kw = {'ln': name}
    if atype == 'enum':
        enum_names = ua.get('enumNames', [])
        kw['at'] = 'enum'
        kw['en'] = ':'.join(enum_names[0].split(':')) if enum_names else 'Off:On'
    elif atype in ('double3', 'float3', 'long3', 'short3'):
        kw['at'] = atype
        kw['nc'] = 3
    elif atype in ('double', 'float', 'long', 'short', 'bool', 'string'):
        kw['at'] = atype
    else:
        return

    try:
        cmds.addAttr(node, **kw)
    except RuntimeError:
        return

    for key, flag in (('min','min'), ('max','max'), ('softMin','smn'), ('softMax','smx')):
        if key in ua and atype not in ('string', 'bool', 'enum'):
            try:
                cmds.addAttr(f'{node}.{name}', e=True, **{flag: ua[key]})
            except Exception:
                pass

    try:
        if atype in ('double3', 'float3', 'long3', 'short3'):
            cmds.setAttr(f'{node}.{name}', *val, type='double3')
        elif atype == 'string':
            cmds.setAttr(f'{node}.{name}', val, type='string')
        else:
            cmds.setAttr(f'{node}.{name}', val)
    except Exception:
        pass

    try:
        cmds.setAttr(f'{node}.{name}', e=True, k=bool(ua.get('keyable', True)))
    except Exception:
        pass
    try:
        cmds.setAttr(f'{node}.{name}', e=True, cb=bool(ua.get('channelBox', False)))
    except Exception:
        pass
    try:
        cmds.setAttr(f'{node}.{name}', l=bool(ua.get('locked', False)))
    except Exception:
        pass


def _restore_limits(node, lim):
    for axis in ('x', 'y', 'z'):
        for prefix, label in (('t', 'translate'), ('r', 'rotate')):
            key = f'{prefix}{axis}'
            if key not in lim:
                continue
            d = lim[key]
            try:
                cmds.setAttr(f'{node}.{label}{axis.upper()}LimitEnable', d['enableMin'], d['enableMax'])
                cmds.setAttr(f'{node}.{label}{axis.upper()}LimitMin', d['min'])
                cmds.setAttr(f'{node}.{label}{axis.upper()}LimitMax', d['max'])
            except Exception:
                pass
