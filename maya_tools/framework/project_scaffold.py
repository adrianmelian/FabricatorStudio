"""Phase 2 bare-bones scaffolding: create only the on-disk directories the
user explicitly checks -- no filler, no speculative tree, no .gitkeep /
README unless asked. Pure Python: no maya, no Qt, so it is import-safe and
unit-testable under bare mayapy (or py -3). See
docs/superpowers/specs/2026-07-07-project-setup-tool-design.md sec 6.
"""
from __future__ import annotations

from pathlib import Path

from maya_tools.framework import config_generator


def _literal_head(source_dir: str) -> str:
    """The literal path prefix of source_dir up to (not including) its
    FIRST {name}/{path} placeholder segment.
    'SourceArt/Characters/{name}' -> 'SourceArt/Characters'.
    'SourceArt/Environments/{path}/Source' -> 'SourceArt/Environments'
    (everything from the first placeholder on is per-asset and is
    dropped, even a literal tail like 'Source' after it -- we cannot
    know which of the many per-asset values to create ahead of time). A
    source_dir with no placeholder at all returns itself, trailing slash
    stripped."""
    placeholders = config_generator.placeholders_in(source_dir)
    if not placeholders:
        return source_dir.rstrip("/")
    head, _, _ = source_dir.partition("{" + placeholders[0] + "}")
    return head.rstrip("/")


def _resolve(source_art_root: str, rel_or_abs: str) -> str:
    """rel_or_abs resolved under the Source Art Root when it is a relative
    path (an asset_class source_dir head is always authored relative to
    it); returned unchanged when it is already absolute (the resolved
    library roots and blueprints_dir arrive pre-merged from
    project_config_resolve -- this handles either shape defensively)."""
    p = Path(rel_or_abs)
    if p.is_absolute():
        return str(p)
    return str(Path(source_art_root) / rel_or_abs)


def candidate_dirs(resolved: dict) -> list[tuple[str, str, str]]:
    """RESOLVED config (project_config_resolve.resolve_project output) ->
    [(key, label, abspath)] candidate scaffold directories (spec sec 6):
    each asset_class's literal source_dir head under the Source Art Root,
    then the resolved pose_library_root, anim_library_root, and
    blueprints_dir (only when set -- a blank blueprints_dir means 'factory
    blueprints only', nothing to create). Content-root dirs are engine-
    managed and never scaffolded. Order is stable so create_dirs' `keys`
    argument can reference these keys."""
    source_art_root = resolved.get("source_art_root", "")
    out = []
    for asset_class in resolved.get("asset_classes", []):
        head = _literal_head(asset_class.get("source_dir", ""))
        if not head:
            continue
        key = f"asset_class:{asset_class['id']}"
        label = f"{asset_class.get('label', asset_class['id'])} source ({head})"
        out.append((key, label, _resolve(source_art_root, head)))
    for key, label, field in (
        ("pose_library_root", "Pose library root", "pose_library_root"),
        ("anim_library_root", "Anim library root", "anim_library_root"),
        ("blueprints_dir", "Blueprints directory", "blueprints_dir"),
    ):
        value = resolved.get(field, "")
        if value:
            out.append((key, label, _resolve(source_art_root, value)))
    return out


def create_dirs(resolved: dict, keys: list[str]) -> list[Path]:
    """mkdir(parents=True, exist_ok=True) ONLY for the checked `keys` (a
    subset of candidate_dirs' first tuple element) -- no filler, nothing
    speculative. Returns the created Path objects, in candidate_dirs
    order. Safe to call twice with the same keys: exist_ok=True means a
    re-run creates nothing new and raises nothing."""
    wanted = set(keys)
    created = []
    for key, _label, abspath in candidate_dirs(resolved):
        if key not in wanted:
            continue
        path = Path(abspath)
        path.mkdir(parents=True, exist_ok=True)
        created.append(path)
    return created
