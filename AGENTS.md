# AGENTS.md — Roblox Studio MCP Project

## STANDING RULES

These are not conventions. Each one exists because the alternative was measured.
Bulk-data, agent-runtime sleep and commit-signing rules moved to the global
AGENTS.md on 2026-10-03 - they are not specific to this repository.

1. **Touch only a Studio instance you launched yourself, or one the user has
   explicitly put in scope.** Stated by the user 2026-10-02, replacing the rule
   keyed on the place name "Game TESTING". That rule treated a *name* as the
   handle on the thing to avoid, and a name is not one: a place can be reached
   without it ever appearing in this repository, and on 2026-10-02 it fired on a
   URL slug alone without the name being confirmable. The instance is the unit.
   - "Touch" covers read, write, execute, launch, stop and capture. An instance
     you did not create is off limits until the user grants it — by naming the
     instance, or by asking for the work that reaches it. If a tool call would
     reach an ungranted one, stop and ask.
   - A grant may be scoped. **Read-only** permits list, identity, grep and
     watch, and nothing else.
   - Grants are per-instance and never carry over to another one. None are
     currently recorded, and none are needed to state this: any instance you
     did not launch is already covered.
   - Before any call needing an explicit `studio_id`, resolve it with
     `extended_list_studios`. An omitted `studio_id` is a guess whenever more
     than one Studio is attached.
2. **A `.py` edit needs an MCP restart, and the command is
   `opencode service restart`.** The process holds the module in memory: a
   config change restarts it, a code fix does not. Without a restart you are
   measuring the old code and will conclude the fix did not work. It is
   `service`, not `server` — `opencode service restart`, verified against
   `opencode service --help`.
   - Restart **after** editing, **before** measuring anything live. Every
     result from a pre-restart Studio describes the old code, not the fix.
   - A restart re-initialises the `Roblox_Studio` namespace, so re-read the
     catalog with `search(...)` after one instead of assuming the tools are
     gone. Restarting is not yours alone — another agent restarting the same
     MCP mid-session drops the namespace out from under you too, and it
     changes *which* code you are measuring without announcing it.
