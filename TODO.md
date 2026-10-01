# TODO - port + lossless capture

> ## STANDING CONSTRAINT - do not touch
>
> **Do not touch "Game TESTING" in Studio.** Stated by the user. Nothing in this
> project may read, write, execute, launch, stop or otherwise interact with it.
>
> This is recorded rather than remembered because "Game TESTING" appears nowhere
> in this repository - not in the attached Studios, which are both mesh-named
> `Place1`, and not in any config. So nothing here can warn me at the point of
> contact; the only thing standing between an agent and that content is this
> note. Treat any Studio, place, script or project by that name as off limits,
> and if a tool call would reach it, stop and ask rather than proceeding.
>
> **AMENDED 2026-09-30 — the name has changed, so this note no longer matches
> the world.** The Studio that reported "Game TESTING" (`fa092c48`) is **gone**;
> it survives only as a stale null-name registry row. A different Studio is
> attached in its place, reporting **`rbx-re.rbxl`** (`studio_id
> 1631ed70-bb8e-43a7-8b24-fc224da7f9a9`, debug id `0_186523`).
>
> **`AGENTS.md` rule 1 carries the same stale text** (it was `CLAUDE.md` until
> the rename, and the rule was copied forward unchanged). Both files must be
> updated together whenever this ruling changes — a rule that exists in two
> places and is corrected in one is a rule that lies in the other.
>
> **Whether that is the same place is UNVERIFIED, and cannot be settled from
> here** — every way of checking is a read of that Studio, which this note
> forbids. A name-based check would now pass it, which is precisely the failure
> mode this note exists to prevent. **RULING 2026-09-30 (user): `rbx-re.rbxl`
> (`1631ed70-bb8e-43a7-8b24-fc224da7f9a9`) is read-only.** No writes, no
> executes, no console writes, no stop, no capture targeting it. Read-only
> tools (list, identity, grep, watch) may observe it; nothing may mutate it
> or run code in it. Verification work uses the baseplate Studio with an
> explicit `studio_id`, never an omitted one.
>
> Weak evidence that it is *not* the same: its console carries
> `CoreGui.__ClampProbe.__L` work and four unrelated faults, which does not
> resemble the same content. That is inference from a console listing, and it is
> **not sufficient to lift the constraint**. Read it only if told to.
>
> Related: prefer `extended_wait_for` and read-only tools over anything that
> mutates a running session, so that verification does not require touching a
> live place at all.

Last updated 2026-09-30. Each item records the evidence that settled it, and
whether the evidence was measured live or read from docs/another implementation.
Those are not the same and the difference matters.

## macOS: researched and implemented, still never executed

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

The routing is now written down where it will be read: `skills/README.md`, a
routing table in `AGENTS.md`, and a "see also" in each skill with a real
boundary (`rsx-breakpoints`, `rsx-capture`, `rsx-discovery`, `rsx-playtest`).

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
- [x] **`extended_skill`** - Python + Node. Serves `skills/*.md` from the repo
      root. Seven skills, `rsx-*` prefixed so they cannot be confused with
      Roblox's `rbx-*`: `rsx-transport`, `rsx-console`, `rsx-targeting`,
      `rsx-breakpoints`, `rsx-playtest`, `rsx-capture`, `rsx-discovery`.
      Index 1,935 chars against 27,525 of bodies, so 7% is always-on and 93% is
      on demand. Verified over real stdio: 41 tools served, 13 extended, typo
      suggestions work.

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

## Corrections to earlier notes in this file

These were wrong when written and are now measured otherwise.

- [x] **CORRECTION: "GetDebugId is a usable stable key" was wrong.** It is
      **session-scoped and does not survive a restart.** Measured, same place
      file, same machine: `0_186696` before a restart, `0_186502` after. It is
      also per *instance*, not per process, since a child `Folder` returns a
      different value. It still usefully separates two Studios open on the same
      place, which nothing else in-band does. Pinned by a test.
- [x] **`AddPlayers` WORKS** - measured on a Studio launched `-task StartServer`:
      `AddPlayers(1)` took `#Players:GetPlayers()` from 1 to 2, giving
      `Player1/u-1` and `Player2/u-2`. No test-service machinery needed.
- [x] **CORRECTION: the `roblox-studio:` URI launch DOES work.** I had recorded
      it as broken, which was wrong. It needs exactly **four** keys and **both**
      ids: `roblox-studio:1+task:EditPlace+placeId:<id>+universeId:<id>`.
      - The eleven-key prefix copied from a real Studio URI is what failed, with
        an `Error` dialog that never attached. The key count was the problem, not
        the route. `launchtime`, `avatar`, `browsertrackerid`, locales,
        `channel`, `browser`, `distributorType`, `baseUrl` and `launchmode` are
        all unnecessary.
      - `universeId` is **required**. The key must be present, and the value
        should be **0** — see the next item for the value, which is the part that
        was measured rather than assumed.
      - Launch with `os.startfile`; `subprocess.Popen` raises
        `FileNotFoundError` because a protocol URI is not an executable.
      - A URI launch has no `-task` flag, so role parsing now reads the task out
        of the URI instead of reporting every URI-launched Studio as `unknown`.
      - Live: the four-key URI opened `Place1` with `parts=3`.
- [x] **`universeId` should be 0, not the place's real universe id.** The URI was
      `...+universeId:28220420` on the belief that both ids had to be real.
      Two things overturned that:
      - File > New in a running Studio opens a place as
        `-task EditPlace -universeId 0 -placeId <id>` — no universe at all, and
        it works. That is the engine's own form for "this place, no universe
        context".
      - Launches with the **real** universe id failed 3 times in 16 with
        `Error fetching latest place version`. With `universeId:0` the same place
        opened. So the real id appears to have been the cause of the intermittent
        fetch failures, and it was never required.
      - `build_launch_uri(place_id, universe_id)` takes **both as required
        arguments**. 0 is not a default: a defaulted one is a default in
        disguise, and it would let a caller inherit the fetch failure by
        omission. `URI_UNIVERSE_ID = 0` is the named value to pass.
      - `list_place_candidates` now reports each template's `place_id`, parsed
        from `Template_<id>_AutoRecovery_*.rbxl`. Measured: 16 candidates, the
        newest `Template_95206881_AutoRecovery_4_20260930_135756.rbxl`. So the
        template's own place id plus 0 is a complete launch, and nobody hardcodes
        an id.
- [ ] **The minimality of the URI is only half measured.** Four keys works. The
      key count and the value are settled, but whether `placeId` **alone** is
      enough was never established — the six-variant sweep
      (`python/verify_uri_minimal.py`, written and not completed) would answer it,
      along with whether `universeId` can be dropped. The key is currently kept
      on the **user's authority**: they have worked with Studio launch arguments
      at length and confirmed `universeId` is required. That supersedes the
      earlier note here, which credited a local attempt that left `name: null` —
      retired as evidence, because a key that can be dropped fails in a way a
      process count cannot see.
- [x] **The file route must NOT get `-universeId`.** Tried, to make the two
      routes consistent, and it breaks the place open: the process starts and
      signs in, then fails with `Cannot open place file for reading.: iostream
      stream error`, mesh name `null`, so nothing is addressable. The flag is for
      the place-id route only. Reverted, and pinned by the measurement rather than
      by taste.
- [x] **CORRECTION: `ExecuteMultiplayerTestAsync` is NOT required.** I asserted
      this in three places from a single failure, and it was wrong. A plain
      Play-mode **server** session works. What actually gates `AddPlayers` is
      whether the session can host more than one player:
      - `-task StartServer` (a Studio server) -> **works**, *confirmed*
      - `start_stop_play` (local single-player session) -> **refused**, and the
        engine falsely blames the Server DataModel while it is in fact available
      - `ExecuteMultiplayerTestAsync` -> documented yes, still **unverified**,
        because driving it blocks past the 120 s tool-call timeout
- [x] **CORRECTION: reflection method entries are tables, not strings.**
      `GetMethodsOfClass` returns `{Name, Display, Owner, Parameters, Permits}`.
      A filter doing `if type(m) == "string"` finds nothing and reads as "this
      API does not exist" - which is exactly how `AddPlayers` stayed hidden.
      `GetMethodsOfClass` also returns nil for `StudioService`.
- [x] **A place open can fail, and the log says why.** `[telemetryLog] State:
      OpenPlaceFailure` with `ErrorType: DataModelLoadingFailure` and an
      `ErrorMessage`. Measured over 47 logs, 25 of them Edit launches:

      | outcome | n |
      |---|---|
      | opened | 20 |
      | failed: "Error fetching latest place version" | 2 |
      | never reached a terminal state, never signed in | 3 |

      The "fetching latest place version" failures were **all URI launches**. That
      is the cause of the signature this file had recorded as an unexplained
      "failed launch" - a Studio that attaches to the mesh but never gets a place
      name. It is a fetch of the published place failing, so retrying the
      transport cannot fix it and the launch must be retried instead.

      The 3 with no terminal state never reached
      `[FLog::LoginController] Login got Standalone DM ready` and spun on repeated
      `status:403 Forbidden`. That is a sign-in failure, not a place failure, and
      the two are now distinguishable.

      Two traps in the state machine, both of which produced wrong answers before
      being fixed:

      * the sequence continues past the terminal state to `PlaceIdle`, so
        "last state wins" reported **0 of 21 successful launches as opened**.
      * `OpenPlacePreSuccess` ends with the word "Success" and is a *progress*
        state. A suffix test calls a half-finished load a success - the worst
        direction to be wrong in. The terminal set is spelled out instead.

      This needs a **full log read**, unlike the identity fields. The outcome
      lines land wherever the load finished, so the 64 KB prefix that makes the
      identity sweep cheap would silently report "no outcome". `read_open_outcome`
      is kept off the hot path for that reason.

- [x] **`EncodingService:Base64Encode` DOES accept a buffer, and is 70x faster.**
      This file previously recorded the opposite and `capture.py` carried a
      hand-rolled per-3-byte base64 loop because of it. Measured on a real
      1233x754x4 frame (3,718,728 B):

      | path | time | output |
      |---|---|---|
      | `ES:Base64Encode` + `buffer.tostring` | **7.5 + 5 ms** | 4,958,304 chars |
      | hand-rolled Luau loop + `table.concat` | **527 ms** | 4,958,304 chars |

      Byte-for-byte identical at every sampled offset, so the swap is free of
      behavioural risk. The loop was a 70x tax paid for a misdiagnosis. Note
      `GetMethodsOfClass("EncodingService")` does **not** list `Base64Encode`
      either, so reflection cannot be used to check this claim - it has to be
      called.

- [x] **The capture chunk size was a speed bug, not a capacity limit.**
      `DEFAULT_CHUNK` was 120,000, justified as "the project already uses this
      size for chunked writes" - a cargo-cult from an unrelated context. Every
      append rewrites the whole module, so N appends write ~P*N/2 bytes, and
      **splitting one module never raises the 6,291,456 ceiling** - a payload
      over it fails at any chunk size. So appends only ever cost time.

      Measured end to end, all sizes returning byte-exact RGBA:

      | chunk | appends | approx written | elapsed |
      |---|---|---|---|
      | 120,000 | 31 | 59.5 MB | 3.60 s |
      | 1,000,000 | 4 | 10.0 MB | 1.85 s |
      | 2,000,000 | 2 | 6.0 MB | 1.71 s |
      | 4,958,304 | 1 | 5.0 MB | 1.70 s |

      Set to the ceiling. Capture is now **1.6-1.7 s, down from 3.60 s (2.1x)**,
      verified 3/3 with every PNG chunk CRC valid and a full un-filter round trip
      back to exactly 3,718,728 bytes (sky `(149,199,219)`, ground `(86,96,118)`,
      alpha 255). A single 4,958,304-char write works, and 6,300,000 also works,
      so the documented ceiling is a module-size limit and not a per-call one.

- [x] **`EncodingService:CompressBuffer` works but is deliberately unused.** Needs
      a second argument of type `CompressionAlgorithm`, where only the value `0`
      is accepted. Not used: the payload already fits under the ceiling, a real
      rendered frame compresses far less than a synthetic gradient (my test data
      claimed 73:1, which proves nothing), and **Python 3.12 has no stdlib zstd**,
      so the saving would land as a host-side dependency.

- [x] **Where the remaining capture time goes**, measured in-Luau: 801 ms waiting
      for the `CaptureScreenshot` callback, 42 ms `CreateEditableImageAsync`,
      2 ms `ReadPixelsBuffer`, 6 ms `Base64Encode`, 2 ms `buffer.tostring`. So
      **855 ms in-Luau is the floor** with this API, and the 801 ms callback wait
      is nearly all of it. The engine-side alternative is
      `StudioCaptureService:CaptureScreenshot` with `OutputSize`, which the
      `gurmyd/roblox-studio-live` brief marks **unresolved and gated** (`E4`), and
      whose "which DataModel can capture" semantics they could not settle. Not
      adopted on an unresolved gate.

- [x] **Added `annotations.readOnlyHint` to the extended tool set.** None of the 15
      tools carried one, and a client that decides parallelism from that hint
      serialises an unmarked tool. Seven are read-only; the two deliberately
      ambiguous ones (`extended_wait_for`, which runs a caller-supplied probe, and
      `extended_manage_instance`, which lists on one action and launches on
      others) stay unmarked, as does everything that writes. Unmarked is the safe
      default, so a new tool cannot accidentally gain parallel dispatch.
      Pinned by `test_readonly_hints.py`, which fails if the read-only set names a
      tool that no longer exists.

