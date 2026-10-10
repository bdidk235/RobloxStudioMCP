# Evidence log — closed research, moved out of `TODO.md`

Everything here is **complete**: findings recorded, nothing open. It was removed
from `TODO.md` on 2026-10-03 because that file had grown to 2790 lines
while holding only 61 open items, and a reader crossing it to find out whether
there was work to do was the problem rather than the record.

**Nothing here was deleted or rewritten.** It is the text as it stood, and git
holds it at every commit. `TODO.md` now carries the open work and the withdrawals;
this file is where a *why* is looked up once the question is already known.

**Paths named below are as they stood when written, and some have since moved.**
`parity/` is now `contract/`, and `test_parity.py` is now
`python/tests/test_contract.py`. The *why* is what this file is for and it does not
expire; a path does. Where a line below reads like a description of how the repo is
enforced *now*, it is a description of how it was enforced on the day it was written
- lines 772, 778, 780 and 1047-1052 name files that no longer exist. For current
state read the live file: `contract/tools.json` for the generated contract,
`TODO.md` for open work, `python/tests/` for the gates.

Sections: 25. No line count: a file cannot state its own length and stay true -
adding this note changed it, and nothing gates it.

## macOS: researched and implemented, still never executed

> **Superseded 2026-10-01 on the "never run" part, not the rest.** There is still
> no Mac here, but live-Studio CI *did* run on `macos-latest` on 2026-09-14 and
> failed — twice over, for reasons unrelated to this code. So macOS has been
> exercised as far as *"Studio installed, first-run init completed"*, and never
> as far as MCP attach. See *"why the live-Studio CI jobs never worked"* below for
> what actually stopped it and what a retry must do first. Everything below about
> the macOS branch of `platform.py` is still theory checked against sources.

The macOS branch of `platform.py` was written from theory. It has now been
re-checked against what people have actually done, and two things changed. **It
still has never run** — there is no Mac here — so this is about correctness
against sources, not against a machine.

**The launcher and the mesh proxy are different files.** On macOS they sit in
the same directory and both sound like "the Studio binary":

| | path |
|---|---|
| launcher, takes `--task` | `/Applications/RobloxStudio.app/Contents/MacOS/RobloxStudio` |
| mesh proxy, what we launch | `/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP` |

`roblox.py` held the proxy and `platform.py` the launcher, so this was already
right — but nothing recorded *why* they differ, which is how one gets replaced by
the other during a tidy-up.

**`roblox-studio:` is not offered on macOS, and that is a change.** The research
found no evidence the scheme is registered on a Mac: no readable
`CFBundleURLTypes`, no forum post opening one, and Roblox's docs documenting only
the two *binary* routes for opening a place. `open_uri` now raises
`CAPABILITY_DENIED` on macOS instead of running `open`, which writes to stderr,
exits non-zero, and — because that call site discarded both — reported a launch
that did nothing as a launch that did nothing. Not a claim the scheme is
*absent*; a claim that an unevidenced route should not be offered.

Three decisions recorded rather than changed, because they are right:

* **`ps -axo`, not `pgrep -fl`.** Prior art (`Chrrxs/robloxstudio-mcp`) chose
  `pgrep` deliberately — the command line arrives as a clean field — but gave up
  per-process start time and substituted a boot id. We *need* start time, which
  is what `parse_ps_row` exists to parse. A considered difference, not oversight.
* **Non-zero exits ignored** on both `ps` and `lsof`. `pgrep` exits **1 when it
  matches nothing**, the normal state of a machine with no Studio; treating that
  as an error would report "could not enumerate processes" for a machine that
  simply has none.
* **`127.0.0.1` stays literal.** On macOS `localhost` can resolve to `::1` while
  the mesh listens on IPv4 only. Refactoring it for tidiness would introduce a bug
  on the one platform that cannot be tested here.

Still unverified, and labelled so: whether a **second** macOS Studio instance can
be created at all (if single-instance is app-level, `--task EditPlace` may reuse
the running window — a silent success that breaks identity); the **log filename
token width** on macOS (the weakest claim here, from a Windows-era example);
whether **`.lock` files** have a macOS location at all, in which case that logic
should be gated off rather than translated; and **no `MainWindowTitle`** exists on
macOS, so a title cannot be a fallback for telling two Studios apart — never in
the chain here, but the capability gap is real. Note
`~/Documents/Roblox/Plugins` **is** TCC-protected and does need Full Disk Access;
the two Roblox directories this module uses are not.

## Identity: the console write is last, and counted

`studio_id -> pid` was already correctly ordered — the process's own log, then
the `-parentPid` edge for a play-test member with no mesh name, then the console
token, gated on `allow_console_write`. **No tool parameter was added**; the flag
stays internal, per decision.

The `pid -> studio_id` direction (`_identify_launched`) **was not**: on a failed
name match it printed a join token into *every* attached Studio in turn, one mesh
round trip each, in a user-visible log.

**And the fix I wrote does less than I claimed.** I reordered candidates by
evidence and wrote that this "costs one print instead of an average of half of
them". The test written to pin that claim **disproved it**: the cheap branch
returns whenever exactly one row matches, so the fallback only runs when the
count is zero or two or more — and in both cases the sort key is constant. The
ordering is inert. It is kept because it is correct and free, and the code now
says so.

What the change delivers: **every path reports `console_writes`**, so "asked one
Studio" and "sprayed four and got nothing" are distinguishable from outside.
That is honest accounting, not a reduction.

Reducing the count for real needs a signal the function does not have —
excluding mesh rows that another process's log already claims — which means
reading every live process's log. New cost traded against user-visible writes,
and **worth measuring on a Mac rather than guessing at here.**

## Which skill answers a question — read this before writing a probe

There are **two** skill systems here, reached by **different tools**, and
conflating them is the recurring waste in this project.

**Neither involves a plugin.** `extended_*` is not a Studio plugin and nothing is
installed into a place: it is this repo's client in front of the MCP Studio
already ships. The `rbx-*` skills come from that same **built-in assistant**, so
they cannot be configured or extended from our side — we only relay them.

| question | go to | tool |
|---|---|---|
| How do I get a value out of Studio over this connection? | `rsx-*` (7 skills) | `extended_skill` |
| Does this engine API exist, and what is it called? | `rbx-docs-search` | relayed `skill` |
| I want to **pause** and inspect | `rbx-debug` | relayed `skill` |
| What is actually in the tree? | `rbx-scene-analysis` | relayed `skill` |
| Why did this test fail? | `rbx-unit-test` | relayed `skill` |
| Frame timing | `rbx-perf-profiling` | relayed `skill` |
| UI across device sizes | `rbx-device-simulator-lua` | relayed `skill` |

**The most expensive wrong turn in this repo** was reaching for
`extended_breakpoints` when the goal was to stop and look at state. It is
logpoints only: a halting breakpoint halts the scheduler that would have
returned the result, measured as a 120 s timeout with the breakpoint working
perfectly. For pausing it is `rbx-debug`, full stop.

The routing is now written down where it will be read:
`src/roblox_studio_mcp/skills/README.md`, a routing table in `AGENTS.md`, and a
"see also" in each skill with a real boundary (`rsx-breakpoints`, `rsx-capture`,
`rsx-discovery`, `rsx-playtest`).

## Built and proven

- [x] **`extended_capture`** - Python `extended/capture.py`, Node
      `extended/capture.ts`. Lossless RGBA -> PNG, host-side, no dependency.
      Live on 1233x754: raw 3,718,728 B (`== w*h*4`), 42 chunked appends, every
      CRC valid, inflated IDAT `== h*(w*4+1)`, 0 mismatches on round trip,
      scratch module destroyed. Node emits 635,388 B and Python 642,576 B
      (different deflate levels, both valid) and **spot pixels are identical**:
      sky `(136,194,219)`, ground `(93,102,124)`. Re-verified after a Studio
      version bump, same pixels, so the path is stable across engine updates.
- [x] **`extended_breakpoints`** - Python + Node. Non-halting logpoints via
      `ScriptDebuggerService`. Live: **60 contiguous hits, i=1..60**, live local
      interpolated, from the first iteration of an 80-iteration loop.
- [x] **`extended_script_grep`** - Python + Node. Orphaned in Node until this
      session: `node/src/extended/grep.ts` was written and never registered.
- [x] **`extended_studio_identity` / `extended_list_studios`** - Python + Node.
- [x] **Tool description budget** - prose cut 4,921 -> 2,361 chars (-52%),
      extended total 12,062 -> 9,502. Enforced by tests in both languages
      (per-description <= 450, total <= 2700).
- [x] **`extended_skill`** - Python. Serves the `rsx-*` skills from
      `python/src/roblox_studio_mcp/skills/`, packaged into the wheel as package
      data. Seven skills, `rsx-*` prefixed so they cannot be confused with
      Roblox's `rbx-*`: `rsx-transport`, `rsx-console`, `rsx-targeting`,
      `rsx-breakpoints`, `rsx-playtest`, `rsx-capture`, `rsx-discovery`.
      Index 1,984 chars against 75,377 of bodies, so ~2.6% is always-on and the
      rest is on demand. Verified over real stdio on a wheel installed into a
      clean venv: 7 skills loaded, both arms (`index` and `skill_name`) answered
      with `isError: false`. **The earlier entry here said "Serves `skills/*.md`
      from the repo root" and was wrong in the way that mattered** - see below,
      *The skills were not in the wheel at all*.

## Settled by measurement

- [x] **`ReadPixelsBuffer` has no 1024^2 cap.** 2048x1024 = 8,388,608 B (2 M px)
      reads back exactly. Chrrxs' `MAX_TILE_SIZE=1024` tiling is unnecessary
      here. Do not port `readPixelsTiled`.
- [x] **Returns DO work** on both `execute_luau` and
      `extended_execute_luau_from_file`; the old AGENTS.md note that inline
      `execute_luau` drops a return value is wrong for the current build.
- [x] **But the return channel truncates at exactly 100,015 chars.** So returns
      carry the small header only; pixels go via the scratch module.
- [x] **`buffer.tostring` is NOT base64.** The capture path base64-encodes, then
      `buffer.tostring` moves the ASCII into a string. **`EncodingService:
      Base64Encode` does accept a buffer** - the older note in this file said it
      rejected one, which was wrong and cost a 70x-slower hand-rolled loop.
- [x] **Scratch-module ceiling is 6,291,456 B**
      (`ScriptEditorService:UpdateSourceAsync`); 8 MB fails `bad allocation`.
- [x] `script_read` prefixes lines with `     1->`; harmless for a single-line
      payload but corrupts base64 if left in.
- [x] `UpdateSourceAsync` is **not** a `ModuleScript` member.
- [x] **Scratch module names must be dot-free** - `script_read` uses
      dot-notation paths, so a dot truncates the lookup.
- [x] **Console must never carry pixel data.** A ~5 MB `print` permanently
      wedged `get_console_output` / `extended_watch_output` for a Studio.
- [x] **Device emulator works end to end.** 45 devices; `SetDeviceAsync`,
      `SetOrientationAsync`, `SetResolutionAsync` all ok; `StopSimulationAsync`
      restored to `default`. `dpi=132` for an iPad 10th gen is the correct real
      spec. `SetOrientationAsync` is rejected on non-mobile devices with
      "orientation can only be set on mobile (phone or tablet) devices".

## Studio identity: what is actually possible

A Studio instance **cannot** be tracked across a restart, because a restart
creates a genuinely new instance and no durable in-band identifier exists.
Probed and ruled out: `game.PlaceId` and `game.GameId` are both `0` for an
unpublished place; `game.JobId` is empty outside a session; and there is no
process-id API reachable from Luau (no `os.getpid`, no `ProcessService`, no
`game.ProcessId`, no `DiagnosticsService`, `settings()` does not exist).

Two tiers, and conflating them is the mistake:

- **Session tier** (dies with the process): `studio_id` addresses,
  `GetDebugId` separates same-place instances. Neither is durable.
- **Place tier** (durable, coarse): place file path or `game.Name`. Cannot tell
  two windows of one place apart - two Studios on one place give identical
  window titles, identical `game.Name`, identical `PlaceId=0`.

Getting PIDs is host-side and clean, because the mesh is a WebSocket on
`127.0.0.1:13469`:

```powershell
Get-NetTCPConnection -RemotePort 13469 | Where-Object State -eq 'Established'
```

