"""Tests for fabricator_mcp.server's MCP tool layer.

No real Maya/socket involved: fabricator_mcp.server.bridge_client.call is
monkeypatched with a fake, and tool functions are called directly (they're
plain, unwrapped callables — the FastMCP `@mcp.tool()`/`@mcp.resource()`
decorators register a tool/resource as a side effect but return the
original function unchanged) or introspected via the FastMCP app's
tool manager (`mcp._tool_manager.list_tools()`, confirmed sync in the
installed mcp==1.28.1 against which this was written).
"""
__author__ = "Adrian Melian"

from mcp.server.fastmcp import Image

from fabricator_mcp import bridge_client, server
from fabricator_mcp.bridge_client import (
    BridgeDownError,
    BridgeError,
    BridgeOpError,
    ProtocolMismatchError,
)

EXPECTED_TOOL_NAMES = {
    "get_scene_summary",
    "get_node_details",
    "get_viewport_screenshot",
    "get_fabricator_status",
    "describe_rig",
    "get_component_details",
    "get_rig_binding",
    "run_build_checks",
    "validate_blueprint",
    "get_scene_report",
    "get_build_report",
    "read_doc",
    "get_skill",
    "report_bug",
}


def test_every_expected_tool_is_registered():
    registered = {tool.name for tool in server.mcp._tool_manager.list_tools()}
    assert registered == EXPECTED_TOOL_NAMES


def test_server_carries_behavioral_instructions():
    # FastMCP's `instructions=` is delivered to every connected client at
    # session initialization (threaded into
    # InitializationOptions.instructions by
    # mcp.server.lowlevel.server.Server.create_initialization_options),
    # without the client needing to know to call read_doc/read the
    # etiquette resource first — CP2's root cause was exactly that: the
    # contract only lived in a doc the AI never read. FastMCP.instructions
    # is a thin read-only proxy onto the same value stored on the
    # underlying lowlevel Server (confirmed against the installed
    # mcp==1.28.1: server.mcp.instructions and
    # server.mcp._mcp_server.instructions are the identical string passed
    # to the FastMCP constructor). Confirm it's wired through and carries
    # the hard rules from CP2: never offer to mutate the scene (fix via
    # the user's own Script Editor), and undo to the pre-failure state
    # before diagnosing a build/unbuild error.
    instructions = server.mcp.instructions

    assert isinstance(instructions, str)
    assert instructions.strip()
    assert instructions is server.mcp._mcp_server.instructions

    lowered = instructions.lower()
    for phrase in ("read-only", "cannot", "script editor", "undo", "skill"):
        assert phrase in lowered, f"instructions missing required phrase: {phrase!r}"


def test_every_tool_docstring_carries_the_read_only_etiquette_line():
    by_name = {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}
    for name in EXPECTED_TOOL_NAMES:
        doc = by_name[name].description or ""
        assert "READ-ONLY" in doc, f"{name} is missing the read-only etiquette line"


def test_relay_tool_returns_the_fake_calls_dict(monkeypatch):
    fake_result = {"maya_version": "2025", "fabricator_loaded": True}
    monkeypatch.setattr(bridge_client, "call", lambda op, params=None, *, port=6292: fake_result)

    result = server.get_fabricator_status()

    assert result == fake_result


def test_get_skill_tool_relays_bridge_op(monkeypatch):
    fake_skill = {"name": "fabricator-assistant", "markdown": "# Fabricator Assistant\n..."}
    seen = {}

    def fake_call(op, params=None, *, port=6292):
        seen["op"] = op
        return fake_skill

    monkeypatch.setattr(bridge_client, "call", fake_call)

    result = server.get_skill()

    assert seen["op"] == "get_skill"
    assert result == fake_skill


def test_skill_resource_relays_bridge_op_and_returns_markdown(monkeypatch):
    fake_skill = {"name": "fabricator-assistant", "markdown": "# Fabricator Assistant\n..."}

    monkeypatch.setattr(bridge_client, "call", lambda op, params=None, *, port=6292: fake_skill)

    result = server.skill_resource()

    assert result == fake_skill["markdown"]


def test_skill_resource_bridge_down_returns_friendly_text(monkeypatch):
    def fake_call(op, params=None, *, port=6292):
        raise BridgeDownError("Maya isn't listening. In Maya, open the DevBot toolbar > Start.")

    monkeypatch.setattr(bridge_client, "call", fake_call)

    result = server.skill_resource()

    assert isinstance(result, str)
    assert "DevBot toolbar" in result


def test_node_details_relays_optional_attrs(monkeypatch):
    seen = {}

    def fake_call(op, params=None, *, port=6292):
        seen["op"] = op
        seen["params"] = params
        return {"node": params["node"]}

    monkeypatch.setattr(bridge_client, "call", fake_call)

    result = server.get_node_details("|group1|pCube1", attrs=["translateX"])

    assert seen["op"] == "node_details"
    assert seen["params"] == {"node": "|group1|pCube1", "attrs": ["translateX"]}
    assert result == {"node": "|group1|pCube1"}


def test_bridge_down_error_becomes_friendly_text_not_traceback(monkeypatch):
    def fake_call(op, params=None, *, port=6292):
        raise BridgeDownError(
            "Maya isn't listening. In Maya, open the DevBot toolbar > "
            "Connect your AI > Start. (Then retry.)"
        )

    monkeypatch.setattr(bridge_client, "call", fake_call)

    result = server.get_fabricator_status()

    assert isinstance(result, str)
    assert "DevBot toolbar" in result
    assert "Traceback" not in result


