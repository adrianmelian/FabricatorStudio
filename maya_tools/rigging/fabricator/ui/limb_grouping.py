# Python/maya_tools/rigging/fabricator/ui/limb_grouping.py
"""Canvas limb-collapse grouping — SPEC 2026-07-09 Limbs + Follower
Joints, section 3.5.

PURE FUNCTION, no `maya.cmds`, no Qt: `build_render_tree(roots,
limb_records)` takes an already-walked joint hierarchy (any tree of
objects duck-typed to `.joint_name` / `.children` — `scene_canvas_
model.CanvasNode` satisfies this without any import coupling back to
that module) plus the scene's limb membership (`LimbRecord` per
`fab_limb` node) and returns an isomorphic tree of `RenderNode`s
flagging which joints collapse into ONE limb-unit row versus render as
today's plain per-joint row. `ui/canvas_panel.py` is the only Qt
consumer; this module is unit-tested headless with plain fixtures (see
`_dev/test_canvas_limb_units.py`) and never touches the scene itself —
callers (`scene_canvas_model.read_limb_records`) do the actual
`fab_limb` reads and hand this module already-resolved, short-named
plain data.

Collapse rule (SPEC 3.5, amended by Derived Limbs spec 2026-07-11):
    A joint that is a limb's `top_joint` collapses into ONE row labeled
    by the limb's `limb_type` — UNLESS:
      (a) RETIRED. The old rule skipped implicit limbs — under Derived
          Limbs EVERY limb is derived (implicit=True is lifecycle
          bookkeeping only: orphan cleanup deletes derived limbs when
          their components go), so gating collapse on it would kill the
          collapse feature outright. An arm/leg component IS a limb;
          it collapses. `LimbRecord.is_implicit` survives as data only.
      (b) the limb has an ATTACHMENT-POINT EXCEPTION: some joint
          strictly inside its own subtree (stopping the walk at any
          OTHER limb's top_joint — that boundary joint belongs to the
          other limb, not this one) has, as a child, another limb's
          top_joint. Collapsing would hide that attached unit, so the
          limb renders expanded instead — its attached limbs still
          resolve (and collapse, or not) independently at their own
          boundary, via the same recursive rule.

A joint that is not any limb's top_joint always renders as a plain
per-joint row, exactly as before this module existed.

"Always expandable" (SPEC 3.5): a collapsed row is not a summary leaf —
its full joint subtree is still present as real children in the
returned tree (`RenderNode.children`), unabridged. `canvas_panel.py`
decides the default Qt expand/collapse state per row; this module only
decides WHICH label a row gets, never whether its children are built.
"""
__author__ = "Adrian Melian"

from dataclasses import dataclass, field
from typing import Iterable, List, Sequence, Tuple


@dataclass(frozen=True)
class LimbRecord:
    """One `fab_limb` node, projected to the plain triple this module
    needs. `top_joint` is a SHORT joint name (matches the short-name
    convention `scene_canvas_model.CanvasNode.joint_name` already uses)
    — callers are responsible for that normalization; this module does
    no path/namespace stripping of its own."""
    top_joint: str
    limb_type: str = ''
    is_implicit: bool = False


@dataclass(frozen=True)
class RenderNode:
    """One row in the canvas render tree. `is_limb_unit=True` means
    "collapse-eligible" — canvas_panel.py labels the row with
    `limb_type` and defaults it to collapsed; `is_limb_unit=False`
    means "plain joint row", rendered exactly as scene_canvas_model's
    own CanvasNode would be today. `children` is ALWAYS the full,
    unabridged subtree regardless of `is_limb_unit` — see the module
    docstring's "Always expandable" note."""
    joint_name: str
    is_limb_unit: bool = False
    limb_type: str = ''
    children: Tuple['RenderNode', ...] = field(default_factory=tuple)


class _HierarchyNodeLike:
    """Documentation-only duck-type: anything with `.joint_name: str`
    and `.children: Iterable[<same shape>]` — e.g.
    `scene_canvas_model.CanvasNode`. Not used at runtime (no isinstance
    checks — this module stays import-free of scene_canvas_model on
    purpose, see the module docstring)."""


def build_render_tree(roots: Sequence, limb_records: Sequence[LimbRecord]
                       ) -> Tuple[RenderNode, ...]:
    """Project a joint hierarchy + limb membership into a render tree.

    `roots`: top-level hierarchy nodes (each duck-typed `.joint_name` /
        `.children`, recursively) — e.g. the list `scene_canvas_model.
        read_scene_canvas()` returns.
    `limb_records`: every limb in the scene, as plain `LimbRecord`s.

    Returns a tuple of `RenderNode`s isomorphic to `roots` (same shape,
    same joint set, same order) — never raises on malformed input
    (an unmatched `top_joint` — e.g. a stale limb record whose joint
    was deleted — simply never matches any node and has no effect;
    duplicate `top_joint` entries — should not occur, `limb_node.
    create_limb_node` enforces one limb per top_joint — resolve
    last-wins, silently).
    """
    by_top = {r.top_joint: r for r in limb_records}

    def has_interior_attachment(node) -> bool:
        """True if any joint strictly inside `node`'s own subtree —
        not crossing into another limb's subtree once found — has,
        as a child, another limb's top_joint. Checked from `node`
        itself downward (so an attachment sitting directly on `node`,
        e.g. legs parented right on the spine's own top_joint, counts
        too — not just attachments several joints down the chain)."""
        for child in node.children:
            if child.joint_name in by_top:
                return True
            if has_interior_attachment(child):
                return True
        return False

    def build(node) -> RenderNode:
        limb = by_top.get(node.joint_name)
        # Derived Limbs (2026-07-11): no is_implicit gate — every limb
        # is derived now, and every limb collapses (rule (a) retired).
        collapse = (
            limb is not None
            and not has_interior_attachment(node)
        )
        children = tuple(build(c) for c in node.children)
        if collapse:
            return RenderNode(joint_name=node.joint_name, is_limb_unit=True,
                              limb_type=limb.limb_type, children=children)
        return RenderNode(joint_name=node.joint_name, is_limb_unit=False,
                          limb_type='', children=children)

    return tuple(build(r) for r in roots)


def find_render_node(render_roots: Sequence[RenderNode], joint_name: str
                      ) -> 'RenderNode | None':
    """Test/debug convenience — depth-first search for the RenderNode
    matching `joint_name`. Not used by canvas_panel.py itself (it walks
    the whole tree unconditionally to build Qt items); kept here rather
    than duplicated in the test suite since more than one test file
    needs it."""
    for node in render_roots:
        if node.joint_name == joint_name:
            return node
        found = find_render_node(node.children, joint_name)
        if found is not None:
            return found
    return None
