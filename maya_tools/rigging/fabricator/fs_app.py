# Python/maya_tools/rigging/fabricator/fs_app.py
"""KS v2 — public orchestration API.

Functions in this module are the entry points for all blueprint operations:
loading, saving, building, unbuilding. The UI calls these; headless scripts
call these. No UI imports here — only blueprint + Maya + components.
"""
__author__ = "Adrian Melian"

import json
import traceback
from pathlib import Path

import maya.cmds as cmds

from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
from maya_tools.rigging.fabricator import armature
from maya_tools.rigging.fabricator import nodes
from maya_tools.rigging.fabricator.blueprint import io as blueprint_io
from maya_tools.rigging.fabricator.blueprint import bootstrap as blueprint_bootstrap
from maya_tools.rigging.fabricator.blueprint.builder import validate_blueprint
from maya_tools.rigging.fabricator.blueprint.schema import (
    Blueprint, ComponentSpec, JointSpec, OriginInfo,
)
from maya_tools.rigging.fabricator import follow_rules
from maya_tools.rigging.fabricator import limb_node
from maya_tools.rigging.fabricator.limbs import io as limbs_io
from maya_tools.rigging.fabricator.limbs.builder import apply_limb_fragment
from maya_tools.rigging.fabricator.limbs.schema import (
    LimbFragment, ExternalAnchor, EXTERNAL_PLACEHOLDER,
)
from maya_tools.rigging.fabricator.modules import (
    get_component_class, resolve_component_type,
)
from maya_tools.rigging.fabricator.modules.component import ComponentInstance
from maya_tools.rigging.fabricator.outliner_color import (
    paint_rig_outliner_colors, clear_rig_outliner_colors,
)
from maya_tools.rigging.fabricator.plugs import BuildContext
from maya_tools.rigging.joint_orient import joint_orient_app
from maya_tools.utils.maya import orientation_convention as oc
from maya_tools.skinning import skin_connect_app
from maya_tools.utils.maya import side_tokens


# ─── Implicit-component rules ────────────────────────────────────────────────

def _filename_stem(path: str) -> str:
    """Return the canonical blueprint name from a `<stem>.blueprint.yaml` file
    path. The filename stem is the single source of truth for blueprint
    name — dropdown items, status bar, top-group naming all read this.
    Renaming the file is the canonical way to rename the blueprint.
    """
    name = Path(path).name
    if name.endswith('.blueprint.yaml'):
        return name[:-len('.blueprint.yaml')]
    return Path(path).stem


def _sort_by_depth(joint_names: list, blueprint) -> list:
    """Sort joint names by their depth in the blueprint's hierarchy
    (root = 0, root's children = 1, ...). Parents come first.

    Joints not in the blueprint sort last (depth = inf). Stable for
    same-depth joints (Python's list sort is stable).
    """
    if blueprint is None:
        return list(joint_names)
    parent_map = {j.name: j.parent for j in blueprint.skeleton_joints}

    def depth(name):
        d = 0
        cur = parent_map.get(name)
        while cur is not None:
            d += 1
            cur = parent_map.get(cur)
            if d > 10000:  # cycle guard; validator catches real cycles
                return float('inf')
        if name not in parent_map:
            return float('inf')
        return d

    return sorted(joint_names, key=depth)


def _first_blueprint_child(joint_name: str, blueprint) -> str:
    """Return the first JointSpec.name whose parent matches joint_name,
    in blueprint.skeleton_joints order. None if joint_name has no children.

    'First' is the order in skeleton_joints — bootstrap preserves
    Skeleton IO's export order, which matches Maya's evaluation order.
    For Michael's lf_shoulder, that's lf_elbow before any twist joints.
    """
    if blueprint is None:
        return None
    for j in blueprint.skeleton_joints:
        if j.parent == joint_name:
            return j.name
    return None


def _first_chain_child(joint_name: str, blueprint) -> str:
    """Like _first_blueprint_child, but prefers children that have their own
    children (chain continuations). Falls back to first child if all
    candidates are leaves.

    Used by _resolve_initial_joints' auto-fill walk so multi-joint components
    (SimpleIK on a biped's upperarm) skip leaf-branches like pauldron and
    follow the actual chain (lowerarm → hand).
    """
    if blueprint is None:
        return None
    children = [j for j in blueprint.skeleton_joints if j.parent == joint_name]
    if not children:
        return None
    parents_in_skel = {j.parent for j in blueprint.skeleton_joints if j.parent}
    for c in children:
        if c.name in parents_in_skel:
            return c.name
    # All children are leaves — return the first by skeleton_joints order
    return children[0].name


def _resolve_initial_joints(blueprint, joint_roles: tuple,
                            selected_joints: list, cursor_joint: str,
                            max_joints: int = 0) -> list:
    """Auto-fill the joints[] list for a multi-joint component being added
    via the UI. Cursor row is always the start; other selected joints
    sorted by depth become candidates for mid/end; missing roles auto-walk
    via _first_blueprint_child.

    Spec 1.5 Section 4 — single-select + auto-fill UX.

    For len(joint_roles) == 0 (single-joint component), returns
    [cursor_joint] for compatibility with existing _add_component flow.

    For variable-length components (max_joints == -1, e.g. FKChain /
    SplineFK), the entire selection is honored in depth order (root -> tip):
    the start/end roles only NAME the ends, they must not cap the count.
    """
    if not joint_roles:
        return [cursor_joint] if cursor_joint else []

    if max_joints == -1:
        ordered, seen = [], set()
        for j in _sort_by_depth(list(selected_joints), blueprint):
            if j not in seen:
                seen.add(j)
                ordered.append(j)
        return ordered

    n = len(joint_roles)
    candidates = [cursor_joint] if cursor_joint else []
    for j in _sort_by_depth([j for j in selected_joints if j != cursor_joint], blueprint):
        if j not in candidates:
            candidates.append(j)
    candidates = candidates[:n]

    # If the selection alone filled all slots, depth-sort the result so the
    # joints land in canonical chain order regardless of which joint the
    # user clicked first (Fabricator's UI selection doesn't preserve
    # top-to-bottom click order, so cursor_joint can be the chain TIP
    # rather than the root). The Spec 1.5 cursor-first preference still
    # applies below for partial selections that need auto-walk.
    if len(candidates) == n:
        candidates = _sort_by_depth(candidates, blueprint)

    while len(candidates) < n:
        last = candidates[-1] if candidates else None
        if last is None:
            break
        first_child = _first_chain_child(last, blueprint)
        if first_child is None:
            break  # ran out of chain
        candidates.append(first_child)

    return candidates


def _default_options_for_create(cls, joints: list, blueprint) -> dict:
    """Give a component class a chance to seed extra default option
    values at ADD time, beyond each OptionField's own static `default`
    (PLAN.md 2026-07-08 Task 4.2 — e.g. RibbonIKArm's auto-discovered `fingers`
    membership, populated by walking the wrist joint's child chains).

    Thin wrapper over Component.default_options_for_create (modules/
    component.py) — the base implementation returns {} so every
    component that doesn't override the hook is unaffected. Called from
    the UI's component-add path (fs_window._add_component, multi-joint
    branch) with the SAME `joints`/`blueprint` values already computed
    there (`resolved` / the transient scene-snapshot Blueprint).

    Never raises: a discovery hook failing must not block a component
    add, so any exception degrades to "no extra options".
    """
    try:
        return cls.default_options_for_create(list(joints), blueprint) or {}
    except Exception:
        return {}


def _ensure_world_on_root(bp: Blueprint) -> None:
    """Every blueprint MUST carry a World on its root joint. This is a
    structural rule — World is not user-managed (hidden from the palette).

    Idempotent: skips if a World is already present on the root. Auto-attaches
    a default-options World if not. Mutates bp in place; doesn't mark dirty
    (the user didn't explicitly add it).
    """
    root_name = next(
        (j.name for j in bp.skeleton_joints if j.parent is None), None
    )
    if root_name is None:
        return  # blank blueprint or skeleton-less; bootstrap will populate
    has_world = any(
        c.type == 'World' and c.joints and c.joints[0] == root_name
        for c in bp.components
    )
    if has_world:
        return
    bp.components.insert(0, ComponentSpec(
        id='world',
        type='World',
        joints=[root_name],
        parent_plug='',
        side='md',
        options={'ctrl_shape': 'circle', 'ctrl_color': 'yellow'},
        persisted={},
    ))


# ─── Load / save ─────────────────────────────────────────────────────────────

_LOAD_IN_FLIGHT = False


def load(path: str) -> str:
    """Reentrancy-guarded public entry — see _load_impl for the real body.

    GUARD (2026-07-10 template-load crash): build_armature()'s GUI
    progress reporter pumps QApplication.processEvents() per joint, which
    can redispatch a queued click (e.g. the tail of the launching
    double-click) and re-enter load() while it is still on the stack —
    nested loads stack heavyweight native frames until Maya dies with no
    crash log. A nested call is always redispatched input, never intent:
    refuse it loudly and return.
    """
    global _LOAD_IN_FLIGHT
    if _LOAD_IN_FLIGHT:
        cmds.warning(
            '[Fabricator] load(): a blueprint load is already in progress '
            '— nested call refused (queued input redispatched mid-build).')
        return path
    _LOAD_IN_FLIGHT = True
    try:
        return _load_impl(path)
    finally:
        _LOAD_IN_FLIGHT = False


def _load_impl(path: str) -> str:
    """Load a blueprint YAML from disk and realize it into the scene
    (registry + component nodes + guides with metadata baked in).

    Scene-is-truth: the returned Blueprint dataclass is consumed by this
    function only and discarded. From the moment this function returns,
    the scene IS the truth. The YAML stays on disk as the origin recipe.

    Returns the canonical path that was loaded.
    """
    from maya_tools.framework.decorators import undo_chunk
    from maya_tools.rigging.fabricator import armature_watch

    # A load is a wholesale scene teardown+rebuild: it deletes every prior
    # node and re-creates the registry, component nodes, ~74 skeleton
    # joints, aimers and pivots, then stands the Armature up and repaints
    # the canvas ONCE at the end. Every cmds.select/cmds.joint in that
    # sequence fires SelectionChanged (measured: ~4600 events / ~2000 that
    # clear the canvas-sync mute gate on a UE5 mannequin), and each one
    # that reaches the live canvas callback triggers a full canvas +
    # properties-panel rebuild MID-LOAD — the 45-60s GUI stall (headless
    # has no callbacks wired, so it never pays this). build_armature()
    # already suspends the watch for its own phase; widen that suspension
    # to the whole operation, where it always belonged. suspended() is
    # depth-counted, so the inner build_armature() suspend nests cleanly.
    with armature_watch.suspended(), undo_chunk('Load Blueprint'):
        bp = blueprint_io.read_yaml(path)
        # Single source of truth for blueprint name: the filename stem.
        bp.name = _filename_stem(path)
        _ensure_world_on_root(bp)

        # Tear down ALL prior scene state — registry + components + guides.
        for cnode in list(nodes.get_all_component_nodes()):
            nodes.delete_component_node(cnode)
        nodes.delete_registry()
        nodes.delete_guides_grp()
        nodes.delete_pivots_grp()

        canon_path = str(Path(path).resolve())

        # Spawn fresh registry with top-level metadata.
        origin_str = ''
        if bp.origin:
            # Origin can be a dict, OriginInfo dataclass, or plain string depending
            # on the YAML. JSON-encode any non-string for round-trip.
            if isinstance(bp.origin, str):
                origin_str = bp.origin
            else:
                try:
                    if hasattr(bp.origin, '__dict__'):
                        origin_str = json.dumps(bp.origin.__dict__)
                    else:
                        origin_str = json.dumps(bp.origin)
                except (TypeError, ValueError):
                    origin_str = str(bp.origin)

        nodes.create_registry(
            bp.name, canon_path, bp.schema_version,
            description=bp.description or '',
            origin=origin_str,
            wiring=list(bp.wiring or []),
        )
        # nodes.create_registry stamps fabricator_version at CURRENT the
        # instant the registry exists (Task 2.3 closing sweep item 1 —
        # protects a brand-new, never-built scene's own fresh registry,
        # e.g. init_rig()'s bring-your-own-skeleton path, from reading as
        # legacy). A blueprint LOAD is a different case: the .yaml file
        # on disk may be genuinely old (pre-1.1.0 IKArm data), so the
        # legacy-map gate must run against the FILE's age, not this
        # brand-new scene's. The file's own fabricator_version stamp
        # (written by save since 1.4.0; '' = pre-stamping file) IS that
        # age: hold the registry at the file's stamp for the WINDOW of
        # the raw-YAML type resolutions below, so resolve_component_
        # type's version gate reads the data's true era. An unstamped
        # file reads as legacy — correct for genuinely old data, and the
        # ONLY possible call for pre-stamping files (a file saved after
        # the free IKArm reclaimed the type string but before stamping
        # landed is indistinguishable from ribbon-era data; one re-save
        # under a stamping build cures it). Blanking unconditionally
        # here was the 2026-07-19 ribbon-arms bug: every load read as
        # legacy and fresh free-IKArm templates misloaded as
        # RibbonIKArm. The stamp goes back to CURRENT after the LAST
        # raw-YAML resolution (see the re-stamp below — 2026-07-12;
        # loads never stay unstamped until a build).
        # (The pre-1.2.0 'fingers'-option migration is retired — Derived
        # Limbs 2026-07-11; a stray 'fingers' option is simply ignored.)
        reg = nodes.get_registry()
        cmds.setAttr(f'{reg}.fabricator_version',
                     bp.fabricator_version or '', type='string')

        # Spawn component nodes from the blueprint's components block.
        for cdata in bp.components:
            ctype = resolve_component_type(cdata.type)
            cls = get_component_class(ctype)
            cid = cdata.id if cdata.id else cls.default_id(cdata.joints)
            live_joints = [j for j in cdata.joints if cmds.objExists(j)]
            region = cdata.region or cls.CONTRACT.default_region
            nodes.create_component_node(
                component_id=cid,
                component_type=ctype,
                joints=live_joints,
                joint_names=list(cdata.joints),  # canonical mirror — survives joint-not-existing-yet
                parent_plug=cdata.parent_plug,
                side=cdata.side,
                role=cdata.role,
                region=region,
                options=cdata.options,
                persisted=cdata.persisted,
            )

        # Create the skeleton directly from the bp (the transient guide
        # scaffold is retired — the Armature is the editable stage now),
        # then restore aimers in their persisted state.
        _create_skeleton_from_blueprint(bp)
        restore_aimers_from_blueprint(bp)

        # Cross-convention conversion (t48, 2026-07-19): a blueprint
        # baked under one mirror convention loading into a rig stamped
        # with the other gets its mirrored-side aimers re-derived
        # BEFORE build_armature bakes them into the joints. Without
        # this, a Standard project loading an Unreal-baked template
        # forces the manual Symmetry-drive dance Adrian described.
        _convert_mirrored_aimers_to_rig_convention(bp)

        # Derived Limbs (spec 2026-07-11): limbs are never serialized in
        # a blueprint — re-derive them here, the ONE pass that makes a
        # loaded template carry the same limb identity a fragment drop
        # or a live add would ('template load has no limbs' fix). Runs
        # after the skeleton exists (the create-loop's own derive call
        # no-ops on empty joints[]).
        for cnode in nodes.get_all_component_nodes():
            nodes.derive_limb(cnode)

        # Spawn component pivots. Joints now exist (build_skeleton spawned
        # them), so this fires unconditionally. Runs INSIDE the file-stamp
        # window: this re-resolves raw cdata.type strings (get_component_
        # class on YAML data), and it must reach the same verdict the
        # spawn loop did — under the pre-fix order (re-stamp before this)
        # a genuinely legacy load resolved 'IKArm' two different ways
        # within one load (ribbon class in the spawn loop, basic class
        # here).
        _sync_component_pivots(bp)

        # Re-stamp CURRENT now that the load's own migration is done —
        # this closes the file-stamp window above; every raw-YAML type
        # resolution is behind us and the spawn loop wrote RESOLVED
        # canonical types onto the scene nodes, so nothing version-gated
        # remains in the scene. (Adrian, 2026-07-12: a fresh template
        # load must never raise the 'Rig predates FS version stamping'
        # notice on its first build.) The stamp correctly records the
        # version that migrated the data: a future version's legacy map
        # compares against its own gate version, so old-vs-new gating
        # still works for later bumps.
        from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION
        nodes.set_registry_fabricator_version(FABRICATOR_VERSION)

        # Stand the Armature up — the edit stage always has it
        # (spec 2026-07-04 §2).
        armature.build_armature()

        return canon_path


# (Derived Limbs, spec 2026-07-11: the pre-1.2.0 legacy 'fingers'-option
# migration is retired with hand-authored membership itself — a legacy
# blueprint's stray 'fingers' option is simply ignored; fingers derive
# fresh at load.)


def _convert_mirrored_aimers_to_rig_convention(bp) -> None:
    """Re-derive the mirrored-side (rt) aimers when a blueprint baked
    under one mirror convention loads into a rig stamped with the other
    (t48, 2026-07-19). Left is the authoring side; each rt aimer whose
    lf counterpart exists is driven to the rig convention's WORLD-frame
    mirror of the lf aimer (Convention.mirror_frame) — frame-EXACT
    regardless of the joints' loaded state, unlike the channel maps,
    which assume the destination frames sit in the corresponding
    lifecycle state. That matters here twice over: the loaded joints
    are baked under the OTHER convention, and templates saved before
    the FS #18 fix carry its slightly-off authored-offset bakes.
    build_armature() then bakes the converted frames into the joints.

    aimTarget enums are NOT touched — the restored per-joint save state
    is already correct; only the orientation differs by convention.
    Center (md) joints never convert. An rt aimer with no lf source is
    left as loaded and reported (asymmetric authoring is literal intent
    in either convention).
    """
    import math
    import maya.api.OpenMaya as om
    from maya_tools.utils.maya import side_tokens

    src_name = bp.orient_convention or 'unreal'   # '' = legacy blueprint
    conv = oc.resolve(nodes.get_registry() or '')
    if conv.name == src_name:
        return

    joints = [j.name for j in bp.skeleton_joints]
    lf_of = {}     # rt joint -> lf joint
    for jnt in joints:
        if side_tokens.detect_side(jnt) == 'lf':
            other = side_tokens.flip_side_token(jnt)
            if other and other != jnt:
                lf_of[other] = jnt

    converted, skipped = 0, []
    for jnt in joints:
        if side_tokens.detect_side(jnt) != 'rt':
            continue
        dst_a = joint_orient_app.aimer_name(jnt)
        if not cmds.objExists(dst_a):
            continue
        src_a = joint_orient_app.aimer_name(lf_of.get(jnt, ''))
        if not (lf_of.get(jnt) and cmds.objExists(src_a)):
            skipped.append(jnt)
            continue
        m = cmds.xform(src_a, q=True, ws=True, matrix=True)
        axes = []
        for r in (0, 4, 8):
            v = (m[r], m[r + 1], m[r + 2])
            n = math.sqrt(sum(c * c for c in v)) or 1.0
            axes.append(tuple(c / n for c in v))
        t = conv.mirror_frame(axes)
        mm = om.MMatrix([t[0][0], t[0][1], t[0][2], 0.0,
                         t[1][0], t[1][1], t[1][2], 0.0,
                         t[2][0], t[2][1], t[2][2], 0.0,
                         0.0, 0.0, 0.0, 1.0])
        e = om.MTransformationMatrix(mm).rotation()
        cmds.xform(dst_a, ws=True, rotation=[math.degrees(e.x),
                                             math.degrees(e.y),
                                             math.degrees(e.z)])
        converted += 1

    msg = (f'[Fabricator] Blueprint is {src_name}-baked; rig convention '
           f'is {conv.name}: {converted} mirrored-side aimer(s) '
           f'converted.')
    if skipped:
        msg += (f' {len(skipped)} rt aimer(s) kept as loaded (no '
                f'left-side source): {", ".join(skipped)}.')
    print(msg)


