# maya_tools/framework/toolbar/ai_bridge/handlers.py
"""The AI bridge's op registry — THE READ-ONLY CONTRACT LIVES HERE.

Every function reachable through OPS is a pure read of the Maya scene:
it inspects state and returns data, and never writes an attribute,
creates or deletes a node, changes a connection, renames or reparents
anything, or saves the file — and never invokes a BuildIssue's auto-fix
callable. This is the product's central trust artifact — the public
claim is "the AI can see, never touch — auditable by reading this
file." Adding any mutating operation to OPS, or having a handler
mutate the scene as a side effect, is a product-contract violation,
full stop.

The one narrow exception is scene_report's use of
scene_cleanup_app.select_mesh_artifacts(), which — as its own
docstring says — replaces the live Maya selection with the problem
components it finds. Selection is UI state, not scene data; this
handler saves it before calling in and restores it exactly afterward,
so the op is read-only with respect to everything that matters (the
scene graph, its attributes, its connections) even though the user's
*selection highlight* flickers during the call.

Two further disclosed exceptions to "reads only the scene", both
narrow and neither a scene mutation: viewport_screenshot writes one
throwaway PNG to a temp directory (filesystem, not the scene) and
deletes it after reading its bytes back; and node_details' per-attr
getAttr() forces Maya's dependency graph to evaluate up to that plug —
in a scene that itself contains a malicious expression node or impure
plug-in node upstream of the queried attribute, that evaluation can
trigger a side effect. That risk is inherent to opening such a scene
in Maya at all (any read that touches the DG shares it); this bridge
does not introduce it, widen it, or evaluate anything beyond what the
caller explicitly asked to read.

dispatch() does no exception handling of its own: handlers raise
BridgeError for expected, nameable failures (bad params, nothing
found, no viewport); anything else is a genuine bug and propagates
unchanged for the service drain (Task 5) to fold into an 'internal'
error line. No handler in this module ever turns an op-level failure
into a fake success — the only exceptions caught anywhere below are
_toolset_version()'s version-import fallback, node_details' per-attr
getAttr() probes (a failed probe is reported as attr_values[attr] =
None, not swallowed silently), and viewport_screenshot's best-effort
temp-file/temp-dir cleanup.
"""
__author__ = "Adrian Melian"

import base64
import tempfile
from pathlib import Path

import maya.cmds as cmds

from maya_tools.framework.toolbar.ai_bridge.protocol import BridgeError, hello_result
from maya_tools.framework.scene_cleanup import scene_cleanup_app
from maya_tools.rigging.fabricator import build_checks
from maya_tools.rigging.fabricator import build_report as build_report_mod
from maya_tools.rigging.fabricator import introspect

_DOCS_DIR = Path(__file__).resolve().parents[3] / 'docs' / 'assistant'
_SKILL_PATH = (Path(__file__).resolve().parents[3] / 'skills'
              / 'fabricator-assistant' / 'SKILL.md')
_MAX_LIST = 200


def _bounded(seq, cap: int = _MAX_LIST) -> dict:
    """Cap an unbounded list for the wire: {'items': [...], 'truncated': bool}.
    Every list of scene-derived, possibly-huge cardinality (children,
    connections, selection) must pass through this before going in a
    result dict — that is what keeps a single query from producing a
    too_large response on a heavy scene."""
    items = list(seq)
    return {'items': items[:cap], 'truncated': len(items) > cap}


def _bounded_mapping(mapping: dict, cap: int = _MAX_LIST) -> dict:
    """Same disclosed-partial contract as _bounded(), for a {key: [...]}
    mapping (find_duplicate_transforms' {short_name: [long_paths]})
    instead of a plain sequence. Caps the number of ENTRIES (not the
    total path count folded inside them) and reports the true entry
    count as 'total' alongside the capped 'items' — the scene most in
    need of this op (a bad-import scene riddled with name collisions)
    is exactly the one where an unbounded version would blow the size
    cap."""
    items = dict(mapping)
    keys = list(items)
    capped = {k: items[k] for k in keys[:cap]}
    return {'items': capped, 'truncated': len(keys) > cap, 'total': len(keys)}


def _toolset_version() -> str:
    """FABRICATOR_VERSION, imported lazily for the same reason
    protocol.hello_result() imports it lazily: this keeps handlers.py's
    own module-level import list light, and a future failure in that
    import chain degrades to 'unknown' rather than breaking every op
    that reports a version."""
    try:
        from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION
        return FABRICATOR_VERSION
    except Exception:
        return 'unknown'


