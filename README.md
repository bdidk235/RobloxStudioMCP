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
your tooling.

Working *on* this rather than using it? Start at
[CONTRIBUTING.md](CONTRIBUTING.md) — setup, the gates, and the conventions
worth knowing before you touch either tree.

Parity is enforced for the **tool surface** and is best-effort underneath. A
generated contract (`parity/tools.json`, asserted by both test suites) fails
either side if a tool, parameter, or required argument drifts, so neither can
change its surface unnoticed. Behaviour is not provably equal: Python resolves
`studio_id → PID` by parsing the Studio's own log and Node does not, so Node's
instance-stop **refuses** rather than guessing — see
`node/src/extended/IDENTITY.md`. Both suites cover the same ground without
needing Studio.

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
- `RobloxStudio` — a convenience wrapper that resolves `studio_id` via
  `list_roblox_studios` and injects it into every tool call whose schema
  declares it. An implicit id is accepted **only when exactly one Studio is
  connected**; with more than one, the call raises and names the candidates
  rather than guessing, because list order is the proxy mesh's and carries no
  intent. Pass `studio_id=` to pin one.
- Singleton connection — `connect()` returns a process-wide shared
  connection by default; close it explicitly (`close_singleton()` /
  `closeSingleton()`).
- Readiness retry — a fresh proxy answers `list_roblox_studios` with
  "Unable to reach Roblox Studio" for a beat after its handshake; both
  clients ride through exactly that transient symptom (up to ~10 s) and let
  every other error throw immediately.
