# maya_tools/rigging/fabricator/build_checks.py
"""Pre-build check registry — every 'this will break the build'
detection, plain-language description, and auto-fix in one place.

UI-free: the Build Issues dialog renders these; headless callers can
run and fix them directly. Adding a new pre-build guard = appending
one _check_* function to _CHECKS. The dialog picks it up unchanged.

Issue semantics:
  fixable    — has an auto-fix (the Fix button runs `fix()`).
  skippable  — the build may proceed leaving it (Build Anyway); the
               dialog maps a skipped 'missing_aimers' issue onto
               build_modules(force_missing_aimers=True).
  neither    — manual attention: the build will abort loudly until the
               user resolves it by hand (description says how).
"""
__author__ = "Adrian Melian"

import json
from dataclasses import dataclass, field

import maya.cmds as cmds

from maya_tools.rigging.fabricator import fs_app
from maya_tools.rigging.fabricator import nodes
from maya_tools.rigging.joint_orient import joint_orient_app


@dataclass
class BuildIssue:
    """One detected pre-build problem, ready for listing + fixing."""
    key: str                 # stable id: 'missing_aimers', ...
    title: str               # list entry: 'Missing 3 aimers'
    description: str         # plain-language: what, why, what Fix does
    fix_label: str = ''      # button verb: 'Create missing aimers'
    fixable: bool = False
    skippable: bool = False
    fix: callable = None     # () -> str summary; None when not fixable
    # 'error' (red — will break or corrupt the build) or 'warning'
    # (yellow — information, build proceeds). Adrian, 2026-07-05:
    # version drift is a warning, never an error.
    severity: str = 'error'


# ─────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────

def _check_missing_aimers() -> list:
    missing = fs_app.find_missing_aimers()
    if not missing:
        return []

    def _fix() -> str:
        # Rebuild-all strategy (Adrian, 2026-07-04): surgically
        # re-inserting one aimer among the stale fragments a hand-
        # delete leaves behind is name-collision roulette. Instead:
        # capture every live aimer's state, wipe the whole aimer layer
        # (delete_all_aimers is pattern-based — catches every stray),
        # then re-create for every skeleton joint. Live state wins;
        # the registry snapshot covers the deleted ones; natural-aim
        # detection covers the rest.
        live = {}
        for j in joint_orient_app.get_all_aimer_joints():
            s = joint_orient_app.get_aimer_state(j)
            if s:
                live[j] = s

        snap = {}
        reg = nodes.get_registry()
        if reg and cmds.attributeQuery('aimer_state_json', node=reg,
                                       exists=True):
            try:
                snap = json.loads(
                    cmds.getAttr(f'{reg}.aimer_state_json') or '{}')
            except (json.JSONDecodeError, ValueError):
                snap = {}

        root = nodes.get_registry_root_joint()
        if root and cmds.objExists(root):
            descendants = cmds.listRelatives(
                root, allDescendents=True, type='joint') or []
            joints = [root] + list(reversed(descendants))
        else:
            joints = sorted(set(list(live) + list(missing)))

        joint_orient_app.delete_all_aimers()

        count = 0
        for j in joints:
            if not cmds.objExists(j) or cmds.nodeType(j) != 'joint':
                continue
            joint_orient_app.create_aimer(j)
            s = live.get(j) or snap.get(j)
            if s:
                joint_orient_app.apply_aimer_state(
                    j, aim_target=s.get('aim_target', ''),
                    aim_offset=list(s.get('aim_offset',
                                          [0.0, 0.0, 0.0])))
            else:
                joint_orient_app.seed_aimer_from_detection(j)
            count += 1
        return (f'Rebuilt all {count} aimer(s) '
                f'({len(missing)} were missing).')

    lines = '\n'.join(f'  • {j}' for j in missing)
    return [BuildIssue(
        key='missing_aimers',
        title=f'Missing {len(missing)} aimer(s)',
        description=(
            f'These joints have no aimer — the aimer arrows were '
            f'deleted in the viewport:\n\n{lines}\n\n'
            f'Fix re-creates them (restoring their saved targets when '
            f'known, otherwise aiming at the child the joint already '
            f'points at).\n\n'
            f'Building anyway skips them in the aim pass — they keep '
            f'their current orientation.'),
        fix_label='Create missing aimers',
        fixable=True,
        skippable=True,
        fix=_fix,
    )]


