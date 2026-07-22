# Python/maya_tools/rigging/fabricator/ui/animation_menu.py
"""KS animation marking menu — persistent popupMenu on viewPanes.

Registered when FSWindow opens (or any other KS tool that calls
register_menu). Triggered by Ctrl+Alt+RMB anywhere in a Maya viewport.
The postMenuCommand callback rebuilds menu items each time based on
current selection: walks the selected ctrl's component contract,
filters actions by ctrl_role, dynamically synthesizes space-switch
entries from contract.space_consumers.

Architecture matches v1 anim_utils.py pattern (cmds.popupMenu on
viewPanes with ctl=1, alt=1, button=3, mm=1, pmc=...).
"""
__author__ = "Adrian Melian"

import importlib

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes as ks_nodes
from maya_tools.rigging.fabricator.modules import get_component_class


_MENU_NAME = 'ks_animation_marking_menu'

# Armature edit-stage suffixes — armature.py's naming contract, spelled
# out locally the same way armature_mirror.py / canvas_panel.py already
# do for their own resolution. This menu is registered as the ANIMATION
# marking menu, but it is really "context menu for whatever KS thing
# you clicked": armature ctrls carry no fab_role/fab_owner tags (they
# are per-JOINT placement objects, not component anim ctrls), so they
# branch here BEFORE the role gate instead of dying on it.
_AMT_CTRL_SUFFIX = '_amt_CTL'
_AMT_SOLO_SUFFIX = '_amtSolo'


def register_menu() -> str:
    """Create (or recreate) the persistent KS animation marking menu.

    Idempotent: deletes any existing menu of the same name first. The menu
    binds Ctrl+Alt+RMB on viewPanes; postMenuCommand rebuilds items
    dynamically based on the currently-selected ctrl.

    Returns the popupMenu node name.
    """
    if cmds.popupMenu(_MENU_NAME, exists=True):
        cmds.deleteUI(_MENU_NAME, menu=True)
    cmds.popupMenu(
        _MENU_NAME,
        parent='viewPanes',
        button=3,
        ctrlModifier=True,
        altModifier=True,
        markingMenu=True,
        postMenuCommand=_build_menu_items,
        allowOptionBoxes=True,
    )
    return _MENU_NAME


def _build_menu_items(*_args) -> None:
    """postMenuCommand callback. Maya passes (menu_name, parent_name);
    we ignore both and reference the module-level _MENU_NAME so we don't
    rely on Maya's signature stability across versions.
    """
    cmds.popupMenu(_MENU_NAME, edit=True, deleteAllItems=True)

    sel = cmds.ls(sl=True, type='transform') or []
    if not sel:
        cmds.menuItem(parent=_MENU_NAME, label='(no selection)', enable=False)
        return
    ctrl = sel[0]

    # Armature edit-stage branch (Kris request, 2026-07-21): solo-swap
    # entries for armature ctrls / solo handles, which have no role tag.
    kind, jnt = _armature_context(ctrl)
    if kind:
        _build_armature_items(kind, jnt)
        return

    # Belt-and-braces: fab_role preferred, ksfab_role fallback for a rig
    # not yet rebuilt/migrated past the 2026-07 rename — remove after v1.
    role_attr = ks_nodes.resolve_ctrl_tag_attr(ctrl, 'fab_role')
    if role_attr is None:
        cmds.menuItem(parent=_MENU_NAME,
                      label='(selected node is not a KS ctrl)', enable=False)
        return
    role = cmds.getAttr(f'{ctrl}.{role_attr}')

    component_node = _find_owning_component(ctrl)
    if not component_node:
        return
    component_id = ks_nodes.get_component_id(component_node)
    component_type = ks_nodes.get_component_type(component_node)
    try:
        cls = get_component_class(component_type)
    except KeyError:
        return
    contract = cls.CONTRACT

    actions = list(_common_actions(role))
    actions += [a for a in contract.actions if a.ctrl_role == role]
    actions += list(_synthesize_space_actions(contract, role, component_id, ctrl))
    if not actions:
        cmds.menuItem(parent=_MENU_NAME,
                      label=f'(no actions for role: {role})', enable=False)
        return

    by_section = {}
    for a in actions:
        by_section.setdefault(a.section, []).append(a)

    first = True
    for section_name in sorted(by_section.keys()):
        if section_name:
            cmds.menuItem(parent=_MENU_NAME, divider=True,
                          dividerLabel=section_name)
        first = False
        for a in by_section[section_name]:
            cmds.menuItem(
                parent=_MENU_NAME, label=a.label,
                command=lambda *_, action=a, cid=component_id, c=ctrl:
                _dispatch(action, cid, c),
            )