- Extended helpers — full-file writes (`write_script` / `writeScript`), batch
  edits with graceful skipping (`update_script` / `updateScript`), script
  search, search-and-read, insert-from-file, lossless viewport capture,
  non-halting breakpoints, console watch, host-side waiting, play-test
  summaries, execute-from-file, Studio identity, and instance control. Also
  served as `extended_*` tools by the extended stdio proxy. See
  [The extended tools](#the-extended-tools) below.
- Stable Studio identity — `studio_id` is minted by the proxy and changes on
  every Studio restart, so it is a transport token rather than an identity.
  `game.UniqueId` is unreadable from this context (it needs the `RobloxScript`
  capability), but `game:GetDebugId()` is not, so instances are paired with
  that and kept in a registry in this machine's state directory — host-side
  only, never written into the DataModel, so it cannot reach the place file, a
  published place, or a team create. Read in Edit mode: a play session reports a
  different value for the same Studio.
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
  EVIDENCE.md             closed research, moved out of TODO.md — look a "why" up here
parity/                 generated tool contract (tools.json) both suites assert
skills/                 rsx-* transport skills, served by extended_skill
CONTRIBUTING.md         how to work on this: setup, gates, conventions, evidence rules
AGENTS.md               standing rules, gates, and where the two sides differ
TODO.md                 open work and withdrawn claims — and nothing else
README.md               this overview
LICENSE                 MIT
```

### Inside `python/src/roblox_studio_mcp/`

```text
client.py           generic MCP JSON-RPC client
roblox.py           RobloxStudio convenience wrapper
server.py           stdio MCP server (base, passes through to Studio MCP)
extended.py         RobloxStudio re-export for subpackage imports
extended_server.py  stdio MCP server with the 16 extended tools
types.py
errors.py           error codes
extended/
  writer.py         write_script + chunked _chunked_write
  updater.py        update_script + UpdateResult
  extensions.py     search_and_read, insert_asset, watch_output,
                    run_tests, execute_luau_from_file, WatchResult
  errors.py         ToolError, classify, the 15 codes
  waiting.py        extended_wait_for: host-side polling + probe wrapper
  platform.py       every platform difference (PowerShell, paths, ps)
  instance.py       launch / list / stop Studio
  logid.py          studio_id -> PID, from the Studio's own log
  locks.py          .lock file PID join
  capture.py        lossless RGBA -> PNG
  breakpoints.py    non-halting logpoints
  grep.py
  registry.py       host-side identity state (never in the place)
  skills.py         the rsx-* transport skills
```

`node/src/extended/` mirrors that surface in TypeScript.

### Using it from Python

Every extended tool is also callable directly:

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

## Prerequisites

Roblox Studio open with a place loaded, and its MCP server enabled
(Assistant → Manage MCP Servers → *Enable Studio as MCP server*).

**Windows is the tested platform; macOS is supported but unproven; Linux is not
supported.** This matters more than a version range, so it is worth being
specific about why.

- **Windows** — everything here was measured against it.
- **macOS** — the paths, process enumeration and log discovery are written from
  Roblox's own documentation and exercised against macOS-shaped fixtures, but
  **the macOS branch has never been run against a real macOS Studio**. See
  `TODO.md` for which parts are measured and which are inferred, and treat a
  macOS bug report as a genuine unknown rather than a regression.
- **Linux** — **not supported, and not a missing feature.** There is no POSIX
  branch: the transport launches the proxy as
  `cmd.exe /c … %LOCALAPPDATA%\Roblox\mcp.bat` on Windows and as the
  `StudioMCP` binary inside the app bundle on macOS, and process enumeration
  and log discovery are built on `os.startfile`, `EnumWindows` and
  `%LOCALAPPDATA%`. A Linux port means writing that layer, not fixing a bug.

Every platform difference is confined to one file per implementation —
`python/src/roblox_studio_mcp/extended/platform.py` and
`node/src/extended/platform.ts` — so a port has a known shape and a known size.

## The extended tools (for agents)

This section and the traps below are for driving the tools; everything above is
for deciding which client to install. If you are an agent, start at
`extended_skill` rather than here.

There is no tool count quoted in this file — it is a generated contract, and a
number in prose drifts. The live list is whatever `tools/list` returns; the
enforced copy is `parity/tools.json`. Grouped by what you are trying to do,
with the one to reach for first.

### Find things

| Tool | Use it for |
| --- | --- |
| `extended_skill` | **Start here.** Fetches a transport skill (`rsx-*`), or lists them. |
| `extended_script_grep` | Search script *contents* with surrounding context, in file order. Substring by default, regex with `regex:true`. |
| `extended_script_search_and_read` | Find scripts by name under a path and batch-read their sources. |

### Change things

| Tool | Use it for |
| --- | --- |
| `extended_write_script` | Replace a whole script body. Atomic; reports `wrote` / `unchanged` / `created`. |
| `extended_update_script` | Batch exact replacements. One bad edit skips with a warning instead of sinking the rest. |
| `extended_insert_asset_from_file` | Put a local script, model, or image into the game tree. |

### Look at it

| Tool | Use it for |
| --- | --- |
| `extended_capture` | Lossless PNG of the viewport. Use when the pixels *are* the measurement. |
| `extended_watch_output` | Console lines since your last poll, filtered and capped. Use instead of `get_console_output` in a loop. |
| `extended_studio_identity` | This Studio's `game:GetDebugId()`. Session-scoped — see the traps below. |
| `extended_list_studios` | Every attached Studio with its id and debug id paired. |

### Run and wait

| Tool | Use it for |
| --- | --- |
| `extended_execute_luau_from_file` | Run Luau read from a `.luau` file on disk, for anything too long to inline. |
| `extended_wait_for` | Poll a condition host-side until true. The only way to wait — you cannot sleep between calls. |
| `extended_run_tests` | Play test, then a `{passed, console_lines, errors}` summary. Green-check only. |
| `extended_breakpoints` | Non-halting logpoint on a running server script; each hit prints one console line. |
| `extended_clear_breakpoints` | Remove every breakpoint. Needs a play session. |

### Control Studios

| Tool | Use it for |
| --- | --- |
| `extended_manage_instance` | `list`, `places`, `make_place`, `launch`, `stop`. `stop` terminates a process and is irreversible. |

**Full detail for every tool** — inputs, outputs, and exact failure modes:
`docs/index.html` has a catalog, `parity/tools.json` is the generated contract,
and `extended_skill` serves the seven `rsx-*` skills covering this transport's
traps. Roblox ships its own `rbx-*` skills through the relayed `skill` tool for
**engine** questions; `skills/README.md` has the routing table for which wire a
question is actually on.

## Traps

These produce plausible wrong answers rather than errors, which is the failure
mode this project exists to prevent. The rest live in the `rsx-*` skills.

- **Never print bulk data.** A multi-megabyte `print` permanently wedges that
  Studio's console output until it is restarted. Report measurements through
  instance attributes instead.
- **`screen_capture` requires a `studio_id`** — the schema refuses the call
  without one. It is JPEG, so it smears 1px edges; `extended_capture` is
  lossless PNG, names the `studio_id` it captured, and can save to a file
  instead of returning megabytes.
- **`return` loses array-ness.** A Luau array comes back as an object with
  `"1"`, `"2"` keys, and `Vector2` as the single string `"3, 4"`. To return
  data, serialise at the source (`HttpService:JSONEncode`) — that survives
  intact. See `rsx-transport`.
- **`studio_id` is not an identity.** The proxy mints it and it changes on every
  restart; `GetDebugId` also changes on restart. Re-resolve each session, never
  write one into a file, and pass it explicitly whenever more than one Studio is
  attached.
- **Editing a `.py` file needs an MCP server restart.** A config change
  restarts the process; a code fix does not, so without a restart you are
  measuring the old code.

## Examples

Each example exists on both sides with the same behavior:

| What it shows | Python | TypeScript |
| --- | --- | --- |
| List every tool | `python -m examples.list_tools` | `npx tsx examples/list_tools.ts` |
| Run Luau, print result | `python -m examples.run_luau` | `npx tsx examples/run_luau.ts` |
| Shared connection + disabled tools | `python -m examples.singleton_usage` | `npx tsx examples/singleton_usage.ts` |
| Play, walk, jump, leave | `python -m examples.walk_jump` | `npx tsx examples/walk_jump.ts` |
| Full-file write helper | `python -m examples.write_script <target> [--create]` | `npx tsx examples/write_script.ts <target> [--create]` |
| Wait for Studio over MCP | `python -m examples.wait_for_studio [timeout_seconds]` | `npx tsx examples/wait_for_studio.ts [timeoutSeconds]` |

(Python commands run from `python/`; TypeScript commands run from `node/`.)

## Place ids and universe ids

A **place** is one file. A **universe** is the published game that owns a set of
places. Almost every Roblox web API is keyed on the **universe**, not the place —
badges, game passes, Marketplace, `/v1/games` — so if you are doing anything with
a published game's services, the universe id is the id you need, and it is not
something you can read off the `.rbxl` file.

The useful property is the direction of the mapping:

```
place id  ──►  universe id      one place belongs to exactly one universe
universe id ──►  place id      one universe owns MANY places - not invertible
```

So **derive the universe from the place, never the reverse.** Both clients do
this, and neither asks you for a universe id you would have to look up anyway:

```python
from roblox_studio_mcp.extended.instance import resolve_universe_id

universe = await resolve_universe_id(95206881)   # -> 28220420
```

```ts
import { resolveUniverseId } from "roblox-studio-mcp/extended";

const universe = await resolveUniverseId(95206881);   // -> 28220420
```

The endpoint is `GET https://apis.roblox.com/universes/v1/places/{place_id}/universe`
and needs no authentication. It retries **3 times** by default, with a doubling
delay: it sits on a launch path, where one fast call turns a network blip into
"could not open the place" and sends you looking at the URI instead of at the
network.

`build_launch_uri(place_id)` uses the same lookup, so a URI launch carries the
right universe without you passing one. Pass `universe_id` explicitly only when
you want a *different* one — and **`0` is a real value, not a stand-in**: it
means "this place, no universe context", which is what a template is and what
Studio's own *File > New* emits.

Both values are measured to open the place (8 of 8 launches on the real id, 6 of
6 on `0`; see `TODO.md` for a claim about this that was withdrawn after being
re-tested).

## Naming map

| Concept | Python | TypeScript |
| --- | --- | --- |
| Connect | `RobloxStudio.connect(studio_id=…)` | `RobloxStudio.connect({ studioId: … })` |
| List tools | `studio.list_tools()` | `studio.listTools()` |
| Call a tool | `studio.call(name, args)` | `studio.call(name, args)` |
| Run Luau | `studio.execute_luau(code)` | `studio.executeLuau(code)` |
| Shared connection | `get_singleton()` / `close_singleton()` | `getSingleton()` / `closeSingleton()` |
| Full-file write | `write_script(…, create_if_missing=…)` | `writeScript(…, { createIfMissing: … })` |
| Batch edits | `update_script(…, skip_missing=…)` | `updateScript(…, { skipMissing: … })` |

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

Two independent toolchains, each with its own working directory and its own
gate. Run Python commands from `python/`, Node commands from `node/`.

```powershell
# Node (run from node/)
pnpm install
pnpm typecheck      # NOT `tsc --noEmit` — see below
pnpm test

# Python (run from python/)
pip install -e .[dev]
python -m pytest tests
```

`python -m unittest discover -s tests` also runs, but `pytest` is the gate:
several tests are `pytest-asyncio` and `unittest discover` does not drive them,
so it can report green while skipping the async coverage.

### Use `pnpm typecheck`, never bare `tsc --noEmit`

The `typecheck` script is `tsc --noEmit -p tsconfig.check.json` — a stricter
project that **includes the tests**. Bare `tsc --noEmit` checks less and passes
files the real gate rejects. That is not theoretical: measured 2026-10-01, the
difference shipped to `main` and turned CI red.

### The gates, and what each one catches

| Gate | Command | Catches |
| --- | --- | --- |
| Python behaviour | `python -m pytest tests -q` | behaviour |
| Python types | `pytest tests/test_typecheck.py` (runs pyright) | wrong key, `None` deref, wrong argument type |
| Node types | `pnpm typecheck` | the same, at compile time |
| Node behaviour | `npx vitest run` | behaviour |
| **Parity** | `pytest tests/test_parity.py` + `npx vitest run tests/parity.test.ts` | either side's tool surface drifting |
| Build freshness | included in vitest | `dist/` older than `src/` |

**Parity is the one that is easy to miss**, because nothing fails until a tool
is added or renamed. `parity/tools.json` is generated from the Python server by
`parity/build_contract.py` and asserted by *both* suites, so a tool, a parameter
or a required argument cannot change on one side unnoticed. Regenerate it
after a deliberate surface change — `python parity/build_contract.py` — and the
diff *is* the parity report.

Two of these gates catch **silent** wrong answers, which is the failure class
this project keeps paying for. Unknown parameters are refused before dispatch
on both sides, because a silently-ignored argument produces a plausible wrong
answer *and reports success* — three separate incidents here did exactly that.
And `tests/test_closed_sets.py` checks exhaustiveness: every error code is
producible and every advertised `action` is handled rather than merely accepted.

## Docs site

`docs/index.html` opens straight from disk (no server or build step) and
explains the architecture, the full tool catalog with inputs and outputs,
and measured performance numbers.

## Continuous integration

`.github/workflows/ci.yml` runs unit tests per language on Windows and
macOS — no Studio needed anywhere (fakes throughout):

| Job | What it runs |
| --- | --- |
| `python-test` | `pytest` in `python/` |
| `node-test` | typecheck + vitest + build in `node/` |

`.github/workflows/studio-bundle-probe.yml` is separate and manual
(`workflow_dispatch` only). It is read-only, takes no secret, and finishes in
about twenty seconds: it checks the macOS Studio bundle for the properties that
would decide whether macOS CI is even possible. It is retained because the
conclusions it supports are recorded as *inferred*, and this keeps them
reproducible rather than remembered.

The live-Studio integration suites (`test_integration_studio`,
`integration.test.ts`) run locally with `ROBLOX_STUDIO_MCP_INTEGRATION=1`
once Studio is open with a place loaded and the MCP server enabled
(Assistant → Manage MCP Servers):

```powershell
# Python (run from python/)
python -m examples.wait_for_studio 600
$env:ROBLOX_STUDIO_MCP_INTEGRATION = "1"
python -m pytest tests/test_integration_studio.py
```

## License

MIT — see [LICENSE](LICENSE).
