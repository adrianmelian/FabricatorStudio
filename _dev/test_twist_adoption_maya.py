# _dev/test_twist_adoption_maya.py
"""mayapy scene tests for twist-joint ADOPTION at component add (Adrian,
2026-07-10 design session, blessed same day).

THE FEATURE: the UE5.8 Manny ships 2 twist joints per segment on arms
and legs, each parented directly to its segment's top joint (all
lowerarm twists are siblings under the lowerarm) — the exact topology
KS's own twist dial produces. When an IK component (free or ribbon, arm
or leg) is added onto such a skeleton, the implicit-limb discovery pass
now ADOPTS those joints onto the limb node's twist_upper[]/twist_lower[]
multis with distribute follow rules whose t comes from each joint's
ACTUAL position along the bone — never repositioning them (they are
usually skinned), unlike the dial's deliberate even re-spacing.

Detection is deliberately conservative (a wrong adoption moves someone's
skinned joint on the next dial change): child of the segment's top
joint + name contains 'twist' (case-insensitive) + childless + not the
main chain. A childless helper joint without 'twist' in its name is
never adopted.

Also under test: IKLegComponent (and RibbonIKLeg by inheritance) now
creates_implicit_limb — a bare leg add on Manny gets a limb node to
adopt onto — and the FREE leg's new lower-segment fractional twist drive
(the free arm's forearm twist, hoisted to _limb_common and consumed by
ik_leg: calf twists rotate t x the ankle bind's bone-axis rotation;
thigh twists ride rigid v1, exact free-arm parity).

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_twist_adoption_maya.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


class Skip(Exception):
    """Environment-gap marker — never absorbed into the pass count."""


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Skip as exc:
        SKIPS.append(f"{name}: {exc}")
        print(f"  SKIP: {name}: {exc}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


# ─── Manny-shaped fixtures ───────────────────────────────────────────────

def _lerp(a, b, t):
    return [a[i] + (b[i] - a[i]) * t for i in range(3)]


def _add_seg_twists(seg_top, seg_end, names, fractions):
    """Twist joints as CHILDREN of seg_top (never chained), at authored
    fractions along seg_top->seg_end — the Manny layout verbatim."""
    import maya.cmds as cmds
    tp = cmds.xform(seg_top, q=True, ws=True, t=True)
    ep = cmds.xform(seg_end, q=True, ws=True, t=True)
    out = []
    for name, t in zip(names, fractions):
        cmds.select(seg_top)
        out.append(cmds.joint(name=name, p=_lerp(tp, ep, t)))
        cmds.select(clear=True)
    return out


def _make_manny_arm(prefix):
    """root -> upperarm -> lowerarm -> hand, plus 2 authored twists per
    segment parented Manny-style, plus a childless NON-twist helper under
    the upperarm (the no-false-adoption control)."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 140, 0), name=f'{prefix}_root')
    upper = cmds.joint(p=(10, 140, 0), name=f'{prefix}_upperarm_l')
    lower = cmds.joint(p=(30, 125, 2), name=f'{prefix}_lowerarm_l')
    hand = cmds.joint(p=(50, 110, 0), name=f'{prefix}_hand_l')
    cmds.select(clear=True)

    up_tw = _add_seg_twists(upper, lower,
                            [f'{prefix}_upperarm_twist_01_l',
                             f'{prefix}_upperarm_twist_02_l'],
                            (1.0 / 3.0, 2.0 / 3.0))
    lo_tw = _add_seg_twists(lower, hand,
                            [f'{prefix}_lowerarm_twist_01_l',
                             f'{prefix}_lowerarm_twist_02_l'],
                            (0.25, 0.75))
    # Control joint: childless, correctly parented, NOT twist-named.
    cmds.select(upper)
    helper = cmds.joint(name=f'{prefix}_deltoid_helper_l',
                        p=(15, 141, 1))
    cmds.select(clear=True)

    nodes.create_registry(f'{prefix}_bp')
    nodes.set_registry_root_joint(root)
    nodes.create_component_node(
        component_id=f'{prefix}_world', component_type='World',
        joints=[root], parent_plug='', side='md', options={}, persisted={},
    )
    return root, upper, lower, hand, up_tw, lo_tw, helper