- [x] **PrintWindow capture is 8-35x faster than the engine path, and VERIFIED
      here.** From `gurmyd/roblox-studio-live` `bridge/src/capture/worker.ps1`,
      which is host-side PowerShell and so portable in full. Their brief claims
      28-44 ms; measured on this machine with 3 Studios open:

      | path | elapsed |
      |---|---|
      | `extended_capture` (mesh + `CaptureScreenshot`) | **1,700 ms** |
      | `PrintWindow` + `PW_RENDERFULLCONTENT` | **48-205 ms** |

      The in-engine 855 ms floor is unavoidable (801 ms of it is the
      `CaptureScreenshot` callback wait), so this is the only way past it: it
      never touches the mesh or the engine at all.

      **Verified, not assumed.** All three Studio windows captured at 97.4%
      non-black, including the one at the bottom of the Z-order, so their
      "works while occluded" claim holds. 1936x1048, colortype=2, inflated IDAT
      exactly `h*(1+w*3)`, row means showing dark chrome / lighter viewport band /
      dark status bar - a real window, not a black rectangle.

      Four traps in their worker that are worth copying verbatim, each of which
      yields a plausible *wrong image* rather than an error:

      * **24bpp, not 32bpp.** `PrintWindow` leaves alpha at 0 on composited
        surfaces and PNG keeps that as full transparency. Confirmed: colortype
        came back 2 (RGB). A 32bpp capture would be a fully transparent image
        with no error anywhere.
      * **`SetProcessDPIAware` before `GetWindowRect`.** Otherwise the rect is
        DPI-virtualised and the bitmap does not match the window.
      * **`WrapMode.TileFlipXY` on the bicubic resize**, or sampling bleeds a
        black border into the edges.
      * **`ShowWindow(SW_SHOWNOACTIVATE)` + park at `HWND_BOTTOM`**, to
        un-minimize without stealing focus. Directly on-theme: this project
        exists to avoid disturbing the user's session.

- [x] **A window title is a third, independent record of which place is open.**
      Found while measuring the above. Studio's title bar carries the **full
      place path**:

      ```
      C:\Users\User\AppData\Local\Temp\...\Baseplate-35375535.rbxl - Roblox Studio
      ```

      The mesh gives only the **basename**; the log command line gives the full
      path. So the file route has three independent records of the same identity.

      **This does NOT improve `studio_id` -> PID, which was already reliable.**
      An earlier note here put the title in that chain, which was wrong in
      ordering: the PID comes from the log's own `UIThreadNotifier` line, and the
      title is derived *from* a PID, so it cannot be a link in the chain. What it
      actually buys is a **check** - having found a PID's HWND, confirm the title
      names the place you expected before trusting the pixels. That is worth
      having, because it is the check that catches a mis-targeted capture, but it
      is a check and not a link.

      The real chain is:

      ```
      studio_id -> mesh name (basename) -> that process's log command line
                -> that log's PID line -> PID
                -> EnumWindows + GetWindowThreadProcessId -> HWND -> PrintWindow
      ```

      The tail is new and is the only part that needed the window work. Measured
      live, all three attached Studios resolved with `allow_console_write=False`.

      Also: `DwmGetWindowAttribute(DWMWA_CLOAKED)` is needed to filter
      virtual-desktop windows, and within one PID the **largest** window is the
      main window rather than a floating dock.

- [x] **The URI route resolves too, when it is unambiguous.** Correcting an
      earlier overstatement that it "does not join". `match_mesh_name` falls back
      from the exact basename to the `placeId` embedded in
      `Template_<placeId>_AutoRecovery_<N>.rbxl`, so a lone URI launch resolves.
      Measured live: `c90cbd13` -> pid 12324 with `allow_console_write=False`.
      It is ambiguous only when **2+ URI launches of the same place are live at
      once**, which is what `ambiguous_reason` says and what the token print
      still covers.

- [x] **The job/handle pattern from `bridge/src/jobs.ts` is worth porting** and
      maps to the open "how do we do >120s work" item. Three details here would
      have been got wrong:
      * `MAX_JOB_WAIT_MS = 50_000`, tighter than this project's `MAX_WAIT = 90`,
        for the same reason (the 120 s client auto-background). Two polls of 50 s
        fit one turn with room to spare.
      * **`timer.unref()`** on job expiry, so a 10-minute expiry timer does not
        keep the process alive. Easy to omit and hard to notice.
      * **`onFinish` fires immediately if the job already finished**, closing the
        registration race where a hook registered after completion never fires.
      * `wait(ms)` resolves `true` on finish and `false` on timeout, so a poll
        that times out is a value, not an error.
      * `JobStore.drain(filter, maxMs)` - wait until nothing matching is running,
        return whatever is still running. Exactly the "flush before capture"
        semantic.

      **What does not port:** their `cancel` sends a frame to their own hub. An
      in-flight `execute_luau` is owned by the mesh, so it cannot be cancelled
      from here. A job handle can be *abandoned* but not *cancelled*, and the
      difference has to be in the tool description.

- [ ] **NOT portable, and it is most of their repo.** Everything under `plugin/`
      is off limits: Roblox's `StudioMCP` is signed, so the hub, the
      `PluginConnection` star, the 8 ms `CooperativeJobRunner`, the
      `ChangeHistoryService` recording wrapper, and the `LogService.MessageOut`
      push journal all require owning the plugin. Their `docs/research-brief.md`
      reaches the same conclusion from the other side: `PluginConnectionService`
      has **zero third-party adoption** and its payload cap is undocumented.
      `bridge/src/sync/*` is a Rojo reimplementation and Rojo exists.
      `bridge/src/vision/*` is a product, not a technique.

- [x] **Cross-checked against another implementation reading the same logs.**
      `Superwheat/renium` `tools/renium/src/studio/diagnosis.rs` independently
      uses the same `for process 'PID'` marker, and also reads
      `OpenPlaceFailure`/`ErrorMessage`, `Running instance count at launch N`, and
      `Login got Standalone DM ready`. Their claim that Studio 0.741 hangs a
      window opened while another is running, because the first window holds the
      sign-in mutex, **did not reproduce here**: `Hang Detected` appears in 0 of
      47 logs, and `Hang In Progress` in 1 of 47, on a log with 5 instances
      already running that then opened its place normally. So
      `Hang In Progress` is not a hang and is not treated as one. Their
      `LOG_HEAD_BYTES` is 256 KB against this project's measured 64 KB, and they
      sort by file `created` rather than the filename stamp; the stamp needs no
      `stat` call and does not move while a log is being written.

- [x] **A server plus its clients is many processes for one place.**
      `-task StartServer` spawns a separate `-task StartClient` Studio per
      player, so one place became 5 `RobloxStudioBeta` processes here. They all
      report `name: null` from the mesh, so name alone cannot separate them; the
      logs can, via `-parentPid` and `-task`, so the token join is no longer the
      only option.

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

## Launch routes fail at different layers (measured by renaming the exe)

Experiment: stop all Studios, rename
`Versions\version-76e1a02649ad4f35\RobloxStudioBeta.exe` to `.exe.bak`, then try
both launch routes. **Both failed, at different layers** — which is the finding.

| route | where it fails | what the caller sees |
|---|---|---|
| **file** (`action=launch` + `place_path`) | **our** discovery, `find_studio_exe()` | `no complete RobloxStudioBeta.exe under ... An update may be in progress; wait for it to finish.` |
| **URI** (`roblox-studio:1+task:EditPlace+...`) | **the OS protocol registration**, before our code runs | `OSError: [WinError -2147221003] Application not found: 'roblox-studio:...'` |

The URI route's handler is a registry entry, and it names the exe **by full
path**:

```
HKCU\Software\Classes\roblox-studio\shell\open\command
  -> "...\Versions\version-76e1a02649ad4f35\RobloxStudioBeta.exe" %1
```

So renaming the file does not merely remove it from our search — it leaves the
protocol handler **dangling**, and `os.startfile` reports that as *the
application not found*, which an agent will read as "the `roblox-studio:`
protocol is not registered". The real cause is "it is registered, and it points
at a file that is not there". Those need different recoveries and the message
conflates them.

- [x] **`find_studio_exe` no longer asserts a cause it cannot verify.** It emitted
      one sentence for three different situations: *"An update may be in progress;
      wait for it to finish."* It knows only that no file cleared
      `MIN_EXE_BYTES`. `platform.describe_installs()` now reports what is actually
      in `Versions\*`, and the three cases get three messages because the
      recoveries differ:

      | found | message says |
      |---|---|
      | exe present but under the floor | half-extracted update — **wait** |
      | Studio's own files present, exe gone | **broken install** — restore or reinstall |
      | only Player | normal machine — **install Studio**, waiting will not help |

      The middle case is the one that mattered: the exe had been **renamed**, so
      both "wait for the update" and "install Studio" were wrong. Detected by
      looking for `RobloxStudioInstaller.exe` / `NativeDialog.exe` in a directory
      with no `RobloxStudioBeta.exe`. 9 tests in `tests/test_studio_discovery.py`,
      one asserting the old sentence is gone from all three branches.
- [ ] **`os.startfile`'s "Application not found" still conflates two causes.**
      *Protocol not registered* and *registered but pointing at a missing file*
      need opposite recoveries ("install Studio" vs "repair the handler"), and the
      second is checkable — resolve the registry command and test the path. Not
      fixed: it needs a registry read, which is Windows-only and not yet a
      platform primitive.
- [x] **The `version-6b0e880a1a144428` mystery is solved — and calling it
      "unexplained" was my error, not a gap in the evidence.**

      | directory | what it is |
      |---|---|
      | `version-2366ba214ec740ca` | Roblox **Player** (`RobloxPlayerBeta.exe`) |
      | `version-6b0e880a1a144428` | an **old Studio install**, emptied by an update |
      | `version-76e1a02649ad4f35` | the current Studio install |

      Old dir created 09-27 11:54; new one 09-30 00:56; old dir last written
      09-30 00:58 — a cleanup pass two minutes *after* the replacement appeared,
      which deleted everything except the one file it had locked. Both hold a
      byte-identical 6,606,288-byte `StudioMCP.exe`. **User-confirmed** as an
      install updated while StudioMCP was in use; the timestamps are what make it
      checkable rather than merely plausible. The lesson is mine to carry: ten
      seconds of `dir` listing would have answered it before I wrote "unexplained".
- [x] **`action=stop` orphans the mesh proxy.** Stopping both Studios left **3
      `StudioMCP.exe` processes alive holding established connections to :13469**,
      ~40 MB, with zero Studio processes behind them. Honest scope: a **resource
      leak, not a correctness bug** — with the mesh empty, `list_roblox_studios`
      correctly reported 0. A later launch did show a second mesh entry, but that
      was a *real* bare Studio started from `explorer.exe`, not a proxy artefact.
      Correcting myself: earlier I claimed the proxy "has no phantom entries" as a
      general property. It holds only when no Studio sits behind the proxy.

## `extended_wait_for`'s probe only works in Edit mode

**The root fault is mine.** Found via a live error, not by the test suite:

```
Even that starts the loadstring() is not available
  Stack Begin
  Script 'AssistantCommand', Line 1
  Stack End
```

**First, a correction I had to make to my own entry.** I originally wrote "Roblox
Luau has no `loadstring`" as a flat fact. That is **wrong**, and my own evidence
already contradicted it: `extended_wait_for` called with `datamodel_type: "Edit"`
returned `satisfied: true, polls: 1`, so the probe *did* evaluate. I generalised
past the observation I was holding. **`loadstring` is reachable in Edit mode and
not in play mode** — user-confirmed. The availability is mode-dependent, not
absent.

So the defect is narrower and more useful than the one I first described:

- `build_probe()` (`python/src/roblox_studio_mcp/extended/waiting.py`, ported to
  `node/src/extended/waiting.ts`) compiles the caller's condition with
  `loadstring`, which resolves in **Edit** and not in **Client/Server**.
