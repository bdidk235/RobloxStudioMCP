# TODO — open work only

> **Every open item in this repository, and the claims that have been
> withdrawn.** Nothing else. Closed research lives in
> [`docs/EVIDENCE.md`](docs/EVIDENCE.md) and in git history; this file used
> to hold all of it, and reaching the work meant crossing 1,300 lines of
> findings that were already settled.
>
> **Open work, then withdrawals.** `- [ ]` is work someone could pick up
> today; the count is whatever `- [ ]` returns, so it is not written here.
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

- [x] **DONE 2026-10-08 — `extended_skill` shipped 0 skills, and said nothing.** Measured before and after by building the wheel and installing it into a clean venv: the wheel had 29 entries, none of them markdown, because `skills/` sat at the repository root, outside the package, so `package-data` could not reach it. The loader then turned the miss into `[]` and the tool answered a successful call with an empty catalogue — the silent-empty class in this project's own terms, with a docstring above it asserting the opposite. Fixed both halves: the skills moved to `python/src/roblox_studio_mcp/skills/` with `["py.typed", "skills/*.md"]` in `package-data`, and `load_skills()` raises rather than returning empty. Verified by build: 37 wheel entries, 8 markdown, and 7 skills loaded from the installed copy. Full measurement and rejected alternatives in `docs/EVIDENCE.md`, *The skills were not in the wheel at all*.

- [x] **DONE 2026-10-08 — the skills folder move invalidated two paths that nothing guarded.** `test_skill_refs.py` computed its own folder from the test file's location, so after the move it would have globbed a directory that no longer exists and **passed vacuously**; `contract/find_unused_python.py` scanned a stale `skills/` path. Both updated, and `test_skill_refs.py` now fails if its folder disappears instead of iterating over nothing. The general shape: a test that derives its input from a path it does not assert is one rename away from being a test that cannot fail.

### Open items: skills and guards, reviewed 2026-10-01

- [x] **DONE 2026-10-05 — `skills/README.md` cost figures deleted, not corrected.** Both totals are gone and the ratio is cited instead. Re-measured before editing: the **body figure was wrong** (claimed 55,465, actual **72,139**) and the **index figure was right** (claimed 1,985, actual **1,984**), which is the worse outcome — one correct number beside a wrong one makes the whole line read as trustworthy. Nothing pinned either: the only gate is `index < bodies//4`, which passes at 2.7%, 7% and 25%. *An earlier revision of this item quoted 27,000 / 52,982 / 7%, and then a second revision quoted 55,465 — so the record of the drift was itself drifting, which is the argument for deleting the numbers rather than refreshing them.*

- [x] **DONE 2026-10-05 — the "never raise the cap" policy line was already gone.** `:110` already reads "The total was 2,700 until 2026-10-01, when it was raised to 3,200 by decision", and `:104-108` already points at `contract/tools.json` for the live figures rather than quoting them. Closed by reading the section, not the single line the item named — which is why this took a second pass.

- [x] **DONE 2026-10-08 — all five gaps closed in `skills/README.md`.** Reachability exception (OnStopped per-DataModel, verified at `rsx-breakpoints.md:164`), the read-before-driving rule (`AGENTS.md:195`), neither-tool-serves-the-other's-names, host-side identity state (`registry.py:1,60`), and the explicit `SKILL_IGNORED` note (`skills.py:49`). Each verified against source before writing.

- [x] **`rsx-playtest.md` — the file's only recipe is the call it says wedges.** Steps 2-4 instruct `ExecuteMultiplayerTestAsync` called directly, while the same file records that it blocks past 120 s and that the timeout is the exact condition that wedges the test service permanently. It also never mentions the confirmed `-task StartServer` route. **An agent following it ends up needing a Studio restart.**

  > **Resolved 2026-10-03** - **already fixed** before this pass (`rsx-playtest.md:226,236`); the tracker was stale, not the file.

- [x] **`rsx-playtest.md` — `extended_run_tests` overstates what it does.** "Starts the engine's runner" is wrong: it calls `start_play()` (the local single-player session) and never touches `StudioTestService`. `passed` is `not errors and not missing` over a console substring scan, and `test_paths` is optional — **so a place with no tests returns `passed: true`.** "Green-check only" in the tool description is right; "did the suite go green" is not.

  > **Resolved 2026-10-03** - **already fixed** - the description now reads "Play the place; console as {passed, console_lines, errors}".

- [x] **`rsx-playtest.md` — no `extended_wait_for` guidance**, so "poll until the count reaches N" names no mechanism. Two behaviours an agent needs are undocumented: a non-boolean return is `INVALID_ARGUMENT` (not a pass), and three hung polls abort `TIMEOUT` naming a restart.

  > **Resolved 2026-10-03** - **already fixed** - there is now a section on waiting plus a poll snippet.

