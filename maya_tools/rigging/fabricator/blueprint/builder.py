# Python/maya_tools/rigging/fabricator/blueprint/builder.py
"""Blueprint validation + build orchestration helpers.

Validation is the gate before any Maya work begins. A blueprint that fails
validation NEVER touches the scene — the caller surfaces the errors and
the user fixes them.

This module contains the pure-Python validation logic. Maya-side build
orchestration lives in fs_app.py and pulls in nodes.py / components.
"""
__author__ = "Adrian Melian"

from maya_tools.rigging.fabricator.blueprint.schema import Blueprint, CURRENT_SCHEMA_VERSION
from maya_tools.rigging.fabricator.modules import (
    get_component_class, all_component_types, resolve_component_type,
)


def validate_blueprint(blueprint: Blueprint) -> list:
    """Return a list of validation error messages. Empty list = valid.

    Validates:
      1. Schema version matches CURRENT_SCHEMA_VERSION.
      2. Joint parent refs resolve to other joints in the same blueprint.
      3. No cycles in joint parenthood.
      4. All joint names are unique within the blueprint.
      5. Every component's `type` matches a registered component class.
      6. min_joints <= len(joints) <= max_joints per component contract.
      7. Every joint referenced by a component exists in the skeleton block.
      8. No two components claim ownership of the same primary drive joint
         (joints[0]).
      9. Every parent_plug points to a real <component_id>.<plug_name>
         declared in another component's contract outputs.
     10. No cycles in the parent_plug graph (DAG).
     11. Every option matches its contract's options_schema (type + choices).
     12. Component IDs are unique (after auto-deriving any nulls).

    Scene-wide unique-name validation (find_duplicate_transforms) happens
    at build_modules time, not here — it requires Maya. Build orchestration
    runs both gates: this validator + scene_cleanup.find_duplicate_transforms.
    """
    errors: list = []

    # 1. Schema version
    if blueprint.schema_version != CURRENT_SCHEMA_VERSION:
        errors.append(
            f"Blueprint schema_version {blueprint.schema_version!r} doesn't match "
            f"current {CURRENT_SCHEMA_VERSION!r}."
        )

    # Joint hierarchy checks
    joint_names = [j.name for j in blueprint.skeleton_joints]
    joint_set = set(joint_names)

    # 4. Unique joint names
    if len(joint_set) != len(joint_names):
        from collections import Counter
        for name, count in Counter(joint_names).items():
            if count > 1:
                errors.append(f"Duplicate joint name {name!r} in skeleton block ({count} entries).")

    # 2. Parent refs resolve
    for j in blueprint.skeleton_joints:
        if j.parent is not None and j.parent not in joint_set:
            errors.append(
                f"Joint {j.name!r} references parent {j.parent!r} which doesn't "
                f"exist in the skeleton block."
            )

    # 3. No cycles in joint parenthood
    parent_map = {j.name: j.parent for j in blueprint.skeleton_joints}
    for j in blueprint.skeleton_joints:
        seen = {j.name}
        cur = j.parent
        while cur is not None:
            if cur in seen:
                errors.append(f"Cycle in joint parenthood involving {j.name!r}.")
                break
            seen.add(cur)
            cur = parent_map.get(cur)

    # Component checks — auto-derive missing IDs first
    available_types = set(all_component_types())
    component_ids: list = []
    for c in blueprint.components:
        # 5. type registered — route through resolve_component_type first
        # (same call get_component_class makes internally just below) so a
        # legacy/version-gated alias like 'IKArm' -> 'RibbonIKArm' validates
        # against its resolved successor, not the retired string. Without
        # this, any blueprint carrying a legacy type string fails
        # validation before build_modules ever reaches the resolving
        # get_component_class call, even though the type is perfectly
        # buildable.
        if resolve_component_type(c.type) not in available_types:
            errors.append(
                f"Component type {c.type!r} is not registered. Available: "
                f"{sorted(available_types)}"
            )
            continue

        cls = get_component_class(c.type)
        contract = cls.CONTRACT

        # Auto-derive id if missing
        cid = c.id if c.id else cls.default_id(c.joints)

        # 12. unique id
        if cid in component_ids:
            errors.append(f"Duplicate component id {cid!r} (resolved from {c.type}).")
        component_ids.append(cid)

        # 6. joint count in range
        n = len(c.joints)
        max_j = contract.max_joints if contract.max_joints != -1 else float('inf')
        if not (contract.min_joints <= n <= max_j):
            errors.append(
                f"Component {cid!r} ({c.type}) has {n} joints, "
                f"requires {contract.min_joints}..{contract.max_joints}."
            )

        # 7. joints exist in skeleton
        for jn in c.joints:
            if jn not in joint_set:
                errors.append(
                    f"Component {cid!r} references joint {jn!r} not in the skeleton block."
                )

        # Per-component pre-build validation. Calls cls.can_apply with the
        # blueprint context so component-specific rules (like SimpleIK's
        # 3-joint-chain requirement) catch issues that simple count checks miss.
        ok, reason = cls.can_apply(list(c.joints), blueprint)
        if not ok:
            errors.append(f"Component {cid!r} ({c.type}): {reason}")
            continue

        # Role descendancy — when contract declares joint_roles with
        # descendant_of constraints, every joint in the role chain must
        # be a descendant of its anchor in the blueprint hierarchy.
        if contract.joint_roles and len(c.joints) >= len(contract.joint_roles):
            for role_idx, role in enumerate(contract.joint_roles):
                if not role.descendant_of:
                    continue
                anchor_idx = next(
                    (i for i, r in enumerate(contract.joint_roles)
                     if r.name == role.descendant_of), None
                )
                if anchor_idx is None:
                    continue
                anchor_joint = c.joints[anchor_idx]
                this_joint = c.joints[role_idx]
                if not anchor_joint or not this_joint:
                    errors.append(
                        f"Component {cid!r} ({c.type}): role {role.name!r} "
                        f"or {role.descendant_of!r} is unset."
                    )
                    continue
                # Walk parent chain from this_joint to root
                parent_map = {j.name: j.parent for j in blueprint.skeleton_joints}
                cur = parent_map.get(this_joint)
                found = False
                while cur is not None:
                    if cur == anchor_joint:
                        found = True
                        break
                    cur = parent_map.get(cur)
                if not found:
                    errors.append(
                        f"Component {cid!r} ({c.type}): role {role.name!r} "
                        f"joint {this_joint!r} is not a descendant of "
                        f"{role.descendant_of!r} joint {anchor_joint!r}."
                    )

        # 11. options match schema
        for opt_name, opt_field in contract.options_schema.items():
            if opt_name in c.options:
                val = c.options[opt_name]
                if opt_field.choices and val not in opt_field.choices:
                    errors.append(
                        f"Component {cid!r} option {opt_name}={val!r} not in choices "
                        f"{list(opt_field.choices)}."
                    )

    # 8. joint ownership (each joint owned by at most one component as joints[0])
    primary_joint_owner: dict = {}
    for c in blueprint.components:
        if not c.joints:
            continue
        primary = c.joints[0]
        if primary in primary_joint_owner:
            errors.append(
                f"Joint {primary!r} is the primary drive joint of both "
                f"{primary_joint_owner[primary]!r} and {c.id or '(auto)'!r}."
            )
        else:
            primary_joint_owner[primary] = c.id or '(auto)'

    # 9 + 10. parent_plug resolves to a real output + DAG
    output_plugs_by_id: dict = {}
    for c in blueprint.components:
        if c.type not in available_types:
            continue
        cls = get_component_class(c.type)
        cid = c.id if c.id else cls.default_id(c.joints)
        output_plugs_by_id[cid] = {p.name for p in cls.CONTRACT.outputs}

    for c in blueprint.components:
        if not c.parent_plug:
            continue
        if '.' not in c.parent_plug:
            errors.append(
                f"Component {c.id or c.type!r} has malformed parent_plug "
                f"{c.parent_plug!r} (expected '<id>.<plug_name>')."
            )
            continue
        target_id, target_plug = c.parent_plug.split('.', 1)
        if target_id not in output_plugs_by_id:
            errors.append(
                f"Component {c.id or c.type!r} parent_plug points to unknown "
                f"component {target_id!r}."
            )
            continue
        if target_plug not in output_plugs_by_id[target_id]:
            errors.append(
                f"Component {c.id or c.type!r} parent_plug references plug "
                f"{target_plug!r} not declared by component {target_id!r}."
            )

    # 10. DAG check on parent_plug graph
    edges: dict = {}
    for c in blueprint.components:
        if c.type not in available_types:
            continue
        cls = get_component_class(c.type)
        cid = c.id if c.id else cls.default_id(c.joints)
        edges[cid] = set()
        if c.parent_plug and '.' in c.parent_plug:
            target_id = c.parent_plug.split('.', 1)[0]
            edges[cid].add(target_id)

    visited_state: dict = {}  # 0=unvisited, 1=in-progress, 2=done
    def has_cycle(node):
        if visited_state.get(node) == 1:
            return True
        if visited_state.get(node) == 2:
            return False
        visited_state[node] = 1
        for nxt in edges.get(node, set()):
            if has_cycle(nxt):
                return True
        visited_state[node] = 2
        return False

    for cid in edges:
        if has_cycle(cid):
            errors.append(f"Cycle in parent_plug graph involving component {cid!r}.")
            break

    return errors
