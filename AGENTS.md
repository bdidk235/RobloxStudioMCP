# Claude.md — Roblox Studio MCP Project

## STANDING RULES

These are not conventions. Each one exists because the alternative was measured.

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
2. **Never print bulk data.** A ~5 MB `print` permanently wedged
   `get_console_output` for a Studio. Report measurements through
   `inspect_instance` attributes instead.
3. **Never sleep in the agent's runtime.** It has no timer, so the call hangs to
   timeout. Use `extended_wait_for`, or issue a wait as its own shell command.
4. **Mirror a change to Node in the same pass — but never do catch-up ports.**
   Parity is a *property of each change*, not a workstream. When you touch
   `extended/foo.py`, touch `extended/foo.ts` before you finish. What you must
   **not** do is go back and port what is already behind: that is how this
   project acquired 1,012 lines of debt, and the debt is now **frozen** rather
   than paid off. `node/src/extended/IDENTITY.md` records the gap and why.
   - *Cheap, do it:* a fix or feature lands on both sides together.
   - *Expensive, do not:* porting an existing Python-only module to Node.
     If Node is behind on something, it stays behind and the file says so.
   The surface invariant is still enforced — `parity/tools.json` fails the build
   if either side's tool list drifts. Behavioural parity is best-effort and
   documented.
5. **A `.py` edit needs an MCP restart, and the command is
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
6. **Saving a place: ask the user for local, `SavePlaceAsync` for cloud. These are
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
          not the measurement** — see `TODO.md`, *The universe id is a function of
          the place id*.
     - *Unsaved local place* — there is **no place id to pass**. Studio reports
       `PlaceId: 0`, and this project's own copies are named `Baseplate-<n>.rbxl`,
       for which `template_place_id()` returns `None` rather than a fabricated one.
       Pass no `PlaceId`.
     It writes to Roblox's cloud, **not** to disk; it is **rate-limited** per minute
     *and* per player; and it needs `AssetCreateUpdate` on the place. Ask first,
     because omitting `PlaceId` saves the *open* place, which may be a published one.
   - **Never** use "saveinstance" scripts — executor-based exploit tooling that
     works by ignoring Roblox's ownership and auth model. Same reason as rule 1:
     the mechanism is the violation. `TODO.md` records why, without naming them.
   - **No MCP exposes any of this.** Not the official `Roblox/studio-rust-mcp-server`
     (6 tools), not the built-in Studio MCP (34 documented tools), and not the
     four community servers checked. Source and method in `TODO.md`.
7. **Run the gates before claiming anything works.** `pytest`, `tsc --noEmit`,
   and both suites. A gate that has not been run is not a gate.
8. **Commits are SSH-signed. Do not pass `--no-gpg-sign`** — that instruction
   was a workaround for a key that did not exist, and outlived its cause.
   `commit.gpgsign` and `tag.gpgsign` are both `true`, so a plain `git commit`
   is already signed. The key carries **no passphrase**, deliberately: an
   unattended agent cannot answer a pinentry prompt. The cost is that anything
   running as this user can sign as them; `ssh-agent` is the fix if wanted.
   - **GitHub needs the public key under *Signing keys*, not *Authentication
     keys*.** Measured 2026-10-02: a commit signed by a key GitHub did not know
     came back `verified: false, reason: unknown_key` — while `git verify-commit`
     still reported `Good signature`. **The local check cannot detect this class
     of failure**; only GitHub's verdict on a pushed commit can.
   - **Compare fingerprints, never eyeball the base64.** The key was registered
     from a string copied out of earlier output instead of re-read from disk, so
     GitHub held a key whose private half had been deleted — and it presented as
     `unknown_key`. `ssh-keygen -lf <key>.pub` settles it in one command.
   - `git verify-commit HEAD`; `%G?` is `G` for good. PowerShell gotcha:
     `ssh-keygen -N '""'` sets a passphrase of the literal two characters `""`,
     so generate through `cmd`, where `""` really is empty.
   - **Not done:** history is unsigned; re-signing rewrites every SHA on `main`.
     Full transcript and a retraction of mine: `TODO.md`, *Commit signing*.

## Gates

