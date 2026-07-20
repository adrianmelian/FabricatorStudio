"""Auto-prime assembly: the reason the Reggie panel exists.

Tier 1 (once per session): system prompt = identity preamble + the
fabricator-assistant SKILL.md + etiquette.md, read from disk (the same
files the bridge serves to external clients).
Tier 2 (every turn): a fresh scan block prepended to the user's message,
so the model is scene-aware from word one without spending tool calls.
Tier 3 is the tool registry itself (tool_specs) — model-pulled on demand.
"""
from __future__ import annotations

import json
from pathlib import Path

__author__ = "Adrian Melian"

_MAYA_TOOLS = Path(__file__).resolve().parents[3]
_SKILL_PATH = _MAYA_TOOLS / "skills" / "fabricator-assistant" / "SKILL.md"
_ETIQUETTE_PATH = _MAYA_TOOLS / "docs" / "assistant" / "etiquette.md"

_PREAMBLE = (
    "You are Reggie, the FabricatorStudio AI TA, chatting with a user inside "
    "their running Maya session through a small dockable panel. Keep answers "
    "tight and practical, like texting a colleague who happens to be a "
    "rigging TA: professional, warm, no fluff. Every user message begins "
    "with a <scene_scan> block: a fresh automatic read of the scene taken "
    "the moment they hit send. Trust it over memory; the scene may have "
    "changed since the last turn. Be brief: lead with the finding, a few "
    "short sentences plus at most one script. No preambles, no recaps, "
    "never restate the scan back to the user. The operating contract "
    "below is binding."
)

# SKILL.md is authored for external MCP clients (resource fetches,
# list_docs). This paragraph re-grounds it for the embedded panel.
_ADAPTER = (
    "Note for this connection: you are the fabricator-assistant skill "
    "running embedded in the Reggie panel inside Maya. There is no "
    "resource-fetch mechanism here; ignore any fabricator:// resource "
    "references and any mention of list_docs. Use the read_doc tool for "
    "the bundled docs (names: concepts, toolset, troubleshooting, "
    "glossary, etiquette), and use only the tools present in your tool "
    "list."
)

SCAN_OPS = ("fabricator_status", "scene_summary", "build_report",
            "build_checks")


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block (--- ... ---) if present;
    return the text unchanged when there is no well-formed block."""
    if not text.startswith("---\n"):
        return text
    lines = text.split("\n")
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1:]).lstrip()
    return text


def _read_or_note(path: Path) -> str:
    """A bundled file that is missing or undecodable degrades to an
    inline note; the session prime always assembles."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return f"(bundled file unreadable or missing: {path.name})"


def build_system_prompt() -> str:
    return "\n\n---\n\n".join([
        _PREAMBLE,
        _strip_frontmatter(_read_or_note(_SKILL_PATH)),
        _ADAPTER,
        _read_or_note(_ETIQUETTE_PATH),
    ])


def build_scan_block(call) -> str:
    """call: callable(op, params) -> dict, raising on failure. A failed op
    degrades to an inline note; the turn always proceeds."""
    lines = ["<scene_scan>"]
    for op in SCAN_OPS:
        try:
            result = call(op, {})
            lines.append(f"{op}: {json.dumps(result, allow_nan=False)}")
        except Exception as exc:
            note = str(exc).replace("\n", " ")
            lines.append(f"{op}: (unavailable: {note})")
    lines.append("</scene_scan>")
    return "\n".join(lines)


def wrap_user_message(text: str, scan_block: str) -> str:
    return f"{scan_block}\n\n{text}"
