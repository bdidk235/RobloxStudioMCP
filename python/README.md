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
    async with await RobloxStudio.connect(singleton=False) as studio:
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

`studio_id` is auto-resolved to the first open Studio — no pin
needed. Pass `singleton=False` to `connect()` for an isolated per-call
connection, or call `get_singleton()` / `close_singleton()` directly for the
same shared behavior.

A fresh proxy needs a moment after its handshake before its Studio uplink is
usable. `resolve_studio_id()` rides through that transient "Unable to reach
Roblox Studio" symptom (retrying up to `timeout=10.0` seconds) so the first
tool call through a new connection just works; tune with
`resolve_studio_id(timeout=..., interval=...)`. Any other error — including a
genuinely empty Studio list — still raises immediately.

### Choosing a Studio

`resolve_studio_id()` returns an id configured by the caller unchanged. When you
have not pinned one, it re-lists on every call and accepts the result **only if
exactly one Studio is connected**. Two or more raises, listing each candidate's
name and id:

```python
await studio.resolve_studio_id()
# MCPToolError: 2 Roblox Studio instances are connected, so no studio_id can be
# inferred: [('Place1', 'sid-a'), ('rbx-re', 'sid-b')]. Pass studio_id= to
# connect() (or per call) to pick one.
```

This is deliberate. List order comes from the proxy mesh and means nothing, so
taking the first entry silently routes work to the wrong Studio — the failure
is invisible in the response, since the reply is well-formed and belongs to
some other place. Resolving per call also means a second Studio opening, or the
first one restarting, is noticed on the next call instead of being masked by a
cached id. `studio.studio_id` stays `None` unless you pin one, so an implicit
resolution never becomes sticky.

### When a pin goes stale

Studio instance ids change on every Studio restart, so a pinned id does not
survive one. When a pinned id is no longer reachable, the error says so plainly
and names the pin, rather than passing the proxy's raw message through:

```python
studio.set_studio_id("some-old-id")
await studio.get_studio_state()
# MCPToolError: The pinned studio_id 'some-old-id' is no longer connected: ...
# Studio instance ids change every time Studio restarts, so a pin does not
# survive one. Re-pin with set_studio_id() using a current id from
# list_studios(), or set_studio_id(None) to fall back to inferring.
```

The pin is deliberately not swapped for whatever Studio happens to be open. A
pin is your explicit choice, and quietly retargeting it is the same failure as
first-wins, so recovery is yours to make: re-pin, or unpin and let inference
apply (which only works while a single Studio is open).

### Lossless capture

`screen_capture` is for a quick look: a small JPEG, inline, instant. It has no
format option — `format`, `image_format`, `output_format`, `mime_type`, `type`,
`quality` and `png` all return byte-identical `image/jpeg` **with no error**, so
"no error" is not evidence an option applied.

`extended_capture` is for when the pixels *are* the measurement. It reads the
framebuffer as raw RGBA and encodes PNG host-side, so it is byte-exact:

```python
from roblox_studio_mcp.extended import RobloxStudio, capture_png

async with await RobloxStudio.connect(studio_id=...) as studio:
    result = await capture_png(studio, save_path="shot.png")
    # {'width': 1233, 'height': 754, 'png_bytes': 642573,
    #  'mime': 'image/png', 'lossless': True, 'save_path': 'shot.png'}
```

Omit `save_path` to get `png_base64` in the result instead. The raw buffer is
also available as `capture_rgba(studio) -> (width, height, rgba, info)`.

How it works, because the constraints are not obvious:

| Step | Detail |
| --- | --- |
| Read | `CaptureService:CaptureScreenshot` → `CreateEditableImageAsync` → **one** `ReadPixelsBuffer`. No 1024px tiling: 2048x1024 (2 M px) reads back exactly. |
| Encode | base64 computed in Luau for the chunked-write path (`buffer.tostring` is *not* base64, it returns a byte-string of the input length); `extended_capture`'s PNG return is encoded host-side instead. |
| Write | chunked into a scratch `ModuleScript` under `PluginGuiService` via `ScriptEditorService:UpdateSourceAsync(target, cb)`. Ceiling 6,291,456 B; 8 MB fails `bad allocation`. |
| Read back | `script_read`, stripping its `     1→` line prefixes. |

A 1233x754 viewport is 4,958,304 base64 chars, so it fits one module with
~1.2x headroom. Larger viewports are refused up front rather than silently
truncated — shrink the viewport or tile the read. Over that ceiling the
`execute_luau` return channel cannot help either: it truncates at exactly
100,015 characters.

The scratch module is studio-only, so a capture never reaches the place file, a
published place, or a team create, and it is destroyed on every exit path.

### Stable identity across restarts

`studio_id` is minted by the proxy process and changes on every Studio restart,
so it is a transport token, not an identity. There is no in-band path to it:
`game.UniqueId` is unreadable from this context (`lacking capability
RobloxScript`) and `ReflectionService` does not list it either.

`game:GetDebugId()` *is* readable and is the substitute:

```python
from roblox_studio_mcp.extended import list_studio_instances, read_in_band_identity

result = await list_studio_instances()
# {'instances': [{'reported_name': 'Place1', 'studio_id': '4382339c-…',
#                 'debug_id': '0_185967', 'place_id': 0, …}, …]}
```

The two are paired in a registry under this machine's state directory
(`%LOCALAPPDATA%\roblox-studio-mcp\studios.json` on Windows). It is **host-side
only** — nothing is written into the DataModel, so an entry can never reach the
place file, a published place, or a team create. Override with
`ROBLOX_STUDIO_MCP_REGISTRY`.

```python
from roblox_studio_mcp.extended import resolve_instance

resolve_instance(debug_id="0_185967")
# {'status': 'ok', 'match': {'last_studio_id': '…', 'id_changed': True, …}}

resolve_instance()          # ambiguous -> every candidate, no guess
# {'status': 'ambiguous', 'candidates': [...]}
```

`id_changed` is `True` once an instance has been seen under more than one
`studio_id`, which is how a restart shows up under a stable key.

**Read it in Edit mode.** `GetDebugId` identifies the DataModel root, not the
process, and a play session reports a different value for the same Studio
(`0_185967` in Edit vs `0_1623123` in Server), so a Server-side read is not
comparable. Whether the value survives a Studio restart is **unverified** — it
is derived from the place instance, so it is expected to be stable, but that has
not been measured.

### Unrecognised `list_roblox_studios` payloads

`list_studios()` returns an empty list only when the proxy genuinely reports no
instances. A payload shape this client does not recognise raises instead, and
shows you the shape it got. Collapsing the two cases would report schema drift
as "no Studio is connected" and send you to check the MCP toggle when the fault
is on this side of the wire.

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
from roblox_studio_mcp.extended import RobloxStudio, write_script

async def main():
    async with await RobloxStudio.connect() as studio:
        status = await write_script(
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
python -m examples.write_script game.ServerScriptService.MyScript --create
python -m examples.wait_for_studio 600
```

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
