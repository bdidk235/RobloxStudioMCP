---
name: rsx-targeting
description: studio_id vs datamodel_type, why GetDebugId is not durable, and getting a Studio PID from the mesh. Read when addressing one.
---

# Addressing a Studio

## Three separate axes, and conflating them is the usual bug

| axis | selects | example |
|---|---|---|
| `studio_id` | which Studio process | `324eb3b1-...` |
| `datamodel_type` | which DataModel inside it | `Edit`, `Server`, `Client` |
| place name or path | which place is open | `Baseplate.rbxl` |

`studio_id` addresses. It does not identify, because it is minted by the proxy
process and **changes on every Studio restart**. A cached id from a previous
session returns `Place is not open`.

## An implicit `studio_id` is refused when more than one Studio is connected

Taking `studios[0]` is not a safe default. The list order is the proxy mesh's
and carries no intent, so it misroutes silently: the reply is well-formed,
correctly shaped, and belongs to a different place.

So:

- with exactly one Studio connected, an omitted `studio_id` resolves to it.
- with more than one, the call **raises and names the candidates**. It does not
  pick one.
- a pinned `studio_id` is trusted and never silently re-swapped. If a pinned id
  is stale you get an error naming the pin, not a quiet redirect to another
  Studio.

Re-resolve the id each session. Never write one into a file.

## The process list is cached host-side

Enumerating processes costs a spawn (~2.5 s), so the process list is cached
briefly (2 s). A Studio launched in the last two seconds may not appear yet;
the launch loop bypasses the cache for exactly this reason. `refresh` on
`extended_list_studios` re-reads per-Studio identities, not the process list -
so a missing Studio is a cache question, and a missing identity is a Studio
question, and the two have different fixes.

## `datamodel_type` is a pass-through, not a routing layer

`Server` and `Client` only exist while a play session is running. In Edit mode
there is no Server DataModel, and asking for one fails with "Server datamodel is
not available in Edit mode".

Peer role in the other implementation is the same axis, not a superset. The
genuine gap is not per-client indexing, it is **identity**: the mesh row carries
only `id` and `name`, so the peer needs a second record to turn a `studio_id`
into a PID. Python reads that Studio's own log file; `logid.py` (33 KB) is
**not ported**, so the peer resolves role and place from the command line and
with two Studios on one place **cannot say which is which** - which is why its
`action=stop` refuses rather than terminating the wrong process. Read
`node/src/extended/IDENTITY.md` before relying on it for instance control.

## `GetDebugId` is session-scoped, not an identity

`game:GetDebugId()` is the only in-band identifier that is readable
(`game.UniqueId` needs a capability that `execute_luau` does not have, and
`game.JobId` is empty outside a session). Measured behaviour:

- stable across repeated calls within a session
- **differs between Edit and a play session** for the same Studio, so only read
  it in **Edit** and never compare a Server read against an Edit one
- **per instance, not per process** - a child `Folder` returns a different value
- **does not survive a restart.** Measured, same place file, same machine:
  `0_186696` before, `0_186502` after

That last point is the important one. **You cannot track a Studio instance
across a restart**, because a restart genuinely creates a new instance and no
durable in-band identifier exists. Also ruled out by probing: `game.PlaceId` and
`game.GameId` are both `0` for an unpublished place, and there is no process-id
API reachable from Luau at all.

What `GetDebugId` *is* good for: separating two Studios open on the same place,
which nothing else available does.

## Getting the PID, host-side

There is no in-band PID. But the MCP mesh is a WebSocket on
`127.0.0.1:13469`, so the attached Studios are visible as TCP connections:

```powershell
Get-NetTCPConnection -RemotePort 13469 | Where-Object State -eq 'Established'
```

Filter to `RobloxStudioBeta`, because `StudioMCP.exe` proxies also hold
connections and the listener is itself a connection holder.

This is strictly better than matching on window title, which collides: two
Studios on one place produce **identical** titles, identical `game.Name`, and
identical `PlaceId=0`.

To join a specific `studio_id` to a PID, read the per-process log under
`%LOCALAPPDATA%\Roblox\logs`. **A log states its own PID**, so no console write
is involved:

```
studio_id --> mesh name --> log command line --> that log's PID line --> PID
```

Three fields, all within the first 4 KB of the log:

| field | line |
|---|---|
| PID | `[FLog::UIThreadNotifier] Constructing UIThreadNotifier for process '16240'` |
| command line | an untimestamped header: `...RobloxStudioBeta.exe --task EditFile --localPlaceFile <path>` |
| role | the `-task` in that same command line |

Measured over 66 logs: **64 of 66** carry the PID line. Not 47 of 47 - that was a
partial count. The 2 without are a non-Studio installer log and a 1,335-byte
Studio log from a process that lived 0.36 s, which died before the notifier line
is written; `no_pid_reason` separates "no PID" from "a format I do not read", and
the right answer for the first is "this process is gone". Among the 64: all PIDs
distinct, none reused across two logs. Source: `docs/EVIDENCE.md` (identity
table) and `node/src/extended/IDENTITY.md:36`. The command line spells the place three different ways, and
missing any one of them loses a whole population:

