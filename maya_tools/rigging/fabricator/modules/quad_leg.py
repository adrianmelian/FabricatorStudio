# maya_tools/rigging/fabricator/modules/quad_leg.py
"""QuadLeg component — Schleifer-style animator-friendly quadruped leg.

5-joint chain: upper_leg, knee, ankle, toe, toe_end.
Spring solver on driver chain (4-joint) for natural curling, three RP
handles on target chain segment the bend, hock FK ctrl with toe-joint
pivot gives manual control on top of the spring solve.

Spec: docs/superpowers/specs/2026-06-17-quad-leg-design.md
"""
__author__ = "Adrian Melian"

import math

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes as ks_nodes
from maya_tools.rigging.fabricator.modules.component import (
    Component, Contract, Plug, OptionField, JointRole, SpaceConsumer, Action,
)
from maya_tools.rigging.fabricator.modules.simple_ik import (
    _position_pv_at_build,
)
from maya_tools.rigging.fabricator.modules.world import _apply_color
from maya_tools.utils.maya import network_nodes as nn




def _duplicate_chain_keep_rotation(bind_joints, suffix, parent):
    """Duplicate a chain of bind joints with `suffix`, KEEPING the bind
    rotation in the duplicate's `rotate` attr (jointOrient zeroed).

    Unlike SimpleIK's _create_chain_duplicate (which makeIdentity-bakes
    rotate into jointOrient for clean rotate=0 at rest), this preserves
    the bind bend in `rotate` so cmds.ikHandle's autoPreferredAngles=True
    captures non-zero preferred angles at handle creation. Without the
    bend hint, the spring solver and 3-joint RP solver can lock the
    chain dead-straight at rest, breaking the bind pose.

    Convention matches Adrian's Joint Aimer tool output: orientation in
    `rotate`, jointOrient zero. We zero the duplicate's jointOrient
    before matchTransform so the world-rotation match lands purely in
    rotate.
    """
    created = []
    parent_jnt = parent
    for bind in bind_joints:
        short = bind.split('|')[-1].split(':')[-1]
        new_name = f'{short}{suffix}'
        new_jnt = cmds.duplicate(bind, parentOnly=True, name=new_name)[0]
        for dest in cmds.listConnections(f'{new_jnt}.message',
                                          source=False, destination=True,
                                          plugs=True) or []:
            cmds.disconnectAttr(f'{new_jnt}.message', dest)
        cmds.parent(new_jnt, parent_jnt)
        cmds.setAttr(f'{new_jnt}.jointOrient', 0, 0, 0)
        cmds.matchTransform(new_jnt, bind,
                            position=True, rotation=True, scale=True)
        ks_nodes.hide_internal(new_jnt)
        created.append(new_jnt)
        parent_jnt = new_jnt
    return created


QUAD_LEG_CONTRACT = Contract(
    type='QuadLeg',
    display_name='IK Quad',
    description=(
        'Schleifer-style animator-friendly quadruped leg. 5-joint chain '
        '(upper_leg, knee, ankle, toe, toe_end). Driver chain runs a 4-joint '
        'ikSpringSolver for natural curling, a target chain hosts three RP IK '
        'handles segmenting the bend, and a hock FK ctrl with a toe-joint '
        'pivot offset gives the animator manual hock control on top of the '
        'spring solve. Full IK/FK blend + switch + match actions.'
    ),
    min_joints=5, max_joints=5,
    parent_strategy='walk_up',
    inputs=(
        Plug(name='parent_in', kind='matrix', required=True,
             description='Parent transform space (e.g. cog or hip ctrl).'),
    ),
    outputs=(
        Plug(name='start_out', kind='matrix', space_target=True,
             description='BIND upper_leg joint world matrix.'),
        Plug(name='mid_out', kind='matrix', space_target=True,
             description='BIND knee joint world matrix.'),
        Plug(name='ankle_out', kind='matrix', space_target=True,
             description='BIND ankle joint world matrix.'),
        Plug(name='toe_out', kind='matrix', space_target=True,
             description='BIND toe joint world matrix.'),
        Plug(name='end_out', kind='matrix', space_target=True,
             description='BIND toe_end joint world matrix.'),
        Plug(name='foot_ctrl_out', kind='matrix', space_target=True,
             description='Foot (IK end) ctrl world matrix.'),
        Plug(name='anchor_out', kind='matrix', space_target=True,
             description=('Cycle-free anchor for internal consumers (PV + foot '
                          'ctrls). Follows the resolved parent ctrl without '
                          'dependence on the IK solve.')),
    ),
    options_schema={
        'ctrl_shape':        OptionField(type='shape_enum', default='capsule',
                                          description='FK ctrl shape (upper_leg, knee, ankle).'),
        'toe_fk_shape':      OptionField(type='shape_enum', default='capsule',
                                          description='Toe FK ctrl shape.'),
        'ik_ctrl_shape':     OptionField(type='shape_enum', default='foot',
                                          description='Foot ctrl shape.'),
        'pv_shape':          OptionField(type='shape_enum', default='diamond',
                                          description='PV ctrl shape.'),
        'hock_ctrl_shape':   OptionField(type='shape_enum', default='sphere',
                                          description='Hock FK ctrl shape.'),
        'switch_ctrl_shape': OptionField(type='shape_enum', default='cog',
                                          description='IK/FK switch ctrl shape.'),
        'ctrl_color':        OptionField(type='color_enum', default='rust',
                                          description='Ctrl color.'),
        'stretchy':          OptionField(type='bool', default=False,
                                          description=('Classic stretchy IK on the spring chain '
                                                       '(distanceBetween upper_leg to spring '
                                                       'handle vs rest length, clamped at 1.0).')),
    },
    side_supported=True,
    color='#D9540D',  # rust — IK family, deepest
    joint_roles=(
        JointRole('upper_leg', 'Upper leg (hip / shoulder)'),
        JointRole('knee',      'Knee',                 descendant_of='upper_leg'),
        JointRole('ankle',     'Ankle',                descendant_of='knee'),
        JointRole('toe',       'Toe',                  descendant_of='ankle'),
        JointRole('toe_end',   'Toe end (length tip)', descendant_of='toe'),
    ),
    # Space switching deferred. The 'root' provider in SpaceConsumer
    # wires foot_ctrl.OPM and pv_ctrl.OPM through root_joint.worldMatrix.
    # In Adrian's blueprint, the root joint IS the bind upper_leg (no
    # joint above it). Bind upper is constrained to blend (Task 11), blend
    # to target (Task 10), target rotation depends on PV pole vector, PV
    # depends on its OPM which has root provider as input. The DG cycle
    # made foot_ctrl settle at wrong world position even at rest, and
    # caused flips when foot ctrl was moved. To re-enable spaces later:
    # remove 'root' from the providers entirely (use only 'world' and
    # 'parent'), OR make the bind drive constraints orient-only so the
    # bind chain root's WORLD doesn't update from solver rotation. For
    # now ship without spaces so the rig is functional.
    space_consumers=(),
    # CP2 follow-up: marking-menu actions on master_switch_ctrl. Plain
    # Switch (no matching) and Match to FK are implemented -- same
    # mechanical pattern as SimpleIK/IKLeg (actions/simple_ik.py),
    # generalized to QuadLeg's 4 FK ctrls. Match to IK is intentionally
    # NOT wired: QuadLeg's hock ctrl feeds hock_pivot_offset.rotate via a
    # live connectAttr layered on top of the IK solve, not a constraint
    # with an invertible bind target -- reproducing the current bind pose
    # on FK-to-IK match would need a genuine inverse-rotation solve, not
    # a mechanical port of SimpleIK's PV-projection match. See
    # actions/quad_leg.py's module docstring and WAVE1-REPORT.md's CP2-fix
    # section for the full reasoning. mirror_rules deferred separately
    # (Task 18, unrelated to this follow-up).
    actions=(
        Action('Match to FK',  'master_switch_ctrl',
               'maya_tools.rigging.fabricator.actions.quad_leg.switch_to_fk_match', section='Mode'),
        Action('Switch to IK', 'master_switch_ctrl',
               'maya_tools.rigging.fabricator.actions.quad_leg.switch_to_ik',       section='Mode'),
        Action('Switch to FK', 'master_switch_ctrl',
               'maya_tools.rigging.fabricator.actions.quad_leg.switch_to_fk',       section='Mode'),
    ),
    # mirror_rules wired in Task 18.
)


