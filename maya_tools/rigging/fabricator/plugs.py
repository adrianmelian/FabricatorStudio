"""Plug resolution + BuildContext.

During build_modules, components register their output plugs as Maya plug
strings (e.g. 'root_ctrl.worldMatrix[0]'). Downstream components query the
BuildContext via resolve_plug('<id>.<plug_name>') to find what to connect
their parent_in to.

Pure-Python — the BuildContext doesn't import maya.cmds. Components do
their own Maya operations and pass plug strings in/out via this layer.
"""
__author__ = "Adrian Melian"

from dataclasses import dataclass, field


@dataclass
class BuildContext:
    """Per-build state holding plug resolutions.

    Components add output plugs via register_output as they build. The
    component graph is topo-sorted before build, so by the time a component
    needs to resolve its parent_plug, the parent has already registered.
    """
    plugs: dict = field(default_factory=dict)  # {'<id>.<plug_name>': '<maya_plug_string>'}
    blueprint: object = None  # the loaded Blueprint, used by build for fallback lookups (e.g. IKLeg's resolve_extra_guide_default when options aren't set)
    options: dict = field(default_factory=dict)  # per-build flags set by UI (e.g. 'reset_ctrl_shapes': True). Components read with context.options.get(...) — empty dict by default keeps the existing build path unchanged.

    def register_output(self, component_id: str, plug_name: str, maya_plug: str) -> None:
        """A component declares 'my plug X resolves to this Maya plug.'

        Called from inside Component.build() after the component has built
        its scene nodes and knows what concrete plug represents its output.
        """
        key = f'{component_id}.{plug_name}'
        self.plugs[key] = maya_plug

    def resolve_plug(self, plug_ref: str) -> str:
        """Look up '<component_id>.<plug_name>' -> live Maya plug string.

        Raises KeyError if the plug is unregistered. Build-order resolution
        (topo sort by parent_plug) ensures registration happens before
        resolution; missing plug = bug in the build orchestration.
        """
        if plug_ref not in self.plugs:
            raise KeyError(
                f"Plug {plug_ref!r} not registered. Build order may be wrong, "
                f"or the referenced component failed to build. Available plugs: "
                f"{sorted(self.plugs.keys())}"
            )
        return self.plugs[plug_ref]

    def has_plug(self, plug_ref: str) -> bool:
        """Check whether a plug is registered without raising."""
        return plug_ref in self.plugs
