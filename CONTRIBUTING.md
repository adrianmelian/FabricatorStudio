# Contributing

Contributions are welcome. Before a pull request can be merged you will be asked to sign a
Contributor License Agreement (CLA). It takes about a minute and is handled automatically
on your first pull request.

Why: FabricatorStudio is free for small studios and licensed commercially to large ones.
That model only works if a single person holds the rights to the whole codebase. The CLA
lets you keep ownership of your work while granting the project the right it needs to keep
operating this way.

You keep the copyright to what you write. Nothing is taken from you.

## Ground rules

- One change per pull request. Small and reviewable beats big and clever.
- Match the code around you: `maya.cmds` and `maya.api.OpenMaya` (API 2.0), no PyMEL, no
  `reload()`. Tools are two files: `tool_app.py` (headless logic, zero UI imports) and
  `tool_ui.py` (PySide6 window).
- Bug reports go through GitHub Issues with reproduction steps and your Maya version.

## License

The toolset is source-available under the Business Source License 1.1 (see `LICENSE`):
free for organizations under 2 million USD annual revenue, commercially licensed above
that (see `COMMERCIAL-LICENSE.md`). Every version converts to GPL-2.0-or-later four years
after its release. Your contributions are licensed to the project under the CLA so they
can ship under those same terms.