def _all_mesh_transforms() -> list:
    """Every mesh transform in the scene, independent of selection —
    the read scene_report needs (an artifact scan of the whole scene),
    as opposed to select_mesh_artifacts' own default of scanning
    whatever happens to be selected."""
    # Armature ctrl balls are meshes (they need mesh.alwaysDrawOnTop to draw
    # through the character), so a bare cmds.ls(type='mesh') would report fifty
    # rig ctrls to the assistant as scene geometry.
    from maya_tools.framework import utilities as utils
    shapes = utils.scene_mesh_shapes()
    transforms = set()
    for shape in shapes:
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        transforms.update(parents)
    return sorted(transforms)


def _hello(params: dict) -> dict:
    return hello_result()


def _scene_summary(params: dict) -> dict:
    counts = {}
    for node_type in ('transform', 'joint', 'mesh', 'skinCluster',
                      'constraint', 'network'):
        counts[node_type] = len(cmds.ls(type=node_type) or [])

    return {
        'scene_path':       cmds.file(query=True, sceneName=True) or '',
        'unsaved_changes':  bool(cmds.file(query=True, modified=True)),
        'units_linear':     cmds.currentUnit(query=True, linear=True),
        'up_axis':          cmds.upAxis(query=True, axis=True),
        'fps':              cmds.currentUnit(query=True, time=True),
        'selection':        _bounded(cmds.ls(selection=True, long=True) or []),
        'counts':           counts,
        'maya_version':     cmds.about(version=True),
        'toolset_version':  _toolset_version(),
        'os':               cmds.about(operatingSystem=True),
    }


def _node_details(params: dict) -> dict:
    node = params.get('node')
    if not isinstance(node, str) or not node:
        raise BridgeError('bad_params', "Param 'node' must be a non-empty string.")
    if not cmds.objExists(node):
        raise BridgeError('not_found', f"No such node: {node!r}.")

    attrs = params.get('attrs') or []
    if not isinstance(attrs, list) or not all(isinstance(a, str) for a in attrs):
        raise BridgeError('bad_params',
                          "Param 'attrs' must be a list of attribute name "
                          "strings.")

    parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
    children = cmds.listRelatives(node, children=True, fullPath=True) or []

    attrs_truncated = len(attrs) > 50
    attr_values = {}
    for attr in attrs[:50]:
        try:
            attr_values[attr] = cmds.getAttr(f'{node}.{attr}')
        except Exception:
            attr_values[attr] = None

    conns_in = cmds.listConnections(node, source=True, destination=False,
                                   plugs=True) or []
    conns_out = cmds.listConnections(node, source=False, destination=True,
                                     plugs=True) or []

    return {
        'node':             node,
        'type':             cmds.nodeType(node),
        'parent':           parents[0] if parents else None,
        'children':         _bounded(children),
        'attr_values':      attr_values,
        'attrs_truncated':  attrs_truncated,
        'connections_in':   _bounded(conns_in),
        'connections_out':  _bounded(conns_out),
    }


def _viewport_screenshot(params: dict) -> dict:
    if cmds.about(batch=True):
        raise BridgeError('not_found', 'No viewport in batch/standalone mode.')

    width = params.get('width', 960)
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise BridgeError('bad_params', "Param 'width' must be a positive int.")
    width = min(width, 1280)
    height = int(width * 9 / 16)

    tmp_dir = Path(tempfile.mkdtemp(prefix='ai_bridge_shot_'))
    requested = tmp_dir / 'shot.png'
    try:
        # playblast's own return value is the file it actually wrote —
        # NOT necessarily completeFilename verbatim (image playblasts
        # commonly land with a frame-number infix). Trust the return,
        # not the request.
        actual = cmds.playblast(
            frame=[cmds.currentTime(query=True)],
            format='image',
            compression='png',
            viewer=False,
            offScreen=True,
            widthHeight=[width, height],
            percent=100,
            quality=100,
            showOrnaments=False,
            forceOverwrite=True,
            completeFilename=str(requested),
        )
        actual_path = Path(actual) if actual else requested
        data = actual_path.read_bytes()
    finally:
        for leftover in tmp_dir.glob('*'):
            try:
                leftover.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass

    return {'png_base64': base64.b64encode(data).decode('ascii'), 'width': width}


def _fabricator_status(params: dict) -> dict:
    return introspect.fabricator_status()


def _describe_rig(params: dict) -> dict:
    """Post-processes introspect.rig_snapshot()'s return value to bound
    its two largest lists (introspect itself stays untouched — this is
    a handler-side wrapping, not a introspect API change): skeleton
    joints and components_live. Ribbon-limb rigs in particular can carry
    very large joint counts, making these the biggest lists on the whole
    op surface — left unbounded, one of them could blow the response's
    256k size cap and hand the AI nothing instead of a disclosed partial
    list, same as every other _bounded() list on this surface."""
    try:
        data = introspect.rig_snapshot()
    except introspect.NoRegistryError as exc:
        raise BridgeError('not_found', str(exc)) from exc

    skeleton = dict(data.get('skeleton') or {})
    skeleton['joints'] = _bounded(skeleton.get('joints') or [])
    data['skeleton'] = skeleton
    data['components_live'] = _bounded(data.get('components_live') or [])
    return data


