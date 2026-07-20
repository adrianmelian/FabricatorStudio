"""Toolbar command layer: ONE undo + error surface for every widget fire,
plus the strip-only selection glue for backends that take explicit args.

Convention (CLAUDE.md): app layer raises; the UI catches. The strip has no
window logger, so this runner is its _handle_error: warning + traceback.
Backends that self-wrap undo_chunk simply nest — harmless."""
from __future__ import annotations

import traceback

import maya.cmds as cmds

from maya_tools.framework.decorators import undo_chunk
from maya_tools.framework.toolbar import toolbar_prefs

TEMP_SKIN_FILENAME = "am_skin_temp.json"


class ToolbarUserError(Exception):
    """An EXPECTED, user-fixable condition (wrong selection, nothing picked).
    The runner surfaces it as a clean in-view heads-up + one-line warning,
    NOT a traceback - a traceback here reads as 'the tool broke' when the
    real message is 'pick the right thing and try again' (Adrian, 2026-07-03)."""


def _user_warn(label, msg):
    """Friendly surface for a ToolbarUserError: a fading viewport message so
    it's seen where the user is looking, plus a Script Editor breadcrumb.
    Fully defensive - surfacing a friendly error must never itself throw."""
    try:
        cmds.warning(f"[FS toolbar] {label}: {msg}")
    except Exception:
        pass
    try:
        cmds.inViewMessage(assistMessage=f"<hl>{msg}</hl>", position="midCenter",
                           fade=True, fadeStayTime=2200, dragKill=True)
    except Exception:
        pass


def run_command(label, fn, *args, **kwargs):
    """Execute a resolved manifest command with one undo chunk and the
    toolbar's error surface. Returns the command's result or None on error."""
    try:
        with undo_chunk(label):
            return fn(*args, **kwargs)
    except ToolbarUserError as exc:
        _user_warn(label, exc)
        return None
    except Exception as exc:
        cmds.warning(f"[FS toolbar] {label}: {exc}")
        traceback.print_exc()
        return None


def run_in_gesture(label, fn, *args, **kwargs):
    """Error-trapped but CHUNK-LESS execution for slider ticks (decision
    B15): the gesture chunk opened by begin_undo_gesture owns the undo;
    per-tick chunks here would flood the queue."""
    try:
        return fn(*args, **kwargs)
    except ToolbarUserError as exc:
        _user_warn(label, exc)
        return None
    except Exception as exc:
        cmds.warning(f"[FS toolbar] {label}: {exc}")
        traceback.print_exc()
        return None


def begin_undo_gesture(label):
    """One slider gesture = one undo chunk (opened at expand)."""
    cmds.undoInfo(openChunk=True, chunkName=label)


def end_undo_gesture():
    """Close the gesture chunk (at collapse; widgets guarantee pairing)."""
    cmds.undoInfo(closeChunk=True)


# --- G2 glue ---------------------------------------------------------------

def mirror_joints_selected():
    from maya_tools.skeleton.joint_mirror import joint_mirror_app
    return joint_mirror_app.mirror_joints()          # raises; runner catches


def create_aimers_from_selected_root(scale=10.0):
    """Root-walk + create in one callable so the WHOLE flow (including the
    'Select a joint' raise) runs inside run_command's error surface."""
    from maya_tools.rigging.joint_orient import joint_orient_app
    return joint_orient_app.create_aimers_from_root(
        find_selected_joint_root(), scale=scale)


def disconnect_mirror_selected():
    from maya_tools.skeleton.joint_mirror import joint_mirror_app
    count = joint_mirror_app.disconnect_mirror()
    cmds.inViewMessage(assistMessage=f"Disconnected {count} mirror link(s)",
                       position="botCenter", fade=True)
    return count


def find_selected_joint_root():
    """Walk from the first selected joint to its top joint (the ~8-line root
    walk from joint_orient_ui._find_root, duplicated here so the popover
    needs no UI-module import)."""
    sel = cmds.ls(sl=True, type="joint")
    if not sel:
        raise RuntimeError("Select a joint")
    node = sel[0]
    while True:
        parents = cmds.listRelatives(node, parent=True, type="joint")
        if not parents:
            return node
        node = parents[0]


# --- utility glue (Task 10 rider, decision B19) ------------------------------

def snap_position_only():
    """Snap A to B, position only - the shelf's exact wiring (undo-wrapped,
    component-centroid mode live in the backing)."""
    from maya_tools.framework import utilities
    return utilities.snap_a_to_b(orientation=False)


