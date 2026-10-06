# HISTORY — how this repository got to its current shape

Written 2026-10-06 for a reader who has none of the context this file carries.
Every claim below traces to a sha, a `file:line`, or a command listed in
[§11](#11-how-to-re-derive-every-number-here). Where the repository
contradicts something commonly said about it, the repository wins and the
contradiction is recorded in [§9](#9-defects-in-this-repositorys-own-record).

**Rewritten 2026-10-06.** Every sha in the prose is the **pre-rewrite** value,
measured before `main`'s history was rewritten; see
[§9.4](#94-the-history-rewrite-of-2026-10-06). The old shas still resolve, and
the tip tree is unchanged.

This file is a **history**, not a status report. For what is open today read
`TODO.md`; for why a design decision was taken read `docs/EVIDENCE.md`; for what
is true right now read the live file. A number here can be stale in a way a
number in a generated artefact cannot — which is the lesson
[`docs/EVIDENCE.md:1049`](EVIDENCE.md) exists to record.

---

## 1. Shape at a glance

Measured 2026-10-06 against `main` = `7189ce6`. **Every sha in this file was
measured before the history rewrite of 2026-10-06 and is therefore the
pre-rewrite value.** The pre-rewrite shas still resolve, on
`origin/backup/pre-public-2026-10-06`; [§9.4](#94-the-history-rewrite-of-2026-10-06)
gives the old→new mapping for all 44 shas cited here.

| | value | how |
|---|---|---|
| Commits on `main` | **93** (was 96 pre-rewrite) | `git rev-list --count main` |
| Date span | **2026-09-13 → 2026-10-06** | `git log --format=%ad --date=short main \| sort` |
| Commits reachable from `--all` | **219** | `git rev-list --count --all \| sort -u \| wc -l` |
| Refs | `main`, `parity`, `backup/…`, `origin/*`, `refs/original/*`, `refs/stash` | `git for-each-ref` |
| `parity` tip | `bd77d97`, **67** commits, **no longer an ancestor of `main`** | `git merge-base --is-ancestor parity main` → exit 1; `git merge-base main parity` → empty |
| Tracked files at `HEAD` | **111** | `git ls-tree -r --name-only HEAD \| wc -l` |
| Tracked files on `parity` | **168**, of which **56** under `node/` | `git ls-tree -r --name-only parity` |
| Live gate | **744 passed, 2 skipped, 206 subtests** | `python -m pytest tests -q` from `python/` |
| Contract surface | 16 tools, 3,025 of 3,200 description chars, 450 per tool | `contract/tools.json` |
| Open work | **36** top-level `- [ ]` items | `grep -c '^- \[ \]' TODO.md` |
| `docs/EVIDENCE.md` | 1,455 lines, 25 `##` sections | `wc -l`, `grep -c '^## '` |
| `AGENTS.md` | **2,250** words against a 2,259 ceiling — **9 to spare** | `python -c "print(len(open('AGENTS.md').read().split()))"` |
| Remote | `https://github.com/bdidk235/RobloxStudioMCP_Full.git` (private) | `git config --get remote.origin.url` |

Commits per day on `main`: 1 on 09-13, 15 on 09-14, 28 on 10-01, 7 on 10-02,
19 on 10-03, 11 on 10-04, 8 on 10-05, 4 on 10-06. The 10-01 spike is
[`940c7b8`](https://github.com/bdidk235/RobloxStudioMCP_Full) *Add the Node
extended tool surface* and the surrounding macOS-CI diagnosis, not a
development phase of its own.

**The 219 figure is not the size of the project.** It is `sort -u`'d, and three
distinct *versions* of this repository's history are reachable at once: the
rewritten `main` (93), the original pre-rewrite line held by
`origin/backup/pre-public-2026-10-06` (96), and `parity` (67), plus a 20-commit
stash. Overlapping commits are counted once, so 219 is smaller than the sum.
Before the rewrite the same command returned 94, when two refs pointed at the
same 92-commit line and the only extra was the stash pair `dff5f0e` /
`b82d41f` — both dated 2026-09-14, both parented on `7768bc7`, which
`git stash list` reports as `stash@{0}: On main: temp tsconfig change`.

---

## 2. The eras

### 2.1 Origins, and a docs site that came first (2026-09-13 → 09-14)

`74bc803` *Initial commit* is 29 files and contains **no Python and no
`node/`**: it is `docs/` (`app.js`, `catalog.js`, `components.js`, `content.js`,
`index.html`, `styles.css`), `README.md`, `.gitignore`, `.gitattributes` and
`package.json` / `pnpm-lock.yaml` / `pnpm-workspace.yaml`. The repository began
as a documentation site with a JavaScript build; the MCP client arrived a day
later.

`231605e` *Unify original Python client and Node port with live-Studio CI*
(2026-09-14) is where both clients and the dual shape begin. It moved a Node
client into `node/` (**28** files added, measured with `git show --name-only
231605e -- node | wc -l`), put the Python client in `python/`, backported
TS-ahead features to Python with matching tests, and split CI into four jobs:
`python-test`, `node-test`, `python-studio`, `node-studio`.

### 2.2 The live-Studio CI era, and why it ended (2026-09-14 → 10-01)

Twenty-eight commits on 2026-10-01 alone are the record of trying to run Roblox
Studio inside GitHub Actions. The thread is legible from the subjects:

- `f25bba6` *Open a place in CI: the no-place launch could never attach* —
  the launch route was reaching no place at all.
- `b06d49c` / `702f1c2` *Record the confirmed cause of the cookie revocation* /
  *Record Account Session Protection as the confirmed cause* — credential
  seeding was being revoked upstream.
- `a8e8fbc` *Localise the macOS attach failure: the burner account is not
  signed in* — and `b21f3d5` *Read the run: `Authenticated : NO`* — the cookie
  store being seeded was the wrong one.
- `4b57c80` *Retire the live-Studio CI tooling and the ROBLOSECURITY
  credential* — the attempt ended in abandonment, not success.
- `2654794` … `f7afb04` — a read-only macOS bundle probe written and measured
  instead, kept reproducible as `.github/workflows/studio-bundle-probe.yml`
  (manual, `workflow_dispatch`, no secret, ~20 s, per
  `README.md:430-435`).

`c1d4ef7` *Drop Studio jobs from CI; unit tests only* is the commit that ended
it. The residual is the standing statement in `README.md:202-217`: Windows
tested, macOS supported but unproven, Linux not supported at all — a missing
layer, not a bug.

### 2.3 Dual implementation → Python-only (2026-10-01 → 10-03)

`940c7b8` *Add the Node extended tool surface; harden identity, launch and
capture* grew `node/` from 28 to 55 tracked files. `bd77d97` *Fix the two
silent-wrong-answer bugs the parity review found in Node* is `parity`'s tip and
the last commit to touch `node/` with intent. Then `e91cdb3` removed the Node
tree from `main`. See [§4](#4-the-python-only-migration) — the reason is the
interesting part, and it is partly wrong.

### 2.4 The documentation passes (2026-10-02 → 10-05)

This repository spent five days deleting prose. The line counts, measured by
`git show <sha>:<path> | wc -l` rather than quoted:

| file | before | after | commit |
|---|---|---|---|
| `AGENTS.md` | 355 | 206 | `20d9800` |
| `TODO.md` | 4,135 | 2,790 | `9bb8e48` |
| `TODO.md` | 2,790 | 170 | `9b44c51` (closed research moved to a new `docs/EVIDENCE.md`, created here at 1,348 lines) |
| `docs/EVIDENCE.md` | 1,348 (at creation) | 1,455 (now) | `9b44c51` onward |

The argument for each cut is in the commit body, and they are consistent: a
rules file that carries a second copy of documentation will drift from it.
`20d9800` deleted a 56-line `MCP Tool Inventory` from `AGENTS.md` that listed
**none** of the seven `extended_*` tools an agent actually calls, and moved the
file tree and the programmatic-use example into `README.md`, which already had
sections to hold them.

`eea8081` *Make the repo workable by someone who has never seen it* is the
diagnostic that started this. It found `AGENTS.md` still quoting a 2,700-char
cap after the cap had been raised, and `TODO.md` quoting "7 characters" and
"8 characters" of headroom in **adjacent bullets** — so the file was
self-contradictory, not merely stale. `docs/EVIDENCE.md:1049-1114` is the
retrospective, including the rule that followed: `test_docs_freshness.py`
asserts **the rule, not the value** — no cap-like figure may appear in the
normative section — so the gate cannot rot the way the prose did.

Two later passes are worth naming because they *removed* rather than corrected:
`633af67` *Remove the stale cost figures instead of refreshing them* and
`e9ff9ec` *State in the README that this project is AI-written*.

### 2.5 The gate era (2026-10-03 → 10-05)

Also covered in [§5](#5-the-gate-work). In order: `c4b0887` (interrogate
verdict, 18 findings), `87051b6` (`parity/` → `contract/`), `9fb5a1d` (pyright
was never in CI), `2e4f27e` (pin the contract generator), `c5ac10c` (path
confinement), `7d5ff53` (the `AGENTS.md` word ceiling), `954e07a` + `6681226`
(three tests CI caught), `1c08e50` (the relay double-send).

---

## 3. `branch parity`

**What it preserves.** The full dual-implementation state as of `bd77d97`:
56 `node/` files (24 `node/src`, 19 `node/tests`, 6 `node/examples`, and the
rest packaging), the Python tree, and `parity/compare_implementations.py` +
`parity/compare_descriptions.py` — two scripts that exist *only* to diff or read
the Node tree, and so exist nowhere on `main`. Measured 2026-10-06 with
`git ls-tree -r --name-only parity | awk -F/ '{print $1"/"$2}' | sort | uniq -c`.

**It was an ancestor of `main`; after the 2026-10-06 rewrite it is not.** Before
the rewrite: `git merge-base --is-ancestor bd77d97 main` exited 0 and
`git rev-list --count main..parity` returned 0 — `main` carried `parity`'s tip
and continued past it. That is now false. `git merge-base main parity` returns
**empty**: the two branches share no commit at all, because `parity` was left
alone while `main`'s 96 commits were rewritten, and the 67 they previously shared
were given new shas on `main` only. `git rev-list --count main..parity` is
now **67**, i.e. all of it.

Nothing was lost and no merge is needed — `parity` is intact at `bd77d97` and
still holds the Node tree — but the reversibility claim in `e91cdb3`'s body, *"Branch
`parity` at bd77d97 holds the full dual state, so this is reversible"*, now
depends on `parity` surviving as a branch rather than on it being reachable from
`main`. **Deleting `parity` would destroy the only ref holding the Node
implementation.** Measured 2026-10-06 after the rewrite.

**Why it exists rather than a tag.** A tag names a point; the branch is where
the Node work is still reviewable, including the test suite that would be
reinstated with it.

**Third branch, different purpose.** `backup/pre-public-2026-10-06` was a
pre-publication marker pointing at `1c08e50`, the same commit as `main`. It has
since been moved twice by the rewrite and now sits at `a41d4b8`, an
*intermediate* state — after the trailer and `node_modules` rewrites, before the
`node/dist` one. **It is not a restore point for any state described in this
file**, and it is the ref that still keeps the pre-rewrite shas alive. Its
purpose is now ambiguous and it should be resolved deliberately; see
[§9.4](#94-the-history-rewrite-of-2026-10-06). *Falsified by
`git branch -v` showing it at a third sha.*

---

## 4. The Python-only migration

### 4.1 What happened

`e91cdb3` *Go Python-only on main; preserve the dual implementation on branch
parity* (2026-10-03). Deleted `node/` (~9,940 source lines, ~6,500 test lines,
6 examples), `python/measure_node_surface.py`, and both `parity/compare_*.py`
scripts. Removed the `node-test` job from `.github/workflows/ci.yml` and the
node paths from `.gitignore`. Reframed `README.md`, `AGENTS.md` and
`CONTRIBUTING.md` single-client. Renamed the tree in `87051b6`:
`parity/` → `contract/`, `test_parity.py` → `test_contract.py`,
`SchemaParity` → `SchemaContract` — because *"the package stopped being a
comparison when Node left; it is the generated contract both suites assert."*
19 files, all mechanical.

### 4.2 The recorded reason, and where it was overstated

**The claim as made.** Three independent reviews (transport, Studio I/O,
authoring) "found the same shape: Python leads functionally in every group, and
Node's remaining gaps are all silent-wrong-answer bugs" (`e91cdb3` body). The
named gaps were: `consoleLines` where the contract says `console_lines`; bare
`nil`/`unknown` in `MISSING_HINTS`; `placeFromCommandLine` truncating at
spaces; `readOnlyHint` computed and dropped on the wire.

**It was re-derived by running it**, and the answer differs.
[`docs/EVIDENCE.md:1411`](EVIDENCE.md) — *The strip's justification, re-derived
by running it (2026-10-04)*. Method: a `git worktree` at `bd77d97` on `parity`,
node v26.4.0, `npm install && npm run build && npm run typecheck` (clean) and
`npm test` → **688 passed, 1 skipped**, matching `bd77d97` exactly. Then
`parity/compare_implementations.py`, pure static analysis.

| named gap | measured | class |
|---|---|---|
| `placeFromCommandLine` truncates at the first space | `dist/extended/platform.js:379`; given `-localPlaceFile "C:\Users\My User\place.rbxl"` returns **`"My"`**; `"C:\Program Files\..."` returns **`"Program"`**. No throw, exit 0 | **silent wrong answer** |
| Node "computes `readOnlyHint` and ships 0 of 7" | 16 tools, 7 carry `readOnly === true` (`extendedServer.js:858,868`); a wire entry serialises `name, description, inputSchema` only, so 0 of 7 ship | **safe omission** — an unmarked set reads as "may mutate", which is safe |
| `instance.ts` never imports `logid.ts` | both `logid` mentions in `instance.js` are comments (lines 6, 12); the import at line 28 takes `processRows` from `platform.js` | **honest refusal** |

**1 of 3 is a silent wrong answer. The justification is overstated; the decision
is not.** The same re-derivation measured Python ahead — 7,642 lines of
`extended/` against Node's 6,859; 10,471 test lines in 38 files against Node's
7,309 in 19; **5,708 Python-only test lines** across 24 subjects Node never
covered — and found that Node's *one-sided-risk* section names a `split()`
semantics mismatch as already having fired (Python's `split(None, n)` keeps the
remainder, JS truncates; "every row came back null" in the macOS process
parser). Same family as the `"My"` above: whitespace handled differently across
two languages, yielding a plausible wrong value.

The honest statement, in `docs/EVIDENCE.md`'s words: *python was further ahead,
and node was exposed to a class of cross-language bug that had already produced
a real defect* — not *"every remaining gap was a silent wrong answer."*

**Falsifier, stated in the file:** re-running the harness on `parity` and
finding a fourth gap that *is* a silent wrong answer, which would make the
justification understated instead.

### 4.3 What was deliberately kept

`e91cdb3`'s body is explicit: resolved `TODO.md` items and `docs/EVIDENCE.md`
keep their verbatim record, **including Node filenames** — *"history must not be
rewritten to match the present."* `docs/EVIDENCE.md:12-19` carries the matching
warning for readers: paths named below are as they stood when written,
`parity/` is now `contract/`, and lines 772, 778, 780 and 1047-1052 name files
that no longer exist.

---

## 5. The gate work

Four separate failures are worth telling together, because they are one class:
**a gate that is green over a case it cannot see.** Named as such in
`954e07a`: *"This is the third time a gate has been green over a case it could
not see."*

### 5.1 pyright was never installed on a runner

`9fb5a1d` (2026-10-04). Cause, in the commit body: `python/pyproject.toml` dev
extras were `pytest`, `pytest-asyncio`, `pytest-xdist` — no `pyright` — and
`.github/workflows/ci.yml` installs only `.[dev]`, so `_has_pyright()` was
false on both runners and the `skipUnless` at `test_typecheck.py:49` fired every
run, green. Reproduction: shadow the pyright import → 735 passed, 7 skipped
(naming `test_typecheck.py:75,80,88,114,140`); restore → 740/2.

*Falsified now by* `python/pyproject.toml:25`, which reads
`dev = ["pytest", "pytest-asyncio", "pytest-xdist>=3.5", "pyright"]`, and by
running `python -m pytest tests -q -rs`: the two skips are
`test_docs_freshness.py:145` ("only 2 sections; no index needed") and
`test_integration_studio.py:31` ("needs live Studio"). **The type gate is not
among them**, so it ran. Measured 2026-10-06.

### 5.2 The contract was pinned on one side only

`2e4f27e` (2026-10-04). `build_contract.py:10-11` claimed the contract "cannot
drift without a test failing." Aspirational: `build()` was called from `main()`
and nowhere else, so every test read `tools.json` off disk and compared the
*server* to it. The generator's own inputs were unpinned — editing `_STEERS` at
`build_contract.py:67` moved neither side of that comparison and the suite
stayed green. `_STEERS` existed in three places, not the two the class
docstring claimed; the new
`test_python_matches_the_contract_exactly` pins the third by regeneration diff.

The test earned its keep on first run **by failing**, and the reason is
recorded in its own docstring: a stale `contract/__pycache__/*.pyc` whose header
mtime-and-size matched the source was accepted by CPython and made `build()`
emit `EXTENDED_write_script` where source and committed contract both say
`extended_write_script` — same byte length, only the case differed. The committed
contract was correct; the cache was the liar.

### 5.3 The `AGENTS.md` word ceiling

`7d5ff53` (2026-10-04). `CEILING = 2259` now lives at
`python/tests/test_docs_freshness.py:300`, asserted by
`test_agents_md_is_within_its_word_ceiling` using `len(text.split())`. Two
structural reasons it had never fired, both in the commit body: the
`agents-md-budget.ps1` script defaults `$Path` to the `AGENTS.md` **next to the
script**, so a bare invocation read `.config/opencode/AGENTS.md` (1,998 words,
exit 0) and never this repo's file; and nothing in CI ran that script at all.
Expressed as a test instead, so it *subtracts* a workflow step rather than
adding one.

**A stale reading worth flagging.** `7d5ff53`'s body says the ceiling is
"exact-current, so the file sits at its ceiling with zero headroom." That was
true on 2026-10-04 — `AGENTS.md` measured **exactly 2,259** words at `7d5ff53`.
At `HEAD` it measures **2,226** words, so **33 words of headroom have
reappeared** because `633af67` and `98a2360` trimmed the file. Both figures are
from `len(text.split())`, the test's own method; `wc -w` disagrees on this file
and reports 2,214 / 2,182, which is wrong.

### 5.4 Closed sets

`test_closed_sets.py`, 9 tests. It found `LAUNCH_FAILED` sitting in the public
vocabulary (`ALL_CODES`) with no pattern producing it and no handler raising it
— every realistic launch failure classified as `UNKNOWN`, so a caller writing
`if (code == "LAUNCH_FAILED")` — which publishing the constant invites —
silently never matched. **No type checker on either side would have caught it**;
`ToolError("LAUNCH_FAILED", ...)` type-checks in Python *and* TypeScript. See
[`docs/EVIDENCE.md:381`](EVIDENCE.md) for the full argument that this, not a
parallel implementation, is the right way to buy exhaustiveness.

`PLACE_NOT_OPEN` was **kept** on a user ruling of 2026-10-01 and turned into a
declared alias of `STALE_STUDIO_ID` rather than deleted — so an existing
caller's branch stays live instead of becoming a different kind of dead code.

### 5.5 The type-checker choice, and its honest limit

pyright over mypy, on measurement
([`docs/EVIDENCE.md:300`](EVIDENCE.md)): both found the same 7 real defects;
mypy additionally reported `instance.py:728`, which is a false positive (line
727 filters `if when is not None`, so the rebound dict genuinely is
`dict[int, float]` — mypy narrows the loop variable in a dict comprehension but
not the resulting container). Current config: `python/pyrightconfig.json`, three
rules promoted to errors because each found a real defect on first run.

The recorded limit matters more than the choice: **neither checker catches the
original bug as written.** A defaulted `.get("text", "")` on a missing key is
legal in both, because `dict.get` accepts any key and any default by design.
The `TypedDict` on `WatchResult` is the fix; the checker is the backstop.

---

## 6. The relayed `tools/call` fix

`1c08e50` (2026-10-05), the current `HEAD`.

**The defect.** The relayed `tools/call` path sent its result **twice** for one
request id: once unconditionally, then again after attaching `proxy_note`. See
`python/src/roblox_studio_mcp/extended_server.py:1731-1747` — the comment
there is the record:

> Two responses to one request id is not readable by a JSON-RPC peer, and the
> note was the casualty — a client that stops at the first response gets the
> answer with no "we could not check" beside it, which is the whole reason the
> note exists.

**Why the peer never saw the guard note.** A JSON-RPC peer is entitled to read
the **first** response. The first response was the bare result; the
`proxy_note` — which exists precisely to distinguish "we verified" from "we
could not check" — arrived on the second, unread one. So the duplicate was not
merely a protocol smell; it silently dropped the one field that told the caller
its safety check had not run.

**Why the second send was not restored.** The author nearly re-added the
duplicate on the hunch that no test could see it. Shadowing proved otherwise:
`_send` writes to stdout, so a test can count responses without a Studio.
Reinserting the duplicate turns exactly two tests red; restoring turns them
green. The tests are
`test_a_relayed_call_emits_exactly_one_response` and
`test_the_single_response_carries_the_guard_note`, both added to
`python/tests/test_improvements.py` in this commit — the second asserts the
note rides on the single response, so the fix cannot later be traded for silent
loss.

*The direction mattered more than the count.* Falsifier: reintroducing the
second `_send` in `extended_server.py` and seeing those two tests go red.

---

## 7. Path confinement and the file-size cap

`c5ac10c` (2026-10-04). Before it, `extended_execute_luau_from_file` and
`extended_insert_asset_from_file` took a `file_path` straight from the model
and read it at the process's full user privilege, after only
`os.path.abspath(os.path.expanduser(file_path))` — which collapses `..`, stops,
does not follow symlinks, and is compared to nothing. Measured across
`python/src` before the change: **zero** `is_relative_to`, **zero** `realpath`,
**zero** `commonpath`, five `abspath`, none compared to a root.

**The rule.** Root is the caller's resolved cwd. One shared helper,
`_confined()`, on both tools (`python/src/roblox_studio_mcp/extended/extensions.py:77`).
`Path.resolve()` rather than `abspath` is load-bearing: resolve follows symlinks
to their real target.

**Why cwd and not a fixed subtree** — the recorded reasoning: *"A tool whose
purpose is 'read the file I name' has to work outside a fixed subtree or it
gets abandoned, and an abandoned tool is a worse outcome than the one it
replaced."* Confinement to cwd still refuses `..` traversal and symlinks that
point out.

**One code for both refusals.** `CAPABILITY_DENIED` covers "exists outside the
root" as well as "outside the root", on purpose: different codes would make the
refusal an existence oracle for the host filesystem. A missing path *inside* the
root still falls through to the caller's own existence check as
`INVALID_ARGUMENT`, so the two layers stay distinguishable.

**The escape hatch** is `allow_outside`, default `False`, on both functions,
both tool schemas and both dispatchers. No config file, no env var.

**The size cap** is two constants, not a constant and a test literal:

```
MAX_FILE_BYTES = 16 * 1024 * 1024      # extensions.py:58
MEASURED_MAX_FILE_BYTES = 13_917_476   # extensions.py:74
```

`MEASURED_MAX_FILE_BYTES` is the largest of 18,508 `.luau`/`.lua`/`.rbxm`/
`.rbxmx` files measured on the authoring machine. Two constants rather than one
plus a test literal, because *"with the number in the test, tightening the cap
and lowering the number in one commit satisfied the check, so the test could not
fail."*

**The cost of the fix.** Eight pre-existing tests write fixtures to `tempfile`,
outside the repo root, so confinement correctly refuses them. They were not
wrong about anything — they never had to care about a root. They now pass
`allow_outside=True` explicitly rather than the default being loosened, which
keeps the default exercised everywhere else.

**The follow-on CI failure.** `c5ac10c` turned CI red at both runners. `954e07a`
traced all three failures to one cause: the tests asserted on the *spelling* of
a path rather than on the file it names, and `tempfile` hands back a short form
(`RUNNER~1` on Windows, `/private/var` on macOS) that `resolve()` expands. The
obvious fix passed a mutation that reintroduced the bug, because on a Windows dev
box the temp path is already its own resolved form — **the assertion cannot fail
locally**. The tests now pass a deliberately non-canonical spelling (`dir/./name`),
which `resolve()` normalises on every platform. `6681226` then subtracted 40
lines from that fix and re-proved the same mutation ladder.

---

## 8. What is deliberately still open

**36** top-level `- [ ]` items in `TODO.md`, distributed:

| section | open |
|---|---|
| Open items: skills and guards, reviewed 2026-10-01 | 10 |
| Remaining | 8 |
| macOS | 5 |
| `extended_wait_for`'s probe only works in Edit mode | 4 |
| To do (recorded 2026-09-30, not yet built) | 4 |
| Corrections to earlier notes in this file | 2 |
| Session operations | 1 |
| Launch routes fail at different layers | 1 |
| Housekeeping | 1 |

Plus **6** entries under `## Withdrawn — do not re-derive these`.

Some are open *by decision*, and a reader should not mistake them for neglect:

- **`invalidate_process_cache` is the last dead symbol, kept on purpose**
  (`TODO.md:184`): public API on a module importable as a library; a consumer
  who starts a process out of band would want it.
- **A `.py` edit needs an MCP server restart** (`TODO.md:198`, and
  `AGENTS.md` rule 2): the process holds the module in memory. A config change
  restarts it; a code fix does not.
- **`tests/test_integration_studio.py` is skipped in CI by design** — it needs a
  live Studio and `ROBLOX_STUDIO_MCP_INTEGRATION=1`.
- **The macOS branch has never run against a real macOS Studio.** Five open
  items; a macOS bug report is a genuine unknown rather than a regression.
- **The URI minimality is only half measured** (`TODO.md:188`): four keys
  works; whether `placeId` alone suffices was never established. The key is
  kept on the **user's authority**, which supersedes an earlier local attempt
  that left `name: null` — retired as evidence, *"because a key that can be
  dropped fails in a way a process count cannot see."* The sweep script this
  named was re-checked on 2026-10-05 and never committed: the measurement is
  unbuilt rather than pending.
- **Nothing sweeps the suite for the "green over a case it cannot see" class**
  — the one thing `954e07a` left open after `6681226` cut the section.

**Withdrawn claims are kept visible, not deleted.** Six entries, including
`### RETRACTED: "the real universe id fails, so never fetch it"` — re-tested
2026-10-02, **8 of 8** launches on the real universe id succeeded first try
and **6 of 6** on `0`, so the 3-failures-in-16 reading did not reproduce. The
claim is withdrawn; the measurement is not. `docs/EVIDENCE.md:1116-1196` carries
the retraction and the design that replaced it.

---

## 9. Defects in this repository's own record

Two of the three entries below were open when this file was written and were
**fixed by a history rewrite on 2026-10-06**; they are kept in the past tense
because the claims they record were true, and a reader who finds the commit
bodies first should know the difference. §9.3 is still open.

### 9.1 2,573 vendored blobs were in published history — removed 2026-10-06

`3653edd` *Remove node_modules accidentally committed with the strip* deleted
**2,665** files (`git show --name-status 3653edd`: 2,665 `D`, 1 `M`; 1,411,978
deletions, 3 insertions). The parent `e91cdb3` tree held exactly 2,665 files
under `node/`, of which:

- **2,573** under `node/node_modules/`
- **92** under `node/dist/`

**They were in history and reachable from `origin/main`.** `e91cdb3` is on
`main`, so anyone cloning got those blobs; deleting the working tree does not
remove them. `.gitignore:16` now carries a single `node/` guard so a stray
checkout cannot re-add them.

**Both sets are now gone from `main`**: `git rev-list --objects main | grep -c
node_modules` → **0**, and the same for `node/dist` → **0**. A third purge of
124 `node/` objects was deliberately **not** made — those are the Node
implementation's own sources (`node/src` 62, `node/tests` 38, `node/examples` 11,
plus packaging), not vendored blobs, and they are absent from the tip anyway.
See [§9.4](#94-the-history-rewrite-of-2026-10-06).

### 9.2 15 of 92 commits carried a false `Co-Authored-By` — removed 2026-10-06

**15** commits on `main` — exactly the **15 most recent**, dated 2026-10-04 and
2026-10-05 — carried `Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>`.
The trailer was **false**: the work in those commits was done by a different
model family. It was known and uncorrected when this file was written, recorded
here rather than fixed, because correcting it is a history rewrite and this
file's job is the record.

The 15, in order (`git log --format='%(trailers:key=Co-Authored-By,valueonly)'`
per commit on `main`): `1c08e50`, `98a2360`, `bb8bdff`, `08abfa0`, `633af67`,
`e9ff9ec`, `f2a44ee`, `c9bb45f`, `0789035`, `b9b2e69`, `6d6ff1a`, `7d5ff53`,
`2e4f27e`, `9fb5a1d`, `8b2b7a2`. The 16th commit back, `c2144b6`, did not carry
it, so the defect covered a contiguous window rather than the history.

**Now 0.** `git log main --format='%(trailers:key=Co-Authored-By,valueonly)' |
grep -c 'Claude Opus 4.5'` returns **0**. Only the trailer *line* was removed:
prose in later commit bodies that names the model in order to explain the
defect survives, and a dry run before the rewrite confirmed the split — **15**
messages would change, **81** would not.

**What the repository itself can and cannot say.** `git grep -i 'co-authored'`
over tracked files returns nothing — the trailer was the only copy. The only
`Claude` strings in tracked source are "Claude Code Edit/Write semantics"
(`writer.py:3`, `updater.py:3`, `extended_server.py:12`), an API format, not an
authorship claim. So the repository contains **no** statement of which model
wrote any commit, and this file does not invent one; the mismatch was
identified outside the repo.

*Falsifier for the fix:* the trailer count returning to a non-zero value on
`main`, which would mean a commit was authored with it after 2026-10-06.

### 9.3 The `_Full` suffix is not explained anywhere in the repository

The remote is `RobloxStudioMCP_Full.git` and the working directory on this
machine is `RobloxStudioMCP` — the suffix lives in the remote URL only.
`git grep -i 'full history' -- . ':!docs/HISTORY.md'` returns **nothing found**,
and no commit body in the 93 explains the name. "It keeps full history" is a
reasonable reading of a name plus §9.1, but it is **inference, not a recorded
reason**, and is labelled as such here. (A negative from `git grep` is one
hypothesis among five — absent, wrapped, renamed, fenced, wrong needle. The
corroboration is that `git log --all` bodies were read for the same window and
none mentions it.)

The `':!docs/HISTORY.md'` exclusion is not cosmetic. This file quotes the phrase
*"It keeps full history"* in order to report that nothing else does, so an
unexcluded `grep` matches **this file** and returns exit 0 — the negative
announces itself as a positive. Measured: 4 self-matches, exit 1 without them.

The rewrite sharpened this rather than settling it. `_Full` now holds the **only**
copy of the pre-rewrite line, because `main` no longer contains it and the public
repository is a different repository entirely (§9.5). So the suffix is more true
than it was, and still unexplained.

### 9.4 The history rewrite of 2026-10-06

Three passes with `git filter-branch`, **scoped to `main`** — `parity` was left
untouched and is still the pre-rewrite `bd77d97`. `git filter-repo` was not
installed (`git filter-repo --version` → *not a git command*).

| # | what | how | verified after |
|---|---|---|---|
| 1 | Remove 15 false `Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>` trailers | `--msg-filter`, a `sed` deleting lines matching `^Co-Authored-By: Claude Opus 4\.5 <noreply@anthropic\.com>$` | 96 → 96 commits; **15** messages changed, **81** untouched |
| 2 | Remove 2,573 vendored `node/node_modules/` files | `--index-filter 'git rm -r --cached --ignore-unmatch -q node/node_modules'` | 96 → 93 commits; **0** `node_modules` objects |
| 3 | Remove 92 compiled `node/dist/` files | `--index-filter '… -q node/dist'` | **0** `node/dist` objects |

**No content changed.** The tip tree hash is `9d2612b9a4400621cb82f5ef2bb2214d36e9872d`
before and after all three passes, and the suite passes on the rewritten tree:
**744 passed, 2 skipped, 206 subtests**. That is the load-bearing check — the
rewrite touched history only.

**Why the trailer filter is anchored to the start of a line.** Later commit
bodies *name* the model in prose, in order to record the defect. An unanchored
`grep` would have deleted those sentences too. A dry run over all 96 messages
before anything was rewritten reported exactly **15** changed and **81**
unchanged, and confirmed the prose mention in `5074b21` survived the filter
intact.

**The 3 commits that disappeared, and why each went.** `--prune-empty` drops any
commit whose diff becomes empty. This is not only about vendored blobs:

- `5db54b6` and `7e86e80` (*Update ci.yml*) — identical trees, 64 files each, 28
  of them under `node/`. Their diffs were **entirely** vendored blobs, so both
  vanished.
- `c9bb45f` (*Trigger a CI run to test whether the overnight failure…*) — **was
  already empty before any rewrite**: its tree `ddc6402780d2c4a80e26dc36ae92453355c99723`
  is identical to its parent's. `filter-branch` pruned a pre-existing empty
  commit, which is a change to the history that has nothing to do with blobs.

96 − 3 = 93, exactly. `e91cdb3` — the commit that carried all 2,573 blobs — was
**not** pruned; it survives as `498a662`, with the same author date and subject
and 109 files instead of 2,774. It is `426d37e` at the intermediate stage
between passes 2 and 3.

**A second `filter-branch` pass silently did nothing.** Pass 2 initially
aborted with *Cannot create a new backup. A previous backup already exists in
refs/original/*, because pass 1 had already created one. Nothing in the command's
output said the rewrite had not happened, and the commit count and tip tree were
unchanged — both of which read like success. Only re-counting the objects
(`git rev-list --objects main | grep -c node_modules` → still 2,780) caught it.
Re-run with `-f`. **A history rewrite that exits 0 has not necessarily rewritten
anything**; count the objects afterwards.

**Every pre-rewrite sha cited in this file still resolves**, on
`origin/backup/pre-public-2026-10-06` (still `f0f8d86`) or, where the commit is in
`parity`'s range, on `parity`. None is reachable from `main`. Of the 44 distinct
shas cited here, **42 were remapped**, 2 (`b82d41f`, `dff5f0e`) are the
stash pair and were never on `main`, and `c9bb45f` was pruned. The map was built
by joining on tree + author date + subject, then checked 1:1 — no new sha is
claimed by two old commits — and spot-checked by comparing subjects.

Selected old → new: `1c08e50`→`46ca7cf`, `c2144b6`→`7c585f3`, `8b2b7a2`→`a7a2e43`,
`9fb5a1d`→`c5d3041`, `7d5ff53`→`ffebe51`, `3653edd`→`bb46c72`, `e91cdb3`→`498a662`,
`bd77d97`→`dbcd2d7` (on `parity` only).

### 9.5 Publication target

`RobloxStudioMCP_Full` is **private** and is the archive: it holds the only copy
of the pre-rewrite line. The public repository is a **different** one,
`bdidk235/RobloxStudioMCP` (id `1407227973`, created 2026-10-06T12:02:45Z), which
was **empty** when this was written. Publishing is therefore a *push of the two
clean refs*, not an export: `main` and `parity` carry no `node_modules`, no
`node/dist`, and no false trailers, so they go as they stand.

*Unverified at the time of writing:* that push had not happened, and
`bdidk235/RobloxStudioMCP` was still `size_kb=0`.

---

## 10. Corrections recorded by this file

Old value beside the new one, with the date each was measured. None of these
overwrites anything; the sources still say what they say.

| # | source | old value | measured value | measured |
|---|---|---|---|---|
| 1 | `3653edd` body: "2,665 untracked **node_modules** files were committed" | 2,665 node_modules | 2,665 total under `node/`, of which **2,573** `node_modules/` and **92** `dist/` | 2026-10-06 |
| 2 | `9bb8e48` subject: "Take **1,344** lines back out" | 1,344 | `TODO.md` 4,135 → 2,790 = **1,345** | 2026-10-06 |
| 3 | `7d5ff53` body: "`AGENTS.md` sits at its ceiling with zero headroom" | 2,259 = ceiling, 0 headroom | true at `7d5ff53`; **2,250** at `HEAD`, **9 words** of headroom | 2026-10-06 |
| 4 | `bc75db8` subject: "Gate the **evidence log**'s size" | a gate on `docs/EVIDENCE.md` | `docs/EVIDENCE.md` **did not exist** at `bc75db8` (created by the next commit, `9b44c51`); the gate was on `TODO.md`, and the content moved the next day | 2026-10-06 |
| 5 | `e91cdb3` body: Node's remaining gaps are **all** silent-wrong-answer bugs | 3 of 3 | **1 of 3**; see [`docs/EVIDENCE.md:1411`](EVIDENCE.md) and §4.2 | 2026-10-04, by `b9b2e69` |
| 6 | §1 of the earlier draft of this file: "Tracked files at `HEAD` — **110**" | 110 | **111**; `git ls-files` and `git ls-tree -r --name-only HEAD` agree. The count was taken against `1c08e50`, and **committing this file made it the 111th** | 2026-10-06 |
| 7 | §1 of the earlier draft: "`AGENTS.md` — **2,226** words" | 2,226 | **2,250**; the tip commit `7189ce6` *Set the real commit targets: 60 and 700* edited `AGENTS.md` after the 2,226 was measured. Not caused by the rewrite | 2026-10-06 |
| 8 | §3 of this file: "`parity` **is an ancestor of `main`** … `main..parity` returns 0" | ancestor, 0 | **no longer related**: `git merge-base main parity` is **empty**, `main..parity` is **67** | 2026-10-06, by the rewrite |

Row 5 is not this file's correction — `b9b2e69` *Record the strip's justification,
re-derived by running it* already made it and wrote it into the evidence log. It
is listed because the original claim is still the one in the commit body, and a
reader who finds `e91cdb3` first should not take it at face value.

Row 6 is the shape of error this file exists to warn about: a number can be
correct when measured and wrong the moment the file reporting it is committed.
Row 7 is the ordinary way a hand-written figure rots — nothing exotic, the file
it describes simply changed afterwards.

---

## 11. How to re-derive every number here

Run from the repository root unless noted. **Shas written in this file's prose are
pre-rewrite** (§9.4), so the commands below use the new values; the pre-rewrite
object is still reachable at `origin/backup/pre-public-2026-10-06` if you want to
re-derive the "before" column.

```bash
# counts and shape
git rev-list --count main                       # 93
git rev-list --all | sort -u | wc -l            # 219 (three history lines overlap)
git stash list                                  # stash@{0}: On main: temp tsconfig change
git merge-base --is-ancestor parity main && echo YES   # prints nothing: NOT an ancestor
git merge-base main parity                      # empty - the branches share no commit
git rev-list --count main..parity               # 67
git ls-tree -r --name-only HEAD | wc -l         # 111
git ls-tree -r --name-only parity | wc -l       # 168

# the vendored blobs are gone from main (§9.1)
git rev-list --objects main | grep -c node_modules        # 0
git rev-list --objects main | grep -c 'node/dist/'        # 0
git rev-list --objects main | grep -c ' node/'            # 124 - real Node source, kept

# ...and were there, on the pre-rewrite line
git ls-tree -r --name-only e91cdb3 | grep -c '^node/'              # 2665
git ls-tree -r --name-only e91cdb3 | grep -c '^node/node_modules/'  # 2573
git ls-tree -r --name-only e91cdb3 | grep -c '^node/dist/'         # 92

# the false trailers are gone from main (§9.2)
git log main --format='%(trailers:key=Co-Authored-By,valueonly)' | grep -c 'Claude Opus 4.5'  # 0
git log f0f8d86 --format='%(trailers:key=Co-Authored-By,valueonly)' | grep -c 'Claude Opus 4.5'  # 15

# the rewrite itself (§9.4) - the tip tree is the load-bearing check
git rev-parse main^{tree}                        # 9d2612b9a4400621cb82f5ef2bb2214d36e9872d
git rev-parse origin/backup/pre-public-2026-10-06^{tree}   # same tree, different line
git rev-list --count f0f8d86                     # 96, the pre-rewrite line

# the _Full explanation is not in the repo (§9.3) - a negative, not an absence
# NOTE the exclusion: this file quotes the phrase, so without it grep self-matches
git grep -n -I -i 'full history' -- . ':!docs/HISTORY.md' ; echo "exit=$?"   # exit=1, nothing found

# gates (from python/)
python -m pytest tests -q -rs                  # 744 passed, 2 skipped, 206 subtests
python -c "import json;d=json.load(open('../contract/tools.json'));print(d['tool_count'],d['total_description_chars'],d['total_description_cap'])"

# the AGENTS.md ceiling — use the test's own method, not wc -w
python -c "print(len(open('../AGENTS.md').read().split()))"          # 2250, ceiling 2259
git show ffebe51:AGENTS.md | python -c "import sys;print(len(sys.stdin.read().split()))"  # 2259 at the ceiling commit
```

`wc -w` undercounts this file by 44 words against Python's `split()` — em-dashes
and an unset locale — which is why §1 quotes the Python figure and the ceiling
test uses it too.

---

## 12. Where the record actually lives

Four files, four jobs, deliberately not merged:

| file | holds | do not put there |
|---|---|---|
| `TODO.md` | open work (36 items) and the 6 withdrawals | closed research |
| `docs/EVIDENCE.md` | closed research, provenance tagged `MEASURED` / `DOCUMENTED` / `INFERRED` / `UNVERIFIED` | open work |
| `AGENTS.md` | standing rules and the gates table | any figure that a generated artefact owns |
| `contract/tools.json` | the generated surface and its budget | hand-edits — it is generated |

The rule that produced this layout, from `TODO.md:1-12`: reaching the work meant
crossing 1,300 lines of findings that were already settled, so closed research
moved out and the count of open items stopped being written down (it rots).

**The one-line version of how this repository thinks**, which is also why the
history above is shaped the way it is: a generated artefact next to a
hand-written number is a standing invitation for the second to rot, and the fix
is a gate on the *rule*, not vigilance about the value.