- `--localPlaceFile <path>` - the Edit task
- `-localProjectFile <path>` - `StartServer` and `StartClient`, i.e. a play test
- `roblox-studio:1+task:EditPlace+placeId:N+universeId:M` - the URI route

`-parentPid` links a play test's client to the server that started it, so a whole
process tree is recoverable from the logs alone.

**The file route joins exactly**: the mesh name is the temp file's basename and
the command line contains that basename verbatim, so it is a string comparison
between two things that already exist.

**The URI route joins too**, in two stages, and not by giving up. Its mesh name
is `Template_<placeId>_AutoRecovery_<N>.rbxl`, and the place id *is* in the log,
so `match_mesh_name` falls back to narrowing the candidates by it
(`_mesh_rows_for_place_id`). When one candidate remains that is the answer. When
two or more URI launches of one place are open, `N` is separated by reading the
path-suffixed `PlaceSessionId` line on that handful of logs and comparing
basenames (`_refine_by_autorecovery_counter`) - a field the command line does
not carry, which is why the earlier claim that the log never records the counter
was wrong. **When that read cannot decide, the caller keeps the full candidate
list** rather than narrowing to a guess, and `ambiguous_reason` returns a sentence
saying which of the remaining situations it is: several logs reporting one place,
or logs that could not be read. Only those, and a server or client reporting
`name: null`, fall back to the old print-a-token join.

## `action=list` returns both sides, deliberately unjoined

`extended_manage_instance` with `action=list` (the default) returns **`processes`
and `mesh` as two separate lists** - not one joined table. Each mesh row carries
the `studio_id` every other tool takes, which is the point: `processes` are OS
processes and carry no `studio_id`, so before this both halves had to be fetched
separately and correlated by hand. The mesh is read unpinned, and a mesh read
failure degrades to `mesh_error` rather than losing the process rows.

They are **not** joined because the join is exactly what cannot be trusted here:
two Studios on one place both report the name `Place1`, so pairing by name is a
guess, and this tool has a documented history of reporting a confidently wrong
Studio. Pair a process's `place_file` with a mesh name yourself, and treat the
pair as unproven when two Studios share one place name.

**A Studio that never opened its place says why in its log.** `State:
OpenPlaceFailure` carries an `ErrorMessage`, and a URI launch that never gets a
name usually failed with "Error fetching latest place version" - a fetch of the
published place, not a mesh fault, so retrying the transport cannot fix it. The
state machine has two parsing traps; see `rsx-discovery`.

**Do not attribute that failure to the universe id.** It was once recorded as
caused by passing a place's real universe id rather than `0`, and that was
re-tested on 2026-10-02 and did not reproduce: 8 launches on the real id, 6 on
`0`, 14 successes, zero errors. `build_launch_uri` now derives the universe from
the place id by default. So treat the failure as **unexplained but rare** - the
advice that matters is unchanged, and it is the advice above: retry the launch,
not the transport.

The token join is now a last resort, and it is exact rather than approximate: it
used to match the log's filename stamp against each process's `CreationDate`
within 5s, which misfires precisely when two launches are seconds apart. It reads
the PID from the log instead. Log stamps are **UTC** and `CreationDate` is
**local**, so parse the `Z` as UTC or the comparison is hours out.

### Cost, and why many logs is not a problem

A long-lived machine accumulates logs without bound while 2-3 Studios stay open,
so the shape that matters is many logs and few Studios, not the reverse.

- Only a **64 KB prefix** is read; all three identity fields are inside 3,809 bytes
  in every log measured. Cost tracks file *count*, not total bytes.
- Candidate files are narrowed to those starting within 60s of a live process
  (measured worst gap between a filename stamp and a log's first line: 1.9s).
  This is a **filter, not a decision** - a narrowed pass finding nothing falls
  back to a full sweep, so a wrong window costs time, not a missing identity.
- It is not a clear win, and was nearly cut for that. At 3,000 logs with the live
  Studios newest, the filter is *slower* (0.098s vs 0.056s) because stamp ordering
  already finds them first. With an old Studio buried under 3,000 newer dead logs
  it is **13x faster** (0.097s vs 1.285s). Kept for the second case.
- Sort by the **filename stamp**, not mtime: the stamp needs no `stat` call, and
  it stays fixed while a log is still being written, so the order cannot shift
  underfoot.

The 100-Studio case is still unmeasured. This machine hit its 31.3 GB commit
ceiling at 5 concurrent Studios, so 100 was never reachable here. The join
extrapolates fine (cost is bounded by the sweep, not the Studio count); whether
100 Studios can attach to one mesh is unknown.

## Registry safety

`extended_list_studios` keeps `studio_id` to `GetDebugId` pairings in this
machine's state directory. It is **never written into the DataModel**, so it
cannot reach the place file, a published place, or a team create. It gives
history, not durability: a restart shows up as a new `studio_id` under a place
whose previous ids are still on record.
