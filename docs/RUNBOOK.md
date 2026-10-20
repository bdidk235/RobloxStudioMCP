# Operator runbook

For the human driving this MCP by hand, and for an agent that needs the whole
chain in one place. **Read the relevant `rsx-*` skill before driving the
transport** (`extended_skill` serves them; an `rbx-*` name is relayed to
Studio's built-in skill tool and labelled as such). This page is the map; the
skills carry the traps, and the code carries the truth.

Nothing here needs a Studio to read. Steps 1 and 6 touch the machine, not the
place: a launch you do not own is a process nobody will stop for you.

## The chain

```
1 launch -> 2 attach -> 3 resolve -> 4 capture
                                   -> 5 play  -> console/tests -> 6 stop
```

### 1. Launch a place

`extended_manage_instance action=launch place_path=<path>` — the only tool that
starts a Studio. It returns the `studio_id` it opened; pass it on.

- A place is required. A Studio joins the mesh only with one open.
- Three source kinds are auto-detected: a local `.rbxl`/`.rbxlx`, a template
  recovery copy (`Template_<id>_AutoRecovery_<n>`), and a real published place.
- **Ask before saving a place.** Local (`.rbxl`) has **no API** — there is no
  plugin-side save either. Cloud (`AssetService:SavePlaceAsync`) writes to
  Roblox, is rate-limited, and needs `AssetCreateUpdate`. Omitting the id saves
  whatever place is open, which may be published.

### 2. Attach

`extended_list_studios`. Returns `mesh[]`, one row per attached Studio, plus
`processes` as a separate deliberately-unjoined list — pair them yourself, and
expect the same mesh name (`Place1`) from two Studios holding one place.

`extended_studio_identity` reads `GetDebugId` from the running DataModel. It
identifies the **DataModel root, not the process**, and it changes between Edit
and play, so it is a per-mode label, not an identity.

### 3. Resolve a target

Every Studio-touching extended tool takes an optional `studio_id`.

- **Exactly one attached Studio** — omit it; it is resolved each call.
- **Several** — the call is **refused** and the candidate ids come back in the
  error. Order carries no intent, so nothing is guessed. Pin one.
- A pin does not survive a Studio restart: ids are re-minted, and a stale pin
  fails loudly with the current mesh reported, never silently redirected.

### 4. Look at the screen

| tool | when |
|---|---|
| `extended_capture` | the pixels are the measurement. Lossless PNG, host-side encoded, byte-exact. A caller-supplied `save_path` is confined to the working directory and written through a temp file and one rename, so a failed write cannot truncate a good capture. |
| relayed `screen_capture` | a quick look. Small inline JPEG; ignores any format argument; returns **null** when Studio is not visible on a monitor. |

### 5. Play, then read the console

`start_stop_play` (relayed) enters play; `get_studio_state` reports which
datamodels exist. A `get_studio_state` answer of only `Edit` means no play
session — a Server/Client-targeted call will refuse.

`extended_run_tests` runs the engine's test runner and reports a summary;
it cannot tell you *why* a test failed (`rbx-unit-test` can). `extended_watch_output`
returns console lines **since your last call**, filtered — the only sane way to
poll. The console arrives as **one line** with literal `\n` escapes, so match
patterns, never parse lines, and never print bulk.

For **pausing**, use `rbx-debug`. `extended_breakpoints` is non-halting
logpoints only: a halting breakpoint blocks the very call that sets it.

### 6. Stop

`extended_manage_instance action=stop studio_id=<id> dry_run=true` first. It
**terminates the process**, irreversibly, and it does not end a play session.
Non-dry-run requires `confirm=true`. Termination tries a graceful exit first and
escalates only if the process is still alive.

**It does not end a play session** — that is `start_stop_play is_start=false`.

## Codes, and what to do about each

Sixteen codes, all reachable by construction (`tests/test_closed_sets.py`
asserts every one has a producer). They arrive three ways, and the third is why
an unrecognised failure is still legible:

| code | what it means | recovery |
|---|---|---|
| `NO_STUDIO` | no Studio instance is connected | open Studio and enable the MCP plugin; on Linux/macOS confirm the proxy can launch at all (`python -m roblox_studio_mcp.doctor`) |
| `AMBIGUOUS_STUDIO` | several attached, no `studio_id` | re-call with a pin taken from `extended_list_studios` |
| `STALE_STUDIO_ID` | the pinned id is no longer connected | Studio ids are re-minted per restart; re-pin from `extended_list_studios` |
| `PLACE_NOT_OPEN` | the launch never got a place open | re-launch with a place; check the Studio log for `OpenPlaceFailure` |
| `LAUNCH_FAILED` | the proxy or Studio could not start | it is reachable **because** an exhaustiveness check found it unreachable before; now: repair or reinstall Studio, or set `ROBLOX_STUDIO_MCP_PROXY_COMMAND` |
| `WITNESS_MISMATCH` | a kill target disagreed with an independent witness | **do not retry the kill.** Re-read `extended_list_studios`; a contradiction between the registry and the process table is the thing this refuses |
| `DATAMODAL_UNAVAILABLE` | the operation ran where that datamodel does not exist | `Edit` needs Edit mode with no play session; `Server`/`Client` need one. Check `get_studio_state` rather than assuming |
| `TEST_BUSY` | a previous test run is still in progress | wait for it (`extended_wait_for`) rather than starting another |
| `TEST_REFUSED` | the runner rejected the call (`:AddPlayers()`, `:EndTest()`, `:LeaveTest()`) | run from a `rbx-unit-test`-shaped scaffold, not an arbitrary script |
| `NOT_FOUND` | the script, instance or tool could not be found | confirm the path exists in the DataModel, not on disk |
| `SIZE_LIMIT` | a payload exceeded the scratch ceiling | split it; a scratch ModuleScript has a measured ceiling, and the tools refuse rather than truncate |
| `CAPABILITY_DENIED` | plugin security or an elevated capability blocked it | this is the place-file confinement and the write-path checks: pass a `save_path` inside the working directory, or use a tool rated for the operation |
| `TIMEOUT` | the request outlived its deadline | there is a movable deadline extended by progress notifications and a hard ceiling past it; re-run, then look for a wedged proxy |
| `LUA_ERROR` | a Luau fault in caller-supplied code | read the engine's message verbatim, it is preserved unmodified |
| `INVALID_ARGUMENT` | the arguments were rejected | the message names the argument and the problem; unknown arguments are refused before dispatch, never ignored |
| `UNKNOWN` | nothing above matched | the message text is preserved — classify it above by its wording and file the gap |

Codes are classified from the engine's own words (`extended/errors.py`,
`_PATTERNS`), and a caller's own reading of the text is never second-guessed.
`PLACE_NOT_OPEN` is accepted as an alias of `STALE_STUDIO_ID`, because a stale
id genuinely presents as "place is not open" and both want the same recovery.

## When nothing works

```
python -m roblox_studio_mcp.doctor            # proxy, handshake, Studio, snapshot drift
python -m roblox_studio_mcp.doctor --upstream # grade the live tool list vs contract/upstream_tools.json
```

Then the gates, from the repo root:

```
PYTHONPATH=python/src python -m pytest python/tests -q   # behaviour + type gate
PYTHONPATH=python/src python -m pytest python/tests/test_contract.py -q
```

## Windows and macOS, in one line each

| | proxy | notes |
|---|---|---|
| Windows | `%LOCALAPPDATA%\Roblox\mcp.bat` via `cmd.exe` | the default path |
| macOS | `StudioMCP` inside `Studio.app` | launch it directly, no shell |

The log-marker route (a `UIThreadNotifier ... for process 'N'` line) and
`lsof`-based PID attribution are **inferred on macOS, not observed** —
`TODO.md` carries both as open. Do not claim macOS verification from this
repository's record.
