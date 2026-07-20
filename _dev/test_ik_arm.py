# _dev/test_ik_arm.py
"""Offscreen contract tests for the FREE-core IKArm component
(modules/ik_arm.py). No live Maya scene required — these just prove the
module imports, declares the IK_ARM_CONTRACT the design promises, and
never reaches for paid (Advanced Ribbon) code.

Suite banner: IK ARM (FREE) TESTS. Companion scene-level suite:
_dev/test_ik_arm_maya.py (Tasks 2+). Design:
docs/superpowers/specs/2026-07-09-basicikarm-module-design.md; task plan:
workspace/2026-07-09_basicikarm-module/PLAN.md (Task 1).

---- PIN TABLE (Task 0, verified 2026-07-10 against KS main, hoist
commit 19b1c7a — summarized from the leader's LANDED-SURFACE FACTS) ----

* modules/ribbon_ik_arm.py carries RibbonIKArmComponent
  (type='RibbonIKArm', limb_features on the contract) — the vacated free
  'IKArm' type string is this module's to claim; pre-rename scenes
  migrate via the version-gated legacy map, never a standing alias.
* _limb_common.build_hand_from_limb(limb, wrist_joint, switch_ctrl,
  opts, component_node, cv_block, index_offset=0) at
  modules/_limb_common.py:727 — the ONE shared hand-orchestration code
  path both RibbonIKArm and this free arm call. Caller resolves the
  limb node (limb_node.find_limb_for_joint(shoulder)); opts pass
  through verbatim; cv_block =
  {'finger_ctrl_cv_data': {...}, 'fingers_ctrl_cv_data': {...}} (both
  optional); index_offset = len(instance.joints). Returns
  {'finger_built': {root: [(offset_ctrl, ctrl), ...]},
  'fingers_ctrl': dict-or-None}. Handles limb=None (zero fingers, inert
  fist ctrl still builds).
* Call-site to mirror (minus ribbon): ribbon_ik_arm.py:406-436. Unbuild
  finger-side pattern to mirror: ribbon_ik_arm.py:571-623. No hoisted
  UNBUILD-side capture helper landed — Task 4 inlines that branch.
* limb_node.py lives at the fabricator ROOT (sibling of limbs/, NOT
  inside it): `from maya_tools.rigging.fabricator import limb_node as
  ln`. Pinned getters used by this build: find_limb_for_joint(joint)
  (:163), list_finger_roots, list_twist_lower (:353) / add_twist_lower
  (:345), list_twist_upper / add_twist_upper, list_curl_excluded.
* Registry stamps fabricator_version AT CREATION
  (nodes.create_registry) — fresh scenes are NOT legacy-gated;
  resolve_component_type('IKArm') returns 'IKArm' verbatim on a fresh
  registry. The legacy map only fires for pre-1.1.0/unstamped OLD data.
* Generic Ribbon ruled PAID (Adrian 2026-07-09): the paid-boundary set
  is {_ribbon_common, ribbon_ik_arm, ribbon_ik_leg, ribbon_spine,
  ribbon} — this module must never import any of them (enforced here
  by test_module_imports_no_paid_code; the repo-wide static walker is
  _dev/test_open_core_import_boundary.py, Task 7).
* component.py's Contract.limb_features opt-in (default empty tuple)
  and the Contract/OptionField/JointRole dataclasses (component.py)
  are landed and used verbatim by both arms.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_ik_arm.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []
SKIPS = []


class Skip(Exception):
    """Raise from a test body to mark it SKIPPED (environment gap, e.g.
    missing optional dependency) — NEVER silently absorbed into the pass
    count. A test that hits this must NOT also be logged 'ok': check()
    below tracks it into SKIPS, a separate tally from both FAILURES and
    the implicit pass count, and main()'s summary reports all three
    distinctly."""


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


def test_contract_identity_and_options():
    from maya_tools.rigging.fabricator.modules.ik_arm import (
        IKArmComponent, IK_ARM_CONTRACT)
    from maya_tools.rigging.fabricator.modules.simple_ik import SIMPLE_IK_CONTRACT

    c = IK_ARM_CONTRACT
    assert c.type == 'IKArm' and c.display_name == 'IK Arm'
    assert c.color == '#5BFF8B' and c.default_region == 'arm'
    # Derived Limbs (2026-07-11): opt-in is the contract, not a class bool.
    assert c.limb_features == ('fingers', 'twists')

    # Everything SimpleIK has, plus ONLY the four hand options.
    extra = set(c.options_schema) - set(SIMPLE_IK_CONTRACT.options_schema)
    assert extra == {'curl_axis', 'finger_fk_shape', 'fingers_ctrl_shape',
                     'fingers_ctrl_color'}, extra
    assert c.options_schema['fingers_ctrl_color'].default == ''
    assert c.options_schema['fingers_ctrl_shape'].default == 'sine_handle'
    assert c.options_schema['finger_fk_shape'].default == 'capsule_ramp'
    assert c.options_schema['curl_axis'].default == 'z'

    # The strip list stays stripped — no ribbon keys, no fingers option.
    for banned in ('mid_ctrl_count', 'ribbon_width', 'ribbon_mid_ctrl_shape',
                   'ribbon_ctrl_color', 'fingers'):
        assert banned not in c.options_schema, banned

    # 3-joint arm roles, SimpleIK plumbing inherited.
    assert [r.name for r in c.joint_roles] == ['start', 'mid', 'end']
    assert c.min_joints == 3 and c.max_joints == 3
    assert c.space_consumers == SIMPLE_IK_CONTRACT.space_consumers
    assert c.mirror_rules == SIMPLE_IK_CONTRACT.mirror_rules


def test_module_imports_no_paid_code():
    import ast, pathlib
    src = pathlib.Path('maya_tools/rigging/fabricator/modules/ik_arm.py').read_text()
    tree = ast.parse(src)
    banned = {'_ribbon_common', 'ribbon_ik_arm', 'ribbon_ik_leg', 'ribbon_spine',
              'ribbon'}   # generic Ribbon ruled PAID (Adrian 2026-07-09)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ''] + [a.name for a in node.names]
        for n in names:
            assert not any(b in n for b in banned), f'paid import: {n}'


def main():
    check("test_contract_identity_and_options", test_contract_identity_and_options)
    check("test_module_imports_no_paid_code", test_module_imports_no_paid_code)

    if FAILURES:
        print(f"IK ARM (FREE) TESTS: {len(FAILURES)} FAILED "
              f"({len(SKIPS)} SKIP)")
        sys.exit(1)
    if SKIPS:
        print(f"IK ARM (FREE) TESTS: OK - {len(SKIPS)} SKIP "
              f"(not counted as pass): {SKIPS}")
    else:
        print("IK ARM (FREE) TESTS: OK - 0 SKIP")


if __name__ == "__main__":
    main()