def test_bridge_op_error_becomes_friendly_text(monkeypatch):
    def fake_call(op, params=None, *, port=6292):
        raise BridgeOpError("not_found", "no Fabricator rig in the scene")

    monkeypatch.setattr(bridge_client, "call", fake_call)

    result = server.describe_rig()

    assert isinstance(result, str)
    assert "not_found" in result
    assert "no Fabricator rig in the scene" in result


def test_protocol_mismatch_error_becomes_friendly_text(monkeypatch):
    def fake_call(op, params=None, *, port=6292):
        raise ProtocolMismatchError("Protocol mismatch: update whichever side is behind.")

    monkeypatch.setattr(bridge_client, "call", fake_call)

    result = server.get_scene_summary()

    assert isinstance(result, str)
    assert "Protocol mismatch" in result


def test_viewport_screenshot_returns_mcp_image(monkeypatch):
    import base64

    png_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    monkeypatch.setattr(
        bridge_client,
        "call",
        lambda op, params=None, *, port=6292: {
            "png_base64": base64.b64encode(png_bytes).decode("ascii"),
            "width": params["width"],
        },
    )

    result = server.get_viewport_screenshot(width=640)

    assert isinstance(result, Image)
    content = result.to_image_content()
    assert content.mimeType == "image/png"


def test_viewport_screenshot_bridge_down_returns_friendly_text(monkeypatch):
    def fake_call(op, params=None, *, port=6292):
        raise BridgeDownError("Maya isn't listening. In Maya, open the DevBot toolbar > Start.")

    monkeypatch.setattr(bridge_client, "call", fake_call)

    result = server.get_viewport_screenshot()

    assert isinstance(result, str)
    assert "DevBot toolbar" in result


def test_viewport_screenshot_missing_png_base64_returns_friendly_text(monkeypatch):
    # Batch-mode / no-viewport case: the bridge answers ok:true but the
    # result dict has no image data.
    monkeypatch.setattr(bridge_client, "call", lambda op, params=None, *, port=6292: {})

    result = server.get_viewport_screenshot()

    assert isinstance(result, str)
    assert "no image data" in result


def test_bare_bridge_error_becomes_friendly_text_not_traceback(monkeypatch):
    # A bare BridgeError (not one of the three named subclasses) is what
    # bridge_client.call raises for malformed/protocol-violating replies:
    # non-JSON/non-UTF-8 body, non-dict, ok:true missing result, ok:false
    # missing code/message, or a missing 'ok' field. _relay must catch
    # this too, not just the three named subclasses, or it escapes as an
    # unhandled exception from the tool call.
    def fake_call(op, params=None, *, port=6292):
        raise BridgeError("Bridge sent a malformed reply: not valid JSON.")

    monkeypatch.setattr(bridge_client, "call", fake_call)

    result = server.get_fabricator_status()

    assert isinstance(result, str)
    assert "malformed reply" in result
    assert "Traceback" not in result


def test_report_bug_empty_attempted_solutions_returns_troubleshoot_message(monkeypatch):
    def fake_call(op, params=None, *, port=6292):
        raise BridgeDownError("Maya isn't listening.")

    monkeypatch.setattr(bridge_client, "call", fake_call)

    result = server.report_bug(
        title="Something broke",
        description="It broke.",
        repro_steps="Do the thing.",
        attempted_solutions="",
    )

    assert "attempted_solutions" in result or "Propose and attempt a fix" in result
    assert "issues/new" not in result


def test_report_bug_valid_returns_url_with_diagnostics(monkeypatch):
    fake_diagnostics = {"maya_version": "2026", "os": "windows"}

    def fake_call(op, params=None, *, port=6292):
        assert op == "diagnostics"
        return fake_diagnostics

    monkeypatch.setattr(bridge_client, "call", fake_call)

    result = server.report_bug(
        title="Rig binding breaks on export",
        description="Something goes wrong during export.",
        repro_steps="1. Open scene\n2. Export",
        attempted_solutions="Tried rebuilding the binding, no change.",
    )

    assert "issues/new" in result
    assert "review and submit" in result
    assert "2026" in result  # fake diagnostics value made it into the URL's body


def test_report_bug_tolerates_bridge_down_for_diagnostics(monkeypatch):
    def fake_call(op, params=None, *, port=6292):
        raise BridgeDownError("Maya isn't listening.")

    monkeypatch.setattr(bridge_client, "call", fake_call)

    result = server.report_bug(
        title="Something broke",
        description="It broke.",
        repro_steps="Do the thing.",
        attempted_solutions="Restarted Maya, still broken.",
    )

    assert "issues/new" in result


def test_report_bug_skips_diagnostics_fetch_when_disabled(monkeypatch):
    def fake_call(op, params=None, *, port=6292):
        raise AssertionError("bridge_client.call should not be invoked when include_diagnostics=False")

    monkeypatch.setattr(bridge_client, "call", fake_call)

    result = server.report_bug(
        title="Something broke",
        description="It broke.",
        repro_steps="Do the thing.",
        attempted_solutions="Tried a few things, no luck.",
        include_diagnostics=False,
    )

    assert "issues/new" in result
