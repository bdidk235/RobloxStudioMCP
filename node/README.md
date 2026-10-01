# roblox-studio-mcp (Node.js)

A lightweight, **dependency-free** Node.js/TypeScript client for
[MCP (Model Context Protocol)](https://modelcontextprotocol.io) servers —
with built-in convenience for the **Roblox Studio MCP**.

Port of the original Python client in `../python`. Zero runtime dependencies:
only Node.js built-ins (`node:child_process`, `node:readline`). Requires Node 18+.

## What it does

- Launches any MCP server over the **stdio** transport and speaks JSON-RPC 2.0.
- Performs the `initialize` handshake, lists tools, and calls tools.
- Ships a `RobloxStudio` convenience client that resolves `studio_id` once and
  injects it into every tool call that needs it.
- Ships transparent (`server`) and extended (`extendedServer`) stdio proxies,
  plus `extended/` helpers (`writeScript`, `updateScript`, …).

## Install

From this folder:

```powershell
pnpm install
pnpm build
```

(From the repo root: `pnpm --dir node install` and `pnpm --dir node build`.)

## Quick start (Roblox Studio)

Make sure Roblox Studio is open with a place loaded (and *Enable Studio as MCP server* turned on under Assistant → Manage MCP Servers), then:

```ts
import { RobloxStudio } from "roblox-studio-mcp";

const studio = await RobloxStudio.connect();
try {
  for (const tool of await studio.listTools()) {
    console.log(tool.name);
  }

  // Run Luau in Studio (datamodelType: "Edit", "Client", or "Server")
  const result = await studio.executeLuau("return 1 + 1");
  console.log(result.text()); // -> 2

  // Or call any tool by name; studio_id is injected automatically
  const inspected = await studio.call("inspect_instance", { path: "Workspace" });
  console.log(inspected.text());
} finally {
  await studio.close();
}
```

> `RobloxStudio.connect()` returns the process-wide shared connection by
> default. `asyncDisposable`/`await using` is **not** used because the
> singleton must stay alive across calls — close it explicitly with
> `await studio.close()` or `await closeSingleton()`.

## Using the generic client

The underlying `MCPClient` works with **any** stdio MCP server:

```ts
import { MCPClient } from "roblox-studio-mcp";

const client = new MCPClient("npx", ["-y", "@modelcontextprotocol/server-filesystem", "."]);
await client.connect();
try {
  for (const tool of await client.listTools()) console.log(tool.name);
} finally {
  await client.close();
}
```

The Roblox Studio server itself is launched as
`cmd.exe /c "cd /d %LOCALAPPDATA%\Roblox && .\mcp.bat"` — the default
`command`/`args` used by `RobloxStudio.connect()` on Windows. On macOS it
instead runs `/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP`
directly (no shell); `defaultCommand()` / `defaultArgs()` / `defaultShell()`
pick per platform, and explicit `command`/`args`/`shell` options always win.

## Handling multiple Studio instances

```ts
const studio = await RobloxStudio.connect();
try {
  for (const s of await studio.listStudios()) console.log(s["id"], s["name"]);
  studio.setStudioId("the-id-you-want"); // or pass studioId to connect()
} finally {
  await studio.close();
}
```

## Disabling tools

```ts
const studio = await RobloxStudio.connect({
  disabledTools: new Set(["generate_mesh", "segment_mesh", "generate_material"]),
});
```

`MCPClient` accepts the same `disabledTools` option.

## Singleton connection

```ts
import { RobloxStudio, closeSingleton } from "roblox-studio-mcp";

const a = await RobloxStudio.connect(); // launches the shared process (once)
const b = await RobloxStudio.connect(); // same connection, same process
await closeSingleton();
```

`studioId` is optional and auto-resolves to the first open Studio.
Pass `singleton: false` to `connect()` for an isolated connection, or use
`getSingleton()` / `closeSingleton()` directly.

A fresh proxy needs a moment after its handshake before its Studio uplink is
usable. `resolveStudioId()` rides through that transient "Unable to reach
Roblox Studio" symptom (retrying up to `timeoutMs: 10_000`) so the first tool
call through a new connection just works; tune with
`resolveStudioId({ timeoutMs, intervalMs })`. Any other error — including a
genuinely empty Studio list — still throws immediately.

### Choosing a Studio

`resolveStudioId()` returns an id configured by the caller unchanged. When you
have not pinned one, it re-lists on every call and accepts the result **only if
exactly one Studio is connected**. Two or more throws, listing each candidate's
name and id:

```ts
await studio.resolveStudioId();
// MCPToolError: 2 Roblox Studio instances are connected, so no studio_id can be
// inferred: [["Place1","sid-a"],["rbx-re","sid-b"]]. Pass studioId to connect()
// (or per call) to pick one.
```

This is deliberate. List order comes from the proxy mesh and means nothing, so
taking the first entry silently routes work to the wrong Studio — the failure
is invisible in the response, since the reply is well-formed and belongs to
some other place. Resolving per call also means a second Studio opening, or the
first one restarting, is noticed on the next call instead of being masked by a
cached id. `studio.studioId` stays `null` unless you pin one, so an implicit
resolution never becomes sticky.

### When a pin goes stale

Studio instance ids change on every Studio restart, so a pinned id does not
survive one. When a pinned id is no longer reachable, the error says so plainly
and names the pin, rather than passing the proxy's raw message through:

```ts
studio.setStudioId("some-old-id");
await studio.getStudioState();
// MCPToolError: The pinned studio_id "some-old-id" is no longer connected: ...
// Studio instance ids change every time Studio restarts, so a pin does not
// survive one. Re-pin with setStudioId() using a current id from
// listStudios(), or setStudioId(null) to fall back to inferring.
```

The pin is deliberately not swapped for whatever Studio happens to be open. A
pin is your explicit choice, and quietly retargeting it is the same failure as
first-wins, so recovery is yours to make: re-pin, or unpin and let inference
apply (which only works while a single Studio is open).

### Stable identity across restarts

`studioId` is minted by the proxy process and changes on every Studio restart, so
it is a transport token, not an identity. There is no in-band path to it:
`game.UniqueId` is unreadable from this context (`lacking capability
RobloxScript`) and `ReflectionService` does not list it either.

`game:GetDebugId()` *is* readable and is the substitute:

```ts
import { listStudioInstances, readInBandIdentity } from "roblox-studio-mcp/extended";

const result = await listStudioInstances();
// { instances: [ { reported_name: "Place1", studio_id: "4382339c-…",
//                  debug_id: "0_185967", place_id: 0, … }, … ] }
```

The two are paired in a registry under this machine's state directory
(`%LOCALAPPDATA%\roblox-studio-mcp\studios.json` on Windows). It is **host-side
only** — nothing is written into the DataModel, so an entry can never reach the
place file, a published place, or a team create. Override with
`ROBLOX_STUDIO_MCP_REGISTRY`.

```ts
resolveInstance({ debugId: "0_185967" });
// { status: "ok", match: { last_studio_id: "…", id_changed: true, … } }

resolveInstance();          // ambiguous -> every candidate, no guess
// { status: "ambiguous", candidates: [ … ] }
```

`id_changed` is `true` once an instance has been seen under more than one
`studioId`, which is how a restart shows up under a stable key.

**Read it in Edit mode.** `GetDebugId` identifies the DataModel root, not the
process, and a play session reports a different value for the same Studio
(`0_185967` in Edit vs `0_1623123` in Server), so a Server-side read is not
comparable. Whether the value survives a Studio restart is **unverified** — it
is derived from the place instance, so it is expected to be stable, but that has
not been measured.

### Unrecognised `list_roblox_studios` payloads

`listStudios()` returns an empty array only when the proxy genuinely reports no
instances. A payload shape this client does not recognise throws instead, and
shows you the shape it got. Collapsing the two cases would report schema drift
as "no Studio is connected" and send you to check the MCP toggle when the fault
is on this side of the wire.

## Stdio servers

```powershell
# Transparent proxy (same tools as StudioMCP)
node ./dist/server.js

# Extended proxy (adds extended_* tools)
node ./dist/extendedServer.js
```

## Extended helpers

```ts
import { RobloxStudio, writeScript } from "roblox-studio-mcp/extended";

const studio = await RobloxStudio.connect();
const status = await writeScript(
  studio,
  "game.ServerScriptService.MyScript",
  "print('hello')",
  { createIfMissing: true },
);
// status is "created" | "wrote" | "unchanged"
await studio.close();
```

## Examples

Runnable scripts live in `examples/` (run from this folder):

```powershell
npx tsx examples/list_tools.ts
npx tsx examples/run_luau.ts
npx tsx examples/singleton_usage.ts
npx tsx examples/walk_jump.ts
npx tsx examples/write_script.ts game.ServerScriptService.MyScript --create
npx tsx examples/wait_for_studio.ts 600
```

Each mirrors a script in `../python/examples/` — same behavior, same output
shape, only the language idioms differ (see the repo-root README for the
Python ↔ TypeScript naming map).

## API overview

| Symbol | Purpose |
| --- | --- |
| `MCPClient(command, args, …)` | Generic stdio MCP client |
| `RobloxStudio.connect(…)` | Studio-specific convenience client |
| `client.listTools()` | `Tool[]` |
| `client.callTool(name, args)` | `CallToolResult` |
| `studio.call(name, args)` | Like above, but auto-injects `studio_id` |
| `studio.executeLuau(code, datamodelType)` | Run Luau in Studio |
| `studio.getStudioState()` / `startPlay()` / `stopPlay()` | Play-mode helpers |
| `getSingleton()` / `closeSingleton()` | Process-wide shared connection |
| `scriptSearchAndRead(rootPath, { maxCharsPerSource })` | Search + batch-read scripts (sources truncated, with line counts) |
| `platformDefaults()` / `defaultCommand()` / `defaultShell()` | Per-platform proxy launch settings (Windows `mcp.bat` vs macOS binary) |
| `disabledTools` | Hide and refuse specific tools |

`CallToolResult` exposes `.text()` and `.json()`.

Naming follows TypeScript camelCase (`listTools`, `callTool`, `executeLuau`,
`studioId`, `datamodelType`, `targetPath`, `createIfMissing`, …). Wire-format
keys (`studio_id`, `datamodel_type`, `target_path`, …) are preserved when
talking to Studio.

## Development

```powershell
pnpm install
pnpm typecheck
pnpm test
```

## License

MIT