3. **Saving a place: ask the user for local, `SavePlaceAsync` for cloud. These are
   different operations and conflating them wastes a session.**
   - **Local (`.rbxl` on disk) — there is no API. Ask the user to save.** Not a
     preference: the feature request for a local-save API is still open, and
     there is no plugin-side save either — `rodeo`, which does implement one
     end-to-end, drives it from the **host** side by firing `Cmd+S` and then
     waiting for the working file's mtime to move. So the mechanism is confirmed
     as keystroke emulation.
     - **Corrected 2026-10-03: the flakiness is in the *verification*, not the
       keystroke.** An earlier version of this line said the emulation "is flaky",
       which put the blame in the wrong place. What makes naive emulation
       unreliable is that **nothing confirms the save happened**. Waiting for the
       file's mtime to change, failing loudly when it does not, and copying
       atomically makes it dependable. **Still ask the user** — this is
       source-read evidence, not measured here, and the instruction should not
       move on someone else's implementation.
     - `user_keyboard_input` does **not** help — the docs
       file it under *Player input simulation* and it is client-datamodel only, so
       it drives the game, not the editor. Ask, then verify the file changed.
   - **Cloud — `AssetService:SavePlaceAsync`,** and the place id is **not** a free
     variable. Two cases, both measured here, and they differ:
      - *Template place* — the id is the template's own, read off the
        `Template_<id>_AutoRecovery_<n>` filename by `template_place_id()`. Pair
        it with **universe id 0** (`URI_UNIVERSE_ID`) to say "this place, no
        universe context" — which is what a template is, and what *File > New*
        emits. **Or pass no universe at all**, which derives one from the place id
        via the API: `build_launch_uri(place_id)` does that, and both forms are
        measured to open the place.
        - **Do not read "use universe id 0 for template places" as a rule.** An
          earlier version of this file said exactly that, because the place's real
          universe id was recorded as "failed 3 times in 16 with `Error fetching
          latest place version`". Re-tested 2026-10-02, that did not reproduce:
          **8 of 8 launches on the real id succeeded on the first attempt**, 6 of 6
          on 0, zero errors, no retries needed. The 3-of-16 is the only
          counter-example and could not be reproduced. **The claim is withdrawn,
          not the measurement** — see `docs/EVIDENCE.md`, *The universe id is a
          function of the place id*.
     - *Unsaved local place* — there is **no place id to pass**. Studio reports
       `PlaceId: 0`, and this project's own copies are named `Baseplate-<n>.rbxl`,
       for which `template_place_id()` returns `None` rather than a fabricated one.
       Pass no `PlaceId`.
     It writes to Roblox's cloud, **not** to disk; it is **rate-limited** per minute
     *and* per player; and it needs `AssetCreateUpdate` on the place. Ask first,
     because omitting `PlaceId` saves the *open* place, which may be a published one.
   - **Never** use "saveinstance" scripts — executor-based exploit tooling that
     works by ignoring Roblox's ownership and auth model. Same reason as rule 1:
     the mechanism is the violation. `docs/EVIDENCE.md` records why, unnamed.
   - **No MCP exposes any of this.** Not the official `Roblox/studio-rust-mcp-server`
     (6 tools), not the built-in Studio MCP (34 documented tools), and not the
     four community servers checked. Source and method in `docs/EVIDENCE.md`.
4. **Run the gates before claiming anything works.** `pytest`, which already
   includes the type gate. A gate that has not been run is not a gate.
## Gates

| | command | what it catches |
|---|---|---|
| Python tests | `python -m pytest tests -q` | behaviour. **Already includes the type gate** — `test_typecheck.py` runs pyright as a subprocess and is collected by this command. Running it separately is a second, redundant pass. |
| Python types | covered above | wrong key, `None` deref, wrong argument type |
| Contract | `pytest tests/test_contract.py` | generated contract still matches the server |

Two of these catch **silent** wrong answers, which is the failure class this
project keeps paying for:

- **Unknown arguments are refused** before dispatch. A silently
  ignored parameter produces a *plausible wrong answer* and reports success —
  three separate incidents here did exactly that. Relayed Studio tools
  (`screen_capture`) are deliberately exempt, because this project cannot add
  parameters to them.
- **`tests/test_closed_sets.py`** checks exhaustiveness over the closed sets:
  every error code is producible, every advertised `action` is handled rather
  than merely accepted. This is what found `LAUNCH_FAILED` being unreachable —
  something no type checker would have caught.

**A run refused upstream reads as silence** — the same failure class from the
other direction: not a wrong answer but no answer. `error.type` on the
assistant row is the discriminator: `provider.*` is refused or truncated
upstream, `aborted` is a deliberate stop, not a failure. Only rows that
consumed **no tokens at all** read as absence — 5 refusals (402 quota, 403
auth) *and* 3 stops, measured across this machine's session store 2026-10-04.
One refusal cost a review arm on 2026-10-03 and three of four still came back,
so the batch **read** as clean — nothing reported otherwise. `finish=error` is
**not** the field: roughly five of every six `finish=error` rows are `aborted`.
That ratio holds as the box is used, the absolute counts do not, so take the
ratio. **Settles against:** a refused run leaving any trace besides the
assistant row's `error.type`.

`contract/tools.json` is generated by `contract/build_contract.py` and asserted by
the suite. Regenerate it after a *deliberate* surface change; the diff is the
contract report.

Type checking: **pyright**, chosen over mypy on measured evidence — both found
the same 7 real defects, but mypy also reported a false positive at
`instance.py:728` (it narrows the loop variable in a dict comprehension, not the
resulting container). The reasoning is in `docs/EVIDENCE.md`. Note the honest
limit: a defaulted `.get()` on a missing key is legal, so the checker is a
backstop and the `TypedDict` on `WatchResult` is the actual fix.

## Launching a Studio

`extended_manage_instance` `action=launch` takes one argument, the place. Two
routes, and their traps differ — read the one you are on.

**URI route** (a published place), dispatched with `os.startfile`;
`subprocess.Popen` cannot do it:

```
roblox-studio:1+task:EditPlace+placeId:<id>+universeId:<id>
```

- **Four keys, and both ids.** Four is what `build_launch_uri()` emits and what
  `test_closed_sets.py::LaunchUriIsComplete` pins — **not** a measured minimum.
  `TODO.md` records that the minimality is only half measured, so do not read
  this as "fewer will not work"; it has not been established either way.
- **Dropping `universeId` still attaches**, so a process count passes — but
  Studio comes up with no place open, which only shows when you ask it for a
  name. This is the trap: the launch *looks* successful.
- *Uncorroborated here:* an earlier eleven-key prefix failed with an `Error`
  dialog and never attached. No source in this repo records it, and no log on
  this machine either — the oldest Studio log is 2026-10-02, after the fact.
  **Settles against:** the session where it failed, if one is ever found.

**File route** (a place that exists only on disk):
`--task EditFile --localPlaceFile <path>`.

- A launch that starts but fails to identify returns `launched: false` **with
  `started_but_unidentified: true`** and an `identified_by` field. **Do not
  re-launch.** The process is live and a second launch orphans it. Read the
  Studio log under `%LOCALAPPDATA%\Roblox\logs` for `State: OpenPlaceFailure`
  instead; a `name: null` check is meaningless on this route. **This bug once
  shipped and always reported failure on a successful launch, leaving the orphan
  invisible** — it is why the flag exists, not a hypothetical.
  *The `OpenPlaceFailure` string itself is uncorroborated.* No source in this
  repo contains it; the sibling `State: OpenPlaceInitialization` is real and
  appears in this machine's logs, but `OpenPlaceFailure` appears in none of
  them, and none of those 15 sessions failed a launch, so its absence bounds
  nothing. **Settles against:** one real failed launch.
- `action=list` returns `processes` and `mesh` as two lists, **deliberately
  unjoined** — the join is what cannot be trusted when two Studios share a place
  and both report mesh name `Place1`.

## MCP Server

Uses the **extended** Studio MCP server (`roblox_studio_mcp.extended_server`),
which adds **16** tools on top of the raw Studio MCP tools:

- `extended_write_script` — full-body script replacement with status return (`created` / `wrote` / `unchanged`)
- `extended_update_script` — batch edits with graceful skipping (`skip_missing`, `skip_no_ops`) and structured `UpdateResult`
- `extended_script_grep` — regex/substring grep with context lines, in file order (nothing ranks them) (`regex=true` for PCRE)
- `extended_script_search_and_read` — search for scripts + batch-read their source
- `extended_insert_asset_from_file` — insert local files (scripts via write_script, models via game:LoadLocalAsset, images via store_image)
- `extended_watch_output` — console lines since your last call, filtered by `pattern`, capped by `max_lines`
- `extended_run_tests` — play test + capture console output as test summary
- `extended_execute_luau_from_file` — execute Luau source read from a local file
- `extended_breakpoints` / `extended_clear_breakpoints` — non-halting logpoints
- `extended_capture` — lossless PNG
- `extended_skill` — transport skills, read before driving the transport
- `extended_studio_identity` / `extended_list_studios` — instance targeting
- `extended_wait_for` — host-side polling, the only way to wait
- `extended_manage_instance` — Studio instances and places: list, places, make_place, launch, stop

### Registered as the `Roblox_Studio` MCP client.

### The tool list is budgeted

The tool descriptions are budgeted — a per-tool cap and a total cap — and
`tests/test_contract.py` enforces both. The list is paid on every call of every
session, so adding a tool is a deliberate decision about permanent cost, not a
free addition.

**No cap, usage or headroom figure is written here, and that is deliberate.**
This section has carried a wrong one three times: a total that survived a cap
raise, and two different headroom figures in *adjacent bullets* that disagreed
with each other and with the contract. That is the same drift that hit the tool
count in two other places, twice. Every figure belongs in one place only:

| want | read |
| --- | --- |
| the live caps and usage | `contract/tools.json` - `total_description_cap`, `per_tool_description_cap`, `total_description_chars` |
| whether a change fits | regenerate and read the diff |
| why a figure was what it was | `docs/EVIDENCE.md`, *The tool list is budgeted* |

`test_docs_freshness.py` enforces the rule above by failing if a cap-like figure
appears in this section. It encodes no number itself, so it cannot rot the way
the prose did. `test_contract.py` likewise derives its caps from the contract
rather than hardcoding them, so **the enforcement was never the thing that
drifted** - only the documentation was.

New surface is funded by trimming existing descriptions, never by raising the
cap. The tool list is paid on every call, every session: a cap raise is a real
cost, so it is a decision for the user, not a silent change.

## Where the record lives

`docs/EVIDENCE.md` is the record of what was **measured** versus **inferred**,
with provenance kept separate on purpose; `TODO.md` carries the open work and the
withdrawals. Read the first before proposing anything that depends on a prior
finding — several plausible-sounding ideas are already marked retracted, and the
reason is usually a measurement that contradicted the obvious reading.
