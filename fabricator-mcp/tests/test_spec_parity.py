"""Parity: the vendored _tool_specs.py must equal the maya_tools original,
and must match the tools the FastMCP server actually registers. Skips the
file-parity half when the toolset repo layout is absent (PyPI install)."""
import ast
import importlib.util
from pathlib import Path

import pytest

from fabricator_mcp import _tool_specs

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = (_REPO_ROOT / "maya_tools" / "framework" / "toolbar" / "ai_bridge"
           / "tool_specs.py")


def _load_source_module():
    spec = importlib.util.spec_from_file_location("ks_tool_specs", _SOURCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _body_dump(path):
    """AST dump of a module's body with any leading docstring node dropped:
    the vendored file carries its own vendoring-header docstring, but
    everything below it must match the original exactly."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = tree.body
    if body and isinstance(body[0], ast.Expr):
        body = body[1:]
    return [ast.dump(node) for node in body]


def test_vendored_specs_equal_toolset_source():
    if not _SOURCE.is_file():
        pytest.skip("toolset repo layout not present (installed package)")
    source = _load_source_module()

    # Data-level parity, tool by tool, so a mismatch names the culprit.
    vendored_names = [t["name"] for t in _tool_specs.TOOLS]
    source_names = [t["name"] for t in source.TOOLS]
    assert vendored_names == source_names
    for name in vendored_names:
        src = next(t for t in source.TOOLS if t["name"] == name)
        assert _tool_specs.by_name(name) == src, name

    # Whole-module AST parity (same mechanism as test_reggie_core's
    # bug_url parity test): also guards the function bodies the data
    # comparison can't see, e.g. anthropic_tools()'s copy.deepcopy.
    vendored_path = Path(_tool_specs.__file__)
    assert _body_dump(vendored_path) == _body_dump(_SOURCE)


def test_vendored_specs_match_registered_mcp_tools():
    from fabricator_mcp import server
    registered = {t.name for t in server.mcp._tool_manager.list_tools()}
    vendored = {t["name"] for t in _tool_specs.TOOLS}
    assert vendored == registered, vendored ^ registered
