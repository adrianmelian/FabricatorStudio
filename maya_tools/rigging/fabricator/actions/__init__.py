# Python/maya_tools/rigging/fabricator/actions/__init__.py
"""Marking-menu action handlers. Discoverable via dotted-path strings
referenced from Contract.actions.

Each module in this package exposes handler functions with the
signature: handler(component_id: str, ctrl: str) -> None
"""
__author__ = "Adrian Melian"