- `extended_wait_for`'s schema **offers `datamodel_type` with `Edit, Client,
  Server`**. Nothing in the tool checks that its probe works in the mode it was
  asked for.
- So a caller who waits on a condition in play mode gets a failure that has
  nothing to do with their condition — and the tool advertises the parameter that
  causes it. **That is a tool-surface defect, not user error.**

**Why the probe uses `loadstring` at all.** The original wrapper was
`pcall(function() return (<condition>) end)`, and a caller condition that failed to
*parse* took the whole `execute_luau` call with it — the client hung to the 120 s
timeout instead of receiving an error. Compilation was moved inside `loadstring`
to isolate the caller's syntax. In Edit mode that is a correct trade. In play
mode the isolation is unavailable and the tool is left with a technique it cannot
use.

**The tests were structurally incapable of catching this.** Both are string
assertions on the generated Luau:

```python
self.assertIn("loadstring", code)                # test_waiting.py:29
self.assertIn('loadstring("return (" ..', code)  # test_waiting.py:37
```

They verify the text produced, never whether it *runs*, and never in a second
DataModel. Naming the blind spot: **a unit test on generated code cannot validate
that the code executes.** Every other guard in this project — the `TypedDict`s,
the acid test, the closed-set checks — was about silent wrong answers, and none
of them reached emitted Luau.

- [x] **The three fix options below are MOOT — the premise died when `loadstring`
      was removed (user ruling 2026-10-01).** Kept as a record because all three
      were reasonable answers to a question that is no longer being asked, and
      option 1 would now be a **regression**:
      1. ~~**Refuse non-Edit modes explicitly.**~~ Would *remove working
         capability. All three DataModels are supported: `build_probe` splices
         the condition as bare code inside `pcall`, with no `loadstring`
         anywhere in either implementation (verified — the word survives only in
         prose explaining its removal). The tool advertises
         `datamodel_type: Edit|Client|Server` and honours all three.
      2. ~~**A play-mode-safe probe.**~~ **This is what shipped**, and it is
         simply the `pcall` form: a faulting condition is caught and reported as
         `THREW`, while a non-parsing one is the narrower `PROBE_CAVEAT` hazard,
         bounded per poll and aborted after `MAX_HUNG_POLLS`.
      3. ~~**Structured conditions instead of Luau text.**~~ Only motivated by the
         same removed hazard. Costs tokens per call to prevent something that is
         no longer reachable.

      **What the residual hazard actually is**, restated because it is the only
      part still live: a condition that does not *parse* is compiled as part of
      the command sent to Studio, before any handler runs. Measured 2026-09-30 to
      wedge Studio-side command execution. Client-side that is now bounded and
      loud — it cannot hang the server loop, and it aborts naming the restart.
      Nothing here can un-wedge Studio, so it stays a Studio-side limitation and
      is documented in `PROBE_CAVEAT` and the `rsx-*` skills rather than papered
      over with a compile step nobody needs.
- [ ] **Audit every emitted-code path for the same blind spot** — still open, and
      no longer about `datamodel_type`. The chunked writer, the breakpoint
      `log_expression`, and the capture encoder all emit Luau and are all tested
      by asserting on *text*, which cannot tell executing code from echoed text.
      The narrower question is the one worth asking: **does each remain correct in
      Client/Server, or does any of them assume Edit?** That is a runtime check,
      not a text assertion.
- [ ] **`AssistantCommand` is a symptom, not the disease.** A script this project
      did not write, and its use of `loadstring` is independently wrong — but the
      technique reached it from this repo, documented prominently as the fix for
      a hang. A confidently-documented wrong technique travels further than an
      undocumented one.
- [ ] **MEASURED 2026-09-30: an unparseable condition wedges the whole server,
      not just the call.** `extended_wait_for({condition: "this is not lua(((",
      timeout_seconds: 4})` never returned; the MCP required a reconnect to
      recover. A second probe with the capture half removed hung identically,
      so the capture is exonerated and the condition is the whole cause.
      Mechanism, from the code just re-read: `build_probe` raw-splices the
      condition, so a non-parsing condition means the entire command fails
      parse; the parse happens before any handler runs (`PROBE_CAVEAT`), the
      `await studio.call(...)` never resolves, and because requests are served
      sequentially the wedged call blocks every later one — which is why the
      fix was reconnecting, not waiting. `timeout_seconds: 4` was bypassed
      entirely: the hang is in the first poll's await, before any deadline
      logic runs. Do NOT re-probe this live without a mitigation in place;
      every repro costs a reconnect. Mitigation candidates, unmeasured: a
      host-side timeout around the poll await (un-wedges the loop even though
      it cannot cancel Studio-side execution), versus establishing whether
      Studio-side command execution itself is wedged (in which case even that
      only converts a dead server into failing polls until Studio restarts).

- [x] **The lock file layout was documented wrong, and measured.** `locks.py`
      claimed `pid | processName | machineName | sessionGuid | |`. Two live lock
      files read byte for byte on this machine:

      ```
      31 32 33 32 34 0a 52 6f 62 6c 6f 78 ...  ->  "12324\nRobloxStudioBeta\n
                                                  DESKTOP-IH0RL4D\n<guid>\n\n"
      ```

      **No `|` byte in either file.** The separator is `\n`. The join was never
      affected — the PID is the first field either way — which is precisely why
      the wrong documentation went unnoticed, and why the test fixture (written
      from the same prose) agreed with the doc and disagreed with reality.
      *A fixture authored from prose inherits the prose's errors.*
- [x] **Consequence of the above, built 2026-09-30 as the approved paired
      edit:** `process`, `machine` and `session` are now parsed from the
      newline shape (pipe split kept as fallback, so the legacy fixtures still
      pass). The **session GUID is in the file** and is now a second
      independent witness available for a log-derived identity - wiring it
      into the join itself deliberately not taken (parsing changes values,
      joining changes resolutions).
- [x] **`tsconfig.check.json` is now a gate I actually run.** It type-checks
      `tests/`, which plain `tsc --noEmit` does not, and it immediately found
      **9 real type gaps** — including a `ContractEntry` missing the `read_only`
      field **I added earlier the same session**, and two test-local unions
      narrower than the values their own tests passed. `package.json` already had
      `npm run typecheck` pointing at it; I was running the weaker command. Same
      failure shape as `watch_output`: a type that stops describing reality, and a
      compiler no longer asked.

- [x] **Dead code found and removed — and the removal broke three live symbols,
      which is the part worth keeping.** `parity/find_unused_python.py` reports
      module-level symbols with no reference anywhere in `src/`, `tests/` or
      `parity/`, split into genuinely dead vs test-only. It reported 10 dead; the
      first version of the detector itself was wrong (145 "dead", because it
      subtracted the defining module and so flagged every private helper used only
      where it lives - `_PID_RE`, `_bounded_int`).

      Removed 8: `instance.LOG_STAMP`, `instance.URI_KEYS`,
      `instance._mesh_has_named_instance`, `instance._ps` + `_json_ps` (a
      mutually-dead pair left by the `platform.process_rows()` refactor, with
      unreachable code after `_json_ps`'s own `return`), `platform._PS_LSTART`,
      `platform._NOTIFIER_MARKER`, `platform.launch_studio`,
      `logid.mesh_names_for_identity`, plus the `import json` that `_json_ps`
      orphaned.

      **Then it broke three things, and the gates caught all three:**
      - `platform.BANNER_RE` - deleted as collateral, so **`logid.py` stopped
        importing** (`module 'platform' has no attribute 'BANNER_RE'`).
      - `instance.ROLE_EDIT/SERVER/CLIENT/UNKNOWN` - deleted with `LOG_STAMP`.
      - `instance.URI_UNIVERSE_ID` - deleted with `URI_KEYS`, so two test modules
        failed to import.

      Cause: the pruning script deleted by **regex**, `^NAME\s*=.*?(?=\n\n\n|\Z)`
      with `re.S`. A non-greedy match under `DOTALL` that is anchored to the next
      triple-newline reaches far past the symbol it was aimed at, and swallows
      every constant in between. Three separate deletions, three separate losses.

      **Never delete code by regex.** Use the `edit` tool with exact strings, or
      delete by AST node and re-print. The saving is a few seconds; the cost here
      was three restored blocks and a server that could not start. Caught only
      because the gate ran immediately after — the same rule as the MCP restart,
      and for the same reason: *measure the thing you changed, not the intention.*
- [ ] **`invalidate_process_cache` is the last dead symbol, and it is a judgement
      call rather than an oversight.** It has no caller in this repo, but it is
      public API on a module that is also importable as a library, and a consumer
      who starts a process out of band would want it. Left in place deliberately.
- [x] **Node kept `meshNamesForIdentity` after Python dropped it.** The port
      agent mirrored the Python surface, which still had the function; deleting it
      in Python leaves Node exporting something Python does not. Flagged rather
      than "fixed", because deciding which side is right is the same call as the
      lock-GUID one: it is about what this surface is for.
- [x] **Six symbols are test-only, and that is the healthy state**:
      `pid_from_lock`, `names_from_locks`, `session_guids`, `read_open_outcome`,
      `is_lua_fault`, `SetBreakpointResult`. No production caller, each pinned by
      a test. Deleting one deletes its test, and in three cases the test is the
      only thing asserting a measured invariant. Not dead; **under-used**, and the
      honest description.

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

## Session operations

- [x] **The agent was served a 14-hour-stale Node build.** `node/dist/extendedServer.js`
      dated 29/09 23:46 while the Python source was hours ahead: 33 tools, 10
      extended, and **none** of this session's work callable. `extended_script_grep`
      was a second instance of the same bug - a module written, never registered.
- [x] **`opencode.jsonc` now runs the Python server.** 33 -> **39 tools**, 10 -> **16
      extended**, all sixteen confirmed present. `PYTHONPATH` points at the working
      tree, so no build step and an edit takes effect on restart. Confirmed to be the
      Python build by the skill index matching byte-for-byte and carrying same-day
      findings.
- [ ] **An edit to a `.py` file needs an MCP server restart** - the process holds the
      module in memory. The config change restarted it; the code fix did not. Same
      trap as the stale dist, and it cost a round of "still broken" measurements.
- [x] **Launch timeline for reference:** process 1.5 s, log naming it 1.5 s, place
      opened 26.3 s, matching mesh name 26.3 s.

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

## macOS

### Next moves, recorded 2026-09-30 so they survive the session

These were the open next moves. Written down before starting, because the
recurring failure in this project is deciding something in conversation and
losing it. Grouped into the three blocks actually worked.

**All of the below is now CLOSED.** Recorded as resolved rather than deleted,
because four of the eight were resolved *against their own premise* and that is
the part worth keeping.

**Block 1 - correctness of dispatch and of one tool's contract**

- [x] **`extended_wait_for` rejected a non-boolean condition by reporting it
      satisfied.** Now a hard `INVALID_ARGUMENT` naming the value. **Not
      "non-boolean means error"**: `nil` stays *not yet*, because
      `return workspace.Foo` is an ordinary condition and erroring it would
      break the tool's main use. The `warning` field was **removed** - it had one
      producer, so it would have shipped as a permanent `null`, which invites a
      caller to check it, find nothing, and conclude all is well. Description now
      documents the condition syntax, at the cost of 5 characters trimmed
      elsewhere.
- [x] **Unknown-argument ordering. THE PREMISE WAS WRONG** - both sides already
      rejected before dispatch. Measured live instead: `root_paths` returns
      `root_path: Missing key` from **the harness**, which validates JSON Schema
      `required` before the request arrives; `max_resultz` (unknown *optional*)
      survives that and is named correctly; and our own `client.py` does no
      schema validation at all. So the server is right and the ordering is simply
      invisible through a pre-validating client. Pinned with tests on both sides,
      docstring carrying the evidence. **Not fixable from this repo.**

**Block 2 - error messages and docs that contradict the tools**

- [x] **`rsx-breakpoints` documented an interface that does not exist.** No
      `action` parameter, never has. Worse than recorded: I nearly "fixed" the
      skill with a wrong answer. The tool **toggles** on existing registry state,
      so calling it twice leaves you with **no** breakpoint and reports success
      both times. Skill rewritten to say so, and the fake `set`/`list`/`remove`/
      `clear` references removed from its checklist and its history section.
- [x] **Edit-mode errors now carry recovery**, via a `_RECOVERY` table keyed by
      code. **Appended** after a blank line, never paraphrased, so the engine's
      wording and any substring match survive. Built a fresh error rather than
      mutating: `ToolError.__init__` passes to `Exception.__init__`, so
      `str(error)` reads `args[0]` while `to_error()` reads `.message`, and
      mutating one leaves them permanently disagreeing. One code seeded; widening
      it is a decision per code, not a formatting pass.
- [x] **`extended_capture`'s `save_path` was a bare `OSError`** on both sides.
      Now a `ToolError` that says the capture **succeeded** and only the write
      failed, plus which of the three fixes applies.
- [x] **The `extended_script_grep` description was NOT stale.** "Prefer over raw
      `script_grep`" is correct advice *now*; it was wrong advice while the tool
      was broken. The real falsehood was **"deduped and ranked"** - nothing
      ranks, and live results come back in file order. Premise wrong, target
      right.

**Block 3 - macOS, researched rather than inferred**

- [x] **Researched and implemented; still never executed.** Two behaviour
      changes: `open_uri` now raises `CAPABILITY_DENIED` on macOS (the scheme is
      unevidenced there, and `open` failed silently), and the launcher-vs-proxy
      distinction is recorded so neither replaces the other. Three prior-art
      differences recorded rather than "fixed": `ps` over `pgrep` (we need start
      time), non-zero exits ignored (`pgrep` exits 1 on no match), and
      `127.0.0.1` stays literal (macOS `localhost` can resolve to `::1`).
- [x] **Prior art.** `Chrrxs/robloxstudio-mcp` is the only project with real
      macOS engineering; `Roblox/studio-rust-mcp-server` is archived. See the
      macOS section above.

**Housekeeping**

- [x] **24 untracked `python/verify_*.py` scratch scripts, 2,742 lines, imported
      by nothing — deleted 2026-09-30.** All one-shot live-measurement drivers
      whose outcomes are recorded in this file (each docstring asked a question
      this file answers). Deletion is also safety: they launch/stop Studios and
      print console tokens, so re-running one now risks touching `rbx-re.rbxl`
      (read-only ruling above) or spraying writes. `parity/` keeps the
      re-runnable inventory (`compare_implementations.py`,
      `find_unused_python.py`); `measure_*`/`probe_progress.py` left alone as
      offline tooling. Two of the deleted files contained dead
      `C:\tmp\baseplates` paths.
- [x] **Description budget headroom.** Was 2,689 of 2,700, 11 spare, then
      2,694 (Node) / 2,699 (Python). Now **2,692 on both sides, 8 spare** -
      the `run_tests` skill pointer (+36) was funded by two cuts ("by
      location", "Cached host-side only", the latter moved to
      `rsx-targeting.md`), net -2.
- [x] **A 2-character budget failure exposed a real defect.** Node's
      `extended_update_script` had drifted 7 characters from Python's, so callers
      were charged differently per implementation. Now textually identical, and
      so are the three smaller ones (`write_script`, `script_grep`,
      `script_search_and_read`): all three pairs are now byte-identical,
      verified programmatically, with the contract regenerated. The
      `regex=true` vs `regex:true` wording difference inside `script_grep` was
      settled toward `regex:true` (JSON-call convention) at zero net length.

**New, found while checking studios rather than planned**

- [x] **The standing constraint at the top of this file keyed on a name that no
      longer exists.** "Game TESTING" was `fa092c48`, now a stale null-name
      registry row. The Studio attached in its place reports **`rbx-re.rbxl`**
      (`1631ed70`, debug id `0_186523`). **Resolved by user ruling 2026-09-30:
      `rbx-re.rbxl` is read-only** (observe via read-only tools; never mutate,
      execute, or stop). What can be said: its console shows
      `CoreGui.__ClampProbe` work and four unrelated faults, which does not
      look like the same content.
- [x] **The Studio registry grew without bound - pruned 2026-09-30.**
      `registered_total: 7` against `count: 2`, and nothing expired. Fixed at
      the write path: `record()` drops entries unseen for 30 days (every
      restart orphans its old entry permanently - new debug_id, new key - so
      the file grew one entry per restart forever). The just-recorded entry
      is exempt, so clock skew can only spare, never delete fresh. The 24 h
      stale flag is unchanged: stale stays discoverable, 30-day-old is gone.
- [ ] **`StudioMCP.exe` proxies accumulate on the mesh.** Three alive for two
      Studios, one per client connection, ~40 MB each. This is ongoing, not
      leftover from the earlier `action=stop` analysis.
- [x] **The console buffer is a sliding window; the log file is the record.**
      Found by disagreement: `rbx-re.rbxl`'s console showed **0** `RBXPID` lines
      while its log contained one. Counting from the console undercounts. Any
      audit - "how often did the last resort fire", "did this happen at all" -
      must come from `%LOCALAPPDATA%\Roblox\logs`, not `extended_watch_output`.
- [x] **The identity console write fired 12 times today across 8 Studios** (all
      before 15:58 local), and the live two hold **3 and 1** - not 2 each. The
      two Studios with exactly 2 are dead. **None of the 12 were produced by the
      code as it stands now**, so the ordering change remains covered by 8 unit
      tests and no live run.

**Status: the parsing is verified, the integration is not.** No Mac was available
in this environment, so no line of the macOS branch has run against a real macOS
Studio. That is a limitation of what could be measured, not a reason to skip the
platform, so the port is built and its unverifiable half is labelled.

Provenance, kept separate as always:

| claim | provenance |
|---|---|
| macOS **parsing** - `ps` rows, banner match, path layout, `Application Support` spaces | **measured here**, on Windows, against macOS-shaped fixtures (`tests/test_platform_macos.py`, 40 tests) |
| macOS **directories** | **user-authoritative**: Roblox's own docs state them (`/studio/mcp`, `/studio/command-line-interface`, `/projects/place-files`, support article on logs); corroborated by `Superwheat/renium` and `Chrrxs/robloxstudio-mcp` |
| macOS **`roblox-studio:` scheme** | **negative result, unproven either way** - no `CFBundleURLTypes` evidence readable, no Mac forum post. The route is refused because it is *unevidenced*, not because it is absent |
| macOS **log filename token width** | **weakest claim in the module** - one Windows-era support example says Mac names "look the same". If log discovery goes quiet on a Mac, check the regex, not the directory |
| macOS **integration** | **unverified** - no Mac, no measurement, no claim |

- [x] **`extended/platform.py` centralises every platform difference.** Previously
      these were scattered through `instance.py`, `logid.py` and `locks.py`:
      PowerShell, `os.startfile`, `%LOCALAPPDATA%`, and `RobloxStudioBeta.exe`
      *inside a regex*. `instance.py` / `logid.py` / `locks.py` now call into it.
- [x] **One platform predicate, branched positively on `is_windows()`.** A first
      draft branched some functions on `is_macos()` and others on `is_windows()`,
      so patching one in a test did not affect the other and three tests passed
      for the wrong reason. The unpatched default must be the branch that is
      actually exercised on this machine.
- [x] **macOS paths are joined with `/` explicitly, not `os.path.join`.** Measured
      defect: `os.path.join` uses the *host* separator, so building the macOS log
      dir on Windows produced `/Users/me\Library\Logs\Roblox` - a path that exists
      on neither platform.
- [x] **A real macOS-only bug, found only because the fixture is macOS-shaped:**
      the place-path patterns were `(--localPlaceFile|-localProjectFile)\s+(\S+)`,
      and `\S+` stops at the space in `Application Support` - a *fixed* component
      of the Roblox-documented macOS AutoSaves path. A Mac launch parsed as
      `place_path='/Users/me/Library/Application'`. Silent: a truncated path still
      yields a basename, so the name join searched for a document called
      `Application` and simply never matched. Now `_path_after()`, which handles
      quoted paths, a following flag, and a path-last-with-spaces. Windows paths
      are covered by the same tests, so the fix is not macOS-only.
- [x] **`MIN_EXE_BYTES` stays 100 MB** and now lives in `platform.py`. A first
      draft of that module used 20 MB, which would have quietly lowered a
      threshold that was set from a real `STATUS_DLL_NOT_FOUND`. Restored
      unchanged, with a test pinning it.
- [x] **`os.path.basename` is *not* a macOS hazard, contrary to an earlier claim
      in this repo.** `ntpath` and `posixpath` both treat `/` and `\` as
      separators, so it returns the right answer for a macOS path on Windows. A
      test had pinned the false claim and caught it. `platform.basename` remains
      as an explicitness wrapper only, documented as such.
- [ ] **`platform.parse_ps_row` and `platform.attached_pids` (lsof) are unproven
      on a Mac.** Both are unit-tested against captured macOS-shaped rows, but
      `lsof -iTCP:13469 -sTCP:ESTABLISHED -t` is inferred from the Windows
      equivalent's semantics, not observed.
- [x] **The paths are confirmed from Roblox's own documentation**, not guessed:
      | what | Windows | macOS |
      |---|---|---|
      | logs | `%LOCALAPPDATA%\Roblox\logs` | `~/Library/Logs/Roblox` |
      | AutoSaves / locks | `%LOCALAPPDATA%\Roblox\RobloxStudio\AutoSaves` | `~/Library/Application Support/Roblox/RobloxStudio/AutoSaves/` |
      | Studio binary | `...\Versions\*\RobloxStudioBeta.exe` | `/Applications/RobloxStudio.app` |

      Sources: Roblox support "How to Retrieve Roblox Studio Logs", and
      `create.roblox.com/docs/studio/debugging`, both of which give the mac path
      explicitly. `Superwheat/renium` independently uses the same two paths.
- [x] **The log FILENAME format is identical on macOS** - same
      `<version>_<UTCstamp>_Studio_<token>_last.log`, from a macOS example in
      Roblox's support article. So `stamp_seconds`, the stamp ordering and the
      time-window filter all port with no change.
- [x] **FLog is documented as one cross-platform standard** for Player, RCCService
      **and Studio**, with a single format spec
      (`worships/roblox-fastlog-viewer/docs/fastlog.md`). The
      `UIThreadNotifier` channel is an FLog channel emitted by the shared C++
      engine, not a Windows subsystem.
- [ ] **The gating measurement is still not made, and I am not going to call it
      settled.** Four converging pieces of evidence that the
      `UIThreadNotifier ... for process 'N'` line is present on macOS:
      1. FLog is one documented format across Player/RCC/Studio.
      2. The filename format is byte-for-byte the same shape.
      3. The channel is engine-level, emitted by shared C++.
      4. **`Superwheat/renium` uses that exact marker on macOS** - its
         `studio_log_for_process` sits behind both the `#[cfg(windows)]` and
         `#[cfg(target_os = "macos")]` process enumeration, so its macOS branch
         would simply never work if the line were absent.

      That is strong and it is still inference. A direct search for the string in a
      macOS Studio log returned nothing. So: **treat as likely, verify before
      relying on it** - one probe on any Mac settles it, and a Mac user debugging
      their own machine is the cheapest source available.