def _component_details(params: dict) -> dict:
    """Same post-process bounding as _describe_rig, for the two list
    fields on a single component (joints, joint_names) - introspect's
    own component_details() return is untouched otherwise."""
    component_id = params.get('component_id')
    if not isinstance(component_id, str) or not component_id:
        raise BridgeError('bad_params',
                          "Param 'component_id' must be a non-empty string.")
    try:
        data = introspect.component_details(component_id)
    except introspect.NoRegistryError as exc:
        raise BridgeError('not_found', str(exc)) from exc
    except KeyError as exc:
        # KeyError's own str() re-reprs its single arg (extra quoting) -
        # unwrap args[0] so the bridge's error message reads cleanly.
        message = exc.args[0] if exc.args else str(exc)
        raise BridgeError('not_found', message) from exc

    data['joints'] = _bounded(data.get('joints') or [])
    data['joint_names'] = _bounded(data.get('joint_names') or [])
    return data


def _rig_binding(params: dict) -> dict:
    summaries = introspect.binding_summaries()
    rig_label = params.get('rig_label')
    if rig_label:
        summaries = [s for s in summaries if s.get('rig_label') == rig_label]
    return {'bindings': summaries}


def _build_checks(params: dict) -> dict:
    issues = build_checks.run_prebuild_checks()
    return {'issues': [
        {
            'key':          issue.key,
            'title':        issue.title,
            'description':  issue.description,
            'severity':     issue.severity,
            'fixable':      issue.fixable,
            'skippable':    issue.skippable,
            'fix_label':    issue.fix_label,
        }
        for issue in issues
    ]}


def _validate_blueprint(params: dict) -> dict:
    try:
        return {'messages': introspect.validate_live_blueprint()}
    except introspect.NoRegistryError as exc:
        raise BridgeError('not_found', str(exc)) from exc


def _scene_report(params: dict) -> dict:
    """duplicate_transforms scans every transform in the scene (cheap - a
    name-collision check, no per-mesh work) but its RESULT is still
    capped via _bounded_mapping (by entry count, not total path count) -
    the scene most in need of this op is exactly the worst-import scene
    with the most collisions. mesh_artifacts is the costlier half:
    polyInfo runs per mesh, so the mesh list hunted for artifacts is
    capped at _MAX_LIST (200) mesh transforms by design.
    meshes_submitted / truncated report exactly how many mesh transforms
    were HANDED to select_mesh_artifacts, not how many were successfully
    inspected — that function silently `continue`s past (cmds.warning
    only, no count returned) any mesh whose polyInfo call raises, and
    those internal skips are not separately counted here. A clean
    non-truncated scan still cannot promise every submitted mesh was
    actually checked, only that all of them were attempted."""
    duplicate_transforms = _bounded_mapping(
        scene_cleanup_app.find_duplicate_transforms())

    all_mesh_transforms = _all_mesh_transforms()
    mesh_transforms = all_mesh_transforms[:_MAX_LIST]
    truncated = len(all_mesh_transforms) > _MAX_LIST

    saved_selection = cmds.ls(selection=True, long=True) or []
    try:
        if mesh_transforms:
            mesh_artifacts = scene_cleanup_app.select_mesh_artifacts(mesh_transforms)
        else:
            mesh_artifacts = {'non_manifold_edges': 0, 'non_manifold_verts': 0,
                              'lamina_faces': 0}
    finally:
        # select_mesh_artifacts REPLACES the live selection with whatever
        # problem components it finds (see its own docstring) - restore
        # the caller's selection exactly, whether that was something or
        # nothing. This is the ONLY place this module touches cmds.select,
        # and it exists purely to undo the side effect of a function this
        # module calls, not to make one of its own; a mutation-guard test
        # pins the exact occurrence count so a future edit can't quietly
        # add a second one.
        if saved_selection:
            cmds.select(saved_selection, replace=True)
        else:
            cmds.select(clear=True)

    mesh_artifacts = dict(mesh_artifacts)
    mesh_artifacts['meshes_submitted'] = len(mesh_transforms)
    mesh_artifacts['truncated'] = truncated

    return {'duplicate_transforms': duplicate_transforms,
            'mesh_artifacts': mesh_artifacts}


def _build_report(params: dict) -> dict:
    report = build_report_mod.read_report()
    if report is None:
        raise BridgeError('not_found',
                          'No build report stamped in this scene yet '
                          '(no build has run).')
    return report