# --- match transforms (Snap button right-click, Adrian 2026-07-16) -----------
# cmds.matchTransform options mirroring Maya's Modify > Match Transformations;
# sources match the LAST selected, same semantics as Snap A to B.

def match_all():
    """Match All Transforms - position + rotation + scale to the last selected."""
    from maya_tools.framework import utilities
    return utilities.match_transform(position=True, rotation=True, scale=True)


def match_rotation():
    """Match world rotation to the last selected."""
    from maya_tools.framework import utilities
    return utilities.match_transform(rotation=True)


def match_translation():
    """Match world translation to the last selected."""
    from maya_tools.framework import utilities
    return utilities.match_transform(position=True)


def match_scale():
    """Match scale to the last selected."""
    from maya_tools.framework import utilities
    return utilities.match_transform(scale=True)


def match_pivot():
    """Match rotate + scale pivots to the last selected."""
    from maya_tools.framework import utilities
    return utilities.match_transform(pivot=True)


# --- create (sticky Create button, Wave B item 2) ----------------------------

# All geo is poly here (no NURBS), so 'Cube' == polyCube etc. spaceLocator /
# joint round out the set. Each maker returns the created transform.
# 'null' (Adrian, 2026-07-05): plain empty group — group() returns the
# transform name directly (no [0]; only shape-makers return lists).
_CREATE_MAKERS = {
    "null":     lambda: cmds.group(empty=True, name='null1'),
    "locator":  lambda: cmds.spaceLocator()[0],
    "cube":     lambda: cmds.polyCube()[0],
    "sphere":   lambda: cmds.polySphere()[0],
    "cylinder": lambda: cmds.polyCylinder()[0],
    "plane":    lambda: cmds.polyPlane()[0],
    "joint":    lambda: cmds.joint(),
}


def select_by_wildcard(pattern):
    """Select everything matching the wildcard pattern(s) — the native
    top-bar quick-select feel (Adrian, 2026-07-05: renamer's Select by
    Name mode). Space-separated patterns union ('*_l *_r' grabs both
    sides). Returns the match count for the log."""
    tokens = [t for t in str(pattern).split() if t]
    if not tokens:
        raise ToolbarUserError("Type a wildcard pattern, e.g. *_r")
    matches = cmds.ls(*tokens, long=False) or []
    if not matches:
        raise ToolbarUserError(
            f"No objects match {' '.join(tokens)!r}")
    cmds.select(matches, replace=True)
    return len(matches)


def _selection_center():
    """World-space center of the current selection's bounding box, or the
    origin when nothing is selected (works on components too, e.g. verts)."""
    sel = cmds.ls(sl=True, flatten=True) or []
    if not sel:
        return (0.0, 0.0, 0.0)
    bb = cmds.exactWorldBoundingBox(sel)      # xmin ymin zmin xmax ymax zmax
    return ((bb[0] + bb[3]) / 2.0,
            (bb[1] + bb[4]) / 2.0,
            (bb[2] + bb[5]) / 2.0)


def create_primitive(kind):
    """Create `kind` at the selection's center (origin if nothing selected).
    Selection is cleared FIRST so joint() doesn't parent under it and no maker
    inherits the current selection; the new node ends up selected."""
    kind = str(kind)
    maker = _CREATE_MAKERS.get(kind)
    if maker is None:
        raise RuntimeError(f"Unknown create kind {kind!r}")
    pos = _selection_center()
    cmds.select(clear=True)
    node = maker()
    cmds.xform(node, worldSpace=True, translation=pos)
    cmds.select(node, replace=True)
    return node


def create_locator():
    """Zero-arg locator maker for the strip's dedicated locator button
    (Adrian, 2026-07-03): a locator at the selection center / origin."""
    return create_primitive("locator")


# --- controls glue ----------------------------------------------------------

def mirror_selected_curves():
    """mirror_curve for every selected side-tokened ctrl, one undo step."""
    from maya_tools.rigging.fabricator import curve_mirror
    sel = cmds.ls(sl=True, type="transform")
    if not sel:
        raise RuntimeError("Select one or more side-tokened controls")
    done = [curve_mirror.mirror_curve(ctrl) for ctrl in sel]
    cmds.select(sel, replace=True)
    return done


# --- constraints (Wave B item 3) ---------------------------------------------

