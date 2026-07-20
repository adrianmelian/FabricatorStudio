# Fabricator

Fabricator is a free Maya rigging, skinning, and export toolset from FabricatorStudio. It covers the full character pipeline inside Maya: modular rigging, skeleton and skin I/O, joint tools, pose and animation libraries, and FBX export, all built around a scene-is-truth architecture (the Maya scene is the source of truth, not a hidden in-memory model).

The developer is available for pipeline work. See [fabricator.studio](https://fabricator.studio).

## Install

1. Download the release zip and unzip it.
2. Drag `Fabricator_Install.py` into the Maya viewport.
3. Click **Install** in the dialog that appears.

That's it. No admin rights needed, everything installs under your own `Documents/maya` folder. The menu, shelves, and hotkeys build immediately, no Maya restart required.

To remove it, drag `Fabricator_Uninstall.py` into the viewport and click **Uninstall**.

## Supported versions

- **Maya 2025** is the tested baseline.
- **Maya 2022+** should work (best effort, not regression-tested every release).
- Python 3 Maya only. Maya 2019-2021 (Python 2) are not supported.

## Tools

- **Fabricator**: modular rigging system, guides, skeleton, and modular rig build in three explicit phases.
- **CtrlEditor**: control curve shape library.
- **Skeleton IO**: save and load joint hierarchies to JSON.
- **Joint Orient**: aimer-driven joint orientation workflow.
- **Smart Joint Mirror**: live joint mirroring across the YZ plane.
- **Skin IO + skinning utilities**: save and load skin weights, plus smoothing, combining, and weight clipboard tools.
- **Pose Studio and Animation Studio**: cross-rig pose and clip storage with thumbnail previews.
- **FBX exporters**: static mesh, skeletal mesh, and animation export.
- **Renamer**: batch rename tool (hash numbering, find/replace, prefix/suffix, renumber).

## Advanced tier (ML skinning)

A few tools (auto-skin bind, delta mush, DemBones bake) use machine-learning dependencies that aren't part of Maya. These install on demand through a one-click "Install ML dependencies" flow inside the tool itself. See [DEPENDENCIES.md](DEPENDENCIES.md) for what gets installed and how to do it manually if you'd rather not use the in-tool installer.

## License

Free for solo developers, indies, and small teams. If your organization made under 2 million USD last year, the whole toolset is free for any use, including commercial work. Above that, one flat annual license covers your studio; see [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md). Source-available under the Business Source License 1.1 (see [LICENSE](LICENSE)); every version becomes GPL four years after release. Personal and portfolio use is always free.

## Support

This is a free tool, offered best effort with no SLA. Once the repo is public, use GitHub issues for bugs and requests.
