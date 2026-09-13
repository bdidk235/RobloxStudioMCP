# roblox-studio-mcp

Two **dependency-free** clients for
[MCP (Model Context Protocol)](https://modelcontextprotocol.io) servers, with
built-in convenience for the **Roblox Studio MCP**:

- `python/` — the **original** Python client (stdlib only, Python 3.9+).
  Full guide: `python/README.md`.
- `node/` — the TypeScript/Node port (Node.js built-ins only, Node 18+).
  Full guide: `node/README.md`.

Both speak the same protocol to the same Studio MCP proxy, expose the same
tool catalog, and ship the same extended helpers. Pick whichever language fits
your tooling — behavior is kept in parity, and each side's test suite
(`python/tests`, `node/tests`) covers the same ground without needing Studio.

## How both work

Roblox Studio exposes its MCP server through a local proxy process: on
Windows it is launched as
`cmd.exe /c "cd /d %LOCALAPPDATA%\Roblox && .\mcp.bat"`, on macOS it is the
`StudioMCP` binary inside the Studio app bundle (run directly, no shell).
Either way it speaks newline-delimited JSON-RPC 2.0 over stdio: one JSON
object per line, with an `initialize` handshake followed by `tools/list` and
`tools/call`.

Both clients follow the same shape around that protocol:

- `MCPClient` — a generic stdio MCP client (works with any MCP server):
  connect + handshake, list tools, call tools.
- `RobloxStudio` — a convenience wrapper that resolves `studio_id` once
  (via `list_roblox_studios`, first instance wins) and injects it into every
  tool call whose schema declares it.
- Singleton connection — `connect()` returns a process-wide shared
  connection by default; close it explicitly (`close_singleton()` /
  `closeSingleton()`).
- Readiness retry — a fresh proxy answers `list_roblox_studios` with
  "Unable to reach Roblox Studio" for a beat after its handshake; both
  clients ride through exactly that transient symptom (up to ~10 s) and let
  every other error throw immediately.
- Extended helpers — full-file writes (`write_like_multi_edit` /
  `writeLikeMultiEdit`), batch edits with graceful skipping
  (`update_like_multi_edit` / `updateLikeMultiEdit`), script search-and-read,
  insert-from-file, console watch, module scaffolding, play-test summaries,
  and execute-from-file. Also served as `extended_*` tools by the extended
  stdio proxy.
- Wire format is identical on both sides (`studio_id`, `datamodel_type`,
  `target_path`, …); only the local naming differs (Python `snake_case`,
  TypeScript `camelCase`).

## Layout

```text
node/                   TypeScript/Node client (port)
  src/                    MCPClient, RobloxStudio, servers, extended/
  tests/                  vitest suites (no Studio needed)
  examples/               runnable TS examples (npx tsx examples/<name>.ts)
python/                 Python client (original)
  src/roblox_studio_mcp/  MCPClient, RobloxStudio, servers, extended/
  tests/                  unittest suites (no Studio needed)
  examples/               runnable Python examples (python -m examples.<name>)
docs/                   docs site, opens from disk (docs/index.html)
README.md               this overview
```

## Prerequisites

Roblox Studio open with a place loaded, and its MCP server enabled
(Assistant → Manage MCP Servers → *Enable Studio as MCP server*).

## Examples

Each example exists on both sides with the same behavior:

| What it shows | Python | TypeScript |
| --- | --- | --- |
| List every tool | `python -m examples.list_tools` | `npx tsx examples/list_tools.ts` |
| Run Luau, print result | `python -m examples.run_luau` | `npx tsx examples/run_luau.ts` |
| Shared connection + disabled tools | `python -m examples.singleton_usage` | `npx tsx examples/singleton_usage.ts` |
| Play, walk, jump, leave | `python -m examples.walk_jump` | `npx tsx examples/walk_jump.ts` |
| Full-file write helper | `python -m examples.write_like_multi_edit <target> [--create]` | `npx tsx examples/write_like_multi_edit.ts <target> [--create]` |
| Wait for Studio over MCP | `python -m examples.wait_for_studio [timeout_seconds]` | `npx tsx examples/wait_for_studio.ts [timeoutSeconds]` |

(Python commands run from `python/`; TypeScript commands run from `node/`.)

## Naming map

| Concept | Python | TypeScript |
| --- | --- | --- |
| Connect | `RobloxStudio.connect(studio_id=…)` | `RobloxStudio.connect({ studioId: … })` |
| List tools | `studio.list_tools()` | `studio.listTools()` |
| Call a tool | `studio.call(name, args)` | `studio.call(name, args)` |
| Run Luau | `studio.execute_luau(code)` | `studio.executeLuau(code)` |
| Shared connection | `get_singleton()` / `close_singleton()` | `getSingleton()` / `closeSingleton()` |
| Full-file write | `write_like_multi_edit(…, create_if_missing=…)` | `writeLikeMultiEdit(…, { createIfMissing: … })` |
| Batch edits | `update_like_multi_edit(…, skip_missing=…)` | `updateLikeMultiEdit(…, { skipMissing: … })` |

## Stdio servers

Each side ships a transparent proxy (same tools as StudioMCP) and an
extended proxy (adds the `extended_*` tools):

```powershell
# Node (run from node/)
node ./dist/server.js
node ./dist/extendedServer.js

# Python (run from python/)
python -m roblox_studio_mcp.server
python -m roblox_studio_mcp.extended_server
```

## Development

```powershell
# Node (run from node/)
pnpm install
pnpm typecheck
pnpm test

# Python (run from python/)
pip install -e .[dev]
python -m unittest discover -s tests
# or, with the dev extras installed:
python -m pytest tests
```

## Docs site

`docs/index.html` opens straight from disk (no server or build step) and
explains the architecture, the full tool catalog with inputs and outputs,
and measured performance numbers.

## Continuous integration

`.github/workflows/ci.yml` runs four jobs on Windows and macOS:

| Job | What it runs | Needs Studio? |
| --- | --- | --- |
| `python-test` | `pytest` in `python/` | No (fakes throughout) |
| `node-test` | typecheck + vitest + build in `node/` | No (fakes throughout) |
| `python-studio` | installs Studio, waits for it, then `pytest` with the live integration suite | Yes |
| `node-studio` | installs Studio, waits for it, then vitest with the live integration suite | Yes |

The `*-studio` jobs install Studio straight from `setup.rbxcdn.com`
(`RobloxStudioInstaller.exe` on Windows, `RobloxStudio.dmg` on macOS), log in with a `ROBLOSECURITY`
secret (a burner account is recommended) stored as a Repository secret,
launch Studio, and poll
`examples/wait_for_studio` before running the suites. Without the secret the
studio jobs skip gracefully. They need three things
from that account:

- It must be able to log in (the `ROBLOSECURITY` cookie).
- It must have *Enable Studio as MCP server* turned on at least once
  (Assistant → Manage MCP Servers) — the setting roams with the account,
  and the jobs additionally pre-seed it from the cookie.
- It opens place `95206881` in edit mode, so the account needs
  edit access to it (or swap in your own `placeId`/`universeId` in
  `.github/workflows/ci.yml`).

Without the secret the studio jobs skip instead of failing. Locally, the
same integration suites run with `ROBLOX_STUDIO_MCP_INTEGRATION=1` once
Studio is open with a place loaded:

```powershell
# Python (run from python/)
python -m examples.wait_for_studio 600
$env:ROBLOX_STUDIO_MCP_INTEGRATION = "1"
python -m pytest tests/test_integration_studio.py
```

## License

MIT