_CONSTRAINT_FNS = {
    "parent": lambda: cmds.parentConstraint(maintainOffset=True),
    "point":  lambda: cmds.pointConstraint(maintainOffset=True),
    "orient": lambda: cmds.orientConstraint(maintainOffset=True),
    "scale":  lambda: cmds.scaleConstraint(maintainOffset=True),
    "aim":    lambda: cmds.aimConstraint(maintainOffset=True),
}


def constrain(kind):
    """Constrain the LAST selected object to the rest (Maya convention: pick
    the driver(s), then the driven object last). maintainOffset on."""
    sel = cmds.ls(sl=True, long=True) or []
    if len(sel) < 2:
        raise RuntimeError("Select driver(s), then the object to constrain last")
    fn = _CONSTRAINT_FNS.get(str(kind))
    if fn is None:
        raise RuntimeError(f"Unknown constraint {kind!r}")
    return fn()


def delete_constraints():
    """Generic delete (item 3): remove EVERY constraint on the selected
    objects AND sever any live mirror network on them - 'delete constraint'
    covers mirror too, not just parent/point/orient/scale/aim."""
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        raise RuntimeError("Select object(s)")
    n = 0
    cons = cmds.listRelatives(sel, children=True, type="constraint",
                              fullPath=True) or []
    if cons:
        cmds.delete(cons)
        n += len(cons)
    try:                                 # sever live mirror links, if any
        from maya_tools.skeleton.joint_mirror import joint_mirror_app
        n += joint_mirror_app.disconnect_mirror() or 0
    except Exception:
        pass                             # no mirror network / no joints selected
    cmds.inViewMessage(assistMessage=f"Deleted {n} constraint/mirror link(s)",
                       position="botCenter", fade=True)
    return n


# --- scene ops (B21/B22; shelf snippets promoted) ----------------------------

def save_scene():
    cmds.SaveScene()


def save_scene_as():
    cmds.SaveSceneAs()


def open_scene():
    cmds.OpenScene()


def reload_scene():
    """Reopen the current scene WITH an unsaved-changes prompt (B22) - the
    shelf snippet's force=True silently discards edits. Reuse
    scene_loader's prompt helper rather than reimplementing it."""
    from maya_tools.framework import scene_loader
    path = cmds.file(query=True, sceneName=True)
    if not path:
        cmds.warning("Scene not saved to disk")
        return
    scene_loader._open_with_unsaved_prompt(path)


# --- selection helpers (B21; inline shelf snippets promoted verbatim) ---------

def select_influencing_joints():
    """Select the influences of the selected meshes' skinClusters.
    Promoted from the inline Selection Helpers snippet (fsAnim.json:133);
    silent no-ops become warnings."""
    sel = cmds.ls(sl=True)
    if not sel:
        cmds.warning("Nothing selected")
        return []
    hist = cmds.listHistory(sel, pruneDagObjects=True) or []
    scs = cmds.ls(hist, type="skinCluster")
    joints = list({j for sc in scs
                   for j in (cmds.skinCluster(sc, query=True, influence=True) or [])})
    if not joints:
        cmds.warning("No skinCluster found on selection")
        return []
    cmds.select(joints, replace=True)
    return joints


def select_influenced_meshes():
    """Select geometry influenced by the selected joints (promoted from
    fsAnim.json:134, warnings instead of silent no-ops)."""
    joints = cmds.ls(sl=True, type="joint")
    if not joints:
        cmds.warning("Select joints first")
        return []
    meshes = list({g for j in joints
                   for sc in (cmds.listConnections(j, type="skinCluster") or [])
                   for g in (cmds.skinCluster(sc, query=True, geometry=True) or [])})
    if not meshes:
        cmds.warning("No skin bindings found")
        return []
    cmds.select(meshes, replace=True)
    return meshes


# --- weights glue (B21) -------------------------------------------------------

def mirror_selected_weights():
    """Mirror skin weights on the FIRST selected skinned mesh across YZ.
    Promoted from the inline shelf snippet (fsAnim.json:279) with its
    silent no-op paths converted to raises (the runner warns)."""
    sel = cmds.ls(sl=True)
    if not sel:
        raise RuntimeError("Select a skinned mesh")
    hist = cmds.listHistory(sel[0], pruneDagObjects=True) or []
    skins = cmds.ls(hist, type="skinCluster")
    if not skins:
        raise RuntimeError("No skinCluster on selection")
    with undo_chunk("Mirror Skin Weights"):
        cmds.copySkinWeights(ss=skins[0], ds=skins[0], mirrorMode="YZ",
                             surfaceAssociation="closestPoint",
                             influenceAssociation=["oneToOne", "oneToOne"],
                             normalize=True)