Filter to `RobloxStudioBeta`; `StudioMCP.exe` proxies also hold connections and
the listener is itself a holder.

**A log states its own PID, so the join needs no console write.** Found by
reading a real log (`..._T091205Z_Studio_75655_last.log`, 1,628 lines, 47
channels) rather than by probing. Python `extended/logid.py`.

| what | where | measured |
|---|---|---|
| PID | `[FLog::UIThreadNotifier] Constructing UIThreadNotifier for process '16240'` | **64/66 logs**, not the 47/47 an earlier partial count reported. The 2 without: `RobloxStudioInstaller_0EA05.log` (an installer log, not a Studio) and `0.741.19.7411056_20260930T110342Z_Studio_163B7*.log` (1,335 bytes, 12 lines - a Studio that died ~0.36 s in, before the notifier line is written). Among the 64: all PIDs distinct, none reused across logs. The denominator needed qualifying, not correcting: a Studio that dies before line ~12 has no PID to report, so a nonzero miss rate is expected rather than a defect. |
| command line | untimestamped header line, verbatim | 3 spellings for the place, all needed |
| role | `-task` in that header | `EditPlace` 17, `StartClient` 10, `StartServer` 7, `EditFile` 6 |
| Session GUID | `[FLog::Output] Session GUID is ...` | 45/45 distinct, per-process |
| Machine GUID | `[FLog::Output] Machine GUID is ...` | **2 distinct in 45 logs - not a stable host identity** |

The command line spells the place three ways, and parsing only the first is what
left 8 of 45 logs with no place at all - the entire play-test population:

- `--localPlaceFile <path>` - Edit
- `-localProjectFile <path>` - `StartServer` / `StartClient`
- `roblox-studio:1+task:EditPlace+placeId:N+universeId:M` - URI route

`-parentPid` gives the process tree for free: measured `StartServer` 19028 with
4 `StartClient`s all parented to it, and the server itself parented to the Edit
Studio that started the test (22656). So a server and its clients, which all
report `name: null`, are distinguishable from logs alone.

**What it cannot do**: identify a `studio_id`. The mesh name and the command line
only meet for the file route. The URI route's mesh name is
`Template_<placeId>_AutoRecovery_<N>.rbxl` and `N` is a per-launch counter the log
never records, so two URI launches of one place stay indistinguishable. That case,
plus `name: null`, still needs the token join.

The token join is now a last resort, and exact: it used to match the log's start
stamp to `CreationDate` within 5s, which misfires exactly when two launches are
seconds apart. It reads the PID from the log. Log stamps are UTC, `CreationDate`
is local, so parse the `Z` as UTC or the comparison is hours out - measured 3h on
this machine (UTC+3).

### Cost at realistic scale

The shape that matters is **many logs, 2-3 Studios** - a long-lived machine
accumulates logs without bound. Nobody has 400 Studios open; everybody eventually
has 400 logs.

- Only a **64 KB prefix** is read. All identity fields sit within 3,809 bytes in
  every log measured (0.21% of the largest, a 1.7 MB file). Cost tracks file
  count, not total bytes.
- Sorted by **filename stamp, not mtime**: no `stat` call, and the stamp is fixed
  while a log is being written, so the order cannot shift underfoot.
- The time-window filter is **not a clear win and was nearly cut for it.** At
  3,000 logs with the live Studios newest it is *slower* (0.098s vs 0.056s),
  because stamp ordering already finds them first. With an old Studio buried
  under 3,000 newer dead logs it is **13x faster** (0.097s vs 1.285s). Kept for
  the second case. It is a filter, never a decision: a narrowed pass finding
  nothing falls back to a full sweep.
- 3,000 logs / 3 Studios: **0.097s**. 47 real logs: 0.002s.
- An earlier scaling harness reported 18-26 ms per log. That was measuring its own
  400 x 1.8 MB write-back, not the resolver; a single read of that log is 0.53 ms.

### Still unmeasured

- **100 Studios.** This machine hit its 31.3 GB commit ceiling at 5 concurrent
  Studios and processes began dying, so 100 was never reachable. The *join*
  extrapolates cleanly (bounded by the sweep, not the Studio count); whether 100
  Studios can attach to one mesh is unknown, and the mesh payload shape at that
  scale is unknown.
- **Durable host identity.** `Machine GUID` looked like the missing federation
  tier and is not: 2 values across 45 logs. No per-machine id was found, so
  multi-machine remains without one.

- [x] **Add host-side PID discovery as a capability.** Done via the log's own PID
      line, so it needs no opt-in and no console write. `extended/manage_instance`
      and `logid.py`. The old name-match path is gone: matching a mesh name to a
      process is now a comparison against that process's own recorded command
      line, which is what makes it evidence rather than a guess.

## Type checking: mypy vs pyright, measured

Python had **no type checker at all** - 171 of 233 defs carried annotations that
nothing validated - while Node has had `tsc` as its build step throughout. That
asymmetry is not cosmetic: **the worst bug found in this project is precisely the
class a checker catches**, and it shipped because the only test on the tool
asserted its *description*.

Both checkers were installed and run against the whole package.

| | mypy 2.3.1 | pyright 1.1.414 |
|---|---|---|
| findings on `src/` | 9 (8 real, 1 false positive) | 7 (all real) |
| elapsed | 6.1 s | 4.1-6.3 s |
| after fixes | **1 error (a false positive)** | **0 errors** |

The two agreed on the seven real defects. mypy additionally reported
`instance.py:728`, which is **wrong**: line 727 filters `if when is not None`, so
the rebound dict genuinely is `dict[int, float]`. mypy narrows the loop variable
in a dict comprehension but not the resulting *container* type - a known
limitation. pyright got it right.

**pyright is the gate.** Same true-positive yield, better precision - and
precision is what decides whether a gate stays respected. mypy remains installed
and is still useful for `reveal_type` output when narrowing goes wrong.

- [x] **`pyrightconfig.json`**, with only the rules that earn their place. Three
      are errors because each found a real defect on first run:
      `reportOptionalMemberAccess` (3 latent `None` dereferences),
      `reportArgumentType`, `reportOptionalIterable`. Style rules are off, because
      they would bury those three.
- [x] **The gate is enforced, not advisory** - `tests/test_typecheck.py`, which
      skips if pyright is absent so it never blocks a contributor who has not
      installed it.
- [x] **The gate has a negative control.** `test_the_gate_would_actually_fail`
      feeds pyright the deliberately-broken acid-test file and asserts a non-zero
      exit. A gate that cannot fail is decoration. This caught a bug in the gate
      itself: a *clean* run used to `skip`, so a working gate reported "skipped"
      and looked like it had not run.
- [x] **7 real defects fixed**, all in code that every runtime test already
      exercised, so none of them was visible behaviourally:
      - `client.py` - `_shell_cmd` and `_argv` were **never declared**, so mypy
        inferred each from its first assignment and rejected the other branch.
        Five of the nine mypy findings came from this one missing declaration.
        Declared as the mutually exclusive `Optional`s they are.
      - `client.py` - `proc.stdin.write()` with `stdin` typed `Optional`. Every
        spawn passes `PIPE`, so this was an unstated invariant; it is now stated,
        turning a latent `AttributeError` into a real message.
      - `client.py` - `' '.join(self._argv)` on a possibly-`None` value.
      - `extended_server.py` - `edits: Sequence = []` then `.append(...)`.
        **`Sequence` has no `append`.** The annotation was wrong, not the code -
        and it was invisible to every runtime path.
      - `instance.py:823` - `identity.get(...)` where `identity` is `Optional`.
        Unreachable in practice (if it were `None`, `pid` would be `None` and the
        guard above would have returned), but the correlation is not visible to a
        checker, so it is now explicit.
      - `server.py` - `getattr(...) or set()` needed an annotation.
- [x] **`WatchResult` is now a real `TypedDict`**, and `watch_output` /
      `_ConsoleWatch.poll` are annotated with it instead of `Dict[str, Any]`.
      **This - not the checker - is the actual fix for the shipped bug**, and the
      distinction is measured rather than assumed. See below.

### The honest limit, measured

`python/typecheck_acid_test.py` tests the exact bug shape against both checkers.

**Neither mypy nor pyright catches the bug as it was written.** With the return
type as bare `Dict[str, Any]`, and even with a correct `TypedDict`, a defaulted
`.get("text", "")` on a missing key is *legal in both* - `dict.get` accepts any
key and any default by design. Both checkers flagged the **subscript** form
(`result["text"]`) and both flagged the `None`-dereference class.

So the ordering is:

1. **The `TypedDict` is the fix.** It makes the three real keys visible in one
   place, and it catches the subscript form.
2. **The checker is the backstop** that keeps that annotation honest, and it does
   catch the `None`-dereference class, which no test in this suite would notice.
3. Neither alone would have caught the original line. Saying otherwise would be
   selling the tooling.

  ### ty is the default backend, pyright stays in CI (2026-10-10)

  Measured on the gate's scope: **ty 0.0.85 runs in 0.40 s, pyright in 12.96 s
  (32x)**, and ty caught the same live defect pyright caught (`Optional[str]`
  passed where `str` was required, at the exact line). `ROBLOX_TYPECHECKER`
  selects the backend, default `ty`; CI pins `pyright`, because ty is `0.0.x`
  and reserves the right to redefine diagnostics between releases.

  The four ty diagnostics on first contact were resolved with no rule
  suppressed: one dead file deleted (a stray module shadowed at runtime by the
  package directory of the same name, imported by nothing, unshipped by
  `packages.find`), one `Dict[str, Any]` annotation, and one platform ignore
  comment next to pyright's own on the same line. Two misses on the way are
  recorded because they are the gate's own failure class: the PATH probe found
  `ty` but the subprocess then raised `FileNotFoundError`, so the binary path
  is resolved once and reused; and the suite's pointer gate caught the deleted
  filename in the new docstring, which is that gate working as designed.

## Closed sets: what a foreign type system would and would not buy

**Question considered:** keep Node not as a second implementation, but as a
*stronger type checker* - express the project's closed sets as TypeScript
discriminated unions and let `tsc` prove the Python conforms. A union can say
"handle every case or fail to compile". Python cannot, and neither can pyright.

**Answer: the idea is sound, the vehicle is wrong.** Exhaustiveness over a closed
set is a real capability gap in pyright, and checking for it found a real defect
in about twenty minutes of Python. A TypeScript spec would have found the same
defect, and then needed maintaining forever.

The defect, on first run:

- [x] **`LAUNCH_FAILED` was unreachable.** It sat in the public vocabulary
      (`ALL_CODES`) with no pattern producing it and no handler raising it - it
      appeared only in `errors.py`, in the constant and the set. Every realistic
      launch failure classified as `UNKNOWN`:

      | message | was | now |
      |---|---|---|
      | `no complete RobloxStudioBeta.exe under ...` | `UNKNOWN` | `LAUNCH_FAILED` |
      | `Studio did not open a place within 75s` | `UNKNOWN` | `LAUNCH_FAILED` |
      | `MCP is not enabled in Studio` | `UNKNOWN` | `LAUNCH_FAILED` |

      This is **worse than a missing code.** A caller who writes
      `if (code === "LAUNCH_FAILED")` - which publishing the constant invites -
      silently never matches, and the branch looks handled. Fixed by adding the
      patterns; pinned by `test_closed_sets.py`.

      **No type checker on either side would have caught it.**
      `ToolError("LAUNCH_FAILED", ...)` type-checks in Python *and* TypeScript.
      Only "is this set fully connected?" finds it. That is precisely the one
      thing a foreign type system genuinely adds here.
- [x] **`PLACE_NOT_OPEN` stays in the vocabulary, and is no longer dead.**
      Ruling 2026-10-01 (user): **keep it**, rather than removing a published
      constant. It is the semantically correct name for the situation, and
      `STALE_STUDIO_ID` is the right *implementation* of it - a stale
      `studio_id` genuinely does present as "place is not open", and the
      recovery text (re-pin from a current id) is the correct recovery for
      both. So the code is not a competing claim on one message; it is the
      general case of a specific one.
      **The shadowing was never the bug - the missing alias was.** A caller who
      writes `if (code === "PLACE_NOT_OPEN")` gets a branch that never fires,
      which is the exact failure this file says a published-but-unproducible
      code causes. So the fix is to make the constant *mean* something, not to
      delete it:
      - `STALE_STUDIO_ID` keeps owning the wording (it must - that is what makes
        the recovery text correct), **and `PLACE_NOT_OPEN` becomes a declared
        alias** of it, so the branch is live and correct rather than dead.
      - `PLACE_NOT_OPEN` leaves `_KNOWN_UNREACHABLE`, because it is now
        producible. `test_every_known_unreachable_code_still_has_its_reason`
        would otherwise fail, which is the gate working.
      Removing it instead would have deleted a name a caller may already branch
      on and made their code fail differently. Aliasing costs a few characters
      in one file and keeps every existing branch correct.
