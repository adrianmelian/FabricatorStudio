# curve_o_matic_app.py
# Curve-O-Matic — headless API. No UI imports.
__author__ = "Adrian Melian"

import json
from pathlib import Path

import maya.cmds as cmds
import maya.api.OpenMaya as om

from maya_tools.framework.decorators import undo_chunk
from maya_tools.utils.maya.side_tokens import CTRL_COLOR_RGB


CURVE_DATA_DIR = Path(__file__).parent / 'curve_data'


def get_all_shapes() -> list[str]:
    """Sorted list of all available shape names."""
    return sorted(p.stem for p in CURVE_DATA_DIR.glob('*.json'))


def build_shape(name: str, ctrl_name: str, radius: float = 1.0) -> str:
    """Build a NURBS control curve by shape name. Returns the transform node name.

    radius scales the curve's CVs in object space (the transform's TRS stays at
    identity), so a shape authored to fit a radius-1 joint comes out pre-sized to
    the joint it drives — no scale to freeze afterward. Default 1.0 = no scaling,
    so every existing caller is unaffected.
    """
    path = CURVE_DATA_DIR / f'{name}.json'
    if not path.exists():
        raise RuntimeError(f"Shape not found: '{name}'  (looked in {CURVE_DATA_DIR})")
    data = json.loads(path.read_text())
    ctrl = _build_transform_from_data(data, ctrl_name)
    if radius and radius > 0 and radius != 1.0:
        _scale_curve_cvs(ctrl, radius)
    return ctrl


def _scale_curve_cvs(transform: str, factor: float) -> None:
    """Scale every nurbsCurve CV under `transform` by `factor` in object space.
    Leaves the transform identity — the size lives in the CVs, so the result is
    a pre-scaled shape with nothing to freeze."""
    for shape in cmds.listRelatives(transform, shapes=True, fullPath=True) or []:
        if cmds.nodeType(shape) != 'nurbsCurve':
            continue
        try:
            sel = om.MSelectionList()
            sel.add(shape)
            fn = om.MFnNurbsCurve(sel.getDagPath(0))
            pts = fn.cvPositions()
            fn.setCVPositions(om.MPointArray([
                om.MPoint(p.x * factor, p.y * factor, p.z * factor) for p in pts
            ]))
            fn.updateCurve()
        except Exception:
            pass


def build_shape_at_selection(name: str, ctrl_name: str) -> str:
    """Build shape then snap it to the first selected object. Falls back to origin if nothing selected."""
    ctrl = build_shape(name, ctrl_name)
    sel = cmds.ls(selection=True) or []
    if sel:
        cmds.matchTransform(ctrl, sel[0], position=True, rotation=True, scale=False)
    return ctrl


def _flatten_cvs_to_world(tmp: str, ctrl: str) -> None:
    """Counter-rotate tmp's nurbsCurve CVs by ctrl's inverse WORLD rotation, so
    that once the shapes are parented under the (possibly rotated) ctrl the
    ctrl's own rotation cancels out and the curve reads axis-aligned in world
    (flat in an iso/ortho view)."""
    wm = om.MMatrix(cmds.getAttr(f'{ctrl}.worldMatrix[0]'))
    inv_rot = om.MTransformationMatrix(wm).rotation(asQuaternion=True).inverse()
    for shape in cmds.listRelatives(tmp, shapes=True, type='nurbsCurve',
                                    fullPath=True) or []:
        try:
            sel = om.MSelectionList()
            sel.add(shape)
            fn = om.MFnNurbsCurve(sel.getDagPath(0))
            pts = fn.cvPositions()
            fn.setCVPositions(om.MPointArray([
                om.MPoint(om.MVector(p).rotateBy(inv_rot)) for p in pts
            ]))
            fn.updateCurve()
        except Exception:
            pass


