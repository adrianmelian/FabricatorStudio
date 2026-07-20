# fabricator-mcp — publish checklist

Ordered steps for Adrian to take `fabricator-mcp` from "built locally" to
"live on PyPI, verified working for a stranger." Nothing in this file is
automated; each step is a manual, credentialed action.

## 0. Blocking dependency: the license decision — RESOLVED 2026-07-11/12

STALE (2026-07-18): license re-ruled to BUSL-1.1 (free under $2M revenue; see repo LICENSE + COMMERCIAL-LICENSE.md); pyproject now carries license = "BUSL-1.1" and fabricator-mcp/LICENSE is the BSL text. Original note kept for history: Apache-2.0 landed on the free core (FS bb6a842; attorney engagement
declined, self-representation position documented). fabricator-mcp/
pyproject.toml then carried `license = "Apache-2.0"` + `license-files`,
and `fabricator-mcp/LICENSE` was the Apache-2.0 text (copied from the
repo root). Nothing here blocks anymore.

## 1. One-time PyPI account and Trusted Publisher setup

1. Create (or confirm you have) a PyPI account at pypi.org, with 2FA
   enabled (PyPI requires this for publishing).
2. Register a **pending Trusted Publisher** for the project name before
   it exists yet: https://pypi.org/manage/account/publishing/
   - PyPI project name: `fabricator-mcp`
   - Owner: `adrianmelian`
   - Repository name: `FabricatorStudio`
   - Workflow filename: `publish-fabricator-mcp.yml`
   - Environment name: `pypi`
3. In the GitHub repo (`adrianmelian/FabricatorStudio`): **Settings ->
   Environments -> New environment**, name it `pypi` (must match step 2
   exactly). Optional but recommended: add yourself as a required
   reviewer so every publish needs a manual approval click before it
   goes out.
4. No PyPI API token needs to be stored anywhere. Trusted Publishing
   mints a short-lived OIDC token per workflow run; there is nothing to
   copy into a GitHub secret and nothing to rotate later.

This only needs to happen once, ever (unless the repo or workflow file
is renamed).

## 2. Version bump policy

- `fabricator-mcp` is versioned independently of the rest of the
  FabricatorStudio repo (it is its own PyPI package).
- Bump `version` in `fabricator-mcp/pyproject.toml` **and**
  `__version__` in `fabricator-mcp/src/fabricator_mcp/__init__.py` and
  the "currently at version" line in `fabricator-mcp/README.md` — all
  three together, every release. `tests/test_spec_parity.py` and the
  rest of the suite don't check this, so it's on the honor system;
  eyeball all three before tagging.
- Use plain semver (`0.1.0`, `0.1.1`, `0.2.0`, ...). Bump the protocol
  version in `bridge_client.py` / the Maya-side bridge separately if a
  wire-format change requires it — that is unrelated to this package
  version.
- Commit the version bump normally on `main` first. Do **not** bump the
  version in the same commit as unrelated work; keep it an isolated,
  easy-to-spot commit.

## 3. Tag and push

Once the version bump commit is on `main`:

```
git tag fabricator-mcp-v<version>      # e.g. fabricator-mcp-v0.1.0
git push origin fabricator-mcp-v<version>
```

Pushing that tag triggers `.github/workflows/publish-fabricator-mcp.yml`:
it builds the sdist + wheel, then publishes to PyPI via Trusted
Publishing. Watch the Actions tab; if you added a required reviewer on
the `pypi` environment, you'll need to approve the `publish` job there.

## 4. Post-publish verification

### 4a. Bare install, no repo present

On a machine (or a throwaway venv on this machine) that has **no**
FabricatorStudio checkout and no local `fabricator-mcp` install:

```
uvx fabricator-mcp --help
```

Expect the `--port` help text to print with no errors, no "no matching
distribution," no reference to a local path. This proves the PyPI
package resolves and its entry point runs standalone.

### 4b. CP3 — the stranger's-machine test

This is the real acceptance bar: does a stranger who has never seen this
repo, following only the README, get a working connection? Verify these
two snippets byte-for-byte against what the Maya-side "Connect AI"
popover actually copies (source of truth:
`maya_tools/framework/toolbar/popovers.py`, `client_config_snippet()`),
**and** against the plain (non-"until this is on PyPI") blocks in
`fabricator-mcp/README.md`:

**Claude Code:**
```
claude mcp add fabricator -- uvx fabricator-mcp
```

**Claude Desktop / Cursor** (`mcpServers` block):
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

Steps:
1. Open the DevBot toolbar's Connect AI popover in Maya, click each
   client's Copy button, and diff the clipboard contents against the two
   blocks above. They must match exactly (including the default port
   being omitted, since `--port` is only appended when non-default).
2. Run the Claude Code command above on a machine with no prior
   `fabricator-mcp` install and confirm `claude mcp list` shows
   `fabricator` connected once the Maya bridge is started.
3. Drop the JSON block into a clean Claude Desktop or Cursor config and
   confirm the same.
4. Once this passes, remove (or leave, your call) the README's
   "Until this is on PyPI" callout — the plain commands now work for
   real, so the callout is no longer load-bearing, though it's harmless
   to leave as a historical note if you'd rather not touch the README
   again.

### 4c. Sanity check the listing itself

Open `https://pypi.org/project/fabricator-mcp/` and confirm: README
renders (long description content-type was set to `text/markdown`),
the three project URLs (Homepage/Repository/Issues) resolve, and the
version shown matches what you tagged.
