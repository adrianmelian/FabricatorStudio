---
title: Installer
summary: <b>Drag one file into Maya and you are set up.</b><br>Installs the FabricatorStudio toolset, wires it into Maya's startup, and gets out of the way.<br><br>Choose where the tools live (your own Maya folder, or a shared network folder the whole team runs from), then press Install. No admin rights, no account, no network calls. The matching uninstaller removes it just as cleanly.
category: framework
gif: ../media/installer.gif
video:
---

# Installer

## What it does
`Fabricator_Install.py` is the drag-and-drop entry point that turns the downloaded folder into a working FabricatorStudio setup. Dropping it into a Maya viewport calls Maya's native `onMayaDroppedPythonFile()` hook, which opens a confirm card and stops there - **nothing is written until you press Install**. On Install it copies the toolset (or, in shared mode, points at it where it already sits), appends a sentinel-guarded bootstrap block to your `userSetup.py` so Maya loads the toolbar on every launch, and then builds everything immediately so you never have to restart. `Fabricator_Uninstall.py`, alongside it, is the exact inverse.

The installer is deliberately self-contained: it runs *before* the toolset exists on `sys.path`, so it carries its own copy of the Mindmeld look (card, colors, progress popover) rather than importing from a payload that is not there yet.

## Quick start
1. Unzip the download and open Maya (2022 or newer).
2. Drag **`Fabricator_Install.py`** from that folder into the Maya viewport.
3. Check the **Install Directory** - it is prefilled with your own Maya scripts folder, which is the right answer for almost everyone.
4. Press **Install**. A progress card runs the copy, the startup wiring, and the toolbar build.
5. The welcome card appears: take the short tour, or skip it. The FabricatorStudio toolbar is already on screen.

Keep the unzipped folder somewhere findable - `Fabricator_Uninstall.py` lives in it, and re-dragging the installer later is how you update.

## Workflow

**Two install modes.** The radio pair under the Install Directory decides how the toolset reaches this machine:

- **Copy the tools to the Install Directory** (default, and what an individual wants). The payload is copied into ONE brand folder: `<Install Directory>/fabricator_studio/`, holding `maya_tools/` and `icons/` side by side. That sibling layout is load-bearing - the shelf builder resolves its icon directory by walking up from its own file, so `icons/` anywhere else silently blanks every icon.
- **Run from current location** (shared / network installs). Nothing is copied. Your Maya is pointed at the payload where it already lives - a network drive or a Perforce workspace. The studio's TA unzips once into the shared folder; every artist opens that folder, drags the same `Fabricator_Install.py` in, picks this mode, and presses Install. Updates then happen once, centrally: sync new files into the share and every pointed Maya picks them up on its next launch, with no re-wiring on anyone's machine.

**Where the default Install Directory comes from.** The installer asks Maya itself (`MAYA_APP_DIR`, else `internalVar(userAppDir=True)` minus the version segment) rather than guessing from Windows environment variables. That matters on machines where Documents is redirected to another drive: guessing produces `C:\Users\<you>\Documents\maya`, while Maya is actually loading from somewhere like `D:\Documents\maya`, and the install appears to work until the first restart loads nothing. The chosen folder is the shared, un-versioned `maya/scripts` - one install serves every Maya version you have.

**The startup wiring.** In BOTH modes, the bootstrap block is written to *your own* `<maya folder>/scripts/userSetup.py` - the payload location is a separate question from how this machine finds it. The block sits between `# FABRICATOR_START` and `# FABRICATOR_END` markers, adds the install root (and its `_vendor` folder) to `sys.path`, and defers `maya_startup.run()` plus the menu/shelf/hotkey builders and the toolbar restore. Everything outside those two markers is preserved byte-for-byte, so your own startup code and other tools' blocks are untouched. Re-running the installer replaces only that block, never appends a second one. The block also records `# FABRICATOR_MODE: copy` or `linked`, which is how the uninstaller later knows whether the files at that path are ours to delete.

**Updating** is just re-running the installer from a newer download. It detects the previously wired install and prefills the Install Directory with it, so the update lands on top of itself rather than nesting a second copy. Your authored project configs are never in the blast radius: they live outside the install tree at `<maya folder>/fabricator_project_configs/`, so neither an update nor an uninstall can destroy them.

