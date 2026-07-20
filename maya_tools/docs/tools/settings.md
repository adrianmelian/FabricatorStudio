# Settings

Your per-machine setup for FabricatorStudio. Settings holds everything
that belongs to you and this computer rather than to the project: where
the shared project configs live, and where each project's folders sit on
your drives. Mindmeld defines the project. Settings binds it to your
machine.

Open it from the toolbar: left-click the gear button. Right-click on the
same button keeps the quick toolbar options (dock, layout, show and hide
tools).

## Shared Configs Root

Where FabricatorStudio looks for project configs. By default this is your
local Maya folder, and solo users never need to change it. On a team,
point it at the studio's shared configs folder (a Perforce or network
path) with Browse. Use Local Default switches back.

The status line shows which source is winning. If your studio sets the
FABRICATOR_PROJECT_CONFIGS environment variable, that wins over anything
picked here and the field locks with a note saying so.

## Project Bindings

Every project at the current configs root is listed with a BOUND or
UNBOUND pill. Select one and set where it lives on this machine:

- Source Art Root (required): the top of the project's working files.
- Content Root (required): the engine content folder exports land in.
- Pose Library Root, Anim Library Root, Blueprints Dir (optional
  overrides): leave blank to keep them under Source Art Root at the
  subpaths the project defines. Set one only when this machine keeps that
  data somewhere else.

Bind saves your choices to a small file on this machine only. Nothing in
the shared project config changes, so binding works even when the shared
folder is read-only. Different machines can use completely different
drive letters and paths; the project does not care.

## Maya Integration

Choose what FabricatorStudio installs into Maya at startup: the main
menu, the shelves, the hotkey set, and the Ctrl+Alt+RMB animation marking
menu. Everything is on by default. Turning one on builds it immediately;
turning one off takes effect the next time Maya starts.

## For teams

A new teammate's whole setup is this window: point Shared Configs Root at
the studio's configs folder, select each project, bind its two roots,
done. Every FabricatorStudio tool then resolves paths for this machine
automatically.
