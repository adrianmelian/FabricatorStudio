"""App facade for the project setup tool: orchestrates create / edit /
duplicate / delete / list / apply-and-activate / list-engine-templates /
template-defaults / scaffold. Zero Qt at module scope -- every function
here is headless-callable (the Plan 7 UI is a thin caller of this module).
Composes config_validation (the sole schema gate), project_config_io (the
atomic storage layer + engine-template reads), and project_scaffold. The
two Maya-session-bound collaborators, maya_startup and toolbar_app, are
NEVER imported here at module top (contract's binding lazy-import rule --
both pull in maya.cmds / maya.OpenMayaUI at their own module top and are
not offscreen-importable); they are imported lazily, inside
apply_and_activate() only, which is the sole function in this module that
needs a live Maya session. See
docs/superpowers/specs/2026-07-07-project-setup-tool-design.md sec 4.1, 4.3.
"""
from __future__ import annotations

from pathlib import Path

from maya_tools.framework import (config_validation, project_config_io,
                                  project_config_resolve, project_scaffold)
from maya_tools.framework.config_validation import Issue


class ValidationError(Exception):
    """Raised by create_project/edit_project when config_validation finds
    any error-severity Issue. Carries .issues -- the FULL list
    config_validation returned (errors AND warnings) so a caller (the
    UI's validation panel) can render every finding, not just the
    first."""

    def __init__(self, issues):
        self.issues = list(issues)
        errors = [issue.message for issue in self.issues if issue.severity == "error"]
        super().__init__("; ".join(errors) or "Validation failed")


def _other_projects(exclude_slug=None):
    """Every OTHER saved project's authored dict -- config_validation's
    existing_projects, for its project_root-overlap and slug-collision
    checks. Excludes exclude_slug so editing/duplicating a project never
    flags itself against itself."""
    return [
        project_config_io.load_project(meta["slug"])
        for meta in project_config_io.list_projects()
        if meta["slug"] != exclude_slug
    ]


def _raise_if_errors(issues):
    if any(issue.severity == "error" for issue in issues):
        raise ValidationError(issues)


def _existing_bindings(exclude_slug=None):
    """[{'slug', ...bindings}] for every OTHER bound project on this
    machine -- validate_bindings' existing_bindings, for its per-root
    overlap warnings. v1 projects contribute their derived bindings."""
    out = []
    for meta in project_config_io.list_projects():
        if meta["slug"] == exclude_slug:
            continue
        bindings = (project_config_resolve.load_bindings(meta["slug"])
                    or project_config_io.derive_v1_bindings(meta["slug"]) or {})
        if bindings.get("source_art_root"):
            out.append({"slug": meta["slug"], **bindings})
    return out


def _validate_both(authored, bindings, exclude_slug=None):
    """Both tiers through their gates, one merged issue list (errors
    first per each gate's own ordering). A bindings error blocks save
    exactly like a config error."""
    issues = list(config_validation.validate_config(
        authored, existing_projects=_other_projects(exclude_slug=exclude_slug)))
    issues += config_validation.validate_bindings(
        bindings, existing_bindings=_existing_bindings(exclude_slug=exclude_slug))
    return issues


def create_project(authored: dict, bindings: dict) -> Path:
    """Validate BOTH tiers (authored against every OTHER existing project,
    bindings against the other bound projects on this machine), then save
    the project pair without overwrite and persist the machine bindings.
    A duplicate name/slug is normally caught here by config_validation's
    collision check (spec sec 4.3: "collision blocks") before
    project_config_io.save_project's own FileExistsError guard is ever
    reached -- that guard remains a defense-in-depth layer for races and
    for direct project_config_io callers, not something this function
    special-cases. This function does NOT catch save_project's own
    FileExistsError or ReadOnlyConfigError -- both propagate to the
    caller uncaught, alongside this module's own ValidationError; the UI
    wraps create_project/edit_project in one try/except that routes all
    three to its validation panel."""
    issues = _validate_both(authored, bindings)
    _raise_if_errors(issues)
    # Bindings FIRST: if the project save then fails (race, read-only),
    # the leftover is a harmless orphan bindings file the next successful
    # create overwrites - never a saved-but-unbound project whose name
    # collides on retry.
    slug = project_config_io.slugify(authored.get("name", ""))
    project_config_resolve.save_bindings(slug, bindings)
    return project_config_io.save_project(authored, overwrite=False)


