# fabricator-mcp

`fabricator-mcp` connects your own AI assistant (Claude Code, Claude Desktop, Cursor, or any other MCP client) to a Maya session running the FabricatorStudio toolset. Once connected, your assistant can inspect your open scene and your Fabricator rig to help you troubleshoot: scene contents, node details, a viewport screenshot, rig status and components, the last build report, and the bundled docs.

You bring your own AI client and your own API key. This project runs no servers of its own and charges nothing: it is a small local bridge between your Maya session and whatever assistant you already use.

The short version: the AI can see. It cannot touch.

## The read-only guarantee

The bridge exposes a fixed set of read and inspection operations. There is no operation in the registry that writes an attribute, creates or deletes a node, changes a connection, renames or reparents anything, saves the file, or runs a rig's auto-fix.

This is not a claim you have to take on faith. The entire op registry is one file in the FabricatorStudio toolset:

```
maya_tools/framework/toolbar/ai_bridge/handlers.py
```

Read it. Every handler in it is a pure read of the scene, and a test (`test_ai_bridge_maya.py`) mechanically scans the file for mutating calls as a backstop against a mutating op sneaking in later.

Two things are worth stating plainly rather than hiding:

1. **Taking a viewport screenshot writes a temporary file.** The screenshot op writes a throwaway PNG to your OS temp folder and deletes it once it has read the bytes back. That is a file write outside the Maya scene, not a scene mutation; the read-only claim is specifically about your scene (the graph, its nodes, its attributes, its connections).
2. **Reading a node attribute makes Maya evaluate its dependency graph.** In a scene that contains a malicious expression node or plug-in node upstream of the attribute being read, that evaluation can run code. This is inherent to how Maya works with any scene you open, not something this bridge adds: selecting a node, playing the timeline, or refreshing the viewport all trigger the same graph evaluation. Only open rigs you trust, the same as you always would.

## Data flow and privacy

Nothing leaves your machine except what your chosen AI client sends to your chosen provider. `fabricator-mcp` collects no telemetry and phones nothing home.

The bridge listens on loopback (`127.0.0.1`) only, and only while you have started it. It is off by default.

## Setup

**Prerequisites**: Maya with the FabricatorStudio toolset installed. In Maya, open the DevBot toolbar's "Connect AI" popover and click **Start**.

Then configure your client with the snippet below. The popover has a Copy button for each client, so you don't have to type these by hand; they are reproduced here for reference.

> **Until this is on PyPI:** `fabricator-mcp` isn't published yet, so `uvx fabricator-mcp` won't resolve anything. Point `uvx` at this checkout instead with `--from <path-to-fabricator-mcp>`, for example:
>
> ```
> claude mcp add fabricator -- uvx --from D:/Documents/KinematicSolutions/fabricator-mcp fabricator-mcp
> ```
>
> and the equivalent `"args"` for Claude Desktop / Cursor: `["--from", "D:/Documents/KinematicSolutions/fabricator-mcp", "fabricator-mcp"]`. Swap in your own path to this repo. Once the package ships to PyPI, switch back to the plain commands below.

### Claude Code

```
claude mcp add fabricator -- uvx fabricator-mcp
```

If you started the bridge on a non-default port, append `--port <n>` to that command.

### Claude Desktop / Cursor

Both use the same `mcpServers` block:

```json
{
  "mcpServers": {
    "fabricator": {
      "command": "uvx",
      "args": ["fabricator-mcp"]
    }
  }
}
```

For Claude Desktop, this goes in its `claude_desktop_config.json`. For Cursor, this goes in your client's MCP config file (typically `~/.cursor/mcp.json`, or a project-local `.cursor/mcp.json`); check your installed Cursor version's docs if the exact filename or location has moved.

If you started the bridge on a non-default port, add `"--port", "<n>"` to the `args` list.

The default bridge port is 6292.

## What your AI can do with it

Once connected, your assistant has tools to:

- Read a scene summary and the details of a specific node.
- Take a viewport screenshot.
- Read Fabricator's status, describe the rig and its components, read the rig's skin/component bindings, and read the last build report.
- Run Fabricator's pre-build checks and read the scene's structural validation.
- Read the bundled troubleshooting and concept docs.
- If a real fix attempt has failed, help you file a well-formed GitHub bug report: it hands back a prefilled "New issue" URL for you to review and submit yourself, under your own GitHub account. It never opens a browser or posts anything on your behalf.

The assistant's behavioral contract arrives automatically at connect, through the MCP server's own instructions: no read required, no step to forget. It is told to propose every fix as steps for you to perform rather than claiming to have done them, and to try a documented fix before ever offering to file a bug. The full operating skill behind that contract also ships as the `fabricator://skill` resource (and as a bundled skill file in the FabricatorStudio toolset, for native install into a compatible AI client).

## Version compatibility

The server and the Maya-side bridge exchange protocol versions on every connect. If they don't match, you'll get a message telling you to update whichever side is behind: the FabricatorStudio toolset in Maya, or your `fabricator-mcp` package. Keep the two roughly in step.

`fabricator-mcp` is currently at version 0.1.0.

## Troubleshooting

**"Maya isn't listening..."** is the message you'll see most. It means either the bridge isn't started (open the DevBot toolbar's Connect AI popover and click Start), or it's listening on a different port than your client is configured for (pass `--port` on both sides to match).

For anything rig-specific, ask your assistant to read the bundled docs (it has a tool for this), or open `maya_tools/docs/assistant/troubleshooting.md` in the FabricatorStudio toolset directly.
