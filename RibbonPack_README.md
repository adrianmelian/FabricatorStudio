# Advanced Ribbon Pack

The Advanced Ribbon Pack adds ribbon-deformation rigging components to
[Fabricator](README.md): a cascading FK-on-a-ribbon-surface component for
tails, tentacles, and other bendy body parts, plus ribbon-flavored arm,
leg, and spine components with per-bone twist/volume ribbon segments on
top of the same IK/FK contract the free components use.

This is a **commercial add-on** for Fabricator. It requires a working free
Fabricator install already present. See [LICENSE-RIBBON-PACK.txt](LICENSE-RIBBON-PACK.txt)
for the license terms.

## What's in the box

- `RibbonPack_Install.py` / `RibbonPack_Uninstall.py` — drag-and-drop
  installer and uninstaller.
- `RibbonPack_Data/` — the pack payload (ribbon modules, the ribbon leg
  limb fragment, doc pages).
- `ribbon_pack_manifest.json` — the exact file list this pack installs
  and removes.
- `LICENSE-RIBBON-PACK.txt` — the commercial license for this pack.

## Install

1. Make sure Fabricator (the free core) is already installed.
2. Download this pack and unzip it.
3. Drag `RibbonPack_Install.py` into the Maya viewport.
4. Click **Install** in the dialog that appears.

No admin rights needed. The installer locates your existing Fabricator
install automatically; if it can't, use the **Browse for core...** button
to point it at the right folder. Ribbon components appear in the Build
Modules component picker and the Load Limb fragment browser immediately
(no Maya restart) whenever a live refresh is possible; otherwise a
restart finishes the job.

Re-running the installer (e.g. after downloading an update) simply
overwrites the pack's files in place — safe to do any time.

## Uninstall

Drag `RibbonPack_Uninstall.py` into the viewport and click **Uninstall**.
This removes exactly the pack's files and nothing else. Any rig you've
already built with ribbon components keeps animating afterward — the
deformation is baked Maya scene content (skinCluster, uvPin, blendShape,
etc.), not Python. You just won't be able to *build a new* ribbon
component, or rebuild an existing one, until you reinstall the pack.

## What's included

- **Ribbon** — a cascading FK chain riding a NURBS ribbon surface, with a
  twist/sine/jiggle/volume dial board. For tails, tentacles, and other
  bendy body parts.
- **RibbonSpine** — the ribbon treatment applied to a spine chain.
- **RibbonIKArm** — an IK/FK arm with per-bone ribbon-twist segments on
  top of the same SimpleIK contract the free arm uses.
- **RibbonIKLeg** — the same treatment for a reverse-foot IK leg, plus a
  ready-to-drop `Leg_RibbonIK` limb fragment (Load Limb).

Swapping a free arm/leg for its ribbon counterpart never loses an
existing animator-facing control — the ribbon build only adds controls
on top of the free naming contract.

## Support

The developer is available for pipeline work. See
[fabricator.studio](https://fabricator.studio).