- [x] **macOS logs are apparently never culled** (devforum, 2016: "it appears logs
      are not being culled at all on Mac"). If that still holds it makes this
      project's log discipline - prefix read, filename-stamp ordering, time-window
      filter - **more** valuable there, not less, since the directory grows without
      bound. Worth re-checking, as the observation is nine years old.
- [x] **Fixed: `_CMDLINE_RE` no longer hardcodes `RobloxStudioBeta\.exe`.** It was
      a real latent bug: on a Mac the parser would have silently found nothing and
      every join would report "no identity" **with no error**. The pattern now
      alternates both spellings and lives in `platform.BANNER_RE`, tested against a
      Windows and a macOS banner. See the macOS section above.
- [ ] **FLog has four format types and this project only parses one.** Type 4 is
      `ISO8601Z,TimeSinceStarted,Thread,LogLevel [FLog::...]`; type 1 is
      `UnixTime,Identifier,LogLevel [FLog::...]`, type 3 is
      `TimeSinceStarted Thread: message`. The parser's negative lookahead for
      `\d{4}-` assumes type 4. That is a **Windows-side** robustness gap too, not
      just a macOS one: an older-format log would be read as having no PID.
- [ ] **No macOS equivalent for the fast capture path.** Win32 `PrintWindow` has no
      counterpart; `CGWindowListCreateImage` is deprecated and needs screen-recording
      permission. So macOS capture stays on the engine path at ~1.7 s, and the
      PrintWindow work does not port.

## Remaining

### Process failure worth recording: I gave two agents the same files

Two subagents were dispatched to disjoint-sounding work and given **overlapping
file lists**. The type-annotation pass had `grep, updater, capture, writer,
registry, skills, breakpoints, waiting`; the error-code pass had
`extended_server.py` plus `grep, updater, capture, writer`. Four files were in
both, and the agents spent the session editing each other's work.

What it cost, measured rather than guessed: the type agent's import edit
clobbered the other agent's `errors` import in `breakpoints.py` (caught and
repaired), and `test_error_parity.py` failed transiently mid-flight, 11 → 0. The
final state converged — 553 passed, pyright 0 — but convergence was luck plus two
agents re-reading before each edit, not design.

**The mistake was mine and it is specific:** I partitioned by *area of the
codebase* ("the extended modules") when the agents were partitioned by *task*.
Two tasks that touch the same files must not run concurrently, however different
the tasks sound.

Rules for the next dispatch:

- **Partition by file, and state the partition in the prompt.** Give each agent an
  explicit allow-list and an explicit do-not-touch list, and make them disjoint.
- **One writer per file at a time.** If two tasks need the same file, run them in
  sequence, or have one produce a patch description for the other to apply.
- **A read-only agent can always run concurrently** with anything — that is what
  made the usability report safe to launch while two writers were mid-flight.



**As a property of each change, not as a workstream.** The distinction is the
whole cost question:

- **Cheap, and the default:** a fix or feature lands on Python and Node in the
  same pass. This is what actually happened throughout the parity work, and it
  cost almost nothing over doing Python alone.
- **Expensive, and now forbidden:** porting a Python-only module that Node already
  lacks. `logid.py` + `locks.py` is 946 lines of the most subtle code in the
  project, and porting it would be pure catch-up. **It stays unported.**

That reframes the 1,012-line gap: it is not a debt to be amortised, it is a
**documented, frozen boundary**. `node/src/extended/IDENTITY.md` states it, and
Node's `action=stop` refuses rather than guessing across it.

The cheap invariant stays enforced. `parity/tools.json` fails both suites if
either side's tool list, schema properties, required arguments or description
budget drifts. That is the part where drift is silent and expensive; behavioural
parity behind it is best-effort.

What this buys, and what it does not:

| | |
|---|---|
| catches | a tool that exists on one side only; a parameter that exists on one side only |
| does not catch | a Node module that implements a behaviour worse than Python's |

The second row is accepted, not overlooked. It is the cost of not paying the
backlog, and it is bounded by `IDENTITY.md`.

### Priority order, and why

**Node parity is closed at the tool surface.** 16 tools on both sides, identical
schemas, enforced by tests rather than by discipline. What remains is one
behavioural difference, documented in `node/src/extended/IDENTITY.md`.

Parity is now **mechanical**, which is the point. `parity/tools.json` is generated
from the Python server by `parity/build_contract.py` and asserted by *both* suites
(`python/tests/test_parity.py`, `node/tests/parity.test.ts`). Neither
implementation can change its surface without a failure somewhere. The previous
approach - reading both sides and comparing by eye - is how they drifted with
nothing to notice: Node was missing three tools and six schemas differed.

| | Python | Node |
|---|---|---|
| extended tools | 16 | 16 |
| description chars | 2,692 / 2,700 | 2,692 / 2,700 |
| tests | 377 pass | 299 pass |

- [x] **Node error codes** - `errors.ts`, all 15 codes, the ordered message table,
      the Luau-fault pattern checked **first**, `ToolError`, `classify`,
      `isLuaFault`. Node previously threw bare `Error`, so a caller porting between
      implementations got strictly worse behaviour.
- [x] **Node `watch_output` filter and cap** - see Bugs. Functional, not cosmetic:
      the schema had neither parameter.
- [x] **Node `extended_wait_for`** - `waiting.ts`, including the `loadstring`
      probe wrapper and the two `_luauString` long-bracket rules, each of which
      cost a debugging session to find. 17 mirrored tests.
- [x] **Node `extended_clear_breakpoints`** - `breakpoints.ts` was written but
      **unregistered**, so the module was dead code. Now wired.
- [x] **Node `extended_manage_instance`** - `instance.ts` + `platform.ts`:
      process enumeration, mesh attachment, termination, the launch URI, and
      template place-id extraction.
- [x] **Node `extended_breakpoints` had no `required` list at all.** `script_path`
      and `line` were optional, so a call with neither was accepted by the schema
      and failed at runtime instead of being rejected. The contract test caught it.
- [x] **Description drift aligned**, 4 tools. This *freed* 337 chars rather than
      consuming them - Node's prose was longer - which is what paid for the two new
      tools. Net: Node went 2,859 (over cap) -> 2,693.

- [ ] **The one real difference: identity resolution.** Python resolves
      `studio_id -> PID` by reading the PID out of the Studio's own log
      (`logid.py`, 33 KB). Not ported. Node uses process enumeration, so with two
      Studios on one place - the current state on this machine, both named
      `Place1` - it **cannot say which is which**. `action=stop` therefore
      *refuses* rather than guessing, because it is irreversible. See
      `node/src/extended/IDENTITY.md`. **1,012 lines / 42,888 B unported**
      (`logid` 769 + `locks` 177 + `__init__` 66). Closing it is the single
      largest remaining item on either side.

- [x] **`node/dist` was 30 built files behind `src/`.** `package.json` has
      `"main": "./dist/index.js"`, so switching the MCP config to Node would have
      run code from *before* the port, with no error anywhere - the server starts,
      answers, and is simply not the code you wrote. Rebuilt, and staleness is now
      a **test** (`node/tests/build-freshness.test.ts`) rather than something to
      remember, with a negative control proving it can fail.

- [x] **Python had no type checker at all.** Now pyright-gated; see the section
      above for the mypy/pyright comparison and the 7 real defects it found in
      code every runtime test already exercised.