def save(path: str = '') -> str:
    """Snapshot the current scene state and write a fresh YAML.

    Reads top-level from registry attrs, components from network nodes,
    skeleton from joints (or guides pre-Build-Skeleton). The in-memory
    bp is NOT consulted — true scene snapshot.

    Args:
        path: optional override. If empty, uses the registry's stored path.

    Returns the path written to.

    Raises:
        RuntimeError: if any component is built (MODE_MODULES_BUILT). Saving
        a built rig snapshots bind_pose_json's local-TRS fallback as if it
        were world_translate, which is correct for root joints but wrong for
        children — reload produces a broken skeleton hierarchy. Adrian
        validated this manually 2026-05-10 (IDEAS.md). Unbuild first.
    """
    built = [cn for cn in nodes.get_all_component_nodes() if nodes.is_built(cn)]
    if built:
        raise RuntimeError(
            'Save Template: rig is built. Unbuild (Edit Rig) before saving a '
            'template — saving a built rig produces a broken skeleton on reload.'
        )

    # If pivot locators are live, capture their positions to component
    # options first so the YAML reflects the current edit state.
    _capture_pivots_to_options_from_scene()

    target = path or nodes.get_blueprint_path()
    if not target:
        raise RuntimeError('No path. Pass path explicitly or load a blueprint first.')

    bp = _snapshot_blueprint_from_scene()
    blueprint_io.write_yaml(bp, target)

    canon = str(Path(target).resolve())
    nodes.set_blueprint_path(canon)

    return canon


def _snapshot_blueprint_from_scene() -> Blueprint:
    """Walk the scene and reconstruct a Blueprint dataclass for YAML
    serialization. No in-memory bp is consulted.

    Skeleton block: joints if they exist (post-Build-Skeleton);
                    guides if they don't (pre-Build-Skeleton).
    Components block: from network nodes.
    Top-level fields: from registry attrs.

    Raises if no registry exists in the scene (nothing to snapshot).
    """
    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION

    reg = nodes.get_registry()
    if not reg:
        raise RuntimeError('No fab_registry in scene; nothing to snapshot.')

    # Components from network nodes.
    components = [
        _snapshot_single_component_spec(cnode)
        for cnode in nodes.get_all_component_nodes(reg)
    ]

    # Skeleton block: prefer joints, fall back to guides.
    skeleton_joints = _snapshot_skeleton_from_scene()

    # Top-level fields from registry.
    name = nodes.get_blueprint_name(reg) or 'untitled'
    schema_version = cmds.getAttr(f'{reg}.schema_version') or '1.0'
    description = nodes.get_blueprint_description(reg)
    origin_raw = nodes.get_blueprint_origin(reg)
    # Origin: JSON-encoded if dict/struct was stored, plain string otherwise.
    # Try to parse — fall back to raw string on parse failure. Dicts get
    # rehydrated into OriginInfo so write_yaml's _origin_to_dict() works
    # on a dataclass instance (matching what read_yaml produces on load).
    origin = None
    if origin_raw:
        try:
            parsed = json.loads(origin_raw)
        except (json.JSONDecodeError, ValueError):
            parsed = origin_raw
        if isinstance(parsed, dict):
            try:
                origin = OriginInfo(**parsed)
            except TypeError:
                # Dict shape doesn't match OriginInfo schema — drop in as-is
                # so YAML writer can still serialize via dict path if it has
                # one, else falls back to repr.
                origin = parsed
        else:
            origin = parsed
    wiring = nodes.get_blueprint_wiring(reg)

    return Blueprint(
        name             = name,
        schema_version   = schema_version,
        description      = description,
        origin           = origin,
        skeleton_joints  = skeleton_joints,
        components       = components,
        wiring           = wiring,
        # The convention this skeleton is BAKED under — load uses it to
        # know what a cross-convention conversion converts FROM (t48).
        orient_convention = oc.resolve(nodes.get_registry() or '').name,
        # The build WRITING this file — load gates the legacy-type-map
        # window on it (1.4.0; the ribbon-arms fix).
        fabricator_version = FABRICATOR_VERSION,
    )


def _snapshot_single_component_spec(cnode: str) -> ComponentSpec:
    """Build a ComponentSpec from one component network node.

    Shared by full-blueprint and limb-fragment snapshot paths so they
    agree byte-for-byte on per-component serialization.
    """
    # Prefer the LIVE message-multi (Maya tracks identity through joint
    # renames; the connection name is always current). Fall back to the
    # frozen joint_names string mirror only when the message-multi is
    # empty — that's the template-loaded-before-Build-Skeleton case
    # (joints don't exist yet) OR the joint-was-deleted case where the
    # canonical name needs to survive for reconciliation on re-create.
    live_joint_names = [j.split('|')[-1].split(':')[-1]
                        for j in nodes.get_component_joints(cnode)]
    ctype = nodes.get_component_type(cnode)
    # De-contaminate before serializing: mirror/duplicate propagates each
    # NEW joint's .message into the SOURCE component's joints[] multi
    # (CLAUDE.md gotcha #1), appending the opposite side's joints at the
    # tail. Build reconciles this live, but a raw snapshot would bake the
    # doubled set into the saved template — which is exactly the
    # BaseMale_Rig reload failure (2026-07-11): on load joint_names goes
    # over the contract bound and the build aborts as unrepairable. The
    # originals stay first (the extras are appended), so bounding an
    # over-connected multi to the component's contract keeps the real
    # joints and drops the mirror echo. Unbounded contracts (cap == -1,
    # e.g. RibbonSpine — a centre chain, not mirrored) are left as-is.
    snap_joints = live_joint_names
    try:
        cap = get_component_class(ctype).CONTRACT.max_joints
        if cap != -1 and len(snap_joints) > cap:
            snap_joints = snap_joints[:cap]
    except (KeyError, AttributeError):
        pass
    return ComponentSpec(
        id          = nodes.get_component_id(cnode),
        type        = ctype,
        joints      = snap_joints or nodes.get_component_joint_names(cnode),
        parent_plug = nodes.get_component_parent_plug(cnode),
        side        = nodes.get_component_side(cnode),
        options     = nodes.get_component_options(cnode),
        persisted   = nodes.get_component_persisted(cnode),
        role        = nodes.get_component_role(cnode),
        region      = nodes.get_component_region(cnode),
    )


def _walk_limb_subtree(anchor_joint: str) -> list:
    """Return DAG descendants of anchor_joint (excludes anchor itself).

    Reverses cmds.listRelatives(allDescendents=True)'s leaf-first order so
    the loader's joint-creation loop sees parents before children.
    """
    descendants = cmds.listRelatives(
        anchor_joint, allDescendents=True, type='joint', fullPath=False,
    ) or []
    descendants.reverse()
    return descendants


def _components_owning_joints(joint_names: list) -> list:
    """Return component nodes whose primary joint (joints[0]) is in joint_names."""
    joint_set = set(joint_names)
    out = []
    for cnode in nodes.get_all_component_nodes():
        comp_joints = nodes.get_component_joints(cnode) or []
        if comp_joints and comp_joints[0] in joint_set:
            out.append(cnode)
    return out


def _validate_limb_containment(component_nodes: list, joint_set: set) -> None:
    """Each component's joints must all live inside joint_set — a chain that
    crosses the subtree boundary can't be cleanly extracted."""
    offenders = []
    for cnode in component_nodes:
        comp_joints = nodes.get_component_joints(cnode) or []
        outside = [j for j in comp_joints if j not in joint_set]
        if outside:
            cid = nodes.get_component_id(cnode) or cnode
            offenders.append(f'{cid} -> {outside}')
    if offenders:
        raise RuntimeError(
            'Limb save aborted - these components extend outside the selected '
            'subtree:\n  ' + '\n  '.join(offenders)
        )


def _resolve_save_subtree(anchor_joint: str) -> list:
    """Save Limb gesture disambiguation (KS #4) — return the ordered
    joint list that becomes the fragment.

    The walk is HOST-anchored (anchor excluded from the fragment), but
    users read the canvas gesture inclusively — Delete Limb on the same
    menu takes the clicked row and its subtree. Clicking a limb's OWN
    first joint (pelvis for a spine, thigh for a leg, clavicle for an
    arm) therefore used to raise 'No Fabricator components found': the
    one joint that proves a component lives here was excluded from the
    search.

    When the clicked joint IS some component's primary joint (joints[0])
    AND it has a joint parent, the clicked joint rides the fragment
    alongside its OWN descendants; the parent serves only as the
    external host.

    2026-07-18 (Adrian, live): this used to re-anchor to the parent and
    then walk the PARENT's whole subtree, which swept in every SIBLING
    limb — saving the left clavicle (a child of spine_05) also saved the
    right arm and the neck/head, and the fragment then collided on load
    against an existing right arm. It also emitted three
    <EXTERNAL>-parented roots into a schema whose loader assumes one.
    Widen by one JOINT, never by one GENERATION.

    The parent guard is load-bearing: the skeleton ROOT is the World
    component's own joints[0] but has no joint parent, so it falls
    through to plain host semantics and never drags World into a
    fragment. The canvas no longer OFFERS Save Limb on the rig root
    (2026-07-18, Adrian: "if you're saving the root joint, you're saving
    a template") — this guard covers the programmatic path and the
    root-anchored fixtures in _dev/test_limb_units_maya.py."""
    descendants = _walk_limb_subtree(anchor_joint)
    if not _components_owning_joints([anchor_joint]):
        return descendants
    if not (cmds.listRelatives(anchor_joint, parent=True,
                               type='joint') or []):
        return descendants
    return [anchor_joint] + descendants


def _snapshot_limb_fragment_from_scene(anchor_joint: str, name: str):
    """Walk the subtree under anchor_joint, snapshot joints + owning
    components, return a LimbFragment ready for write_yaml.

    anchor_joint is the HOST: it stays outside the fragment and its
    descendants become the limb — except when it starts a component,
    where it joins its OWN subtree instead (KS #4, see
    _resolve_save_subtree). Sibling branches are never included."""
    if not cmds.objExists(anchor_joint):
        raise RuntimeError(f'Anchor joint not found: {anchor_joint!r}')

    subtree = _resolve_save_subtree(anchor_joint)
    if not subtree:
        raise RuntimeError(
            f'Limb is empty - {anchor_joint!r} has no joint descendants.'
        )

    component_nodes = _components_owning_joints(subtree)
    if not component_nodes:
        raise RuntimeError(
            f'No Fabricator components found under {anchor_joint!r}.'
        )

    joint_set = set(subtree)
    _validate_limb_containment(component_nodes, joint_set)

    rotate_order_names = ['xyz', 'yzx', 'zxy', 'xzy', 'yxz', 'zyx']
    skel_specs = []
    for jname in subtree:
        parent = cmds.listRelatives(jname, parent=True) or [None]
        parent_name = parent[0]
        skel_parent = parent_name if parent_name in joint_set else EXTERNAL_PLACEHOLDER
        translate = cmds.getAttr(f'{jname}.translate')[0]
        rotate = cmds.getAttr(f'{jname}.rotate')[0]
        joint_orient = cmds.getAttr(f'{jname}.jointOrient')[0]
        rotate_order_idx = cmds.getAttr(f'{jname}.rotateOrder')
        radius = cmds.getAttr(f'{jname}.radius')
        # Authored aimer state (2026-07-20). The fragment FORMAT has
        # carried aim_target/aim_offset all along — limbs/io.py writes
        # and reads them, _setup_limb_aimers restores them — but this
        # snapshot never populated them, so every fragment on disk was
        # aimer-blank and every drop fell back to creation defaults.
        # Mirrors the blueprint snapshot's own capture.
        aim_state = joint_orient_app.get_aimer_state(jname) or {}
        skel_specs.append(JointSpec(
            name=jname,
            parent=skel_parent,
            translate=list(translate),
            rotate=list(rotate),
            joint_orient=list(joint_orient),
            rotate_order=rotate_order_names[rotate_order_idx],
            radius=radius,
            aim_target=aim_state.get('aim_target', ''),
            aim_offset=list(aim_state.get('aim_offset', [0.0, 0.0, 0.0])),
        ))

    comp_specs = [_snapshot_single_component_spec(c) for c in component_nodes]
    component_ids_in_limb = {s.id for s in comp_specs}

    # Any component whose parent_plug references outside the limb (or has
    # no plug parent at all) is a root — rewrite to <EXTERNAL>.<plug>.
    # Empty parent_plug is common: a component authored with no upstream
    # plug connection is still a valid limb member, it just needs the
    # loader to attach it to the host component owning the drop target.
    # The World component itself is filtered earlier by inclusion in the
    # subtree, not by parent_plug shape.
    for spec in comp_specs:
        parent_ref = spec.parent_plug
        if not parent_ref:
            spec.parent_plug = f'{EXTERNAL_PLACEHOLDER}.'
            continue
        parent_cid, _, plug_name = parent_ref.partition('.')
        if parent_cid not in component_ids_in_limb:
            spec.parent_plug = f'{EXTERNAL_PLACEHOLDER}.{plug_name}'

    # Authored Armature-ctrl local transforms (LimbFragment.
    # armature_ctrls docstring): joint TRS above is DRIVEN state — the
    # ctrls are the editable stage, and only their locals reproduce the
    # authored placement exactly when the fragment is dropped back in
    # (build_armature's re-derivation passes recompute everything else).
    # Follow-ruled joints have no ctrl (armature module contract) and
    # are skipped naturally — their position re-derives at drop time
    # when nodes.derive_limb re-adopts them (Derived Limbs, 2026-07-11:
    # nothing limb-level is serialized; the old limb: block is retired).
    armature_ctrls = {}
    for jname in subtree:
        ctrl = armature.ctrl_for_joint(jname)
        if not ctrl:
            continue
        armature_ctrls[jname] = {
            'translate': list(cmds.getAttr(f'{ctrl}.translate')[0]),
            'rotate': list(cmds.getAttr(f'{ctrl}.rotate')[0]),
        }

    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION
    return LimbFragment(
        name=name,
        description='',
        external_anchor=ExternalAnchor(plug_kind='matrix'),
        skeleton_joints=skel_specs,
        components=comp_specs,
        armature_ctrls=armature_ctrls,
        # The build WRITING this file — apply_limb_fragment gates the
        # legacy type map on it (fragment parity, 2026-07-20).
        fabricator_version=FABRICATOR_VERSION,
    )
# (Derived Limbs, spec 2026-07-11: _snapshot_limb_block_from_scene is
# retired with the limb: YAML block — fingers and twist joints in a
# fragment are plain skeleton joints, re-derived on every drop.)


def save_limb(anchor_joint: str, path: str, name: str = '') -> None:
    """Snapshot the subtree under anchor_joint and write as a .limb.yaml.

    name: optional display name; defaults to the file stem when empty.
    """
    path_obj = Path(path)
    if not name:
        name = path_obj.stem.removesuffix('.limb')

    fragment = _snapshot_limb_fragment_from_scene(anchor_joint, name)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    limbs_io.write_yaml(fragment, path_obj)


def load_limb(path: str, target_joint: str) -> None:
    """Read a .limb.yaml and apply it under target_joint in the current scene."""
    from maya_tools.framework.decorators import undo_chunk

    with undo_chunk('Load Limb'):
        fragment = limbs_io.read_yaml(path)
        apply_limb_fragment(fragment, target_joint)


def _snapshot_skeleton_from_scene() -> list:
    """Return list of JointSpec representing the current scene skeleton.
    Prefer real joints (post-Build-Skeleton); fall back to guides
    (pre-Build-Skeleton). Build Skeleton deletes guides, so seeing both
    is anomalous — joints win when both present.
    """
    rotate_orders = ('xyz', 'yzx', 'zxy', 'xzy', 'yxz', 'zyx')

    # Bind-pose preflight: if any component is built and bind_pose_json
    # exists on the registry, prefer the captured bind values over live
    # joint reads (the rig might be posed; live reads would bake the pose
    # into the YAML). rotateOrder + radius read live regardless — they
    # aren't driven by constraints.
    reg = nodes.get_registry()
    bind_pose = {}
    if reg:
        any_built = any(nodes.is_built(c) for c in nodes.get_all_component_nodes(reg))
        if any_built and cmds.attributeQuery('bind_pose_json', node=reg, exists=True):
            raw = cmds.getAttr(f'{reg}.bind_pose_json') or '{}'
            try:
                bind_pose = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                bind_pose = {}

    # KS-scoped: prefer walking descendants of the registry's root_joint.
    # Falls back to whole-scene walk if root_joint isn't connected (pre-fix
    # scenes saved before this attr existed).
    ks_root = nodes.get_registry_root_joint() if nodes.get_registry() else ''
    if ks_root and cmds.objExists(ks_root):
        # Scoped to KS subtree: root + all descendant joints.
        descendants = cmds.listRelatives(ks_root, allDescendents=True,
                                         type='joint', fullPath=False) or []
        all_joints = [ks_root] + list(reversed(descendants))
    else:
        all_joints = cmds.ls(type='joint', long=False) or []

    if all_joints:
        # Topo-sort: roots first, descendants follow.
        roots = [j for j in all_joints
                 if not (cmds.listRelatives(j, parent=True, type='joint') or [])]
        ordered = []
        visited = set()

        def walk(j):
            if j in visited:
                return
            visited.add(j)
            ordered.append(j)
            children = cmds.listRelatives(j, children=True, type='joint') or []
            for c in sorted(children):
                walk(c)

        for r in sorted(roots):
            walk(r)

        out = []
        for j in ordered:
            par_list = cmds.listRelatives(j, parent=True, type='joint') or []
            par = par_list[0] if par_list else None

            # bind-pose-preferred: when available, the captured bind values
            # are the truth (live values could be driven by ctrls).
            bp = bind_pose.get(j)
            if bp:
                trans = bp['t']
                rot   = bp['r']
                jo    = bp.get('jo', [0.0, 0.0, 0.0])
                # World follows local chain at reload (local TRS from bind
                # pose is the truth); use translate as world fallback so the
                # create_guides world-vs-local check at j.world_translate
                # detects the non-origin case correctly.
                world_t = list(trans)
            else:
                trans = cmds.getAttr(f'{j}.translate')[0]
                rot = cmds.getAttr(f'{j}.rotate')[0]
                jo = cmds.getAttr(f'{j}.jointOrient')[0]
                world_t = cmds.xform(j, q=True, ws=True, t=True)

            ro_idx = cmds.getAttr(f'{j}.rotateOrder') or 0
            ro = rotate_orders[ro_idx] if 0 <= ro_idx < len(rotate_orders) else 'xyz'
            radius = cmds.getAttr(f'{j}.radius')
            if radius is None:
                radius = 1.0

            # Aimer state (Armature, spec 2026-07-04 §7): captured when
            # the joint has a live aimer so save/load restores it exactly.
            aim_state = joint_orient_app.get_aimer_state(j) or {}

            # Root (no joint parent): capture the WORLD rotation too. The
            # locals above are relative to whatever non-joint parent the
            # root has (a UE import's -90 axis group, the build's top
            # group) and that parent is NOT part of the blueprint —
            # recreating from locals alone dropped the group's rotation
            # and the template came back sideways (2026-07-14). Prefer the
            # bind-pose snapshot's world matrix (live values could be
            # ctrl-driven on a built rig).
            world_r = None
            if par is None:
                if bp and 'world_m' in bp:
                    import math as _math
                    import maya.api.OpenMaya as _om2
                    _e = _om2.MTransformationMatrix(
                        _om2.MMatrix(bp['world_m'])).rotation(asQuaternion=False)
                    world_r = [_math.degrees(_e.x), _math.degrees(_e.y),
                               _math.degrees(_e.z)]
                else:
                    world_r = list(cmds.xform(j, q=True, ws=True,
                                              rotation=True))

            out.append(JointSpec(
                name=j,
                parent=par,
                translate=list(trans),
                rotate=list(rot),
                joint_orient=list(jo),
                rotate_order=ro,
                radius=float(radius),
                world_translate=list(world_t),
                aim_target=aim_state.get('aim_target', ''),
                aim_offset=list(aim_state.get('aim_offset',
                                              [0.0, 0.0, 0.0])),
                world_rotate=world_r,
            ))
        return out

    # No joints — nothing to snapshot. (The transient guide layer was
    # removed 2026-07-04; the Armature edits real joints directly.)
    return []


def rebuild_modules() -> None:
    """Re-apply blueprint state to the scene. Unbuilds first if currently
    built (component build methods aren't idempotent against existing
    ctrls — a naked second build would create duplicates).

    Used by 'live preview' on properties-panel edits when the rig is
    already built. No-op when nothing was built (the unbuild path is
    skipped, and build_modules isn't useful without a prior unbuild
    capture step at this point).
    """
    is_built = any(nodes.is_built(n) for n in nodes.get_all_component_nodes())
    if not is_built:
        return
    unbuild_modules()
    build_modules()