| | command | what it catches |
|---|---|---|
| Python tests | `python -m pytest tests -q` | behaviour. **Already includes the type gate** — `test_typecheck.py` runs pyright as a subprocess and is collected by this command. Running it separately is a second, redundant pass. |
| Python types | covered above | wrong key, `None` deref, wrong argument type |
| Node types | `pnpm --dir node typecheck` | the same, at compile time. **Use this, not bare `tsc --noEmit`** — the script is `tsc --noEmit -p tsconfig.check.json`, a stricter project that includes the tests. Measured 2026-10-01: the bare invocation passed a file the checked one rejected, and the difference shipped to `main` and turned CI red. |
| Node tests | `npx vitest run` | behaviour |
| Build freshness | included in vitest | `dist/` older than `src/` |
| **Parity** | `pytest tests/test_parity.py` + `npx vitest run tests/parity.test.ts` | either side's tool surface drifting |

Two of these catch **silent** wrong answers, which is the failure class this
project keeps paying for:

- **Unknown arguments are refused** before dispatch, on both sides. A silently
  ignored parameter produces a *plausible wrong answer* and reports success —
  three separate incidents here did exactly that. Relayed Studio tools
  (`screen_capture`) are deliberately exempt, because this project cannot add
  parameters to them.
- **`tests/test_closed_sets.py`** checks exhaustiveness over the closed sets:
  every error code is producible, every advertised `action` is handled rather
  than merely accepted. This is what found `LAUNCH_FAILED` being unreachable —
  something no type checker on either side would have caught.

`parity/tools.json` is generated by `parity/build_contract.py` and asserted by
both suites. Regenerate it after a *deliberate* surface change; the diff is the
parity report.

Type checking: **pyright**, chosen over mypy on measured evidence — both found
the same 7 real defects, but mypy also reported a false positive at
`instance.py:728` (it narrows the loop variable in a dict comprehension, not the
resulting container). The reasoning is in `TODO.md`. Note the honest limit: a
defaulted `.get()` on a missing key is legal in **both**, so the checker is a
backstop and the `TypedDict` on `WatchResult` is the actual fix.

## Where the two implementations differ

They are at parity on the tool surface (16 tools, identical schemas, enforced).
They are **not** equivalent underneath: Python resolves `studio_id → PID` by
reading the Studio's own log, and that chain (`logid.ts`'s Python original) is not
ported to Node. So on a machine with two Studios open on one place — the normal
case — Node's `action=stop` **refuses** rather than guessing, because it is
irreversible. Read `node/src/extended/IDENTITY.md` before relying on Node for
instance control.

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
`tests/test_parity.py` enforces both. The list is paid on every call of every
session, so adding a tool is a deliberate decision about permanent cost, not a
free addition.

**No cap, usage or headroom figure is written here, and that is deliberate.**
This section has carried a wrong one three times: a total that survived a cap
raise, and two different headroom figures in *adjacent bullets* that disagreed
with each other and with the contract. That is the same drift that hit the tool
count in two other places, twice. Every figure belongs in one place only:

| want | read |
| --- | --- |
| the live caps and usage | `parity/tools.json` - `total_description_cap`, `per_tool_description_cap`, `total_description_chars` |
| whether a change fits | regenerate and read the diff |
| why a figure was what it was | `TODO.md`, *The tool list is budgeted* |

`test_docs_freshness.py` enforces the rule above by failing if a cap-like figure
appears in this section. It encodes no number itself, so it cannot rot the way
the prose did. `test_parity.py` likewise derives its caps from the contract
rather than hardcoding them, so **the enforcement was never the thing that
drifted** - only the documentation was.

New surface is funded by trimming existing descriptions, never by raising the
cap. The tool list is paid on every call, every session: a cap raise is a real
cost, so it is a decision for the user, not a silent change.

## MCP Tool Inventory (current session)

### Scripts & Search
- `mcp__Roblox_Studio__script_read`
- `mcp__Roblox_Studio__script_search`
- `mcp__Roblox_Studio__script_grep`
- `mcp__Roblox_Studio__extended_script_grep`
- `mcp__Roblox_Studio__search_game_tree`
- `mcp__Roblox_Studio__inspect_instance`
- `mcp__Roblox_Studio__extended_script_search_and_read`

### Editing
- `mcp__Roblox_Studio__multi_edit` (raw string replacement)
- `mcp__Roblox_Studio__extended_write_script` (full-body replace)
- `mcp__Roblox_Studio__extended_update_script` (batch, graceful skip)
- `mcp__Roblox_Studio__extended_insert_asset_from_file` (local file → asset insert)

