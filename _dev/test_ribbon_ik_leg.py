# _dev/test_ribbon_ik_leg.py
"""Offscreen tests for the RibbonIKLeg component (Phase P1 — pure parity
subclass). No live Maya scene required — these just prove the module
imports, registers with the component auto-discovery registry, and
declares the contract P1 promises: IK_LEG_CONTRACT's full surface plus
the four ribbon options, with zero behavior delta (build/unbuild/
can_apply/guides all inherited verbatim from IKLegComponent).

Mirrors _dev/test_ribbon_ik_arm.py's header/harness pattern.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_ribbon_ik_leg.py
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
    count. See test_ribbon_ik_arm.py's identical Skip class for the full
    rationale."""


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
    from maya_tools.rigging.fabricator.modules.ribbon_ik_leg import (
        RIBBON_IK_LEG_CONTRACT, RibbonIKLegComponent,
    )
    from maya_tools.rigging.fabricator.modules.ik_leg import (
        IKLegComponent, IK_LEG_CONTRACT,
    )
    c = RIBBON_IK_LEG_CONTRACT
    assert c.type == 'RibbonIKLeg'
    assert c.display_name == 'Ribbon IK Leg'
    assert c.color == '#9B7BFF'
    assert c.default_region == 'leg'
    # Inherits ALL of IKLeg's surface untouched.
    assert c.min_joints == 4 and c.max_joints == 4
    assert c.joint_roles == IK_LEG_CONTRACT.joint_roles
    assert c.extra_guides == IK_LEG_CONTRACT.extra_guides
    assert c.mirror_rules == IK_LEG_CONTRACT.mirror_rules
    assert c.outputs == IK_LEG_CONTRACT.outputs        # ball_out included
    # Every IKLeg option survives; the four ribbon options are added.
    for key in IK_LEG_CONTRACT.options_schema:
        assert key in c.options_schema
    for key in ('mid_ctrl_count', 'ribbon_width',
                'ribbon_mid_ctrl_shape', 'ribbon_ctrl_color'):
        assert key in c.options_schema
    # Subclass seam: it IS an IKLeg (reverse foot comes along for free).
    assert issubclass(RibbonIKLegComponent, IKLegComponent)
    assert RibbonIKLegComponent.CONTRACT is c


def test_component_auto_discovery_registers_ribbon_ik_leg():
    """Component auto-discovery iterates modules/ (modules/__init__.py's
    pkgutil-based _discover(), consumed via get_component_class /
    all_component_types — the REAL discovery entry point this codebase
    uses; there is no discover_components() API). A new module file must
    register cleanly and must not destabilize discovery for siblings —
    the exact risk the arm's own suite (test_ik_arm_is_discovered) guards
    from its side."""
    from maya_tools.rigging.fabricator.modules import (
        get_component_class, all_component_types,
    )
    from maya_tools.rigging.fabricator.modules.ribbon_ik_leg import (
        RibbonIKLegComponent,
    )

    types = all_component_types()
    assert 'RibbonIKLeg' in types, types
    assert 'IKLeg' in types, types             # sibling still discovered

    cls = get_component_class('RibbonIKLeg')
    assert cls is RibbonIKLegComponent, cls
    assert cls.CONTRACT.type == 'RibbonIKLeg'

    sibling = get_component_class('IKLeg')
    assert sibling.CONTRACT.type == 'IKLeg'


def test_free_ik_leg_never_imports_paid_modules():
    """P4 Task 8 gate (import-boundary, SPEC §4 / PLAN.md Task 8): the
    FREE-core ik_leg.py must never import any paid ribbon module — the
    exact boundary that keeps RibbonIKLeg's paid package (ribbon_ik_leg,
    ribbon_ik_arm, ribbon_spine, _ribbon_common) cleanly separable from
    the free tier's IKLeg.

    Disposition (PLAN.md Task 8 decision tree): _dev/test_
    open_core_import_boundary.py does NOT exist in this repo yet (checked
    directly — no such file under _dev/), so this leg-scoped AST
    assertion lives in THIS lane's own offscreen suite per the plan's
    Step 1 "if NO" branch, rather than duplicating or extending a
    sibling-owned file that isn't there. If/when the Depot Leader (or the
    BasicIKArm thread) lands a shared test_open_core_import_boundary.py
    covering ribbon_ik_leg (paid) + ik_leg (free), that file becomes the
    canonical home for this exact check; this one stays here regardless
    as a same-lane belt-and-braces guard (duplication is cheap and one
    less cross-lane dependency for this component's own gate)."""
    import ast, pathlib
    src = pathlib.Path('maya_tools/rigging/fabricator/modules/ik_leg.py')
    tree = ast.parse(src.read_text(encoding='utf-8'))
    banned = {'_ribbon_common', 'ribbon_ik_leg', 'ribbon_ik_arm',
              'ribbon_spine'}
    for node in ast.walk(tree):
        names = ([a.name for a in node.names]
                 if isinstance(node, ast.Import) else
                 [node.module] if isinstance(node, ast.ImportFrom) else [])
        for name in names:
            assert not any(b in (name or '') for b in banned), (
                f'free ik_leg.py imports paid module {name!r}')


def main():
    check('test_contract_identity_and_options',
          test_contract_identity_and_options)
    check('test_component_auto_discovery_registers_ribbon_ik_leg',
          test_component_auto_discovery_registers_ribbon_ik_leg)
    check('test_free_ik_leg_never_imports_paid_modules',
          test_free_ik_leg_never_imports_paid_modules)

    if FAILURES:
        print(f"RIBBON IK LEG OFFSCREEN TESTS: {len(FAILURES)} FAILED "
              f"({len(SKIPS)} SKIP)")
        sys.exit(1)
    if SKIPS:
        print(f"RIBBON IK LEG OFFSCREEN TESTS: OK - {len(SKIPS)} SKIP "
              f"(not counted as pass): {SKIPS}")
    else:
        print("RIBBON IK LEG OFFSCREEN TESTS: OK - 0 SKIP")


if __name__ == "__main__":
    main()