def bootstrap_from_skeleton(skeleton_json_path: str, save_to: str = '') -> Blueprint:
    """Read a Skeleton IO JSON and produce + save a starter blueprint.

    Caller is expected to follow up with load(save_to) to register the
    blueprint as current and spawn its (empty) component-node block.
    """
    bp = blueprint_bootstrap.from_skeleton_json(skeleton_json_path)
    if save_to:
        blueprint_io.write_yaml(bp, save_to)
    return bp


# ─── Build phase entry points (Tasks 13-15 fill these in) ────────────────────

def _create_skeleton_from_blueprint(bp: Blueprint) -> list:
    """Create the skeleton directly from the blueprint's skeleton block.

    Replaces the retired transient-guide pipeline (create_guides +
    build_skeleton, removed 2026-07-04 — the Armature is the editable
    stage now). Joints are created parent-first with their local TRS /
    jointOrient / rotateOrder / radius applied verbatim — the exact
    pattern proven by limb drops (_create_limb_joints). Existing joints
    are skipped so additive loads stay legal.

    Ends with the same bookkeeping build_skeleton used to do:
    component joints[] reconciliation + registry root_joint.

    Returns the list of created joint names.
    """
    rotate_order_names = ['xyz', 'yzx', 'zxy', 'xzy', 'yxz', 'zyx']
    created = []
    for jspec in _topo_sort_joints(bp.skeleton_joints):
        if cmds.objExists(jspec.name):
            if cmds.nodeType(jspec.name) == 'joint':
                continue
            raise RuntimeError(
                f'Blueprint joint name {jspec.name!r} collides with a '
                f'non-joint node in the scene.')
        cmds.select(clear=True)
        node = cmds.joint(name=jspec.name)
        if jspec.parent and cmds.objExists(jspec.parent):
            node = cmds.parent(node, jspec.parent)[0]
        cmds.setAttr(f'{node}.translate', *jspec.translate)
        cmds.setAttr(f'{node}.rotate', *jspec.rotate)
        cmds.setAttr(f'{node}.jointOrient', *jspec.joint_orient)
        if jspec.rotate_order in rotate_order_names:
            cmds.setAttr(f'{node}.rotateOrder',
                         rotate_order_names.index(jspec.rotate_order))
        cmds.setAttr(f'{node}.radius', jspec.radius)
        # Root: the captured locals were relative to a non-joint parent
        # that isn't part of the blueprint (a UE import's -90 group) —
        # the recorded WORLD rotation is the truth (2026-07-14). Legacy
        # blueprints (world_rotate=None) keep the locals-only behavior.
        if jspec.parent is None and getattr(jspec, 'world_rotate',
                                            None) is not None:
            cmds.xform(node, ws=True, rotation=jspec.world_rotate)
        created.append(jspec.name)

    _reconcile_component_joint_connections()

    # Root registration must survive the skip-existing branch: an
    # additive load onto a scene that already holds the blueprint's
    # joints creates nothing, but the registry was just torn down and
    # respawned above, so it must still be wired to a root or
    # build_armature dies at _resolve_root (template-drop-on-existing-
    # skeleton, 2026-07-24). Resolve roots over every blueprint joint
    # present in the scene, created or pre-existing.
    scene_joints = [js.name for js in bp.skeleton_joints
                    if cmds.objExists(js.name)]
    roots = [j for j in scene_joints
             if not (cmds.listRelatives(j, parent=True,
                                        type='joint') or [])]
    if roots:
        nodes.set_registry_root_joint(roots[0])

    # Fresh joints go straight into the _Joints reference layer —
    # visible, viewport-unselectable (Armature ctrls are the interface).
    add_joints_to_reference_layer(created)
    return created


def _bp_world_translate(joint_name: str, blueprint) -> tuple:
    """Return the worldspace translation of a joint.

    When blueprint is provided, reads JointSpec.world_translate (populated
    by Skeleton IO bootstrap); falls back to JointSpec.translate (root
    joints + hand-authored bps).

    When blueprint is None (scene-is-truth callers post-Build-Skeleton),
    reads the joint's worldspace transform directly from the scene.
    Returns (0, 0, 0) if neither the blueprint nor the scene has it.
    """
    if blueprint is None:
        if cmds.objExists(joint_name):
            world_t = cmds.xform(joint_name, q=True, ws=True, t=True)
            return tuple(world_t)
        return (0.0, 0.0, 0.0)

    for j in blueprint.skeleton_joints:
        if j.name == joint_name:
            if j.world_translate and any(abs(v) > 1e-9 for v in j.world_translate):
                return tuple(j.world_translate)
            return tuple(j.translate)
    return (0.0, 0.0, 0.0)


def _bp_world_distance(jname_a: str, jname_b: str, blueprint) -> float:
    """Worldspace distance between two joints in the blueprint."""
    a = _bp_world_translate(jname_a, blueprint)
    b = _bp_world_translate(jname_b, blueprint)
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _color_guide_curve(transform: str, color_index: int = 17) -> None:
    """Set Maya override colour-index on every nurbsCurve shape under transform."""
    shapes = cmds.listRelatives(transform, shapes=True, type='nurbsCurve',
                                fullPath=True) or []
    for s in shapes:
        cmds.setAttr(f'{s}.overrideEnabled', 1)
        cmds.setAttr(f'{s}.overrideColor', color_index)


def _reconcile_component_joint_connections() -> None:
    """Walk component network nodes, refresh joints[] message-multi to
    match each component's joint_names string mirror against scene joints
    that now exist.

    Called after build_skeleton spawns joints. Components added at
    template-load time have empty joints[] (no joints existed at create
    time) but have joint_names populated from the blueprint YAML. This
    pass rebinds the message connections so subsequent validation and
    build paths see the correct joints.

    Idempotent: replace_message_multi handles already-connected joints
    correctly.
    """
    from maya_tools.utils.maya import network_nodes as nn

    for cnode in nodes.get_all_component_nodes():
        expected_names = nodes.get_component_joint_names(cnode)
        if not expected_names:
            # Pre-fix scene with no joint_names attr, OR a legitimately
            # empty component. Either way, nothing to reconnect.
            continue
        live_joints = [n for n in expected_names if cmds.objExists(n)]
        nn.replace_message_multi(cnode, 'joints', live_joints)


def restore_aimers_from_blueprint(blueprint: Blueprint = None,
                                  scale: float = 10.0) -> list:
    """Create aimers for every blueprint joint present in the scene and
    apply the persisted aimer state (Armature, spec 2026-07-04 §7):
    aim_target restored by label (right child on multi-child joints,
    Local, World) and the aimer rotation offset re-applied exactly.

    Existing aimers are kept and just re-stated. Joints missing from the
    scene are skipped silently (partial builds are legal).

    Returns:
        List of joint names whose aimers were created/updated.
    """
    if blueprint is not None:
        bp = blueprint
    else:
        # Standalone call — recover the bp from disk via registry.
        path = nodes.get_blueprint_path()
        if not path:
            raise RuntimeError(
                'No registry in scene. Load a template first.'
            )
        bp = blueprint_io.read_yaml(path)

    touched = []
    with joint_orient_app.undo_chunk('RestoreAimers'):
        for spec in _topo_sort_joints(bp.skeleton_joints):
            if (not cmds.objExists(spec.name)
                    or cmds.nodeType(spec.name) != 'joint'):
                continue
            fresh = not joint_orient_app.aimer_exists(spec.name)
            if fresh:
                joint_orient_app.create_aimer(spec.name, scale=scale)
            target = spec.aim_target
            if not target and fresh:
                # Unset in the blueprint — recognize an existing ±aim
                # geometrically so clean chains keep their SC-IK edges
                # (a −axis match captures the mirror flip into the
                # offset); authored joints stay Local (orientation
                # kept). The seeder owns the offset in this branch.
                joint_orient_app.seed_aimer_from_detection(spec.name)
            else:
                joint_orient_app.apply_aimer_state(
                    spec.name,
                    aim_target=target,
                    aim_offset=list(spec.aim_offset or [0.0, 0.0, 0.0]),
                )
            touched.append(spec.name)
    return touched


def _topo_sort_joints(joints):
    """Sort joints parent-first via DFS — required so cmds.parent() can find
    the parent before its children are processed."""
    by_name = {j.name: j for j in joints}
    sorted_list = []
    visited = set()

    def visit(j):
        if j.name in visited:
            return
        visited.add(j.name)
        if j.parent and j.parent in by_name:
            visit(by_name[j.parent])
        sorted_list.append(j)

    for j in joints:
        visit(j)
    return sorted_list


def _spawn_pivots_for_component(cls, cid: str, cjoints: list, options: dict,
                                blueprint=None) -> None:
    """Spawn pivot locators for one component. Helper extracted from
    _sync_component_pivots so the scene-only path can reuse it.

    Args take what the old loop body read from a ComponentSpec:
    - cls: the component class (from get_component_class(cdata.type))
    - cid: resolved component id (cdata.id if cdata.id else cls.default_id(cdata.joints))
    - cjoints: list of joint short names (from cdata.joints)
    - options: dict of component options (from cdata.options)
    - blueprint: optional Blueprint object passed through to
      resolve_extra_guide_default (required when joints aren't in the scene yet).

    The extra_guides gate (CONTRACT.extra_guides check) is done by callers;
    this function may be called for any component that has extra_guides.

    Pivot locator naming convention: f'{cid}_{eg.name}_pivot'
    where cid is the component id and eg.name is the ExtraGuide name.
    Attrs on each locator: fab_extra_guide_owner (string), fab_extra_guide_name (string).

    An ExtraGuide declaring eg.parent nests its locator under that sibling
    guide's locator instead of directly under fab_pivots_grp (IKLeg's toe_tip
    rides the heel, so dragging the heel carries the toe). Positions are always
    captured/restored in worldspace, so the nesting is transform-transparent.
    """
    grp = nodes.ensure_pivots_grp()

    # Find existing locators by owner+name to avoid duplicate spawns.
    existing = {}  # (owner_id, guide_name) -> locator name
    for loc in (cmds.listRelatives(grp, allDescendents=True, type='transform') or []):
        if not cmds.attributeQuery('fab_extra_guide_owner', node=loc, exists=True):
            continue
        owner = cmds.getAttr(f'{loc}.fab_extra_guide_owner')
        name  = cmds.getAttr(f'{loc}.fab_extra_guide_name')
        existing[(owner, name)] = loc

    def _dag_parent_for(eg) -> str:
        """Where eg's locator belongs: the anchor guide's locator when eg.parent
        is declared and already spawned, else the pivots group."""
        if eg.parent:
            anchor = existing.get((cid, eg.parent))
            if anchor and cmds.objExists(anchor):
                return anchor
        return grp

    for eg in cls.CONTRACT.extra_guides:
        want_parent = _dag_parent_for(eg)

        if (cid, eg.name) in existing:
            # Already spawned. Re-home it if the contract's nesting changed
            # under it (cmds.parent preserves worldspace, so the fitted
            # position survives).
            loc = existing[(cid, eg.name)]
            current = (cmds.listRelatives(loc, parent=True) or [None])[0]
            if current != want_parent.split('|')[-1]:
                cmds.parent(loc, want_parent)
            continue

        captured = options.get(f'{eg.name}_position')
        if captured and len(captured) == 3:
            pos = tuple(captured)
        else:
            pos = cls.resolve_extra_guide_default(eg.name, cjoints, blueprint)

        loc_name = f'{cid}_{eg.name}_pivot'
        loc = com.build_shape(eg.shape, loc_name)
        cmds.parent(loc, want_parent)
        cmds.xform(loc, ws=True, t=pos)

        cmds.addAttr(loc, ln='fab_extra_guide_owner', dt='string')
        cmds.setAttr(f'{loc}.fab_extra_guide_owner', cid, type='string')
        cmds.addAttr(loc, ln='fab_extra_guide_name', dt='string')
        cmds.setAttr(f'{loc}.fab_extra_guide_name', eg.name, type='string')

        if eg.side_aware and cjoints:
            side = side_tokens.detect_side(cjoints[0])
            _color_guide_curve(loc, color_index=side_tokens.SIDE_COLORS[side])
        else:
            _color_guide_curve(loc, color_index=eg.color)

        # Register immediately so a later guide naming this one as its parent
        # nests under it in this same pass (declaration order is spawn order).
        existing[(cid, eg.name)] = loc


def _sync_component_pivots(blueprint) -> None:
    """Spawn pivot locators for every component declaring extra_guides.

    Spawns into fab_pivots_grp (sibling lifecycle to ksfab_guides_grp).
    For each (component, extra_guide), if a locator with that owner+name
    already exists in the scene, leave it alone. Otherwise spawn at the
    captured option position (returning user) or resolve_extra_guide_default
    (first-time fitting).

    Each locator is tagged with fab_extra_guide_owner + _name so
    _capture_pivots_to_options can map them back. Side-aware coloring
    matches joint guides.

    Idempotent — safe to call multiple times. No-op for components with
    empty extra_guides.
    """
    if not blueprint:
        return

    for cdata in blueprint.components:
        cls = get_component_class(cdata.type)
        if not cls.CONTRACT.extra_guides:
            continue
        cid = cdata.id if cdata.id else cls.default_id(cdata.joints)
        cjoints = list(cdata.joints)
        options = dict(cdata.options)
        _spawn_pivots_for_component(cls, cid, cjoints, options, blueprint)


def _sync_component_pivots_from_scene() -> None:
    """Walk component network nodes, spawn pivot locators for each
    component declaring extra_guides. Scene-only version of
    _sync_component_pivots (which takes a blueprint).
    """
    for cnode in nodes.get_all_component_nodes():
        ctype = nodes.get_component_type(cnode)
        try:
            cls = get_component_class(ctype)
        except KeyError:
            continue
        if not cls.CONTRACT.extra_guides:
            continue
        cid = nodes.get_component_id(cnode)
        cjoints = [j.split('|')[-1].split(':')[-1]
                   for j in nodes.get_component_joints(cnode)]
        if not cjoints:
            continue
        options = nodes.get_component_options(cnode)
        _spawn_pivots_for_component(cls, cid, cjoints, options)


class MissingAimersError(RuntimeError):
    """Raised when a build hits joints whose aimer curves were deleted
    (viewport delete leaves the offset node behind). Carries `.missing`
    so the UI can prompt instead of dumping a stack trace."""

    def __init__(self, missing):
        self.missing = list(missing)
        super().__init__(
            f'{len(self.missing)} joint(s) have no aimer curve (deleted '
            f'in the viewport?): {", ".join(self.missing)}. Run Aim '
            f'Joints at Aimers to re-create them, or build with '
            f'force_missing_aimers=True to skip them and keep their '
            f'current orientation.')


def find_missing_aimers() -> list:
    """Joints tracked by the aimer system whose aimer CURVE is gone
    (viewport-deleted; the offset node lingers). UI pre-build guard
    queries this to prompt before the build starts."""
    return [j for j in joint_orient_app.get_all_aimer_joints()
            if not cmds.objExists(joint_orient_app.aimer_name(j))]


def _capture_aimer_state_to_registry() -> None:
    """Snapshot every live aimer's state onto the registry as JSON.

    The Armature lifecycle deletes aimers at Animation-Rig build; this
    blob is what unbuild reads to bring them back exactly as they were
    (spec 2026-07-04 §9). Follows the bind_pose_json pattern."""
    reg = nodes.get_registry()
    if not reg:
        return
    state = {}
    for j in joint_orient_app.get_all_aimer_joints():
        s = joint_orient_app.get_aimer_state(j)
        if s:
            state[j] = s
    from maya_tools.utils.maya import network_nodes as nn
    nn.ensure_string_attr(reg, 'aimer_state_json', default='{}')
    cmds.setAttr(f'{reg}.aimer_state_json', json.dumps(state),
                 type='string')


def find_stale_restored_aimers(tol_deg: float = 5.0) -> list:
    """Live aimers whose WORLD orientation disagrees with their joint's
    by more than tol_deg (quaternion angle). Returns [(joint, deg), ...].

    A healthy lifecycle keeps aimer == joint: the bake orients the joint
    to its aimer, and both the aimer snapshot and the bind-pose snapshot
    are captured from that agreed state, so a restore reproduces the
    agreement. A disagreement right after a registry restore therefore
    means the STORED aimer state is stale or contaminated (e.g. captured
    under the pre-2026-07-14 clobbering bugs) — and the next Armature
    bake would silently re-orient the joint to the bad aimer. That is
    exactly the "two right-leg aimers flipped on unbuild" incident
    (Adrian, Reggie, 2026-07-14): the code faithfully replayed poisoned
    data with no tell. Detection only — never mutates (validators WARN,
    never fix)."""
    import math
    import maya.api.OpenMaya as om

    def _world_quat(node):
        m = om.MMatrix(cmds.xform(node, q=True, ws=True, matrix=True))
        return om.MTransformationMatrix(m).rotation(asQuaternion=True)

    stale = []
    for j in joint_orient_app.get_all_aimer_joints():
        ctl = joint_orient_app.aimer_name(j)
        if not (cmds.objExists(ctl) and cmds.objExists(j)):
            continue
        qa, qj = _world_quat(ctl), _world_quat(j)
        dot = abs(qa.x * qj.x + qa.y * qj.y + qa.z * qj.z + qa.w * qj.w)
        ang = math.degrees(2.0 * math.acos(min(1.0, dot)))
        if ang > tol_deg:
            stale.append((j, round(ang, 1)))
    return stale


def _restore_aimers_from_registry() -> int:
    """Recreate aimers from the registry snapshot taken at build time.
    Joints missing from the scene are skipped. Returns the count
    restored. Aimers for joints absent from the snapshot are created
    by the Armature build's ensure pass (natural-aim detection).
    Warns (never fixes) when a restored aimer disagrees with the built
    pose — see find_stale_restored_aimers."""
    reg = nodes.get_registry()
    if not reg or not cmds.attributeQuery('aimer_state_json', node=reg,
                                          exists=True):
        return 0
    raw = cmds.getAttr(f'{reg}.aimer_state_json') or '{}'
    try:
        state = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        state = {}
    count = 0
    for j, s in state.items():
        if not cmds.objExists(j) or cmds.nodeType(j) != 'joint':
            continue
        if not joint_orient_app.aimer_exists(j):
            joint_orient_app.create_aimer(j)
        # world_rotation (when the snapshot has it) restores the aimer's
        # WORLD orientation frame-independently; legacy snapshots without
        # it fall back to the local offset (2026-07-14).
        joint_orient_app.apply_aimer_state(
            j, aim_target=s.get('aim_target', ''),
            aim_offset=list(s.get('aim_offset', [0.0, 0.0, 0.0])),
            world_rotation=s.get('world_rotation'))
        count += 1

    if count:
        stale = find_stale_restored_aimers()
        if stale:
            names = ', '.join(f'{j} ({d} deg)' for j, d in stale)
            cmds.warning(
                f'[Fabricator] {len(stale)} restored aimer(s) disagree with '
                f'the built pose: {names}. The stored aimer state is likely '
                f'STALE or contaminated (e.g. a scene saved before the '
                f'2026-07-14 lifecycle fixes) — the next Armature bake would '
                f're-orient those joints to the bad aimers. Re-author or '
                f're-aim the flagged aimers before building. Joints were '
                f'NOT changed.')
    return count


def _armature_teardown_for_build(force_missing_aimers: bool = False) -> None:
    """Armature lifecycle prologue for Animation-Rig build (spec
    2026-07-04 §9): the Armature and aimers never survive into the
    Animation Rig.

    Order: capture aimer state to the registry → delete the Armature →
    orientation bake (orient joints to aimers; deletes the aimers) →
    validate the export contract. (Skin reconnect is NOT here anymore —
    build_modules brackets the whole build with disconnect at the start
    and reconnect dead last; Adrian, 2026-07-13.)

    Raises MissingAimersError when aimer curves were deleted in the
    viewport and force_missing_aimers is False; with True they are
    skipped and keep their current orientation.
    """
    missing = find_missing_aimers()
    if missing and not force_missing_aimers:
        raise MissingAimersError(missing)

    had_aimers = bool(joint_orient_app.get_all_aimer_joints())
    if had_aimers:
        _capture_aimer_state_to_registry()
    if armature.armature_exists():
        armature.delete_armature()
    if had_aimers:
        # Missing aimers were either vetoed above or explicitly forced —
        # always skip-through here.
        joint_orient_app.orient_all_aimers(force=True)

    # Contract normalizer over the whole skeleton — catches joints the
    # bake didn't cover (legacy scenes, aimers deleted by hand).
    # World transforms are untouched; JO/RA fold into rotate.
    ks_root = nodes.get_registry_root_joint()
    if ks_root and cmds.objExists(ks_root):
        descendants = cmds.listRelatives(ks_root, allDescendents=True,
                                         type='joint') or []
        joint_orient_app.collapse_joint_orient([ks_root] + descendants)

    if ks_root and cmds.objExists(ks_root):
        violations = armature.validate_orientation_contract()
        if violations:
            raise RuntimeError(
                'Rig cannot be built — export orientation contract '
                'violations (rotate carries orient, jointOrient=0, '
                'rotateAxis=0). Run Aim Joints at Aimers, then build '
                'again:\n\n• ' + '\n• '.join(violations))
    else:
        # Legacy guard (2026-07-05, same as the unbuild epilogue): a
        # pre-registry rig has no registry root — build proceeds, the
        # Armature-era validation just can't run.
        cmds.warning(
            'Build: no registry root joint — export orientation '
            'contract not validated (pre-registry rig). The build '
            'proceeds; verify orientation in-engine, or modernize '
            'via registry adoption (File > New Rig would delete the '
            'authored modules).')


