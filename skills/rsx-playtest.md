---
name: rsx-playtest
description: Play and multiplayer sessions. Read before one: AddPlayers needs a session that can host players, and a long Studio call inside a tool call can wedge Studio.
---

# Playtests and multiplayer tests

## Evidence status, stated up front

The play-test flow is read from Roblox's `StudioTestService` docs and from a
working reference implementation. Some of it was reproduced directly on this
machine, marked *confirmed* below, and some of it is still inference from a
failure, marked as such. `ExecuteMultiplayerTestAsync` in particular is
**unverified**: the attempt to drive it blocked past the 120 s tool-call
timeout, so treat it as a documented contract rather than an observation.

Everything in the waiting section is read from `waiting.py`, so it is true by
construction rather than by measurement. Where this file says *unverified*, no
one has observed the thing at all.

## Two different kinds of session

| how it was started | can host extra players? |
|---|---|
| `-task StartServer` (a Studio **server**) | **yes**, *confirmed* |
| `-task StartClient` (a client Studio) | n/a, it is a client |
| `start_stop_play` / the Play button (local session) | **no**, *confirmed* |
| `StudioTestService:ExecuteMultiplayerTestAsync(n, args)` | documented yes, unverified here |
| `StudioTestService:ExecutePlayModeAsync(args)` | no, from the engine docs |

A `-task StartServer` Studio is the cheap way to get a multi-player-capable
session, and it needs no test-service machinery at all. That is what makes
`AddPlayers` reachable without fighting a blocking tool call.

## Identifying a server Studio

The command line tells you, host-side:

```powershell
Get-CimInstance Win32_Process -Filter "Name='RobloxStudioBeta.exe'" |
  Select-Object ProcessId, CreationDate, CommandLine
```

`-task StartServer` is the server; `-task StartClient` is a client; `-task
EditFile` is an edit-mode instance. One server plus N clients means N+1
`RobloxStudioBeta` processes for a single place, all attached to the mesh.

## `AddPlayers` needs a session that can host more than one player

*Measured, and the gate is not the one the error message implies.*

Confirmed working, on a Studio launched with `-task StartServer`:

```lua
-- Server DataModel of that session
local T = game:GetService("StudioTestService")
T:AddPlayers(1)          -- ok
-- #Players:GetPlayers() went 1 -> 2, giving Player1/u-1 and Player2/u-2
```

A Studio started that way is a **server**: it spawns a separate
`-task StartClient` Studio per player, which is how you end up with several
`RobloxStudioBeta` processes for one place.

Confirmed **refused**: the same call on a session opened by the relayed
`start_stop_play`, with a valid Server DataModel live, returns

```
AddPlayers: can only be called from the server DataModel of a running Studio test session
```

while the Server DataModel *is* available and a session *is* running. The
message is misleading. The real distinction is that `start_stop_play` opens a
**local single-player** session, which cannot host extra players, whereas
`-task StartServer` opens one that can.

**Correction worth keeping:** an earlier version of this skill claimed
`ExecuteMultiplayerTestAsync` was *required*. That was inferred from a failure,
not measured, and it is wrong - a plain Play-mode server session works fine.
`ExecuteMultiplayerTestAsync` is one way to get a multi-player-capable session,
not the only one. Its own behaviour is still unverified here, because the
attempt to drive it blocked past the tool-call timeout.

So the practical rule: **check whether your session can host players at all**
before concluding anything from the error string. `GetDebugId` will not tell
you; look at how the Studio was launched.

Other signature notes:

- `AddPlayers(numPlayers: number)` takes a plain **integer**. Passing a table
  fails with `Unable to cast Array to int`, *reproduced*. The documented 1 to 8
  range is unsourced in this repo, so treat the bound as the engine's, not as
  something measured here.
- it must be called where `RunService:IsRunning() and RunService:IsServer()`.
  That is the **documented** precondition and it is **not** what gates the call:
  both were true at the moment of the refusal above. The real gate is whether
  the session can host players at all.
- it does not pause. Poll host-side for `Players:GetPlayers()` reaching
  `before + numPlayers`:

  ```python
  extended_wait_for(
      condition='#game:GetService("Players"):GetPlayers() >= 2',
      datamodel_type="Server",
  )
  ```

  Note the comparison. Returning the bare count is a non-boolean and raises
  `INVALID_ARGUMENT`, for the reason in the waiting section.

