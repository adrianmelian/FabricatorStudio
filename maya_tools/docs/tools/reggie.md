---
title: Reggie - Your AI TA
summary: Your resident AI tech artist, docked right inside Maya. Reggie can see your scene and your Fabricator rig, so ask him anything - why a build failed, where a rig went weird, how a tool works - and he answers with real scene facts and paste-ready fixes. Like having a senior TA on call, minus the coffee runs.
category: framework
gif: ../media/reggie.gif
video:
---

# Reggie - Your AI TA

## What it does
Reggie is the AI TA built into the Bridge toolbar: a docked panel (AI.TA) where
you talk to an assistant that can actually inspect the open Maya scene and the
Fabricator rig in it. He reads through the same read-only operation registry as
the Connect Your AI bridge (`handlers.py`), so he can pull scene summaries,
build reports, component details, and node state to ground his answers in what
is really in your file - he never guesses when he can look.

Reggie is read-only by construction: he can see everything and touch nothing.
When something needs changing, he hands you a script to paste into the Script
Editor and run yourself, so you stay in control of every edit to your scene.

## When to reach for him
- A Build Rig or Unbuild failed and you want to know why, in plain language.
- Something in the rig looks wrong and you want a second pair of eyes that can
  actually read the node graph.
- You forgot how a KS tool or Fabricator component works and want the answer
  without leaving Maya.

## Related
For connecting your OWN external AI client (Claude Code, Claude Desktop,
Cursor) to the same read-only bridge, see Connect Your AI under Settings >
Connect AI on the toolbar strip.
