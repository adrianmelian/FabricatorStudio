"""Annotation source: resolves title + text + gif for tools, components, options.

Single source of truth, no separate content model:
- tools:      maya_tools/docs/tools/<slug>.md       front-matter title/summary + gif
- components: maya_tools/docs/components/<slug>.md   front-matter title/summary + gif
- options:    the OptionField.description already in each component CONTRACT
              (code is truth; same text the generated options tables use), the
              option name as the title, plus an optional per-option gif at
              docs/media/options/<comp>__<option>.gif

Gifs ship under maya_tools/docs/media/. Everything is lazy + cached; a Maya
session reads each doc file at most once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# maya_tools/docs/  (this module sits at maya_tools/framework/annotations.py)
_DOCS_ROOT = Path(__file__).resolve().parents[1] / "docs"
_MEDIA = _DOCS_ROOT / "media"


@dataclass(frozen=True)
class Annotation:
    """What a hover popover shows: a title, a line of text, and an optional
    gif. `banner` is an optional brand-image header rendered ABOVE the title
    (first user: the Bridge button's About card, Adrian 2026-07-19); doc-fed
    annotations leave it empty."""
    title: str = ""
    text: str = ""
    gif: str = ""  # absolute path, or "" when no gif exists on disk
    banner: str = ""  # absolute path to a banner PNG, or "" for none

    def __bool__(self) -> bool:
        return bool(self.title or self.text or self.gif or self.banner)


def _slug(name: str) -> str:
    """Component type / tool name -> doc slug. 'RibbonSpine' | 'ribbon_spine' ->
    'ribbon-spine'; 'IKLeg' -> 'ik-leg', 'FKAim' -> 'fk-aim' (a leading acronym
    splits from the following Word too).

    Verify against the real registered component-type strings at wiring time;
    override with an explicit map here if any slug does not match its doc file.
    """
    # Split an acronym from a trailing Word first (IKLeg -> IK-Leg, RibbonIKArm
    # -> RibbonIK-Arm): a leading acronym has no lowercase before its capitals,
    # so the camelCase rule below can't see that boundary on its own.
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "-", name)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", s)   # camelCase boundary
    return s.replace("_", "-").lower()


def _front_matter(path: Path) -> dict:
    """Flat `key: value` front-matter parse. No YAML dependency (the docs
    front matter is intentionally flat: title/summary/category/gif/video)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not raw.startswith("---"):
        return {}
    out: dict[str, str] = {}
    seen_open = False
    for line in raw.splitlines():
        if line.strip() == "---":
            if seen_open:
                break
            seen_open = True
            continue
        if seen_open and ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _gif_for(slug: str) -> str:
    p = _MEDIA / f"{slug}.gif"
    return str(p) if p.exists() else ""


@lru_cache(maxsize=256)
def for_tool(tool_slug: str) -> Annotation:
    fm = _front_matter(_DOCS_ROOT / "tools" / f"{tool_slug}.md")
    return Annotation(title=fm.get("title", ""),
                      text=fm.get("summary", ""),
                      gif=_gif_for(tool_slug))


@lru_cache(maxsize=256)
def for_component(comp_type: str) -> Annotation:
    slug = _slug(comp_type)
    fm = _front_matter(_DOCS_ROOT / "components" / f"{slug}.md")
    return Annotation(title=fm.get("title", ""),
                      text=fm.get("summary", ""),
                      gif=_gif_for(slug))


def for_option(comp_type: str, option_name: str, description: str) -> Annotation:
    """Option title is the option name, text is the contract's
    OptionField.description (passed in by the caller so this stays decoupled
    from the contract import). Gif is optional."""
    gif = _MEDIA / "options" / f"{_slug(comp_type)}__{option_name}.gif"
    return Annotation(title=option_name,
                      text=description or "",
                      gif=str(gif) if gif.exists() else "")


def for_toolbar(button_id: str, title: str = "", tooltip: str = "") -> Annotation:
    """A toolbar button with no dedicated tool doc: annotate from its own
    tooltip text (and title, if any), with an optional gif at
    docs/media/toolbar/<button_id>.gif. Drop a gif in and it shows on hover."""
    gif = _MEDIA / "toolbar" / f"{button_id}.gif"
    return Annotation(title=title or "",
                      text=tooltip or "",
                      gif=str(gif) if gif.exists() else "")


def clear_cache() -> None:
    """Call after a dev-reload or a docs refresh so edits show without a restart."""
    for_tool.cache_clear()
    for_component.cache_clear()