def _armature_context(node: str) -> tuple:
    """('ctrl'|'solo'|'', joint) — is `node` an Armature edit-stage
    object, and for which joint?

    Name-suffix resolution against a confirmed live joint, the same
    contract every other consumer of armature.py's naming uses
    (armature_mirror._side_of_selection, canvas_panel's viewport sync).
    A node that merely LOOKS suffixed but has no matching joint returns
    ('', '') and falls through to the normal role-tag path — never a
    guess."""
    short = node.split('|')[-1]
    for kind, suffix in (('ctrl', _AMT_CTRL_SUFFIX),
                         ('solo', _AMT_SOLO_SUFFIX)):
        if short.endswith(suffix):
            jnt = short[:-len(suffix)]
            if (cmds.objExists(jnt)
                    and cmds.nodeType(jnt) == 'joint'):
                return kind, jnt
    return '', ''


def _build_armature_items(kind: str, jnt: str) -> None:
    """Menu items for the Armature edit stage: the solo-nudge swap.

    On the main ctrl: 'Nudge This Joint' hides the ctrl's shapes and
    reveals the solo cage — one clickable thing where the joint is,
    moving ONLY that joint. On the cage: 'Back to Ctrl' restores, and
    'Zero This Nudge' snaps the joint back onto its ctrl. Handlers are
    armature.py app functions; this menu stays a thin caller."""
    from maya_tools.rigging.fabricator import armature

    cmds.menuItem(parent=_MENU_NAME, divider=True, dividerLabel='Armature')
    if kind == 'ctrl':
        if armature.solo_handle_for_joint(jnt):
            cmds.menuItem(
                parent=_MENU_NAME, label='Nudge This Joint',
                annotation='Swap to the solo cage: move ONLY this '
                           'joint, children stay put.',
                command=lambda *_, j=jnt: _run_armature_action(
                    'Nudge This Joint', armature.enter_solo, j),
            )
        else:
            cmds.menuItem(
                parent=_MENU_NAME, enable=False,
                label='(no solo handle — rebuild the Armature)')
    else:  # kind == 'solo'
        cmds.menuItem(
            parent=_MENU_NAME, label='Back to Ctrl',
            annotation='Swap back to the main ctrl. The nudge keeps.',
            command=lambda *_, j=jnt: _run_armature_action(
                'Back to Ctrl', armature.exit_solo, j),
        )
        cmds.menuItem(
            parent=_MENU_NAME, label='Zero This Nudge',
            annotation='Snap this joint back onto its ctrl. Children '
                       'never move either way.',
            command=lambda *_, j=jnt: _run_armature_action(
                'Zero This Nudge', armature.reset_solo_handles, [j]),
        )


def _run_armature_action(label: str, fn, arg) -> None:
    """Same failure contract as _dispatch: warn + traceback, never a
    silent swallow, never an unhandled exception out of a menu item."""
    try:
        fn(arg)
    except Exception as e:
        cmds.warning(f'{label} failed: {e}')
        import traceback
        traceback.print_exc()


def _find_owning_component(ctrl: str) -> str:
    # Belt-and-braces: fab_owner preferred, ksfab_owner fallback for a
    # rig not yet rebuilt/migrated past the 2026-07 rename — remove
    # after v1.
    owner_attr = ks_nodes.resolve_ctrl_tag_attr(ctrl, 'fab_owner')
    if owner_attr is None:
        return ''
    owner = cmds.listConnections(f'{ctrl}.{owner_attr}',
                                 source=True, destination=False) or []
    return owner[0] if owner else ''