def flatten_ctrl_shape_to_world(ctrl: str) -> None:
    """Counter-rotate ctrl's OWN nurbsCurve shape CVs by its inverse world
    rotation — the build-time twin of swap_shape(worldspace=True). The
    transform (and everything behavioral riding it) keeps its orientation;
    only the DRAWN shape reads axis-aligned in world space (Adrian,
    2026-07-11: RibbonSpine hip/COG/chest shapes world-flat at build).

    NOT idempotent: each call counter-rotates the current CVs again, so
    call it exactly once, on a freshly built library shape — never on a
    shape that was already flattened or carries authored CV edits.
    """
    if not cmds.objExists(ctrl):
        return
    _flatten_cvs_to_world(ctrl, ctrl)


def swap_shape(name: str, ctrl: str, worldspace: bool = False) -> None:
    """Replace ctrl's nurbsCurve shape nodes with shapes from the named library entry.

    Transform identity is preserved — only the shape nodes are swapped, so all
    incoming/outgoing connections on the TRANSFORM (constraints, message links,
    keyframes, anim layers, etc.) survive intact. Shape-level state does NOT
    survive on its own (the old shape nodes are deleted), so it is captured and
    re-applied via _capture_shape_state/_apply_shape_state — override colour,
    override display type, and the incoming .visibility connection the rig's
    IK/FK switching drives. See _capture_shape_state for why that matters.

    worldspace=True counter-rotates the incoming CVs by the ctrl's inverse world
    rotation, so the swapped curve reads axis-aligned in WORLD space (flat in an
    iso/ortho view), centered on the ctrl's position but ignoring the ctrl/joint
    rotation. The ctrl still sits at the joint, so position is snapped; only the
    rotation is dropped.
    """
    if not cmds.objExists(ctrl):
        raise RuntimeError(f"Control not found: '{ctrl}'")

    old_shapes = cmds.listRelatives(ctrl, shapes=True, type='nurbsCurve', fullPath=True) or []

    shape_state = _capture_shape_state(old_shapes)

    tmp = build_shape(name, '_com_swap_tmp')
    tmp_shapes = cmds.listRelatives(tmp, shapes=True, type='nurbsCurve', fullPath=True) or []
    if not tmp_shapes:
        cmds.delete(tmp)
        raise RuntimeError(f"Library shape '{name}' has no nurbsCurve shapes.")

    if worldspace:
        _flatten_cvs_to_world(tmp, ctrl)

    cmds.parent(tmp_shapes, ctrl, shape=True, relative=True)
    if old_shapes:
        cmds.delete(old_shapes)
    if cmds.objExists(tmp):
        cmds.delete(tmp)

    ctrl_short = ctrl.split('|')[-1]
    new_shapes = cmds.listRelatives(ctrl, shapes=True, type='nurbsCurve', fullPath=True) or []
    _apply_shape_state(new_shapes, shape_state)
    for i, s in enumerate(new_shapes):
        suffix = '' if i == 0 else str(i)
        cmds.rename(s, f'{ctrl_short}Shape{suffix}')


# ─────────────────────────────────────────────────────────────────────────────
# Serialize / deserialize — single source of truth for NURBS shape data
# ─────────────────────────────────────────────────────────────────────────────

def serialize_shape(transform: str) -> dict:
    """Return shape data for all NURBS shapes under transform.

    Schema:
        {'shapes': [{'degree': int, 'cvs': [[x,y,z], ...], 'knots'?: [...], 'periodic'?: True}, ...]}

    Multi-shape transforms (e.g. cog, arrow_double) produce one entry per shape.
    Uses MFnNurbsCurve so degree, knots, and periodicity are recorded accurately.

    Raises RuntimeError if transform has no nurbsCurve shapes.
    """
    if not cmds.objExists(transform):
        raise RuntimeError(f"Transform not found: '{transform}'")
    shape_nodes = cmds.listRelatives(transform, shapes=True, type='nurbsCurve',
                                     fullPath=True) or []
    if not shape_nodes:
        raise RuntimeError(f'No nurbsCurve shapes found under {transform}.')
    return {'shapes': [_extract_one_shape(s) for s in shape_nodes]}


