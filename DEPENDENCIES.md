# Dependencies

Fabricator's core tools (rigging, skinning, export, curve library, Pose Studio and Animation Studio, renamer) run on Maya and Python alone, no extra install needed. This document covers the one tier that isn't: the ML-backed skinning tools.

## ML skinning tools (Advanced tier)

Three tools use machine-learning solvers that live outside Maya's own Python:

- **Auto Skin BBW bind**: bounded biharmonic weights, a geometry-aware automatic skin bind.
- **Delta mush**: smoothing pass on top of a skin bind.
- **DemBones bake**: bakes an animated mesh sequence down to a skinCluster plus joint transforms.

These need `numpy`, `scipy`, `libigl`, `tetgen`, `pymeshfix`, and `py_dem_bones` installed into Maya's bundled Python interpreter (`mayapy`). None of this installs automatically when you install Fabricator. It's opt-in, and only runs if you ask for it.

## Installing (one click, in-tool)

The first time you open a tool that needs these packages, you'll get a prompt offering to install them for you. Accept it, and Fabricator runs the same install described below on your behalf, then retries the operation automatically.

You can also trigger the same install from the "Install ML dependencies" checkbox in `Fabricator_Install.py` at install time, or manually from any of the ML tools if you skipped it earlier.

### What it runs

```
mayapy -m pip install --user numpy==2.4.6
mayapy -m pip install --user scipy==1.17.1
mayapy -m pip install --user libigl==2.6.1
mayapy -m pip install --user tetgen
mayapy -m pip install --user pymeshfix
mayapy -m pip install --user py_dem_bones
```

`numpy`, `scipy`, and `libigl` are pinned. Those exact versions are the newest releases confirmed (via PyPI's package index) to still ship a Windows, Python 3.11 wheel, matching Maya 2025's bundled `mayapy`. Newer releases of all three have dropped that wheel combination. `tetgen`, `pymeshfix`, and `py_dem_bones` are left unpinned since their current releases still support this combination; if a future release breaks that, the fix is to pin them the same way.

Installs go to `mayapy`'s user site-packages (`--user`), never system Python, so this doesn't touch anything outside your Maya user environment.

## Offline behavior

If you're not connected to the internet, the install fails per-package with a clear message rather than a stack trace, and Fabricator tells you it looks like a network problem. The ML tools stay visible and usable either way. Declining the install, or having it fail, doesn't hide or break anything else in the toolset. Reconnect and retry when you're ready.

## Manual install (if you'd rather not use the in-tool flow)

Find your `mayapy.exe` (typically `C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe`) and run the same commands directly from a terminal:

```
"C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" -m pip install --user numpy==2.4.6
"C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" -m pip install --user scipy==1.17.1
"C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" -m pip install --user libigl==2.6.1
"C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" -m pip install --user tetgen
"C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" -m pip install --user pymeshfix
"C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" -m pip install --user py_dem_bones
```

Adjust the path for your Maya version and install location. Restart Maya after installing so the new packages are picked up.