## `EndTest` comes from the Server DataModel

*Observed, though `TODO.md` has no entry behind them. Treat as weaker than the
`AddPlayers` measurements above, which are recorded there.*

- `EndTest(value)` works from **Server**, and is what returns the session to the
  Edit DataModel.
- `EndTest` from **Client** fails: "can only be called from the server DataModel
  of a running Studio play session".
- `CanLeaveTest` / `LeaveTest` are the opposite: they need the **Client**
  DataModel.
- the Edit DataModel is **unavailable** during a test, so you cannot call
  anything there to fix a stuck session.

## Never `task.spawn` a long Studio call, and never `wait()` in your own runtime

*Reproduced, and this is the expensive one.*

Two ways to arrange a wait around a long test call. Both fail, and the first
fails permanently.

### `task.spawn` around the long call

It returns immediately, which is why it reads as the fix for a blocking call.
It is not. The spawned thread dies with the context of the tool call that
spawned it, so Studio never receives its completion callback and the test
service is left holding:

```
Failed to start the test because a previous one is still in progress.
```

That survives `start_stop_play(false)`, survives `IsRunning()` returning false
and `EditModeActive` returning true, and leaves no orphan client processes to
find. **Only a Studio restart clears it.**

### `wait()` / `task.wait()` / a sleep between calls, in your own runtime

The agent's code runtime has no timer, so the call hangs until its own timeout.
Not a Luau problem, and no amount of correct Luau fixes it. `AGENTS.md` carries
this as a standing rule.

### The shape that works

Make the call directly and let it block. Keep it well inside the 120 s
tool-call timeout, and put the **waiting** host-side between tool calls, with
`extended_wait_for`. If a call does hit the timeout, do not retry it. The
timeout is the exact condition that wedges the service, so the retry spends its
budget on a Studio that is already wedged.

## Waiting is host-side, and `extended_wait_for` is the tool

*Read from `python/src/roblox_studio_mcp/extended/waiting.py`. These are the
constants and branches the code has, not a fresh measurement.*

Each poll is one small `execute_luau` call, so no Luau thread has to survive
between tool calls. That is the whole reason this works where `task.spawn` and an
in-runtime sleep did not.

```python
extended_wait_for(
    condition='#game:GetService("Players"):GetPlayers() >= 2',
    timeout_seconds=30.0,     # default 30, hard cap 90
    datamodel_type="Server",  # Edit | Client | Server
    studio_id=...,
)
```

Four behaviours that will bite you if you do not know them:

- **The condition must evaluate to `true`, `false` or `nil`.** Anything else
  raises `INVALID_ARGUMENT`. Not pedantry: in Lua `0` is truthy, so
  `return #Players:GetPlayers()` comes back `satisfied: true` at one player.
  Compare instead. `nil` is falsy and keeps polling, so `return workspace.Foo`
  is a legal condition.
- **Three consecutive hung polls abort with `TIMEOUT` naming a Studio restart**
  (`POLL_TIMEOUT` 30 s per poll, `MAX_HUNG_POLLS` 3). Any reply at all resets
  the counter. Bounding the await frees this end only and does not cancel
  Studio-side execution, so three hangs in a row means the Studio side is
  wedged rather than slow, and retrying burns the budget on polls that cannot
  return.
- **A condition that never ran is never reported as a timeout.** One that does
  not compile raises `INVALID_ARGUMENT`; one that compiles and throws raises
  `LUA_ERROR`. A `DATAMODEL_UNAVAILABLE` poll raises at once instead of
  spending the budget on a DataModel that is not coming.
- **There is no `warning` key.** It was removed once the non-boolean case
  became an error, and a field that is always null invites a caller to read it,
  find nothing, and conclude all is well.

Timing: first poll after `MIN_POLL` 0.25 s, interval growing 1.25x and 1.5x
once the value has repeated `SETTLED_AFTER` 3 times, ceiling `MAX_POLL` 2.0 s.
`MAX_WAIT` is 90 s, chosen so the budget plus one in-flight poll still fits the
120 s client timeout (90 + 30 = 120). That is why you get a verdict instead of
an opaque client-side timeout.