def deserialize_shape_to(transform: str, data: dict) -> None:
    """Replace transform's NURBS shapes with shapes from data.

    Deletes existing nurbsCurve shapes, creates new ones built from data['shapes'],
    parents them under transform. Transform identity (and all its connections)
    are preserved. Shape-level state (override colour and display type, plus any
    incoming .visibility connection from rig IK/FK vis switching) is captured
    from the existing shapes and reapplied to every new one — see
    _capture_shape_state, shared verbatim with swap_shape.
    """
    if not cmds.objExists(transform):
        raise RuntimeError(f"Transform not found: '{transform}'")
    if not data.get('shapes'):
        raise RuntimeError('deserialize_shape_to: data has no shapes')

    old_shapes = cmds.listRelatives(transform, shapes=True, type='nurbsCurve',
                                    fullPath=True) or []
    shape_state = _capture_shape_state(old_shapes)

    # Build a temp transform from the data, then steal its shapes
    tmp = _build_transform_from_data(data, '_com_deser_tmp')
    tmp_shapes = cmds.listRelatives(tmp, shapes=True, type='nurbsCurve',
                                    fullPath=True) or []
    if not tmp_shapes:
        cmds.delete(tmp)
        raise RuntimeError('deserialize_shape_to: built transform had no shapes')

    cmds.parent(tmp_shapes, transform, shape=True, relative=True)
    if old_shapes:
        cmds.delete(old_shapes)
    if cmds.objExists(tmp):
        cmds.delete(tmp)

    # Reapply colour + visibility wiring + sane shape names
    transform_short = transform.split('|')[-1]
    new_shapes = cmds.listRelatives(transform, shapes=True, type='nurbsCurve',
                                    fullPath=True) or []
    _apply_shape_state(new_shapes, shape_state)
    for i, s in enumerate(new_shapes):
        suffix = '' if i == 0 else str(i)
        cmds.rename(s, f'{transform_short}Shape{suffix}')


def save_shape_from_selection(name: str) -> Path:
    """Extract NURBS curve data from the selected transform(s) and save to
    curve_data/{name}.json. Overwrites if the file already exists.

    Multi-selection combines every selected transform's nurbsCurve shapes into
    one library entry. Each transform's world matrix is baked into its CVs so
    the saved layout matches what's visible in the viewport — selecting three
    arrow curves arranged as an XYZ aimer saves a single composed XYZ shape.
    """
    sel = cmds.ls(selection=True, transforms=True) or []
    if not sel:
        raise RuntimeError('Select one or more transforms with nurbsCurve shapes.')

    if len(sel) == 1:
        data = serialize_shape(sel[0])
    else:
        all_shapes: list = []
        for xform in sel:
            shape_nodes = cmds.listRelatives(xform, shapes=True, type='nurbsCurve',
                                             fullPath=True) or []
            for s in shape_nodes:
                all_shapes.append(_extract_one_shape(s, space=om.MSpace.kWorld))
        if not all_shapes:
            raise RuntimeError('No nurbsCurve shapes found on selection.')
        data = {'shapes': all_shapes}

    data['display_name'] = name.replace('_', ' ').title()
    out_path = CURVE_DATA_DIR / f'{name}.json'
    out_path.write_text(json.dumps(data, indent=2))
    return out_path


def combine_curves(transforms: list[str]) -> str:
    """Combine multiple curve transforms into a single transform.

    The LAST transform in `transforms` is the merge target. Each earlier
    transform has its world TRS frozen into its CVs and its nurbsCurve
    shape nodes re-parented under the target; the now-empty source
    transform is deleted. The target is also frozen first so its
    worldMatrix is identity — that keeps the merged shapes visually in
    place after re-parenting (CV coords in target's local space = CV
    coords in worldspace).

    Returns the target transform name.

    Raises RuntimeError if fewer than 2 transforms are supplied or any
    transform has no nurbsCurve shapes.
    """
    if len(transforms) < 2:
        raise RuntimeError(
            f'combine_curves: need 2+ transforms; got {len(transforms)}'
        )
    for t in transforms:
        if not cmds.objExists(t):
            raise RuntimeError(f'combine_curves: transform missing: {t!r}')
        if not (cmds.listRelatives(t, shapes=True, type='nurbsCurve',
                                   fullPath=True) or []):
            raise RuntimeError(
                f'combine_curves: {t!r} has no nurbsCurve shapes'
            )

    target = transforms[-1]
    sources = transforms[:-1]

    cmds.makeIdentity(target, apply=True,
                      translate=True, rotate=True, scale=True)
    for src in sources:
        cmds.makeIdentity(src, apply=True,
                          translate=True, rotate=True, scale=True)
        shapes = cmds.listRelatives(src, shapes=True, type='nurbsCurve',
                                    fullPath=True) or []
        for s in shapes:
            cmds.parent(s, target, shape=True, relative=True)
        if not (cmds.listRelatives(src, children=True, fullPath=True) or []):
            cmds.delete(src)
    return target


