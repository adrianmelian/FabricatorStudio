# components/__init__.py
# Kinematic Solutions v2 — auto-discovers all Component subclasses in this package.
"""Component auto-discovery.

Drop a new .py file in modules/ with a Component subclass that has a
CONTRACT class attr — the system picks it up automatically.
"""
__author__ = "Adrian Melian"

import importlib
import pkgutil
from pathlib import Path
from typing import Dict, List, Type

from maya_tools.rigging.fabricator.modules.component import Component

_REGISTRY: Dict[str, Type[Component]] = {}


def _discover() -> None:
    pkg_path = str(Path(__file__).parent)
    for _, module_name, _ in pkgutil.iter_modules([pkg_path]):
        if module_name in ('component',):
            continue
        mod = importlib.import_module(f'.{module_name}', package=__name__)
        for obj in vars(mod).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, Component)
                and obj is not Component
                and getattr(obj, 'CONTRACT', None) is not None
                and obj.CONTRACT.type
            ):
                _REGISTRY[obj.CONTRACT.type] = obj


_discover()


# Retired component types -> their successors. One release of grace:
# loads resolve with a loud warning; persisted options/settings are NOT
# translated (the dial vocabulary changed). PERMANENT/ungated — these
# names were retired for good, so the mapping applies unconditionally,
# regardless of the data's age. (Empty since 2026-07-12: 'AdvancedRibbon'
# -> 'Ribbon' died with the Ribbon component itself — FKChain, Ribbon,
# and SplineFK left the shipped set pre-launch, no successors.)
_LEGACY_TYPE_MAP = {}

# VERSION-GATED legacy mappings: unlike _LEGACY_TYPE_MAP above, these type
# strings are being RESERVED for a future component that will legitimately
# reclaim the exact same name — 'IKArm' is set aside for a future free
# basic-arm component (RibbonIKArm is the paid, ribbon-twist arm this name
# used to belong to; see version.py 1.1.0). The mapping must stop applying
# once data is authored AT OR AFTER the version below, or a future free
# IKArm would forever misload as RibbonIKArm. {legacy_type: (successor_type,
# version_at_which_the_rename_landed)}.
_VERSION_GATED_LEGACY_TYPE_MAP = {
    'IKArm': ('RibbonIKArm', '1.1.0'),
}

# A single blueprint load resolves the same legacy type multiple times
# (once per spawn, again when _sync_component_pivots re-resolves cdata.type);
# one loud warning per legacy type per session is enough.
_LEGACY_WARNED = set()


def _version_tuple(version_str: str) -> tuple:
    """Dotted 'major.minor.patch' string -> a comparable int tuple. Ignores
    a trailing non-numeric segment (best-effort — this studio's versions
    are always plain dotted integers, but a garbage/hand-edited stamp
    should degrade to '0.0.0', not raise)."""
    parts = []
    for p in (version_str or '').split('.'):
        try:
            parts.append(int(p))
        except (TypeError, ValueError):
            parts.append(0)
    return tuple(parts) or (0,)


def _version_lt(a: str, b: str) -> bool:
    """True if version string `a` < `b`, comparing as dotted int tuples
    (shorter tuple zero-padded to the longer one's length)."""
    ta, tb = _version_tuple(a), _version_tuple(b)
    n = max(len(ta), len(tb))
    ta = ta + (0,) * (n - len(ta))
    tb = tb + (0,) * (n - len(tb))
    return ta < tb


def _is_legacy_scene_data(gate_version: str) -> bool:
    """True when the CURRENT scene's data predates `gate_version` — the
    version-gated legacy mapping's own applicability check.

    Source of truth: the registry's stamped fabricator_version (nodes.
    get_registry_fabricator_version — version.py: "Build Modules stamps
    FABRICATOR_VERSION onto the registry ... at the end of every
    successful build"). This is a single per-SCENE value, read identically
    regardless of which of resolve_component_type's two call paths is
    asking (blueprint-load spawns components into a scene whose registry
    was just freshly created with no fabricator_version stamp yet; a
    scene-node component_type re-read happens against whatever registry
    stamp that scene already carries) — so one check here structurally
    covers both.

    An EMPTY/missing stamp (no registry, or a registry never stamped by a
    successful build — including the fresh registry a blueprint load just
    created) is treated as legacy: conservatively correct, since every
    real scene in existence today predates this version bump (it just
    landed) and a future free 'IKArm' component cannot yet exist in any
    of them. Deferred import (matches this module's own Maya-optional
    posture elsewhere): offscreen/no-Maya contexts (pure-logic tests)
    degrade to "no registry" -> legacy, never raise.
    """
    try:
        from maya_tools.rigging.fabricator import nodes
        stamped = nodes.get_registry_fabricator_version()
    except Exception:
        return True
    return (not stamped) or _version_lt(stamped, gate_version)


