# roblox-studio-mcp-node

A lightweight, **dependency-free** Node.js/TypeScript client for
[MCP (Model Context Protocol)](https://modelcontextprotocol.io) servers —
with built-in convenience for the **Roblox Studio MCP**.

Port of the Python [`roblox-studio-mcp`](../RobloxMCP) package. Zero runtime
dependencies: only Node.js built-ins (`node:child_process`, `node:readline`).
Requires Node 18+.

## What it does

- Launches any MCP server over the **stdio** transport and speaks JSON-RPC 2.0.
- Performs the `initialize` handshake, lists tools, and calls tools.
- Ships a `RobloxStudio` convenience client that resolves `studio_id` once and
  injects it into every tool call that needs it.
- Ships transparent (`server`) and extended (`extendedServer`) stdio proxies,
  plus `extended/` helpers (`writeLikeMultiEdit`, `updateLikeMultiEdit`, …).

## Install

```powershell
pnpm install
pnpm build
```

## Quick start (Roblox Studio)

Make sure Roblox Studio is open (and its MCP plugin enabled), then:

```ts
import { RobloxStudio } from "roblox-studio-mcp-node";

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
import { MCPClient } from "roblox-studio-mcp-node";

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
import { RobloxStudio, closeSingleton } from "roblox-studio-mcp-node";

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

## Stdio servers

```powershell
# Transparent proxy (same tools as StudioMCP)
node ./dist/server.js

# Extended proxy (adds extended_* tools)
node ./dist/extendedServer.js
```

## Extended helpers

```ts
import { RobloxStudio, writeLikeMultiEdit } from "roblox-studio-mcp-node/extended";

const studio = await RobloxStudio.connect();
const status = await writeLikeMultiEdit(
  studio,
  "game.ServerScriptService.MyScript",
  "print('hello')",
  { createIfMissing: true },
);
// status is "created" | "wrote" | "unchanged"
await studio.close();
```

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
