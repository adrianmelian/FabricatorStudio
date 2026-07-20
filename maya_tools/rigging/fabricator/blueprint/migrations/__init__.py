"""Schema migration registry for KS v2 blueprints.

When a blueprint's schema_version is older than CURRENT_SCHEMA_VERSION,
the loader (read_yaml) routes through this module to upgrade the data
dict in place before constructing the Blueprint dataclass.

Spec 1 ships version 1.0 only — no migrations are needed yet. This
module is the framework for them.

Pattern for a future migration from 1.0 to 1.1:
    @register('1.0', '1.1')
    def migrate_1_0_to_1_1(data: dict) -> dict:
        # mutate / return upgraded dict
        return data
"""
__author__ = "Adrian Melian"


_MIGRATIONS: dict = {}  # {(from_ver, to_ver): callable}


def register(from_version: str, to_version: str):
    """Decorator to register a migration function."""
    def deco(func):
        _MIGRATIONS[(from_version, to_version)] = func
        return func
    return deco


def migrate(data: dict, target_version: str) -> dict:
    """Upgrade a blueprint data dict to target_version.

    Walks registered migrations in order. Raises if no migration path
    exists from the data's schema_version to target_version.
    """
    current = data.get('schema_version', '0.0')
    if current == target_version:
        return data
    # Spec 1: no migrations exist yet. Future: walk graph of registrations
    # to find a path from current to target_version.
    raise RuntimeError(
        f"No migration path from schema_version {current!r} to {target_version!r}. "
        f"Spec 1 only supports schema_version '1.0'."
    )