def _capture_skeleton_bind_pose(blueprint) -> None:
    """Snapshot every skeleton joint's local TRS + jointOrient + preferredAngle
    onto the registry node as a JSON blob. Paired with
    _restore_skeleton_bind_pose to make build/unbuild cycles non-destructive
    on the bind chain — a build that happens to lock the IK solver bakes
    the locked pose into bind.rotate via the BIND constraint's per-frame
    output, and without a restore, unbuild leaves bind in that locked state.

    Called at build_modules start, just before any component build runs.
    Overwrites the saved blob each call (the assumption: bind is at its
    intended state at build start, e.g. the user just loaded a fresh
    skeleton or the prior unbuild restored it).
    """
    reg = nodes.get_registry()
    if not reg:
        return
    pose = {}
    for jspec in blueprint.skeleton_joints:
        j = jspec.name
        if not cmds.objExists(j):
            continue
        pose[j] = {
            't':  list(cmds.getAttr(f'{j}.translate')[0]),
            'r':  list(cmds.getAttr(f'{j}.rotate')[0]),
            's':  list(cmds.getAttr(f'{j}.scale')[0]),
            'jo': list(cmds.getAttr(f'{j}.jointOrient')[0]) if cmds.attributeQuery(
                'jointOrient', node=j, exists=True) else [0.0, 0.0, 0.0],
            'pa': list(cmds.getAttr(f'{j}.preferredAngle')[0]) if cmds.attributeQuery(
                'preferredAngle', node=j, exists=True) else [0.0, 0.0, 0.0],
        }
        # Blueprint-parentless joints (the root): locals are relative to
        # whatever Maya parent the root has NOW (e.g. a UE import's -90
        # group), but the build reparents the root under the identity top
        # group (_organize_scene) BEFORE unbuild restores this snapshot —
        # replaying group-relative locals in the new frame pitched the
        # whole skeleton 90 deg (face-plant, found 2026-07-14). Capture
        # the world matrix too; restore drives the root by world.
        if not jspec.parent:
            pose[j]['world_m'] = cmds.xform(j, q=True, ws=True, matrix=True)
    from maya_tools.utils.maya import network_nodes as nn
    nn.ensure_string_attr(reg, 'bind_pose_json', default='{}')
    cmds.setAttr(f'{reg}.bind_pose_json', json.dumps(pose), type='string')


def _restore_skeleton_bind_pose(blueprint) -> None:
    """Restore every skeleton joint to the snapshot taken by
    _capture_skeleton_bind_pose. Called at unbuild_modules end after rig_grp
    has been deleted (which cascades constraint deletion). No-op if no
    snapshot exists or registry is gone.
    """
    reg = nodes.get_registry()
    if not reg or not cmds.attributeQuery('bind_pose_json', node=reg, exists=True):
        return
    raw = cmds.getAttr(f'{reg}.bind_pose_json') or '{}'
    pose = json.loads(raw)
    for j, vals in pose.items():
        if not cmds.objExists(j):
            continue
        cmds.setAttr(f'{j}.translate',      *vals['t'])
        cmds.setAttr(f'{j}.rotate',         *vals['r'])
        cmds.setAttr(f'{j}.scale',          *vals['s'])
        if cmds.attributeQuery('jointOrient', node=j, exists=True):
            cmds.setAttr(f'{j}.jointOrient',     *vals.get('jo', [0.0, 0.0, 0.0]))
        if cmds.attributeQuery('preferredAngle', node=j, exists=True):
            cmds.setAttr(f'{j}.preferredAngle',  *vals.get('pa', [0.0, 0.0, 0.0]))
        # Root (blueprint-parentless): world wins over the group-relative
        # locals just applied — the build may have reparented it since the
        # capture (see _capture_skeleton_bind_pose, 2026-07-14 face-plant).
        if 'world_m' in vals:
            cmds.xform(j, ws=True, matrix=vals['world_m'])


def _capture_pivots_to_options_from_scene() -> None:
    """Walk pivot locators in the scene; for each, find the owning
    component network node and write the world position into the
    component's options under '<guide_name>_position'.

    Uses fab_extra_guide_owner / fab_extra_guide_name attrs on each
    locator to find the owning component — rename-resilient, no name parsing.
    Idempotent — no-op if pivots_grp doesn't exist.
    """
    pivots_grp = nodes.get_pivots_grp()
    if not pivots_grp:
        return
    locators = cmds.listRelatives(pivots_grp, allDescendents=True,
                                  type='transform', fullPath=True) or []
    if not locators:
        return

    # Build component node lookup: component_id -> cnode.
    reg = nodes.get_registry()
    cnode_by_id = {}
    for cnode in nodes.get_all_component_nodes(reg):
        cnode_by_id[nodes.get_component_id(cnode)] = cnode

    for loc in locators:
        if not cmds.attributeQuery('fab_extra_guide_owner', node=loc, exists=True):
            continue
        owner_id   = cmds.getAttr(f'{loc}.fab_extra_guide_owner')
        guide_name = cmds.getAttr(f'{loc}.fab_extra_guide_name')
        cnode = cnode_by_id.get(owner_id)
        if cnode is None:
            cmds.warning(f'Pivot locator {loc!r} references unknown component '
                         f'{owner_id!r}; position not captured.')
            continue
        world_t = cmds.xform(loc, q=True, ws=True, t=True)
        key = f'{guide_name}_position'
        current = nodes.get_component_options(cnode)
        if current.get(key) != list(world_t):
            current[key] = list(world_t)
            nodes.set_component_options(cnode, current)


def mirror_guide_position() -> dict:
    """Mirror selected guide(s) across the YZ plane onto their opposite-side
    counterpart. Selection-driven, CurveOMatic-style: select the guide you've
    positioned, the matching opposite-side guide snaps to the mirrored world
    position (negate X). Position only -- joint-guide orientation is recomputed
    downstream by Joint Orient, and IKLeg pivots are point locators.

    Two guide kinds:
      - Joint guides ('<joint>_guide', tagged fab_joint_name): the guide IS
        the truth, so just snap the opposite guide's world position.
      - Extra-guide pivots (IKLeg heel/toe-tip locators, tagged
        fab_extra_guide_owner/_name): snap the opposite locator AND persist
        the mirrored position into the opposite component's
        options['<name>_position'] so build/resync keep it.

    Returns: {'pairs': [(src_short, dst_short), ...],
              'skipped': [(node_short, reason), ...]}
    Raises:  RuntimeError on empty selection or when nothing could be mirrored.
    """
    from maya_tools.framework.decorators import undo_chunk

    sel = cmds.ls(selection=True, long=True) or []
    if not sel:
        raise RuntimeError('Mirror Guide Position: select a guide first.')

    pairs: list = []
    skipped: list = []
    with undo_chunk('Mirror Guide Position'):
        for src in sel:
            short = src.split('|')[-1]
            if cmds.attributeQuery('fab_joint_name', node=src, exists=True):
                dst = _opposite_joint_guide(src)
                if not dst:
                    skipped.append((short, 'center-line or no opposite joint guide'))
                    continue
                _snap_mirror_position(src, dst)
                pairs.append((short, dst))
            elif cmds.attributeQuery('fab_extra_guide_owner', node=src, exists=True):
                dst = _mirror_extra_guide_pivot(src)
                if not dst:
                    skipped.append((short, 'center-line or no opposite pivot'))
                    continue
                pairs.append((short, dst.split('|')[-1]))
            else:
                skipped.append((short, 'not a guide'))

    if not pairs:
        reasons = '; '.join(f'{n}: {r}' for n, r in skipped) or 'no valid guides selected'
        raise RuntimeError(f'Mirror Guide Position: nothing mirrored ({reasons}).')
    return {'pairs': pairs, 'skipped': skipped}


def _snap_mirror_position(src: str, dst: str) -> None:
    """Snap dst's world position to the YZ-mirror (negate X) of src's."""
    pos = cmds.xform(src, q=True, ws=True, t=True)
    cmds.xform(dst, ws=True, t=side_tokens.mirror_point(pos))


def _opposite_joint_guide(src: str):
    """Opposite-side joint guide for a selected joint guide. Guides are named
    '<joint>_guide', so flip the side token in the source's joint name. None if
    center-line (no side token) or the opposite guide isn't in the scene."""
    jname = cmds.getAttr(f'{src}.fab_joint_name')
    flipped = side_tokens.flip_side_token(jname)
    if not flipped or flipped == jname:
        return None
    dst = f'{flipped}_guide'
    return dst if cmds.objExists(dst) else None


def _mirror_extra_guide_pivot(src: str):
    """Mirror an IKLeg-style extra-guide pivot locator to its opposite. Snaps
    the opposite locator AND persists the mirrored position into the opposite
    component's options so build/resync keep it. Returns the dst long-name, or
    None if center-line or no opposite pivot/component exists."""
    owner = cmds.getAttr(f'{src}.fab_extra_guide_owner')
    name = cmds.getAttr(f'{src}.fab_extra_guide_name')
    flipped_owner = side_tokens.flip_side_token(owner)
    if not flipped_owner or flipped_owner == owner:
        return None
    dst = _find_extra_guide_pivot(flipped_owner, name)
    if not dst:
        return None
    mirrored = side_tokens.mirror_point(cmds.xform(src, q=True, ws=True, t=True))
    cmds.xform(dst, ws=True, t=mirrored)
    dst_cnode = nodes.find_component_node_by_id(flipped_owner)
    if dst_cnode:
        opts = nodes.get_component_options(dst_cnode)
        opts[f'{name}_position'] = list(mirrored)
        nodes.set_component_options(dst_cnode, opts)
    return dst


def _find_extra_guide_pivot(owner_id: str, guide_name: str):
    """Find the extra-guide pivot locator for a component id + guide name by its
    fab_extra_guide_owner/_name attrs (rename-resilient). None if not found."""
    pivots_grp = nodes.get_pivots_grp()
    if not pivots_grp:
        return None
    for loc in cmds.listRelatives(pivots_grp, allDescendents=True,
                                  type='transform', fullPath=True) or []:
        if not cmds.attributeQuery('fab_extra_guide_owner', node=loc, exists=True):
            continue
        if (cmds.getAttr(f'{loc}.fab_extra_guide_owner') == owner_id
                and cmds.getAttr(f'{loc}.fab_extra_guide_name') == guide_name):
            return loc
    return None


def mirror_component(component_id: str) -> str:
    """Mirror an existing component to the opposite side. One-shot.

    Reads L component's network-node state, derives R side via
    side_tokens.flip_side_token on every joint name + the parent_plug
    component-id prefix, creates the R component network node with
    flipped ctrl_color + verbatim options. User ctrl-shape edits mirror
    at the NEXT BUILD (persisted.pending_cv_mirror -> the build_modules
    tail world-YZ-mirrors every source ctrl curve onto its counterpart,
    one-shot).

    Returns the new component_id. Raises RuntimeError on:
    - missing L component
    - any R joint missing in the scene (caller should run Mirror Joints first)
    - any R component already existing for the resolved R joints
    - rig already built (Edit Mode only — foundational pillar)

    Per-instance space targets are NOT mirrored — BY DESIGN (Adrian
    ratified 2026-07-05: wipe on mirror, no smart side-matching this
    version). The R component starts with contract-default spaces
    only; the closing warning tells the artist to re-add in
    Properties. Duplicate is the opposite: it copies user spaces.
    parent_plug is best-effort flipped (component-id prefix); verbatim
    fallback with a logger warning if the flipped target doesn't exist.
    """
    # Sweep orphan component network nodes left over from joint deletes.
    # Without this, a previously-mirrored R component whose joints were
    # later deleted would block re-mirror via the id-collision guard,
    # and Build Modules would re-build the zombie components.
    n_orphans = nodes.cleanup_orphan_components()
    if n_orphans:
        cmds.warning(f'Mirror Modules: cleaned {n_orphans} orphan '
                     f'component(s) before mirror.')

    # Edit Mode gate (defensive — UI hides the button in Animation Mode,
    # but a scripted call should also bounce off this check.)
    for cnode in nodes.get_all_component_nodes():
        if nodes.is_built(cnode):
            raise RuntimeError(
                'Mirror Modules: rig is built. Edit Rig (unbuild) first; '
                'editing operations live in Edit Mode only.'
            )

    l_node = nodes.find_component_node_by_id(component_id)
    if not l_node:
        raise RuntimeError(f"Mirror Modules: component {component_id!r} not found.")

    ctype = nodes.get_component_type(l_node)
    l_side = nodes.get_component_side(l_node)
    l_joints = nodes.get_component_joints(l_node)
    l_joint_names = nodes.get_component_joint_names(l_node)
    l_options = nodes.get_component_options(l_node)
    l_persisted = nodes.get_component_persisted(l_node)
    l_parent_plug = nodes.get_component_parent_plug(l_node)
    l_role = nodes.get_component_role(l_node)
    l_region = nodes.get_component_region(l_node)

    # Resolve R joints via token flip — short names.
    r_joints = []
    for jn in (l_joint_names or l_joints):
        short = jn.split('|')[-1].split(':')[-1]
        r_short = side_tokens.flip_side_token(short)
        if r_short is None:
            raise RuntimeError(
                f"Mirror Modules: joint {short!r} has no side token; "
                f"cannot derive R counterpart."
            )
        if not cmds.objExists(r_short):
            raise RuntimeError(
                f"Mirror Modules: R joint {r_short!r} does not exist. "
                f"Run Mirror Joints first, or hand-author the joint."
            )
        r_joints.append(r_short)

    # Conflict check — does any existing component own these R joints?
    for cnode in nodes.get_all_component_nodes():
        existing = [j.split('|')[-1].split(':')[-1]
                    for j in (nodes.get_component_joints(cnode) or [])]
        if existing and set(existing) == set(r_joints):
            cid = nodes.get_component_id(cnode)
            raise RuntimeError(
                f"Mirror Modules: component {cid!r} already owns joints "
                f"{sorted(r_joints)}. Remove it first to re-mirror."
            )

    # Flip side.
    r_side = side_tokens.flip_side_color(l_side)

    # Derive R component id — flip the L id's side token.
    r_id = side_tokens.flip_side_token(component_id)
    if r_id is None or r_id == component_id:
        # Component id has no side token — fall back to suffixing.
        r_id = f'{component_id}_mirror'
        cmds.warning(
            f"Mirror Modules: could not flip side token in component id "
            f"{component_id!r}; using {r_id!r}. Rename later if desired."
        )
    existing_r_node = nodes.find_component_node_by_id(r_id)
    if existing_r_node:
        # Auto-clean orphan components — the R component network node
        # exists but its joints[] multi has zero live targets. This
        # happens when the artist deletes R joints to redo a mirror;
        # Maya cleans the message connections but the orphan network
        # node stays. Without auto-clean the re-mirror loop is stuck.
        existing_joints = nodes.get_component_joints(existing_r_node) or []
        live = [j for j in existing_joints if cmds.objExists(j)]
        if not live:
            cmds.warning(
                f"Mirror Modules: orphan component {r_id!r} has no live "
                f"joints; deleting and re-creating."
            )
            cmds.delete(existing_r_node)
        else:
            raise RuntimeError(
                f"Mirror Modules: component id {r_id!r} already exists. "
                f"Remove it first to re-mirror."
            )

    # Best-effort parent_plug flip.
    r_parent_plug = l_parent_plug
    if l_parent_plug and '.' in l_parent_plug:
        parent_id, plug = l_parent_plug.split('.', 1)
        flipped = side_tokens.flip_side_token(parent_id)
        if flipped and flipped != parent_id:
            if nodes.find_component_node_by_id(flipped):
                r_parent_plug = f'{flipped}.{plug}'
            else:
                cmds.warning(
                    f"Mirror Modules: parent_plug {l_parent_plug!r} resolved "
                    f"to {flipped!r} on the R side but no such component "
                    f"exists; keeping verbatim. Verify after build."
                )

    # ctrl_color flips with the side (lf=blue / rt=red — side seeding
    # RESTORED 2026-07-14, reverting the 2026-07-12 family-color
    # crossing): a source color still sitting on its own side's default
    # flips to the mirrored side's default; anything else is a
    # deliberate rigger pick and crosses verbatim.
    r_options = dict(l_options)
    if r_options.get('ctrl_color') == side_tokens.side_to_ctrl_color(l_side):
        r_options['ctrl_color'] = side_tokens.side_to_ctrl_color(r_side)

    # Flip joint-role-style option values that reference joint names.
    # Apply flip_side_token to any string value (bare, or nested one
    # level inside a list); if the result resolves to an existing joint
    # name and differs from the original, swap.
    #
    # dict-shaped list items pass through verbatim, unflipped. Finger/
    # twist membership lives on the component's fab_limb node and is
    # DERIVED fresh for the mirrored side (nodes.derive_limb inside
    # create_component_node below) — never flipped through this loop.
    for k, v in list(r_options.items()):
        if isinstance(v, str):
            flipped = side_tokens.flip_side_token(v)
            if flipped and flipped != v and cmds.objExists(flipped):
                r_options[k] = flipped
        elif isinstance(v, list):
            new_v = []
            for item in v:
                if isinstance(item, str):
                    flipped = side_tokens.flip_side_token(item)
                    new_v.append(flipped if flipped and cmds.objExists(flipped) else item)
                else:
                    new_v.append(item)
            r_options[k] = new_v

    # Ctrl shapes: do NOT copy cv_data across (Adrian, 2026-07-12). Its
    # keys name the SOURCE side's ctrls/shape slots (they'd never match
    # the R build's lookups), and a naive local-X CV flip lands rolled
    # 180° on mirror-aimed frames (the clavicle lesson — see
    # curve_mirror). Instead, flag the new component: the build_modules
    # tail runs curve_mirror.mirror_component_ctrls once BOTH sides'
    # ctrls exist live, world-YZ-mirroring every source ctrl curve onto
    # its counterpart, then clears the flag. One-shot by design — later
    # sculpts on the mirrored side are never stomped by a rebuild; the
    # next unbuild captures the mirrored shapes as normal user edits.
    # Strip RECURSIVELY: ribbon components nest per-segment cv_data
    # (persisted.ribbon_segments.<seg>.cv_data) — a top-level pop leaves
    # source-side-keyed junk riding into the mirrored component (seen
    # live on reggie's R arm, 2026-07-12).
    r_persisted = _strip_cv_data_blocks(l_persisted)
    r_persisted['pending_cv_mirror'] = component_id

    # Create the R component network node. Derived Limbs (spec
    # 2026-07-11): nodes.derive_limb fires inside create_component_node
    # and gives the mirrored side its OWN limb — fingers/twists derived
    # fresh off R's live subtree, region-bounded so it can never adopt
    # an ancestor (e.g. spine) limb. Nothing limb-related is flipped
    # from L: fresh derivation of R is strictly more correct than
    # copying the source side's lists.
    r_node = nodes.create_component_node(
        component_id=r_id,
        component_type=ctype,
        joints=r_joints,
        parent_plug=r_parent_plug,
        side=r_side,
        options=r_options,
        persisted=r_persisted,
        role=l_role,
        region=l_region,
        joint_names=r_joints,
    )

    # Log the deferred-by-design limitations so the artist sees them.
    cmds.warning(
        f"Mirror Modules: created {r_id!r}. Ctrl shape edits mirror over "
        f"on the next Build Rig. Note: per-instance space targets are "
        f"NOT mirrored (R component starts with empty user spaces); "
        f"inherit contract defaults only. Re-add space targets in "
        f"Properties if needed."
    )

    return r_id