def combine_selected_curves() -> str:
    """Combine the currently-selected curve transforms into one.

    Selection order matters — the last-selected transform is the merge
    target. Convenience wrapper over `combine_curves`.
    """
    sel = cmds.ls(selection=True, transforms=True) or []
    if len(sel) < 2:
        raise RuntimeError(
            'Select 2+ transforms with nurbsCurve shapes; the last-selected '
            'transform receives the merged shapes.'
        )
    return combine_curves(sel)


def delete_shape(name: str) -> None:
    """Delete a shape JSON file. Raises if the file does not exist."""
    path = CURVE_DATA_DIR / f'{name}.json'
    if not path.exists():
        raise RuntimeError(f"Shape not found: '{name}'")
    path.unlink()


def rename_shape(old_name: str, new_name: str) -> None:
    """Rename a shape JSON file. Raises if old does not exist or new already exists."""
    old_path = CURVE_DATA_DIR / f'{old_name}.json'
    new_path = CURVE_DATA_DIR / f'{new_name}.json'
    if not old_path.exists():
        raise RuntimeError(f"Shape not found: '{old_name}'")
    if new_path.exists():
        raise RuntimeError(f"Shape already exists: '{new_name}'")
    old_path.rename(new_path)


# ── color ───────────────────────────────────────────────────────────────────

def color_names() -> list[str]:
    """The named ctrl-color palette, shared with Fabricator (side_tokens)."""
    return list(CTRL_COLOR_RGB)


def color_rgb(name: str) -> tuple:
    """RGB (0..1) for a palette color name. Falls back to yellow."""
    return CTRL_COLOR_RGB.get(name, CTRL_COLOR_RGB['yellow'])


def apply_curve_color(color_name: str, transforms: list[str] = None,
                      save_to_rig: bool = True) -> dict:
    """Set the override color on each transform's nurbsCurve shapes, the same
    way Fabricator colors its ctrls (overrideRGBColors), so live color and
    rebuild color match exactly.

    For KS rig ctrls (fab_owner → component node), the owning component's
    ctrl_color option is also updated when save_to_rig is True, so a rebuild
    restores the color. Fabricator stores one ctrl_color per component, so
    recoloring any ctrl of a multi-ctrl component sets the whole component's
    color on rebuild.

    transforms: explicit list, or None to use the live selection (transforms
                carrying nurbsCurve shapes).
    Returns {'color', 'recolored', 'saved', 'targets'}. Raises on an unknown
    color name or an empty target set.
    """
    if color_name not in CTRL_COLOR_RGB:
        raise RuntimeError(
            f"Unknown color: {color_name!r}. Valid: {', '.join(CTRL_COLOR_RGB)}.")

    if transforms is None:
        transforms = [t for t in (cmds.ls(selection=True, transforms=True) or [])
                      if cmds.listRelatives(t, shapes=True, type='nurbsCurve')]
    if not transforms:
        raise RuntimeError(
            'Select one or more control curves (transforms with nurbsCurve '
            'shapes) in the viewport first.')

    rgb = CTRL_COLOR_RGB[color_name]
    recolored: list[str] = []
    saved_components: set[str] = set()
    with undo_chunk(f'Apply Curve Color: {color_name}'):
        for t in transforms:
            shapes = cmds.listRelatives(t, shapes=True, type='nurbsCurve',
                                        fullPath=True) or []
            if not shapes:
                continue
            for s in shapes:
                cmds.setAttr(f'{s}.overrideEnabled', True)
                cmds.setAttr(f'{s}.overrideRGBColors', True)
                cmds.setAttr(f'{s}.overrideColorR', rgb[0])
                cmds.setAttr(f'{s}.overrideColorG', rgb[1])
                cmds.setAttr(f'{s}.overrideColorB', rgb[2])
            recolored.append(t)
            if save_to_rig:
                cnode = _save_ctrl_color_to_rig(t, color_name)
                if cnode:
                    saved_components.add(cnode)

    return {
        'color': color_name,
        'recolored': len(recolored),
        'saved': len(saved_components),
        'targets': recolored,
    }