def add_influence_from_selected():
    """Add the selected influence(s) to the selected skinned mesh directly -
    NO options dialog (Adrian, 2026-07-03): weight 0, weights LOCKED. Selection
    is Maya's addInfluence convention: the influence transform(s) plus one
    skinned mesh, in any order. Already-bound influences are skipped."""
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        raise RuntimeError("Select influence(s) then a skinned mesh")
    mesh = None
    for node in sel:
        hist = cmds.listHistory(node, pruneDagObjects=True) or []
        if cmds.ls(hist, type="skinCluster"):
            mesh = node
            break
    if mesh is None:
        raise RuntimeError("No skinned mesh in the selection")
    sc = cmds.ls(cmds.listHistory(mesh, pruneDagObjects=True),
                 type="skinCluster")[0]
    bound = set(cmds.ls(cmds.skinCluster(sc, query=True, influence=True) or [],
                        long=True))
    influences = [n for n in sel
                  if n != mesh and n not in bound
                  and cmds.ls(n, type="transform")]
    if not influences:
        raise RuntimeError("Select one or more unbound influences plus the mesh")
    for inf in influences:
        cmds.skinCluster(sc, edit=True, addInfluence=inf,
                         weight=0.0, lockWeights=True)
    cmds.select(sel, replace=True)
    return influences


def remove_influence():
    """Maya's RemoveInfluence runtime command (selected influence + mesh)."""
    cmds.RemoveInfluence()


# --- skin popover glue --------------------------------------------------------

def disconnect_all_skins():
    """skin_connect_app.disconnect_all_skins under one undo chunk (the
    module has none - scout-verified gap)."""
    from maya_tools.skinning import skin_connect_app
    with undo_chunk("Disconnect All Skins"):
        return skin_connect_app.disconnect_all_skins()


def reconnect_all_skins():
    from maya_tools.skinning import skin_connect_app
    with undo_chunk("Reconnect All Skins"):
        return skin_connect_app.reconnect_all_skins()


# --- skeleton temp slot (mirrors the skin temp pattern) ------------------------

TEMP_SKELETON_FILENAME = "am_skeleton_temp.json"


def save_temp_skeleton():
    from maya_tools.skeleton.skeleton_io import skeleton_io_app
    root = find_selected_joint_root()
    path = cmds.internalVar(userAppDir=True) + TEMP_SKELETON_FILENAME
    skeleton_io_app.export_skeleton(root, path)
    cmds.inViewMessage(assistMessage=f"Skeleton saved (temp) from {root}",
                       position="botCenter", fade=True)


def load_temp_skeleton():
    import os
    from maya_tools.skeleton.skeleton_io import skeleton_io_app
    path = cmds.internalVar(userAppDir=True) + TEMP_SKELETON_FILENAME
    if not os.path.exists(path):
        raise RuntimeError("No temp skeleton found - run Save Temp first")
    with undo_chunk("Import Skeleton"):
        return skeleton_io_app.import_skeleton(path)


# --- fabricator quick actions (B24) --------------------------------------------

def fabricator_build_quick():
    """Raw fs_app.build_modules - the FSWindow confirm-guards are USER
    decisions a one-click button must not auto-take; RuntimeErrors surface
    through the runner and the full window is the escape hatch."""
    from maya_tools.rigging.fabricator import fs_app
    fs_app.build_modules()
    # SANCTIONED SEAM (decision C5): the Qt-free command layer may lazily
    # import the app layer for post-action UI refresh - never at module
    # scope. The scene events do NOT cover this case: Build/Unbuild mutates
    # attrs on EXISTING network nodes and creates/deletes rig_grp, a
    # transform, so no network node-added/removed event fires.
    from maya_tools.framework.toolbar import toolbar_app
    toolbar_app.refresh_fabricator_lit()


def fabricator_unbuild_quick():
    from maya_tools.rigging.fabricator import fs_app
    fs_app.unbuild_modules()
    from maya_tools.framework.toolbar import toolbar_app   # sanctioned seam (C5)
    toolbar_app.refresh_fabricator_lit()


# --- fabricator context slot (Phase C, decision C5) -----------------------------