- [x] **`rsx-playtest.md` — "all confirmed" is not true for three items:** `ExecutePlayModeAsync` appears nowhere else in the repo (its row's own class of claim, but unsourced); the `AddPlayers` 1–8 bound has no provenance; and the `EndTest` section is headed "Reproduced" with no `TODO.md` entry — and this file says the StartServer route works for `AddPlayers` **but not** for ending a test with a value, a combination the file presents as one loop. *(The line ref was `TODO.md:1687`, back when this file was 2,790 lines; it named a line that no longer exists, so it is dropped rather than renumbered.)*

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

- [x] **`rsx-breakpoints.md` repeated the unscoped one-line console claim** (scoped in `rsx-console.md`). **Already fixed** before this pass — re-measured 2026-10-05 at `rsx-breakpoints.md:121-128`, which now says the shape is consumer-dependent and names the agent caller path; the tracker was stale, not the file.

- [x] **`rsx-discovery.md` — `RunService.IsEdit()` is described backwards:** "a parse error, not a nil call" is wrong twice. It is valid Luau; the engine says `attempt to call a nil value (field 'IsEdit')`, which the project's own classifier maps to `LUA_ERROR` — so the distinction the skill draws is not one the codebase makes.

  > **Resolved 2026-10-03** - **fixed here** - `errors.py` maps `attempt to call` and the parse failures to one `LUA_ERROR`, so there is no distinction to act on.

- [x] **`rsx-discovery.md` — its own "RIGHT" snippet throws** on the case two sections later documents: `ipairs(GetMethodsOfClass("StudioService"))` where the function returns nil. Needs `or {}`, which is the guard the same file teaches later and never connects.

  > **Resolved 2026-10-03** - **fixed here** - the snippet now `pcall`s the call and iterates `ok and methods or {}`.

- [x] **DONE 2026-10-08 — evidence classes separated inline; sourceless member deleted.** The `StudioTestService` row now marks confirmed members vs `ExecuteMultiplayerTestAsync` (unconfirmed, with pointer to the note). `GetTestArgs` appeared nowhere else in the repo (verified by grep), so it was deleted per the delete-rather-than-correct precedent.

- [ ] **`rsx-discovery.md` — missing, partly fixed.** Re-measured 2026-10-05: the capability gate **is** now documented (`rsx-discovery.md:128-134` — `game.UniqueId`, the `RobloxScript` capability, and why reflection cannot answer it either), and the telemetry duplication is gone (no `telemetryLog` in `rsx-targeting.md`). `rsx-discovery.md` — array/`Vector2` shapes and `see also` boundary **done 2026-10-08**; `settings()` still sits under the reflection heading rather than in the capability story, so the no-process-id ruling is incomplete. That remainder stays open below.

- [x] **DONE 2026-10-08 — every sub-item closed.** Slice (done 10-05), ` 1->` warning, dead `REQUEST-luau-return-shapes.md` pointer, wait-for hung-poll abort section, second-content-block note. Nothing on the original list remains missing.

  > **Partly resolved 2026-10-03** - the tracker had marked the whole item done on the strength of the slice fix alone. Re-opened 2026-10-05 against the file.

- [x] **DONE 2026-10-08 — all three sites scoped.** `breakpoints.py:32-35`, `extended/skills.py:10`, and `python/README.md:194` (which also now notes `extended_capture` encodes host-side, verified at `capture.py:454`) all state both console shapes. The skill already scoped it, so the code caught up to the docs rather than the reverse.

- [x] **DONE 2026-10-08 — `test_dispatch_coverage.py` drives all 16 tools through `_handle_message`.** Bare calls must fail with the handler's own `INVALID_ARGUMENT`; the rest must answer with a result or declared-code error. Each dispatch is time-boxed at 20 s.

- [x] **RETRACTED 2026-10-03 - moot: the Python guard has its dispatch-path test and the Node side is gone.** ~~Two of my own test fakes were wrong in the same way on both sides** - bare objects where the code calls `.json()`. The guard reported "could not check" and the refusal test passed **for the wrong reason**. Mirrored suites caught it, which is the strongest argument yet for the habit.~~

- [x] ~~**`skills/` is untracked and the repo has no CI over it.**~~ **Withdrawn 2026-10-03 - false on both counts.** `git ls-files skills` returns 8 files, none ignored; `ci.yml:35` runs `python -m pytest tests` on a two-OS matrix and `test_skills.py` loads the real directory. The real gap is narrower and recorded separately.

- [x] **DONE 2026-10-08 — the cheap floor exists as `test_skill_refs.py`.** Every `file:line` pointer in `skills/*.md` must name a real file and in-range lines; mutation-proven by planting a bad pointer. It judges reachability, not truth, exactly as the item prescribes.

- [x] **`TODO.md`'s description of the `save_path` failure was wrong** - it said the message survived while the `INTERNAL_ERROR` item said it was discarded, and the two contradicted each other. **Both are now moot**: re-measured 2026-10-05, the code raises `INVALID_ARGUMENT` with the recovery message intact (`capture.py:415-439`), so this file and `skills/rsx-capture.md` agree again.

- [ ] **Unsourced figures across the skills** (640 classes, 50 methods on `StudioTestService`, `GetClass` returns nil, 0.002 px JPEG localisation, the ViewportFrame readback claim, ~139-char console sample). Each reads as measured against these files' own provenance standard. Either cite or label — "wrong notes that survive because nothing contradicts them".

- [x] **`extended_capture`'s `save_path` failure raised an undeclared code.** **Fixed before this pass** — re-measured 2026-10-05 at `capture.py:415-439`: it raises `INVALID_ARGUMENT` (in `ALL_CODES`) and the `CAPTURE_OK_RECOVERY` message survives, with `capture.py:416-426` recording why. The tracker was stale on all three counts — the code, the discarded message, and the `UNKNOWN` the caller saw.

- [x] **CLOSED 2026-10-08 — premise does not survive reading, nothing to fix.** None of the unsourced-figures item's figures (`640`, `50 methods`, `GetClass`, `0.002`, ViewportFrame, `139`) appears in `rsx-playtest.md` (verified by grep: all zero), and every *confirmed* marker in the file carries adjacent evidence — the AddPlayers 1→2 transcript, the refusal output, the up-front evidence-status section separating confirmed from unverified. The file already meets the standard the item asks for.

- [x] **DONE 2026-10-08 — both keys are documented in the skill.** `hit_prefix` and `how_to_read_hits` (returned at `extended_server.py:1053-1054`) now appear in `rsx-breakpoints.md` with their literal values, so callers stop re-deriving them.

### Remaining

- [x] **DONE 2026-10-08 — `test_path_spelling_sweep.py` keeps the class closed.** It fails on any assert line naming a path by spelling, with a reason-bearing `spelling-ok` escape hatch (two legitimate cases carry one). The `954e07a` instances stay fixed; the sweep stops the fourth.

- [x] **RETRACTED 2026-10-03 — moot: the repo is Python-only and the Node client is gone, so there is no second implementation left to wire up.** ~~The one real difference: identity resolution — and the code is already written.** `logid.ts` (86 KB) and `locks.ts` are ported and tested; `logid.test.ts` has 60-odd references. What is missing is the **call site**: `instance.ts` never imports `logid.ts`, so `listStudioProcesses` derives role and place from the command line (`processRows`, `roleFromCommandLine`, `placeFromCommandLine`) and `stopProcess(pid)` has no PID to be given. With two Studios on one place — the current state on this machine, both named `Place1` — Node **cannot say which is which**, so `action=stop` refuses rather than terminating the wrong process. **Closing it is now a wiring change, not a port: import `logid.ts` where `processRows` supplies role and place, and decide what a log with no PID does — it must be an error, never a silent "no identity".** Corrected 2026-10-03; this item previously said the module was unported and put the figure at 1,012 lines, which was wrong.~~

- [ ] **The description budget is effectively full, 16 tools.** Re-measured 2026-10-05: **3,025 of 3,200**, 175 chars of headroom (live figures in `contract/tools.json`). *Two revisions of this item carried the figure: one said 2,897, true when written and since drifted; the other quoted none at all, so the figures moved and the sentence did not.* This is a hard blocker on adding a 17th tool, and a 17th tool is a decision for the user, not an accident — worth knowing *before* designing one.

- [ ] **The PrintWindow *implementation* is not in the repo - only its measurement is.** Worth being precise about, because the 48-205 ms figure reads as though the code exists. It does not: no `PrintWindow` call exists in `python/src`. The chain up to the PID is built (`logid.resolve`), but the window tail is not written, so shipping it is new code, not wiring.

- [ ] **`ExecuteMultiplayerTestAsync` still unverified**, and blocked behind a real constraint: a direct call blocks past the 120 s tool-call timeout, while `task.spawn` around it dies with the call's context and can wedge the test service. Those two are in tension, and resolving it needs either a way to hold a call open (a scratch instance acting as a mailbox) or a persistent host. The `-task StartServer` route sidesteps it for `AddPlayers`, but not for ending a test with a result value.

- [ ] **Chrrxs' 7-tool playtest surface still not built.** Their advantage is not `AddPlayers`, which we now have; it is ending a test with a *value* (an assertion result), per-client `LeaveTest`, and a stop path that separates "no test running" from "signal did not propagate". Take their granularity, keep our descriptions. Every call needs `studio_id`, since a play session belongs to one Studio.

- [ ] **`OnStopped` mode for breakpoints.** `rbx-debug` documents `OnStopped` + `GetVariables`/`Evaluate` and says to prefer it over logpoints for richer state. Ours is logpoints only, so capturing two locals means string concatenation and parsing. Constraints: `OnStopped` is per-DataModel and does **not** propagate edit -> play DMs, and a callback that throws **resumes by default**, so it must return an explicit `Enum.DebuggerResumeType`.

- [ ] **`screen_capture` PNG** (request P0.2b) is **not possible** - it is a relayed Studio tool and we cannot add parameters to it. `extended_capture` supersedes the need.

- [ ] **Schema is now about half the footprint, by count.** Re-measured 2026-10-05: **3,144 schema chars against 3,025 description chars — 51%**. *Corrected: this said "7,141 of 9,502", which put schema at 75% and overstated the absolute size by ~2.3×. The conclusion survives; the numbers did not.* Needs a decision on whether the `replaceAll` alias earns its place.

### To do (recorded 2026-09-30, not yet built)

- [x] **DONE 2026-10-08 — shape faults fail fast with `INVALID_ARGUMENT`.** Non-`game.` `root_path` and non-integer/out-of-range `max_results` raise before any Studio call, matching `script_grep` strictness (`_bounded_int`). Residual, documented in the docstring: a *well-formed* path naming nothing that exists still returns `[]`, because non-existence needs the round trip whose error shape is unverified without live Studio.

- [x] **Capture `save_path` failure emitted an undeclared code (MEDIUM).** **Fixed before this pass** — re-measured 2026-10-05 at `capture.py:415-439`, it raises `INVALID_ARGUMENT`, which is in `ALL_CODES`. This item and the one under *Remaining* said the same thing twice; merged into the entry there. Kept rather than deleted, because the reasoning is what stopped it recurring.

- [x] **The closed-set gate had a reverse gap** (mine, found via the above). **Already closed** — re-measured 2026-10-05: `test_closed_sets.py::EveryRaisedCodeIsDeclared::test_no_raise_site_names_an_undeclared_code` scans every `ToolError("CODE"` literal across `src/` and asserts membership in `ALL_CODES`, which is the direction this item asked for. Its docstring names `INTERNAL_ERROR` as the defect that motivated it. The Node half is moot: the repo went Python-only on 2026-10-03.

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

### Found by external review 2026-10-07, verified against source

Eight defects and two stale figures, every one reproduced at the line cited. Verdicts, refutations and provenance: [`docs/EVIDENCE.md`](EVIDENCE.md), *External review of 2026-10-07*. Ordered by severity, not by the reviewer's order.

- [x] **DONE 2026-10-08 — `stop` routes through `platform.terminate`; the false docstring is corrected.** `terminate_process` now delegates instead of hardcoding `powershell`; `platform.terminate` no longer claims exclusivity. Covered by `TerminateProcessRouting` (4 tests). Residual, unchanged: no Mac was available, so the `kill -9` branch is correct-by-construction — same standing as every other macOS path.

- [x] **DONE 2026-10-08 — see above; fixed together, not separately.**

- [x] **DONE 2026-10-08 — route 1 verifies the pid or says `verified: False`.** A lone arrival is checked against the launched pid's own log identity: match → `verified: True`; unreadable → row returned with `verified: False`; mismatch → falls through to routes 2/3. The `>1` refusal is untouched. Covered by 3 new route-1 tests.

- [x] **DONE 2026-10-08 — the listed call sites are off the loop.** All three `list_studio_processes` reads, the 2s poll sleep, and the universe fetch now yield via `to_thread`/`asyncio.sleep`. Residual, out of scope: `logid` file reads on the route-2/token-fallback paths are still sync (~0.1s each).

- [x] **DONE 2026-10-08 — the comment now describes the loop.** `allow_console_write` authorises one token per candidate *in this launch's set*, including unnamed Studios; outside that set it grants nothing. The behaviour is unchanged, the claim is now true.

- [x] **DONE 2026-10-08 — revalidation lands; schema-level confirmation deliberately does not.** `revalidate_pid_for_stop` runs between resolve and kill (liveness, then log/task identity) and refuses with `stopped: False` instead of terminating; covered by `test_stop_revalidation.py` (8 tests). No `dry_run`/`confirm` was added: that would break the tool surface for existing callers, so confirmation stays a caller-policy decision, recorded here rather than silently dropped.

- [x] **DONE 2026-10-08 — the ordering is fixed defensively.** The new-PID scan now precedes the `proc.poll()` exit check, and the exit report fires only when no new PID was seen. The handoff premise itself remains untested.

- [x] **DONE 2026-10-08 — explicit guarded cleanup exists; automatic cleanup deliberately cut.** `cleanup_throwaway_place` removes only `roblox-studio-*.rbxl` inside the platform temp dir and never raises; covered by guard tests. An age-based sweeper was built on the branch and **removed before merge** as speculative surface (arbitrary threshold, uncalled by anything). What remains: no caller invokes the cleanup yet, so accumulation persists until it is wired into a lifecycle — a follow-up, not a pretended fix.

- [x] **DONE 2026-10-08 — the §0 pooled-scratch proposal was analysed and REJECTED with measurements; nothing implemented.** One reusable scratch path with a ≤4 suffix pool is **not strictly better** than `mkstemp` per call, so the honest move was a non-change. Four measurements, all run here:

  * **Concurrency — the pool starves, and a lock does not save it.** A claim protocol (O_CREAT|O_EXCL per pool member, held while the Studio runs) under 12 concurrent claimers gave **296 of 300 requests no file at all**: 4 members, none released. So the design must either **refuse** (today every `make_place` succeeds — measured: 100 of 100) or **hand back a member a live Studio already has open**. `locks.py` offers no mutex — its seven functions all read Studio's own `.lock` files, nothing acquires one. An `asyncio.Lock` would be dead weight besides: `serve()` awaits `_handle_message` in a `while True` (`extended_server.py:1796-1807`), so two calls in one host cannot interleave, while the multi-host case that *does* race is measured real (8 `StudioMCP.exe` proxies for one attached Studio, same user, same `%TEMP%`). No lock exists that covers it.
  * **The reuse branch lands on a destructive path.** For a file launch the mesh name **is** the opened file's basename (`logid.match_mesh_name:1167-1189`, `instance.py:202-222`). Measured: two Studios on one path → `match_mesh_name` returns **2** candidates and `ambiguous_reason` says the name does not identify one process, so `resolve_pid_for_studio` sets `needs_console_write` — the authorisation-gated last resort becomes the *normal* path for `stop`. Two Studios on two unique paths → **1** candidate, exact. `_identify_launched` route 1 (the before/after mesh diff) is what copes with shared names on the *launch* side; `resolve_pid_for_studio` has no equivalent, and this repo has already killed the wrong Studio once. So the pool converts an exact resolution into an ambiguous one, in exchange for eliminating a class whose measured volume is **1 file**.
  * **The baseplate is still required, and reuse breaks the copy's contract.** With no discovered baseplate `make_throwaway_place` raises `FileNotFoundError` (measured). Re-copying over a member each call restores pristine content but collides with a Studio still holding that file open — the same sharing-violation class the existing docstring records for Windows deletes. Handing the member back uncopied silently changes "copy a discovered baseplate to a temp file" into "here is whatever the last session left in it".
  * **Backwards compatibility holds for the *old* names and fails for the *new* ones.** `cleanup_throwaway_place` still removes a legacy `mkstemp` name (measured True). But the exit criterion's second half — `tempdir | grep roblox-studio-` empty — **contradicts the proposal's own naming**: a pool file called `roblox-studio-scratch-0.rbxl` always matches (measured 1 hit), so only a rename away from the prefix satisfies it, and that renamed file is refused by the guard built from `THROWAWAY_PREFIX` (measured False). The criterion is unachievable as written without either weakening the guard or leaving the 4 pool files grep-visible.

  Residual, unchanged and now the only real gap: **no tool can delete a minted place** — that is the delete-tool item below, and `contract/tools.json` measures enough headroom today for a ~150-char one. Accumulation is bounded by the number of `make_place` calls the caller actually made, and `cleanup_throwaway_place` is ready for that tool. Do not re-derive this; re-open only with a measurement that refutes one of the four above.

- [x] **DONE 2026-10-08 — the rotted figure is gone.** The comment now says "unchanged pass count" with no number. Residual, needs a decision: `requires-python = ">=3.9"` is declared while CI pins 3.12, so the floor is never exercised — left alone as potentially breaking.

### Security audit 2026-10-08: fix in severity order, A1 first

External audit, PoC-confirmed where stated, verdicts in `docs/EVIDENCE.md`
(*External security audit*). Severity is the auditor's; the file:line pointers
were re-verified here for A1 and A3 before recording.

- [ ] **A1 HIGH — confine `extended_capture`'s `save_path`, un-mark it read-only.** Today one call truncates any user-writable file with PNG bytes (no `_confined()` anywhere in `capture.py`), while `readOnlyHint: true` tells clients it is safe to parallelise. Route it through the same `_confined()` as the other file tools, drop it from `_READONLY_TOOLS`, add it to the `writes` set in `test_readonly_hints.py`.
- [ ] **A2 HIGH — make confinement a policy, not a parameter.** `allow_outside` is a model-settable boolean with no gate; `file_type="script"` + read-back discloses any readable file into model context in two calls. Make the root a configured constant (`ROBLOX_STUDIO_MCP_FILE_ROOT`) instead of `Path.cwd()`, and treat `allow_outside` as operator configuration rather than a tool argument.
- [ ] **A3 MEDIUM — one shared game-tree path validator.** `target_path`, `parent_path` and `root_path` get `^game(\.[A-Za-z_][A-Za-z0-9_]*)+$` with the received value in the error; anything with whitespace or a newline is refused outright. Place content must not become executed code through the search→write loop.
- [ ] **A4 MEDIUM — scope guard + graceful path in front of `stop`.** Add `dry_run`/`confirm`; restrict the console-token fallback to the pool the caller's `studio_id` resolves to, never all live processes; `SIGTERM`-then-`SIGKILL` on POSIX, `Stop-Process` without `-Force` first on Windows.
- [ ] **A5 MEDIUM — independent witness before a kill.** Don't take the target from log files alone: cross-check the AutoSaves `.lock` session GUID and require the attachment set to contain the PID.
- [ ] **A6–A11 LOW — in one pass, lowest priority.** `disabled_tools` on the extended proxy; read caps; deadline-extension ceiling + relay origin marks; second proxy per `list_studios`; bound `wait_seconds`; state the relay exemption's true scope.

### Found by all-mcprevs verification 2026-10-08

Four verifiers (one per review source, model `step-5-preview-free`, live-Studio access under read-mostly rules) re-checked every claim in `mcprev1/`, `mcprev2/IMPROVEMENT.md`, `mcprev3/full.txt` and `mcprev3-new/`. Most of the reviews are stale where this week obsoleted them; what follows is only what survives re-derivation. Verdicts, evidence and refutations: the verifier reports are the record — this list is the actionable residue.

**Still open, worth doing:**

- [x] **§0 scratch place — ANALYSED AND REJECTED, see the DONE entry above (TODO:196).** `make_place` still mints one `roblox-studio-*.rbxl` per call, deliberately. The pooled-scratch proposal was designed out with four measurements: the pool starves 296/300 requests under concurrency with no lock available to fix it (`locks.py` has no mutex, `asyncio.Lock` cannot cross hosts), reuse hands two Studios one path and so turns `stop`'s exact resolution into the authorisation-gated console-token last resort, the baseplate copy's contract cannot survive reuse, and the exit criterion's `grep` half is unachievable under the pool's own prefix. Measured volume of the class being eliminated: **1 file**. What actually remains is the missing delete tool, not a missing pool.

- [ ] **Proxy leak, and the repo's own figure is wrong.** Live-measured 2026-10-08: **8 `StudioMCP.exe` proxies for 1 attached Studio**, and a `RobloxStudio.connect()` added one that **survived the client closing**. Per-proxy memory is **11.9–15.6 MB** by `tasklist`, not the "~40 MB each" TODO records — fix that figure wherever it is quoted. No reaper exists; singleton only dedupes within one host process. Exit: 20 client opens → 1 proxy.

- [x] **DONE 2026-10-08 — the authorisation is named, not hidden in a literal.** `extended_server.py:1195` now calls `resolve_pid_for_studio(studio, studio_id, allow_console_write=True)` with a comment saying this is the authorisation point and what it permits: it lets the resolver print one `RBXPID` join token into the console of the Studio named by `studio_id` and read it back out of Studio's logs. Behaviour unchanged: the flag was already `True`. Pinned by `test_stop_revalidation.py::StopDispatchRevalidates::test_the_console_write_authorisation_is_named_at_the_call_site` — a bare positional `True` fails it, which is the point. Residual, unchanged and still open: no `dry_run`/`confirm` on the stop schema (deliberate non-break, recorded at TODO:191).

- [ ] **`locks.py` is dead code, and a cheaper join sits unused in it.** Nothing in `python/src` imports it — only `tests/test_locks.py`. It documents a non-log join (`studio_id → mesh name → <name>.lock → first field`) that would take pressure off `logid`'s log scraping. Decide: wire it into the resolver, or delete it. Note `stop` now depends on the log-resolution chain (`instance.py:1440-1456` revalidates between resolve and kill), so a log-format break sits under a destructive path, not only a convenience one.

- [ ] **Upstream tool surface unpinned (biggest silent risk).** `contract/tools.json` covers only the 16 `extended_*`; a live `tools/list` returned **28** upstream tools and nothing asserts them. A rename surfaces as a user-session failure, not CI red. Measured today: zero drift — `docs/catalog.js` carries the 28 and matches exactly, unasserted and hand-maintained. Fix: check in an upstream snapshot and assert on it, separating `proxied_upstream` from `extended`, warning rather than failing on a transient disappearance.

- [ ] **`wait_for` per-request queue, and the emitter audit.** Poll bounds landed (`POLL_TIMEOUT=30`, `MAX_HUNG_POLLS=3`, abort naming a Studio restart), so a single hang no longer wedges — but the serve loop is still sequential (`extended_server.py` `await _handle_message(...)`), so a sequence of hangs still aborts, and no host-side parse validation exists. Same blind spot un-audited for the chunked writer, breakpoint `log_expression` and capture encoder: all tested by asserting on text, none executed.

- [ ] **Release story.** Not on PyPI (404), no tags, no CHANGELOG, no `doctor`. One verified denominator: a fresh-venv `pip install .` succeeds and imports — but `python -m examples.list_tools` **fails from outside `python/`** (`ModuleNotFoundError: No module named 'examples'`; `examples/` has no `__init__.py` and pyproject packages only `src/`). README's install section should either fix the path or state the `python/` cwd requirement.

- [ ] **Operator runbook and error-code recovery.** No runbook exists anywhere (`grep -rln runbook` → no files). The six-step chain launch → attach → resolve → capture → play → stop, with every declared code's recovery, is the missing "first 10 minutes" doc. Codes live in `contract/errors.json` and `extended/errors.py`; the pieces exist, the runbook does not.

- [ ] **Upstream bug links + removal conditions.** Both kept workarounds ("never bulk-print", "JSONEncode at source") have reasoning shipped, but no "Studio bug, workaround pending upstream" label, no bug-report link, no removal condition — `rsx-transport` is the closest and names no report.

- [ ] **Docs site drift.** `docs/catalog.js` is hand-maintained and matches the contract exactly today — zero drift by luck. No test reads `docs/`. Generate it from the contract, or assert every contract tool appears.

**Doc defects our own wording caused (each misled a reviewer, verified):**

- [ ] **`extended_server.py:1288` contradicts the repo's own measurement.** The live `extended_studio_identity` tool still emits *"Whether it survives a Studio restart is unverified"* — while `registry.py:30-33` records it **measured**: same place, same machine, `0_186696` before → `0_186502` after. The review read the tool output and concluded "currently UNVERIFIED"; the doc lies to callers in the conservative direction. Fix the tool string.

- [ ] **Breakpoint descriptions are asymmetric, and the asymmetry is wording, not behaviour.** `extended_clear_breakpoints` says *"Needs a play session."*; `extended_breakpoints` says only *"on a running server script"*. A reviewer concluded clear needs a session and set does not. Code refutes it: both go through the same `execute_luau` with `datamodel_type="Server"`, and live in Edit mode both return the identical `DATAMODAL_UNAVAILABLE`. (The *remedy* — success-with-note when the session has ended — is genuinely missing; `clear_breakpoints` raises on any non-`ok` status.) Doc fix: same precondition on both.

- [ ] **README names `EnumWindows`, which is not in the shipped code.** README:271 lists it alongside `os.startfile`/`%LOCALAPPDATA%` as a transport primitive. It appears only in `python/scripts/verify_*.ps1` and a skill describing a `PrintWindow` path the repo's own TODO says is unimplemented. The real primitives are `Get-CimInstance`, `lsof` and `os.startfile`. A reviewer sizing the POSIX port from this doc enumerated the wrong blockers.

- [ ] **README:274 "every platform difference is confined to one file" is false.** There are three: `roblox.py:43` (`cmd.exe /c mcp.bat` per non-darwin platform, i.e. Linux raises before handshake), `platform.py:132-149`, and `registry.py:285-296` (the tree's only XDG path). The "bounded, not open-ended" port estimate rests on this.

- [ ] **README "762 tests" is stale** — the Windows suite with the pyright gate is **781 passed, 2 skipped**.

- [ ] **A dead `REQUEST-luau-return-shapes.md` pointer survives outside `skills/`.** `extensions.py:769` and `tests/test_array_escape.py:5` still cite the report as being "in this repo"; `git log --all` shows it was never committed. I removed it from the skill and missed these two. `test_skill_refs.py` only scans `skills/*.md`, so nothing guards `python/src` or `python/tests` — widen the gate or drop the pointer.

- [ ] **`test_integration_studio.py:5` cites a CI job that does not exist.** It names `python-studio` in `ci.yml`; the workflow has one job, `python-test`, and installs no Studio.

- [ ] **`TODO.md`'s delete-tool blocker is stale.** It records "the audit freed net -2 (headroom 8); a ~150-char delete tool still does not fit" while `contract/tools.json` measures **175 chars of headroom** and per-tool max 383/450 — a ~150-char `delete_instance` fits today with no trimming.

### `extended_wait_for`'s probe only works in Edit mode

- [ ] **Audit every emitted-code path for the same blind spot** — still open, and no longer about `datamodel_type`. The chunked writer, the breakpoint `log_expression`, and the capture encoder all emit Luau and are all tested by asserting on *text*, which cannot tell executing code from echoed text. The narrower question is the one worth asking: **does each remain correct in Client/Server, or does any of them assume Edit?** That is a runtime check, not a text assertion.

- [ ] **`AssistantCommand` is a symptom, not the disease.** A script this project did not write, and its use of `loadstring` is independently wrong — but the technique reached it from this repo, documented prominently as the fix for a hang. A confidently-documented wrong technique travels further than an undocumented one.

- [ ] **MEASURED 2026-09-30: an unparseable condition wedges the whole server, not just the call.** `extended_wait_for({condition: "this is not lua(((", timeout_seconds: 4})` never returned; the MCP required a reconnect to recover. A second probe with the capture half removed hung identically, so the capture is exonerated and the condition is the whole cause. Mechanism, from the code just re-read: `build_probe` raw-splices the condition, so a non-parsing condition means the entire command fails parse; the parse happens before any handler runs (`PROBE_CAVEAT`), the `await studio.call(...)` never resolves, and because requests are served sequentially the wedged call blocks every later one — which is why the fix was reconnecting, not waiting. `timeout_seconds: 4` was bypassed entirely: the hang is in the first poll's await, before any deadline logic runs. Do NOT re-probe this live without a mitigation in place; every repro costs a reconnect. Mitigation candidates, unmeasured: a host-side timeout around the poll await (un-wedges the loop even though it cannot cancel Studio-side execution), versus establishing whether Studio-side command execution itself is wedged (in which case even that only converts a dead server into failing polls until Studio restarts).

- [ ] **`invalidate_process_cache` is the last dead symbol, and it is a judgement call rather than an oversight.** It has no caller in this repo, but it is public API on a module that is also importable as a library, and a consumer who starts a process out of band would want it. Left in place deliberately.

### Corrections to earlier notes in this file

- [ ] **The minimality of the URI is only half measured.** Four keys works. The key count and the value are settled, but whether `placeId` **alone** is enough was never established — a six-variant sweep would answer it, along with whether `universeId` can be dropped. *Re-measured 2026-10-05: the sweep script this named, `python/verify_uri_minimal.py`, is not in the repo and never was committed, so the measurement is unbuilt rather than pending — nothing is waiting on it.* The key is currently kept on the **user's authority**: they have worked with Studio launch arguments at length and confirmed `universeId` is required. That supersedes the earlier note here, which credited a local attempt that left `name: null` — retired as evidence, because a key that can be dropped fails in a way a process count cannot see.

- [ ] **NOT portable, and it is most of their repo.** Everything under `plugin/` is off limits: Roblox's `StudioMCP` is signed, so the hub, the `PluginConnection` star, the 8 ms `CooperativeJobRunner`, the `ChangeHistoryService` recording wrapper, and the `LogService.MessageOut` push journal all require owning the plugin. Their `docs/research-brief.md` reaches the same conclusion from the other side: `PluginConnectionService` has **zero third-party adoption** and its payload cap is undocumented. `bridge/src/sync/*` is a Rojo reimplementation and Rojo exists. `bridge/src/vision/*` is a product, not a technique.

### Launch routes fail at different layers (measured by renaming the exe)

- [ ] **`os.startfile`'s "Application not found" still conflates two causes.** *Protocol not registered* and *registered but pointing at a missing file* need opposite recoveries ("install Studio" vs "repair the handler"), and the second is checkable — resolve the registry command and test the path. Not fixed: it needs a registry read, which is Windows-only and not yet a platform primitive.

### Session operations

- [ ] **An edit to a `.py` file needs an MCP server restart** - the process holds the module in memory. The config change restarted it; the code fix did not. Same trap as the stale dist, and it cost a round of "still broken" measurements.

### Housekeeping

- [ ] The throwaway baseplate is a `roblox-studio-*.rbxl` file in the platform temp dir (one `mkstemp` file per `make_place`), launched with `--task EditFile --localPlaceFile`. Worth deleting when done — see `cleanup_throwaway_place`.

## Withdrawn — do not re-derive these

- > **Superseded 2026-10-01 on the "never run" part, not the rest.** There is still *(`macOS: researched and implemented, still never executed`)*
- ## The lock file, and a claim retracted *(`The lock file, and a claim retracted`)*
- - [x] **RETRACTED: the lock join does not separate concurrent URI launches.** `locks.py` claimed exactly that. Live, with two URI launches of one place: both reported the mesh name **`Place1`**, **neither held a lock**, and the only locks present were stale. It resolved **0 of 3** named rows. It wor *(`The lock file, and a claim retracted`)*
- retracted `os.path.basename` claim, and the retracted `locks.py` URI claim. *(`Housekeeping`)*
- ### RETRACTED: "the real universe id fails, so never fetch it" *(`The universe id is a function of the place id`)*
- **The claim is withdrawn; the measurement is not.** It is still the only recorded *(`The universe id is a function of the place id`)*


  > **Resolved 2026-10-03** - **fixed here** - all four: `save_path` failure mode (with the `CAPTURE_OK_RECOVERY` message and the arithmetic showing the base64 route is ~50x over the return ceiling), the reported `studio_id`, PrintWindow measured-not-built, and chunk size not a caller knob.