- [ ] **The budget is still the binding constraint**, and now tighter on the Node
      side: 2,693 of 2,700, **7 characters** of headroom. A 17th tool is a
      decision for the user, not an accident.

- [ ] **The description budget is effectively full, 16 tools.** Re-measured
      2026-09-30 after the Block 1 and Block 2 edits, which moved it. This is a
      hard blocker on adding a 17th tool and it is worth knowing *before*
      designing one:

      | | chars |
      |---|---|
      | Python total | 2,692 |
      | Node total | 2,692 |
      | cap | 2,700 |
      | headroom | **8 both sides** |
      | longest single | `extended_update_script` 249 (per-tool cap 450) |

      Closed 2026-09-30 at 2,694/2,694 after converging the last three
      description pairs to byte-identical text (toward the shorter wording).
      Treat any description edit as zero-sum.

      Previously recorded as 2,689 with 11 spare. The two sides now agree on
      length to the character (2,694 each), so a one-sided description edit
      fails the budget symmetrically — which is the property that made the
      `extended_update_script` drift worth fixing: the contract compares
      *normalised* schemas so it stays green on wording drift, while the
      budget is charged on *raw* length.

      So a new tool needs ~150 chars freed by trimming, or a raised cap. A cap
      raise is a real cost - the tool list is paid on every call, every session -
      so it is a decision for the user, not a silent change.

- [ ] **The PrintWindow *implementation* is not in the repo - only its
      measurement is.** Worth being precise about, because the 48-205 ms figure
      reads as though the code exists. It does not: no `PrintWindow` call exists
      in `python/src` or `node/src`. The chain up to the PID is built
      (`logid.resolve`), but the window tail is not written, so shipping it is new
      code, not wiring.

      Two things follow, both of which argue for doing Node parity first:

      1. **It is not a user request.** `REQUEST-capture-and-font-assets.md` asks
         for P0.2b, lossless PNG of the *right* Studio. `extended_capture` already
         covers that. PrintWindow was found while measuring, and would be scope I
         added to myself.
      2. **It has a contract problem, not just a code problem.**
         `extended_capture` promises the **viewport**. `PrintWindow` returns the
         **whole window**, chrome included. Substituting one for the other
         silently is exactly the "plausible wrong image, no error" shape this file
         keeps cataloguing, so the two must be separate surfaces - which is the
         thing the budget above cannot currently afford.

      Unblocking it needs a decision: raise the cap, trim ~150 chars from existing
      descriptions, or skip it. The four traps are already written down above and
      port cleanly; the work is bounded.

- [ ] **`ExecuteMultiplayerTestAsync` still unverified**, and blocked behind a
      real constraint: a direct call blocks past the 120 s tool-call timeout,
      while `task.spawn` around it dies with the call's context and can wedge
      the test service. Those two are in tension, and resolving it needs either
      a way to hold a call open (a scratch instance acting as a mailbox) or a
      persistent host. The `-task StartServer` route sidesteps it for
      `AddPlayers`, but not for ending a test with a result value.
- [ ] **Chrrxs' 7-tool playtest surface still not built.** Their advantage is
      not `AddPlayers`, which we now have; it is ending a test with a *value*
      (an assertion result), per-client `LeaveTest`, and a stop path that
      separates "no test running" from "signal did not propagate". Take their
      granularity, keep our descriptions. Every call needs `studio_id`, since a
      play session belongs to one Studio.
- [ ] **`OnStopped` mode for breakpoints.** `rbx-debug` documents
      `OnStopped` + `GetVariables`/`Evaluate` and says to prefer it over
      logpoints for richer state. Ours is logpoints only, so capturing two
      locals means string concatenation and parsing. Constraints: `OnStopped` is
      per-DataModel and does **not** propagate edit -> play DMs, and a callback
      that throws **resumes by default**, so it must return an explicit
      `Enum.DebuggerResumeType`.
- [x] **Unknown tool parameters are now refused (request P0.2a).** The single
      highest-value check in the server, and it is there for what it *prevents*
      rather than what it reports. Three incidents in this project were a
      silently-ignored parameter producing a plausible wrong answer, **all
      reporting success**:
      - `screen_capture` accepted `format: "png"` and returned JPEG. Eight
        parameter names were tried before anyone noticed, because none errored.
      - **Node's `extended_watch_output` had no `pattern`**, so passing it
        returned the whole console buffer - indistinguishable from "no
        breakpoint was hit".
      - `max_lines: 0` became 200 through a falsy default.

      Now rejected before dispatch, on both sides, naming the tool, the offender,
      **every** unknown at once, and the nearest declared name. Measured against
      the real vocabulary rather than guessed:

      | sent | now |
      |---|---|
      | `extended_capture {format: "png"}` | refused |
      | `extended_watch_output {patern: ...}` | refused, suggests `pattern` |
      | `extended_breakpoints {line_number: 3}` | refused, suggests `line` |
      | `extended_capture {save_path: ...}` | accepted |
      | `screen_capture {format: "png"}` | **accepted** - relayed, not ours |

      The suggestion cutoff was set by measurement: at 0.6 every suggestion was
      sensible but `line_number` scored 0.53 and got nothing, and an abbreviation
      is a common caller error. At 0.5 the only change is that one case, and
      `filters`, `q`, `xyzzy` still get nothing against every tool.

      Relayed Studio tools are deliberately left alone - this project cannot add
      parameters to them, and refusing would break the passthrough. So
      `screen_capture {format: "png"}` is **still** silently ignored, and that is
      documented rather than fixed. P0.2b remains impossible for the same reason.

      Tests: `python/tests/test_arguments.py` and `node/tests/arguments.test.ts`.
      The two use different similarity metrics (difflib ratio vs Dice over
      bigrams) with cutoffs 0.5 and 0.4 chosen so the **accept/reject decision
      agrees**; the suggestion *wording* may differ on a near-tie, and the tests
      assert the decision rather than the message.
- [ ] **`screen_capture` PNG** (request P0.2b) is **not possible** - it is a
      relayed Studio tool and we cannot add parameters to it.
      `extended_capture` supersedes the need.
- [ ] **Schema bloat is now the bigger half of our footprint.** 7,141 of 9,502
      chars are schema, and `extended_update_script` alone is 1,450, mostly a
      nested `edits.items` block. Needs a decision on whether the `replaceAll`
      alias earns its place.

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

## Housekeeping

- [x] **Server restart verified, not assumed.** The Python changes live once the
      process is restarted - a config change does it, a code fix does not - so
      this needed proof rather than a restart message. The discriminator:
      `extended_manage_instance action=list` returns pids `18588` / `8072` with
      `role: edit` and `attached_to_mesh: true`. Those flow through
      `platform.process_rows()` and `platform.attached_pids()`, both moved today.
      **If the old half of that pair were still live, every pid would read `0`**,
      because the old consumer reads `row["ProcessId"]` and the new normaliser
      emits `row["pid"]`. Correct pids prove both halves reloaded together.
- [x] **macOS work closed out**, including the `47/47` -> `64/66` correction, the
      retracted `os.path.basename` claim, and the retracted `locks.py` URI claim.
- [x] Probe scripts removed from the baseplate: `ServerScriptService.BpTest` and
      `.MPProbe` destroyed, `ServerScriptService` empty again. Scratch folders
      under `PluginGuiService` were already gone (capture self-cleans; the rest
      lived in a play DM that has ended).
- [ ] The throwaway baseplate is at
      `%TEMP%\robloxstudio-mcp-baseplates\Baseplate-*.rbxl`, launched with
      `--task EditFile --localPlaceFile`. Worth deleting when done.
- [x] Node `extended_script_grep` registered, closing a Python/Node parity gap.
- [x] Breaking rename recorded: `extended_write_like_multi_edit` ->
      `extended_write_script`, `extended_update_like_multi_edit` ->
      `extended_update_script`. Any config or prompt naming the old tools
      breaks.

## To do (recorded 2026-09-30, not yet built)

### From the hostile fuzz pass (subagent `ses_f0c4c7a91ffeb84d9`, temp baseplate only)

Target throughout was `1b6a1ed2` (`roblox-studio-zrgmrwpq.rbxl`). Forbidden
targets never touched, no repo files written, target left healthy in Edit mode.
`FuzzBp`, `HelperScript`, `FuzzInserted` remain in the throwaway place - no
delete tool exists (see the last item). Everything here was reported with exact
call and exact reply; what follows is the fix shape, not the report.

- [x] **Recovery text fired in the wrong direction (HIGH) - built 2026-09-30
      as a paired edit.** Reworded neutrally: covers both directions, never
      asserts a session is running, and states `action='stop'` terminates the
      process rather than recommending it. Pinned by `RecoveryWording` /
      `recovery wording` on both sides (absent "while a play session is
      running" and "stop it first"; present "terminates the Studio"; engine
      message stays first).
- [x] **`search_and_read` defaulted the optional filter to a filter (HIGH) -
      fixed 2026-10-01, both sides.** `keywords="Script" if query is None`, so
      omitting `query` returned only scripts named `*Script*`. An audit of
      `ServerScriptService` missed every script not matching that substring,
      **with no indication anything was excluded** - the silent-wrong-answer
      class this project exists to prevent. Now `keywords=query or ""`, which
      is what the underlying tool takes for "no filter" and what the schema
      already promised by making the parameter optional. Pinned both sides
      ("omitting query sends NO name filter", plus a companion test that an
      explicit query still forwards) - the pair matters, because a fix that
      broke filtering would be a new wrong answer in the other direction.
- [ ] **`search_and_read` still validates nothing (MEDIUM, unchanged).**
      `root_path: "game.NoSuchService123"`, `max_results: -5` and a garbage path
      all return `[]` - indistinguishable from an empty service. Sibling
      `extended_script_grep` strictly refuses `context_lines: 99`,
      `max_results: 9999`, `query: ""` and a bad regex. Inconsistent strictness
      across two tools that look interchangeable. Also still unsettled: the
      `game.` prefix (inputs require it, returned paths drop it).
