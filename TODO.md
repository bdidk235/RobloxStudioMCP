# TODO — open work only

> **Every open item in this repository, and the claims that have been
> withdrawn.** Nothing else. Closed research lives in
> [`docs/EVIDENCE.md`](docs/EVIDENCE.md) and in git history; this file used
> to hold all of it, and reaching the work meant crossing 1,300 lines of
> findings that were already settled.
>
> **61 open items.** `- [ ]` is work someone could pick up today.
> Withdrawals are kept because the value of a ledger is that a retraction
> stays visible — an item struck from this list tends to be re-derived.

> **Provenance tags.** `MEASURED` / `DOCUMENTED` were executed or read and
> the output quoted. `INFERRED` is reasoning from a source not read.
> `UNVERIFIED` is a known unknown.
>
> **Standing constraint on live Studio work** — an instance the agent did not
> launch is off limits, reads included, unless a user puts it in scope. It is
> mirrored in `AGENTS.md` rule 1 and both must change together.

## Open items

### Open items: skills and guards, reviewed 2026-10-01

- [ ] **`skills/README.md` cost figures are wrong by ~2x.** Claims ~27,000 chars of skill bodies; measured **52,982**. Index share claimed 7%, actual **3.6%**. Nothing pins it — the only gate is `index < bodies//4`, which passes at 3.6%, 7% and 25%. **Fix: delete the numbers** and cite the two tests, per the project's own policy on quoted counts.

- [ ] **`skills/README.md` still states "never raise the cap"** as policy. The cap was raised to 3,200, so the file asserts the opposite of what was done.

- [ ] **`skills/README.md` routing gaps:** missing the *reachability exception* (a skill may document something this transport cannot reach — `rbx-debug` prescribes `OnStopped`/`GetVariables`, and `OnStopped` does not propagate edit → play); missing the "read it, don't build it" rule that the global `AGENTS.md` now carries; missing "neither tool serves the other's names"; missing the registry-is-host-side fact; and it is `SKILL_IGNORED`, so it is NOT the file `extended_skill` serves — an agent will treat it as the index.

- [x] **`rsx-playtest.md` — the file's only recipe is the call it says wedges.** Steps 2-4 instruct `ExecuteMultiplayerTestAsync` called directly, while the same file records that it blocks past 120 s and that the timeout is the exact condition that wedges the test service permanently. It also never mentions the confirmed `-task StartServer` route. **An agent following it ends up needing a Studio restart.**

  > **Resolved 2026-10-03** - **already fixed** before this pass (`rsx-playtest.md:226,236`); the tracker was stale, not the file.

- [x] **`rsx-playtest.md` — `extended_run_tests` overstates what it does.** "Starts the engine's runner" is wrong: it calls `start_play()` (the local single-player session) and never touches `StudioTestService`. `passed` is `not errors and not missing` over a console substring scan, and `test_paths` is optional — **so a place with no tests returns `passed: true`.** "Green-check only" in the tool description is right; "did the suite go green" is not.

  > **Resolved 2026-10-03** - **already fixed** - the description now reads "Play the place; console as {passed, console_lines, errors}".

- [x] **`rsx-playtest.md` — no `extended_wait_for` guidance**, so "poll until the count reaches N" names no mechanism. Two behaviours an agent needs are undocumented: a non-boolean return is `INVALID_ARGUMENT` (not a pass), and three hung polls abort `TIMEOUT` naming a restart.

  > **Resolved 2026-10-03** - **already fixed** - there is now a section on waiting plus a poll snippet.