def _make_manny_leg(prefix):
    """root -> thigh -> calf -> foot -> ball, 2 authored twists per
    segment parented Manny-style."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes

    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    root = cmds.joint(p=(0, 90, 0), name=f'{prefix}_root')
    thigh = cmds.joint(p=(9, 84, 0), name=f'{prefix}_thigh_l')
    calf = cmds.joint(p=(9, 45, 3), name=f'{prefix}_calf_l')
    foot = cmds.joint(p=(9, 8, 0), name=f'{prefix}_foot_l')
    ball = cmds.joint(p=(9, 1, 12), name=f'{prefix}_ball_l')
    cmds.select(clear=True)

    th_tw = _add_seg_twists(thigh, calf,
                            [f'{prefix}_thigh_twist_01_l',
                             f'{prefix}_thigh_twist_02_l'],
                            (1.0 / 3.0, 2.0 / 3.0))
    ca_tw = _add_seg_twists(calf, foot,
                            [f'{prefix}_calf_twist_01_l',
                             f'{prefix}_calf_twist_02_l'],
                            (0.25, 0.75))

    nodes.create_registry(f'{prefix}_bp')
    nodes.set_registry_root_joint(root)
    nodes.create_component_node(
        component_id=f'{prefix}_world', component_type='World',
        joints=[root], parent_plug='', side='md', options={}, persisted={},
    )
    return root, thigh, calf, foot, ball, th_tw, ca_tw


def _bone_axis_letter_of(joint):
    import maya.cmds as cmds
    t = cmds.getAttr(f'{joint}.translate')[0]
    return 'xyz'[max(range(3), key=lambda i: abs(t[i]))]


# ─── Adoption: arm ───────────────────────────────────────────────────────

def test_arm_add_adopts_manny_twists_with_positional_t():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes, limb_node as ln
    from maya_tools.rigging.fabricator import follow_rules as fr

    (_root, upper, lower, hand,
     up_tw, lo_tw, helper) = _make_manny_arm('adarm')

    pre_pos = {j: cmds.xform(j, q=True, ws=True, t=True)
               for j in up_tw + lo_tw}

    nodes.create_component_node(
        component_id='adarm_C0', component_type='IKArm',
        joints=[upper, lower, hand], parent_plug='', side='lf',
        options={}, persisted={},
    )

    limb = ln.find_limb_for_joint(upper)
    assert limb, 'implicit limb missing after IKArm add'
    assert ln.list_twist_upper(limb) == up_tw, (
        f'upper adoption (t-ordered): {ln.list_twist_upper(limb)}')
    assert ln.list_twist_lower(limb) == lo_tw, (
        f'lower adoption (t-ordered): {ln.list_twist_lower(limb)}')

    # Distribute rules with POSITIONAL t (authored fractions, not the
    # dial's even respace) — and zero repositioning.
    for j, want_t, (a, b) in ((up_tw[0], 1.0 / 3.0, (upper, lower)),
                              (up_tw[1], 2.0 / 3.0, (upper, lower)),
                              (lo_tw[0], 0.25, (lower, hand)),
                              (lo_tw[1], 0.75, (lower, hand))):
        rule = fr.get_follow_rule(j)
        assert rule is not None, f'{j}: no follow rule after adoption'
        assert rule['kind'] == 'distribute', rule
        assert set(rule['targets']) == {a, b}, (j, rule['targets'])
        assert abs(rule['t'] - want_t) < 1e-3, (j, rule['t'], want_t)
        got = cmds.xform(j, q=True, ws=True, t=True)
        assert all(abs(g - p) < 1e-4 for g, p in zip(got, pre_pos[j])), (
            f'{j} MOVED during adoption: {pre_pos[j]} -> {got}')

    # The control joint: childless, parent-correct, not twist-named —
    # never adopted, no rule.
    assert helper not in ln.list_twist_upper(limb)
    assert fr.get_follow_rule(helper) is None


def test_arm_adoption_is_idempotent_across_rediscovery():
    from maya_tools.rigging.fabricator import nodes, limb_node as ln

    (_root, upper, lower, hand,
     up_tw, lo_tw, _helper) = _make_manny_arm('adarm2')
    nodes.create_component_node(
        component_id='adarm2_C0', component_type='RibbonIKArm',
        joints=[upper, lower, hand], parent_plug='', side='lf',
        options={}, persisted={},
    )
    limb = ln.find_limb_for_joint(upper)
    assert ln.list_twist_upper(limb) == up_tw
    # Re-run the same derivation pass (fs_app.load's derive-all path).
    cnode = nodes.find_component_node_by_id('adarm2_C0')
    nodes.derive_limb(cnode)
    assert ln.list_twist_upper(limb) == up_tw, 'duplicate adoption'
    assert ln.list_twist_lower(limb) == lo_tw, 'duplicate adoption'


# ─── Adoption: leg (+ the new creates_implicit_limb flag) ────────────────

def test_leg_add_creates_implicit_limb_and_adopts_twists():
    from maya_tools.rigging.fabricator import nodes, limb_node as ln
    from maya_tools.rigging.fabricator import follow_rules as fr

    (_root, thigh, calf, foot, ball,
     th_tw, ca_tw) = _make_manny_leg('adleg')
    nodes.create_component_node(
        component_id='adleg_C0', component_type='IKLeg',
        joints=[thigh, calf, foot, ball], parent_plug='', side='lf',
        options={}, persisted={},
    )
    limb = ln.find_limb_for_joint(thigh)
    assert limb, ('IKLeg must now create an implicit limb '
                  '(creates_implicit_limb, 2026-07-10)')
    assert ln.is_implicit(limb) is True
    assert ln.list_twist_upper(limb) == th_tw, ln.list_twist_upper(limb)
    assert ln.list_twist_lower(limb) == ca_tw, ln.list_twist_lower(limb)
    # 'lower' spans calf->FOOT, never calf->ball (a foot is not a shin).
    rule = fr.get_follow_rule(ca_tw[0])
    assert set(rule['targets']) == {calf, foot}, rule['targets']


# ─── Free-leg fractional twist drive (hoisted free-arm helper) ───────────

def test_free_ikleg_calf_fractional_twist_drive():
    """The free leg's new lower-segment drive: each adopted calf twist
    rotates t x the ANKLE bind joint's bone-axis rotation (post-blend,
    so IK and FK both work); thigh twists ride rigid — free-arm parity."""
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes

    (_root, thigh, calf, foot, ball,
     th_tw, ca_tw) = _make_manny_leg('adlegdrv')
    nodes.create_component_node(
        component_id='adlegdrv_C0', component_type='IKLeg',
        joints=[thigh, calf, foot, ball], parent_plug='', side='lf',
        options={}, persisted={},
    )
    fs_app.build_modules()

    axis = _bone_axis_letter_of(foot).upper()
    cmds.setAttr(f'{foot}_IK_ctrl.rotate{axis}', 60.0)
    a = cmds.getAttr(f'{foot}.rotate{axis}')
    r = [cmds.getAttr(f'{j}.rotate{axis}') for j in ca_tw]
    assert abs(a) > 1.0, f'ankle never rolled in IK (a={a})'
    assert abs(r[0] - a * 0.25) < 0.5 and abs(r[1] - a * 0.75) < 0.5, (r, a)

    # Thigh twists: rigid v1 — no incoming rotate connections.
    for j in th_tw:
        src = cmds.listConnections(f'{j}.rotate{axis}', source=True,
                                   destination=False) or []
        assert not src, f'{j}: thigh twists must ride rigid in v1 ({src})'

    fs_app.unbuild_modules()
    # Sweep proof: no twist scalar nodes survive unbuild.
    stray = [n for n in (cmds.ls(type='multDoubleLinear') or [])
             if '_twist_frac' in n]
    assert not stray, f'unbuild left twist DG nodes: {stray}'


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    check("test_arm_add_adopts_manny_twists_with_positional_t",
          test_arm_add_adopts_manny_twists_with_positional_t)
    check("test_arm_adoption_is_idempotent_across_rediscovery",
          test_arm_adoption_is_idempotent_across_rediscovery)
    check("test_leg_add_creates_implicit_limb_and_adopts_twists",
          test_leg_add_creates_implicit_limb_and_adopts_twists)
    check("test_free_ikleg_calf_fractional_twist_drive",
          test_free_ikleg_calf_fractional_twist_drive)

    if FAILURES:
        print(f"TWIST ADOPTION TESTS: {len(FAILURES)} FAILED "
              f"({len(SKIPS)} SKIP)")
        sys.exit(1)
    print(f"TWIST ADOPTION TESTS: OK - {len(SKIPS)} SKIP"
          if SKIPS else "TWIST ADOPTION TESTS: OK - 0 SKIP")


if __name__ == "__main__":
    main()