def _check_overconnected() -> list:
    found = fs_app.find_overconnected_components()
    issues = []

    repairs = found.get('repairs') or []
    if repairs:
        # Auto-release safe over-connections in the background — no dialog
        # (Adrian 2026-07-11: "clean it up silently on every clean mirror").
        # find_overconnected_components only puts FULL-CONFIDENCE matches in
        # 'repairs' (every canonical joint name resolves to a live
        # connection), so releasing them is always safe — it strips the
        # mirror/duplicate .message echo and leaves each module its real
        # joints. Anything ambiguous lands in 'manual' below and still
        # prompts. A cmds.warning keeps it traceable in the Script Editor.
        n = fs_app.release_extra_joint_connections(repairs)
        from maya import cmds
        cmds.warning(
            f'[Fabricator] Auto-released {n} duplicated-joint connection(s) '
            f'from {len(repairs)} module(s) (mirror/duplicate echo) before '
            f'build.')

    manual = found.get('manual') or []
    if manual:
        lines = '\n'.join(
            f"  • {m['id']} ({m['type']}): {m['live_count']} joints "
            f"connected, expected {m['canonical']} — {m['reason']}"
            for m in manual)
        issues.append(BuildIssue(
            key='overconnected_manual',
            title=f'{len(manual)} module(s) need manual joint repair',
            description=(
                f'These modules have extra joint connections that '
                f'cannot be auto-repaired (an original joint was '
                f'deleted or renamed, so the right ones to keep are '
                f'ambiguous):\n\n{lines}\n\n'
                f'Fix them by hand, then build again. The build aborts '
                f'while these remain.'),
        ))
    return issues


def _check_orphaned_modules() -> list:
    orphans = fs_app.find_unbuildable_components()
    if not orphans:
        return []

    def _fix(orphans=orphans) -> str:
        n = fs_app.delete_components([o['node'] for o in orphans])
        return f'Removed {n} module(s) with missing joints.'

    lines = '\n'.join(
        f"  • {o['id']} ({o['type']}): missing "
        + ', '.join(o['missing_joints'])
        for o in orphans)
    return [BuildIssue(
        key='orphaned_modules',
        title=f'{len(orphans)} module(s) reference deleted joints',
        description=(
            f'These modules point at joints that no longer exist in '
            f'the scene (deleted in the viewport), which aborts the '
            f'build:\n\n{lines}\n\n'
            f'Fix removes the modules; the skeleton is untouched. '
            f'Re-add modules to the surviving joints if needed.'),
        fix_label='Remove orphaned modules',
        fixable=True,
        fix=_fix,
    )]


def _check_legacy_registry() -> list:
    """Pre-registry rig: no registry, or a registry whose root_joint
    resolves to nothing (Adrian's Dybbuk-era unbuild/build failures,
    2026-07-05). Fix adopts WITHOUT touching modules — the thing
    File > New Rig cannot do."""
    reg = nodes.get_registry()
    root = nodes.get_registry_root_joint() if reg else ''
    if reg and root and cmds.objExists(root):
        return []

    def _fix() -> str:
        return fs_app.adopt_registry()

    what = ('has no Fabricator registry'
            if not reg else "registry's root joint is missing")
    return [BuildIssue(
        key='legacy_registry',
        title='Pre-registry rig (no registry root joint)',
        description=(
            f'This rig {what} — it was built before the registry '
            f'era. The build proceeds, but Armature-age features '
            f'(orientation-contract validation, aimer snapshots, '
            f'version stamping) cannot run.\n\n'
            f'Fix adopts the rig: creates the registry, connects the '
            f"scene's root joint, persists the rig label and stamps "
            f'the current FS build version. Modules, skins and geo '
            f'are untouched. (Do NOT use File > New Rig for this — '
            f'it deletes the authored modules.)'),
        fix_label='Adopt registry (modules kept)',
        fixable=True,
        skippable=True,
        severity='warning',
        fix=_fix,
    )]