- [x] **`tests/test_closed_sets.py`** - the permanent version, 9 tests: every code
      is producible, every pattern maps to a declared code, every advertised
      `action` is handled rather than merely accepted, the launch URI carries
      exactly four keys, and the role parser produces only declared roles.

**What this settles.** The dual-language *implementation* was the wrong way to buy
exhaustiveness. A closed-set test buys it for one file instead of a second
codebase, and it found the same bug. Keep the habit - re-derive a module in a
different shape and it finds things nothing else will - but do not keep paying
for a parallel implementation to get it.

## Bugs found and fixed

- [x] **`grep` served a line NUMBER as the excerpt, and skipped the read that
      would have supplied the text.** Found by the type-annotation pass, not by a
      test. The excerpt lookup read `hit.get("excerpt") or hit.get("line") or …`,
      but `line` is *also* the first-choice alias for the line **number** two
      lines above. So a server answering `{"line": 12}` put the int `12` into
      `excerpt` — and because `12` is truthy it **skipped the `script_read`** that
      would have fetched the real source. Every consumer treats `excerpt` as text.

      This is the `WatchResult` bug class **one layer up**: an alias that is
      correct in one namespace and wrong in another. Fixed on two independent
      levels — `line` is no longer an excerpt alias, and a non-`str` excerpt is
      coerced rather than passed through. Mutation-tested: restoring the original
      line makes the new test fail with `12 is not an instance of <class 'str'>`,
      so the test bites rather than decorates.

- [x] **`ResolveOk` / `ResolveNotFound` / `ResolveAmbiguous` replace one dict with
      holes.** A single shape would let `result["match"]` type-check on a
      `not_found` reply that has no such key — the same silent-wrong-answer class,
      and the reason `Dict[str, Any]` was a hole worth closing rather than a style
      preference.


All were found by the live work, not by the test suite, and all report something
other than what happened.

- [x] **`extended_watch_output` returned zero lines on every call, and reported
      success.** The handler read `result["text"]`; `watch_output` returns a dict
      keyed `new_lines` / `total_lines` / `last_line`. `dict.get` supplied the `""`
      default, the split produced nothing, and the tool answered
      `{"returned": 0, "matched": 0, "total_seen": 0}` with `isError: false`.
      Live: `total_seen: 0` against a real Studio.

      This is the tool AGENTS.md and the `rsx-breakpoints` skill tell an agent to
      use for reading breakpoint hits, so the failure mode is **"no breakpoint was
      hit"** - indistinguishable from the truth.

      It survived because the only test on the tool asserted its *description*.
      That is the lesson worth keeping: a description test proves the surface, and
      the surface was correct. `tests/test_watch_output.py` now pins the payload
      (13 tests), and the same test file exists on the Node side.

      Honest limit on the evidence: I could not get a **positive control**. Both
      attached Studios report a 0-char console this session, so "console empty" and
      "tool broken" are not separable by that observation alone. The defect is
      certain by inspection - there is no `text` key - and the fix plus the tests
      establish the behaviour, but the live confirmation is consistent-with rather
      than exclusive-of the alternative. Re-check after a restart with output
      flowing.

- [x] **`max_lines: 0` was silently replaced by the default 200.** The handler
      read `arguments.get("max_lines") or 200`, and `0` is falsy, so a caller
      asking for **no** lines got 200. A default in disguise, which is the same
      defect request P0.2a is about reached from the other direction - and the
      wrong direction to get wrong, since it is also a bulk-data hazard. Now an
      explicit `is None` test, with `INVALID_ARGUMENT` for a non-integer.

- [x] **Node's `extended_watch_output` had no `pattern` and no `max_lines`.** Its
      schema carried only `studio_id`, so the filter and the cap existed on one
      implementation alone. Because unknown parameters are **silently ignored**
      (P0.2a), sending `pattern` on the Node side did not fail - it returned the
      whole buffer, which reads exactly like "no hits". Ported, and the port is
      covered by `node/tests/errors.test.ts` and the mirrored Python tests.

- [x] **`place_from_command_line` had the same `(\S+)` bug as the log parser.**
      Found while porting it to Node, where the mirrored test caught the port
      immediately. Harmless for this machine's own paths - they live under
      `%TEMP%`, no spaces - and silently wrong for any place in a directory that
      has one. A truncated path still yields a basename, a *fragment*, so it never
      matched a mesh name rather than raising. Now routes through
      `logid._path_after`.

- [x] **A JS/Python `split` difference broke the macOS process parser.** Python's
      `split(None, 6)` **keeps the remainder** in the last field; JavaScript's
      `split(re, 7)` **truncates** it. The port therefore cut every command line at
      its first space and returned null for all of them - a macOS-only failure that
      read as "no Studio running". Caught by the mirrored test; fixed by splitting
      fully and rejoining the tail.

- [x] **`extended_manage_instance` marked `action` as required *and* gave it
      `default: "list"`.** The default can never apply, and it advertises the
      argument as optional - a default in disguise, which this project rejects.
      Caught by the new parity test. Node had the same shape on
      `extended_breakpoints`, plus **no `required` list at all**.

- [x] **Every `action=launch` reported failure on a launch that had succeeded.**
      `launch_instance` reused the name `resolved` for two things: the absolute
      place path, then the result of `_identify_launched`. The path was overwritten
      with a dict, so `os.path.basename(resolved)` raised `TypeError: expected str,
      bytes or os.PathLike object, not dict` **after** the Studio had started,
      attached and opened its place. Worst shape: real side effect, report says the
      opposite, and nothing in the error mentions an orphan. Renamed `identified`.
      Pinned by `test_launch_response.py`, which asserts the *response* - that is
      the only signal a caller gets.
- [x] **The token fallback could never run in the case it exists for.**
      `_resolve_by_console_token` called `execute_luau` with no `studio_id`, relying
      on inference - and inference refuses to choose when several Studios are
      connected. Ambiguity only arises *because* several are connected, so the last
      resort failed exactly when needed. Measured live against two Studios both
      named `Place1`: `could not print a join token: 2 Roblox Studio instances are
      connected`. It now names its target. Pinned by `test_token_fallback.py`.
- [x] **`logid` missed every `PlaceSessionId` line.** The 64 KB prefix stopped
      short: measured, the numeric-form lines sit at **64,520 to 74,960 bytes**, so
      all 13 were outside it. Prefix is now 256 KB - the measured knee: 0.100 ms per
      file at 64 KB vs 0.130 ms at 256 KB, 1.3x cost for 4x the bytes, because the
      cost is mostly the open rather than the read. Past that it stops being cheap
      (1 MB costs 0.626 ms).
- [x] **The token fallback's pool was restricted to *attached* processes** - wrong,
      since an unattached process is still a candidate and is typically the one just
      launched, and impossible once attachment stopped being fetched.

## The performance finding that matters most

- [x] **One PowerShell spawn costs 1.9 s, and a resolve was spending 9 s on it.**

      | | cost | share of a resolve |
      |---|---|---|
      | `attached_studio_pids()` | **9.5 s** | 99.8% |
      | `list_studio_processes()` | **9.0 s** | 99.9% |
      | the log sweep | **0.08 s** | 0.02% |

      The log work got twenty times faster this session and it is **0.02% of the
      call**. That is the whole lesson in one table.

      Fixed three ways: attachment is not fetched where nothing joins on it
      (`with_attachment=False`, 9.5 s -> 2.1 s); the process list is cached 2 s; and
      the launch loop bypasses that cache with `max_age=0.0` because it asks whether
      a pid that did not exist a moment ago exists now.
      **Resolve 13.4 s -> 2.7 s.** The remaining ~2.5 s is one unavoidable spawn;
      below that needs a persistent worker rather than spawn-per-call.
- [x] **The launch was never hitting its budget.** `LAUNCH_WAIT` is 75 s; the file
      route opens at **26.3 s**. Process and log naming it are available at **1.5 s**,
      then a 25 s gap, then the place and the mesh name arrive together. The 44 s
      observed was 26.3 s + 2 s sleep + a **13.4 s** resolve - the PowerShell cost
      again, plus the shadowing bug.

## The lock file, and a claim retracted

- [x] **A `.lock` file names the PID holding it.**
      `%LOCALAPPDATA%\Roblox\RobloxStudio\AutoSaves\<place>.lock` holds
      `pid | processName | machineName | sessionGuid |  |`, measured
      `12324 | RobloxStudioBeta | DESKTOP-IH0RL4D | 6cc6718a-... |  |`. So
      `studio_id -> mesh name -> <name>.lock -> pid` needs **no log read at all**.
      Two traps, both real: a lock **outlives its process** (one named pid 18896 long
      dead), and **not every document holds one**.
- [x] **RETRACTED: the lock join does not separate concurrent URI launches.**
      `locks.py` claimed exactly that. Live, with two URI launches of one place:
      both reported the mesh name **`Place1`**, **neither held a lock**, and the only
      locks present were stale. It resolved **0 of 3** named rows. It works only when
      Studio creates an autorecovery document - **1 log in 16**. The docstring claim
      still needs correcting.
- [x] **Two Studios on one place can both be named `Place1`.** Observed live, and it
      is what a URI launch usually produces, not a corner case. No path, no lock, so
      neither host-side join has anything to key on. The token print is all that is
      left, which is why fixing it mattered.
- [x] **The `AutoRecovery_N` counter IS in the log - twice over, I said otherwise
      twice.** On the *path-suffixed* `PlaceSessionId` line:
      `PlaceSessionId: 77C06B69-...-C:/.../AutoSaves\Template_95206881_AutoRecovery_3.rbxl`.
      It appears in 1 of 16 URI logs - the ones that made an autorecovery document.
      The matcher now narrows ambiguous URI candidates by it instead of declaring
      the case unresolvable.
- [x] **A per-place-open GUID exists** on that line, distinct in 13 of 13 logs that
      opened a place. The mesh never reports it, so it cannot close a join alone.
- [x] **The mesh row carries only `id` and `name`.** Checked directly - no hidden
      field, no timestamp, no discriminator beyond the name.

## A fourth launch route

- [x] **File > New produces a child that names its parent Studio:**
      `-task EditPlace -universeId 0 -placeId 95206881 -userid 1183256136
      -parentPid 9352 -parentSessionGuid 1B7B85EC-... -baseUrl ... -channel production`
      The place arrives as **separate flags**, not inline in a URI, so `place_id`
      parsed as `None` for a process that plainly had one. Both forms now parse, plus
      `-parentSessionGuid`. `-parentPid` is the cleanest identity edge found here: it
      names the launching process outright, so a child needs no name matching.

- [x] **`-parentPid` is now wired to `name: null`, and the honest limit is
      measured, not assumed.** `logid.playtest_children` /
      `logid.resolve_unnamed_studio` walk the edge; the parent is identified by the
      *existing* log-PID chain (`live_identities`), so nothing new identifies it.
      `instance.resolve_pid_for_studio` routes a row with no name there instead of
      into the name join, which cannot start.

      What the edge buys, and what it does not:

      | | |
      |---|---|
      | identifies a child's **place** and process tree from logs alone | **yes** - client -> server -> the Edit Studio that pressed Play |
      | narrows the console-token pool from every Studio to the play test's own processes | **yes** - 5 processes to 4 in the measured shape |
      | resolves a `studio_id` end to end | **only when one anchored child and one unnamed mesh row account for each other exactly** |

      The third row is the real finding, and it is a **negative** one. A play test
      is a `StartServer` plus its `StartClient`s: two processes, two `name: null`
      mesh rows, and **nothing in any log or on the mesh says which row is which**.
      The mesh row was already checked - it carries only `id` and `name`, no hidden
      field. So the ordinary play test stays unresolved, and it has to: the caller
      of this join is `terminate_process`, and a coin flip here is a wrong Studio
      killed. The refusal is loud - it reports both counts, the candidate pids and
      the tree the logs did establish - and it narrows the token fallback rather
      than pretending.

      The narrowing predicate is the **task**, not the flag. Plenty of Studio
      processes carry a `-parentPid`: the Roblox launcher points at whatever
      started Studio, and a File > New child points at its launcher and then *does*
      open a named place. Keying on the flag alone would put a named Studio into
      the candidate pool for an unnamed one.

      `-parentSessionGuid` is cross-checked against the parent's `Session GUID is`
      and reported as `True` / `False` / `None`. **The premise is unverified** - that
      the flag holds the parent *process's* session GUID is read off the flag's name
      and one command line, and was never observed to equal a real parent's. A
      disagreement is therefore surfaced as a warning and does **not** veto the pid
      edge; letting an unverified premise disable a working edge would be worse than
      reporting the conflict. `parent_session_guid` is uppercased at parse and
      `session_guid` is not, so the comparison is case-insensitive.