# (_mirror_cv_data is retired, 2026-07-12: the local-X CV flip it did is
# wrong for mirror-aimed ctrl frames AND its keys named the source side's
# ctrls, so the mirrored component never matched them at build. Mirror
# Modules now defers to curve_mirror.mirror_component_ctrls in the
# build_modules tail via persisted.pending_cv_mirror.)


def _strip_cv_data_blocks(persisted: dict) -> dict:
    """A deep copy of a persisted dict with every cv_data-ish block
    removed, at ANY nesting depth ('cv_data', '*_cv_data' — including
    ribbon components' per-segment persisted.ribbon_segments.<seg>.cv_data).
    Used by mirror_component: those blocks are keyed by the SOURCE side's
    ctrl names and must never ride into the mirrored component."""
    out = {}
    for k, v in (persisted or {}).items():
        if k == 'cv_data' or k.endswith('_cv_data'):
            continue
        out[k] = _strip_cv_data_blocks(v) if isinstance(v, dict) else v
    return out


# (Derived Limbs, spec 2026-07-11: _mirror_limb_membership and its
# subtree guards are retired — the mirrored side derives its own
# fingers/twists fresh via nodes.derive_limb at component creation.)


def _auto_disconnect_mirrors() -> int:
    """At Build Modules start, sever any active joint mirror networks on
    the rig's joints. Mirror constraints fight the connector_null
    parentConstraint chain — Build Modules is the bake.

    Returns the count of mirror nodes deleted. Logged as info if > 0.
    """
    from maya_tools.skeleton.joint_mirror.joint_mirror_app import disconnect_mirror

    # Sweep all joints currently owned by components. Disconnect operates
    # idempotently; no-op for joints without active mirrors.
    all_joints = []
    for cnode in nodes.get_all_component_nodes():
        all_joints.extend(nodes.get_component_joints(cnode) or [])
    if not all_joints:
        return 0
    return disconnect_mirror(all_joints)


def find_unbuildable_components() -> list:
    """Return components whose build joints no longer exist in the scene.

    Mirrors builder.validate_blueprint rule 7 (every component joint must
    appear in the skeleton block) against live scene state, so the result is
    exactly the set that would otherwise abort build_modules with a
    'references joint ... not in the skeleton block' error — the case where
    an artist deletes joints in the viewport (e.g. pauldrons) and the modules
    bound to them are left dangling.

    Each component's joints are resolved the same way the snapshot does
    (_snapshot_single_component_spec): live message targets, falling back to
    the frozen joint_names mirror when the multi is empty (the joint-deleted
    case, where the dangling names are precisely what trips the validator).

    Returns a list of dicts: {'node', 'id', 'type', 'missing_joints'}.

    Gated on at least one component having live joints — the post-Build-
    Skeleton signal — so a freshly-loaded template (joints not created yet,
    every component legitimately jointless) never false-positives.
    """
    all_components = nodes.get_all_component_nodes()
    if not any(nodes.get_component_joints(c) for c in all_components):
        return []
    skeleton_names = {j.name for j in _snapshot_skeleton_from_scene()}
    out = []
    for cnode in all_components:
        live = [j.split('|')[-1].split(':')[-1]
                for j in nodes.get_component_joints(cnode)]
        effective = live or nodes.get_component_joint_names(cnode)
        missing = [jn for jn in effective if jn not in skeleton_names]
        if missing:
            out.append({
                'node': cnode,
                'id': nodes.get_component_id(cnode) or cnode,
                'type': nodes.get_component_type(cnode),
                'missing_joints': missing,
            })
    return out


def find_misparented_ctrls() -> list:
    """Fabricator-owned controls parented outside their expected
    hierarchy (report-only structural check).

    Two populations: fab_role-tagged ctrls, whose ancestor chain must
    reach fab_controls_grp; and *_amt_CTL armature ctrls (untagged by
    design), whose chain must reach fab_armature_grp. Returns
    [{'node', 'parent', 'expected'}]. Empty scene / no registry -> [].

    Chain checks compare FULL paths against each group's one canonical
    location (armature grp = the node literally at `|fab_armature_grp`;
    controls grp = the node at `|rig_grp|fab_controls_grp`), not short
    names — a decoy node elsewhere in the scene sharing either group's
    short name must not launder a real misparent into a clean report.
    Results are also deduplicated to one hit per broken subtree root
    (a misparented ancestor's descendants aren't reported again).

    Namespaced/referenced rigs are out of scope for v1: the tagged-ctrl
    enumeration (`cmds.ls('*.fab_role', ...)`) does not cross
    namespace boundaries.
    """
    if not nodes.get_registry():
        return []

    def _chain_reaches(node, target_full):
        cur = node
        while True:
            par = cmds.listRelatives(cur, parent=True, fullPath=True)
            if not par:
                return False
            cur = par[0]
            if cur == target_full:
                return True

    hits = []

    # Armature population: canonical target is the fab_armature_grp
    # that is a direct child of world.
    armature_full = '|fab_armature_grp'
    if cmds.objExists(armature_full):
        for ctl in cmds.ls('*_amt_CTL', type='transform', long=True) or []:
            if not _chain_reaches(ctl, armature_full):
                par = cmds.listRelatives(ctl, parent=True) or ['<world>']
                hits.append({'node': ctl, 'parent': par[0],
                             'expected': 'fab_armature_grp'})

    # Tagged-ctrl population: canonical target is the fab_controls_grp
    # that lives under rig_grp (the one ensure_controls_grp creates).
    # Single-call enumeration of tagged ctrls (attributeQuery-per-
    # transform measured 85x slower on a mid-size rig scene).
    controls_full = '|rig_grp|fab_controls_grp'
    if cmds.objExists(controls_full):
        for ctl in cmds.ls('*.fab_role', o=True, long=True) or []:
            if not _chain_reaches(ctl, controls_full):
                par = cmds.listRelatives(ctl, parent=True) or ['<world>']
                hits.append({'node': ctl, 'parent': par[0],
                             'expected': 'fab_controls_grp'})

    # One issue per broken subtree root: shallow-to-deep, drop any hit
    # that is a descendant of an already-kept hit.
    hits.sort(key=lambda h: h['node'].count('|'))
    kept = []
    for h in hits:
        if any(h['node'].startswith(k['node'] + '|') for k in kept):
            continue
        kept.append(h)
    return kept


def find_renamed_component_joints() -> list:
    """Component joints whose live scene name no longer matches the name
    recorded on the component node (illegal-rename detection). Returns
    [{'component', 'node', 'recorded', 'live'}]. Skips components whose
    live/recorded lists differ in length (missing joints are
    find_unbuildable_components' report, not a rename).

    Live joints are read per explicit joints[] multi index (sorted), NOT
    via nodes.get_component_joints — get_message_targets explicitly
    disclaims index-order ('For ordered-by-index access, iterate the
    multi indices manually'), and the position-wise pairing against the
    recorded joint_names array must be index-true or a reordered return
    would produce false rename reports.
    """
    if not nodes.get_registry():
        return []
    hits = []
    for cnode in nodes.get_all_component_nodes():
        recorded = nodes.get_component_joint_names(cnode)
        if not recorded:
            continue
        if not cmds.attributeQuery('joints', node=cnode, exists=True):
            continue
        indices = cmds.getAttr(f'{cnode}.joints', multiIndices=True) or []
        if not indices or len(indices) != len(recorded):
            continue
        cid = nodes.get_component_id(cnode)
        for i, rec in zip(sorted(indices), recorded):
            src = cmds.listConnections(f'{cnode}.joints[{i}]',
                                       source=True, destination=False) or []
            if not src:
                continue
            jn_long = cmds.ls(src[0], long=True)[0]
            live = jn_long.split('|')[-1].split(':')[-1]
            if live != rec:
                hits.append({'component': cid, 'node': jn_long,
                             'recorded': rec, 'live': live})
    return hits


def delete_component_pivots(component_ids) -> int:
    """Delete every extra-guide pivot locator (heel/toe-tip/… under
    fab_pivots_grp) owned by any id in `component_ids`, by its
    fab_extra_guide_owner tag. Returns the count removed.

    Called when a component is genuinely DELETED (branch delete / Remove
    Component), NOT on drop-replace or unbuild where the pivots must
    carry to the replacement. Without this the locators outlived their
    leg: they floated in the viewport and, because _spawn_pivots_for_
    component de-dupes by (owner, name), a later same-id leg silently
    re-adopted the stale pivots ("delete the leg and they heal").
    Idempotent; no-op when pivots_grp is absent."""
    ids = {c for c in (component_ids or []) if c}
    if not ids:
        return 0
    grp = nodes.get_pivots_grp()
    if not grp:
        return 0
    doomed = []
    for loc in (cmds.listRelatives(grp, allDescendents=True,
                                   type='transform', fullPath=True) or []):
        if not cmds.attributeQuery('fab_extra_guide_owner', node=loc,
                                   exists=True):
            continue
        if cmds.getAttr(f'{loc}.fab_extra_guide_owner') in ids:
            doomed.append(loc)
    removed = 0
    # A nested pivot (toe_tip rides heel) dies with its parent, so guard
    # objExists — the child's stale full path is already gone by then.
    for loc in doomed:
        if cmds.objExists(loc):
            cmds.delete(loc)
            removed += 1
    return removed


def delete_components(component_nodes: list) -> int:
    """Delete the given component network nodes. Pre-build only — these are
    bare fab_<id> network nodes with no rig structure to unbuild, so a
    plain node delete is sufficient. Returns the count deleted.

    Extra-guide pivot locators owned by the deleted components go too
    (delete_component_pivots) — a deleted leg must take its heel/toe-tip
    pivots with it, not leave them orphaned under fab_pivots_grp.
    """
    deleted = 0
    doomed_cids = []
    for cnode in component_nodes:
        if cmds.objExists(cnode):
            cid = nodes.get_component_id(cnode)
            if cid:
                doomed_cids.append(cid)
            nodes.delete_component_node(cnode)
            deleted += 1
    delete_component_pivots(doomed_cids)
    return deleted


def find_overconnected_components() -> dict:
    """Find components whose joints[] message-multi holds MORE connections
    than the component's canonical joint count — the signature of viewport
    joint duplication (CLAUDE.md Fabricator gotcha #1, user-side variant:
    cmds.duplicate AND Maya's native mirrorJoint propagate each duplicated
    joint's outgoing .message connection, auto-extending the source
    component's joints[] multi at the next free index).

    Canonical joint count per component is len(joint_names) when the
    string mirror is non-empty and does not itself exceed the contract
    bound. (A mirror longer than the bound means contamination was
    laundered INTO joint_names — e.g. the Properties role-edit path
    writes the live multi back.) Blank '' entries (persisted by the
    role-edit invalidation cascade) count toward the length but can never
    name-match, so blank-bearing mirrors land in `manual` when
    over-connected. An unbounded FKChain with an unusable mirror is
    genuinely indeterminate (no bound to compare against) and is skipped.

    AUTO-REPAIR ONLY ON FULL-CONFIDENCE MATCH: a component is repairable
    only when EVERY canonical name resolves to a live connection. Keep =
    those matches in joint_names order (same semantics as Build
    Skeleton's _reconcile_component_joint_connections — mirror order is
    chain order, and joints[0] derives the component id); release = the
    rest. When any canonical name is missing from the live set, the
    original was deleted or renamed — indistinguishable cases, and
    guessing (e.g. keep-first-N) can graft a duplicate into the
    component and launder it into joint_names. Those components go to
    `manual` untouched: the build aborts loudly on them exactly as it
    did before this guard existed.

    Returns {'repairs': [...], 'manual': [...]}:
      repairs: [{'node','id','type','keep','release'}]
      manual:  [{'node','id','type','live_count','canonical','reason'}]
    """
    def _short(n: str) -> str:
        return n.split('|')[-1].split(':')[-1]

    repairs = []
    manual = []
    for cnode in nodes.get_all_component_nodes():
        live = nodes.get_component_joints(cnode)
        if not live:
            continue
        ctype = nodes.get_component_type(cnode)
        try:
            cls = get_component_class(ctype)
        except KeyError:
            continue
        cap = cls.CONTRACT.max_joints
        names = nodes.get_component_joint_names(cnode)
        cid = nodes.get_component_id(cnode) or cnode

        mirror_usable = bool(names) and (cap == -1 or len(names) <= cap)
        if mirror_usable:
            canonical = len(names)
            if len(live) <= canonical:
                continue
            by_short = {}
            for j in live:
                by_short.setdefault(_short(j), j)
            if all(nm and nm in by_short for nm in names):
                keep = [by_short[nm] for nm in names]
                keep_set = set(keep)
                repairs.append({
                    'node': cnode,
                    'id': cid,
                    'type': ctype,
                    'keep': keep,
                    'release': [j for j in live if j not in keep_set],
                })
            else:
                manual.append({
                    'node': cnode,
                    'id': cid,
                    'type': ctype,
                    'live_count': len(live),
                    'canonical': canonical,
                    'reason': ('saved joint names not all found among the '
                               'live connections — an original joint was '
                               'deleted or renamed. Fix by hand (Node '
                               'Editor) or remove/re-add the module.'),
                })
        elif cap != -1 and len(live) > cap:
            manual.append({
                'node': cnode,
                'id': cid,
                'type': ctype,
                'live_count': len(live),
                'canonical': cap,
                'reason': ('joint_names mirror missing or over the '
                           'contract bound — no safe way to pick which '
                           'joints to keep.'),
            })
        # else: unbounded contract + unusable mirror — indeterminate; skip.
    return {'repairs': repairs, 'manual': manual}


def release_extra_joint_connections(repairs: list) -> int:
    """Disconnect each repair's released joints from the component's
    joints[] multi and re-stamp joint_names from the kept set (healing any
    laundered contamination in the mirror at the same time). Pure
    connection surgery — released joints stay in the scene/skeleton and
    become free to receive their own components.

    `repairs` is find_overconnected_components() output. Returns the
    number of connections released.
    """
    from maya_tools.framework.decorators import undo_chunk
    from maya_tools.utils.maya import network_nodes as nn

    total = 0
    with undo_chunk('Release Duplicated Joints'):
        for r in repairs:
            cnode = r['node']
            if not cmds.objExists(cnode):
                continue
            nn.replace_message_multi(cnode, 'joints', r['keep'])
            nodes.set_component_joint_names(
                cnode,
                [j.split('|')[-1].split(':')[-1] for j in r['keep']],
            )
            total += len(r['release'])
    return total