- [ ] **Capture `save_path` failure emits an undeclared code (MEDIUM).**
      My own fix raises `INTERNAL_ERROR`, which is not in `ALL_CODES` - the
      exact defect the closed-set tests exist to prevent, and the harness
      correctly reports it as unknown. Fix: `INVALID_ARGUMENT` (a bad path
      is the caller's argument) or declare the code properly with its
      producibility test. Paired edit, both sides.
- [ ] **The closed-set gate has a reverse gap (mine, found via the above).**
      `test_closed_sets.py` proves every declared code is producible; nothing
      proves every raised code is declared - which is how `INTERNAL_ERROR`
      shipped. Add the reverse test: every `ToolError("CODE", ...)` literal
      in `src/` must be in `ALL_CODES`. Both sides (Node's suite needs the
      same direction).
- [x] **`wait_for` wedge - mitigation built 2026-09-30 as a paired edit,
      without waiting for the safe probe.** Each poll's await is bounded
      (`POLL_TIMEOUT` 30 s); 3 consecutive hangs abort with `TIMEOUT` naming
      the wedge and the Studio restart, instead of wedging the sequential
      server loop until reconnect. Cancelling frees this end only - it cannot
      un-wedge Studio - which is why consecutive hangs, not one, is the abort
      signal. Pinned by `TestHungPolls` / `waitFor hung polls` (fake hung
      studio, no live Studio needed). The residual question stands: whether
      the temp Studio is still wedged from the fuzz probes needs the restart
      + one `nil.foo == 1` probe to settle, and the garbage-condition probe
      stays off-limits until then.
- [ ] **Scope the console one-line claim (LOW-MEDIUM).** The fuzzer measured
      real newlines through the agent caller path, so `rsx-console.md`'s
      absolute prohibition ("match the pattern, do not parse lines") is
      consumer-dependent. Skill edit: state which path the one-line shape
      holds on instead of asserting it universally. No code.
- [x] **Array-escape note is best-effort on collapsing paths (record only).**
      Recorded: the note rides as a second content block; proxies that
      collapse blocks lose it while the payload survives. Payload-first
      ordering stays (callers parse `content[0]`).
- [ ] **No delete tool.** Fuzz scratch cannot be removed from the throwaway
      place. Update 2026-09-30: the cap-raise framing is retired per user
      ruling - new surface is funded by trimming under the
      what/when/why policy, not by raising the cap. The audit freed net -2
      (headroom 8); a ~150-char delete tool still does not fit, so this
      stays open. Harmless while the place is a throwaway.

### Mine, not from the fuzz

- [x] **P1 from the drift request: resolved by rename, not by an edit.** The
      request was right that `AGENTS.md` did not exist here, and right that the
      reference was a pointer at nothing. Resolved at the root: the repo's
      `CLAUDE.md` was **renamed to `AGENTS.md`** (same bytes, hash-verified), so
      `TODO.md:152`'s reference now resolves. The drift request's alternative -
      repoint the sentence at `skills/README.md` - is what the line would need
      if the rename had not happened; the rename is strictly better because an
      agent that opens `AGENTS.md` gets the standing rules *and* the gate list.
      Note for the requester: the claim that this file "carries no routing
      table" was accurate and **still is** - the `rsx-*` vs `rbx-*` table lives
      in `skills/README.md` and the global consumer config, not here. That
      sentence in `TODO.md:152` remains slightly overstated.
- [x] **P0.1 is now FIXED, not documented - and the premise behind deferring it
      was wrong (2026-10-01).** For weeks this sat as *"almost certainly
      relayed-and-therefore-documented, like P0.2b"*. That reasoning was wrong,
      and it was wrong in a way worth keeping: **we are the server the client
      talks to.** Every relayed call arrives here with its `name` and
      `arguments` before it is forwarded. "We cannot add parameters to a
      relayed tool" is true of the tool's implementation and irrelevant to
      whether the **proxy** can look at what the caller asked for.

      So `screen_capture` with no `studio_id` and more than one Studio attached
      is now **refused** with `AMBIGUOUS_STUDIO` and the candidates attached, so
      the caller picks one in the next call instead of receiving a valid image
      of the wrong place. One Studio attached passes through untouched.
      Implemented as `_RELAY_GUARDS` / `RELAY_GUARDS`: a guard runs before
      pass-through and may refuse; it never rewrites the tool.

      Two details that are the actual design:

      * **A guard that cannot run its check passes through and says so**
        (`proxy_note`). If the mesh is unreadable, refusing would turn our own
        transport hiccup into a refusal that reads as a policy decision, sending
        the caller to hunt for ambiguity that may not exist. "We checked" and
        "we could not check" must not look the same.
      * **An explicit `studio_id` is never second-guessed**, however many
        Studios are attached. That argument *is* the disambiguation.

      Pinned by `python/tests/test_relay_guards.py` (8) and four tests in
      `node/tests/improvements.test.ts`. Two of my own test fakes were wrong
      first - they returned bare objects where the code calls `.json()`, so the
      guard reported "could not check" and the refusal test passed for the wrong
      reason. The identical mistake on both sides is the strongest argument yet
      for the mirrored-suite habit.
- [x] **Relay-to-extended steering is now a table, not one hand-written
      annotation (2026-10-01).** Was: a special case for `multi_edit`. Now
      `_STEERS` covers `multi_edit`, `screen_capture`, `script_grep`,
      `get_console_output` and `script_search`, each appended to the *relayed*
      tool's own description. **In the description and not a skill, because the
      model chooses a tool while reading the tool list** - the only always-on
      surface. A skill is opt-in, so a trap living only there has already been
      walked into; that is exactly why `screen_capture`'s wrong-Studio hazard was
      invisible while it lived only in `rsx-capture`.
      The table is generated into `parity/tools.json` and asserted equal on both
      sides by `SteersMatchTheGeneratedContract`, because a one-sided drift is
      a model silently steered to the worse tool forever and **nothing anywhere
      reports it** - every call still succeeds.
      Notes are **appended, never substituted**: the relayed description is
      Studio's and a caller may rely on it.
- [x] **Consumer 13-vs-16 count — resolved 2026-10-01 (the user applied it).**
      The better fix turned out to be deleting the number rather than updating
      it: a hardcoded count drifted once with a "re-measure" instruction
      sitting next to it, so it would drift again. The consumer `AGENTS.md`
      now quotes no count at all, tells the agent to ask the MCP via
      `list_tools()` and treat the live list as the source of truth, and keeps
      the table as an explicitly-lagging index carrying all 16. That ruling was
      then applied repo-side too - see the `AGENTS.md` headroom line, which used
      to quote a stale figure and now points at the generated contract.
- [x] **Parity stated precisely in the README (2026-10-01).** The README said
      "behavior is kept in parity" unqualified, which is not true and never was.
      Now: the **tool surface** is enforced by the generated contract and fails
      either side on drift; **behaviour** is best-effort, with the one real
      difference (Python's log-parsing `studio_id → PID` chain, Node's
      `stop`-refuses) named and linked to `node/src/extended/IDENTITY.md`. The
      repo-side parity boundary stays in `AGENTS.md` and `IDENTITY.md`; the
      README states it in one sentence and points there.
- [x] **`docs/catalog.js` was the stalest doc in the repo - fixed 2026-10-01.**
      It is the natural "all tools" link target and could not be linked as-is:
      - header said "28 relayed Studio tools + **8** extended" while the list
        carried **11** of the 16 (missing `extended_skill`,
        `extended_breakpoints`, `extended_clear_breakpoints`,
        `extended_wait_for`, `extended_manage_instance`);
      - `extended_script_grep` "dedup, and ranking" - nothing ranks, measured
        false, same claim already removed from the tool description;
      - `extended_studio_identity` said `GetDebugId` "does not change when the
        proxy re-mints its studio_id" - **it does change on restart**, which is
        the correction this file records and which `AGENTS.md` already carries.
        That last one was the dangerous kind: it teaches an agent to cache an id
        that is worthless across a restart.

      All three fixed, plus the five missing entries added. The header now
      quotes **no count at all** and explains why - the same ruling the consumer
      `AGENTS.md` got - and names `parity/tools.json` as the enforced source.
      Verified by loading the file and diffing its extended entries against the
      generated contract: 16 entries, none missing, none extra, no malformed
      entry. `extended_list_studios`'s "traceable across restarts" claim went
      with it; entries expire after 30 days now, so nothing is traceable across
      a restart by design. Two more copies of the count were found in
      `docs/content.js` and are queued below.
- [ ] **Pin description raw-identity.** The three pairs just converged to
      byte-identical text, and nothing stops the next one-sided edit from
      re-dividing the budget asymmetrically. A test asserting raw equality
      across the 16 pairs costs nothing and fails symmetrically.
- [x] **Built 2026-09-30:** lock session-GUID parsing (paired edit, newline
      shape + pipe fallback, session GUID now parsed on both sides; wiring it
      into the join deliberately not taken), registry expiry (30-day prune on
      record, both sides), `_RECOVERY` DATAMODAL rewrite (the fuzz HIGH above;
      other codes intentionally NOT widened - no certain text exists for them,
      and a "check that it is enabled" line teaches callers to keep trying
      things). Judgment on `PLACE_NOT_OPEN`: keep with its reason (removing a
      published constant is compat risk with no gain; the unreachable-reason
      test already guards it). **Update 2026-10-01: `PLACE_NOT_OPEN` is now a
      declared ALIAS of `STALE_STUDIO_ID` via `canonical_code()` on both sides —
      a skill reviewer found the alias documented here while absent from the
      tree, which is the "a decision written down is not an implementation"
      failure this file catalogues. See the skill-review section below.**
- [ ] **Still open and unchanged:** the `os.startfile` registry check,
      identity live measurement, PrintWindow (now funded by trimming, not a
      cap raise - but still unbuilt), `ExecuteMultiplayerTestAsync`, the
      Chrrxs playtest surface, `OnStopped` breakpoints. All above in this
      file under their own headings.

## Skill review pass, 2026-10-01 — 8 parallel reviewers, one per skill

Each `skills/*.md` got its own read-only reviewer, cross-checked against
`TODO.md` and the code on both sides. Disjoint files and no writers, so they
could not collide. **The yield justifies the pattern: every reviewer found
something in code or prose written THAT SAME SESSION, and the gates had passed
it.** Independent review without authorship context catches what self-review
structurally cannot.

### The three `REQUEST-*.md` files — this file is now their only record

They were removed from the working tree on 2026-10-01, deliberately: unfinished
work belongs here rather than in a request file that outlives the decision. They
were untracked, so git holds no copy. **Everything still owed from them is a
`- [ ]` in this file**; a future pointer citing a `REQUEST-*.md` means the rows
below.

| item | outcome |
|---|---|
| capture **P0.1** (`screen_capture` wrong Studio) | **fixed** — a proxy guard, both sides (see below) |
| capture **P0.2a** (refuse unknown params) | fixed, extended tools only; relayed tools deliberately exempt |
| capture **P0.2b** (`screen_capture` PNG) | **impossible** — relayed tool, cannot add parameters; `extended_capture` supersedes |
| capture **P2** `save_path` + target metadata | **partly, open** — `extended_capture` returns `studio_id`, but `save_path`'s own result never says *which* capture it wrote |
| capture fonts | rejected — premise disproved, `FontFamily` not creatable |
| Creator Store load | rejected — needs cookie auth; we hold no credentials |
| `get_runtime_logs` | rejected — redundant with `extended_watch_output` |
| return-shapes **P0** (preserve array-ness) | **not fixable here** — Studio's serialiser; documented, plus a reporting note |
| return-shapes **P1** (`print` is shape-preserving) | fixed in `rsx-console` and `rsx-transport` |
| doc-drift **P1** (`AGENTS.md` reference) | resolved by the `CLAUDE.md` -> `AGENTS.md` rename |
| doc-drift **P2** (consumer 13-vs-16) | resolved by the user — count deleted, live list is the source of truth |
| doc-drift **P2** (P0.1 disposition) | resolved — it was fixable, not documented; see below |
| doc-drift **P2** (`browser.preview` fallback) | **declined** by user direction; the skill names a file and stops |

Nothing from the three requests is left unrecorded here. The one still open is
capture **P2**.

### Measured live 2026-10-01: two identical Studio sessions

Launched two Studios on the **same** throwaway place (`make_place`, then
`action=launch` twice), 47 s apart. Both landed: pid **14080** and **14788**,
both `--localPlaceFile` on the identical path. Studio's own lock refused the
second with *"This file is currently in use by another Studio instance"* —
`locks.py` doing its job, and it means the worst case (both mutating one file)
is prevented upstream rather than by this project.

Three discriminators survive an identical place:

| | 14080 | 14788 |
|---|---|---|
| `debug_id` | `0_186406` | `0_186334` |
| session GUID (log) | `749D6509…` | `93072AB8…` |
| log file | `…094625Z_Studio_9A535` | `…094712Z_Studio_6D77A` |
| place path | **identical** | **identical** |

**First live confirmation of the return-primary identity read**: both debug ids
came back through the `return` channel, two calls, **no console write and no
print fallback** — the change made earlier the same day, previously unverified
because only a read-only Studio was attached.

**What does not work — the honest answer to "can you identify both":**

```
studio_id -> pid, LOG CHAIN ONLY (allow_console_write=False)
  A: resolved=False  pid=None  by=None  ambiguous=None
  B: resolved=False  pid=None  by=None  ambiguous=None
```

Neither resolves. With two Studios on one place the name join cannot separate
them, and with console writes disallowed it refuses rather than guessing — so
`action=stop` against either would not work without the token fallback.
**Defect: `ambiguous_reason` is `None`**, so "could not resolve, and here is
why" is indistinguishable from "nothing matched". Same failure class as the
unreported `console_writes` found earlier today.

**Defect: `launch` wrote into a Studio the user ruled read-only.** Two `RBXPID`
join tokens landed in `rbx-re.rbxl`. The launch passed no `studio_id`, so
`_identify_launched` fanned a token to **every** attached Studio to find which
one answered. The ruling was honoured by the agent and **violated by the tool**:
launching one Studio mutated an unrelated one. Cross-contamination, and the
strongest argument yet for the return-primary change — the console is a shared
channel that anything aimed at one Studio can spill into.

### `screen_capture`'s `studio_id` is REQUIRED — and the routing is CORRECT

Measured the guard live with three Studios attached:

| call | result |
|---|---|
| `{}` | `capture_id: Missing key`, **`studio_id: Missing key`** |
| `{capture_id}` only | **`studio_id: Missing key`** |
| `{capture_id, studio_id: A}` | succeeds |
| `{capture_id, studio_id: B}` | succeeds |

**The schema requires `studio_id`.** So the hazard the whole of
`rsx-capture.md` documents — *"takes an **optional** studio_id, and lands on
whichever one the mesh returns first"* — **is not reproducible on the current
build.** Three consequences, stated carefully:

- The **P0.1 guard is defence-in-depth, not the primary defence.** Schema
  validation runs before dispatch, so a call without `studio_id` never reaches
  it over MCP. It still earns its place on the path that does no validation at
  all, and this repo's own `client.py` does none — so that path is real.
- `rsx-capture.md`'s wrong-Studio section describes behaviour that cannot be
  reproduced against the current schema.
- **The originating request's P0.1 premise needs re-checking** before anyone
  treats the guard as the fix for a live hazard.

### SETTLED 2026-10-01: the "captured the wrong Studio" failure was **misreported**

This sat *unexplained* for a day, which is not the same as undetermined — it was
testable, and the test kept being avoided because both Studios were **identical
empty baseplates**, so a capture could not be attributed to either. Fixed by
making them distinguishable.

**Method.** Two Studios, two distinct throwaway places, each flooded with a
different dominant colour so the answer is arithmetic rather than judgemental:

| | Studio A `…15f6f23435d6` (`roblox-studio-vr84c0oz`) | Studio B `…03c1fc6d9d88` (`roblox-studio-3daotsb8`) |
|---|---|---|
| `Lighting.Ambient` + `OutdoorAmbient` | `Color3.fromRGB(255,0,0)` | `Color3.fromRGB(0,0,255)` |
| `ProbePart` | 600x1x600, red | 600x1x600, blue |

Each viewport is then overwhelmingly its own colour, so a capture's mean RGB says
which Studio answered. `rbx-re.rbxl` stayed attached throughout and was never
written to.

**Result — 4 of 4 correct, both tools:**

| call | mean RGB | verdict |
|---|---|---|
| `extended_capture` `studio_id=A` | (216.1, 59.9, 79.4) | **RED = A** ✓ |
| `extended_capture` `studio_id=B` | (42.3, 59.9, 237.3) | **BLUE = B** ✓ |
| `screen_capture` `studio_id=A` | image is red | **RED = A** ✓ |
| `screen_capture` `studio_id=B` | image is blue | **BLUE = B** ✓ |

**Conclusion.** Both tools route by `studio_id` correctly and exactly. The
"silently captures whichever Studio the mesh returns first" mechanism **does not
exist on this build**, for either tool. The `png_bytes` difference noticed
earlier the same day (642,558 vs 642,643 on two identical places) is now
explained: it was *window state* differing, not a misroute — and with colour
injected the attribution is unambiguous.

So the original observation — *"probes ran against one place while every capture
came back as an empty baseplate"* — cannot be this mechanism. Most likely it was
**two empty baseplates being compared**: an empty viewport is what an unpainted
baseplate looks like, so "wrong place" and "nothing set up yet" are the same
picture. That is the lesson worth keeping, and it is the one the
`rsx-capture.md` 3D-content heuristic already encodes for a reader who has had the
good fortune to put something in the viewport first.

**What is still not determined, deliberately.** Whether the *mesh* would misroute
a capture arriving with **no** `studio_id` at all. The only probe for that is an
unaddressed capture or `execute_luau`, and with `rbx-re.rbxl` attached either
could land on a Studio the user has ruled read-only. Not run. Every path
available without violating that ruling either refuses (schema) or routes
correctly (measured above), so the gap is narrow and stated rather than guessed.

**Also unexplained, and separate:** one `extended_capture` `save_path` write to a
directory that demonstrably exists and is writable failed `ENOENT`, then the
identical call succeeded 3 of 3 on immediate retry. Not reproduced. Recorded
because it is the same *shape* as the thing just settled — a plausible-looking
error with nothing behind it — and a reader who hits it deserves to know it was
seen once and not explained.

### Fixed during the pass

- [x] **`launch` no longer writes into Studios the caller never named.** This is
      the defect that violated a user ruling, so it is the one that mattered most.
      - **What it did.** `_identify_launched` could not name the new Studio by
        route 2 (its own log's command line vs. the mesh name) and fell back to
        printing a unique `RBXPID` token from **every** attached Studio until one
        answered. Measured 2026-10-01: launching one Studio put two tokens into
        `rbx-re.rbxl`, which the user had ruled read-only. The tool did not know
        the ruling; it just wrote.
      - **Why the design reached for the console at all.** A *shared* token needs
        a *shared* channel. Asking "who holds token X" cannot be done without
        broadcasting X. So this was not carelessness — it was a consequence of
        the discriminator chosen.
      - **The fix: broadcast nothing.** A pre-spawn snapshot of the mesh's
        `studio_id` set, and the new Studio is the id absent from it. Per-Studio
        identity is unique where a shared token had to be made unique, and it
        needs no channel. This is route 1, ahead of the log read, and it is the
        only route that works when two Studios share a place name — the exact case
        that fell through before.
      - **The snapshot has to predate the spawn**, so the mesh client is now
        connected *before* `Popen`. Taken afterwards it would already contain the
        new Studio and the diff would be empty for the one process it exists to
        find.
      - **`None` is not `set()`.** `_mesh_ids` returns `None` when the mesh could
        not be read, and the diff is skipped entirely in that case. Collapsing
        them would make every attached Studio look newly-arrived and return the
        first row — which is *picking*, the one thing this function refuses.
      - **The spray is now opt-in**, via `allow_console_write` defaulting to
        **False**, and the writes are still counted. It is a last resort the
        caller authorises, not a default. Measured 2026-10-01 was the evidence
        that the default was wrong.
      - **An unidentified launch is no longer reported as a failed launch.** It
        now returns `launched: True, studio_id: None` with a reason, because the
        process *did* start and a caller told "failed" will retry and orphan it.
        That is the same "visible in the error, invisible in the side effect"
        shape the `resolved`/`identified` collision already caused once.
      - **Node needed no mirror, and that is a measured fact, not an omission.**
        Node's `action=launch` calls `launchViaFile` and returns the pid; it never
        had `_identify_launched` and never sprayed. `studio_id -> pid` is
        Python-only by design (`node/src/extended/IDENTITY.md`), and rule 4
        forbids catch-up ports.
      - **`allow_console_write` was deliberately NOT added to the tool schema.**
        Both implementations must carry identical schemas, and on Node the
        parameter would do nothing — a silently ignored parameter, which this
        project records as one of its worst defects because it reports success
        while doing nothing. It stays a library-level parameter.

- [x] **`ambiguous_reason` returned `None` for a genuinely ambiguous case.** Root
      cause, and it is not the one the symptom suggested:
      ```python
      # before: the caller passed the identities it happened to read
      logid.ambiguous_reason(name, [identities[p] for p in candidates if p in identities])
      # and the function decided on len(matches) == 1 -> None
      ```
      Candidates were filtered down to those whose log was readable *before* the
      count, so a candidate with an unreadable log **vanished**. Two candidate
      processes and one readable log reported "not ambiguous" with no reason —
      exactly the `resolved=False, ambiguous=None` measured live.
      - The count that decides is now the **candidate** count. The signature is
        `(mesh_name, candidates, identities)` so the filtered list cannot be
        passed by accident again.
      - An unreadable log is now **named as the reason**, on the grounds that a
        process we could not read is not evidence for the process we did.
      - **`_with_reason` guards every `resolved: False` exit**, so a refusal can
        never ship `error: None`. Applied to both refusal paths (the named route
        and `_resolve_unnamed_studio`). The guard exists because the failure is
        silent by construction: a resolver that correctly declines to guess is
        *right*, so nothing crashes and no test fails unless someone reads the
        field — which is how a `None` reason survived a live measurement of this
        exact path.
      - Python-only, correctly: Node has no `ambiguous_reason`. Two new
        regression tests, one for each half of the conflation.

### Fixed during the pass

- [x] **The disproven `screen_capture` premise was on the paid surface.** The
      finding above says `studio_id` is required; the *steer* still told every
      agent to avoid the tool because it *"takes an optional studio_id, so with
      several Studios attached it silently captures the wrong one"*. That is
      the description block paid on every call of every session, so it was the
      highest-leverage place the false claim lived — and it steered agents away
      from a tool for a hazard that cannot occur. Replaced with the two reasons
      that **are** measured: JPEG smears 1px edges, and `extended_capture` names
      the `studio_id` it captured and can save to a file instead of returning
      megabytes. Both confirmed live today (two captures, 642,558 and 642,643
      bytes, each echoing its own `studio_id`).
      - Propagated to all five places that asserted it, both implementations in
        the same pass: `_STEERS` on both servers, the guard docstring and the
        refusal message on both, `parity/build_contract.py`'s mirror tuple,
        `skills/rsx-capture.md`, both READMEs, and the two tests that asserted
        the old wording.
      - **The guard itself stays.** It is defence-in-depth for the paths that
        validate nothing, and this repo's own `client.py` validates nothing.
        Only the *reason* was wrong. Removing the guard would have traded a
        correct-but-weakly-justified defence for no defence.
      - One test assertion changed from naming the old mechanism
        (`"whichever one the mesh returns first"`) to the surviving one
        (`"no single right answer"`). The test's intent — that the message say
        *why*, not merely "ambiguous" — is preserved, so it still fails if the
        message degrades to a generic refusal.

- [x] **Live verification of three fixes that unit tests could only half
      prove**, using the two throwaway Studios as the bed:
      - `search_and_read`'s `keywords=query or ""` fix, tested **decisively**
        rather than by proxy. Wrote `ServerScriptService.Helper` and
        `ServerScriptService.Zebra` — names containing neither the string
        `Script` nor a substring of the default filter. With no `query`, both
        came back; with `query: "Script"`, `[]` (correct — the filter is on
        *name*, and neither contains it). Under the old code the no-query call
        returned `[]`, silently hiding two scripts that existed. That is the
        HIGH-severity wrong-answer case, now observed on a live Studio rather
        than inferred from a fixture.
      - The array-escape note in `execute_luau_from_file`, both shapes:
        `return {10,20,30}` and `local r = {10,20,30}; return r` both arrived
        as `{"1":10,"3":30,"2":20}` with the note appended and the payload
        **untouched**; `HttpService:JSONEncode` correctly did **not** flag. Also
        confirmed the reviewer's point that the note rides as prose *after* the
        JSON, so parsing the whole result would fail. `Vector2.new(3,4)` came
        through as the single string `"3, 4"`, as documented.
      - `wait_for`'s non-boolean refusal, live, on a real value: a
        `#game:GetDescendants()` probe returned `26137` and was **refused**
        rather than reported satisfied. In Lua `0` and `''` are both truthy, so
        without this the wait would have succeeded instantly and wrongly.

- [x] **`extended_capture`'s `save_path` failure destroyed the record of a good
      capture.** Worse than reported, and the closed-set gate is what found it:
      - The old code raised `INTERNAL_ERROR`, which is **not in `ALL_CODES`**, so
        `ToolError.__init__` raised `AssertionError: unknown error code
        'INTERNAL_ERROR'` *while constructing the error*. The caller received
        neither a code nor a message, and the one sentence saying the pixels
        were fine was gone. An undeclared code is not a wrong code here; it is
        **no** code, plus a traceback-shaped message.
      - Now `INVALID_ARGUMENT` — the refusal is about a value the caller supplied
        and has to change. `UNKNOWN` was rejected as worse: it is `classify`'s
        catch-all, so branching on it catches every unrelated failure too.
      - **The capture record rides in `data`**: `capture_succeeded`, `width`,
        `height`, `raw_bytes`, `png_bytes`, `mime`, `lossless`, `captured_at`,
        `save_path`, `errno`. A caller can tell "it failed" from "the pixels were
        fine and only the write failed" without re-capturing.
      - **Two claims the old message got wrong, now stated:**
        - the pixels are **gone**, not "still retrievable". The scratch module is
          destroyed in a `finally` the moment the base64 is read back, so
          recovery means re-capturing;
        - `png_base64` is **not** a general escape. It rides a return channel
          that truncates at exactly 100,015 characters **with no error**, and a
          1233x754 viewport needs ~4,958,304. Recommending it unqualified walks
          the caller into a second silent truncation, so the message names the
          ~75,000 `png_bytes` ceiling under which it is safe.
      - `errno` is a **name** (`ENOENT`/`ENOSPC`), not a number or `strerror`:
        numbers differ between Python and Node for the same failure, and the
        name is the one spelling both sides agree on, which makes it a branch
        key rather than something to string-match.
      - Writes go through a sibling temp file and one `os.replace`, so a write
        that dies partway cannot truncate a previous good capture. The real
        failure — full disk, quota — cannot be produced on demand, so that test
        patches `os.fdopen` with a double that keeps the real contract.
      - **The error-parity contract got a new site, and the gate caught the
        entry being added without the probe**: *"A contract entry nothing
        exercises is a comment that looks like a test."* Fixing that ran into the
        recorded lesson twice:
        - the probe returned `UNKNOWN` because the Studio double was wrong;
        - fixing it exposed that **every content-based discriminator between the
          capture call and the discard call matches both** — `RBXCapture`,
          `FindFirstChild` and `Destroy()` each appear in *both* snippets, and the
          header is generated by Studio at runtime so it is not in the source at
          all. The double now counts `execute_luau` calls, because the sequence
          (capture, read, discard) is fixed and content-sniffing is not. The
          Node double needed the same fix, independently, which is the second
          occurrence of the identical fake being wrong on both sides.
      - **The two implementations had drifted in coverage, not in code.** Node's
        implementation was correct; its *suite* had one parity site and no
        behavioural tests, while Python had fifteen. Node now mirrors it: declared
        code, surviving record, prose, the two corrected claims, errno shape,
        JSON-serialisability, no scratch left behind, and a direct test that the
        recovery advice the message gives actually works.
      - One Python assertion was wrong, not the code: it compared the raw
        `save_path` against a message that renders arguments with `describe()`.
        That JSON rendering is **load-bearing** — it is what makes both servers
        emit byte-identical error strings, pinned by `parity/errors.json` — so
        the test was corrected to compare the described form rather than
        weakening the rendering.

- [x] **`rsx-playtest.md` told the reader to walk into its own documented
      hazard.** The `task.spawn` warning was already correct; the real defect was
      the recipe underneath it:
      - The recipe says open the test with `ExecuteMultiplayerTestAsync(n, args)`,
        *called directly*. That call is recorded as **blocking past the 120 s
        tool-call timeout** — never observed to return. The same file says a
        timed-out test call is the exact condition that wedges the test service,
        and only a Studio restart clears it.
      - So the documented recipe and the documented hazard point the same way,
        and **there is no measured safe variant**: the shape that avoids the
        wedge is "call it directly", and so is the shape that risks it. That
        tension is now stated rather than papered over, with what it does and
        does not license, and `extended_run_tests` named as the route to prefer
        when the argument list is not the point.
      - Also fixed the `Reproduduced` typo in the same section.

- [x] **A parity gap no gate covers: `extended_capture`'s result keys differed
      between the two servers.** Found while mirroring the capture tests, not by
      a gate.
      - `CaptureResult` is camelCase internally (`rawBytes`, `savePath`,
        `capturedAt`) while Python's result dict is snake_case, and
        `callCapture` shipped it with `JSON.stringify({ ...result, studio_id })`.
        So the Node server answered `extended_capture` with `rawBytes` and
        `savePath` where Python answered `raw_bytes` and `save_path`.
      - A caller reading `save_path` got **`undefined` and no error** — the
        silent-wrong-answer shape, and the worst kind here because the value
        looks absent rather than wrong. The dispatch test caught it; the parity
        gate did not, because **`test_parity` compares the tool *surface*, not
        result payloads.** That is a real hole in the invariant and it is
        recorded here rather than left as a surprise.
      - Now spelled out field by field on the Node side, with `save_path` and
        `png_base64` present only when there is something to report, matching
        Python's omit-rather-than-null behaviour.
      - **Nothing asserts result-key parity between the servers.** A caller
        cannot tell which server answered from the payload, and both are
        documented as returning the same fields. The cheapest honest guard is a
        test that captures one real result per implementation and compares key
        sets — but the two servers cannot both be driven in one test run, so this
        is a known gap, not a solved one.

- [x] **Test pollution found by running the suite, not by reading it.** The
      half-written-file capture test needs `vi.resetModules()` and a mocked
      `node:fs/promises`. Run in the shared file it left a **second copy of
      `errors.ts` alive**, so every later dispatch test's `ToolError` failed
      `instanceof` against the statically imported one and `classify` fell
      through to `UNKNOWN`. Every dispatch test failed for that reason and no
      other. Moved to `tests/capture-partial-write.test.ts`; vitest gives each
      file a fresh registry, which is the isolation the test needed.
      - A second, independent trap in the same test: `writeAtomic` does
        `const { open } = await import("node:fs/promises")` *inside the function*
        and destructures at call time, so `vi.spyOn(fsp, "open")` on the
        `fs.promises` object never reaches the binding it calls. The first
        version spied on the object, the write succeeded, and the test failed
        with *"expected the write to fail"* — which reads like the code was fine.
        Mocking the **module** is what works.

- [x] **The identity read paid a console round trip per Studio for nothing.**
      `registry.py` justified the console channel with *"inline `execute_luau`
      drops a script's return value"* — a belief `TODO.md:192` had already
      recorded as **measured false** for the current build. The refutation
      outlived the call site that depended on it. Now **`return` is primary and
      `print` is the fallback** (user ruling), both sides. What that buys beyond
      halving the calls:
      - **no user-visible console write** on the common path — this project has
        otherwise gone out of its way to avoid those (internal-only flag,
        `allow_console_write` gate, `console_writes` accounting), and this path
        wrote unconditionally;
      - **no dependence on the console sliding window**, which is the fragile
        channel here: `TODO.md:1388` records a Studio whose console showed **0**
        `MCPID` lines while its log held one. An identity read depending on the
        console is an identity read depending on the thing already measured to
        be unreliable;
      - **no `MCPID` tag left in the buffer** for the next reader to misparse.

      The return sends a **concatenated string, not a table**, deliberately: a
      table arrives with integer keys stringified (`{"1":…}`), and a string has
      no keys to lose. Payload is bytes against a 100,015-char limit.

      `print` is kept because it is the only channel known to work on **every**
      build seen — so a regression in the return channel costs a console write
      rather than an identity. Both channels share one `_parse_identity` /
      `parseIdentity`, because the only difference is how the text arrives and
      two copies would be two things to keep in step.

      **Not confirmed live**: only `rbx-re.rbxl` was attached, which is
      read-only. The primary path rests on the recorded measurement, not a fresh
      one; the fallback is the original behaviour. Re-measure when a writable
      Studio is attached.

- [x] **Other fixes during the pass:**

- [x] **The P0.1 `screen_capture` guard was DEAD CODE (highest-value find).**
      `guard(client, arguments)` shipped without `await`: the coroutine was
      created and dropped, `except ToolError` was unreachable,
      `_RELAY_GUARD_NOTES` never populated, and the wrong-Studio capture
      continued — on the **Python** server, the one actually configured.
      **Both gates passed it.** Two causes, both now fixed:
      1. `test_relay_guards.py` called `_guard_screen_capture` *directly*,
         bypassing dispatch entirely. A unit test that skips the call path
         cannot see this class of bug.
      2. `_RELAY_GUARDS` was typed `Dict[str, Any]`, which erases the coroutine
         type, so the missing `await` was legal to pyright. Now
         `Dict[str, Callable[..., Awaitable[None]]]`.
- [x] **`PLACE_NOT_OPEN` documented as shipped but absent from the tree** —
      caught by the `rsx-targeting` reviewer. Now a real alias via
      `canonical_code()` on both sides, plus `CODE_ALIASES`. The finding is the
      point: *a decision written down is not an implementation.*
- [x] **Node's `capture.ts` never received the base64 fix Python got.** It still
      carried the **retracted** claim (`EncodingService:Base64Encode ... rejects
      a buffer`) above a 26-line hand-rolled loop — the disproved note made
      load-bearing, costing 70x (527 ms vs 7.5 ms). Also `DEFAULT_CHUNK`
      120,000 → `MAX_BASE64_CHARS`. Found independently by two reviewers.
- [x] **The console one-line claim, scoped** (the queued LOW-MEDIUM item). The
      code proves it better than a measurement: `_ConsoleWatch.poll` calls
      `splitlines()`, so the codebase itself assumes real newlines. Advice kept
      unconditional ("match the pattern"); only the *reason* is now scoped.
- [x] **`rsx-transport.md` asserted the retracted base64 claim**, steering agents
      to a 527 ms loop that `rsx-discovery.md` and `capture.py` both contradict.
- [x] `rsx-console.md`: unbound `Players` in the attributes snippet (threw on
      paste), and the missing first-poll-is-last-20-lines caveat.
- [x] `skills/README.md`: the 2,700 cap figure (now 3,200 / 2,897 spent).

### Open — skills (each cites the reviewer)

- [ ] **`skills/README.md` cost figures are wrong by ~2x.** Claims ~27,000 chars
      of skill bodies; measured **52,982**. Index share claimed 7%, actual
      **3.6%**. Nothing pins it — the only gate is `index < bodies//4`, which
      passes at 3.6%, 7% and 25%. **Fix: delete the numbers** and cite the two
      tests, per the project's own policy on quoted counts.
- [ ] **`skills/README.md` still states "never raise the cap"** as policy. The cap
      was raised to 3,200, so the file asserts the opposite of what was done.
- [ ] **`skills/README.md` routing gaps:** missing the *reachability exception*
      (a skill may document something this transport cannot reach — `rbx-debug`
      prescribes `OnStopped`/`GetVariables`, and `OnStopped` does not propagate
      edit → play); missing the "read it, don't build it" rule that the global
      `AGENTS.md` now carries; missing "neither tool serves the other's names";
      missing the registry-is-host-side fact; and it is `SKILL_IGNORED`, so it is
      NOT the file `extended_skill` serves — an agent will treat it as the index.
- [ ] **`rsx-playtest.md` — the file's only recipe is the call it says wedges.**
      Steps 2-4 instruct `ExecuteMultiplayerTestAsync` called directly, while the
      same file records that it blocks past 120 s and that the timeout is the
      exact condition that wedges the test service permanently. It also never
      mentions the confirmed `-task StartServer` route. **An agent following it
      ends up needing a Studio restart.**
- [ ] **`rsx-playtest.md` — `extended_run_tests` overstates what it does.** "Starts
      the engine's runner" is wrong: it calls `start_play()` (the local
      single-player session) and never touches `StudioTestService`. `passed` is
      `not errors and not missing` over a console substring scan, and
      `test_paths` is optional — **so a place with no tests returns
      `passed: true`.** "Green-check only" in the tool description is right;
      "did the suite go green" is not.
- [ ] **`rsx-playtest.md` — no `extended_wait_for` guidance**, so "poll until the
      count reaches N" names no mechanism. Two behaviours an agent needs are
      undocumented: a non-boolean return is `INVALID_ARGUMENT` (not a pass), and
      three hung polls abort `TIMEOUT` naming a restart.
- [ ] **`rsx-playtest.md` — "all confirmed" is not true for three items:**
      `ExecutePlayModeAsync` appears nowhere else in the repo (its row's own
      class of claim, but unsourced); the `AddPlayers` 1–8 bound has no
      provenance; and the `EndTest` section is headed "Reproduced" with no
      `TODO.md` entry — and `TODO.md:1687` says the StartServer route works for
      `AddPlayers` **but not** for ending a test with a value, a combination the
      file presents as one loop.
- [ ] **`rsx-playtest.md` — `IsRunning() and IsServer()` is called a confirmed
      gate** but the file's own evidence shows both true at the moment of
      refusal. As a preflight checklist it teaches a diagnostic that always
      passes. The real gate is whether the session can host players.
- [ ] **`rsx-playtest.md` — "no way to address the second or third client" is
      overstated.** Each `StartClient` is a separate process with its own mesh
      row and `studio_id`; the real gap is a convenience mapping, not
      reachability.
- [ ] **`rsx-targeting.md` — repeats a retraction TODO made twice:** "the URI
      route does not join". `logid.py` falls back to the place id and narrows by
      the `AutoRecovery_N` counter. **This one will re-stale from
      `logid.py`'s own module docstring**, which carries the same sentence — fix
      both or neither.
- [ ] **`rsx-targeting.md` — stale numbers:** "47 of 47" logs carry the PID line
      (corrected to **64/66**, and the 2 exceptions are what `no_pid_reason`
      exists to explain); "64 KB prefix" (raised to **256 KB**); "3,809 bytes in
      every log measured" (largest of 43). It also never mentions
      `no_pid_reason`, so "no PID line" reads as "process is gone".
- [ ] **`rsx-targeting.md` — the peer-implementation paragraph describes code
      that does not exist** (`RunService:IsEdit()` appears nowhere in
      `node/src`) and names the wrong gap: the real one is Node's inability to
      resolve `studio_id → PID`, per `IDENTITY.md`.
- [ ] **`rsx-targeting.md` — missing `action=list`**, including that it returns
      `processes` and `mesh` **deliberately unjoined**; `extended_list_studios`
      vs `action=list` undifferentiated; no error code named (so a caller
      branching on `PLACE_NOT_OPEN` writes a dead branch); registry expiry and
      what `registered: true` actually means; and the macOS `lsof` counterpart
      flagged as unproven.
- [ ] **`rsx-capture.md` — the two "fixed" performance facts are Python-only.**
      The skill's own "identical pixels, either is safe" is what would make an
      agent swap implementations for speed. Now true on both (see fixed above).
- [ ] **`rsx-capture.md` — missing:** `save_path` failure mode (and `TODO.md`'s
      description of it is wrong — see below); that `extended_capture` **reports
      the `studio_id` it captured**, the cheap post-hoc check for the hazard the
      file spends 17 lines on; that PrintWindow is **not implemented here**; and
      that the chunk size is **not a caller knob** (no such parameter; unknown
      parameters are refused).
- [ ] **`rsx-breakpoints.md` — the `added` / `removed` return keys are
      undocumented**, which makes its own "if you are not sure, set it" advice
      wrong in exactly that case: if one is set, the call removes it and hits go
      to zero, which reads as "that line never ran".
- [ ] **`rsx-breakpoints.md` — the registry can desync** (it mirrors, never reads
      engine state), so a Studio-side breakpoint is invisible; "no enum that can
      disagree with the actual state" overstates.
- [ ] **`rsx-breakpoints.md` — `log_expression` failing is advice, not
      enforcement** (`breakpoints.py` only defaults it; no validation), and
      `log_expression: ""` is silently replaced rather than honoured.
- [ ] **`rsx-breakpoints.md` — repeats the unscoped one-line console claim**
      (fixed in `rsx-console.md`; this file still says it universally).
- [ ] **`rsx-discovery.md` — `RunService.IsEdit()` is described backwards:**
      "a parse error, not a nil call" is wrong twice. It is valid Luau; the
      engine says `attempt to call a nil value (field 'IsEdit')`, which the
      project's own classifier maps to `LUA_ERROR` — so the distinction the
      skill draws is not one the codebase makes.
- [ ] **`rsx-discovery.md` — its own "RIGHT" snippet throws** on the case two
      sections later documents: `ipairs(GetMethodsOfClass("StudioService"))`
      where the function returns nil. Needs `or {}`, which is the guard the same
      file teaches later and never connects.
- [ ] **`rsx-discovery.md` — a "useful services" table mixes evidence classes.**
      `ExecuteMultiplayerTestAsync` is documented-but-**unverified** (calling it
      is what wedges the test service) yet sits in a table whose neighbours are
      confirmed; `GetTestArgs` has no source anywhere.
- [ ] **`rsx-discovery.md` — missing:** the one measured capability gate
      (`game.UniqueId` → "The current thread cannot read 'UniqueId'"); the
      complete no-process-id ruling, where `settings()` currently sits under a
      reflection heading rather than the capability story; array/`Vector2` return
      shapes, in the one skill that pushes APIs returning arrays; and any
      `see also` boundary (its telemetry section is near-verbatim duplicated
      from `rsx-targeting`).
- [ ] **`rsx-transport.md` — still stale after the base64 fix:** "append in
      slices of roughly 120,000" under the ceiling heading (it is neither current
      nor a capacity strategy — splitting never raises the ceiling); missing
      `script_read`'s `     1->` line prefixes, which **corrupt base64 if left
      in** and produce a valid-looking wrong image; missing the wait-for hung-poll
      abort; missing that the array note rides a **second content block** that
      our own `text()` collapses, so the note appends prose after the JSON and a
      caller that trims to parse discards it; a dangling pointer to the deleted
      `REQUEST-luau-return-shapes.md`; a heading that inverts its own body; and
      an unsourced `declare`-block claim.