def _check_version_stamp() -> list:
    """FS build version drift (Adrian, 2026-07-05: yellow, never red —
    'This rig was originally built with Fabricator version X, and you
    are now building with version Y'). A successful build re-stamps,
    so both cases self-resolve by building."""
    from maya_tools.rigging.fabricator.version import FABRICATOR_VERSION
    reg = nodes.get_registry()
    root = nodes.get_registry_root_joint() if reg else ''
    if not (reg and root and cmds.objExists(root)):
        return []  # the legacy_registry check owns that story
    stamped = nodes.get_registry_fabricator_version()
    if stamped == FABRICATOR_VERSION:
        return []
    if not stamped:
        def _fix_unstamped() -> str:
            nodes.set_registry_fabricator_version(FABRICATOR_VERSION)
            return f'Stamped rig at Fabricator {FABRICATOR_VERSION}.'

        return [BuildIssue(
            key='version_unstamped',
            title='Rig predates FS version stamping',
            description=(
                f'This rig was last built before Fabricator carried '
                f'a build version. Nothing is wrong — building now '
                f'stamps it with Fabricator {FABRICATOR_VERSION} and '
                f'this notice retires. Fix stamps it now without a '
                f'full rebuild.'),
            fix_label='Stamp version',
            fixable=True,
            skippable=True,
            severity='warning',
            fix=_fix_unstamped,
        )]
    # Version drift (built with an older FS, building with a newer one) is NOT
    # surfaced as a build-gate issue: a successful build re-stamps the rig, so
    # it self-resolves and must never interrupt the build (Adrian, 2026-07-08).
    # Build-relevant contract changes between versions live in fabricator/version.py.
    return []


def _check_misparented_ctrls() -> list:
    hits = fs_app.find_misparented_ctrls()
    if not hits:
        return []
    lines = '\n'.join(
        f"  • {h['node'].split('|')[-1]}: parented under {h['parent']}, "
        f"expected under {h['expected']}"
        for h in hits)
    return [BuildIssue(
        key='misparented_ctrls',
        title=f"{len(hits)} control(s) parented outside the rig",
        description=(
            'These Fabricator controls live outside their expected '
            'hierarchy (often accidentally parented to the world). '
            'Builds may fail downstream or silently misbehave. Re-parent '
            'them back by hand or undo the change that moved them:\n'
            + lines),
        fixable=False,
        skippable=True,
    )]


def _check_renamed_component_joints() -> list:
    hits = fs_app.find_renamed_component_joints()
    if not hits:
        return []
    lines = '\n'.join(
        f"  • {h['component']}: '{h['recorded']}' is now '{h['live']}'"
        for h in hits)
    return [BuildIssue(
        key='renamed_component_joints',
        title=f"{len(hits)} joint(s) renamed since the component recorded them",
        description=(
            'These joints were renamed after their component captured '
            'them. Connections keep working, but exports, bindings, and '
            'rebuilds that rely on recorded names may break. Rename them '
            'back, or rebuild the component to re-record:\n' + lines),
        fixable=False,
        skippable=True,
        severity='warning',
    )]


# IK/FK visibility switching drives the ctrl's SHAPE nodes (so the
# transform's channels stay clean for animators). Maps the condition-node
# name suffix each limb component creates to the fab_role values it drives.
_VIS_COND_ROLES = {
    '_fk_vis_cond': ('fk_ctrl',),
    '_ik_vis_cond': ('ik_end_ctrl', 'pv_ctrl'),
}


def _owned_ctrls_by_role(component_node: str, roles: tuple) -> list:
    """Every ctrl tagged with one of `roles` and owned by component_node.

    Deliberately not fs_app._find_ctrl_by_role: that returns a single ctrl
    per role, and the whole point here is the three FK ctrls that share
    fab_role='fk_ctrl'. No cmds.ls glob — it is namespace-restricted and
    returns nothing on referenced rigs in anim scenes.
    """
    out = []
    for ctrl in cmds.ls(type='transform') or []:
        if not cmds.attributeQuery('fab_role', node=ctrl, exists=True):
            continue
        if cmds.getAttr(f'{ctrl}.fab_role') not in roles:
            continue
        if not cmds.attributeQuery('fab_owner', node=ctrl, exists=True):
            continue
        owners = cmds.listConnections(f'{ctrl}.fab_owner',
                                      source=True, destination=False) or []
        if component_node in owners:
            out.append(ctrl)
    return out


