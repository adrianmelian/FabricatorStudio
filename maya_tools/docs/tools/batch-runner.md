---
title: Batch Runner
summary: Run a list of Maya scenes through a batch - each scene opens in its own isolated mayapy subprocess, runs an optional Python script, and optionally exports and saves. Live log, cancel-responsive, and the editor state restores between sessions.
category: framework
gif: ../media/batch-runner.gif
video:
---

# Batch Runner

## What it does
Batch Runner processes a list of Maya scene files without opening any of them in your session. For each scene it spawns a fresh `mayapy` subprocess and, in order, opens the scene, runs an optional Python script you provide, then optionally exports and saves. Progress streams live into the log as each scene reports back, and Cancel (or Esc) stays responsive the whole time, even while a scene is mid-run. Because every scene runs in its own isolated subprocess, one bad scene cannot corrupt your Maya session or stop the rest of the batch. Your scene list, script, and checkboxes are remembered between sessions.

## Quick start
1. Open Batch Runner and press **+ Add Scenes** to add the Maya files you want to process (**- Remove Selected** and **Clear All** manage the list).
2. Type a Python script in the **Script (Python)** box if you need one - it runs after each scene opens, before export and save. Leave it empty to just open/export/save.
3. Tick **Export** and/or **Save** under "Per scene" (Export is on by default, Save is off).
4. Press **Run**. Watch the log stream each scene's progress; press **Cancel** or Esc to stop after the current scene.

## How it works
Each scene runs end to end in its own `mayapy.exe` subprocess (the same isolation the anim exporter uses), with `maya.standalone` initialized and `fbxmaya` loaded up front. The parent window hands the child a JSON payload (scene path, your script, the export/save flags) and then polls it while pumping Qt events, which is what keeps the Cancel button and Esc live during the roughly 5 to 20 seconds of silent Maya boot per scene. The child reports each stage back as a `[batch]` marker (opened, script ok, export ok, save ok, or the matching error), and the window turns those into per-scene results in the log. Cancel is graceful: the current scene finishes, and the batch simply stops launching new ones.

Your script runs with `maya.cmds` available in a headless Maya. Load any extra plugins it needs at the top of the script (`fbxmaya` is already loaded). Batch Runner finds `mayapy.exe` from `MAYA_LOCATION`, then from the running Maya's own bin folder, then from known install fallbacks.

## Options
- **Scenes** - the list of Maya files to process, with Add / Remove Selected / Clear All.
- **Script (Python)** - runs after each scene opens, before export and save. Optional.
- **Export** (per scene, default on) - export each scene after the script runs.
- **Save** (per scene, default off) - save each scene after the script runs.
- **Run** / **Cancel** - start the batch, or stop it gracefully after the current scene.

## Gotchas
- Each scene runs headless in `mayapy` (`maya.standalone`), not in your GUI Maya, so your script must use `maya.cmds` and cannot rely on anything GUI-only. Load any plugins it needs at the top of the script; `fbxmaya` is preloaded.
- The script runs after the scene opens and before export and save - order it accordingly.
- Cancel is graceful, not a kill: it lets the current scene finish, then skips the rest. There is no mid-scene abort.
- Export is on by default and Save is off by default. A batch that only fixes scenes in place wants Save on and Export off.
- Batch Runner must be able to locate `mayapy.exe`. If it cannot, set the `MAYA_LOCATION` environment variable to your Maya install root (the folder containing `bin/mayapy.exe`).
- The editor state (scene list, script, checkboxes) is saved to `scene_batch_state.json` in your Maya user app dir and restored next session. It is schema-versioned, so a state file from an incompatible older build is discarded rather than loaded with wrong defaults.
- The public name is Batch Runner; the code identity is `scene_batch` / `SceneBatchWindow`.

## Troubleshooting
**"Could not locate mayapy.exe."** Batch Runner could not find the batch interpreter. Set `MAYA_LOCATION` to your Maya install root and try again.

**A scene shows OPEN_FAIL and nothing else ran for it.** The scene could not be opened (missing file, unreadable, or a fatal error on load). The rest of the batch continues; check the log line for that scene.

**My script did nothing / errored.** The script runs in a headless Maya - GUI-only commands will not work, and a plugin your script relies on may not be loaded. Load it at the top of the script and re-run; the log shows a SCRIPT_ERROR line for the failing scene.

**Cancel did not stop it immediately.** By design - Cancel finishes the scene in flight and then stops launching new ones, so the current scene is never left half-processed.