### Extended Conveniences
- `mcp__Roblox_Studio__extended_watch_output` (live-tail console output)
- `mcp__Roblox_Studio__extended_run_tests` (play testing + console summary)

### Code Execution
- `mcp__Roblox_Studio__execute_luau`
- `mcp__Roblox_Studio__extended_execute_luau_from_file` (local file → execute)

### Assets
- `mcp__Roblox_Studio__search_asset`
- `mcp__Roblox_Studio__insert_asset`
- `mcp__Roblox_Studio__store_image`

### Studio Control
- `mcp__Roblox_Studio__list_roblox_studios`
- `mcp__Roblox_Studio__get_studio_state`
- `mcp__Roblox_Studio__start_stop_play`
- `mcp__Roblox_Studio__get_console_output`

### Generation
- `mcp__Roblox_Studio__generate_procedural_model`
- `mcp__Roblox_Studio__generate_texture`
- `mcp__Roblox_Studio__run_as_job`
- `mcp__Roblox_Studio__skill`

### Interaction (Client dataModel only)
- `mcp__Roblox_Studio__character_navigation`
- `mcp__Roblox_Studio__user_keyboard_input`
- `mcp__Roblox_Studio__user_mouse_input`
- `mcp__Roblox_Studio__screen_capture`

### Permissions
Per user settings.json, the following are **denied**:
- `Bash`
- `mcp__Roblox_Studio__generate_mesh`
- `mcp__Roblox_Studio__segment_mesh`
- `mcp__Roblox_Studio__generate_material`
- `mcp__Roblox_Studio__subagent`
- `mcp__Roblox_Studio__upload_image`

## Programmatic Use

All extended tools can also be used directly from Python:

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

## Project Structure

```
src/roblox_studio_mcp/
├── __init__.py
├── client.py        ← generic MCP JSON-RPC client
├── roblox.py        ← RobloxStudio convenience wrapper
├── server.py        ← stdio MCP server (base, passes through to Studio MCP)
├── extended.py      ← RobloxStudio re-export for subpackage imports
├── extended_server.py ← stdio MCP server with the 16 extended tools
├── types.py
├── errors.py        ← error codes
└── extended/
    ├── writer.py       ← write_script + chunked _chunked_write
    ├── updater.py      ← update_script + UpdateResult
    ├── extensions.py   ← search_and_read, insert_asset, watch_output,
    │                     run_tests, execute_luau_from_file, WatchResult
    ├── errors.py       ← ToolError, classify, the 15 codes
    ├── waiting.py      ← extended_wait_for: host-side polling + probe wrapper
    ├── platform.py     ← EVERY platform difference (PowerShell, paths, ps)
    ├── instance.py     ← launch / list / stop Studio
    ├── logid.py        ← studio_id -> PID, from the Studio's own log
    ├── locks.py        ← .lock file PID join
    ├── capture.py      ← lossless RGBA -> PNG
    ├── breakpoints.py  ← non-halting logpoints
    ├── grep.py
    ├── registry.py     ← host-side identity state (never in the place)
    └── skills.py       ← the rsx-* transport skills

parity/
├── build_contract.py  ← generates tools.json from the Python server
├── tools.json         ← the shared contract BOTH suites assert
└── compare_implementations.py ← what differs between the two, with numbers

node/src/extended/      ← the same surface in TypeScript
node/src/extended/IDENTITY.md ← the one place they are NOT equivalent

skills/rsx-*.md         ← transport skills, surfaced through extended_skill
```

### Evidence lives in `TODO.md`

That file is the record of what was **measured** versus **inferred**, with
provenance kept separate on purpose. Read it before proposing anything that
depends on a prior finding — several plausible-sounding ideas in it are already
marked retracted, and the reason is usually a measurement that contradicted the
obvious reading.

### Chunked Write (>200K content)

`_chunked_write` uses multiple `execute_luau` calls to pass more than 200K lines
as Lua long-bracket strings (`[==========[..]==========]`),
iterates with `UpdateSourceAsync(target, function(old) return old .. slice end)`.
Studio handles the >200K accumulation internally, bypassing the `Script.Source` limit.