class QuadLegComponent(Component):
    """Schleifer quadruped leg with spring solver + hock FK ctrl."""

    CONTRACT = QUAD_LEG_CONTRACT

    @classmethod
    def can_apply(cls, joints, blueprint) -> tuple:
        """Validate the 5-joint quadruped leg chain.

        Selection order doesn't matter — joints are sorted by hierarchy
        depth first (mirrors the SimpleIK / IKLeg validators).
        """
        ok, reason = super().can_apply(joints, blueprint)
        if not ok:
            return False, reason
        if not joints or blueprint is None:
            return False, 'No joint(s) selected or no blueprint loaded.'

        from maya_tools.rigging.fabricator.fs_app import _sort_by_depth
        sorted_joints = _sort_by_depth(list(joints), blueprint)
        if len(sorted_joints) < 5:
            return False, (f'Selection did not produce a 5-joint chain after '
                           f'depth sort; got {len(sorted_joints)}.')

        parent_map = {j.name: j.parent for j in blueprint.skeleton_joints}
        for i in range(1, 5):
            if parent_map.get(sorted_joints[i]) != sorted_joints[i - 1]:
                return False, (
                    f'{sorted_joints[i]!r} is not a direct child of '
                    f'{sorted_joints[i - 1]!r}; select 5 joints that form '
                    f'a parent chain (upper_leg, knee, ankle, toe, toe_end).'
                )
        return True, ''

    @classmethod
    def build(cls, instance, context) -> None:
        """Build a quadruped leg rig from 5 bind joints.

        This task lays down the build scaffolding: plugin load, container
        groups, setup_grp, component-node resolution, _tag_ctrl closure.
        Tasks 3-15 fill in the actual chains, ctrls, IK handles, and DG.
        """
        bind_joints = list(instance.joints)
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

        # Defensive: can_apply should have caught any wrong-size selection,
        # but the contract is enforced at can_apply, not at build. Belt
        # and suspenders.
        if len(bind_joints) != 5 or not all(cmds.objExists(j) for j in bind_joints):
            raise RuntimeError(
                f"QuadLeg on {instance.id!r} needs 5 existing joints; "
                f"got {bind_joints}."
            )

        # Defensive depth-sort: regardless of how instance.joints was
        # ordered by the framework's resolver (cursor-first + depth-sort
        # of the rest can put the deepest joint at index 0 if the user
        # clicks the end joint first), reorder to canonical chain order
        # so the rest of build() can rely on
        # [upper_leg, knee, ankle, toe, toe_end].
        def _parent_chain_depth(jnt: str) -> int:
            d = 0
            cur = jnt
            seen = set()
            while True:
                p = cmds.listRelatives(cur, parent=True, type='joint') or []
                if not p:
                    return d
                if p[0] in seen:
                    return d  # cycle guard
                seen.add(p[0])
                cur = p[0]
                d += 1
                if d > 10000:
                    return d
        bind_joints.sort(key=_parent_chain_depth)
        upper, knee, ankle, toe, toe_end = bind_joints
        short_start = upper.split('|')[-1].split(':')[-1]

        # ikSpringSolver lives in an optional plugin. Load defensively so a
        # fresh Maya session that hasn't auto-loaded it still builds.
        # Loading the plugin defines the node type but does NOT instantiate
        # the singleton solver node (unlike ikRPsolver, which auto-creates).
        # Without a live ikSpringSolver node in the scene, cmds.ikHandle
        # raises "The ikSolver does not exist." Create one if absent.
        if not cmds.pluginInfo('ikSpringSolver', q=True, loaded=True):
            try:
                cmds.loadPlugin('ikSpringSolver', quiet=True)
            except Exception as e:
                raise RuntimeError(
                    f"QuadLeg on {instance.id!r}: failed to load "
                    f"ikSpringSolver plugin: {e}"
                )
        if not cmds.ls(type='ikSpringSolver'):
            cmds.createNode('ikSpringSolver', name='ikSpringSolver')

        # Resolve component network node for tagging + persistence. Mirrors
        # SimpleIK's resolution pattern (simple_ik.py:384-388).
        component_node = None
        for cn in ks_nodes.get_all_component_nodes():
            if ks_nodes.get_component_id(cn) == instance.id:
                component_node = cn
                break

        # Tag-ctrl closure -- captures component_node so call sites don't
        # repeat it. Mirrors SimpleIK's pattern (simple_ik.py:390-396).
        def _tag_ctrl(ctrl_name: str, role: str, joint_index: int = -1):
            """Thin wrapper over ks_nodes.tag_ctrl — captures the closure's
            component_node so the call sites don't repeat it.
            """
            ks_nodes.tag_ctrl(
                ctrl_name, role,
                component_node=component_node or '',
                joint_index=joint_index,
            )

        # Standard rig containers.
        controls_grp = ks_nodes.ensure_controls_grp()
        nulls_grp = ks_nodes.ensure_nulls_grp()

        # Per-component setup group (holds IK chain duplicates, IK handles,
        # internal nulls). Idempotent: if a prior partial build orphaned
        # one, delete and recreate.
        setup_grp_name = f'{short_start}_quad_setup_grp'
        if cmds.objExists(setup_grp_name):
            cmds.delete(setup_grp_name)
        setup_grp = cmds.createNode('transform',
                                     name=setup_grp_name, parent=nulls_grp)
        cmds.setAttr(f'{setup_grp}.inheritsTransform', False)
        cmds.setAttr(f'{setup_grp}.visibility', 0)

        # Stash for downstream tasks (Task 3+ reads these).
        instance._setup_grp = setup_grp
        instance._controls_grp = controls_grp
        instance._tag_ctrl = _tag_ctrl

        # Ctrl color is the COMPONENT FAMILY color (Adrian, 2026-07-12:
        # all IKs are orange shades; IK Quad takes the deepest, rust).
        # The old build-time side override (blue L / red R / yellow
        # center) is retired with side-based coloring itself — the
        # contract default is the single source, and an explicit
        # per-instance ctrl_color still wins because it simply IS the
        # option value read here.
        side_color = instance.options.get('ctrl_color') or 'rust'
        instance._side_color = side_color
        instance._component_node = component_node

        # ─── No driver chain / no spring solver ────────────────────────
        # Schleifer's recipe originally calls for ikSpringSolver to give
        # natural weighted curling across knee+ankle+toe. ikSpringSolver
        # in Maya 2025 has a load-bearing bug: cmds.ikHandle(sol=
        # 'ikSpringSolver') captures a degenerate (straight) rest pose
        # on reoriented chains regardless of joint state at creation
        # time. Confirmed via Adrian's diagnostic: preferredAngle
        # captured correctly (knee=-92, ankle=60) but solver still locked
        # chain straight. The ikRPsolver create + message swap workaround
        # (mGear pattern) also failed.
        #
        # Pragmatic fix: drop the spring chain entirely. The target
        # chain's 3 RP handles (built next) already segment bend per
        # standard quad rig recipe. We lose spring's weighted multi-
        # joint distribution but gain a working rig. Per-joint bend
        # distribution becomes hock-ctrl + PV controlled instead of
        # automatic.
        #
        # Legacy instance attrs preserved as None so downstream tasks
        # (stretchy, persistence) can skip the spring-specific paths.
        instance._driver_chain = []
        instance._spring_handle = None

        # ─── Target chain (RP IK handle host) ──────────────────────────
        # Separate 5-joint duplicate. Hosts the three RP handles that
        # segment the bend (upper→ankle, ankle→toe, toe→toe_end). Per the
        # spec's "IK chain vs blend chain composition" call: dedicated
        # target chain matches SimpleIK pattern, makes match handlers
        # cleaner than putting handles directly on blend chain. Tasks
        # 5-7 reparent the handles to their final hierarchy positions.
        target_chain = _duplicate_chain_keep_rotation(
            bind_joints, '_ik', instance._setup_grp,
        )

        # ─── Stamp preferredAngle from bind rotates ────────────────────
        # cmds.ikHandle's documented autoPreferredAngles capture either
        # doesn't fire or doesn't survive sticky='sticky' handles -
        # Adrian's diagnostic showed target chain preferredA=(0,0,0)
        # on every joint despite bind rotates being non-zero. Without
        # a bend hint, RP solver picks the straight-chain solution and
        # the chain settles at full extension. Stamp preferredAngle
        # explicitly from bind values BEFORE handle creation.
        for bind_jnt, tgt_jnt in zip(bind_joints, target_chain):
            bind_rot = cmds.getAttr(f'{bind_jnt}.rotate')[0]
            cmds.setAttr(f'{tgt_jnt}.preferredAngle',
                         *bind_rot, type='double3')

        # ─── Three RP IK handles ───────────────────────────────────────
        # sticky='sticky' on EVERY handle. Default sticky='off' makes the
        # handle's translate auto-update each evaluation to wherever the
        # end effector currently is, creating an FK-feedback loop: as
        # successive handles are created, their solvers iterate, the
        # chain moves slightly, end effectors move, handles follow, chain
        # moves more. sticky='sticky' breaks the feedback - handle
        # translate stays where the user/parent puts it.
        ankle_handle, _ = cmds.ikHandle(
            sj=target_chain[0],            # upper_leg_ik
            ee=target_chain[2],            # ankle_ik (3-joint span)
            sol='ikRPsolver',
            sticky='sticky',
            name=f'{short_start}_ankle_ikHandle',
        )
        hock_handle, _ = cmds.ikHandle(
            sj=target_chain[2],            # ankle_ik
            ee=target_chain[3],            # toe_ik (2-joint span)
            sol='ikRPsolver',
            sticky='sticky',
            name=f'{short_start}_hock_ikHandle',
        )
        toe_handle, _ = cmds.ikHandle(
            sj=target_chain[3],            # toe_ik
            ee=target_chain[4],            # toe_end_ik (2-joint span)
            sol='ikRPsolver',
            sticky='sticky',
            name=f'{short_start}_toe_ikHandle',
        )

        # Park all three under setup_grp, hidden. Reparented in Tasks 5-7.
        for h in (ankle_handle, hock_handle, toe_handle):
            cmds.parent(h, instance._setup_grp)
            cmds.setAttr(f'{h}.visibility', 0)

        # Re-stamp preferredAngle AFTER handle creation - ikHandle's
        # autoPreferredAngles, if it fires, would write current rotate
        # to preferredAngle, but the solver might have already changed
        # rotate to satisfy initial chain state. Re-stamp from bind
        # values guarantees the bend hint matches bind no matter what
        # the solver did first.
        for bind_jnt, tgt_jnt in zip(bind_joints, target_chain):
            bind_rot = cmds.getAttr(f'{bind_jnt}.rotate')[0]
            cmds.setAttr(f'{tgt_jnt}.preferredAngle',
                         *bind_rot, type='double3')

        instance._target_chain = target_chain
        instance._ankle_handle = ankle_handle
        instance._hock_handle = hock_handle
        instance._toe_handle = toe_handle

        # ─── Foot ctrl ─────────────────────────────────────────────────
        # Shape from ik_ctrl_shape option (default 'foot' per the
        # spec). Placed at toe_end world position (ground level for
        # typical quadruped guides). Rotation matched=False — foot
        # ctrls face world, not the local chain orient.
        opts = instance.options
        ik_ctrl_shape = opts.get('ik_ctrl_shape', 'foot')
        short_end = toe_end.split('|')[-1].split(':')[-1]

        # SimpleIK PV pattern: ctrl parented directly under controls_grp
        # (at world identity), xform'd to bind world, bind matrix
        # captured, local TRS zeroed, offsetParentMatrix set to bind.
        # Required for the post-build space-switch framework: it assumes
        # the ctrl's parent is at world identity and wires OPM to ctrl's
        # bind matrix at rest. With a non-identity offset wrapper, the
        # OPM math drifts the ctrl past its build position (Adrian's
        # diagnostic: foot_ctrl world ended up at [44.91, -6.25] instead
        # of toe_end [25.69, -9.51]).
        foot_ctrl = com.build_shape(ik_ctrl_shape,
                                     f'{short_end}_IK_ctrl',
                                     radius=cmds.getAttr(f'{ankle}.radius'))
        cmds.parent(foot_ctrl, instance._controls_grp)
        toe_end_pos = cmds.xform(toe_end, q=True, ws=True, t=True)
        cmds.xform(foot_ctrl, ws=True, t=toe_end_pos)
        foot_bind = cmds.xform(foot_ctrl, q=True, ws=True, matrix=True)
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{foot_ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{foot_ctrl}.{attr}', 1)
        cmds.setAttr(f'{foot_ctrl}.offsetParentMatrix', *foot_bind,
                     type='matrix')
        _apply_color(foot_ctrl, instance._side_color)
        instance._tag_ctrl(foot_ctrl, role='ik_end_ctrl', joint_index=4)

        # ─── Toe IK handle directly under foot_ctrl ───────────────────
        # Task 12 will insert a toe_roll_GRP between foot_ctrl and the
        # toe_handle so foot_roll attr can pivot it. For now, direct.
        # Defensive matchTransform after parent: pins the handle at the
        # bind end effector world even if any solver feedback iteration
        # drifted it during build.
        cmds.parent(instance._toe_handle, foot_ctrl)
        cmds.matchTransform(instance._toe_handle, instance._target_chain[4],
                            position=True)

        # ─── Hock RP handle child of foot_ctrl ────────────────────────
        # Driver chain dropped. hock_handle (ankle->toe RP) needs to
        # track foot_ctrl so toe_ik follows foot during animation.
        # matchTransform pins the handle at bind toe world.
        cmds.parent(instance._hock_handle, foot_ctrl)
        cmds.matchTransform(instance._hock_handle, instance._target_chain[3],
                            position=True)

        instance._foot_ctrl = foot_ctrl
        instance._foot_ctrl_offset = None  # SimpleIK pattern: no offset wrapper
        instance._foot_spring_offset = None  # Spring solver dropped

        # ─── Hock setup: invisible pivot offset + visible ctrl ────────
        # Adrian's Schleifer recipe: rotating a hock ctrl swings the upper-
        # to-ankle RP handle around the TOE joint pivot. The spring solver
        # above re-solves to keep the foot ctrl reachable, so the ctrl
        # gives the animator hock bend control without breaking the foot
        # placement.
        #
        # Split into two transforms:
        #   1. hock_pivot_offset (invisible, under foot_ctrl) holds the
        #      pivot at the toe joint world and the upper-to-ankle IK
        #      handle. Rotation driven by the visible ctrl.
        #   2. hock_ctrl_offset (visible, under controls_grp) parent-
        #      constrained to hock_pivot_offset so the ctrl follows the
        #      ankle's world position. Hock ctrl is its child.
        # Pivot offset under foot_ctrl: it follows the foot during
        # animation, and its rotatePivot offset (set in world to the
        # bind toe position) gives the local pivot-from-foot-to-toe
        # vector, so hock rotation pivots ankle handle around the
        # current toe position.
        hock_ctrl_shape = opts.get('hock_ctrl_shape', 'arrow_four_way')
        short_toe = toe.split('|')[-1].split(':')[-1]
        toe_pos = cmds.xform(toe, q=True, ws=True, t=True)
        ankle_pos = cmds.xform(ankle, q=True, ws=True, t=True)

        # Invisible pivot offset: child of foot_ctrl, world at ankle bind
        # position, rotatePivot at toe bind position. When animator
        # rotates it (via hock ctrl), the ankle_handle child rotates
        # around the toe world position.
        hock_pivot_offset = cmds.createNode(
            'transform',
            name=f'{short_toe}_hock_pivot',
            parent=foot_ctrl,
        )
        cmds.xform(hock_pivot_offset, ws=True, t=ankle_pos)
        cmds.xform(hock_pivot_offset, ws=True, piv=toe_pos)
        # Ankle handle hangs off the pivot offset - hock rotation pivots
        # it around the toe world position. matchTransform pins the
        # handle at bind ankle world.
        cmds.parent(instance._ankle_handle, hock_pivot_offset)
        cmds.matchTransform(instance._ankle_handle, instance._target_chain[2],
                            position=True)

        # Visible ctrl offset: under controls_grp, parent-constrained
        # to hock_pivot_offset so the ctrl follows the ankle's world.
        hock_ctrl_offset = cmds.createNode(
            'transform',
            name=f'{short_toe}_hock_FK_offset',
            parent=instance._controls_grp,
        )
        cmds.matchTransform(hock_ctrl_offset, hock_pivot_offset,
                            position=True, rotation=True)
        cmds.parentConstraint(hock_pivot_offset,
                              hock_ctrl_offset, mo=True)

        hock_ctrl = com.build_shape(hock_ctrl_shape,
                                     f'{short_toe}_hock_FK_ctrl',
                                     radius=cmds.getAttr(f'{ankle}.radius'))
        cmds.parent(hock_ctrl, hock_ctrl_offset)
        cmds.setAttr(f'{hock_ctrl}.translate', 0, 0, 0, type='double3')
        cmds.setAttr(f'{hock_ctrl}.rotate', 0, 0, 0, type='double3')

        # Connect visible ctrl rotations to the invisible pivot offset's
        # rotations - the ctrl drives the IK pivot indirectly.
        for axis in ('X', 'Y', 'Z'):
            cmds.connectAttr(
                f'{hock_ctrl}.rotate{axis}',
                f'{hock_pivot_offset}.rotate{axis}',
                force=True,
            )

        _apply_color(hock_ctrl, instance._side_color)
        instance._tag_ctrl(hock_ctrl, role='hock_ctrl', joint_index=2)

        instance._hock_ctrl = hock_ctrl
        instance._hock_ctrl_offset = hock_ctrl_offset
        instance._hock_pivot_offset = hock_pivot_offset
        # Keep the legacy attr name for downstream tasks that reference
        # the pivot offset (Task 15 persistence, Task 16 unbuild tracking).
        instance._hock_offset = hock_pivot_offset

        # ─── hock_handle already parented under foot_ctrl in Task 5 ───

        # ─── PV ctrl ──────────────────────────────────────────────────
        # Perpendicular projection from knee onto upper_leg->toe line. For
        # a forward-bending quad knee in rest pose, the projection naturally
        # points forward, so the PV ctrl lands IN FRONT of the knee
        # (opposite convention to biped arms, where PV goes behind the
        # elbow). _position_pv_at_build handles the projection + chain-
        # length scaling; falls back to (0, 0, -1) for fully-straight chains.
        pv_shape = opts.get('pv_shape', 'diamond')
        short_knee = knee.split('|')[-1].split(':')[-1]
        pv_distance = 0.5  # fraction of chain length

        pv_pos = _position_pv_at_build(upper, knee, toe, pv_distance)
        pv_ctrl = com.build_shape(pv_shape, f'{short_knee}_PV_ctrl',
                                  radius=cmds.getAttr(f'{knee}.radius'))
        cmds.parent(pv_ctrl, instance._controls_grp)
        cmds.xform(pv_ctrl, ws=True, t=pv_pos)

        # Capture bind matrix BEFORE zeroing local TRS so we can write it
        # to offsetParentMatrix — preserves world position while keeping
        # the ctrl's local channels at identity. Same idiom SimpleIK uses
        # (simple_ik.py:602-610).
        pv_bind = cmds.xform(pv_ctrl, q=True, ws=True, matrix=True)
        for attr in ('tx', 'ty', 'tz', 'rx', 'ry', 'rz'):
            cmds.setAttr(f'{pv_ctrl}.{attr}', 0)
        for attr in ('sx', 'sy', 'sz'):
            cmds.setAttr(f'{pv_ctrl}.{attr}', 1)
        cmds.setAttr(f'{pv_ctrl}.offsetParentMatrix', *pv_bind, type='matrix')

        # Pole vector constraint on the upper->ankle RP handle. With the
        # spring driver dropped, the ankle_handle is what bends knee_ik
        # to determine knee direction at solve time, so PV controls
        # knee bend the same way it would on a standard 3-joint leg.
        cmds.poleVectorConstraint(pv_ctrl, instance._ankle_handle)

        _apply_color(pv_ctrl, instance._side_color)
        instance._tag_ctrl(pv_ctrl, role='pv_ctrl', joint_index=1)
        instance._pv_ctrl = pv_ctrl
        # Auto-PV deferred along with space switching - the magic DG
        # ultimately fed into the same root-provider cycle. Re-add once
        # the rig's space framework can be made cycle-safe for QuadLeg.
        instance._auto_pv_nodes = []

        # ─── PV debug line ────────────────────────────────────────────
        # Greyed template line from knee bind joint to PV ctrl. Helps
        # animators locate the PV in the viewport. CV[0] cluster-driven
        # by knee, CV[1] by PV ctrl. Same pattern as SimpleIK
        # (simple_ik.py:962-1009). Visibility is gated by ik_fk_blend
        # downstream (wired in Task 10's visibility block, which already
        # connects ik_fk_blend to other IK ctrl shapes).
        knee_pos = cmds.xform(knee, q=True, ws=True, t=True)
        pv_pos_ws = cmds.xform(pv_ctrl, q=True, ws=True, t=True)
        pv_line = cmds.curve(
            name=f'{short_knee}_pv_line',
            degree=1, point=[knee_pos, pv_pos_ws],
        )
        cmds.parent(pv_line, instance._controls_grp)
        ks_nodes.hide_internal(pv_line)

        # CV[0] follows knee bind joint via cluster + parentConstraint.
        cmds.select(f'{pv_line}.cv[0]', replace=True)
        cluster_knee_node, cluster_knee_handle = cmds.cluster(
            name=f'{short_knee}_pv_line_cluster_knee')
        cmds.parent(cluster_knee_handle, instance._setup_grp)
        cmds.matchTransform(cluster_knee_handle, knee)
        cmds.parentConstraint(knee, cluster_knee_handle, mo=True)
        ks_nodes.hide_internal(cluster_knee_handle)

        # CV[1] follows PV ctrl.
        cmds.select(f'{pv_line}.cv[1]', replace=True)
        cluster_pv_node, cluster_pv_handle = cmds.cluster(
            name=f'{short_knee}_pv_line_cluster_pv')
        cmds.parent(cluster_pv_handle, instance._setup_grp)
        cmds.matchTransform(cluster_pv_handle, pv_ctrl)
        cmds.parentConstraint(pv_ctrl, cluster_pv_handle, mo=True)
        ks_nodes.hide_internal(cluster_pv_handle)
        cmds.select(clear=True)

        # Template the line shape (greyed + non-selectable).
        for s in cmds.listRelatives(pv_line, shapes=True,
                                     fullPath=True) or []:
            cmds.setAttr(f'{s}.overrideEnabled', 1)
            cmds.setAttr(f'{s}.overrideDisplayType', 1)  # 1 = template

        instance._pv_line = pv_line
        instance._pv_line_clusters = [cluster_knee_node, cluster_pv_node]

        # ─── FK chain (4 joints, skip toe_end) ─────────────────────────
        # The FK chain is the parallel-to-IK chain that the bind chain
        # constraint-blends with. toe_end is excluded - it's a length
        # endpoint, no FK ctrl is useful there. _fk_chain holds the FK
        # joints; instance._fk_ctrls holds the 4 ctrls in chain order.
        fk_chain = _duplicate_chain_keep_rotation(
            bind_joints[:4],  # upper, knee, ankle, toe
            '_fk',
            instance._setup_grp,
        )

        # FK ctrls: one per FK joint, nested chain (each ctrl's offset
        # parented under the previous ctrl so rotating the parent drives
        # the chain). Toe ctrl uses toe_fk_shape; others use ctrl_shape.
        ctrl_shape = opts.get('ctrl_shape', 'capsule')
        toe_fk_shape = opts.get('toe_fk_shape', 'capsule')

        fk_ctrls = []
        parent_ctrl = None
        for i, fk_jnt in enumerate(fk_chain):
            short_jnt = fk_jnt.split('|')[-1].split(':')[-1]
            shape = toe_fk_shape if i == 3 else ctrl_shape
            fk_ctrl = com.build_shape(shape, f'{short_jnt}_FK_ctrl',
                                      radius=cmds.getAttr(f'{fk_jnt}.radius'))
            fk_ctrl_offset = cmds.createNode(
                'transform',
                name=f'{short_jnt}_FK_offset',
                parent=parent_ctrl or instance._controls_grp,
            )
            cmds.parent(fk_ctrl, fk_ctrl_offset)
            cmds.matchTransform(fk_ctrl_offset, fk_jnt,
                                position=True, rotation=True)
            cmds.setAttr(f'{fk_ctrl}.translate', 0, 0, 0, type='double3')
            cmds.setAttr(f'{fk_ctrl}.rotate', 0, 0, 0, type='double3')
            cmds.parentConstraint(fk_ctrl, fk_jnt, mo=False)
            _apply_color(fk_ctrl, instance._side_color)
            instance._tag_ctrl(fk_ctrl, role='fk_ctrl', joint_index=i)
            fk_ctrls.append(fk_ctrl)
            parent_ctrl = fk_ctrl

        instance._fk_chain = fk_chain
        instance._fk_ctrls = fk_ctrls

        # ─── BLEND chain (4 joints, matches FK chain depth) ─────────────
        # The blend chain is the chain that drives bind (Task 11 wires
        # that). Per joint, parent+scale constrained from both the IK
        # target chain and the FK chain, with weights driven by
        # ik_fk_blend. toe_end has no FK/blend counterpart - it follows
        # the target chain's toe_end directly via Task 11 constraint.
        blend_chain = _duplicate_chain_keep_rotation(
            bind_joints[:4],
            '_blend',
            instance._setup_grp,
        )

        # ─── IK/FK switch ctrl ─────────────────────────────────────────
        # Cog shape, offset to the side of the ankle (side-aware) and
        # following the BLEND chain's ankle joint, matching the
        # SimpleIK/IKLeg sibling convention exactly.
        #
        # CP2 fix (post-review): the switch ctrl previously sat exactly on
        # top of the ankle joint with no offset and no follow constraint at
        # all -- unselectable in a crowded viewport and dead once placed.
        # Two changes:
        #   1. Side-aware world-X offset. Project convention (side_tokens.
        #      MIRROR_AXIS = 'x', MIRROR_PLANE = 'YZ') mirrors left/right by
        #      negating world X, so a symmetric "outward" placement needs
        #      the offset sign flipped between lf/rt (md/center gets a
        #      stable default, sign doesn't matter for an on-centerline leg).
        #   2. parentConstraint to the blend chain's ankle joint (mo=True),
        #      matching SimpleIK/IKLeg (simple_ik.py:636-641: parentConstraint
        #      to wrist_blend). An initial pass followed foot_ctrl (the IK
        #      ctrl) directly per an early reading of Adrian's direction;
        #      at the CP2 re-check he confirmed the blend-chain convention
        #      was what he meant. Following the blend chain means the
        #      switch ctrl tracks the leg in BOTH IK and FK modes (the
        #      blend chain resolves through both), where foot_ctrl alone
        #      would only move in IK mode.
        switch_shape = opts.get('switch_ctrl_shape', 'cog')
        short_end = toe_end.split('|')[-1].split(':')[-1]
        switch_ctrl = com.build_shape(switch_shape,
                                       f'{short_end}_IKFK_ctrl',
                                       radius=cmds.getAttr(f'{ankle}.radius'))
        switch_offset = cmds.createNode(
            'transform',
            name=f'{short_end}_IKFK_offset',
            parent=instance._controls_grp,
        )
        cmds.parent(switch_ctrl, switch_offset)

        side_sign = -1.0 if instance.side == 'rt' else 1.0
        ankle_pos = cmds.xform(ankle, q=True, ws=True, t=True)
        # Offset magnitude scaled off the upper->knee segment so it reads
        # sensibly across differently-scaled quad rigs (Manny-scale vs a
        # much bigger creature), same spirit as SimpleIK's chain_len*0.15
        # selectability offset (simple_ik.py:623-635).
        upper_pos = cmds.xform(upper, q=True, ws=True, t=True)
        seg_len = math.sqrt(sum((ankle_pos[i] - upper_pos[i]) ** 2 for i in range(3)))
        offset_x = side_sign * seg_len * 0.3
        cmds.xform(switch_offset, ws=True,
                   t=(ankle_pos[0] + offset_x, ankle_pos[1], ankle_pos[2]))

        # Follow the BLEND chain's ankle joint -- Adrian confirmed at the
        # CP2 re-check this is what he meant ("that's totally what I
        # meant"), matching the SimpleIK/IKLeg sibling convention exactly
        # (simple_ik.py:636-641: parentConstraint to wrist_blend, not the
        # IK ctrl). The blend chain resolves through both IK and FK, so
        # unlike a follow on foot_ctrl (which doesn't move under FK), the
        # switch ctrl now tracks the leg in either mode. mo=True captures
        # the side-step above as the maintained offset.
        ankle_blend = blend_chain[2]
        cmds.parentConstraint(ankle_blend, switch_offset, mo=True)
        cmds.scaleConstraint(ankle_blend, switch_offset, mo=True)

        cmds.setAttr(f'{switch_ctrl}.translate', 0, 0, 0, type='double3')
        cmds.setAttr(f'{switch_ctrl}.rotate', 0, 0, 0, type='double3')

        if not cmds.attributeQuery('ik_fk_blend', node=switch_ctrl,
                                    exists=True):
            cmds.addAttr(switch_ctrl, ln='ik_fk_blend',
                          at='float', min=0.0, max=1.0, dv=1.0, k=True)
        _apply_color(switch_ctrl, instance._side_color)
        instance._tag_ctrl(switch_ctrl, role='master_switch_ctrl')

        # Track: the follow constraints live on switch_offset, which is
        # DAG-parented under controls_grp -- cascades free with rig_grp on
        # unbuild. No explicit tracking needed for them (unlike the bind-
        # joint constraints, which live outside rig_grp).

        # ─── Reverse for FK weight (FK weight = 1 - blend) ─────────────
        rev_node = cmds.createNode('reverse',
                                    name=f'{short_start}_ikfk_rev')
        cmds.connectAttr(f'{switch_ctrl}.ik_fk_blend',
                          f'{rev_node}.inputX')

        # Track: DG node with no DAG parent -- rig_grp cascade delete on
        # unbuild won't catch it. Mirrors SimpleIK's 'bind_blend_nodes'
        # bucket pattern (simple_ik.py:918-920).
        if component_node:
            nn.connect_message_multi(rev_node, component_node, 'bind_blend_nodes')

        # ─── Per-joint blend constraints ───────────────────────────────
        # Gotcha 4: parentConstraint MUST use interpType=2 (Shortest /
        # quaternion blend) to avoid wrap-around twist on Euler-different
        # IK/FK pose representations.
        for blend_jnt, fk_jnt, ik_jnt in zip(
            blend_chain, instance._fk_chain, instance._target_chain[:4],
        ):
            pc = cmds.parentConstraint(
                ik_jnt, fk_jnt, blend_jnt, mo=False,
            )[0]
            cmds.setAttr(f'{pc}.interpType', 2)
            sc = cmds.scaleConstraint(
                ik_jnt, fk_jnt, blend_jnt, mo=False,
            )[0]
            # Read weight alias names from each constraint and wire them
            # to blend / reverse. Aliases are the constraint targets'
            # short names; using the API returned aliases is more robust
            # than name-string concatenation.
            pweights = cmds.parentConstraint(pc, q=True,
                                              weightAliasList=True)
            sweights = cmds.scaleConstraint(sc, q=True,
                                             weightAliasList=True)
            cmds.connectAttr(f'{switch_ctrl}.ik_fk_blend',
                              f'{pc}.{pweights[0]}', force=True)
            cmds.connectAttr(f'{rev_node}.outputX',
                              f'{pc}.{pweights[1]}', force=True)
            cmds.connectAttr(f'{switch_ctrl}.ik_fk_blend',
                              f'{sc}.{sweights[0]}', force=True)
            cmds.connectAttr(f'{rev_node}.outputX',
                              f'{sc}.{sweights[1]}', force=True)

        # ─── Visibility gating ─────────────────────────────────────────
        # IK ctrls (foot, pv, hock) visible when ik_fk_blend > 0
        # FK ctrls visible when ik_fk_blend < 1
        # Direct connection of the blend attr (or reverse) to shape
        # visibility - any non-zero value makes the shape visible, zero
        # hides it (matches SimpleIK's idiom).
        for ik_ctrl in (instance._foot_ctrl, instance._pv_ctrl,
                         instance._hock_ctrl, instance._pv_line):
            shapes = cmds.listRelatives(ik_ctrl, shapes=True,
                                         fullPath=True) or []
            for shp in shapes:
                cmds.connectAttr(f'{switch_ctrl}.ik_fk_blend',
                                  f'{shp}.visibility', force=True)
        for fk_ctrl in instance._fk_ctrls:
            shapes = cmds.listRelatives(fk_ctrl, shapes=True,
                                         fullPath=True) or []
            for shp in shapes:
                cmds.connectAttr(f'{rev_node}.outputX',
                                  f'{shp}.visibility', force=True)

        instance._blend_chain = blend_chain
        instance._switch_ctrl = switch_ctrl
        instance._ikfk_rev = rev_node

        # ─── Bind chain drive (blend chain -> bind joints) ─────────────
        # Per joint for the first 4 (upper, knee, ankle, toe): parent +
        # scale constraint from the corresponding blend joint with
        # mo=True so the existing world pose stays put at constraint
        # creation. Constraints live UNDER the bind joints (Maya
        # default) - unbuild (Task 16) walks bind joints to find and
        # delete them.
        #
        # toe_end has no blend counterpart (FK + blend chains stop at toe
        # since toe_end is just a length endpoint with no animator-facing
        # ctrl). Constrain bind toe_end directly from the target chain's
        # toe_end so it follows the IK solve through Tasks 4-7.
        #
        # Gotcha 4: parentConstraint with interpType=2 even on
        # single-source constraints, defensively.
        for blend_jnt, bind_jnt in zip(instance._blend_chain,
                                        bind_joints[:4]):
            pc = cmds.parentConstraint(blend_jnt, bind_jnt, mo=True)[0]
            cmds.setAttr(f'{pc}.interpType', 2)
            cmds.scaleConstraint(blend_jnt, bind_jnt, mo=True)

        target_toe_end = instance._target_chain[4]
        pc_te = cmds.parentConstraint(target_toe_end, toe_end, mo=True)[0]
        cmds.setAttr(f'{pc_te}.interpType', 2)
        cmds.scaleConstraint(target_toe_end, toe_end, mo=True)

        # ─── foot_roll attribute ──────────────────────────────────────
        # Animator-friendly toe-tip lift via a single attribute on the
        # foot ctrl. Range -10..+10 maps to -90..+90 degrees on the toe
        # roll group rotation. The toe IK handle (parented directly
        # under foot_ctrl in Task 5) is reparented under a new
        # toe_roll_GRP so the attr-driven rotation pivots the handle's
        # world transform without disturbing the foot ctrl itself.
        #
        # Rotation axis is rotateX as a best guess for UE5
        # X-down-chain orientation. If foot_roll yaws or twists instead
        # of pitching the toe tip, change the connectAttr below to rotateY
        # or rotateZ.
        if not cmds.attributeQuery('foot_roll',
                                    node=instance._foot_ctrl,
                                    exists=True):
            cmds.addAttr(instance._foot_ctrl, ln='foot_roll',
                          at='float', min=-10.0, max=10.0, dv=0.0, k=True)

        roll_mult = cmds.createNode(
            'multiplyDivide',
            name=f'{short_start}_foot_roll_mult',
        )
        cmds.setAttr(f'{roll_mult}.operation', 1)  # multiply
        cmds.connectAttr(f'{instance._foot_ctrl}.foot_roll',
                          f'{roll_mult}.input1X')
        cmds.setAttr(f'{roll_mult}.input2X', 9.0)  # -10..10 -> -90..90

        # Insert toe_roll_GRP between foot_ctrl and the toe handle so
        # the foot_roll attr can pivot the handle. matchTransform to the
        # toe handle's current world frame so the GRP's local rotate
        # pivots around the handle's own pivot point.
        toe_roll_grp = cmds.createNode(
            'transform',
            name=f'{short_start}_toe_roll_offset',
            parent=instance._foot_ctrl,
        )
        cmds.matchTransform(toe_roll_grp, instance._toe_handle,
                            position=True, rotation=True)
        cmds.parent(instance._toe_handle, toe_roll_grp)

        cmds.connectAttr(f'{roll_mult}.outputX',
                          f'{toe_roll_grp}.rotateX', force=True)

        instance._foot_roll_nodes = [roll_mult, toe_roll_grp]

        # Track: roll_mult is a DG node with no DAG parent (toe_roll_grp
        # is fine -- it's parented under foot_ctrl, cascades with rig_grp).
        if component_node:
            nn.connect_message_multi(roll_mult, component_node, 'bind_blend_nodes')

        # ─── Stretchy IK on the spring chain ───────────────────────────
        # Classic stretch: when the distance from upper_drv to the spring
        # handle exceeds the rest chain length, per-joint translateX
        # scales proportionally so the chain stretches naturally as the
        # spring solver bends. Clamped at 1.0 - stretch only, no squash.
        # Three driver joints stretch (knee_drv, ankle_drv, toe_drv);
        # upper_drv is the chain root with translateX=0 and stays put.
        # Mirrors SimpleIK's pattern (simple_ik.py:730-799), gated on
        # the 'stretchy' option (default False).
        stretchy_nodes: list = []
        # Stretchy was wired to the spring driver chain + spring_handle.
        # With the spring solver dropped (Maya bug), the driver chain is
        # empty and the spring_handle is None - skip the entire block.
        # Future rework: distance from upper joint to foot_ctrl world vs
        # target chain rest_total, driving target chain joints' tx.
        if opts.get('stretchy', False) and instance._spring_handle:
            knee_drv = instance._driver_chain[1]
            ankle_drv = instance._driver_chain[2]
            toe_drv = instance._driver_chain[3]
            rest_tx_knee = float(cmds.getAttr(f'{knee_drv}.translateX'))
            rest_tx_ankle = float(cmds.getAttr(f'{ankle_drv}.translateX'))
            rest_tx_toe = float(cmds.getAttr(f'{toe_drv}.translateX'))
            rest_total = (abs(rest_tx_knee) + abs(rest_tx_ankle)
                          + abs(rest_tx_toe))
            if rest_total > 1e-6:
                # Distance from chain root to spring handle (current
                # chain demand). distanceBetween via worldMatrix inputs
                # so it's constraint-agnostic.
                dist_node = cmds.createNode(
                    'distanceBetween',
                    name=f'{short_start}_stretch_dist',
                )
                cmds.connectAttr(
                    f'{instance._driver_chain[0]}.worldMatrix[0]',
                    f'{dist_node}.inMatrix1',
                )
                cmds.connectAttr(
                    f'{instance._spring_handle}.worldMatrix[0]',
                    f'{dist_node}.inMatrix2',
                )
                # Ratio = current_distance / rest_total.
                ratio_node = cmds.createNode(
                    'multiplyDivide',
                    name=f'{short_start}_stretch_ratio',
                )
                cmds.setAttr(f'{ratio_node}.operation', 2)  # divide
                cmds.connectAttr(f'{dist_node}.distance',
                                  f'{ratio_node}.input1X')
                cmds.setAttr(f'{ratio_node}.input2X', rest_total)
                # Clamp: ratio > 1 uses ratio, else 1.0 (stretch only).
                clamp_node = cmds.createNode(
                    'condition',
                    name=f'{short_start}_stretch_clamp',
                )
                cmds.setAttr(f'{clamp_node}.operation', 2)  # greater than
                cmds.connectAttr(f'{ratio_node}.outputX',
                                  f'{clamp_node}.firstTerm')
                cmds.setAttr(f'{clamp_node}.secondTerm', 1.0)
                cmds.connectAttr(f'{ratio_node}.outputX',
                                  f'{clamp_node}.colorIfTrueR')
                cmds.setAttr(f'{clamp_node}.colorIfFalseR', 1.0)
                # Per-joint scaled translateX. rest_tx is signed (some
                # joints may have negative tx depending on chain axis
                # orientation); preserve sign by setting input2X to the
                # signed rest_tx so output keeps the same sign.
                for drv_jnt, rest_tx in (
                    (knee_drv, rest_tx_knee),
                    (ankle_drv, rest_tx_ankle),
                    (toe_drv, rest_tx_toe),
                ):
                    short_j = drv_jnt.split('|')[-1].split(':')[-1]
                    scale_node = cmds.createNode(
                        'multiplyDivide',
                        name=f'{short_j}_stretch_tx',
                    )
                    cmds.setAttr(f'{scale_node}.operation', 1)  # multiply
                    cmds.connectAttr(f'{clamp_node}.outColorR',
                                      f'{scale_node}.input1X')
                    cmds.setAttr(f'{scale_node}.input2X', rest_tx)
                    cmds.connectAttr(f'{scale_node}.outputX',
                                      f'{drv_jnt}.translateX', force=True)
                    stretchy_nodes.append(scale_node)
                stretchy_nodes.extend([dist_node, ratio_node, clamp_node])

        instance._stretchy_nodes = stretchy_nodes

        # Track: all stretchy nodes are bare DG (no DAG parent). Dead code
        # path today (instance._spring_handle is always None -- spring
        # solver dropped, see comment above), but tracked defensively so
        # a future spring-solver revival doesn't reopen the leak unbuild()
        # was written to close.
        if component_node:
            for n in stretchy_nodes:
                nn.connect_message_multi(n, component_node, 'stretchy_nodes')

    @classmethod
    def unbuild(cls, instance) -> dict:
        """Capture ctrl/attr state, delete bind-joint constraints + all
        owned DG nodes, matching the build/unbuild round-trip contract
        every other component honors (SimpleIK / IKLeg reference pattern).

        QuadLeg is not a Component subclass of SimpleIK (unlike IKLeg), so
        there's no shared base unbuild() to delegate to -- Component.unbuild
        is an abstract stub with no generic behavior. This is a from-scratch
        implementation, structurally closest to SimpleIK's unbuild (parallel
        FK/IK/BLEND chains) plus IKLeg's bind-joint-constraint sweep
        (ik_leg.py:761-769) generalized across all 5 bind joints instead of
        just the foot.

        Most owned transforms (target/FK/blend chains, IK handles, hock
        pivot rig, PV line + clusters, toe_roll_grp) are DAG-parented under
        setup_grp / controls_grp, which cascade-delete when the orchestrator
        deletes rig_grp after this returns -- no explicit tracking needed
        for those. What DOES need explicit handling:
          - bind-joint constraints (parentConstraint/scaleConstraint on the
            5 bind joints themselves -- outside rig_grp, cascade misses them)
          - bare DG nodes with no DAG parent (ikfk reverse, foot_roll
            multiplyDivide, stretchy chain nodes if ever live) -- tracked at
            build time on the 'bind_blend_nodes' / 'stretchy_nodes' buckets
        """
        from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com

        full_joints = list(instance.joints)
        if len(full_joints) != 5:
            return {}
        upper, knee, ankle, toe, toe_end = full_joints
        short_start = upper.split('|')[-1].split(':')[-1]
        short_knee = knee.split('|')[-1].split(':')[-1]
        short_ankle = ankle.split('|')[-1].split(':')[-1]
        short_toe = toe.split('|')[-1].split(':')[-1]
        short_end = toe_end.split('|')[-1].split(':')[-1]

        opts = instance.options

        # ─── Capture CV shape data for every re-shapeable ctrl ─────────
        ctrls_to_capture = [
            (f'{short_start}_FK_ctrl', opts.get('ctrl_shape', 'capsule')),
            (f'{short_knee}_FK_ctrl',  opts.get('ctrl_shape', 'capsule')),
            (f'{short_ankle}_FK_ctrl', opts.get('ctrl_shape', 'capsule')),
            (f'{short_toe}_FK_ctrl',   opts.get('toe_fk_shape', 'capsule')),
            (f'{short_end}_IK_ctrl',   opts.get('ik_ctrl_shape', 'foot')),
            (f'{short_knee}_PV_ctrl',  opts.get('pv_shape', 'diamond')),
            (f'{short_toe}_hock_FK_ctrl', opts.get('hock_ctrl_shape', 'arrow_four_way')),
            (f'{short_end}_IKFK_ctrl', opts.get('switch_ctrl_shape', 'cog')),
        ]
        cv_data_block = {}
        for ctrl_name, _shape_key in ctrls_to_capture:
            if not cmds.objExists(ctrl_name):
                continue
            try:
                shape_data = com.serialize_shape(ctrl_name)
                if shape_data.get('shapes'):
                    cv_data_block[ctrl_name] = shape_data['shapes']
            except RuntimeError:
                pass

        # ─── Capture attr state ─────────────────────────────────────────
        switch_ctrl_name = f'{short_end}_IKFK_ctrl'
        ik_fk_blend = 0.0
        if cmds.objExists(switch_ctrl_name) and cmds.attributeQuery(
                'ik_fk_blend', node=switch_ctrl_name, exists=True):
            ik_fk_blend = float(cmds.getAttr(f'{switch_ctrl_name}.ik_fk_blend'))

        foot_ctrl_name = f'{short_end}_IK_ctrl'
        foot_roll_value = 0.0
        if cmds.objExists(foot_ctrl_name) and cmds.attributeQuery(
                'foot_roll', node=foot_ctrl_name, exists=True):
            foot_roll_value = float(cmds.getAttr(f'{foot_ctrl_name}.foot_roll'))

        captured = {
            'starting_shape': opts.get('ctrl_shape', 'capsule'),
            'cv_data': cv_data_block,
            'ik_fk_blend': ik_fk_blend,
            'foot_roll_value': foot_roll_value,
            'enum_orders': {},
        }

        # ─── Delete bind-joint constraints ──────────────────────────────
        # Constraints created directly on the bind chain (build() lines
        # ~768-777) live under the bind joints themselves, NOT under
        # rig_grp -- the orchestrator's rig_grp cascade delete misses them.
        # All 5 bind joints get a constraint (first 4 from blend_chain,
        # toe_end from the target chain directly) -- sweep all 5, not just
        # one, unlike IKLeg's single-joint (ball) sweep.
        for jnt in full_joints:
            if not cmds.objExists(jnt):
                continue
            constraints = (
                (cmds.listRelatives(jnt, type='parentConstraint') or [])
                + (cmds.listRelatives(jnt, type='scaleConstraint') or [])
            )
            if constraints:
                cmds.delete(constraints)

        # ─── Delete owned DG nodes tracked at build time ────────────────
        component_node = None
        for cn in ks_nodes.get_all_component_nodes():
            if ks_nodes.get_component_id(cn) == instance.id:
                component_node = cn
                break
        if component_node:
            for bucket in ('bind_blend_nodes', 'stretchy_nodes'):
                for n in nn.get_message_targets(component_node, bucket):
                    if cmds.objExists(n):
                        cmds.delete(n)

        return captured
