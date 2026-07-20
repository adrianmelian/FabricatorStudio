"""Vendored copy of the Fabricator/Maya bridge wire protocol constants.

This is a deliberate copy, not an import, of
``maya_tools/framework/toolbar/ai_bridge/protocol.py`` from the
KinematicSolutions toolset repo. fabricator-mcp ships to PyPI as an
independent package (installed by an AI client host such as Claude Code,
Claude Desktop, or Cursor) and must never depend on that repo's internal
`maya_tools` package layout. If the Maya-side protocol ever changes its
framing, this file (and PROTOCOL) is the thing that needs bumping here.

Wire shapes (NDJSON over loopback TCP, one JSON object per line, UTF-8):
  request: {"id": str, "op": str, "params": dict}
  ok:      {"id": str, "ok": True, "result": dict}
  error:   {"id": str, "ok": False, "error": {"code": str, "message": str}}
"""
__author__ = "Adrian Melian"

PROTOCOL = 1
DEFAULT_PORT = 6292  # 'MAYA' on a phone keypad

ERROR_CODES = (
    "unknown_op",
    "bad_params",
    "not_found",
    "maya_busy",
    "too_large",
    "internal",
)
