# roblox-studio-mcp (Python)

A lightweight, **dependency-free** Python client for [MCP (Model Context Protocol)](https://modelcontextprotocol.io) servers — with built-in convenience for the **Roblox Studio MCP**.

No third-party packages: just the Python standard library (`asyncio`, `subprocess`, `json`). Works with Python 3.9+.

## What it does

- Launches any MCP server over the **stdio** transport and speaks the JSON-RPC 2.0 protocol to it.
- Performs the `initialize` handshake, lists tools, and calls tools.
- Ships a `RobloxStudio` convenience client that resolves the `studio_id` once and
  injects it into every tool call that needs it.

## Install

```powershell
pip install -e .
```

## Quick start (Roblox Studio)

Make sure Roblox Studio is open (and its MCP plugin enabled), then:

```python
import asyncio
from roblox_studio_mcp import RobloxStudio

async def main():
    async with await RobloxStudio.connect() as studio:
        # List every tool the Studio MCP exposes
        for tool in await studio.list_tools():
            print(tool.name)

        # Run Luau in Studio (datamodel_type: "Edit", "Client", or "Server")
        result = await studio.execute_luau("return 1 + 1")
        print(result.text())  # -> 2

        # Or call any tool by name; studio_id is injected automatically
        result = await studio.call("inspect_instance", {"path": "Workspace"})
        print(result.text())

asyncio.run(main())
```

## Using the generic client

The underlying `MCPClient` works with **any** stdio MCP server:

```python
import asyncio
from roblox_studio_mcp import MCPClient

async def main():
    # e.g. the filesystem MCP server
    client = MCPClient("npx", ["-y", "@modelcontextprotocol/server-filesystem", "."])
    async with await client.connect() as c:
        for tool in await c.list_tools():
            print(tool.name)

asyncio.run(main())
```

The Roblox Studio server itself is launched as
`cmd.exe /c "cd /d %LOCALAPPDATA%\Roblox && .\mcp.bat"` — the default
`command`/`args` used by `RobloxStudio.connect()` on Windows. On macOS it
instead runs `/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP`
directly (no shell); `default_command()` / `default_args()` /
`default_shell()` pick per platform, and explicit `command`/`args`/`shell`
options always win.

## Handling multiple Studio instances

If you have more than one Studio open, list them and pick the one you want:

```python
async with await RobloxStudio.connect() as studio:
    for s in await studio.list_studios():
        print(s["id"], s["name"])

    studio.set_studio_id("the-id-you-want")  # or pass studio_id=... to connect()
```

## Disabling tools

Pass a set of tool names to hide them from `list_tools` and refuse to call them:

```python
async with await RobloxStudio.connect(
    disabled_tools={"generate_mesh", "segment_mesh", "generate_material"}
) as studio:
    ...
```

`MCPClient` accepts the same `disabled_tools` argument for any MCP server.

## Singleton connection

`RobloxStudio.connect()` returns a single process-wide shared connection by
default — repeat calls reuse the same `StudioMCP.exe` process instead of
spawning a fresh proxy:

```python
import asyncio
from roblox_studio_mcp import RobloxStudio, close_singleton

async def main():
    a = await RobloxStudio.connect()   # launches the shared process (once)
    b = await RobloxStudio.connect()   # same connection, same process
    ...                                # reuse it across calls
    await close_singleton()
```

`studio_id` is optional and auto-resolves to the first open Studio — no pin
needed. Pass `singleton=False` to `connect()` for an isolated per-call
connection, or call `get_singleton()` / `close_singleton()` directly for the
same shared behavior.

A fresh proxy needs a moment after its handshake before its Studio uplink is
usable. `resolve_studio_id()` rides through that transient "Unable to reach
Roblox Studio" symptom (retrying up to `timeout=10.0` seconds) so the first
tool call through a new connection just works; tune with
`resolve_studio_id(timeout=..., interval=...)`. Any other error — including a
genuinely empty Studio list — still raises immediately.

## Stdio servers

```powershell
# Transparent proxy (same tools as StudioMCP)
python -m roblox_studio_mcp.server

# Extended proxy (adds extended_* tools)
python -m roblox_studio_mcp.extended_server
```

## Extended helpers

```python
import asyncio
from roblox_studio_mcp.extended import RobloxStudio, write_like_multi_edit

async def main():
    async with await RobloxStudio.connect() as studio:
        status = await write_like_multi_edit(
            studio,
            "game.ServerScriptService.MyScript",
            "print('hello')",
            create_if_missing=True,
        )
        # status is "created", "wrote", or "unchanged"

asyncio.run(main())
```

## Examples

Runnable scripts live in `examples/` (run from this folder):

```powershell
python -m examples.list_tools
python -m examples.run_luau
python -m examples.singleton_usage
python -m examples.walk_jump
python -m examples.write_like_multi_edit game.ServerScriptService.MyScript --create
python -m examples.wait_for_studio 600
```

Each mirrors a script in `../node/examples/` — same behavior, same output
shape, only the language idioms differ (see the repo-root README for the
Python ↔ TypeScript naming map).

## API overview

| Class / function | Purpose |
| --- | --- |
| `MCPClient(command, args, ...)` | Generic stdio MCP client |
| `RobloxStudio.connect(...)` | Studio-specific convenience client |
| `client.list_tools()` | `list[Tool]` |
| `client.call_tool(name, arguments)` | `CallToolResult` |
| `studio.call(name, arguments)` | Like above, but auto-injects `studio_id` |
| `studio.execute_luau(code, datamodel_type)` | Run Luau in Studio |
| `studio.get_studio_state()` / `start_play()` / `stop_play()` | Play-mode helpers |
| `get_singleton(studio_id=...)` / `close_singleton()` | Process-wide shared connection |
| `script_search_and_read(studio, root_path, ...)` | Search + batch-read scripts (sources truncated, with line counts) |
| `platform_defaults()` / `default_command()` / `default_shell()` | Per-platform proxy launch settings (Windows `mcp.bat` vs macOS binary) |
| `disabled_tools={...}` (client/studio) | Hide and refuse specific tools |

`CallToolResult` exposes `.text()` (concatenated text blocks) and `.json()`
(best-effort JSON parse of the response).

## Development

Run from this folder:

```powershell
pip install -e ".[dev]"
python -m pytest tests
```

No dev extras installed? The suite is plain `unittest`, so this works too:

```powershell
python -m unittest discover -s tests
```

## License

MIT