- [ ] **Cross-file:** `README.md:168` carries the same pre-guard wrong-Studio
      wording. `extended_server.py` and `breakpoints.py` still assert the
      one-line console shape in comments/docstrings. `python/README.md:194` still
      says base64 is computed in Luau. `AGENTS.md:1` is still titled
      `# Claude.md`. Fixing one side of each leaves the falsehood live elsewhere.

### Follow-ups the pass exposed about the pass itself

- [ ] **Gate gap: a unit test that calls the function instead of the dispatch
      path cannot see a missing `await`.** Worth one test per guard that drives
      the real JSON-RPC entry point. The Python guard now has one; the Node side
      has four.
- [ ] **Two of my own test fakes were wrong in the same way on both sides** —
      bare objects where the code calls `.json()`. The guard reported "could not
      check" and the refusal test passed **for the wrong reason**. Mirrored
      suites caught it, which is the strongest argument yet for the habit.
- [ ] **`skills/` is untracked and the repo has no CI over it.** Eight reviewers
      found ~25 issues by hand; nothing prevents them returning.
- [ ] **Unsourced figures across the skills** (640 classes, 50 methods on
      `StudioTestService`, `GetClass` returns nil, 0.002 px JPEG localisation,
      the ViewportFrame readback claim, ~139-char console sample). Each reads as
      measured against these files' own provenance standard. Either cite or
      label — "wrong notes that survive because nothing contradicts them".

### Two code defects the reviewers found that are NOT yet fixed

- [ ] **`extended_capture`'s `save_path` failure raises an undeclared code.**
      `INTERNAL_ERROR` is not in `ALL_CODES`, and `ToolError.__init__` asserts
      before storing — so the message saying *the capture succeeded and only the
      write failed* is **discarded**, and the caller sees
      `UNKNOWN: unknown error code 'INTERNAL_ERROR'`. This is the queued MEDIUM
      item, and the review establishes that `TODO.md`'s description of the fix
      describes behaviour that does not exist.
- [ ] **`rsx-playtest.md`'s "confirmed" claims need the same treatment** — three
      unsourced, listed above.

## Built 2026-09-30, per user rulings

Four rulings, applied in order. No live Studio calls in any of it - the temp
Studio may still be wedged from the fuzz probes, `rbx-re.rbxl` is read-only,
and nothing here needed a Studio to prove.

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