def fabricator_rig_present():
    """Cheap constant-time probe (decision C5): name + marker attr.
    NEVER detect_mode() here - it carries a setAttr self-heal side
    effect and an O(scene) walk (scout-verified)."""
    return bool(cmds.objExists("fab_registry")
                and cmds.attributeQuery("fab_registry",
                                        node="fab_registry", exists=True))

# Known v1 limit (C5): the name-based probe misses a RENAMED or
# REFERENCED/namespaced registry (ns:fab_registry) - the fabricator's
# own nodes.get_registry() marker-walk is the exact-but-heavier fallback
# if CP-C hits it in practice. For item 4 (2026-07-03) that limit is a
# FEATURE: a referenced rig in an anim scene reads as not-present, so the
# Build/Unbuild popover correctly suppresses itself there.


def fabricator_is_built():
    """True when the rig is built (rig_grp present). Pairs with
    fabricator_rig_present() for the item-4 popover: present + built ->
    Unbuild, present + unbuilt -> Build, not present -> no popover."""
    return bool(cmds.objExists("rig_grp"))


# --- paint-on-select mode (Phase C, decision C4) --------------------------------

_PAINT_CB = None          # scene_listeners cid when the mode is ON
_PAINT_REENTRY = False


def paint_on_select_enabled():
    """Truth lives in the LISTENER REGISTRY, not this module's flag
    (review-caught critical): teardown()/dock-switch/hide/dev-reload all
    drain scene_listeners without telling this module - a bare flag would
    relight the rebuilt Toggle while auto-paint is silently dead."""
    global _PAINT_CB
    if _PAINT_CB is None:
        return False
    from maya_tools.framework.toolbar import scene_listeners
    if not scene_listeners.is_registered(_PAINT_CB):
        _PAINT_CB = None               # registry drained under us: we're OFF
        return False
    return True


def toggle_paint_on_select():
    """Adrian's auto-paint mode: while ON, selecting a skinned mesh drops you
    straight into Paint Skin Weights, and activating it also opens the Paint
    Skin Weights Tool Settings (Adrian, 2026-07-08). Returns the new state
    (the Toggle widget syncs its lit look from it)."""
    global _PAINT_CB
    from maya_tools.framework.toolbar import scene_listeners
    if paint_on_select_enabled():      # registry-backed truth, not the flag
        scene_listeners.remove(_PAINT_CB)
        _PAINT_CB = None
        return False
    _PAINT_CB = scene_listeners.on_selection_changed(_paint_on_selection)
    _paint_on_selection()              # honor the current selection immediately
    open_paint_tool_settings()         # surface the Tool Settings on activation
    return True


def _skinned_meshes_from_selection():
    """Mesh SHAPES with a skinCluster touched by the current selection - whether
    OBJECT mode (a mesh transform) or COMPONENT mode (verts/edges/faces the user
    picked to isolate an area). objectsOnly collapses components to their mesh
    (Adrian, 2026-07-03: selecting verts must also arm paint, not just the mesh)."""
    objs = cmds.ls(sl=True, objectsOnly=True, long=True) or []
    shapes = set()
    for o in objs:
        if cmds.nodeType(o) == "mesh":
            shapes.add(o)
        else:
            for sh in (cmds.listRelatives(o, shapes=True, type="mesh",
                                          noIntermediate=True, fullPath=True) or []):
                shapes.add(sh)
    return [s for s in shapes
            if cmds.ls(cmds.listHistory(s, pruneDagObjects=True) or [],
                       type="skinCluster")]


def _paint_on_selection():
    global _PAINT_REENTRY
    if _PAINT_REENTRY:
        return
    if cmds.currentCtx() == "artAttrSkinContext":
        return                          # already painting - don't re-enter
    try:
        sel = cmds.ls(sl=True, long=True) or []
        if not sel:
            return
        skinned = _skinned_meshes_from_selection()
        if not skinned:
            return
        _PAINT_REENTRY = True           # our own select refires the event
        try:
            # A component selection (verts/edges/faces) is the user isolating an
            # area - KEEP it. An object-mode selection has no component context,
            # so convert to verts on the skinned mesh(es) for the paint tool.
            if not cmds.filterExpand(sel, sm=(31, 32, 34)):
                verts = cmds.polyListComponentConversion(skinned, toVertex=True)
                if verts:
                    cmds.select(verts, replace=True)
            # Same verb as the shelf's Paint Skin Weights button (Adrian,
            # 2026-07-03: it opens the tool GUI the way he wants). The
            # currentCtx guard above means this fires only when ENTERING paint,
            # so the tool surfaces once - not on every subsequent selection.
            cmds.ArtPaintSkinWeightsToolOptions()
        finally:
            _PAINT_REENTRY = False
    except Exception:
        import traceback
        traceback.print_exc()           # never raise from a listener body


