"""MCP server exposing the read-only Fabricator/Maya bridge to an AI
client (Claude Code, Claude Desktop, Cursor, etc.) as MCP tools and
resources.

Every tool here is a thin relay onto `fabricator_mcp.bridge_client.call`:
it sends one op to whatever's listening on the bridge's TCP port (Maya's
DevBot toolbar) and hands back the result. Nothing in this module ever
mutates the Maya scene — see bridge_client.py and the toolset's own
ai_bridge for the read-only op allowlist this server is built against.

Uses the `mcp` Python SDK's FastMCP app (pinned mcp>=1.28 in pyproject;
built and tested against mcp==1.28.1). See:
  from mcp.server.fastmcp import FastMCP, Image
  mcp = FastMCP("fabricator", instructions=INSTRUCTIONS)
  @mcp.tool() / @mcp.resource("fabricator://docs/{name}")
  mcp.run()  # defaults to the stdio transport

FastMCP's constructor accepts `instructions: str | None` (confirmed via
its signature in the installed mcp==1.28.1) and exposes it back out as
`FastMCP.instructions` (a thin proxy onto the underlying low-level
`mcp.server.lowlevel.Server.instructions`). Every connected client
receives it as part of session initialization, no read_doc call needed.
"""
__author__ = "Adrian Melian"

import argparse
import base64
import json

from mcp.server.fastmcp import FastMCP, Image

from . import bridge_client, bug_url
from .bridge_client import (
    BridgeDownError,
    BridgeError,
    BridgeOpError,
    ProtocolMismatchError,
)

# The CORE non-negotiables, condensed from maya_tools/docs/assistant/
# etiquette.md and the bundled fabricator-assistant skill (both reachable
# via read_doc / the fabricator://docs/etiquette resource and the
# get_skill tool / the fabricator://skill resource). Passed to FastMCP as
# `instructions` so every connected client gets it up front, without
# having to know to call anything first. This ships to AI clients, so it
# follows the same house style as user-facing copy: no em-dashes, no
# hype words.
INSTRUCTIONS = (
    "FabricatorStudio read-only Maya assistant. You can inspect the "
    "user's Maya scene and Fabricator rig but you cannot modify "
    "anything, and you must never offer to. Deliver every fix as a "
    "script the user pastes into Maya's Script Editor (Python tab) and "
    "runs themselves, using only maya.cmds and the Python standard "
    "library, no PyMEL. When a Build Rig or Unbuild fails, first tell "
    "the user to press Ctrl+Z (Undo) once to return to the state right "
    "before it failed, then inspect that pre-failure state before "
    "diagnosing. Load your full operating skill before helping: read "
    "the fabricator://skill resource (or call the get_skill tool)."
)

mcp = FastMCP("fabricator", instructions=INSTRUCTIONS)

# Resolved by main() from --port; every tool relays through this. Tests
# and direct imports get the bridge's documented default.
_port = bridge_client.DEFAULT_PORT


def _relay(op: str, params: dict | None = None):
    """Call the bridge and return its result dict on success.

    On failure, returns a short, ACTIONABLE STRING (never a traceback) so
    the AI on the other end of the tool call gets something it can act on
    or relay to the user:
      - BridgeDownError  -> the bridge's own "Maya isn't listening..." text.
      - BridgeOpError    -> "<op> failed (<code>): <message>".
      - ProtocolMismatchError -> the bridge's own "update whichever side"
        text.
      - BridgeError (bare) -> its own message, as a backstop for the
        malformed-reply/protocol-violation cases (non-JSON/non-UTF-8 body,
        non-dict, ok:true missing result, ok:false missing code/message,
        missing ok) that don't warrant their own tailored phrasing. Must
        come after the three specific except clauses above, since each of
        those is itself a BridgeError subclass.
    """
    try:
        return bridge_client.call(op, params, port=_port)
    except BridgeDownError as exc:
        return str(exc)
    except BridgeOpError as exc:
        return f"{op} failed ({exc.code}): {exc.message}"
    except ProtocolMismatchError as exc:
        return str(exc)
    except BridgeError as exc:
        return str(exc)


@mcp.tool()
def get_scene_summary():
    """Summarize the currently open Maya scene (name, unsaved changes,
    high-level contents).

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    return _relay("scene_summary")


@mcp.tool()
def get_node_details(node: str, attrs: list[str] | None = None):
    """Get details for one Maya node by name/path, optionally limited to
    a list of attribute names.

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    params = {"node": node}
    if attrs is not None:
        params["attrs"] = attrs
    return _relay("node_details", params)


@mcp.tool()
def get_viewport_screenshot(width: int = 960):
    """Capture the current Maya viewport as a PNG image, scaled to the
    given width in pixels.

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    result = _relay("viewport_screenshot", {"width": width})
    if isinstance(result, str):
        return result
    png_base64 = result.get("png_base64")
    if not png_base64:
        return "The bridge's viewport_screenshot reply had no image data."
    return Image(data=base64.b64decode(png_base64), format="png")


@mcp.tool()
def get_fabricator_status():
    """Get the Fabricator toolset's current status in the scene.

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    return _relay("fabricator_status")