# ── private ───────────────────────────────────────────────────────────────────

_OVERRIDE_COLOR_ATTRS = (
    'overrideEnabled', 'overrideRGBColors',
    'overrideColorR', 'overrideColorG', 'overrideColorB',
    'overrideColor',
)

# Everything carried across a shape replacement. The colour set plus
# overrideDisplayType (the template flag SimpleIK stamps on the PV guide
# line) and lodVisibility. Deliberately NOT 'visibility' itself: when the
# rig drives it, the captured value is whatever mode the switch happened
# to be in, and baking that onto a ctrl whose vis connection could not be
# restored would leave it invisible with no way back short of a rebuild.
_SHAPE_CARRY_ATTRS = _OVERRIDE_COLOR_ATTRS + (
    'overrideDisplayType', 'lodVisibility',
)


def _capture_override_color(shape: str, attrs: tuple = None) -> dict:
    """Best-effort read of an attr set from a shape node.

    Returns a dict of attr_name -> value for every attr that read successfully.
    Missing or query-failing attrs are silently skipped (Maya version drift).
    Defaults to the override-colour set; callers pass _SHAPE_CARRY_ATTRS for
    the full shape-replacement carry set.
    """
    out: dict = {}
    for attr in (attrs or _OVERRIDE_COLOR_ATTRS):
        try:
            out[attr] = cmds.getAttr(f'{shape}.{attr}')
        except Exception:
            pass
    return out


def _apply_override_color(shapes: list[str], color_attrs: dict) -> None:
    """Best-effort apply of a captured attr set to shapes."""
    for s in shapes:
        for attr, val in color_attrs.items():
            try:
                cmds.setAttr(f'{s}.{attr}', val)
            except Exception:
                pass


def _capture_shape_state(old_shapes: list[str]) -> dict:
    """Capture the shape-level state that must survive a shape replacement.

    Both shape-replacing paths (swap_shape and deserialize_shape_to) delete
    the old nurbsCurve shape nodes, which destroys everything living ON those
    nodes. Two things have to come back:

    - Override colour and display type, so a swapped ctrl keeps its colour and
      a templated curve stays templated.
    - The incoming .visibility connection. Rig switching (the IK/FK vis
      conditions in Fabricator's SimpleIK and the ribbon limbs) drives ctrl
      visibility on the SHAPE nodes so the transform's channels stay clean for
      animators. Deleting the shapes severs it, leaving FK ctrls drawn in IK
      mode (Adrian's R-arm bug 2026-07-12; reported again from the field
      2026-07-28 against the Ctrl Editor's Swap button, which is how we
      learned deserialize_shape_to had been fixed and swap_shape had not).

    That second fix living in exactly one of the two functions is what caused
    the field report, so both now go through this pair. Any future
    shape-replacing function must too.

    Returns {'attrs': {...}, 'vis_src': '<plug>' or ''}. Empty/degenerate
    input yields an empty state that _apply_shape_state no-ops on.
    """
    state: dict = {'attrs': {}, 'vis_src': ''}
    if not old_shapes:
        return state
    state['attrs'] = _capture_override_color(old_shapes[0], _SHAPE_CARRY_ATTRS)
    for s in old_shapes:
        conns = cmds.listConnections(f'{s}.visibility', source=True,
                                     destination=False, plugs=True) or []
        if conns:
            state['vis_src'] = conns[0]
            break
    return state