def edit_project(slug: str, authored: dict, bindings: dict) -> Path:
    """Validate BOTH tiers against the other projects, guard that the
    identity folder does not silently move out from under the edit (spec
    sec 4.3: the folder name is the fixed identity key; `name` is
    cosmetic -- renaming to a name whose slug differs is rejected, not
    silently re-routed to a new folder), then save the pair in place
    (overwrite=True) and persist the machine bindings. Does NOT catch
    save_project's own ReadOnlyConfigError -- it propagates uncaught,
    exactly like create_project (see its docstring)."""
    issues = _validate_both(authored, bindings, exclude_slug=slug)
    _raise_if_errors(issues)
    target_slug = project_config_io.slugify(authored.get("name", ""))
    if target_slug != slug:
        raise ValidationError([Issue(
            "error", "name",
            f'Renaming to "{authored.get("name", "")}" would move the '
            f'identity folder from "{slug}" to "{target_slug}". Use '
            f'Duplicate to create a project under the new name, then '
            f'delete "{slug}" if it is no longer needed.',
        )])
    path = project_config_io.save_project(authored, overwrite=True)
    project_config_resolve.save_bindings(slug, bindings)
    return path


def duplicate_project(slug: str, new_name: str) -> Path:
    """Load slug's authored config, rename it, and create it fresh under
    the new name's slug (via create_project, so it gets the identical
    validate-then-save path as a brand-new project). The source project's
    machine bindings copy to the new slug -- same machine, same roots
    until the user edits them; a warning-only root overlap with the
    source is expected and does not block the save. A v1 source
    contributes its derived bindings."""
    authored = dict(project_config_io.load_project(slug))
    authored["name"] = new_name
    bindings = (project_config_resolve.load_bindings(slug)
                or project_config_io.derive_v1_bindings(slug) or {})
    if not bindings.get("source_art_root"):
        raise ValidationError([Issue(
            "error", "bindings",
            f'Project "{slug}" has no machine bindings on this machine, '
            f'so there is nothing to copy for the duplicate. Edit '
            f'"{slug}" in Mindmeld and bind Source Art Root + Content '
            f'Root first, then duplicate.')])
    return create_project(authored, bindings)


def delete_project(slug: str) -> Path:
    return project_config_io.delete_project(slug)


def list_projects() -> list[dict]:
    return project_config_io.list_projects()


def list_engine_templates() -> list[dict]:
    return project_config_io.list_templates()


def template_defaults(template_id: str) -> dict:
    return project_config_io.load_template(template_id)


def apply_and_activate(slug: str) -> None:
    """The manager's primary action (spec sec 4.3): apply session.json
    AND set the toolbar's active project in one action, keeping the two
    name-based active-project stores in sync (maya_startup's
    'FS_activeProjectConfig' optionVar and toolbar_prefs'
    active_project). Copy is explicit: this does NOT change which
    export_mapping.json a given scene resolves to -- export_core's
    matcher reads neither store, only the open scene's path vs
    project_root (unifying all three "active project" notions is out of
    scope for this release, spec sec 4.3 / sec 11).

    maya_startup and toolbar_app are imported HERE, lazily, and nowhere
    else in this module (contract's binding rule): both import
    maya.cmds / maya.OpenMayaUI at THEIR module top and are not
    offscreen-importable, so pulling them in at project_setup_app module
    scope would make project_setup_app itself -- and the Plan 7 UI that
    imports it -- unimportable offscreen. Deferring the import to the
    one function that actually needs a live Maya session keeps every
    other function in this module, and the module import itself, safe
    to exercise under bare mayapy with no maya.standalone.initialize()
    (Task 6's smoke check pins this)."""
    from maya_tools.framework import maya_startup
    from maya_tools.framework.toolbar import toolbar_app

    maya_startup.run(slug)
    toolbar_app.set_active_project(slug)


def scaffold(slug: str, keys: list[str]) -> list[Path]:
    """Resolve slug (project truth + this machine's bindings) and create
    only the checked candidate directories (Phase 2, spec sec 6). An
    unbound project raises UnboundProjectError with the actionable
    remedy."""
    resolved = project_config_resolve.resolve_project(slug)
    return project_scaffold.create_dirs(resolved, keys)