def open_paint_tool_settings():
    cmds.ArtPaintSkinWeightsToolOptions()


def enter_paint_once():
    """The old one-shot stub behavior, kept as a menu action."""
    sel = cmds.ls(sl=True)
    if sel:
        verts = cmds.polyListComponentConversion(sel, toVertex=True)
        if verts:
            cmds.select(verts, replace=True)
    cmds.ArtPaintSkinWeightsToolOptions()


# --- insert joints (Task 3 core) ------------------------------------------------
# Strip sliders retired 2026-07-18 (Adrian: insert/offset joints are obsolete
# under the Armature rig system; the manifest entries are cut). The insert +
# offset gesture commands below stay spec-complete for a re-add, same
# treatment as the ConstraintsPopover retirement. Menu access to inserting
# joints (fs_menu.json -> skeleton_utils.insert_joints_between_selection) is
# untouched.

def insert_joints_apply(count, bias=0.0):
    from maya_tools.skeleton import skeleton_utils
    return skeleton_utils.insert_joints_between(int(round(count)), bias=bias)


# --- live insert-joints slider (idea A, 2026-07-03) ---------------------------
# The ExpandingSlider drives this: gesture_begin captures the two ADJACENT
# selected joints (by UUID - names change as the chain reparents), each live
# tick REBUILDS to exactly N joints between them (delete our in-betweens, then
# re-insert N), gesture_end clears. Adjacency is required so we only ever
# delete joints WE inserted, never a pre-existing chain.
_INSERT_STATE = None


def _resolve_uuid(uuid):
    # Non-long form on purpose: skeleton_utils' parent-walk compares SHORT
    # names, so we feed it the same form (long DAG paths silently never match).
    names = cmds.ls(uuid) or []
    return names[0] if names else None


def _joints_between(ancestor, descendant):
    """The joints strictly between (walk child->parent up to, not incl, ancestor)."""
    node = (cmds.listRelatives(descendant, parent=True, type="joint") or [None])[0]
    mids = []
    while node and node != ancestor:
        mids.append(node)
        node = (cmds.listRelatives(node, parent=True, type="joint") or [None])[0]
    return mids


def insert_joints_begin():
    """Capture the two ADJACENT selected joints (parent + direct child) for a
    live insert gesture; open the undo chunk. Adjacency is checked directly
    (short names) - no resolve_ancestor_descendant, whose short-name walk
    can't match long-path inputs."""
    global _INSERT_STATE
    _INSERT_STATE = None
    sel = cmds.ls(sl=True, type="joint") or []
    if len(sel) != 2:
        raise ToolbarUserError("Select exactly two adjacent joints "
                               "(a joint and its direct parent) to insert between")
    j0, j1 = sel
    p0 = (cmds.listRelatives(j0, parent=True, type="joint") or [None])[0]
    p1 = (cmds.listRelatives(j1, parent=True, type="joint") or [None])[0]
    if p1 == j0:
        a, d = j0, j1
    elif p0 == j1:
        a, d = j1, j0
    else:
        raise ToolbarUserError("Those two joints aren't adjacent - pick a joint "
                               "and its DIRECT parent to insert between")
    # Validation passed: only now do we open the chunk, so a rejected
    # selection never leaves a dangling openChunk for end() to mis-close.
    _INSERT_STATE = {"a": cmds.ls(a, uuid=True)[0],
                     "d": cmds.ls(d, uuid=True)[0]}
    cmds.undoInfo(openChunk=True, chunkName="Insert Joints")


def insert_joints_live(n):
    """Rebuild to exactly N joints between the captured pair (idea A2)."""
    if _INSERT_STATE is None:
        return
    from maya_tools.skeleton import skeleton_utils
    a = _resolve_uuid(_INSERT_STATE["a"])
    d = _resolve_uuid(_INSERT_STATE["d"])
    if not a or not d:
        return
    n = max(0, int(round(n)))
    mids = _joints_between(a, d)
    if mids:
        cmds.parent(d, a)            # reconnect child directly (keeps world pos)
        cmds.delete(mids)
    if n > 0:
        skeleton_utils.insert_joints_between(n, 0.0, [a, d])