## Dropped from the build list

Roblox's own relayed set ships a **`skill`** tool with these already-written
recipes, so building tools for them duplicates shipped work. Check `skill`
before writing anything new here:

- `rbx-debug` (16,818 chars) - supersedes most of the breakpoint research above.
- `rbx-device-simulator-lua` (46,816 chars) - **device emulator: do not build.**
- `rbx-perf-profiling` - **micro profiler: do not build.**
- `rbx-scene-analysis` - **scene analysis: do not build.**
- `rbx-unit-test`, `rbx-docs-search`, `rbx-create-skill`.

This is the same architecture Chrrxs uses, and now ours too: a long skill read
on demand beats prose in a description paid on every call. The `rsx-*` skills
carry what Roblox's `rbx-*` skills cannot, which is this transport's traps. The
boundary is deliberate: **engine topics go to `rbx-*`, transport topics go to
`rsx-*`**, and the two are kept apart by prefix so neither is mistaken for the
other.

Skills to write next, all currently AGENTS.md-only and therefore invisible to a
tool-using agent: `rsx-write` (write vs update vs insert-asset, and the
`replaceAll` alias), and folding the Studio-launch recipe (baseplate copy plus
`--task EditFile --localPlaceFile`) into `rsx-targeting`, since a Studio with no
place open does not attach to the mesh at all.

## Rejected

- [x] **Fonts: premise disproved.** `FontFamily` is not creatable, absent from
      `ReflectionService`, has no `Enum` entry, and zero instances exist in any
      DataModel - it is a Content type, so `file_type: "font"` cannot yield a
      font instance. Reachable alternative is name-based lookup: 143 `.ttf` and
      `families/*.json` on disk, `Enum.Font` has 53 selectable faces.
- [x] **Creator Store load: authorization.** Chrrxs authenticate to the site
      with cookies; we deliberately do not hold credentials.
- [x] **`get_runtime_logs`: redundant** with `extended_watch_output`.

## Built 2026-09-30, per user rulings

Four rulings, applied in order. No live Studio calls in any of it - the temp
Studio may still be wedged from the fuzz probes, any Studio the agent did not
launch is off limits under the standing rule above, and nothing here needed a
Studio to prove.

**Ruling 1 - no cap raise; descriptions answer what/when/why, the rest goes
in a skill.** No fixed order, per-tool context. The audit asked the question
of all 16 and the answer is that the earlier -52% cut already did the job:
every sentence but two maps to a measured incident. The two cuts ("by
location", 14; "Cached host-side only", 23 - the latter moved a cache line
into `rsx-targeting.md`) funded the one genuine gap the audit found:
`extended_run_tests` (59 chars, no pointer) now routes to `rsx-playtest`
("Green-check only. See rsx-playtest.", 95). Net -2 across the surface:
**2,692 both sides, 8 spare, all 16 pairs byte-identical** (verified
programmatically). Policy going forward: new surface is funded by trimming
under what/when/why, never by raising the cap; the delete tool (~150) still
does not fit. A return-shape pointer for `execute_luau_from_file` was
considered and refused: the trap belongs to the relayed transport, not the
tool, and `rsx-transport` + the runtime note already cover every channel
except the empty-result case.

**Ruling 2 - `wait_for` fixed; play-mode capability not pursued.** The wedge
mitigation (bounded poll await, abort after 3 consecutive hangs with the
restart named) stands on its own - no mode logic changed, no
`datamodel_type` surface changed, play-mode waiting neither fixed nor
removed. The residual live question (is the temp Studio still wedged) needs
the restart + one `nil.foo == 1` probe, which is a Studio operation and is
not done here.

**Ruling 3 - the small batch, judged per item and built where good.** Lock
GUID parsing: built (paired edit). Registry expiry: built (30-day prune on
the write path, both sides). `_RECOVERY`: the DATAMODAL rewrite only - other
codes have no certain text and stay unseeded. `PLACE_NOT_OPEN`: kept with
its reason (compat risk, no gain).

**Ruling 4 - this section.** Gates at close: Python 639 passed + pyright 0;
Node 641 passed + build + typecheck clean; contract regenerated at 2,692.
Left open, not approved: `search_and_read` filter/validation, capture
`INTERNAL_ERROR`, console-claim scoping, the reverse closed-set test, the
raw-identity pin, P1/P0.1/consumer-count dispositions.

## Budget raised to 3,200, and `manage_instance` taught its returns (2026-10-01)

**Ruling (user): raise the cap, do meaningful reductions first, then expand
what is missing.** This reverses the earlier "never raise the cap, trim
instead" - because the cap had started dictating tool *behaviour*.

**Why it was wrong at 2,700:** with 8 characters spare, the only way to say
what `launch` returns was to delete something else. The budget was forcing a
choice between two descriptions when neither was wrong. Concretely: an agent
that does not know `launch` hands back a `studio_id` makes a **second tool
call** to find one. The cap was not costing prose, it was costing round trips.

**The reductions (right to do first):** three descriptions had genuinely
redundant clauses - `extended_studio_identity` said "does NOT survive a
restart, **so treat it as session-scoped**" where the first clause *is* the
second, and `extended_breakpoints` omitted that it toggles.

**What was added,** because it is what an agent needs *before acting* and
cannot get from a skill:
- `extended_manage_instance` - every action's **useful return**, named:
  `list -> mesh[].studio_id`, `launch -> the studio_id it opened`,
  `places -> paths + place_id`, `make_place -> a throwaway path`,
  `stop -> TERMINATES a process`.
- `extended_breakpoints` now says **"Toggles"** - it toggles on registry state,
  so calling it twice leaves you with *no* breakpoint and reports success both
  times. The description is the only always-on place that can prevent that.

**And a code fix the description exposed:** `action=list` returned PIDs and
**no `studio_id`**, making it useless as a first call - the agent had to follow
it with `extended_list_studios` and correlate rows itself. Both sides now return
the mesh rows alongside the process rows. **Not joined, deliberately:** two
Studios on one place both report `"Place1"`, so a name join is a guess, and this
tool has a documented history of reporting a confidently wrong Studio. Handing
back both sides lets the caller use the pair when it is unambiguous and *see*
that it is not when it is not - the project's standing rule against fabricating
a match.

