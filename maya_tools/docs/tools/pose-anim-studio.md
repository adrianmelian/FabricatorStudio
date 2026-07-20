---
title: Pose & Animation Studios
summary: Save and reuse full-rig poses and animation clips across every rig built with Fabricator; poses and clips travel cross-rig by component identity, not node names.
category: animation
gif: ../media/pose-anim-studio.gif
video:
---

# Pose & Animation Studios

## What it does
One popover with two tabs, POSE and ANIM, launching Pose Studio and Animation Studio. Both save and load onto any rig built with Fabricator: a pose or clip saved on one character loads onto another because controls are addressed by component identity (type, side, role), not by node name. Thumbnails are framed in an embedded viewport; search and user-authored sets keep large libraries usable; selection sets mask a pose down to just the controls you choose.

## Quick start
1. Open the Library button on the Bridge toolbar and pick the POSE or ANIM tab.
2. Select the rig controls (or the whole rig) you want to capture.
3. Save, frame the thumbnail, and name it into a set.
4. On another rig, select it and load; the library maps controls by identity.

## Workflow
POSE hosts Pose Studio, ANIM hosts Animation Studio, each embedded in its own tab. Both resolve their library location per project, so a team shares one place. See the Pose Studio and Animation Studio pages for the per-tool detail.

## Gotchas
- Cross-rig load maps by component identity; a control with no matching identity on the target rig is skipped rather than mis-applied.
- Pose mirroring currently lives on the shelf, not inside the Pose Studio UI (roadmap).