def build_modules(progress_cb=None, options=None,
                  force_missing_aimers: bool = False) -> None:
    """Validate, topo-sort components, build each, register binding,
    organize_scene.

    Constructs a transient Blueprint snapshot from scene state at entry —
    used as a local working representation by helpers that take a
    Blueprint. The snapshot is dropped at return; no persistent bp.

    If progress_cb is provided, it is called as
    progress_cb(index, total, component_id) before each component build,
    and once more with (total, total, '') after all components finish.
    Exceptions from progress_cb are silently swallowed so a UI hiccup
    never blocks a build.

    options is an optional dict of per-build flags pushed into the
    BuildContext.options dict; components read with
    `context.options.get(...)`. Currently recognized keys:
      - 'reset_ctrl_shapes' (bool): when True, ctrl-bearing components
        skip the persisted CV restore and use the default library shape.
    Empty / None options preserves the existing build behavior exactly."""
    from maya_tools.framework.decorators import undo_chunk

    if not nodes.get_registry():
        raise RuntimeError(
            'No fab_registry in the scene. Load a blueprint first.'
        )

    from maya_tools.rigging.fabricator import build_report
    # Suspend the Armature watchers for the WHOLE build, exactly like its
    # two sibling bulk paths (_load_impl's Load Blueprint, limbs/builder's
    # apply_limb_fragment — both carry this same note): the component
    # build loop fires thousands of SelectionChanged/DAG events
    # (cmds.select/joint/curve/constraints auto-select), and any live
    # watcher processing them mid-build (armature_mirror rewires, future
    # hooks) is storm fuel — the 2026-07-10 Build Rig GUI lockup. The
    # teardown prologue's own suspend nests cleanly (depth-counted).
    from maya_tools.rigging.fabricator import armature_watch
    with (armature_watch.suspended(),
          build_report.reporting('build') as _brep,
          undo_chunk('Build Modules')):
        # Skin lifecycle, build side — the OPENING bracket (Adrian,
        # 2026-07-13). Disconnect every skin up front, unconditionally: a
        # mesh the user bound while in edit mode is live, and the teardown's
        # orient bake (and every later build step) is about to move joints.
        # Detached, the skin can't ride that motion, so it can't candy-wrap or
        # collapse; reconnect_all_skins at the very end re-activates it at the
        # final pose. disconnect_all_skins already no-ops on clusters that
        # aren't live, so "even if already disconnected" costs nothing.
        skin_connect_app.disconnect_all_skins()

        # Armature lifecycle prologue (spec 2026-07-04 §9) — must run
        # BEFORE the blueprint snapshot so everything downstream sees
        # the final baked skeleton, free of Armature constraints.
        _armature_teardown_for_build(
            force_missing_aimers=force_missing_aimers)

        bp = _snapshot_blueprint_from_scene()

        # Legacy hygiene — nuke any stale guides group from a
        # pre-Armature scene.
        nodes.delete_guides_grp()

        # Pre-validation: blueprint structure
        errors = validate_blueprint(bp)
        if errors:
            raise RuntimeError(
                'Rig cannot be built — blueprint issues:\n\n• ' + '\n• '.join(errors)
            )

        # Pre-validation: scene-wide unique-names check
        from maya_tools.framework.scene_cleanup import scene_cleanup_app as cleanup
        duplicates = cleanup.find_duplicate_transforms()
        if duplicates:
            scene_errors = [
                f"Duplicate short name {short!r} ({len(paths)} matches): " + ', '.join(paths)
                for short, paths in duplicates.items()
            ]
            raise RuntimeError(
                'Rig cannot be built — fix these issues first:\n\n• '
                + '\n• '.join(scene_errors)
            )

        # Capture live pivot positions directly into component network nodes + delete
        # pivots_grp. MUST run before the ComponentInstance snapshot loop below —
        # the snapshot reads network-node options, so captures must land there first.
        # Components read from options.<guide>_position during build.
        _capture_pivots_to_options_from_scene()
        nodes.delete_pivots_grp()

        # Auto-disconnect any active joint mirror networks before module build.
        # Mirror constraints would fight the connector_null parentConstraint
        # chain — the bake happens here.
        n_disconnected = _auto_disconnect_mirrors()
        if n_disconnected:
            cmds.warning(f'Build Modules: disconnected {n_disconnected} mirror '
                         f'node(s) before building.')

        # Derived Limbs (spec 2026-07-11): re-derive every component's
        # limb membership from the scene before building. Fingers/twists
        # authored in the Armature phase since the last derive (new
        # joints under a wrist, dialed or hand-parented twists) join
        # here automatically; deleted/reparented joints drop out. Cheap,
        # idempotent, and the reason no hand-repair path exists.
        for _cnode in nodes.get_all_component_nodes():
            nodes.derive_limb(_cnode)

        # Sweep orphan component network nodes before the build loop iterates.
        # Without this, zombies left over from joint deletes get rebuilt and
        # produce phantom ctrls / null pairs with no joints to drive.
        n_orphans = nodes.cleanup_orphan_components()
        if n_orphans:
            cmds.warning(f'Build Modules: cleaned {n_orphans} orphan '
                         f'component(s) before building.')

        # Re-snapshot so the transient bp reflects freshly-captured pivot positions.
        bp = _snapshot_blueprint_from_scene()

        # Resolve component IDs (auto-derive missing ones) into runtime instances.
        instances = []
        for cdata in bp.components:
            cls = get_component_class(cdata.type)
            cid = cdata.id if cdata.id else cls.default_id(cdata.joints)
            # Region falls back to the contract's default_region when the
            # blueprint instance doesn't override (so IKLeg always reports
            # region='leg' to the pose library without per-instance setup).
            region = cdata.region or cls.CONTRACT.default_region
            inst = ComponentInstance(
                id          = cid,
                type        = cdata.type,
                joints      = list(cdata.joints),
                parent_plug = cdata.parent_plug,
                side        = cdata.side,
                role        = cdata.role,
                region      = region,
                options     = dict(cdata.options),
                persisted   = dict(cdata.persisted),
            )
            instances.append(inst)

        # Auto-resolve any blank parent_plug values to the nearest ancestor
        # joint's matrix output. With World auto-attached to the root joint,
        # every non-root component has at least the World ctrl_out reachable
        # by walking up the joint hierarchy.
        _auto_resolve_parent_plugs(instances, bp)

        # Fill in any missing options from the contract's defaults so component
        # build code can read instance.options[field] directly without each
        # call site duplicating the default. The Contract is the single source
        # of truth for defaults — to change a default for a component, change
        # it in the contract's options_schema and nowhere else.
        for inst in instances:
            cls = get_component_class(inst.type)
            for field_name, field_spec in cls.CONTRACT.options_schema.items():
                inst.options.setdefault(field_name, field_spec.default)

        sorted_instances = _topo_sort_components(instances)
        context = BuildContext(blueprint=bp)
        if options:
            context.options.update(options)

        # Capture bind pose on every skeleton joint BEFORE constraints attach.
        # Component constraints (IK solver, blend chain → bind, etc.) overwrite
        # bind.rotate per-frame; if the IK solver locks a chain mid-build, the
        # locked pose gets baked into bind.rotate and stays even after unbuild
        # deletes the constraints. _restore_skeleton_bind_pose at unbuild end
        # snaps everything back to what was captured here.
        _capture_skeleton_bind_pose(bp)

        # Build each component in order. Errors propagate (per Phase 1 policy —
        # half-built rigs with inconsistent is_built flags are worse than loud failures).
        total = len(sorted_instances)
        for i, inst in enumerate(sorted_instances):
            if progress_cb is not None:
                try:
                    progress_cb(i, total, inst.id)
                except Exception:
                    pass  # UI hiccup should never block a build
            cls = get_component_class(inst.type)
            cls.build(inst, context)
            for cnode in nodes.get_all_component_nodes():
                if nodes.get_component_id(cnode) == inst.id:
                    nodes.set_built(cnode, True)
                    # Self-heal a legacy/version-gated type alias (e.g.
                    # 'IKArm' -> 'RibbonIKArm') onto its resolved successor
                    # now that it has been rebuilt. Without this, the
                    # registry's fabricator_version stamp (written at the
                    # end of THIS same successful build, below) flips the
                    # whole scene to "current" while this node's type
                    # string is still the retired one — so the very next
                    # read of it (e.g. an immediate Unbuild) fails to
                    # resolve. modules.resolve_component_type is a no-op
                    # for already-canonical types, so this only ever
                    # touches genuinely legacy-aliased data.
                    if inst.type != cls.CONTRACT.type:
                        nodes.set_component_type(cnode, cls.CONTRACT.type)
                    break
        if progress_cb is not None:
            try:
                progress_cb(total, total, '')  # final "complete" tick
            except Exception:
                pass

        # Space-switching wiring — runs after every component has built
        # and registered its outputs. Unified path (no per-type special
        # cases): contract declares per-ctrl-role defaults, per-instance
        # message-multi stores user additions, the loop combines both and
        # invokes the matrix-DG builder.
        providers = _collect_space_providers(bp, sorted_instances)
        # Fill in the per-component matrix plugs from the BuildContext.
        for inst in sorted_instances:
            cls = get_component_class(inst.type)
            for plug in cls.CONTRACT.outputs:
                if plug.space_target:
                    key = f'{inst.id}.{plug.name}'
                    if context.has_plug(key):
                        providers[key] = context.resolve_plug(key)

        # Unified per-consumer wiring loop.
        for inst in sorted_instances:
            cls = get_component_class(inst.type)
            if not cls.CONTRACT.space_consumers:
                continue
            component_node = nodes.find_component_node_by_id(inst.id)
            if not component_node:
                continue

            for consumer in cls.CONTRACT.space_consumers:
                ctrl = _find_ctrl_by_role(component_node, consumer.ctrl_role)
                if not ctrl:
                    continue

                resolved = []

                # 1. Contract-declared defaults.
                for name in consumer.defaults:
                    plug = _resolve_space_provider(name, inst, providers, context)
                    if plug is None:
                        continue  # _resolve_space_provider already warned
                    resolved.append((name, plug))

                # 2. Per-instance user additions from spaces_<ctrl_role>.
                # Direct worldMatrix construction (no _resolve_space_provider
                # round-trip) -- get_ctrl_space_names already filtered for
                # existence and add_ctrl_space validated joint-type at insert.
                for joint_name in nodes.get_ctrl_space_names(
                        component_node, consumer.ctrl_role):
                    resolved.append((joint_name, f'{joint_name}.worldMatrix[0]'))

                if resolved:
                    build_space_switch_dg(
                        ctrl, consumer.attr_name, resolved,
                        consumer.default, component_node,
                    )

        _refresh_rig_binding(bp)
        # Baked per-rig selection sets (Adrian, 2026-07-05): truth
        # lives ON the rig as Maya objectSets; the pose library reads
        # them instead of re-deriving from scene-wide component walks.
        from maya_tools.rigging.fabricator import selection_sets
        selection_sets.rebuild_selection_sets()
        _organize_scene(bp)
        # FKAim link sweep mutates aim_offset locals to anchor link_ctrl at
        # the midpoint of member targets. Must run BEFORE the channel-locking
        # pass below, which locks every *_offset transform's TRS.
        _apply_post_build_fk_aim_links()
        _apply_post_build_channel_locking()
        _ensure_joints_display_layer(bp)
        _ensure_geo_display_layer()
        # Joint coloring runs AFTER the display layer because
        # cmds.createDisplayLayer connects layer.drawInfo → joint.drawOverride
        # (a compound), which drives the override-color child attrs to the
        # layer's defaults. We have to break those specific connections per
        # joint before our setAttr will stick. Visibility connection stays
        # intact so the _Joints layer's visibility toggle still works.
        _apply_post_build_joint_colors()

        paint_rig_outliner_colors()

        # Mirror Modules deferred ctrl-shape pass (Adrian, 2026-07-12):
        # components created by mirror_component carry
        # persisted.pending_cv_mirror = <source component id>. This is
        # the one moment both sides' ctrls exist live with FINAL world
        # transforms (after the FKAim link sweep above), so mirror every
        # source ctrl curve world-YZ onto its counterpart now, then
        # clear the flag. One-shot on a NORMAL build — retrying on a
        # later build could stomp sculpts the artist made on the
        # mirrored side in between. The next unbuild captures the
        # mirrored shapes into cv_data as ordinary user edits.
        #
        # Reset Control Shapes builds skip the pass and KEEP the flags
        # (review wf_2f4a49ba finding #1): the source ctrls carry their
        # just-reset default shapes this build, so mirroring now would
        # burn the one-shot copying garbage; the next normal build (with
        # the source sculpt restored from its untouched cv_data) runs
        # the pass instead. The flag also survives unbuild (see the
        # capture-loop preserve in unbuild_modules) so a failed build's
        # unbuild/fix/rebuild recovery keeps the promise alive.
        pending = []
        for cnode in nodes.get_all_component_nodes():
            persisted = nodes.get_component_persisted(cnode) or {}
            if persisted.get('pending_cv_mirror'):
                pending.append((cnode, persisted))
        if pending and context.options.get('reset_ctrl_shapes'):
            print('[Fabricator] Reset Control Shapes build: ctrl-shape '
                  'mirror deferred to the next normal build '
                  f'({len(pending)} component(s) still pending).')
        elif pending:
            from maya_tools.rigging.fabricator import curve_mirror
            ctrl_map = curve_mirror.component_ctrl_map()  # ONE scene walk
            for cnode, persisted in pending:
                src_id = persisted.pop('pending_cv_mirror', None)
                cid = nodes.get_component_id(cnode)
                try:
                    src_node = nodes.find_component_node_by_id(src_id)
                    src_ctrls = ctrl_map.get(src_node, []) if src_node else []
                    if not src_node:
                        cmds.warning(f'Build Modules: ctrl-shape mirror '
                                     f'source {src_id!r} no longer exists; '
                                     f'{cid!r} keeps default shapes.')
                    n_mirrored = curve_mirror.mirror_component_ctrls(
                        src_id, ctrls=src_ctrls)
                    if n_mirrored:
                        print(f'[Fabricator] Mirrored {n_mirrored} ctrl '
                              f'curve(s): {src_id} -> {cid}.')
                except Exception as exc:
                    cmds.warning(f'Build Modules: ctrl-shape mirror for '
                                 f'{cid!r} failed: {exc}')
                finally:
                    nodes.set_component_persisted(cnode, persisted)

        # Record whether THIS build skipped the persisted CV restore.
        # The next unbuild reads it and keeps the components' prior
        # cv_data instead of capturing the live (library-default)
        # shapes — otherwise Edit Rig after a Reset Control Shapes
        # build would silently destroy every sculpt, breaking the
        # checkbox's own 'Persisted shape data is NOT erased' promise
        # (pre-existing gap, surfaced by review wf_2f4a49ba finding #1).
        from maya_tools.utils.maya import network_nodes as nn
        _reg = nodes.get_registry()
        nn.ensure_bool_attr(_reg, 'built_with_reset_shapes', default=False)
        cmds.setAttr(f'{_reg}.built_with_reset_shapes',
                     bool(context.options.get('reset_ctrl_shapes')))

        # Skin lifecycle, build side — the CLOSING bracket (Adrian, 2026-07-13,
        # revised 2026-07-14). RESET every skin's bind to the joints' FINAL
        # positions, dead last, after every step above has moved joints. A
        # plain reconnect only re-activates DORMANT clusters — a cluster left
        # live (a component build re-activated it) keeps its stale pre-orient
        # bind, which is the twist-joint candy-wrapper Adrian hit in the export
        # (verified: a live re-oriented cluster stays at delta 16.0 through
        # reconnect, and only a disconnect+reconnect drops it to 0).
        # reset_all_binds_to_pose re-derives every bindPreMatrix regardless of
        # live/dormant. The rig is at ctrl-zero (bind pose) here, exactly when a
        # bind reset is correct.
        _recon = skin_connect_app.reset_all_binds_to_pose()
        if _recon:
            print(f'[Fabricator] Reset bind on {len(_recon)} skinned mesh(es) '
                  f'at the final joint pose.')

        # FS build version stamp (Adrian, 2026-07-05) — the last act
        # of a successful build. Pre-build checks compare this against
        # the current FABRICATOR_VERSION and warn (yellow, never red)
        # on drift.
        from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION
        nodes.set_registry_fabricator_version(FABRICATOR_VERSION)

        _brep.components = [{'id': inst.id, 'status': 'ok'}
                            for inst in sorted_instances]

# Override attrs we unlock + disconnect per joint before writing colors.
# The display layer wires layer.drawInfo → joint.drawOverride as a single
# COMPOUND connection (verified empirically — child-level listConnections
# returns empty; the connection lives only on the parent). So we have to
# break the parent compound, not the children. We still defensively
# unlock/clear the children in case some other code path connected
# individually.
_JOINT_COLOR_OVERRIDE_ATTRS = (
    'overrideEnabled', 'overrideRGBColors', 'overrideColor',
    'overrideColorRGB', 'overrideColorR', 'overrideColorG', 'overrideColorB',
)


def _apply_post_build_joint_colors() -> None:
    """Color every bind joint owned by a built component, using that
    component's ctrl_color option. Components without ctrl_color in their
    schema (FollowJoint) leave their joints uncolored. Joints not owned
    by any component are left uncolored too — per Adrian's spec.

    Joints sit in the _Joints display layer at this point. Maya's
    createDisplayLayer wires layer.drawInfo → joint.drawOverride as a
    single compound connection that blocks all per-child setAttrs (with
    a "locked or connected" error). To get per-joint colors AND keep the
    layer's visibility toggle working:

      1. Snapshot the layer's drawInfo plug from joint.drawOverride.
      2. Disconnect the drawOverride compound (frees every child plug).
      3. Defensively disconnect + unlock individual color children.
      4. setAttr the RGB override (COMPOUND overrideColorRGB — three-arg
         setAttr — triggers Maya's joint draw color refresh; per-child
         setAttrs don't always redraw).
      5. Reconnect just layer.drawInfo.visibility → joint.drawOverride
         .overrideVisibility, so the _Joints layer's visibility toggle
         still hides the joint. drawInfo.color / drawInfo.overrideRGB*
         intentionally NOT reconnected — those are what we're overriding.
    """
    from maya_tools.utils.maya.side_tokens import CTRL_COLOR_RGB

    n_colored = 0
    for cnode in nodes.get_all_component_nodes():
        if not nodes.is_built(cnode):
            continue
        opts = nodes.get_component_options(cnode) or {}
        color_name = opts.get('ctrl_color')
        if not color_name:
            # Network-node options can be missing ctrl_color even when the
            # contract declares it — e.g. components added before the
            # auto-detect fix wrote 'md' explicitly. Fall back to the
            # schema default so legacy / partially-migrated components
            # still color on rebuild without re-add.
            try:
                cls = get_component_class(nodes.get_component_type(cnode))
                field = cls.CONTRACT.options_schema.get('ctrl_color')
                if field is not None:
                    color_name = field.default
            except Exception:
                color_name = None
            if not color_name:
                continue  # FollowJoint and similar — no ctrl_color in schema
        rgb = CTRL_COLOR_RGB.get(color_name)
        if rgb is None:
            continue
        for j in nodes.get_component_joints(cnode):
            if not cmds.objExists(j) or cmds.nodeType(j) != 'joint':
                continue

            # 1. Snapshot the display-layer source so we can restore
            #    visibility after the disconnect.
            layer_drawinfo_src = None
            do_srcs = cmds.listConnections(
                f'{j}.drawOverride',
                source=True, destination=False, plugs=True,
            ) or []
            if do_srcs:
                layer_drawinfo_src = do_srcs[0]
            # 2. Disconnect the parent compound.
            for src in do_srcs:
                try:
                    cmds.disconnectAttr(src, f'{j}.drawOverride')
                except Exception:
                    pass
            # 3. Defensive child-level disconnect + unlock.
            for attr in _JOINT_COLOR_OVERRIDE_ATTRS:
                plug = f'{j}.{attr}'
                for src in cmds.listConnections(plug, source=True,
                                                 destination=False,
                                                 plugs=True) or []:
                    try:
                        cmds.disconnectAttr(src, plug)
                    except Exception:
                        pass
                try:
                    cmds.setAttr(plug, lock=False)
                except Exception:
                    pass

            # 4. Apply the component's color.
            try:
                cmds.setAttr(f'{j}.overrideEnabled', 1)
                cmds.setAttr(f'{j}.overrideRGBColors', 1)
                cmds.setAttr(f'{j}.overrideColorRGB', rgb[0], rgb[1], rgb[2])
                n_colored += 1
            except Exception as e:
                cmds.warning(f'Joint coloring failed on {j}: {e}')
                continue

            # 5. Re-attach the layer's visibility-only sub-connection.
            #    layer.drawInfo.visibility → joint.drawOverride
            #    .overrideVisibility preserves the _Joints layer toggle
            #    without re-driving the color overrides.
            if layer_drawinfo_src:
                try:
                    cmds.connectAttr(
                        f'{layer_drawinfo_src}.visibility',
                        f'{j}.drawOverride.overrideVisibility',
                        force=True,
                    )
                except Exception:
                    pass

    if n_colored:
        # Surface the count in the script editor so it's obvious whether
        # the pass ran. (Build's outer logger doesn't see returns from
        # _apply_post_*; print is the simplest signal.)
        print(f'[Fabricator] Colored {n_colored} bind joint(s) by component ctrl_color.')


def _apply_post_build_channel_locking() -> None:
    """Post-build channel locking per Adrian's standing convention.

    Two passes:

    1. Name-pattern locking — anything matching `*_offset` (ctrl offset
       buffers) or `*_grp` (rig containers): lock + hide all 9 TRS
       channels. Direct setAttr per plug; wrapped in try/except so
       connection-driven plug edge cases don't blow up the build.

    2. Role-based overrides on Fabricator-tagged ctrls under
       fab_controls_grp:
       - master_switch_ctrl: lock + hide all 9 (only its custom attrs,
         e.g. ik_blend, should be keyable)
       - pv_ctrl: lock + hide R + S; T stays keyable (positionable
         polevector)
       - Other tagged ctrls (fk_ctrl, ik_end_ctrl, advfk_ctrl,
         world_ctrl, …): keep whatever the component's Contract.channels
         option set during build.

    Idempotent — safe to re-run.
    """
    # 1. Name-pattern locking on *_offset + *_grp.
    targets = []
    for pat in ('*_offset', '*_grp'):
        targets.extend(cmds.ls(pat, type='transform') or [])
    for node in set(targets):
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz'):
            plug = f'{node}.{attr}'
            try:
                cmds.setAttr(plug, lock=True)
                cmds.setAttr(plug, keyable=False)
                cmds.setAttr(plug, channelBox=False)
            except Exception:
                pass  # connected-attr / locked edge cases — skip silently

    # 2. Role-based ctrl overrides.
    from maya_tools.rigging.fabricator.modules.world import _apply_channels
    if cmds.objExists('fab_controls_grp'):
        for node in cmds.listRelatives('fab_controls_grp',
                                         allDescendents=True,
                                         fullPath=True,
                                         type='transform') or []:
            if not cmds.attributeQuery('fab_role', node=node, exists=True):
                continue
            role = cmds.getAttr(f'{node}.fab_role') or ''
            if role == 'master_switch_ctrl':
                _apply_channels(node, {'keyable': []})
            elif role == 'pv_ctrl':
                _apply_channels(node, {'keyable': ['tx', 'ty', 'tz']})
            # else: leave the component's channels config as-is.


def _apply_post_build_fk_aim_links() -> None:
    """Group every built FKAim component by its link_group option and
    wire a space switch on each shared link_ctrl.

    Per-group spaces:
      - 'world'  (held identity matrix at link_ctrl bind)
      - 'root'   (root joint worldMatrix)
      - one entry per unique parent component (matched by component
        id, via parent_plug). Each space resolves to that component's
        primary ctrl's worldMatrix.

    Default space: 'root' (eye animation typically follows the head/spine,
    not the world origin).

    Single-member groups are no-ops (the link_ctrl wasn't created in the
    first place — solo FKAim parents the aim ctrl under its own parent
    ctrl directly). Empty link_group instances are skipped entirely.
    """
    # 1. Collect built FKAim component nodes, grouped by link_group.
    groups: dict = {}
    for cnode in nodes.get_all_component_nodes():
        if nodes.get_component_type(cnode) != 'FKAim':
            continue
        if not nodes.is_built(cnode):
            continue
        opts = nodes.get_component_options(cnode) or {}
        lg = (opts.get('link_group') or '').strip()
        if not lg:
            continue
        groups.setdefault(lg, []).append(cnode)

    # 2. Build a global root-joint matrix plug once (shared across groups).
    root_joint = ''
    for j in cmds.ls(type='joint', long=False) or []:
        if not (cmds.listRelatives(j, parent=True, type='joint') or []):
            root_joint = j
            break
    root_plug = f'{root_joint}.worldMatrix[0]' if root_joint else ''

    for link_group, members in groups.items():
        if len(members) < 2:
            # Solo group → no link_ctrl to wire. The aim ctrl is parented
            # under the FKAim's own parent ctrl in this case.
            continue

        link_ctrl = f'{link_group}_aim_ctrl'
        if not cmds.objExists(link_ctrl):
            cmds.warning(
                f'FKAim link sweep: link_ctrl {link_ctrl!r} missing for '
                f'group {link_group!r} (skipping).'
            )
            continue

        # 2.5. Re-anchor link_ctrl to the midpoint of its members' aim
        # targets. fk_aim.build positions each member's aim_offset at
        # its individual eye's aim_target_pos in worldspace; the first
        # builder also set link_ctrl.fab_bind_matrix to ITS eye's
        # target (since the first builder didn't know about siblings).
        # The midpoint is what the animator expects ("eyes ctrl is
        # directly in the middle of the L and R aim ctrls"). We update
        # fab_bind_matrix to the midpoint translation, then adjust
        # each aim_offset's local so the world positions of all aim
        # ctrls are preserved once the space switch drives link_ctrl
        # via offsetParentMatrix.
        aim_offsets = []
        for m in members:
            member_joints = nodes.get_component_joints(m) or []
            if not member_joints:
                continue
            short = member_joints[0].split('|')[-1].split(':')[-1]
            offset_name = f'{short}_aim_ctrl_offset'
            if not cmds.objExists(offset_name):
                continue
            wpos = cmds.xform(offset_name, q=True, ws=True, t=True)
            aim_offsets.append((offset_name, tuple(wpos)))

        if aim_offsets:
            n = len(aim_offsets)
            midpoint = tuple(
                sum(o[1][i] for o in aim_offsets) / n for i in range(3)
            )
            midpoint_matrix = [
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                midpoint[0], midpoint[1], midpoint[2], 1.0,
            ]
            cmds.setAttr(f'{link_ctrl}.fab_bind_matrix',
                         *midpoint_matrix, type='matrix')
            for offset_node, world_pos in aim_offsets:
                cmds.setAttr(f'{offset_node}.tx', world_pos[0] - midpoint[0])
                cmds.setAttr(f'{offset_node}.ty', world_pos[1] - midpoint[1])
                cmds.setAttr(f'{offset_node}.tz', world_pos[2] - midpoint[2])

        # 3. Collect parent_plug owners — one entry per unique parent
        # component id. We pick each parent component's PRIMARY ctrl
        # (first fab_owner-tagged ctrl owned by that component) for
        # the matrix source.
        parent_entries = []  # list of (component_id, primary_ctrl_name)
        seen_parent_ids = set()
        for m in members:
            pp = nodes.get_component_parent_plug(m) or ''
            parent_cid = pp.split('.', 1)[0] if pp else ''
            if not parent_cid or parent_cid in seen_parent_ids:
                continue
            seen_parent_ids.add(parent_cid)
            owner_node = nodes.find_component_node_by_id(parent_cid)
            if not owner_node:
                continue
            primary_ctrl = _find_primary_ctrl_of_component(owner_node)
            if primary_ctrl:
                parent_entries.append((parent_cid, primary_ctrl))

        # 4. Assemble the providers list for build_space_switch_dg.
        # build_space_switch_dg accepts ('name', 'IDENTITY' | 'node.attr')
        # tuples — 'IDENTITY' triggers the held-matrix-at-bind path.
        resolved = [('world', 'IDENTITY')]
        if root_plug:
            resolved.append(('root', root_plug))
        for cid, ctrl in parent_entries:
            resolved.append((cid, f'{ctrl}.worldMatrix[0]'))

        # Default to 'root' unless root resolution failed, in which case
        # fall back to 'world' (always available).
        default_name = 'root' if root_plug else 'world'

        # Wire it. The first member of the group owns the
        # space_switch_nodes message-multi so unbuild's rig_grp cascade
        # plus the DG-node delete chain (which lives elsewhere) cleans
        # up consistently.
        build_space_switch_dg(
            link_ctrl, 'space', resolved, default_name, members[0],
        )