**No restart needed.** After wiring, the installer runs the same calls the bootstrap would have run at startup, so the toolbar appears immediately. On a first install this is also where the welcome tour fires: a four-step walkthrough of the toolbar button, Settings, the hover cards, and Project Setup. It shows once ever, and skipping counts as seeing it.

**The toolbar is the front door.** A fresh install loads the toolbar and the hotkeys, and deliberately does *not* add a FabricatorStudio menu or shelves to your Maya. If you want either, turn it on in **Settings** (the toolbar gear) under MAYA INTEGRATION - both build immediately on enable, no restart. Existing installs keep whatever they already had.

**ML dependencies.** The installer itself makes zero network calls. The one tool with a heavier appetite, AutoSkin, downloads its inference backend on demand from inside the tool (a button appears only when the backend is missing), so nothing large rides along with this install.

**Uninstalling.** Drag `Fabricator_Uninstall.py` into the viewport the same way. It strips exactly the sentinel block from `userSetup.py`, removes the FabricatorStudio menu and shelves from the running session, and deletes the toolbar prefs file. What it does with the files depends on the recorded mode: a **copy** install has its whole `fabricator_studio/` folder removed, while a **linked** (shared) install has nothing deleted at all - only this machine's wiring is undone, because a shared payload is never one artist's to remove.

## Gotchas
- **Drag the installer from the unzipped folder, not from a repository checkout.** The installer needs its `Fabricator_Data/` payload folder sitting beside it; without one it refuses with a clear message rather than half-installing.
- **The Install Directory is the PARENT.** Pick `.../maya/scripts` and the toolset lands at `.../maya/scripts/fabricator_studio/`. Pointing it at an existing `fabricator_studio` folder would nest a second one - the prefill already accounts for this on an update, so accept the prefilled path unless you are deliberately relocating.
- **Shared installs and project configs are separate decisions.** Running the tools from a network folder does not by itself share project configs; those follow `FABRICATOR_PROJECT_CONFIGS` or the shared-configs pointer in [Settings](settings.md).
- **A read-only share is fine for linked mode** (nothing is written to it), but a *copy* install into a read-only or unchecked-out Perforce path will fail on the copy step. Check it out first, or install locally.
- **Uninstall deletes the toolbar prefs**, which includes the "you have seen the welcome tour" flag. Uninstalling and reinstalling therefore replays the first-run tour - useful when you want to see it again, surprising if you did not expect it.
- **The dialog has no title bar**, by design. Drag it by its background; Cancel or the Esc key closes it.
- **Maya 2022 to 2024 ship PySide2 and Maya 2025+ ships PySide6.** The installer supports both, but it is the one file in the toolset that has to - do not copy patterns out of it into regular tool code, which is PySide6-only.

## Troubleshooting
**"Fabricator_Data/maya_tools not found next to the installer."** - You dragged a copy of `Fabricator_Install.py` that is not sitting beside its payload folder. Drag the one inside the unzipped release folder.

**"FabricatorStudio requires Maya 2022 or newer (detected ...)."** - The version guard refused rather than installing something that cannot run. Nothing was written.

**Everything installs, but the next Maya launch has no toolbar.** - The bootstrap block did not run. Check that `<maya folder>/scripts/userSetup.py` contains a `# FABRICATOR_START` block, and that the path in `_FAB_REPO_ROOT` still exists (a moved or renamed install folder will do this). Re-running the installer repairs the wiring.

**"Installed. Menu/shelf build hit an error and will retry on next Maya launch."** - The files landed and the startup wiring is correct; only the immediate in-session build stumbled. Restart Maya and it will load normally.

**The toolbar is there but every icon is blank.** - `icons/` is not sitting beside `maya_tools/` inside the install folder. Re-run the installer rather than moving folders by hand.

**A second FabricatorStudio setup keeps loading.** - There is probably a second wiring: a hand-written `sys.path` entry or an older block in another `userSetup.py` (Maya reads one per version folder as well as the shared `scripts` folder). Remove the older one; the installer only manages its own sentinel block.

**I want the menu and shelves back.** - Settings (the toolbar gear) - MAYA INTEGRATION - tick "Load FabricatorStudio menu" and/or "Load FabricatorStudio shelves". They build on the spot.
