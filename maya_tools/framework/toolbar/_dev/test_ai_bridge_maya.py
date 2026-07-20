# Python/maya_tools/framework/toolbar/_dev/test_ai_bridge_maya.py
"""maya.standalone tests for the Fabricator public read-only introspect
contract (maya_tools/rigging/fabricator/introspect.py).

Covers empty-scene contracts + registry-only paths: fabricator_status()
on a scene with no registry, the NoRegistryError raise paths for
rig_snapshot() and component_details() when there is nothing to read,
and a minimal synthesized registry + component (stale is_built flag,
no rig_grp) to prove fabricator_status() never mutates the scene where
ui.state.detect_mode() legitimately self-heals. Deep coverage of a real,
BUILT rig is live-checkpoint territory, not this offscreen-adjacent
standalone file.

Run:
PYTHONNOUSERSITE=1 "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" maya_tools/framework/toolbar/_dev/test_ai_bridge_maya.py
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


def test_status_empty_scene():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import introspect
    from maya_tools.rigging.fabricator.ui import state as ui_state
    cmds.file(new=True, force=True)
    st = introspect.fabricator_status()
    assert st["mode"] == "empty", st
    assert st["has_registry"] is False, st
    assert st["fabricator_version"] == "", st
    # Drift guard: on an empty scene detect_mode() is pure, so calling it
    # after is safe — it must agree with our readonly mode detection.
    assert ui_state.detect_mode() == st["mode"], st


def test_snapshot_empty_scene_raises():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import introspect
    cmds.file(new=True, force=True)
    try:
        introspect.rig_snapshot()
    except introspect.NoRegistryError:
        return
    raise AssertionError("rig_snapshot on empty scene must raise NoRegistryError")


def test_component_details_missing_raises():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import introspect
    cmds.file(new=True, force=True)
    try:
        introspect.component_details("nope_C0")
    except introspect.NoRegistryError:
        return
    raise AssertionError("must raise NoRegistryError with no registry")


def test_status_pure_even_with_stale_built_flags():
    # Synthesize the exact condition ui.state.detect_mode() self-heals
    # (is_built=True, no rig_grp): a component network node with a stale
    # built flag and nothing else. introspect must report the staleness
    # via stale_built_flags WITHOUT writing is_built back to False.
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import introspect, nodes
    from maya_tools.rigging.fabricator.ui import state as ui_state
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    cnode = nodes.create_component_node(
        "test_C0", "test_component", [], "", "md", {}, {})
    nodes.set_built(cnode, True)
    assert not cmds.objExists("rig_grp")

    st = introspect.fabricator_status()
    assert st["mode"] in ("skeleton", "empty"), st
    assert st["stale_built_flags"] is True, st
    assert nodes.is_built(cnode) is True, "introspect must not mutate is_built"

    # Drift guard, LAST on purpose: the real detect_mode() self-heals this
    # exact scene (that's the whole divergence under test), so calling it
    # mutates is_built — must run after every pure-read assertion above.
    real_mode = ui_state.detect_mode()
    assert real_mode == st["mode"], (real_mode, st)
    assert nodes.is_built(cnode) is False, (
        "detect_mode() should have healed the stale flag; if this scene "
        "didn't genuinely hit the divergent branch, the test proves nothing")


def test_build_report_roundtrip_and_overwrite():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import build_report, nodes
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    build_report.write_report(action='build', outcome='failed',
                              components=[{'id': 'spine_C0', 'status': 'error'}],
                              error_tail='RuntimeError: boom')
    rep = build_report.read_report()
    assert rep['action'] == 'build' and rep['outcome'] == 'failed', rep
    assert rep['error_tail'] == 'RuntimeError: boom', rep
    build_report.write_report(action='build', outcome='ok', components=[])
    rep2 = build_report.read_report()
    assert rep2['outcome'] == 'ok', rep2          # last-only: overwritten


def test_build_report_size_cap():
    import json
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import build_report, nodes
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    # error_tail is pre-trimmed to 4000 chars BEFORE the size check, so a
    # huge error_tail alone can't push the payload past MAX_REPORT_BYTES.
    # Drive the cap with a huge components list instead so the
    # truncated=True branch genuinely executes (not vacuously).
    huge_components = [{'id': f'comp_{i}', 'status': 'ok', 'note': 'x' * 40}
                       for i in range(5000)]
    build_report.write_report(action='build', outcome='failed',
                              components=huge_components,
                              error_tail='x' * 200_000)
    rep = build_report.read_report()
    assert len(json.dumps(rep)) <= build_report.MAX_REPORT_BYTES, 'cap not enforced'
    # Prove the truncation branch actually ran (components genuinely cut),
    # not a vacuous pass via the unreadable fallback.
    assert rep.get('truncated') is True, rep
    assert len(rep['components']) <= 20, rep


def test_build_report_none_when_absent():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import build_report
    cmds.file(new=True, force=True)
    assert build_report.read_report() is None


def test_build_report_never_raises_without_registry():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import build_report
    cmds.file(new=True, force=True)
    build_report.write_report(action='build', outcome='ok', components=[])  # must not raise


def test_reporting_context_manager():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import build_report, nodes
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    with build_report.reporting('build') as rep:
        rep.components = [{'id': 'a_C0', 'status': 'ok'}]
    r = build_report.read_report()
    assert r['outcome'] == 'ok' and r['components'] == [{'id': 'a_C0', 'status': 'ok'}], r
    try:
        with build_report.reporting('unbuild'):
            raise RuntimeError('boom')
    except RuntimeError:
        pass
    else:
        raise AssertionError('reporting must re-raise')
    r2 = build_report.read_report()
    assert r2['action'] == 'unbuild' and r2['outcome'] == 'failed', r2
    assert 'boom' in r2['error_tail'], r2


def test_write_report_survives_undo():
    # write_report deliberately keeps its attr write off the undo queue
    # (stateWithoutFlush toggled around it) — no sequence of undos should
    # ever remove the stamped report, because it never entered the queue
    # in the first place.
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import build_report, nodes
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    cmds.undoInfo(state=True)  # ensure undo is on in standalone
    build_report.write_report(action='build', outcome='ok', components=[])
    cmds.polyCube()            # a real undoable command on the queue
    cmds.undo()                # undoes the cube...
    assert build_report.read_report() is not None      # ...report intact
    try:
        cmds.undo()            # even another undo must not eat the report
    except RuntimeError:
        pass                   # queue may be empty - fine
    rep = build_report.read_report()
    assert rep is not None and rep['outcome'] == 'ok', rep


def test_read_report_unreadable_degrades():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import build_report, nodes
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    build_report.write_report(action='build', outcome='ok', components=[])  # ensures attr exists
    cmds.setAttr(f"{nodes.get_registry()}.{build_report.ATTR}", '{"torn', type='string')
    rep = build_report.read_report()
    assert rep['outcome'] == 'unreadable' and rep['raw_prefix'].startswith('{"torn'), rep


# ─────────────────────────────────────────────
# ai_bridge/handlers.py — the read-only op registry
# ─────────────────────────────────────────────

def test_dispatch_unknown_op():
    from maya_tools.framework.toolbar.ai_bridge import handlers
    from maya_tools.framework.toolbar.ai_bridge.protocol import BridgeError
    try:
        handlers.dispatch("nope_this_op_does_not_exist", {})
    except BridgeError as exc:
        assert exc.code == "unknown_op", exc.code
        # M4: the valid op set is echoed in-band so a typo'd op name is
        # self-recoverable without a second round trip.
        message = str(exc)
        assert "Valid ops:" in message, message
        assert "hello" in message, message
        return
    raise AssertionError("dispatch must raise BridgeError for an unknown op")


def test_registry_pins_exact_op_set():
    # Pins the exact 16-op surface AND runs a mechanical mutation guard
    # over the whole module source. The guard is deliberately dumb —
    # plain substring search, not an AST-aware "is this really a call"
    # check — so it can false-positive on a token that only appears in
    # a comment/string, and it cannot catch mutation hidden behind some
    # cleverer indirection (a helper function called from handlers.py
    # that itself mutates elsewhere in the call graph is outside this
    # guard's reach entirely — that transitive-call-graph risk is the
    # review layer's job, not this test's). What it buys: if anyone
    # ever pastes a setAttr/createNode/delete/om2/xform/duplicate/etc.
    # directly into handlers.py, this test fails loud on the very next
    # run.
    import inspect
    import re
    from maya_tools.framework.toolbar.ai_bridge import handlers

    expected_ops = {
        "hello", "scene_summary", "node_details", "viewport_screenshot",
        "fabricator_status", "describe_rig", "component_details",
        "rig_binding", "build_checks", "validate_blueprint", "scene_report",
        "build_report", "diagnostics", "read_doc", "list_docs", "get_skill",
    }
    assert set(handlers.OPS) == expected_ops, set(handlers.OPS)
    assert all(callable(v) for v in handlers.OPS.values())

    src = inspect.getsource(handlers)

    # Plain substring bans — no legitimate read-only form of any of
    # these exists in this module, so a bare "is it present at all"
    # check is fine here (unlike cmds.file/cmds.xform/etc. below, which
    # DO have a legitimate query form and need call-level scrutiny).
    forbidden_substrings = (
        "setAttr", "createNode", "cmds.delete", "connectAttr",
        "disconnectAttr", "addAttr", "cmds.rename", "cmds.parent",
        "cmds.move", "cmds.rotate", "cmds.scale", "cmds.duplicate",
        "cmds.group", "cmds.instance", "cmds.setKeyframe",
        "cmds.makeIdentity", "cmds.undoInfo", "mel.eval", "maya.mel",
    )
    for token in forbidden_substrings:
        assert token not in src, f"forbidden mutating token found: {token!r}"

    # Identifier-style tokens need a word-boundary match, not a bare
    # substring: a plain "om2" ban would false-positive on an ordinary
    # identifier like zoom2/room2/custom2 that merely contains it. None
    # of these currently appear in handlers.py at all, boundaried or not.
    forbidden_identifiers = ("om2", "OpenMaya", "MPlug", "doIt")
    for token in forbidden_identifiers:
        assert not re.search(rf"\b{re.escape(token)}\b", src), (
            f"forbidden mutating identifier found: {token!r}")

    # These four cmds calls all have a legitimate READ-ONLY query=True
    # form (cmds.file — scene_path/unsaved_changes in _scene_summary;
    # cmds.xform query is THE standard world-space read idiom;
    # cmds.namespace and cmds.lockNode both have query forms too)
    # alongside a mutating form, so a bare substring ban would also
    # outlaw the legitimate use — same treatment for all four: pull each
    # call's own argument text and require query=True, forbid a
    # save-style write. Only cmds.file( is actually used in handlers.py
    # today; the other three are future-proofing so the next read op
    # doesn't force a guard rewrite.
    query_gated_calls = ("cmds.file(", "cmds.xform(", "cmds.namespace(",
                        "cmds.lockNode(")
    for cmds_call in query_gated_calls:
        call_args = [seg.split(")")[0] for seg in src.split(cmds_call)[1:]]
        for args in call_args:
            assert "query=True" in args, (
                f"{cmds_call} call missing query=True — looks like a "
                f"write: {args!r}")
            assert "save" not in args, (
                f"{cmds_call} call must not save: {args!r}")

    # cmds.file( is the one of the four actually in use today — pin its
    # call count too, so a silent drop to zero (scene_summary quietly
    # losing its scene_path/unsaved_changes reads) doesn't hide behind
    # an emptily-passing loop above.
    assert src.count("cmds.file(") == 2, (
        "expected exactly 2 cmds.file( calls (scene_path + "
        "unsaved_changes in _scene_summary); a different count means "
        "scene_summary's read shape changed — update this pin")

    # cmds.select is the ONE documented exception: scene_report saves and
    # restores the user's selection around scene_cleanup_app's
    # select_mesh_artifacts(), which replaces the live selection as a
    # side effect (see handlers.py's module docstring and the comment in
    # _scene_report). That restore is exactly two calls — reselect the
    # saved selection, or clear() when there was none. Pin the count so
    # a THIRD cmds.select call anywhere else in the file fails loud.
    assert src.count("cmds.select(") == 2, (
        "expected exactly 2 cmds.select( calls (the scene_report "
        "save/restore pair around select_mesh_artifacts); a different "
        "count means a new selection mutation crept into handlers.py")


def test_scene_summary_empty():
    import maya.cmds as cmds
    from maya_tools.framework.toolbar.ai_bridge import handlers
    cmds.file(new=True, force=True)
    result = handlers.dispatch("scene_summary", {})
    assert result["scene_path"] == "", result
    assert isinstance(result["unsaved_changes"], bool), result
    assert result["maya_version"], result
    assert result["toolset_version"], result
    assert result["os"], result
    assert isinstance(result["counts"], dict) and result["counts"], result


def test_fabricator_ops_not_found_on_empty():
    import maya.cmds as cmds
    from maya_tools.framework.toolbar.ai_bridge import handlers
    from maya_tools.framework.toolbar.ai_bridge.protocol import BridgeError
    cmds.file(new=True, force=True)

    not_found_cases = (
        ("describe_rig", {}),
        ("component_details", {"component_id": "whatever_C0"}),
        ("validate_blueprint", {}),
        ("build_report", {}),
    )
    for op, params in not_found_cases:
        try:
            handlers.dispatch(op, params)
        except BridgeError as exc:
            assert exc.code == "not_found", (op, exc.code)
        else:
            raise AssertionError(f"{op} must raise not_found on an empty scene")

    status = handlers.dispatch("fabricator_status", {})
    assert status["mode"] == "empty", status


def test_node_details():
    import maya.cmds as cmds
    from maya_tools.framework.toolbar.ai_bridge import handlers
    from maya_tools.framework.toolbar.ai_bridge.protocol import BridgeError
    cmds.file(new=True, force=True)

    try:
        handlers.dispatch("node_details", {"node": "does_not_exist_at_all"})
    except BridgeError as exc:
        assert exc.code == "not_found", exc.code
    else:
        raise AssertionError("node_details must raise not_found for a missing node")

    joint = cmds.joint(name="ai_bridge_test_joint")

    # attrs elements must themselves be strings — attrs=['tx', ['nested']]
    # must be rejected up front as bad_params, not crash uncaught trying
    # to use a list as a dict key inside the getAttr probe loop.
    try:
        handlers.dispatch("node_details",
                          {"node": joint, "attrs": ["tx", ["nested"]]})
    except BridgeError as exc:
        assert exc.code == "bad_params", exc.code
    else:
        raise AssertionError(
            "node_details must reject a non-string attrs element")

    result = handlers.dispatch("node_details", {"node": joint})
    assert result["type"] == "joint", result
    assert "parent" in result, result
    assert result["attrs_truncated"] is False, result

    result2 = handlers.dispatch("node_details", {"node": joint, "attrs": ["tx"]})
    assert result2["attr_values"]["tx"] == 0.0, result2
    assert result2["attrs_truncated"] is False, result2


def test_component_details_bogus_id_on_registry_scene():
    # test_fabricator_ops_not_found_on_empty already covers the
    # NoRegistryError branch (no registry at all). This covers the
    # OTHER not_found path in _component_details: a real registry that
    # simply has no component with the given id (introspect raises
    # KeyError there). Also pins the exc.args[0] unwrap in the handler
    # — KeyError's own str() re-reprs its single arg (adds a stray pair
    # of quotes around the whole message), so a naive str(exc) would
    # leak that into the bridge's error text.
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.framework.toolbar.ai_bridge import handlers
    from maya_tools.framework.toolbar.ai_bridge.protocol import BridgeError
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")

    try:
        handlers.dispatch("component_details", {"component_id": "nope_C0"})
    except BridgeError as exc:
        assert exc.code == "not_found", exc.code
        message = str(exc)
        assert not message.startswith('"'), message
        assert not message.endswith('"'), message
    else:
        raise AssertionError(
            "component_details must raise not_found for an unknown id "
            "on a scene that DOES have a registry")


def test_describe_rig_and_component_details_bounded_lists():
    # describe_rig's skeleton.joints/components_live and
    # component_details' joints/joint_names are the largest lists on
    # the whole op surface (ribbon-limb rigs especially) — this pins
    # that the HANDLER wraps them in the same {'items','truncated'}
    # envelope every other list on this surface gets, without touching
    # introspect's own return shape.
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.framework.toolbar.ai_bridge import handlers
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")
    nodes.create_component_node(
        "test_C0", "test_component", [], "", "md", {}, {})

    rig = handlers.dispatch("describe_rig", {})
    joints = rig["skeleton"]["joints"]
    assert set(joints) == {"items", "truncated"}, joints
    components_live = rig["components_live"]
    assert set(components_live) == {"items", "truncated"}, components_live

    details = handlers.dispatch(
        "component_details", {"component_id": "test_C0"})
    assert set(details["joints"]) == {"items", "truncated"}, details["joints"]
    assert set(details["joint_names"]) == {"items", "truncated"}, (
        details["joint_names"])


def test_build_checks_shape_on_registry_scene():
    import maya.cmds as cmds
    from maya_tools.rigging.fabricator import nodes
    from maya_tools.framework.toolbar.ai_bridge import handlers
    cmds.file(new=True, force=True)
    nodes.create_registry("test_bp")

    result = handlers.dispatch("build_checks", {})
    issues = result["issues"]
    assert isinstance(issues, list), result
    for entry in issues:
        for key in ("key", "title", "description", "severity", "fixable",
                    "skippable"):
            assert key in entry, (key, entry)
        assert entry["severity"] in ("error", "warning"), entry


def test_rig_binding_empty():
    import maya.cmds as cmds
    from maya_tools.framework.toolbar.ai_bridge import handlers
    cmds.file(new=True, force=True)
    result = handlers.dispatch("rig_binding", {})
    assert result == {"bindings": []}, result


def test_scene_report_shape_empty_scene():
    # Pins I3 (mesh_artifacts.meshes_submitted, renamed from
    # meshes_scanned) and I4 (duplicate_transforms wrapped in
    # _bounded_mapping's {'items','truncated','total'} envelope).
    import maya.cmds as cmds
    from maya_tools.framework.toolbar.ai_bridge import handlers
    cmds.file(new=True, force=True)
    result = handlers.dispatch("scene_report", {})

    dup = result["duplicate_transforms"]
    assert set(dup) == {"items", "truncated", "total"}, dup
    assert dup["items"] == {} and dup["total"] == 0, dup
    assert dup["truncated"] is False, dup

    artifacts = result["mesh_artifacts"]
    for key in ("non_manifold_edges", "non_manifold_verts", "lamina_faces",
                "meshes_submitted", "truncated"):
        assert key in artifacts, (key, artifacts)
    assert artifacts["meshes_submitted"] == 0, artifacts
    assert artifacts["truncated"] is False, artifacts


def test_read_doc_rejects_traversal():
    from maya_tools.framework.toolbar.ai_bridge import handlers
    from maya_tools.framework.toolbar.ai_bridge.protocol import BridgeError
    for name in ("../CLAUDE", "a/b", "..\\x", "concepts.md"):
        try:
            handlers.dispatch("read_doc", {"name": name})
        except BridgeError as exc:
            assert exc.code in ("bad_params", "not_found"), (name, exc.code)
        else:
            raise AssertionError(f"read_doc must reject {name!r}")

    # Windows drive-letter containment bypass (C1): 'C:foo' is a
    # DRIVE-RELATIVE path, not '..'-style traversal — Path(_DOCS_DIR) /
    # 'C:foo.md' resolves to C:\foo.md, entirely outside _DOCS_DIR. The
    # ':' ban in the character guard catches these before the
    # containment check even runs, so all three must come back
    # bad_params specifically (not_found here would mean the guard let
    # an escaping name through to the filesystem check).
    for name in ("C:foo", "E:bar", "z:x"):
        try:
            handlers.dispatch("read_doc", {"name": name})
        except BridgeError as exc:
            assert exc.code == "bad_params", (name, exc.code)
        else:
            raise AssertionError(f"read_doc must reject {name!r}")


def test_read_doc_returns_markdown():
    # The context-pack docs are real, substantial prose (Task 7) - pin that
    # read_doc actually returns each one's full markdown body, not a stub or
    # a truncated read.
    from maya_tools.framework.toolbar.ai_bridge import handlers
    for stem in ("concepts", "etiquette", "glossary", "toolset",
                "troubleshooting"):
        result = handlers.dispatch("read_doc", {"name": stem})
        assert result["name"] == stem, result
        markdown = result["markdown"]
        assert isinstance(markdown, str), (stem, type(markdown))
        assert len(markdown) > 500, (stem, len(markdown))


def test_read_doc_unknown_still_not_found():
    # M2 fold-in: an unknown doc name's not_found message must point the
    # caller at list_docs so a typo'd name is self-recoverable in one more
    # round trip, same shape as test_dispatch_unknown_op's op-name guidance.
    from maya_tools.framework.toolbar.ai_bridge import handlers
    from maya_tools.framework.toolbar.ai_bridge.protocol import BridgeError
    try:
        handlers.dispatch("read_doc", {"name": "nope"})
    except BridgeError as exc:
        assert exc.code == "not_found", exc.code
        assert "list_docs" in str(exc), str(exc)
    else:
        raise AssertionError(
            "read_doc must raise not_found for an unknown doc name")


def test_list_docs_missing_dir_is_empty():
    # This suite runs against a real checkout where maya_tools/docs/assistant
    # now genuinely exists with 5 docs (Task 7) - so the missing-dir
    # CONTRACT (absent dir -> {"docs": []}) can no longer be exercised by
    # just calling list_docs against whatever happens to be on disk. Point
    # handlers._DOCS_DIR at a path that provably does not exist instead,
    # monkeypatching the module attribute and restoring it in `finally`
    # regardless of outcome, so this test keeps proving the contract itself
    # rather than an accident of the repo's current directory layout.
    import shutil
    import tempfile
    from pathlib import Path
    from maya_tools.framework.toolbar.ai_bridge import handlers

    tmp_root = Path(tempfile.mkdtemp(prefix="ai_bridge_missing_docs_"))
    missing_dir = tmp_root / "assistant_docs_that_do_not_exist"
    assert not missing_dir.exists()

    original_docs_dir = handlers._DOCS_DIR
    handlers._DOCS_DIR = missing_dir
    try:
        result = handlers.dispatch("list_docs", {})
        assert result == {"docs": []}, result
    finally:
        handlers._DOCS_DIR = original_docs_dir
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_get_skill_returns_markdown():
    # Pure file read of the bundled fabricator-assistant skill (Task 8):
    # no scene setup needed, just dispatch and check the shape.
    from maya_tools.framework.toolbar.ai_bridge import handlers
    result = handlers.dispatch("get_skill", {})
    assert result["name"] == "fabricator-assistant", result
    markdown = result["markdown"]
    assert isinstance(markdown, str), (type(markdown), result)
    assert len(markdown) > 500, len(markdown)
    assert "fabricator-assistant" in markdown, markdown


def test_list_docs_returns_the_five_docs():
    from maya_tools.framework.toolbar.ai_bridge import handlers
    result = handlers.dispatch("list_docs", {})
    assert result == {"docs": sorted(["concepts", "etiquette", "glossary",
                                      "toolset", "troubleshooting"])}, result


def test_viewport_screenshot_batch_guard():
    import maya.cmds as cmds
    from maya_tools.framework.toolbar.ai_bridge import handlers
    from maya_tools.framework.toolbar.ai_bridge.protocol import BridgeError
    assert cmds.about(batch=True), "this suite always runs in standalone/batch mode"
    try:
        handlers.dispatch("viewport_screenshot", {})
    except BridgeError as exc:
        assert exc.code == "not_found", exc.code
    else:
        raise AssertionError("viewport_screenshot must raise not_found in batch mode")


def test_diagnostics_shape_empty_scene():
    import maya.cmds as cmds
    from maya_tools.framework.toolbar.ai_bridge import handlers
    cmds.file(new=True, force=True)
    result = handlers.dispatch("diagnostics", {})
    for key in ("toolset_version", "maya_version", "os", "mode",
                "stale_built_flags", "has_registry", "rig_label",
                "check_summary", "last_build"):
        assert key in result, (key, result)
    assert result["mode"] == "empty", result
    assert result["last_build"] is None, result
    assert isinstance(result["check_summary"]["errors"], int), result
    assert isinstance(result["check_summary"]["warnings"], int), result


def main():
    import maya.standalone
    maya.standalone.initialize(name="python")

    check("test_status_empty_scene", test_status_empty_scene)
    check("test_snapshot_empty_scene_raises", test_snapshot_empty_scene_raises)
    check("test_component_details_missing_raises", test_component_details_missing_raises)
    check("test_status_pure_even_with_stale_built_flags",
          test_status_pure_even_with_stale_built_flags)
    check("test_build_report_roundtrip_and_overwrite",
          test_build_report_roundtrip_and_overwrite)
    check("test_build_report_size_cap", test_build_report_size_cap)
    check("test_build_report_none_when_absent", test_build_report_none_when_absent)
    check("test_build_report_never_raises_without_registry",
          test_build_report_never_raises_without_registry)
    check("test_reporting_context_manager", test_reporting_context_manager)
    check("test_write_report_survives_undo", test_write_report_survives_undo)
    check("test_read_report_unreadable_degrades", test_read_report_unreadable_degrades)

    check("test_dispatch_unknown_op", test_dispatch_unknown_op)
    check("test_registry_pins_exact_op_set", test_registry_pins_exact_op_set)
    check("test_scene_summary_empty", test_scene_summary_empty)
    check("test_fabricator_ops_not_found_on_empty",
          test_fabricator_ops_not_found_on_empty)
    check("test_node_details", test_node_details)
    check("test_component_details_bogus_id_on_registry_scene",
          test_component_details_bogus_id_on_registry_scene)
    check("test_describe_rig_and_component_details_bounded_lists",
          test_describe_rig_and_component_details_bounded_lists)
    check("test_build_checks_shape_on_registry_scene",
          test_build_checks_shape_on_registry_scene)
    check("test_rig_binding_empty", test_rig_binding_empty)
    check("test_scene_report_shape_empty_scene", test_scene_report_shape_empty_scene)
    check("test_read_doc_rejects_traversal", test_read_doc_rejects_traversal)
    check("test_read_doc_returns_markdown", test_read_doc_returns_markdown)
    check("test_read_doc_unknown_still_not_found", test_read_doc_unknown_still_not_found)
    check("test_list_docs_missing_dir_is_empty", test_list_docs_missing_dir_is_empty)
    check("test_list_docs_returns_the_five_docs", test_list_docs_returns_the_five_docs)
    check("test_get_skill_returns_markdown", test_get_skill_returns_markdown)
    check("test_viewport_screenshot_batch_guard", test_viewport_screenshot_batch_guard)
    check("test_diagnostics_shape_empty_scene", test_diagnostics_shape_empty_scene)

    if FAILURES:
        print(f"AI BRIDGE MAYA TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("AI BRIDGE MAYA TESTS: OK")


if __name__ == "__main__":
    main()