def _find_primary_ctrl_of_component(component_node: str) -> str:
    """Return the primary (first matrix-output-equivalent) ctrl owned by
    a component network node, or '' if none found.

    Walks ctrl.fab_owner message connections back to component_node and
    picks by role priority: world_ctrl > ik_end_ctrl > advfk_ctrl >
    fk_ctrl > any other role. The world_ctrl-first ordering lets World
    components serve as the matrix source for FKAim's parent space.
    """
    if not component_node:
        return ''
    role_priority = (
        'world_ctrl', 'ik_end_ctrl', 'advfk_ctrl', 'fk_ctrl',
    )
    candidates_by_role: dict = {}
    for ctrl in cmds.ls(type='transform') or []:
        if not cmds.attributeQuery('fab_owner', node=ctrl, exists=True):
            continue
        owners = cmds.listConnections(f'{ctrl}.fab_owner',
                                       source=True, destination=False) or []
        if component_node not in owners:
            continue
        if not cmds.attributeQuery('fab_role', node=ctrl, exists=True):
            continue
        role = cmds.getAttr(f'{ctrl}.fab_role') or ''
        candidates_by_role.setdefault(role, ctrl)
    for r in role_priority:
        if r in candidates_by_role:
            return candidates_by_role[r]
    # Fall back to whatever ctrl we found first if no priority role matched.
    if candidates_by_role:
        return next(iter(candidates_by_role.values()))
    return ''


def add_joints_to_reference_layer(joints: list) -> None:
    """Add joints to the `_Joints` display layer: HIDDEN by default, and
    TEMPLATE (displayType=1) rather than Reference when shown.

    Adrian, 2026-07-13: the Armature ctrls are x-ray balls now — they draw
    through the character mesh, so the joints do not need to be visible to
    place them. The layer stays as the one-toggle escape hatch for when you
    do want to see them, and template keeps them unselectable and out of
    the way. (The function name is now a mild misnomer; renaming it touches
    every call site and buys nothing.)

    Additive: the layer is created once and reused; joints join it the
    moment they exist (template load, limb drop) and stay through
    build/unbuild cycles. Selection happens through Armature ctrls and
    the canvas, never by grabbing joints in the viewport.
    """
    joints = [j for j in joints if cmds.objExists(j)]
    if not joints:
        return
    layer_name = '_Joints'
    if not cmds.objExists(layer_name):
        cmds.createDisplayLayer(name=layer_name, empty=True)
        # Hidden by default (Adrian, 2026-07-13). The Armature ctrls are
        # x-ray balls now — they draw THROUGH the mesh, so the joints no
        # longer need to be visible to be placed. Set ON CREATE ONLY: this
        # function also runs on every template load and limb drop, so
        # re-asserting it would slam the joints back off every time the
        # user had deliberately turned them on.
        cmds.setAttr(f'{layer_name}.visibility', 0)
    cmds.setAttr(f'{layer_name}.displayType', 1)  # Template
    cmds.editDisplayLayerMembers(layer_name, joints, noRecurse=True)


def _selected_parent_joint() -> str:
    """The joint a new Add Joint should hang under, resolved from the
    current selection: a selected joint, or the joint behind a selected
    Armature ctrl. Last one wins, matching create_joint()'s own rule.

    The ctrl case is what keeps Add Joint chaining now that each press
    leaves the new CTRL selected (Adrian, 2026-07-18) — create_joint()
    only looks for joints, so without this a second press would find no
    joint in the selection and drop a free joint at the origin.
    """
    for node in reversed(cmds.ls(selection=True, long=False) or []):
        if cmds.nodeType(node) == 'joint':
            return node
        if node.endswith('_amt_CTL'):
            jnt = node[:-len('_amt_CTL')]
            if cmds.objExists(jnt) and cmds.nodeType(jnt) == 'joint':
                return jnt
    return ''


def add_single_joint(target: str = None) -> str:
    """Create one joint and wrap it into the Armature.

    The single entry point behind both Add Joint gestures (the Skeleton
    Helpers Bar button and the palette's SingleJoint drop onto a canvas
    row). Creating the joint is only half the job: a joint the Armature
    does not own has no ctrl and no aimer, and stays out of the hidden
    `_Joints` layer, so it is the one visible, unlocked joint in a scene
    of hidden templates. Guarded rebuild + reference-layer add is the
    same pair every other joint-creating path runs (engine_ik,
    branch_ops, limbs/builder).

    `target` is the drop gesture: select it first, so the new joint
    lands under the row the user dropped on. With no target the current
    selection drives it, so the last-selected joint (or the joint behind
    the selected ctrl) gets the child and repeated presses chain.

    Two things happen afterwards (Adrian, 2026-07-18). When the newcomer
    is the parent's FIRST child, the parent aimer's aimTarget enum is set
    to it, filling in a target a childless joint could not have had; a
    parent that already has children keeps its authored aim untouched.
    And the new joint's CTRL ends up selected, not the joint, so it can
    be dragged straight into place.

    Returns:
        str: The new joint's name.
    """
    from maya_tools.framework.decorators import undo_chunk
    from maya_tools.rigging.joint_orient import joint_orient_app as joa
    from maya_tools.skeleton import skeleton_utils

    if target and not cmds.objExists(target):
        raise RuntimeError(f"Target joint '{target}' does not exist.")

    with undo_chunk('AddJoint'):
        parent = target or _selected_parent_joint()
        if parent:
            cmds.select(parent, replace=True)
        new_joint = skeleton_utils.create_joint()

        # Set the parent aimer's aimTarget enum to the newcomer, but ONLY
        # when it is the parent's FIRST child (Adrian, 2026-07-18): a
        # childless joint's aimer has nothing to point at, so this is
        # filling a blank. A parent that already has children has an
        # authored aim, and a second child must never steal it.
        # The enum is baked at aimer-creation time, so an existing aimer
        # has to be rebuilt before the new child is nameable in it; the
        # rebuild re-applies prior state by label, so nothing is lost.
        parent = (cmds.listRelatives(new_joint, parent=True,
                                     type='joint') or [''])[0]
        if parent and len(cmds.listRelatives(parent, children=True,
                                             type='joint') or []) == 1:
            if joa.aimer_exists(parent):
                joa.rebuild_aimer(parent)
            else:
                joa.create_aimer(parent)
            joa.apply_aimer_state(parent, aim_target=new_joint)

        add_joints_to_reference_layer([new_joint])
        if armature.armature_exists():
            armature.build_armature()

        # Select the new joint's CTRL so it can be dragged immediately.
        # The joint itself is locked and unselectable, and the rebuild
        # above replaced the whole ctrl tree, so this resolves the ctrl
        # fresh rather than trusting any earlier selection to survive.
        ctrl = armature.ctrl_for_joint(new_joint)
        cmds.select(ctrl or new_joint, replace=True)
    return new_joint


def _ensure_joints_display_layer(blueprint) -> None:
    """Put every skeleton joint into the `_Joints` layer.

    Animator QoL — the layer is hidden by default (the x-ray ball ctrls
    made visible joints redundant), and one toggle in the Display Layer
    Editor brings them back as unselectable templates. Additive via
    add_joints_to_reference_layer (the layer survives unbuilds).
    """
    add_joints_to_reference_layer(
        [j.name for j in blueprint.skeleton_joints])


def _ensure_geo_display_layer() -> None:
    """Put geo_grp into a `_Geo` display layer.

    Animator QoL companion to `_Joints` — one toggle in the Display
    Layer Editor hides the rig's geometry without disturbing anything
    else. Layered at the geo_grp transform (not the meshes) so any
    user-added geo under the group inherits visibility automatically.

    No-op when geo_grp doesn't exist (rig has no skinned meshes yet).
    Idempotent: deletes any pre-existing `_Geo` layer before recreating.
    """
    layer_name = '_Geo'
    if cmds.objExists(layer_name):
        cmds.delete(layer_name)
    if not cmds.objExists('geo_grp'):
        return
    cmds.createDisplayLayer('geo_grp', name=layer_name, noRecurse=True)


def _auto_resolve_parent_plugs(instances, blueprint) -> None:
    """For any instance with a required matrix input but no parent_plug,
    walk up the joint hierarchy from its primary joint to find the nearest
    ancestor joint that owns a component, then wire to that component's
    first matrix output.

    The user can override by setting parent_plug explicitly in the
    properties panel (or hand-edited blueprint). This auto-resolution
    only kicks in when parent_plug is empty.

    Raises RuntimeError if a component has required inputs and no
    ancestor with a matrix output is reachable.
    """
    by_primary = {inst.joints[0]: inst for inst in instances if inst.joints}
    joint_parent = {j.name: j.parent for j in blueprint.skeleton_joints}

    for inst in instances:
        if inst.parent_plug:
            continue  # user explicitly set it — leave alone
        cls = get_component_class(inst.type)
        required_matrix_inputs = [
            p for p in cls.CONTRACT.inputs if p.kind == 'matrix' and p.required
        ]
        if not required_matrix_inputs:
            continue  # no matrix input needed (e.g. World)
        if not inst.joints:
            continue
        cur = joint_parent.get(inst.joints[0])
        resolved = ''
        while cur is not None:
            ancestor = by_primary.get(cur)
            if ancestor is not None:
                ancestor_cls = get_component_class(ancestor.type)
                matrix_outs = [
                    p.name for p in ancestor_cls.CONTRACT.outputs
                    if p.kind == 'matrix'
                ]
                if matrix_outs:
                    resolved = f'{ancestor.id}.{matrix_outs[0]}'
                    break
            cur = joint_parent.get(cur)
        if not resolved:
            raise RuntimeError(
                f"Component {inst.id!r} ({inst.type}) on joint "
                f"{inst.joints[0]!r} requires a parent matrix input but no "
                f"ancestor joint owns a component with a matrix output. "
                f"Ensure World is attached to the root joint."
            )
        inst.parent_plug = resolved


def _resolve_space_provider(name: str, inst, providers: dict,
                             context: BuildContext):
    """Resolve a SpaceConsumer.defaults entry (or a contract-declared
    default name) into the matrix plug string fed to build_space_switch_dg.

    Recognized name forms:
    - 'world'                  -> providers['world'] (IDENTITY sentinel
                                  set by _collect_space_providers;
                                  build_space_switch_dg interprets
                                  IDENTITY as the held bind matrix at
                                  ctrl bind).
    - 'root'                   -> providers['root'] (root joint
                                  worldMatrix, populated by
                                  _collect_space_providers).
    - 'auto'                   -> context.resolve_plug(f'{inst.id}.auto'),
                                  registered per-component (SimpleIK's
                                  Magic PV uses this). Warns if not
                                  registered.
    - 'parent'                 -> joint's direct joint-parent's
                                  worldMatrix[0]; falls back to 'world'
                                  resolution if the joint has no joint
                                  parent (root-attached case).
    - '<id>.<plug>'            -> substitute <id> with inst.id, then
                                  providers.get(resolved). Used by
                                  SimpleIK for '<id>.anchor_out' etc.
    - bare joint name          -> f'{name}.worldMatrix[0]'. Defensive --
                                  defaults shouldn't really contain
                                  bare joint names (those go in the
                                  per-instance multi); resolver handles
                                  it for symmetry with user additions.

    Returns the matrix plug string. On lookup failure issues
    cmds.warning and returns None (caller skips the slot -- enum stays
    stable, the wtAddMatrix builder ignores the gap).
    """
    if name == 'world':
        # 'world' is already populated in `providers` via
        # _collect_space_providers (IDENTITY sentinel). Use the dict
        # entry -- build_space_switch_dg interprets IDENTITY as the
        # held bind matrix at ctrl bind.
        return providers.get('world', 'IDENTITY')

    if name == 'root':
        return providers.get('root') or 'IDENTITY'

    if name == 'auto':
        auto_key = f'{inst.id}.auto'
        if context.has_plug(auto_key):
            return context.resolve_plug(auto_key)
        cmds.warning(
            f"Consumer for {inst.id} requested 'auto' but component "
            f"didn't register an auto provider."
        )
        return None

    if name == 'parent':
        joint = inst.joints[0] if inst.joints else ''
        if not joint or not cmds.objExists(joint):
            return _resolve_space_provider('world', inst, providers, context)
        par_list = cmds.listRelatives(joint, parent=True, type='joint') or []
        if par_list:
            return f'{par_list[0]}.worldMatrix[0]'
        # No joint parent -- collapse to world (enum slot stays stable).
        return _resolve_space_provider('world', inst, providers, context)

    if '.' in name:
        # Dotted reference '<id>.<plug>' with '<id>' template substitution.
        resolved = name.replace('<id>', inst.id)
        plug = providers.get(resolved)
        if plug is None:
            cmds.warning(
                f"Space provider {resolved!r} not found for {inst.id}."
            )
        return plug

    # Bare name. Try providers dict first (in case it's a registered
    # provider via _collect_space_providers); fall back to joint
    # worldMatrix if the name refers to a scene joint.
    if name in providers:
        return providers[name]
    if cmds.objExists(name) and cmds.nodeType(name) == 'joint':
        return f'{name}.worldMatrix[0]'
    cmds.warning(
        f"Space provider {name!r} not found for {inst.id} "
        f"(not a registered provider, not a scene joint)."
    )
    return None


def _collect_space_providers(blueprint, instances: list) -> dict:
    """Return {provider_name: matrix_plug_string} for the build pass.

    System providers (always available):
      - 'world': identity matrix (constant — no plug, special-cased in
        build_space_switch_dg).
      - 'root':  the rig's root joint worldMatrix.

    Per-component providers: for each instance whose contract output has
    space_target=True, register f'{instance.id}.{plug.name}'. The actual
    matrix-plug string gets filled in by the BuildContext after the
    component's build registers its outputs (so this function returns the
    namespace; resolution happens against context at wiring time).
    """
    root_joint = next(
        (j.name for j in blueprint.skeleton_joints if j.parent is None), None
    )
    providers = {}
    if root_joint:
        providers['root'] = f'{root_joint}.worldMatrix[0]'
    providers['world'] = 'IDENTITY'  # sentinel; build_space_switch_dg handles it

    for inst in instances:
        cls = get_component_class(inst.type)
        for plug in cls.CONTRACT.outputs:
            if plug.space_target:
                providers[f'{inst.id}.{plug.name}'] = None  # filled at wiring time
    return providers


def _find_ctrl_by_role(component_node: str, role: str) -> str:
    """Find a ctrl by its fab_role attr, owned by component_node via
    its fab_owner message back-link.

    Returns '' if not found. Used by build_modules' space-switch wiring
    pass to resolve SpaceConsumer.ctrl_role into the actual Maya ctrl.
    """
    if not component_node:
        return ''
    # No glob pattern — cmds.ls('*', ...) is namespace-restricted; on
    # referenced rigs in anim scenes it returns nothing.
    for ctrl in cmds.ls(type='transform') or []:
        if not cmds.attributeQuery('fab_role', node=ctrl, exists=True):
            continue
        if cmds.getAttr(f'{ctrl}.fab_role') != role:
            continue
        if not cmds.attributeQuery('fab_owner', node=ctrl, exists=True):
            continue
        owner = cmds.listConnections(f'{ctrl}.fab_owner',
                                     source=True, destination=False) or []
        if component_node in owner:
            return ctrl
    return ''