The residual hazard is Studio's, not ours. The condition is spliced in as
**code**, so a condition Studio cannot parse is compiled into the command before
any handler runs, and such a command has wedged Studio's command execution
before (measured 2026-09-30). Bounded on this side; only a Studio restart clears
it. Keep conditions to simple comparisons.

Play mode is supported: all three DataModels are honoured and there is no
`loadstring` in the probe. That fix is **read from the code, not measured in
play mode.** An earlier version wrapped the condition in `loadstring`, which
resolves in Edit and not in play, so the tool advertised a `datamodel_type` it
could not honour, and `TODO.md` records play-mode waiting as neither fixed nor
removed at the time of that ruling.

## Put the driver in the place, and open the session with `-task StartServer`

A tool call cannot both open the session and run code in it, because the Server
DataModel does not exist until the session is live. So the server-side work has
to be a Script in the place that runs on session start:

1. write the driver to `game.ServerScriptService.<Name>` while in Edit
2. launch a Studio on that place file with `-task StartServer`, host-side
3. the driver runs in the Server DataModel, does its work, and ends the session
4. poll from the host for whatever the driver left, with `extended_wait_for`
   and `datamodel_type: "Server"`

This is the *confirmed* route and the one to reach for. `AddPlayers` works on
it, measured. What is **not** measured is ending the session with a *result
value* on this route. `EndTest(value)` is reproduced on a multiplayer test
session; nobody has combined the two here.

Do not substitute `ExecuteMultiplayerTestAsync(n, args)` for step 2. It is
documented as the way to open a multiplayer session and it is **unverified
here**: the attempt to drive it blocked past the 120 s tool-call timeout and
never returned. That timeout is the condition the section above names as the
one that wedges the test service, and only a Studio restart clears that. So the
one call that would replace step 2 is the one call whose timeout costs you the
Studio.

So `-task StartServer` plus a result value is a real gap, and it is open.
`TODO.md` records the two routes as being in tension and names a scratch
instance acting as a mailbox, or a persistent host, as the things that would
close it. Neither exists.

`extended_insert_asset_from_file` with `file_type: "script"` puts a file from
disk into the tree.

`extended_run_tests` is the supported wrapper when you only need a play test to
run and report. Reach past it only when you need to drive the session yourself.

## Reporting from a driver

The driver runs in the Server DataModel, and anything it parents there is
destroyed on teardown. So attributes set on a Server-DM instance are gone by the
time you can read them.

Put the holder somewhere that outlives the session, or report through the
console. Note that a server-side `print` during a multiplayer test was **not**
observed reaching `get_console_output` in testing, so verify the channel before
relying on it. See `rsx-console`.

## Per-DataModel targeting

`datamodel_type` covers `Edit`, `Server`, and `Client`. In a multiplayer test
with several clients there is no convenience mapping from "player 2" to a
`studio_id`: each `-task StartClient` is its own process with its own mesh row,
so they are addressable by id, but nothing here hands you the right one. That is
the gap, and it is a lookup rather than a reachability limit.

## Running tests, versus finding out why one failed

`extended_run_tests` is a thin wrapper, and thinner than the tool description
suggests. Read from `extended/extensions.py::run_tests`: it calls
`start_play()` / `stop_play()` (`start_stop_play` underneath), waits for the
console to stop changing, and returns `{passed, console_lines, errors}`. It
never touches `StudioTestService`, so it opens the **local single-player**
session, the one that cannot host extra players.

`passed` is `not errors and not missing`, where `errors` is a substring scan of
the console lines and `missing` only exists if you passed `test_paths`. Two
consequences worth stating:

- **`test_paths` is optional, so a place with no tests returns `passed: true`.**
  Pass `test_paths` when you need "there were tests" to mean something.
- `passed: true` means no console line contained `error`, `failed`,
  `stack trace` or `exception`. It does not mean assertions passed.

For anything else, authoring TestEZ specs or reading **why** a test failed, use
Roblox's `rbx-unit-test` skill through the relayed `skill` tool. That one knows
the spec conventions and how to interpret a failure; this one cannot, because it
only sees console lines.

Two things to carry across when you switch. The console's newline shape is
consumer-dependent, so match the pattern rather than parsing lines (see
`rsx-console`). And the waiting rules above hold here too: a blocking call
inside `task.spawn` wedges the test service, and only a restart clears it.
Neither is documented on the `rbx-*` side, because both belong to this
transport rather than to the engine.