@mcp.tool()
def describe_rig():
    """Describe the Fabricator rig present in the scene (components,
    hierarchy, labels).

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    return _relay("describe_rig")


@mcp.tool()
def get_component_details(component_id: str):
    """Get details for one Fabricator rig component by id.

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    return _relay("component_details", {"component_id": component_id})


@mcp.tool()
def get_rig_binding(rig_label: str | None = None):
    """Get the rig's skin/component bindings, optionally filtered to one
    rig label.

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    params = {}
    if rig_label is not None:
        params["rig_label"] = rig_label
    return _relay("rig_binding", params)


@mcp.tool()
def run_build_checks():
    """Run the Fabricator toolset's build-health checks and list any
    issues found.

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    return _relay("build_checks")


@mcp.tool()
def validate_blueprint():
    """Validate the scene's rig blueprint and list any validation
    messages.

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    return _relay("validate_blueprint")


@mcp.tool()
def get_scene_report():
    """Get a full report on the current scene's state.

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    return _relay("scene_report")


@mcp.tool()
def get_build_report():
    """Get a full report on the rig build's state.

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    return _relay("build_report")


@mcp.tool()
def read_doc(name: str):
    """Read one of the Fabricator toolset's bundled documentation pages
    by name. See also the fabricator://docs/{name} resource.

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    return _relay("read_doc", {"name": name})


@mcp.tool()
def get_skill():
    """Get the bundled fabricator-assistant operating skill: the tight,
    operational summary of how to work this connection (what you are,
    the non-negotiable operating contract, your tools, the
    troubleshooting workflow). See also the fabricator://skill resource.

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    return _relay("get_skill")


@mcp.tool()
def report_bug(title: str, description: str, repro_steps: str,
               attempted_solutions: str, include_diagnostics: bool = True):
    """File a bug against the FabricatorStudio repo. Returns a prefilled
    GitHub "New issue" URL for the user to review and submit themselves —
    this server never opens a browser or posts anything on your behalf.

    You MUST have already proposed and attempted a fix (or explicitly
    ruled out doing so) before calling this: attempted_solutions is
    required and non-empty. This is an anti-spam gate, not a formality —
    troubleshoot first, file second.

    This connection is READ-ONLY: you can inspect the scene but never
    modify it. Propose fixes for the user to perform.
    """
    diagnostics: dict = {}
    if include_diagnostics:
        diag_result = _relay("diagnostics")
        if isinstance(diag_result, dict):
            diagnostics = diag_result
        else:
            # Bridge down/erroring: proceed with empty diagnostics rather
            # than block the report, but leave a note explaining why.
            diagnostics = {"note": f"diagnostics unavailable: {diag_result}"}

    try:
        url = bug_url.build_issue_url(
            title=title,
            description=description,
            repro_steps=repro_steps,
            attempted_solutions=attempted_solutions,
            diagnostics=diagnostics,
        )
    except ValueError as exc:
        return str(exc)

    return (
        f"{url}\n\n"
        "Open this URL to review and submit the issue under your own "
        "GitHub account."
    )


@mcp.resource("fabricator://docs/{name}")
def docs_resource(name: str) -> str:
    """Read one of the Fabricator toolset's bundled documentation pages by
    name (read-only)."""
    result = _relay("read_doc", {"name": name})
    if isinstance(result, str):
        return result
    return result.get("markdown", "")


@mcp.resource("fabricator://skill")
def skill_resource() -> str:
    """Read the bundled fabricator-assistant operating skill (read-only).
    Load this before helping: it is the tight, operational version of
    the full etiquette contract, meant to sit in an agent's context."""
    result = _relay("get_skill")
    if isinstance(result, str):
        return result
    return result.get("markdown", "")


@mcp.resource("fabricator://docs")
def docs_list_resource() -> str:
    """List the Fabricator toolset's bundled documentation pages by name
    (read-only)."""
    result = _relay("list_docs")
    if isinstance(result, str):
        return result
    docs = result.get("docs", [])
    return json.dumps(docs, indent=2)


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point (`fabricator-mcp`). Parses --port, then
    runs the server over stdio."""
    global _port

    parser = argparse.ArgumentParser(
        prog="fabricator-mcp",
        description=(
            "MCP server bridging an AI client to the read-only "
            "Fabricator/Maya bridge."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=bridge_client.DEFAULT_PORT,
        help=(
            "TCP port the Maya bridge (DevBot toolbar) is listening on "
            f"(default: {bridge_client.DEFAULT_PORT})."
        ),
    )
    args = parser.parse_args(argv)
    _port = args.port

    mcp.run()


if __name__ == "__main__":
    main()