def build_space_switch_dg(ctrl: str,
                          attr_name: str,
                          providers: list,
                          default_name: str,
                          network_node: str) -> list:
    """Wire ctrl.offsetParentMatrix ← wtAddMatrix({providers}) per ctrl.<attr>.

    Uses wtAddMatrix (NOT blendMatrix) so the weighted output is a straight
    sum of full 4x4 matrices — no TRS decomposition, no per-channel filtering.
    With condition-gated weights (one active = 1, others = 0), the wtAddMatrix
    output equals the active matrix verbatim, so translation derived from
    rotation × offset composition propagates correctly.

    providers: list of (name, matrix_plug_or_'IDENTITY') tuples in enum order.

    Reads ctrl.fab_bind_matrix (set by component build) for proper world-
    space rest pose and per-provider static offsets:
      - 'world' provider: held matrix at ctrl_bind, fed into wtAddMatrix slot.
      - other providers: provider.worldMatrix * static_offset → wtAddMatrix
        slot, where static_offset = inv(provider_bind) * ctrl_bind, captured
        at wiring time. Provider at bind ⇒ ctrl at bind. Provider's delta-from-
        bind applies to the ctrl's bind position.

    Returns the list of created DG node names so the caller can connect them
    to network_node.space_switch_nodes for unbuild tracking.
    """
    from maya_tools.utils.maya import network_nodes as nn
    import maya.api.OpenMaya as om

    # Read ctrl's bind world matrix.
    if cmds.attributeQuery('fab_bind_matrix', node=ctrl, exists=True):
        ctrl_bind = cmds.getAttr(f'{ctrl}.fab_bind_matrix')
    else:
        ctrl_bind = None

    # Build animator-friendly enum labels: check each matrix-plug's node
    # for a fab_display_name attr; fall back to the raw provider name.
    def _label_for(provider_name, matrix_plug):
        if matrix_plug and matrix_plug != 'IDENTITY':
            node = matrix_plug.split('.')[0]
            if (cmds.objExists(node)
                    and cmds.attributeQuery('fab_display_name',
                                            node=node, exists=True)):
                custom = cmds.getAttr(f'{node}.fab_display_name')
                if custom:
                    return custom
        return provider_name

    if not cmds.attributeQuery(attr_name, node=ctrl, exists=True):
        labels = [_label_for(name, plug) for name, plug in providers]
        cmds.addAttr(ctrl, ln=attr_name, at='enum',
                     en=':'.join(labels), k=True)
    default_idx = next(
        (i for i, (n, _) in enumerate(providers) if n == default_name), 0
    )
    cmds.setAttr(f'{ctrl}.{attr_name}', default_idx)

    # wtAddMatrix: weighted sum. Output = sum_i(weight_i * matrix_i).
    wam = cmds.createNode('wtAddMatrix', name=f'{ctrl}_space_wam')
    created = [wam]

    for i, (provider_name, matrix_plug) in enumerate(providers):
        # Determine the matrix source plug for this slot.
        source_plug = None
        if matrix_plug == 'IDENTITY':
            # World provider — held matrix at ctrl_bind.
            if ctrl_bind is not None:
                hm = cmds.createNode('holdMatrix',
                                     name=f'{ctrl}_space_hold_{i}')
                created.append(hm)
                cmds.setAttr(f'{hm}.inMatrix', *ctrl_bind, type='matrix')
                source_plug = f'{hm}.outMatrix'
            # else: no ctrl_bind — skip this slot (target stays unconnected,
            # matrixSum simply doesn't include it).
        else:
            # Non-world provider — multMatrix(provider × static_offset).
            try:
                provider_bind = cmds.getAttr(matrix_plug)
            except Exception:
                provider_bind = None

            if provider_bind is not None and ctrl_bind is not None:
                # Row-vector math: matrixSum = static_offset × provider.world,
                # where static_offset = ctrl_bind × inv(provider_bind).
                # Maya's MMatrix multiplication is row-vector (matches Maya
                # internal convention); the matrices are applied left-to-right.
                p_inv = om.MMatrix(provider_bind).inverse()
                c_mat = om.MMatrix(ctrl_bind)
                static_offset = list(c_mat * p_inv)
                mm = cmds.createNode('multMatrix',
                                     name=f'{ctrl}_space_mm_{i}')
                created.append(mm)
                cmds.setAttr(f'{mm}.matrixIn[0]', *static_offset,
                             type='matrix')
                cmds.connectAttr(matrix_plug, f'{mm}.matrixIn[1]')
                source_plug = f'{mm}.matrixSum'
            else:
                # Fallback: raw provider plug, no offset.
                source_plug = matrix_plug

        if source_plug:
            cmds.connectAttr(source_plug, f'{wam}.wtMatrix[{i}].matrixIn')

        # Condition node — outputs 1 when ctrl.attr == i, else 0.
        cond = cmds.createNode('condition', name=f'{ctrl}_space_cond_{i}')
        created.append(cond)
        cmds.connectAttr(f'{ctrl}.{attr_name}', f'{cond}.firstTerm')
        cmds.setAttr(f'{cond}.secondTerm', i)
        cmds.setAttr(f'{cond}.colorIfTrueR', 1)
        cmds.setAttr(f'{cond}.colorIfFalseR', 0)
        cmds.connectAttr(f'{cond}.outColorR',
                         f'{wam}.wtMatrix[{i}].weightIn')

    cmds.connectAttr(f'{wam}.matrixSum',
                     f'{ctrl}.offsetParentMatrix', force=True)

    if network_node:
        for n in created:
            nn.connect_message_multi(n, network_node, 'space_switch_nodes')

    return created


def _topo_sort_components(instances):
    """Sort component instances by parent_plug edges, parents before children."""
    by_id = {i.id: i for i in instances}
    sorted_list = []
    visited = set()
    in_progress = set()

    def visit(inst):
        if inst.id in visited:
            return
        if inst.id in in_progress:
            raise RuntimeError(f"Cycle in component graph involving {inst.id!r}")
        in_progress.add(inst.id)
        if inst.parent_plug and '.' in inst.parent_plug:
            target_id = inst.parent_plug.split('.', 1)[0]
            if target_id in by_id:
                visit(by_id[target_id])
        in_progress.remove(inst.id)
        visited.add(inst.id)
        sorted_list.append(inst)

    for inst in instances:
        visit(inst)
    return sorted_list


def _refresh_rig_binding(blueprint):
    """Write/refresh FAB_RigBinding so anim export sees this rig."""
    from maya_tools.utils.maya import rig_binding

    root_joint = next(
        (j.name for j in blueprint.skeleton_joints if j.parent is None), None
    )
    if not root_joint or not cmds.objExists(root_joint):
        return

    descendants = cmds.listRelatives(
        root_joint, allDescendents=True, type='joint', fullPath=True
    ) or []
    chain = [root_joint] + descendants
    seen = set()
    unique_chain = []
    for j in chain:
        long_name = cmds.ls(j, long=True)
        key = long_name[0] if long_name else j
        if key not in seen:
            seen.add(key)
            unique_chain.append(j)

    nulls_grp_name = 'fab_nulls_grp' if cmds.objExists('fab_nulls_grp') else None
    controls_grp_name = 'fab_controls_grp' if cmds.objExists('fab_controls_grp') else None

    # Prefer the registry's stored rig_label (auto-derived + persisted on
    # first read; user-editable via the Fabricator window). Falls back to
    # derive_rig_label if no registry exists.
    from maya_tools.rigging.fabricator import nodes as ks_nodes
    rig_label = ks_nodes.get_or_init_rig_label() or rig_binding.derive_rig_label(root_joint)
    rig_binding.write_rig_binding(
        rig_label=rig_label,
        root_joint=root_joint,
        export_joints=unique_chain,
        nulls_grp=nulls_grp_name,
        controls_grp=controls_grp_name,
    )


def _organize_scene(blueprint):
    """Top group, geo_grp, rig_grp containing rig contents.

    Top group name tracks the SCENE FILE (lowercased basename), not the
    blueprint name. Animators rename rigs by saving the scene under a
    new file (michael.ma vs the original biped_male.blueprint.yaml);
    the top group should follow that, not the cached template name.
    Falls back to blueprint.name for unsaved scenes.
    """
    from maya_tools.utils.maya import rig_binding

    top_name = _scene_derived_top_name(blueprint)
    if not top_name:
        return

    # Defensive: collide-with-non-transform → suffix _grp (carry-forward of Phase 1 fix).
    if cmds.objExists(top_name) and cmds.nodeType(top_name) != 'transform':
        top_name = f'{top_name}_grp'

    top = _ensure_group(top_name, inherits_transform=True)
    _ensure_group('rig_grp', parent=top, inherits_transform=True)

    # Root joint into top (preserve world position).
    root_joint = next(
        (j.name for j in blueprint.skeleton_joints if j.parent is None), None
    )
    if root_joint and cmds.objExists(root_joint):
        current_parent = cmds.listRelatives(root_joint, parent=True) or []
        if not current_parent or current_parent[0] != top:
            cmds.parent(root_joint, top)

    # Sweep an orphan top group left over from a prior build under a
    # different name (template loaded as biped_male, scene saved as
    # michael, etc.). Only deletes when empty + a transform — never
    # touches user authored content.
    _cleanup_stale_top_group(blueprint, keep=top_name)


def _scene_derived_top_name(blueprint) -> str:
    """Top-group name derived from the current scene file basename,
    lowercased, '.ma'/'.mb' stripped. Falls back to blueprint.name for
    unsaved scenes (which keeps fresh-untitled builds working off the
    template name)."""
    scene_path = cmds.file(query=True, sceneName=True) or ''
    if scene_path:
        from pathlib import Path
        stem = Path(scene_path).stem
        if stem:
            return stem.lower()
    return blueprint.name


def _cleanup_stale_top_group(blueprint, keep: str) -> None:
    """Delete a leftover top group from a prior build whose name no
    longer matches the scene. Only deletes when the candidate is an
    empty transform — anything with children stays put."""
    candidate = blueprint.name
    if not candidate or candidate == keep:
        return
    if not cmds.objExists(candidate):
        return
    if cmds.nodeType(candidate) != 'transform':
        return
    if cmds.listRelatives(candidate, children=True) or []:
        return  # not empty — user owns it now
    cmds.delete(candidate)


def _ensure_group(name, parent=None, inherits_transform=True):
    if cmds.objExists(name):
        node = name
    else:
        node = cmds.createNode('transform', name=name)
    cmds.setAttr(f'{node}.inheritsTransform', bool(inherits_transform))
    if parent:
        current = cmds.listRelatives(node, parent=True) or []
        if not current or current[0] != parent:
            cmds.parent(node, parent)
    return node


def _find_meshes_skinned_to(joints):
    """Find mesh transforms that have any of `joints` as a skin influence."""
    found = set()
    seen_clusters = set()
    for j in joints:
        if not cmds.objExists(j):
            continue
        for sc in (cmds.listConnections(j, type='skinCluster') or []):
            if sc in seen_clusters:
                continue
            seen_clusters.add(sc)
            for g in (cmds.skinCluster(sc, q=True, geometry=True) or []):
                if cmds.nodeType(g) in ('mesh', 'nurbsSurface'):
                    parents = cmds.listRelatives(g, parent=True, fullPath=True) or []
                    if parents:
                        found.add(parents[0])
    return sorted(found)


def unbuild_modules() -> None:
    """Capture state per component, write to network nodes, delete rig_grp."""
    from maya_tools.framework.decorators import undo_chunk
    from maya_tools.rigging.fabricator import build_report

    # Watch-suspended for the whole unbuild, same rationale as
    # build_modules above: module teardown + the Armature re-stand at
    # the epilogue are one bulk mutation, and its event storm must not
    # reach live watchers mid-flight. Inner suspends nest (depth-counted).
    from maya_tools.rigging.fabricator import armature_watch
    with (armature_watch.suspended(),
          build_report.reporting('unbuild') as _brep,
          undo_chunk('Unbuild Modules')):
        clear_rig_outliner_colors()
        bp = _snapshot_blueprint_from_scene()

        # Resolve runtime instances (auto-derive ids).
        instances = []
        for cdata in bp.components:
            cls = get_component_class(cdata.type)
            cid = cdata.id if cdata.id else cls.default_id(cdata.joints)
            region = cdata.region or cls.CONTRACT.default_region
            inst = ComponentInstance(
                id          = cid,
                type        = cdata.type,
                joints      = list(cdata.joints),
                parent_plug = cdata.parent_plug,
                side        = cdata.side,
                role        = cdata.role,
                region      = region,
                options     = dict(cdata.options),
                persisted   = dict(cdata.persisted),
            )
            instances.append(inst)

        # Fill in any missing options from the contract's defaults — symmetry
        # with build_modules so unbuild() can read instance.options[field]
        # the same way build() does.
        for inst in instances:
            cls = get_component_class(inst.type)
            for field_name, field_spec in cls.CONTRACT.options_schema.items():
                inst.options.setdefault(field_name, field_spec.default)

        # Reverse topo order — children captured before parents (per Phase 1
        # unbuild policy; matters more once dependent components share state).
        sorted_instances = _topo_sort_components(instances)
        sorted_instances.reverse()

        # Did the LAST build skip the persisted CV restore (Reset
        # Control Shapes)? Then the live ctrls carry library defaults,
        # and capturing them would destroy every saved sculpt — keep
        # each component's prior cv_data blocks instead, honoring the
        # checkbox's 'Persisted shape data is NOT erased' promise.
        _reg = nodes.get_registry()
        built_with_reset = bool(
            _reg and cmds.attributeQuery('built_with_reset_shapes',
                                         node=_reg, exists=True)
            and cmds.getAttr(f'{_reg}.built_with_reset_shapes'))
        if built_with_reset:
            print('[Fabricator] Last build used Reset Control Shapes — '
                  'keeping saved ctrl sculpts instead of capturing the '
                  'reset defaults.')

        # Capture state per component → write to its network node.
        for inst in sorted_instances:
            cls = get_component_class(inst.type)
            try:
                captured = cls.unbuild(inst)
            except Exception as e:
                cmds.warning(f'Failed to capture state for {inst.id!r}: {e}')
                captured = {}

            for cnode in nodes.get_all_component_nodes():
                if nodes.get_component_id(cnode) == inst.id:
                    # Module capture is a full REPLACE of persisted;
                    # carry the pending ctrl-shape-mirror flag across it
                    # (review wf_2f4a49ba finding #0). The flag survives
                    # a build that never reached the mirror pass (an
                    # unrelated component failed, or a Reset Control
                    # Shapes build) so the next normal build still
                    # honors the mirror promise. Consumed ONLY by the
                    # build_modules tail.
                    prior = nodes.get_component_persisted(cnode) or {}
                    if prior.get('pending_cv_mirror'):
                        captured['pending_cv_mirror'] = \
                            prior['pending_cv_mirror']
                    if built_with_reset:
                        for k, v in prior.items():
                            if k == 'cv_data' or k.endswith('_cv_data'):
                                captured[k] = v
                    nodes.set_component_persisted(cnode, captured)
                    nodes.set_built(cnode, False)
                    break

        if built_with_reset and _reg:
            cmds.setAttr(f'{_reg}.built_with_reset_shapes', False)

        # Tear-down: delete rig_grp (cascades to all component-owned ctrls/nulls).
        if cmds.objExists('rig_grp'):
            cmds.delete('rig_grp')
        # Baked selection sets are not DAG children of rig_grp — they
        # would survive the cascade as empty husks. Remove explicitly;
        # the next build re-bakes them.
        from maya_tools.rigging.fabricator import selection_sets
        selection_sets.delete_selection_sets()

        # Restore bind pose. Component constraints write to bind.rotate during
        # build; deleting the constraints leaves whatever the last solve set as
        # the joint's local rotate. Without restoration, every build/unbuild
        # cycle accumulates drift (or worse, IK lockout damage). The pre-build
        # capture above paired with this restore makes the cycle non-destructive.
        _restore_skeleton_bind_pose(bp)

        # Respawn component pivot locators from captured options so the user
        # can edit them in the next iteration.
        _sync_component_pivots_from_scene()

        # Armature lifecycle epilogue (spec 2026-07-04 §9): back to the
        # edit stage — skins detached, aimers restored from the
        # registry snapshot, Armature standing. build_armature also
        # runs the bake, which is a no-op on the just-restored state.
        # Legacy guard (2026-07-05): rigs built before the registry
        # era have no registry root joint — the unbuild itself
        # succeeded, so don't die at the epilogue; skip the Armature
        # with directions instead.
        skin_connect_app.disconnect_all_skins()
        if nodes.get_registry_root_joint():
            _restore_aimers_from_registry()
            armature.build_armature()
        else:
            cmds.warning(
                'Unbuild: no registry root joint — Armature not '
                'rebuilt (pre-registry rig, or a referenced rig '
                'unbuilt outside its own scene). The rig still '
                'round-trips; ask about registry adoption to '
                'modernize WITHOUT re-authoring modules (File > New '
                'Rig would delete them).')

        _brep.components = [{'id': inst.id, 'status': 'ok'}
                            for inst in sorted_instances]


def adopt_registry() -> str:
    """Adopt a pre-registry rig into the registry era WITHOUT touching
    its modules (Adrian, 2026-07-05 — File > New Rig would delete
    them): create the registry if missing, connect the scene's root
    joint, persist the rig label, stamp the current FS build version.
    Components, skins and geo are untouched.

    Returns a summary string. Raises when the scene has no single
    root joint to adopt."""
    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION
    root = find_scene_root_joint()
    if not root:
        raise RuntimeError('Adopt: no root joint in the scene.')
    if not nodes.get_registry():
        nodes.create_registry(blueprint_name='adopted',
                              origin='registry-adoption')
    nodes.set_registry_root_joint(root)
    label = nodes.get_or_init_rig_label() or ''
    nodes.set_registry_fabricator_version(FABRICATOR_VERSION)
    return (f'Registry adopted: root {root!r}, label {label!r}, '
            f'Fabricator {FABRICATOR_VERSION}.')


def new_rig() -> None:
    """Tear down the animation rig — clean slate for a fresh build.

    Deletes (if present):
      - rig_grp (ctrls + nulls cascade).
      - guides_grp.
      - pivots_grp.
      - All Fabricator network nodes (per-component nodes, then registry).
      - All FAB_RigBinding network nodes (rig contract — orphaned once
        rig_grp is gone, deleting them avoids dangling pointers for the
        anim exporter / pose library).
      - _Joints and _Geo display layers (created by build_modules; no
        reason to keep empty layers around).

    Does NOT touch:
      - joints (the skeleton stays — re-Build Modules to reapply rigging).
      - skin clusters / weights.
      - geo_grp / meshes.
      - ksMirror constraints / ghosts (skeleton-editing tools, not rig
        artifacts — clear separately via Break All Mirror Constraints
        if you want a true scene-wide reset).
    """
    if cmds.objExists('rig_grp'):
        cmds.delete('rig_grp')

    # Armature + aimers are edit-stage tooling — a clean slate drops
    # them too (rebuilt on the next load / Aim).
    if armature.armature_exists():
        armature.delete_armature()
    joint_orient_app.delete_all_aimers()

    nodes.delete_guides_grp()
    nodes.delete_pivots_grp()

    # Component + registry teardown via MARKER-based discovery, not the
    # registry's components message multi. get_all_component_nodes only
    # returns nodes still wired into the registry; an orphaned fab_<id>
    # (e.g. left over from a partial / broken cleanup) would be missed
    # and then collide on the next template load. find_*_by_marker
    # catches every tagged node regardless of connection state.
    for cnode in nodes.find_all_component_nodes_by_marker():
        nodes.delete_component_node(cnode)
    for reg in nodes.find_all_registry_nodes():
        if cmds.objExists(reg):
            cmds.delete(reg)

    # FAB_RigBinding nodes — orphaned once rig_grp is gone. Defer-import so
    # fs_app's module-load graph doesn't pull in utils.maya.rig_binding for
    # every scene.
    from maya_tools.utils.maya import rig_binding
    bindings = rig_binding.find_all_bindings() or []
    if bindings:
        cmds.delete(bindings)

    # Display layers created by build_modules — _Joints holds bind joints,
    # _Geo holds the geo_grp transform. Deleting the LAYER doesn't delete
    # its members (the skeleton + geo survive).
    for layer in ('_Joints', '_Geo'):
        if cmds.objExists(layer):
            cmds.delete(layer)
def find_scene_root_joint() -> str | None:
    """The scene skeleton's single root joint (long name).

    A root joint = a joint with no joint ancestor (grouping transforms
    above it are fine — imported UE skeletons often sit under a group).

    Returns None when the scene has no joints at all (New Rig then
    seeds a fresh root at origin). Raises RuntimeError (user-readable)
    when there is more than one root — New Rig adopts exactly one
    skeleton hierarchy.
    """
    def _has_joint_ancestor(j: str) -> bool:
        parents = cmds.listRelatives(j, parent=True, fullPath=True) or []
        while parents:
            p = parents[0]
            if cmds.nodeType(p) == 'joint':
                return True
            parents = cmds.listRelatives(p, parent=True,
                                         fullPath=True) or []
        return False

    all_joints = cmds.ls(type='joint', long=True) or []
    if not all_joints:
        return None
    roots = [j for j in all_joints if not _has_joint_ancestor(j)]
    if len(roots) > 1:
        names = ', '.join(r.split('|')[-1] for r in roots)
        raise RuntimeError(
            f'New Rig needs exactly one skeleton root, found '
            f'{len(roots)}: {names}. Parent them under a single root '
            f'joint (or delete the strays) and retry.')
    return roots[0]


def init_rig(rig_name: str) -> str:
    """Start a Fabricator rig WITHOUT a blueprint file.

    The bring-your-own-skeleton entry point (spec: Adrian 2026-07-04):
    adopt the scene's existing skeleton — or, in an empty scene, create
    a root joint at world origin — then create the registry, wire the
    root joint, stamp the rig label, put every joint in the _Joints
    reference layer, and stand the Armature up (fresh aimers seeded by
    natural-aim detection). The caller adds the World component — the
    UI path owns component creation.

    Returns the root joint (long name). Raises RuntimeError when the
    scene holds more than one skeleton root, or when a registry
    already exists (tear down first via new_rig()).
    """
    from maya_tools.framework.decorators import undo_chunk

    root = find_scene_root_joint()
    with undo_chunk('NewRig'):
        if root is None:
            # Starting from scratch: a root joint at 000.
            cmds.select(clear=True)
            root = cmds.joint(name='root', position=(0.0, 0.0, 0.0))
            root = cmds.ls(root, long=True)[0]

        nodes.create_registry(blueprint_name=rig_name or 'untitled')
        nodes.set_registry_root_joint(root)
        if rig_name:
            nodes.set_rig_label(rig_name)

        descendants = cmds.listRelatives(
            root, allDescendents=True, type='joint', fullPath=True) or []
        add_joints_to_reference_layer([root] + descendants)

        armature.build_armature()
    return root