def is_legacy_scene_data(gate_version: str) -> bool:
    """Public wrapper around `_is_legacy_scene_data` (Task 2.3, SPEC
    2026-07-09 Limbs + Follower Joints §3.4/§4: the 'fingers'-option-to-
    limb-node migration gates on the SAME fabricator_version mechanism
    resolve_component_type uses internally for the IKArm->RibbonIKArm
    rename). Exposed here so callers OUTSIDE this module (fs_app.py's
    migration, currently the only consumer) don't need to duplicate the
    version-stamp comparison or reach into a private function."""
    return _is_legacy_scene_data(gate_version)


def resolve_component_type(type_str: str, data_version: str = None) -> str:
    """Map retired type strings to their successor, warning loudly.

    Two independent maps: _LEGACY_TYPE_MAP (permanent, ungated) and
    _VERSION_GATED_LEGACY_TYPE_MAP (only applies to data older than the
    version at which the rename landed — see that map's own docstring).
    Both call paths — fs_app.load's blueprint spawn (resolve_component_
    type(cdata.type) directly) and every scene-node read (get_component_
    class -> resolve_component_type internally) — route through this one
    function, so the version gate covers both without special-casing
    either.

    data_version (fragment parity, 2026-07-20): when the caller KNOWS the
    version stamp of the data the type string came from (a .limb.yaml's
    own fabricator_version — the one case where the scene stamp can't
    date the data, because a fragment drops into a LIVE scene whose
    registry is stamped current while the file may be old), pass it and
    the version gate compares against IT instead of the scene registry.
    '' means unstamped/pre-versioning data (legacy map applies); None
    (the default) means "no file context — gate on the scene stamp",
    the behavior every pre-existing caller keeps. Blueprint loads don't
    use this: fs_app.load holds the registry AT the file's stamp for its
    resolution window, which routes here through the scene path.
    """
    if type_str in _LEGACY_TYPE_MAP:
        new = _LEGACY_TYPE_MAP[type_str]
        if type_str not in _LEGACY_WARNED:
            _LEGACY_WARNED.add(type_str)
            try:
                import maya.cmds as cmds
                cmds.warning(f'[Fabricator] Component type {type_str!r} is '
                             f'retired; loading as {new!r}. Legacy deformer '
                             'settings are dropped; re-dial on the new board.')
            except Exception:
                pass
        return new
    if type_str in _VERSION_GATED_LEGACY_TYPE_MAP:
        new, gate_version = _VERSION_GATED_LEGACY_TYPE_MAP[type_str]
        if data_version is not None:
            is_legacy = ((not data_version)
                         or _version_lt(data_version, gate_version))
        else:
            is_legacy = _is_legacy_scene_data(gate_version)
        if is_legacy:
            warn_key = f'{type_str}@{gate_version}'
            if warn_key not in _LEGACY_WARNED:
                _LEGACY_WARNED.add(warn_key)
                try:
                    import maya.cmds as cmds
                    cmds.warning(
                        f'[Fabricator] Component type {type_str!r} is from '
                        f'data older than Fabricator {gate_version}; '
                        f'loading as {new!r} (the renamed component this '
                        f'type used to mean). {type_str!r} is reserved for '
                        f'a future component — this mapping will stop '
                        f'applying once this rig is rebuilt at '
                        f'{gate_version} or later.')
                except Exception:
                    pass
            return new
        return type_str
    return type_str


def get_component_class(type_str: str) -> Type[Component]:
    """Return the Component class for a given type string. Raises KeyError if not found."""
    return _REGISTRY[resolve_component_type(type_str)]


def all_component_types() -> List[str]:
    """Return all discovered component type strings, sorted."""
    return sorted(_REGISTRY.keys())


def all_component_classes() -> List[Type[Component]]:
    """Return all discovered Component subclasses, sorted by display_name."""
    return sorted(_REGISTRY.values(), key=lambda c: c.CONTRACT.display_name)


def reload_all() -> None:
    """Re-discover all components (useful during development hot-reloads)."""
    _REGISTRY.clear()
    _discover()


def get_mirror_negate(component_type: str, ctrl_role: str) -> frozenset:
    """Return the per-channel negate set for mirroring a (component_type,
    ctrl_role) ctrl across a L↔R pair.

    Looks up the matching MirrorRule on the component's contract. Returns
    an empty frozenset (= verbatim swap, no sign flips) when the
    component is unknown or the role has no declared rule. Anim Helpers'
    Mirror Pose uses this as the per-ctrl dispatch — each component owns
    its own mirror semantics on its contract.
    """
    try:
        cls = get_component_class(component_type)
    except KeyError:
        return frozenset()
    for rule in cls.CONTRACT.mirror_rules:
        if rule.ctrl_role == ctrl_role:
            return rule.negate
    return frozenset()