def _common_actions(role: str):
    """Universal entries that apply to every KS ctrl regardless of
    component type — Zero All / Zero Translates / Zero Rotations / Zero
    Scale. Slotted under a 'Common' section so they're consistently the
    first thing animators see in the marking menu. Every handler skips
    locked + driven plugs, so partially-locked ctrls (PV with R+S
    locked, master switch with all 9 locked, …) stay safe."""
    from maya_tools.rigging.fabricator.modules.component import Action
    return [
        Action(
            label='Zero All',
            ctrl_role=role,
            handler='maya_tools.rigging.fabricator.actions.common.zero_all',
            section='Common',
        ),
        Action(
            label='Zero Translates',
            ctrl_role=role,
            handler='maya_tools.rigging.fabricator.actions.common.zero_translates',
            section='Common',
        ),
        Action(
            label='Zero Rotations',
            ctrl_role=role,
            handler='maya_tools.rigging.fabricator.actions.common.zero_rotations',
            section='Common',
        ),
        Action(
            label='Zero Scale',
            ctrl_role=role,
            handler='maya_tools.rigging.fabricator.actions.common.zero_scale',
            section='Common',
        ),
    ]


def _synthesize_space_actions(contract, role: str, component_id: str, ctrl: str):
    """Generate Switch to space: <name> (and ' (match)') menu entries for
    every space enum on this ctrl.

    Two sources:
      1. Contract space_consumers whose ctrl_role matches — the declared
         path (AdvancedFK, SimpleIK).
      2. Fallback: a live 'space' enum on the ctrl that no consumer claimed.
         FKAim's linked 'eyes' ctrl is wired dynamically in
         _apply_post_build_fk_aim_links (providers = world/root/parents),
         so it has a 'space' enum but no static SpaceConsumer. Without this
         fallback the marking menu would be blind to it.

    Reads the LIVE enum from ctrl.<attr_name> for both menu labels and
    handler args. The enum's display labels were set by build_space_switch_dg
    via the fab_display_name lookup, so the marking menu shows the same
    animator-friendly names as the channel box. Handler args also use these
    labels (switch_with_match looks up new_space against live enum names —
    internal '<id>.anchor_out' style strings would silently fail there).
    """
    out = []
    handled_attrs = set()
    for consumer in contract.space_consumers:
        if consumer.ctrl_role != role:
            continue
        if not cmds.attributeQuery(consumer.attr_name, node=ctrl, exists=True):
            continue
        handled_attrs.add(consumer.attr_name)
        out += _space_actions_for_attr(ctrl, role, consumer.attr_name)

    # Fallback for dynamically-wired space switches (e.g. FKAim 'eyes' ctrl).
    if ('space' not in handled_attrs
            and cmds.attributeQuery('space', node=ctrl, exists=True)):
        out += _space_actions_for_attr(ctrl, role, 'space')
    return out


def _space_actions_for_attr(ctrl: str, role: str, attr_name: str):
    """Build the Match/Switch action pair for each label in ctrl.<attr_name>'s
    live enum. Returns [] if the attr isn't an enum (listEnum yields nothing).

    Iterates the live enum in full — it includes the contract defaults PLUS
    any user-added joints from spaces_<ctrl_role>, in order. Capping would
    hide user additions from the marking menu.
    """
    from maya_tools.rigging.fabricator.modules.component import Action
    enum_str = cmds.attributeQuery(attr_name, node=ctrl, listEnum=True)
    if not enum_str:
        return []
    out = []
    for label in enum_str[0].split(':'):
        out.append(Action(
            label=f'Match to Space: {label}',
            ctrl_role=role,
            handler=f'maya_tools.rigging.fabricator.actions.space.switch_with_match'
                    f'#attr_name={attr_name}&new_space={label}',
            section='Spaces (match)',
        ))
        out.append(Action(
            label=f'Switch to space: {label}',
            ctrl_role=role,
            handler=f'maya_tools.rigging.fabricator.actions.space.switch_no_match'
                    f'#attr_name={attr_name}&new_space={label}',
            section='Spaces',
        ))
    return out


def _dispatch(action, component_id: str, ctrl: str):
    """Resolve handler dotted-path; call with (component_id, ctrl) plus
    any kwargs encoded after '#' in the handler string."""
    handler_str = action.handler
    extra_kwargs = {}
    if '#' in handler_str:
        path_part, query = handler_str.split('#', 1)
        for pair in query.split('&'):
            if '=' in pair:
                k, v = pair.split('=', 1)
                extra_kwargs[k] = v
        handler_str = path_part

    module_path, _, fn_name = handler_str.rpartition('.')
    try:
        mod = importlib.import_module(module_path)
        handler = getattr(mod, fn_name)
        handler(component_id, ctrl, **extra_kwargs)
    except Exception as e:
        cmds.warning(f'Action {action.label!r} failed: {e}')
        import traceback
        traceback.print_exc()