def _find_severed_ctrl_vis() -> list:
    """Ctrl shapes that should be driven by their limb's IK/FK vis
    condition and are not. Returns [(shape, source_plug), ...].

    Scoped per component via its own bind_blend_nodes multi (where
    SimpleIK tracks the condition nodes), so it never guesses at names and
    never crosses limbs.
    """
    from maya_tools.utils.maya import network_nodes as nn

    severed = []
    for cnode in nodes.get_all_component_nodes():
        for node in nn.get_message_targets(cnode, 'bind_blend_nodes') or []:
            if not cmds.objExists(node) or cmds.nodeType(node) != 'condition':
                continue
            suffix = next((s for s in _VIS_COND_ROLES if node.endswith(s)), '')
            if not suffix:
                continue
            src = f'{node}.outColorR'
            for ctrl in _owned_ctrls_by_role(cnode, _VIS_COND_ROLES[suffix]):
                for shp in cmds.listRelatives(ctrl, shapes=True,
                                              type='nurbsCurve',
                                              fullPath=True) or []:
                    conns = cmds.listConnections(f'{shp}.visibility',
                                                 source=True, destination=False,
                                                 plugs=True) or []
                    if not conns or conns[0] != src:
                        severed.append((shp, src))
    return severed


def _check_ctrl_vis_severed() -> list:
    """A rig whose IK/FK switch no longer hides the inactive ctrls.

    Cause in the wild: replacing a ctrl's curve shape deletes the shape
    nodes the switch was wired to. Fixed in curve_o_matic 2026-07-28, but
    scenes broken by an earlier build stay broken until something rewires
    them — a normal build does (connectAttr force=True), which is exactly
    why this is a warning and not a gate. The value is telling the user
    WHY their switch looked broken, and offering the repair without
    making them rebuild.
    """
    severed = _find_severed_ctrl_vis()
    if not severed:
        return []

    ctrls = sorted({s.split('|')[-2] for s, _ in severed
                    if len(s.split('|')) > 1})
    lines = '\n'.join(f'  • {c}' for c in ctrls[:12])
    if len(ctrls) > 12:
        lines += f'\n  • ...and {len(ctrls) - 12} more'

    def _fix() -> str:
        n = 0
        for shp, src in _find_severed_ctrl_vis():
            if cmds.objExists(shp) and cmds.objExists(src.split('.')[0]):
                cmds.connectAttr(src, f'{shp}.visibility', force=True)
                n += 1
        return f'Reconnected {n} control shape visibility connection(s).'

    return [BuildIssue(
        key='ctrl_vis_severed',
        title=f"{len(ctrls)} control(s) no longer follow the IK/FK switch",
        description=(
            "These controls' shapes have lost the visibility connection "
            'from their limb\'s IK/FK switch, so they stay drawn in the '
            'mode that should hide them. This happens when a control\'s '
            'curve shape is replaced by a tool version older than '
            '2026-07-28: swapping the shape deleted the nodes the switch '
            'was wired to. Building the rig repairs it, or press Fix to '
            'reconnect them now without a rebuild:\n' + lines),
        fix_label='Reconnect visibility',
        fixable=True,
        skippable=True,
        fix=_fix,
        severity='warning',
    )]


_CHECKS = (
    _check_overconnected,
    _check_orphaned_modules,
    _check_missing_aimers,
    _check_legacy_registry,
    _check_version_stamp,
    _check_misparented_ctrls,
    _check_renamed_component_joints,
    _check_ctrl_vis_severed,
)


def run_prebuild_checks() -> list:
    """Run every registered check. Returns a list of BuildIssue
    (empty = clean, build straight away)."""
    issues = []
    for check in _CHECKS:
        try:
            issues.extend(check())
        except Exception as exc:
            cmds.warning(f'build_checks: {check.__name__} failed — {exc}')
    return issues
