"""Single source of truth for the LLM-visible Fabricator tool surface.

Consumed by the Reggie panel's agent loop directly, and VENDORED into
fabricator-mcp as src/fabricator_mcp/_tool_specs.py (a deliberate copy,
same pattern as _wire.py) with a parity test on each side. If you change
anything here, update the vendored copy and both parity tests will hold
you to it.

Each entry: name (LLM tool name), description (must carry the READ-ONLY
line), input_schema (JSON Schema), op (bridge op name, or None for the
one locally-executed tool, report_bug).
"""
from __future__ import annotations

__author__ = "Adrian Melian"

import copy

_RO = (" This connection is READ-ONLY: you can inspect the scene but never "
       "modify it. Propose fixes for the user to perform.")

_NONE = {"type": "object", "properties": {}, "required": []}

TOOLS = [
    {"name": "get_scene_summary", "op": "scene_summary",
     "description": "Summarize the currently open Maya scene (name, unsaved "
                    "changes, high-level contents)." + _RO,
     "input_schema": _NONE},
    {"name": "get_node_details", "op": "node_details",
     "description": "Get details for one Maya node by name/path, optionally "
                    "limited to a list of attribute names (the bridge caps "
                    "attrs at 50 per call)." + _RO,
     "input_schema": {"type": "object", "properties": {
         "node": {"type": "string", "description": "Node name or DAG path."},
         "attrs": {"type": "array", "items": {"type": "string"},
                   "description": "Optional attribute names to fetch."}},
         "required": ["node"]}},
    {"name": "get_viewport_screenshot", "op": "viewport_screenshot",
     "description": "Capture the current Maya viewport as a PNG image, scaled "
                    "to the given width in pixels (the bridge caps width at "
                    "1280)." + _RO,
     "input_schema": {"type": "object", "properties": {
         "width": {"type": "integer", "description": "Image width, default 960."}},
         "required": []}},
    {"name": "get_fabricator_status", "op": "fabricator_status",
     "description": "Get the Fabricator toolset's current status in the scene."
                    + _RO,
     "input_schema": _NONE},
    {"name": "describe_rig", "op": "describe_rig",
     "description": "Describe the Fabricator rig present in the scene "
                    "(components, hierarchy, labels)." + _RO,
     "input_schema": _NONE},
    {"name": "get_component_details", "op": "component_details",
     "description": "Get details for one Fabricator rig component by id." + _RO,
     "input_schema": {"type": "object", "properties": {
         "component_id": {"type": "string", "description": "Component id."}},
         "required": ["component_id"]}},
    {"name": "get_rig_binding", "op": "rig_binding",
     "description": "Get the rig's skin/component bindings, optionally "
                    "filtered to one rig label." + _RO,
     "input_schema": {"type": "object", "properties": {
         "rig_label": {"type": "string", "description": "Optional rig label."}},
         "required": []}},
    {"name": "run_build_checks", "op": "build_checks",
     "description": "Run the Fabricator toolset's build-health checks and "
                    "list any issues found." + _RO,
     "input_schema": _NONE},
    {"name": "validate_blueprint", "op": "validate_blueprint",
     "description": "Validate the scene's rig blueprint and list any "
                    "validation messages." + _RO,
     "input_schema": _NONE},
    {"name": "get_scene_report", "op": "scene_report",
     "description": "Get a full report on the current scene's state." + _RO,
     "input_schema": _NONE},
    {"name": "get_build_report", "op": "build_report",
     "description": "Get a full report on the rig build's state." + _RO,
     "input_schema": _NONE},
    {"name": "read_doc", "op": "read_doc",
     "description": "Read one of the Fabricator toolset's bundled "
                    "documentation pages by name (plain stem, e.g. "
                    "'troubleshooting')." + _RO,
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Doc stem, no extension."}},
         "required": ["name"]}},
    {"name": "get_skill", "op": "get_skill",
     "description": "Get the bundled fabricator-assistant operating skill: "
                    "the tight, operational summary of how to work this "
                    "connection." + _RO,
     "input_schema": _NONE},
    {"name": "report_bug", "op": None,
     "description": "File a bug against the KinematicSolutions repo. Returns "
                    "a prefilled GitHub 'New issue' URL for the user to review "
                    "and submit themselves; nothing is posted on their behalf. "
                    "You MUST have already proposed and attempted a fix (or "
                    "explicitly ruled one out) before calling this: "
                    "attempted_solutions is required and non-empty." + _RO,
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"},
         "description": {"type": "string"},
         "repro_steps": {"type": "string"},
         "attempted_solutions": {"type": "string"},
         "include_diagnostics": {"type": "boolean",
                                 "description": "Default true."}},
         "required": ["title", "description", "repro_steps",
                      "attempted_solutions"]}},
]


def anthropic_tools() -> list[dict]:
    """The Messages-API 'tools' array projection.

    Each entry's input_schema is a deep copy, safe for callers to mutate
    (several TOOLS entries share the one _NONE schema object internally).
    """
    return [{"name": t["name"], "description": t["description"],
             "input_schema": copy.deepcopy(t["input_schema"])} for t in TOOLS]


def by_name(name: str) -> dict | None:
    for t in TOOLS:
        if t["name"] == name:
            return t
    return None
