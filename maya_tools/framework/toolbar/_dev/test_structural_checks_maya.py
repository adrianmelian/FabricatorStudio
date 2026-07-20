# Python/maya_tools/framework/toolbar/_dev/test_structural_checks_maya.py
"""maya.standalone tests for the Fabricator structural-integrity prebuild
checks (maya_tools/rigging/fabricator/fs_app.py finders).

Covers find_misparented_ctrls(): the no-registry clean-scene case, the
untagged *_amt_CTL armature population (must chain to
fab_armature_grp), and the fab_role-tagged ctrl population (must
chain to fab_controls_grp) — both in their happy-path parented state and
in the CP-C repro shape (accidentally parented to world).

Run:
PYTHONNOUSERSITE=1 "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" maya_tools/framework/toolbar/_dev/test_structural_checks_maya.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "maya_tools" / "_vendor"))

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


def test_misparented_finder_clean_scene_is_clean():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app
    cmds.file(new=True, force=True)
    assert fs_app.find_misparented_ctrls() == []   # no registry -> clean


def test_misparented_amt_ctrl_detected():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    grp = cmds.createNode("transform", name="fab_armature_grp")
    good = cmds.createNode("transform", name="upperarm_l_amt_CTL")
    cmds.parent(good, grp)
    assert fs_app.find_misparented_ctrls() == []
    cmds.parent(good, world=True)                  # the CP-C repro shape
    hits = fs_app.find_misparented_ctrls()
    assert len(hits) == 1, hits
    assert hits[0]["node"].endswith("upperarm_l_amt_CTL")
    assert hits[0]["expected"] == "fab_armature_grp"


def test_misparented_tagged_ctrl_detected():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    ctrls_grp = nodes.ensure_controls_grp()        # makes rig_grp too
    ctl = cmds.createNode("transform", name="hand_l_CTL")
    cmds.parent(ctl, ctrls_grp)
    nodes.tag_ctrl(ctl, "fk_ctrl")
    assert fs_app.find_misparented_ctrls() == []
    cmds.parent(ctl, world=True)
    hits = fs_app.find_misparented_ctrls()
    assert len(hits) == 1, hits
    assert hits[0]["expected"] == "fab_controls_grp"


def test_misparented_finder_catches_ctrl_under_decoy_same_name_group():
    # Reviewer repro: a decoy transform elsewhere in the scene happens to
    # share the short name "fab_controls_grp" (or fab_armature_grp). A
    # short-name-only chain walk stops at the FIRST node with that short
    # name it meets while climbing, which is the decoy, not the real
    # canonical group under rig_grp — laundering a genuinely misparented
    # ctrl into a clean report.
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    nodes.ensure_controls_grp()                      # the real |rig_grp|fab_controls_grp
    decoy_parent = cmds.createNode("transform", name="decoy_parent")
    decoy_grp = cmds.createNode("transform", name="fab_controls_grp",
                                 parent=decoy_parent)
    ctl = cmds.createNode("transform", name="hand_l_CTL")
    cmds.parent(ctl, decoy_grp)
    nodes.tag_ctrl(ctl, "fk_ctrl")
    hits = fs_app.find_misparented_ctrls()
    assert len(hits) == 1, f"expected decoy-parented ctrl to be caught, got {hits}"


def test_misparented_finder_reports_one_hit_per_broken_subtree():
    # A 2-deep amt chain misparented at its root must yield exactly ONE
    # hit (the subtree root) — not one per descendant.
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    grp = cmds.createNode("transform", name="fab_armature_grp")
    root = cmds.createNode("transform", name="shoulder_l_amt_CTL", parent=grp)
    cmds.createNode("transform", name="elbow_l_amt_CTL", parent=root)
    assert fs_app.find_misparented_ctrls() == []
    cmds.parent(root, world=True)                     # whole subtree at world
    hits = fs_app.find_misparented_ctrls()
    assert len(hits) == 1, f"expected one hit for the subtree root, got {hits}"
    assert hits[0]["node"].endswith("shoulder_l_amt_CTL"), hits


def test_build_checks_reports_misparented_ctrl():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.framework.toolbar.ai_bridge import handlers
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    cmds.createNode("transform", name="fab_armature_grp")
    cmds.createNode("transform", name="upperarm_l_amt_CTL")  # born at world
    issues = handlers.dispatch("build_checks", {})["issues"]
    hit = [i for i in issues if i["key"] == "misparented_ctrls"]
    assert len(hit) == 1, issues
    assert hit[0]["severity"] == "error"
    assert hit[0]["fixable"] is False
    assert "upperarm_l_amt_CTL" in hit[0]["description"]
    assert "fab_armature_grp" in hit[0]["description"]


def test_renamed_joint_detected():
    # Component construction: the harness's own component_details tests
    # (test_ai_bridge_maya.py) construct components with an EMPTY joints
    # list (nodes.create_component_node("test_C0", "test_component", [],
    # "", "md", {}, {})) — there is no existing idiom that wires a
    # component to real, named joints. Per the plan's fallback, this
    # calls nodes.create_component_node directly with real joint
    # transforms as the `joints` list (joint_names defaults to mirror
    # `joints`, and create_component_node connects joints[] message-multi
    # for any joint that cmds.objExists at construction time).
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    j1 = cmds.createNode("joint", name="upperarm_l")
    j2 = cmds.createNode("joint", name="lowerarm_l", parent=j1)
    nodes.create_component_node(
        "arm_l_C0", "test_component", [j1, j2], "", "l", {}, {})
    assert fs_app.find_renamed_component_joints() == []
    cmds.rename("lowerarm_l", "lowerarm_l_RENAMED")
    hits = fs_app.find_renamed_component_joints()
    assert len(hits) == 1, hits
    assert hits[0]["recorded"] == "lowerarm_l"
    assert hits[0]["live"] == "lowerarm_l_RENAMED"


def test_renamed_finder_skips_length_mismatch():
    # component missing a joint entirely (orphaned_modules' territory):
    # live/recorded lengths differ -> skip, not a false rename report.
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import fs_app, nodes
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    j1 = cmds.createNode("joint", name="upperarm_l")
    j2 = cmds.createNode("joint", name="lowerarm_l", parent=j1)
    nodes.create_component_node(
        "arm_l_C0", "test_component", [j1, j2], "", "l", {}, {})
    cmds.delete(j2)
    assert fs_app.find_renamed_component_joints() == []


def test_build_checks_reports_renamed_component_joint():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.framework.toolbar.ai_bridge import handlers
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    j1 = cmds.createNode("joint", name="upperarm_l")
    j2 = cmds.createNode("joint", name="lowerarm_l", parent=j1)
    nodes.create_component_node(
        "arm_l_C0", "test_component", [j1, j2], "", "l", {}, {})
    cmds.rename("lowerarm_l", "lowerarm_l_RENAMED")
    issues = handlers.dispatch("build_checks", {})["issues"]
    hit = [i for i in issues if i["key"] == "renamed_component_joints"]
    assert len(hit) == 1, issues
    assert hit[0]["severity"] == "warning"
    assert hit[0]["fixable"] is False
    assert "lowerarm_l" in hit[0]["description"]
    assert "lowerarm_l_RENAMED" in hit[0]["description"]


def main():
    import maya.standalone
    maya.standalone.initialize(name="python")

    check("test_misparented_finder_clean_scene_is_clean",
          test_misparented_finder_clean_scene_is_clean)
    check("test_misparented_amt_ctrl_detected",
          test_misparented_amt_ctrl_detected)
    check("test_misparented_tagged_ctrl_detected",
          test_misparented_tagged_ctrl_detected)
    check("test_misparented_finder_catches_ctrl_under_decoy_same_name_group",
          test_misparented_finder_catches_ctrl_under_decoy_same_name_group)
    check("test_misparented_finder_reports_one_hit_per_broken_subtree",
          test_misparented_finder_reports_one_hit_per_broken_subtree)
    check("test_build_checks_reports_misparented_ctrl",
          test_build_checks_reports_misparented_ctrl)
    check("test_renamed_joint_detected", test_renamed_joint_detected)
    check("test_renamed_finder_skips_length_mismatch",
          test_renamed_finder_skips_length_mismatch)
    check("test_build_checks_reports_renamed_component_joint",
          test_build_checks_reports_renamed_component_joint)

    if FAILURES:
        print(f"STRUCTURAL CHECKS MAYA TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("STRUCTURAL CHECKS MAYA TESTS: OK")


if __name__ == "__main__":
    main()