Both `stop`-without-`studio_id` errors now say where the id comes from ("Call
action='list'"), and `parity/errors.json` was updated to match - the contract
pins exact wording, so the paired edit had to update it or the parity test would
fail correctly.

**Cap source moved.** The caps lived as a hardcoded `2700` in two test files
and in `build_contract.py`. The tests now read the generated
`parity/tools.json`, because a second copy of the number is a second number to
drift - the same lesson as the tool counts. An intermediate attempt imported
`parity/build_contract.js` from TypeScript; `tsc` rejected it (does not resolve
from `node/`), so it reads the JSON instead.

**Result:** 2,897 of 3,200, all 16 pairs byte-identical, 642 Python + 644 Node
tests green, pyright 0, typecheck 0.

## Review pass 2026-09-30 (two findings, both fixed with tests)

A reviewer pass over the session's changes found one bug and one stale
docstring, both fixed as paired edits:

- **Python registry prune crashed on truthy non-dict entries**
  (`AttributeError` from `(old_entry or {}).get`, uncaught) while Node
  deleted them - a crash plus a divergence, on a path that runs with every
  refreshing list against a file `_load` explicitly leaves unvalidated.
  Fixed with an `isinstance` guard (drop, never crash), pinned both sides
  ("dropped not crashed on" / "drops non-object").
- **Stale loadstring docstrings** in both waiting test headers, asserting the
  removed design on a safety-relevant mechanism. Rewritten to describe the
  raw-splice + bounded-poll reality. (And the pass caught its own near-miss:
  an edit deleted a `def` line instead of adding a test; restored
  immediately, verified by re-reading before running.)

## Retired 2026-10-01: the live-Studio CI tooling, and how to bring it back

The investigation is over and its tooling is removed, so the repo no longer
carries a cookie-dependent workflow with no way to make it pass. Deleted:

| removed | last commit holding it |
|---|---|
| `.github/workflows/macos-mcp-attach.yml` | `453f646` |
| `.github/workflows/studio-smoke.yml` | `d0883c8` |
| `.github/workflows/validate-cookie.yml` | `453f646` |
| `ci/fixture-baseplate.rbxl` | `f25bba6` |

Restoring any of them is one command, byte-exact:

```
git show 453f646:.github/workflows/macos-mcp-attach.yml
git show 453f646:.github/workflows/validate-cookie.yml
git show d0883c8:.github/workflows/studio-smoke.yml
git show f25bba6:ci/fixture-baseplate.rbxl
```

**The `ROBLOSECURITY` environment was deleted**, which took its secret with it.
It is a live credential with no consumer otherwise, and the cookie it held was
already known to be short-lived. Recreating it needs a fresh cookie, and the
validator that checked one is the second command above.

Kept deliberately:

- **`ci.yml`** - unit tests only, four jobs green on Windows and macOS. It never
  needed a Studio.
- **`studio-bundle-probe.yml`** - the read-only bundle probe. No secret, about 20
  seconds, and it is the tool that would re-check the two structural findings
  (hardened runtime, no `disable-library-validation`) if Roblox ever re-signs a
  bundle. Findings 12 and 13 are worthless without something that reproduces
  them.
- **`TODO.md`** - every measurement, including the retractions.

Nothing in `README.md` or `AGENTS.md` referenced any of the deleted files, so no
doc needed rewriting. `REVIEW-2026-10-01.md` names `ci/fixture-baseplate.rbxl`
once, in a list of what that review did **not** audit - a statement about a dated
pass over the repo, so it is left as written rather than edited to match today's
tree.

### The one thing that would revive any of this

A **self-hosted macOS runner with a real user session.** Not a better cookie, not
a better workflow. All three walls are properties of a hosted runner rather than
of this code: no interactive desktop on the Windows image, a sealed
hardened-runtime bundle with no obtainable app behind it on macOS, and an OAuth2
sign-in needing a browser authorisation a datacentre IP cannot complete. One
machine with a desktop and a signed-in account removes all three at once, and the
first command in the table above then runs unchanged.

## Saving a place: local vs cloud, researched 2026-10-02

The rule now lives in `AGENTS.md` standing rule 6. This is the evidence behind
it, kept separate because the two were very easy to conflate - I did.

**Provenance.** Everything marked `DOCUMENTED` was read from a page I fetched.
Everything marked `MEASURED` I ran. The one item marked otherwise was confirmed
only by a search snippet. **No Roblox API was executed.**

### Two operations, not one

| | destination | gated on | content-validated |
|---|---|---|---|
| **Local** (`.rbxl` on disk) | the filesystem | nothing - or a human | no |
| **Cloud** (`SavePlaceAsync`) | Roblox's cloud | identity + `AssetCreateUpdate` | no |

Neither is content-validated. The deep UGC validation is a *third*, unrelated
thing (below) and it applies to neither.

### Local: no API exists. Ruling: ask the user.

`DOCUMENTED` - the devforum thread is titled *"Add support for a place save API to
save local files in place."* A feature request is the evidence: the API does not
exist. The author's stated workaround is sending keystrokes to emulate Cmd+S /
Ctrl+S, described in the thread as *"flaky so isn't 100% reliable."*

`DOCUMENTED` - the built-in Studio MCP **does** have `user_keyboard_input`, which
is that same keystroke hack as a first-party tool. It does not help. The docs
(`create.roblox.com/docs/studio/mcp`) file it under **"Player input
simulation"**, beside `character_navigation` and `user_mouse_input`. This repo's
own transport notes independently record interaction tools as client-datamodel
only. Two sources agree: it drives the *game*, not the editor chrome, so it
cannot press Ctrl+S in the editor.

So the only reliable local save is the user pressing save. That is a ruling, not
a preference, and it follows from the absence of an API rather than from any
judgement about risk.

### Cloud: `AssetService:SavePlaceAsync` is real and works

`DOCUMENTED` - `create.roblox.com/docs/reference/engine/classes/AssetService`:

```luau
AssetService:SavePlaceAsync(requestParameters: Dictionary?): ()
```

Yields, returns nothing, **Capabilities: `AssetCreateUpdate`**. The page's prose
gives the usage - and this is the one line I could not find anywhere else:

```luau
AssetService:SavePlaceAsync({PlaceId = 1, SaveWithoutPublish = true})
```

`PlaceId` is **optional**; omit it and it saves the currently open place, which
may be a published one. That is the sharp edge, and it is why the rule says ask
first even though the call is one line.

**The place id is not a free variable**, and `TODO.md` records this repo's own
measured rule for it: a *template* place is its `Template_<id>_AutoRecovery_<n>`
id paired with **universe id 0** (`URI_UNIVERSE_ID`), because the real universe
id failed 3 times in 16 with `Error fetching latest place version`. An *unsaved
local* place has **no id at all** - Studio reports `PlaceId: 0`, and the copies
this project makes are named `Baseplate-<n>.rbxl`, for which
`template_place_id()` returns `None` rather than a fabricated id. Never invent
one.

`MEASURED` - from the leaked 2016 engine source
(`SANS3R66/roblox-2016-source-code`, `App/v8datamodel/AssetService.cpp`), which
gives three facts the public docs do not:

```cpp
static Reflection::BoundYieldFuncDesc<AssetService, void()> func_savePlace
    (&AssetService::savePlaceAsync, "SavePlaceAsync", Security::None);
```

- **`Security::None`** - callable from an ordinary script, no elevated security.
  Contrast `AllowInsertFreeAssets`, the one property on the service carrying
  *Roblox Script Security*.
- **It is web-API bound.** The constructor's URL fields - `placeAccessUrl`,
  `assetVersionsUrl`, `assetRevertUrl` - confirm cloud, not local disk. Now
  corroborated three independent ways: docs, engine source, and the absence of
  any local-save API.
- **It is rate-limited**, and this is the bit I did not know before looking:
  `savePlaceThrottle(&DFInt::S...`, alongside
  `createPlaceThrottle(&DFInt::CreatePlacePerMinute, &DFInt::CreatePlacePerPlayerPerMinute)`.
  Per-minute *and* per-player. So it is unsuitable for a tight save loop.

*Caveat: 2016 source. The architecture has clearly been stable, but the specific
`DFInt` constants are not current.*

`DOCUMENTED` - `CreatePlaceAsync(placeName, templatePlaceID, description)`
returns a new place id, same capability, and `templatePlaceID` is **required**, so
a brand-new account has a chicken-and-egg problem.
`CreatePlaceInPlayerInventoryAsync` is deprecated.

### What "UGC validation" actually refers to - and why it is not in this path

`DOCUMENTED` - `create.roblox.com/docs/marketplace/validation-system` is an
**avatar asset** gate: bodies, cosmetics, clothing, accessories, dynamic heads,
makeup, animations. It runs on Marketplace upload via Studio, or via
`AvatarCreationService`. Eight categories: Schema, Mesh geometry,
Texture/materials, Rigging/skinning, Inner/outer cages, Attachments, Dynamic
head, Security/moderation.

Four mechanisms worth having:

- **It is rasterization-based.** "Mesh is completely invisible" and "not opaque
  enough from a given view" come from rendering **six orthographic views** and
  counting opaque pixels. A geometrically valid mesh can fail on silhouette.
- **Custom attributes are banned** on published avatar assets - *"runtime
  key-value pairs that could carry hidden data or scripts."*
- **Skinning is restricted to the official R15 rig**; unauthorised joints are
  rejected because they *"could lead to unpredictable deformations or exploit
  avatar rendering."*
- **HRD/DRD bone animation is gated behind trusted-creator status**, separately.

And the line that settles the conflation: *"Assets that you don't intend to use
for the Marketplace, such as those for in-game use only, do not need to pass the
validation process."*

So `SavePlaceAsync` is in the **same capability class as `CreateAssetAsync`** -
both `Security::None`, both web-API, both gated on creator identity. Neither is
content-validated. The place path asks *"may this identity write to this place"*,
not *"is this place well-formed"*.

Capability taxonomy across `AssetService`, which is the useful part of the surface:

| capability | members |
|---|---|
| `AssetCreateUpdate` | `CreateAssetAsync`, `CreateAssetVersionAsync`, `CreatePlaceAsync`, `SavePlaceAsync`, `PromptCreatePlatformContentAsync` |
| `LoadUnownedAsset` | `LoadAssetAsync` |
| `DynamicGeneration` | `CreateEditableImage/Mesh`, `CreateDataModelContentAsync` |
| `Basic` | `CreateDecalAsync`, `CreateMeshPartAsync`, `CreateSurfaceAppearanceAsync` |
| `AssetManagement` | `PromptImportAnimationClipFromVideoAsync` |

### No MCP exposes any of it

`MEASURED` - searched for `SavePlaceAsync`, `save_place` and `save_*` across the
official repo and four community servers. **Zero hits everywhere**, and a control
query (`execute_luau`, known to exist) returned four hits, so the negatives are
real rather than a silent search failure.

| implementation | place-save? |
|---|---|
| `Roblox/studio-rust-mcp-server` (official) | No - 6 plugin tools: `GetConsoleOutput`, `GetStudioMode`, `InsertModel`, `RunCode`, `RunScriptInPlayMode`, `StartStopPlay` |
| Built-in Studio MCP | No - 34 documented tools, none save a place |
| `drgost1/robloxstudio-mcp` (claims 51 tools) | No |
| `boshyxd/robloxstudio-mcp` (490 stars) | No |
| `Chrrxs/robloxstudio-mcp` | No |
| `hope1026/weppy-roblox-mcp` | No |

A global `SavePlaceAsync` code search also showed **unofficial C# bindings** that
already wrap it (`RobloxCS.Types`, `LUSharp`, `roblox-modloader`). Other
ecosystems have surfaced this call; it never reached an MCP.

### Rejected: "saveinstance" scripts

The only mechanism found for writing a **local** `.rbxl` from a live session is a
serializer hook driven by executor-based tooling, which works by ignoring
Roblox's ownership and auth model. **Not a route**, and deliberately not named
here - a reader who needs the exclusion does not need the repository, and naming
it makes this file a pointer to it. Its one diagnostic value is that the local
mechanism is a hook rather than an API, which is why there is no sanctioned
version of it.

### Confirmed only by a search snippet - verify before relying on it

`AssetService:CreateAssetAsync` - *"can only be used in locally loaded plugins
and uploads assets without prompting."* From a search snippet; the fetched page
rendered the signature and a code sample but no prose description, so the
constraint is probably on the page and I did not read it. The confirmed parts
are the signature, the `AssetCreateUpdate` capability, and the
`requestParameters` shape (`CreatorId`, `CreatorType`, `Name`, `Description`;
returns `Enum.CreateAssetResult` plus the new id or an upload error).

### If this is ever needed in CI

Cloud save needs an authenticated Studio with a capability-enabled place, which
is the same OAuth2 wall as the retired live-Studio work - so it is not
CI-reachable. `rbx-dom`, `lune`'s `@lune/roblox` and Rojo write a `.rbxl` with no
Studio and no auth, and remain the only route to disk. Their trap: they operate
on the file, so if Studio holds unsaved changes, the file is stale.

## The tool list is budgeted: three drifts, one gate

`AGENTS.md` states the rule and quotes **no figure**. This section is where the
numbers live, and it is where the history lives. The gate that keeps them honest
is `python/tests/test_docs_freshness.py`.

### The asymmetry that made this possible

Every enforcement threshold in this repo is **derived from the generated
contract**. `test_parity.py` reads `total_description_cap` and
`per_tool_description_cap` out of `parity/tools.json` rather than hardcoding
them, so raising the cap cannot desynchronise the gate. That is deliberate, and
`test_docs_freshness.py` now pins it - a hardcoded cap in `test_parity.py` would
move the failure from the documentation into the gate, where it would be silent.

**The enforcement was never the thing that drifted. The prose beside it was.**
Every failure below is a hand-maintained number disagreeing with a generated one,
and not one of them turned a gate red.

### The three drifts

| # | where | what it said | what was true |
|---|---|---|---|
| 1 | `AGENTS.md` | a total cap of **2,700** | the cap had been raised; the line survived the raise |
| 2 | `TODO.md`, one bullet | **7 characters** of headroom | not the live figure |
| 3 | `TODO.md`, the *adjacent* bullet | **8 characters** of headroom, "both sides" | disagreed with #2 **and** with the contract |

Drifts 2 and 3 are the pair that matters. They are adjacent bullets in the same
section, so the file was not merely stale - it was **self-contradictory**, and a
reader had no way to tell which was current except to go and read the generated
contract themselves, which is the one thing the prose existed to save them from.

The live figures at the time of writing, from `parity/tools.json`:
`total_description_chars` **2,897** against `total_description_cap` **3,200**,
with `per_tool_description_cap` 450 across **16** tools - about **303**
characters spare, roughly two tools' worth. So a contributor reading drift #2
would have concluded the surface was welded shut, and declined to propose a tool
that fitted comfortably.

Note the pattern, because it has now happened to the **tool count** as well, in
two other places. A generated artefact with a hand-written number next to it is
a standing invitation for the second to rot. The fix is not vigilance.

### The gate, and why it encodes no number

The obvious check - assert the prose quotes the live figure - has two defects. It
encodes the number, so it would rot the same way on the next cap change. And it
contradicts the rule the section states, which is that no figure belongs there at
all.

So the gate asserts **the rule, not the value**: no cap-like figure may appear in
the normative section. It contains no threshold of its own, so it cannot drift.
It catches a four-or-five-digit figure (`2,700`, `3200`) *and* a small integer
next to a unit word (`8 characters`, `11 spare`), because drift #2 was a
one-digit number and a four-digit-only detector passes straight over it.

That second pattern was not designed in advance. The negative control caught it:
with only the four-digit rule, poisoning the section with the real
"there are 7 spare" sentence left the detector silent and the control green. The
detector was wrong, not the test - and the control is what made that visible
rather than shipping a gate that watched nothing.

`TODO.md` is **deliberately exempt**. It is a dated evidence log; recording what
a number *was* is its job, and rewriting history to match the present would
destroy the record that makes the drift visible. Only the normative document is
held figure-free.

## The universe id is a function of the place id

### RETRACTED: "the real universe id fails, so never fetch it"

This file, `AGENTS.md`, a code comment and a commit message all carried the
claim that launches with a place's real universe id *"failed 3 times in 16 with
`Error fetching latest place version`"*, and therefore that the API's value was
the one thing not to pass. I repeated it to the user **twice**, unprompted, and
recommended against the change they had proposed.

It does not hold. Re-measured 2026-10-02:

| arm | value | launches | opened on attempt 1 | errors |
|---|---|---|---|---|
| control | `universeId:0` | 6 | **6** | 0 |
| treatment | `universeId:28220420` (what the API returns) | 8 | **8** | 0 |

**14 launches, 14 successes, zero `Error fetching latest place version`.** The
value I called the broken one is the value that worked every time.

### What that establishes, and what it does not

It does **not** prove the two values are equivalent - 14 consecutive successes
cannot establish that, and 0 remains the honest choice for a template place,
which genuinely has no universe context. It establishes that **the failure did
not recur**, and that the only counter-example is a 3-of-16 nobody could
reproduce. Plausibly transient network, a Studio version difference, or a
condition specific to that window.

**The claim is withdrawn; the measurement is not.** It is still the only recorded
failure, and it is still unexplained. What changed is that it no longer supports
a rule.

### Why I was wrong, in a form worth keeping

I asserted a mechanism from a nearby observation instead of re-running the
measurement, and then compounded it: when the API returned `28220420` and the
tests pinned `28220420` as the failing value, I read that as decisive *against*
the proposal rather than as the question to test. The match was real. The
inference from it was not, because the 3-of-16 had never been reproduced and I
treated a historical note as a current fact.

The user's own hypothesis was better than mine: that the failures were a fast
single call rather than a property of the value. Testing it cost fourteen
launches and answered the question in one run.

### The design that replaced it

`build_launch_uri(place_id, universe_id=None)`. **A place's universe is not an
independent parameter - it is a function of the place id.** So the default is
`None`, meaning *ask*, and `resolve_universe_id` fetches it from
`apis.roblox.com/universes/v1/places/{place_id}/universe`.

That is better than defaulting to `0`, for a reason the old comment had
backwards. It previously said 0 must not be a default because *"a defaulted one
is a default in disguise, and it would let a caller inherit the fetch failure by
omission."* But the deeper problem was structural: `universe_id` was a value the
caller had to **already know**, and what people supply by reflex is whatever
their last place's universe was. Deriving it removes the foot-gun rather than
managing it.

Three properties the tests pin, each with a negative control:

- **An explicit value never touches the network.** Otherwise a launch would
  silently depend on connectivity and report a network failure as a bad URI.
- **A response with no `universeId` raises; it never becomes `0`.** Coercing it
  would mean "no universe context" - the exact substitution this derivation
  exists to make impossible.
- **`retries` defaults to 3**, doubling the delay, because a single fast call is
  the failure mode worth designing against on a launch path.

Both implementations changed in one pass. Python's `build_launch_uri` and
`launch_via_uri` are now `async`; Node's take `null` for the same reason and
accept an injectable `fetchImpl`.

**Not verified:** the derivation was exercised against the real API once
(`95206881` → `28220420`, one call, first attempt) but the *launch* it produced
was not re-run, because that means opening Studios on someone's desktop. The 8
launches above used the resolved value; the end-to-end path - call the API, then
launch with what it returned - is not separately measured.

## Place save, revisited: an independent implementation (2026-10-03)

The user went back to the devforum thread behind *Add support for a place save
API* and found `rodeo-rbx/rodeo` referenced from it. MIT, public, 20 stars.

### What it is, and is not

**It is not the excluded category.** It installs a Roblox Studio **plugin**, and
for its `--context elevated` identity it uses **StudioMCP itself**. It drives the
legitimate editor through sanctioned mechanisms. Executor tooling works by
injecting into a running client and ignoring ownership and auth; this does
neither. So rule 6's exclusion does not reach it, and naming it here is fine.

Worth being careful about, because the name invites a snap judgement and I have
already made one snap judgement today that turned out to be wrong.

**DOCUMENTED — the local save is host-side keystroke emulation.**
`rodeo-cli/src/studio_backend/backend.rs:376`, verbatim:

    // fire Cmd+S + wait for mtime + reply with SaveResult

and `rodeo-cli/src/commands/save.rs` requires a `session_guid` — *"Studio was
not launched by rodeo (no session) — save it from Studio directly."*

**DOCUMENTED — there is no plugin-side save.** `rodeo-plugin/src/library/`
contains no `AssetService` or `StudioService` save call. So the confirmed
mechanism is the one rule 6 already named: emulate the keystroke.

### Corrected: the flakiness is in the verification, not the keystroke

`AGENTS.md` rule 6 said the emulation *"is flaky"*, which put the blame on the
mechanism. Comparing the two implementations, the keystroke is the easy part:

| | naive emulation | rodeo |
|---|---|---|
| fires the save | yes | yes |
| **confirms it happened** | **no** | **waits for the working file's mtime to move**, up to 60s |
| on failure | silent | hard error — *"never a silent exit 0"* |
| destination write | — | copies to a `.tmp` then renames, so a failed copy cannot truncate the target |

That is the whole difference, and it is a large one. What makes naive emulation
unreliable is that **nothing observes the result**, so a save that did not happen
is indistinguishable from one that did. This is the same failure class this file
keeps cataloguing, in a different costume: a call that reports success without
evidence — like a silently-ignored `format: "png"`, or a URI launch that attaches
and opens no place.

The mtime discipline is already native here. `build-freshness.test.ts` gates on
`dist/` being newer than `src/`, and `logid` compares log filename stamps against
process creation times. The pattern was present; it was just not applied to saves.

**The instruction does not change.** Still ask the user to save. This is
source-read evidence, not measured here, and an instruction should not move on
someone else's implementation — especially one whose whole selling point is that
it verifies. Building it would be new work on an unverified path.

**Untested by me:** I read the source; I did not run it. Its own tests are real
integration tests (`tests/cli/operations/save.test.ts` launches a Studio, mutates
a place, saves, then reopens the file and asserts the change survived), which is
good evidence, but it is the author's evidence and not mine.

### It independently corroborates a finding this project already had

`rodeo-plugin/src/library/studio.luau:92-93`:

    -- This is the stable studio identity — independent of the launch
    -- session_guid (only owned/launched studios have one) and of
    -- StudioMCP's flaky id.

An unrelated implementation calls StudioMCP's `studio_id` **flaky** and mints its
own per-process identity attribute to route around it. That is this project's own
conclusion — *"`studio_id` is a transport token rather than an identity"* — reached
from the opposite direction, by someone who had no reason to agree.

Worth recording for a reason beyond the fact itself: when a finding about
*Roblox's* surface is reproduced by a third party, it is a property of that
surface rather than of our reading of it. That is a stronger claim than either
project can make alone.

### Not changed by any of this

- **Cloud save is untouched.** `rodeo` is about local files; `SavePlaceAsync` and
  its capability, rate limit and place-id rules stand as recorded.
- **No dependency is being taken.** rodeo is a separate tool with its own plugin,
  its own port, and its own lifecycle. Nothing here is being adopted.
- **Its macOS support is evidence about rodeo**, not about this project, and does
  not move `README.md`'s "macOS supported but unproven" line. That claim is about
  *this* codebase's macOS branch, and one tool working on macOS says nothing
  about whether ours does.

## Field review, 2026-10-02: what is still open

A full session driving the tool against a 94-script place, three live Studios,
`execute_luau` in every datamodel. Verdict: **intuitive in its model, hostile in
its diagnostics** - the verbs are clear and the core primitive is good, but
failure feedback points at the wrong thing often enough that a one-line bug
produced the same opaque error three times and cost two wrong hypotheses first.

Re-checked against the code 2026-10-03. **Six of seven findings are still open.**
The standalone report was removed; this is the durable part.

| # | finding | status, checked 2026-10-03 |
|---|---|---|
| 1 | **P0** - script errors are rooted in Studio's plugin, so the submitted file's line is never reported. `CommandExecution:54` is the same for every call and identifies nothing the caller wrote | **open.** No line offset anywhere in `extensions.py` |
| 2 | **P1** - `get_console_output` is an unbounded rolling buffer; data is lost silently, with nothing indicating truncation | **partly addressed.** `extended_watch_output` added with `pattern` + `max_lines`, which covers the grep half. The silent-loss half is unchanged and **cannot be fixed** - it is a relayed tool, so the 100,015-char ceiling has no marker. Documented in `rsx-transport` instead |
| 3 | **P2** - `execute_luau` vs `execute_luau_from_file`: neither says the file is re-read every call. Measured: no caching. The reviewer spent two turns chasing a cache that does not exist | **open.** `extended_server.py:788` says nothing about caching |
| 4 | **P3** - `execute_luau` returns `Failed to parse command code` on syntactically valid Luau, Client datamodel only, then succeeds unchanged | **open, cause never isolated.** One session's worth of observation |
| 5 | **P3** - `studio_id` is re-minted every launch, there is no alias, and a successful launch still returns `mesh_name: null` | **true and unchanged**, but now documented in `README.md` traps and pinned by the standing rule to re-resolve each session |
| 6 | **P4** - nothing on the surface distinguishes a user's Studio from one an agent launched, so `Play` on someone's working Studio needs a guardrail written by hand | **open.** No `owned_by` or equivalent in either tree |
| 7 | **P4** - Roblox's own error wording passes through with nothing connecting it to consequences: which instance, which script, whether repeating | **open** |

### The symptom list, which is the durable part

Every one of these exists in a consumer's instructions **only** to work around
this surface. They are a cost ledger for the findings above, and they are the
part worth keeping now the report is gone:

| guardrail | works around |
|---|---|
| a byte-identical error across code variants is in your code, not the tool | §1 blame inversion |
| never print bulk data - a ~5 MB `print` wedges the channel until restart | §2 unbounded write |
| the console arrives as one line with literal `\n` escapes | §2 formatting |
| returns truncate at exactly 100,015 characters, silently | return cap, no marker |
| never cache a `studio_id` across sessions | §5 |

**Six rules in one consumer's instruction file, all downstream of this surface.**
Fixing §1 and §2 would let four of them go. That ratio - guardrails bought per
fix - is a better way to prioritise this surface than severity labels alone.

### Do not regress these

- **`extended_wait_for`** - the single most valuable thing in the surface. Being
  unable to sleep inside an agent's runtime is the hardest constraint it works
  under, and this hands back a declarative escape (`os.clock() > 8`,
  `#Players:GetPlayers() >= 1`) *plus* structured diagnostics (`satisfied`,
  `timed_out`, `polls`, `poll_errors`, `settled_repeats`), so a timeout says
  why rather than just failing.
- **Refusing to guess when ambiguous.** An omitted `studio_id` with several
  Studios attached errors out and **names the candidates**; the same holds for
  `list_roblox_studios resolve`. This is what keeps rule 1 cheap to obey.
- **`manage_instance action=stop` returns the pid and how it resolved the target.**
  Rare, and exactly the provenance you want before killing a process.
- **Returning an arbitrary value from arbitrary Luau**, with the caveat that raw
  tables lose array-ness. This is how engine behaviour gets measured instead of
  inferred.
- **The `skill` / `extended_skill` split** - engine skills behind a live Studio
  id, transport skills behind none, ordering dependency stated.

### Priority, if this surface is worked on

1. **§1** - report the script line. Cheapest change, largest effect on accuracy.
2. **§2** - a visible `dropped` count. Not available: relayed tool.
3. **§5** - accept a stable label wherever the id is taken.
4. **§6** - record launch provenance. Deletes a rule from every consumer's
   instructions, and it is the one with a directly observed cost: an agent that
   launched its own Studios could not tell them from the user's.
5. **§3, §4** - two description and error-detail lines.
6. **§7** - annotate Roblox errors with instance path and repeat count.

**Not done, and the reason:** §2's truncation half is a property of a tool this
project relays and cannot extend. The rest are unbuilt because they are a
surface change nobody has asked for, not because they are hard.

## Why no gate can check a skill's claims (2026-10-03)

Sixteen documentation defects were fixed on 2026-10-03, all one shape: a skill
file asserting something the implementation contradicts. The obvious guard is a
gate that greps skill bodies for figures absent from source. **Measured against
the six defects fixed that day, it catches zero of them.**

| defect | would a figures-vs-source gate catch it? |
|---|---|
| `rsx-targeting.md` "47 of 47" logs | **no** - `47` is present in source |
| `logid.py` docstring "44 of 44" | **no** - `44` is present in source |
| `rsx-transport.md` "slices of 120,000" | **no** - `120,000` is present in source |
| `rsx-capture.md` "identical pixels, either is safe" | **not catchable** - attribution, not a number |
| `rsx-discovery.md` "parse error, not a nil call" | **not catchable** - prose about engine behaviour |
| `rsx-breakpoints.md` "`log_expression` must fail" | **not catchable** - a heading, not a figure |

The three figure-level failures share one property, and it is the interesting
part: **the skill and the code agreed with each other and were both wrong.**
`logid.py`'s docstring carried the same bad count as the skill, which is exactly
why the skill looked authoritative. A cross-file consistency check does not just
fail to catch this class - it *rewards* it, since two files saying the same
thing is the property it is looking for.

The defect is a **shared false premise**, and no mechanical check can see a false
premise. Catching it needs something that reads the sentence and the code and
judges whether one supports the other, which is what the subagent verification
pass did and what no test does.

**The two checks that do work, and why neither was adopted.** Both pass clean and
both fire on deliberately broken input, so they are real rather than trivially
true:

1. Every `file:line` reference in a skill body must name a file that exists and
   a line within it. Negative control: `` `extended/nope.py:1` ``,
   `` `capture.py:999999` `` and `` `ok.py:2` `` are all caught - missing file,
   out-of-range line, and ambiguous basename respectively.
2. Every `` `rsx-*` `` a skill names must be a real sibling. Currently zero
   dangling.

Rejected anyway: there were **4 `file:line` refs across 7 files** when this was
written. Re-measured 2026-10-05 there are **2, both in `rsx-capture.md`** — a
test file guarding two references is overhead that reads as safety, which is the
same failure as a gate whose negative control is trivially true. Revisit if the
count grows.

**The blind spot that is real.** Both suites assert only *structure* over skills -
name prefix, frontmatter parses, index smaller than bodies. `test_skills.py` loads
the full text of `rsx-capture.md` and never inspects a word of it. So the gate
surface over skill *content* is empty, deliberately: see above for why it cannot
be filled mechanically.

## The strip's justification, re-derived by running it (2026-10-04)

The repo went Python-only on 2026-10-03 (`e91cdb3`). Its justification: **Node's
remaining gaps were silent-wrong-answer bugs.** That rested on `bd77d97`'s commit
message and a dated narrative in this file - never re-derived. Recorded because the
answer differs from the claim in a way worth keeping.

**How.** A `git worktree` at `bd77d97` on branch `parity`, node v26.4.0, `npm install`,
`npm run build`, `npm run typecheck` (clean), `npm test` (**688 passed, 1 skipped** -
matching `bd77d97` exactly), then `parity/compare_implementations.py`, which exists only
on that branch and is pure static analysis. Worktree removed; `main` never on it.

**What `bd77d97` named, and what each is when executed:**

| named gap | measured | class |
|---|---|---|
| `placeFromCommandLine` truncates at the first space | `dist/extended/platform.js:379`, given `-localPlaceFile "C:\Users\My User\place.rbxl"` returns **`"My"`**; `"C:\Program Files\..."` returns **`"Program"`**. No throw, exit 0 | **silent wrong answer** |
| Node "computes `readOnlyHint` and ships 0 of 7" | 16 tools, **7 carry `readOnly === true`** (`extendedServer.js:858,868`); a wire entry serialises `name, description, inputSchema` only, so **0 of 7 ship** | **safe omission** - an unmarked set reads as "may mutate", which is safe, per Python's own docstring |
| `instance.ts` never imports `logid.ts` | both `logid` mentions in `instance.js` are comments (lines 6, 12); the import at line 28 takes `processRows` from `platform.js` | **honest refusal** |

**1 of 3 is a silent wrong answer.** The justification is overstated; the decision is
not. `compare_implementations.py` measures python `extended/` at 7,642 lines against
node's 6,859, and python tests at 10,471 lines in 38 files against node's 7,309 in 19 -
**5,708 python-only test lines** across 24 subjects node never covered (`typecheck`,
`readonly_hints`, `platform_macos`, `logid_formats`, `relay_guards`, `parent_edge`, and
18 more). Its one-sided-risk section names `split()` semantics as **already having
fired**: python's `split(None, n)` keeps the remainder, JS truncates, and *"every row
came back null"* in the macOS process parser. Same family as the `"My"` above -
whitespace handled differently across two languages, yielding a plausible wrong value.

**The honest statement:** *python was further ahead, and node was exposed to a class of
cross-language bug that had already produced a real defect.* Not *"every remaining gap
was a silent wrong answer."*

**Three claims in the sources above are wrong, recorded so nobody inherits them.**
(1) An earlier reading of this said `readOnlyHint` appears **zero** times in `node/`,
therefore absent - wrong needle, the code says `READ_ONLY_TOOLS` and `tool.readOnly`.
(2) `TODO.md`'s retracted item says `stopProcess(pid)` "has no PID to be given";
`processRows()` returns `pid` straight from `Get-CimInstance Win32_Process`, so the
`studio_id`-to-pid *mapping* is missing, not a pid. (3) The harness's section 5 reports
*"node/dist does not exist - the Node server has never been built here"* - true of that
worktree, false as history.

**Settles against:** re-running the harness on `parity` and finding a fourth gap that
*is* a silent wrong answer, which would make the justification understated instead.

---

## External review of 2026-10-07, verified against source: 8 defects, 5 wrong claims

An outside reviewer produced three write-ups and a 13-question transcript about
this repository. The claims were **not taken on trust**: each was checked against
the source by four independent readers working from disjoint claim sets, and the
disagreements were then resolved against the file. Measured 2026-10-07 by that
verification pass; the code had not changed since `1680942`.

**What the review is worth: roughly half of it.** Five of its specific factual
claims are wrong, two of them flattering to the reviewer and three unflattering
to this repository. That ratio is the reason the verdicts are recorded per claim
rather than the review being adopted or dismissed as a whole.

### Provenance, so a reader can tell whether they are reading the same thing

The review is **not in this repository** and lives in an ephemeral location. The
hashes below pin what was read; if a reader's copy differs, every verdict here
needs redoing.

| file | sha256 (first 16) | bytes | authored |
|---|---|---|---|
| `roblox-mcp-improvements.md` | `fcf2f70e9380e08e` | 2,926 | 2026-10-07 ~14:08Z (zip entry) |
| `roblox-mcp-final-stretch.md` | `99f17c358a4d40b7` | 1,952 | 2026-10-07 ~14:07Z (zip entry) |
| `IMPROVEMENT.md` | `de529f232e75decb` | 10,092 | 2026-10-07 15:08:26Z |
| `full.txt` (transcript) | `4d7c40c3ca8f0132` | 42,308 | see note |
| `…Questions_and_Answers.docx` | `1bed1b1955a827c5` | 19,443 | 2026-10-07 20:10:45Z (internal `docProps`) |
| `mcprevs.zip` | `772dfce147c4406f` | 7,794 | - |

**`full.txt` is not the full transcript.** Its filesystem mtime (23:11Z) is a
*copy* time, not an authoring time, so the `.docx` internal `created` is the only
authoring anchor. And the two disagree on content: both carry **13** questions,
but they are not the same 13. `full.txt` is missing the *"What about for 10?"*
exchange, which the `.docx` contains; `full.txt` instead ends with *"Now give a
download for it"*, which the `.docx` does not. Overlap is 12. A file named
`full.txt` that is missing an exchange is the same defect as a figure named
`total` that is a subset.

*Settles against:* a copy of either document containing the other's extra
exchange. That would mean one was edited after authoring and these hashes
describe an intermediate state.

### Confirmed — real defects, all reproduced at the line cited

| # | defect | evidence |
|---|---|---|
| 1 | **`stop` cannot work on macOS.** `terminate_process` hardcodes `powershell` with **zero** platform branching, so it raises `FileNotFoundError` before killing anything. The platform-aware implementation exists — `platform.terminate` (`platform.py:336-345`) has the `is_windows()` branch and the `kill -9` fallback — and **nothing in `src` calls it** | `instance.py:1262-1272`; only `.terminate(` in src is `client.py:207` `proc.terminate()` on the MCP subprocess, unrelated |
| 2 | **A launch can be attributed to the wrong Studio, and that id then kills a process.** `_identify_launched` route 1 filters only on `studio_id not in before_ids`; the `pid` it was handed is first used at `:856`, *after* route 1 has returned. One concurrent Studio inside the 75s window is credited instead. `launched: True` is returned, and `action=stop` terminates that `studio_id` | `instance.py:823-832`; `extended_server.py:1195→1203`; window `LAUNCH_WAIT=75.0` `:230` |
| 3 | **The event loop is blocked for up to 75s.** `list_studio_processes` is plain `def` (`:127`) called bare from coroutines at `:630`, `:675`, `:1024`; plus `time.sleep(2.0)` at `:727` and a synchronous `urlopen(timeout=10)` at `:448` | **zero** `asyncio.to_thread` in `instance.py` |
| 4 | **`to_thread` is known and not applied where it matters.** `stop` uses it (`extended_server.py:1203`); the launch path (`:1187`) and `resolve_pid_for_studio` (`:1195`) do not | as above |
| 5 | **The `allow_console_write` docstring contradicts its own body.** It states the flag *"declines to touch Studios the caller never named"* (`:885-889`), and eleven lines later the same function loops `for row in ranked` (`:911-916`) — `ranked` is every row with a `studio_id` — writing a join token into each until one answers | both in `_identify_launched` (783-933) |
| 6 | **`stop` has no confirmation and no revalidation.** Schema is 3 properties, no `dry_run`/`confirm`; resolve at `:1195` then kill at `:1203` with nothing between. `_pid_alive` is called only *after* the kill (`:1276`, `:1279`) | `extended_server.py:668-687`, `:1195-1203` |
| 7 | **Early exit is misreported.** `proc.poll()` returns "Studio exited during launch" (`:657-667`) *before* the new-PID scan (`:673`), so a handing-off child is never looked for | ordering only; unverified whether Roblox hands off |
| 8 | **Temp place copies are never deleted, repo-wide.** `mkstemp` + `copyfile` at `:576-579`; no `os.remove`/`unlink`/`rmtree`/`TemporaryDirectory`/`atexit` anywhere references the `"roblox-studio-"` prefix | disclosed at `:565-569` as a known limitation, not a hidden defect |

### Refuted — the review is wrong, and the errors run both ways

| claim | measured | how it was wrong |
|---|---|---|
| "`instance.py` alone **over 1,300** lines" | **1,290** | ten *under* |
| "**40-plus** functions" | **29** (24 module-level, 4 nested, 1 class) | overstated ~40% |
| "About **23,000** lines of Python" | `python/src` = **11,003**; `python/tests` = 10,879 | conflates tests with shipped code; overstates production ~2× |
| "`stop` … **silently grants console writes to any attached Studio**" | `_resolve_by_console_token:1206` passes `studio_id=studio_id` — **one** named Studio | true for the hardcoded `True`, wrong about the blast radius |
| "kills with `kill -9` on macOS" | that function has **no** macOS branch at all | describes `platform.terminate`, which `src` never calls |
| "**Zero stars**" | 1 (`mobogreatthegreat`) | **true when written** — published 07-06, review 07-07 — so this is a claim that aged, not one that was false |

Two figures were **exact** and are recorded because exact agreement is what makes
the errors above credible rather than merely annoying: **756** test functions, and
**16** contract tools matching 16 registered `extended_*` names.

### Two defects found here that the review did not report

1. **`platform.terminate`'s docstring is false.** It claims *"the only place in
   the project that kills"*, while `instance.terminate_process` duplicates the
   logic without the platform branch. So the correct implementation is dead code
   wearing a claim of exclusivity, and the wrong one is live.
2. **`python/pyproject.toml:30` still says "same 720 passed"** — a benchmark
   comment now contradicting the 744 in `docs/HISTORY.md`, and *lower* than the
   756 test functions. Related and also stale: `requires-python = ">=3.9"` is
   declared but `ci.yml` pins **3.12**, so 3.9 is declared and never exercised.

### Correction, same day: this record's author verified one claim against the wrong function

While checking the review, the claim *"the `allow_console_write` docstring
contradicts the code"* was reported as **refuted** — on the grounds that
`_resolve_by_console_token` passes an explicit `studio_id`. That was **wrong**.
The docstring making the claim lives in `_identify_launched` (783-933); the
function inspected was its helper `_resolve_by_console_token` (1180-1255). Both
facts are true; the refutation was of a claim about the wrong function. The
claim is **confirmed** — defect 5 above.

This is recorded rather than fixed silently because the error and its cause are
the useful part: two readers disagreed, and the disagreement was only resolvable
by asking *which function owns the line*, not by re-reading either answer.

### Not verified, and not claimed

- **The competitor-repository findings** (`drgost1` binding `0.0.0.0` with an
  unauthenticated `/api/execute-luau`; `EL4CTEO`'s Host/Origin defences) describe
  **other people's repositories**. The reviewer states plainly that no exploit was
  tested. Nothing here confirms or repeats them. Two of the eight repos were never
  cloned. **These are unverified claims about third-party code and are excluded
  from this record's findings entirely** — not counted as either true or false.
- **The review's Tier-1 proposals** (a POSIX branch, pinning the upstream Studio
  tool surface) are *proposals*, not defect claims, so "confirmed" does not apply.
  One incidental fact was checked: `ci.yml` `matrix.os` is
  `[windows-latest, macos-latest]` — **no Linux job**, as claimed.
- **Whether Roblox's launcher hands off to a child process** (defect 7's
  precondition) was not tested. The code ordering is confirmed; the consequence
  is conditional on a premise this record does not have.