def _diagnostics(params: dict) -> dict:
    status = introspect.fabricator_status()
    issues = build_checks.run_prebuild_checks()
    errors = sum(1 for issue in issues if issue.severity == 'error')
    warnings = sum(1 for issue in issues if issue.severity == 'warning')

    report = build_report_mod.read_report()
    if report is None:
        last_build = None
    else:
        last_build = {
            'action':      report.get('action', ''),
            'outcome':     report.get('outcome', ''),
            'stamped_at':  report.get('stamped_at', ''),
        }

    return {
        'toolset_version':    _toolset_version(),
        'maya_version':       cmds.about(version=True),
        'os':                 cmds.about(operatingSystem=True),
        'mode':               status['mode'],
        'stale_built_flags':  status['stale_built_flags'],
        'has_registry':       status['has_registry'],
        'rig_label':          status['rig_label'],
        'check_summary': {
            'errors':   errors,
            'warnings': warnings,
            'keys':     [issue.key for issue in issues],
        },
        'last_build': last_build,
    }


def _read_doc(params: dict) -> dict:
    """name must be a plain stem: no path separators, no colon (Windows
    drive letters make 'C:foo' a DRIVE-RELATIVE path — Path(_DOCS_DIR) /
    'C:foo.md' resolves OUTSIDE _DOCS_DIR entirely, not underneath it -
    that is why ':' is rejected here even though it looks unrelated to
    '..'-style traversal), and no dot at all (which already subsumes
    '..', so there is nothing left for an explicit '..' check to catch).
    A second, independent containment check below re-verifies the
    resolved path is actually inside _DOCS_DIR — belt-and-suspenders in
    case some other reserved-name form (Windows has several) slips past
    the character-level guard above."""
    name = params.get('name')
    if not isinstance(name, str) or not name:
        raise BridgeError('bad_params', "Param 'name' must be a non-empty string.")
    if '/' in name or '\\' in name or ':' in name or '.' in name:
        raise BridgeError('bad_params',
                          "Param 'name' must be a plain stem (no path "
                          "separators, no colons, no dots).")
    if not _DOCS_DIR.is_dir():
        raise BridgeError('not_found', 'No assistant docs directory found.')
    path = _DOCS_DIR / f'{name}.md'
    docs_root = _DOCS_DIR.resolve()
    if not path.resolve().is_relative_to(docs_root):
        raise BridgeError('bad_params', 'Doc name escapes the docs directory.')
    if not path.is_file():
        raise BridgeError('not_found',
                          f'No such doc: {name!r}. Call list_docs for '
                          f'available names.')
    return {'name': name, 'markdown': path.read_text(encoding='utf-8')}


def _list_docs(params: dict) -> dict:
    if not _DOCS_DIR.is_dir():
        return {'docs': []}
    return {'docs': sorted(p.stem for p in _DOCS_DIR.glob('*.md'))}


def _get_skill(params: dict) -> dict:
    """Read the bundled fabricator-assistant SKILL.md and return its raw
    markdown, same shape as _read_doc's {'name', 'markdown'} pair. A
    plain file read of a file this repo ships, no name/path parameter
    to sanitize (unlike _read_doc, which resolves an arbitrary caller-
    supplied stem under _DOCS_DIR)."""
    if not _SKILL_PATH.is_file():
        raise BridgeError('not_found',
                          'The fabricator-assistant skill file is not '
                          'installed.')
    return {'name': 'fabricator-assistant',
            'markdown': _SKILL_PATH.read_text(encoding='utf-8')}


OPS = {
    'hello':               _hello,
    'scene_summary':       _scene_summary,
    'node_details':        _node_details,
    'viewport_screenshot': _viewport_screenshot,
    'fabricator_status':   _fabricator_status,
    'describe_rig':        _describe_rig,
    'component_details':   _component_details,
    'rig_binding':         _rig_binding,
    'build_checks':        _build_checks,
    'validate_blueprint':  _validate_blueprint,
    'scene_report':        _scene_report,
    'build_report':        _build_report,
    'diagnostics':         _diagnostics,
    'read_doc':            _read_doc,
    'list_docs':           _list_docs,
    'get_skill':           _get_skill,
}


def dispatch(op: str, params: dict) -> dict:
    """Look up op in OPS and run it. Unknown op -> BridgeError('unknown_op').
    Any other BridgeError raised by the handler propagates unchanged;
    any non-BridgeError exception also propagates unchanged (the service
    drain, Task 5, is what turns that into an 'internal' error line) -
    this function does no exception handling of its own."""
    handler = OPS.get(op)
    if handler is None:
        raise BridgeError('unknown_op',
                          f"Unknown operation {op!r}. This bridge is "
                          f"read-only and exposes a fixed op set. Valid "
                          f"ops: {', '.join(sorted(OPS))}.")
    return handler(params)
