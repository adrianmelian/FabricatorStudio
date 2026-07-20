---
title: Connect Your AI
summary: Starts a read-only local bridge so your own AI client (Claude Code, Claude Desktop, Cursor) can inspect your Maya scene and Fabricator rig to help you troubleshoot.
category: framework
gif: ../media/connect-your-ai.gif
video:
---

# Connect Your AI

## What it does
Connect Your AI starts a small local bridge (`ai_bridge`) inside the Bridge toolbar that listens on loopback only (`127.0.0.1`, default port 6292) and lets your own AI client, Claude Code, Claude Desktop, Cursor, or any other MCP client, inspect the open Maya scene and the Fabricator rig in it. The bridge's operation registry (`handlers.py`) is read-only by construction: every handler reads scene state and returns data, and none of them write an attribute, create or delete a node, change a connection, rename or reparent anything, or save the file. Your AI reaches the bridge through a separate small package, `fabricator-mcp` (installed and run via `uvx`), which relays each tool call to the bridge over a local connection and never talks to any server but the one on your own machine. Everything lives under Settings > Connect AI on the toolbar strip.

## Quick start
1. In Maya, open the Bridge toolbar's Settings zone and click **Connect AI** to open its popover.
2. Click **Start**. The status pill switches from `[ STOPPED ]` to `[ LISTENING ON <port> ]` (default 6292).
3. Pick your client from the **Client** dropdown (claude-code, claude-desktop, cursor) and click **Copy** to grab that client's config snippet.
4. Set up your client: for Claude Code, run the copied command (`claude mcp add fabricator -- uvx fabricator-mcp`); for Claude Desktop or Cursor, paste the whole copied `mcpServers` block into that client's own MCP config file.
5. In your AI client, ask it to check your Fabricator rig's status. If it can answer with real details about your open scene, the connection is working.

## Workflow
Connect Your AI is not an export step in the Maya-to-Unreal pipeline; it sits alongside the rest of the toolbar as a diagnostic layer, useful whenever a Fabricator rig won't build or an export is behaving oddly, before you get to Skeleton IO / Skin IO / the FBX exporters.

- **Panel**: the Connect AI popover, on the settings side of the strip next to the Settings gear. Real controls: a status pill (`Stopped` / `Listening on <port>`), **Start** / **Stop** buttons, a **Start with Maya** checkbox (autostart, off by default), a **Client** dropdown (claude-code / claude-desktop / cursor), a read-only snippet box, a **Copy** button, and the fixed trust line "READ-ONLY: your AI can see, never touch."
- The AI is instructed (via its MCP server's own connection instructions, and a bundled operating skill it is told to load first) to gather evidence before proposing anything: scene/rig status, pre-build checks, the last build report, the scene report, a viewport screenshot if the problem is visual.
- It then proposes the smallest documented fix from the bundled troubleshooting doc, delivered one of two ways only: a plain `maya.cmds` script for you to paste into the Script Editor (Python tab) and run yourself, or a pointer to a button that already exists in Fabricator's own UI (Build Issues dialog > Fix Selected/Fix All, Build Rig, Edit Rig). It is instructed never to claim it performed a change itself.
- Only after a real fix attempt has failed (or the assistant has genuinely ruled out everything in the troubleshooting doc) does it offer to file a bug: this hands back a pre-filled GitHub "New issue" URL against `adrianmelian/KinematicSolutions` (labeled `bug, needs-triage`), with diagnostics attached. You review it and click submit yourself under your own GitHub account; nothing is auto-posted.
- On a build or unbuild failure specifically, the assistant is told to have you press Ctrl+Z once (Build Rig and Edit Rig are each one undo chunk) before it scans anything, so it diagnoses the pre-failure state rather than the wreckage left behind.

## Gotchas
- The bridge is off by default and stays off across Maya restarts unless **Start with Maya** is checked. If your AI reports it can't reach Maya, the bridge most likely just isn't started yet.
- No authentication, loopback only: any process on your machine can connect to the bridge while it's running. That is a stated trust boundary (the same posture other local DCC bridges use), not a bug, but worth knowing if you leave it running on a shared machine.
- Reading a node's attributes (`get_node_details`) forces Maya to evaluate its dependency graph up to that attribute. In a scene containing a malicious expression or plug-in node upstream of it, that evaluation can run arbitrary code. This is inherent to opening and touching such a scene at all (selecting a node, playing the timeline, and refreshing the viewport all trigger the same evaluation); the bridge doesn't add or widen that risk, but only connect it to scenes you trust.
- The viewport screenshot tool writes one throwaway PNG to your OS temp directory and deletes it after reading the bytes back. That's a real filesystem write outside the Maya scene, disclosed rather than hidden, and it only works in an interactive session (it raises in batch/standalone Maya).
- `get_fabricator_status` deliberately calls a separate read-only mirror of Fabricator's real mode-detection, not the real one: the real one has a self-heal branch that writes (resets stale "built" flags) as a side effect, which would break the read-only contract. If the mirror and the real detection were ever to drift, that's what your AI would see.
- `report_bug` refuses to build an issue URL if `attempted_solutions` is empty or whitespace-only. That's intentional friction to force a real diagnosis pass first, not a broken tool.
- A burst of many requests in one moment is capped at 64 answered per 50ms tick, so a heavy session doesn't stall Maya's main thread; anything past that cap simply waits for the next tick rather than failing.
- The bridge and `fabricator-mcp` exchange a protocol version on every connect. If the Fabricator toolset in Maya and your installed `fabricator-mcp` package drift far enough apart, you get an explicit "update whichever side is behind" message instead of silently broken behavior.

## Troubleshooting
**"Maya isn't listening..."** The bridge isn't started, or it's listening on a different port than your client is configured for. Open the Bridge toolbar's Connect AI popover and click Start; if you changed the port, pass `--port <n>` on both sides (the popover's copied snippet already includes it when the port isn't the default 6292).

**Status pill reads `[ ERROR ]`.** Hover it: the tooltip carries the actual failure text (for example, the port is already in use by something else, or the prefs file couldn't be read/written). Click Stop, then Start again, or set a different port.

**Every tool call fails with "unknown_op."** Your `fabricator-mcp` package is calling an operation the running Maya-side bridge doesn't recognize. Update the FabricatorStudio toolset in Maya, or update your `fabricator-mcp` package, so both sides agree on the same set of operations.

**"Protocol mismatch" message.** The bridge's protocol version and `fabricator-mcp`'s don't match. Update whichever side is behind: the Fabricator toolset in Maya, or the `fabricator-mcp` package your AI client runs.

**A tool call comes back "maya_busy."** Maya's main thread didn't answer within its reply window (about 10 seconds), typically because a long-running operation or an open modal dialog is blocking it. Let Maya go idle, then try again.

**"Propose and attempt a fix first" / report_bug won't run.** `attempted_solutions` was empty. Have the assistant actually try (or explicitly rule out) a documented fix, then call report_bug again with that description filled in.

**"No viewport in batch/standalone mode."** Maya is running headless; there's no viewport to capture. The screenshot tool only works in an interactive Maya session.

**"No such rig registry" / rig tools return "not_found."** There's no Fabricator registry in the current scene, meaning nothing has been loaded or built yet. Load or build a Fabricator rig first, then ask again.

**"No build report stamped in this scene yet."** No Build Rig or Unbuild has run in this scene session, so there's nothing to report. Run a build once, then ask again.