*Settles against:* a run on macOS where `stop` returns `stopped: true`. That
would make defect 1 a harness artefact rather than a real failure.

## The skills were not in the wheel at all (2026-10-08)

`extended_skill` loaded **zero** skills from an installed wheel. Reproduced
before anything was changed: build the wheel, install it into a clean venv,
import and ask.

```
find_skills_dir() -> None
skills loaded: 0
skill names: []
```

The wheel was not broken in an interesting way. It was **correct for what it was
told to package**: `[tool.setuptools.packages.find] where = ["src"]` picks up
`roblox_studio_mcp` and its subpackages, and `[tool.setuptools.package-data]`
named one entry, `py.typed`. The skills lived at the repository root, *outside*
the package, so nothing in the configuration could reach them. The built wheel
had **29 entries, 25 of them under `roblox_studio_mcp/`, and not one markdown
file**.

### Why it was silent, which is the part that matters

`find_skills_dir()` walked up from its own source file looking for a folder
named `skills`. In a checkout that works. In site-packages it walks up out of
the install and finds nothing, so it returned `None` - and `load_skills()`
turned that into `[]`:

```python
root = directory or find_skills_dir()
if not root or not os.path.isdir(root):
    return []          # the shipped behaviour
```

So the tool answered a successful `tools/call` with an empty catalogue. The
docstring directly above it claimed the opposite: *"Walking up rather than using
a fixed relative path is what lets one copy of the skills ship with the package,
and keeps working whether it is imported from the source tree or from
site-packages."* Both halves are false. A walk-up cannot ship anything; only
`package-data` put the files in the wheel, and it was not configured to.

