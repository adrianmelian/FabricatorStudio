# _dev/test_ribbon_ik_arm_ui.py
"""Headless smoke tests proving RibbonIKArm's Properties-panel fingers
editor is RETIRED (Task 2.3, SPEC 2026-07-09 Limbs + Follower Joints
§3.4, "Always-limb membership").

Formerly (PLAN.md 2026-07-08 Task 4.3, FIRST PASS) this file exercised
option_widgets.FingerListField + its 'finger_list' OptionField factory
(option_widgets._build_finger_list) end to end. Both are gone: RibbonIKArm's
'fingers' name-string option they edited no longer exists on the
contract — finger/twist membership lives on the component's fab_limb
node now (limb_node.py's finger_roots[]/curl_excluded[] message multis),
edited via the limb dials landing in P3 (interim editing is script-level,
acceptable per the SPEC's own phasing). This file now proves the clean
retirement: the class/factory are actually gone (not just unused), and a
'finger_list'-typed OptionField (should one ever be hand-authored again,
e.g. by a stale contract on disk) degrades to the generic disabled
fallback widget rather than crashing.

Mirrors maya_tools/framework/_dev/test_project_setup_ui.py's established
offscreen-Qt idiom exactly: bare `mayapy` with QT_QPA_PLATFORM=offscreen
and NO `maya.standalone.initialize()`.

Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_ribbon_ik_arm_ui.py
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()
    finally:
        _flush_qt()


_APP = None


def qt_app():
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication
        _APP = QApplication.instance() or QApplication([])
    return _APP


def _flush_qt():
    if _APP is None:
        return
    import gc
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    gc.collect()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.processEvents()


def test_option_widgets_module_imports_offscreen():
    """Smoke check: option_widgets.py still imports cleanly under bare
    mayapy + QT_QPA_PLATFORM=offscreen with no Maya session."""
    import maya_tools.rigging.fabricator.ui.option_widgets as ow
    assert ow is not None


def test_finger_list_field_class_no_longer_exported():
    """The retirement itself: FingerListField must not exist on the
    module anymore — not merely unregistered from the factory dict, but
    genuinely removed (a lingering-but-unreachable class would still be
    dead code worth catching)."""
    import maya_tools.rigging.fabricator.ui.option_widgets as ow
    assert not hasattr(ow, 'FingerListField'), (
        "option_widgets.FingerListField still exists — Task 2.3 removal "
        "incomplete")
    assert not hasattr(ow, '_build_finger_list'), (
        "option_widgets._build_finger_list factory still exists — Task "
        "2.3 removal incomplete")


def test_finger_list_option_field_falls_back_to_default_widget():
    """The dispatch path _populate_options_form actually uses:
    widget_for_option_field(field, current_value) -> OptionWidget. A
    'finger_list'-typed OptionField (no component declares one anymore,
    but the type string itself isn't forbidden) must degrade to the
    generic disabled '?' fallback widget_for_option_field already uses
    for any unregistered type, not raise — proving the factory
    registration is genuinely gone, not just untested."""
    qt_app()
    from maya_tools.rigging.fabricator.modules.component import OptionField
    from maya_tools.rigging.fabricator.ui.option_widgets import widget_for_option_field
    field = OptionField('finger_list', [])
    ow = widget_for_option_field(field, [{'root_joint': 'thumb_01'}])
    # The documented fallback shape (see widget_for_option_field's own
    # "Fallback: a disabled QLineEdit with a '?'" branch) — a fixed '?'
    # value, disabled, never crashes on a value it can't actually store.
    assert ow.get_value() == '?', ow.get_value()
    assert ow.widget.isEnabled() is False, (
        'fallback widget for an unregistered OptionField type should be '
        'disabled')
    ow.widget.close()


def test_ribbon_ik_arm_contract_has_no_finger_list_field():
    """End-to-end confirmation from the OTHER direction: RIBBON_IK_ARM_
    CONTRACT itself declares no 'finger_list'-typed option anymore (this
    mirrors _dev/test_ribbon_ik_arm.py's own offscreen contract-shape
    coverage; kept here too since this file is the UI-widget suite of
    record for this retirement)."""
    from maya_tools.rigging.fabricator.modules.ribbon_ik_arm import (
        RIBBON_IK_ARM_CONTRACT,
    )
    assert 'fingers' not in RIBBON_IK_ARM_CONTRACT.options_schema, (
        RIBBON_IK_ARM_CONTRACT.options_schema.keys())
    assert all(
        f.type != 'finger_list'
        for f in RIBBON_IK_ARM_CONTRACT.options_schema.values()
    )


def main():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

    check('test_option_widgets_module_imports_offscreen',
          test_option_widgets_module_imports_offscreen)
    check('test_finger_list_field_class_no_longer_exported',
          test_finger_list_field_class_no_longer_exported)
    check('test_finger_list_option_field_falls_back_to_default_widget',
          test_finger_list_option_field_falls_back_to_default_widget)
    check('test_ribbon_ik_arm_contract_has_no_finger_list_field',
          test_ribbon_ik_arm_contract_has_no_finger_list_field)

    if FAILURES:
        print(f"RIBBON IK ARM UI OFFSCREEN TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("RIBBON IK ARM UI OFFSCREEN TESTS: OK")


if __name__ == '__main__':
    main()