def _apply_shape_state(new_shapes: list[str], state: dict) -> None:
    """Re-apply a _capture_shape_state result to freshly built shapes.

    Attrs go on first, the visibility connection second, so a driven
    visibility always wins over any static value that came along with the
    attr set. The source node is re-checked for existence: swapping a ctrl on
    a rig that is mid-unbuild degrades to 'no vis wiring' rather than raising.
    """
    if not state:
        return
    _apply_override_color(new_shapes, state.get('attrs') or {})
    src = state.get('vis_src') or ''
    if src and cmds.objExists(src.split('.')[0]):
        for s in new_shapes:
            try:
                cmds.connectAttr(src, f'{s}.visibility', force=True)
            except Exception:
                pass


def _save_ctrl_color_to_rig(ctrl: str, color_name: str) -> str:
    """If ctrl is a Fabricator ctrl, write its owning component's ctrl_color
    option so a rebuild restores the color. Returns the component node name,
    or '' when ctrl isn't a KS rig ctrl.

    Lazy-imports fabricator.nodes — Fabricator imports this module for shape
    building, so a module-level import here would be a cycle.
    """
    if not cmds.attributeQuery('fab_owner', node=ctrl, exists=True):
        return ''
    owners = cmds.listConnections(f'{ctrl}.fab_owner',
                                  source=True, destination=False) or []
    if not owners:
        return ''
    cnode = owners[0]
    from maya_tools.rigging.fabricator import nodes as fab_nodes
    options = fab_nodes.get_component_options(cnode)
    options['ctrl_color'] = color_name
    fab_nodes.set_component_options(cnode, options)
    return cnode


def _build_transform_from_data(data: dict, ctrl_name: str) -> str:
    """Build a transform with all shapes from a serialised data dict."""
    shapes = data.get('shapes', [])
    if not shapes:
        raise RuntimeError('No shapes defined in data')

    def _make_curve(shape_data: dict, name: str) -> str:
        kwargs: dict = {
            'name':   name,
            'degree': shape_data['degree'],
            'point':  shape_data['cvs'],
        }
        if 'knots' in shape_data:
            kwargs['knot'] = shape_data['knots']
        if shape_data.get('periodic'):
            kwargs['periodic'] = True
        return cmds.curve(**kwargs)

    first = _make_curve(shapes[0], ctrl_name)
    for shape_data in shapes[1:]:
        tmp = _make_curve(shape_data, '_com_tmp_curve')
        tmp_shapes = cmds.listRelatives(tmp, shapes=True, fullPath=True) or []
        cmds.parent(tmp_shapes, first, shape=True, relative=True)
        cmds.delete(tmp)
    return first


def _extract_one_shape(shape_node: str, space: int | None = None) -> dict:
    """Extract one NURBS curve shape's data via MFnNurbsCurve.

    space defaults to MSpace.kObject. Pass MSpace.kWorld to bake the parent
    transform into the CVs (used by multi-select save so composed layouts
    survive the round-trip into a single rebuilt transform).
    """
    if space is None:
        space = om.MSpace.kObject
    sel = om.MSelectionList()
    sel.add(shape_node)
    fn = om.MFnNurbsCurve(sel.getDagPath(0))

    degree = fn.degree
    cvs = [[round(p.x, 4), round(p.y, 4), round(p.z, 4)]
           for p in fn.cvPositions(space)]
    result: dict = {'degree': degree, 'cvs': cvs}
    if degree > 1:
        result['knots'] = [round(k, 6) for k in fn.knots()]
    if fn.form == om.MFnNurbsCurve.kPeriodic:
        result['periodic'] = True
    return result


def swap_shape_on_selection(name):
    """Swap every selected curve ctrl to library shape *name*, one undo step.
    Replicates the UI loop (curve_o_matic_ui._on_swap) headlessly; reselects."""
    sel = cmds.ls(selection=True, transforms=True) or []
    ctrls = [t for t in sel
             if cmds.listRelatives(t, shapes=True, type="nurbsCurve")]
    if not ctrls:
        raise RuntimeError("Select one or more curve controls")
    with undo_chunk(f"Swap Shape: {name}"):
        for ctrl in ctrls:
            swap_shape(name, ctrl)
    cmds.select(ctrls, replace=True)
    return ctrls