**This is the project's own failure class, not a new one.** An empty result that
reads as a working answer is what `AGENTS.md` exists to prevent, and the suite
had no gate over it because every test ran against the source tree, where the
walk-up always succeeds. A checkout cannot see an install bug. That is the
general lesson: the tests were not wrong, they were **run in the only
environment where the bug is invisible**.

### The fix, and why that layout

The skills moved to `python/src/roblox_studio_mcp/skills/` - inside the package -
with `package-data` extended to `["py.typed", "skills/*.md"]`. One copy, one
place, and it is the place that ships. `find_skills_dir()` now checks the
package's own folder first and keeps the upward walk only as a source-tree
fallback.

**Rejected alternatives, and why:**

| alternative | why not |
|---|---|
| keep `skills/` at the root, add `MANIFEST.in` | `MANIFEST.in` drives the **sdist**, not the wheel. The data would still be absent from the wheel. |
| keep it at the root, use `data-files` | installs to `sys.prefix`, not beside the module. The loader would then need the interpreter's prefix, and a venv and a system install disagree. |
| symlink `python/src/roblox_studio_mcp/skills` -> `../../../skills` | one copy on disk, but setuptools' `package_data` glob does not reliably follow a symlinked directory, and a symlink is the first thing to break on a Windows checkout. |
| copy `skills/` into the package at build time | two copies, and the sync is exactly the failure this project keeps paying for. |