def insert_joints_end():
    global _INSERT_STATE
    # Only close a chunk we actually opened: a rejected begin (bad selection)
    # left _INSERT_STATE None and opened nothing, so closeChunk here would
    # unbalance Maya's undo queue.
    if _INSERT_STATE is None:
        return
    _INSERT_STATE = None
    cmds.undoInfo(closeChunk=True)


# --- offset / spread joints slider (idea B, 2026-07-03) -----------------------
# A center-return jog beside insert. Two modes, chosen in the companion panel
# (or right-click): OFFSET translates the selected joints by the slider amount
# along the checked axes; SPREAD moves each joint away from / toward the
# selection's centroid in a "scale from center" pattern - pure translation, no
# actual scale (idea B2). Axis + mode live in _OFFSET_OPTS; each gesture
# snapshots them + the joints' world positions so a live drag is a pure remap
# of captured baselines (the same rebuild-from-baseline shape as insert).
_OFFSET_STATE = None
_OFFSET_OPTS = {"x": True, "y": False, "z": False, "mode": "offset"}
# Mirrors toolbar.json offset_joints "max": SPREAD is a ratio, so it
# normalizes against this (v=+/-_OFFSET_MAX -> full double / collapse to
# center) while OFFSET uses v straight as world units. Keep in sync with JSON.
_OFFSET_MAX = 100.0


def offset_axis_enabled(axis):
    return bool(_OFFSET_OPTS.get(axis, False))


def offset_set_axis(axis, on):
    if axis in ("x", "y", "z"):
        _OFFSET_OPTS[axis] = bool(on)


def offset_mode():
    return _OFFSET_OPTS["mode"]


def offset_mode_is(mode):
    return _OFFSET_OPTS["mode"] == mode


def offset_set_mode(mode):
    if mode in ("offset", "spread"):
        _OFFSET_OPTS["mode"] = mode


def _compute_offset(items, centroid, axes, mode, v, vmax=_OFFSET_MAX):
    """Pure position remap (unit-tested offscreen): given captured (uuid, pos)
    baselines, the centroid, the checked (ax, ay, az) axes and the slider
    value, return the new (uuid, pos) list. OFFSET adds v (world units) on each
    checked axis; SPREAD moves each joint out from the centroid by v/vmax of
    its distance on each checked axis (v=+vmax doubles the spread, v=-vmax
    collapses to center) - normalized so the offset range can grow without
    over-spreading."""
    ax, ay, az = axes
    cx, cy, cz = centroid
    out = []
    for u, (ox, oy, oz) in items:
        if mode == "spread":
            f = v / (vmax or 1.0)
            nx = ox + ((ox - cx) * f if ax else 0.0)
            ny = oy + ((oy - cy) * f if ay else 0.0)
            nz = oz + ((oz - cz) * f if az else 0.0)
        else:
            nx = ox + (v if ax else 0.0)
            ny = oy + (v if ay else 0.0)
            nz = oz + (v if az else 0.0)
        out.append((u, (nx, ny, nz)))
    return out


def offset_joints_begin():
    """Snapshot the selected joints (world positions, by UUID), the centroid,
    and the current axis/mode for a live offset gesture; open the chunk. Bad
    selections raise ToolbarUserError -> clean in-view message, no traceback."""
    global _OFFSET_STATE
    _OFFSET_STATE = None
    sel = cmds.ls(sl=True, type="joint") or []
    if not sel:
        raise ToolbarUserError("Select the joint(s) you want to offset")
    if not (_OFFSET_OPTS["x"] or _OFFSET_OPTS["y"] or _OFFSET_OPTS["z"]):
        raise ToolbarUserError("Pick at least one axis (X, Y or Z) in the panel first")
    if _OFFSET_OPTS["mode"] == "spread" and len(sel) < 2:
        raise ToolbarUserError("Spread needs two or more joints "
                               "(it moves them apart from their shared center)")
    # Shallow-to-deep so setting a parent's world position never drags an
    # already-placed child off its mark (world xform sets are order-sensitive
    # in a chain). Longer DAG path == deeper.
    depth = {j: (cmds.ls(j, long=True) or [j])[0].count("|") for j in sel}
    ordered = sorted(sel, key=lambda j: depth[j])
    uuids = cmds.ls(ordered, uuid=True)
    items = [(u, tuple(cmds.xform(j, q=True, ws=True, t=True)))
             for j, u in zip(ordered, uuids)]
    xs = [p[0] for _, p in items]
    ys = [p[1] for _, p in items]
    zs = [p[2] for _, p in items]
    n = len(items)
    _OFFSET_STATE = {"items": items,
                     "centroid": (sum(xs) / n, sum(ys) / n, sum(zs) / n),
                     "axes": (_OFFSET_OPTS["x"], _OFFSET_OPTS["y"], _OFFSET_OPTS["z"]),
                     "mode": _OFFSET_OPTS["mode"]}
    cmds.undoInfo(openChunk=True, chunkName="Offset Joints")