- [x] **`rsx-playtest.md` — "all confirmed" is not true for three items:** `ExecutePlayModeAsync` appears nowhere else in the repo (its row's own class of claim, but unsourced); the `AddPlayers` 1–8 bound has no provenance; and the `EndTest` section is headed "Reproduced" with no `TODO.md` entry — and `TODO.md:1687` says the StartServer route works for `AddPlayers` **but not** for ending a test with a value, a combination the file presents as one loop.

  > **Resolved 2026-10-03** - **already fixed**; one residue survives: `ExecutePlayModeAsync` still appears at line 29 only, now marked unsourced.

- [x] **`rsx-playtest.md` — `IsRunning() and IsServer()` is called a confirmed gate** but the file's own evidence shows both true at the moment of refusal. As a preflight checklist it teaches a diagnostic that always passes. The real gate is whether the session can host players.

  > **Resolved 2026-10-03** - **already fixed** - the file now says the opposite, that it is documented and not the real gate.

- [x] **`rsx-playtest.md` — "no way to address the second or third client" is overstated.** Each `StartClient` is a separate process with its own mesh row and `studio_id`; the real gap is a convenience mapping, not reachability.

  > **Resolved 2026-10-03** - **already fixed** - reframed as a lookup gap, not a reachability limit.

- [x] **`rsx-targeting.md` — repeats a retraction TODO made twice:** "the URI route does not join". `logid.py` falls back to the place id and narrows by the `AutoRecovery_N` counter. **This one will re-stale from `logid.py`'s own module docstring**, which carries the same sentence — fix both or neither.

  > **Resolved 2026-10-03** - **fixed here, in both places** - `rsx-targeting.md` and the `logid.py:34-38` docstring that carried the same claim. The docstring also held a third stale figure (44/44), corrected in the same pass.

- [x] **`rsx-targeting.md` — stale numbers:** "47 of 47" logs carry the PID line (corrected to **64/66**, and the 2 exceptions are what `no_pid_reason` exists to explain); "64 KB prefix" (raised to **256 KB**); "3,809 bytes in every log measured" (largest of 43). It also never mentions `no_pid_reason`, so "no PID line" reads as "process is gone".

  > **Resolved 2026-10-03** - **fixed here** - now 64 of 66 with both exceptions named and `no_pid_reason` cited.

- [x] **`rsx-targeting.md` — the peer-implementation paragraph describes code that does not exist** (`RunService:IsEdit()` appears nowhere in `node/src`) and names the wrong gap: the real one is Node's inability to resolve `studio_id → PID`, per `IDENTITY.md`.

  > **Resolved 2026-10-03** - **fixed here** - replaced with the real gap (`logid.py` unported, per `IDENTITY.md`).

- [x] **`rsx-targeting.md` — missing `action=list`**, including that it returns `processes` and `mesh` **deliberately unjoined**; `extended_list_studios` vs `action=list` undifferentiated; no error code named (so a caller branching on `PLACE_NOT_OPEN` writes a dead branch); registry expiry and what `registered: true` actually means; and the macOS `lsof` counterpart flagged as unproven.

  > **Resolved 2026-10-03** - **partly fixed** - the action, its deliberately unjoined `processes`/`mesh`, and the name-collision caveat are now documented. Still open: no error code named (`PLACE_NOT_OPEN`), registry expiry, and the unproven macOS `lsof` counterpart.

- [x] **`rsx-capture.md` — the two "fixed" performance facts are Python-only.** The skill's own "identical pixels, either is safe" is what would make an agent swap implementations for speed. Now true on both (see fixed above).

  > **Resolved 2026-10-03** - **fixed here** - the two measurement sets are now attributed separately; `capture.ts` carries its own.

- [x] **`rsx-capture.md` — missing:** `save_path` failure mode (and `TODO.md`'s description of it is wrong — see below); that `extended_capture` **reports the `studio_id` it captured**, the cheap post-hoc check for the hazard the file spends 17 lines on; that PrintWindow is **not implemented here**; and that the chunk size is **not a caller knob** (no such parameter; unknown parameters are refused).

- [x] **`rsx-breakpoints.md` — the `added` / `removed` return keys are undocumented**, which makes its own "if you are not sure, set it" advice wrong in exactly that case: if one is set, the call removes it and hits go to zero, which reads as "that line never ran".

  > **Resolved 2026-10-03** - **fixed here** - documented as mutually exclusive, which is what makes the old "set it and check for hits" advice wrong. Noted, not fixed: `hit_prefix` and `how_to_read_hits` are also undocumented.

- [x] **`rsx-breakpoints.md` — the registry can desync** (it mirrors, never reads engine state), so a Studio-side breakpoint is invisible; "no enum that can disagree with the actual state" overstates.

  > **Resolved 2026-10-03** - **fixed here** - the desync consequence is now stated where the mirroring is described.

- [x] **`rsx-breakpoints.md` — `log_expression` failing is advice, not enforcement** (`breakpoints.py` only defaults it; no validation), and `log_expression: ""` is silently replaced rather than honoured.

  > **Resolved 2026-10-03** - **fixed here** - heading retitled; the code's default-and-pass-through behaviour is stated. `log_expression: ""` being replaced rather than honoured is now covered by that same sentence.

- [ ] **`rsx-breakpoints.md` — repeats the unscoped one-line console claim** (fixed in `rsx-console.md`; this file still says it universally).

- [x] **`rsx-discovery.md` — `RunService.IsEdit()` is described backwards:** "a parse error, not a nil call" is wrong twice. It is valid Luau; the engine says `attempt to call a nil value (field 'IsEdit')`, which the project's own classifier maps to `LUA_ERROR` — so the distinction the skill draws is not one the codebase makes.

  > **Resolved 2026-10-03** - **fixed here** - `errors.py` maps `attempt to call` and the parse failures to one `LUA_ERROR`, so there is no distinction to act on.

- [x] **`rsx-discovery.md` — its own "RIGHT" snippet throws** on the case two sections later documents: `ipairs(GetMethodsOfClass("StudioService"))` where the function returns nil. Needs `or {}`, which is the guard the same file teaches later and never connects.

  > **Resolved 2026-10-03** - **fixed here** - the snippet now `pcall`s the call and iterates `ok and methods or {}`.

- [ ] **`rsx-discovery.md` — a "useful services" table mixes evidence classes.** `ExecuteMultiplayerTestAsync` is documented-but-**unverified** (calling it is what wedges the test service) yet sits in a table whose neighbours are confirmed; `GetTestArgs` has no source anywhere.

- [ ] **`rsx-discovery.md` — missing:** the one measured capability gate (`game.UniqueId` → "The current thread cannot read 'UniqueId'"); the complete no-process-id ruling, where `settings()` currently sits under a reflection heading rather than the capability story; array/`Vector2` return shapes, in the one skill that pushes APIs returning arrays; and any `see also` boundary (its telemetry section is near-verbatim duplicated from `rsx-targeting`).

- [x] **`rsx-transport.md` — still stale after the base64 fix:** "append in slices of roughly 120,000" under the ceiling heading (it is neither current nor a capacity strategy — splitting never raises the ceiling); missing `script_read`'s ` 1->` line prefixes, which **corrupt base64 if left in** and produce a valid-looking wrong image; missing the wait-for hung-poll abort; missing that the array note rides a **second content block** that our own `text()` collapses, so the note appends prose after the JSON and a caller that trims to parse discards it; a dangling pointer to the deleted

  > **Resolved 2026-10-03** - **fixed here** - "slices of roughly 120,000" is gone; the slice is documented as a speed knob with the measurement that shows why.

- [ ] **Cross-file:** `README.md:168` carries the same pre-guard wrong-Studio wording. `extended_server.py` and `breakpoints.py` still assert the one-line console shape in comments/docstrings. `python/README.md:194` still says base64 is computed in Luau. `AGENTS.md:1` is still titled `# Claude.md`. Fixing one side of each leaves the falsehood live elsewhere.

- [ ] **Gate gap: a unit test that calls the function instead of the dispatch path cannot see a missing `await`.** Worth one test per guard that drives the real JSON-RPC entry point. The Python guard now has one; the Node side has four.

- [x] **RETRACTED 2026-10-03 - moot: the Python guard has its dispatch-path test and the Node side is gone.** ~~Two of my own test fakes were wrong in the same way on both sides** - bare objects where the code calls `.json()`. The guard reported "could not check" and the refusal test passed **for the wrong reason**. Mirrored suites caught it, which is the strongest argument yet for the habit.~~

- [x] ~~**`skills/` is untracked and the repo has no CI over it.**~~ **Withdrawn 2026-10-03 - false on both counts.** `git ls-files skills` returns 8 files, none ignored; `ci.yml:35` runs `python -m pytest tests` on a two-OS matrix and `test_skills.py` loads the real directory. The real gap is narrower and recorded separately.

- [ ] **No gate reads a claim inside a skill body - and the obvious one provably cannot work.** `test_skills.py` asserts only structure (name prefix, frontmatter, index ratio); not one test looks at a word of `rsx-capture.md`'s text. The natural gate - figures in skills must appear in source - was measured against the six defects fixed on 2026-10-03 and **catches 0**: two have no figure at all, and the rest (`47 of 47`, `44 of 44`, `120,000`) had figures *present* in source, because the skill and the docstring agreed with each other and were both wrong. The defect is a shared false premise, and cross-file consistency checks reward exactly that. See `docs/EVIDENCE.md`. Cheap floor that does work, over only **4 `file:line` refs** in 7 files: a ref must name a file that exists and a line in range (negative control confirmed). Not worth a test file at that count - **revisit if the refs grow.**

- [ ] **`TODO.md`'s description of the `save_path` failure is wrong** - it says the message survives and names the recovery; the message is discarded (see the `INTERNAL_ERROR` item above). `skills/rsx-capture.md` was corrected on 2026-10-03 to describe what the code does, so this file now contradicts a skill it should agree with.

- [ ] **Unsourced figures across the skills** (640 classes, 50 methods on `StudioTestService`, `GetClass` returns nil, 0.002 px JPEG localisation, the ViewportFrame readback claim, ~139-char console sample). Each reads as measured against these files' own provenance standard. Either cite or label — "wrong notes that survive because nothing contradicts them".

- [ ] **`extended_capture`'s `save_path` failure raises an undeclared code.** `INTERNAL_ERROR` is not in `ALL_CODES`, and `ToolError.__init__` asserts before storing — so the message saying *the capture succeeded and only the write failed* is **discarded**, and the caller sees `UNKNOWN: unknown error code 'INTERNAL_ERROR'`. This is the queued MEDIUM item, and the review establishes that `TODO.md`'s description of the fix describes behaviour that does not exist.

- [ ] **`rsx-playtest.md`'s "confirmed" claims need the same treatment** — three unsourced, listed above.

- [ ] **`rsx-breakpoints.md` returns two more undocumented keys** — `hit_prefix` and `how_to_read_hits` are in the result at `extended_server.py:1012-1022` and appear nowhere in the skill. Found 2026-10-03 while fixing the `added`/`removed` item; out of scope there and not yet written up.

### Remaining

- [x] **RETRACTED 2026-10-03 — moot: the repo is Python-only and the Node client is gone, so there is no second implementation left to wire up.** ~~The one real difference: identity resolution — and the code is already written.** `logid.ts` (86 KB) and `locks.ts` are ported and tested; `logid.test.ts` has 60-odd references. What is missing is the **call site**: `instance.ts` never imports `logid.ts`, so `listStudioProcesses` derives role and place from the command line (`processRows`, `roleFromCommandLine`, `placeFromCommandLine`) and `stopProcess(pid)` has no PID to be given. With two Studios on one place — the current state on this machine, both named `Place1` — Node **cannot say which is which**, so `action=stop` refuses rather than terminating the wrong process. **Closing it is now a wiring change, not a port: import `logid.ts` where `processRows` supplies role and place, and decide what a log with no PID does — it must be an error, never a silent "no identity".** Corrected 2026-10-03; this item previously said the module was unported and put the figure at 1,012 lines, which was wrong.~~

- [ ] **The budget is still the binding constraint**: 2,897 of 3,200 (live figures in `contract/tools.json`). A 17th tool is a decision for the user, not an accident.

- [ ] **The description budget is effectively full, 16 tools.** Re-measured 2026-09-30 after the Block 1 and Block 2 edits, which moved it. This is a hard blocker on adding a 17th tool and it is worth knowing *before* designing one:

- [ ] **The PrintWindow *implementation* is not in the repo - only its measurement is.** Worth being precise about, because the 48-205 ms figure reads as though the code exists. It does not: no `PrintWindow` call exists in `python/src`. The chain up to the PID is built (`logid.resolve`), but the window tail is not written, so shipping it is new code, not wiring.

- [ ] **`ExecuteMultiplayerTestAsync` still unverified**, and blocked behind a real constraint: a direct call blocks past the 120 s tool-call timeout, while `task.spawn` around it dies with the call's context and can wedge the test service. Those two are in tension, and resolving it needs either a way to hold a call open (a scratch instance acting as a mailbox) or a persistent host. The `-task StartServer` route sidesteps it for `AddPlayers`, but not for ending a test with a result value.

- [ ] **Chrrxs' 7-tool playtest surface still not built.** Their advantage is not `AddPlayers`, which we now have; it is ending a test with a *value* (an assertion result), per-client `LeaveTest`, and a stop path that separates "no test running" from "signal did not propagate". Take their granularity, keep our descriptions. Every call needs `studio_id`, since a play session belongs to one Studio.

- [ ] **`OnStopped` mode for breakpoints.** `rbx-debug` documents `OnStopped` + `GetVariables`/`Evaluate` and says to prefer it over logpoints for richer state. Ours is logpoints only, so capturing two locals means string concatenation and parsing. Constraints: `OnStopped` is per-DataModel and does **not** propagate edit -> play DMs, and a callback that throws **resumes by default**, so it must return an explicit `Enum.DebuggerResumeType`.

- [ ] **`screen_capture` PNG** (request P0.2b) is **not possible** - it is a relayed Studio tool and we cannot add parameters to it. `extended_capture` supersedes the need.

- [ ] **Schema bloat is now the bigger half of our footprint.** 7,141 of 9,502 chars are schema, and `extended_update_script` alone is 1,450, mostly a nested `edits.items` block. Needs a decision on whether the `replaceAll` alias earns its place.

### To do (recorded 2026-09-30, not yet built)

- [ ] **`search_and_read` still validates nothing (MEDIUM, unchanged).** `root_path: "game.NoSuchService123"`, `max_results: -5` and a garbage path all return `[]` - indistinguishable from an empty service. Sibling `extended_script_grep` strictly refuses `context_lines: 99`, `max_results: 9999`, `query: ""` and a bad regex. Inconsistent strictness across two tools that look interchangeable. Also still unsettled: the `game.` prefix (inputs require it, returned paths drop it).

- [ ] **Capture `save_path` failure emits an undeclared code (MEDIUM).** My own fix raises `INTERNAL_ERROR`, which is not in `ALL_CODES` - the exact defect the closed-set tests exist to prevent, and the harness correctly reports it as unknown. Fix: `INVALID_ARGUMENT` (a bad path is the caller's argument) or declare the code properly with its producibility test. Paired edit, both sides.

- [ ] **The closed-set gate has a reverse gap (mine, found via the above).** `test_closed_sets.py` proves every declared code is producible; nothing proves every raised code is declared - which is how `INTERNAL_ERROR` shipped. Add the reverse test: every `ToolError("CODE", ...)` literal in `src/` must be in `ALL_CODES`. Both sides (Node's suite needs the same direction).

- [ ] **Scope the console one-line claim (LOW-MEDIUM).** The fuzzer measured real newlines through the agent caller path, so `rsx-console.md`'s absolute prohibition ("match the pattern, do not parse lines") is consumer-dependent. Skill edit: state which path the one-line shape holds on instead of asserting it universally. No code.

- [ ] **No delete tool.** Fuzz scratch cannot be removed from the throwaway place. Update 2026-09-30: the cap-raise framing is retired per user ruling - new surface is funded by trimming under the what/when/why policy, not by raising the cap. The audit freed net -2 (headroom 8); a ~150-char delete tool still does not fit, so this stays open. Harmless while the place is a throwaway.

- [x] **RETRACTED 2026-10-03 - moot: with one implementation there are no pairs to divide.** ~~Pin description raw-identity.** The three pairs just converged to byte-identical text, and nothing stops the next one-sided edit from re-dividing the budget asymmetrically. A test asserting raw equality across the 16 pairs costs nothing and fails symmetrically.~~

- [ ] **Still open and unchanged:** the `os.startfile` registry check, identity live measurement, PrintWindow (now funded by trimming, not a cap raise - but still unbuilt), `ExecuteMultiplayerTestAsync`, the Chrrxs playtest surface, `OnStopped` breakpoints. All above in this file under their own headings.

### macOS

- [ ] **`StudioMCP.exe` proxies accumulate on the mesh.** Three alive for two Studios, one per client connection, ~40 MB each. This is ongoing, not leftover from the earlier `action=stop` analysis.

- [ ] **`platform.parse_ps_row` and `platform.attached_pids` (lsof) are unproven on a Mac.** Both are unit-tested against captured macOS-shaped rows, but `lsof -iTCP:13469 -sTCP:ESTABLISHED -t` is inferred from the Windows equivalent's semantics, not observed.

- [ ] **The gating measurement is still not made, and I am not going to call it settled.** Four converging pieces of evidence that the `UIThreadNotifier ... for process 'N'` line is present on macOS: 1. FLog is one documented format across Player/RCC/Studio. 2. The filename format is byte-for-byte the same shape. 3. The channel is engine-level, emitted by shared C++. 4. **`Superwheat/renium` uses that exact marker on macOS** - its `studio_log_for_process` sits behind both the `#[cfg(windows)]` and `#[cfg(target_os = "macos")]` process enumeration, so its macOS branch would simply never work if the line were absent.

- [ ] **FLog has four format types and this project only parses one.** Type 4 is `ISO8601Z,TimeSinceStarted,Thread,LogLevel [FLog::...]`; type 1 is `UnixTime,Identifier,LogLevel [FLog::...]`, type 3 is `TimeSinceStarted Thread: message`. The parser's negative lookahead for `\d{4}-` assumes type 4. That is a **Windows-side** robustness gap too, not just a macOS one: an older-format log would be read as having no PID.

- [ ] **No macOS equivalent for the fast capture path.** Win32 `PrintWindow` has no counterpart; `CGWindowListCreateImage` is deprecated and needs screen-recording permission. So macOS capture stays on the engine path at ~1.7 s, and the PrintWindow work does not port.

### `extended_wait_for`'s probe only works in Edit mode

- [ ] **Audit every emitted-code path for the same blind spot** — still open, and no longer about `datamodel_type`. The chunked writer, the breakpoint `log_expression`, and the capture encoder all emit Luau and are all tested by asserting on *text*, which cannot tell executing code from echoed text. The narrower question is the one worth asking: **does each remain correct in Client/Server, or does any of them assume Edit?** That is a runtime check, not a text assertion.

- [ ] **`AssistantCommand` is a symptom, not the disease.** A script this project did not write, and its use of `loadstring` is independently wrong — but the technique reached it from this repo, documented prominently as the fix for a hang. A confidently-documented wrong technique travels further than an undocumented one.

- [ ] **MEASURED 2026-09-30: an unparseable condition wedges the whole server, not just the call.** `extended_wait_for({condition: "this is not lua(((", timeout_seconds: 4})` never returned; the MCP required a reconnect to recover. A second probe with the capture half removed hung identically, so the capture is exonerated and the condition is the whole cause. Mechanism, from the code just re-read: `build_probe` raw-splices the condition, so a non-parsing condition means the entire command fails parse; the parse happens before any handler runs (`PROBE_CAVEAT`), the `await studio.call(...)` never resolves, and because requests are served sequentially the wedged call blocks every later one — which is why the fix was reconnecting, not waiting. `timeout_seconds: 4` was bypassed entirely: the hang is in the first poll's await, before any deadline logic runs. Do NOT re-probe this live without a mitigation in place; every repro costs a reconnect. Mitigation candidates, unmeasured: a host-side timeout around the poll await (un-wedges the loop even though it cannot cancel Studio-side execution), versus establishing whether Studio-side command execution itself is wedged (in which case even that only converts a dead server into failing polls until Studio restarts).

- [ ] **`invalidate_process_cache` is the last dead symbol, and it is a judgement call rather than an oversight.** It has no caller in this repo, but it is public API on a module that is also importable as a library, and a consumer who starts a process out of band would want it. Left in place deliberately.

### Corrections to earlier notes in this file

- [ ] **The minimality of the URI is only half measured.** Four keys works. The key count and the value are settled, but whether `placeId` **alone** is enough was never established — the six-variant sweep (`python/verify_uri_minimal.py`, written and not completed) would answer it, along with whether `universeId` can be dropped. The key is currently kept on the **user's authority**: they have worked with Studio launch arguments at length and confirmed `universeId` is required. That supersedes the earlier note here, which credited a local attempt that left `name: null` — retired as evidence, because a key that can be dropped fails in a way a process count cannot see.

- [ ] **NOT portable, and it is most of their repo.** Everything under `plugin/` is off limits: Roblox's `StudioMCP` is signed, so the hub, the `PluginConnection` star, the 8 ms `CooperativeJobRunner`, the `ChangeHistoryService` recording wrapper, and the `LogService.MessageOut` push journal all require owning the plugin. Their `docs/research-brief.md` reaches the same conclusion from the other side: `PluginConnectionService` has **zero third-party adoption** and its payload cap is undocumented. `bridge/src/sync/*` is a Rojo reimplementation and Rojo exists. `bridge/src/vision/*` is a product, not a technique.

### Launch routes fail at different layers (measured by renaming the exe)

- [ ] **`os.startfile`'s "Application not found" still conflates two causes.** *Protocol not registered* and *registered but pointing at a missing file* need opposite recoveries ("install Studio" vs "repair the handler"), and the second is checkable — resolve the registry command and test the path. Not fixed: it needs a registry read, which is Windows-only and not yet a platform primitive.

### Session operations

- [ ] **An edit to a `.py` file needs an MCP server restart** - the process holds the module in memory. The config change restarted it; the code fix did not. Same trap as the stale dist, and it cost a round of "still broken" measurements.

### Housekeeping

- [ ] The throwaway baseplate is at `%TEMP%\robloxstudio-mcp-baseplates\Baseplate-*.rbxl`, launched with `--task EditFile --localPlaceFile`. Worth deleting when done.

## Withdrawn — do not re-derive these

- > **Superseded 2026-10-01 on the "never run" part, not the rest.** There is still *(`macOS: researched and implemented, still never executed`)*
- ## The lock file, and a claim retracted *(`The lock file, and a claim retracted`)*
- - [x] **RETRACTED: the lock join does not separate concurrent URI launches.** `locks.py` claimed exactly that. Live, with two URI launches of one place: both reported the mesh name **`Place1`**, **neither held a lock**, and the only locks present were stale. It resolved **0 of 3** named rows. It wor *(`The lock file, and a claim retracted`)*
- retracted `os.path.basename` claim, and the retracted `locks.py` URI claim. *(`Housekeeping`)*
- ### RETRACTED: "the real universe id fails, so never fetch it" *(`The universe id is a function of the place id`)*
- **The claim is withdrawn; the measurement is not.** It is still the only recorded *(`The universe id is a function of the place id`)*


  > **Resolved 2026-10-03** - **fixed here** - all four: `save_path` failure mode (with the `CAPTURE_OK_RECOVERY` message and the arithmetic showing the base64 route is ~50x over the return ceiling), the reported `studio_id`, PrintWindow measured-not-built, and chunk size not a caller knob.