The Reader's note about "no second copy to keep in sync" **survives** the move,
but only because the location moved with it: one copy, inside the package, and
the wheel is built from the package. What does not survive is the claim that a
walk-up could ship it.

### Verified, not asserted

Built with `python -m build --wheel` after the move: **37 entries, 8 of them
markdown**, all under `roblox_studio_mcp/skills/`. Installed into a fresh venv
and imported from outside the repository:

```
find_skills_dir() -> .../site-packages/roblox_studio_mcp/skills
skills loaded: 7
skill names: ['rsx-breakpoints', 'rsx-capture', 'rsx-console',
              'rsx-discovery', 'rsx-playtest', 'rsx-targeting',
              'rsx-transport']
```

Then the failure mode was re-armed to check the second half of the fix: deleting
`skills/` from the installed package and dispatching `extended_skill` through
the real handler. It answers a JSON-RPC **error** naming both locations it
looked in, where the old code answered `skill names: []` with `isError: false`.

The gates now cover both halves from inside the suite: `test_skills.py` builds a
real wheel from a copy of the tree and reads its listing, and imports the
package alone from outside the repository with `PYTHONPATH` stripped and the CWD
elsewhere - the two environments a checkout cannot fake.

*Settles against:* a wheel built from this tree whose listing lacks
`roblox_studio_mcp/skills/`, or an install from that wheel for which
`load_skills()` returns something other than those 7 skills.

---

## External security audit (2026-10-08): 3 shippable findings, 10 lesser ones

An external auditor read all of `python/src` plus tests, contract, `pyproject.toml`
and CI, ran the suite (793 passed), and confirmed five findings with throwaway
PoCs under `/tmp/opencode` — no repo files touched, no Studio contacted. Method
and full text: the auditor's report is the record; what follows is the verdict
table with independent re-verification where it was re-done here.

**Independently re-verified here before recording:** A1 (zero `_confined` refs
in `capture.py`; `extended_capture` in `_READONLY_TOOLS`) and A3 (only a
`startswith("game.")` check, then `local parent = {container}` spliced raw).
Both hold as stated.

| # | severity | finding | status |
|---|---|---|---|
| A1 | HIGH | `extended_capture` writes any host file via unconfined `save_path`, while marked `readOnlyHint: true` | closed (`9e3f7a0`) |
| A2 | HIGH | `allow_outside` turns confinement into a disclosure primitive (`~/.ssh/id_rsa` → `ModuleScript.Source` → model context in two calls) | closed (merged to `main`) |
| A3 | MEDIUM | game-tree paths splice raw into Luau (`local parent = {container}`); place content becomes executed code via search→write | closed (`82acaaf`) |
| A4 | MEDIUM | `stop`: no confirm/dry-run, force-only kill, console-token fallback touches unnamed Studios, `studio_id` is a transport token | closed (see `TODO.md`, 2026-10-09) |
| A5 | MEDIUM | kill target derived from user-writable log files; revalidation re-reads the same spoofable file | closed (see `TODO.md`, 2026-10-09) |
| A6 | LOW | `disabled_tools` honoured by `server.py`, silently ignored by `extended_server.py` | closed (`client.py:304`) |
| A7 | LOW | unbounded reads (`_readline_unbounded`, full-file `read_identity` fallback) | closed (64 MiB cap `client.py:39`, 4 MiB `read_identity` with `read_truncated`) |
| A8 | LOW | progress-token deadline extensions unbounded; relayed descriptions unmarked by origin | closed (600 s absolute `client.py:52`) |
| A9 | LOW | `extended_list_studios` spawns a second proxy per call | closed (reuses the caller's `studio` client) |
| A10 | LOW | `wait_seconds` the one unbounded numeric (`1e9` self-terminates; weak DoS) | closed (clamped to 90 s) |
| A11 | LOW | `_reject_unknown_arguments` exemption covers all relayed tools, not just `screen_capture` as `AGENTS.md` states | closed (AGENTS.md now states the true scope) |
| A12 | — | supply chain clean: stdlib-only true, no secrets/tokens, no install scripts; `apis.roblox.com` fetch validated, agent-unreachable | nothing to do |
| A13 | — | no network surface of its own: no socket/bind/listen anywhere in `src` | nothing to do |

**Deliberately not re-verified here:** B1 (mesh-name attacker control), B2 (macOS log parse), B3 (planted-log-to-kill end to end), B4 (`python/mac_live/`, uncommitted at audit time). Each names its own settling experiment in the report.

**Credit the audit gives that checks out:** `resolve_studio_id` refuses to guess
with candidates (`roblox.py:320-339`); unknown arguments refused before dispatch;
extended names take dispatch priority so upstream cannot shadow them
(`extended_server.py:1684`); the relay guard is awaited and wired (`:1737`); PID
revalidation between resolve and kill is real.

*Settles against:* a `save_path` outside the working directory being refused;
`extended_capture` absent from `_READONLY_TOOLS`; a `target_path` containing a
newline being refused rather than executed.

## The Python floor was never exercised, and the wheel tests skip silently (2026-10-09)

`requires-python = ">=3.9"` was a claim about four interpreter versions that no
job and no local run had exercised. CI ran `os: [windows-latest, macos-latest]`
with no `python-version` matrix, so it tested whatever the runner image shipped
and nothing else.

Measured here, all four present on this box, suite run with
`-o addopts=""` (the venvs lacked `pytest-xdist`, which `addopts = "-n 4"`
needs):

| python | setuptools importable | result |
|---|---|---|
| 3.9.25 | yes | 859 passed, 7 skipped, 233 subtests |
| 3.10.22 | yes | 859 passed, 7 skipped, 233 subtests |
| 3.11.17 | yes | 859 passed, 7 skipped, 233 subtests |
| 3.12.15 | **no** (fresh venv) | 849 passed, **17 skipped** |
| 3.12.15 | yes | 859 passed, 7 skipped, 233 subtests |

**The 10-test gap is a silent skip, not a failure, and that is the finding.**
`test_packaging.py`'s wheel tests are guarded by a `setUpClass` raising
`unittest.SkipTest` when `importlib.util.find_spec("setuptools")` is `None`, and a
class-level skip reports as skipped. Python stopped bundling setuptools into new
venvs at **3.12** - 3.9 to 3.11 still ship it - so every wheel assertion
(licence files, wheel contents, console-script entry point) is *absent* on a
bare 3.12 run while the suite still exits 0.

`setuptools` now appears in `[dev]` explicitly, which is what CI installs.
Without that the new 3.12 job would have been green while checking nothing about
the artifact PyPI actually serves.

The remaining seven skips are environmental and are not gaps: five `pyright is
not installed`, one `needs live Studio`, one `only 2 sections; no index needed`.

## The socket query was the whole cost, and it is now a syscall (2026-10-09)

`attached_pids()` asked Windows which Studio holds the mesh connection on 13469
by running `Get-NetTCPConnection` through PowerShell. Measured here on Windows
Python 3.12, same machine, same moment, both paths returning the same seven pids:

| method | per call | rows read |
|---|---|---|
| `iphlpapi!GetExtendedTcpTable` via ctypes | **31 ms** (IPv4), **89 ms** (v4+v6) | 4,469 |
| `Get-NetTCPConnection` through PowerShell | **27.0 s** | same table |

About **300x**. The query is not the difference - both reach the same kernel
table. The cost is that PowerShell materialises every row as a .NET
`NetTCPConnection` object and then pushes each one through a `Where-Object`
scriptblock, re-parsed per row: 4,469 allocations plus 4,469 scriptblock
invocations to answer a question about one port. The table has been measured at
14,948 rows in the same session, so the absolute figure moves with the machine.

**A first measurement said 14.5 ms and did not hold.** Re-measured under the same
code it was 31 ms for IPv4 and 89 ms with both families. The 14.5 ms figure came
from a probe that read one address family only, and it was quoted in three
docstrings and two test files before that was checked. Corrected everywhere; the
range is what the code now claims, not a constant.

**The trap this writes for, recorded because it nearly shipped.** A ctypes call
whose `argtypes` are undeclared *guesses* the pointer parameter, and the guess
succeeds: the call returns 0 and the buffer reads back as a plausible **one-row**
table with a plausible pid. No exception, no non-zero return. That happened on the
first version of this function and only surfaced because it was diffed against the
PowerShell answer. The struct layouts are also not order-portable - `MIB_TCP6ROW`
puts state and ports *after* the two 16-byte addresses where `MIB_TCPROW` puts
them first - and ports come back in network byte order, so the comparison is
against `socket.htons(13469)`.

**`NativeAndShellAgree`** in `python/tests/test_platform_macos.py` is the parity
test, gated behind `ROBLOX_STUDIO_MCP_SOCKET_PARITY=1` because the slow side
costs 27 s. It is the only thing that would catch a silent struct regression, so
it is written to compare the two rather than to assert a value.

Two smaller wins on the same path, both structural rather than measured: the
witness now reads the AutoSaves lock (a directory listing, no subprocess) before
the socket table, because either witness alone confirms; and `attached_pids`
takes a `studio_pids` argument so a caller asking one membership question about
an already-known Studio pid skips the `Get-CimInstance` enumeration (~1 s) that
the intersection used to pay for. The lock-first ordering is pinned by a test
that fails if the order is swapped back.

*Settles against:* `attached_pids()` still running `Get-NetTCPConnection` through
PowerShell on any path that does not need a filtered list; a struct layout change
that does not trip the parity test.