def offset_joints_live(v):
    """Remap every captured joint to baseline + offset for the slider value."""
    if _OFFSET_STATE is None:
        return
    new = _compute_offset(_OFFSET_STATE["items"], _OFFSET_STATE["centroid"],
                          _OFFSET_STATE["axes"], _OFFSET_STATE["mode"], float(v))
    for u, pos in new:
        node = _resolve_uuid(u)
        if node:
            cmds.xform(node, ws=True, t=pos)


def offset_joints_end():
    global _OFFSET_STATE
    if _OFFSET_STATE is None:
        return
    _OFFSET_STATE = None
    cmds.undoInfo(closeChunk=True)


# --- temp-skin pattern (fs_menu.json:128-138) -----------------------------------

def _temp_skin_path():
    return cmds.internalVar(userAppDir=True) + TEMP_SKIN_FILENAME


def _selected_skinnable_meshes():
    """Mesh-transform filter matching the Temp Skin menu precedent
    (fs_menu.json:128-138): typed transforms, intermediate shapes excluded."""
    sel = cmds.ls(sl=True, type="transform", long=True) or []
    meshes = [t for t in sel
              if cmds.listRelatives(t, shapes=True, type="mesh",
                                    noIntermediate=True, fullPath=True)]
    if not meshes:
        raise RuntimeError("Select one or more meshes")
    return meshes


def _require_temp_skin():
    import os
    path = _temp_skin_path()
    if not os.path.exists(path):
        raise RuntimeError("No temp skin found - run Save Temp Skin first")
    return path


def save_temp_skin():
    from maya_tools.skinning.skin_io import skin_io_app
    skin_io_app.save_skin_from_meshes(_selected_skinnable_meshes(),
                                      _temp_skin_path())
    cmds.inViewMessage(assistMessage="Skin saved (temp)",
                       position="botCenter", fade=True)


def load_temp_skin():
    from maya_tools.skinning.skin_io import skin_io_app
    skin_io_app.load_skin_to_meshes(_selected_skinnable_meshes(),
                                    _require_temp_skin(), mode="direct")


def transfer_temp_skin():
    from maya_tools.skinning.skin_io import skin_io_app
    skin_io_app.load_skin_to_meshes(_selected_skinnable_meshes(),
                                    _require_temp_skin(), mode="transfer")


# --- smoosh sticky strength (Task 8 rider, decision B18; persisted per C9) ------

_SMOOSH_DEFAULT_ALPHA = 0.3


def _stored_smoosh_alpha():
    """per_widget gains its first consumer (C9). Guarded float coercion:
    a hand-edited prefs file must never break the module import."""
    try:
        return float(toolbar_prefs.load_prefs()["per_widget"]["smoosh"]["alpha"])
    except (KeyError, TypeError, ValueError):
        return _SMOOSH_DEFAULT_ALPHA


_SMOOSH_ALPHA = _stored_smoosh_alpha()


def get_smoosh_alpha():
    return _SMOOSH_ALPHA


def set_smoosh_alpha(v):
    global _SMOOSH_ALPHA
    _SMOOSH_ALPHA = float(v)
    prefs = toolbar_prefs.load_prefs()
    slot = prefs["per_widget"].get("smoosh")
    if not isinstance(slot, dict):
        slot = {}
        prefs["per_widget"]["smoosh"] = slot
    slot["alpha"] = _SMOOSH_ALPHA
    toolbar_prefs.save_prefs(prefs)


def smoosh_at_stored_strength(iterations=1):
    """Strip click + popover apply both land here: smoosh at the
    last-applied strength (hover panel tunes it, click re-applies)."""
    from maya_tools.skinning import smoosh_cmd
    return smoosh_cmd.smoosh(_SMOOSH_ALPHA, iterations)


def smoosh_click():
    """The Smoosh strip button's click action (Task 10 rider, decision B18):
    apply at the hover-popover's stored strength."""
    return smoosh_at_stored_strength()
